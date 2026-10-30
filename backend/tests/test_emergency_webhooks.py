"""
Emergency Call — the Vonage webhook routes.

`test_vonage_provider.py` covers the provider layer (JWT signing, the NCCO
body, status mapping, DTMF parsing) with a fake transport. This suite covers
the two HTTP routes the provider calls back into:

    POST /emergency/vonage/ack    (the "press 1" DTMF webhook)
    POST /emergency/vonage/event  (call status events)

The important properties are negative ones:

  * an unsigned or wrongly-signed webhook is refused with 403 and changes
    nothing;
  * a webhook can only move an attempt towards a state the provider reported —
    it can never invent an acknowledgement;
  * a digit other than 1 never acknowledges;
  * no response body, and no event, ever contains a phone number.

Run directly: python tests/test_emergency_webhooks.py
"""

import importlib
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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


# ---- A throwaway DB, before anything imports it --------------------------

_TMP = Path(tempfile.mkdtemp(prefix="sahaara_webhooks_"))
os.environ["SAHAARA_DB_PATH"] = str(_TMP / "webhooks.db")
os.environ["SAHAARA_AUTH_ENABLED"] = "false"
os.environ["EMERGENCY_CALLS_ENABLED"] = "true"
os.environ["SAHAARA_EMERGENCY_PROVIDER"] = "simulated"
os.environ["SAHAARA_EMERGENCY_REAL_CALLS_CONFIRMED"] = "false"
os.environ["SAHAARA_EMERGENCY_ACK_SECONDS"] = "45"
os.environ["SAHAARA_PUSH_ENABLED"] = "false"

FULL_NUMBER = "+919876543210"
MASKED = "+91 **** 3210"


# ---- A real Vonage provider with a test key pair -------------------------

