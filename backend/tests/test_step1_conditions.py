"""
test_step1_conditions.py

Tests for the three conditions attached to the M5 Step 1 approval.

1. SSE/WS authenticate from the `auth_token` cookie, and `GET /auth/me` no
   longer returns the opaque token.
2. The cookie's `Secure` attribute is driven by `SAHAARA_COOKIE_SECURE`.
3. (Documentation-only condition — CORS is a no-op while everything is
   proxied. See `main.py` next to `add_middleware(CORSMiddleware, ...)`.)

Run directly: python tests/test_step1_conditions.py
"""

import sys

sys.path.insert(0, "/Users/parthmangeshgurav/Desktop/sahaara/backend")

import asyncio
import os
import tempfile
from pathlib import Path

# Isolated DB: never touch the live caregiver account or its live tokens.
_TEST_DB = Path(tempfile.gettempdir()) / "sahaara_test_step1.db"
for _suffix in ("", "-wal", "-shm"):
    Path(str(_TEST_DB) + _suffix).unlink(missing_ok=True)
os.environ["SAHAARA_DB_PATH"] = str(_TEST_DB)
os.environ["SAHAARA_AUTH_ENABLED"] = "true"
os.environ.pop("SAHAARA_COOKIE_SECURE", None)

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

import database
import auth
from auth import create_token, create_user
from auth_routes import (
    MeResponse,
    cookie_secure,
    session_token_from_request,
    session_token_from_websocket,
)
from auth_routes import router as auth_router
from dashboard.stream import _validate_stream_token

database.init_db()

USERNAME = "step1_caregiver"
PASSWORD = "demo123"
PROFILE = "asha_demo_001"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

class _StubWebSocket:
    """Just enough of a WebSocket for the token helper."""

    def __init__(self, cookies=None, query=None):
        self.cookies = cookies or {}
        self.query_params = query or {}


def _request(cookie_token=None, query="", bearer=None) -> Request:
    headers = []
    if cookie_token:
        headers.append((b"cookie", f"auth_token={cookie_token}".encode()))
    if bearer:
        headers.append((b"authorization", f"Bearer {bearer}".encode()))
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/stream/events",
            "query_string": query.encode(),
            "headers": headers,
        }
    )


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(auth_router)
    return app


def _reset_users() -> None:
    conn = database.get_connection()
    try:
        conn.execute("DELETE FROM auth_tokens")
        conn.execute("DELETE FROM users")
        conn.commit()
    finally:
        conn.close()


def _seed() -> int:
    _reset_users()
    return create_user(USERNAME, PASSWORD, "caregiver", PROFILE)


def _login(client: TestClient) -> str:
    response = client.post(
        "/auth/login", json={"username": USERNAME, "password": PASSWORD}
    )
    assert response.status_code == 200, response.text
    return response.text


# ---------------------------------------------------------------------------
# condition 1 — cookie auth for HTTP, SSE and WS; /auth/me hides the token
# ---------------------------------------------------------------------------

def test_me_does_not_return_the_token():
    _seed()
    client = TestClient(_app())
    body = _login(client)

    me = client.get("/auth/me")
    assert me.status_code == 200, me.text
    payload = me.json()
    assert "token" not in payload, f"/auth/me still exposes a token: {payload}"
    assert set(payload) == {"user_id", "username", "role", "profile_id"}, payload

    # The login body still carries a token for non-browser clients.
    import json as _json
    assert _json.loads(body)["token"]
    print("PASS: /auth/me returns the user without the token")


def test_me_response_model_has_no_token_field():
    assert "token" not in MeResponse.model_fields, MeResponse.model_fields.keys()
    print("PASS: MeResponse declares no token field")


def test_sse_accepts_the_session_cookie():
    _seed()
    client = TestClient(_app())
    _login(client)
    cookie_token = client.cookies.get("auth_token")
    assert cookie_token, "login did not set the auth_token cookie"

    user = asyncio.run(_validate_stream_token(_request(cookie_token=cookie_token)))
    assert user is not None and user["username"] == USERNAME, user
    print("PASS: SSE validates the session cookie")


def test_sse_still_accepts_the_query_token():
    _seed()
    issued, _ = create_token(auth.get_user_by_username(USERNAME)["id"])
    user = asyncio.run(_validate_stream_token(_request(query=f"token={issued}")))
    assert user is not None and user["username"] == USERNAME, user
    print("PASS: SSE still accepts ?token= for non-browser clients")


def test_sse_cookie_wins_over_a_bad_query_token():
    _seed()
    user_row = auth.get_user_by_username(USERNAME)
    issued, _ = create_token(user_row["id"])
    user = asyncio.run(
        _validate_stream_token(_request(cookie_token=issued, query="token=not-a-token"))
    )
    assert user is not None, "cookie was ignored because the query token was bad"
    print("PASS: a valid cookie beats an invalid query token")


def test_sse_without_credentials_is_401():
    _seed()
    try:
        asyncio.run(_validate_stream_token(_request()))
    except HTTPException as exc:
        assert exc.status_code == 401, exc.status_code
        print("PASS: SSE without credentials raises 401")
        return
    raise AssertionError("SSE accepted a request with no cookie and no token")


