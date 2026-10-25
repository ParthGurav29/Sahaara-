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

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from conversation import process_turn, provider, _get_session, get_session_for_profile
from dashboard.stream import router as stream_router, set_main_loop
from dashboard.summary import generate_daily_summary, DailySummary
from voice import voice_pipeline
from database import init_db, get_profile
from auth import auth_router, get_current_user, require_caregiver, require_person, require_profile_access
from auth.deps import get_current_user_ws, require_caregiver_ws, require_person_ws, require_profile_access_ws

logger = logging.getLogger("main")

app = FastAPI(title="Sahaara API")

app.include_router(stream_router)
app.include_router(auth_router)


@app.on_event("startup")
async def startup_event():
    """Initialize database and start background services."""
    # Initialize database (creates tables, runs migrations)
    init_db()
    
    """Capture the main event loop for thread-safe broadcasting."""
    set_main_loop(asyncio.get_running_loop())
    
    # Setup SSE stream hooks (requires active session)
    from dashboard.stream import setup_stream_hooks
    setup_stream_hooks()
    
    # Start proactive scheduler
    import conversation
    s = conversation._get_session()
    from scheduler import Scheduler, make_llm_caller
    global scheduler
    scheduler = Scheduler(s.profile)
    llm_call = make_llm_caller()
    await scheduler.start(llm_call=llm_call)
    
    # Wire scheduler into conversation module for set_reminder action
    import conversation as conv
    conv._scheduler = scheduler
    
    # Background task to push scheduler initiatives into SSE
    asyncio.create_task(_broadcast_scheduler_initiatives())


async def _broadcast_scheduler_initiatives():
    """Push scheduler initiative messages into SSE broadcast."""
    from dashboard.stream import _broadcast_to_queues
    while True:
        try:
            initiative = await scheduler.get_initiative()
            if initiative:
                _broadcast_to_queues("sahaara_initiative", initiative["data"])
        except Exception:
            pass
        await asyncio.sleep(1)


@app.on_event("shutdown")
async def shutdown_event():
    global scheduler
    if scheduler:
        await scheduler.stop()


app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
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


@app.get("/escalation/status")
def escalation_status(user: User = Depends(require_caregiver)):
    """Current escalation tier and status for dashboard."""
    import conversation
    s = conversation.get_session_for_profile(user.profile_id)
    return s.escalation_engine.get_status()


@app.get("/escalation/events")
def escalation_events(
    event_type: str = None,
    since: float = None,
    limit: int = 100,
    user: User = Depends(require_caregiver),
):
    """Get recent events from the event log."""
    import conversation
    s = conversation.get_session_for_profile(user.profile_id)
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
def escalation_summary(user: User = Depends(require_caregiver)):
    """Session summary for daily summary screen."""
    import conversation
    s = conversation.get_session_for_profile(user.profile_id)
    return s.event_log.summary()


@app.get("/daily-summary")
def daily_summary(user: User = Depends(require_caregiver)):
    """Generate daily summary from event log for caregiver dashboard."""
    import conversation
    s = conversation.get_session_for_profile(user.profile_id)
    summary = generate_daily_summary(s.event_log, s.profile)
    return summary.__dict__


@app.post("/demo/reset")
def demo_reset(user: User = Depends(require_person)):
    """Reset all demo state for a clean re-run."""
    import conversation
    s = conversation.get_session_for_profile(user.profile_id)
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
    status: str = None,
    limit: int = 50,
    user: User = Depends(require_caregiver),
):
    """Get alerts for the dashboard. Now backed by AlertStore."""
    import conversation
    s = conversation.get_session_for_profile(user.profile_id)
    alert_store = s.alert_store
    alerts = alert_store.get_all(status=status)
    return {
        "alerts": [a.to_dict() for a in alerts[-limit:]],
        "unread_count": alert_store.unread_count(),
    }


@app.post("/alerts/{alert_id}/acknowledge")
def acknowledge_alert(alert_id: str, body: dict = None, user: User = Depends(require_caregiver)):
    """Acknowledge an alert with an optional note."""
    import conversation
    s = conversation.get_session_for_profile(user.profile_id)
    alert_store = s.alert_store
    event_log = s.event_log
    
    note = (body or {}).get("note", "")
    alert = alert_store.acknowledge(alert_id, note)
    if alert is None:
        return {"error": "Alert not found", "alert_id": alert_id}
    
    event_log.log_alert_acknowledged(alert_id, note)
    return {"status": "ok", "alert": alert.to_dict()}


