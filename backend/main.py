"""
backend/main.py

FastAPI entrypoint. The actual turn logic (memory -> intent -> prompt ->
LLM -> guardrails) lives in conversation.py, shared with the voice
pipeline (voice/voice_pipeline.py) — this file is just the HTTP/WebSocket
wrapper.

/chat: text mode, unchanged from the original prototype (App.tsx needs
       no changes).
/ws/voice: voice mode. Browser sends raw PCM16 mono 16kHz audio frames
       (binary); server sends back a mix of JSON text frames (orb state:
       {"type": "state", "state": "listening"|"thinking"|"speaking"}) and
       binary frames (PCM16 mono 22050Hz audio to play). See VoiceOrb.tsx
       for the client side of this contract.
"""

import asyncio
import logging
import os
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent / ".env")

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends, Query, status, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import conversation
from conversation import process_turn, provider, _get_session
from dashboard.stream import router as stream_router, set_main_loop
from dashboard.companion import router as companion_router
from dashboard.summary import generate_daily_summary, DailySummary
from voice import voice_pipeline
from auth_routes import router as auth_router, require_caregiver, auth_enabled
from push_routes import router as push_router
from emergency_routes import router as emergency_router
import push
import scheduler

logger = logging.getLogger("main")

app = FastAPI(title="Sahaara API")

app.include_router(stream_router)
app.include_router(companion_router)
app.include_router(auth_router)
app.include_router(push_router)
app.include_router(emergency_router)


@app.on_event("startup")
async def startup_event():
    """Capture the main event loop for thread-safe broadcasting."""
    set_main_loop(asyncio.get_running_loop())
    
    # Setup SSE stream hooks (requires active session)
    from dashboard.stream import setup_stream_hooks
    setup_stream_hooks()
    
    # Start proactive scheduler. Use the per-profile factory (not a fresh
    # Scheduler(...)) so /ws/voice listens on the SAME instance: the voice
    # initiative queue and the open-session flag live on that object.
    import conversation
    s = conversation._get_session()
    from scheduler import get_scheduler_for_profile, make_llm_caller
    global scheduler
    scheduler = get_scheduler_for_profile(s.profile.profile_id)
    llm_call = make_llm_caller()
    await scheduler.start(llm_call=llm_call)
    
    # Wire scheduler into conversation module for set_reminder action
    import conversation as conv
    conv._scheduler = scheduler
    
    # Background task to push scheduler initiatives into SSE
    asyncio.create_task(_broadcast_scheduler_initiatives())


async def _broadcast_scheduler_initiatives():
    """Push scheduler initiative messages into SSE broadcast.

    Drains every per-profile Scheduler: a timer belongs to the person who set
    it, and its dashboard line must appear even when the process is currently
    serving another profile.
    """
    from dashboard.stream import _broadcast_to_queues
    import scheduler as scheduler_module

    while True:
        try:
            for sched in list(scheduler_module._scheduler_instances.values()):
                while True:
                    initiative = await sched.get_initiative()
                    if not initiative:
                        break
                    _broadcast_to_queues("sahaara_initiative", initiative["data"])
        except Exception:
            pass
        await asyncio.sleep(1)


@app.on_event("shutdown")
async def shutdown_event():
    global scheduler
    if scheduler:
        await scheduler.stop()


# CORS is a no-op for the shipped app: the frontend calls the backend through
# the same-origin `/api` proxy (Vite dev server, `vite preview`, or any reverse
# proxy), so no request is ever cross-origin. The allow-list below only covers
# the one case that *is* cross-origin — calling :8000 directly from the Vite dev
# server on :5173 — and it must stay credential-scoped: the session cookie is
# SameSite=Lax, so a real cross-site frontend would still not receive it.
# Do not widen `allow_origins` to "*" while `allow_credentials=True`.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5174",
        "http://localhost:5175",
        "http://127.0.0.1:5175",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ChatRequest(BaseModel):
    message: str


@app.get("/")
def root():
    return {
        "name": "Sahaara",
        "status": "running",
        "provider": os.environ.get("LLM_PROVIDER", "nemotron"),
    }


