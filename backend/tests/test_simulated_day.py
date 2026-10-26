"""
test_simulated_day.py

Tests for the M3 Step 8 simulated-day feature:
- engine.simulate_day() runs the real evaluate() logic on a fake clock
- configured check-in times / quiet hours / expiry / dedupe / caps are honored
- nothing is persisted (pure in-memory simulation)
"""
import sys
sys.path.insert(0, "/Users/parthmangeshgurav/Desktop/sahaara/backend")

from datetime import datetime, timedelta

from scheduling.models import (
    SchedulerSettings,
    CheckinFiredRecord,
    CheckinType,
    SimulatedDayResult,
)
from scheduling.engine import SchedulingEngine


DAY = "2026-11-01"


def make_settings(**overrides) -> SchedulerSettings:
    defaults = dict(
        enabled=True,
        max_checkins_per_day=4,
        min_gap_after_conversation_minutes=20,
        checkin_window_minutes=15,
        response_window_minutes=10,
        retry_once=True,
        intro_throttle_hours=24,
        checkin_times=["08:00", "14:00", "19:00"],
        quiet_hours_start="22:00",
        quiet_hours_end="07:00",
        intro_enabled=True,
        intro_overrides={},
        wording_recent_limit=4,
        proactive_v2_enabled=True,
    )
    defaults.update(overrides)
    return SchedulerSettings(**defaults)


def at(hhmm: str, date: str = DAY) -> datetime:
    return datetime.strptime(f"{date} {hhmm}", "%Y-%m-%d %H:%M")


def run(engine: SchedulingEngine, start: str, end: str, step: int = 15, **kwargs) -> SimulatedDayResult:
    settings = kwargs.pop("settings", None) or make_settings()
    date = kwargs.pop("date", DAY)
    return engine.simulate_day(
        start=at(start, date),
        end=at(end, date),
        step_minutes=step,
        settings=settings,
        **kwargs,
    )


def fired_slots(result: SimulatedDayResult) -> list:
    out = []
    for step in result.steps:
        for d in step.decisions:
            if d.action == "fire":
                out.append(d)
    return out


# ---------------------------------------------------------------- tests

def test_full_day_fires_all_configured_slots():
    """Intro + 3 configured slots fire exactly once across a normal day."""
    engine = SchedulingEngine()
    result = run(engine, "06:00", "23:00", 15)

    fires = fired_slots(result)
    slot_ids = [d.slot_id for d in fires]
    types = sorted(d.checkin_type.value for d in fires)

    assert len(slot_ids) == len(set(slot_ids)), f"Duplicate fires: {slot_ids}"
    assert types == ["daytime_checkin", "evening_checkin", "intro", "morning_greeting"], types
    assert result.total_fired == 4, result.total_fired
    assert result.total_expired == 0, result.total_expired
    assert result.feature_enabled is True
    print("PASS: full day fires all configured slots once")


def test_fire_times_match_schedule():
    """Fires land inside their 15-minute window."""
    engine = SchedulingEngine()
    result = run(engine, "06:00", "23:00", 5)

    expected = {
        "intro": at("07:30"),
        "morning_greeting": at("08:00"),
        "daytime_checkin": at("14:00"),
        "evening_checkin": at("19:00"),
    }
    for d in fired_slots(result):
        step_time = next(s.time for s in result.steps if d in s.decisions)
        delta = (step_time - expected[d.checkin_type.value]).total_seconds() / 60
        assert 0 <= delta < 15, f"{d.checkin_type.value} fired at {step_time} (delta {delta}m)"
    print("PASS: fire times match schedule")


def test_custom_checkin_times_are_used():
    """Caregiver-configured checkin_times drive slot generation."""
    engine = SchedulingEngine()
    result = run(
        engine, "06:00", "23:00", 15,
        settings=make_settings(checkin_times=["09:15", "12:45"]),
    )
    fires = fired_slots(result)
    times = sorted(s.time for s in result.steps for d in s.decisions if d.action == "fire")
    hhmm = [t.strftime("%H:%M") for t in times]
    assert "09:15" in hhmm, hhmm
    assert "12:45" in hhmm, hhmm
    # first configured time = morning, last = evening
    by_type = {d.checkin_type.value for d in fires}
    assert by_type == {"intro", "morning_greeting", "evening_checkin"}, by_type
    print("PASS: custom checkin_times are used")


