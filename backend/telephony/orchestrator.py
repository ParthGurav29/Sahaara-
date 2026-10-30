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
    get_emergency_attempt,
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
    return int(os.environ.get("SAHAARA_EMERGENCY_MAX_ATTEMPTS", "4"))

def _cooldown_seconds() -> float:
    return float(os.environ.get("SAHAARA_EMERGENCY_COOLDOWN_SECONDS", "300"))

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


# Providers report status in their own vocabulary. Map everything the
# orchestrator can receive onto the attempt states it knows, so a campaign
# progresses the same way whether it is simulated or real.
_PROVIDER_TERMINAL_STATUS = {
    "acknowledged": AttemptStatus.ACKNOWLEDGED.value,
    "no_answer": AttemptStatus.NO_ANSWER.value,
    "no-answer": AttemptStatus.NO_ANSWER.value,
    "busy": AttemptStatus.NO_ANSWER.value,
    "failed": AttemptStatus.FAILED.value,
    "canceled": AttemptStatus.CANCELED.value,
    "cancelled": AttemptStatus.CANCELED.value,
}

# An incident in one of these states may still be dialling. Anything else is a
# finished record and must not be rewritten by a late webhook.
_LIVE_INCIDENT_STATUSES = {
    IncidentStatus.TRIGGERED.value,
    IncidentStatus.DIALING.value,
    IncidentStatus.RINGING.value,
    IncidentStatus.ANSWERED.value,
    IncidentStatus.ANSWERED_UNACKNOWLEDGED.value,
    IncidentStatus.NO_ANSWER.value,
}

# Every status a provider webhook may report, mapped onto an attempt state.
# Intermediate states matter: without them a real call's "ringing"/"answered"
# webhooks are dropped and the dashboard can only ever show "dialing" until the
# campaign polls again.
_PROVIDER_STATUS = {
    "dialing": AttemptStatus.DIALING.value,
    "started": AttemptStatus.DIALING.value,
    "queued": AttemptStatus.QUEUED.value,
    "ringing": AttemptStatus.RINGING.value,
    "answered": AttemptStatus.ANSWERED.value,
    "human": AttemptStatus.ANSWERED.value,
    # A machine answered the line (voicemail, IVR). That is an answer to the
    # provider and no proof a person heard anything — the incident ends
    # `answered_unacknowledged` if no keypress ever arrives.
    "machine": AttemptStatus.ANSWERED.value,
    "acknowledged": AttemptStatus.ACKNOWLEDGED.value,
    "no_answer": AttemptStatus.NO_ANSWER.value,
    "no-answer": AttemptStatus.NO_ANSWER.value,
    "busy": AttemptStatus.NO_ANSWER.value,
    "unanswered": AttemptStatus.NO_ANSWER.value,
    "timeout": AttemptStatus.NO_ANSWER.value,
    "failed": AttemptStatus.FAILED.value,
    "rejected": AttemptStatus.FAILED.value,
    "canceled": AttemptStatus.CANCELED.value,
    "cancelled": AttemptStatus.CANCELED.value,
}

# "The call is over" without saying whether anyone picked up.
_PROVIDER_COMPLETED = {"completed", "disconnected"}

# Nothing may move an attempt out of these.
_PROVIDER_FINAL_ATTEMPT_STATUSES = {
    AttemptStatus.ACKNOWLEDGED.value,
    AttemptStatus.CANCELED.value,
    AttemptStatus.FAILED.value,
    AttemptStatus.NO_ANSWER.value,
}

# Progress order, so an out-of-order webhook cannot move an attempt backwards.
_ATTEMPT_RANK = {
    AttemptStatus.QUEUED.value: 0,
    AttemptStatus.DIALING.value: 1,
    AttemptStatus.RINGING.value: 2,
    AttemptStatus.ANSWERED.value: 3,
    AttemptStatus.ACKNOWLEDGED.value: 4,
    AttemptStatus.NO_ANSWER.value: 4,
    AttemptStatus.FAILED.value: 4,
    AttemptStatus.CANCELED.value: 4,
}


def _attempt_rank(status: str) -> int:
    return _ATTEMPT_RANK.get(status, 0)


