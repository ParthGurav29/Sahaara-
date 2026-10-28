"""
actions.py

Fixed-list tasks Sahaara can actually perform, and the parser that decides
when the person asked for one.

Design rules (owner decisions, 2026-10-28):
- Only claim an action that really happened. Every failure returns
  success=False with a result_text that says so.
- Timers and reminders run on REAL wall-clock time. The 300x demo
  compression used by the escalation engine is deliberately NOT applied —
  "set a timer for 5 minutes" means five minutes. Seconds are supported so a
  demo can still show one firing.
- Reminder text is sanitised before it is ever spoken: if it mentions
  medication in any form, Sahaara says "take your medication" and never the
  dose or the number. The person's exact words go to the dashboard only.
- Weather is deliberately NOT wired to any trigger (owner decision,
  2026-10-28). `get_weather()` is retained but dormant; nothing calls it.

Fixed actions:
- tell_time()                     — the current time
- set_timer(seconds, label)       — a one-off countdown
- set_reminder(seconds, label)    — a one-off reminder
- list_timers()                   — what is pending
- cancel_timer()                  — cancel a pending timer/reminder
- play_song(name)                 — a local music file, served at /media/...
- play_radio(station)             — a caregiver-approved stream
- stop_media()                    — stop whatever is playing
"""

import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from urllib.parse import quote, urlencode
from urllib.request import urlopen, Request

logger = logging.getLogger("actions")

MUSIC_DIR = Path(__file__).parent / "voice" / "assets" / "music"

# The browser streams local tracks from this backend path (see main.py).
MEDIA_URL_PREFIX = "/media"

# Pune, Maharashtra coordinates (kept for the dormant weather helper).
PUNE_LATITUDE = 18.5204
PUNE_LONGITUDE = 73.8567


@dataclass
class ActionResult:
    success: bool
    action_type: str
    result_text: str  # what Sahaara says about it
    detail: dict = None

    def __post_init__(self):
        if self.detail is None:
            self.detail = {}


# ---------------------------------------------------------------------------
# Time
# ---------------------------------------------------------------------------

_PERIODS = (
    (0, "in the morning"),
    (12, "in the afternoon"),
    (17, "in the evening"),
    (21, "at night"),
)


def format_clock(moment: datetime) -> str:
    """'4:32 in the afternoon' — 12-hour, no invented detail."""
    hour12 = moment.hour % 12 or 12
    period = "in the morning"
    for start, name in _PERIODS:
        if moment.hour >= start:
            period = name
    if moment.minute == 0:
        return f"{hour12} o'clock {period}"
    return f"{hour12}:{moment.minute:02d} {period}"


def tell_time(now: Optional[datetime] = None) -> ActionResult:
    """The current local time, stated plainly."""
    moment = now or datetime.now()
    return ActionResult(
        success=True,
        action_type="tell_time",
        result_text=f"It's {format_clock(moment)}.",
        detail={"hour": moment.hour, "minute": moment.minute, "iso": moment.isoformat()},
    )


# ---------------------------------------------------------------------------
# Durations and clock times
# ---------------------------------------------------------------------------

_UNIT_SECONDS = {
    "second": 1, "seconds": 1, "sec": 1, "secs": 1, "s": 1,
    "minute": 60, "minutes": 60, "min": 60, "mins": 60, "m": 60,
    "hour": 3600, "hours": 3600, "hr": 3600, "hrs": 3600, "h": 3600,
    "day": 86400, "days": 86400,
}

_WORD_NUMBERS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
    "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20,
    "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "ninety": 90,
}

_NUM = r"(?:\d+|" + "|".join(_WORD_NUMBERS.keys()) + r")"
_UNITS_ALT = "|".join(_UNIT_SECONDS.keys())


def _number(token: str) -> Optional[int]:
    token = token.strip().lower()
    if token.isdigit():
        return int(token)
    return _WORD_NUMBERS.get(token)


