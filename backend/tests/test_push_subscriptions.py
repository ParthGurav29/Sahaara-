"""
test_push_subscriptions.py

M5 Step 3 — Web Push subscription storage and endpoints.

Storage and configuration only: nothing here sends a notification, so no
delivery is claimed or tested.

Run directly: python tests/test_push_subscriptions.py
"""

import sys
sys.path.insert(0, "/Users/parthmangeshgurav/Desktop/sahaara/backend")

import base64
import json
import os
import tempfile
from pathlib import Path

# Isolated DB: these tests write subscription rows, never the live database.
_TEST_DB = Path(tempfile.gettempdir()) / "sahaara_test_push.db"
for _suffix in ("", "-wal", "-shm"):
    Path(str(_TEST_DB) + _suffix).unlink(missing_ok=True)
os.environ["SAHAARA_DB_PATH"] = str(_TEST_DB)

from database import init_db, get_connection
init_db()

import database
import push
from auth_routes import require_caregiver
from fastapi import FastAPI
from fastapi.testclient import TestClient
from push_routes import router as push_router

PROFILE = "asha_demo_001"
OTHER_PROFILE = "other_profile_002"
ENDPOINT = "https://fcm.googleapis.com/fcm/send/abc123"
ENDPOINT_2 = "https://fcm.googleapis.com/fcm/send/def456"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _client(profile_id: str = PROFILE) -> TestClient:
    """A tiny app with only the push router and a stubbed caregiver."""
    app = FastAPI()
    app.include_router(push_router)
    app.dependency_overrides[require_caregiver] = lambda: {
        "user_id": 1,
        "username": "caregiver",
        "role": "caregiver",
        "profile_id": profile_id,
    }
    return TestClient(app)


def _clear_subscriptions() -> None:
    conn = get_connection()
    try:
        conn.execute("DELETE FROM push_subscriptions")
        conn.commit()
    finally:
        conn.close()


def _enable_push() -> None:
    os.environ["SAHAARA_PUSH_ENABLED"] = "true"


def _disable_push() -> None:
    os.environ["SAHAARA_PUSH_ENABLED"] = "false"


def _set_generated_keypair() -> str:
    """Put a freshly generated VAPID private key in the env; return its public key."""
    from cryptography.hazmat.primitives import serialization
    from py_vapid import Vapid

    vapid = Vapid()
    vapid.generate_keys()
    pem = vapid.private_pem().decode()
    os.environ["VAPID_PRIVATE_KEY"] = pem.replace("\n", "\\n")
    os.environ.pop("VAPID_PUBLIC_KEY", None)

    raw = vapid.public_key.public_bytes(
        serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint,
    )
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


# ---------------------------------------------------------------------------
# database layer
# ---------------------------------------------------------------------------

def test_table_exists():
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='push_subscriptions'"
        ).fetchone()
        columns = [c[1] for c in conn.execute("PRAGMA table_info(push_subscriptions)").fetchall()]
    finally:
        conn.close()

    assert row is not None, "push_subscriptions table missing"
    for column in ("profile_id", "endpoint", "p256dh", "auth", "created_at", "revoked"):
        assert column in columns, f"missing column {column}"
    print("  ✓ push_subscriptions table present with the expected columns")


def test_add_and_get():
    _clear_subscriptions()
    stored = database.add_push_subscription(PROFILE, ENDPOINT, "p256dh-value", "auth-value", "UA/1.0")

    assert stored["endpoint"] == ENDPOINT
    assert stored["profile_id"] == PROFILE
    assert stored["p256dh"] == "p256dh-value"
    assert stored["auth"] == "auth-value"
    assert stored["user_agent"] == "UA/1.0"
    assert stored["revoked"] == 0
    assert stored["created_at"] > 0

    fetched = database.get_push_subscription(ENDPOINT)
    assert fetched is not None and fetched["id"] == stored["id"]
    print("  ✓ add + get round-trip")


