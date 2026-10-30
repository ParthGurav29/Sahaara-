"""
emergency_routes.py

Caregiver-facing read/cancel surface for emergency-call incidents
(Emergency Call Task 4).

Two rules govern this module:

1. **No phone numbers leave here.** The attempts table stores only a masked
   number (last 4 digits); the incident table stores none at all. Nothing in
   this file can widen that — there is no `phone` column to select.
2. **No claim of delivery.** A status shown here is what the provider
   reported. "acknowledged" means a keypress reached the provider, never that
   a human understood anything.
"""

from __future__ import annotations

import json
import time
import urllib.parse
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request

from auth_routes import require_caregiver
from database import (
    get_emergency_attempts,
    get_emergency_incident,
    get_recent_emergency_incidents,
    update_emergency_incident_status,
)

router = APIRouter()

# Statuses that must never be overwritten by a caregiver cancel.
_TERMINAL_INCIDENT_STATUSES = {"acknowledged", "cancelled", "canceled", "exhausted", "failed"}


def incident_payload(incident: dict) -> dict:
    """Shape one incident row for the dashboard, with masked attempts attached."""
    item = dict(incident)
    # SQLite stores booleans as 0/1 — make the contract explicit for the client.
    item["simulated"] = bool(item.get("simulated"))
    item["attempts_detail"] = [
        {**attempt, "simulated": bool(attempt.get("simulated"))}
        for attempt in get_emergency_attempts(item["id"])
    ]
    return item


@router.get("/emergency/incidents")
def get_emergency_incidents(
    limit: int = 20,
    user: dict = Depends(require_caregiver),
):
    """Recent emergency-call incidents for this caregiver's profile.

    A simulated incident is labelled `simulated: true` so the dashboard can
    say so. Nothing here ever claims a call was answered by a person.
    """
    limit = max(1, min(int(limit), 100))
    incidents = get_recent_emergency_incidents(user["profile_id"], limit=limit)
    return {"incidents": [incident_payload(i) for i in incidents]}


@router.post("/emergency/incidents/{incident_id}/cancel")
def cancel_emergency_incident_route(
    incident_id: str,
    user: dict = Depends(require_caregiver),
):
    """Caregiver says "I'm on it" — stop the phone campaign for this incident.

    The incident row is marked `cancelled` so the dashboard timeline stays
    truthful even when the orchestrator thread already finished. Nothing is
    spoken to Asha by this route.
    """
    from telephony.orchestrator import cancel_emergency_incident

    incident = get_emergency_incident(incident_id)
    if incident is None or incident["profile_id"] != user["profile_id"]:
        raise HTTPException(status_code=404, detail="Incident not found")

    stopped = cancel_emergency_incident(incident_id)
    if incident["status"] not in _TERMINAL_INCIDENT_STATUSES:
        # Same reason string the orchestrator uses (`caregiver_cancelled`), so a
        # consumer mapping reasons sees one vocabulary, not two spellings of the
        # same event.
        update_emergency_incident_status(
            incident_id, "cancelled", reason="caregiver_cancelled", ended_at=time.time()
        )
    return {"status": "ok", "incident_id": incident_id, "orchestrator_stopped": stopped}


@router.get("/emergency/status")
def emergency_call_status(user: dict = Depends(require_caregiver)):
    """
    Every gate between this profile and a real phone call, right now.

    Caregiver-only. Credential gates report only "is set" / "is unset" — no key,
    secret or full phone number is ever returned. The same facts are logged in
    one line at startup, so a difference between the log and this response means
    the environment changed while the server was running.
    """
    import os

    from database import get_profile, mask_phone_number
    from memory.profile import Profile as ProfileClass, get_callable_contacts
    from telephony.provider import emergency_status

    profile_data = get_profile(user["profile_id"])
    contacts = (
        get_callable_contacts(ProfileClass(profile_data)) if profile_data else []
    )
    first = contacts[0] if contacts else None

    status = emergency_status(first)
    base_url = os.environ.get("SAHAARA_EMERGENCY_PUBLIC_BASE_URL", "").strip().rstrip("/")
    status["contacts"] = [
        {
            "name": contact.get("name"),
            "relationship": contact.get("relationship"),
            "notify_order": contact.get("notify_order"),
            "phone_masked": mask_phone_number(contact.get("phone") or ""),
            "may_call": bool(contact.get("may_call")),
        }
        for contact in contacts
    ]
    status["webhooks"] = {
        "event_path": "/emergency/vonage/event",
        "ack_path": "/emergency/vonage/ack",
        "public_base_url_set": bool(base_url),
        "signature_required": True,
        "invalid_signature_status": 401,
    }
    return status


