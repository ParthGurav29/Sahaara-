"""
test_push_send.py

M5 Step 5 — Web Push send path.

Everything here runs against an isolated temp DB and a fake push service: no
network calls, no real notifications. "sent" in these tests means the (fake)
push service accepted the message — never that a phone displayed it.

Run directly: python tests/test_push_send.py
"""

import sys
sys.path.insert(0, "/Users/parthmangeshgurav/Desktop/sahaara/backend")

import json
import os
import tempfile
import threading
import time
from pathlib import Path

# Isolated DB: these tests write subscription rows, never the live database.
_TEST_DB = Path(tempfile.gettempdir()) / "sahaara_test_push_send.db"
for _suffix in ("", "-wal", "-shm"):
    Path(str(_TEST_DB) + _suffix).unlink(missing_ok=True)
os.environ["SAHAARA_DB_PATH"] = str(_TEST_DB)

from database import init_db, add_push_subscription, list_push_subscriptions
init_db()

import push_notify
from dashboard.alerts import Alert, AlertBuilder, AlertStore
from events.event_log import EventLog, EventType, EscalationTier

# The negative tests below deliberately make the sender fail; their tracebacks
# are expected, so keep the log quiet and let the asserts do the talking.
import logging
logging.getLogger("push_notify").setLevel(logging.CRITICAL)

PROFILE = "asha_demo_001"
OTHER_PROFILE = "other_profile_002"
ENDPOINT = "https://fcm.googleapis.com/fcm/send/step5_abc"
ENDPOINT_2 = "https://fcm.googleapis.com/fcm/send/step5_def"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

# Throwaway VAPID key generated at runtime, so tests never read backend/.env.
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

_TEST_KEY = ec.generate_private_key(ec.SECP256R1())
_TEST_PEM = _TEST_KEY.private_bytes(
    serialization.Encoding.PEM,
    serialization.PrivateFormat.PKCS8,
    serialization.NoEncryption(),
).decode()


def _enable_push(key: str = _TEST_PEM) -> None:
    """Gate ON, usable VAPID key, 5-minute cooldown, fresh cooldown state."""
    os.environ["SAHAARA_PUSH_ENABLED"] = "true"
    os.environ["VAPID_PRIVATE_KEY"] = key.replace("\n", "\\n")
    os.environ["VAPID_SUBJECT"] = "mailto:test@example.com"
    os.environ["SAHAARA_PUSH_COOLDOWN_SECONDS"] = "300"
    push_notify.reset_state()


def _disable_push() -> None:
    os.environ["SAHAARA_PUSH_ENABLED"] = "false"
    push_notify.reset_state()


def _clear_subscriptions() -> None:
    import database
    conn = database.get_connection()
    try:
        conn.execute("DELETE FROM push_subscriptions")
        conn.commit()
    finally:
        conn.close()


def _subscribe(endpoint: str = ENDPOINT, profile_id: str = PROFILE) -> dict:
    return add_push_subscription(
        profile_id=profile_id,
        endpoint=endpoint,
        p256dh="BNcRdreALRFXTkOOUHK1EtK2wtaz5Ry4YfYCA_0QTpQtUbVlUls0VJXg7A8u-Ts1XbjhazAkj7I99e8QcYP7DkM",
        auth="tBHItJI5svbpez7KI4CCXg",
        user_agent="test",
    )


def _alert(
    tier: EscalationTier = EscalationTier.ORANGE,
    alert_id: str = "alert-1",
    tier_from: EscalationTier = EscalationTier.YELLOW,
) -> Alert:
    return Alert(
        timestamp=time.time(),
        tier_from=tier_from,
        tier_to=tier,
        reason="sustained_distress",
        profile_id=PROFILE,
        message="Asha seemed anxious. She said she cannot breathe and is bleeding.",
        detail={"private": "she fell and cannot get up"},
        alert_id=alert_id,
    )


class FakeResponse:
    def __init__(self, status_code: int = 201):
        self.status_code = status_code


class FakeWebPushError(Exception):
    """Duck-typed `pywebpush.WebPushException` with a response status."""

    def __init__(self, status_code: int):
        super().__init__(f"push service said {status_code}")
        self.response = FakeResponse(status_code)