@app.get("/alerts/test")
def test_alert(user: User = Depends(require_caregiver)):
    """Test endpoint to build a sample alert."""
    import conversation
    s = conversation.get_session_for_profile(user.profile_id)
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
def alert_context(alert_id: str, turns: int = 10, user: User = Depends(require_caregiver)):
    """Get an alert with surrounding conversation turns for detail view."""
    import conversation
    s = conversation._get_session()
    if user.profile_id != s.profile_id:
        raise HTTPException(status_code=403, detail="Access denied to this profile")
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
    tier_path = [f"{e.detail.get('old_tier', '?')} → {e.detail.get('new_tier', '?')}" for e in tier_events]
    
    return {
        "alert": alert.to_dict(),
        "surrounding_turns": surrounding_turns[-turns:],
        "trigger_event": trigger_event,
        "tier_path": tier_path,
        "recommended_action": alert.message,
    }


@app.get("/suggestions")
def get_suggestions(user: User = Depends(require_caregiver)):
    """Get pending profile fact suggestions for caregiver review."""
    import conversation
    s = conversation.get_session_for_profile(user.profile_id)
    suggestion_queue = s.suggestion_queue
    return {
        "suggestions": [s.to_dict() for s in suggestion_queue.get_all()],
        "total": len(suggestion_queue.get_all()),
    }


@app.post("/suggestions/{suggestion_id}/approve")
def approve_suggestion(suggestion_id: str, user: User = Depends(require_caregiver)):
    """Approve a suggested fact and apply it to the profile."""
    import conversation
    s = conversation.get_session_for_profile(user.profile_id)
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
def reject_suggestion(suggestion_id: str, user: User = Depends(require_caregiver)):
    """Reject a suggested fact."""
    import conversation
    s = conversation.get_session_for_profile(user.profile_id)
    suggestion_queue = s.suggestion_queue
    
    suggestion = suggestion_queue.get_by_id(suggestion_id)
    if suggestion is None:
        return {"error": "Suggestion not found"}
    
    suggestion_queue.remove(suggestion_id)
    return {"status": "ok", "rejected": suggestion.suggested_value}


@app.get("/turn/context")
def turn_context(turn_index: int = -1, user: User = Depends(require_caregiver)):
    """Get the explainability context for a Sahaara turn."""
    import conversation
    s = conversation.get_session_for_profile(user.profile_id)
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
def pause_monitoring(user: User = Depends(require_caregiver)):
    """Pause escalation monitoring."""
    import conversation
    s = conversation.get_session_for_profile(user.profile_id)
    s.escalation_engine.config.demo_mode = False  # effectively pauses
    return {"status": "ok", "monitoring": "paused"}


@app.post("/monitoring/resume")
def resume_monitoring(user: User = Depends(require_caregiver)):
    """Resume escalation monitoring."""
    import conversation
    s = conversation.get_session_for_profile(user.profile_id)
    s.escalation_engine.config.demo_mode = True
    return {"status": "ok", "monitoring": "active"}


@app.post("/scheduler/toggle")
def toggle_scheduler(user: User = Depends(require_caregiver)):
    """Pause or resume proactive reminders."""
    import conversation
    s = conversation.get_session_for_profile(user.profile_id)
    global scheduler
    if scheduler:
        paused = scheduler.toggle()
        return {"status": "ok", "paused": paused}
    return {"error": "Scheduler not initialized"}


@app.get("/scheduler/status")
def scheduler_status(user: User = Depends(require_caregiver)):
    """Get scheduler status for dashboard."""
    import conversation
    s = conversation.get_session_for_profile(user.profile_id)
    global scheduler
    if scheduler:
        return scheduler.get_status()
    return {"error": "Scheduler not initialized"}


@app.get("/profiles")
def list_profiles(user: User = Depends(require_caregiver)):
    """List all available profiles."""
    import database
    return {"profiles": database.list_profiles()}


@app.get("/profile/current")
def get_current_profile(user: User = Depends(get_current_user)):
    """Get the active profile's data."""
    import conversation
    s = conversation.get_session_for_profile(user.profile_id)
    return {"profile_id": s.profile_id, "data": s.profile._data}


