"""UI split Step 2 — the companion channel can never carry caregiver data.

The companion screen is open on the same device as the caregiver dashboard. It
must receive a caption, a clock, a reminder countdown, the next routine and a
playing title — and nothing else. This suite enumerates every internal event
type, feeds hostile details into the mapper, and asserts that only the three
allowed transitions survive. It also checks the payload whitelist itself
(unknown kind, unknown key, forbidden key) and the caregiver-stream role gate.

Run directly:  .venv/bin/python tests/test_companion_stream.py
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dashboard import companion
from dashboard.stream import stream_role_allowed
from events.event_log import Event, EventType

PASSED = 0
FAILED = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if condition:
        PASSED += 1
        print(f"  ok   {name}")
    else:
        FAILED += 1
        print(f"  FAIL {name}" + (f" — {detail}" if detail else ""))


def event(event_type: EventType, detail: dict) -> Event:
    return Event(
        timestamp=1_700_000_000.0,
        type=event_type,
        profile_id="asha_demo_001",
        detail=detail,
    )


def keys_of(message: dict) -> set[str]:
    return set(message.get("data", {}).keys())


# ---------------------------------------------------------------------------
# 1. Payload whitelist
# ---------------------------------------------------------------------------

print("payload whitelist")
check(
    "unknown kind is refused",
    companion.build_message("alert", {"message": "She fell"}) is None,
)
check(
    "tier kind is refused",
    companion.build_message("tier_change", {"new_tier": "red"}) is None,
)
check(
    "forbidden key is refused even with an allowed kind",
    companion.build_message(
        "caption", {"text": "hello", "tier": "red"}
    )
    is None,
)
for forbidden_key in (
    "alert_id",
    "consent_state",
    "danger_level",
    "symptom",
    "emotion",
    "severity",
    "distress",
    "repeat_count",
    "fallback_type",
    "physical_urgency",
    "diagnosis",
):
    check(
        f"forbidden key {forbidden_key!r} is refused",
        companion.build_message("caption", {"text": "hi", forbidden_key: 1})
        is None,
    )

caption = companion.build_message(
    "caption", {"text": "  I'm here with you.  ", "at": 123.0}
)
check("caption keeps text and at", caption == {
    "type": "caption",
    "data": {"text": "  I'm here with you.  ", "at": 123.0},
}, str(caption))

check(
    "empty caption is refused",
    companion.build_message("caption", {"text": "   "}) is None,
)
check(
    "clock without now is refused",
    companion.build_message("clock", {"date": "Thursday"}) is None,
)
check(
    "unknown keys are dropped",
    companion.build_message(
        "clock", {"now": 1.0, "iso": "x", "date": "Thursday", "extra": "nope"}
    )
    == {"type": "clock", "data": {"now": 1.0, "iso": "x", "date": "Thursday"}},
)
check(
    "reminder without present is refused",
    companion.build_message("reminder_countdown", {"label": "Tea"}) is None,
)
check(
    "reminder present=false is valid",
    companion.build_message("reminder_countdown", {"present": False})
    == {"type": "reminder_countdown", "data": {"present": False}},
)
check(
    "next_routine requires day today/tomorrow",
    companion.build_message(
        "next_routine", {"label": "Lunch", "time": "13:00", "day": "someday"}
    )
    is None,
)

# ---------------------------------------------------------------------------
# 2. Every event type: only three transitions are allowed
# ---------------------------------------------------------------------------

print("\nevent mapping — allowlist")
HOSTILE_DETAILS: dict[EventType, dict] = {
    EventType.REPEAT: {"question": "Where is Priya?", "count": 4, "tier": "yellow"},
    EventType.DISTRESS: {"level": "high", "reason": "sustained sadness"},
    EventType.TIER_CHANGE: {"old_tier": "green", "new_tier": "red", "reason": "danger"},
    EventType.CONSENT_ASKED: {"contact_name": "Rohan", "question": "Shall I call him?"},
    EventType.CONSENT_ANSWERED: {"outcome": "accepted", "contact_name": "Rohan"},
    EventType.FALLBACK: {"fallback_type": "guardrail", "reason": "diagnosis language"},
    EventType.DANGER_STATEMENT: {"phrase": "I fell and cannot get up", "tier": "red"},
    EventType.SYSTEM_HEALTH: {"message": "Nebius 402", "severity": "warning"},
    EventType.SESSION_START: {"profile_id": "asha_demo_001"},
    EventType.SESSION_END: {"summary": "She missed Priya."},
    EventType.TIMING: {"turn_ms": 812, "model": "nano"},
    EventType.PHYSICAL_SYMPTOM: {
        "phrase": "chest pain",
        "urgency": "urgent",
        "tier": "red",
    },
    EventType.MEMORY_BOX_PLAYED: {"clip": "priya_hello.mp3"},
    EventType.ALERT_ACKNOWLEDGED: {"alert_id": "alert_1", "note": "calling now"},
    EventType.ALERT_SENT: {"status": "sent", "sent": 1, "reason": "tier_rise"},
    EventType.CONVERSATION_TURN: {"speaker": "user", "text": "I fell"},
    EventType.ACTION: {"action_type": "tell_time", "success": True},
    EventType.REMINDER_FIRED: {"reminder_id": 1, "kind": "timer", "label": "Tea"},
}

missing = set(EventType) - set(HOSTILE_DETAILS)
check("the hostile table covers every EventType", not missing, str(missing))

for event_type, detail in HOSTILE_DETAILS.items():
    mapped = companion.map_event(
        event(event_type, detail), pending=lambda: {"present": False}
    )
    if event_type in (EventType.CONVERSATION_TURN, EventType.ACTION, EventType.REMINDER_FIRED):
        # These have allowed *sub-cases*; the hostile sub-case must still be
        # refused. A user turn, a non-media action and a fired reminder with no
        # injected pending provider are not companion payloads here.
        if event_type == EventType.CONVERSATION_TURN:
            check(f"{event_type.value}: user speech is refused", mapped is None)
        elif event_type == EventType.ACTION:
            check(f"{event_type.value}: non-media action is refused", mapped is None)
        else:
            check(
                f"{event_type.value}: reminder maps to reminder_fired",
                mapped is not None
                and mapped["type"] == "reminder_fired"
                and "label" in mapped["data"]
                and "spoken" in mapped["data"]
                and not any(
                    k in keys_of(mapped)
                    for k in ("tier", "alert_id", "consent", "danger", "symptom")
                ),
            )
    else:
        check(f"{event_type.value}: refused", mapped is None, str(mapped))

# The three allowed transitions.
assistant_turn = event(
    EventType.CONVERSATION_TURN,
    {"speaker": "sahaara", "text": "I'm right here. Would you like tea?"},
)
mapped = companion.map_event(assistant_turn)
check("assistant turn becomes a caption", mapped is not None and mapped["type"] == "caption")
check(
    "assistant caption keeps only text and at",
    keys_of(mapped) == {"text", "at"},
    str(mapped),
)

song = event(
    EventType.ACTION,
    {"action_type": "play_song", "success": True, "title": "Lag Jaa Gale"},
)
mapped = companion.map_event(song)
check("successful song becomes now_playing", mapped is not None and mapped["type"] == "now_playing")
check(
    "now_playing keeps title/kind/playing/at",
    mapped is not None and keys_of(mapped) == {"title", "kind", "playing", "at"},
    str(mapped),
)
check("now_playing says kind=song", mapped is not None and mapped["data"]["kind"] == "song")

radio = event(
    EventType.ACTION,
    {"action_type": "play_radio", "success": True, "station_name": "Radio Mirchi"},
)
mapped = companion.map_event(radio)
check(
    "successful radio becomes now_playing with the station",
    mapped is not None
    and mapped["type"] == "now_playing"
    and mapped["data"]["title"] == "Radio Mirchi"
    and mapped["data"]["kind"] == "radio",
)

failed_song = event(
    EventType.ACTION,
    {"action_type": "play_song", "success": False, "title": "Lag Jaa Gale"},
)
check("failed song never becomes now_playing", companion.map_event(failed_song) is None)

no_title = event(EventType.ACTION, {"action_type": "play_song", "success": True})
check("a song with no title is refused", companion.map_event(no_title) is None)

pending_payload = {
    "present": True,
    "id": 7,
    "label": "Tea",
    "fire_at": 1_700_000_600.0,
    "seconds_remaining": 600,
    "at": 1_700_000_000.0,
}
timer = event(
    EventType.ACTION,
    {"action_type": "set_timer", "success": True, "label": "Tea", "seconds": 600},
)
mapped = companion.map_event(timer, pending=lambda: dict(pending_payload))
check(
    "setting a timer publishes the next reminder countdown",
    mapped is not None
    and mapped["type"] == "reminder_countdown"
    and mapped["data"]["present"] is True
    and mapped["data"]["seconds_remaining"] == 600,
)
check(
    "timer countdown has no tier or alert key",
    mapped is not None
    and not any(
        k in keys_of(mapped)
        for k in ("tier", "alert_id", "consent", "danger", "symptom")
    ),
)

empty_pending = {"present": False}
cancelled = event(
    EventType.ACTION, {"action_type": "cancel_timer", "success": True}
)
mapped = companion.map_event(cancelled, pending=lambda: dict(empty_pending))
check(
    "cancelling the last timer clears the countdown",
    mapped is not None
    and mapped["type"] == "reminder_countdown"
    and mapped["data"] == {"present": False},
)

# ---------------------------------------------------------------------------
# 3. Hub cannot publish a caregiver kind
# ---------------------------------------------------------------------------

print("\nhub whitelist")
check("hub refuses an alert kind", companion.hub.publish("alert", {"message": "x"}) is None)
check(
    "hub refuses a tier kind",
    companion.hub.publish("tier_change", {"new_tier": "red"}) is None,
)
check(
    "hub refuses a caption carrying a tier key",
    companion.hub.publish("caption", {"text": "hi", "tier": "red"}) is None,
)
ok = companion.hub.publish("caption", {"text": "I'm here."})
check("hub publishes an allowed caption", ok == {
    "type": "caption",
    "data": {"text": "I'm here."},
}, str(ok))
check("hub subscriber list starts empty after publish", companion.hub.subscriber_count() == 0)
check(
    "the module-level observe hook exists for stream.py",
    callable(getattr(companion, "observe", None)),
)
check(
    "module-level observe maps a caption",
    companion.observe(
        event(EventType.CONVERSATION_TURN, {"speaker": "sahaara", "text": "I'm here."})
    )
    == {"type": "caption", "data": {"text": "I'm here.", "at": 1_700_000_000.0}},
)

# ---------------------------------------------------------------------------
# 4. Next routine
# ---------------------------------------------------------------------------

print("\nnext routine")
routine = {
    "wake_time": "07:00",
    "meals": {"breakfast": "08:00", "lunch": "13:00", "dinner": "19:30"},
    "medication_times": ["08:30", "20:00"],
}
at_11 = datetime(2026, 10, 29, 11, 0)
item = companion.next_routine_item(routine, at_11)
check(
    "11:00 picks lunch today",
    item == {"label": "Lunch", "time": "13:00", "day": "today"},
    str(item),
)
at_22 = datetime(2026, 10, 29, 22, 0)
item = companion.next_routine_item(routine, at_22)
check(
    "22:00 picks wake tomorrow",
    item == {"label": "Wake up", "time": "07:00", "day": "tomorrow"},
    str(item),
)
check(
    "medication times never appear in the strip",
    companion.next_routine_item(routine, at_11)["label"] != "Medication",
)
check("empty routine returns None", companion.next_routine_item({}, at_11) is None)
bad = {"wake_time": "not-a-time", "meals": {"lunch": "99:99"}}
check("malformed times are ignored, not guessed", companion.next_routine_item(bad, at_11) is None)

# ---------------------------------------------------------------------------
# 5. Snapshot
# ---------------------------------------------------------------------------

print("\nsnapshot")
messages = companion.snapshot(
    "asha_demo_001",
    routine,
    now=at_11.timestamp(),
    pending=lambda: {"present": False},
)
kinds = [m["type"] for m in messages]
check("snapshot starts with clock", kinds[0] == "clock", str(kinds))
check("snapshot includes the reminder state", "reminder_countdown" in kinds, str(kinds))
check("snapshot includes the next routine", "next_routine" in kinds, str(kinds))
check(
    "snapshot carries no caregiver key",
    all(
        not any(
            k in keys_of(m)
            for k in ("tier", "alert_id", "consent", "danger", "symptom")
        )
        for m in messages
    ),
)

snapshot_with_reminder = companion.snapshot(
    "asha_demo_001",
    routine,
    now=at_11.timestamp(),
    pending=lambda: dict(pending_payload),
)
reminder = next(m for m in snapshot_with_reminder if m["type"] == "reminder_countdown")
check(
    "snapshot reminder is the pending one",
    reminder["data"]["label"] == "Tea" and reminder["data"]["present"] is True,
    str(reminder),
)

# ---------------------------------------------------------------------------
# 6. Caregiver-stream role gate
# ---------------------------------------------------------------------------

print("\ncaregiver stream role gate")
check("auth off (None) stays open", stream_role_allowed(None) is True)
check("caregiver is allowed", stream_role_allowed({"role": "caregiver"}) is True)
check("person is refused", stream_role_allowed({"role": "person"}) is False)
check("missing role is refused", stream_role_allowed({}) is False)
check("empty role is refused", stream_role_allowed({"role": ""}) is False)

# ---------------------------------------------------------------------------

print(f"\n{PASSED} passed, {FAILED} failed")
if FAILED:
    sys.exit(1)