# Each entry: (regex, handler). Handlers are tried in order, so the
# "and a half" forms win over the plain "N hours" form.
def _find_duration(text: str) -> tuple[Optional[int], tuple[int, int]]:
    """Return (seconds, span) for the first duration phrase in `text`."""
    lowered = text.lower()

    match = re.search(r"\b(?:an?|one)\s+(hour|hours|hr|hrs)\s+and\s+a\s+half\b", lowered)
    if match:
        return 5400, match.span()

    match = re.search(rf"\b({_NUM})\s+(hour|hours|hr|hrs)\s+and\s+a\s+half\b", lowered)
    if match:
        value = _number(match.group(1))
        if value is not None:
            return value * 3600 + 1800, match.span()

    match = re.search(r"\b(?:an?|one)\s+and\s+a\s+half\s+(hour|hours|hr|hrs)\b", lowered)
    if match:
        return 5400, match.span()

    match = re.search(rf"\b({_NUM})\s+and\s+a\s+half\s+(hour|hours|hr|hrs)\b", lowered)
    if match:
        value = _number(match.group(1))
        if value is not None:
            return value * 3600 + 1800, match.span()

    match = re.search(r"\bhalf\s+(?:an\s+)?(hour|hours)\b", lowered)
    if match:
        return 1800, match.span()

    match = re.search(r"\b(?:a|one)\s+minute\s+and\s+a\s+half\b", lowered)
    if match:
        return 90, match.span()

    match = re.search(r"\b(?:an|one)\s+(hour|hours|hr|hrs)\b", lowered)
    if match:
        return 3600, match.span()

    match = re.search(rf"\b({_NUM})\s*({_UNITS_ALT})\b", lowered)
    if match:
        value = _number(match.group(1))
        unit = match.group(2)
        if value is not None:
            return value * _UNIT_SECONDS.get(unit, 0), match.span()

    return None, (0, 0)



def parse_duration_seconds(text: str) -> Optional[int]:
    """Seconds in '30 seconds', 'ten minutes', 'half an hour', '90 sec'."""
    seconds, _ = _find_duration(text.lower())
    return seconds


def format_duration(seconds: int) -> str:
    """'30 seconds', '5 minutes', '1 hour and 30 minutes'."""
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds} second{'s' if seconds != 1 else ''}"
    if seconds < 3600 and seconds % 60 == 0:
        minutes = seconds // 60
        return f"{minutes} minute{'s' if minutes != 1 else ''}"
    if seconds < 3600:
        minutes, extra = divmod(seconds, 60)
        return f"{minutes} minute{'s' if minutes != 1 else ''} and {extra} seconds"
    hours, rest = divmod(seconds, 3600)
    text = f"{hours} hour{'s' if hours != 1 else ''}"
    if rest:
        minutes = rest // 60
        if minutes:
            text += f" and {minutes} minute{'s' if minutes != 1 else ''}"
    return text


_CLOCK_RE = re.compile(
    r"\b(?:at|by|for)\s+(?P<h1>\d{1,2})(?:[:.](?P<m1>\d{2}))?\s*"
    r"(?P<ap1>am|pm|a\.m\.|p\.m\.)?\b"
    r"|\b(?P<h2>\d{1,2})(?:[:.](?P<m2>\d{2}))?\s*(?P<ap2>am|pm|a\.m\.|p\.m\.)\b",
    re.I,
)


def parse_clock_time(text: str, now: Optional[datetime] = None) -> Optional[datetime]:
    """Next occurrence of 'at 7', 'at 6:30 pm', 'for 6 pm', 'at 18:30'."""
    match = _CLOCK_RE.search(text)
    if not match:
        return None
    hour = int(match.group("h1") or match.group("h2"))
    minute = int(match.group("m1") or match.group("m2") or 0)
    meridiem = (match.group("ap1") or match.group("ap2") or "").replace(".", "").lower()

    if minute > 59 or hour > 23:
        return None
    if meridiem == "pm" and hour < 12:
        hour += 12
    elif meridiem == "am" and hour == 12:
        hour = 0
    elif not meridiem and hour <= 7:
        # "at 7" from someone who means the evening. 7am already gone by then
        # in practice; prefer the next occurrence rather than guessing hard.
        pass

    reference = now or datetime.now()
    candidates = [reference.replace(hour=hour, minute=minute, second=0, microsecond=0)]
    if not meridiem and hour < 12:
        # "at 7" — could be morning or evening. Take whichever is sooner.
        candidates.append(
            reference.replace(hour=hour + 12, minute=minute, second=0, microsecond=0)
        )

    future = []
    for candidate in candidates:
        if candidate <= reference:
            candidate += timedelta(days=1)
        future.append(candidate)
    return min(future, key=lambda c: c - reference)