@app.put("/profile/{profile_id}")
def update_profile(profile_id: str, body: dict, user: User = Depends(require_caregiver)):
    """Update a profile's data.

    Shape-validated: a malformed payload must never corrupt the session
    or the DB. History (Oct 23 black-screen bug): this handler blindly did
    `s.profile._data = data`, so a partial/malformed PUT replaced object
    sections with empty strings and crashed the Profile tab's render.
    """
    import database
    import conversation
    s = conversation.get_session_for_profile(user.profile_id)
    if user.profile_id != s.profile_id:
        raise HTTPException(status_code=403, detail="Access denied to this profile")
    data = body.get("data", body)

    # --- Shape validation (422 on anything that could poison consumers) ---
    if not isinstance(data, dict):
        raise HTTPException(status_code=422, detail="profile data must be an object")
    for section in ("name", "life_story", "likes", "avoid", "daily_routine"):
        if section in data and not isinstance(data[section], dict):
            raise HTTPException(status_code=422, detail=f"profile section '{section}' must be an object")
    for section in ("family",):
        if section in data and not isinstance(data[section], list):
            raise HTTPException(status_code=422, detail=f"profile section '{section}' must be an array")
    if "likes" in data:
        for k, v in data["likes"].items():
            if not isinstance(v, list):
                raise HTTPException(status_code=422, detail=f"likes.{k} must be an array")
    if "daily_routine" in data:
        dr = data["daily_routine"]
        if "meals" in dr and not isinstance(dr["meals"], dict):
            raise HTTPException(status_code=422, detail="daily_routine.meals must be an object")
        if "medication_times" in dr and not isinstance(dr["medication_times"], list):
            raise HTTPException(status_code=422, detail="daily_routine.medication_times must be an array")
    if "avoid" in data and "topics" in data["avoid"] and not isinstance(data["avoid"]["topics"], list):
        raise HTTPException(status_code=422, detail="avoid.topics must be an array")

    # Merge with the existing profile so a partial PUT cannot DELETE
    # sections the client didn't send (unknown keys pass through).
    if s.profile_id == profile_id:
        merged = {**s.profile._data, **data}
    else:
        merged = data
    database.save_profile(profile_id, merged)
    # Refresh active session only AFTER a successful DB write
    if s.profile_id == profile_id:
        s.profile._data = merged

    return {"status": "ok", "profile_id": profile_id}


@app.websocket("/ws/alerts")
async def alerts_ws(websocket: WebSocket, user: User = Depends(require_caregiver_ws)):
    """WebSocket for real-time alert updates (caregiver only)."""
    await websocket.accept()
    import conversation
    s = conversation.get_session_for_profile(user.profile_id)
    escalation_engine = s.escalation_engine
    try:
        while True:
            await websocket.send_json({
                "type": "status",
                "data": escalation_engine.get_status(),
            })
            await asyncio.sleep(1.0)
    except WebSocketDisconnect:
        logger.info("Alerts WebSocket client disconnected.")
    except Exception:
        logger.exception("Alerts WebSocket handler crashed.")
    finally:
        try:
            await websocket.close()
        except Exception:
            pass


@app.websocket("/ws/escalation")
async def escalation_ws(websocket: WebSocket, user: User = Depends(require_caregiver_ws)):
    """WebSocket for real-time escalation updates (SSE alternative, caregiver only)."""
    await websocket.accept()
    import conversation
    s = conversation.get_session_for_profile(user.profile_id)
    event_log = s.event_log
    escalation_engine = s.escalation_engine
    last_event_count = len(event_log.get_all())
    
    try:
        while True:
            # Check for new events
            current_events = event_log.get_all()
            if len(current_events) > last_event_count:
                new_events = current_events[last_event_count:]
                for event in new_events:
                    await websocket.send_json(event.to_dict())
                last_event_count = len(current_events)
            
            # Also send current status periodically
            await websocket.send_json({
                "type": "status",
                "data": escalation_engine.get_status(),
            })
            
            await asyncio.sleep(1.0)
    except WebSocketDisconnect:
        logger.info("Escalation WebSocket client disconnected.")
    except Exception:
        logger.exception("Escalation WebSocket handler crashed.")
    finally:
        try:
            await websocket.close()
        except Exception:
            pass


@app.post("/chat")
async def chat(request: ChatRequest, user: User = Depends(require_person)):
    import conversation
    s = conversation.get_session_for_profile(user.profile_id)
    # Set active session for this request
    conversation._session_manager._active_profile_id = user.profile_id
    return process_turn(request.message)


@app.websocket("/ws/voice")
async def voice_ws(websocket: WebSocket, user: User = Depends(require_person_ws)):
    await websocket.accept()
    import conversation
    s = conversation.get_session_for_profile(user.profile_id)
    conversation._session_manager._active_profile_id = user.profile_id

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
        async for event in voice_pipeline.stream(audio_in()):
            if event.kind == "state":
                await websocket.send_json({"type": "state", "state": event.state})
            elif event.kind == "audio" and event.audio:
                await websocket.send_bytes(event.audio)
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