class RecordingSender:
    def __init__(self, status_code: int = 201, raise_status: int = 0):
        self.calls: list[dict] = []
        self.status_code = status_code
        self.raise_status = raise_status

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        if self.raise_status:
            raise FakeWebPushError(self.raise_status)
        return FakeResponse(self.status_code)


class PerEndpointSender:
    """Answers per endpoint, so one send can succeed and another 410."""

    def __init__(self, mapping: dict):
        self.mapping = mapping
        self.calls: list[str] = []

    def __call__(self, **kwargs):
        endpoint = kwargs["subscription_info"]["endpoint"]
        self.calls.append(endpoint)
        code = self.mapping[endpoint]
        if code >= 400:
            raise FakeWebPushError(code)
        return FakeResponse(code)


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------

def test_gate_off_sends_nothing_and_logs_nothing():
    _disable_push()
    _clear_subscriptions()
    _subscribe()
    sender = RecordingSender()
    event_log = EventLog(PROFILE)

    alert = _alert()
    result = push_notify.send_alert_push(alert, event_log, now=1000.0, sender=sender)

    assert result["status"] == "skipped", result
    assert result["reason"] == "gate_off", result
    assert sender.calls == [], "gate OFF must not call the push service"
    assert event_log.get_events(EventType.ALERT_SENT) == [], "gate OFF must stay silent"
    assert push_notify.notify_alert(_alert(), event_log) is False


def test_no_subscriptions_is_skipped_and_logged():
    _enable_push()
    _clear_subscriptions()
    event_log = EventLog(PROFILE)
    sender = RecordingSender()

    alert = _alert()
    result = push_notify.send_alert_push(alert, event_log, now=1000.0, sender=sender)

    assert result["status"] == "skipped", result
    assert result["reason"] == "no_subscriptions", result
    assert sender.calls == []
    events = event_log.get_events(EventType.ALERT_SENT)
    assert len(events) == 1, events
    assert events[0].detail["reason"] == "no_subscriptions"
    assert events[0].detail["status"] == "skipped"


def test_single_subscription_is_sent_with_fixed_payload():
    _enable_push()
    _clear_subscriptions()
    _subscribe()
    event_log = EventLog(PROFILE)
    sender = RecordingSender(status_code=201)

    alert = _alert()
    result = push_notify.send_alert_push(alert, event_log, now=1000.0, sender=sender)

    assert result == {
        "status": "sent",
        "reason": "",
        "sent": 1,
        "failed": 0,
        "revoked": 0,
        "results": [{"outcome": "accepted", "status": 201}],
    }, result
    assert len(sender.calls) == 1

    call = sender.calls[0]
    assert call["subscription_info"]["endpoint"] == ENDPOINT
    assert call["subscription_info"]["keys"]["p256dh"]
    assert call["subscription_info"]["keys"]["auth"]
    assert call["data"] == push_notify.push_payload(alert)
    assert call["vapid_claims"] == {"sub": "mailto:test@example.com"}
    assert call["timeout"] == push_notify.SEND_TIMEOUT_SECONDS
    assert call["ttl"] == push_notify.DEFAULT_TTL_SECONDS, (
        "pywebpush defaults to TTL 0, which drops the alert instead of holding it for a sleeping phone"
    )

    events = event_log.get_events(EventType.ALERT_SENT)
    assert len(events) == 1, events
    detail = events[0].detail
    assert detail["status"] == "sent"
    assert detail["sent"] == 1
    assert detail["tier"] == "orange"
    assert detail["alert_id"] == "alert-1"


def test_payload_carries_no_conversation_content():
    alert = _alert()
    payload = push_notify.push_payload(alert)
    assert json.loads(payload) == {
        "v": 1,
        "type": "caregiver_alert",
        "alert_id": "alert-1",
    }, payload

    # The alert's own words must never be in the payload that leaves the server.
    lowered = payload.lower()
    for banned in ("bleed", "breathe", "fell", "asha", "orange", "yellow", "red"):
        assert banned not in lowered, f"{banned!r} leaked into the push payload"
    assert alert.message.lower() not in lowered
    assert json.dumps(alert.detail).lower() not in lowered


def test_payload_drops_an_unsafe_alert_id():
    class Hostile:
        alert_id = 'alert"><script>'

    payload = json.loads(push_notify.push_payload(Hostile()))
    assert payload == {"v": 1, "type": "caregiver_alert"}, payload


