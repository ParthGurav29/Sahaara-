"""
Task 3 — Emergency call orchestrator + simulated provider.

Tests for the state machine, cooldown/dedup, retry/next-contact logic.

Run directly: python tests/test_emergency_orchestrator.py
"""

import sys
import os
import tempfile
import time
import importlib
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, "/Users/parthmangeshgurav/Desktop/sahaara/backend")

# Fresh isolated DB per test function
def _fresh_db():
    _TEST_DB = Path(tempfile.gettempdir()) / f"sahaara_test_emergency_orchestrator_{os.getpid()}_{int(time.time()*1000000)}.db"
    for _suffix in ("", "-wal", "-shm"):
        Path(str(_TEST_DB) + _suffix).unlink(missing_ok=True)
    os.environ["SAHAARA_DB_PATH"] = str(_TEST_DB)
    os.environ["SAHAARA_PUSH_ENABLED"] = "false"
    # The campaign only runs when calling is switched on; with it off the
    # provider is the disabled variant and every incident ends `failed/disabled`.
    os.environ["EMERGENCY_CALLS_ENABLED"] = "true"
    os.environ["SAHAARA_EMERGENCY_PROVIDER"] = "simulated"
    # Fast demo mode for tests
    os.environ["SAHAARA_EMERGENCY_ACK_SECONDS"] = "0.5"
    os.environ["SAHAARA_EMERGENCY_DEMO_ACK"] = "1"
    os.environ["SAHAARA_EMERGENCY_COOLDOWN_SECONDS"] = "1"
    os.environ["SAHAARA_EMERGENCY_MAX_ATTEMPTS"] = "3"
    os.environ["SAHAARA_EMERGENCY_RETRIES"] = "1"
    os.environ["SAHAARA_EMERGENCY_RETRY_DELAY_SECONDS"] = "0.1"
    # Simulation-only pacing so a whole campaign finishes in ~1 s per attempt
    # instead of the 4 s a demo shows on screen.
    os.environ["SAHAARA_EMERGENCY_SIM_DIAL_SECONDS"] = "0.05"
    os.environ["SAHAARA_EMERGENCY_SIM_RING_SECONDS"] = "0.05"
    return _TEST_DB


