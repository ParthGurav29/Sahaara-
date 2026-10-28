"""
Step 8a — consent-accepted push wording.

Tests that the consent-accepted path in conversation.py:
1. Says "I've sent X an alert." when push is accepted within 1.0 s
2. Falls back to dashboard-only line when push is not accepted
3. Declines with "I understand. I won't call X right now..."
4. Times out with "I'll give you some time..."
5. Swallows push exceptions (never breaks the turn)
6. Logs push_sent_late when push completes after the 1.0 s window

Everything runs against an isolated temp DB and a fake push service: no network,
no real notifications. "sent" means the push service accepted — never that a
phone displayed it.

Run directly: python tests/test_consent_push.py
"""

import sys
sys.path.insert(0, "/Users/parthmangeshgurav/Desktop/sahaara/backend")

import os
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace

# Isolated DB: never touches the live database.
_TEST_DB = Path(tempfile.gettempdir()) / "sahaara_test_consent_push.db"
for _suffix in ("", "-wal", "-shm"):
    Path(str(_TEST_DB) + _suffix).unlink(missing_ok=True)
os.environ["SAHAARA_DB_PATH"] = str(_TEST_DB)

from database import init_db, add_push_subscription
init_db()

from safety.escalation import EscalationTier
from safety.consent import ConsentState
from events.event_log import EventLog, EventType
import push_notify
import conversation

# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------

class FakeResponse:
    def __init__(self, status_code: int = 201):
        self.status_code = status_code


class BlockingSender:
    """Fake push service that blocks until the test releases it."""

    def __init__(self, release: threading.Event, status_code: int = 201):
        self.release = release
        self.status_code = status_code
        self.calls = 0

    def __call__(self, **kwargs):
        self.calls += 1
        self.release.wait()  # block until test releases
        return FakeResponse(self.status_code)


def _enable_push() -> None:
    os.environ["SAHAARA_PUSH_ENABLED"] = "true"
    os.environ["VAPID_PRIVATE_KEY"] = "test"
    os.environ["VAPID_SUBJECT"] = "mailto:test@example.com"
    os.environ["SAHAARA_PUSH_COOLDOWN_SECONDS"] = "0"  # no cooldown for these tests
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


def _subscribe(profile_id: str = "asha_demo_001") -> None:
    add_push_subscription(
        profile_id=profile_id,
        endpoint="https://fcm.googleapis.com/fcm/send/test_consent",
        p256dh="BNcRdreALRFXTkOOUHK1EtK2wtaz5Ry4YfYCA_0QTpQtUbVlUls0VJXg7A8u-Ts1XbjhazAkj7I99e8QcYP7DkM",
        auth="tBHItJI5svbpez7KI4CCXg",
        user_agent="test",
    )


# Fake provider so process_turn doesn't error out (consent path returns before LLM).
class FakeProvider:
    def complete_from_prompt_bundle(self, bundle, max_tokens=None):
        return SimpleNamespace(text="fake", model="fake")


conversation.provider = FakeProvider()

# Monkey-patch point: conversation.py does `from push_notify import send_alert_push`
# inside the function, so patching push_notify.send_alert_push is sufficient.
_original_send_alert_push = push_notify.send_alert_push


def _make_session() -> object:
    """Get the same active session that process_turn uses (module-level manager)."""
    return conversation._session_manager.get_active()


def _set_consent_pending(s, contact_name: str = "Rohan") -> None:
    s.consent_manager.ask_consent(
        contact_name=contact_name, contact_relationship="son",
        tier=EscalationTier.YELLOW, question_text="Call Rohan?",
    )
    assert s.consent_manager.state == ConsentState.PENDING


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------

passed = 0
failed = 0


def check(name: str, condition, detail: str = "") -> None:
    global passed, failed
    if condition:
        passed += 1
        print(f"  PASS: {name}")
    else:
        failed += 1
        print(f"  FAIL: {name} {detail}")


def test_consent_accepted_push_accepted():
    """1. Consent accepted + push accepted within 1.0 s -> 'I've sent Rohan an alert.'"""
    _enable_push()
    _clear_subscriptions()
    _subscribe()

    call_record = {"called": False}

    def fake_send(alert, event_log=None, now=None, sender=None):
        call_record["called"] = True
        return {
            "status": "sent", "reason": "", "sent": 1, "failed": 0,
            "revoked": 0, "results": []
        }

    push_notify.send_alert_push = fake_send

    s = _make_session()
    _set_consent_pending(s, "Rohan")

    result = conversation.process_turn("yes, please call him")

    push_notify.send_alert_push = _original_send_alert_push
    _disable_push()

    check("response mentions sending an alert",
          "sent" in result["response"].lower() and "alert" in result["response"].lower(),
          f"got: {result['response']}")
    check("response does not claim delivery/seen",
          "phone" not in result["response"].lower() and "seen" not in result["response"].lower()
          and "on his" not in result["response"].lower() and "on her" not in result["response"].lower(),
          f"got: {result['response']}")
    check("meta consent_outcome is accepted",
          result["meta"]["consent_outcome"] == "accepted", f"got: {result['meta']}")
    check("send_alert_push was called", call_record["called"], f"called: {call_record['called']}")


