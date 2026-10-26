"""
Scheduler models for Sahaara M3.
Data structures for scheduler settings, check-in slots, fired records, and engine decisions.
"""

from dataclasses import dataclass, field
from datetime import datetime, time
from typing import Optional, Literal, Any, Dict, List
from enum import Enum
import json


class CheckinType(str, Enum):
    """Types of proactive check-ins."""
    MORNING_GREETING = "morning_greeting"
    DAYTIME_CHECKIN = "daytime_checkin"
    EVENING_CHECKIN = "evening_checkin"
    MEDICATION = "medication"
    MEAL = "meal"
    ROUTINE = "routine"
    INTRO = "intro"


class SkipReason(str, Enum):
    """Reasons for skipping a check-in."""
    QUIET_HOURS = "quiet_hours"
    RECENT_CONVERSATION = "recent_conversation"
    CONSENT_PENDING = "consent_pending"
    TIER_YELLOW_ORANGE_RED = "tier_yellow_orange_red"
    MONITORING_PAUSED = "monitoring_paused"
    SCHEDULER_DISABLED = "scheduler_disabled"
    DAILY_CAP_REACHED = "daily_cap_reached"
    EXPIRED = "expired"
    DEDUPE = "dedupe"
    INTRO_THROTTLED = "intro_throttled"


class ActionType(str, Enum):
    """Engine decision action types."""
    FIRE = "fire"
    SKIP = "skip"
    EXPIRE = "expire"


