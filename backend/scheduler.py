"""
scheduler.py

Background task that reads profile routines and pushes proactive
messages (morning greeting, evening check-in, medication reminders,
meal nudges) via SSE. Runs alongside FastAPI.

M3: Uses the new SchedulingEngine with injectable clock for testability.
- Per-profile settings from SQLite with defaults
- Skip rules: quiet hours, recent conversation, consent pending, tier, daily cap
- Expiry and dedupe with SQLite persistence
- Wording rotation with guardrail gate
- Response tracking with retry logic
- No I/O in engine — all decisions are pure and testable with fake clocks
"""

import asyncio
import logging
import os
import json
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from typing import Optional, List, Dict, Any

from memory.profile import Profile
from scheduling import SchedulingEngine
from scheduling.models import (
    SchedulerSettings, 
    CheckinFiredRecord, 
    CheckinType, 
    SkipReason,
    EngineContext,
    EngineDecision,
    CheckinSlot,
    SimulatedDayResult,
)
from database import (
    get_scheduler_settings, 
    save_scheduler_settings, 
    get_default_scheduler_settings,
    get_checkin_fired_history,
    save_checkin_fired,
    mark_checkin_response_received,
    update_checkin_audio_delivery,
    get_profile,
    decode_json_field,
)
from safety.escalation import EscalationTier

logger = logging.getLogger("scheduler")

# Check interval in seconds (production: 30s, demo: can be faster)
CHECK_INTERVAL = int(os.environ.get("SCHEDULER_CHECK_INTERVAL", "30"))


