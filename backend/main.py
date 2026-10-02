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

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from conversation import process_turn, provider, escalation_engine, event_log, alert_builder
from dashboard.stream import router as stream_router, set_main_loop
from dashboard.summary import generate_daily_summary, DailySummary
from voice import voice_pipeline

logger = logging.getLogger("main")

app = FastAPI(title="Sahaara API")

app.include_router(stream_router)


@app.on_event("startup")
async def startup_event():
    """Capture the main event loop for thread-safe broadcasting."""
    set_main_loop(asyncio.get_running_loop())


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
def escalation_status():
    """Current escalation tier and status for dashboard."""
    return escalation_engine.get_status()


@app.get("/escalation/events")
def escalation_events(
    event_type: str = None,
    since: float = None,
    limit: int = 100,
):
    """Get recent events from the event log."""
    events = event_log.get_all()
    
    # Filter by type
    if event_type:
        from events.event_log import EventType
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
def escalation_summary():
    """Session summary for daily summary screen."""
    return event_log.summary()


@app.get("/daily-summary")
def daily_summary():
    """Generate daily summary from event log for caregiver dashboard."""
    from conversation import profile
    summary = generate_daily_summary(event_log, profile)
    return summary.__dict__


@app.get("/alerts")
def get_alerts(
    since: float = None,
    limit: int = 50,
):
    """Get alerts for the dashboard."""
    # Since we don't persist alerts separately, we build them from events
    # In production, alerts would be persisted separately
    return {
        "alerts": [],
        "message": "Alerts are built from events. Use /escalation/events and /escalation/status for now.",
    }


@app.get("/alerts/test")
def test_alert():
    """Test endpoint to build a sample alert."""
    from conversation import alert_builder, escalation_engine
    from safety.escalation import EscalationTier
    
    alert = alert_builder.build_tier_change_alert(
        EscalationTier.GREEN,
        EscalationTier.YELLOW,
        "repeat_threshold",
        {"repeat_count": 3, "sustained_distress": False},
    )
    return alert.to_dict()


@app.websocket("/ws/alerts")
async def alerts_ws(websocket: WebSocket):
    """WebSocket for real-time alert updates."""
    await websocket.accept()
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
async def escalation_ws(websocket: WebSocket):
    """WebSocket for real-time escalation updates (SSE alternative)."""
    await websocket.accept()
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
async def chat(request: ChatRequest):
    return process_turn(request.message)


@app.websocket("/ws/voice")
async def voice_ws(websocket: WebSocket):
    await websocket.accept()

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