def test_vapid_key_is_a_vapid_instance_not_raw_pem():
    _enable_push()
    _clear_subscriptions()
    _subscribe()
    sender = RecordingSender()

    push_notify.send_alert_push(_alert(), None, now=1000.0, sender=sender)

    from py_vapid import Vapid
    assert isinstance(sender.calls[0]["vapid_private_key"], Vapid), (
        "pywebpush 2.5 needs a Vapid instance; raw PEM raises AttributeError"
    )


def test_dead_endpoint_is_revoked():
    _enable_push()
    _clear_subscriptions()
    _subscribe()

    sender = RecordingSender(raise_status=410)
    result = push_notify.send_alert_push(_alert(), None, now=1000.0, sender=sender)

    assert result["revoked"] == 1, result
    assert result["sent"] == 0, result
    assert result["status"] == "skipped", result  # nothing left to notify
    assert result["reason"] == "dead_subscriptions", result
    assert list_push_subscriptions(PROFILE) == [], "revoked rows must not be sent again"

    # A second alert must not call the dead endpoint again.
    sender2 = RecordingSender()
    push_notify.reset_state()
    result2 = push_notify.send_alert_push(_alert(), None, now=2000.0, sender=sender2)
    assert result2["reason"] == "no_subscriptions", result2
    assert sender2.calls == []


def test_server_error_keeps_subscription_and_reports_error():
    _enable_push()
    _clear_subscriptions()
    _subscribe()
    event_log = EventLog(PROFILE)

    sender = RecordingSender(raise_status=500)
    alert = _alert()
    result = push_notify.send_alert_push(alert, event_log, now=1000.0, sender=sender)

    assert result["status"] == "error", result
    assert result["failed"] == 1, result
    assert result["revoked"] == 0, result
    assert len(list_push_subscriptions(PROFILE)) == 1, "a 500 must not revoke the row"
    assert event_log.get_events(EventType.ALERT_SENT)[0].detail["failed"] == 1


def test_cooldown_skips_second_alert_then_expires():
    _enable_push()
    _clear_subscriptions()
    _subscribe()

    first = RecordingSender()
    r1 = push_notify.send_alert_push(_alert(alert_id="a1"), None, now=1000.0, sender=first)
    assert r1["status"] == "sent", r1

    second = RecordingSender()
    r2 = push_notify.send_alert_push(_alert(alert_id="a2"), None, now=1100.0, sender=second)
    assert r2["status"] == "skipped", r2
    assert r2["reason"] == "cooldown", r2
    assert second.calls == [], "cooldown must not reach the push service"

    third = RecordingSender()
    r3 = push_notify.send_alert_push(_alert(alert_id="a3"), None, now=1000.0 + 301, sender=third)
    assert r3["status"] == "sent", r3
    assert len(third.calls) == 1


def test_red_alert_after_orange_always_sends():
    _enable_push()
    _clear_subscriptions()
    _subscribe()

    first = RecordingSender()
    assert push_notify.send_alert_push(
        _alert(tier=EscalationTier.ORANGE, alert_id="o1"), None, now=1000.0, sender=first
    )["status"] == "sent"

    red_sender = RecordingSender()
    red = push_notify.send_alert_push(
        _alert(tier=EscalationTier.RED, alert_id="r1"), None, now=1001.0, sender=red_sender
    )
    assert red["status"] == "sent", red
    assert len(red_sender.calls) == 1, "a Red escalation must never be suppressed by the cooldown"


def test_cooldown_zero_disables_the_limit():
    _enable_push()
    os.environ["SAHAARA_PUSH_COOLDOWN_SECONDS"] = "0"
    _clear_subscriptions()
    _subscribe()
    push_notify.reset_state()

    for i in range(3):
        sender = RecordingSender()
        result = push_notify.send_alert_push(_alert(alert_id=f"z{i}"), None, now=1000.0, sender=sender)
        assert result["status"] == "sent", result


