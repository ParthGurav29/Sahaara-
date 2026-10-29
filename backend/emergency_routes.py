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

import time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

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
