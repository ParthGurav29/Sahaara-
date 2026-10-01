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
    
    def log_consent_answered(self, outcome: ConsentOutcome, contact_name: str) -> Event:
        """Log consent response."""
        event = Event(
            timestamp=time.time(),
            type=EventType.CONSENT_ANSWERED,
            profile_id=self.profile_id,
            detail={
                "outcome": outcome.value,
                "contact_name": contact_name,
            },
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