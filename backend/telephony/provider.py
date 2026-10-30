"""
telephony/provider.py

Provider interface for emergency calls — mirrors llm/provider.py pattern.
Only this module knows about concrete providers (Simulated, Vonage).

Twilio was abandoned on 2026-10-29 (a trial account could not place calls) and
its provider module is deleted: there must be exactly ONE real provider route,
so nobody can flip an env var and dial through a code path we do not test.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Protocol, Optional
import time


@dataclass
class CallHandle:
    """Opaque handle returned by place_call, used for fetch_status/cancel."""
    provider_call_id: str
    status: str
    raw: dict


class CallProvider(Protocol):
    """Structural interface for call providers."""

    name: str
    simulated: bool

    def place_call(
        self,
        *,
        to_e164: str,
        message: str,
        incident_id: str,
        attempt_id: str,
    ) -> CallHandle:
        ...

    def fetch_status(self, handle: CallHandle) -> str:
        ...

    def cancel(self, handle: CallHandle) -> None:
        ...


class TelephonyProviderError(RuntimeError):
    """Raised when a provider cannot place a call (missing config, network error, etc.)."""
    pass


def parse_dtmf_digits(payload: dict) -> Optional[str]:
    """Return the digit pressed in a DTMF webhook, whatever shape it arrives in.

    Providers differ: some post a nested object, some post a URL-encoded JSON
    string (Vonage does: ``dtmf={"digits":"1","timed_out":false}``), some
    flatten it to a bare ``digits`` field. A DTMF timeout is never a press.
    This lives here, not in one provider, so the simulated provider can also be
    acknowledged through the same webhook in a demo.
    """
    if not isinstance(payload, dict):
        return None

    dtmf = payload.get("dtmf")
    if isinstance(dtmf, str) and dtmf.strip()[:1] in "{[":
        try:
            dtmf = json.loads(dtmf)
        except Exception:  # noqa: BLE001
            dtmf = None

    if isinstance(dtmf, dict):
        if dtmf.get("timed_out"):
            return None
        digits = dtmf.get("digits")
        return str(digits) if digits else None
    if isinstance(dtmf, str) and dtmf.strip():
        return dtmf.strip()

    digits = payload.get("digits") or payload.get("dtmf_digits")
    if digits:
        if str(payload.get("timed_out", "")).lower() == "true":
            return None
        return str(digits)
    return None


def get_call_provider() -> CallProvider:
    """
    Factory: returns the configured call provider.
    Selection logic:

    EMERGENCY_CALLS_ENABLED=false            -> SimulatedCallProvider (disabled mode)
    EMERGENCY_CALLS_ENABLED=true, simulated  -> SimulatedCallProvider
    EMERGENCY_CALLS_ENABLED=true, vonage     -> VonageCallProvider (needs secrets)
    """
    import os

    enabled = os.environ.get("EMERGENCY_CALLS_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}
    provider_name = os.environ.get("SAHAARA_EMERGENCY_PROVIDER", "simulated").strip().lower()

    if not enabled:
        # Disabled mode: simulated provider that immediately fails with "disabled" reason
        from .simulated import SimulatedCallProvider
        return SimulatedCallProvider(disabled=True)

    if provider_name == "vonage":
        # Try to construct Vonage provider; fall back to simulated if config missing
        try:
            from .vonage import VonageCallProvider
            return VonageCallProvider()
        except TelephonyProviderError as e:
            # Log and fall back — never silently do nothing, never claim a real call
            import logging
            logging.getLogger(__name__).warning(
                "Vonage provider unavailable, falling back to simulated: %s", e
            )
            from .simulated import SimulatedCallProvider
            return SimulatedCallProvider()

    # Default: simulated
    from .simulated import SimulatedCallProvider
    return SimulatedCallProvider()


def can_place_real_call(profile_id: str, contact: dict) -> tuple[bool, str]:
    """
    Check if a real call can be placed (all gates pass).
    Returns (allowed, reason_if_not_allowed).
    """
    import os

    # Gate 1: EMERGENCY_CALLS_ENABLED
    if os.environ.get("EMERGENCY_CALLS_ENABLED", "false").strip().lower() not in {"1", "true", "yes", "on"}:
        return False, "EMERGENCY_CALLS_ENABLED is not true"

    # Gate 2: SAHAARA_EMERGENCY_PROVIDER must be vonage
    provider = os.environ.get("SAHAARA_EMERGENCY_PROVIDER", "simulated").strip().lower()
    if provider != "vonage":
        return False, f"provider '{provider}' does not support real calls"

    # Gate 3: SAHAARA_EMERGENCY_REAL_CALLS_CONFIRMED
    if os.environ.get("SAHAARA_EMERGENCY_REAL_CALLS_CONFIRMED", "false").strip().lower() not in {"1", "true", "yes", "on"}:
        return False, "SAHAARA_EMERGENCY_REAL_CALLS_CONFIRMED is not true"

    # Gate 4: Contact must have valid E.164 phone and may_call=true
    phone = contact.get("phone", "").strip()
    if not phone:
        return False, "contact has no phone number"
    if not phone.startswith("+") or len(phone) < 10:
        return False, "contact phone is not valid E.164"
    if not contact.get("may_call", False):
        return False, "contact may_call is false"

    # Gate 5: ALLOWED_TEST_NUMBERS (optional - if set, only those numbers allowed)
    allowed_numbers = os.environ.get("ALLOWED_TEST_NUMBERS", "").strip()
    if allowed_numbers:
        allowed_list = [n.strip() for n in allowed_numbers.split(",") if n.strip()]
        if phone not in allowed_list:
            # Never put the full number in a reason string: it is logged and
            # returned to callers. The masked form is the only safe form.
            from database import mask_phone_number
            return False, f"phone {mask_phone_number(phone)} not in ALLOWED_TEST_NUMBERS"

    return True, ""