"""
telephony/orchestrator.py

Emergency call orchestrator — deterministic state machine (no LLM).
Triggered by urgent/danger turns; runs as background daemon thread.

State machine per spec §7:
  triggered -> dialing -> ringing -> answered -> acknowledged | no_answer
    -> retry same (attempt < 2) -> next contact (attempt < 3) -> exhausted

Cooldown/dedup per spec §7: 10 min cooldown, max 3 calls per incident.
Cooldown applies ONLY to the phone campaign — dashboard alert + Red tier still raised.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Optional, List

from database import (
    create_emergency_incident,
    get_emergency_incident,
    update_emergency_incident_status,
    increment_emergency_incident_attempts,
    create_emergency_attempt,
    update_emergency_attempt_status,
    get_emergency_attempts,
    get_open_emergency_incident,
    get_recent_emergency_incident_in_cooldown,
    mask_phone_number,
    get_connection,
)
from telephony.provider import get_call_provider, CallHandle
from memory.profile import get_callable_contacts, Profile
from events.event_log import EventLog, EventType, Event
from safety.escalation import EscalationTier

logger = logging.getLogger(__name__)


# ---- Configuration (with env overrides) ----

def _ack_seconds() -> float:
    return float(os.environ.get("SAHAARA_EMERGENCY_ACK_SECONDS", "45"))

def _ring_seconds() -> float:
    return float(os.environ.get("SAHAARA_EMERGENCY_RING_SECONDS", "25"))

def _retry_delay_seconds() -> float:
    return float(os.environ.get("SAHAARA_EMERGENCY_RETRY_DELAY_SECONDS", "20"))

def _max_attempts() -> int:
    return int(os.environ.get("SAHAARA_EMERGENCY_MAX_ATTEMPTS", "3"))

def _cooldown_seconds() -> float:
    return float(os.environ.get("SAHAARA_EMERGENCY_COOLDOWN_SECONDS", "600"))

def _max_incident_seconds() -> float:
    return float(os.environ.get("SAHAARA_EMERGENCY_MAX_SECONDS", "300"))


# ---- State Machine ----

class IncidentStatus(str, Enum):
    TRIGGERED = "triggered"
    DIALING = "dialing"
    RINGING = "ringing"
    ANSWERED = "answered"
    ANSWERED_UNACKNOWLEDGED = "answered_unacknowledged"
    ACKNOWLEDGED = "acknowledged"
    NO_ANSWER = "no_answer"
    FAILED = "failed"
    EXHAUSTED = "exhausted"
    CANCELLED = "cancelled"


class AttemptStatus(str, Enum):
    QUEUED = "queued"
    DIALING = "dialing"
    RINGING = "ringing"
    ANSWERED = "answered"
    ACKNOWLEDGED = "acknowledged"
    NO_ANSWER = "no_answer"
    FAILED = "failed"
    CANCELED = "canceled"


@dataclass
class IncidentContext:
    """All context needed to run the orchestrator for one incident."""
    incident_id: str
    profile_id: str
    trigger: str              # "physical_urgent" | "danger" | "consent_accepted"
    trigger_phrase: Optional[str]  # sanitised, <=15 words; None for danger
    tier: str                 # "red" | "orange"
    simulated: bool
    provider_name: str
    contacts: List[dict]      # from get_callable_contacts()
    event_log: EventLog


class EmergencyOrchestrator:
    """
    Runs the emergency call campaign for one incident.
    Each incident runs in its own daemon thread.
    """

    def __init__(self, context: IncidentContext):
        self.ctx = context
        self.provider = get_call_provider()
        self._stop_event = threading.Event()
        self._current_attempt: Optional[str] = None
        self._handle: Optional[CallHandle] = None
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        """Start the orchestrator in a daemon thread."""
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            daemon=True,
            name=f"emergency-{self.ctx.incident_id}",
        )
        self._thread.start()

    def cancel(self) -> None:
        """Caregiver says 'I'm handling it' — stop retries, end cancelled."""
        self._stop_event.set()
        if self._handle:
            try:
                self.provider.cancel(self._handle)
            except Exception:
                pass

    def _run(self) -> None:
        """Main state machine loop."""
        try:
            self._execute_campaign()
        except Exception:
            logger.exception("Emergency orchestrator crashed for %s", self.ctx.incident_id)
            self._end_incident(IncidentStatus.FAILED, reason="orchestrator_error")

    def _execute_campaign(self) -> None:
        # Check cooldown — but still log the incident and raise Red/alert
        cooldown = _cooldown_seconds()
        recent_in_cooldown = get_recent_emergency_incident_in_cooldown(self.ctx.profile_id, cooldown)
        if recent_in_cooldown and recent_in_cooldown["id"] != self.ctx.incident_id:
            # Cooldown active: log incident as failed (no campaign), but Red tier + alert already raised
            self._log_event(EventType.EMERGENCY_CALL_SKIPPED, {
                "incident_id": self.ctx.incident_id,
                "reason": "cooldown",
                "contact_name": "N/A",
            })
            self._end_incident(IncidentStatus.FAILED, reason="cooldown")
            return

        # Get callable contacts
        contacts = self.ctx.contacts
        if not contacts:
            self._log_event(EventType.EMERGENCY_CALL_SKIPPED, {
                "incident_id": self.ctx.incident_id,
                "reason": "no_callable_contact",
                "contact_name": "N/A",
            })
            self._end_incident(IncidentStatus.FAILED, reason="no_callable_contact")
            return

        # Iterate through contacts
        for contact_order, contact in enumerate(contacts):
            if self._stop_event.is_set():
                self._end_incident(IncidentStatus.CANCELLED, reason="caregiver_cancelled")
                return

            # Try this contact (up to 2 attempts per contact = 1 retry)
            max_contact_attempts = 2
            for contact_attempt in range(1, max_contact_attempts + 1):
                if self._stop_event.is_set():
                    self._end_incident(IncidentStatus.CANCELLED, reason="caregiver_cancelled")
                    return

                # Check total attempts across incident
                incident = get_emergency_incident(self.ctx.incident_id)
                if incident and incident["attempts"] >= _max_attempts():
                    self._end_incident(IncidentStatus.EXHAUSTED, reason="max_attempts_reached")
                    return

                # Check incident hard timeout
                if incident and (time.time() - incident["started_at"]) > _max_incident_seconds():
                    self._end_incident(IncidentStatus.EXHAUSTED, reason="max_incident_time")
                    return

                # Place the call
                attempt_number = incident["attempts"] + 1 if incident else 1
                attempt_id = f"{self.ctx.incident_id}_a{attempt_number}"
                self._place_and_monitor_attempt(contact, contact_order, attempt_id, attempt_number, contact_attempt)

                # If acknowledged, we're done
                attempt = self._get_attempt(attempt_id)
                if attempt and attempt["status"] == AttemptStatus.ACKNOWLEDGED:
                    self._end_incident(IncidentStatus.ACKNOWLEDGED, reason="acknowledged")
                    return

                # If this was a retry and it failed, continue to next contact
                if contact_attempt >= max_contact_attempts:
                    break

                # Wait before retry
                if not self._stop_event.wait(_retry_delay_seconds()):
                    continue
                else:
                    self._end_incident(IncidentStatus.CANCELLED, reason="caregiver_cancelled")
                    return

        # All contacts exhausted
        self._end_incident(IncidentStatus.EXHAUSTED, reason="all_contacts_exhausted")

    def _place_and_monitor_attempt(
        self,
        contact: dict,
        contact_order: int,
        attempt_id: str,
        attempt_number: int,
        contact_attempt: int,
    ) -> None:
        """Place a single call attempt and monitor its status."""
        phone = contact.get("phone", "")
        phone_masked = mask_phone_number(phone)
        contact_name = contact.get("name", "caregiver")
        contact_rel = contact.get("relationship", "")

        # Build message per spec §9.2
        if self.ctx.trigger == "physical_urgent":
            phrase = self.ctx.trigger_phrase or "physical emergency"
            message = (
                f"This is an automated call from Sahaara. Asha has reported a physical "
                f"emergency. She said: \"{phrase}\". Press 1 to confirm you "
                f"received this call."
            )
        elif self.ctx.trigger == "danger":
            message = (
                f"This is an automated call from Sahaara. Asha has reported that she needs "
                f"help right away. Press 1 to confirm you received this call."
            )
        else:  # consent_accepted
            message = (
                f"This is an automated call from Sahaara. Asha asked to speak with you. "
                f"Press 1 to confirm you received this call."
            )

        # Create attempt record
        create_emergency_attempt(
            attempt_id=attempt_id,
            incident_id=self.ctx.incident_id,
            contact_order=contact_order,
            contact_name=contact_name,
            contact_rel=contact_rel,
            phone_masked=phone_masked,
            attempt=attempt_number,
            status=AttemptStatus.QUEUED.value,
            simulated=self.ctx.simulated,
        )
        self._log_event(EventType.EMERGENCY_CALL_ATTEMPT, {
            "incident_id": self.ctx.incident_id,
            "attempt_id": attempt_id,
            "contact_order": contact_order,
            "contact_name": contact_name,
            "phone_masked": phone_masked,
            "attempt": attempt_number,
            "simulated": self.ctx.simulated,
        })

        # Place the call
        self._current_attempt = attempt_id
        try:
            handle = self.provider.place_call(
                to_e164=phone,
                message=message,
                incident_id=self.ctx.incident_id,
                attempt_id=attempt_id,
            )
            self._handle = handle
        except Exception as e:
            logger.exception("Failed to place call for attempt %s", attempt_id)
            update_emergency_attempt_status(attempt_id, AttemptStatus.FAILED.value, ended_at=time.time())
            self._log_event(EventType.EMERGENCY_CALL_STATUS, {
                "incident_id": self.ctx.incident_id,
                "attempt_id": attempt_id,
                "status": "failed",
                "simulated": self.ctx.simulated,
            })
            increment_emergency_incident_attempts(self.ctx.incident_id)
            return

        # Update to dialing
        update_emergency_attempt_status(
            attempt_id, AttemptStatus.DIALING.value,
            provider_call_id=handle.provider_call_id,
        )
        self._log_event(EventType.EMERGENCY_CALL_STATUS, {
            "incident_id": self.ctx.incident_id,
            "attempt_id": attempt_id,
            "status": "dialing",
            "simulated": self.ctx.simulated,
        })

        # Monitor status progression
        self._monitor_attempt(attempt_id, contact_order, attempt_number, contact_name, phone_masked)

    def _monitor_attempt(
        self,
        attempt_id: str,
        contact_order: int,
        attempt_number: int,
        contact_name: str,
        phone_masked: str,
    ) -> None:
        """Poll provider status until terminal state or stop event."""
        ack_deadline: Optional[float] = None
        answered_at: Optional[float] = None

        while not self._stop_event.is_set():
            attempt = self._get_attempt(attempt_id)
            if not attempt:
                return

            status = self.provider.fetch_status(self._handle) if self._handle else attempt["status"]

            # State transitions
            if attempt["status"] in (AttemptStatus.QUEUED.value, AttemptStatus.DIALING.value):
                if status in ("ringing", "in-progress"):
                    update_emergency_attempt_status(attempt_id, AttemptStatus.RINGING.value, answered_at=time.time() if status == "in-progress" else None)
                    self._log_event(EventType.EMERGENCY_CALL_STATUS, {
                        "incident_id": self.ctx.incident_id,
                        "attempt_id": attempt_id,
                        "status": "ringing",
                        "simulated": self.ctx.simulated,
                    })
                    if status == "in-progress":
                        answered_at = time.time()

            elif attempt["status"] == AttemptStatus.RINGING.value:
                if status == "in-progress":
                    update_emergency_attempt_status(attempt_id, AttemptStatus.ANSWERED.value, answered_at=time.time())
                    answered_at = time.time()
                    ack_deadline = time.time() + _ack_seconds()
                    self._log_event(EventType.EMERGENCY_CALL_STATUS, {
                        "incident_id": self.ctx.incident_id,
                        "attempt_id": attempt_id,
                        "status": "answered",
                        "simulated": self.ctx.simulated,
                    })

            elif attempt["status"] == AttemptStatus.ANSWERED.value:
                # Check for acknowledgement (DTMF 1)
                # For simulated provider, check if acked
                if self.ctx.simulated and hasattr(self.provider, 'acknowledge'):
                    if self.provider.acknowledge(attempt_id):
                        update_emergency_attempt_status(attempt_id, AttemptStatus.ACKNOWLEDGED.value, acknowledged_at=time.time(), ended_at=time.time())
                        self._log_event(EventType.EMERGENCY_CALL_STATUS, {
                            "incident_id": self.ctx.incident_id,
                            "attempt_id": attempt_id,
                            "status": "acknowledged",
                            "simulated": self.ctx.simulated,
                        })
                        return

                # Check ack deadline
                if ack_deadline and time.time() >= ack_deadline:
                    update_emergency_attempt_status(attempt_id, AttemptStatus.NO_ANSWER.value, ended_at=time.time())
                    self._log_event(EventType.EMERGENCY_CALL_STATUS, {
                        "incident_id": self.ctx.incident_id,
                        "attempt_id": attempt_id,
                        "status": "no_answer",
                        "simulated": self.ctx.simulated,
                    })
                    return

                # For real Twilio, ack comes via webhook — we'll poll but webhook updates status
                if status == "completed" and attempt["status"] == AttemptStatus.ANSWERED.value:
                    # Call completed without ack -> no_answer
                    update_emergency_attempt_status(attempt_id, AttemptStatus.NO_ANSWER.value, ended_at=time.time())
                    self._log_event(EventType.EMERGENCY_CALL_STATUS, {
                        "incident_id": self.ctx.incident_id,
                        "attempt_id": attempt_id,
                        "status": "no_answer",
                        "simulated": self.ctx.simulated,
                    })
                    return

            elif attempt["status"] in (
                AttemptStatus.ACKNOWLEDGED.value,
                AttemptStatus.NO_ANSWER.value,
                AttemptStatus.FAILED.value,
                AttemptStatus.CANCELED.value,
            ):
                return

            # Brief sleep before next poll
            time.sleep(0.5)

        # Stop event triggered
        if self._handle:
            try:
                self.provider.cancel(self._handle)
            except Exception:
                pass
        update_emergency_attempt_status(attempt_id, AttemptStatus.CANCELED.value, ended_at=time.time())

    def _get_attempt(self, attempt_id: str) -> Optional[dict]:
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT * FROM emergency_attempts WHERE id = ?", (attempt_id,)
            ).fetchone()
        finally:
            conn.close()
        return dict(row) if row else None

    def _end_incident(self, status: IncidentStatus, reason: str) -> None:
        """Finalize incident."""
        update_emergency_incident_status(self.ctx.incident_id, status.value, reason=reason, ended_at=time.time())
        self._log_event(EventType.EMERGENCY_CALL_ENDED, {
            "incident_id": self.ctx.incident_id,
            "status": status.value,
            "attempts": self._get_total_attempts(),
            "simulated": self.ctx.simulated,
            "reason": reason,
        })

    def _get_total_attempts(self) -> int:
        attempts = get_emergency_attempts(self.ctx.incident_id)
        return len(attempts)

    def _log_event(self, event_type: EventType, detail: dict) -> None:
        self.ctx.event_log.append(
            Event(
                timestamp=time.time(),
                type=event_type,
                profile_id=self.ctx.profile_id,
                detail=detail,
            )
        )


