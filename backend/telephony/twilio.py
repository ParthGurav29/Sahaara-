"""
telephony/twilio.py

TwilioCallProvider — optional, flag-gated.
Constructed only when EMERGENCY_CALLS_ENABLED=true AND SAHAARA_EMERGENCY_PROVIDER=twilio
AND all required secrets are present. Otherwise raises TelephonyProviderError
and the factory falls back to simulated.
"""

import os
import time
import logging
from typing import Optional

from .provider import CallProvider, CallHandle, TelephonyProviderError

logger = logging.getLogger(__name__)


class TwilioCallProvider:
    name = "twilio"
    simulated = False
    # Real acknowledgements arrive through the DTMF webhook, never by polling.
    auto_ack = False

    def __init__(self):
        # All required config for Twilio
        self.account_sid = os.environ.get("TWILIO_ACCOUNT_SID", "").strip()
        self.api_key = os.environ.get("TWILIO_API_KEY", "").strip()
        self.api_secret = os.environ.get("TWILIO_API_SECRET", "").strip()
        self.auth_token = os.environ.get("TWILIO_AUTH_TOKEN", "").strip()
        self.from_number = os.environ.get("TWILIO_FROM_NUMBER", "").strip()
        self.public_base_url = os.environ.get("SAHAARA_EMERGENCY_PUBLIC_BASE_URL", "").strip()

        missing = []
        if not self.account_sid:
            missing.append("TWILIO_ACCOUNT_SID")
        if not self.api_key:
            missing.append("TWILIO_API_KEY")
        if not self.api_secret:
            missing.append("TWILIO_API_SECRET")
        if not self.auth_token:
            missing.append("TWILIO_AUTH_TOKEN")
        if not self.from_number:
            missing.append("TWILIO_FROM_NUMBER")
        if not self.public_base_url:
            missing.append("SAHAARA_EMERGENCY_PUBLIC_BASE_URL")

        if missing:
            raise TelephonyProviderError(f"Missing Twilio config: {', '.join(missing)}")

        # Import twilio here so it's not a hard dependency
        try:
            from twilio.rest import Client
            from twilio.request_validator import RequestValidator
        except ImportError:
            raise TelephonyProviderError("twilio package not installed")

        # Try API Key auth first; fall back to Account SID + Auth Token
        # "actor doesn't have any assertions" = API Key lacks perms or wrong account
        try:
            self._client = Client(self.api_key, self.api_secret, self.account_sid)
            # Test the client with a lightweight fetch
            self._client.api.accounts(self.account_sid).fetch()
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(
                "API Key auth failed (%s), falling back to Account SID + Auth Token", e
            )
            self._client = Client(self.account_sid, self.auth_token)
        self._validator = RequestValidator(self.auth_token)

    def place_call(
        self,
        *,
        to_e164: str,
        message: str,
        incident_id: str,
        attempt_id: str,
    ) -> CallHandle:
        """Place a call with TwiML message and status callbacks."""
        from twilio.twiml.voice_response import VoiceResponse, Gather, Say

        # Build TwiML per spec §9.2
        response = VoiceResponse()
        gather = Gather(
            input="dtmf",
            num_digits=1,
            action=f"{self.public_base_url}/emergency/twilio/ack?attempt_id={attempt_id}",
            method="POST",
            timeout=15,
        )
        gather.say(message, voice="woman", language="en-IN")
        response.append(gather)
        # Fallback if no input
        response.say("Goodbye.", voice="woman", language="en-IN")

        # Place the call
        call = self._client.calls.create(
            to=to_e164,
            from_=self.from_number,
            twiml=str(response),
            status_callback=f"{self.public_base_url}/emergency/twilio/status?attempt_id={attempt_id}",
            status_callback_event=["initiated", "ringing", "answered", "completed"],
            status_callback_method="POST",
            timeout=25,  # Ring timeout
        )

        return CallHandle(
            provider_call_id=call.sid,
            status=call.status,
            raw={
                "to": to_e164,
                "from": self.from_number,
                "incident_id": incident_id,
                "attempt_id": attempt_id,
            },
        )

    def fetch_status(self, handle: CallHandle) -> str:
        """Fetch call status from Twilio."""
        call = self._client.calls(handle.provider_call_id).fetch()
        return call.status

    def cancel(self, handle: CallHandle) -> None:
        """Cancel/hang up a call."""
        try:
            self._client.calls(handle.provider_call_id).update(status="canceled")
        except Exception as e:
            logger.warning("Failed to cancel Twilio call %s: %s", handle.provider_call_id, e)

    def validate_webhook_signature(self, url: str, params: dict, signature: str) -> bool:
        """Validate X-Twilio-Signature."""
        return self._validator.validate(url, params, signature)