def _make_provider():
    """A Genuine VonageProvider, configured with a throwaway RSA key."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()

    os.environ["VONAGE_APPLICATION_ID"] = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    os.environ["VONAGE_PRIVATE_KEY"] = pem
    os.environ["VONAGE_FROM_NUMBER"] = "+15550001111"
    os.environ["SAHAARA_EMERGENCY_PUBLIC_BASE_URL"] = "https://example.test"

    import telephony.vonage as vonage_module

    importlib.reload(vonage_module)
    provider = vonage_module.VonageCallProvider()
    return provider, pem, key


def _sign(pem: str, application_id: str = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"):
    import jwt

    return jwt.encode(
        {
            "application_id": application_id,
            "iat": int(time.time()),
            "exp": int(time.time()) + 300,
            "jti": "test-webhook",
        },
        pem,
        algorithm="RS256",
        headers={"kid": application_id},
    )


def _fresh_incident_with_attempt(suffix: str = ""):
    """Create one live incident and one dialing attempt for it."""
    import database

    incident_id = f"inc_webhook{suffix}"
    attempt_id = f"{incident_id}_a1"
    database.create_emergency_incident(
        incident_id=incident_id,
        profile_id="asha_demo_001",
        trigger="physical_urgent",
        trigger_phrase="I can't breathe",
        tier="red",
        simulated=False,
        provider="vonage",
    )
    database.create_emergency_attempt(
        attempt_id=attempt_id,
        incident_id=incident_id,
        contact_order=0,
        contact_name="Rohan",
        contact_rel="son",
        phone_masked=MASKED,
        attempt=1,
        status="dialing",
        simulated=False,
    )
    database.increment_emergency_incident_attempts(incident_id)
    return incident_id, attempt_id


def main():
    print("=" * 74)
    print("  EMERGENCY CALL — VONAGE WEBHOOK ROUTES")
    print("=" * 74)

    import database

    database.init_db()

    provider, pem, key = _make_provider()

    # The routes call `get_call_provider()` at request time; pin it to the real
    # provider so signature enforcement is actually exercised.
    import telephony.provider as provider_module

    original_factory = provider_module.get_call_provider
    provider_module.get_call_provider = lambda: provider

    import main as main_module

    from fastapi.testclient import TestClient

    client = TestClient(main_module.app)

    try:
        # ------------------------------------------------------------------
        print("\n[1] A webhook with no signature is refused")
        # ------------------------------------------------------------------
        incident_id, attempt_id = _fresh_incident_with_attempt()

        unsigned = client.post(
            f"/emergency/vonage/ack?attempt_id={attempt_id}",
            data={"dtmf": '{"digits":"1","timed_out":false}'},
        )
        check("ack without a signature -> 403", unsigned.status_code == 403, unsigned.status_code)
        check(
            "the attempt was not acknowledged",
            database.get_emergency_attempt(attempt_id)["status"] == "dialing",
            database.get_emergency_attempt(attempt_id)["status"],
        )

        unsigned_event = client.post(
            f"/emergency/vonage/event?attempt_id={attempt_id}",
            data={"status": "answered"},
        )
        check(
            "event without a signature -> 403",
            unsigned_event.status_code == 403,
            unsigned_event.status_code,
        )
        check(
            "an unsigned event cannot move the attempt",
            database.get_emergency_attempt(attempt_id)["status"] == "dialing",
            database.get_emergency_attempt(attempt_id)["status"],
        )

        # ------------------------------------------------------------------
        print("\n[2] A signature minted for another application is refused")
        # ------------------------------------------------------------------
        foreign = _sign(pem, application_id="99999999-9999-9999-9999-999999999999")
        refused = client.post(
            f"/emergency/vonage/ack?attempt_id={attempt_id}",
            data={"dtmf": '{"digits":"1","timed_out":false}'},
            headers={"Authorization": f"Bearer {foreign}"},
        )
        check("foreign application_id -> 403", refused.status_code == 403, refused.status_code)
        check(
            "still not acknowledged",
            database.get_emergency_attempt(attempt_id)["status"] == "dialing",
            database.get_emergency_attempt(attempt_id)["status"],
        )

        # ------------------------------------------------------------------
        print("\n[3] A genuine DTMF 1 acknowledges the attempt")
        # ------------------------------------------------------------------
        good = _sign(pem)
        ack = client.post(
            f"/emergency/vonage/ack?attempt_id={attempt_id}",
            # This is the shape Vonage posts: the `dtmf` field is a JSON string.
            data={"dtmf": '{"digits":"1","timed_out":false}'},
            headers={"Authorization": f"Bearer {good}"},
        )
        check("ack accepted", ack.status_code == 200, ack.status_code)
        check("ack reports acknowledged", ack.json().get("status") == "acknowledged", ack.text)
        check(
            "the attempt is acknowledged",
            database.get_emergency_attempt(attempt_id)["status"] == "acknowledged",
            database.get_emergency_attempt(attempt_id)["status"],
        )
        check(
            "the incident is acknowledged",
            database.get_emergency_incident(incident_id)["status"] == "acknowledged",
            database.get_emergency_incident(incident_id)["status"],
        )

        # ------------------------------------------------------------------
        print("\n[4] A second acknowledgement is idempotent, not a second event")
        # ------------------------------------------------------------------
        again = client.post(
            f"/emergency/vonage/ack?attempt_id={attempt_id}",
            data={"dtmf": '{"digits":"1","timed_out":false}'},
            headers={"Authorization": f"Bearer {good}"},
        )
        check("re-ack is not an error", again.status_code == 200, again.status_code)
        check("re-ack reports ignored", again.json().get("status") == "ignored", again.text)

        # ------------------------------------------------------------------
        print("\n[5] Any digit other than 1 is ignored")
        # ------------------------------------------------------------------
        incident_b, attempt_b = _fresh_incident_with_attempt("b")
        for digit in ("2", "9", "", "12"):
            res = client.post(
                f"/emergency/vonage/ack?attempt_id={attempt_b}",
                data={"dtmf": f'{{"digits":"{digit}","timed_out":false}}'},
                headers={"Authorization": f"Bearer {good}"},
            )
            check(f"digit {digit!r} ignored", res.json().get("status") == "ignored", res.text)
        check(
            "the attempt is still not acknowledged",
            database.get_emergency_attempt(attempt_b)["status"] == "dialing",
            database.get_emergency_attempt(attempt_b)["status"],
        )

        # A DTMF timeout is not an acknowledgement either.
        timed_out = client.post(
            f"/emergency/vonage/ack?attempt_id={attempt_b}",
            data={"dtmf": '{"digits":"","timed_out":true}'},
            headers={"Authorization": f"Bearer {good}"},
        )
        check("timed-out DTMF ignored", timed_out.json().get("status") == "ignored", timed_out.text)
        check(
            "a timeout never acknowledges",
            database.get_emergency_attempt(attempt_b)["status"] == "dialing",
            database.get_emergency_attempt(attempt_b)["status"],
        )

        # ------------------------------------------------------------------
        print("\n[6] Status events are recorded, never promoted to an ack")
        # ------------------------------------------------------------------
        ringing = client.post(
            f"/emergency/vonage/event?attempt_id={attempt_b}",
            data={"status": "ringing"},
            headers={"Authorization": f"Bearer {good}"},
        )
        check("ringing accepted", ringing.status_code == 200, ringing.status_code)
        check(
            "the attempt is ringing",
            database.get_emergency_attempt(attempt_b)["status"] == "ringing",
            database.get_emergency_attempt(attempt_b)["status"],
        )

        answered = client.post(
            f"/emergency/vonage/event?attempt_id={attempt_b}",
            data={"status": "answered"},
            headers={"Authorization": f"Bearer {good}"},
        )
        check("answered accepted", answered.status_code == 200, answered.status_code)
        check(
            "an answered call is answered, NOT acknowledged",
            database.get_emergency_attempt(attempt_b)["status"] == "answered",
            database.get_emergency_attempt(attempt_b)["status"],
        )
        check(
            "the incident is not acknowledged by an answer alone",
            database.get_emergency_incident(incident_b)["status"] != "acknowledged",
            database.get_emergency_incident(incident_b)["status"],
        )

        # An intermediate event we do not act on must not error (Vonage would
        # retry it forever) and must not change anything.
        unknown = client.post(
            f"/emergency/vonage/event?attempt_id={attempt_b}",
            data={"status": "some_future_status"},
            headers={"Authorization": f"Bearer {good}"},
        )
        check("unknown status is accepted", unknown.status_code == 200, unknown.status_code)
        check("unknown status is ignored", unknown.json().get("status") == "ignored", unknown.text)
        check(
            "the attempt did not move",
            database.get_emergency_attempt(attempt_b)["status"] == "answered",
            database.get_emergency_attempt(attempt_b)["status"],
        )

        # ------------------------------------------------------------------
        print("\n[7] Missing attempt_id is a 400, not a crash")
        # ------------------------------------------------------------------
        no_id = client.post(
            "/emergency/vonage/ack",
            data={"dtmf": '{"digits":"1","timed_out":false}'},
            headers={"Authorization": f"Bearer {good}"},
        )
        check("ack without attempt_id -> 400", no_id.status_code == 400, no_id.status_code)
        no_id_event = client.post(
            "/emergency/vonage/event",
            data={"status": "answered"},
            headers={"Authorization": f"Bearer {good}"},
        )
        check("event without attempt_id -> 400", no_id_event.status_code == 400, no_id_event.status_code)

        # ------------------------------------------------------------------
        print("\n[8] An unknown attempt_id changes nothing")
        # ------------------------------------------------------------------
        ghost = client.post(
            "/emergency/vonage/ack?attempt_id=does-not-exist",
            data={"dtmf": '{"digits":"1","timed_out":false}'},
            headers={"Authorization": f"Bearer {good}"},
        )
        check("ghost ack does not 500", ghost.status_code in (200, 400, 404), ghost.status_code)
        check("ghost ack is not acknowledged", ghost.json().get("status") != "acknowledged", ghost.text)

        # ------------------------------------------------------------------
        print("\n[9] No response body or event contains the full number")
        # ------------------------------------------------------------------
        bodies = [
            unsigned.text,
            unsigned_event.text,
            ack.text,
            again.text,
            ringing.text,
            answered.text,
            unknown.text,
            ghost.text,
        ]
        blob = "\n".join(bodies)
        check("the full number never appears", "9876543210" not in blob, "leak")
        check("the masked form is what is stored", MASKED in (database.get_emergency_attempt(attempt_b).get("phone_masked") or ""), "masked")

        attempts = database.get_emergency_attempts(incident_b)
        check(
            "no attempt row carries the raw number",
            all("9876543210" not in str(a) for a in attempts),
            "leak",
        )

        # ------------------------------------------------------------------
        print("\n[10] With the simulated provider there is no signature to check")
        # ------------------------------------------------------------------
        provider_module.get_call_provider = original_factory
        os.environ["SAHAARA_EMERGENCY_PROVIDER"] = "simulated"

        incident_c, attempt_c = _fresh_incident_with_attempt("c")
        sim_ack = client.post(
            f"/emergency/vonage/ack?attempt_id={attempt_c}",
            data={"dtmf": '{"digits":"1","timed_out":false}'},
        )
        check("simulated ack needs no signature", sim_ack.status_code == 200, sim_ack.status_code)
        check(
            "the simulated attempt is acknowledged",
            database.get_emergency_attempt(attempt_c)["status"] == "acknowledged",
            database.get_emergency_attempt(attempt_c)["status"],
        )
    finally:
        provider_module.get_call_provider = original_factory

    print("\n" + "=" * 74)
    print(f"  RESULTS: {passed} passed, {failed} failed")
    print("=" * 74)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