@app.get("/health")
def health():
    return {
        "status": "ok",
        "provider_ready": provider is not None,
    }


@app.get("/config")
def public_config():
    """
    Public, non-secret frontend configuration.

    `push.py` owns the gates and the keys; with the send gate OFF the public
    VAPID key is omitted entirely and the private key is never returned.
    """
    return push.public_push_config()


# Caregiver-only routes (require authentication when SAHAARA_AUTH_ENABLED=true)
# These are the routes that expose caregiver dashboard data and controls

@app.get("/escalation/status")
def escalation_status(user: dict = Depends(require_caregiver)):
    """Current escalation tier and status for dashboard."""
    import conversation
    s = conversation.get_session_for_profile(user["profile_id"])
    return s.escalation_engine.get_status()


@app.get("/escalation/events")
def escalation_events(
    event_type: str = None,
    since: float = None,
    limit: int = 100,
    user: dict = Depends(require_caregiver),
):
    """Get recent events from the event log."""
    import conversation
    s = conversation.get_session_for_profile(user["profile_id"])
    event_log = s.event_log
    from events.event_log import EventType
    
    events = event_log.get_all()
    
    # Filter by type
    if event_type:
        try:
            et = EventType(event_type)
            events = [e for e in events if e.type == et]
        except ValueError:
            pass
    
    # Filter by time
    if since:
        events = [e for e in events if e.timestamp >= since]
    
    # Limit
    events = events[-limit:]
    
    return {
        "events": [e.to_dict() for e in events],
        "total": len(event_log.get_all()),
    }


@app.get("/escalation/summary")
def escalation_summary(user: dict = Depends(require_caregiver)):
    """Session summary for daily summary screen."""
    import conversation
    s = conversation.get_session_for_profile(user["profile_id"])
    return s.event_log.summary()


@app.get("/daily-summary")
def daily_summary(user: dict = Depends(require_caregiver)):
    """Generate daily summary from event log for caregiver dashboard."""
    import conversation
    s = conversation.get_session_for_profile(user["profile_id"])
    summary = generate_daily_summary(s.event_log, s.profile)
    return summary.__dict__


@app.post("/demo/reset")
def demo_reset(user: dict = Depends(require_caregiver)):
    """Reset all demo state for a clean re-run."""
    import conversation
    s = conversation.get_session_for_profile(user["profile_id"])
    event_log = s.event_log
    session_notes = s.session_notes
    escalation_engine = s.escalation_engine
    consent_manager = s.consent_manager
    memory_box = s.memory_box
    alert_store = s.alert_store
    suggestion_queue = s.suggestion_queue
    profile = s.profile
    
    # Save session summary before clearing events
    from memory.summarizer import generate_summary_from_events, save_summary, load_recent_summaries, build_session_context_block
    if event_log and event_log.get_all():
        summary = generate_summary_from_events(event_log.get_all(), profile.preferred_address)
        save_summary(summary)
    
    event_log.clear()
    session_notes.reset()
    escalation_engine.reset()
    consent_manager.reset()
    memory_box.reset()
    alert_store.clear()
    suggestion_queue.clear()
    
    # Reset session-level state
    s._last_sahaara_prompt_time = 0.0
    s._unresponsive_enabled = False
    
    # Reload session context for the new session
    summaries = load_recent_summaries()
    s.session_context_block = build_session_context_block(summaries, s.profile.preferred_address)
    
    # Re-initialize event log with session_start
    event_log.log_session_start()
    
    return {"status": "ok", "message": "Demo state reset"}


@app.get("/alerts")
def get_alerts(
    status: str = "new",
    limit: int = 50,
    user: dict = Depends(require_caregiver),
):
    """Get alerts for the dashboard. Now backed by AlertStore."""
    import conversation
    from push import push_attempts_by_alert

    s = conversation.get_session_for_profile(user["profile_id"])
    alert_store = s.alert_store
    # M5 Step 6: attach the push attempt recorded for each alert, so the
    # dashboard can show what the push service answered. `None` when nothing
    # was attempted (e.g. the send gate is OFF) — the UI renders nothing then.
    attempts = push_attempts_by_alert(s.event_log)
    items = []
    for alert in alert_store.get_all(status=status)[-limit:]:
        item = alert.to_dict()
        item["push"] = attempts.get(alert.alert_id)
        items.append(item)
    return {
        "alerts": items,
        "unread_count": alert_store.unread_count(),
    }


