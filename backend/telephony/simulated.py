"""
telephony/simulated.py

SimulatedCallProvider — the provider used whenever a real call is not allowed.

It places NOTHING. `place_call` returns a single terminal status,
``simulated_not_placed``, carrying the exact gate that stopped the real call.
There is no scripted ``dialing``/``ringing``/``answered`` progression and no
``acknowledge()`` success path, because every one of those was a fabricated
statement about a call that never happened — the dashboard rendered
"Answered at 08:37:03 PM" for a telephone that never rang.

Tests that need a *real* ladder must inject their own fake provider; the
shipped simulated provider must never be able to claim an answer.
"""

from __future__ import annotations

from typing import Optional

from .provider import CallHandle


class SimulatedCallProvider:
    """Honest no-op provider: a call was not placed, and it says so once."""

    name = "simulated"
    simulated = True

    def __init__(self, gate_reason: str = ""):
        # The gate that refused the real call, decided by the factory at call
        # time. Empty only when something constructed this provider without
        # asking the factory, which no product path does.
        self.gate_reason = gate_reason or "no gate recorded"

    def place_call(
        self,
        *,
        to_e164: str,
        message: str,
        incident_id: str,
        attempt_id: str,
        gate_reason: Optional[str] = None,
    ) -> CallHandle:
        reason = gate_reason or self.gate_reason or "no gate recorded"
        return CallHandle(
            provider_call_id=f"sim_{attempt_id}",
            status="simulated_not_placed",
            raw={
                "simulated": True,
                "gate_reason": reason,
                "incident_id": incident_id,
                "attempt_id": attempt_id,
            },
        )

    def fetch_status(self, handle: CallHandle) -> str:
        """A simulated call stays not-placed forever; there is nothing to poll."""
        return "simulated_not_placed"

    def cancel(self, handle: CallHandle) -> None:
        return None

    def acknowledge(self, attempt_id: str) -> bool:
        """No real call exists, so no keypress can ever arrive."""
        return False
