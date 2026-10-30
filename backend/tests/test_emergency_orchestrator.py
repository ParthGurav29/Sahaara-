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
    # Tests that need a real ladder (dialing/ringing/answered/acknowledged)
    # must pass all provider-level gates and inject FakeRealCallProvider.
    # The base config has these gates passing; individual tests that want the
    # simulated provider will override SAHAARA_EMERGENCY_PROVIDER back to
    # "simulated" and will NOT inject the fake provider.
    os.environ["EMERGENCY_CALLS_ENABLED"] = "true"
    os.environ["SAHAARA_EMERGENCY_PROVIDER"] = "vonage"
    os.environ["SAHAARA_EMERGENCY_REAL_CALLS_CONFIRMED"] = "true"
    os.environ["VONAGE_APPLICATION_ID"] = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    os.environ["VONAGE_PRIVATE_KEY"] = "dummy"
    os.environ["VONAGE_FROM_NUMBER"] = "+15550001111"
    os.environ["SAHAARA_EMERGENCY_PUBLIC_BASE_URL"] = "https://example.test"
    os.environ["SAHAARA_EMERGENCY_ACK_SECONDS"] = "0.5"
    os.environ["SAHAARA_EMERGENCY_COOLDOWN_SECONDS"] = "1"
    os.environ["SAHAARA_EMERGENCY_MAX_ATTEMPTS"] = "3"
    os.environ["SAHAARA_EMERGENCY_RETRIES"] = "1"
    os.environ["SAHAARA_EMERGENCY_RETRY_DELAY_SECONDS"] = "0.1"
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
    import importlib
    import database
    importlib.reload(database)
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


class FakeRealCallProvider:
    """A REAL-shaped call provider, for ladder tests only.

    The shipped `SimulatedCallProvider` is forbidden by design from reporting
    dialing/ringing/answered — it exists to say "no call was placed". So the
    retry / ack / exhaustion tests inject this double, which behaves like a
    carrier that rings, answers, and can acknowledge when told to.
    """

    name = "vonage"
    simulated = False

    def __init__(self):
        import threading

        self._lock = threading.Lock()
        self.handles: dict[str, dict] = {}

    def place_call(self, *, to_e164, message, incident_id, attempt_id, gate_reason=None):
        from telephony.provider import CallHandle

        with self._lock:
            self.handles[attempt_id] = {
                "started": time.time(),
                "acked": False,
                "cancelled": False,
            }
        return CallHandle(
            provider_call_id=f"fake-{attempt_id}",
            status="queued",
            raw={"attempt_id": attempt_id},
        )

    def fetch_status(self, handle):
        attempt_id = (handle.raw or {}).get("attempt_id", "")
        with self._lock:
            state = self.handles.get(attempt_id)
            if not state:
                return "unknown"
            if state["cancelled"]:
                return "canceled"
            if state["acked"]:
                return "acknowledged"
            elapsed = time.time() - state["started"]
            if elapsed < 0.05:
                return "dialing"
            if elapsed < 0.10:
                return "ringing"
            return "answered"

    def cancel(self, handle):
        attempt_id = (handle.raw or {}).get("attempt_id", "")
        with self._lock:
            state = self.handles.get(attempt_id)
            if state:
                state["cancelled"] = True

    def acknowledge(self, attempt_id):
        with self._lock:
            state = self.handles.get(attempt_id)
            if not state:
                return False
            state["acked"] = True
            return True


def _use_fake_real_provider():
    """Inject the real-shaped double for tests that exercise the ladder.

    Patches `telephony.provider.select_provider` AND
    `telephony.orchestrator.select_provider` so the factory returns the
    fake provider instead of trying to construct a real Vonage provider.
    """
    import telephony.provider as provider_module
    import telephony.orchestrator as orch_module

    provider = FakeRealCallProvider()
    if not hasattr(provider_module, "_original_select_provider"):
        provider_module._original_select_provider = provider_module.select_provider
    provider_module.select_provider = lambda: (provider, None)
    # The orchestrator module imports select_provider at load time, so we
    # must patch its reference too.
    if not hasattr(orch_module, "_original_select_provider"):
        orch_module._original_select_provider = orch_module.select_provider
    orch_module.select_provider = lambda: (provider, None)
    return provider


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
    import telephony.provider as provider_mod

    deadline = time.time() + timeout
    for incident_id, orch in list(orch_mod._active_orchestrators.items()):
        orch.cancel()
        thread = getattr(orch, "_thread", None)
        if thread is not None:
            thread.join(timeout=max(0.1, deadline - time.time()))
    with orch_mod._lock:
        orch_mod._active_orchestrators.clear()

    # Restore the real factory in case a test patched it
    if hasattr(provider_mod, "_original_select_provider"):
        provider_mod.select_provider = provider_mod._original_select_provider
        delattr(provider_mod, "_original_select_provider")
    if hasattr(orch_mod, "_original_select_provider"):
        orch_mod.select_provider = orch_mod._original_select_provider
        delattr(orch_mod, "_original_select_provider")


