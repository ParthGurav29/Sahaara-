"""
auth_routes.py

Authentication routes for Sahaara M2.
Feature-gated by SAHAARA_AUTH_ENABLED.
"""

import os
import json
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Response, Request, WebSocket, status
from pydantic import BaseModel

from auth import (
    hash_password, verify_password,
    create_token, validate_token, revoke_token,
    check_rate_limit, record_failed_login,
    create_user, get_user_by_username, get_user_by_id,
    seed_user_if_needed
)

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    user_id: int
    username: str
    role: str
    profile_id: str
    token: str  # Returned in body for non-browser clients


class MeResponse(BaseModel):
    user_id: int
    username: str
    role: str
    profile_id: str


def auth_enabled() -> bool:
    """Check if auth feature flag is enabled."""
    return os.environ.get("SAHAARA_AUTH_ENABLED", "false").lower() == "true"


def cookie_secure() -> bool:
    """
    Whether the session cookie carries the `Secure` attribute.

    Default OFF so plain-HTTP localhost/adb-reverse development keeps working.
    Set `SAHAARA_COOKIE_SECURE=true` wherever the app is served over HTTPS: the
    browser then refuses to send the cookie over http:// at all.
    """
    return os.environ.get("SAHAARA_COOKIE_SECURE", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def session_token_from_request(request: Request) -> str | None:
    """
    The session token for this request: cookie first, bearer header second.

    The browser never stores the token itself — it lives only in the HttpOnly
    cookie — but command-line/API clients may still send a bearer header.
    """
    token = request.cookies.get("auth_token")
    if token:
        return token
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        return auth_header[7:]
    return None


def session_token_from_websocket(websocket: WebSocket) -> str | None:
    """
    The session token for a WebSocket handshake: session cookie first, then
    `?token=`.

    Browsers attach the cookie to a same-origin upgrade automatically, so the
    app never has to put a long-lived credential in a URL. The query parameter
    stays supported for non-browser clients and test tooling.
    """
    token = websocket.cookies.get("auth_token")
    if token:
        return token
    return websocket.query_params.get("token")


# Stub user for when auth is disabled (maintains backward compatibility)
STUB_CAREGIVER = {
    "user_id": 0,
    "username": "caregiver",
    "role": "caregiver",
    "profile_id": "asha_demo_001",
}


async def get_current_user_optional(request: Request) -> dict:
    """
    Get current user if auth is enabled, otherwise return stub.
    Does NOT raise 401 - used for endpoints that work both with and without auth.
    """
    if not auth_enabled():
        return STUB_CAREGIVER
    
    # Try cookie first (for browser), then Authorization header
    token = session_token_from_request(request)

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    from auth import validate_token, get_user_by_id
    user_id = validate_token(token)
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    user = get_user_by_id(user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )
    
    return {
        "user_id": user["id"],
        "username": user["username"],
        "role": user["role"],
        "profile_id": user["profile_id"],
    }


def _json_field(value, default):
    """Defensively decode a JSON settings column (handles legacy double-encoding)."""
    from database import decode_json_field
    return decode_json_field(value, default)


async def require_caregiver(user: dict = Depends(get_current_user_optional)) -> dict:
    """
    Dependency that requires caregiver role.
    If auth is disabled, returns stub caregiver (backward compatible).
    If auth is enabled, validates token and checks role.
    """
    if user["role"] != "caregiver":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Caregiver role required",
        )
    return user