# ---------------------------------------------------------------------------
# Reminder text safety
# ---------------------------------------------------------------------------

_MEDICATION_RE = re.compile(
    r"\b(pill|pills|tablet|tablets|medicine|medicines|medication|medications|"
    r"dose|doses|dosage|mg|milligram|milligrams|ml|capsule|capsules|syrup|"
    r"injection|insulin|inhaler|drops|ointment|supplement|supplements|"
    r"painkiller|painkillers|antibiotic|antibiotics)\b|"
    # A number with a dose unit ("5mg", "10 ml") is medication-shaped even if
    # the drug is not named.
    r"\b\d+\s*(?:mg|ml|mcg|g|iu|units?)\b|"
    # Common generic-name endings, so an unnamed drug is still treated as one.
    r"\b\w+(?:formin|pril|sartan|statin|olol|mycin|cillin|azepam|"
    r"azolam|prazole|dipine|triptan|thyroxine)\b",
    re.I,
)

_DOSE_FRAGMENT_RE = re.compile(
    r"\b\d+\s*(?:mg|milligrams?|ml|pills?|tablets?|capsules?|drops?)\b", re.I
)

GENERIC_MEDICATION_LABEL = "take your medication"


def sanitise_reminder_text(raw: str) -> tuple[str, bool]:
    """Return (safe_to_speak_label, contains_medication).

    If anything medication-shaped is in the request, the spoken label becomes
    "take your medication" — no dose, no count, no drug name. Sahaara never
    gives dosing instructions, even by repeating the person's own words back.
    """
    text = (raw or "").strip().strip(".,!?").strip()
    if _MEDICATION_RE.search(text):
        return GENERIC_MEDICATION_LABEL, True
    cleaned = _DOSE_FRAGMENT_RE.sub("", text).strip(" ,.")
    return (cleaned or "your reminder"), False


# ---------------------------------------------------------------------------
# Timers and reminders
# ---------------------------------------------------------------------------

def _profile(profile_id: Optional[str]) -> str:
    return profile_id or "asha_demo_001"


def set_timer(
    seconds: int,
    label: str = "",
    profile_id: Optional[str] = None,
    label_raw: Optional[str] = None,
) -> ActionResult:
    """Create a real one-off timer. Persisted, so it survives a restart."""
    import database

    if not seconds or seconds <= 0:
        return ActionResult(
            success=False,
            action_type="set_timer",
            result_text="How long should the timer be?",
            detail={"needs": "duration"},
        )

    safe_label, was_medication = sanitise_reminder_text(label) if label else ("timer", False)
    if not label:
        safe_label = "timer"

    fire_at = time.time() + seconds
    reminder_id = database.add_one_time_reminder(
        _profile(profile_id),
        kind="timer",
        label=safe_label,
        label_raw=label_raw or label or "",
        fire_at=fire_at,
    )

    spoken = f"I've set a timer for {format_duration(seconds)}"
    if safe_label and safe_label != "timer":
        spoken += f" for {safe_label}"
    spoken += "."

    return ActionResult(
        success=True,
        action_type="set_timer",
        result_text=spoken,
        detail={
            "id": reminder_id,
            "label": safe_label,
            "label_raw": label_raw or label or "",
            "seconds": seconds,
            "fire_at": fire_at,
            "medication": was_medication,
        },
    )


def set_reminder(
    seconds: Optional[int] = None,
    label: str = "",
    profile_id: Optional[str] = None,
    label_raw: Optional[str] = None,
    at: Optional[datetime] = None,
) -> ActionResult:
    """Create a real one-off reminder, either after `seconds` or at a clock time."""
    import database

    if at is not None:
        fire_at = at.timestamp()
        seconds = max(0, int(fire_at - time.time()))
    elif seconds and seconds > 0:
        fire_at = time.time() + seconds
    else:
        return ActionResult(
            success=False,
            action_type="set_reminder",
            result_text="When should I remind you?",
            detail={"needs": "time"},
        )

    safe_label, was_medication = sanitise_reminder_text(label)
    when = f"at {format_clock(at)}" if at is not None else f"in {format_duration(seconds or 0)}"

    reminder_id = database.add_one_time_reminder(
        _profile(profile_id),
        kind="reminder",
        label=safe_label,
        label_raw=label_raw or label or "",
        fire_at=fire_at,
    )

    return ActionResult(
        success=True,
        action_type="set_reminder",
        result_text=f"I'll remind you {when} to {safe_label}.",
        detail={
            "id": reminder_id,
            "label": safe_label,
            "label_raw": label_raw or label or "",
            "seconds": seconds,
            "fire_at": fire_at,
            "medication": was_medication,
        },
    )


