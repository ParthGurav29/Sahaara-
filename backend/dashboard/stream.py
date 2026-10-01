"""
dashboard/stream.py

Real-time event streaming for the caregiver dashboard.
Provides both Server-Sent Events (SSE) and WebSocket endpoints.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Optional

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from sse_starlette.sse import EventSourceResponse

from conversation import event_log, escalation_engine, consent_manager, alert_builder
from events.event_log import EventType, EscalationTier
from safety.escalation import EscalationTier


router = APIRouter(prefix="/stream", tags=["stream"])


# In-memory subscribers for SSE
_sse_subscribers: list[asyncio.Queue] = []

# Store reference to main event loop for thread-safe broadcasting
_main_loop: asyncio.AbstractEventLoop | None = None


def set_main_loop(loop: asyncio.AbstractEventLoop) -> None:
    """Call this at app startup to capture the main event loop."""
    global _main_loop
    _main_loop = loop


async def _broadcast_event(event_type: str, data: dict):
    """Broadcast an event to all SSE subscribers."""
    message = {
        "type": event_type,
        "data": data,
        "timestamp": time.time(),
    }
    dead_queues = []
    for queue in _sse_subscribers:
        try:
            queue.put_nowait(message)
        except asyncio.QueueFull:
            dead_queues.append(queue)
    
    # Clean up dead queues
    for q in dead_queues:
        _sse_subscribers.remove(q)


def _format_sse(event_type: str, data: dict) -> str:
    """Format data as SSE event."""
    return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"


@router.get("/events")
async def stream_events(request: Request):
    """
    Server-Sent Events (SSE) endpoint for real-time updates.
    
    Streams:
    - tier_change: escalation tier changes with alert message
    - consent_change: consent asked/answered
    - distress: sustained distress detected
    - repeat: repeated question detected
    - fallback: system fallback triggered
    - danger_statement: danger statement detected
    - status: periodic status updates (every 5 seconds)
    """
    queue: asyncio.Queue = asyncio.Queue(maxsize=100)
    _sse_subscribers.append(queue)
    
    # Send initial status
    await queue.put({
        "type": "status",
        "data": escalation_engine.get_status(),
    })
    
    async def event_generator():
        try:
            # Send initial events
            for event in event_log.get_all()[-20:]:
                yield _format_sse(event.type.value, event.to_dict())
            
            # Send current status
            yield _format_sse("status", escalation_engine.get_status())
            
            while True:
                if await request.is_disconnected():
                    break
                
                try:
                    # Wait for new events with timeout for periodic status
                    message = await asyncio.wait_for(queue.get(), timeout=5.0)
                    yield _format_sse(message["type"], message["data"])
                except asyncio.TimeoutError:
                    # Send periodic status update
                    yield _format_sse("status", escalation_engine.get_status())
                    
        except Exception as e:
            yield _format_sse("error", {"message": str(e)})
        finally:
            if queue in _sse_subscribers:
                _sse_subscribers.remove(queue)
    
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
        },
    )


@router.websocket("/ws/events")
async def websocket_events(websocket: WebSocket):
    """
    WebSocket endpoint for real-time bidirectional communication.
    
    Client can send:
    - {"action": "get_status"} - request current status
    - {"action": "get_events", "limit": 50} - request recent events
    - {"action": "subscribe"} - subscribe to real-time updates (default)
    
    Server sends:
    - {"type": "status", "data": {...}} - current status
    - {"type": "event", "data": {...}} - new event
    - {"type": "alert", "data": {...}} - new alert
    - {"type": "error", "data": {"message": "..."}} - error
    """
    await websocket.accept()
    
    # Send initial status
    await websocket.send_json({
        "type": "status",
        "data": escalation_engine.get_status(),
    })
    
    # Track last event index for this connection
    last_event_index = len(event_log.get_all())
    
    try:
        while True:
            # Check for new events
            current_events = event_log.get_all()
            if len(current_events) > last_event_index:
                new_events = current_events[last_event_index:]
                for event in new_events:
                    await websocket.send_json({
                        "type": "event",
                        "data": event.to_dict(),
                    })
                last_event_index = len(current_events)
            
            # Check for incoming messages (non-blocking)
            try:
                message = await asyncio.wait_for(websocket.receive_json(), timeout=0.1)
                
                if message.get("action") == "get_status":
                    await websocket.send_json({
                        "type": "status",
                        "data": escalation_engine.get_status(),
                    })
                elif message.get("action") == "get_events":
                    limit = message.get("limit", 50)
                    events = event_log.get_all()[-limit:]
                    await websocket.send_json({
                        "type": "events",
                        "data": [e.to_dict() for e in events],
                    })
                elif message.get("action") == "ping":
                    await websocket.send_json({"type": "pong"})
                    
            except asyncio.TimeoutError:
                pass
            
            # Periodic status update
            await asyncio.sleep(2.0)
            await websocket.send_json({
                "type": "status",
                "data": escalation_engine.get_status(),
            })
            
    except WebSocketDisconnect:
        pass
    except Exception as e:
        try:
            await websocket.send_json({"type": "error", "data": {"message": str(e)}})
        except:
            pass


# Hook into event_log to broadcast new events
_original_append = event_log.append


def _broadcast_append(event):
    _original_append(event)
    # Schedule broadcast on main event loop (thread-safe)
    _schedule_broadcast(event.type.value, event.to_dict())


def _broadcast_to_queues(event_type: str, data: dict):
    """Thread-safe broadcast to all subscriber queues."""
    global _main_loop
    
    message = {
        "type": event_type,
        "data": data,
        "timestamp": time.time(),
    }
    dead_queues = []
    for queue in _sse_subscribers:
        try:
            queue.put_nowait(message)
        except asyncio.QueueFull:
            dead_queues.append(queue)
    
    # Clean up dead queues
    for q in dead_queues:
        if q in _sse_subscribers:
            _sse_subscribers.remove(q)


def _schedule_broadcast(event_type: str, data: dict):
    """Schedule a broadcast on the main event loop from any thread."""
    global _main_loop
    if _main_loop and _main_loop.is_running():
        _main_loop.call_soon_threadsafe(_broadcast_to_queues, event_type, data)


async def _broadcast_event(event_type: str, data: dict):
    """Async version for use in async contexts."""
    _broadcast_to_queues(event_type, data)


event_log.append = _broadcast_append


# Also hook consent manager
_original_ask = consent_manager.ask_consent


def _broadcast_ask(contact_name, contact_relationship, tier, question_text):
    _original_ask(contact_name, contact_relationship, tier, question_text)
    _schedule_broadcast("consent_asked", {
        "contact_name": contact_name,
        "contact_relationship": contact_relationship,
        "tier": tier.value,
        "question": question_text,
        "timestamp": time.time(),
    })


consent_manager.ask_consent = _broadcast_ask


_original_interpret = consent_manager.interpret_response


def _broadcast_interpret(user_message):
    state_before = consent_manager.state
    result = _original_interpret(user_message)
    if state_before == consent_manager.ConsentState.PENDING and result != state_before:
        # Consent was resolved
        contact = consent_manager.context.contact_name if consent_manager.context else None
        if contact:
            _schedule_broadcast("consent_resolved", {
                "contact_name": contact,
                "outcome": result.value,
                "tier": consent_manager.context.tier_when_asked.value if consent_manager.context else None,
                "timestamp": time.time(),
            })
    return result


consent_manager.interpret_response = _broadcast_interpret


# Hook alert builder
_original_alert = alert_builder.build_tier_change_alert


def _broadcast_alert(tier_from, tier_to, reason, context):
    alert = _original_alert(tier_from, tier_to, reason, context)
    _schedule_broadcast("alert", alert.to_dict())
    return alert


alert_builder.build_tier_change_alert = _broadcast_alert