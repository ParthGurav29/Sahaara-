"""
Scheduling Engine for Sahaara M3.

Pure trigger engine with injectable clock - no I/O, no LLM, no sleep calls.
All decisions are deterministic and testable with fake clocks.

M3 Step 8 adds simulate_day(): runs the same evaluate() logic across a
compressed day using a purely in-memory history, so caregivers can see
exactly which check-ins fire, skip, or expire — without waiting real time
and without touching persisted state.
"""

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Optional, List, Dict, Tuple

from .models import (
    SchedulerSettings,
    CheckinSlot,
    CheckinFiredRecord,
    EngineDecision,
    CheckinType,
    SkipReason,
    EngineContext,
    SimulatedDayStep,
    SimulatedDayResult,
    WORDING_POOLS,
)


class SchedulingEngine:
    """
    Pure trigger engine for proactive check-ins.

    No I/O, no LLM, no sleep calls. All decisions are deterministic
    and testable with fake clocks.
    """

    # Fallback slots when a profile has no configured checkin_times.
    # (time, type, wording pool key)
    DEFAULT_SLOTS = [
        ("08:00", CheckinType.MORNING_GREETING, "morning_greeting"),
        ("14:00", CheckinType.DAYTIME_CHECKIN, "daytime_checkin"),
        ("19:00", CheckinType.EVENING_CHECKIN, "evening_checkin"),
    ]

    # Default intro slot (first proactive contact of the day; dedupe = once/day)
    INTRO_TIME = "07:30"

    # Hard safety cap for simulate_day so a bad request can't hang the server
    MAX_SIMULATION_STEPS = 2880  # one full day at 30-second resolution

    def __init__(self, clock=None):
        """
        Initialize the scheduling engine.

        Args:
            clock: Optional callable returning datetime. Defaults to datetime.now.
                   For testing, pass a fake clock that returns controlled times.
        """
        self._clock = clock or datetime.now

    def _now(self) -> datetime:
        """Get current time from injected clock."""
        return self._clock()

    def _parse_time(self, time_str: str) -> time:
        """Parse HH:MM string to time object. Raises ValueError on bad input."""
        parts = str(time_str).strip().split(":")
        if len(parts) < 2:
            raise ValueError(f"Invalid HH:MM time: {time_str!r}")
        hour, minute = int(parts[0]), int(parts[1])
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError(f"Time out of range: {time_str!r}")
        return time(hour, minute)

    # ---- Slot generation ------------------------------------------------

    def _configured_slots(self, settings: SchedulerSettings) -> List[Tuple[str, CheckinType, str]]:
        """Return the profile's configured check-in slots.

        The caregiver-facing API stores check-in times as an ordered list of
        HH:MM strings (labels are positional). Mapping rule mirrors that UI:
        first time -> morning greeting, last time -> evening check-in,
        everything in between -> daytime check-in. Falls back to DEFAULT_SLOTS
        when the profile has no valid configured times.
        """
        raw = getattr(settings, "checkin_times", None) or []
        parsed: List[time] = []
        for item in raw:
            try:
                parsed.append(self._parse_time(item))
            except (ValueError, TypeError):
                continue  # ignore malformed entries rather than crash the loop

        if not parsed:
            return list(self.DEFAULT_SLOTS)

        parsed.sort()
        n = len(parsed)
        slots: List[Tuple[str, CheckinType, str]] = []
        for i, t in enumerate(parsed):
            if n == 1:
                checkin_type, pool = CheckinType.DAYTIME_CHECKIN, "daytime_checkin"
            elif i == 0:
                checkin_type, pool = CheckinType.MORNING_GREETING, "morning_greeting"
            elif i == n - 1:
                checkin_type, pool = CheckinType.EVENING_CHECKIN, "evening_checkin"
            else:
                checkin_type, pool = CheckinType.DAYTIME_CHECKIN, "daytime_checkin"
            slots.append((t.strftime("%H:%M"), checkin_type, pool))
        return slots

    def _get_slots_for_day(self, now: datetime, settings: SchedulerSettings) -> List[CheckinSlot]:
        """Generate check-in slots for the given day (date taken from `now`)."""
        slots: List[CheckinSlot] = []
        date_str = now.strftime("%Y-%m-%d")

        # Intro slot (fires once per day, deduped by date-scoped slot_id)
        if settings.intro_enabled:
            slots.append(CheckinSlot(
                slot_id=f"intro_{date_str}_0",
                checkin_type=CheckinType.INTRO,
                scheduled_time=self._parse_time(self.INTRO_TIME),
                slot_date=date_str,
                wording_pool_key="intro",
                slot_index=0,
                is_intro=True,
            ))

        for i, (time_str, checkin_type, pool) in enumerate(self._configured_slots(settings)):
            slots.append(CheckinSlot(
                slot_id=f"{checkin_type.value}_{date_str}_{i}",
                checkin_type=checkin_type,
                scheduled_time=self._parse_time(time_str),
                slot_date=date_str,
                wording_pool_key=pool,
                slot_index=i,
                is_intro=False,
            ))
        return slots

    # ---- Predicates -----------------------------------------------------

    def _is_slot_fired_today(self, slot_id: str, fired_history: List[CheckinFiredRecord]) -> bool:
        """True if this slot already has ANY record today (fire/skip/expire).

        Any recorded outcome is terminal for that slot — this is what keeps
        the scheduler loop from re-firing (or re-logging) the same slot.
        """
        return any(r.slot_id == slot_id for r in fired_history)

    def _is_recent_conversation(
        self,
        last_user_turn: Optional[datetime],
        settings: SchedulerSettings,
        now: Optional[datetime] = None,
    ) -> bool:
        """Check if there was a recent user conversation within the min gap."""
        if not last_user_turn:
            return False
        current = now or self._now()
        gap = current - last_user_turn
        return gap < timedelta(minutes=settings.min_gap_after_conversation_minutes)

    def _count_fired_today(self, fired_history: List[CheckinFiredRecord], today: str) -> int:
        """Count check-ins actually FIRED today (skips/expires don't count)."""
        return sum(
            1 for r in fired_history
            if r.fired_at.strftime("%Y-%m-%d") == today and not r.skip_reason
        )

    def _is_quiet_hours(self, now: datetime, settings: SchedulerSettings) -> bool:
        """Check if current time is within quiet hours (handles midnight crossing)."""
        start = self._parse_time(settings.quiet_hours_start)
        end = self._parse_time(settings.quiet_hours_end)
        current = now.time()

        if start <= end:
            return start <= current <= end
        # Crosses midnight (e.g., 22:00-07:00)
        return current >= start or current <= end

    # ---- Wording selection ----------------------------------------------

    def _select_wording(
        self,
        pool_key: str,
        recent_wording: List[str],
        limit: int,
        overrides: Dict[str, str],
    ) -> Tuple[str, str]:
        """Select a wording line, respecting caregiver overrides + rotation.

        Returns (wording_id, wording_text). Rotation: never reuse any of the
        last K ids for this pool; falls back to the full pool when exhausted.
        """
        override_text = (overrides or {}).get(pool_key)
        if override_text:
            return (f"override_{pool_key}", override_text)

        pool = WORDING_POOLS.get(pool_key, [])
        if not pool:
            return ("", "")

        recent = set((recent_wording or [])[-limit:]) if limit > 0 else set()
        candidates = [line for line in pool if line.get("id") not in recent]
        if not candidates:
            candidates = list(pool)
        # Deterministic pick keeps simulate_day reproducible.
        chosen = candidates[0]
        return (chosen.get("id", ""), chosen.get("text", ""))

    # ---- Core evaluation ------------------------------------------------

    def evaluate(self, context: EngineContext) -> List[EngineDecision]:
        """
        Evaluate the scheduling engine for the current moment.

        Args:
            context: EngineContext with all inputs (time, settings, session state, history)

        Returns:
            List of EngineDecision objects. Slots that are already handled or
            not yet due produce no decision (keeps the scheduler loop quiet and
            prevents duplicate history rows).
        """
        now = context.now if getattr(context, "now", None) else self._now()
        settings = context.profile_settings

        # Feature flag
        if not settings.proactive_v2_enabled:
            return []

        # ---- Global gates (no individual slot can fire) ----
        if not settings.enabled or context.scheduler_disabled:
            return [EngineDecision(
                action="skip", slot_id="all",
                reason=SkipReason.SCHEDULER_DISABLED.value,
            )]

        if context.monitoring_paused:
            return [EngineDecision(
                action="skip", slot_id="all",
                reason=SkipReason.MONITORING_PAUSED.value,
            )]

        if context.consent_pending:
            return [EngineDecision(
                action="skip", slot_id="all",
                reason=SkipReason.CONSENT_PENDING.value,
            )]

        if context.tier in ("yellow", "orange", "red"):
            return [EngineDecision(
                action="skip", slot_id="all",
                reason=SkipReason.TIER_YELLOW_ORANGE_RED.value,
            )]

        today = now.strftime("%Y-%m-%d")
        if self._count_fired_today(context.fired_history, today) >= settings.max_checkins_per_day:
            return [EngineDecision(
                action="skip", slot_id="all",
                reason=SkipReason.DAILY_CAP_REACHED.value,
            )]

        # ---- Per-slot evaluation ----
        decisions: List[EngineDecision] = []
        window = timedelta(minutes=settings.checkin_window_minutes)

        for slot in self._get_slots_for_day(now, settings):
            # Already fired/skipped/expired today -> terminal, stay silent
            if self._is_slot_fired_today(slot.slot_id, context.fired_history):
                continue

            scheduled_dt = datetime.combine(now.date(), slot.scheduled_time)

            # Not due yet -> no decision (must not consume the slot)
            if now < scheduled_dt:
                continue

            base = dict(
                slot_id=slot.slot_id,
                checkin_type=slot.checkin_type,
                is_intro=slot.is_intro,
            )

            # Window passed -> expire
            if now > scheduled_dt + window:
                decisions.append(EngineDecision(
                    action="expire",
                    reason=SkipReason.EXPIRED.value,
                    **base,
                ))
                continue

            # Quiet hours -> skip
            if self._is_quiet_hours(now, settings):
                decisions.append(EngineDecision(
                    action="skip",
                    reason=SkipReason.QUIET_HOURS.value,
                    **base,
                ))
                continue

            # Recent conversation -> skip (don't interrupt)
            if context.last_user_turn and self._is_recent_conversation(
                context.last_user_turn, settings, now
            ):
                decisions.append(EngineDecision(
                    action="skip",
                    reason=SkipReason.RECENT_CONVERSATION.value,
                    **base,
                ))
                continue

            # All checks passed -> fire
            wording_id, wording_text = self._select_wording(
                slot.wording_pool_key,
                context.recent_wording,
                settings.wording_recent_limit,
                settings.intro_overrides,
            )
            decisions.append(EngineDecision(
                action="fire",
                reason="fire",
                wording_id=wording_id,
                wording_text=wording_text,
                **base,
            ))

        return decisions

    # ---- Simulated day (M3 Step 8) --------------------------------------

    def simulate_day(
        self,
        start: datetime,
        end: datetime,
        step_minutes: int,
        settings: SchedulerSettings,
        fired_history: Optional[List[CheckinFiredRecord]] = None,
        last_user_turn: Optional[datetime] = None,
        session_state: Optional[dict] = None,
        recent_wording: Optional[List[str]] = None,
        profile_id: str = "simulation",
    ) -> SimulatedDayResult:
        """Run evaluate() across a compressed day without touching real state.

        Each step advances a caller-supplied clock. Fire/skip/expire outcomes
        are recorded in a LOCAL history copy so dedupe, daily caps, and
        rotation behave exactly like a real day — but nothing is persisted.

        Raises:
            ValueError: on non-positive step size or end < start.
        """
        if step_minutes < 1:
            raise ValueError("step_minutes must be >= 1")
        if end < start:
            raise ValueError("end must be >= start")

        history: List[CheckinFiredRecord] = list(fired_history or [])
        wording: List[str] = list(recent_wording or [])
        steps: List[SimulatedDayStep] = []

        total_fired = total_skipped = total_expired = total_gated = 0
        fired_by_type: Dict[str, int] = {}
        skip_reasons: Dict[str, int] = {}
        gated_reasons: Dict[str, int] = {}

        current = start
        iterations = 0
        while current <= end and iterations < self.MAX_SIMULATION_STEPS:
            context = EngineContext(
                now=current,
                profile_settings=settings,
                last_user_turn=last_user_turn,
                session_state=dict(session_state or {}),
                fired_history=list(history),
                recent_wording=list(wording),
            )
            decisions = self.evaluate(context)

            for decision in decisions:
                # Global gates block every slot for this step; report them
                # separately so "skipped" stays meaningful for the caregiver.
                if decision.slot_id == "all":
                    if decision.action == "skip":
                        total_gated += 1
                        reason = decision.reason
                        gated_reasons[reason] = gated_reasons.get(reason, 0) + 1
                    continue

                if decision.action == "fire":
                    total_fired += 1
                    key = decision.checkin_type.value
                    fired_by_type[key] = fired_by_type.get(key, 0) + 1
                elif decision.action == "skip":
                    total_skipped += 1
                    skip_reasons[decision.reason] = skip_reasons.get(decision.reason, 0) + 1
                elif decision.action == "expire":
                    total_expired += 1

                # Mirror the real scheduler: any slot-level outcome is terminal.
                history.append(CheckinFiredRecord(
                    profile_id=profile_id,
                    slot_id=decision.slot_id,
                    checkin_type=decision.checkin_type,
                    fired_at=current,
                    wording_id=decision.wording_id,
                    response_received=False,
                    retry_count=0,
                    skip_reason="" if decision.action == "fire" else decision.reason,
                ))
                if decision.action == "fire" and decision.wording_id:
                    wording.append(decision.wording_id)
                    wording = wording[-settings.wording_recent_limit:]

            steps.append(SimulatedDayStep(time=current, decisions=decisions))
            current += timedelta(minutes=step_minutes)
            iterations += 1

        return SimulatedDayResult(
            profile_id=profile_id,
            start=start,
            end=end,
            step_minutes=step_minutes,
            steps=steps,
            total_fired=total_fired,
            total_skipped=total_skipped,
            total_expired=total_expired,
            total_gated_steps=total_gated,
            fired_by_type=fired_by_type,
            skip_reasons=skip_reasons,
            gated_reasons=gated_reasons,
        )