@app.post("/alerts/{alert_id}/acknowledge")
def acknowledge_alert(alert_id: str, body: dict = None, user: dict = Depends(require_caregiver)):
    """Acknowledge an alert with an optional note."""
    import conversation
    s = conversation.get_session_for_profile(user["profile_id"])
    alert_store = s.alert_store
    event_log = s.event_log
    
    note = (body or {}).get("note", "")
    alert = alert_store.acknowledge(alert_id, note)
    if alert is None:
        return {"error": "Alert not found", "alert_id": alert_id}
    
    event_log.log_alert_acknowledged(alert_id, note)
    return {"status": "ok", "alert": alert.to_dict()}


@app.get("/alerts/test")
def test_alert(user: dict = Depends(require_caregiver)):
    """Test endpoint to build a sample alert."""
    import conversation
    s = conversation._get_session()
    alert_builder = s.alert_builder
    alert_store = s.alert_store
    escalation_engine = s.escalation_engine
    from safety.escalation import EscalationTier
    
    # Clear any existing test alerts
    alert_store.clear()
    
    alert = alert_builder.build_tier_change_alert(
        EscalationTier.GREEN,
        EscalationTier.YELLOW,
        "repeat_threshold",
        {"repeat_count": 3, "sustained_distress": False},
    )
    return {"alert": alert.to_dict(), "unread_count": alert_store.unread_count()}


@app.get("/alerts/{alert_id}/context")
def alert_context(alert_id: str, turns: int = 10, user: dict = Depends(require_caregiver)):
    """Get an alert with surrounding conversation turns for detail view."""
    import conversation
    s = conversation.get_session_for_profile(user["profile_id"])
    alert_store = s.alert_store
    event_log = s.event_log
    from events.event_log import EventType
    
    alert = alert_store.get_by_id(alert_id)
    if alert is None:
        return {"error": "Alert not found", "alert_id": alert_id}
    
    # Get all conversation turns from the event log
    all_turns = event_log.get_events(EventType.CONVERSATION_TURN)
    all_events = event_log.get_all()
    
    # Find turns around the alert timestamp
    alert_ts = alert.timestamp
    surrounding_turns = []
    for turn in all_turns:
        ts = turn.detail.get("timestamp", turn.timestamp)
        surrounding_turns.append(turn.to_dict())
    
    # Get tier change that triggered this alert
    tier_changes = event_log.get_events(EventType.TIER_CHANGE)
    trigger_event = None
    for tc in tier_changes:
        if abs(tc.timestamp - alert_ts) < 5:
            trigger_event = tc.to_dict()
            break
    
    # Build tier path
    tier_events = sorted(event_log.get_events(EventType.TIER_CHANGE), key=lambda e: e.timestamp)
    tier_path = [f"{e.detail.get('old_tier', '?')} \u2192 {e.detail.get('new_tier', '?')}" for e in tier_events]

    # M5 Step 6: the push record for this alert (same source as /alerts).
    from push import push_attempts_by_alert

    push_attempts = [
        e.to_dict()
        for e in event_log.get_events(EventType.ALERT_SENT)
        if e.detail.get("alert_id") == alert_id
    ]

    return {
        "alert": alert.to_dict(),
        "surrounding_turns": surrounding_turns[-turns:],
        "trigger_event": trigger_event,
        "tier_path": tier_path,
        "recommended_action": alert.message,
        "push": push_attempts_by_alert(event_log).get(alert_id),
        "push_attempts": push_attempts,
    }


@app.get("/suggestions")
def get_suggestions(user: dict = Depends(require_caregiver)):
    """Get pending profile fact suggestions for caregiver review."""
    import conversation
    s = conversation.get_session_for_profile(user["profile_id"])
    suggestion_queue = s.suggestion_queue
    return {
        "suggestions": [s.to_dict() for s in suggestion_queue.get_all()],
        "total": len(suggestion_queue.get_all()),
    }


