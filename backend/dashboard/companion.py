"""Companion live channel (UI split, Step 2).

The person's screen is open on the same device as the caregiver dashboard, so it
must not subscribe to the caregiver stream: that stream carries tiers, alerts,
consent questions, danger phrases and quoted speech. This module is the only
push channel the companion view may use.

It is a *whitelist*, not a filter. A payload can only be built for one of the
five kinds below, unknown kinds are refused, unknown keys are dropped, and a
payload that even mentions a caregiver concept in a key is refused outright.
The mapping from internal events (`map_event`) only ever copies fixed fields —
there is no "forward the event" path.

Kinds:
  clock               now / iso / date
  caption             the last reply Sahaara actually said
  now_playing         a song/radio action that succeeded
  reminder_countdown  the next pending timer/reminder, if any
  next_routine        the next wake/meal time from the profile routine

Nothing here authenticates: the companion view is the no-login experience by
design (owner decision, UI split Step 2). It exposes no caregiver data, so
there is nothing to protect beyond the five kinds above.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import datetime, timedelta
from typing import Any, Callable, Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from events.event_log import Event, EventType

logger = logging.getLogger("dashboard.companion")


# ---------------------------------------------------------------------------
# Whitelist
# ---------------------------------------------------------------------------

COMPANION_KINDS = {
    "clock",
    "caption",
    "now_playing",
    "reminder_countdown",
    "next_routine",
}

ALLOWED_KEYS: dict[str, set[str]] = {
    "clock": {"now", "iso", "date"},
    "caption": {"text", "at"},
    "now_playing": {"title", "kind", "playing", "at"},
    "reminder_countdown": {
        "present",
        "id",
        "label",
        "fire_at",
        "seconds_remaining",
        "at",
    },
    "next_routine": {"label", "time", "day"},
}

# Keys that may never appear even if a future edit adds one to ALLOWED_KEYS.
# This is the second lock on the door, and the test asserts it stays shut.
FORBIDDEN_KEY_RE = re.compile(
    r"tier|alert|consent|danger|symptom|emotion|severity|distress|"
    r"repeat|fallback|physical|diagnos",
    re.IGNORECASE,
)

MEDIA_ACTIONS = {"play_song", "play_radio"}
TIMER_ACTIONS = {"set_timer", "set_reminder", "cancel_timer"}

# Keep one caption from becoming a paragraph on a glanceable screen.
MAX_CAPTION_CHARS = 500

# Reconnect/keepalive: a quiet companion still gets a clock line, which also
# proves the socket is alive to both ends.
CLOCK_INTERVAL_SECONDS = 30.0


def build_message(kind: str, data: Any) -> Optional[dict]:
    """Validate and trim one companion message. Returns None when refused."""
    if kind not in COMPANION_KINDS:
        logger.warning("companion payload refused: unknown kind %r", kind)
        return None
    if not isinstance(data, dict):
        logger.warning("companion payload refused: data is not an object")
        return None

    for key in data:
        if FORBIDDEN_KEY_RE.search(str(key)):
            logger.warning("companion payload refused: forbidden key %r", key)
            return None

    clean = {key: value for key, value in data.items() if key in ALLOWED_KEYS[kind]}

    # Minimal shape checks. A kind with no defining field is a bug upstream,
    # not something to render.
    if kind == "caption":
        text = clean.get("text")
        if not isinstance(text, str) or not text.strip():
            return None
    elif kind == "clock":
        if not isinstance(clean.get("now"), (int, float)):
            return None
        if not isinstance(clean.get("date"), str):
            return None
    elif kind == "now_playing":
        if not isinstance(clean.get("title"), str) or not clean["title"].strip():
            return None
        if clean.get("kind") not in ("song", "radio"):
            return None
        if not isinstance(clean.get("playing"), bool):
            return None
    elif kind == "reminder_countdown":
        if not isinstance(clean.get("present"), bool):
            return None
        if clean["present"]:
            if not isinstance(clean.get("id"), int):
                return None
            if not isinstance(clean.get("seconds_remaining"), (int, float)):
                return None
    elif kind == "next_routine":
        if not isinstance(clean.get("label"), str) or not clean["label"].strip():
            return None
        if not isinstance(clean.get("time"), str) or not clean["time"].strip():
            return None
        if clean.get("day") not in ("today", "tomorrow"):
            return None

    return {"type": kind, "data": clean}


# ---------------------------------------------------------------------------
# Small pure builders
# ---------------------------------------------------------------------------


def clock_message(now: Optional[float] = None) -> dict:
    """The one message every connection starts with."""
    stamp = time.time() if now is None else float(now)
    moment = datetime.fromtimestamp(stamp)
    return {
        "type": "clock",
        "data": {
            "now": stamp,
            "iso": moment.isoformat(),
            "date": moment.strftime("%A, %d %B %Y"),
        },
    }


def _parse_hm(value: Any) -> Optional[tuple[int, int]]:
    """Parse a profile 'HH:MM' time. Anything else is ignored, never guessed."""
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", value)
    if not match:
        return None
    hour, minute = int(match.group(1)), int(match.group(2))
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return hour, minute


def next_routine_item(routine: Any, now: datetime) -> Optional[dict]:
    """The next wake/meal time after `now`, or None.

    Deliberately excludes `medication_times`: medication scheduling belongs to
    the caregiver scheduler (UI split spec §8), not to the companion strip.
    """
    if not isinstance(routine, dict):
        return None

    items: list[tuple[str, str]] = []
    wake = routine.get("wake_time")
    if isinstance(wake, str) and wake.strip():
        items.append(("Wake up", wake))
    meals = routine.get("meals")
    if isinstance(meals, dict):
        for key, label in (
            ("breakfast", "Breakfast"),
            ("lunch", "Lunch"),
            ("dinner", "Dinner"),
        ):
            value = meals.get(key)
            if isinstance(value, str) and value.strip():
                items.append((label, value))
    sleep = routine.get("sleep_time")
    if isinstance(sleep, str) and sleep.strip():
        items.append(("Bedtime", sleep))

    candidates: list[tuple[datetime, str, str]] = []
    for label, raw in items:
        parsed = _parse_hm(raw)
        if parsed is None:
            continue
        candidate = now.replace(
            hour=parsed[0], minute=parsed[1], second=0, microsecond=0
        )
        if candidate < now:
            candidate += timedelta(days=1)
        candidates.append((candidate, label, f"{parsed[0]:02d}:{parsed[1]:02d}"))

    if not candidates:
        return None

    when, label, hhmm = min(candidates, key=lambda item: item[0])
    return {
        "label": label,
        "time": hhmm,
        "day": "today" if when.date() == now.date() else "tomorrow",
    }


def _pending_reminder(
    profile_id: str, now: Optional[float] = None
) -> dict:
    """The soonest pending reminder for the profile, in companion shape."""
    stamp = time.time() if now is None else float(now)
    try:
        import database

        rows = database.list_pending_reminders(profile_id)
    except Exception:
        logger.warning("companion reminder lookup failed", exc_info=False)
        return {"present": False}

    if not rows:
        return {"present": False}

    row = rows[0]
    try:
        fire_at = float(row.get("fire_at") or 0)
    except (TypeError, ValueError):
        fire_at = 0.0
    return {
        "present": True,
        "id": int(row.get("id") or 0),
        "label": str(row.get("label") or "Reminder"),
        "fire_at": fire_at,
        "seconds_remaining": max(0, int(round(fire_at - stamp))),
        "at": stamp,
    }


# ---------------------------------------------------------------------------
# Event mapping (allowlist)
# ---------------------------------------------------------------------------


def map_event(
    event: Event,
    *,
    pending: Optional[Callable[[], dict]] = None,
) -> Optional[dict]:
    """Map one internal event to a companion message, or None.

    Only three transitions are recognised:
      * a Sahaara conversation turn -> caption
      * a successful song/radio action -> now_playing
      * a timer set/cancel/fire -> the next reminder_countdown

    Everything else (tier changes, alerts, consent, danger, symptoms, fallback,
    repetition, distress, system health, session marks) is deliberately
    unrecognised. The test enumerates every EventType so a future addition is
    forced to make an explicit decision here.
    """
    if not isinstance(event, Event):
        return None
    detail = event.detail if isinstance(event.detail, dict) else {}

    if event.type == EventType.CONVERSATION_TURN:
        if detail.get("speaker") != "sahaara":
            return None
        text = detail.get("text")
        if not isinstance(text, str) or not text.strip():
            return None
        return build_message(
            "caption",
            {"text": text.strip()[:MAX_CAPTION_CHARS], "at": event.timestamp},
        )

    if event.type == EventType.ACTION:
        if detail.get("success") is not True:
            return None
        action_type = detail.get("action_type")
        if action_type in MEDIA_ACTIONS:
            title = detail.get("title") or detail.get("station_name")
            if not isinstance(title, str) or not title.strip():
                return None
            return build_message(
                "now_playing",
                {
                    "title": title.strip()[:120],
                    "kind": "song" if action_type == "play_song" else "radio",
                    "playing": True,
                    "at": event.timestamp,
                },
            )
        if action_type in TIMER_ACTIONS:
            data = pending() if pending else _pending_reminder(event.profile_id)
            return build_message("reminder_countdown", data)
        return None

    if event.type == EventType.REMINDER_FIRED:
        data = pending() if pending else _pending_reminder(event.profile_id)
        return build_message("reminder_countdown", data)

    return None


# ---------------------------------------------------------------------------
# Hub
# ---------------------------------------------------------------------------


class CompanionHub:
    """Fan-out to companion WebSocket subscribers.

    `publish` may be called from the event-loop thread or a worker thread; when
    a loop is registered the hand-off goes through `call_soon_threadsafe`.
    """

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue] = set()
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def set_main_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.discard(queue)

    def subscriber_count(self) -> int:
        return len(self._subscribers)

    def _deliver(self, message: dict) -> None:
        dead: list[asyncio.Queue] = []
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                dead.append(queue)
        for queue in dead:
            self._subscribers.discard(queue)

    def publish(self, kind: str, data: Any) -> Optional[dict]:
        message = build_message(kind, data)
        if message is None:
            return None

        loop = self._loop
        if loop is not None and loop.is_running():
            try:
                loop.call_soon_threadsafe(self._deliver, message)
            except RuntimeError:
                # Loop closed during shutdown; nothing to deliver to.
                pass
        else:
            self._deliver(message)
        return message

    def observe(self, event: Event) -> Optional[dict]:
        """Map and broadcast one event. Never raises into the event log."""
        try:
            message = map_event(event)
            if message is None:
                return None
            return self.publish(message["type"], message["data"])
        except Exception:
            logger.warning("companion event mapping failed", exc_info=False)
            return None


hub = CompanionHub()


def set_main_loop(loop: asyncio.AbstractEventLoop) -> None:
    """Capture the app loop so event-log threads can hand payloads over."""
    hub.set_main_loop(loop)


def observe(event: Event) -> Optional[dict]:
    """Map and broadcast one event (module-level hook for `stream.py`)."""
    return hub.observe(event)


# ---------------------------------------------------------------------------
# Snapshot (sent on connect)
# ---------------------------------------------------------------------------


def snapshot(
    profile_id: str,
    routine: Any,
    *,
    now: Optional[float] = None,
    pending: Optional[Callable[[], dict]] = None,
) -> list[dict]:
    """The first messages a companion connection receives."""
    stamp = time.time() if now is None else float(now)
    messages = [clock_message(stamp)]

    reminder = pending() if pending else _pending_reminder(profile_id, stamp)
    reminder_message = build_message("reminder_countdown", reminder)
    if reminder_message:
        messages.append(reminder_message)

    item = next_routine_item(routine, datetime.fromtimestamp(stamp))
    if item:
        routine_message = build_message("next_routine", item)
        if routine_message:
            messages.append(routine_message)

    return messages


# ---------------------------------------------------------------------------
# WebSocket
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/stream", tags=["companion"])


@router.websocket("/ws/companion")
async def companion_stream(websocket: WebSocket) -> None:
    """The no-login companion channel.

    Sends a small snapshot, then only whitelisted messages. The periodic clock
    doubles as a keepalive, so a silent household still has a live socket.
    """
    await websocket.accept()

    # The companion screen follows the default (Asha) profile. Imported here to
    # avoid pulling the whole conversation graph in at module import time.
    import conversation

    session = conversation._get_session()
    profile_id = session.profile.profile_id
    routine = session.profile.get_routine()

    queue = hub.subscribe()
    try:
        for message in snapshot(profile_id, routine):
            await websocket.send_json(message)

        while True:
            try:
                message = await asyncio.wait_for(
                    queue.get(), timeout=CLOCK_INTERVAL_SECONDS
                )
                await websocket.send_json(message)
            except asyncio.TimeoutError:
                await websocket.send_json(clock_message())
    except WebSocketDisconnect:
        pass
    except Exception:
        # A closed socket during shutdown is normal; do not log a traceback.
        logger.debug("companion socket closed", exc_info=False)
    finally:
        hub.unsubscribe(queue)
