"""
scheduler.py

Background task that reads profile routines and pushes proactive
messages (morning greeting, evening check-in, medication reminders,
meal nudges) via SSE. Runs alongside FastAPI.

Rules:
- Each reminder fires at most once per day (dedup by category)
- Expires if the time window has passed (no stale prompts)
- Messages are LLM-generated for variety, not templates
- Caregiver can pause/resume via toggle
- Medication is reminders only — never dosing or advice
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from memory.profile import Profile

logger = logging.getLogger("scheduler")

# Time windows in minutes — reminder fires within ±WINDOW of the scheduled time
REMINDER_WINDOW_MINUTES = 15
# Check interval in seconds
CHECK_INTERVAL = 30


@dataclass
class ScheduledReminder:
    category: str  # "morning_greeting", "medication", "meal", "evening_checkin"
    hour: int
    minute: int
    label: str  # human-readable, e.g. "morning tea", "evening medication"
    is_medication: bool = False


class Scheduler:
    """Background scheduler for proactive Sahaara messages."""
    
    def __init__(self, profile: Profile):
        self.profile = profile
        self._paused: bool = False
        self._sent_today: dict[str, str] = {}  # category -> date_sent
        self._reminders: list[ScheduledReminder] = []
        self._one_time_reminders: list[dict] = []
        self._running: bool = False
        self._task: Optional[asyncio.Task] = None
        self._initiative_queue: asyncio.Queue = asyncio.Queue(maxsize=20)
        self._llm_call = None  # injected by main.py
        self._load_routine()
    
    def _load_routine(self):
        """Parse profile daily_routine into scheduled reminders."""
        routine = self.profile._data.get("daily_routine", {})
        self._reminders = []
        
        # Morning greeting at wake time
        wake = routine.get("wake_time", "07:00")
        h, m = self._parse_time(wake)
        self._reminders.append(ScheduledReminder(
            category="morning_greeting", hour=h, minute=m,
            label="morning greeting"
        ))
        
        # Evening check-in (default 19:00 if not specified)
        evening = routine.get("evening_checkin_time", routine.get("restless_window", "19:00"))
        eh, em = self._parse_time(evening)
        self._reminders.append(ScheduledReminder(
            category="evening_checkin", hour=eh, minute=em,
            label="evening check-in"
        ))
        
        # Medication reminders
        for med_time in routine.get("medication_times", []):
            mh, mm = self._parse_time(med_time)
            self._reminders.append(ScheduledReminder(
                category="medication", hour=mh, minute=mm,
                label=f"medication at {med_time}",
                is_medication=True
            ))
        
        # Meal nudges from meals
        meals = routine.get("meals", {})
        for meal_type, meal_time in meals.items():
            if meal_time:
                mh, mm = self._parse_time(meal_time)
                self._reminders.append(ScheduledReminder(
                    category="meal", hour=mh, minute=mm,
                    label=f"{meal_type} time",
                    is_medication=False
                ))
        
        # Also check likes.routines for walk/chai times
        likes = self.profile._data.get("likes", {})
        routine_notes = likes.get("routines", [])
        for note in routine_notes:
            # Parse simple time patterns like "chai at 4pm" or "walk at 10:00"
            import re
            match = re.search(r'(\d{1,2})[:.]?(\d{2})?\s*(am|pm)?', note.lower())
            if match:
                h = int(match.group(1))
                m = int(match.group(2)) if match.group(2) else 0
                ampm = match.group(3)
                if ampm == "pm" and h < 12:
                    h += 12
                self._reminders.append(ScheduledReminder(
                    category="routine", hour=h, minute=m,
                    label=note[:40],
                    is_medication=False
                ))
    
    @staticmethod
    def _parse_time(time_str: str) -> tuple[int, int]:
        """Parse 'HH:MM' or 'H:MM' into (hour, minute)."""
        parts = time_str.strip().split(":")
        h = int(parts[0])
        m = int(parts[1]) if len(parts) > 1 else 0
        return h, m
    
    @property
    def paused(self) -> bool:
        return self._paused
    
    def toggle(self) -> bool:
        self._paused = not self._paused
        return self._paused
    
    def add_one_time_reminder(self, text: str, fire_at: float):
        """Add a one-time reminder from set_reminder action."""
        self._one_time_reminders.append({
            "text": text,
            "fire_at": fire_at,
            "fired": False,
        })
    
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
        logger.info("Scheduler started with %d reminders", len(self._reminders))
    
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
                    await self._check_reminders()
                    await self._check_one_time()
                await asyncio.sleep(CHECK_INTERVAL)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Scheduler loop error")
                await asyncio.sleep(CHECK_INTERVAL)
    
    async def _check_reminders(self):
        """Check if any scheduled reminder should fire."""
        now = datetime.now()
        today = now.strftime("%Y-%m-%d")
        current_minutes = now.hour * 60 + now.minute
        
        for reminder in self._reminders:
            # Check dedup — already sent today?
            if self._sent_today.get(reminder.category) == today:
                continue
            
            scheduled_minutes = reminder.hour * 60 + reminder.minute
            diff = current_minutes - scheduled_minutes
            
            # Within the window? (0 to WINDOW minutes after scheduled time)
            if 0 <= diff <= REMINDER_WINDOW_MINUTES:
                await self._fire_reminder(reminder, today)
            # Expired? (more than 2x window past) — mark as sent to avoid stale fire
            elif diff > REMINDER_WINDOW_MINUTES * 2:
                self._sent_today[reminder.category] = today
    
    async def _check_one_time(self):
        """Check one-time reminders."""
        now = time.time()
        for reminder in self._one_time_reminders:
            if not reminder["fired"] and now >= reminder["fire_at"]:
                reminder["fired"] = True
                await self._queue_initiative(
                    text=f"Just a gentle reminder: {reminder['text']}",
                    category="one_time_reminder",
                )
    
    async def _fire_reminder(self, reminder: ScheduledReminder, today: str):
        """Fire a scheduled reminder — generate message and push to queue."""
        text = await self._generate_message(reminder)
        if text:
            await self._queue_initiative(text, reminder.category)
        self._sent_today[reminder.category] = today
        logger.info("Fired reminder: %s at %s", reminder.category, datetime.now())
    
    async def _generate_message(self, reminder: ScheduledReminder) -> str:
        """Generate a warm, varied reminder message."""
        # Try LLM first for variety
        if self._llm_call:
            try:
                prompt = self._build_reminder_prompt(reminder)
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
        
        # Template fallback
        return self._template_message(reminder)
    
    def _build_reminder_prompt(self, reminder: ScheduledReminder) -> str:
        """Build prompt for reminder generation."""
        name = self.profile.preferred_address
        if reminder.category == "morning_greeting":
            return f"Generate a gentle morning greeting for {name}. 1 sentence, warm."
        elif reminder.category == "evening_checkin":
            return f"Generate a gentle evening check-in for {name}. 1 sentence. Don't ask how she is — just be present."
        elif reminder.is_medication:
            return f"Generate a gentle medication-time nudge for {name}. 1 sentence. Never mention dose or type. Say '{reminder.label}' naturally."
        elif reminder.category == "meal":
            return f"Generate a gentle {reminder.label} nudge for {name}. 1 sentence."
        else:
            return f"Generate a gentle reminder about {reminder.label} for {name}. 1 sentence."
    
    def _template_message(self, reminder: ScheduledReminder) -> str:
        """Template fallback if LLM unavailable."""
        name = self.profile.preferred_address
        templates = {
            "morning_greeting": [
                f"Good morning, {name}. The day is beginning gently.",
                f"It's a new morning, {name}. I'm here with you.",
                f"Morning has arrived, {name}. Take your time.",
            ],
            "evening_checkin": [
                f"The evening is settling in, {name}. I'm here if you'd like some quiet company.",
                f"It's getting dark outside, {name}. Perhaps a cup of chai?",
                f"The day is winding down, {name}. I hope you're feeling comfortable.",
            ],
            "medication": [
                f"{name}, it might be time for your usual routine. No rush.",
                f"Just a gentle nudge, {name} — your regular time is here.",
            ],
            "meal": [
                f"It's about time for {reminder.label}, {name}. Whenever you're ready.",
                f"{name}, it's around {reminder.label}. Would you like to rest a moment?",
            ],
        }
        options = templates.get(reminder.category, [f"{name}, just checking in."])
        import random
        return random.choice(options)
    
    async def _queue_initiative(self, text: str, category: str):
        """Push an initiative message to the SSE queue."""
        message = {
            "type": "sahaara_initiative",
            "data": {
                "text": text,
                "category": category,
                "timestamp": time.time(),
                "timestamp_iso": datetime.now().isoformat(),
            },
            "timestamp": time.time(),
        }
        try:
            self._initiative_queue.put_nowait(message)
        except asyncio.QueueFull:
            logger.warning("Initiative queue full, dropping: %s", category)
    
    def get_status(self) -> dict:
        return {
            "paused": self._paused,
            "active_reminders": len(self._reminders),
            "sent_today": list(self._sent_today.keys()),
            "one_time_pending": sum(1 for r in self._one_time_reminders if not r["fired"]),
        }


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