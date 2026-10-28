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

import json
import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode
from urllib.request import urlopen, Request

MUSIC_DIR = Path(__file__).parent / "voice" / "assets" / "music"

# Pune, Maharashtra coordinates (from the profile's hometown)
PUNE_LATITUDE = 18.5204
PUNE_LONGITUDE = 73.8567


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


def set_timer(minutes: int, label: str, scheduler=None) -> ActionResult:
    """Set a one-shot timer. Requires scheduler instance to store it."""
    if not scheduler:
        return ActionResult(
            success=False,
            action_type="set_timer",
            result_text="I'm sorry, I can't set timers right now.",
        )
    
    fire_at = time.time() + (minutes * 60)
    scheduler.add_one_time_reminder(f"Timer: {label}", fire_at)
    
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
        action_type="set_timer",
        result_text=f"I've set a timer for {time_desc}.",
        detail={"text": label, "minutes": minutes, "fire_at": fire_at},
    )


def parse_duration(text: str) -> Optional[int]:
    """Parse spoken duration like 'ten minutes', 'half an hour', '5 min'.
    
    Returns minutes or None if unparseable.
    """
    text = text.lower().strip()
    
    # Handle "half an hour" / "half hour"
    if re.search(r"\bhalf ?(an )?hour\b", text):
        return 30
    
    # Handle "an hour" / "one hour"
    if re.search(r"\b(an|one) hour\b", text):
        return 60
    
    # Handle "X minutes" / "X minute" / "X min" / "X mins"
    match = re.search(r"(\d+)\s*(minute|minutes|min|mins)", text)
    if match:
        return int(match.group(1))
    
    # Handle written numbers
    word_to_num = {
        "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
        "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
        "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20,
        "twenty-five": 25, "thirty": 30, "forty": 40, "fifty": 50,
        "sixty": 60,
    }
    
    match = re.search(r"(?<![-\w])(" + "|".join(word_to_num.keys()) + r")\s*(minute|minutes|min|mins)\b", text)
    if match:
        return word_to_num[match.group(1)]
    
    return None


def list_timers(profile_id: str) -> ActionResult:
    """List pending (unresponded) timers for a profile."""
    import database
    timers = database.list_timers(profile_id)
    
    if not timers:
        return ActionResult(
            success=True,
            action_type="list_timers",
            result_text="You don't have any timers set.",
            detail={"timers": []},
        )
    
    timer_list = []
    for t in timers:
        mins = int((t["fired_at"] - time.time()) / 60)
        if mins <= 0:
            mins = 0
        timer_list.append({
            "slot_id": t["slot_id"],
            "label": t.get("detail", {}).get("text", "timer") if isinstance(t.get("detail"), dict) else "timer",
            "minutes_remaining": max(0, mins),
        })
    
    timer_desc = ", ".join(f"{t['label']} in {t['minutes_remaining']} min" for t in timer_list)
    return ActionResult(
        success=True,
        action_type="list_timers",
        result_text=f"You have {len(timer_list)} timer(s): {timer_list}.",
        detail={"timers": timer_list},
    )


def cancel_timer(timer_id: str, scheduler=None) -> ActionResult:
    """Cancel a pending timer by slot_id."""
    import database
    
    # We need the profile_id to cancel. The timer_id is the slot_id.
    # We need to know which profile this belongs to. For now, we'll
    # need to look it up. But since cancel_timer is called from conversation.py
    # which has access to the session/profile, we should pass profile_id.
    # However, the current signature only takes timer_id. Let's look up
    # the profile from the timer.
    
    # First, try to find which profile this timer belongs to
    conn = database.get_connection()
    try:
        row = conn.execute(
            "SELECT profile_id FROM checkin_fired_history WHERE slot_id = ? AND response_received = 0",
            (timer_id,)
        ).fetchone()
    finally:
        conn.close()
    
    if not row:
        return ActionResult(
            success=False,
            action_type="cancel_timer",
            result_text="I don't have a timer by that name.",
            detail={"timer_id": timer_id},
        )
    
    profile_id = row["profile_id"]
    
    if database.cancel_timer(profile_id, timer_id):
        return ActionResult(
            success=True,
            action_type="cancel_timer",
            result_text="I've cancelled that timer.",
            detail={"timer_id": timer_id, "profile_id": profile_id},
        )
    else:
        return ActionResult(
            success=False,
            action_type="cancel_timer",
            result_text="I couldn't cancel that timer. It may have already fired or been cancelled.",
            detail={"timer_id": timer_id, "profile_id": profile_id},
        )