# ---- Public API ----

_active_orchestrators: dict[str, EmergencyOrchestrator] = {}
_lock = threading.Lock()


def trigger_emergency_call(
    *,
    profile_id: str,
    trigger: str,
    trigger_phrase: Optional[str],
    tier: str,
    event_log: EventLog,
    profile: Profile,
) -> str:
    """
    Entry point from conversation.py when an urgent/danger turn is detected.
    Returns the incident_id.
    """
    # Cooldown check (synchronous, before creating incident)
    # Per spec: cooldown applies ONLY to the phone campaign — dashboard alert + Red tier still raised
    cooldown = _cooldown_seconds()
    recent_in_cooldown = get_recent_emergency_incident_in_cooldown(profile_id, cooldown)
    if recent_in_cooldown:
        # Log skipped event but don't create new incident
        event_log.append(
            Event(
                timestamp=time.time(),
                type=EventType.EMERGENCY_CALL_SKIPPED,
                profile_id=profile_id,
                detail={
                    "incident_id": recent_in_cooldown["id"],
                    "reason": "cooldown",
                    "contact_name": "N/A",
                },
            )
        )
        return recent_in_cooldown["id"]

    # Generate incident ID
    incident_id = f"emg_{int(time.time() * 1000)}_{os.getpid() % 1000}"

    # Get callable contacts from DATABASE (fresh) — the session profile may be stale
    from database import get_profile
    from memory.profile import Profile as ProfileClass
    fresh_profile_data = get_profile(profile_id)
    if fresh_profile_data:
        fresh_profile = ProfileClass(fresh_profile_data)
    else:
        fresh_profile = profile
    contacts = get_callable_contacts(fresh_profile)
    provider = get_call_provider()

    # Create incident record
    create_emergency_incident(
        incident_id=incident_id,
        profile_id=profile_id,
        trigger=trigger,
        trigger_phrase=trigger_phrase,
        tier=tier,
        simulated=provider.simulated,
        provider=provider.name,
    )

    # Log STARTED event
    event_log.append(
        Event(
            timestamp=time.time(),
            type=EventType.EMERGENCY_CALL_STARTED,
            profile_id=profile_id,
            detail={
                "incident_id": incident_id,
                "trigger": trigger,
                "tier": tier,
                "simulated": provider.simulated,
                "provider": provider.name,
            },
        )
    )

    # Create and start orchestrator
    ctx = IncidentContext(
        incident_id=incident_id,
        profile_id=profile_id,
        trigger=trigger,
        trigger_phrase=trigger_phrase,
        tier=tier,
        simulated=provider.simulated,
        provider_name=provider.name,
        contacts=contacts,
        event_log=event_log,
    )
    orchestrator = EmergencyOrchestrator(ctx)

    with _lock:
        _active_orchestrators[incident_id] = orchestrator

    orchestrator.start()
    return incident_id


def get_orchestrator(incident_id: str) -> Optional[EmergencyOrchestrator]:
    with _lock:
        return _active_orchestrators.get(incident_id)


def cancel_emergency_incident(incident_id: str) -> bool:
    """Caregiver dashboard 'I'm on it' button."""
    with _lock:
        orch = _active_orchestrators.get(incident_id)
        if not orch:
            return False
        orch.cancel()
        return True


def acknowledge_attempt(attempt_id: str) -> bool:
    """Called from Twilio webhook when DTMF 1 is received."""
    with _lock:
        for orch in _active_orchestrators.values():
            if orch._current_attempt == attempt_id:
                # For simulated provider
                if hasattr(orch.provider, 'acknowledge'):
                    return orch.provider.acknowledge(attempt_id)
    return False