def test_resubscribe_upserts():
    _clear_subscriptions()
    first = database.add_push_subscription(PROFILE, ENDPOINT, "old-key", "old-auth")

    database.revoke_push_subscription(ENDPOINT)
    assert database.list_push_subscriptions(PROFILE) == []

    second = database.add_push_subscription(PROFILE, ENDPOINT, "new-key", "new-auth")
    rows = database.list_push_subscriptions(PROFILE, include_revoked=True)

    assert len(rows) == 1, f"expected one row per endpoint, got {len(rows)}"
    assert second["id"] == first["id"], "same endpoint must keep its row id"
    assert second["p256dh"] == "new-key" and second["auth"] == "new-auth"
    assert second["revoked"] == 0, "re-subscribing must un-revoke the row"
    print("  ✓ re-subscribing upserts and un-revokes instead of duplicating")


def test_revoke():
    _clear_subscriptions()
    database.add_push_subscription(PROFILE, ENDPOINT, "k", "a")

    assert database.revoke_push_subscription(ENDPOINT) is True
    assert database.list_push_subscriptions(PROFILE) == []
    assert len(database.list_push_subscriptions(PROFILE, include_revoked=True)) == 1
    assert database.get_push_subscription(ENDPOINT)["revoked"] == 1
    print("  ✓ revoke hides the row from the active list but keeps it for audit")


def test_revoke_unknown_endpoint():
    _clear_subscriptions()
    assert database.revoke_push_subscription("https://example.com/never-registered") is False
    print("  ✓ revoking an unknown endpoint reports no match")


def test_list_scoped_to_profile_and_ordered():
    _clear_subscriptions()
    database.add_push_subscription(PROFILE, ENDPOINT, "k1", "a1")
    database.add_push_subscription(OTHER_PROFILE, "https://fcm.googleapis.com/fcm/send/other", "k2", "a2")
    database.add_push_subscription(PROFILE, ENDPOINT_2, "k3", "a3")

    mine = database.list_push_subscriptions(PROFILE)
    theirs = database.list_push_subscriptions(OTHER_PROFILE)

    assert [row["endpoint"] for row in mine] == [ENDPOINT, ENDPOINT_2], "oldest first, own profile only"
    assert [row["endpoint"] for row in theirs] == ["https://fcm.googleapis.com/fcm/send/other"]
    print("  ✓ subscriptions are scoped per profile and returned oldest first")


def test_user_id_is_stored_and_refreshed():
    _clear_subscriptions()
    row = database.add_push_subscription(
        PROFILE, ENDPOINT, "k1", "a1", user_agent="UA", user_id="7"
    )
    assert row["user_id"] == "7", row

    # A different caregiver signing in on the same browser replaces the owner.
    row2 = database.add_push_subscription(
        PROFILE, ENDPOINT, "k1", "a1", user_agent="UA", user_id="9"
    )
    assert row2["user_id"] == "9", row2
    assert len(database.list_push_subscriptions(PROFILE)) == 1, "still one row, updated"
    print("  ✓ user_id is stored and refreshed on re-subscribe (no duplicate row)")


def test_last_success_at_is_recorded_only_on_success():
    _clear_subscriptions()
    row = database.add_push_subscription(PROFILE, ENDPOINT, "k1", "a1")
    assert row["last_success_at"] is None, "a new subscription has never been accepted"

    assert database.mark_push_subscription_success(ENDPOINT, when=1234.5) is True
    row = database.get_push_subscription(ENDPOINT)
    assert row["last_success_at"] == 1234.5, row

    assert database.mark_push_subscription_success("https://example.com/nope") is False
    print("  ✓ last_success_at is written by an accepted send and never invents one")