def test_quiet_hours_produce_skip_not_fire():
    """A due slot inside quiet hours is skipped with quiet_hours."""
    engine = SchedulingEngine()
    result = run(
        engine, "23:15", "23:45", 15,
        settings=make_settings(
            checkin_times=["23:30"],
            quiet_hours_start="22:00",
            quiet_hours_end="07:00",
        ),
    )
    skipped = [d for s in result.steps for d in s.decisions if d.action == "skip"]
    reasons = {d.reason for d in skipped}
    assert "quiet_hours" in reasons, reasons
    assert not fired_slots(result), "Nothing should fire during quiet hours"
    print("PASS: quiet hours skip")


def test_expired_slots_are_expired():
    """A slot whose window has passed is expired, not fired."""
    engine = SchedulingEngine()
    result = run(engine, "09:00", "09:30", 15)

    expired = [d for s in result.steps for d in s.decisions if d.action == "expire"]
    expired_ids = {d.slot_id for d in expired}
    assert any(sid.startswith("morning_greeting_") for sid in expired_ids), expired_ids
    assert not any(d.checkin_type == CheckinType.MORNING_GREETING for d in fired_slots(result))
    print("PASS: expired slots expire")


def test_not_due_slots_are_silent():
    """Before the first slot is due, no decisions are emitted (and no history)."""
    engine = SchedulingEngine()
    result = run(engine, "06:00", "07:00", 15)
    assert result.steps, "Simulation should produce steps"
    for step in result.steps:
        assert step.decisions == [], f"Unexpected decisions at {step.time}: {step.decisions}"
    assert result.total_fired == 0
    print("PASS: not-due slots are silent")


def test_dedupe_with_seeded_history():
    """A slot already in history is not fired again."""
    engine = SchedulingEngine()
    seeded = CheckinFiredRecord(
        profile_id="asha_demo_001",
        slot_id=f"morning_greeting_{DAY}_0",
        checkin_type=CheckinType.MORNING_GREETING,
        fired_at=at("08:00"),
        wording_id="mg_1",
    )
    result = run(engine, "07:55", "08:10", 5, fired_history=[seeded])
    assert not any(
        d.checkin_type == CheckinType.MORNING_GREETING
        for d in fired_slots(result)
    ), "Morning greeting should be deduped"
    print("PASS: dedupe with seeded history")


def test_recent_conversation_skips():
    """A user turn inside the min-gap window suppresses the check-in."""
    engine = SchedulingEngine()
    result = run(
        engine, "07:55", "08:10", 5,
        settings=make_settings(min_gap_after_conversation_minutes=20),
        last_user_turn=at("07:55"),
    )
    skipped = [d for s in result.steps for d in s.decisions if d.action == "skip"]
    assert any(d.reason == "recent_conversation" for d in skipped), [d.reason for d in skipped]
    assert not any(
        d.checkin_type == CheckinType.MORNING_GREETING for d in fired_slots(result)
    )
    print("PASS: recent conversation skips")


def test_tier_suppression():
    """Yellow/Orange/Red suppresses all check-ins."""
    engine = SchedulingEngine()
    for tier in ("yellow", "orange", "red"):
        result = run(engine, "06:00", "23:00", 15, session_state={"tier": tier})
        assert result.total_fired == 0, f"Fired at tier {tier}"
        assert result.gated_reasons.get("tier_yellow_orange_red", 0) > 0
    print("PASS: tier suppression")


def test_daily_cap():
    """Only max_checkins_per_day check-ins fire in a day."""
    engine = SchedulingEngine()
    result = run(engine, "06:00", "23:00", 15, settings=make_settings(max_checkins_per_day=1))
    assert result.total_fired == 1, result.total_fired
    assert result.gated_reasons.get("daily_cap_reached", 0) > 0
    print("PASS: daily cap enforced")