@app.post("/suggestions/{suggestion_id}/approve")
def approve_suggestion(suggestion_id: str, user: dict = Depends(require_caregiver)):
    """Approve a suggested fact and apply it to the profile."""
    import conversation
    s = conversation.get_session_for_profile(user["profile_id"])
    suggestion_queue = s.suggestion_queue
    profile = s.profile
    event_log = s.event_log
    from events.event_log import EventType
    import time
    
    suggestion = suggestion_queue.get_by_id(suggestion_id)
    if suggestion is None:
        return {"error": "Suggestion not found"}
    
    # Apply to profile
    field = suggestion.suggested_field
    value = suggestion.suggested_value
    
    if field == "likes.music":
        if value not in profile.get_likes().get("music", []):
            profile._data.setdefault("likes", {}).setdefault("music", []).append(value)
    elif field == "likes.topics":
        if value not in profile.get_likes().get("topics", []):
            profile._data.setdefault("likes", {}).setdefault("topics", []).append(value)
    elif field == "likes.food":
        if value not in profile.get_likes().get("food", []):
            profile._data.setdefault("likes", {}).setdefault("food", []).append(value)
    elif field == "key_events":
        profile._data.setdefault("life_story", {}).setdefault("key_events", []).append(value)
    elif field == "daily_routine":
        profile._data.setdefault("daily_routine", {}).setdefault("notes", []).append(value)
    elif field == "family":
        profile._data.setdefault("family", []).append({
            "name": value, "relationship": "mentioned", "notes": "Suggested by Sahaara, approved by caregiver"
        })
    
    # Remove from queue
    suggestion_queue.remove(suggestion_id)
    
    # Log event
    from events.event_log import Event as Evt
    event = Evt(
        timestamp=time.time(),
        type=EventType.SYSTEM_HEALTH,
        profile_id=profile.profile_id,
        detail={
            "message": f"Suggestion approved: {value} -> {field}",
            "severity": "info",
        },
    )
    event_log.append(event)
    
    return {"status": "ok", "applied_field": field, "applied_value": value}


@app.post("/suggestions/{suggestion_id}/reject")
def reject_suggestion(suggestion_id: str, user: dict = Depends(require_caregiver)):
    """Reject a suggested fact."""
    import conversation
    s = conversation.get_session_for_profile(user["profile_id"])
    suggestion_queue = s.suggestion_queue
    
    suggestion = suggestion_queue.get_by_id(suggestion_id)
    if suggestion is None:
        return {"error": "Suggestion not found"}
    
    suggestion_queue.remove(suggestion_id)
    return {"status": "ok", "rejected": suggestion.suggested_value}


@app.get("/turn/context")
def turn_context(turn_index: int = -1, user: dict = Depends(require_caregiver)):
    """Get the explainability context for a Sahaara turn."""
    import conversation
    s = conversation.get_session_for_profile(user["profile_id"])
    event_log = s.event_log
    from events.event_log import EventType
    
    turns = event_log.get_events(EventType.CONVERSATION_TURN)
    sahaara_turns = [t for t in turns if t.detail.get("speaker") == "sahaara"]
    if not sahaara_turns:
        return {"error": "No Sahaara turns found", "explanation": "No responses yet."}
    
    if turn_index < 0 or turn_index >= len(sahaara_turns):
        turn_index = len(sahaara_turns) - 1
    
    turn = sahaara_turns[turn_index]
    context = turn.detail.get("context_trace", {})
    
    return {
        "turn_text": turn.detail.get("text", "")[:150],
        "context_trace": context,
        "explanation": _build_explanation_text(context),
    }


