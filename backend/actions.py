"""
actions.py

Safe, fixed-list task actions that Sahaara can perform.
Only claims an action if it actually succeeded.
No open-ended device control.

Fixed actions:
- tell_time() — returns current local time
- play_song(name) — plays from local music files
- set_reminder(text, minutes) — one-time reminder
"""

import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

MUSIC_DIR = Path(__file__).parent / "voice" / "assets" / "music"


@dataclass
class ActionResult:
    success: bool
    action_type: str
    result_text: str  # what Sahaara can say about it
    detail: dict = None
    
    def __post_init__(self):
        if self.detail is None:
            self.detail = {}


def tell_time() -> ActionResult:
    """Returns the current time in a gentle format."""
    now = datetime.now()
    hour = now.hour
    minute = now.minute
    
    # Format gently
    if minute == 0:
        time_str = f"{hour} o'clock"
    elif minute < 10:
        time_str = f"just past {hour} o'clock"
    elif minute == 30:
        time_str = f"half past {hour}"
    elif minute == 15:
        time_str = f"quarter past {hour}"
    elif minute == 45:
        time_str = f"quarter to {hour + 1}"
    else:
        time_str = f"{minute} past {hour}"
    
    return ActionResult(
        success=True,
        action_type="tell_time",
        result_text=f"It's {time_str}.",
        detail={"hour": hour, "minute": minute, "iso": now.isoformat()},
    )


def play_song(song_name: str) -> ActionResult:
    """Try to play a song from local music files.
    Does fuzzy matching against filenames in MUSIC_DIR.
    """
    MUSIC_DIR.mkdir(parents=True, exist_ok=True)
    
    # List available files
    available = list(MUSIC_DIR.glob("*.mp3")) + list(MUSIC_DIR.glob("*.wav"))
    if not available:
        return ActionResult(
            success=False,
            action_type="play_song",
            result_text="I'd love to play that, but I don't have any music files loaded yet.",
        )
    
    # Fuzzy match: check if song_name appears in any filename
    name_lower = song_name.lower().replace(" ", "_").replace("'", "")
    for filepath in available:
        filename = filepath.stem.lower().replace(" ", "_").replace("'", "")
        if name_lower in filename or filename in name_lower:
            return ActionResult(
                success=True,
                action_type="play_song",
                result_text=f"I found that song. Playing {filepath.stem} for you now.",
                detail={"filename": filepath.name, "path": str(filepath)},
            )
    
    # No match — suggest what's available
    song_list = ", ".join(f.stem.replace("_", " ") for f in available[:5])
    return ActionResult(
        success=False,
        action_type="play_song",
        result_text=f"I don't have that exact song, but I do have: {song_list}.",
    )


def set_reminder(text: str, minutes: int, scheduler=None) -> ActionResult:
    """Set a one-time reminder. Requires scheduler instance to store it."""
    if not scheduler:
        return ActionResult(
            success=False,
            action_type="set_reminder",
            result_text="I'm sorry, I can't set reminders right now.",
        )
    
    fire_at = time.time() + (minutes * 60)
    scheduler.add_one_time_reminder(text, fire_at)
    
    if minutes <= 1:
        time_desc = "in a moment"
    elif minutes < 60:
        time_desc = f"in {minutes} minutes"
    else:
        hours = minutes // 60
        mins = minutes % 60
        time_desc = f"in {hours} hour{'s' if hours > 1 else ''}"
        if mins:
            time_desc += f" and {mins} minutes"
    
    return ActionResult(
        success=True,
        action_type="set_reminder",
        result_text=f"I'll remind you about that {time_desc}.",
        detail={"text": text, "minutes": minutes, "fire_at": fire_at},
    )


# ---- Detection patterns for action triggers ----

import re

ACTION_TRIGGERS = [
    # tell_time
    (r"\bwhat(?:'?s| is) (?:the )?time\b", "tell_time"),
    (r"\btell (?:me )?(?:the )?time\b", "tell_time"),
    (r"\bwhat time is it\b", "tell_time"),
    # play_song
    (r"\b(?:play|put on|can you play) (?:the |some |a )?(?:song |music )?(.+?)(?: please| for me| now)?[?.!]?$", "play_song"),
    (r"\b(?:i want to|can i|let me) hear (.+?)(?: please| now)?[?.!]?$", "play_song"),
    # set_reminder (two patterns with different group layouts)
    (r"\bremind me (?:about |to |in )?(.+?)(?: in| after) (\d+)\s*(minute|minutes|min|mins|hour|hours)\b", "set_reminder"),
    (r"\b(set|create) (?:a |an )?reminder (?:for |about )?(.+?)(?:in|after) (\d+)\s*(minute|minutes|min|mins)\b", "set_reminder_alt"),
]


def detect_action(user_message: str) -> Optional[dict]:
    """Detect if the user message triggers a known action.
    Returns {action_type, params} or None.
    """
    text_lower = user_message.lower().strip()
    
    for pattern, action_type in ACTION_TRIGGERS:
        match = re.search(pattern, text_lower)
        if not match:
            continue
        
        if action_type == "tell_time":
            return {"action_type": "tell_time", "params": {}}
        
        elif action_type == "play_song":
            song = match.group(1).strip().rstrip(".").rstrip("?")
            # Filter out common non-song endings
            invalid_songs = {"for me", "for you", "please", "now", "something", "anything", "music", "a song", "some music"}
            if song and len(song) > 1 and song.lower() not in invalid_songs:
                return {"action_type": "play_song", "params": {"song_name": song}}
        
        elif action_type == "set_reminder":
            # remind_me pattern: group 1 = text, group 2 = number, group 3 = unit
            reminder_text = match.group(1).strip().rstrip(" ,")
            try:
                number = int(match.group(2).strip())
            except ValueError:
                continue
            unit = match.group(3)
            minutes = number
            if "hour" in unit:
                minutes = number * 60
            return {
                "action_type": "set_reminder",
                "params": {"text": reminder_text, "minutes": minutes},
            }
        
        elif action_type == "set_reminder_alt":
            # set/create pattern: group 1 = verb, group 2 = text, group 3 = number, group 4 = unit
            reminder_text = match.group(2).strip().rstrip(" ,")
            try:
                number = int(match.group(3).strip())
            except ValueError:
                continue
            unit = match.group(4)
            minutes = number
            if "hour" in unit:
                minutes = number * 60
            return {
                "action_type": "set_reminder",
                "params": {"text": reminder_text, "minutes": minutes},
            }
    
    return None


if __name__ == "__main__":
    # Quick test
    print("=== tell_time ===")
    r = tell_time()
    print(f"  {r.result_text}")
    
    print("\n=== play_song ===")
    r = play_song("lag ja gale")
    print(f"  success={r.success}, {r.result_text}")
    r = play_song("nonexistent")
    print(f"  success={r.success}, {r.result_text}")
    
    print("\n=== detect_action ===")
    tests = [
        "What time is it?",
        "Tell me the time please",
        "Play Lag Ja Gale",
        "Can you play some music for me",
        "Remind me to call Rohan in 30 minutes",
        "Set a reminder for chai in 5 mins",
        "Hello how are you",
    ]
    for t in tests:
        a = detect_action(t)
        if a:
            print(f"  '{t}' -> {a['action_type']} {a['params']}")
        else:
            print(f"  '{t}' -> no action")