def test_init_db_migrates_an_old_push_table():
    """An installed DB created before Step 8 must gain the columns in place."""
    import sqlite3

    old_path = Path(tempfile.gettempdir()) / "sahaara_test_push_old.db"
    for suffix in ("", "-wal", "-shm"):
        Path(str(old_path) + suffix).unlink(missing_ok=True)

    conn = sqlite3.connect(str(old_path))
    conn.execute(
        """CREATE TABLE push_subscriptions (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               profile_id TEXT NOT NULL,
               endpoint TEXT NOT NULL UNIQUE,
               p256dh TEXT NOT NULL,
               auth TEXT NOT NULL,
               user_agent TEXT NOT NULL DEFAULT '',
               created_at REAL NOT NULL,
               updated_at REAL NOT NULL,
               revoked INTEGER NOT NULL DEFAULT 0
           )"""
    )
    conn.execute(
        "INSERT INTO push_subscriptions (profile_id, endpoint, p256dh, auth, created_at, updated_at, revoked)"
        " VALUES (?, ?, 'k', 'a', 1, 1, 0)",
        (PROFILE, "https://fcm.googleapis.com/fcm/send/old"),
    )
    conn.commit()
    conn.close()

    previous = os.environ.get("SAHAARA_DB_PATH")
    os.environ["SAHAARA_DB_PATH"] = str(old_path)
    try:
        import importlib

        import database as db_module

        reloaded = importlib.reload(db_module)
        reloaded.init_db()
        migrated = reloaded.get_push_subscription("https://fcm.googleapis.com/fcm/send/old")
        assert migrated is not None, "the pre-existing row must survive the migration"
        assert migrated["user_id"] == "", "legacy rows get an empty owner, not a crash"
        assert migrated["last_success_at"] is None
    finally:
        if previous is None:
            os.environ.pop("SAHAARA_DB_PATH", None)
        else:
            os.environ["SAHAARA_DB_PATH"] = previous
        import importlib

        import database as db_module

        importlib.reload(db_module)
    print("  ✓ an old push_subscriptions table migrates in place (columns added, row kept)")


# ---------------------------------------------------------------------------
# configuration / key handling
# ---------------------------------------------------------------------------

def test_push_disabled_by_default():
    os.environ.pop("SAHAARA_PUSH_ENABLED", None)
    assert push.push_enabled() is False
    assert push.public_push_config() == {"push_enabled": False}
    print("  ✓ push is OFF when the env var is absent, and no key is served")


def test_flag_parsing():
    for value in ("true", "TRUE", "1", "yes", "on", " true "):
        os.environ["SAHAARA_PUSH_ENABLED"] = value
        assert push.push_enabled() is True, f"{value!r} should enable push"
    for value in ("false", "0", "no", "off", "", "banana"):
        os.environ["SAHAARA_PUSH_ENABLED"] = value
        assert push.push_enabled() is False, f"{value!r} should not enable push"
    _disable_push()
    print("  ✓ only explicit truthy values enable push")


def test_public_key_derivation():
    _enable_push()
    expected = _set_generated_keypair()

    derived = push.vapid_public_key()
    assert derived == expected
    assert len(derived) == 87, "uncompressed P-256 point is 65 bytes -> 87 base64url chars"

    raw = base64.urlsafe_b64decode(derived + "=" * (-len(derived) % 4))
    assert len(raw) == 65 and raw[0] == 0x04, "expected an uncompressed EC point"

    _disable_push()
    print("  ✓ public key is derived from the private key (87-char base64url P-256 point)")


def test_private_key_accepts_escaped_and_real_newlines():
    _enable_push()
    public = _set_generated_keypair()
    escaped_form = os.environ["VAPID_PRIVATE_KEY"]
    assert "\\n" in escaped_form

    real_newlines = escaped_form.replace("\\n", "\n")
    os.environ["VAPID_PRIVATE_KEY"] = real_newlines
    assert push.vapid_public_key() == public

    os.environ["VAPID_PRIVATE_KEY"] = escaped_form
    assert push.vapid_public_key() == public

    _disable_push()
    print("  ✓ escaped \\n and real newlines in .env both work")


def test_malformed_key_does_not_crash_or_leak():
    _enable_push()
    os.environ["VAPID_PRIVATE_KEY"] = "not-even-a-pem"
    os.environ.pop("VAPID_PUBLIC_KEY", None)

    assert push.vapid_public_key() is None
    assert push.public_push_config() == {"push_enabled": True}

    body = json.dumps(push.public_push_config())
    assert "not-even-a-pem" not in body
    _disable_push()
    print("  ✓ a broken key degrades to 'unconfigured' instead of raising")


