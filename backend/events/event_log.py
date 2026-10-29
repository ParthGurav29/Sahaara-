"""
events/event_log.py

Append-only event log for a session. Every notable occurrence is recorded here:
- Repetition events
- Distress signals
- Tier changes (Green/Yellow/Orange/Red)
- Consent asked/answered
- Fallback triggers (guardrail, standby ladder)
- Danger statements
- System health events

This is the single source of truth for the session timeline. The dashboard,
alerts, and daily summary all read from here.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from memory.profile import Profile

logger = logging.getLogger("events.event_log")

# Event types as defined in the Phase 3 plan
class EventType(str, Enum):
    REPEAT = "repeat"
    DISTRESS = "distress"
    TIER_CHANGE = "tier_change"
    CONSENT_ASKED = "consent_asked"
    CONSENT_ANSWERED = "consent_answered"
    FALLBACK = "fallback"
    DANGER_STATEMENT = "danger_statement"
    SYSTEM_HEALTH = "system_health"
    SESSION_START = "session_start"
    SESSION_END = "session_end"
    TIMING = "timing"
    PHYSICAL_SYMPTOM = "physical_symptom"
    MEMORY_BOX_PLAYED = "memory_box_played"
    CONVERSATION_TURN = "conversation_turn"
    ALERT_ACKNOWLEDGED = "alert_acknowledged"
    ALERT_SENT = "alert_sent"
    ACTION = "action"
    REMINDER_FIRED = "reminder_fired"
    # Emergency Call (Task 3)
    EMERGENCY_CALL_STARTED = "emergency_call_started"
    EMERGENCY_CALL_ATTEMPT = "emergency_call_attempt"
    EMERGENCY_CALL_STATUS = "emergency_call_status"
    EMERGENCY_CALL_ENDED = "emergency_call_ended"
    EMERGENCY_CALL_SKIPPED = "emergency_call_skipped"


class EscalationTier(str, Enum):
    GREEN = "green"
    YELLOW = "yellow"
    ORANGE = "orange"
    RED = "red"


class FallbackType(str, Enum):
    GUARDRAIL = "guardrail"
    STANDBY_MILD_DELAY = "standby_mild_delay"
    STANDBY_REAL_DELAY = "standby_real_delay"
    STANDBY_HARD_FAILURE = "standby_hard_failure"


class ConsentOutcome(str, Enum):
    ACCEPTED = "accepted"
    DECLINED = "declined"
    TIMEOUT = "timeout"
    # The pending question was abandoned, not answered — e.g. the person said
    # something safety-critical while Sahaara was waiting for a yes/no. Logging
    # this as DECLINED would be a false record that she refused help.
    SUPERSEDED = "superseded"


@dataclass
class Event:
    """A single event in the session log."""
    timestamp: float  # Unix epoch
    type: EventType
    profile_id: str
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "timestamp_iso": datetime.fromtimestamp(self.timestamp).isoformat(),
            "type": self.type.value,
            "profile_id": self.profile_id,
            "detail": self.detail,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Event":
        return cls(
            timestamp=data["timestamp"],
            type=EventType(data["type"]),
            profile_id=data["profile_id"],
            detail=data.get("detail", {}),
        )


class EventLog:
    """
    Append-only event log for a single session/profile.
    
    Events are written to an in-memory list and optionally persisted to a JSONL file.
    The log is scoped to a profile_id so multiple users can have independent logs.
    """
    
    def __init__(
        self,
        profile_id: str,
        persist_path: Optional[Path] = None,
        demo_mode: bool = False,
    ):
        self.profile_id = profile_id
        self.persist_path = persist_path
        self.demo_mode = demo_mode  # If True, compresses time for demo
        self._events: list[Event] = []
        self._tier = EscalationTier.GREEN
        self._tier_since: float = time.time()
        
        if self.persist_path:
            self.persist_path.parent.mkdir(parents=True, exist_ok=True)
    
    @property
    def current_tier(self) -> EscalationTier:
        return self._tier
    
    @property
    def tier_duration(self) -> float:
        return time.time() - self._tier_since
    
    def append(self, event: Event) -> None:
        """Append an event to the log."""
        if event.profile_id != self.profile_id:
            raise ValueError(f"Event profile_id {event.profile_id} doesn't match log {self.profile_id}")
        
        self._events.append(event)
        
        # Persist to JSONL if configured
        if self.persist_path:
            try:
                with open(self.persist_path, "a") as f:
                    f.write(json.dumps(event.to_dict()) + "\n")
            except Exception as e:
                logger.warning("Failed to persist event: %s", e)
        
        # Log tier changes specially
        if event.type == EventType.TIER_CHANGE:
            self._tier = EscalationTier(event.detail.get("new_tier", "green"))
            self._tier_since = event.timestamp
            logger.info(
                "Tier change: %s -> %s (reason: %s)",
                event.detail.get("old_tier", "?"),
                self._tier.value,
                event.detail.get("reason", "unknown"),
            )
    
    def get_events(
        self,
        event_type: Optional[EventType] = None,
        since: Optional[float] = None,
    ) -> list[Event]:
        """Get filtered events."""
        events = self._events
        if event_type:
            events = [e for e in events if e.type == event_type]
        if since:
            events = [e for e in events if e.timestamp >= since]
        return events
    
    def get_recent(self, seconds: float = 300) -> list[Event]:
        """Get events from the last N seconds."""
        cutoff = time.time() - seconds
        return self.get_events(since=cutoff)
    
    def get_all(self) -> list[Event]:
        """Get all events."""
        return list(self._events)
    
    def clear(self) -> None:
        """Clear the in-memory log (does not delete persisted file)."""
        self._events.clear()
    
    # Convenience methods for common event types
    
    def log_repeat(self, question: str, count: int, is_repeated: bool) -> Event:
        """Log a repetition event."""
        event = Event(
            timestamp=time.time(),
            type=EventType.REPEAT,
            profile_id=self.profile_id,
            detail={
                "question": question,
                "count": count,
                "is_repeated": is_repeated,
            },
        )
        self.append(event)
        return event
    
    def log_distress(self, sustained: bool, emotion: str, duration_seconds: float) -> Event:
        """Log a distress signal."""
        event = Event(
            timestamp=time.time(),
            type=EventType.DISTRESS,
            profile_id=self.profile_id,
            detail={
                "sustained": sustained,
                "emotion": emotion,
                "duration_seconds": duration_seconds,
            },
        )
        self.append(event)
        return event
    
    def log_tier_change(
        self,
        old_tier: EscalationTier,
        new_tier: EscalationTier,
        reason: str,
        context: Optional[dict[str, Any]] = None,
    ) -> Event:
        """Log a tier change."""
        event = Event(
            timestamp=time.time(),
            type=EventType.TIER_CHANGE,
            profile_id=self.profile_id,
            detail={
                "old_tier": old_tier.value,
                "new_tier": new_tier.value,
                "reason": reason,
                "context": context or {},
            },
        )
        self.append(event)
        return event
    
    def log_consent_asked(self, question: str, contact_name: str, tier: EscalationTier) -> Event:
        """Log that consent was asked."""
        event = Event(
            timestamp=time.time(),
            type=EventType.CONSENT_ASKED,
            profile_id=self.profile_id,
            detail={
                "question": question,
                "contact_name": contact_name,
                "tier": tier.value,
            },
        )
        self.append(event)
        return event
    
    def log_consent_answered(
        self,
        outcome: ConsentOutcome,
        contact_name: str,
        reason: Optional[str] = None,
    ) -> Event:
        """Log consent response."""
        detail: dict[str, Any] = {
            "outcome": outcome.value,
            "contact_name": contact_name,
        }
        # Only add the reason when one was supplied, so existing events keep
        # exactly the shape they had before.
        if reason:
            detail["reason"] = reason
        event = Event(
            timestamp=time.time(),
            type=EventType.CONSENT_ANSWERED,
            profile_id=self.profile_id,
            detail=detail,
        )
        self.append(event)
        return event
    
    def log_fallback(
        self,
        fallback_type: FallbackType,
        detail: Optional[dict[str, Any]] = None,
    ) -> Event:
        """Log a fallback trigger."""
        event = Event(
            timestamp=time.time(),
            type=EventType.FALLBACK,
            profile_id=self.profile_id,
            detail={
                "fallback_type": fallback_type.value,
                **(detail or {}),
            },
        )
        self.append(event)
        return event
    
    def log_danger_statement(self, phrase: str, matched_pattern: str) -> Event:
        """Log a detected danger statement."""
        event = Event(
            timestamp=time.time(),
            type=EventType.DANGER_STATEMENT,
            profile_id=self.profile_id,
            detail={
                "phrase": phrase,
                "matched_pattern": matched_pattern,
            },
        )
        self.append(event)
        return event

    def log_physical_symptom(
        self,
        phrase: str,
        matched_pattern: str,
        urgency: str,  # "urgent" or "general"
        tier_escalated_to: str,  # "red" or "orange"
    ) -> Event:
        """Log a detected physical symptom that triggered immediate escalation."""
        event = Event(
            timestamp=time.time(),
            type=EventType.PHYSICAL_SYMPTOM,
            profile_id=self.profile_id,
            detail={
                "phrase": phrase,
                "matched_pattern": matched_pattern,
                "urgency": urgency,
                "tier_escalated_to": tier_escalated_to,
            },
        )
        self.append(event)
        return event
    
    def log_danger_statement(self, phrase: str, matched_pattern: str) -> Event:
        """Log a detected danger statement."""
        event = Event(
            timestamp=time.time(),
            type=EventType.DANGER_STATEMENT,
            profile_id=self.profile_id,
            detail={
                "phrase": phrase,
                "matched_pattern": matched_pattern,
            },
        )
        self.append(event)
        return event
    
    def log_memory_box_played(
        self,
        clip_filename: str,
        family_member_name: str,
        triggered_by: str,  # "longing_match" or "replay_request"
    ) -> Event:
        """Log that a Memory Box clip was played."""
        event = Event(
            timestamp=time.time(),
            type=EventType.MEMORY_BOX_PLAYED,
            profile_id=self.profile_id,
            detail={
                "clip_filename": clip_filename,
                "family_member_name": family_member_name,
                "triggered_by": triggered_by,
            },
        )
        self.append(event)
        return event
    
    def log_conversation_turn(
        self,
        speaker: str,  # "user" or "sahaara"
        text: str,
        emotion: str = "neutral",
        context_trace: Optional[dict] = None,
    ) -> Event:
        """Log a full conversation turn."""
        detail = {
            "speaker": speaker,
            "text": text,
            "emotion": emotion,
        }
        if context_trace:
            detail["context_trace"] = context_trace
        event = Event(
            timestamp=time.time(),
            type=EventType.CONVERSATION_TURN,
            profile_id=self.profile_id,
            detail=detail,
        )
        self.append(event)
        return event
    
    def log_alert_acknowledged(
        self,
        alert_id: str,
        note: str = "",
    ) -> Event:
        """Log that a caregiver acknowledged an alert."""
        event = Event(
            timestamp=time.time(),
            type=EventType.ALERT_ACKNOWLEDGED,
            profile_id=self.profile_id,
            detail={
                "alert_id": alert_id,
                "note": note,
            },
        )
        self.append(event)
        return event
    
    def log_alert_sent(
        self,
        alert_id: str,
        tier: str = "",
        status: str = "",
        sent: int = 0,
        failed: int = 0,
        revoked: int = 0,
        reason: str = "",
        results: Optional[list] = None,
    ) -> Event:
        """
        Log a Web Push attempt for an alert.

        `status` is "sent" (the push service accepted at least one message),
        "skipped" (gate, not pushable, cooldown or no subscriptions) or
        "error". "sent" never means a phone displayed it.

        `results` is one `{"outcome", "status"}` entry per subscription — never
        the endpoint itself, which is a bearer URL.
        """
        event = Event(
            timestamp=time.time(),
            type=EventType.ALERT_SENT,
            profile_id=self.profile_id,
            detail={
                "alert_id": alert_id,
                "tier": tier,
                "status": status,
                "sent": sent,
                "failed": failed,
                "revoked": revoked,
                "reason": reason,
                "results": results or [],
            },
        )
        self.append(event)
        return event
    
    def log_action(
        self,
        action_type: str,
        success: bool,
        spoken: str = "",
        raw_text: str = "",
        detail: Optional[dict] = None,
    ) -> Event:
        """Log one fixed task action (timer, reminder, radio, ...).

        `spoken` is what Sahaara actually said. `raw_text` is the person's exact
        words — shown to the caregiver so they can check a reminder, and never
        spoken back (a medication request is sanitised before it is voiced).
        """
        event = Event(
            timestamp=time.time(),
            type=EventType.ACTION,
            profile_id=self.profile_id,
            detail={
                "action_type": action_type,
                "success": bool(success),
                "spoken": spoken,
                "raw_text": raw_text,
                **(detail or {}),
            },
        )
        self.append(event)
        return event

    def log_reminder_fired(
        self,
        reminder_id: int,
        kind: str,
        label: str,
        spoken: str,
        delivered_voice: bool = False,
    ) -> Event:
        """Log a timer/reminder actually going off."""
        event = Event(
            timestamp=time.time(),
            type=EventType.REMINDER_FIRED,
            profile_id=self.profile_id,
            detail={
                "reminder_id": reminder_id,
                "kind": kind,
                "label": label,
                "spoken": spoken,
                "delivered_voice": bool(delivered_voice),
            },
        )
        self.append(event)
        return event
    
    def log_system_health(self, message: str, severity: str = "info") -> Event:
        """Log a system health event."""
        event = Event(
            timestamp=time.time(),
            type=EventType.SYSTEM_HEALTH,
            profile_id=self.profile_id,
            detail={
                "message": message,
                "severity": severity,
            },
        )
        self.append(event)
        return event

    # Emergency Call event loggers (Task 3)
    def log_emergency_call_started(
        self,
        incident_id: str,
        trigger: str,
        tier: str,
        simulated: bool,
        provider: str,
    ) -> Event:
        event = Event(
            timestamp=time.time(),
            type=EventType.EMERGENCY_CALL_STARTED,
            profile_id=self.profile_id,
            detail={
                "incident_id": incident_id,
                "trigger": trigger,
                "tier": tier,
                "simulated": simulated,
                "provider": provider,
            },
        )
        self.append(event)
        return event

    def log_emergency_call_attempt(
        self,
        incident_id: str,
        attempt_id: str,
        contact_order: int,
        contact_name: str,
        phone_masked: str,
        attempt: int,
        simulated: bool,
    ) -> Event:
        event = Event(
            timestamp=time.time(),
            type=EventType.EMERGENCY_CALL_ATTEMPT,
            profile_id=self.profile_id,
            detail={
                "incident_id": incident_id,
                "attempt_id": attempt_id,
                "contact_order": contact_order,
                "contact_name": contact_name,
                "phone_masked": phone_masked,
                "attempt": attempt,
                "simulated": simulated,
            },
        )
        self.append(event)
        return event

    def log_emergency_call_status(
        self,
        incident_id: str,
        attempt_id: str,
        status: str,
        simulated: bool,
    ) -> Event:
        event = Event(
            timestamp=time.time(),
            type=EventType.EMERGENCY_CALL_STATUS,
            profile_id=self.profile_id,
            detail={
                "incident_id": incident_id,
                "attempt_id": attempt_id,
                "status": status,
                "simulated": simulated,
            },
        )
        self.append(event)
        return event

    def log_emergency_call_ended(
        self,
        incident_id: str,
        status: str,
        attempts: int,
        simulated: bool,
        reason: str,
    ) -> Event:
        event = Event(
            timestamp=time.time(),
            type=EventType.EMERGENCY_CALL_ENDED,
            profile_id=self.profile_id,
            detail={
                "incident_id": incident_id,
                "status": status,
                "attempts": attempts,
                "simulated": simulated,
                "reason": reason,
            },
        )
        self.append(event)
        return event

    def log_emergency_call_skipped(
        self,
        incident_id: str,
        reason: str,
        contact_name: str,
    ) -> Event:
        event = Event(
            timestamp=time.time(),
            type=EventType.EMERGENCY_CALL_SKIPPED,
            profile_id=self.profile_id,
            detail={
                "incident_id": incident_id,
                "reason": reason,
                "contact_name": contact_name,
            },
        )
        self.append(event)
        return event

    def log_session_start(self) -> Event:
        """Log session start."""
        event = Event(
            timestamp=time.time(),
            type=EventType.SESSION_START,
            profile_id=self.profile_id,
            detail={},
        )
        self.append(event)
        return event
    
    def log_session_end(self) -> Event:
        """Log session end."""
        event = Event(
            timestamp=time.time(),
            type=EventType.SESSION_END,
            profile_id=self.profile_id,
            detail={},
        )
        self.append(event)
        return event
    
    def log_timing(self, turn_id: str, timings: dict[str, float]) -> Event:
        """Log timing measurements for a voice turn.
        
        Args:
            turn_id: Unique identifier for the turn
            timings: Dictionary of timing points (e.g., t_speech_end, t_llm_request, 
                     t_first_tts_byte, etc.) as Unix timestamps
        """
        event = Event(
            timestamp=time.time(),
            type=EventType.TIMING,
            profile_id=self.profile_id,
            detail={
                "turn_id": turn_id,
                "timings": timings,
            },
        )
        self.append(event)
        return event
    
    def to_jsonl(self) -> str:
        """Export all events as JSONL string."""
        return "\n".join(json.dumps(e.to_dict()) for e in self._events)
    
    def summary(self) -> dict[str, Any]:
        """Return a summary of the session for debugging/display."""
        counts: dict[str, int] = {}
        for e in self._events:
            counts[e.type.value] = counts.get(e.type.value, 0) + 1
        
        return {
            "profile_id": self.profile_id,
            "total_events": len(self._events),
            "event_counts": counts,
            "current_tier": self._tier.value,
            "tier_duration_seconds": self.tier_duration,
            "demo_mode": self.demo_mode,
        }


# Global event log instance (will be initialized in conversation.py)
_event_log: Optional[EventLog] = None


def get_event_log() -> Optional[EventLog]:
    """Get the global event log instance."""
    return _event_log


def set_event_log(log: EventLog) -> None:
    """Set the global event log instance."""
    global _event_log
    _event_log = log


def init_event_log(
    profile: Profile,
    persist: bool = False,
    demo_mode: bool = False,
) -> EventLog:
    """
    Initialize the global event log for a profile.
    
    Args:
        profile: The user profile
        persist: If True, persist to backend/events/data/{profile_id}.jsonl
        demo_mode: If True, enables time-compression for demos
    """
    persist_path = None
    if persist:
        data_dir = Path(__file__).resolve().parent / "data"
        data_dir.mkdir(exist_ok=True)
        persist_path = data_dir / f"{profile.profile_id}.jsonl"
    
    log = EventLog(
        profile_id=profile.profile_id,
        persist_path=persist_path,
        demo_mode=demo_mode,
    )
    log.log_session_start()
    set_event_log(log)
    return log