@dataclass
class Scheduler:
    """Background scheduler for proactive Sahaara messages using M3 engine."""
    
    profile: Profile
    
    # Engine instance
    _engine: SchedulingEngine = field(init=False, default=None)
    _settings: Optional[SchedulerSettings] = field(init=False, default=None)
    _fired_history: List[CheckinFiredRecord] = field(init=False, default_factory=list)
    
    # Runtime state
    _paused: bool = field(default=False)
    _running: bool = field(default=False)
    _task: Optional[asyncio.Task] = field(default=None)
    _initiative_queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=20))
    _voice_initiative_queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=20))
    _llm_call: Optional[Any] = field(default=None)
    _last_user_turn: Optional[datetime] = field(default=None)
    _session_state: Dict = field(default_factory=dict)
    _recent_wording: List[str] = field(default_factory=list)
    # M3 feature flag (env-level hard gate; default OFF per agents.md)
    _env_v2_enabled: bool = field(init=False, default=False)
    
    def __post_init__(self):
        self._env_v2_enabled = os.environ.get("SAHAARA_PROACTIVE_V2", "false").strip().lower() in (
            "1", "true", "yes", "on",
        )
        self._load_settings()
        self._engine = SchedulingEngine()
        self._load_fired_history()
    
    def _load_settings(self):
        """Load scheduler settings from DB with defaults."""
        db_settings = get_scheduler_settings(self.profile_id)
        if db_settings:
            # Convert DB row to SchedulerSettings
            self._settings = SchedulerSettings(
                enabled=bool(db_settings["enabled"]),
                max_checkins_per_day=db_settings["max_checkins_per_day"],
                min_gap_after_conversation_minutes=db_settings["min_gap_after_conversation_minutes"],
                checkin_window_minutes=db_settings["checkin_window_minutes"],
                response_window_minutes=db_settings["response_window_minutes"],
                intro_throttle_hours=db_settings["intro_throttle_hours"],
                checkin_times=decode_json_field(db_settings["checkin_times"], []),
                quiet_hours_start=db_settings["quiet_hours_start"],
                quiet_hours_end=db_settings["quiet_hours_end"],
                intro_enabled=bool(db_settings["intro_enabled"]),
                intro_overrides=decode_json_field(db_settings["intro_overrides"], {}),
                wording_recent_limit=db_settings["wording_recent_limit"],
                retry_once=bool(db_settings["retry_once"]),
                proactive_v2_enabled=bool(db_settings["proactive_v2_enabled"]),
            )
        else:
            # Use defaults and save to DB
            defaults = get_default_scheduler_settings()
            self._settings = SchedulerSettings(**defaults)
            save_scheduler_settings(self.profile_id, self._settings.to_dict())
    
    def _load_fired_history(self):
        """Load fired check-in history from DB."""
        rows = get_checkin_fired_history(self.profile_id, limit=500)
        self._fired_history = [CheckinFiredRecord(
            profile_id=r["profile_id"],
            slot_id=r["slot_id"],
            checkin_type=CheckinType(r["checkin_type"]),
            fired_at=datetime.fromisoformat(r["fired_at"]) if isinstance(r["fired_at"], str) else datetime.fromtimestamp(r["fired_at"]),
            wording_id=r["wording_id"],
            response_received=bool(r["response_received"]),
            retry_count=r["retry_count"],
            skip_reason=r["skip_reason"],
            delivered_audio=bool(r["delivered_audio"]),
            audio_blocked=bool(r["audio_blocked"]),
        ) for r in rows]
    
    @property
    def profile_id(self) -> str:
        return self.profile.profile_id
    
    @property
    def paused(self) -> bool:
        return self._paused
    
    def toggle(self) -> bool:
        self._paused = not self._paused
        # Persist to DB
        if self._settings:
            self._settings.enabled = not self._paused
            save_scheduler_settings(self.profile_id, self._settings.to_dict())
        return self._paused
    
    def add_one_time_reminder(self, text: str, fire_at: float):
        """Add a one-time reminder from set_reminder action."""
        # This is handled by the engine now, but keep for backward compat
        pass
    
    async def get_initiative(self) -> Optional[dict]:
        """Non-blocking check for pending initiative messages."""
        try:
            return self._initiative_queue.get_nowait()
        except asyncio.QueueEmpty:
            return None
    
    async def start(self, llm_call=None):
        """Start the background scheduler loop."""
        self._llm_call = llm_call
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("Scheduler started for profile %s", self.profile_id)
    
    async def stop(self):
        """Stop the scheduler."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
    
    async def _loop(self):
        """Main scheduler loop — runs every CHECK_INTERVAL seconds."""
        while self._running:
            try:
                if not self._paused:
                    await self._check_engine()
                await asyncio.sleep(CHECK_INTERVAL)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Scheduler loop error")
                await asyncio.sleep(CHECK_INTERVAL)
    
    async def _check_engine(self):
        """Run the scheduling engine and process decisions."""
        # M3 feature flag — hard env gate, default OFF
        if not self._env_v2_enabled:
            return
        # Pick up caregiver setting changes without requiring a restart
        self._load_settings()
        # Build engine context
        from conversation import _get_session
        s = _get_session()
        
        context = EngineContext(
            now=datetime.now(),
            profile_settings=self._settings,
            last_user_turn=self._last_user_turn,
            session_state={
                "tier": s.escalation_engine.current_tier.value,
                "consent_pending": s.consent_manager.state.value == "pending",
                "monitoring_paused": not s.escalation_engine.config.demo_mode,
                "scheduler_disabled": self._paused,
                "last_checkin_time": self._session_state.get("last_checkin_time"),
            },
            fired_history=self._fired_history,
            recent_wording=self._recent_wording,
        )
        
        decisions = self._engine.evaluate(context)
        
        for decision in decisions:
            await self._process_decision(decision)
        
        # Check for check-in responses (user turns during response window)
        self._check_response_windows()
    
    async def _process_decision(self, decision):
        """Process a single engine decision."""
        if decision.action == "fire":
            await self._fire_checkin(decision)
        elif decision.action == "skip":
            # Log skip reason
            await self._log_skip(decision)
        elif decision.action == "expire":
            await self._log_expire(decision)
    
    async def _fire_checkin(self, decision: EngineDecision):
        """Fire a check-in: generate message, queue for delivery, record in history."""
        text = await self._generate_message(decision)
        if not text:
            return
        
        # Determine category for delivery
        category = decision.checkin_type.value
        if decision.is_intro:
            category = "intro"
        elif decision.checkin_type == CheckinType.MEDICATION:
            category = f"medication_{decision.medication_label}"
        
        # Queue for delivery (SSE to caregiver + voice WS to person)
        await self._queue_initiative(text, category, decision.slot_id, decision.wording_id)
        
        # Record in fired history
        record = CheckinFiredRecord(
            profile_id=self.profile_id,
            slot_id=decision.slot_id,
            checkin_type=decision.checkin_type,
            fired_at=datetime.now(),
            wording_id=decision.wording_id,
            response_received=False,
            retry_count=0,
            skip_reason="",
        )
        save_checkin_fired(record.to_dict())
        self._fired_history.append(record)
        
        # Update recent wording
        if decision.wording_id:
            self._recent_wording.append(decision.wording_id)
            # Keep only last K
            limit = self._settings.wording_recent_limit
            self._recent_wording = self._recent_wording[-self._settings.wording_recent_limit:]
        
        # Log
        logger.info("Fired check-in: %s for profile %s", decision.slot_id, self.profile_id)
    
    async def _generate_message(self, decision: EngineDecision) -> str:
        """Generate the check-in message text."""
        name = self.profile.preferred_address
        
        # Try LLM first for variety
        if self._llm_call:
            try:
                prompt = self._build_prompt(decision)
                system = (
                    "You are Sahaara, a warm companion for Asha. Generate ONE short, "
                    "gentle sentence for this moment. Never say 'reminder' or 'alert.' "
                    "Frame it as a caring nudge. Be warm and conversational."
                )
                result = self._llm_call(system=system, user=prompt)
                if result and result.text:
                    return result.text.strip()
            except Exception:
                pass  # Fall through to template
        
        # Template fallback using pools
        return self._template_message(decision)
    
    def _build_prompt(self, decision: EngineDecision) -> str:
        """Build prompt for LLM based on check-in type."""
        name = self.profile.preferred_address
        ct = decision.checkin_type
        
        if decision.is_intro:
            return f"Generate a brief introduction for {name}. State that you are Sahaara, a voice companion, not a person. 1-2 sentences, warm."
        elif decision.checkin_type == CheckinType.MORNING_GREETING:
            return f"Generate a gentle morning greeting for {name}. 1 sentence, warm."
        elif decision.checkin_type == CheckinType.EVENING_CHECKIN:
            return f"Generate a gentle evening check-in for {name}. 1 sentence. Don't ask how she is — just be present."
        elif decision.checkin_type == CheckinType.MEDICATION:
            return f"Generate a gentle medication-time nudge for {name}. 1 sentence. Never mention dose or type."
        elif decision.checkin_type == CheckinType.MEAL:
            return f"Generate a gentle meal-time nudge for {name}. 1 sentence."
        elif decision.checkin_type == CheckinType.DAYTIME_CHECKIN:
            return f"Generate a gentle daytime check-in for {name}. 1 sentence, just checking in."
        else:
            return f"Generate a gentle check-in for {name}. 1 sentence, warm."
    
    def _template_message(self, decision: EngineDecision) -> str:
        """Template fallback if LLM unavailable."""
        name = self.profile.preferred_address
        
        templates = {
            CheckinType.INTRO: [
                f"Hello {name}. I'm Sahaara, your voice companion. I'm not a person, but I'm here to listen and chat.",
                f"Hi {name}. I'm Sahaara, a voice companion here to keep you company. I'm not a person, just a friendly voice.",
            ],
            CheckinType.MORNING_GREETING: [
                f"Good morning, {name}. The day is beginning gently.",
                f"It's a new morning, {name}. I'm here with you.",
                f"Morning has arrived, {name}. Take your time.",
            ],
            CheckinType.EVENING_CHECKIN: [
                f"The evening is settling in, {name}. I'm here if you'd like some quiet company.",
                f"It's getting dark outside, {name}. Perhaps a cup of chai?",
                f"The day is winding down, {name}. I hope you're feeling comfortable.",
            ],
            CheckinType.MEDICATION: [
                f"{name}, it might be time for your usual routine. No rush.",
                f"Just a gentle nudge, {name} — your regular time is here.",
            ],
            CheckinType.MEAL: [
                f"It's about time for a meal, {name}. Whenever you're ready.",
            ],
            CheckinType.DAYTIME_CHECKIN: [
                f"Just checking in, {name}. How's the day going?",
                f"Thinking of you, {name}. How are things?",
            ],
        }
        
        ct = decision.checkin_type
        options = templates.get(ct, [f"{name}, just checking in."])
        import random
        return random.choice(options)
    
    async def _queue_initiative(self, text: str, category: str, slot_id: str, wording_id: str):
        """Push an initiative message to the SSE queue and voice WS queue."""
        message = {
            "type": "sahaara_initiative",
            "data": {
                "text": text,
                "category": category,
                "slot_id": slot_id,
                "wording_id": wording_id,
                "timestamp": datetime.now().timestamp(),
                "timestamp_iso": datetime.now().isoformat(),
            },
            "timestamp": datetime.now().timestamp(),
        }
        try:
            self._initiative_queue.put_nowait(message)
        except asyncio.QueueFull:
            logger.warning("Initiative queue full, dropping: %s", category)
        
        # Also push to voice initiative queue for voice WebSocket delivery
        voice_message = {
            "type": "initiative",
            "text": text,
            "slot_id": slot_id,
            "wording_id": wording_id,
            "category": category,
        }
        try:
            self._voice_initiative_queue.put_nowait(voice_message)
        except asyncio.QueueFull:
            logger.warning("Voice initiative queue full, dropping: %s", category)
    
    def get_voice_initiative_queue(self) -> asyncio.Queue:
        """Get the voice initiative queue for voice WebSocket handler."""
        return self._voice_initiative_queue
    
    def _check_response_windows(self):
        """Check if any fired check-ins have received a user response within the window."""
        now = datetime.now()
        for record in self._fired_history:
            if not record.response_received:
                elapsed = (datetime.now() - record.fired_at).total_seconds() / 60
                if elapsed <= self._settings.response_window_minutes:
                    # Check if there was a user turn since the check-in fired
                    # This is tracked by the conversation module
                    pass  # Response tracking is handled by conversation module
    
    def record_user_turn(self):
        """Call this when a user turn is processed to track response windows."""
        self._last_user_turn = datetime.now()
        # Check if any pending check-ins should be marked as responded
        for record in self._fired_history:
            if not record.response_received:
                elapsed = (datetime.now() - record.fired_at).total_seconds() / 60
                if elapsed <= self._settings.response_window_minutes:
                    record.response_received = True
                    mark_checkin_response_received(self.profile_id, record.slot_id)
    
    def record_audio_delivery(self, slot_id: str, delivered_audio: bool, audio_blocked: bool = False):
        """Record audio delivery status for a check-in."""
        for record in self._fired_history:
            if record.slot_id == slot_id:
                record.delivered_audio = delivered_audio
                record.audio_blocked = audio_blocked
                update_checkin_audio_delivery(self.profile_id, slot_id, 
                                             1 if delivered_audio else 0, 
                                             1 if audio_blocked else 0)
                break
    
    def get_status(self) -> dict:
        today = datetime.now().strftime("%Y-%m-%d")
        fired_today = [r for r in self._fired_history
                       if r.fired_at.strftime("%Y-%m-%d") == today and not r.skip_reason]
        return {
            "paused": self._paused,
            "fired_today": len(fired_today),
            "pending_retries": len([r for r in self._fired_history
                                    if r.retry_count > 0 and not r.response_received]),
        }

    def simulate_day(
        self,
        start_time: str = "06:00",
        end_time: str = "23:00",
        step_minutes: int = 15,
        date_str: Optional[str] = None,
        include_history: bool = False,
    ) -> SimulatedDayResult:
        """Run a read-only simulated day using the profile's current settings.

        Args:
            start_time/end_time: HH:MM bounds of the simulated day.
            step_minutes: clock resolution (default 15 = the check-in window).
            date_str: YYYY-MM-DD; defaults to today.
            include_history: seed the simulation with the profile's real fired
                history (default False = deterministic clean-day preview).

        Nothing is persisted: this is a diagnostic/demo tool only.
        """
        # Always preview against the latest caregiver settings.
        self._load_settings()
        if date_str:
            try:
                day = datetime.strptime(date_str, "%Y-%m-%d").date()
            except ValueError:
                raise ValueError(f"Invalid date {date_str!r}; expected YYYY-MM-DD")
        else:
            day = datetime.now().date()

        def _combine(hhmm: str) -> datetime:
            return datetime.combine(day, self._engine._parse_time(hhmm))

        start = _combine(start_time)
        end = _combine(end_time)

        # Session state for the simulation — mirrors the live scheduler inputs.
        session_state = {
            "tier": "green",
            "consent_pending": False,
            "monitoring_paused": False,
            "scheduler_disabled": self._paused,
        }
        try:
            from conversation import _get_session
            s = _get_session()
            if s.profile.profile_id == self.profile_id:
                session_state.update({
                    "tier": s.escalation_engine.current_tier.value,
                    "consent_pending": s.consent_manager.state.value == "pending",
                    "monitoring_paused": not s.escalation_engine.config.demo_mode,
                })
        except Exception:
            # No active session (e.g., tests) — use the neutral defaults above.
            pass

        result = self._engine.simulate_day(
            start=start,
            end=end,
            step_minutes=step_minutes,
            settings=self._settings,
            fired_history=self._fired_history if include_history else [],
            last_user_turn=self._last_user_turn,
            session_state=session_state,
            recent_wording=self._recent_wording,
            profile_id=self.profile_id,
        )
        # Preview reflects the caregiver setting; feature_enabled tells the
        # caller whether the live loop would actually fire (env gate included).
        result.feature_enabled = bool(self._env_v2_enabled and self._settings.proactive_v2_enabled)
        return result

    async def _log_skip(self, decision: EngineDecision):
        """Log a skipped check-in (global 'all' skips are log-only, not persisted)."""
        if decision.slot_id == "all":
            logger.debug("Scheduler global skip: %s", decision.reason)
            return
        record = CheckinFiredRecord(
            profile_id=self.profile_id,
            slot_id=decision.slot_id,
            checkin_type=decision.checkin_type,
            fired_at=datetime.now(),
            wording_id="",
            response_received=False,
            retry_count=0,
            skip_reason=decision.reason,
        )
        save_checkin_fired(record.to_dict())
        self._fired_history.append(record)
        logger.info("Skipped check-in: %s for profile %s (reason: %s)", 
                    decision.slot_id, self.profile_id, decision.reason)

    async def _log_expire(self, decision: EngineDecision):
        """Log an expired check-in."""
        record = CheckinFiredRecord(
            profile_id=self.profile_id,
            slot_id=decision.slot_id,
            checkin_type=decision.checkin_type,
            fired_at=datetime.now(),
            wording_id="",
            response_received=False,
            retry_count=0,
            skip_reason=decision.reason,
        )
        save_checkin_fired(record.to_dict())
        self._fired_history.append(record)
        logger.info("Expired check-in: %s for profile %s", decision.slot_id, self.profile_id)

# ---- Helpers for LLM call injection ----

def make_llm_caller():
    """Create a simple LLM call function for the scheduler.
    Returns None if provider not available."""
    try:
        from llm.provider import get_provider
        provider = get_provider()
        return lambda system, user: provider.complete(
            system=system, user=user, max_tokens=80, temperature=0.9
        )
    except Exception:
        return None


# Global scheduler instances per profile
_scheduler_instances: Dict[str, Scheduler] = {}


def get_scheduler_for_profile(profile_id: str) -> Scheduler:
    """Get or create a scheduler instance for the given profile."""
    if profile_id not in _scheduler_instances:
        data = get_profile(profile_id)
        if data is None:
            raise ValueError(f"Unknown profile: {profile_id}")
        _scheduler_instances[profile_id] = Scheduler(Profile(data))
    return _scheduler_instances[profile_id]
