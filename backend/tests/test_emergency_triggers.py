"""
Emergency Call — triggers, gate enforcement, idempotency, ladder and wording.

Covers the brief's deterministic trigger set:
  1. explicit request ("call Rohan") and consent-accept after an offer
  2. Red (danger / physical urgent) immediately, same turn
  3. Orange after the dwell window, on a background tick with no user turn
  5. Yellow never auto-calls
and the guarantees around them: one ladder per incident, no double dial, a
step-down before the dwell cancels, Rohan -> Priya -> exhausted, and wording
that never claims a call that did not happen.

Run directly: python tests/test_emergency_triggers.py
"""

import sys
import os
import time
import asyncio
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


# ---- Isolation helpers ---------------------------------------------------

def _fresh_db():
    db_path = Path(tempfile.gettempdir()) / (
        f"sahaara_test_triggers_{os.getpid()}_{int(time.time() * 1000000)}.db"
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
    os.environ["SAHAARA_EMERGENCY_COOLDOWN_SECONDS"] = "1"
    os.environ["SAHAARA_EMERGENCY_MAX_ATTEMPTS"] = "4"
    os.environ["SAHAARA_EMERGENCY_RETRY_DELAY_SECONDS"] = "0.05"
    os.environ["SAHAARA_EMERGENCY_MAX_SECONDS"] = "30"
    return db_path


class FakeProvider:
    def complete_from_prompt_bundle(self, bundle, max_tokens=None):
        return SimpleNamespace(text="I'm right here with you.", model="fake")


def _drain_orchestrators(timeout=8.0):
    import telephony.orchestrator as orch_mod

    deadline = time.time() + timeout
    for _incident_id, orch in list(orch_mod._active_orchestrators.items()):
        orch.cancel()
        thread = getattr(orch, "_thread", None)
        if thread is not None:
            thread.join(timeout=max(0.05, deadline - time.time()))
    with orch_mod._lock:
        orch_mod._active_orchestrators.clear()
        orch_mod._active_by_profile.clear()


def _reset_session(db_path):
    _drain_orchestrators()
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


def _set_contacts(rohan_phone="+919876543210", priya_phone="+919876543211"):
    from database import get_profile, save_profile

    profile = get_profile("asha_demo_001")
    for member in profile["family"]:
        name = (member.get("name") or "").lower()
        if name == "rohan":
            member["phone"] = rohan_phone
            member["may_call"] = True
            member["notify_order"] = 1
        elif name == "priya":
            member["phone"] = priya_phone
            member["may_call"] = True
            member["notify_order"] = 2
    save_profile("asha_demo_001", profile)


def _wait_for_ended(event_log, timeout=12.0):
    from events.event_log import EventType

    deadline = time.time() + timeout
    while time.time() < deadline:
        ended = event_log.get_events(EventType.EMERGENCY_CALL_ENDED)
        if ended:
            return ended
        time.sleep(0.02)
    return event_log.get_events(EventType.EMERGENCY_CALL_ENDED)


def _incident_count():
    from database import get_connection

    conn = get_connection()
    try:
        return conn.execute("SELECT COUNT(*) AS n FROM emergency_incidents").fetchone()["n"]
    finally:
        conn.close()


def _latest_incident():
    from database import get_connection

    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM emergency_incidents ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


# ---- 1. Triggers ---------------------------------------------------------

def test_explicit_request_triggers():
    print("\n[1] An explicit request triggers a call")
    s, conversation = _reset_session(_fresh_db())
    _set_contacts()

    result = conversation.process_turn("Can you call Rohan please?")

    from events.event_log import EventType

    started = s.event_log.get_events(EventType.EMERGENCY_CALL_STARTED)
    check("incident started", len(started) == 1, f"got {len(started)}")
    if started:
        check("trigger is explicit_request", started[-1].detail.get("trigger") == "explicit_request")
        check(
            "phrase keeps her own words",
            "call rohan" in (started[-1].detail.get("trigger_phrase") or "").lower(),
            started[-1].detail,
        )
    check("exactly one incident row", _incident_count() == 1)
    check("reply exists", bool(result.get("response")))
    check(
        "reply never claims a live call in simulated mode",
        "i'm calling" not in result.get("response", "").lower(),
        result.get("response", ""),
    )
    check(
        "simulated wording says dashboard (no demo to Asha)",
        "dashboard" in result.get("response", "").lower(),
        result.get("response", ""),
    )
    check(
        "simulated wording never says demo",
        "demo" not in result.get("response", "").lower(),
        result.get("response", ""),
    )


def test_consent_accept_triggers():
    print("\n[2] Accepting Sahaara's offer triggers a call")
    s, conversation = _reset_session(_fresh_db())
    _set_contacts()

    # Put the consent manager in the PENDING state an offer would create
    s.consent_manager.ask_consent(
        contact_name="Rohan",
        contact_relationship="son",
        tier=__import__("safety.escalation", fromlist=["EscalationTier"]).EscalationTier.YELLOW,
        question_text="Would you like me to call Rohan?",
    )

    result = conversation.process_turn("Yes please, that would help.")

    from events.event_log import EventType

    started = s.event_log.get_events(EventType.EMERGENCY_CALL_STARTED)
    check("incident started", len(started) == 1, f"got {len(started)}")
    check("consent was accepted", s.consent_manager.state.value == "accepted", s.consent_manager.state.value)
    check("reply never claims a live call", "i'm calling" not in result.get("response", "").lower())


def test_red_triggers_immediately():
    print("\n[3] Red triggers on the same turn, with a physical urgent trigger")
    s, conversation = _reset_session(_fresh_db())
    _set_contacts()

    result = conversation.process_turn("I can't breathe")

    from events.event_log import EventType

    started = s.event_log.get_events(EventType.EMERGENCY_CALL_STARTED)
    check("incident started", len(started) == 1)
    if started:
        check("trigger is physical_urgent", started[-1].detail.get("trigger") == "physical_urgent")
    check("tier is red", result.get("meta", {}).get("tier") == "red")
    check("response is not empty", bool(result.get("response")))


def test_yellow_does_not_call():
    print("\n[4] Yellow never auto-calls")
    s, conversation = _reset_session(_fresh_db())
    _set_contacts()

    from safety.escalation import EscalationTier
    from events.event_log import EventType

    # Yellow is the "ask once, dashboard alert" tier. Put the engine there
    # deterministically and run an ordinary turn through the full pipeline.
    s.escalation_engine.force_tier(EscalationTier.YELLOW, "repeat_question")
    conversation.process_turn("It's a quiet afternoon.")

    check("tier is yellow", s.escalation_engine.current_tier.value == "yellow",
          s.escalation_engine.current_tier.value)
    check("no incident started",
          len(s.event_log.get_events(EventType.EMERGENCY_CALL_STARTED)) == 0)
    check("no incident row", _incident_count() == 0, _incident_count())


# ---- 2. Idempotency ------------------------------------------------------

def test_duplicate_does_not_double_dial():
    print("\n[5] A duplicate urgent turn does not place a second call")
    s, conversation = _reset_session(_fresh_db())
    _set_contacts()

    conversation.process_turn("I can't breathe")
    conversation.process_turn("I can't breathe")
    conversation.process_turn("chest pain")

    from events.event_log import EventType

    started = s.event_log.get_events(EventType.EMERGENCY_CALL_STARTED)
    check("only one incident started", len(started) == 1, f"got {len(started)}")
    check("only one incident row", _incident_count() == 1)
    skipped = s.event_log.get_events(EventType.EMERGENCY_CALL_SKIPPED)
    check(
        "duplicate was logged with a reason",
        any(e.detail.get("reason") in {"already_active", "cooldown"} for e in skipped),
        [e.detail.get("reason") for e in skipped],
    )


def test_tier_flicker_does_not_double_dial():
    print("\n[6] A tier flicker inside the cooldown window does not dial again")
    s, conversation = _reset_session(_fresh_db())
    _set_contacts()
    os.environ["SAHAARA_EMERGENCY_COOLDOWN_SECONDS"] = "300"

    conversation.process_turn("I can't breathe")
    _wait_for_ended(s.event_log)
    _drain_orchestrators()

    conversation.process_turn("I fell and I cant get up")

    from events.event_log import EventType

    check("still one incident", _incident_count() == 1, _incident_count())
    skipped = s.event_log.get_events(EventType.EMERGENCY_CALL_SKIPPED)
    check("cooldown logged", any(e.detail.get("reason") == "cooldown" for e in skipped),
          [e.detail.get("reason") for e in skipped])
    # And the Red tier is still raised — the cooldown only silences the phone
    check("tier still red", s.escalation_engine.current_tier.value == "red")


# ---- 3. Ladder -----------------------------------------------------------

def _no_answer_provider():
    """A provider whose calls never get acknowledged."""

    class NoAnswerProvider:
        name = "fake-no-answer"
        simulated = True

        def place_call(self, *, to_e164, message, incident_id, attempt_id, gate_reason="simulated"):
            from telephony.provider import CallHandle

            self.last_to = to_e164
            return CallHandle(
                provider_call_id=f"fake-{attempt_id}",
                status="queued",
                raw={"to": to_e164},
            )

        def fetch_status(self, handle):
            return "no_answer"

        def cancel(self, handle):
            return None

        def available(self):
            return True

    return NoAnswerProvider()


def test_ladder_calls_rohan_then_priya_then_exhausts():
    print("\n[7] Ladder: Rohan, then Priya, then exhausted")
    s, conversation = _reset_session(_fresh_db())
    _set_contacts()

    import telephony.provider as provider_mod
    import telephony.orchestrator as orch_mod

    fake = _no_answer_provider()
    original = provider_mod.get_call_provider
    provider_mod.get_call_provider = lambda: fake
    orch_mod.get_call_provider = lambda: fake
    try:
        conversation.process_turn("I can't breathe")
        ended = _wait_for_ended(s.event_log, timeout=15.0)
    finally:
        provider_mod.get_call_provider = original
        orch_mod.get_call_provider = original
        _drain_orchestrators()

    from database import get_emergency_attempts

    incident = _latest_incident()
    check("incident ended", bool(ended), "no ENDED event")
    check("ended exhausted", incident and incident["status"] == "exhausted", incident and incident["status"])
    check("ended because every contact was exhausted",
          incident and incident["reason"] == "all_contacts_exhausted",
          incident and incident["reason"])

    attempts = get_emergency_attempts(incident["id"]) if incident else []
    names = [a["contact_name"] for a in attempts]
    check("Rohan tried first", names and names[0] == "Rohan", names)
    check("Priya tried after Rohan", "Priya" in names, names)
    check("Rohan first in the ladder order", names.index("Rohan") < names.index("Priya") if "Priya" in names else False)
    check("two attempts on Rohan before moving on", names.count("Rohan") == 2, names)
    check("two attempts on Priya", names.count("Priya") == 2, names)
    check("never more than 4 calls", len(attempts) <= 4, len(attempts))
    check(
        "no attempt stores a full number",
        all(
            (a["phone_masked"] or "") != phone
            for a in attempts
            for phone in ("+919876543210", "+919876543211")
        ),
        [a["phone_masked"] for a in attempts],
    )
    check(
        "numbers are masked to last four",
        all((a["phone_masked"] or "").endswith(("3210", "3211")) and "*" in (a["phone_masked"] or "")
            for a in attempts),
        [a["phone_masked"] for a in attempts],
    )


def test_exhausted_raises_no_caregiver_reached_alert():
    print("\n[8] Exhausted raises the Red 'No caregiver reached' alert")
    s, conversation = _reset_session(_fresh_db())
    _set_contacts()

    import telephony.provider as provider_mod
    import telephony.orchestrator as orch_mod

    fake = _no_answer_provider()
    original = provider_mod.get_call_provider
    provider_mod.get_call_provider = lambda: fake
    orch_mod.get_call_provider = lambda: fake
    try:
        conversation.process_turn("I can't breathe")
        _wait_for_ended(s.event_log, timeout=15.0)
        # The alert is raised at the end of the campaign, in the campaign thread
        deadline = time.time() + 5
        while time.time() < deadline and not any(
            a.reason == "no_caregiver_reached" for a in s.alert_builder.alert_store.get_all()
        ):
            time.sleep(0.05)
    finally:
        provider_mod.get_call_provider = original
        orch_mod.get_call_provider = original
        _drain_orchestrators()

    alerts = [a for a in s.alert_builder.alert_store.get_all() if a.reason == "no_caregiver_reached"]
    check("alert raised", len(alerts) == 1, len(alerts))
    if alerts:
        alert = alerts[0]
        check("alert tier is red", str(getattr(alert.tier_to, "value", alert.tier_to)).lower() == "red", alert.tier_to)
        check("says a caregiver was not reached", "no caregiver was reached" in alert.message.lower())
        check("never claims contact", "spoke to" not in alert.message.lower())
        check("states it was simulated", "simulated" in alert.message.lower() or
              "no real call was placed" in alert.message.lower())


# ---- 4. Orange dwell -----------------------------------------------------

def test_orange_dwell_fires_on_background_tick():
    print("\n[9] A silent Orange fires on the background tick (no user turn)")
    s, conversation = _reset_session(_fresh_db())
    _set_contacts()

    import scheduler as scheduler_module
    importlib.reload(scheduler_module)

    # Shrink the dwell so the test does not wait two minutes
    os.environ["SAHAARA_ORANGE_CALL_DWELL_SECONDS"] = "0.3"
    scheduler_module.ORANGE_CALL_DWELL_SECONDS = 0.3

    walker = scheduler_module.get_scheduler_for_profile("asha_demo_001")

    # Drive the tier to Orange deterministically, with no further conversation
    from safety.escalation import EscalationTier

    s.escalation_engine.force_tier(EscalationTier.ORANGE, "physical_general")

    async def drive():
        await walker._check_orange_dwell(s)          # first look starts the clock
        await asyncio.sleep(0.4)
        await walker._check_orange_dwell(s)          # dwell expired -> trigger

    asyncio.run(drive())

    from events.event_log import EventType

    started = s.event_log.get_events(EventType.EMERGENCY_CALL_STARTED)
    check("incident started from the tick", len(started) == 1, len(started))
    if started:
        check("tier recorded as orange", started[-1].detail.get("tier") == "orange")
        check("trigger is the physical-general dwell", started[-1].detail.get("trigger") == "physical_general_dwell")

    # A second tick must not dial again
    asyncio.run(walker._check_orange_dwell(s))
    check("second tick does not dial again", len(s.event_log.get_events(EventType.EMERGENCY_CALL_STARTED)) == 1)
    _drain_orchestrators()


def test_step_down_before_dwell_cancels():
    print("\n[10] Stepping down before the dwell expires cancels the pending call")
    s, conversation = _reset_session(_fresh_db())
    _set_contacts()

    import scheduler as scheduler_module
    importlib.reload(scheduler_module)
    os.environ["SAHAARA_ORANGE_CALL_DWELL_SECONDS"] = "120"
    scheduler_module.ORANGE_CALL_DWELL_SECONDS = 120.0

    walker = scheduler_module.get_scheduler_for_profile("asha_demo_001")
    from safety.escalation import EscalationTier

    s.escalation_engine.force_tier(EscalationTier.ORANGE, "physical_general")

    async def drive():
        await walker._check_orange_dwell(s)   # clock starts
        # Step down before the window closes
        s.escalation_engine.reset()
        await walker._check_orange_dwell(s)

    asyncio.run(drive())

    from events.event_log import EventType

    check("no incident started", len(s.event_log.get_events(EventType.EMERGENCY_CALL_STARTED)) == 0)
    check("dwell clock cleared", walker._orange_since is None, walker._orange_since)
    check("dwell flag cleared", walker._orange_call_triggered is False)

    skipped = s.event_log.get_events(EventType.EMERGENCY_CALL_SKIPPED)
    check("step-down is on the record", len(skipped) == 1, len(skipped))
    if skipped:
        check("step-down reason", skipped[-1].detail.get("reason") == "tier_stepped_down",
              skipped[-1].detail)
        check("step-down says nothing was dialled",
              skipped[-1].detail.get("held_seconds", 999) < 120,
              skipped[-1].detail)
    _drain_orchestrators()


def test_dwell_is_not_gated_by_the_checkin_flag_or_pause():
    """An emergency call must not depend on the gentle check-in feature.

    Live defect found on 2026-10-30: `_check_orange_dwell` was called from
    `_check_engine`, which returns immediately when `SAHAARA_PROACTIVE_V2` is
    false (the default) and never runs while monitoring is paused. So on a
    stock install the Orange dwell could not fire at all — the tier said
    Orange, Asha went quiet, and no campaign started. This drives the real
    `_dwell_loop` with both gates in their blocking position.
    """
    print("\n[11] The Orange dwell runs with the check-in flag OFF and monitoring paused")
    s, conversation = _reset_session(_fresh_db())
    _set_contacts()

    os.environ["SAHAARA_PROACTIVE_V2"] = "false"
    os.environ["SAHAARA_ORANGE_CALL_DWELL_SECONDS"] = "0.2"
    os.environ["SAHAARA_ORANGE_DWELL_POLL_SECONDS"] = "0.1"

    import scheduler as scheduler_module
    importlib.reload(scheduler_module)

    walker = scheduler_module.get_scheduler_for_profile("asha_demo_001")
    check("the check-in feature is off", walker._env_v2_enabled is False, walker._env_v2_enabled)
    walker._paused = True

    from events.event_log import EventType
    from safety.escalation import EscalationTier

    s.escalation_engine.force_tier(EscalationTier.ORANGE, "physical_general")

    async def drive():
        # Start the real lifecycle, so this proves the dwell loop is actually
        # WIRED, not merely that a coroutine can be called by hand.
        await walker.start(llm_call=None)
        for _ in range(60):
            await asyncio.sleep(0.05)
            if s.event_log.get_events(EventType.EMERGENCY_CALL_STARTED):
                break
        await walker.stop()

    asyncio.run(drive())

    started = s.event_log.get_events(EventType.EMERGENCY_CALL_STARTED)
    check("the dwell loop started a campaign", len(started) == 1, len(started))
    if started:
        check("trigger is the physical-general dwell",
              started[-1].detail.get("trigger") == "physical_general_dwell",
              started[-1].detail.get("trigger"))
    _drain_orchestrators()


def main():
    print("=" * 74)
    print("  EMERGENCY CALL — TRIGGERS, GATES, IDEMPOTENCY, LADDER")
    print("=" * 74)

    test_explicit_request_triggers()
    test_consent_accept_triggers()
    test_red_triggers_immediately()
    test_yellow_does_not_call()
    test_duplicate_does_not_double_dial()
    test_tier_flicker_does_not_double_dial()
    test_ladder_calls_rohan_then_priya_then_exhausts()
    test_exhausted_raises_no_caregiver_reached_alert()
    test_orange_dwell_fires_on_background_tick()
    test_step_down_before_dwell_cancels()
    test_dwell_is_not_gated_by_the_checkin_flag_or_pause()

    print("\n" + "=" * 74)
    print(f"  RESULTS: {passed} passed, {failed} failed")
    print("=" * 74)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
