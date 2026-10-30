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
import logging
import os
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
        gate_reason: str = "",
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


# ---- Gates ---------------------------------------------------------------
#
# A real phone call is allowed only when EVERY gate below passes. The gates are
# evaluated in this order at call time (never at import time), and the FIRST
# failure is the sentence stored on the incident and shown on the dashboard.
# One evaluator feeds the trigger path, the factory, GET /emergency/status and
# the startup log, so those four can never disagree about why a call did not
# happen.

TRUTHY = {"1", "true", "yes", "on"}

GATE_CALLS_ENABLED = "EMERGENCY_CALLS_ENABLED"
GATE_PROVIDER = "SAHAARA_EMERGENCY_PROVIDER"
GATE_REAL_CONFIRMED = "SAHAARA_EMERGENCY_REAL_CALLS_CONFIRMED"
GATE_VONAGE_APPLICATION_ID = "VONAGE_APPLICATION_ID"
GATE_VONAGE_PRIVATE_KEY = "VONAGE_PRIVATE_KEY"
GATE_VONAGE_FROM_NUMBER = "VONAGE_FROM_NUMBER"
GATE_PUBLIC_BASE_URL = "SAHAARA_EMERGENCY_PUBLIC_BASE_URL"
GATE_VONAGE_API_KEY = "VONAGE_API_KEY"
GATE_VONAGE_API_SECRET = "VONAGE_API_SECRET"
GATE_CONTACT_PHONE = "caregiver_contact_phone"
GATE_CONTACT_MAY_CALL = "caregiver_contact_may_call"
GATE_ALLOWED_NUMBERS = "ALLOWED_TEST_NUMBERS"


@dataclass(frozen=True)
class GateResult:
    """One gate, evaluated now.

    `detail` is safe to log and to render. Credential gates report only
    "is set" / "is unset"; phone numbers are masked; nothing secret is ever
    included in the text.
    """

    name: str
    passed: bool
    detail: str
    required: bool = True
    secret: bool = False

    def line(self) -> str:
        """The one-sentence form: ``EMERGENCY_CALLS_ENABLED is false``."""
        return f"{self.name} {self.detail}"

    def as_dict(self) -> dict:
        return {
            "gate": self.name,
            "passed": self.passed,
            "detail": self.detail,
            "required": self.required,
            "secret": self.secret,
        }


def _flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in TRUTHY


def _is_set(name: str) -> bool:
    return bool(os.environ.get(name, "").strip())


def _validate_vonage_private_key(key: str) -> tuple[bool, str]:
    """Try to parse the private key with cryptography. Returns (ok, detail)."""
    if not key or not key.strip():
        return False, "is unset"
    try:
        from cryptography.hazmat.primitives import serialization
        # Handle both actual PEM with newlines and escaped newlines in .env
        pem = key.strip().replace(chr(92) + "n", chr(10))
        serialization.load_pem_private_key(pem.encode(), password=None)
        return True, "is set and parses"
    except Exception as e:
        return False, f"is set but unparseable ({type(e).__name__})"

def configured_provider_name() -> str:
    """The provider requested in the environment right now (not at import)."""
    return os.environ.get("SAHAARA_EMERGENCY_PROVIDER", "simulated").strip().lower() or "simulated"


def _mask(number: str) -> str:
    """Masked form for anything a caregiver can see. Never raises."""
    try:
        from database import mask_phone_number

        return mask_phone_number(number)
    except Exception:  # noqa: BLE001 - masking must never break a status read
        digits = "".join(ch for ch in str(number) if ch.isdigit())
        return f"**** {digits[-4:]}" if digits else "(number)"


