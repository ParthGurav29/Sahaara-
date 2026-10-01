"""
safety/escalation.py

Deterministic escalation engine for Sahaara. Manages Green/Yellow/Orange/Red
tiers based on repetition and distress signals. No ML, no heuristics — pure
threshold logic so the demo is 100% reproducible.

Demo time-compression: "10-15 minutes" of real time can be compressed to
~30 seconds on stage via `demo_mode` multiplier.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from events.event_log import EventLog, EventType, EscalationTier


class EscalationReason(str, Enum):
    """Reason for a tier change."""
    REPEAT_THRESHOLD = "repeat_threshold"
    SUSTAINED_DISTRESS = "sustained_distress"
    DISTRESS_PERSISTENCE = "distress_persistence"
    DANGER_STATEMENT = "danger_statement"
    UNRESPONSIVE = "unresponsive"
    MANUAL_OVERRIDE = "manual_override"
    STEP_DOWN = "step_down"
    SESSION_START = "session_start"


@dataclass
class EscalationConfig:
    """Configuration for escalation thresholds. All times in seconds."""
    # Repetition: number of repeated questions to trigger Yellow
    repeat_threshold: int = 3
    
    # Distress: sustained_distress() must be True for this long to trigger Yellow
    distress_hold_seconds: float = 300.0  # 5 minutes real time
    
    # Orange: distress persists after Yellow for this long
    orange_hold_seconds: float = 600.0  # 10 minutes real time
    
    # Minimum time a tier must hold before it can change (hysteresis)
    min_tier_hold_seconds: float = 60.0  # 1 minute real time
    
    # Demo mode time compression factor (e.g., 300x = 5 min -> 1 sec)
    demo_mode: bool = False
    demo_multiplier: float = 300.0
    
    def effective_distress_hold(self) -> float:
        if self.demo_mode:
            return self.distress_hold_seconds / self.demo_multiplier
        return self.distress_hold_seconds
    
    def effective_orange_hold(self) -> float:
        if self.demo_mode:
            return self.orange_hold_seconds / self.demo_multiplier
        return self.orange_hold_seconds
    
    def effective_min_hold(self) -> float:
        if self.demo_mode:
            return self.min_tier_hold_seconds / self.demo_multiplier
        return self.min_tier_hold_seconds


@dataclass
class EscalationState:
    """Current state of the escalation engine."""
    tier: EscalationTier = EscalationTier.GREEN
    tier_since: float = field(default_factory=time.time)
    yellow_reason: Optional[EscalationReason] = None
    yellow_since: Optional[float] = None
    distress_since: Optional[float] = None
    last_repeat_count: int = 0
    
    def tier_duration(self) -> float:
        return time.time() - self.tier_since


class EscalationEngine:
    """
    Deterministic escalation engine.
    
    Rules:
    - GREEN: Default. No alerts.
    - YELLOW: Triggered by EITHER:
        a) Repetition count >= repeat_threshold (configurable, default 3)
        b) Sustained distress held for distress_hold_seconds (default 5 min)
    - ORANGE: Distress persists after YELLOW for orange_hold_seconds (default 10 min)
    - RED: Only via explicit triggers (danger statement, unresponsive)
    
    Hysteresis:
    - Each tier holds for at least min_tier_hold_seconds before it can change
    - Step-down is deliberate: only when the triggering condition clears
      AND the minimum hold time has passed.
    """
    
    def __init__(
        self,
        event_log: EventLog,
        session_notes,  # SessionNotes for repeat/distress checks
        config: Optional[EscalationConfig] = None,
    ):
        self.event_log = event_log
        self.session_notes = session_notes
        self.config = config or EscalationConfig()
        self.state = EscalationState()
        
        # Initialize with session start
        self.state.tier = EscalationTier.GREEN
        self.state.tier_since = time.time()
        if event_log:
            event_log.log_session_start()
    
    @property
    def current_tier(self) -> EscalationTier:
        return self.state.tier
    
    @property
    def time_in_current_tier(self) -> float:
        return time.time() - self.state.tier_since
    
    def evaluate(self, user_message: str) -> EscalationTier:
        """
        Evaluate current state and transition tiers if needed.
        Called on every turn.
        """
        now = time.time()
        
        # Check if minimum hold time has passed for current tier
        if self.time_in_current_tier < self.config.effective_min_hold():
            return self.state.tier
        
        # Get current signals
        repeat_count = self.session_notes.repeat_count(user_message)
        is_repeated = self.session_notes.is_repeated_question(user_message)
        sustained_distress = self.session_notes.sustained_distress()

        # The current turn will be recorded after this evaluation, so if it's
        # a repeated question, the effective count will be +1
        effective_repeat_count = repeat_count + (1 if is_repeated else 0)
        
        # Track distress timing
        if sustained_distress and self.state.distress_since is None:
            self.state.distress_since = now
        elif not sustained_distress:
            self.state.distress_since = None
        
        # Track repeat count (effective, including current turn)
        self.state.last_repeat_count = effective_repeat_count
        
        # Evaluate transitions based on current tier
        if self.state.tier == EscalationTier.GREEN:
            self._evaluate_green(now, effective_repeat_count, is_repeated, sustained_distress)
        elif self.state.tier == EscalationTier.YELLOW:
            self._evaluate_yellow(now, sustained_distress)
        elif self.state.tier == EscalationTier.ORANGE:
            self._evaluate_orange(now, sustained_distress)
        elif self.state.tier == EscalationTier.RED:
            self._evaluate_red(now, sustained_distress)
        
        return self.state.tier
    
    def _evaluate_green(
        self,
        now: float,
        repeat_count: int,
        is_repeated: bool,
        sustained_distress: bool,
    ) -> None:
        """Evaluate transitions from GREEN."""
        
        # Check repetition threshold
        if repeat_count >= self.config.repeat_threshold:
            self._transition_to(EscalationTier.YELLOW, EscalationReason.REPEAT_THRESHOLD, now)
            self.state.yellow_reason = EscalationReason.REPEAT_THRESHOLD
            return
        
        # Check sustained distress
        if sustained_distress:
            if self.state.distress_since is not None:
                distress_duration = now - self.state.distress_since
                if distress_duration >= self.config.effective_distress_hold():
                    self._transition_to(EscalationTier.YELLOW, EscalationReason.SUSTAINED_DISTRESS, now)
                    self.state.yellow_reason = EscalationReason.SUSTAINED_DISTRESS
                    return
    
    def _evaluate_yellow(self, now: float, sustained_distress: bool) -> None:
        """Evaluate transitions from YELLOW."""
        
        # Check if we should escalate to ORANGE (distress persistence)
        if sustained_distress and self.state.yellow_since is not None:
            yellow_duration = now - self.state.yellow_since
            if yellow_duration >= self.config.effective_orange_hold():
                self._transition_to(EscalationTier.ORANGE, EscalationReason.DISTRESS_PERSISTENCE, now)
                return
        
        # Check if we should step down to GREEN
        # Only step down if BOTH triggers are cleared AND min hold passed
        repeat_count = self.session_notes.repeat_count("")
        is_repeated = self.session_notes.is_repeated_question("")
        
        repeat_cleared = repeat_count < self.config.repeat_threshold
        distress_cleared = not sustained_distress
        
        if repeat_cleared and distress_cleared:
            self._transition_to(EscalationTier.GREEN, EscalationReason.STEP_DOWN, now)
    
    def _evaluate_orange(self, now: float, sustained_distress: bool) -> None:
        """Evaluate transitions from ORANGE."""
        
        # ORANGE only steps down when distress clears
        if not sustained_distress:
            self._transition_to(EscalationTier.GREEN, EscalationReason.STEP_DOWN, now)
    
    def _evaluate_red(self, now: float, sustained_distress: bool) -> None:
        """Evaluate transitions from RED."""
        
        # RED only steps down via manual override or session end
        # (In practice, RED is terminal for the session)
        pass
    
    def _transition_to(self, new_tier: EscalationTier, reason: EscalationReason, now: float) -> None:
        """Perform a tier transition with logging."""
        if new_tier == self.state.tier:
            return
        
        old_tier = self.state.tier
        self.state.tier = new_tier
        self.state.tier_since = now
        
        # Track YELLOW entry time for ORANGE evaluation
        if new_tier == EscalationTier.YELLOW:
            self.state.yellow_since = now
        elif old_tier == EscalationTier.YELLOW:
            self.state.yellow_since = None
        
        # Log the tier change
        if self.event_log:
            self.event_log.log_tier_change(
                old_tier=old_tier,
                new_tier=new_tier,
                reason=reason.value,
                context={
                    "repeat_count": self.state.last_repeat_count,
                    "sustained_distress": self.session_notes.sustained_distress(),
                    "time_in_previous_tier": now - self.state.tier_since if old_tier != new_tier else 0,
                },
            )
    
    def force_tier(self, tier, reason: str) -> None:
        """Force a tier change (used for RED triggers: danger statement, unresponsive)."""
        now = time.time()
        if isinstance(tier, str):
            tier = EscalationTier(tier)
        reason_enum = EscalationReason(reason) if reason in EscalationReason.__members__.values() else EscalationReason.MANUAL_OVERRIDE
        self._transition_to(tier, reason_enum, now)
    
    def reset(self) -> None:
        """Reset engine to initial state."""
        now = time.time()
        old_tier = self.state.tier
        self.state = EscalationState()
        self.state.tier = EscalationTier.GREEN
        self.state.tier_since = now
        
        if self.event_log and old_tier != EscalationTier.GREEN:
            self.event_log.log_tier_change(
                old_tier=old_tier,
                new_tier=EscalationTier.GREEN,
                reason=EscalationReason.SESSION_START.value,
                context={},
            )
    
    def get_status(self) -> dict:
        """Get current escalation status for dashboard/API."""
        return {
            "tier": self.state.tier.value,
            "tier_duration_seconds": self.time_in_current_tier,
            "yellow_reason": self.state.yellow_reason.value if self.state.yellow_reason else None,
            "yellow_duration_seconds": (
                time.time() - self.state.yellow_since if self.state.yellow_since else None
            ),
            "distress_duration_seconds": (
                time.time() - self.state.distress_since if self.state.distress_since else None
            ),
            "last_repeat_count": self.state.last_repeat_count,
            "repeat_threshold": self.config.repeat_threshold,
            "demo_mode": self.config.demo_mode,
        }


if __name__ == "__main__":
    # Quick manual test
    from memory.session_notes import SessionNotes
    from events.event_log import EventLog
    from memory.profile import load_profile
    from pathlib import Path
    from dotenv import load_dotenv
    
    load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")
    profile = load_profile()
    
    event_log = EventLog(profile_id=profile.profile_id, demo_mode=True)
    session_notes = SessionNotes(profile_id=profile.profile_id)
    
    config = EscalationConfig(demo_mode=True, demo_multiplier=300.0)
    engine = EscalationEngine(event_log, session_notes, config)
    
    print("=== Escalation Engine Demo (compressed time) ===")
    print(f"Config: repeat_threshold={config.repeat_threshold}, "
          f"distress_hold={config.effective_distress_hold()}s, "
          f"orange_hold={config.effective_orange_hold()}s")
    print()
    
    # Simulate turns
    test_turns = [
        ("Hello", False, 0),
        ("Where is my daughter?", False, 1),
        ("Where is my daughter?", False, 2),
        ("Where is my daughter?", True, 3),  # 3rd repeat -> Yellow
    ]
    
    for msg, is_repeat, count in test_turns:
        session_notes.record_turn(msg, emotion="calm" if not is_repeat else "anxious")
        tier = engine.evaluate(msg)
        print(f"Turn: '{msg}' -> Tier: {tier.value} (repeat_count={count})")
    
    print()
    print("=== Sustained distress test ===")
    session_notes.reset()
    for i in range(4):
        session_notes.record_turn(f"Worried turn {i}", emotion="anxious")
        tier = engine.evaluate(f"Worried turn {i}")
        print(f"Turn {i+1}: sustained_distress={session_notes.sustained_distress()} -> Tier: {tier.value}")