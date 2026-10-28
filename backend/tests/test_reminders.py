"""
Tests for the one-off timer/reminder lifecycle.

Owner decisions under test (2026-10-28):
- Real wall-clock time, seconds supported, no 300x demo compression
- With no voice session open the timer stays on the dashboard and is NEVER
  spoken at the next session
- With a session open it is spoken (queued to the voice channel)
- Medication reminders are sanitised before they are ever spoken, and the
  person's exact wording is kept for the caregiver dashboard
- A timer fires once, even if the poll loop runs many times
"""

import asyncio
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_TMP_DB = Path(tempfile.mkdtemp()) / "reminders.db"
os.environ["SAHAARA_DB_PATH"] = str(_TMP_DB)

import database  # noqa: E402
database.init_db()

import actions  # noqa: E402
from actions import (  # noqa: E402
    cancel_timer,
    describe_reminder_fired,
    list_timers,
    sanitise_reminder_text,
    set_reminder,
    set_timer,
)
from scheduler import get_scheduler_for_profile  # noqa: E402

passed = 0
failed = 0


def check(name: str, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  PASS: {name}")
    else:
        failed += 1
        print(f"  FAIL: {name} {detail}")


PROFILE = "asha_demo_001"


def drain(queue) -> list:
    items = []
    while True:
        try:
            items.append(queue.get_nowait())
        except asyncio.QueueEmpty:
            return items


def run_ticks(scheduler, voice: bool, ticks: int = 3, pause: float = 0.05):
    """Drive the timer loop by hand — no real sleeping, no event loop games."""

    async def _run():
        if voice:
            scheduler.mark_voice_session(True)
        try:
            for _ in range(ticks):
                await scheduler._fire_due_reminders()
                await asyncio.sleep(pause)
        finally:
            if voice:
                scheduler.mark_voice_session(False)

    asyncio.run(_run())


# ============================================================
# Test 1: Sanitising reminder text
# ============================================================
print("=== Test 1: Medication text is sanitised ===")

sanitise_tests = [
    ("take my pills", "take your medication", True),
    ("take two pills at 8", "take your medication", True),
    ("take 5mg of metformin", "take your medication", True),
    ("my blood pressure tablets", "take your medication", True),
    ("take my insulin", "take your medication", True),
    ("call Rohan", "call Rohan", False),
    ("drink some water", "drink some water", False),
    ("steep the tea", "steep the tea", False),
]

for raw, expected, expected_med in sanitise_tests:
    label, is_med = sanitise_reminder_text(raw)
    check(f"'{raw}' -> '{expected}'", label == expected, f"got {label!r}")
    check(f"'{raw}' medication flag = {expected_med}", is_med == expected_med, f"got {is_med}")

# ============================================================
# Test 2: A reminder stores safe + raw text
# ============================================================
print("\n=== Test 2: Reminder stores both versions ===")

result = set_reminder(
    seconds=60,
    label="take two pills at 8 with water",
    profile_id=PROFILE,
    label_raw="remind me in a minute to take two pills at 8 with water",
)
check("reminder created", result.success is True, result.result_text)
check(
    "spoken text never names a dose",
    "pills" not in result.result_text.lower() and "two" not in result.result_text,
    result.result_text,
)
check("spoken text is generic", "take your medication" in result.result_text, result.result_text)

row = database.get_reminder(result.detail["id"])
check("row keeps the safe label", row and row["label"] == "take your medication")
check(
    "row keeps the person's exact words for the dashboard",
    row and "two pills at 8" in row["label_raw"],
    row and row["label_raw"],
)
check("row is flagged medication", result.detail.get("medication") is True)

fired_ids = []

# ============================================================
# Test 3: Firing with NO voice session
# ============================================================
print("\n=== Test 3: Firing with no voice session ===")

scheduler = get_scheduler_for_profile(PROFILE)
drain(scheduler._initiative_queue)
drain(scheduler.get_voice_initiative_queue())

short = set_timer(0.05, label="tea", profile_id=PROFILE)
timer_id = short.detail["id"]
fired_ids.append(timer_id)
time.sleep(0.1)

run_ticks(scheduler, voice=False)

row = database.get_reminder(timer_id)
check("timer marked fired", row and row["status"] == "fired", row and row["status"])
check("not delivered by voice", row and row["delivered_voice"] == 0)

dashboard = drain(scheduler._initiative_queue)
voice = drain(scheduler.get_voice_initiative_queue())
check("dashboard got the timer", len(dashboard) == 1, f"got {len(dashboard)}")
check(
    "dashboard text is the spoken line",
    dashboard and "tea" in dashboard[0]["data"]["text"],
    dashboard and dashboard[0]["data"]["text"],
)
check("voice queue stayed empty", voice == [], f"got {voice}")

# ============================================================
# Test 4: Firing WITH a voice session
# ============================================================
print("\n=== Test 4: Firing with a voice session open ===")

short = set_timer(0.05, label="", profile_id=PROFILE)
timer_id = short.detail["id"]
fired_ids.append(timer_id)
time.sleep(0.1)

run_ticks(scheduler, voice=True)

row = database.get_reminder(timer_id)
check("timer marked fired", row and row["status"] == "fired")
check("delivered by voice", row and row["delivered_voice"] == 1)

voice = drain(scheduler.get_voice_initiative_queue())
check("voice queue got one message", len(voice) == 1, f"got {len(voice)}")
if voice:
    check("voice message type", voice[0]["type"] == "initiative")
    check("voice message is the timer line", voice[0]["text"] == "Your timer is done.", voice[0]["text"])
    check("category is timer", voice[0]["category"] == "timer")

check("session counter back to zero", scheduler.voice_session_open is False)

# ============================================================
# Test 5: A timer fires exactly once
# ============================================================
print("\n=== Test 5: Exactly once ===")

short = set_timer(0.05, label="once", profile_id=PROFILE)
timer_id = short.detail["id"]
fired_ids.append(timer_id)
time.sleep(0.1)

drain(scheduler._initiative_queue)
drain(scheduler.get_voice_initiative_queue())
run_ticks(scheduler, voice=False, ticks=5)

dashboard = drain(scheduler._initiative_queue)
check("five ticks, one announcement", len(dashboard) == 1, f"got {len(dashboard)}")

# ============================================================
# Test 6: An unconsumed voice queue is not replayed later
# ============================================================
print("\n=== Test 6: No stale timer at the next session ===")

# The requirement is that a fire with no session never enters the voice queue
# at all, so there is nothing left to speak later.
drain(scheduler.get_voice_initiative_queue())
short = set_timer(0.05, label="silent", profile_id=PROFILE)
fired_ids.append(short.detail["id"])
time.sleep(0.1)
run_ticks(scheduler, voice=False, ticks=2)
check(
    "nothing waiting to be spoken at the next session",
    drain(scheduler.get_voice_initiative_queue()) == [],
)

# ============================================================
# Test 7: Fired reminders leave the pending list
# ============================================================
print("\n=== Test 7: Fired reminders are not pending ===")

pending_ids = {row["id"] for row in database.list_pending_reminders(PROFILE)}
check(
    "every fired timer is gone from the pending list",
    not (pending_ids & set(fired_ids)),
    f"still pending: {pending_ids & set(fired_ids)}",
)
check(
    "fired timers are not served as due",
    all(r["id"] not in fired_ids for r in database.get_due_reminders(PROFILE, time.time() + 3600)),
)

# ============================================================
# Test 8: Firing text
# ============================================================
print("\n=== Test 8: What Sahaara says when it fires ===")

check(
    "timer with a label",
    describe_reminder_fired({"kind": "timer", "label": "tea"}) == "Your timer for tea is done.",
)
check(
    "timer without a label",
    describe_reminder_fired({"kind": "timer", "label": "timer"}) == "Your timer is done.",
)
check(
    "reminder",
    describe_reminder_fired({"kind": "reminder", "label": "take your medication"})
    == "A reminder — take your medication.",
)

# ============================================================
# Test 9: Cancelled timers never fire
# ============================================================
print("\n=== Test 9: Cancelled timers do not fire ===")

short = set_timer(0.05, label="cancel-me", profile_id=PROFILE)
time.sleep(0.1)
cancelled = cancel_timer(profile_id=PROFILE, which="cancel-me")
check("cancelled the pending timer", cancelled.success is True, cancelled.result_text)
drain(scheduler._initiative_queue)
run_ticks(scheduler, voice=False, ticks=3)
check("cancelled timer never announced", drain(scheduler._initiative_queue) == [])

# ============================================================
# Test 10: Real time, not the 300x demo compression
# ============================================================
print("\n=== Test 10: No demo compression ===")

before = time.time()
created = set_timer(30, label="real", profile_id=PROFILE)
row = database.get_reminder(created.detail["id"])
elapsed_target = row["fire_at"] - before
check(
    "a 30 second timer is 30 seconds away",
    29 <= elapsed_target <= 31,
    f"got {elapsed_target:.2f}s (300x compression would make it 0.1s)",
)

print(f"\n  RESULT: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
