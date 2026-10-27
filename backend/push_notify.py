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
- Only an **upward tier move** rings a phone (`is_pushable`): Green, consent
  outcomes, fallback notices and de-escalations stay on the dashboard.
- Cooldown is per profile, **persisted in SQLite** (`push_cooldown_state`), so a
  restart cannot silently reset it. Defaults are per tier — Yellow 1800 s,
  Orange 600 s, Red 120 s — and an escalation always sends even inside the
  window of the previous tier. One explicit `SAHAARA_PUSH_COOLDOWN_SECONDS`
  overrides every tier (`0` disables the cooldown).
- A 404/410 from the push service revokes the stored row, so later sends stop
  hammering an endpoint the browser has already dropped.
- `SAHAARA_PUSH_TTL_SECONDS` (default 3600) is how long the push service may
  hold a message for a device that is asleep or offline. pywebpush defaults to
  **0**, which tells the service to drop it immediately — wrong for an alert
  that should still arrive when a phone wakes up.

Nothing an endpoint URL or a push-service exception message contains is ever
logged: results are recorded as `{"outcome", "status"}` pairs only.

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

from database import (
    clear_push_cooldown_state,
    get_push_cooldown_state,
    list_push_subscriptions,
    revoke_push_subscription,
    set_push_cooldown_state,
)
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

#: Only upward tier moves are worth a phone alert. Green (de-escalation),
#: consent outcomes and fallback notices are dashboard-only.
TIER_RANK = {"green": 0, "yellow": 1, "orange": 2, "red": 3}

#: Step 5 hardening: per-tier windows, so a long Yellow stretch does not mute
#: the Orange that follows, and a Red escalation is never held back.
TIER_COOLDOWN_DEFAULTS = {"yellow": 1800, "orange": 600, "red": 120}
TIER_COOLDOWN_ENV = {
    "yellow": "SAHAARA_PUSH_COOLDOWN_YELLOW_SECONDS",
    "orange": "SAHAARA_PUSH_COOLDOWN_ORANGE_SECONDS",
    "red": "SAHAARA_PUSH_COOLDOWN_RED_SECONDS",
}


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return max(0, int(float(raw)))
    except ValueError:
        return default


def cooldown_seconds() -> int:
    """The global override. `0` disables the cooldown for every tier."""
    return _env_int("SAHAARA_PUSH_COOLDOWN_SECONDS", DEFAULT_COOLDOWN_SECONDS)


def tier_cooldown_seconds(tier: str) -> int:
    """
    Seconds before another push of `tier` for the same profile.

    One explicit global override (`SAHAARA_PUSH_COOLDOWN_SECONDS`) wins for all
    tiers, which is what the pre-hardening tests use. Otherwise each tier has
    its own window and its own env var.
    """
    tier = (tier or "").lower()
    env = TIER_COOLDOWN_ENV.get(tier)
    if env and os.environ.get(env, "").strip():
        return _env_int(env, TIER_COOLDOWN_DEFAULTS.get(tier, DEFAULT_COOLDOWN_SECONDS))
    if os.environ.get("SAHAARA_PUSH_COOLDOWN_SECONDS", "").strip():
        return _env_int("SAHAARA_PUSH_COOLDOWN_SECONDS", DEFAULT_COOLDOWN_SECONDS)
    return TIER_COOLDOWN_DEFAULTS.get(tier, DEFAULT_COOLDOWN_SECONDS)


def reset_state() -> None:
    """Forget cooldown state (tests). Clears the persisted rows as well."""
    try:
        clear_push_cooldown_state()
    except Exception:
        logger.warning("Could not clear stored push cooldown state")


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


def _tier_from_value(alert: Any) -> str:
    tier = getattr(alert, "tier_from", None)
    if tier is None:
        return ""
    return getattr(tier, "value", str(tier))