@router.post("/login", response_model=LoginResponse)
async def login(request: LoginRequest, response: Response, req: Request):
    """
    Login with username and password.
    Sets HttpOnly cookie and returns token in body.
    Rate limited to 5 attempts per minute per IP.
    """
    if not auth_enabled():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication not enabled on this server",
        )
    
    # Rate limit by IP
    client_ip = req.client.host if req.client else "unknown"
    rate_key = f"login:{client_ip}"
    if not check_rate_limit(rate_key):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many login attempts. Please wait.",
        )
    
    user = get_user_by_username(request.username)
    if not user or not verify_password(request.password, user["password_hash"], user["salt"]):
        record_failed_login(rate_key)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )
    
    # Successful login
    raw_token, expires_at = create_token(user["id"])
    
    # Set HttpOnly cookie (SameSite=Lax for CSRF protection, works with 127.0.0.1).
    # `secure` is opt-in via SAHAARA_COOKIE_SECURE: turning it on unconditionally
    # would drop the cookie on plain-HTTP localhost and adb-reverse testing.
    # `domain="localhost"` makes the cookie available to all localhost ports
    # (frontend on :5173/:5174, backend on :8000) so the proxy flow works.
    response.set_cookie(
        key="auth_token",
        value=raw_token,
        httponly=True,
        secure=cookie_secure(),
        samesite="lax",
        max_age=12 * 3600,  # 12 hours
        path="/",
        domain="localhost",
    )
    
    return LoginResponse(
        user_id=user["id"],
        username=user["username"],
        role=user["role"],
        profile_id=user["profile_id"],
        token=raw_token,
    )


@router.post("/logout")
async def logout(request: Request, response: Response):
    """Logout - revoke token and clear cookie."""
    if not auth_enabled():
        # Even if auth disabled, clear cookie for cleanliness
        response.delete_cookie(key="auth_token", path="/", domain="localhost")
        return {"message": "Logged out"}

    token = session_token_from_request(request)
    if token:
        revoke_token(token)
    
    response.delete_cookie(key="auth_token", path="/", domain="localhost")
    return {"message": "Logged out successfully"}


@router.get("/me", response_model=MeResponse)
async def get_me(user: dict = Depends(get_current_user_optional)):
    """
    Current session user.

    Deliberately does **not** return the token: it lives only in the HttpOnly
    cookie, and SSE/WebSocket authenticate from that cookie too.
    """
    return MeResponse(**user)


@router.get("/status")
async def auth_status():
    """Check if authentication is enabled."""
    return {"enabled": auth_enabled()}


# Scheduler settings endpoints (caregiver only)
class SchedulerSettingsRequest(BaseModel):
    enabled: bool = True
    max_checkins_per_day: int = 4
    min_gap_after_conversation_minutes: int = 20
    checkin_window_minutes: int = 15
    response_window_minutes: int = 10
    retry_once: bool = True
    intro_throttle_hours: int = 24
    checkin_times: list[str] = ["08:00", "14:00", "19:00"]
    quiet_hours_start: str = "22:00"
    quiet_hours_end: str = "07:00"
    intro_enabled: bool = True
    intro_overrides: dict = {}
    wording_recent_limit: int = 4
    proactive_v2_enabled: bool = True


class SchedulerSettingsResponse(BaseModel):
    enabled: bool
    max_checkins_per_day: int
    min_gap_after_conversation_minutes: int
    checkin_window_minutes: int
    response_window_minutes: int
    retry_once: bool
    intro_throttle_hours: int
    checkin_times: list[str]
    quiet_hours_start: str
    quiet_hours_end: str
    intro_enabled: bool
    intro_overrides: dict
    wording_recent_limit: int
    proactive_v2_enabled: bool


class IntroOverrideRequest(BaseModel):
    pool_key: str  # e.g., "intro", "morning_greeting", etc.
    text: str


@router.get("/scheduler/settings", response_model=SchedulerSettingsResponse)
async def get_scheduler_settings(user: dict = Depends(require_caregiver)):
    """Get current scheduler settings for the caregiver's profile."""
    from database import get_scheduler_settings, get_default_scheduler_settings
    settings = get_scheduler_settings(user["profile_id"])
    if not settings:
        # Return defaults if not set
        defaults = get_default_scheduler_settings()
        return SchedulerSettingsResponse(**defaults)
    # Convert DB row to response model
    return SchedulerSettingsResponse(
        enabled=bool(settings["enabled"]),
        max_checkins_per_day=settings["max_checkins_per_day"],
        min_gap_after_conversation_minutes=settings["min_gap_after_conversation_minutes"],
        checkin_window_minutes=settings["checkin_window_minutes"],
        response_window_minutes=settings["response_window_minutes"],
        retry_once=bool(settings["retry_once"]),
        intro_throttle_hours=settings["intro_throttle_hours"],
        checkin_times=_json_field(settings["checkin_times"], []),
        quiet_hours_start=settings["quiet_hours_start"],
        quiet_hours_end=settings["quiet_hours_end"],
        intro_enabled=bool(settings["intro_enabled"]),
        intro_overrides=_json_field(settings["intro_overrides"], {}),
        wording_recent_limit=settings["wording_recent_limit"],
        proactive_v2_enabled=bool(settings["proactive_v2_enabled"]),
    )


