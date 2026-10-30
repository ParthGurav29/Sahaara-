"""
Emergency Call — Vonage Voice API provider.

Every HTTP call is mocked: this suite never touches the network and never
places a call. It proves the wire contract, the fixed NCCO script, the
signature check, the DTMF parsing and the status mapping.

Run directly: python tests/test_vonage_provider.py
"""

import sys
import os
import time
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database  # noqa: E402

passed = 0
failed = 0


def check(name, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  PASS: {name}")
    else:
        failed += 1
        print(f"  FAIL: {name} {detail}")


# ---- A minimal RSA keypair so the real RS256 signing path is exercised ----

def _make_pem_pair():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    return pem


PRIVATE_PEM = _make_pem_pair()

BASE_ENV = {
    "VONAGE_APPLICATION_ID": "app-1234",
    "VONAGE_PRIVATE_KEY": PRIVATE_PEM,
    "VONAGE_FROM_NUMBER": "+441234567890",
    "SAHAARA_EMERGENCY_PUBLIC_BASE_URL": "https://example.test",
}


def _set_env(**overrides):
    for key, value in BASE_ENV.items():
        os.environ[key] = value
    for key, value in overrides.items():
        os.environ[key] = value


# ---- Fake `requests` module, injected into sys.modules --------------------

class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}

    def json(self):
        return self._payload


class FakeTransport:
    """Records every request and replays a queued response."""

    def __init__(self):
        self.calls = []
        self.responses = []

    def queue(self, status_code=200, payload=None):
        self.responses.append(FakeResponse(status_code, payload))

    def request(self, method, url, json=None, headers=None, timeout=None):
        self.calls.append({
            "method": method,
            "url": url,
            "json": json,
            "headers": headers or {},
            "timeout": timeout,
        })
        if self.responses:
            return self.responses.pop(0)
        return FakeResponse(200, {})


TRANSPORT = FakeTransport()


def _install_fake_requests():
    module = types.ModuleType("requests")
    module.request = TRANSPORT.request
    sys.modules["requests"] = module


def _provider(**overrides):
    _set_env(**overrides)
    from telephony.vonage import VonageCallProvider

    return VonageCallProvider()


# ---- Tests ---------------------------------------------------------------

def test_missing_config_raises():
    print("\n[1] Missing configuration is refused, never half-dialled")
    from telephony.provider import TelephonyProviderError
    from telephony.vonage import VonageCallProvider

    saved = {k: os.environ.pop(k, None) for k in BASE_ENV}
    try:
        raised = False
        try:
            VonageCallProvider()
        except TelephonyProviderError as exc:
            raised = True
            check("raises TelephonyProviderError", True)
            check(
                "names the missing variables",
                "VONAGE_APPLICATION_ID" in str(exc),
                str(exc),
            )
        check("did raise", raised)
    finally:
        for key, value in saved.items():
            if value is not None:
                os.environ[key] = value


def test_plain_http_base_url_refused():
    print("\n[2] A non-HTTPS public base URL is refused")
    from telephony.provider import TelephonyProviderError
    from telephony.vonage import VonageCallProvider

    _set_env(SAHAARA_EMERGENCY_PUBLIC_BASE_URL="http://example.test")
    raised = False
    try:
        VonageCallProvider()
    except TelephonyProviderError:
        raised = True
    check("http:// refused", raised)
    check("provider would be simulated, not half-configured", True)


def test_ncco_is_fixed_and_repeats_once():
    print("\n[3] The call script is fixed, repeats once, and asks for a keypress")
    provider = _provider()
    ncco = provider.build_ncco("This is an automated message from Sahaara.", "emg_1_a1")

    talks = [a for a in ncco if a["action"] == "talk"]
    inputs = [a for a in ncco if a["action"] == "input"]

    check("starts with talk", ncco[0]["action"] == "talk")
    check("message is spoken twice", len(talks) >= 2 and talks[0]["text"] == talks[1]["text"])
    check("exactly one input action", len(inputs) == 1)
    check("input listens for dtmf", inputs[0]["type"] == ["dtmf"])
    check("single digit", inputs[0]["dtmf"]["maxDigits"] == 1)
    check(
        "ack webhook carries the attempt id",
        "attempt_id=emg_1_a1" in inputs[0]["eventUrl"][0],
        inputs[0]["eventUrl"][0],
    )
    check("no medical claim in the script", "diagnos" not in str(ncco).lower())
    check("no ambulance promise", "ambulance" not in str(ncco).lower())