def test_private_key_never_serialized():
    _enable_push()
    public = _set_generated_keypair()

    body = json.dumps(push.public_push_config())
    assert public in body
    assert "BEGIN PRIVATE KEY" not in body
    for line in os.environ["VAPID_PRIVATE_KEY"].split("\\n"):
        if len(line) > 20:
            assert line not in body, "private key material leaked into the public config"
    _disable_push()
    print("  ✓ only the public key appears in /config; the private key never does")


# ---------------------------------------------------------------------------
# HTTP endpoints
# ---------------------------------------------------------------------------

def test_subscribe_endpoint_stores():
    _enable_push()
    _set_generated_keypair()
    _clear_subscriptions()

    response = _client().post(
        "/push/subscribe",
        json={"endpoint": ENDPOINT, "keys": {"p256dh": "bp256", "auth": "bauth"}, "user_agent": "Pixel"},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["profile_id"] == PROFILE
    assert isinstance(payload["subscription_id"], int)

    row = database.get_push_subscription(ENDPOINT)
    assert row["p256dh"] == "bp256" and row["auth"] == "bauth" and row["user_agent"] == "Pixel"
    _disable_push()
    print("  ✓ POST /push/subscribe stores the browser's subscription")


def test_subscribe_rejects_non_https_endpoint():
    _enable_push()
    _set_generated_keypair()
    _clear_subscriptions()

    response = _client().post(
        "/push/subscribe",
        json={"endpoint": "http://insecure.example.com/push", "keys": {"p256dh": "k", "auth": "a"}},
    )
    assert response.status_code == 400, response.text
    assert "https" in response.json()["detail"].lower()
    assert database.list_push_subscriptions(PROFILE) == []
    _disable_push()
    print("  ✓ non-https endpoints are rejected and nothing is stored")


def test_subscribe_rejects_incomplete_payload():
    _enable_push()
    _set_generated_keypair()

    missing_keys = _client().post("/push/subscribe", json={"endpoint": ENDPOINT})
    assert missing_keys.status_code == 422, missing_keys.text

    empty_key = _client().post(
        "/push/subscribe",
        json={"endpoint": ENDPOINT, "keys": {"p256dh": "", "auth": "a"}},
    )
    assert empty_key.status_code == 422, empty_key.text
    _disable_push()
    print("  ✓ malformed subscriptions are rejected by validation")


def test_subscribe_conflicts_when_push_disabled():
    _disable_push()
    _clear_subscriptions()

    response = _client().post(
        "/push/subscribe",
        json={"endpoint": ENDPOINT, "keys": {"p256dh": "k", "auth": "a"}},
    )
    assert response.status_code == 409, response.text
    assert "SAHAARA_PUSH_ENABLED" in response.json()["detail"]
    assert database.list_push_subscriptions(PROFILE) == [], "a disabled server must not store subscriptions"
    print("  ✓ with the send gate OFF, /push/subscribe refuses to store anything")


def test_unsubscribe_endpoint():
    _enable_push()
    _set_generated_keypair()
    _clear_subscriptions()
    database.add_push_subscription(PROFILE, ENDPOINT, "k", "a")

    response = _client().post("/push/unsubscribe", json={"endpoint": ENDPOINT})
    assert response.status_code == 200, response.text
    assert database.list_push_subscriptions(PROFILE) == []

    # Idempotent: a browser retrying a failed cleanup must not get an error.
    again = _client().post("/push/unsubscribe", json={"endpoint": ENDPOINT})
    assert again.status_code == 200, again.text
    assert database.list_push_subscriptions(PROFILE) == []

    unknown = _client().post("/push/unsubscribe", json={"endpoint": "https://example.com/never-registered"})
    assert unknown.status_code == 404, unknown.text
    _disable_push()
    print("  ✓ POST /push/unsubscribe revokes idempotently; unknown endpoints 404")


def test_unsubscribe_works_when_push_disabled():
    _enable_push()
    _set_generated_keypair()
    _clear_subscriptions()
    database.add_push_subscription(PROFILE, ENDPOINT, "k", "a")
    _disable_push()

    response = _client().post("/push/unsubscribe", json={"endpoint": ENDPOINT})
    assert response.status_code == 200, "cleanup must work even with push turned off"
    assert database.list_push_subscriptions(PROFILE) == []
    print("  ✓ unsubscribe still works with the gate OFF (cleanup path)")


def test_status_endpoint():
    _enable_push()
    public = _set_generated_keypair()
    _clear_subscriptions()
    database.add_push_subscription(PROFILE, ENDPOINT, "k", "a")

    enabled = _client().get("/push/status")
    assert enabled.status_code == 200, enabled.text
    body = enabled.json()
    assert body["push_enabled"] is True
    assert body["vapid_public_key"] == public
    assert body["active_subscriptions"] == 1
    assert body["profile_id"] == PROFILE

    _disable_push()
    disabled = _client().get("/push/status")
    assert disabled.status_code == 200, disabled.text
    off = disabled.json()
    assert off["push_enabled"] is False
    assert off["vapid_public_key"] is None
    print("  ✓ GET /push/status reports the flag, the public key only when enabled, and the active count")


def test_endpoints_require_caregiver_when_auth_enabled():
    _enable_push()
    _set_generated_keypair()
    os.environ["SAHAARA_AUTH_ENABLED"] = "true"
    try:
        # No dependency override: exercise the real require_caregiver.
        app = FastAPI()
        app.include_router(push_router)
        anonymous = TestClient(app)

        assert anonymous.get("/push/status").status_code == 401
        assert anonymous.post(
            "/push/subscribe",
            json={"endpoint": ENDPOINT, "keys": {"p256dh": "k", "auth": "a"}},
        ).status_code == 401
        assert anonymous.post("/push/unsubscribe", json={"endpoint": ENDPOINT}).status_code == 401
    finally:
        os.environ["SAHAARA_AUTH_ENABLED"] = "false"
        _disable_push()
    print("  ✓ all three endpoints require a caregiver token when auth is enabled")


def test_stub_caregiver_when_auth_disabled():
    _enable_push()
    _set_generated_keypair()
    os.environ["SAHAARA_AUTH_ENABLED"] = "false"
    _clear_subscriptions()

    app = FastAPI()
    app.include_router(push_router)
    response = TestClient(app).get("/push/status")
    assert response.status_code == 200, response.text
    assert response.json()["profile_id"] == PROFILE, "auth disabled -> stub caregiver profile"
    _disable_push()
    print("  ✓ with auth disabled the stub caregiver is used (same as every other caregiver route)")


def run_all_tests():
    print("Running push subscription tests (M5 Step 3)...")
    print("=" * 50)

    test_table_exists()
    test_add_and_get()
    test_resubscribe_upserts()
    test_revoke()
    test_revoke_unknown_endpoint()
    test_list_scoped_to_profile_and_ordered()
    test_user_id_is_stored_and_refreshed()
    test_last_success_at_is_recorded_only_on_success()
    test_init_db_migrates_an_old_push_table()

    test_push_disabled_by_default()
    test_flag_parsing()
    test_public_key_derivation()
    test_private_key_accepts_escaped_and_real_newlines()
    test_malformed_key_does_not_crash_or_leak()
    test_private_key_never_serialized()

    test_subscribe_endpoint_stores()
    test_subscribe_rejects_non_https_endpoint()
    test_subscribe_rejects_incomplete_payload()
    test_subscribe_conflicts_when_push_disabled()
    test_unsubscribe_endpoint()
    test_unsubscribe_works_when_push_disabled()
    test_status_endpoint()
    test_endpoints_require_caregiver_when_auth_enabled()
    test_stub_caregiver_when_auth_disabled()

    print("=" * 50)
    print("ALL PUSH SUBSCRIPTION TESTS PASSED!")
    print("=" * 50)


if __name__ == "__main__":
    run_all_tests()