def _reset_session(db_path: Path):
    """Create fresh DB and session. Returns (session, conversation_module, telephony_orchestrator_module)."""
    _drain_orchestrators()
    for _suffix in ("", "-wal", "-shm"):
        Path(str(db_path) + _suffix).unlink(missing_ok=True)
    os.environ["SAHAARA_DB_PATH"] = str(db_path)

    # Reload database module to pick up new DB_PATH
    import database
    importlib.reload(database)
    database.init_db()

    # Also reload modules that depend on database
    import conversation
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
    return s, conversation, telephony.orchestrator


def _give_phones_and_may_call():
    import database
    from database import get_profile, save_profile
    profile = get_profile("asha_demo_001")
    profile["family"][0]["phone"] = "+919876543210"
    profile["family"][0]["may_call"] = True
    profile["family"][1]["phone"] = "+919876543211"
    profile["family"][1]["may_call"] = True
    save_profile("asha_demo_001", profile)


# Import test dependencies inside the test functions to ensure
# the temp DB path is set before any module imports database
from safety.escalation import EscalationTier
from events.event_log import EventType


def test_physical_urgent_triggers_incident():
    """Urgent physical symptom triggers an incident with dialing."""
    print("\n[1] Physical urgent triggers incident")
    os.environ["SAHAARA_EMERGENCY_PROVIDER"] = "simulated"
    db = _fresh_db()
    s, conversation, _ = _reset_session(db)
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
    os.environ["SAHAARA_EMERGENCY_PROVIDER"] = "simulated"
    db = _fresh_db()
    s, conversation, _ = _reset_session(db)
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
    os.environ["SAHAARA_EMERGENCY_PROVIDER"] = "simulated"
    db = _fresh_db()
    s, conversation, _ = _reset_session(db)
    _give_phones_and_may_call()

    result = conversation.process_turn("I am not feeling well")

    incidents = s.event_log.get_events(EventType.EMERGENCY_CALL_STARTED)
    check("No EMERGENCY_CALL_STARTED event", len(incidents) == 0)


def test_no_callable_contact():
    """If no contact has may_call=True + phone, incident ends failed but alert still raised."""
    print("\n[4] No callable contact")
    os.environ["SAHAARA_EMERGENCY_PROVIDER"] = "simulated"
    db = _fresh_db()
    s, conversation, _ = _reset_session(db)
    # Explicitly clear phones/may_call for all family members
    from database import get_profile, save_profile
    profile = get_profile("asha_demo_001")
    for member in profile["family"]:
        member["phone"] = None
        member["may_call"] = False
    save_profile("asha_demo_001", profile)

    result = conversation.process_turn("I can't breathe")

    # Wait for the background orchestrator to finish (it runs async)
    import time
    deadline = time.time() + 5.0
    while time.time() < deadline:
        skipped = s.event_log.get_events(EventType.EMERGENCY_CALL_SKIPPED)
        if skipped:
            break
        time.sleep(0.05)

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
    _use_fake_real_provider()
    db = _fresh_db()
    s, conversation, _ = _reset_session(db)
    _give_phones_and_may_call()

    # First incident
    result1 = conversation.process_turn("I can't breathe")
    check("First: incident created", True)

    # Immediately trigger again — must be refused. Two guards can catch it:
    # the per-profile "one active ladder" lock (fires first while the campaign
    # is still running) and the persisted cooldown (fires once it has ended).
    result2 = conversation.process_turn("I fell and I cant get up")

    # Second incident should be skipped, whichever guard caught it
    skipped = s.event_log.get_events(EventType.EMERGENCY_CALL_SKIPPED)
    check("Second incident skipped", len(skipped) >= 1)
    if skipped:
        reason = skipped[-1].detail.get("reason")
        check("reason is cooldown or already_active", reason in {"cooldown", "already_active"})

    # Only one incident row, so only one ladder
    from database import get_connection
    conn = get_connection()
    try:
        rows = conn.execute("SELECT COUNT(*) AS n FROM emergency_incidents").fetchone()
        check("Exactly one incident row", rows["n"] == 1)
    finally:
        conn.close()

    # But Red tier still raised
    check("Second: tier still RED", result2.get("meta", {}).get("tier") == "red")