def test_non_escalations_never_ring_a_phone():
    """Green, consent outcomes, fallback notices and de-escalations stay on the dashboard."""
    _enable_push()
    _clear_subscriptions()
    _subscribe()
    event_log = EventLog(PROFILE)

    cases = [
        ("fallback notice", EscalationTier.GREEN, EscalationTier.GREEN),
        ("de-escalation", EscalationTier.RED, EscalationTier.ORANGE),
        ("de-escalation to green", EscalationTier.ORANGE, EscalationTier.GREEN),
        ("consent outcome", EscalationTier.ORANGE, EscalationTier.ORANGE),
        ("consent outcome (red)", EscalationTier.RED, EscalationTier.RED),
    ]
    for index, (label, tier_from, tier_to) in enumerate(cases):
        sender = RecordingSender()
        alert = _alert(tier=tier_to, alert_id=f"np{index}", tier_from=tier_from)
        assert push_notify.is_pushable(alert) is False, label
        result = push_notify.send_alert_push(alert, event_log, now=1000.0, sender=sender)
        assert result["status"] == "skipped", (label, result)
        assert result["reason"] == "not_pushable", (label, result)
        assert sender.calls == [], f"{label} must not reach the push service"

    assert event_log.get_events(EventType.ALERT_SENT) == [], (
        "non-escalations are the normal case; they must not spam ALERT_SENT rows"
    )

    fallback = _alert(tier=EscalationTier.GREEN, alert_id="np-fb", tier_from=EscalationTier.GREEN)
    assert push_notify.notify_alert(fallback, event_log) is False, (
        "the alert path must not even start a thread for a non-escalation"
    )


def test_green_to_yellow_is_pushable():
    _enable_push()
    _clear_subscriptions()
    _subscribe()

    alert = _alert(tier=EscalationTier.YELLOW, alert_id="gy1", tier_from=EscalationTier.GREEN)
    assert push_notify.is_pushable(alert) is True
    assert push_notify.send_alert_push(
        alert, None, now=1000.0, sender=RecordingSender()
    )["status"] == "sent"


def test_per_tier_windows_and_env_overrides():
    os.environ.pop("SAHAARA_PUSH_COOLDOWN_SECONDS", None)
    os.environ.pop("SAHAARA_PUSH_COOLDOWN_YELLOW_SECONDS", None)
    os.environ.pop("SAHAARA_PUSH_COOLDOWN_ORANGE_SECONDS", None)
    os.environ.pop("SAHAARA_PUSH_COOLDOWN_RED_SECONDS", None)
    try:
        assert push_notify.tier_cooldown_seconds("yellow") == 1800
        assert push_notify.tier_cooldown_seconds("orange") == 600
        assert push_notify.tier_cooldown_seconds("red") == 120

        os.environ["SAHAARA_PUSH_COOLDOWN_RED_SECONDS"] = "45"
        assert push_notify.tier_cooldown_seconds("red") == 45, "per-tier env must win"
        os.environ.pop("SAHAARA_PUSH_COOLDOWN_RED_SECONDS", None)

        os.environ["SAHAARA_PUSH_COOLDOWN_SECONDS"] = "0"
        assert push_notify.tier_cooldown_seconds("red") == 0, (
            "one explicit global override must win for every tier"
        )
        os.environ.pop("SAHAARA_PUSH_COOLDOWN_SECONDS", None)
        assert push_notify.tier_cooldown_seconds("") == 300, "unknown tiers fall back"
    finally:
        os.environ.pop("SAHAARA_PUSH_COOLDOWN_RED_SECONDS", None)
        os.environ.pop("SAHAARA_PUSH_COOLDOWN_SECONDS", None)


def test_yellow_window_is_half_an_hour():
    _enable_push()
    os.environ.pop("SAHAARA_PUSH_COOLDOWN_SECONDS", None)
    _clear_subscriptions()
    _subscribe()

    yellow = "Yellow window is 1800 s"
    assert push_notify.send_alert_push(
        _alert(tier=EscalationTier.YELLOW, alert_id="y1", tier_from=EscalationTier.GREEN),
        None,
        now=1000.0,
        sender=RecordingSender(),
    )["status"] == "sent", yellow

    inside = RecordingSender()
    r2 = push_notify.send_alert_push(
        _alert(tier=EscalationTier.YELLOW, alert_id="y2", tier_from=EscalationTier.GREEN),
        None,
        now=1000.0 + 1799,
        sender=inside,
    )
    assert r2["reason"] == "cooldown", r2
    assert inside.calls == []

    after = RecordingSender()
    r3 = push_notify.send_alert_push(
        _alert(tier=EscalationTier.YELLOW, alert_id="y3", tier_from=EscalationTier.GREEN),
        None,
        now=1000.0 + 1801,
        sender=after,
    )
    assert r3["status"] == "sent", r3