def _was_answered(attempt_id: str) -> bool:
    attempt = get_emergency_attempt(attempt_id)
    if not attempt:
        return False
    return bool(attempt.get("answered_at")) or attempt.get("status") in (
        AttemptStatus.ANSWERED.value,
        AttemptStatus.ACKNOWLEDGED.value,
    )


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
    real_call_allowed: bool = False
    gate_reason: str = "simulated"
    alert_builder: object = None


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
        # Set when the provider itself says the campaign cannot run (e.g.
        # emergency calling is switched off). The incident then ends `failed`
        # with that reason instead of pretending every contact went unanswered.
        self._fatal_reason: Optional[str] = None
        # Set when a human pressed the acknowledgement digit. It stops the
        # ladder the same way a cancel does, but the record must say
        # "acknowledged", never "cancelled by caregiver": those are different
        # facts and the dashboard repeats whichever one it is given.
        self._acknowledged = False

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

    def acknowledge(self) -> None:
        """A human pressed the acknowledgement digit — stop the ladder.

        Same stop mechanics as `cancel()`, but the ladder ends `acknowledged`.
        This can arrive after the campaign has already moved on to the next
        contact (a press-1 on a call that is still ringing while we dial the
        next number), so the stop has to be campaign-wide, not attempt-local.
        """
        self._acknowledged = True
        self.cancel()

    def _end_for_stop(self) -> None:
        """Resolve a stop that arrived mid-ladder to the right ending."""
        if self._acknowledged:
            self._end_incident(IncidentStatus.ACKNOWLEDGED, reason="acknowledged")
        else:
            self._end_incident(IncidentStatus.CANCELLED, reason="caregiver_cancelled")

    def is_active(self) -> bool:
        """True while the campaign thread is still working through the ladder."""
        return bool(self._thread and self._thread.is_alive())

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
                self._end_for_stop()
                return

            # Try this contact (up to 2 attempts per contact = 1 retry)
            max_contact_attempts = 2
            for contact_attempt in range(1, max_contact_attempts + 1):
                if self._stop_event.is_set():
                    self._end_for_stop()
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

                if self._fatal_reason:
                    self._end_incident(IncidentStatus.FAILED, reason=self._fatal_reason)
                    return

                # If acknowledged, we're done
                attempt = self._get_attempt(attempt_id)
                if attempt and attempt["status"] == AttemptStatus.ACKNOWLEDGED.value:
                    self._end_incident(IncidentStatus.ACKNOWLEDGED, reason="acknowledged")
                    return

                # If this was a retry and it failed, continue to next contact
                if contact_attempt >= max_contact_attempts:
                    break

                # Wait before retry
                if not self._stop_event.wait(_retry_delay_seconds()):
                    continue
                else:
                    self._end_for_stop()
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

        # Count the attempt as soon as it starts. Without this the incident's
        # `attempts` counter stays 0, every retry reuses the id `<incident>_a1`
        # (a primary-key collision) and the max-attempts guard never trips.
        increment_emergency_incident_attempts(self.ctx.incident_id)

        # Place the call
        self._current_attempt = attempt_id
        try:
            handle = self.provider.place_call(
                to_e164=phone,
                message=message,
                incident_id=self.ctx.incident_id,
                attempt_id=attempt_id,
                gate_reason=self.ctx.gate_reason,
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
            return

        # A provider can refuse the call outright (calling switched off,
        # number not permitted on the account). Honour that before monitoring,
        # otherwise the attempt sits at "dialing" forever and the dashboard
        # shows a call that never happened.
        initial_terminal = _PROVIDER_TERMINAL_STATUS.get(handle.status)
        if initial_terminal:
            reason = (handle.raw or {}).get("reason") or initial_terminal
            if reason == "disabled":
                self._fatal_reason = "disabled"
            update_emergency_attempt_status(
                attempt_id,
                initial_terminal,
                provider_call_id=handle.provider_call_id,
                ended_at=time.time(),
            )
            self._log_event(EventType.EMERGENCY_CALL_STATUS, {
                "incident_id": self.ctx.incident_id,
                "attempt_id": attempt_id,
                "status": initial_terminal,
                "simulated": self.ctx.simulated,
            })
            self._handle = None
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

            # Providers speak different vocabularies: Twilio reports
            # "in-progress"/"completed", the simulated provider reports
            # "answered"/"acknowledged"/"no_answer". Normalise first, otherwise
            # a simulated call that was answered looks like it is still ringing
            # forever and the campaign never retries or ends.
            provider_terminal = _PROVIDER_TERMINAL_STATUS.get(status)
            if provider_terminal and attempt["status"] != provider_terminal:
                now = time.time()
                update_emergency_attempt_status(
                    attempt_id,
                    provider_terminal,
                    acknowledged_at=now if provider_terminal == AttemptStatus.ACKNOWLEDGED.value else None,
                    ended_at=now,
                )
                self._log_event(EventType.EMERGENCY_CALL_STATUS, {
                    "incident_id": self.ctx.incident_id,
                    "attempt_id": attempt_id,
                    "status": provider_terminal,
                    "simulated": self.ctx.simulated,
                })
                return

            if status in ("answered", "in-progress") and attempt["status"] in (
                AttemptStatus.QUEUED.value,
                AttemptStatus.DIALING.value,
                AttemptStatus.RINGING.value,
            ):
                answered_at = answered_at or time.time()
                update_emergency_attempt_status(
                    attempt_id, AttemptStatus.ANSWERED.value, answered_at=answered_at
                )
                ack_deadline = answered_at + _ack_seconds()
                self._log_event(EventType.EMERGENCY_CALL_STATUS, {
                    "incident_id": self.ctx.incident_id,
                    "attempt_id": attempt_id,
                    "status": "answered",
                    "simulated": self.ctx.simulated,
                })

            # State transitions
            elif attempt["status"] in (AttemptStatus.QUEUED.value, AttemptStatus.DIALING.value):
                if status in ("ringing",):
                    update_emergency_attempt_status(attempt_id, AttemptStatus.RINGING.value)
                    self._log_event(EventType.EMERGENCY_CALL_STATUS, {
                        "incident_id": self.ctx.incident_id,
                        "attempt_id": attempt_id,
                        "status": "ringing",
                        "simulated": self.ctx.simulated,
                    })

            elif attempt["status"] == AttemptStatus.ANSWERED.value:
                # Check for acknowledgement (DTMF 1).
                # Only a provider that says it auto-acks may be polled here.
                # Calling `acknowledge()` unconditionally made the simulated
                # campaign claim an acknowledgement the instant the call was
                # answered, so the retry / next-contact ladder could never run.
                if getattr(self.provider, "auto_ack", False):
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

        # Release the per-profile lock so a later trigger can start a new ladder.
        with _lock:
            if _active_by_profile.get(self.ctx.profile_id) == self.ctx.incident_id:
                _active_by_profile.pop(self.ctx.profile_id, None)

        self._log_event(EventType.EMERGENCY_CALL_ENDED, {
            "incident_id": self.ctx.incident_id,
            "status": status.value,
            "attempts": self._get_total_attempts(),
            "simulated": self.ctx.simulated,
            "reason": reason,
        })

        # Ladder ran out: raise the Red "No caregiver reached" alert. This is a
        # dashboard statement of what was tried, never a claim of contact.
        if status == IncidentStatus.EXHAUSTED and self.ctx.alert_builder is not None:
            try:
                attempted = []
                for att in get_emergency_attempts(self.ctx.incident_id):
                    name = att.get("contact_name")
                    if name and name not in attempted:
                        attempted.append(name)
                self.ctx.alert_builder.build_no_caregiver_reached_alert(
                    incident_id=self.ctx.incident_id,
                    attempted_contacts=attempted,
                    simulated=self.ctx.simulated,
                )
            except Exception:
                logger.exception("Could not raise no-caregiver-reached alert for %s", self.ctx.incident_id)

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

# One active ladder per profile. A duplicate trigger (repeated urgent turn, a
# tier flicker, a retried HTTP call) must never dial a second time while a
# campaign is already running — the caller gets the existing incident id back.
_active_by_profile: dict[str, str] = {}


def get_active_incident_id(profile_id: str) -> Optional[str]:
    """Return the incident id of a ladder that is still running, if any."""
    with _lock:
        incident_id = _active_by_profile.get(profile_id)
    if not incident_id:
        return None
    orch = get_orchestrator(incident_id)
    if orch is None or not orch.is_active():
        with _lock:
            if _active_by_profile.get(profile_id) == incident_id:
                _active_by_profile.pop(profile_id, None)
        return None
    return incident_id


def trigger_emergency_call(
    *,
    profile_id: str,
    trigger: str,
    trigger_phrase: Optional[str],
    tier: str,
    event_log: EventLog,
    profile: Profile,
    real_call_allowed: bool = False,
    gate_reason: str = "simulated",
    alert_builder: object = None,
) -> str:
    """
    Entry point from conversation.py / scheduler.py when a call trigger fires.
    Returns the incident_id (the existing one when a ladder is already running).
    """
    # Idempotency: one incident = one active ladder per profile.
    existing = get_active_incident_id(profile_id)
    if existing:
        event_log.append(
            Event(
                timestamp=time.time(),
                type=EventType.EMERGENCY_CALL_SKIPPED,
                profile_id=profile_id,
                detail={
                    "incident_id": existing,
                    "reason": "already_active",
                    "contact_name": "N/A",
                },
            )
        )
        return existing
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
                "trigger_phrase": trigger_phrase,
                "tier": tier,
                "simulated": provider.simulated,
                "provider": provider.name,
                "real_call_allowed": real_call_allowed,
                "gate_reason": gate_reason,
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
        real_call_allowed=real_call_allowed,
        gate_reason=gate_reason,
        alert_builder=alert_builder,
    )
    orchestrator = EmergencyOrchestrator(ctx)

    with _lock:
        _active_orchestrators[incident_id] = orchestrator
        _active_by_profile[profile_id] = incident_id

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