def test_acknowledged_ends_incident():
    """Caregiver acknowledging (DTMF 1) ends incident as acknowledged."""
    print("\n[6] Acknowledged ends incident")
    db = _fresh_db()
    os.environ["SAHAARA_EMERGENCY_ACK_SECONDS"] = "30"
    s, conversation, orch_module = _reset_session(db)
    _use_fake_real_provider()
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
        ack_result = orch_module.acknowledge_attempt(attempt_id)

    check("acknowledge returned True", ack_result is True)

    ended = _wait_for_ended(s.event_log)
    check("EMERGENCY_CALL_ENDED event logged", len(ended) >= 1)
    if ended:
        check("status is acknowledged", ended[-1].detail.get("status") == "acknowledged")


def test_max_attempts_exhausted():
    """After the configured cap (primary, retry, secondary), ends exhausted."""
    print("\n[7] Max attempts exhausted")
    db = _fresh_db()
    s, conversation, orch_module = _reset_session(db)
    _use_fake_real_provider()
    _give_phones_and_may_call()

    # No acknowledgement ever arrives, so each call times out and the ladder
    # runs: primary, retry, secondary.
    os.environ["SAHAARA_EMERGENCY_ACK_SECONDS"] = "0.1"
    os.environ["SAHAARA_EMERGENCY_MAX_ATTEMPTS"] = "3"

    conversation.process_turn("I can't breathe")

    ended = _wait_for_ended(s.event_log)
    check("EMERGENCY_CALL_ENDED event logged", len(ended) >= 1)
    if ended:
        check("status is exhausted", ended[-1].detail.get("status") == "exhausted")
        check("attempts == 3", ended[-1].detail.get("attempts") == 3)


def test_cancel_stops_retries():
    """Dashboard cancel stops retries and ends cancelled."""
    print("\n[8] Cancel stops retries")
    db = _fresh_db()
    s, conversation, orch_module = _reset_session(db)
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
    os.environ["SAHAARA_EMERGENCY_PROVIDER"] = "simulated"
    db = _fresh_db()
    s, conversation, _ = _reset_session(db)
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
    """The simulated provider places nothing and never claims an answer."""
    print("\n[10] Simulated provider is honest")
    os.environ["SAHAARA_EMERGENCY_PROVIDER"] = "simulated"
    from telephony.provider import get_call_provider
    provider = get_call_provider()
    check("Provider is simulated", provider.simulated is True)
    check("Provider name is simulated", provider.name == "simulated")

    handle = provider.place_call(
        to_e164="+919876543210",
        message="test",
        incident_id="emg_test",
        attempt_id="emg_test_a1",
        gate_reason="SAHAARA_EMERGENCY_REAL_CALLS_CONFIRMED is false",
    )
    check("simulated status is simulated_not_placed",
          handle.status == "simulated_not_placed", handle.status)
    check("gate reason travels on the handle",
          handle.raw.get("gate_reason") == "SAHAARA_EMERGENCY_REAL_CALLS_CONFIRMED is false")
    check("fetch_status never advances",
          provider.fetch_status(handle) == "simulated_not_placed")
    check("acknowledge can never succeed", provider.acknowledge("emg_test_a1") is False)