def test_escalation_sends_inside_the_previous_window():
    _enable_push()
    os.environ.pop("SAHAARA_PUSH_COOLDOWN_SECONDS", None)
    _clear_subscriptions()
    _subscribe()

    first = RecordingSender()
    assert push_notify.send_alert_push(
        _alert(tier=EscalationTier.YELLOW, alert_id="e1", tier_from=EscalationTier.GREEN),
        None,
        now=1000.0,
        sender=first,
    )["status"] == "sent"

    # One second later, still far inside the 1800 s Yellow window.
    orange_sender = RecordingSender()
    orange = push_notify.send_alert_push(
        _alert(tier=EscalationTier.ORANGE, alert_id="e2", tier_from=EscalationTier.YELLOW),
        None,
        now=1001.0,
        sender=orange_sender,
    )
    assert orange["status"] == "sent", "an escalation must never be muted by the lower tier"
    assert len(orange_sender.calls) == 1


def test_red_window_is_two_minutes():
    _enable_push()
    os.environ.pop("SAHAARA_PUSH_COOLDOWN_SECONDS", None)
    _clear_subscriptions()
    _subscribe()

    assert push_notify.send_alert_push(
        _alert(tier=EscalationTier.RED, alert_id="r1", tier_from=EscalationTier.ORANGE),
        None,
        now=1000.0,
        sender=RecordingSender(),
    )["status"] == "sent"

    # A second Red (same tier, not an escalation) waits out the 120 s window.
    inside = RecordingSender()
    r2 = push_notify.send_alert_push(
        _alert(tier=EscalationTier.RED, alert_id="r2", tier_from=EscalationTier.ORANGE),
        None,
        now=1000.0 + 119,
        sender=inside,
    )
    assert r2["reason"] == "cooldown", r2
    assert inside.calls == []

    after = RecordingSender()
    r3 = push_notify.send_alert_push(
        _alert(tier=EscalationTier.RED, alert_id="r3", tier_from=EscalationTier.ORANGE),
        None,
        now=1000.0 + 121,
        sender=after,
    )
    assert r3["status"] == "sent", r3


def test_cooldown_survives_a_restart():
    """The cooldown is in SQLite, so dropping the in-memory module cannot reset it."""
    import importlib

    _enable_push()
    _clear_subscriptions()
    _subscribe()

    assert push_notify.send_alert_push(
        _alert(alert_id="p1"), None, now=1000.0, sender=RecordingSender()
    )["status"] == "sent"

    import database
    state = database.get_push_cooldown_state(PROFILE)
    assert state is not None, "an accepted push must be recorded"
    assert state["last_sent_at"] == 1000.0
    assert state["last_tier"] == "orange"

    reloaded = importlib.reload(push_notify)
    try:
        sender = RecordingSender()
        result = reloaded.send_alert_push(_alert(alert_id="p2"), None, now=1100.0, sender=sender)
        assert result["reason"] == "cooldown", result
        assert sender.calls == [], "a restart must not re-arm the cooldown"
    finally:
        importlib.reload(reloaded)


def test_results_are_per_subscription_and_never_contain_endpoints():
    _enable_push()
    _clear_subscriptions()
    _subscribe(endpoint=ENDPOINT)
    _subscribe(endpoint=ENDPOINT_2)
    event_log = EventLog(PROFILE)

    sender = PerEndpointSender({ENDPOINT: 201, ENDPOINT_2: 410})
    result = push_notify.send_alert_push(_alert(), event_log, now=1000.0, sender=sender)

    assert result["status"] == "sent", result
    assert result["sent"] == 1
    assert result["revoked"] == 1
    assert result["results"] == [
        {"outcome": "accepted", "status": 201},
        {"outcome": "expired", "status": 410},
    ], result

    detail = event_log.get_events(EventType.ALERT_SENT)[0].detail
    assert detail["results"] == result["results"]
    blob = json.dumps(result) + json.dumps(detail)
    assert ENDPOINT not in blob and ENDPOINT_2 not in blob, (
        "a push endpoint is a bearer URL; it must never reach a result or an event"
    )


