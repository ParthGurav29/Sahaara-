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

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect, Query, status, HTTPException
from fastapi.responses import StreamingResponse
from sse_starlette.sse import EventSourceResponse

from events.event_log import EventType, EscalationTier
from safety.escalation import EscalationTier
from auth import validate_token_and_get_user, auth_enabled


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


async def _validate_stream_token(request: Request) -> Optional[dict]:
    """Validate token from query param for SSE/WebSocket endpoints."""
    if not auth_enabled():
        return None
    
    token = request.query_params.get("token")
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    
    user_data = validate_token_and_get_user(token)
    if not user_data:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")
    
    return user_data


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
    # Validate token if auth is enabled
    user_data = await _validate_stream_token(request)
    
    queue: asyncio.Queue = asyncio.Queue(maxsize=100)
    _sse_subscribers.append(queue)
    
    # Resolve session state for the authenticated user's profile
    import conversation
    s = conversation.get_session_for_profile(user_data["profile_id"]) if user_data else conversation._get_session()
    _esc_engine = s.escalation_engine
    _event_log = s.event_log
    
    # Send initial status
    await queue.put({
        "type": "status",
        "data": _esc_engine.get_status(),
    })
    
    async def event_generator():
        try:
            # Send initial events
            for event in _event_log.get_all()[-20:]:
                yield _format_sse(event.type.value, event.to_dict())
            
            # Send current status
            yield _format_sse("status", _esc_engine.get_status())
            
            while True:
                if await request.is_disconnected():
                    break
                
                try:
                    # Wait for new events with timeout for periodic status
                    message = await asyncio.wait_for(queue.get(), timeout=5.0)
                    yield _format_sse(message["type"], message["data"])
                except asyncio.TimeoutError:
                    # Send periodic status update
                    yield _format_sse("status", _esc_engine.get_status())
                    
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
async def websocket_events(websocket: WebSocket, token: Optional[str] = Query(None)):
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
    # Validate token if auth is enabled
    if auth_enabled():
        if not token:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Authentication required")
            return
        user_data = validate_token_and_get_user(token)
        if not user_data:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Invalid or expired token")
            return
    else:
        user_data = None
    
    await websocket.accept()
    
    # Resolve session state
    import conversation
    s = conversation.get_session_for_profile(user_data["profile_id"]) if user_data else conversation._get_session()
    _event_log = s.event_log
    _esc_engine = s.escalation_engine
    
    # Send initial status
    await websocket.send_json({
        "type": "status",
        "data": _esc_engine.get_status(),
    })
    
    # Track last event index for this connection
    last_event_index = len(_event_log.get_all())
    
    try:
        while True:
            # Check for new events
            current_events = _event_log.get_all()
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
                        "data": _esc_engine.get_status(),
                    })
                elif message.get("action") == "get_events":
                    limit = message.get("limit", 50)
                    events = _event_log.get_all()[-limit:]
                    await websocket.send_json({
                        "type": "events",
                        "data": [e.to_dict() for e in events],
                    })
            except asyncio.TimeoutError:
                pass
            except WebSocketDisconnect:
                break
            except Exception:
                pass
            
            await asyncio.sleep(0.1)
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        try:
            await websocket.close()
        except Exception:
            pass


def _format_sse(event_type: str, data: dict) -> str:
    """Format data as SSE event."""
    return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"


async def _broadcast_to_queues(event_type: str, data: dict):
    """Internal helper to broadcast to SSE subscribers."""
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
    
    for q in dead_queues:
        if q in _sse_subscribers:
            _sse_subscribers.remove(q)


def setup_stream_hooks():
    """
    Called at app startup to hook into the default session's event log.
    This ensures events from the default session are broadcast to SSE subscribers.
    """
    import conversation
    s = conversation._get_session()
    event_log = s.event_log
    
    original_append = event_log.append
    def hooked_append(event):
        original_append(event)
        # Broadcast to all SSE subscribers
        asyncio.run_coroutine_threadsafe(_broadcast_to_queues(event.type.value, event.to_dict()), _main_loop)
    event_log.append = hooked_append