def _build_explanation_text(context: dict) -> str:
    """Build a human-readable explanation from context trace."""
    parts = []
    profile = context.get("profile_facts_used", {})
    intent = context.get("intent", {})
    memory = context.get("memory_box", {})
    esc = context.get("escalation", {})
    
    if profile.get("preferred_address"):
        parts.append(f"Sahaara knows {profile['preferred_address']} is from {profile.get('hometown', 'home')} and was {profile.get('work', 'a teacher')}")
    
    emotion = intent.get("emotion", "neutral")
    if emotion in ("anxious", "distressed", "sad"):
        parts.append(f"detected {emotion} feeling in her words")
    
    if memory.get("matched"):
        parts.append(f"offered Memory Box clip from {memory.get('family_member')}")
    
    if esc.get("is_repeated_question"):
        parts.append("noticed repeated question, kept same warmth")
    
    if esc.get("sustained_distress"):
        parts.append("noted sustained distress, adjusted approach")
    
    tier = esc.get("tier", "green")
    if tier != "green":
        parts.append(f"escalation at {tier} tier")
    
    if context.get("recent_replies_count", 0) > 0:
        parts.append(f"aware of {context.get('recent_replies_count')} recent replies to vary response")
    
    if context.get("used_fallback"):
        parts.append("used safe fallback response")
    
    if not parts:
        return "Sahaara responded based on Asha's profile and the current conversation."
    
    return ". ".join(parts) + "."


@app.post("/monitoring/pause")
def pause_monitoring(user: dict = Depends(require_caregiver)):
    """Pause escalation monitoring."""
    import conversation
    s = conversation.get_session_for_profile(user["profile_id"])
    s.escalation_engine.config.demo_mode = False  # effectively pauses
    return {"status": "ok", "monitoring": "paused"}


@app.post("/monitoring/resume")
def resume_monitoring(user: dict = Depends(require_caregiver)):
    """Resume escalation monitoring."""
    import conversation
    s = conversation.get_session_for_profile(user["profile_id"])
    s.escalation_engine.config.demo_mode = True
    return {"status": "ok", "monitoring": "active"}


@app.post("/scheduler/toggle")
def toggle_scheduler(user: dict = Depends(require_caregiver)):
    """Pause or resume proactive reminders."""
    global scheduler
    if scheduler:
        paused = scheduler.toggle()
        return {"status": "ok", "paused": paused}
    return {"error": "Scheduler not initialized"}


@app.get("/scheduler/status")
def scheduler_status(user: dict = Depends(require_caregiver)):
    """Get scheduler status for dashboard."""
    global scheduler
    if scheduler:
        return scheduler.get_status()
    return {"error": "Scheduler not initialized"}


@app.get("/profiles")
def list_profiles(user: dict = Depends(require_caregiver)):
    """List all available profiles."""
    import database
    return {"profiles": database.list_profiles()}


@app.get("/profile/current")
def get_current_profile(user: dict = Depends(require_caregiver)):
    """Return the active profile for the authenticated caregiver."""
    import database
    profile_id = user.get("profile_id") or "asha_demo_001"
    profile = database.get_profile(profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="profile not found")
    return {"data": profile, "profile_id": profile_id}


@app.get("/media/{filename}")
def get_media_file(request: Request, filename: str):
    """Serve a local music file for in-browser playback with range request support.

    Unguarded on purpose: this is the person's side of the app, the same trust
    level as `/chat` and `/ws/voice` (Asha never logs in). The filename is
    checked against the music directory listing — no path separators, no
    traversal, no arbitrary files.
    """
    from fastapi.responses import Response
    from actions import _music_files

    if Path(filename).name != filename or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail="invalid filename")

    for path in _music_files():
        if path.name == filename:
            file_size = path.stat().st_size
            media_type = "audio/wav" if path.suffix.lower() == ".wav" else "audio/mpeg"

            # Support range requests for audio seeking
            range_header = request.headers.get("Range")
            start = 0
            end = file_size - 1
            status_code = 200
            headers = {
                "Accept-Ranges": "bytes",
                "Content-Length": str(file_size),
                "Content-Type": media_type,
            }

            if range_header:
                # Parse Range header (e.g., "bytes=0-1023")
                range_match = range_header.replace("bytes=", "").split("-")
                if len(range_match) == 2:
                    try:
                        start = int(range_match[0]) if range_match[0] else 0
                        end = int(range_match[1]) if range_match[1] else file_size - 1
                    except ValueError:
                        pass
                    # Clamp to valid range
                    start = max(0, min(start, file_size - 1))
                    end = max(start, min(end, file_size - 1))
                    if start <= end:
                        status_code = 206  # Partial Content
                        headers["Content-Range"] = f"bytes {start}-{end}/{file_size}"
                        headers["Content-Length"] = str(end - start + 1)

            def iter_file():
                with open(path, "rb") as f:
                    f.seek(start)
                    remaining = end - start + 1
                    chunk_size = 8192
                    while remaining > 0:
                        chunk = f.read(min(chunk_size, remaining))
                        if not chunk:
                            break
                        remaining -= len(chunk)
                        yield chunk

            return Response(
                content=iter_file(),
                status_code=status_code,
                headers=headers,
                media_type=media_type,
            )

    raise HTTPException(status_code=404, detail="track not found")