def test_push_outcome_does_not_touch_alert_or_tier_state():
    _enable_push()
    _clear_subscriptions()
    _subscribe()

    alert = _alert(tier=EscalationTier.ORANGE)
    sender = RecordingSender(raise_status=500)
    push_notify.send_alert_push(alert, None, now=1000.0, sender=sender)

    assert alert.tier_to == EscalationTier.ORANGE
    assert alert.tier_from == EscalationTier.YELLOW
    assert alert.message and "bleeding" in alert.message, "the alert text must be untouched"
    assert push_notify.send_alert_push(
        _alert(alert_id="after"), None, now=1001.0, sender=RecordingSender()
    )["status"] == "sent", (
        "a failed send must not be treated as an accepted one for cooldown purposes"
    )


def test_accepted_send_records_last_success_per_subscription():
    _enable_push()
    _clear_subscriptions()
    _subscribe(endpoint=ENDPOINT)
    _subscribe(endpoint=ENDPOINT_2)

    from database import get_push_subscription

    sender = PerEndpointSender({ENDPOINT: 201, ENDPOINT_2: 500})
    push_notify.send_alert_push(_alert(), None, now=1234.5, sender=sender)

    accepted = get_push_subscription(ENDPOINT)
    rejected = get_push_subscription(ENDPOINT_2)
    assert accepted["last_success_at"] == 1234.5, accepted
    assert rejected["last_success_at"] is None, (
        "a failed send must not be recorded as an accepted one"
    )


def test_malformed_vapid_key_reports_error_and_never_raises():
    _enable_push(key="not-a-real-key")
    _clear_subscriptions()
    _subscribe()
    event_log = EventLog(PROFILE)
    sender = RecordingSender()

    alert = _alert()
    result = push_notify.send_alert_push(alert, event_log, now=1000.0, sender=sender)

    assert result["status"] == "error", result
    assert result["reason"] == "no_vapid_key", result
    assert sender.calls == []
    assert event_log.get_events(EventType.ALERT_SENT)[0].detail["reason"] == "no_vapid_key"


def test_notify_alert_dispatches_in_a_thread():
    _enable_push()
    _clear_subscriptions()
    _subscribe()

    called = threading.Event()
    original = push_notify.send_alert_push

    def fake(alert, event_log=None, now=None, sender=None):
        called.set()
        return {"status": "sent", "reason": "", "sent": 1, "failed": 0, "revoked": 0}

    push_notify.send_alert_push = fake
    try:
        started = push_notify.notify_alert(_alert(), EventLog(PROFILE))
        assert started is True
        assert called.wait(2.0), "notify_alert never reached the sender"
    finally:
        push_notify.send_alert_push = original


def test_notify_alert_swallows_a_crashing_sender():
    _enable_push()
    _clear_subscriptions()
    _subscribe()

    finished = threading.Event()
    original = push_notify.send_alert_push

    def boom(alert, event_log=None, now=None, sender=None):
        try:
            raise RuntimeError("push exploded")
        finally:
            finished.set()

    push_notify.send_alert_push = boom
    try:
        assert push_notify.notify_alert(_alert(), None) is True
        assert finished.wait(2.0)
    finally:
        push_notify.send_alert_push = original
    # The test passing at all proves nothing escaped into the calling thread.


def test_ttl_defaults_to_one_hour_and_can_be_overridden():
    _enable_push()
    _clear_subscriptions()
    _subscribe()

    os.environ.pop("SAHAARA_PUSH_TTL_SECONDS", None)
    assert push_notify.ttl_seconds() == 3600

    sender = RecordingSender()
    push_notify.send_alert_push(_alert(), None, now=1000.0, sender=sender)
    assert sender.calls[0]["ttl"] == 3600

    os.environ["SAHAARA_PUSH_TTL_SECONDS"] = "900"
    assert push_notify.ttl_seconds() == 900
    sender2 = RecordingSender()
    push_notify.reset_state()
    push_notify.send_alert_push(_alert(), None, now=2000.0, sender=sender2)
    assert sender2.calls[0]["ttl"] == 900

    os.environ["SAHAARA_PUSH_TTL_SECONDS"] = "junk"
    assert push_notify.ttl_seconds() == 3600, "a bad value must fall back, not crash"
    os.environ.pop("SAHAARA_PUSH_TTL_SECONDS", None)