def acknowledge_attempt(attempt_id: str, digit: str = "1") -> bool:
    """
    Record a caregiver acknowledgement for one attempt.

    Called from the provider webhook when the caregiver presses the ack digit.
    It writes the ACKNOWLEDGED status straight to the database so the campaign
    thread (which polls) sees it within one tick, whatever the provider is.
    The digit is the caller's proof that a human pressed the key; this function
    never acknowledges on its own.
    """
    if not attempt_id or digit != "1":
        return False

    attempt = get_emergency_attempt(attempt_id)
    if not attempt:
        return False
    # A late press is still a press. `no_answer` only means no keypress arrived
    # inside the window; a DTMF 1 arriving after it is proof that a human did
    # press the key, and discarding that would throw away the strongest signal
    # the system ever gets. Duplicates and cancelled/failed rows are refused.
    if attempt["status"] in (
        AttemptStatus.ACKNOWLEDGED.value,
        AttemptStatus.CANCELED.value,
        AttemptStatus.FAILED.value,
    ):
        return False

    now = time.time()
    update_emergency_attempt_status(
        attempt_id,
        AttemptStatus.ACKNOWLEDGED.value,
        acknowledged_at=now,
        ended_at=now,
    )

    incident_id = attempt.get("incident_id")
    with _lock:
        orch = _active_orchestrators.get(incident_id) if incident_id else None
        stop_now = orch is not None
        if orch is not None:
            # Campaign-wide, not attempt-local: a press-1 on a call that is
            # still ringing must stop the next number being dialled even if the
            # campaign has already moved on. `_end_for_stop()` keeps the ending
            # honest ("acknowledged", not "cancelled").
            orch.acknowledge()
        if orch is not None and hasattr(orch.provider, "acknowledge"):
            if orch._current_attempt == attempt_id:
                try:
                    orch.provider.acknowledge(attempt_id)
                except Exception:  # noqa: BLE001
                    pass

    if not stop_now and incident_id:
        # Nothing is running any more. If the incident was left open, close it
        # here so the dashboard never shows a live campaign that no thread is
        # working on. A campaign that already ended keeps its record: a late
        # press-1 is shown on the attempt, and history is not rewritten.
        incident = get_emergency_incident(incident_id)
        if incident and incident["status"] in _LIVE_INCIDENT_STATUSES:
            update_emergency_incident_status(
                incident_id,
                IncidentStatus.ACKNOWLEDGED.value,
                reason="acknowledged",
                ended_at=now,
            )
            with _lock:
                if _active_by_profile.get(incident.get("profile_id")) == incident_id:
                    _active_by_profile.pop(incident.get("profile_id"), None)
    return True