def list_timers(profile_id: Optional[str] = None, now: Optional[float] = None) -> ActionResult:
    """What is still pending for this profile."""
    import database

    now = time.time() if now is None else now
    rows = database.list_pending_reminders(_profile(profile_id))

    if not rows:
        return ActionResult(
            success=True,
            action_type="list_timers",
            result_text="You don't have any timers set.",
            detail={"timers": []},
        )

    items, spoken = [], []
    for row in rows:
        remaining = max(0, int(row["fire_at"] - now))
        items.append({
            "id": row["id"],
            "kind": row["kind"],
            "label": row["label"],
            "seconds_remaining": remaining,
        })
        if row["kind"] == "timer":
            if row["label"] and row["label"] != "timer":
                spoken.append(f"a timer for {row['label']} with {format_duration(remaining)} left")
            else:
                spoken.append(f"a timer with {format_duration(remaining)} left")
        elif row["label"] and row["label"] != "your reminder":
            spoken.append(f"a reminder to {row['label']} in {format_duration(remaining)}")
        else:
            spoken.append(f"a reminder in {format_duration(remaining)}")

    if len(spoken) == 1:
        text = f"You have {spoken[0]}."
    else:
        text = "You have " + ", ".join(spoken[:-1]) + f" and {spoken[-1]}."

    return ActionResult(
        success=True,
        action_type="list_timers",
        result_text=text,
        detail={"timers": items},
    )


def cancel_timer(
    profile_id: Optional[str] = None,
    which: Optional[str] = None,
    timer_id: Optional[int] = None,
) -> ActionResult:
    """Cancel a pending timer/reminder — by id, by label, or the only one left."""
    import database

    pid = _profile(profile_id)
    rows = database.list_pending_reminders(pid)

    if not rows:
        return ActionResult(
            success=False,
            action_type="cancel_timer",
            result_text="There's no timer to cancel.",
        )

    target = None
    if timer_id is not None:
        target = next((r for r in rows if r["id"] == int(timer_id)), None)
    elif which:
        needle = which.lower().strip()
        target = next(
            (r for r in rows if needle in r["label"].lower() or needle in (r["label_raw"] or "").lower()),
            None,
        )
    elif len(rows) == 1:
        target = rows[0]

    if target is None:
        if len(rows) > 1 and not which:
            def _describe(row: dict) -> str:
                if row["kind"] == "timer":
                    if row["label"] and row["label"] != "timer":
                        return f"the timer for {row['label']}"
                    return "a timer"
                return f"a reminder to {row['label']}"

            options = ", ".join(_describe(r) for r in rows)
            return ActionResult(
                success=False,
                action_type="cancel_timer",
                result_text=f"You have more than one — {options}. Which one should I cancel?",
                detail={"candidates": [r["id"] for r in rows]},
            )
        return ActionResult(
            success=False,
            action_type="cancel_timer",
            result_text="I don't have a timer like that.",
        )

    if not database.cancel_reminder(pid, target["id"]):
        return ActionResult(
            success=False,
            action_type="cancel_timer",
            result_text="I couldn't cancel that timer — it may have already gone off.",
        )

    label = target["label"]
    text = "I've cancelled that timer." if target["kind"] == "timer" else "I've cancelled that reminder."
    if label and label not in ("timer", "your reminder"):
        text = text[:-1] + f" for {label}."
    return ActionResult(
        success=True,
        action_type="cancel_timer",
        result_text=text,
        detail={"id": target["id"], "label": label},
    )


def describe_reminder_fired(row: dict) -> str:
    """What Sahaara says when a timer/reminder actually goes off."""
    label = (row.get("label") or "").strip()
    if row.get("kind") == "timer":
        if label and label != "timer":
            return f"Your timer for {label} is done."
        return "Your timer is done."
    if label:
        return f"A reminder — {label}."
    return "This is your reminder."


# ---------------------------------------------------------------------------
# Music and radio
# ---------------------------------------------------------------------------

