"""
telephony/simulated.py

SimulatedCallProvider — deterministic, no network, for demos and tests.
Scripted progression per spec §8.1:
  dialing (1s) -> ringing (3s) -> answered -> acknowledged (if DEMO_ACK=1) 
  or no_answer after ack window.
"""

import os
import time
import threading
from dataclasses import dataclass
from typing import Optional

from .provider import CallProvider, CallHandle


TRUTHY = {"1", "true", "yes", "on"}


def _flag(value: Optional[str]) -> bool:
    return bool(value) and value.strip().lower() in TRUTHY


class SimulatedCallProvider:
    """Deterministic simulated provider. No network calls."""

    name = "simulated"
    simulated = True
    # Only the simulated provider can report an acknowledgement without a
    # webhook. It does so only when SAHAARA_EMERGENCY_DEMO_ACK is on, so the
    # demo can show the retry ladder as well as the happy path.
    auto_ack = False

    def __init__(self, disabled: bool = False):
        self.disabled = disabled
        self.ack_seconds = float(os.environ.get("SAHAARA_EMERGENCY_ACK_SECONDS", "45"))
        self.demo_ack = _flag(os.environ.get("SAHAARA_EMERGENCY_DEMO_ACK", "0"))
        self.auto_ack = self.demo_ack
        # Simulation-only pacing. These do not exist for real calls — a real
        # call is paced by the carrier. Tests set them very small so the whole
        # retry ladder runs in well under a second.
        self.dial_seconds = float(os.environ.get("SAHAARA_EMERGENCY_SIM_DIAL_SECONDS", "1.0"))
        self.ring_seconds = float(os.environ.get("SAHAARA_EMERGENCY_SIM_RING_SECONDS", "3.0"))
        self._lock = threading.Lock()
        self._handles: dict[str, dict] = {}  # attempt_id -> handle state

    def place_call(
        self,
        *,
        to_e164: str,
        message: str,
        incident_id: str,
        attempt_id: str,
        gate_reason: str = "simulated",
    ) -> CallHandle:
        if self.disabled:
            # Immediately return a "failed" handle with disabled reason
            handle = CallHandle(
                provider_call_id=f"sim_disabled_{attempt_id}",
                status="failed",
                raw={"reason": "disabled", "to": to_e164, "incident_id": incident_id},
            )
            return handle

        # Scripted progression: dialing -> ringing -> answered -> (acknowledged | no_answer)
        now = time.time()
        handle_state = {
            "provider_call_id": f"sim_{attempt_id}",
            "status": "dialing",
            "to_e164": to_e164,
            "message": message,
            "incident_id": incident_id,
            "attempt_id": attempt_id,
            "started_at": now,
            "dialing_until": now + self.dial_seconds,
            "ringing_until": now + self.dial_seconds + self.ring_seconds,
            "acked": False,
            "gate_reason": gate_reason,
        }
        with self._lock:
            self._handles[attempt_id] = handle_state

        # Start background progression
        threading.Thread(target=self._run_progression, args=(attempt_id,), daemon=True).start()

        return CallHandle(
            provider_call_id=handle_state["provider_call_id"],
            status="dialing",
            raw={"simulated": True, "stage": "dialing", "gate_reason": gate_reason},
        )

    def _run_progression(self, attempt_id: str) -> None:
        """Background thread: dialing -> ringing -> answered -> acknowledged/no_answer"""
        while True:
            with self._lock:
                state = self._handles.get(attempt_id)
                if not state:
                    return
                now = time.time()

                if state["status"] == "dialing" and now >= state["dialing_until"]:
                    state["status"] = "ringing"
                elif state["status"] == "ringing" and now >= state["ringing_until"]:
                    state["status"] = "answered"
                    state["answered_at"] = now
                    # If demo ack is enabled, auto-acknowledge after a short
                    # delay. The provider sets `acked` itself: relying on the
                    # orchestrator to poll `acknowledge()` inside a 0.5 s window
                    # raced against this provider's own no-answer deadline.
                    if self.demo_ack:
                        state["ack_deadline"] = now + 0.5
                        state["auto_ack_at"] = state["ack_deadline"]
                    else:
                        state["ack_deadline"] = now + self.ack_seconds

                elif state["status"] == "answered":
                    if state.get("auto_ack_at") and now >= state["auto_ack_at"]:
                        state["acked"] = True
                    if state.get("acked"):
                        state["status"] = "acknowledged"
                        state["acknowledged_at"] = time.time()
                        state["ended_at"] = state["acknowledged_at"]
                        return
                    if now >= state.get("ack_deadline", float("inf")):
                        state["status"] = "no_answer"
                        state["ended_at"] = now
                        return

                elif state["status"] in ("acknowledged", "no_answer", "failed", "canceled"):
                    return

            time.sleep(0.1)

    def fetch_status(self, handle: CallHandle) -> str:
        """Return current status of the call."""
        with self._lock:
            state = self._handles.get(handle.provider_call_id.replace("sim_", "") + "")
            # The attempt_id is the last part of provider_call_id
            attempt_id = handle.provider_call_id.replace("sim_", "")
            state = self._handles.get(attempt_id)
            if not state:
                return "unknown"
            return state["status"]

    def cancel(self, handle: CallHandle) -> None:
        attempt_id = handle.provider_call_id.replace("sim_", "")
        with self._lock:
            if attempt_id in self._handles:
                self._handles[attempt_id]["status"] = "canceled"
                self._handles[attempt_id]["ended_at"] = time.time()

    def acknowledge(self, attempt_id: str) -> bool:
        """External ack (DTMF 1 from webhook or dashboard). Returns True if was in answered state."""
        with self._lock:
            state = self._handles.get(attempt_id)
            if not state:
                return False
            if state["status"] == "answered":
                state["acked"] = True
                return True
            return False