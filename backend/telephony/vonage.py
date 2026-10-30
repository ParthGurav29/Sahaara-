"""
telephony/vonage.py

VonageCallProvider — the real call provider, flag-gated.

Constructed only when the factory is asked for `vonage`; the factory itself is
only asked for it when `SAHAARA_EMERGENCY_PROVIDER=vonage`. Missing config
raises `TelephonyProviderError`, and the factory falls back to the simulated
provider — a misconfigured server can never half-dial.

Verified against the current Vonage Voice API docs (checked 2026-10-29):

* `POST https://api.nexmo.com/v1/calls` with `Authorization: Bearer <JWT>`,
  body `{to:[{type:"phone",number}], from:{type:"phone",number}, ncco:[...],
  event_url:[...], ringing_timer:<seconds>}`.
  https://developer.vonage.com/en/voice/voice-api/code-snippets/make-an-outbound-call
* JWT is RS256 over the application's private key, claims `application_id`,
  `iat`, `jti`, `exp`.
* Call statuses are `started`, `ringing`, `answered`, `busy`, `cancelled`,
  `unanswered`, `disconnected`, `rejected`, `failed`, `timeout`, `completed`
  (plus `human`/`machine` when machine detection is on).
* Webhooks arrive signed: a JWT in the `Authorization: Bearer ...` header,
  verified with the application's public key.
* The DTMF `input` action reports `{"dtmf": {"digits": "1", "timed_out": false}}`.

The provider deliberately speaks from a **fixed** NCCO built here; nothing the
LLM produces can reach the phone.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Optional

from .provider import (
    CallProvider,
    CallHandle,
    TelephonyProviderError,
    parse_dtmf_digits,
)

logger = logging.getLogger(__name__)

VONAGE_API_BASE = "https://api.nexmo.com"

# Vonage call status -> the vocabulary the orchestrator understands.
# Anything not listed here means "still in progress".
_VONAGE_TERMINAL = {
    "busy": "no_answer",
    "unanswered": "no_answer",
    "timeout": "no_answer",
    "rejected": "failed",
    "failed": "failed",
    "cancelled": "canceled",
    "canceled": "canceled",
    "completed": "completed",
    "disconnected": "completed",
}

_VONAGE_ANSWERED = {"answered", "human"}


class VonageCallProvider(CallProvider):
    """
    Vonage Voice API provider.

    Every HTTP call goes through `self._post` / `self._get` / `self._put` so the
    whole provider can be exercised in tests with a fake transport and no
    network access.
    """

    name = "vonage"
    simulated = False

    def __init__(self) -> None:
        self.application_id = os.environ.get("VONAGE_APPLICATION_ID", "").strip()
        self.private_key = os.environ.get("VONAGE_PRIVATE_KEY", "").strip()
        self.from_number = os.environ.get("VONAGE_FROM_NUMBER", "").strip()
        self.public_base_url = os.environ.get(
            "SAHAARA_EMERGENCY_PUBLIC_BASE_URL", ""
        ).strip().rstrip("/")

        missing = [
            name
            for name, value in (
                ("VONAGE_APPLICATION_ID", self.application_id),
                ("VONAGE_PRIVATE_KEY", self.private_key),
                ("VONAGE_FROM_NUMBER", self.from_number),
                ("SAHAARA_EMERGENCY_PUBLIC_BASE_URL", self.public_base_url),
            )
            if not value
        ]
        if missing:
            raise TelephonyProviderError(
                f"Missing Vonage config: {', '.join(missing)}"
            )

        if not self.public_base_url.startswith("https://"):
            # Vonage will not call back to plain HTTP, and an unsigned/unreachable
            # webhook would leave every call in limbo.
            raise TelephonyProviderError(
                "SAHAARA_EMERGENCY_PUBLIC_BASE_URL must be a public HTTPS URL"
            )

        self._private_key_pem = self._normalise_pem(self.private_key)
        self._public_key = self._derive_public_key(self._private_key_pem)

    # ---- key handling -------------------------------------------------

    @staticmethod
    def _normalise_pem(value: str) -> str:
        """
        Accept a PEM block, a base64 body, or a PEM stored on one line with
        literal ``\\n`` escapes (the usual shape in a `.env` file).
        """
        text = value.strip()
        if "\\n" in text and "-----BEGIN" in text:
            text = text.replace("\\n", "\n")
        if text.startswith("-----BEGIN"):
            return text if text.endswith("\n") else text + "\n"
        # A bare base64 body: wrap it as PKCS#8, which is what Vonage issues.
        return (
            "-----BEGIN PRIVATE KEY-----\n"
            + text.replace("\n", "").strip()
            + "\n-----END PRIVATE KEY-----\n"
        )

    @staticmethod
    def _derive_public_key(private_key_pem: str):
        """Derive the public key used to verify signed webhooks."""
        try:
            from cryptography.hazmat.primitives import serialization

            key = serialization.load_pem_private_key(
                private_key_pem.encode(), password=None
            )
            return key.public_key()
        except Exception as exc:  # noqa: BLE001 - config problem, not a crash
            raise TelephonyProviderError(
                f"Invalid VONAGE_PRIVATE_KEY ({type(exc).__name__})"
            ) from exc

    def _auth_headers(self) -> dict:
        """Build the short-lived RS256 JWT Vonage requires on every request."""
        try:
            import jwt
        except ImportError as exc:  # pragma: no cover - dependency guard
            raise TelephonyProviderError("PyJWT is not installed") from exc

        now = int(time.time())
        token = jwt.encode(
            {
                "application_id": self.application_id,
                "iat": now,
                "exp": now + 300,
                "jti": f"{self.application_id}-{now}",
            },
            self._private_key_pem,
            algorithm="RS256",
        )
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    # ---- HTTP transport (overridden in tests) -------------------------

    def _request(self, method: str, path: str, payload: Optional[dict] = None) -> dict:
        import requests

        url = f"{VONAGE_API_BASE}{path}"
        response = requests.request(
            method,
            url,
            json=payload,
            headers=self._auth_headers(),
            timeout=10,
        )
        if response.status_code >= 400:
            # Never echo the body wholesale: it can contain the destination
            # number. The status code is enough to act on.
            raise TelephonyProviderError(
                f"Vonage {method} {path} failed with HTTP {response.status_code}"
            )
        try:
            return response.json() or {}
        except ValueError:
            return {}

    # ---- CallProvider surface -----------------------------------------

    def build_ncco(self, message: str, attempt_id: str) -> list:
        """
        The fixed call script.

        `talk` says the message, then repeats it once, then an `input` action
        listens for a keypress so a caregiver can acknowledge, and a closing
        `talk` thanks them. No medical claims, no LLM text.
        """
        voice = os.environ.get("VONAGE_TTS_VOICE", "Salli")

        def talk(text: str) -> dict:
            return {
                "action": "talk",
                "text": text,
                "voiceName": voice,
                "language": "en-IN",
            }

        return [
            talk(message),
            talk(message),
            {
                "action": "input",
                "type": ["dtmf"],
                "dtmf": {
                    "maxDigits": 1,
                    "timeOut": int(os.environ.get("VONAGE_DTMF_TIMEOUT", "10")),
                    "submitOnHash": True,
                },
                "eventUrl": [
                    f"{self.public_base_url}/emergency/vonage/ack?attempt_id={attempt_id}"
                ],
                "eventMethod": "POST",
            },
            talk("Thank you. This message will now end."),
        ]

    def place_call(
        self,
        *,
        to_e164: str,
        message: str,
        incident_id: str,
        attempt_id: str,
    ) -> CallHandle:
        payload = {
            "to": [{"type": "phone", "number": to_e164}],
            "from": {"type": "phone", "number": self.from_number},
            "ncco": self.build_ncco(message, attempt_id),
            "event_url": [
                f"{self.public_base_url}/emergency/vonage/event?attempt_id={attempt_id}"
            ],
            "event_method": "POST",
            "ringing_timer": int(os.environ.get("VONAGE_RINGING_TIMER", "45")),
        }

        data = self._request("POST", "/v1/calls", payload)
        call_uuid = data.get("uuid") or data.get("conversation_uuid")
        if not call_uuid:
            raise TelephonyProviderError("Vonage accepted the request but returned no call id")

        return CallHandle(
            provider_call_id=call_uuid,
            status="queued",
            raw={
                "attempt_id": attempt_id,
                "incident_id": incident_id,
                # The raw number is kept in memory only; nothing logs this dict.
                "to": to_e164,
            },
        )

    def fetch_status(self, handle: CallHandle) -> str:
        try:
            data = self._request("GET", f"/v1/calls/{handle.provider_call_id}")
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Vonage status fetch failed for %s (%s)",
                handle.provider_call_id,
                type(exc).__name__,
            )
            return "unknown"

        status = str(data.get("status", "")).lower()
        if not status:
            return "unknown"
        if status in _VONAGE_ANSWERED:
            return "answered"
        if status == "ringing":
            return "ringing"
        if status == "started":
            return "dialing"
        mapped = _VONAGE_TERMINAL.get(status)
        if mapped:
            # A call that was answered and is now finished is still an answer,
            # not a miss — the orchestrator decides from its own state.
            if mapped == "completed" and data.get("start_time"):
                return "answered"
            return mapped
        return "unknown"

    def cancel(self, handle: CallHandle) -> None:
        try:
            self._request(
                "PUT",
                f"/v1/calls/{handle.provider_call_id}",
                {"action": "hangup"},
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Vonage hangup failed for %s (%s)",
                handle.provider_call_id,
                type(exc).__name__,
            )

    def available(self) -> bool:
        return True

    # ---- Webhook helpers ----------------------------------------------

    def validate_webhook_signature(self, authorization_header: str) -> bool:
        """
        Verify a signed Vonage webhook.

        Vonage sends `Authorization: Bearer <JWT>` signed with the application's
        private key. We verify with the matching public key and require the
        `application_id` claim to be exactly ours, so a token minted for a
        different Vonage application — or one with no application claim at all —
        cannot drive our incidents.
        """
        if not authorization_header or not authorization_header.lower().startswith("bearer "):
            return False
        token = authorization_header.split(" ", 1)[1].strip()
        if not token:
            return False
        try:
            import jwt

            claims = jwt.decode(
                token,
                self._public_key,
                algorithms=["RS256"],
                options={"verify_aud": False},
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Vonage webhook signature rejected (%s)", type(exc).__name__)
            return False

        return claims.get("application_id") == self.application_id

    @staticmethod
    def parse_dtmf_webhook(payload: dict) -> Optional[str]:
        """Return the digit pressed, or None when nothing was pressed.

        Vonage posts the DTMF result as a URL-encoded **JSON string** in the
        `dtmf` field, e.g. ``dtmf={"digits":"1","timed_out":false}`` — not as a
        nested object. Reading only the object form meant a genuine "press 1"
        arrived as the literal string and never acknowledged anything, so the
        shared parser handles both shapes.
        """
        return parse_dtmf_digits(payload)

    @staticmethod
    def parse_status_webhook(payload: dict) -> Optional[str]:
        """Map an event-webhook `status` onto the orchestrator vocabulary."""
        if not isinstance(payload, dict):
            return None
        status = str(payload.get("status", "")).lower()
        if not status:
            return None
        if status in _VONAGE_ANSWERED:
            return "answered"
        if status == "ringing":
            return "ringing"
        if status == "started":
            return "dialing"
        mapped = _VONAGE_TERMINAL.get(status)
        if mapped:
            if mapped == "completed" and payload.get("start_time"):
                return "answered"
            return mapped
        return None


def verify_vonage_webhook_signature(authorization_header: str) -> bool:
    """
    Verify a callback using ONLY our application id and private key.

    Deliberately independent of which provider the factory would select right
    now: a callback is genuine or it is not. Missing/blank signatures, a
    missing application id or key, a token signed by another key, and a token
    minted for another application all return False. Never raises.

    This is what protects the webhook routes from fabricating an
    acknowledgement: with no signature there is nothing to trust, and the
    simulated provider no longer opens a hole ("no provider, no check").
    """
    application_id = os.environ.get("VONAGE_APPLICATION_ID", "").strip()
    private_key = os.environ.get("VONAGE_PRIVATE_KEY", "").strip()
    if not application_id or not private_key:
        return False

    header = (authorization_header or "").strip()
    if not header.lower().startswith("bearer "):
        return False
    token = header.split(" ", 1)[1].strip()
    if not token:
        return False

    try:
        import jwt

        public_key = VonageCallProvider._derive_public_key(
            VonageCallProvider._normalise_pem(private_key)
        )
        claims = jwt.decode(
            token,
            public_key,
            algorithms=["RS256"],
            options={"verify_aud": False},
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Vonage webhook signature rejected (%s)", type(exc).__name__)
        return False

    return claims.get("application_id") == application_id