def _music_files() -> list[Path]:
    if not MUSIC_DIR.exists():
        return []
    files = list(MUSIC_DIR.glob("*.mp3")) + list(MUSIC_DIR.glob("*.wav"))
    return sorted(files, key=lambda p: p.name.lower())


def _media_url(path: Path) -> str:
    return f"{MEDIA_URL_PREFIX}/{quote(path.name)}"


def _title(path: Path) -> str:
    name = path.stem.replace("_", " ").strip()
    # Files are usually lower-case on disk; a spoken title reads better cased.
    if name.islower():
        name = name.title()
    return name


def play_song(song_name: str = "", profile_id: Optional[str] = None) -> ActionResult:
    """Play a local music file. Honest when nothing matches or nothing is loaded."""
    files = _music_files()

    if not files:
        return ActionResult(
            success=False,
            action_type="play_song",
            result_text="I don't have any music loaded yet. I could play the radio instead.",
            detail={"reason": "no_music_files"},
        )

    query = (song_name or "").strip().lower()
    for filler in ("some music", "music", "a song", "song", "something", "anything"):
        if query == filler:
            query = ""

    chosen = None
    if query:
        needle = re.sub(r"[^a-z0-9]", "", query)
        for path in files:
            haystack = re.sub(r"[^a-z0-9]", "", path.stem.lower())
            if needle and (needle in haystack or haystack in needle):
                chosen = path
                break
    if chosen is None and not query:
        chosen = files[0]

    if chosen is None:
        available = ", ".join(_title(p) for p in files[:5])
        return ActionResult(
            success=False,
            action_type="play_song",
            result_text=f"I couldn't find that song. I have {available}. Or I could play the radio.",
            detail={"available": [_title(p) for p in files]},
        )

    return ActionResult(
        success=True,
        action_type="play_song",
        result_text=f"Playing {_title(chosen)}.",
        detail={
            "filename": chosen.name,
            "title": _title(chosen),
            "audio_url": _media_url(chosen),
        },
    )


# ---- Radio station list (caregiver-approved) ----

DEFAULT_RADIO_STATIONS = [
    # Every URL below was fetched and returned an audio stream when this list
    # was written (2026-10-28). Radio URLs rot: play_radio probes the stream
    # before it claims anything, so a dead URL can never be announced as
    # "playing". Caregivers can replace these in the profile.
    {"name": "Hindi Gold Radio", "url": "https://azuracast.vibesounds.in:8010/radio.mp3", "language": "Hindi"},
    {"name": "Radio Mirchi Hindi", "url": "https://eu8.fastcast4u.com/proxy/clyedupq/stream", "language": "Hindi"},
    {"name": "BBC World Service", "url": "https://stream.live.vc.bbcmedia.co.uk/bbc_world_service", "language": "English"},
]


def _stream_alive(url: str, timeout: float = 4.0) -> bool:
    """True when `url` really answers with audio.

    Sahaara must never say "playing X" if nothing will play. A dead link is
    common for public radio, so this is checked before the claim, not after.
    """
    try:
        request = Request(
            url, headers={"User-Agent": "Mozilla/5.0", "Icy-MetaData": "0"}
        )
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed list
            chunk = response.read(1024)
            content_type = (response.headers.get("Content-Type") or "").lower()
    except Exception as exc:
        # Never raise: a failed probe must not become a claim. Log the class so
        # a code bug cannot hide behind "the station is down".
        logger.debug("Radio probe failed: %s", type(exc).__name__)
        return False
    if len(chunk) < 256:
        return False
    return any(word in content_type for word in ("audio", "mpeg", "octet-stream"))



def _stations(profile_id: Optional[str], seed: bool = False) -> list[dict]:
    """The profile's station list.

    `seed=False` (used by detection) never writes: deciding whether an
    utterance is about the radio must not change stored data. `play_radio`
    passes `seed=True`, which installs the defaults once when the caregiver has
    not chosen any.
    """
    import database

    pid = _profile(profile_id)
    stations = database.get_radio_stations(pid)
    if not stations and seed:
        database.save_radio_stations(pid, DEFAULT_RADIO_STATIONS)
        stations = list(DEFAULT_RADIO_STATIONS)
    return stations