# ---- Provider webhooks (unauthenticated: Vonage cannot send a cookie) ----
#
# Both routes are written to be safe when abused:
#   * every request must carry a signature that verifies against OUR Vonage
#     application, or it is refused with 403;
#   * nothing here reads or writes a phone number;
#   * a webhook can only ever move an attempt towards a state the provider
#     itself reported — it can never invent an acknowledgement.

def _verify_provider_signature(request: Request) -> bool:
    """
    True when the request is a genuine provider callback.

    Always required and always checked against our Vonage application — even
    when the simulated provider is the one selected. A callback that cannot be
    verified must never move an attempt.
    """
    from telephony.provider import verify_webhook_signature

    return verify_webhook_signature(request.headers.get("Authorization", ""))


async def _webhook_payload(request: Request) -> dict:
    """Read a provider callback body without the multipart dependency.

    Vonage posts ``application/x-www-form-urlencoded``; some dashboard setups
    post JSON. Starlette's ``request.form()`` asserts that ``python-multipart``
    is installed even for urlencoded bodies, which turns every real callback
    into a 500 — so the body is decoded here instead. Field values stay
    strings, exactly as the form parser would have returned them.
    """
    raw = await request.body()
    if not raw:
        return {}

    content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if content_type == "application/json":
        try:
            decoded = json.loads(raw)
        except Exception:  # noqa: BLE001
            return {}
        return decoded if isinstance(decoded, dict) else {}

    try:
        text = raw.decode("utf-8", "replace")
    except Exception:  # noqa: BLE001
        return {}
    parsed = urllib.parse.parse_qs(text, keep_blank_values=True)
    return {key: values[-1] if values else "" for key, values in parsed.items()}


@router.post("/emergency/vonage/ack")
async def vonage_ack_webhook(request: Request):
    """DTMF acknowledgement from the call ("press 1")."""
    from telephony.orchestrator import acknowledge_attempt
    from telephony.provider import get_call_provider

    if not _verify_provider_signature(request):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    payload = await _webhook_payload(request)
    attempt_id = request.query_params.get("attempt_id") or payload.get("attempt_id")
    if not attempt_id:
        raise HTTPException(status_code=400, detail="Missing attempt_id")

    provider = get_call_provider()
    from telephony.provider import parse_dtmf_digits

    parser = getattr(provider, "parse_dtmf_webhook", None)
    # The real webhook can flatten the fields instead of sending the `dtmf`
    # JSON string, so reconstruct the object form when only digits arrive.
    if "dtmf" not in payload:
        digits = payload.get("digits") or payload.get("dtmf_digits")
        if digits is not None:
            payload["dtmf"] = {
                "digits": digits,
                "timed_out": str(payload.get("timed_out", "")).lower() == "true",
            }
    # A provider without its own parser (the simulated one) still uses the
    # shared shape-tolerant parser, so the demo can be acknowledged too.
    digit = parser(payload) if parser is not None else parse_dtmf_digits(payload)

    if digit != "1":
        return {"status": "ignored", "detail": "no acknowledgement digit"}

    if acknowledge_attempt(attempt_id, digit="1"):
        return {"status": "acknowledged"}
    return {"status": "ignored", "detail": "attempt not open"}


@router.post("/emergency/vonage/event")
@router.post("/vonage/events")
async def vonage_event_webhook(request: Request):
    """Call status events (ringing / answered / completed / ...).

    `/vonage/events` is the same handler: some Vonage setups are configured
    with that path, and a callback that verifies must not 404.
    """
    from telephony.orchestrator import record_provider_status
    from telephony.provider import get_call_provider

    if not _verify_provider_signature(request):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    payload = await _webhook_payload(request)
    attempt_id = request.query_params.get("attempt_id") or payload.get("attempt_id")
    if not attempt_id:
        raise HTTPException(status_code=400, detail="Missing attempt_id")

    provider = get_call_provider()
    parser = getattr(provider, "parse_status_webhook", None)
    status = parser(payload) if parser is not None else None

    if status is None:
        # Not a status we act on (e.g. an intermediate event). Acknowledge the
        # request so Vonage does not retry, but record nothing.
        return {"status": "ignored"}

    record_provider_status(attempt_id, status)
    return {"status": "received"}