def get_weather(latitude: float = PUNE_LATITUDE, longitude: float = PUNE_LONGITUDE) -> ActionResult:
    """Get current weather for a location using the Open-Meteo API.

    No API key required. Timeout is 3s; any exception is swallowed and
    returns a failure result so the caller can respond honestly.
    """
    import json

    params = {
        "latitude": latitude,
        "longitude": longitude,
        "current_weather": "true",
        "daily": "temperature_2m_max,temperature_2m_min,weathercode",
        "timezone": "auto",
    }
    full_url = f"https://api.open-meteo.com/v1/forecast?{urlencode(params)}"

    try:
        req = Request(full_url)
        with urlopen(req, timeout=3) as response:
            data = json.loads(response.read().decode())

        current = data.get("current_weather", {})
        daily = data.get("daily", {})

        temperature = current.get("temperature")
        weathercode = current.get("weathercode")
        daily_max_list = daily.get("temperature_2m_max", [])
        daily_min_list = daily.get("temperature_2m_min", [])
        daily_max = daily_max_list[0] if daily_max_list else None
        daily_min = daily_min_list[0] if daily_min_list else None

        condition = _weathercode_to_condition(weathercode)

        return ActionResult(
            success=True,
            action_type="get_weather",
            result_text=f"It's {temperature}°C and {condition.lower()} in Pune right now.",
            detail={
                "temperature": temperature,
                "condition": condition,
                "high": daily_max,
                "low": daily_min,
                "latitude": latitude,
                "longitude": longitude,
            },
        )
    except Exception:
        return ActionResult(
            success=False,
            action_type="get_weather",
            result_text="I'm sorry, I can't check the weather right now.",
            detail={},
        )


def _weathercode_to_condition(code: int | None) -> str:
    """Map WMO weather code to a human-readable condition."""
    if code is None:
        return "unknown"
    code_map = {
        0: "clear",
        1: "mainly clear",
        2: "partly cloudy",
        3: "overcast",
        45: "foggy",
        48: "depositing rime fog",
        51: "light drizzle",
        53: "moderate drizzle",
        55: "dense drizzle",
        56: "light freezing drizzle",
        57: "dense freezing drizzle",
        61: "slight rain",
        63: "moderate rain",
        65: "heavy rain",
        66: "light freezing rain",
        67: "heavy freezing rain",
        71: "slight snow fall",
        73: "moderate snow fall",
        75: "heavy snow fall",
        77: "snow grains",
        80: "slight rain showers",
        81: "moderate rain showers",
        82: "heavy rain showers",
        85: "slight snow showers",
        86: "heavy snow showers",
        95: "thunderstorm",
        96: "thunderstorm with slight hail",
        99: "thunderstorm with heavy hail",
    }
    return code_map.get(code, "unknown")


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
    # weather
    (r"\bhow (?:is|'s) the weather\b", "get_weather"),
    (r"\bwhat'?s the weather like\b", "get_weather"),
    (r"\bwill it (rain|snow|storm)\b", "get_weather"),
    (r"\bis it (hot|cold|warm|chilly|humid)\b", "get_weather"),
    # set_timer
    (r"\bset (?:a )?timer for (.+?)\s*(minute|minutes|min|mins|hour|hours)(?: (?:called|named) (.+?))?(?: please| now|[.!?])?$", "set_timer"),
    (r"\btimer for (.+?)\s*(minute|minutes|min|mins|hour|hours)(?: (?:called|named) (.+?))?(?: please| now|[.!?])?$", "set_timer"),
    # list_timers
    (r"\b(?:what|which) timers? (?:do i have|are set|active)\b", "list_timers"),
    (r"\b(?:list|show) (?:my )?timers\b", "list_timers"),
    # cancel_timer
    (r"\b(?:cancel|remove|delete|stop) (?:the )?(?:timer|alarm)(?: for (.+?))?(?: please| now|[.!?])?$", "cancel_timer"),
    (r"\bcancel (?:the )?(?:timer|alarm)(?: named| called) (.+?)(?: please| now|[.!?])?$", "cancel_timer"),
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
        
        elif action_type == "set_timer":
            # timer pattern: group 1 = duration text, group 2 = unit, group 3 = optional label
            minutes = parse_duration(match.group(1) + " " + match.group(2))
            if minutes is None:
                continue
            label = match.group(3).strip() if match.group(3) else "timer"
            return {
                "action_type": "set_timer",
                "params": {"text": label, "minutes": minutes},
            }
        
        elif action_type == "list_timers":
            return {"action_type": "list_timers", "params": {}}
        
        elif action_type == "cancel_timer":
            # cancel_timer pattern: group 1 = optional label
            label = match.group(1).strip() if match.group(1) else None
            return {
                "action_type": "cancel_timer",
                "params": {"label": label},
            }
        
        elif action_type == "get_weather":
            return {"action_type": "get_weather", "params": {}}
    
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