def play_radio(station_name: str = "", profile_id: Optional[str] = None) -> ActionResult:
    """Start a caregiver-approved radio stream.

    Nothing is claimed until the stream answers: a dead station is reported as
    dead, and with no station named the first one that actually responds wins.
    """
    stations = _stations(profile_id, seed=True)
    if not stations:
        return ActionResult(
            success=False,
            action_type="play_radio",
            result_text="I don't have any radio stations saved.",
        )

    chosen = None
    query = (station_name or "").strip().lower()
    if query and query not in ("radio", "the radio", "some radio", "music"):
        needle = re.sub(r"[^a-z0-9]", "", query)
        for station in stations:
            haystack = re.sub(r"[^a-z0-9]", "", station.get("name", "").lower())
            if needle and (needle in haystack or haystack in needle):
                chosen = station
                break
        if chosen is None:
            names = ", ".join(s.get("name", "") for s in stations)
            return ActionResult(
                success=False,
                action_type="play_radio",
                result_text=f"I don't have that station. I have {names}.",
                detail={"stations": [s.get("name", "") for s in stations]},
            )
        if not _stream_alive(chosen["url"]):
            return ActionResult(
                success=False,
                action_type="play_radio",
                result_text=f"{chosen['name']} isn't answering right now.",
                detail={"station_name": chosen["name"]},
            )
    else:
        # No station named: take the first one that actually answers.
        for station in stations:
            if _stream_alive(station["url"]):
                chosen = station
                break
        if chosen is None:
            return ActionResult(
                success=False,
                action_type="play_radio",
                result_text="The radio isn't answering right now.",
                detail={"stations": [s.get("name", "") for s in stations]},
            )

    return ActionResult(
        success=True,
        action_type="play_radio",
        result_text=f"Playing {chosen['name']}.",
        detail={
            "station_name": chosen["name"],
            "stream_url": chosen["url"],
            "language": chosen.get("language", ""),
        },
    )


# What the browser is currently playing, per profile. Used so "stop the music"
# can tell the truth instead of always claiming it stopped something.
_MEDIA_PLAYING: dict[str, str] = {}


def mark_media_playing(profile_id: Optional[str], what: str) -> None:
    _MEDIA_PLAYING[_profile(profile_id)] = what


def stop_media(profile_id: Optional[str] = None) -> ActionResult:
    """Stop whatever is playing — honestly."""
    pid = _profile(profile_id)
    was_playing = _MEDIA_PLAYING.pop(pid, None)
    if not was_playing:
        return ActionResult(
            success=False,
            action_type="stop_media",
            result_text="Nothing is playing right now.",
            detail={"was_playing": None},
        )
    return ActionResult(
        success=True,
        action_type="stop_media",
        result_text="I've stopped the audio.",
        detail={"was_playing": was_playing},
    )


# ---------------------------------------------------------------------------
# Weather — deliberately NOT wired to any trigger (owner decision 2026-10-28)
# ---------------------------------------------------------------------------

def get_weather(latitude: float = PUNE_LATITUDE, longitude: float = PUNE_LONGITUDE) -> ActionResult:
    """Dormant. `detect_action()` never returns get_weather; nothing calls this.

    Kept because it talks to a real source (Open-Meteo, no key needed). Wiring
    it back up is a one-line change in `detect_action()` if the owner ever
    wants weather answers.
    """
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
        temperature = current.get("temperature")
        condition = _weathercode_to_condition(current.get("weathercode"))

        return ActionResult(
            success=True,
            action_type="get_weather",
            result_text=f"It's {temperature}°C and {condition.lower()} in Pune right now.",
            detail={"temperature": temperature, "condition": condition},
        )
    except Exception:
        return ActionResult(
            success=False,
            action_type="get_weather",
            result_text="I'm sorry, I can't check the weather right now.",
        )


def _weathercode_to_condition(code: Optional[int]) -> str:
    if code is None:
        return "unknown"
    code_map = {
        0: "clear", 1: "mainly clear", 2: "partly cloudy", 3: "overcast",
        45: "foggy", 48: "depositing rime fog", 51: "light drizzle",
        53: "moderate drizzle", 55: "dense drizzle", 56: "light freezing drizzle",
        57: "dense freezing drizzle", 61: "slight rain", 63: "moderate rain",
        65: "heavy rain", 66: "light freezing rain", 67: "heavy freezing rain",
        71: "slight snow fall", 73: "moderate snow fall", 75: "heavy snow fall",
        77: "snow grains", 80: "slight rain showers", 81: "moderate rain showers",
        82: "heavy rain showers", 85: "slight snow showers", 86: "heavy snow showers",
        95: "thunderstorm", 96: "thunderstorm with slight hail",
        99: "thunderstorm with heavy hail",
    }
    return code_map.get(code, "unknown")


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