def is_pushable(alert: Any) -> bool:
    """
    Phone alerts are for upward tier moves only (Step 5 hardening).

    Yellow/Orange/Red that are above the previous tier qualify — including a
    physical-symptom alert, which is built as Green -> Orange/Red. Consent
    outcomes (same tier), fallback notices (Green -> Green) and de-escalations
    (Red -> Orange) stay on the dashboard and never ring a phone.
    """
    to_tier = _tier_value(alert).lower()
    from_tier = _tier_from_value(alert).lower()
    if TIER_RANK.get(to_tier, 0) <= 0:
        return False
    return TIER_RANK[to_tier] > TIER_RANK.get(from_tier, 0)


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
    results: Optional[list] = None,
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
            results=results or [],
        )
    except Exception:
        logger.exception("Could not log ALERT_SENT")


def _per_subscription(outcome: str, code: Optional[int]) -> dict:
    """
    One line of send evidence, safe to store and show.

    Deliberately carries no endpoint and no keys: a push endpoint is a bearer
    URL that must never reach a log, an event row or the dashboard.
    """
    return {"outcome": outcome, "status": code if isinstance(code, int) else None}


def send_alert_push(
    alert: Any,
    event_log: Any = None,
    now: Optional[float] = None,
    sender: Optional[Callable[..., Any]] = None,
) -> dict:
    """
    Send this alert to every active subscription for its profile.

    Returns `{"status", "reason", "sent", "failed", "revoked", "results"}` where
    status is one of `sent` (at least one push service accepted it), `skipped`
    (gate, not pushable, cooldown, no subscriptions, dead subscriptions) or
    `error` (nothing accepted).

    Step 5 hardening: only an upward tier move is pushable, the cooldown is
    persisted per profile (a restart does not reset it), and an escalation
    always sends even when the window for the previous tier is still open.
    """
    result = {
        "status": "skipped",
        "reason": "",
        "sent": 0,
        "failed": 0,
        "revoked": 0,
        "results": [],
    }

    # Gate OFF is the default install: the feature is simply not in play, so
    # nothing is logged and no thread is started.
    if not push_enabled():
        result["reason"] = "gate_off"
        return result

    if not is_pushable(alert):
        # Green, consent outcomes, fallback notices and de-escalations are
        # dashboard-only. Nothing rings a phone and nothing is logged: this is
        # the normal case for most alerts, not a failure.
        result["reason"] = "not_pushable"
        return result

    profile_id = str(getattr(alert, "profile_id", "") or "")
    now = time.time() if now is None else now
    tier = _tier_value(alert).lower()

    if profile_id:
        try:
            state = get_push_cooldown_state(profile_id)
        except Exception:
            logger.exception("Could not read the stored push cooldown")
            state = None
        if state:
            last_sent_at = float(state.get("last_sent_at") or 0.0)
            last_tier = str(state.get("last_tier") or "").lower()
            window = tier_cooldown_seconds(tier)
            escalated = TIER_RANK.get(tier, 0) > TIER_RANK.get(last_tier, 0)
            if window > 0 and (now - last_sent_at) < window and not escalated:
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
                result["results"].append(_per_subscription("accepted", code))
            else:
                result["failed"] += 1
                result["results"].append(_per_subscription("failed", code))
                logger.warning("Push service answered %s", code)
        except Exception as exc:  # WebPushException and anything else
            code = _exception_status(exc)
            if code in DEAD_ENDPOINT_STATUSES:
                try:
                    if revoke_push_subscription(endpoint):
                        result["revoked"] += 1
                except Exception:
                    logger.exception("Could not revoke a dead subscription")
                result["results"].append(_per_subscription("expired", code))
            else:
                result["failed"] += 1
                result["results"].append(_per_subscription("failed", code))
            # `exc` itself can contain the endpoint URL (requests puts it in the
            # message), so only the class name is logged.
            logger.warning(
                "Push send failed (status=%s, %s)", code, type(exc).__name__
            )

    if result["sent"]:
        result["status"] = "sent"
        if profile_id:
            try:
                set_push_cooldown_state(profile_id, now, tier)
            except Exception:
                logger.exception("Could not store the push cooldown")
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
        results=result["results"],
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

    Returns True when a send was started, False when the gate is OFF, the alert
    is not worth a phone alert (non-escalation) or the thread could not be
    created. Never blocks, never raises.
    """
    if not push_enabled():
        return False
    if not is_pushable(alert):
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
