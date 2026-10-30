"""
dashboard/alerts.py

Builds specific, context-bearing alerts on tier changes for the caregiver dashboard.
All caregiver-facing text passes through guardrail checks to avoid diagnostic language.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional
import logging
import time

from events.event_log import EventLog, EventType, EscalationTier
from memory.profile import Profile
from safety.guardrails import check_guardrails, SAFE_FALLBACK_RESPONSE

logger = logging.getLogger(__name__)


@dataclass
class Alert:
    """An alert for the caregiver dashboard."""
    timestamp: float
    tier_from: EscalationTier
    tier_to: EscalationTier
    reason: str
    profile_id: str
    message: str
    detail: dict
    alert_id: str = ""
    status: str = "new"  # "new", "acknowledged", "resolved"
    acknowledged_at: Optional[float] = None
    acknowledged_note: Optional[str] = None
    
    def to_dict(self) -> dict:
        return {
            "alert_id": self.alert_id,
            "timestamp": self.timestamp,
            "timestamp_iso": datetime.fromtimestamp(self.timestamp).isoformat(),
            "tier_from": self.tier_from.value,
            "tier_to": self.tier_to.value,
            "reason": self.reason,
            "profile_id": self.profile_id,
            "message": self.message,
            "detail": self.detail,
            "status": self.status,
            "acknowledged_at": self.acknowledged_at,
            "acknowledged_note": self.acknowledged_note,
        }


class AlertStore:
    """In-memory store for alerts. Holds all alerts for the current session."""
    
    def __init__(self):
        self._alerts: list[Alert] = []
        self._counter: int = 0
    
    def add(self, alert: Alert) -> Alert:
        self._counter += 1
        alert.alert_id = f"alert_{self._counter}"
        self._alerts.append(alert)
        return alert
    
    def get_all(self, status: Optional[str] = None) -> list[Alert]:
        if status:
            return [a for a in self._alerts if a.status == status]
        return list(self._alerts)
    
    def get_by_id(self, alert_id: str) -> Optional[Alert]:
        for a in self._alerts:
            if a.alert_id == alert_id:
                return a
        return None
    
    def acknowledge(self, alert_id: str, note: str = "") -> Optional[Alert]:
        alert = self.get_by_id(alert_id)
        if alert is None:
            return None
        alert.status = "acknowledged"
        alert.acknowledged_at = time.time()
        alert.acknowledged_note = note
        return alert
    
    def unread_count(self) -> int:
        return sum(1 for a in self._alerts if a.status == "new")
    
    def clear(self):
        self._alerts.clear()
        self._counter = 0
    
    def to_dict_list(self) -> list[dict]:
        return [a.to_dict() for a in self._alerts]


class AlertBuilder:
    """
    Builds alerts from escalation events and session context.
    
    Alerts are specific and actionable, e.g.:
    - "Asha has asked about Priya 3 times in 5 minutes and seemed anxious. 
      Sahaara offered the Memory Box clip. A call might help."
    - NOT: "Check on Asha" or "Signs of decline detected"
    """
    
    def __init__(self, event_log: EventLog, profile: Profile, alert_store: Optional["AlertStore"] = None):
        self.event_log = event_log
        self.profile = profile
        self.alert_store = alert_store
    
    def _store(self, alert: "Alert", send_push: bool = True) -> "Alert":
        """Store alert if a store is configured.

        When `send_push` is False (used by the consent-accepted path in
        conversation.py), the alert is stored in the dashboard store but push
        dispatch is left to the caller, which can wait ~1.0 s for the result
        before choosing its response wording (Step 8a).
        """
        if self.alert_store:
            self.alert_store.add(alert)
            if send_push:
                # M5 Step 5: fan the alert out to caregiver devices. Fire-and-forget
                # and never raises, so a push failure cannot break the turn.
                try:
                    from push_notify import notify_alert

                    notify_alert(alert, self.event_log)
                except Exception:
                    # Push must never break the turn; still leave a trace, because a
                    # silently missing import would look like "alerts are just quiet".
                    logger.exception("Push dispatch could not start")
        return alert
    
    def _get_contact_name(self, tier: EscalationTier, reason: str, context: dict) -> Optional[str]:
        """Get the most relevant contact name for this alert."""
        # Use context from tier change event first
        contact_from_context = context.get("contact_name")
        if contact_from_context:
            return contact_from_context
        
        # Check recent consent events
        consent_events = self.event_log.get_events(EventType.CONSENT_ASKED)
        if consent_events:
            return consent_events[-1].detail.get("contact_name")
        
        # Check for repeated question about a specific person
        repeat_events = self.event_log.get_events(EventType.REPEAT)
        for e in reversed(repeat_events):
            question = e.detail.get("question", "").lower()
            for member in self.profile.get_family():
                if member["name"].lower() in question:
                    return member["name"]
        
        # Default to primary caregiver
        for member in self.profile.get_family():
            if "caregiver" in member.get("relationship", "").lower() or \
               "daughter" in member.get("relationship", "").lower() or \
               "son" in member.get("relationship", "").lower():
                return member["name"]
        
        return None
    
    def _get_repeat_count(self, context: dict) -> int:
        """Get repeat count from context."""
        return context.get("repeat_count", 0)
    
    def _get_distress_duration(self) -> float:
        """Get duration of sustained distress in seconds."""
        distress_events = self.event_log.get_events(EventType.DISTRESS)
        if not distress_events:
            return 0.0
        latest = distress_events[-1]
        return latest.detail.get("duration_seconds", 0.0)
    
    def _get_memory_box_offered(self) -> bool:
        """Check if Memory Box was offered recently."""
        # Check recent turns for memory box offers
        # This is a simplification - in production would check session_notes
        return False
    
    def _format_duration(self, seconds: float) -> str:
        """Format seconds into human-readable string."""
        if seconds < 60:
            return f"{int(seconds)} seconds"
        mins = int(seconds / 60)
        secs = int(seconds % 60)
        if secs == 0:
            return f"{mins} minute{'s' if mins > 1 else ''}"
        return f"{mins} minute{'s' if mins > 1 else ''} and {secs} seconds"
    
    def _build_guardrail_safe_message(self, raw_message: str) -> str:
        """Ensure alert message passes guardrails (no diagnostic language)."""
        result = check_guardrails(raw_message, self.profile)
        if result.passed:
            return raw_message
        
        # Fallback: sanitize by removing problematic phrases
        safe = raw_message
        for violation in result.violations:
            if "diagnosis" in violation:
                # Remove diagnostic framing
                safe = safe.replace("dementia", "memory changes")
                safe = safe.replace("decline", "changes")
            elif "fabricated_reassurance" in violation:
                # Remove specific reassurance claims
                import re
                safe = re.sub(r"\b(he|she|they|someone) is (on (his|her|their) way|coming|arriving)\b", 
                             "someone has been notified", safe, flags=re.IGNORECASE)
        
        # Re-check
        result2 = check_guardrails(safe, self.profile)
        if result2.passed:
            return safe
        
        # Ultimate fallback
        return "Asha needs your attention. Please check in when you can."
    
    def build_tier_change_alert(
        self,
        tier_from: EscalationTier,
        tier_to: EscalationTier,
        reason: str,
        context: dict,
    ) -> Alert:
        """Build an alert for a tier change."""
        now = datetime.now().timestamp()
        
        contact_name = self._get_contact_name(tier_to, reason, context)
        repeat_count = self._get_repeat_count(context)
        distress_duration = self._get_distress_duration()
        memory_offered = self._get_memory_box_offered()
        
        # Build context-rich message based on tier transition
        if tier_to == EscalationTier.YELLOW:
            if reason == "repeat_threshold":
                message = (
                    f"{self.profile.preferred_address} has asked about {contact_name or 'a loved one'} "
                    f"{repeat_count} times in the last few minutes. "
                )
                if memory_offered:
                    message += "Sahaara offered a Memory Box clip. "
                message += "A call might help reassure them."
            
            elif reason == "sustained_distress":
                message = (
                    f"{self.profile.preferred_address} has seemed anxious for "
                    f"{self._format_duration(distress_duration)}. "
                )
                if memory_offered:
                    message += "Sahaara offered a Memory Box clip. "
                message += "A check-in call might help."
            
            else:
                message = (
                    f"{self.profile.preferred_address} is showing signs of concern. "
                    f"Sahaara has been offering reassurance. "
                    f"A call might help."
                )
        
        elif tier_to == EscalationTier.ORANGE:
            message = (
                f"{self.profile.preferred_address} has been distressed for "
                f"{self._format_duration(distress_duration)} despite Sahaara's reassurance. "
            )
            if repeat_count > 0:
                message += f"They've asked about {contact_name} {repeat_count} times. "
            message += "A call is strongly recommended."
        
        elif tier_to == EscalationTier.RED:
            if reason == "danger_statement":
                message = (
                    f"URGENT: {self.profile.preferred_address} said something that needs a check-in. "
                    f"An urgent alert has been logged on the caregiver dashboard. Please check immediately."
                )
            elif reason == "unresponsive":
                message = (
                    f"URGENT: {self.profile.preferred_address} has not responded for an extended period. "
                    f"Sahaara has escalated. Please check on them now."
                )
            else:
                message = (
                    f"URGENT: {self.profile.preferred_address} needs immediate attention. "
                    f"Please check on them now."
                )
        
        elif tier_to == EscalationTier.GREEN:
            if tier_from in (EscalationTier.ORANGE, EscalationTier.RED):
                message = (
                    f"{self.profile.preferred_address} has calmed down after being distressed. "
                    f"The situation has de-escalated. No immediate action needed."
                )
            elif tier_from == EscalationTier.YELLOW:
                message = (
                    f"{self.profile.preferred_address} is doing better now. "
                    f"Earlier concern has passed. All clear."
                )
            else:
                message = (
                    f"{self.profile.preferred_address} is calm. No concerns at this time."
                )
        else:
            message = f"Tier changed from {tier_from.value} to {tier_to.value}."
        
        # Apply guardrail safety
        safe_message = self._build_guardrail_safe_message(message)
        
        alert = Alert(
            timestamp=now,
            tier_from=tier_from,
            tier_to=tier_to,
            reason=reason,
            profile_id=self.profile.profile_id,
            message=safe_message,
            detail={
                "contact_name": contact_name,
                "repeat_count": repeat_count,
                "distress_duration_seconds": distress_duration,
                "memory_box_offered": memory_offered,
                "context": context,
            },
        )
        self._store(alert)
        return alert
    
    def build_consent_alert(
        self,
        outcome: str,
        contact_name: str,
        tier: EscalationTier,
    ) -> Alert:
        """Build an alert for consent outcome."""
        now = datetime.now().timestamp()
        
        if outcome == "accepted":
            message = (
                f"{self.profile.preferred_address} agreed to a call with {contact_name}. "
                f"An alert has been logged on the caregiver dashboard."
            )
        elif outcome == "declined":
            message = (
                f"{self.profile.preferred_address} declined a call with {contact_name}. "
                f"Sahaara respected their choice and will continue monitoring."
            )
        elif outcome == "timeout":
            message = (
                f"{self.profile.preferred_address} didn't respond to the call offer. "
                f"Sahaara treated this as a decline for safety and will continue monitoring."
            )
        else:
            message = f"Consent outcome: {outcome} for call with {contact_name}."
        
        safe_message = self._build_guardrail_safe_message(message)
        
        alert = Alert(
            timestamp=now,
            tier_from=tier,
            tier_to=tier,
            reason=f"consent_{outcome}",
            profile_id=self.profile.profile_id,
            message=safe_message,
            detail={
                "contact_name": contact_name,
                "outcome": outcome,
                "tier": tier.value,
            },
        )
        self._store(alert, send_push=False)
        return alert
    
    def build_fallback_alert(self, fallback_type: str, detail: dict) -> Alert:
        """Build an alert for a fallback trigger."""
        now = datetime.now().timestamp()
        
        if fallback_type == "guardrail":
            message = (
                f"Sahaara's response was adjusted for safety. "
                f"The original response contained content that didn't meet our guidelines "
                f"and was replaced with a safe, supportive message."
            )
        elif fallback_type.startswith("standby_"):
            message = (
                f"Sahaara used a standby response because the AI was taking longer than expected. "
                f"The conversation continued smoothly."
            )
        else:
            message = f"System fallback triggered: {fallback_type}."
        
        safe_message = self._build_guardrail_safe_message(message)
        
        alert = Alert(
            timestamp=now,
            tier_from=EscalationTier.GREEN,
            tier_to=EscalationTier.GREEN,
            reason=fallback_type,
            profile_id=self.profile.profile_id,
            message=safe_message,
            detail=detail,
        )
        self._store(alert)
        return alert

    def build_fallback_alert(self, fallback_type: str, detail: dict) -> Alert:
        """Build an alert for a fallback trigger."""
        now = datetime.now().timestamp()
        
        if fallback_type == "guardrail":
            message = (
                f"Sahaara's response was adjusted for safety. "
                f"The original response contained content that didn't meet our guidelines "
                f"and was replaced with a safe, supportive message."
            )
        elif fallback_type.startswith("standby_"):
            message = (
                f"Sahaara used a standby response because the AI was taking longer than expected. "
                f"The conversation continued smoothly."
            )
        else:
            message = f"System fallback triggered: {fallback_type}."
        
        safe_message = self._build_guardrail_safe_message(message)
        
        alert = Alert(
            timestamp=now,
            tier_from=EscalationTier.GREEN,
            tier_to=EscalationTier.GREEN,
            reason=fallback_type,
            profile_id=self.profile.profile_id,
            message=safe_message,
            detail=detail,
        )
        self._store(alert)
        return alert

    def build_physical_symptom_alert(
        self,
        phrase: str,
        matched_pattern: str,
        urgency: str,  # "urgent" or "general"
        tier_escalated_to: str,  # "red" or "orange"
    ) -> Alert:
        """Build an alert for a detected physical symptom."""
        now = datetime.now().timestamp()
        
        contact_name = self._get_contact_name(
            EscalationTier.RED if urgency == "urgent" else EscalationTier.ORANGE,
            "physical_symptom",
            {},
        )
        
        if urgency == "urgent":
            message = (
                f"URGENT: {self.profile.preferred_address} said: \"{phrase}\". "
                f"Please check on them now. "
                f"Sahaara stayed with them and urged them to reach you right away."
            )
        else:
            message = (
                f"{self.profile.preferred_address} said they're not feeling well: \"{phrase}\". "
                f"Sahaara has offered to call {contact_name or 'a caregiver'} and stay with them. "
                f"A check-in is recommended."
            )
        
        safe_message = self._build_guardrail_safe_message(message)
        
        alert = Alert(
            timestamp=now,
            tier_from=EscalationTier.GREEN,
            tier_to=EscalationTier.RED if urgency == "urgent" else EscalationTier.ORANGE,
            reason=f"physical_symptom_{urgency}",
            profile_id=self.profile.profile_id,
            message=safe_message,
            detail={
                "phrase": phrase,
                "matched_pattern": matched_pattern,
                "urgency": urgency,
                "contact_name": contact_name,
            },
        )
        self._store(alert)
        return alert

    def build_no_caregiver_reached_alert(
        self,
        incident_id: str,
        attempted_contacts: list[str],
        simulated: bool,
    ) -> Alert:
        """
        Build the Red alert raised when the call ladder ran out.

        This is the only place the dashboard tells the caregiver *and* Asha's
        side that nobody was reached. It states exactly what was tried and never
        claims a human was contacted.
        """
        now = datetime.now().timestamp()
        tried = ", ".join(attempted_contacts) if attempted_contacts else "no contacts"

        if simulated:
            message = (
                f"No caregiver was reached. Attempts to {tried} were simulated — "
                f"no real call was placed. Please check on "
                f"{self.profile.preferred_address} now."
            )
        else:
            message = (
                f"No caregiver was reached. Sahaara tried {tried} and got no "
                f"acknowledgement. Please check on "
                f"{self.profile.preferred_address} now."
            )

        safe_message = self._build_guardrail_safe_message(message)

        alert = Alert(
            timestamp=now,
            tier_from=EscalationTier.GREEN,
            tier_to=EscalationTier.RED,
            reason="no_caregiver_reached",
            profile_id=self.profile.profile_id,
            message=safe_message,
            detail={
                "incident_id": incident_id,
                "attempted_contacts": attempted_contacts,
                "simulated": simulated,
            },
        )
        self._store(alert)
        return alert


if __name__ == "__main__":
    # Quick manual test
    from memory.profile import load_profile
    from events.event_log import EventLog, EscalationTier
    from pathlib import Path
    from dotenv import load_dotenv
    
    load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")
    profile = load_profile()
    
    event_log = EventLog(profile_id=profile.profile_id, demo_mode=True)
    builder = AlertBuilder(event_log, profile)
    
    print("=== Alert Builder Demo ===")
    
    # Test Yellow from repeat
    alert = builder.build_tier_change_alert(
        EscalationTier.GREEN, EscalationTier.YELLOW, "repeat_threshold",
        {"repeat_count": 3, "sustained_distress": False}
    )
    print(f"Green->Yellow (repeat): {alert.message}")
    
    # Test Yellow from distress
    alert = builder.build_tier_change_alert(
        EscalationTier.GREEN, EscalationTier.YELLOW, "sustained_distress",
        {"repeat_count": 0, "sustained_distress": True}
    )
    print(f"Green->Yellow (distress): {alert.message}")
    
    # Test Orange
    alert = builder.build_tier_change_alert(
        EscalationTier.YELLOW, EscalationTier.ORANGE, "distress_persistence",
        {"repeat_count": 2, "sustained_distress": True}
    )
    print(f"Yellow->Orange: {alert.message}")
    
    # Test Red
    alert = builder.build_tier_change_alert(
        EscalationTier.ORANGE, EscalationTier.RED, "danger_statement",
        {}
    )
    print(f"Orange->Red (danger): {alert.message}")
    
    # Test Green (step down)
    alert = builder.build_tier_change_alert(
        EscalationTier.ORANGE, EscalationTier.GREEN, "step_down",
        {}
    )
    print(f"Orange->Green: {alert.message}")
    
    # Test consent alerts
    alert = builder.build_consent_alert("accepted", "Priya", EscalationTier.YELLOW)
    print(f"Consent accepted: {alert.message}")
    
    alert = builder.build_consent_alert("declined", "Rohan", EscalationTier.ORANGE)
    print(f"Consent declined: {alert.message}")