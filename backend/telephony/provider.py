"""
telephony/provider.py

Provider interface for emergency calls — mirrors llm/provider.py pattern.
Only this module knows about concrete providers (Simulated, Twilio).
"""

from __future__ import annotations

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


def get_call_provider() -> CallProvider:
    """
    Factory: returns the configured call provider.
    Selection logic mirrors the spec §8.3:

    EMERGENCY_CALLS_ENABLED=false            -> SimulatedCallProvider (disabled mode)
    EMERGENCY_CALLS_ENABLED=true, simulated  -> SimulatedCallProvider
    EMERGENCY_CALLS_ENABLED=true, twilio     -> TwilioCallProvider (needs secrets)
    """
    import os

    enabled = os.environ.get("EMERGENCY_CALLS_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}
    provider_name = os.environ.get("SAHAARA_EMERGENCY_PROVIDER", "simulated").strip().lower()

    if not enabled:
        # Disabled mode: simulated provider that immediately fails with "disabled" reason
        from .simulated import SimulatedCallProvider
        return SimulatedCallProvider(disabled=True)

    if provider_name == "twilio":
        # Try to construct Twilio provider; fall back to simulated if config missing
        try:
            from .twilio import TwilioCallProvider
            return TwilioCallProvider()
        except TelephonyProviderError as e:
            # Log and fall back — never silently do nothing, never claim a real call
            import logging
            logging.getLogger(__name__).warning(
                "Twilio provider unavailable, falling back to simulated: %s", e
            )
            from .simulated import SimulatedCallProvider
            return SimulatedCallProvider()

    # Default: simulated
    from .simulated import SimulatedCallProvider
    return SimulatedCallProvider()