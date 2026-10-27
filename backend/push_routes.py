"""
push_routes.py

Caregiver-only Web Push subscription endpoints for M5 Step 3.

Storage and configuration only: **nothing is sent from here**, so no delivery
can be claimed. The actual send path lands in a later M5 step.

Endpoints:
    POST /push/subscribe     store (or refresh) this browser's subscription
    POST /push/unsubscribe   revoke one endpoint
    GET  /push/status        enabled flag, public VAPID key, active count
"""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

import database
import push
from auth_routes import require_caregiver

router = APIRouter(tags=["push"])


class PushKeys(BaseModel):
    p256dh: str = Field(min_length=1)
    auth: str = Field(min_length=1)


class PushSubscriptionIn(BaseModel):
    endpoint: str = Field(min_length=1)
    keys: PushKeys
    user_agent: str = ""


class PushEndpointIn(BaseModel):
    endpoint: str = Field(min_length=1)


def _profile_id(user: dict) -> str:
    return user.get("profile_id") or "asha_demo_001"


def _require_push_enabled() -> None:
    if not push.push_enabled():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Push is disabled on this server (SAHAARA_PUSH_ENABLED=false).",
        )


@router.post("/push/subscribe")
async def subscribe(
    body: PushSubscriptionIn,
    user: dict = Depends(require_caregiver),
):
    """Store this browser's push subscription for the caregiver's profile."""
    _require_push_enabled()

    if not body.endpoint.startswith("https://"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Push endpoint must be an https URL.",
        )

    profile_id = _profile_id(user)
    row = database.add_push_subscription(
        profile_id=profile_id,
        endpoint=body.endpoint,
        p256dh=body.keys.p256dh,
        auth=body.keys.auth,
        user_agent=body.user_agent,
    )

    return {
        "status": "ok",
        "subscription_id": row.get("id"),
        "profile_id": profile_id,
    }


@router.post("/push/unsubscribe")
async def unsubscribe(
    body: PushEndpointIn,
    user: dict = Depends(require_caregiver),
):
    """Revoke one stored subscription. Allowed even when push is disabled."""
    if not database.revoke_push_subscription(body.endpoint):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Unknown push endpoint.",
        )
    return {"status": "ok"}


@router.get("/push/status")
async def push_status(user: dict = Depends(require_caregiver)):
    """Push configuration and how many of this profile's subscriptions are live."""
    profile_id = _profile_id(user)
    active = database.list_push_subscriptions(profile_id)

    enabled = push.push_enabled()
    return {
        "push_enabled": enabled,
        "vapid_public_key": push.vapid_public_key() if enabled else None,
        "active_subscriptions": len(active),
        "profile_id": profile_id,
    }