def evaluate_gates(
    contact: Optional[dict] = None,
    *,
    include_contact: bool = True,
) -> list[GateResult]:
    """Evaluate every gate in order. Pure read of the environment + profile."""
    provider = configured_provider_name()
    base_url = os.environ.get(GATE_PUBLIC_BASE_URL, "").strip().rstrip("/")
    from_number = os.environ.get(GATE_VONAGE_FROM_NUMBER, "").strip()
    api_key_set = _is_set(GATE_VONAGE_API_KEY)
    api_secret_set = _is_set(GATE_VONAGE_API_SECRET)

    key = os.environ.get(GATE_VONAGE_PRIVATE_KEY, "")
    key_ok, key_detail = _validate_vonage_private_key(key)

    gates = [
        GateResult(
            GATE_CALLS_ENABLED,
            _flag(GATE_CALLS_ENABLED),
            "is true" if _flag(GATE_CALLS_ENABLED) else "is false",
        ),
        GateResult(
            GATE_PROVIDER,
            provider == "vonage",
            'is "vonage"' if provider == "vonage" else f'is "{provider}" (expected "vonage")',
        ),
        GateResult(
            GATE_REAL_CONFIRMED,
            _flag(GATE_REAL_CONFIRMED),
            "is true" if _flag(GATE_REAL_CONFIRMED) else "is false",
        ),
        GateResult(
            GATE_VONAGE_APPLICATION_ID,
            _is_set(GATE_VONAGE_APPLICATION_ID),
            "is set" if _is_set(GATE_VONAGE_APPLICATION_ID) else "is unset",
        ),
        GateResult(
            GATE_VONAGE_PRIVATE_KEY,
            key_ok,
            key_detail,
            secret=True,
        ),
        GateResult(
            GATE_VONAGE_FROM_NUMBER,
            bool(from_number),
            f"is set ({_mask(from_number)})" if from_number else "is unset",
        ),
        GateResult(
            GATE_PUBLIC_BASE_URL,
            bool(base_url) and base_url.startswith("https://"),
            (
                "is set"
                if base_url.startswith("https://")
                else ("is not an https URL" if base_url else "is unset")
            ),
        ),
        # Not needed for the Voice API (JWT auth), reported so an operator can
        # see whether the Vonage account credentials are present at all.
        GateResult(
            GATE_VONAGE_API_KEY,
            api_key_set,
            "is set" if api_key_set else "is unset",
            required=False,
            secret=True,
        ),
        GateResult(
            GATE_VONAGE_API_SECRET,
            api_secret_set,
            "is set" if api_secret_set else "is unset",
            required=False,
            secret=True,
        ),
    ]

    if not include_contact:
        return gates

    phone = str((contact or {}).get("phone") or "").strip()
    name = str((contact or {}).get("name") or "").strip() or "the caregiver"

    if contact is None:
        gates.append(
            GateResult(GATE_CONTACT_PHONE, False, "no caregiver has a dialable number")
        )
        gates.append(
            GateResult(GATE_CONTACT_MAY_CALL, False, "no caregiver has may_call enabled")
        )
        return gates

    valid_e164 = bool(phone) and phone.startswith("+") and len(phone) >= 10
    gates.append(
        GateResult(
            GATE_CONTACT_PHONE,
            valid_e164,
            (
                f"is set for {name} ({_mask(phone)})"
                if valid_e164
                else (f"{name} has no phone number" if not phone else f"{name}'s number is not E.164")
            ),
        )
    )
    may_call = bool(contact.get("may_call", False))
    gates.append(
        GateResult(
            GATE_CONTACT_MAY_CALL,
            may_call,
            f"is true for {name}" if may_call else f"is false for {name}",
        )
    )

    allowed_raw = os.environ.get(GATE_ALLOWED_NUMBERS, "").strip()
    allowed = [n.strip() for n in allowed_raw.split(",") if n.strip()]
    if not allowed:
        gates.append(
            GateResult(
                GATE_ALLOWED_NUMBERS,
                True,
                f"is unset — no number allow-list ({_mask(phone)})",
                required=False,
            )
        )
    else:
        gates.append(
            GateResult(
                GATE_ALLOWED_NUMBERS,
                phone in allowed,
                (
                    f"contains {_mask(phone)}"
                    if phone in allowed
                    else f"does not contain {_mask(phone)}"
                ),
            )
        )
    return gates


def first_failure(gates: list[GateResult]) -> Optional[GateResult]:
    """The gate that stops the call, or None when every required gate passed."""
    for gate in gates:
        if gate.required and not gate.passed:
            return gate
    return None


def gate_reason(gates: list[GateResult]) -> str:
    """One sentence naming the first failing gate ('' when all pass)."""
    failure = first_failure(gates)
    return failure.line() if failure else ""


def can_place_real_call(profile_id: str, contact: dict) -> tuple[bool, str]:
    """
    Check whether a real call may be placed for this contact, right now.
    Returns (allowed, reason_if_not_allowed); the reason names the exact gate.
    """
    reason = gate_reason(evaluate_gates(contact))
    return (reason == "", reason)


def select_provider() -> tuple[CallProvider, Optional[str]]:
    """Return (provider, gate_reason).

    `gate_reason` is None only when a real provider was actually constructed.
    Flags are read here, on every call — never captured at import time, so a
    corrected `.env` takes effect on the next trigger without a restart.
    """
    provider_gates = evaluate_gates(include_contact=False)
    failure = first_failure(provider_gates)
    if failure is not None:
        from .simulated import SimulatedCallProvider

        reason = failure.line()
        return SimulatedCallProvider(gate_reason=reason), reason

    try:
        from .vonage import VonageCallProvider

        return VonageCallProvider(), None
    except TelephonyProviderError as exc:
        # Never silently do nothing, never claim a real call was possible.
        logging.getLogger(__name__).warning(
            "Vonage provider unavailable, falling back to simulated: %s", exc
        )
        reason = f"VONAGE configuration is unusable ({type(exc).__name__})"
        from .simulated import SimulatedCallProvider

        return SimulatedCallProvider(gate_reason=reason), reason


def get_call_provider() -> CallProvider:
    """Factory: the provider to use for the next call attempt."""
    return select_provider()[0]


def verify_webhook_signature(authorization_header: str) -> bool:
    """
    True only for a callback signed by OUR Vonage application.

    There is deliberately no "no provider configured, so allow it" branch. An
    unverifiable callback must never move an attempt — accepting one would let
    anyone acknowledge a call that a caregiver never answered.
    """
    try:
        from .vonage import verify_vonage_webhook_signature

        return bool(verify_vonage_webhook_signature(authorization_header))
    except Exception:  # noqa: BLE001 - a failed check is a refusal, never a crash
        return False


def emergency_status(contact: Optional[dict]) -> dict:
    """The full gate table for the caregiver dashboard and the startup log.

    Reads the environment at call time. Never dials and never returns a secret.
    """
    provider, provider_reason = select_provider()
    gates = evaluate_gates(contact)
    failure = first_failure(gates)
    return {
        "provider_requested": configured_provider_name(),
        "provider_selected": provider.name,
        "simulated": bool(provider.simulated),
        "real_calls_allowed": provider_reason is None and failure is None,
        "gate_reason": failure.line() if failure else None,
        "gates": [g.as_dict() for g in gates],
    }


def status_line(contact: Optional[dict]) -> str:
    """One log line summarising every gate. Same facts as /emergency/status."""
    status = emergency_status(contact)
    verdict = "PASS" if status["real_calls_allowed"] else "BLOCKED"
    parts = [
        f"{g['gate']}={'PASS' if g['passed'] else 'FAIL'}"
        for g in status["gates"]
        if g["required"]
    ]
    return (
        f"emergency calls: {verdict} provider={status['provider_selected']} "
        + " ".join(parts)
    )