def test_alert_builder_store_dispatches_a_push():
    _enable_push()
    _clear_subscriptions()
    _subscribe()

    event_log = EventLog(PROFILE)
    store = AlertStore()
    builder = AlertBuilder(event_log, profile=None, alert_store=store)  # profile unused by _store

    called = threading.Event()
    seen: dict = {}
    original = push_notify.send_alert_push

    def fake(alert, event_log=None, now=None, sender=None):
        seen["alert"] = alert
        seen["log"] = event_log
        called.set()
        return {"status": "sent", "reason": "", "sent": 1, "failed": 0, "revoked": 0}

    push_notify.send_alert_push = fake
    try:
        alert = _alert()
        builder._store(alert)
        assert called.wait(2.0), "_store did not dispatch a push"
        assert alert in store.get_all(), "the alert must still be stored on the dashboard"
        assert seen["alert"] is alert
        assert seen["log"] is event_log
    finally:
        push_notify.send_alert_push = original


def test_alert_sent_event_type_and_helper():
    assert EventType.ALERT_SENT.value == "alert_sent"
    event_log = EventLog(PROFILE)
    event = event_log.log_alert_sent(
        alert_id="x1", tier="red", status="sent", sent=2, failed=0, revoked=1, reason=""
    )
    assert event.type == EventType.ALERT_SENT
    assert event.detail["alert_id"] == "x1"
    assert event.detail["tier"] == "red"
    assert event.detail["sent"] == 2
    assert event.detail["revoked"] == 1


def test_subscriptions_are_scoped_to_the_alert_profile():
    _enable_push()
    _clear_subscriptions()
    _subscribe(endpoint=ENDPOINT, profile_id=PROFILE)
    _subscribe(endpoint=ENDPOINT_2, profile_id=OTHER_PROFILE)

    sender = RecordingSender()
    push_notify.send_alert_push(_alert(), None, now=1000.0, sender=sender)

    assert len(sender.calls) == 1, "only the alert's own profile may be pushed"
    assert sender.calls[0]["subscription_info"]["endpoint"] == ENDPOINT


def run_all_tests():
    tests = [
        test_gate_off_sends_nothing_and_logs_nothing,
        test_no_subscriptions_is_skipped_and_logged,
        test_single_subscription_is_sent_with_fixed_payload,
        test_payload_carries_no_conversation_content,
        test_payload_drops_an_unsafe_alert_id,
        test_vapid_key_is_a_vapid_instance_not_raw_pem,
        test_dead_endpoint_is_revoked,
        test_server_error_keeps_subscription_and_reports_error,
        test_cooldown_skips_second_alert_then_expires,
        test_red_alert_after_orange_always_sends,
        test_cooldown_zero_disables_the_limit,
        test_non_escalations_never_ring_a_phone,
        test_green_to_yellow_is_pushable,
        test_per_tier_windows_and_env_overrides,
        test_yellow_window_is_half_an_hour,
        test_escalation_sends_inside_the_previous_window,
        test_red_window_is_two_minutes,
        test_cooldown_survives_a_restart,
        test_results_are_per_subscription_and_never_contain_endpoints,
        test_accepted_send_records_last_success_per_subscription,
        test_push_outcome_does_not_touch_alert_or_tier_state,
        test_malformed_vapid_key_reports_error_and_never_raises,
        test_ttl_defaults_to_one_hour_and_can_be_overridden,
        test_notify_alert_dispatches_in_a_thread,
        test_notify_alert_swallows_a_crashing_sender,
        test_alert_builder_store_dispatches_a_push,
        test_alert_sent_event_type_and_helper,
        test_subscriptions_are_scoped_to_the_alert_profile,
    ]

    passed = 0
    failed = 0
    for test in tests:
        try:
            test()
            print(f"PASS {test.__name__}")
            passed += 1
        except Exception as exc:
            print(f"FAIL {test.__name__}: {exc}")
            failed += 1

    _disable_push()
    _clear_subscriptions()

    print("=" * 50)
    print(f"{passed} passed, {failed} failed")
    print("ALL PUSH SEND TESTS PASSED!" if not failed else "PUSH SEND TESTS FAILED")
    print("=" * 50)
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    run_all_tests()