_MEDIA_NOUNS = r"(?:music|radio|audio|playback|song|songs|the song|the music|the radio|it)"
_STOP_RE = re.compile(
    rf"\b(?:stop|turn off|switch off|pause|kill|end)\s+{_MEDIA_NOUNS}\b|"
    r"\b(?:stop|pause)\s+(?:playing|the playback)\b",
    re.I,
)
_CANCEL_RE = re.compile(
    r"\b(?:cancel|remove|delete|stop|clear|get rid of)\s+(?:the\s+|my\s+|that\s+)?"
    r"((?:\w+\s+){0,2})(?:timer|timers|alarm|alarms|reminder|reminders|countdown)\b(.*)$",
    re.I,
)
_LIST_RE = re.compile(
    r"\b(?:what|which|any)\s+(?:timers?|alarms?|reminders?)\b|"
    r"\b(?:list|show|check)\s+(?:my\s+)?(?:timers?|alarms?|reminders?)\b|"
    r"\bhow long\s+(?:is|until|till|left)\b.*\b(?:timer|left)\b|"
    r"\bdo i have any\s+(?:timers?|alarms?|reminders?)\b",
    re.I,
)
_TIME_RE = re.compile(
    r"\bwhat(?:'?s| is)?\s+(?:the\s+)?time\b|"
    r"\bwhat time is it\b|"
    r"\btell me the time\b|"
    r"\bhave you got the time\b|"
    r"\bwhat o'?clock is it\b",
    re.I,
)
_TIMER_RE = re.compile(
    r"\b(?:set|start|begin|put on|make)\s+(?:me\s+)?(?:a|an|the)?\s*"
    r"(?:timer|countdown)\b(.*)$|"
    r"\b(?:timer|countdown)\s+for\b(.*)$|"
    r"\b((?:\d+|" + "|".join(_WORD_NUMBERS.keys()) + r")\s*"
    r"(?:seconds?|secs?|minutes?|mins?|hours?|hrs?))\s+timer\b(.*)$",
    re.I,
)
_REMINDER_RE = re.compile(
    r"\bremind\s+me\b(.*)$|"
    r"\b(?:set|create|make)\s+(?:me\s+)?(?:a|an)?\s*reminder\b(.*)$|"
    r"\b(?:don'?t|do not)\s+let me forget\b(.*)$",
    re.I,
)
_PLAY_RE = re.compile(
    r"\b(?:play|put on|start|switch on|turn on)\s+(.*)$",
    re.I,
)


def _extract_label(text: str) -> str:
    """Pull a human label out of 'for the tea', 'to steep the tea', 'called tea'."""
    if not text:
        return ""
    text = re.sub(r"\s+", " ", text).strip(" .,!?")
    if not text:
        return ""
    text = re.sub(r"\b(?:please|now|thanks|thank you)\b", " ", text, flags=re.I)
    text = re.sub(r"\b(?:called|named)\s+", "", text, flags=re.I)
    text = re.sub(r"\s+", " ", text).strip()
    # Peel leading and trailing connectives ("for the tea", "call Rohan in").
    previous = None
    while previous != text:
        previous = text
        text = re.sub(r"^(?:for|in|after|to|about|that|of|at)\s+", "", text, flags=re.I)
        text = re.sub(r"\s+(?:for|in|after|to|about|that|of|at)$", "", text, flags=re.I)
        text = re.sub(r"^(?:for|in|after|to|about|that|of|at)$", "", text, flags=re.I)
        text = text.strip(" .,!?-–—:")
    return text


def _split_timer_args(rest: str) -> tuple[Optional[int], str]:
    seconds, span = _find_duration(rest or "")
    leftover = rest or ""
    if seconds is not None and span != (0, 0):
        leftover = leftover[: span[0]] + " " + leftover[span[1]:]
    label = _extract_label(leftover)
    if label.lower() in ("", "timer", "a timer", "the timer", "countdown"):
        label = ""
    return seconds, label


