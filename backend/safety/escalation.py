"""
safety/escalation.py

Deterministic rule-based state machine for escalation tiers.
"""

import time
from dataclasses import dataclass
from enum import Enum

from memory.session_notes import SessionNotes
from safety.event_log import EventLog

class EscalationTier(str, Enum):
    GREEN = "green"
    YELLOW = "yellow"
    ORANGE = "orange"
    RED = "red"

@dataclass
class TierDecision:
    tier: EscalationTier
    previous_tier: EscalationTier
    reason: str
    changed: bool
    timestamp: float

class EscalationEngine:
    def __init__(
        self,
        event_log: EventLog,
        session_notes: SessionNotes,
        demo_time_scale: float = 1.0,
        repeat_threshold: int = 3,
        orange_hold_seconds: float = 600.0,
        min_hold_seconds: float = 120.0
    ):
        self.event_log = event_log
        self.session_notes = session_notes
        self.demo_time_scale = demo_time_scale
        self.repeat_threshold = repeat_threshold
        
        self.orange_hold_seconds = orange_hold_seconds / demo_time_scale
        self.min_hold_seconds = min_hold_seconds / demo_time_scale

        self._current_tier = EscalationTier.GREEN
        self._last_change_time = time.time()
        self._yellow_start_time = None

    @property
    def current_tier(self) -> EscalationTier:
        return self._current_tier

    def evaluate(self, current_text: str = "") -> TierDecision:
        """Evaluate and possibly update the current tier."""
        now = time.time()
        
        # Red is a terminal/absorbing state for the session, or forced externally
        if self._current_tier == EscalationTier.RED:
            return self._make_decision(EscalationTier.RED, "Already at RED tier", now)

        # Check conditions
        is_sustained = self.session_notes.sustained_distress()
        repeat_count = self.session_notes.repeat_count(current_text) if current_text else 0
        has_yellow_condition = is_sustained or (repeat_count >= self.repeat_threshold)
        
        target_tier = EscalationTier.GREEN
        reason = "Routine conversation"

        if has_yellow_condition:
            target_tier = EscalationTier.YELLOW
            if is_sustained:
                reason = "Sustained distress detected"
            else:
                reason = f"Question repeated {repeat_count} times"

            # Check Orange upgrade
            if self._yellow_start_time is not None:
                if (now - self._yellow_start_time) >= self.orange_hold_seconds and is_sustained:
                    target_tier = EscalationTier.ORANGE
                    reason = "Distress persisted past yellow hold period"
            
            # Note: if we just hit yellow condition, but haven't recorded yellow start, we do it in the change logic below

        # Hysteresis and state transitions
        if target_tier == self._current_tier:
            # Maintain current
            return self._make_decision(self._current_tier, reason, now)

        # We want to change tier
        time_in_current = now - self._last_change_time
        
        # Upgrading is immediate (except orange which has its own hold above)
        is_upgrade = self._tier_rank(target_tier) > self._tier_rank(self._current_tier)
        
        if is_upgrade or (time_in_current >= self.min_hold_seconds):
            # Apply change
            return self._apply_change(target_tier, reason, now)
        else:
            # Blocked by hysteresis
            return self._make_decision(self._current_tier, f"Holding {self._current_tier.value} due to hysteresis", now)

    def force_tier(self, tier: EscalationTier, reason: str) -> TierDecision:
        """Force the engine into a specific tier, typically RED."""
        now = time.time()
        if self._current_tier == tier:
            return self._make_decision(tier, reason, now)
        return self._apply_change(tier, reason, now)

    def _apply_change(self, new_tier: EscalationTier, reason: str, now: float) -> TierDecision:
        prev = self._current_tier
        self._current_tier = new_tier
        self._last_change_time = now
        
        if new_tier == EscalationTier.YELLOW and prev == EscalationTier.GREEN:
            self._yellow_start_time = now
        elif new_tier == EscalationTier.GREEN:
            self._yellow_start_time = None

        self.event_log.set_current_tier(new_tier.value)
        self.event_log.record("tier_change", {
            "previous_tier": prev.value,
            "new_tier": new_tier.value,
            "reason": reason
        }, tier=new_tier.value)
        
        return TierDecision(
            tier=new_tier,
            previous_tier=prev,
            reason=reason,
            changed=True,
            timestamp=now
        )

    def _make_decision(self, tier: EscalationTier, reason: str, now: float) -> TierDecision:
        return TierDecision(
            tier=tier,
            previous_tier=tier,
            reason=reason,
            changed=False,
            timestamp=now
        )

    def _tier_rank(self, tier: EscalationTier) -> int:
        return {
            EscalationTier.GREEN: 0,
            EscalationTier.YELLOW: 1,
            EscalationTier.ORANGE: 2,
            EscalationTier.RED: 3
        }[tier]