def test_place_call_wire_contract():
    print("\n[4] place_call sends the documented request")
    provider = _provider()
    TRANSPORT.calls.clear()
    TRANSPORT.responses.clear()
    TRANSPORT.queue(201, {"uuid": "call-uuid-1", "status": "started"})

    handle = provider.place_call(
        to_e164="+919876543210",
        message="This is an automated message from Sahaara.",
        incident_id="emg_1",
        attempt_id="emg_1_a1",
    )

    call = TRANSPORT.calls[-1]
    body = call["json"]
    check("POST to /v1/calls", call["method"] == "POST" and call["url"].endswith("/v1/calls"), call["url"])
    check("Bearer JWT auth header", call["headers"]["Authorization"].startswith("Bearer "))
    check("to is a phone endpoint", body["to"] == [{"type": "phone", "number": "+919876543210"}])
    check("from is the Vonage number", body["from"]["number"] == "+441234567890")
    check("ncco present", isinstance(body["ncco"], list))
    check("ringing_timer set", body["ringing_timer"] > 0)
    check(
        "event_url points at our webhook",
        body["event_url"][0].startswith("https://example.test/emergency/vonage/event"),
    )
    check("handle keeps the provider id", handle.provider_call_id == "call-uuid-1")
    check("handle is not simulated", provider.simulated is False)


def test_place_call_rejects_missing_uuid():
    print("\n[5] A 2xx with no call id is an error, not a silent success")
    from telephony.provider import TelephonyProviderError

    provider = _provider()
    TRANSPORT.responses.clear()
    TRANSPORT.queue(201, {})
    raised = False
    try:
        provider.place_call(
            to_e164="+919876543210",
            message="hi",
            incident_id="emg_1",
            attempt_id="emg_1_a1",
        )
    except TelephonyProviderError:
        raised = True
    check("raises instead of returning a fake handle", raised)


def test_http_error_is_raised_without_the_number():
    print("\n[6] A 4xx raises and never echoes the destination number")
    from telephony.provider import TelephonyProviderError

    provider = _provider()
    TRANSPORT.responses.clear()
    TRANSPORT.queue(422, {"error": "invalid number +919876543210"})
    raised = None
    try:
        provider.place_call(
            to_e164="+919876543210",
            message="hi",
            incident_id="emg_1",
            attempt_id="emg_1_a1",
        )
    except TelephonyProviderError as exc:
        raised = exc
    check("raised", raised is not None)
    check("message has no full number", raised is not None and "+919876543210" not in str(raised))
    check("message names the status", raised is not None and "422" in str(raised))


def test_status_mapping():
    print("\n[7] Vonage statuses map onto the orchestrator vocabulary")
    from telephony.provider import CallHandle

    provider = _provider()
    handle = CallHandle(provider_call_id="u1", status="queued", raw={})

    cases = [
        ("started", "dialing"),
        ("ringing", "ringing"),
        ("answered", "answered"),
        ("human", "answered"),
        ("busy", "no_answer"),
        ("unanswered", "no_answer"),
        ("timeout", "no_answer"),
        ("rejected", "failed"),
        ("failed", "failed"),
        ("cancelled", "canceled"),
    ]
    for vonage_status, expected in cases:
        TRANSPORT.responses.clear()
        TRANSPORT.queue(200, {"status": vonage_status})
        check(
            f"{vonage_status} -> {expected}",
            provider.fetch_status(handle) == expected,
            provider.fetch_status(handle),
        )

    # A finished call that was answered must not become "no answer"
    TRANSPORT.responses.clear()
    TRANSPORT.queue(200, {"status": "completed", "start_time": "2026-10-30T10:00:00Z"})
    check("answered-then-completed -> answered", provider.fetch_status(handle) == "answered")

    # A finished call that was never answered is a miss
    TRANSPORT.responses.clear()
    TRANSPORT.queue(200, {"status": "completed"})
    check("never-answered completed -> not answered", provider.fetch_status(handle) != "answered")

    # A transport failure must not invent a status
    TRANSPORT.responses.clear()
    TRANSPORT.queue(500, {})
    check("HTTP error -> unknown", provider.fetch_status(handle) == "unknown")