def record_provider_status(attempt_id: str, status: str) -> bool:
    """
    Record a status reported by a provider webhook.

    The campaign polls as well, so this only ever brings a state forward; it
    never invents one, and it never moves an attempt backwards (a webhook that
    arrives out of order cannot make a ringing call "dialing" again). Pressing
    the acknowledgement digit is *not* handled here — that requires the digit
    itself, in `acknowledge_attempt`.
    """
    if not attempt_id:
        return False

    mapped = _PROVIDER_STATUS.get(status)
    if mapped is None and status in _PROVIDER_COMPLETED:
        # A call that finished. Whether that was an answer depends on whether
        # the line was ever picked up, which is already recorded.
        mapped = (
            AttemptStatus.ANSWERED.value
            if _was_answered(attempt_id)
            else AttemptStatus.NO_ANSWER.value
        )
    if mapped is None:
        return False

    attempt = get_emergency_attempt(attempt_id)
    if not attempt:
        return False

    current = attempt["status"]
    if current in _PROVIDER_FINAL_ATTEMPT_STATUSES:
        return False
    if _attempt_rank(mapped) < _attempt_rank(current):
        return False

    now = time.time()
    if mapped == AttemptStatus.ANSWERED.value:
        update_emergency_attempt_status(
            attempt_id,
            mapped,
            answered_at=attempt.get("answered_at") or now,
        )
    elif mapped in (
        AttemptStatus.DIALING.value,
        AttemptStatus.RINGING.value,
    ):
        update_emergency_attempt_status(attempt_id, mapped)
    else:
        update_emergency_attempt_status(attempt_id, mapped, ended_at=now)
    return True