@router.put("/scheduler/settings", response_model=SchedulerSettingsResponse)
async def update_scheduler_settings(
    request: SchedulerSettingsRequest,
    user: dict = Depends(require_caregiver)
):
    """Update scheduler settings for the caregiver's profile."""
    from database import save_scheduler_settings, get_scheduler_settings
    settings_dict = request.model_dump()
    save_scheduler_settings(user["profile_id"], settings_dict)
    # Return updated settings
    updated = get_scheduler_settings(user["profile_id"])
    if not updated:
        raise HTTPException(status_code=500, detail="Failed to save settings")
    return SchedulerSettingsResponse(
        enabled=bool(updated["enabled"]),
        max_checkins_per_day=updated["max_checkins_per_day"],
        min_gap_after_conversation_minutes=updated["min_gap_after_conversation_minutes"],
        checkin_window_minutes=updated["checkin_window_minutes"],
        response_window_minutes=updated["response_window_minutes"],
        retry_once=bool(updated["retry_once"]),
        intro_throttle_hours=updated["intro_throttle_hours"],
        checkin_times=_json_field(updated["checkin_times"], []),
        quiet_hours_start=updated["quiet_hours_start"],
        quiet_hours_end=updated["quiet_hours_end"],
        intro_enabled=bool(updated["intro_enabled"]),
        intro_overrides=_json_field(updated["intro_overrides"], {}),
        wording_recent_limit=updated["wording_recent_limit"],
        proactive_v2_enabled=bool(updated["proactive_v2_enabled"]),
    )


@router.get("/scheduler/status")
async def get_scheduler_status(user: dict = Depends(require_caregiver)):
    """Get current scheduler status for the caregiver's profile."""
    import scheduler
    scheduler_instance = scheduler.get_scheduler_for_profile(user["profile_id"])
    return scheduler_instance.get_status()


# ---- Caregiver Control Endpoints (M3 Step 7) ----

class CheckinTimeItem(BaseModel):
    time: str  # HH:MM format
    label: str  # e.g., "morning_greeting", "evening_checkin"
    enabled: bool = True


class CheckinTimesRequest(BaseModel):
    checkin_times: list[CheckinTimeItem]


class CheckinTimesResponse(BaseModel):
    checkin_times: list[CheckinTimeItem]


def _checkin_label(index: int, total: int) -> str:
    """Mirror the engine's positional label mapping (first=morning, last=evening)."""
    if total == 1:
        return "daytime_checkin"
    if index == 0:
        return "morning_greeting"
    if index == total - 1:
        return "evening_checkin"
    return "daytime_checkin"


@router.get("/scheduler/checkin-times", response_model=CheckinTimesResponse)
async def get_checkin_times(user: dict = Depends(require_caregiver)):
    """Get configured check-in times for the caregiver's profile."""
    from database import get_scheduler_settings
    settings = get_scheduler_settings(user["profile_id"])
    if not settings:
        from database import get_default_scheduler_settings
        defaults = get_default_scheduler_settings()
        times = defaults["checkin_times"]
    else:
        times = _json_field(settings["checkin_times"], [])

    return CheckinTimesResponse(checkin_times=[
        CheckinTimeItem(time=t, label=_checkin_label(i, len(times)), enabled=True)
        for i, t in enumerate(times)
    ])