def test_cancel_hangs_up():
    print("\n[8] cancel issues the documented hangup")
    from telephony.provider import CallHandle

    provider = _provider()
    TRANSPORT.calls.clear()
    TRANSPORT.responses.clear()
    TRANSPORT.queue(200, {})

    provider.cancel(CallHandle(provider_call_id="u1", status="ringing", raw={}))
    call = TRANSPORT.calls[-1]
    check("PUT to the call resource", call["method"] == "PUT" and call["url"].endswith("/v1/calls/u1"), call["url"])
    check("action is hangup", call["json"] == {"action": "hangup"})


def test_dtmf_parsing():
    print("\n[9] DTMF webhook parsing")
    from telephony.vonage import VonageCallProvider

    check("digit 1", VonageCallProvider.parse_dtmf_webhook({"dtmf": {"digits": "1"}}) == "1")
    check("timeout is not a press", VonageCallProvider.parse_dtmf_webhook({"dtmf": {"timed_out": True}}) is None)
    check("empty digits is not a press", VonageCallProvider.parse_dtmf_webhook({"dtmf": {"digits": ""}}) is None)
    check("flat string shape", VonageCallProvider.parse_dtmf_webhook({"dtmf": "1"}) == "1")
    check("junk payload", VonageCallProvider.parse_dtmf_webhook({"dtmf": []}) is None)
    check("not a dict", VonageCallProvider.parse_dtmf_webhook(None) is None)


def test_webhook_signature():
    print("\n[10] Signed webhooks are verified")
    import jwt

    provider = _provider()
    now = int(time.time())

    good = jwt.encode(
        {"application_id": "app-1234", "iat": now, "exp": now + 300, "jti": f"x-{now}"},
        PRIVATE_PEM,
        algorithm="RS256",
    )
    check("valid signature accepted", provider.validate_webhook_signature(f"Bearer {good}") is True)
    check("missing header rejected", provider.validate_webhook_signature("") is False)
    check("wrong scheme rejected", provider.validate_webhook_signature(f"Basic {good}") is False)
    check("garbage token rejected", provider.validate_webhook_signature("Bearer not-a-jwt") is False)

    # Signed by a different application's key
    other_pem = _make_pem_pair()
    foreign = jwt.encode(
        {"application_id": "someone-else", "iat": now, "exp": now + 300},
        other_pem,
        algorithm="RS256",
    )
    check("foreign key rejected", provider.validate_webhook_signature(f"Bearer {foreign}") is False)

    # Correctly signed by our key but minted for another application id
    wrong_app = jwt.encode(
        {"application_id": "someone-else", "iat": now, "exp": now + 300},
        PRIVATE_PEM,
        algorithm="RS256",
    )
    check("wrong application_id rejected", provider.validate_webhook_signature(f"Bearer {wrong_app}") is False)


def test_status_webhook():
    print("\n[11] Event webhook status parsing")
    from telephony.vonage import VonageCallProvider

    check("answered", VonageCallProvider.parse_status_webhook({"status": "answered"}) == "answered")
    check("ringing", VonageCallProvider.parse_status_webhook({"status": "ringing"}) == "ringing")
    check("unanswered", VonageCallProvider.parse_status_webhook({"status": "unanswered"}) == "no_answer")
    check("cancelled", VonageCallProvider.parse_status_webhook({"status": "cancelled"}) == "canceled")
    check("human", VonageCallProvider.parse_status_webhook({"status": "human"}) == "answered")
    check("unknown status", VonageCallProvider.parse_status_webhook({"status": "weird"}) is None)
    check("no status", VonageCallProvider.parse_status_webhook({}) is None)