def test_simulated_clock_used_for_slot_ids():
    """Slot IDs use the simulated date, not the real date (clock bug regression)."""
    engine = SchedulingEngine()
    date = "2027-03-15"
    result = run(engine, "08:00", "08:10", 5, date=date)
    fires = fired_slots(result)
    assert fires, "Expected a morning fire"
    for d in fires:
        assert date in d.slot_id, f"slot_id {d.slot_id} does not contain simulated date {date}"
    print("PASS: simulated clock used for slot ids")


def test_wording_text_is_populated():
    """Fires carry a human-readable wording line (rotation/overrides aware)."""
    engine = SchedulingEngine()
    result = run(engine, "06:00", "23:00", 15)
    for d in fired_slots(result):
        assert d.wording_id, d
        assert d.wording_text, d
    print("PASS: wording text populated")


def test_override_wording_wins():
    """A caregiver intro override is used for the intro slot."""
    engine = SchedulingEngine()
    override = "Hello Asha, this is Sahaara checking in."
    result = run(
        engine, "06:00", "23:00", 15,
        settings=make_settings(intro_overrides={"intro": override}),
    )
    intro_fires = [d for d in fired_slots(result) if d.is_intro]
    assert intro_fires, "Intro should fire"
    assert intro_fires[0].wording_text == override
    print("PASS: intro override wins")


def test_summary_counts():
    """Summary aggregates match the step decisions (slot vs global gates)."""
    engine = SchedulingEngine()
    result = run(engine, "06:00", "23:00", 15)

    slot_decisions = [
        d for s in result.steps for d in s.decisions if d.slot_id != "all"
    ]
    fired = sum(1 for d in slot_decisions if d.action == "fire")
    skipped = sum(1 for d in slot_decisions if d.action == "skip")
    expired = sum(1 for d in slot_decisions if d.action == "expire")
    gated = sum(
        1 for s in result.steps for d in s.decisions
        if d.slot_id == "all" and d.action == "skip"
    )

    assert result.total_fired == fired
    assert result.total_skipped == skipped
    assert result.total_expired == expired
    assert result.total_gated_steps == gated
    assert sum(result.fired_by_type.values()) == result.total_fired
    assert sum(result.skip_reasons.values()) == result.total_skipped
    assert sum(result.gated_reasons.values()) == result.total_gated_steps

    # A clean day: every slot fires once, no slot-level skips/expires
    assert result.total_skipped == 0, result.skip_reasons
    assert result.total_expired == 0

    payload = result.to_dict()
    assert payload["summary"]["total_fired"] == fired
    assert payload["summary"]["total_gated_steps"] == gated
    assert payload["steps"][0]["decisions"] == []
    print("PASS: summary counts")


def test_invalid_inputs_raise():
    """Bad step size / reversed range raise ValueError."""
    engine = SchedulingEngine()
    settings = make_settings()
    try:
        engine.simulate_day(at("08:00"), at("09:00"), 0, settings)
        assert False, "Expected ValueError for step_minutes=0"
    except ValueError:
        pass
    try:
        engine.simulate_day(at("09:00"), at("08:00"), 15, settings)
        assert False, "Expected ValueError for end < start"
    except ValueError:
        pass
    print("PASS: invalid inputs raise")


def test_simulation_does_not_mutate_input_history():
    """Passing real history must not append to the caller's list."""
    engine = SchedulingEngine()
    history = []
    run(engine, "06:00", "23:00", 15, fired_history=history)
    assert history == [], f"Simulation mutated caller history: {history}"
    print("PASS: simulation does not mutate input history")


if __name__ == "__main__":
    print("Testing simulated day...")
    print()
    test_full_day_fires_all_configured_slots()
    test_fire_times_match_schedule()
    test_custom_checkin_times_are_used()
    test_quiet_hours_produce_skip_not_fire()
    test_expired_slots_are_expired()
    test_not_due_slots_are_silent()
    test_dedupe_with_seeded_history()
    test_recent_conversation_skips()
    test_tier_suppression()
    test_daily_cap()
    test_simulated_clock_used_for_slot_ids()
    test_wording_text_is_populated()
    test_override_wording_wins()
    test_summary_counts()
    test_invalid_inputs_raise()
    test_simulation_does_not_mutate_input_history()
    print("\nAll simulated day tests passed!")