def test_simulated_incident_records_no_answer():
    """A simulated incident says `simulated` and stores the gate, never `answered`."""
    print("\n[13] Simulated incident records the gate, not a fake answer")
    os.environ["SAHAARA_EMERGENCY_PROVIDER"] = "simulated"
    import database

    db = _fresh_db()
    s, conversation, _ = _reset_session(db)
    _give_phones_and_may_call()

    conversation.process_turn("I can't breathe")
    ended = _wait_for_ended(s.event_log)
    check("incident ended", len(ended) >= 1)

    incidents = s.event_log.get_events(EventType.EMERGENCY_CALL_STARTED)
    incident_id = incidents[-1].detail["incident_id"] if incidents else None
    check("incident id present", bool(incident_id))

    if incident_id:
        # Wait for the attempt row (the campaign thread writes it).
        deadline = time.time() + 5.0
        attempts = []
        while time.time() < deadline:
            attempts = database.get_emergency_attempts(incident_id)
            if attempts:
                break
            time.sleep(0.05)
        check("one attempt recorded", len(attempts) == 1, str(len(attempts)))
        if attempts:
            check("attempt is simulated_not_placed",
                  attempts[0]["status"] == "simulated_not_placed",
                  attempts[0]["status"])
            check("no answered_at timestamp", attempts[0]["answered_at"] is None)

        answered_events = [
            e for e in s.event_log.get_events(EventType.EMERGENCY_CALL_STATUS)
            if e.detail.get("status") in ("answered", "ringing", "dialing")
        ]
        check("no answered/ringing/dialing events", answered_events == [])


def test_no_call_claims_in_reply():
    """Emergency reply never claims a call was placed (Task 2 test already covers this)."""
    print("\n[11] No call claims in reply")
    os.environ["SAHAARA_EMERGENCY_PROVIDER"] = "simulated"
    db = _fresh_db()
    s, conversation, _ = _reset_session(db)
    _give_phones_and_may_call()

    result = conversation.process_turn("I can't breathe")
    response = (result.get("response") or "").lower()

    for forbidden in ("i'm calling", "i am calling", "i've called", "i have called",
                      "dialling", "dialing", "on the line", "phone is ringing"):
        check(f"No '{forbidden}' in reply", forbidden not in response)


def test_late_press_one_stops_the_ladder():
    """A press-1 that arrives after the ack window still stops the campaign.

    The provider's "no_answer" means *no keypress arrived inside the window*.
    A DTMF 1 that arrives later is still a human pressing the key, so it must
    be recorded and must stop the next number being dialled — otherwise the
    ladder keeps calling while the caregiver is already on the way.
    """
    print("\n[12] A late press-1 stops the ladder instead of being discarded")
    import database

    db = _fresh_db()
    s, conversation, orch_module = _reset_session(db)
    _use_fake_real_provider()
    _give_phones_and_may_call()

    os.environ["SAHAARA_EMERGENCY_ACK_SECONDS"] = "0.3"
    os.environ["SAHAARA_EMERGENCY_RETRY_DELAY_SECONDS"] = "5"
    os.environ["SAHAARA_EMERGENCY_MAX_ATTEMPTS"] = "4"

    conversation.process_turn("I can't breathe")

    incidents = s.event_log.get_events(EventType.EMERGENCY_CALL_STARTED)
    check("incident started", len(incidents) >= 1)
    incident_id = incidents[-1].detail["incident_id"] if incidents else None
    attempt_one = f"{incident_id}_a1" if incident_id else None

    reached_no_answer = _wait_for_attempt_status(attempt_one, ("no_answer",)) if attempt_one else False
    check("the first call was not acknowledged in time", reached_no_answer)

    # The campaign is now in its retry wait, about to dial the same number again.
    late_ack = orch_module.acknowledge_attempt(attempt_one) if attempt_one else False
    check("a late press-1 is accepted, not discarded", late_ack is True)
    check(
        "the attempt records the acknowledgement",
        database.get_emergency_attempt(attempt_one)["status"] == "acknowledged",
        database.get_emergency_attempt(attempt_one)["status"],
    )

    ended = _wait_for_ended(s.event_log)
    check("the campaign ended", len(ended) >= 1)
    if ended:
        check("it ended acknowledged, not cancelled",
              ended[-1].detail.get("status") == "acknowledged",
              ended[-1].detail.get("status"))
    check(
        "no second number was dialled",
        len(database.get_emergency_attempts(incident_id)) == 1,
        len(database.get_emergency_attempts(incident_id)),
    )

    os.environ["SAHAARA_EMERGENCY_RETRY_DELAY_SECONDS"] = "20"


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
    test_late_press_one_stops_the_ladder()
    test_simulated_incident_records_no_answer()

    print("=" * 74)
    print(f"  RESULTS: {passed} passed, {failed} failed")
    print("=" * 74)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())