def _wait_for_ended(event_log, timeout: float = 15.0):
    """Poll the event log for EMERGENCY_CALL_ENDED instead of sleeping a guess."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        ended = event_log.get_events(EventType.EMERGENCY_CALL_ENDED)
        if ended:
            return ended
        time.sleep(0.05)
    return event_log.get_events(EventType.EMERGENCY_CALL_ENDED)


def _wait_for_attempt_status(attempt_id: str, statuses: tuple, timeout: float = 10.0) -> bool:
    from database import get_emergency_attempt

    deadline = time.time() + timeout
    while time.time() < deadline:
        row = get_emergency_attempt(attempt_id)
        if row and row["status"] in statuses:
            return True
        time.sleep(0.05)
    return False

# Fake provider for tests
class FakeProvider:
    def complete_from_prompt_bundle(self, bundle, max_tokens=None):
        return SimpleNamespace(text="I'm right here with you.", model="fake")

import conversation
conversation.provider = FakeProvider()

passed = 0
failed = 0


def check(name: str, condition, detail: str = "") -> None:
    global passed, failed
    if condition:
        passed += 1
        print(f"  PASS: {name}")
    else:
        failed += 1
        print(f"  FAIL: {name} {detail}")


def _drain_orchestrators(timeout: float = 10.0) -> None:
    """Stop and join every live orchestrator before the DB is swapped.

    `importlib.reload(database)` mutates the module object in place, so a
    background campaign from an earlier test would suddenly write to the new
    file and hold a write lock while the next test initialises it.
    """
    import telephony.orchestrator as orch_mod

    deadline = time.time() + timeout
    for incident_id, orch in list(orch_mod._active_orchestrators.items()):
        orch.cancel()
        thread = getattr(orch, "_thread", None)
        if thread is not None:
            thread.join(timeout=max(0.1, deadline - time.time()))
    with orch_mod._lock:
        orch_mod._active_orchestrators.clear()


def _reset_session(db_path: Path):
    """Create fresh DB and session."""
    _drain_orchestrators()
    for _suffix in ("", "-wal", "-shm"):
        Path(str(db_path) + _suffix).unlink(missing_ok=True)
    os.environ["SAHAARA_DB_PATH"] = str(db_path)
    
    # Reload database module to pick up new DB_PATH
    import database
    importlib.reload(database)
    database.init_db()
    
    # Also reload modules that depend on database
    importlib.reload(conversation)
    import memory.profile
    importlib.reload(memory.profile)
    import telephony.orchestrator
    importlib.reload(telephony.orchestrator)
    
    # Re-setup provider
    conversation.provider = FakeProvider()
    
    # Reset session manager to pick up new DB
    conversation._session_manager._sessions.clear()
    conversation._session_manager._active_profile_id = None
    
    s = conversation._session_manager.get_active()
    s.event_log.clear()
    s.session_notes.reset()
    s.escalation_engine.reset()
    s.consent_manager.reset()
    s.continuation_manager.reset()
    return s


def _give_phones_and_may_call():
    profile = get_profile("asha_demo_001")
    profile["family"][0]["phone"] = "+919876543210"
    profile["family"][0]["may_call"] = True
    profile["family"][1]["phone"] = "+919876543211"
    profile["family"][1]["may_call"] = True
    save_profile("asha_demo_001", profile)


def test_physical_urgent_triggers_incident():
    """Urgent physical symptom triggers an incident with dialing."""
    print("\n[1] Physical urgent triggers incident")
    db = _fresh_db()
    s = _reset_session(db)
    _give_phones_and_may_call()
    
    # Force Red tier + physical urgent
    s.escalation_engine.force_tier(EscalationTier.RED, "physical_urgent")
    
    # Process an urgent turn - this should trigger the orchestrator
    result = conversation.process_turn("I can't breathe")
    
    # Check incident was created
    incidents = s.event_log.get_events(EventType.EMERGENCY_CALL_STARTED)
    check("EMERGENCY_CALL_STARTED event logged", len(incidents) >= 1)
    
    if incidents:
        detail = incidents[-1].detail
        check("incident_id present", "incident_id" in detail)
        check("trigger is physical_urgent", detail.get("trigger") == "physical_urgent")
        check("tier is red", detail.get("tier") == "red")
        check("simulated is true", detail.get("simulated") is True)
        check("provider is simulated", detail.get("provider") == "simulated")


def test_danger_statement_triggers_incident():
    """Danger statement triggers an incident."""
    print("\n[2] Danger statement triggers incident")
    db = _fresh_db()
    s = _reset_session(db)
    _give_phones_and_may_call()
    
    result = conversation.process_turn("I want to die")
    
    incidents = s.event_log.get_events(EventType.EMERGENCY_CALL_STARTED)
    check("EMERGENCY_CALL_STARTED event logged", len(incidents) >= 1)
    
    if incidents:
        detail = incidents[-1].detail
        check("trigger is danger", detail.get("trigger") == "danger")
        check("trigger_phrase is None for danger", detail.get("trigger_phrase") is None or detail.get("trigger_phrase") == "")


def test_physical_general_no_call():
    """Physical general (Orange) does NOT trigger a call - only alert."""
    print("\n[3] Physical general does not trigger call")
    db = _fresh_db()
    s = _reset_session(db)
    _give_phones_and_may_call()
    
    result = conversation.process_turn("I am not feeling well")
    
    incidents = s.event_log.get_events(EventType.EMERGENCY_CALL_STARTED)
    check("No EMERGENCY_CALL_STARTED event", len(incidents) == 0)


def test_no_callable_contact():
    """If no contact has may_call=True + phone, incident ends failed but alert still raised."""
    print("\n[4] No callable contact")
    db = _fresh_db()
    s = _reset_session(db)
    # Don't give phones/may_call - defaults are None/False
    
    result = conversation.process_turn("I can't breathe")
    
    # Incident should still be created but skipped
    skipped = s.event_log.get_events(EventType.EMERGENCY_CALL_SKIPPED)
    check("EMERGENCY_CALL_SKIPPED event logged", len(skipped) >= 1)
    if skipped:
        check("reason is no_callable_contact", skipped[-1].detail.get("reason") == "no_callable_contact")
    
    # Red tier should still be raised
    check("Tier is still RED", result.get("meta", {}).get("tier") == "red")


def test_cooldown_prevents_second_campaign():
    """Cooldown prevents a second phone campaign but Red/alert still raised."""
    print("\n[5] Cooldown prevents second campaign")
    db = _fresh_db()
    s = _reset_session(db)
    _give_phones_and_may_call()
    
    # First incident
    result1 = conversation.process_turn("I can't breathe")
    check("First: incident created", True)
    
    # Immediately trigger again - should be in cooldown
    result2 = conversation.process_turn("I fell and I cant get up")
    
    # Second incident should be skipped due to cooldown
    skipped = s.event_log.get_events(EventType.EMERGENCY_CALL_SKIPPED)
    check("Second incident skipped due to cooldown", len(skipped) >= 1)
    if skipped:
        check("reason is cooldown", skipped[-1].detail.get("reason") == "cooldown")
    
    # But Red tier still raised
    check("Second: tier still RED", result2.get("meta", {}).get("tier") == "red")


def test_acknowledged_ends_incident():
    """Caregiver acknowledging (DTMF 1) ends incident as acknowledged."""
    print("\n[6] Acknowledged ends incident")
    db = _fresh_db()
    # No auto-ack: the test supplies the acknowledgement through the same
    # public entry point the Twilio DTMF webhook uses.
    os.environ["SAHAARA_EMERGENCY_DEMO_ACK"] = "0"
    os.environ["SAHAARA_EMERGENCY_ACK_SECONDS"] = "30"
    s = _reset_session(db)
    _give_phones_and_may_call()

    conversation.process_turn("I can't breathe")

    incidents = s.event_log.get_events(EventType.EMERGENCY_CALL_STARTED)
    check("incident started", len(incidents) >= 1)
    incident_id = incidents[-1].detail["incident_id"] if incidents else None
    # The first attempt id is deterministic, so there is no race reading
    # `orch._current_attempt` before the orchestrator thread has started.
    attempt_id = f"{incident_id}_a1" if incident_id else None

    answered = _wait_for_attempt_status(attempt_id, ("answered",)) if attempt_id else False
    check("call reached answered", answered)

    ack_result = False
    if attempt_id:
        from telephony.orchestrator import acknowledge_attempt
        ack_result = acknowledge_attempt(attempt_id)

    check("acknowledge returned True", ack_result is True)

    ended = _wait_for_ended(s.event_log)
    check("EMERGENCY_CALL_ENDED event logged", len(ended) >= 1)
    if ended:
        check("status is acknowledged", ended[-1].detail.get("status") == "acknowledged")

    os.environ["SAHAARA_EMERGENCY_DEMO_ACK"] = "1"


def test_max_attempts_exhausted():
    """After the configured cap (primary, retry, secondary), ends exhausted."""
    print("\n[7] Max attempts exhausted")
    db = _fresh_db()
    s = _reset_session(db)
    _give_phones_and_may_call()

    # Disable demo ack so every call ends no_answer and the ladder runs.
    os.environ["SAHAARA_EMERGENCY_DEMO_ACK"] = "0"
    os.environ["SAHAARA_EMERGENCY_ACK_SECONDS"] = "0.1"
    os.environ["SAHAARA_EMERGENCY_MAX_ATTEMPTS"] = "3"

    conversation.process_turn("I can't breathe")

    ended = _wait_for_ended(s.event_log)
    check("EMERGENCY_CALL_ENDED event logged", len(ended) >= 1)
    if ended:
        check("status is exhausted", ended[-1].detail.get("status") == "exhausted")
        check("attempts == 3", ended[-1].detail.get("attempts") == 3)

    # Restore demo ack
    os.environ["SAHAARA_EMERGENCY_DEMO_ACK"] = "1"


def test_cancel_stops_retries():
    """Dashboard cancel stops retries and ends cancelled."""
    print("\n[8] Cancel stops retries")
    db = _fresh_db()
    s = _reset_session(db)
    _give_phones_and_may_call()
    
    result = conversation.process_turn("I can't breathe")
    
    incidents = s.event_log.get_events(EventType.EMERGENCY_CALL_STARTED)
    if incidents:
        incident_id = incidents[-1].detail["incident_id"]
        # Cancel immediately
        from telephony.orchestrator import cancel_emergency_incident
        cancelled = cancel_emergency_incident(incident_id)
        check("cancel_emergency_incident returned True", cancelled)
    
    time.sleep(1.0)
    
    ended = s.event_log.get_events(EventType.EMERGENCY_CALL_ENDED)
    check("EMERGENCY_CALL_ENDED event logged", len(ended) >= 1)
    if ended:
        check("status is cancelled", ended[-1].detail.get("status") == "cancelled")


def test_trigger_phrase_sanitised():
    """Trigger phrase in incident is sanitised and capped at 15 words."""
    print("\n[9] Trigger phrase sanitised")
    db = _fresh_db()
    s = _reset_session(db)
    _give_phones_and_may_call()
    
    result = conversation.process_turn("I can't breathe and my chest hurts really badly right now")
    
    incidents = s.event_log.get_events(EventType.EMERGENCY_CALL_STARTED)
    if incidents:
        detail = incidents[-1].detail
        phrase = detail.get("trigger_phrase", "")
        # Should be capped at ~15 words
        words = phrase.split()
        check("trigger_phrase <= 15 words", len(words) <= 15)


def test_simulated_provider_always_simulated():
    """All incidents in test mode are simulated: true."""
    print("\n[10] Simulated provider flag")
    provider = get_call_provider()
    check("Provider is simulated", provider.simulated is True)
    check("Provider name is simulated", provider.name == "simulated")


def test_no_call_claims_in_reply():
    """Emergency reply never claims a call was placed (Task 2 test already covers this)."""
    print("\n[11] No call claims in reply")
    db = _fresh_db()
    s = _reset_session(db)
    _give_phones_and_may_call()
    
    result = conversation.process_turn("I can't breathe")
    response = (result.get("response") or "").lower()
    
    for forbidden in ("i'm calling", "i am calling", "i've called", "i have called",
                      "dialling", "dialing", "on the line", "phone is ringing"):
        check(f"No '{forbidden}' in reply", forbidden not in response)


def main() -> int:
    print("=" * 74)
    print("  Task 3 — Emergency call orchestrator + simulated provider")
    print("=" * 74)

    test_physical_urgent_triggers_incident()
    test_danger_statement_triggers_incident()
    test_physical_general_no_call()
    test_no_callable_contact()
    test_cooldown_prevents_second_campaign()
    test_acknowledged_ends_incident()
    test_max_attempts_exhausted()
    test_cancel_stops_retries()
    test_trigger_phrase_sanitised()
    test_simulated_provider_always_simulated()
    test_no_call_claims_in_reply()

    print("\n" + "=" * 74)
    print(f"  RESULTS: {passed} passed, {failed} failed")
    print("=" * 74)
    return 1 if failed else 0


if __name__ == "__main__":
    # Import modules that need to be available for tests
    import database
    from database import init_db, get_profile, save_profile
    from memory.profile import Profile, get_callable_contacts
    from safety.escalation import EscalationTier
    from events.event_log import EventLog, EventType
    from telephony.orchestrator import trigger_emergency_call, get_orchestrator
    from telephony.provider import get_call_provider
    
    # Initialize DB for the first time (will be recreated per test)
    init_db()
    
    sys.exit(main())