@router.put("/scheduler/checkin-times")
async def update_checkin_times(
    request: CheckinTimesRequest,
    user: dict = Depends(require_caregiver)
):
    """Update check-in times for the caregiver's profile."""
    from database import get_scheduler_settings, save_scheduler_settings
    settings = get_scheduler_settings(user["profile_id"]) or {}
    
    # Validate times
    for item in request.checkin_times:
        try:
            datetime.strptime(item.time, "%H:%M")
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid time format: {item.time}. Use HH:MM format.")
    
    # Update checkin_times in settings
    current = get_scheduler_settings(user["profile_id"]) or {}
    checkin_times = [item.time for item in request.checkin_times]
    settings_dict = dict(current) if current else {}
    settings_dict["checkin_times"] = json.dumps([item.time for item in request.checkin_times])
    save_scheduler_settings(user["profile_id"], settings_dict)
    
    return {"message": "Check-in times updated", "checkin_times": request.checkin_times}


class QuietHoursRequest(BaseModel):
    start: str  # HH:MM format
    end: str    # HH:MM format


class QuietHoursResponse(BaseModel):
    start: str
    end: str


@router.get("/scheduler/quiet-hours", response_model=QuietHoursResponse)
async def get_quiet_hours(user: dict = Depends(require_caregiver)):
    """Get quiet hours for the caregiver's profile."""
    from database import get_scheduler_settings
    settings = get_scheduler_settings(user["profile_id"])
    if not settings:
        return QuietHoursResponse(start="22:00", end="07:00")
    return QuietHoursResponse(
        start=settings.get("quiet_hours_start", "22:00"),
        end=settings.get("quiet_hours_end", "07:00")
    )


@router.put("/scheduler/quiet-hours")
async def update_quiet_hours(
    request: QuietHoursRequest,
    user: dict = Depends(require_caregiver)
):
    """Update quiet hours for the caregiver's profile."""
    from database import get_scheduler_settings, save_scheduler_settings
    
    # Validate time format
    try:
        datetime.strptime(request.start, "%H:%M")
        datetime.strptime(request.end, "%H:%M")
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid time format. Use HH:MM format.")
    
    settings = get_scheduler_settings(user["profile_id"]) or {}
    settings_dict = dict(settings) if settings else {}
    settings_dict["quiet_hours_start"] = request.start
    settings_dict["quiet_hours_end"] = request.end
    save_scheduler_settings(user["profile_id"], settings_dict)
    
    return {"message": "Quiet hours updated", "start": request.start, "end": request.end}


class IntroOverrideRequest(BaseModel):
    pool_key: str  # e.g., "intro", "morning_greeting", etc.
    text: str


@router.get("/scheduler/intro-overrides")
async def get_intro_overrides(user: dict = Depends(require_caregiver)):
    """Get intro wording overrides for the caregiver's profile."""
    from database import get_scheduler_settings
    settings = get_scheduler_settings(user["profile_id"])
    if not settings:
        return {"overrides": {}}
    return {"overrides": _json_field(settings.get("intro_overrides", "{}"), {})}


@router.put("/scheduler/intro-override")
async def set_intro_override(
    request: IntroOverrideRequest,
    user: dict = Depends(require_caregiver)
):
    """Set a custom intro wording override for a specific pool."""
    from database import get_scheduler_settings, save_scheduler_settings
    settings = get_scheduler_settings(user["profile_id"])
    if not settings:
        from database import get_default_scheduler_settings, save_scheduler_settings
        defaults = get_default_scheduler_settings()
        overrides = defaults.get("intro_overrides", {})
    else:
        overrides = _json_field(settings["intro_overrides"], {})
    
    # Validate the override text passes guardrails
    from safety.guardrails import check_guardrails
    result = check_guardrails(request.text)
    if not result.passed:
        raise HTTPException(
            status_code=400,
            detail=f"Wording rejected by guardrails: {result.violations}"
        )
    
    overrides[request.pool_key] = request.text
    
    # Save updated settings
    current = get_scheduler_settings(user["profile_id"]) or {}
    current["intro_overrides"] = json.dumps(overrides)
    save_scheduler_settings(user["profile_id"], current)
    
    return {"message": f"Override set for {request.pool_key}", "text": request.text}


