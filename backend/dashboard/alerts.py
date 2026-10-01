"""
dashboard/alerts.py

Context-bearing alerts generated on escalation tier changes.
"""

import uuid
from dataclasses import dataclass
from typing import Optional, Dict, Any, List

from memory.profile import Profile
from safety.event_log import EventLog, Event
from safety.escalation import TierDecision, EscalationTier
from safety.guardrails import check_guardrails

@dataclass
class Alert:
    alert_id: str
    timestamp: float
    tier: str
    previous_tier: str
    headline: str       # "Asha has asked about Priya 3 times..."
    detail: str         # "...and has seemed anxious for ~12 min."
    sahaara_tried: str  # "Sahaara offered the Memory Box clip."
    suggested_action: str  # "A call might help."
    raw_events: List[Dict[str, Any]]
    
    def to_json(self):
        return {
            "alert_id": self.alert_id,
            "timestamp": self.timestamp,
            "tier": self.tier,
            "previous_tier": self.previous_tier,
            "headline": self.headline,
            "detail": self.detail,
            "sahaara_tried": self.sahaara_tried,
            "suggested_action": self.suggested_action,
            "raw_events": self.raw_events
        }

class AlertBuilder:
    def __init__(self, event_log: EventLog, profile: Profile):
        self.event_log = event_log
        self.profile = profile

    def build_on_tier_change(self, decision: TierDecision) -> Optional[Alert]:
        if not decision.changed:
            return None
            
        if decision.tier == EscalationTier.GREEN:
            return None # We don't alert on stepping down or green.
            
        name = self.profile.preferred_address
        
        # Default placeholder texts
        headline = f"{name} is experiencing distress."
        detail = "A pattern was detected."
        sahaara_tried = "Sahaara is attempting to comfort."
        suggested_action = "Please check in."
        
        recent_events = [e.to_json() for e in self.event_log.events_since(decision.timestamp - 900)]
        
        if decision.tier == EscalationTier.YELLOW:
            if "repeated" in decision.reason:
                headline = f"{name} has asked a similar question several times recently."
            else:
                headline = f"{name} has seemed consistently anxious."
            detail = decision.reason
            sahaara_tried = "Sahaara validated the feelings and offered a redirection choice."
            suggested_action = "No immediate action required, but keep an eye on the dashboard."
            
        elif decision.tier == EscalationTier.ORANGE:
            headline = f"{name}'s distress has persisted despite redirection."
            detail = decision.reason
            
            # Check if memory box was offered
            turns = [e for e in recent_events if e["event_type"] == "turn"]
            offered_box = any(t["detail"].get("memory_box_offered") for t in turns)
            if offered_box:
                sahaara_tried = "Sahaara offered the Memory Box clip."
            else:
                sahaara_tried = "Sahaara validated and grounded the conversation."
                
            suggested_action = "A call might help."
            
        elif decision.tier == EscalationTier.RED:
            if "unresponsive" in decision.reason.lower():
                headline = f"{name} has been unresponsive after a prompt."
                detail = "Prolonged silence detected."
            else:
                headline = f"A danger pattern was detected in {name}'s speech."
                detail = decision.reason
            sahaara_tried = "Sahaara directed the conversation toward emergency support."
            suggested_action = "Contact emergency services or check in immediately."

        # Pass caregiver-facing text through guardrails. 
        # Even though we authored it, we enforce the rule.
        full_text = f"{headline} {detail} {sahaara_tried} {suggested_action}"
        guardrail = check_guardrails(full_text, self.profile)
        if not guardrail.passed:
            # If our alert logic violates a guardrail (e.g. diagnostic language),
            # fall back to a safe generic alert.
            headline = f"Notification regarding {name}."
            detail = "A conversation pattern crossed a threshold."
            sahaara_tried = "Sahaara provided standard support."
            suggested_action = "Review recent session notes if possible."

        return Alert(
            alert_id=str(uuid.uuid4()),
            timestamp=decision.timestamp,
            tier=decision.tier.value,
            previous_tier=decision.previous_tier.value,
            headline=headline,
            detail=detail,
            sahaara_tried=sahaara_tried,
            suggested_action=suggested_action,
            raw_events=recent_events
        )