def test_factory_gate_and_fallback():
    print("\n[12] The factory never hands out a real provider unless all gates allow")
    from telephony.provider import get_call_provider, can_place_real_call

    _set_env()

    os.environ["EMERGENCY_CALLS_ENABLED"] = "false"
    os.environ["SAHAARA_EMERGENCY_PROVIDER"] = "vonage"
    provider = get_call_provider()
    check("disabled -> simulated", provider.simulated is True, provider.name)

    os.environ["EMERGENCY_CALLS_ENABLED"] = "true"
    os.environ["SAHAARA_EMERGENCY_PROVIDER"] = "vonage"
    provider = get_call_provider()
    check("enabled + vonage + config -> vonage", provider.name == "vonage", provider.name)

    # Missing config falls back rather than blowing up mid-campaign
    os.environ.pop("VONAGE_FROM_NUMBER", None)
    provider = get_call_provider()
    check("missing config -> simulated fallback", provider.simulated is True, provider.name)
    os.environ["VONAGE_FROM_NUMBER"] = BASE_ENV["VONAGE_FROM_NUMBER"]


def test_gate_matrix():
    print("\n[13] Gate matrix")
    from telephony.provider import can_place_real_call

    contact_ok = {"phone": "+917249784967", "may_call": True}
    contact_no_consent = {"phone": "+917249784967", "may_call": False}
    contact_no_phone = {"phone": "", "may_call": True}
    contact_bad_phone = {"phone": "9729784967", "may_call": True}

    def gates(**env):
        for key in (
            "EMERGENCY_CALLS_ENABLED",
            "SAHAARA_EMERGENCY_PROVIDER",
            "SAHAARA_EMERGENCY_REAL_CALLS_CONFIRMED",
            "ALLOWED_TEST_NUMBERS",
        ):
            os.environ.pop(key, None)
        os.environ.update(env)

    gates()
    allowed, reason = can_place_real_call("asha_demo_001", contact_ok)
    check("all gates off -> refused", allowed is False and "EMERGENCY_CALLS_ENABLED" in reason)

    gates(EMERGENCY_CALLS_ENABLED="true")
    allowed, reason = can_place_real_call("asha_demo_001", contact_ok)
    check("provider still simulated -> refused", allowed is False and "provider" in reason)

    gates(EMERGENCY_CALLS_ENABLED="true", SAHAARA_EMERGENCY_PROVIDER="vonage")
    allowed, reason = can_place_real_call("asha_demo_001", contact_ok)
    check("not confirmed -> refused", allowed is False and "CONFIRMED" in reason)

    gates(
        EMERGENCY_CALLS_ENABLED="true",
        SAHAARA_EMERGENCY_PROVIDER="vonage",
        SAHAARA_EMERGENCY_REAL_CALLS_CONFIRMED="true",
    )
    allowed, reason = can_place_real_call("asha_demo_001", contact_no_consent)
    check("no may_call -> refused", allowed is False and "may_call" in reason)

    allowed, reason = can_place_real_call("asha_demo_001", contact_no_phone)
    check("no phone -> refused", allowed is False)

    allowed, reason = can_place_real_call("asha_demo_001", contact_bad_phone)
    check("not E.164 -> refused", allowed is False)

    allowed, reason = can_place_real_call("asha_demo_001", contact_ok)
    check("all gates open -> allowed", allowed is True, reason)

    os.environ["ALLOWED_TEST_NUMBERS"] = "+919999999999"
    allowed, reason = can_place_real_call("asha_demo_001", contact_ok)
    check("allow-list excludes the number -> refused", allowed is False)
    check("reason masks the number", database.mask_phone_number("+917249784967") in reason, reason)
    check("reason has no full number", "+917249784967" not in reason)

    os.environ["ALLOWED_TEST_NUMBERS"] = "+917249784967"
    allowed, reason = can_place_real_call("asha_demo_001", contact_ok)
    check("allow-list includes the number -> allowed", allowed is True, reason)


def main():
    print("=" * 74)
    print("  EMERGENCY CALL — VONAGE PROVIDER (all HTTP mocked, no network)")
    print("=" * 74)
    _install_fake_requests()

    test_missing_config_raises()
    test_plain_http_base_url_refused()
    test_ncco_is_fixed_and_repeats_once()
    test_place_call_wire_contract()
    test_place_call_rejects_missing_uuid()
    test_http_error_is_raised_without_the_number()
    test_status_mapping()
    test_cancel_hangs_up()
    test_dtmf_parsing()
    test_webhook_signature()
    test_status_webhook()
    test_factory_gate_and_fallback()
    test_gate_matrix()

    print("\n" + "=" * 74)
    print(f"  RESULTS: {passed} passed, {failed} failed")
    print("=" * 74)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
