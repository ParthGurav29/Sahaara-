"""
Emergency Call — the wording spoken to Asha.

The call itself is deterministic, and so is everything Sahaara says about it.
These checks exist to make sure a simulated, disabled or failed campaign can
never be described with the words that belong to a call that actually happened.

Run directly: python tests/test_emergency_wording.py
"""

import sys
import os
import re
import time
import importlib
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

passed = 0
failed = 0


def check(name, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  PASS: {name}")
    else:
        failed += 1
        print(f"  FAIL: {name} {detail}")


# Words that may only be used when a real call was actually placed / answered.
CLAIM_WORDS = (
    "i'm calling",
    "i am calling",
    "i've called",
    "i have called",
    "i placed a call",
    "the call was placed",
    "placed a call",
    "on the line",
    "phone is ringing",
    "dialling",
    "dialing",
    "has been called",
    "spoke to",
    "speaking with",
)

NON_CLAIM_STATES = ("simulated", "blocked", "not_reached")


def _contains_claim(text: str) -> str | None:
    lowered = text.lower()
    for word in CLAIM_WORDS:
        if word in lowered:
            return word
    return None


# ---- Isolation -----------------------------------------------------------

def _fresh_db():
    db_path = Path(tempfile.gettempdir()) / (
        f"sahaara_test_wording_{os.getpid()}_{int(time.time() * 1000000)}.db"
    )
    for suffix in ("", "-wal", "-shm"):
        Path(str(db_path) + suffix).unlink(missing_ok=True)
    os.environ["SAHAARA_DB_PATH"] = str(db_path)
    os.environ["SAHAARA_PUSH_ENABLED"] = "false"
    os.environ["EMERGENCY_CALLS_ENABLED"] = "true"
    os.environ["SAHAARA_EMERGENCY_PROVIDER"] = "simulated"
    os.environ["SAHAARA_EMERGENCY_REAL_CALLS_CONFIRMED"] = "false"
    os.environ["ALLOWED_TEST_NUMBERS"] = ""
    os.environ["SAHAARA_EMERGENCY_ACK_SECONDS"] = "0.4"
    os.environ["SAHAARA_EMERGENCY_DEMO_ACK"] = "1"
    os.environ["SAHAARA_EMERGENCY_COOLDOWN_SECONDS"] = "0"
    os.environ["SAHAARA_EMERGENCY_RETRY_DELAY_SECONDS"] = "0.05"
    os.environ["SAHAARA_EMERGENCY_MAX_SECONDS"] = "30"
    os.environ["SAHAARA_EMERGENCY_SIM_DIAL_SECONDS"] = "0.03"
    os.environ["SAHAARA_EMERGENCY_SIM_RING_SECONDS"] = "0.03"
    return db_path


class FakeProvider:
    def complete_from_prompt_bundle(self, bundle, max_tokens=None):
        return SimpleNamespace(text="I'm right here with you.", model="fake")


def _drain():
    import telephony.orchestrator as orch_mod

    for _incident_id, orch in list(orch_mod._active_orchestrators.items()):
        orch.cancel()
        thread = getattr(orch, "_thread", None)
        if thread is not None:
            thread.join(timeout=3.0)
    with orch_mod._lock:
        orch_mod._active_orchestrators.clear()
        orch_mod._active_by_profile.clear()


def _reset_session(db_path):
    _drain()
    for suffix in ("", "-wal", "-shm"):
        Path(str(db_path) + suffix).unlink(missing_ok=True)
    os.environ["SAHAARA_DB_PATH"] = str(db_path)

    import database
    importlib.reload(database)
    database.init_db()

    import conversation
    importlib.reload(conversation)
    import memory.profile
    importlib.reload(memory.profile)
    import telephony.orchestrator
    importlib.reload(telephony.orchestrator)
    import telephony.provider
    importlib.reload(telephony.provider)

    conversation.provider = FakeProvider()
    conversation._session_manager._sessions.clear()
    conversation._session_manager._active_profile_id = None
    s = conversation._session_manager.get_active()
    s.event_log.clear()
    s.session_notes.reset()
    s.escalation_engine.reset()
    s.consent_manager.reset()
    s.continuation_manager.reset()
    return s, conversation


def _set_contacts():
    from database import get_profile, save_profile

    profile = get_profile("asha_demo_001")
    for member in profile["family"]:
        if (member.get("name") or "").lower() == "rohan":
            member["phone"] = "+919876543210"
            member["may_call"] = True
            member["notify_order"] = 1
    save_profile("asha_demo_001", profile)


# ---- 1. The wording table -------------------------------------------------

def test_every_state_has_one_sentence():
    print("\n[1] Every call state has exactly one sentence")
    from conversation import CALL_LINE_STATES, emergency_call_line_for_state

    expected_states = {"placed", "answered", "not_reached", "blocked", "simulated"}
    check("all states present", set(CALL_LINE_STATES) == expected_states, set(CALL_LINE_STATES))

    for state in expected_states:
        line = emergency_call_line_for_state(state, "Rohan")
        check(f"{state}: non-empty", bool(line.strip()), line)
        check(f"{state}: no template braces left", "{" not in line, line)
        check(f"{state}: names the contact", "Rohan" in line, line)

    check(
        "unknown state falls back to the safe blocked line",
        emergency_call_line_for_state("nonsense", "Rohan")
        == emergency_call_line_for_state("blocked", "Rohan"),
    )
    check(
        "a missing contact is never rendered as 'None'",
        "None" not in emergency_call_line_for_state("placed", None),
    )


def test_placed_and_answered_sentences():
    print("\n[2] The placed and answered sentences")
    from conversation import emergency_call_line_for_state

    placed = emergency_call_line_for_state("placed", "Rohan")
    check("placed says it is calling", "i'm calling rohan now" in placed.lower(), placed)
    check("placed reassures presence", "right here with you" in placed.lower(), placed)
    check("placed does not mention 112", "112" not in placed, placed)

    answered = emergency_call_line_for_state("answered", "Rohan")
    check("answered says he is on the line", "rohan is on the line" in answered.lower(), answered)
    check("answered does not mention 112", "112" not in answered, answered)
    check("answered makes no medical claim", "safe" not in answered.lower(), answered)


def test_non_claim_states_never_claim_a_call():
    print("\n[3] Simulated / blocked / not-reached never claim a call")
    from conversation import emergency_call_line_for_state

    for state in NON_CLAIM_STATES:
        line = emergency_call_line_for_state(state, "Rohan")
        claim = _contains_claim(line)
        check(f"{state}: no claim wording", claim is None, f"found {claim!r} in {line!r}")
        check(f"{state}: offers 112", "112" in line, line)
        # `not_reached` is the brief's wording and points the person at 112 and
        # someone nearby; the other two states are also the ones where the
        # dashboard is the only record, so they must say so.
        if state in {"simulated", "blocked"}:
            check(f"{state}: points at the dashboard", "dashboard" in line.lower(), line)
        check(f"{state}: no medical claim", "safe" not in line.lower(), line)
        check(f"{state}: no ambulance promise", "ambulance" not in line.lower(), line)
        check(f"{state}: no diagnosis", "diagnos" not in line.lower(), line)


def test_simulated_says_blocked_line():
    print("\n[4] Simulated mode says the blocked line (no 'demo' to Asha)")
    from conversation import emergency_call_line_for_state

    line = emergency_call_line_for_state("simulated", "Rohan")
    check("says dashboard", "dashboard" in line.lower(), line)
    check("offers 112", "112" in line, line)
    check("offers someone nearby", "someone nearby" in line.lower(), line)
    check("never says 'placed'", "placed" not in line.lower(), line)
    check("never says 'delivered'", "delivered" not in line.lower(), line)
    check("never says 'demo'", "demo" not in line.lower(), line)


def test_not_reached_offers_112_and_stays_present():
    print("\n[5] Not reached offers 112 and stays present")
    from conversation import emergency_call_line_for_state

    line = emergency_call_line_for_state("not_reached", "Rohan")
    check("says it could not reach", "couldn't reach rohan" in line.lower(), line)
    check("offers 112", "112" in line, line)
    check("offers someone nearby", "someone nearby" in line.lower(), line)
    check("stays present", "still here" in line.lower(), line)
    check("says 'yet', not 'never'", "yet" in line.lower(), line)


# ---- 2. The state chooser -------------------------------------------------

def test_chooser_prioritises_simulated():
    print("\n[6] A simulated provider can never produce the 'calling' line")
    from conversation import _emergency_call_line

    # Even with a (theoretically impossible) allowed gate, simulated wins -> blocked line
    line = _emergency_call_line(
        contact_name="Rohan",
        real_call_allowed=True,
        gate_reason="allowed",
        provider_simulated=True,
    )
    check("simulated + allowed -> blocked line", "dashboard" in line.lower(), line)
    check("no claim wording", _contains_claim(line) is None, line)
    check("never says 'demo'", "demo" not in line.lower(), line)

    # Real provider, gate refused -> blocked line
    line = _emergency_call_line(
        contact_name="Rohan",
        real_call_allowed=False,
        gate_reason="EMERGENCY_CALLS_ENABLED is not true",
        provider_simulated=False,
    )
    check("real + blocked -> dashboard line", "dashboard" in line.lower(), line)
    check("no claim wording", _contains_claim(line) is None, line)

    # Real provider, gate open -> the only state that may say "calling"
    line = _emergency_call_line(
        contact_name="Rohan",
        real_call_allowed=True,
        gate_reason="allowed",
        provider_simulated=False,
    )
    check("real + allowed -> calling line", "i'm calling rohan now" in line.lower(), line)


def test_chooser_reads_the_live_provider_when_unknown():
    print("\n[7] With no explicit provider flag, the live provider decides")
    _fresh_db()
    import database
    importlib.reload(database)
    database.init_db()

    import telephony.provider
    importlib.reload(telephony.provider)
    import conversation
    importlib.reload(conversation)

    os.environ["EMERGENCY_CALLS_ENABLED"] = "false"
    os.environ["SAHAARA_EMERGENCY_PROVIDER"] = "vonage"
    line = conversation._emergency_call_line("Rohan", True, "allowed")
    check("defaults to simulated when calling is off", "dashboard" in line.lower(), line)
    check("no claim wording", _contains_claim(line) is None, line)
    check("never says 'demo'", "demo" not in line.lower(), line)


# ---- 3. End to end --------------------------------------------------------

def test_disabled_campaign_word_is_honest():
    print("\n[8] Calling switched off: an honest reply, an incidence, and no call")
    s, conversation = _reset_session(_fresh_db())
    _set_contacts()
    os.environ["EMERGENCY_CALLS_ENABLED"] = "false"

    import telephony.orchestrator
    importlib.reload(telephony.orchestrator)

    result = conversation.process_turn("I can't breathe")
    reply = result.get("response", "")

    check("reply exists", bool(reply), reply)
    check("no claim wording in the reply", _contains_claim(reply) is None, reply)
    check("reply says dashboard", "dashboard" in reply.lower(), reply)
    check("reply offers 112", "112" in reply, reply)
    check("never says 'demo'", "demo" not in reply.lower(), reply)
    check("meta carries the deterministic line", bool(result.get("meta", {}).get("emergency_call_line")))
    check(
        "the deterministic line is the tail of the reply",
        reply.endswith(result["meta"]["emergency_call_line"]),
        reply,
    )
    check("tier is still red", result.get("meta", {}).get("tier") == "red")
    _drain()


def test_simulated_campaign_exhausts_honestly():
    print("\n[9] A simulated ladder that nobody answers ends honestly")
    s, conversation = _reset_session(_fresh_db())
    _set_contacts()
    os.environ["SAHAARA_EMERGENCY_DEMO_ACK"] = "0"

    result = conversation.process_turn("I can't breathe")
    reply = result.get("response", "")
    check("no claim wording", _contains_claim(reply) is None, reply)

    from events.event_log import EventType

    deadline = time.time() + 12
    ended = []
    while time.time() < deadline:
        ended = s.event_log.get_events(EventType.EMERGENCY_CALL_ENDED)
        if ended:
            break
        time.sleep(0.02)
    check("campaign ended", bool(ended), "no ENDED event")

    # Every attempt must be marked simulated and carry a masked number only
    from database import get_emergency_attempts, get_connection

    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM emergency_incidents ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        incident = dict(row) if row else None
    finally:
        conn.close()

    check("incident exists", incident is not None)
    if incident:
        attempts = get_emergency_attempts(incident["id"])
        check("at least one attempt", len(attempts) >= 1, len(attempts))
        check("every attempt simulated", all(a["simulated"] == 1 for a in attempts),
              [a["simulated"] for a in attempts])
        check(
            "every attempt shows a masked number",
            all("*" in (a["phone_masked"] or "") for a in attempts),
            [a["phone_masked"] for a in attempts],
        )
        check(
            "no full number is stored",
            all("+919876543210" not in str(a) for a in attempts),
        )
        check(
            "incident records that it was simulated",
            incident["simulated"] == 1,
            incident["simulated"],
        )
    _drain()


def test_112_not_leaked_into_ordinary_turns():
    print("\n[10] 112 is not offered on an ordinary turn")
    s, conversation = _reset_session(_fresh_db())
    _set_contacts()

    result = conversation.process_turn("It's a quiet afternoon, isn't it?")
    reply = result.get("response", "")
    check("no 112 on a calm turn", "112" not in reply, reply)
    check("no emergency line on a calm turn",
          not result.get("meta", {}).get("emergency_call_line"),
          result.get("meta", {}).get("emergency_call_line"))


def test_emergency_turn_has_no_go_on_checkin():
    print("\n[11] An emergency reply never asks 'Would you like me to go on?'")

    s, conversation = _reset_session(_fresh_db())
    _set_contacts()

    # The reply-length rule asks for this check-in after a short lead. It is
    # right for a factual answer and wrong on a symptom turn, where it invites
    # her to ask for more explanation instead of getting a person.
    class ChattyProvider:
        def complete_from_prompt_bundle(self, bundle, max_tokens=None):
            return SimpleNamespace(
                text=(
                    "I hear how scary this feels, I'm right here with you. "
                    "Please get Rohan right now. "
                    "Would you like me to go on?"
                ),
                model="fake",
            )

    conversation.provider = ChattyProvider()
    result = conversation.process_turn("I can't breathe")
    reply = result.get("response", "")

    check("the check-in is gone", "go on" not in reply.lower(), reply)
    check("the real reply survives", "right here with you" in reply.lower(), reply)
    check("the emergency line is still appended", "112" in reply, reply)
    check(
        "nothing is left dangling",
        not reply.strip().lower().endswith("more?"),
        reply,
    )
    _drain()

    # The helper itself: narrow, and it leaves ordinary prose alone.
    strip = conversation._strip_continuation_offer
    variants = [
        "Would you like me to go on?",
        "would you like me to go on",
        "Shall I go on?",
        "Should I continue?",
        "Do you want me to say more?",
    ]
    for variant in variants:
        check(
            f"strips: {variant!r}",
            strip(f"I'm here. {variant}").strip() == "I'm here.",
            strip(f"I'm here. {variant}"),
        )
    check("keeps an ordinary sentence", strip("I'm here with you.") == "I'm here with you.")
    check("keeps 'on' in normal prose", "go on a walk" in strip("Let's go on a walk."))
    check("empty stays empty", strip("") == "")
    check("a check-in mid-reply is removed too",
          "go on" not in strip("I'm here. Would you like me to go on? Tell me more.").lower(),
          strip("I'm here. Would you like me to go on? Tell me more."))


def main():
    print("=" * 74)
    print("  EMERGENCY CALL — WORDING TO ASHA (deterministic, never LLM-written)")
    print("=" * 74)

    test_every_state_has_one_sentence()
    test_placed_and_answered_sentences()
    test_non_claim_states_never_claim_a_call()
    test_simulated_says_blocked_line()
    test_not_reached_offers_112_and_stays_present()
    test_chooser_prioritises_simulated()
    test_chooser_reads_the_live_provider_when_unknown()
    test_disabled_campaign_word_is_honest()
    test_simulated_campaign_exhausts_honestly()
    test_112_not_leaked_into_ordinary_turns()
    test_emergency_turn_has_no_go_on_checkin()

    print("\n" + "=" * 74)
    print(f"  RESULTS: {passed} passed, {failed} failed")
    print("=" * 74)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
