"""
push_notify.py

Web Push send path for M5 Step 5.

What this does:
- When an alert is stored, send ONE small fixed payload to every active
  caregiver subscription for that profile.

What this deliberately does NOT do:
- It carries no names, no speech, no tier wording and no symptom words. The
  payload is a constant (`PUSH_PAYLOAD`); the service worker ignores it and
  renders a fixed body, so conversation content cannot reach a lock screen.
- "Sent" means the push service **accepted** the message (2xx). It does not
  mean a phone displayed it. The dashboard stays the record of what happened.

Gates and limits:
- `SAHAARA_PUSH_ENABLED` must be ON **and** a VAPID private key must be
  configured. Both default OFF, so this module is inert out of the box.
- `SAHAARA_PUSH_COOLDOWN_SECONDS` (default 300) spaces pushes per profile so a
  burst of alerts cannot become a notification storm. **Red-tier alerts bypass
  the cooldown** on purpose: a danger alert must never be suppressed by an
  earlier Orange one.
- A 404/410 from the push service revokes the stored row, so later sends stop
  hammering an endpoint the browser has already dropped.
- `SAHAARA_PUSH_TTL_SECONDS` (default 3600) is how long the push service may
  hold a message for a device that is asleep or offline. pywebpush defaults to
  **0**, which tells the service to drop it immediately — wrong for an alert
  that should still arrive when a phone wakes up.

`notify_alert()` is fire-and-forget and never raises into the alert path;
`send_alert_push()` is the synchronous, testable core.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from typing import Any, Callable, Optional

from database import list_push_subscriptions, revoke_push_subscription
from events.event_log import EscalationTier
from push import push_enabled, vapid_private_key_pem, vapid_subject

logger = logging.getLogger(__name__)

#: The only fields a device ever receives. No alert text, no tier, no names.
PUSH_PAYLOAD_BASE = {"v": 1, "type": "caregiver_alert"}

#: Alert ids are opaque short strings; anything else is dropped (M5 Step 7).
ALERT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def push_payload(alert: Any) -> str:
    """
    The JSON body of one push.

    `alert_id` is included so a tap can open the right alert (Step 7). It is an
    opaque key — no speech, no tier, no symptom words. The service worker
    renders a fixed body regardless and validates the id before using it.
    """
    payload = dict(PUSH_PAYLOAD_BASE)
    alert_id = str(getattr(alert, "alert_id", "") or "")
    if ALERT_ID_RE.match(alert_id):
        payload["alert_id"] = alert_id
    return json.dumps(payload, separators=(",", ":"))

DEFAULT_COOLDOWN_SECONDS = 300
DEFAULT_TTL_SECONDS = 3600
SEND_TIMEOUT_SECONDS = 10
DEAD_ENDPOINT_STATUSES = {404, 410}

_lock = threading.Lock()
_last_sent_at: dict[str, float] = {}


def cooldown_seconds() -> int:
    """Seconds between pushes for one profile. `0` disables the cooldown."""
    raw = os.environ.get("SAHAARA_PUSH_COOLDOWN_SECONDS", "").strip()
    if not raw:
        return DEFAULT_COOLDOWN_SECONDS
    try:
        return max(0, int(float(raw)))
    except ValueError:
        return DEFAULT_COOLDOWN_SECONDS


def reset_state() -> None:
    """Forget the per-profile cooldown (used by tests)."""
    with _lock:
        _last_sent_at.clear()


def ttl_seconds() -> int:
    """How long the push service may store an undelivered message."""
    raw = os.environ.get("SAHAARA_PUSH_TTL_SECONDS", "").strip()
    if not raw:
        return DEFAULT_TTL_SECONDS
    try:
        return max(0, int(float(raw)))
    except ValueError:
        return DEFAULT_TTL_SECONDS


def _tier_value(alert: Any) -> str:
    tier = getattr(alert, "tier_to", None)
    if tier is None:
        return ""
    return getattr(tier, "value", str(tier))


def _is_red(alert: Any) -> bool:
    return _tier_value(alert) == EscalationTier.RED.value


def _vapid():
    """A `Vapid` instance built from the configured PEM, or None."""
    pem = vapid_private_key_pem()
    if not pem:
        return None
    from py_vapid import Vapid

    # pywebpush 2.5 does not accept the raw PEM here (it calls `.encode` on it);
    # it needs a Vapid instance. Confirmed on device 2026-10-27.
    return Vapid.from_pem(pem)


def _default_sender() -> Callable[..., Any]:
    from pywebpush import webpush

    return webpush


def _exception_status(exc: Exception) -> Optional[int]:
    response = getattr(exc, "response", None)
    code = getattr(response, "status_code", None)
    return code if isinstance(code, int) else None


def _log(
    event_log: Any,
    alert: Any,
    status: str,
    sent: int = 0,
    failed: int = 0,
    revoked: int = 0,
    reason: str = "",
) -> None:
    """Record the attempt in the session log. Never raises."""
    if event_log is None:
        return
    try:
        event_log.log_alert_sent(
            alert_id=getattr(alert, "alert_id", ""),
            tier=_tier_value(alert),
            status=status,
            sent=sent,
            failed=failed,
            revoked=revoked,
            reason=reason,
        )
    except Exception:
        logger.exception("Could not log ALERT_SENT")


def send_alert_push(
    alert: Any,
    event_log: Any = None,
    now: Optional[float] = None,
    sender: Optional[Callable[..., Any]] = None,
) -> dict:
    """
    Send this alert to every active subscription for its profile.

    Returns `{"status", "reason", "sent", "failed", "revoked"}` where status is
    one of `sent` (at least one push service accepted it), `skipped` (gate,
    cooldown, no subscriptions) or `error` (nothing accepted).
    """
    result = {"status": "skipped", "reason": "", "sent": 0, "failed": 0, "revoked": 0}

    # Gate OFF is the default install: the feature is simply not in play, so
    # nothing is logged and no thread is started.
    if not push_enabled():
        result["reason"] = "gate_off"
        return result

    profile_id = str(getattr(alert, "profile_id", "") or "")
    now = time.time() if now is None else now

    if profile_id and not _is_red(alert):
        cooldown = cooldown_seconds()
        if cooldown > 0:
            with _lock:
                last = _last_sent_at.get(profile_id)
            if last is not None and (now - last) < cooldown:
                result["reason"] = "cooldown"
                _log(event_log, alert, "skipped", reason="cooldown")
                return result

    try:
        subscriptions = [
            row
            for row in list_push_subscriptions(profile_id)
            if not row.get("revoked")
        ]
    except Exception:
        logger.exception("Could not load push subscriptions")
        result.update(status="error", reason="storage_error")
        _log(event_log, alert, "error", reason="storage_error")
        return result

    if not subscriptions:
        result["reason"] = "no_subscriptions"
        _log(event_log, alert, "skipped", reason="no_subscriptions")
        return result

    try:
        vapid = _vapid()
    except Exception:
        logger.exception("VAPID private key could not be loaded")
        vapid = None
    if vapid is None:
        result.update(status="error", reason="no_vapid_key")
        _log(event_log, alert, "error", reason="no_vapid_key")
        return result

    send = sender or _default_sender()
    claims = {"sub": vapid_subject()}

    for subscription in subscriptions:
        endpoint = subscription["endpoint"]
        try:
            response = send(
                subscription_info={
                    "endpoint": endpoint,
                    "keys": {
                        "p256dh": subscription["p256dh"],
                        "auth": subscription["auth"],
                    },
                },
                data=push_payload(alert),
                vapid_private_key=vapid,
                vapid_claims=claims,
                timeout=SEND_TIMEOUT_SECONDS,
                ttl=ttl_seconds(),
            )
            code = getattr(response, "status_code", None)
            if isinstance(code, int) and 200 <= code < 300:
                result["sent"] += 1
            else:
                result["failed"] += 1
                logger.warning("Push service answered %s", code)
        except Exception as exc:  # WebPushException and anything else
            code = _exception_status(exc)
            if code in DEAD_ENDPOINT_STATUSES:
                try:
                    if revoke_push_subscription(endpoint):
                        result["revoked"] += 1
                except Exception:
                    logger.exception("Could not revoke a dead subscription")
            else:
                result["failed"] += 1
            logger.warning("Push send failed (status=%s): %s", code, exc)

    if result["sent"]:
        result["status"] = "sent"
        if profile_id:
            with _lock:
                _last_sent_at[profile_id] = now
    elif result["revoked"] and not result["failed"]:
        # The browser had already dropped these; nothing left to notify.
        result["status"] = "skipped"
        result["reason"] = "dead_subscriptions"
    else:
        result["status"] = "error"
        result["reason"] = result["reason"] or "send_failed"

    _log(
        event_log,
        alert,
        result["status"],
        sent=result["sent"],
        failed=result["failed"],
        revoked=result["revoked"],
        reason=result["reason"],
    )
    return result


def _notify_worker(alert: Any, event_log: Any) -> None:
    try:
        send_alert_push(alert, event_log)
    except Exception:
        logger.exception("Push send crashed")


def notify_alert(alert: Any, event_log: Any = None) -> bool:
    """
    Fire-and-forget dispatch used by the alert path.

    Returns True when a send was started, False when the gate is OFF or the
    thread could not be created. Never blocks, never raises.
    """
    if not push_enabled():
        return False
    try:
        threading.Thread(
            target=_notify_worker,
            args=(alert, event_log),
            name="sahaara-push-alert",
            daemon=True,
        ).start()
        return True
    except Exception:
        logger.exception("Could not start the push thread")
        return False