@app.put("/profile/{profile_id}")
def update_profile(profile_id: str, body: dict, user: dict = Depends(require_caregiver)):
    """Save profile edits for the authenticated caregiver."""
    import database
    existing = database.get_profile(profile_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="profile not found")
    data = body.get("data", body)
    database.save_profile(profile_id, data)
    # Reload the in-memory session so the caregiver's changes take effect
    # immediately without restarting the server.
    try:
        conversation._session_manager.reload_profile(profile_id)
        logger.info("Profile %s reloaded after save", profile_id)
    except Exception:
        logger.exception("Could not reload profile session after save")
    return {"status": "ok", "profile_id": profile_id}


# Public routes (no auth required) - Person's side
@app.post("/chat")
async def chat(request: ChatRequest):
    return process_turn(request.message)


async def _speak_initiative(websocket: WebSocket, text: str, state: dict) -> None:
    """Speak one scheduler message through the normal Cartesia voice.

    Same voice as every reply — no second voice, no browser speech synthesis.
    The orb is put into "speaking" first so the mic gate closes and Sahaara's
    own words cannot be transcribed as a new turn.
    """
    try:
        from voice import tts

        state["value"] = "speaking"
        await websocket.send_json({"type": "state", "state": "speaking"})
        async for chunk in tts.stream_speech(text):
            await websocket.send_bytes(chunk)
    except Exception:
        logger.exception("Could not speak initiative")
    finally:
        state["value"] = "listening"
        try:
            await websocket.send_json({"type": "state", "state": "listening"})
        except Exception:
            pass


async def _pump_voice_pipeline(websocket: WebSocket, audio_in, state=None) -> None:
    """Consume the voice pipeline's async generator and forward its events.

    `voice_pipeline.stream()` is an async generator (it yields VoiceEvent), so
    it must be consumed with `async for`. Passing it directly to
    `asyncio.create_task()` raises "a coroutine was expected" and kills the
    orb — see tests/test_voice_ws_wiring.py.

    `state` is an optional mutable dict the handler shares with the scheduler
    initiative consumer, so a timer is only spoken while the orb is idle.
    """
    async for event in voice_pipeline.stream(audio_in):
        if event.kind == "state":
            if state is not None:
                state["value"] = event.state
            await websocket.send_json({"type": "state", "state": event.state})
        elif event.kind == "audio" and event.audio:
            await websocket.send_bytes(event.audio)
        elif event.kind == "play_stream" and event.url:
            # Radio stream or a local track served from /media. The browser
            # owns playback (the backend cannot play audio to the phone).
            await websocket.send_json({"type": "play_stream", "url": event.url})
        elif event.kind == "stop_media":
            await websocket.send_json({"type": "stop_media"})


