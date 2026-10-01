"""
safety/event_log.py

Append-only, in-memory event log for the current session.
Every notable thing in a session is recorded here: repeats, distress, tier changes,
consent events, fallbacks, danger statements, and regular turns.
"""

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class Event:
    timestamp: float
    event_type: str  # "repeat", "distress", "tier_change", "consent_asked", "consent_answered", "fallback", "danger_statement", "turn", "unresponsive"
    detail: Dict[str, Any]
    tier: str = "green"  # The escalation tier at the time the event occurred

    def to_json(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "event_type": self.event_type,
            "tier": self.tier,
            "detail": self.detail,
        }


class EventLog:
    """Holds the stream of events for a single session."""
    def __init__(self, session_id: str):
        self.session_id = session_id
        self._events: List[Event] = []
        self._current_tier = "green"

    def set_current_tier(self, tier: str) -> None:
        """Update the tracker's current tier so subsequent events log with it."""
        self._current_tier = tier

    def record(self, event_type: str, detail: Dict[str, Any], tier: Optional[str] = None) -> Event:
        """Record a new event."""
        event_tier = tier if tier is not None else self._current_tier
        event = Event(
            timestamp=time.time(),
            event_type=event_type,
            detail=detail,
            tier=event_tier,
        )
        self._events.append(event)
        return event

    def events_since(self, since_timestamp: float) -> List[Event]:
        """Return all events that occurred after the given timestamp."""
        return [e for e in self._events if e.timestamp > since_timestamp]

    def events_of_type(self, event_type: str) -> List[Event]:
        """Return all events of a specific type."""
        return [e for e in self._events if e.event_type == event_type]

    def all_events(self) -> List[Event]:
        return list(self._events)

    def to_json(self) -> List[Dict[str, Any]]:
        return [e.to_json() for e in self._events]

