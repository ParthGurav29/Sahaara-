"""
session_manager.py

Per-profile session state factory. Replaces the global singletons
in conversation.py with profile-scoped instances.

Usage:
    manager = SessionManager()
    session = manager.get("asha_demo_001")
    session.process_turn("Hello")
    manager.reset("asha_demo_001")
"""

import time
from dataclasses import dataclass, field
from typing import Optional

from memory.profile import Profile
from memory.memory_box import MemoryBox
from memory.session_notes import SessionNotes
from memory.suggestions import SuggestionQueue, detect_suggestions
from memory.summarizer import generate_summary_from_events, build_session_context_block
from safety.escalation import EscalationEngine, EscalationConfig
from safety.consent import ConsentManager, ContinuationManager
from dashboard.alerts import AlertBuilder, AlertStore
from events.event_log import EventLog, init_event_log
import database


@dataclass
class ProfileSession:
    """All state for one profile's active session."""
    profile_id: str
    profile: Profile
    session_notes: SessionNotes
    memory_box: MemoryBox
    event_log: EventLog
    escalation_engine: EscalationEngine
    consent_manager: ConsentManager
    alert_store: AlertStore
    alert_builder: AlertBuilder
    suggestion_queue: SuggestionQueue
    continuation_manager: ContinuationManager
    session_context_block: str = ""
    _last_sahaara_prompt_time: float = 0.0
    _unresponsive_enabled: bool = False
    
    def record_turn_tracking(self):
        """Called after each turn to advance memory box + suggestions."""
        self.memory_box.record_turn()
    
    def detect_and_queue_suggestions(self, user_message: str):
        """Scan user message for profile facts and queue suggestions."""
        detected = detect_suggestions(user_message, self.profile.get_likes(), self.profile.get_family())
        for s in detected:
            self.suggestion_queue.add(s)
    
    def reload_context(self):
        """Reload session context from recent summaries."""
        summaries = database.get_recent_summaries(self.profile_id)
        self.session_context_block = build_session_context_block(summaries, self.profile.preferred_address)


class SessionManager:
    """Manages per-profile sessions. One instance for the app."""
    
    def __init__(self):
        self._sessions: dict[str, ProfileSession] = {}
        self._active_profile_id: str = ""
        database.init_db()
    
    @property
    def active_profile_id(self) -> str:
        return self._active_profile_id
    
    def get(self, profile_id: str) -> ProfileSession:
        """Get or create a session for the given profile."""
        if profile_id in self._sessions:
            return self._sessions[profile_id]
        
        return self._build_session(profile_id)

    def reload_profile(self, profile_id: str) -> ProfileSession:
        """Force re-read the profile from the database and rebuild the session.

        Call this after a PUT /profile/{id} edit so the in-memory session
        picks up the caregiver's changes immediately.
        """
        return self._build_session(profile_id)

    def _build_session(self, profile_id: str) -> ProfileSession:
        """Build a fresh ProfileSession from the database profile row."""
        data = database.get_profile(profile_id)
        if data is None:
            raise ValueError(f"Profile not found: {profile_id}")
        
        profile = Profile(data)
        
        session_notes = SessionNotes(profile_id=profile_id)
        
        memory_box = MemoryBox(profile)
        
        event_log = init_event_log(profile, persist=False, demo_mode=True)
        # Hook event_log.append to also write to SQLite
        _original_append = event_log.append
        def _db_append(event):
            _original_append(event)
            database.append_event(profile_id, event.type.value, event.detail, event.timestamp)
        event_log.append = _db_append
        
        escalation_config = EscalationConfig(demo_mode=True, demo_multiplier=300.0)
        escalation_engine = EscalationEngine(event_log, session_notes, escalation_config)
        
        consent_manager = ConsentManager(
            event_log=event_log, timeout_seconds=30.0,
            demo_mode=True, demo_multiplier=300.0,
        )
        
        continuation_manager = ContinuationManager(
            event_log=event_log, timeout_seconds=10.0,
        )
        
        alert_store = AlertStore()
        alert_builder = AlertBuilder(event_log, profile, alert_store=alert_store)
        
        suggestion_queue = SuggestionQueue()
        
        session = ProfileSession(
            profile_id=profile_id,
            profile=profile,
            session_notes=session_notes,
            memory_box=memory_box,
            event_log=event_log,
            escalation_engine=escalation_engine,
            consent_manager=consent_manager,
            continuation_manager=continuation_manager,
            alert_store=alert_store,
            alert_builder=alert_builder,
            suggestion_queue=suggestion_queue,
        )
        session.reload_context()
        
        self._sessions[profile_id] = session
        return session
    
    def activate(self, profile_id: str) -> ProfileSession:
        """Set the active profile and return its session."""
        session = self.get(profile_id)
        self._active_profile_id = profile_id
        return session
    
    def get_active(self) -> Optional[ProfileSession]:
        if not self._active_profile_id:
            # Default to first profile
            profiles = database.list_profiles()
            if profiles:
                return self.activate(profiles[0]["profile_id"])
            return None
        return self.get(self._active_profile_id)
    
    def reset(self, profile_id: str):
        """Reset session state — save summary, clear events, reinit."""
        if profile_id not in self._sessions:
            return
        
        session = self._sessions[profile_id]
        
        # Save summary before clearing
        events = session.event_log.get_all()
        if events:
            summary = generate_summary_from_events(events, session.profile.preferred_address)
            database.save_session_summary(profile_id, summary)
        
        session.event_log.clear()
        session.session_notes.reset()
        session.escalation_engine.reset()
        session.consent_manager.reset()
        session.continuation_manager.reset()
        session.memory_box.reset()
        session.alert_store.clear()
        session.suggestion_queue.clear()
        session._last_sahaara_prompt_time = 0.0
        session._unresponsive_enabled = False
        
        session.event_log.log_session_start()
        session.reload_context()