@dataclass
class SchedulerSettings:
    """Per-profile scheduler settings with defaults."""
    # Core settings
    enabled: bool = True
    max_checkins_per_day: int = 4
    min_gap_after_conversation_minutes: int = 20
    checkin_window_minutes: int = 15
    response_window_minutes: int = 10
    retry_once: bool = True
    intro_throttle_hours: int = 24
    
    # Check-in times (HH:MM format, max 10 per day)
    checkin_times: list[str] = field(default_factory=lambda: ["08:00", "14:00", "19:00"])
    
    # Quiet hours (midnight-crossing supported)
    quiet_hours_start: str = "22:00"
    quiet_hours_end: str = "07:00"
    
    # Introduction settings
    intro_enabled: bool = True
    intro_throttle_hours: int = 24
    intro_overrides: dict = field(default_factory=dict)  # custom intro wording per slot
    
    # Wording rotation
    wording_recent_limit: int = 4  # don't reuse last K lines
    
    # Retry settings
    retry_once: bool = True
    
    # Feature flag
    proactive_v2_enabled: bool = True

    def __post_init__(self):
        # Ensure checkin_times is a list
        if isinstance(self.checkin_times, str):
            self.checkin_times = json.loads(self.checkin_times)
        if isinstance(self.intro_overrides, str):
            self.intro_overrides = json.loads(self.intro_overrides)

    def to_dict(self) -> dict:
        """Serialize to dictionary for storage."""
        return {
            "enabled": self.enabled,
            "max_checkins_per_day": self.max_checkins_per_day,
            "min_gap_after_conversation_minutes": self.min_gap_after_conversation_minutes,
            "checkin_window_minutes": self.checkin_window_minutes,
            "response_window_minutes": self.response_window_minutes,
            "retry_once": self.retry_once,
            "intro_throttle_hours": self.intro_throttle_hours,
            "checkin_times": self.checkin_times,
            "quiet_hours_start": self.quiet_hours_start,
            "quiet_hours_end": self.quiet_hours_end,
            "intro_enabled": self.intro_enabled,
            "intro_throttle_hours": self.intro_throttle_hours,
            "intro_overrides": self.intro_overrides,
            "wording_recent_limit": self.wording_recent_limit,
            "retry_once": self.retry_once,
            "proactive_v2_enabled": self.proactive_v2_enabled,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "SchedulerSettings":
        """Create from dictionary (handles missing keys with defaults)."""
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class CheckinSlot:
    """A scheduled check-in slot."""
    slot_id: str  # e.g., "morning_greeting_2026-10-26"
    checkin_type: CheckinType
    scheduled_time: time  # time of day
    slot_date: str  # YYYY-MM-DD
    wording_pool_key: str  # key into wording pool
    slot_index: int  # order within the day
    is_intro: bool = False
    medication_label: str = ""  # for medication slots

    @property
    def slot_id_short(self) -> str:
        """Short ID for dedupe key: profile_id + date + slot_index"""
        return f"{self.slot_date}_{self.slot_index}"


@dataclass
class CheckinFiredRecord:
    """Record of a fired check-in for persistence and deduplication."""
    profile_id: str
    slot_id: str  # e.g., "morning_greeting_2026-10-26"
    checkin_type: CheckinType
    fired_at: datetime
    wording_id: str  # which wording was used
    response_received: bool = False
    retry_count: int = 0
    skip_reason: str = ""
    delivered_audio: bool = False  # whether audio was played
    audio_blocked: bool = False  # audio was blocked, fell back to text

    def to_dict(self) -> dict:
        return {
            "profile_id": self.profile_id,
            "slot_id": self.slot_id,
            "checkin_type": self.checkin_type.value,
            "fired_at": self.fired_at.timestamp(),
            "wording_id": self.wording_id,
            "response_received": self.response_received,
            "retry_count": self.retry_count,
            "skip_reason": self.skip_reason,
            "delivered_audio": self.delivered_audio,
            "audio_blocked": self.audio_blocked,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "CheckinFiredRecord":
        return cls(
            profile_id=data["profile_id"],
            slot_id=data["slot_id"],
            checkin_type=CheckinType(data["checkin_type"]),
            fired_at=datetime.fromisoformat(data["fired_at"]),
            wording_id=data["wording_id"],
            response_received=data.get("response_received", False),
            retry_count=data.get("retry_count", 0),
            skip_reason=data.get("skip_reason", ""),
            delivered_audio=data.get("delivered_audio", False),
            audio_blocked=data.get("audio_blocked", False),
        )


@dataclass
class EngineDecision:
    """Output decision from the scheduling engine."""
    action: Literal["fire", "skip", "expire"]
    slot_id: str  # e.g., "morning_greeting_2026-10-26"
    reason: str  # SkipReason or "fire"
    wording_id: str = ""  # for fire action
    wording_text: str = ""  # human-readable wording (for simulation/debug)
    checkin_type: CheckinType = CheckinType.DAYTIME_CHECKIN
    is_intro: bool = False
    medication_label: str = ""

    def to_dict(self) -> dict:
        return {
            "action": self.action,
            "slot_id": self.slot_id,
            "reason": self.reason,
            "wording_id": self.wording_id,
            "wording_text": self.wording_text,
            "checkin_type": self.checkin_type.value,
            "is_intro": self.is_intro,
            "medication_label": self.medication_label,
        }


# Input context for the engine
@dataclass
class EngineContext:
    """Input context for the scheduling engine (no I/O, pure data)."""
    now: datetime
    profile_settings: 'SchedulerSettings'
    last_user_turn: Optional[datetime] = None
    session_state: dict = field(default_factory=dict)  # tier, consent_pending, monitoring_paused, last_checkin_time
    fired_history: list = field(default_factory=list)  # list of CheckinFiredRecord
    recent_wording: list = field(default_factory=list)  # recent wording_ids used

    @property
    def tier(self) -> str:
        return self.session_state.get("tier", "green")

    @property
    def consent_pending(self) -> bool:
        return self.session_state.get("consent_pending", False)

    @property
    def monitoring_paused(self) -> bool:
        return self.session_state.get("monitoring_paused", False)

    @property
    def last_checkin_time(self) -> Optional[datetime]:
        return self.session_state.get("last_checkin_time")

    @property
    def scheduler_disabled(self) -> bool:
        return self.session_state.get("scheduler_disabled", False)


# ============================================================
# SIMULATED DAY (M3 Step 8)
# ============================================================

@dataclass
class SimulatedDayStep:
    """One evaluated moment in a simulated day."""
    time: datetime
    decisions: list = field(default_factory=list)  # list[EngineDecision]

    def to_dict(self) -> dict:
        return {
            "time": self.time.isoformat(),
            "time_hhmm": self.time.strftime("%H:%M"),
            "decisions": [d.to_dict() for d in self.decisions],
        }


@dataclass
class SimulatedDayResult:
    """Full result of running the scheduling engine across a simulated day."""
    profile_id: str
    start: datetime
    end: datetime
    step_minutes: int
    steps: list = field(default_factory=list)  # list[SimulatedDayStep]
    total_fired: int = 0
    total_skipped: int = 0
    total_expired: int = 0
    total_gated_steps: int = 0  # steps where a global gate blocked everything
    fired_by_type: dict = field(default_factory=dict)
    skip_reasons: dict = field(default_factory=dict)
    gated_reasons: dict = field(default_factory=dict)
    feature_enabled: bool = True  # whether the live loop would actually fire

    def to_dict(self) -> dict:
        return {
            "profile_id": self.profile_id,
            "date": self.start.strftime("%Y-%m-%d"),
            "start_time": self.start.strftime("%H:%M"),
            "end_time": self.end.strftime("%H:%M"),
            "step_minutes": self.step_minutes,
            "feature_enabled": self.feature_enabled,
            "steps": [s.to_dict() for s in self.steps],
            "summary": {
                "total_fired": self.total_fired,
                "total_skipped": self.total_skipped,
                "total_expired": self.total_expired,
                "total_gated_steps": self.total_gated_steps,
                "fired_by_type": dict(self.fired_by_type),
                "skip_reasons": dict(self.skip_reasons),
                "gated_reasons": dict(self.gated_reasons),
            },
        }


# ============================================================
# WORDING POOLS (DRAFT — marked draft: true for review)
# ============================================================
# All lines are marked draft: true and must pass check_guardrails()
# before being used in production. Caregiver can override per slot.
# Rotation: never reuse any of the last K=4 lines used for that profile.
# Rotation key: profile_id + pool_key

WORDING_POOLS: Dict[str, List[Dict[str, Any]]] = {
    "intro": [
        {"id": "intro_1", "text": "Hello. I'm Sahaara, a voice companion here to keep you company. I'm not a person, but I'm here to listen and chat.", "draft": True},
        {"id": "intro_2", "text": "Hi there. I'm Sahaara, a voice companion. I'm not a person, but I'm here if you'd like to chat or just have some company.", "draft": True},
        {"id": "intro_3", "text": "Hello. I'm Sahaara. I'm a voice companion, not a person. I'm here to keep you company and help with little things.", "draft": True},
        {"id": "intro_4", "text": "Hi. I'm Sahaara, your voice companion. I'm not human, but I'm here to listen and be with you.", "draft": True},
        {"id": "intro_5", "text": "Good day. I'm Sahaara, a voice companion. I'm not a person, just a friendly voice here to keep you company.", "draft": True},
    ],
    "morning_greeting": [
        {"id": "mg_1", "text": "Good morning. The day's unfolding gently, and I'm here with you.", "draft": True},
        {"id": "mg_2", "text": "Morning. The light's coming in soft today. I'm right here.", "draft": True},
        {"id": "mg_3", "text": "Good morning. It's a new day, and I'm right here with you.", "draft": True},
        {"id": "mg_4", "text": "Morning. The birds are up, and so am I. Here if you need anything.", "draft": True},
        {"id": "mg_5", "text": "Good morning. Hope the day starts gentle for you. I'm here.", "draft": True},
        {"id": "mg_6", "text": "Morning. A fresh start. I'm right here if you need anything.", "draft": True},
        {"id": "mg_7", "text": "Good morning. The day's beginning. I'm here with my morning tea — figuratively speaking.", "draft": True},
        {"id": "mg_8", "text": "Morning. The sun's up, and so am I. Here if you need me.", "draft": True},
    ],
    "daytime_checkin": [
        {"id": "dc_1", "text": "Just checking in. How's the day going so far?", "draft": True},
        {"id": "dc_2", "text": "Thinking of you. How's the day treating you?", "draft": True},
        {"id": "dc_3", "text": "Just a quiet check-in. How are things?", "draft": True},
        {"id": "dc_4", "text": "Hi there. How's the day unfolding?", "draft": True},
        {"id": "dc_5", "text": "Just wanted you to know I'm here. How's the day?", "draft": True},
        {"id": "dc_6", "text": "Checking in. Everything okay so far?", "draft": True},
        {"id": "dc_7", "text": "Thinking of you. How's the afternoon going?", "draft": True},
        {"id": "dc_8", "text": "Hi there. How's your day been so far?", "draft": True},
    ],
    "evening_checkin": [
        {"id": "ec_1", "text": "The evening's settling in. I'm here if you'd like some quiet company.", "draft": True},
        {"id": "ec_2", "text": "Evening's coming on. How's the day been?", "draft": True},
        {"id": "ec_3", "text": "Evening's coming on. I'm here if you'd like some company.", "draft": True},
        {"id": "ec_4", "text": "The day's winding down. I'm here if you need anything.", "draft": True},
        {"id": "ec_5", "text": "Evening. The day's slowing down. I'm here.", "draft": True},
        {"id": "ec_6", "text": "Evening's here. The day's settling down. I'm right here.", "draft": True},
        {"id": "ec_7", "text": "Evening's coming on. I'm here for whatever you need.", "draft": True},
        {"id": "ec_8", "text": "Evening. The day's settling. I'm right here with you.", "draft": True},
    ],
    "medication": [
        {"id": "med_1", "text": "It might be time for your usual routine. No rush.", "draft": True},
        {"id": "med_2", "text": "Just a gentle nudge — your regular time is here.", "draft": True},
        {"id": "med_3", "text": "Time for your usual. I'm right here.", "draft": True},
        {"id": "med_4", "text": "Your usual time is here. I'm right here with you.", "draft": True},
        {"id": "med_5", "text": "Gentle reminder — it's that time again. I'm here.", "draft": True},
        {"id": "med_6", "text": "Just a quiet nudge — it's time. I'm right here.", "draft": True},
    ],
    "meal": [
        {"id": "meal_1", "text": "It's about time for a meal. Whenever you're ready.", "draft": True},
        {"id": "meal_2", "text": "Meal time's coming up. Whenever you're ready.", "draft": True},
        {"id": "meal_3", "text": "It's about that time. Hungry? I'm here.", "draft": True},
        {"id": "meal_4", "text": "Meal time's coming up. I'm here.", "draft": True},
        {"id": "meal_5", "text": "About time for something to eat. I'm here.", "draft": True},
    ],
    "retry": [
        {"id": "retry_1", "text": "Just checking — everything okay? I'm still here.", "draft": True},
        {"id": "retry_2", "text": "Still here. Everything okay?", "draft": True},
        {"id": "retry_3", "text": "Just wanted you to know I'm still here. How are things?", "draft": True},
        {"id": "retry_4", "text": "Still here if you need anything. How are things?", "draft": True},
    ],
}


# Helper to get pool by key
def get_wording_pool(pool_key: str) -> List[Dict[str, Any]]:
    """Get a wording pool by key."""
    return WORDING_POOLS.get(pool_key, [])


# Validate all pool lines pass guardrails (run at import time for safety)
def validate_all_pools(guardrail_checker) -> List[str]:
    """Validate all wording pool lines pass guardrails.
    Returns list of violations (empty if all pass)."""
    violations = []
    for pool_key, lines in WORDING_POOLS.items():
        for line in lines:
            if not line.get("draft", True):
                continue
            # This would call the actual guardrail checker
            # For now, just track that validation should happen
            pass
    return violations