def test_consent_accepted_push_not_accepted():
    """2. Consent accepted + push not accepted -> dashboard-only line."""
    _enable_push()
    _clear_subscriptions()
    # no subscriptions -> push returns skipped

    call_record = {"called": False}

    def fake_send(alert, event_log=None, now=None, sender=None):
        call_record["called"] = True
        return {
            "status": "skipped", "reason": "no_subscriptions", "sent": 0, "failed": 0,
            "revoked": 0, "results": []
        }

    push_notify.send_alert_push = fake_send

    s = _make_session()
    _set_consent_pending(s, "Rohan")

    result = conversation.process_turn("yes, please call him")

    push_notify.send_alert_push = _original_send_alert_push
    _disable_push()

    check("response is dashboard-only line",
          "dashboard" in result["response"].lower(),
          f"got: {result['response']}")
    check("meta consent_outcome is accepted",
          result["meta"]["consent_outcome"] == "accepted", f"got: {result['meta']}")
    check("send_alert_push was called", call_record["called"], f"called: {call_record['called']}")


def test_consent_accepted_push_exception_swallowed():
    """5. Push raises an exception -> turn still completes, dashboard-only line."""
    _enable_push()
    _clear_subscriptions()
    _subscribe()

    def failing_send(**kwargs):
        raise RuntimeError("push service exploded")

    push_notify.send_alert_push = failing_send

    s = _make_session()
    _set_consent_pending(s, "Rohan")

    # Should not raise, even though the sender raises.
    result = conversation.process_turn("yes, please call him")

    push_notify.send_alert_push = _original_send_alert_push
    _disable_push()

    check("turn completes despite push exception",
          "response" in result, f"got: {result}")
    check("response is dashboard-only line (safe fallback)",
          "dashboard" in result["response"].lower(),
          f"got: {result['response']}")


def test_consent_declined():
    """3. Consent declined -> 'I understand. I won't call Rohan right now...'"""
    _disable_push()
    _clear_subscriptions()

    s = _make_session()
    _set_consent_pending(s, "Rohan")

    result = conversation.process_turn("no, don't call")

    check("response declines the call",
          "won't call" in result["response"].lower() or "will not call" in result["response"].lower(),
          f"got: {result['response']}")
    check("meta consent_outcome is declined",
          result["meta"]["consent_outcome"] == "declined", f"got: {result['meta']}")


def test_consent_timeout_state():
    """4. Consent timed out -> the turn is processed normally, not wedged.

    Safety bug fixed 2026-10-28: the timeout branch used to return a canned
    "I'll give you some time" reply AND leave the session in TIMED_OUT, so every
    later turn hit the same branch and danger/physical-urgent statements were
    never classified. A timeout must reset consent to IDLE and fall through.
    """
    _disable_push()
    _clear_subscriptions()

    s = _make_session()
    s.consent_manager.ask_consent(
        contact_name="Rohan", contact_relationship="son",
        tier=EscalationTier.YELLOW, question_text="Call Rohan?",
    )
    # Force timeout by setting the timeout timestamp in the past.
    s.consent_manager._timeout_at = time.time() - 1000
    s.consent_manager.check_timeout()

    result = conversation.process_turn("anything")

    check("timeout does not replay the canned reply",
          "give you some time" not in result["response"].lower(),
          f"got: {result['response']}")
    check("consent state cleared after timeout",
          s.consent_manager.state == ConsentState.IDLE,
          f"got: {s.consent_manager.state}")

    # The next turn must still be able to escalate on danger.
    danger = conversation.process_turn("I can't breathe")
    check("danger after timeout still escalates",
          danger["meta"].get("tier") == "red",
          f"got: {danger['meta'].get('tier')}")


def test_push_sent_late_event_logged():
    """6. Push completes after 1.0 s window -> push_sent_late event logged.

    Uses a fake send_alert_push that blocks past the 1.0 s join window
    (dashboard wording used), then completes. The worker thread logs
    push_sent_late because response_chosen was already True.
    """
    _enable_push()
    _clear_subscriptions()
    _subscribe()

    release = threading.Event()

    def late_send(alert, event_log=None, now=None, sender=None):
        # Block past the 1.0 s join window
        release.wait(timeout=5.0)
        return {
            "status": "sent", "reason": "", "sent": 1, "failed": 0,
            "revoked": 0, "results": []
        }

    push_notify.send_alert_push = late_send

    s = _make_session()
    _set_consent_pending(s, "Rohan")

    result = conversation.process_turn("yes, please call him")

    # At this point join(1.0) has returned but sender is still blocked.
    # push_accepted should be False (dashboard wording used).
    check("dashboard wording used while push is pending",
          "dashboard" in result["response"].lower(),
          f"got: {result['response']}")

    # Release the sender so it completes late.
    release.set()
    time.sleep(0.3)  # give the daemon thread a moment to finish and log

    # The event log should contain ALERT_SENT with reason push_sent_late.
    events = s.event_log.get_events(EventType.ALERT_SENT)
    late_events = [e for e in events if e.detail.get("reason") == "push_sent_late"]
    check("push_sent_late event logged after late push",
          len(late_events) >= 1, f"events: {[e.detail for e in events]}")

    push_notify.send_alert_push = _original_send_alert_push
    _disable_push()


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("Step 8a — consent-accepted push wording tests\n")

    test_consent_accepted_push_accepted()
    test_consent_accepted_push_not_accepted()
    test_consent_accepted_push_exception_swallowed()
    test_consent_declined()
    test_consent_timeout_state()
    test_push_sent_late_event_logged()

    print(f"\n{passed} passed, {failed} failed")
    if failed:
        sys.exit(1)