class CheckinOutcome(BaseModel):
    slot_id: str
    checkin_type: str
    fired_at: float
    fired_at_iso: str
    wording_id: str
    response_received: bool
    retry_count: int
    skip_reason: str
    delivered_audio: bool
    audio_blocked: bool


class CheckinHistoryResponse(BaseModel):
    history: list[CheckinOutcome]


@router.get("/scheduler/history", response_model=CheckinHistoryResponse)
async def get_checkin_history(
    user: dict = Depends(require_caregiver)
):
    """Get check-in history for the caregiver's profile."""
    from database import get_checkin_fired_history
    history = get_checkin_fired_history(profile_id=user["profile_id"], limit=100)
    
    outcomes = []
    for r in history:
        outcomes.append(CheckinOutcome(
            slot_id=r["slot_id"],
            checkin_type=r["checkin_type"],
            fired_at=r["fired_at"],
            fired_at_iso=datetime.fromtimestamp(r["fired_at"]).isoformat() if isinstance(r["fired_at"], (int, float)) else r["fired_at"],
            wording_id=r["wording_id"],
            response_received=bool(r["response_received"]),
            retry_count=r["retry_count"],
            skip_reason=r["skip_reason"],
            delivered_audio=bool(r["delivered_audio"]),
            audio_blocked=bool(r["audio_blocked"]),
        ))
    
    return CheckinHistoryResponse(history=outcomes)


# ---- Simulated Day (M3 Step 8) ----

class SimulateDayRequest(BaseModel):
    """Request for a read-only simulated day preview."""
    date: str | None = None          # YYYY-MM-DD (default: today)
    start_time: str = "06:00"        # HH:MM
    end_time: str = "23:00"          # HH:MM
    step_minutes: int = 15           # clock resolution
    include_history: bool = False    # seed with real fired history


class SimulatedDecisionOut(BaseModel):
    action: str
    slot_id: str
    reason: str
    wording_id: str = ""
    wording_text: str = ""
    checkin_type: str
    is_intro: bool = False
    medication_label: str = ""


class SimulatedStepOut(BaseModel):
    time: str
    time_hhmm: str
    decisions: list[SimulatedDecisionOut]


class SimulatedSummaryOut(BaseModel):
    total_fired: int
    total_skipped: int
    total_expired: int
    total_gated_steps: int
    fired_by_type: dict[str, int]
    skip_reasons: dict[str, int]
    gated_reasons: dict[str, int]


class SimulateDayResponse(BaseModel):
    profile_id: str
    date: str
    start_time: str
    end_time: str
    step_minutes: int
    feature_enabled: bool
    steps: list[SimulatedStepOut]
    summary: SimulatedSummaryOut


@router.post("/scheduler/simulate-day", response_model=SimulateDayResponse)
async def simulate_scheduler_day(
    request: SimulateDayRequest,
    user: dict = Depends(require_caregiver),
):
    """Run a read-only simulated day for the caregiver's profile.

    Advances the scheduling engine across a compressed day and returns every
    fire/skip/expire decision. Persists nothing; safe to call repeatedly.
    """
    # Validate time formats
    for value in (request.start_time, request.end_time):
        try:
            datetime.strptime(value, "%H:%M")
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid time format: {value}. Use HH:MM format.",
            )
    if request.date:
        try:
            datetime.strptime(request.date, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid date: {request.date}. Use YYYY-MM-DD format.",
            )
    if not (1 <= request.step_minutes <= 1440):
        raise HTTPException(
            status_code=400,
            detail="step_minutes must be between 1 and 1440.",
        )

    import scheduler as scheduler_module

    try:
        scheduler_instance = scheduler_module.get_scheduler_for_profile(user["profile_id"])
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    try:
        result = scheduler_instance.simulate_day(
            start_time=request.start_time,
            end_time=request.end_time,
            step_minutes=request.step_minutes,
            date_str=request.date,
            include_history=request.include_history,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return result.to_dict()