def test_sse_auth_disabled_needs_nothing():
    _seed()
    os.environ["SAHAARA_AUTH_ENABLED"] = "false"
    try:
        user = asyncio.run(_validate_stream_token(_request()))
        assert user is None, user
        print("PASS: auth disabled -> SSE needs no credentials")
    finally:
        os.environ["SAHAARA_AUTH_ENABLED"] = "true"


def test_ws_cookie_is_used_first():
    issued, _ = create_token(auth.get_user_by_username(USERNAME)["id"])
    ws = _StubWebSocket(cookies={"auth_token": issued})
    assert session_token_from_websocket(ws) == issued

    ws_query_only = _StubWebSocket(query={"token": "query-token"})
    assert session_token_from_websocket(ws_query_only) == "query-token"

    ws_none = _StubWebSocket()
    assert session_token_from_websocket(ws_none) is None
    print("PASS: websocket token helper prefers the cookie, keeps ?token=")


def test_request_helper_prefers_cookie_then_bearer():
    req = _request(cookie_token="cookie-token", bearer="bearer-token")
    assert session_token_from_request(req) == "cookie-token"

    req_bearer = _request(bearer="bearer-token")
    assert session_token_from_request(req_bearer) == "bearer-token"

    assert session_token_from_request(_request()) is None
    print("PASS: request token helper prefers cookie, falls back to bearer")


def test_voice_ws_rejects_without_credentials_with_a_clean_close():
    """
    The `/ws/voice` handler used to raise NameError (`status` was never
    imported) and answer HTTP 500. It must now deny the handshake cleanly.
    """
    import main as main_module

    _seed()
    client = TestClient(main_module.app)
    denied = None
    try:
        with client.websocket_connect("/ws/voice") as ws:
            ws.receive_text()
    except Exception as exc:  # starlette raises its own denial type
        denied = exc

    assert denied is not None, "unauthenticated /ws/voice was accepted"
    message = str(denied)
    assert "500" not in message, f"handshake failed with a 500: {message}"
    print(f"PASS: /ws/voice denies an unauthenticated handshake ({type(denied).__name__})")


# ---------------------------------------------------------------------------
# condition 2 — Secure attribute behind SAHAARA_COOKIE_SECURE
# ---------------------------------------------------------------------------

def _set_cookie_header(client: TestClient) -> str:
    response = client.post(
        "/auth/login", json={"username": USERNAME, "password": PASSWORD}
    )
    assert response.status_code == 200, response.text
    header = response.headers.get("set-cookie", "")
    assert "auth_token=" in header, header
    return header


def test_cookie_is_not_secure_by_default():
    _seed()
    os.environ.pop("SAHAARA_COOKIE_SECURE", None)
    header = _set_cookie_header(TestClient(_app()))
    assert "httponly" in header.lower(), header
    assert "samesite=lax" in header.lower(), header
    assert "secure" not in header.lower(), header
    assert cookie_secure() is False
    print("PASS: cookie is HttpOnly + SameSite=Lax and NOT Secure by default")


def test_cookie_is_secure_when_the_env_var_is_on():
    _seed()
    os.environ["SAHAARA_COOKIE_SECURE"] = "true"
    try:
        header = _set_cookie_header(TestClient(_app()))
        assert "secure" in header.lower(), header
        assert cookie_secure() is True
    finally:
        os.environ.pop("SAHAARA_COOKIE_SECURE", None)
    print("PASS: SAHAARA_COOKIE_SECURE=true adds the Secure attribute")


def test_cookie_secure_env_parsing():
    for value, expected in [
        ("true", True), ("TRUE", True), ("1", True), ("yes", True), ("on", True),
        ("false", False), ("0", False), ("", False), ("nonsense", False),
    ]:
        os.environ["SAHAARA_COOKIE_SECURE"] = value
        assert cookie_secure() is expected, (value, cookie_secure())
    os.environ.pop("SAHAARA_COOKIE_SECURE", None)
    print("PASS: SAHAARA_COOKIE_SECURE parses truthy/falsey values")


# ---------------------------------------------------------------------------
# runner
# ---------------------------------------------------------------------------

def main() -> None:
    print("Testing M5 Step 1 conditions...")
    print()
    test_me_does_not_return_the_token()
    test_me_response_model_has_no_token_field()
    test_sse_accepts_the_session_cookie()
    test_sse_still_accepts_the_query_token()
    test_sse_cookie_wins_over_a_bad_query_token()
    test_sse_without_credentials_is_401()
    test_sse_auth_disabled_needs_nothing()
    test_ws_cookie_is_used_first()
    test_request_helper_prefers_cookie_then_bearer()
    test_voice_ws_rejects_without_credentials_with_a_clean_close()
    test_cookie_is_not_secure_by_default()
    test_cookie_is_secure_when_the_env_var_is_on()
    test_cookie_secure_env_parsing()
    print("\nAll M5 Step 1 condition tests passed!")


if __name__ == "__main__":
    main()
