"""
test_push_status.py

M5 Step 6 — the dashboard's read side of the push record.

`push.push_attempts_by_alert()` joins `ALERT_SENT` events back to their alert
so `/alerts` and `/alerts/{id}/context` can show what the push service
answered. Pure function over an EventLog: no DB, no FastAPI, no network.

Run directly: python tests/test_push_status.py
"""

import sys
sys.path.insert(0, "/Users/parthmangeshgurav/Desktop/sahaara/backend")

import json
from datetime import datetime

import push
from events.event_log import Event, EventLog, EventType, EscalationTier

PROFILE = "asha_demo_001"


def _log() -> EventLog:
    return EventLog(PROFILE)


def test_empty_log_returns_empty_map():
    assert push.push_attempts_by_alert(_log()) == {}


def test_none_event_log_is_safe():
    assert push.push_attempts_by_alert(None) == {}


def test_single_attempt_is_mapped():
    log = _log()
    log.log_alert_sent(alert_id="alert_1", tier="red", status="sent", sent=1)

    attempts = push.push_attempts_by_alert(log)

    assert list(attempts) == ["alert_1"], attempts
    block = attempts["alert_1"]
    assert block["status"] == "sent"
    assert block["reason"] == ""
    assert block["sent"] == 1
    assert block["failed"] == 0
    assert block["revoked"] == 0
    assert block["at"] > 0
    assert block["at_iso"]


def test_latest_attempt_wins():
    log = _log()
    log.log_alert_sent(alert_id="alert_1", status="skipped", reason="cooldown")
    log.log_alert_sent(alert_id="alert_1", status="sent", sent=2)

    block = push.push_attempts_by_alert(log)["alert_1"]

    assert block["status"] == "sent", block
    assert block["sent"] == 2, block
    assert block["reason"] == "", block


def test_alerts_do_not_leak_into_each_other():
    log = _log()
    log.log_alert_sent(alert_id="alert_1", status="sent", sent=1)
    log.log_alert_sent(alert_id="alert_2", status="error", reason="no_vapid_key", failed=1)

    attempts = push.push_attempts_by_alert(log)

    assert set(attempts) == {"alert_1", "alert_2"}, attempts
    assert attempts["alert_1"]["status"] == "sent"
    assert attempts["alert_2"]["status"] == "error"
    assert attempts["alert_2"]["reason"] == "no_vapid_key"


def test_records_without_alert_id_are_skipped():
    log = _log()
    log.log_alert_sent(alert_id="", status="skipped", reason="gate_off")
    log.log_alert_sent(alert_id="alert_1", status="sent", sent=1)

    attempts = push.push_attempts_by_alert(log)

    assert set(attempts) == {"alert_1"}, attempts


def test_malformed_event_is_skipped_not_fatal():
    log = _log()
    log.log_alert_sent(alert_id="alert_1", status="sent", sent=1)

    # A hand-made event with a non-dict detail must not break the join.
    log._events.append(
        Event(
            timestamp=1.0,
            type=EventType.ALERT_SENT,
            profile_id=PROFILE,
            detail=None,  # type: ignore[arg-type]
        )
    )

    attempts = push.push_attempts_by_alert(log)

    assert set(attempts) == {"alert_1"}, attempts
    assert attempts["alert_1"]["status"] == "sent"


def test_at_and_at_iso_agree():
    log = _log()
    log.log_alert_sent(alert_id="alert_1", status="sent", sent=1)

    block = push.push_attempts_by_alert(log)["alert_1"]
    parsed = datetime.fromisoformat(block["at_iso"])

    assert abs(parsed.timestamp() - block["at"]) < 2, (parsed.timestamp(), block["at"])


def test_block_carries_only_push_facts():
    log = _log()
    event = log.log_alert_sent(
        alert_id="alert_1",
        tier="red",
        status="sent",
        sent=1,
        failed=0,
        revoked=0,
    )
    # Even if an event detail ever grew more fields, the dashboard block is a
    # whitelist: conversation text can never ride along.
    event.detail["private"] = "she fell and cannot breathe"

    block = push.push_attempts_by_alert(log)["alert_1"]

    assert set(block) == {"status", "reason", "sent", "failed", "revoked", "at", "at_iso"}, block
    blob = json.dumps(block).lower()
    for banned in ("fell", "breathe", "private", "she "):
        assert banned not in blob, f"{banned!r} leaked into the dashboard push block"


def test_skipped_reasons_survive():
    log = _log()
    log.log_alert_sent(alert_id="a", status="skipped", reason="no_subscriptions")
    log.log_alert_sent(alert_id="b", status="skipped", reason="dead_subscriptions", revoked=1)

    attempts = push.push_attempts_by_alert(log)

    assert attempts["a"]["reason"] == "no_subscriptions"
    assert attempts["b"]["reason"] == "dead_subscriptions"
    assert attempts["b"]["revoked"] == 1


def test_tier_is_not_duplicated_in_the_dashboard_block():
    """The timeline already knows the alert's tier; the block stays minimal."""
    log = _log()
    log.log_alert_sent(alert_id="alert_1", tier=EscalationTier.RED.value, status="sent", sent=1)

    block = push.push_attempts_by_alert(log)["alert_1"]

    assert "tier" not in block, "tier belongs to the alert, not the push block"


def run_all_tests():
    tests = [
        test_empty_log_returns_empty_map,
        test_none_event_log_is_safe,
        test_single_attempt_is_mapped,
        test_latest_attempt_wins,
        test_alerts_do_not_leak_into_each_other,
        test_records_without_alert_id_are_skipped,
        test_malformed_event_is_skipped_not_fatal,
        test_at_and_at_iso_agree,
        test_block_carries_only_push_facts,
        test_skipped_reasons_survive,
        test_tier_is_not_duplicated_in_the_dashboard_block,
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

    print("=" * 50)
    print(f"{passed} passed, {failed} failed")
    print("ALL PUSH STATUS TESTS PASSED!" if not failed else "PUSH STATUS TESTS FAILED")
    print("=" * 50)
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    run_all_tests()