def _split_reminder_args(rest: str) -> tuple[Optional[int], Optional[datetime], str]:
    rest = rest or ""
    seconds, span = _find_duration(rest)
    # Only look for a clock time when there is no duration — otherwise
    # "for 5 minutes" would also read as "at 5".
    at = None if seconds is not None else parse_clock_time(rest)
    leftover = rest
    if seconds is not None and span != (0, 0):
        leftover = leftover[: span[0]] + " " + leftover[span[1]:]
    if at is not None:
        leftover = _CLOCK_RE.sub(" ", leftover)
    label = _extract_label(leftover)
    label = re.sub(r"^(?:that|to|about|for)\s+", "", label, flags=re.I).strip()
    return seconds, at, label


def detect_action(user_message: str, profile_id: Optional[str] = None) -> Optional[dict]:
    """Does this utterance ask for one of the fixed actions?

    Returns {"action_type": str, "params": {...}} or None. Order matters:
    stop and cancel are checked before play, and the station list is consulted
    before falling back to a local song.
    """
    text = (user_message or "").strip()
    if not text:
        return None
    lowered = text.lower()

    # ---- stop media -------------------------------------------------------
    if _STOP_RE.search(lowered) and not re.search(r"\btimer|alarm|reminder\b", lowered):
        return {"action_type": "stop_media", "params": {}}

    # ---- cancel a timer/reminder -----------------------------------------
    cancel_match = _CANCEL_RE.search(text)
    if cancel_match:
        which = _extract_label((cancel_match.group(1) or "") + " " + (cancel_match.group(2) or ""))
        return {"action_type": "cancel_timer", "params": {"which": which or None}}

    # ---- list timers ------------------------------------------------------
    if _LIST_RE.search(lowered):
        return {"action_type": "list_timers", "params": {}}

    # ---- time -------------------------------------------------------------
    if _TIME_RE.search(lowered):
        return {"action_type": "tell_time", "params": {}}

    # ---- timer ------------------------------------------------------------
    timer_match = _TIMER_RE.search(text)
    if timer_match:
        groups = [g for g in timer_match.groups() if g]
        rest = " ".join(groups)
        seconds, label = _split_timer_args(rest)
        return {
            "action_type": "set_timer",
            "params": {"seconds": seconds, "label": label},
        }

    # ---- reminder ---------------------------------------------------------
    reminder_match = _REMINDER_RE.search(text)
    if reminder_match:
        groups = [g for g in reminder_match.groups() if g]
        rest = " ".join(groups)
        seconds, at, label = _split_reminder_args(rest)
        return {
            "action_type": "set_reminder",
            "params": {"seconds": seconds, "at": at, "label": label},
        }

    # ---- play -------------------------------------------------------------
    play_match = _PLAY_RE.search(text)
    if play_match:
        target = (play_match.group(1) or "").strip(" .,!?")
        lowered_target = target.lower()

        # A named station wins over a local song ("play All India Radio Pune").
        try:
            for station in _stations(profile_id):
                name = station.get("name", "")
                if not name:
                    continue
                lowered_name = name.lower()
                if lowered_name in lowered_target or (
                    len(lowered_target) >= 3 and lowered_target in lowered_name
                ):
                    return {"action_type": "play_radio", "params": {"station_name": name}}
        except Exception:
            pass

        if re.search(r"\b(?:radio|fm|station)\b", lowered_target):
            return {"action_type": "play_radio", "params": {"station_name": target}}

        if lowered_target in ("", "something", "a song", "some music", "music", "the music", "song"):
            return {"action_type": "play_song", "params": {"song_name": ""}}

        return {"action_type": "play_song", "params": {"song_name": target}}

    return None


if __name__ == "__main__":  # pragma: no cover - manual smoke check
    samples = [
        "what time is it",
        "set a timer",
        "set a timer for 30 seconds",
        "set a 5 minute timer",
        "set a timer for 10 minutes to steep the tea",
        "remind me in 10 minutes to take my pills",
        "remind me to call Rohan in 30 minutes",
        "remind me at 7 to take my medicine",
        "what timers do I have",
        "cancel the timer",
        "play some music",
        "play the radio",
        "play all india radio pune",
        "stop the music",
        "how is the weather",
        "hello how are you",
    ]
    for sample in samples:
        print(f"{sample!r:48} -> {detect_action(sample)}")