@app.websocket("/ws/voice")
async def voice_ws(websocket: WebSocket, token: str = Query(default=None)):
    # The browser authenticates with the same HttpOnly session cookie as every
    # other request; `?token=` stays supported for non-browser clients.
    # When auth is enabled but no token is provided, fall back to the default
    # profile (like /chat does) so the no-login companion view can use voice.
    user_data = None
    if auth_enabled():
        from auth_routes import session_token_from_websocket

        session_token = session_token_from_websocket(websocket)
        if session_token:
            from auth import validate_token_and_get_user
            user_data = validate_token_and_get_user(session_token)
            if not user_data:
                await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Invalid or expired token")
                return
        # No token provided — continue with default profile (companion view)
    # If auth is disabled, user_data stays None (default profile)
    
    await websocket.accept()

    import conversation
    s = conversation.get_session_for_profile(user_data["profile_id"]) if user_data else conversation._get_session()
    conversation._session_manager._active_profile_id = user_data["profile_id"] if user_data else "asha_demo_001"
    profile_id = user_data["profile_id"] if user_data else "asha_demo_001"

    async def audio_in():
        """Wraps incoming WebSocket binary frames as an async byte
        generator — this is what asr.py streams to Deepgram. Runs
        concurrently with the send loop below via asyncio, so receiving
        mic audio never blocks on sending audio back out."""
        while True:
            try:
                data = await websocket.receive_bytes()
            except WebSocketDisconnect:
                return
            yield data

    try:
        # Start the voice pipeline. stream() is an async generator, so it is
        # consumed by the pump coroutine — create_task() cannot take a generator.
        voice_state = {"value": "listening"}
        pipeline_task = asyncio.create_task(
            _pump_voice_pipeline(websocket, audio_in(), voice_state)
        )
        
        # Start consuming proactive initiatives from scheduler
        import scheduler
        scheduler_for_profile = scheduler.get_scheduler_for_profile(profile_id)
        voice_queue = scheduler_for_profile.get_voice_initiative_queue()

        # A timer only speaks while someone is actually here. With no session
        # open it goes to the dashboard and is never queued for later.
        scheduler_for_profile.mark_voice_session(True)
        
        async def initiative_consumer():
            """Consume proactive initiatives from scheduler and send to WebSocket.

            A timer/reminder is spoken as well as shown — through the same
            Cartesia voice as every other reply (never a second voice). It is
            only spoken while the orb is idle, so it can never talk over a
            turn that is already in progress.
            """
            while True:
                try:
                    initiative = await voice_queue.get()
                    await websocket.send_json({
                        "type": "initiative",
                        "text": initiative["text"],
                        "slot_id": initiative["slot_id"],
                        "wording_id": initiative["wording_id"],
                        "category": initiative["category"],
                    })

                    if (
                        initiative.get("category") in ("timer", "reminder")
                        and voice_state["value"] == "listening"
                    ):
                        await _speak_initiative(websocket, initiative["text"], voice_state)
                except asyncio.CancelledError:
                    break
                except Exception:
                    logger.exception("Error consuming voice initiative")
                    break
        
        initiative_task = asyncio.create_task(initiative_consumer())
        
        try:
            await pipeline_task
        except WebSocketDisconnect:
            logger.info("Voice WebSocket client disconnected.")
        except voice_pipeline.VoiceBackendUnavailable as e:
            logger.error("Voice backend unavailable: %s", e)
            try:
                await websocket.send_json({"type": "error", "message": str(e)})
            except Exception:
                pass
        except Exception:
            logger.exception("Voice WebSocket handler crashed.")
            try:
                await websocket.send_json({"type": "error", "message": "internal error"})
            except Exception:
                pass
        finally:
            scheduler_for_profile.mark_voice_session(False)
            pipeline_task.cancel()
            try:
                await pipeline_task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
            initiative_task.cancel()
            try:
                await initiative_task
            except asyncio.CancelledError:
                pass
            try:
                await websocket.close()
            except Exception:
                pass
    except WebSocketDisconnect:
        logger.info("Voice WebSocket client disconnected.")
    except voice_pipeline.VoiceBackendUnavailable as e:
        logger.error("Voice backend unavailable: %s", e)
        try:
            await websocket.send_json({"type": "error", "message": str(e)})
        except Exception:
            pass
    except Exception:
        logger.exception("Voice WebSocket handler crashed.")
        try:
            await websocket.send_json({"type": "error", "message": "internal error"})
        except Exception:
            pass
    finally:
        try:
            await websocket.close()
        except Exception:
            pass