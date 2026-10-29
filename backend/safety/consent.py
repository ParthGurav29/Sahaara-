"""
safety/consent.py

Consent state machine for "ask before escalating" rule.
When escalation reaches Yellow/Orange, Sahaara must ASK before contacting
a family member. The next user utterance is interpreted as accept/decline/timeout.

This module is deterministic and works identically for text and voice
since both go through the shared conversation.py pipeline.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from events.event_log import EventLog, EventType, ConsentOutcome, EscalationTier

# ---- Continuation state machine -----------------------------------------
# After a long answer is truncated to 2-4 sentences with a "Would you like
# me to go on?" check-in, the next utterance is interpreted as yes/no/
# timeout, exactly like the consent state machine.


class ContinuationState(str, Enum):
    IDLE = "idle"
    PENDING = "pending"  # Waiting for "go on" / "no" / timeout


@dataclass
class ContinuationContext:
    """Details about a pending continuation offer."""
    remaining_text: str      # The rest of the answer to deliver on "yes"
    topic_note: str          # Brief topic note for context
    asked_at: float
    timeout_seconds: float


class ContinuationManager:
    """
    Manages the long-answer continuation flow.

    When Sahaara's answer is too long for voice (2-4 sentence limit), she
    delivers the short version and asks "Would you like me to go on?".
    The next user utterance is interpreted as:

    - Yes ("go on", "yes", "tell me more"): deliver the next chunk
    - No or topic change: drop it, process the new utterance normally
    - Silence/timeout: treat as no, never consume the turn
    """

    YES_PATTERNS = [
        r"\byes\b", r"\byeah\b", r"\byep\b", r"\byup\b", r"\byea\b",
        r"\bgo on\b", r"\bgo ahead\b", r"\btell me more\b",
        r"\bcontinue\b", r"\bplease\b", r"\bsure\b", r"\bok\b",
        r"\bokay\b",
    ]

    NO_PATTERNS = [
        r"\bno\b", r"\bnope\b", r"\bnah\b", r"\bnot\b",
        r"\bdon'?t\b", r"\bnot now\b", r"\bmaybe later\b",
        r"\bstop\b", r"\bthat'?s (fine|ok|okay)\b",
        r"\bdon'?t bother\b", r"\bnever mind\b", r"\bnevermind\b",
    ]

    def __init__(
        self,
        event_log: Optional[EventLog] = None,
        timeout_seconds: float = 10.0,
    ):
        self.event_log = event_log
        self.timeout_seconds = timeout_seconds

        self.state = ContinuationState.IDLE
        self.context: Optional[ContinuationContext] = None
        self._timeout_at: Optional[float] = None

    def effective_timeout(self) -> float:
        return self.timeout_seconds

    def ask_continuation(
        self,
        remaining_text: str,
        topic_note: str,
    ) -> None:
        """Start a continuation request. Call this after delivering a
        truncated answer with "Would you like me to go on?"."""
        now = time.time()
        self.state = ContinuationState.PENDING
        self.context = ContinuationContext(
            remaining_text=remaining_text,
            topic_note=topic_note,
            asked_at=now,
            timeout_seconds=self.effective_timeout(),
        )
        self._timeout_at = now + self.effective_timeout()

    def check_timeout(self) -> bool:
        """Check if pending continuation has timed out. Returns True if timed out."""
        if self.state != ContinuationState.PENDING or self._timeout_at is None:
            return False

        if time.time() >= self._timeout_at:
            self._handle_timeout()
            return True
        return False

    def _handle_timeout(self) -> None:
        """Handle continuation timeout - treat as implicit decline."""
        self.state = ContinuationState.IDLE
        self._reset_context()

    def _reset_context(self) -> None:
        """Clear continuation context after resolution."""
        self.context = None
        self._timeout_at = None

    def interpret_response(self, user_message: str) -> ContinuationState:
        """
        Interpret the user's response to a pending continuation offer.
        Returns the new continuation state.

        Only call this when state == PENDING.
        """
        if self.state != ContinuationState.PENDING:
            return self.state

        # Check timeout first
        if self.check_timeout():
            return ContinuationState.IDLE

        text_lower = user_message.lower().strip()

        # Check yes FIRST (more specific)
        for pattern in self.YES_PATTERNS:
            if re.search(pattern, text_lower):
                self.state = ContinuationState.IDLE
                self._reset_context()
                return ContinuationState.IDLE

        # Check no / topic shift
        for pattern in self.NO_PATTERNS:
            if re.search(pattern, text_lower):
                self.state = ContinuationState.IDLE
                self._reset_context()
                return ContinuationState.IDLE

        # Ambiguous / not a clear answer - check for topic shift
        # A short response is likely an answer; a long new question is a
        # topic shift -> treat as no
        if len(text_lower.split()) <= 3:
            # Very short, likely a muted yes/no - treat as no to be safe
            self.state = ContinuationState.IDLE
            self._reset_context()
            return ContinuationState.IDLE

        question_words = ["where", "when", "what", "who", "why", "how", "is", "are", "do", "can"]
        if any(text_lower.startswith(w + " ") for w in question_words):
            # Topic shift - treat as no, but don't consume the turn
            self.state = ContinuationState.IDLE
            self._reset_context()
            return ContinuationState.IDLE

        # Unclear - stay pending, re-ask
        return ContinuationState.PENDING

    def get_remaining_text(self) -> Optional[str]:
        """Get the remaining text to deliver on 'yes', or None if not pending."""
        if self.state == ContinuationState.PENDING and self.context:
            return self.context.remaining_text
        return None

    def reset(self) -> None:
        """Full reset to idle."""
        self.state = ContinuationState.IDLE
        self._reset_context()


# Keep ConsentManager below (unchanged)


class ConsentState(str, Enum):
    IDLE = "idle"
    PENDING = "pending"
    ACCEPTED = "accepted"
    DECLINED = "declined"
    TIMED_OUT = "timed_out"


@dataclass
class ConsentContext:
    """Details about the pending consent request."""
    contact_name: str
    contact_relationship: str
    tier_when_asked: EscalationTier
    question_text: str
    asked_at: float
    timeout_seconds: float


class ConsentManager:
    """
    Manages the consent flow for escalation contact.
    
    Flow:
    1. Escalation engine decides a contact should be called (Yellow/Orange)
    2. Sahaara's response includes a consent question: "Would you like me to call [Name]?"
    3. ConsentManager.ask_consent() is called with the contact details
    4. Next user utterance is passed to interpret_response()
    5. Returns ACCEPTED/DECLINED/TIMED_OUT, which determines action
    """
    
    # Keywords for accept/decline classification
    ACCEPT_PATTERNS = [
        r"\byes\b", r"\byeah\b", r"\byep\b", r"\byup\b", r"\byea\b",
        r"\bplease\b", r"\bok\b", r"\bokay\b", r"\bsure\b", r"\bfine\b",
        r"\bgo ahead\b", r"\bcall (him|her|them)\b",
        r"\byes please\b", r"\bthat'?d be (good|nice|great)\b",
        r"\bi'?d like that\b", r"\bwould like\b",
    ]
    
    DECLINE_PATTERNS = [
        r"\bno\b", r"\bnope\b", r"\bnah\b",
        # A bare \bnot\b used to live here. It matched "not able to breathe"
        # and "I'm not feeling well" and recorded them as her refusing help.
        # Only phrases that are actually refusals belong in this list; genuine
        # negatives are handled by the patterns below.
        r"\bnot (now|really|right now|yet)\b",
        r"\bdon'?t\b", r"\bdon'?t (call|bother)\b",
        r"\bno (thanks|thank you)\b", r"\bno need\b",
        r"\bnot now\b", r"\bmaybe later\b", r"\bstop\b",
        r"\bleave (me|it) alone\b", r"\bdont want\b", r"\bdo not want\b",
    ]
    
    def __init__(
        self,
        event_log: Optional[EventLog] = None,
        timeout_seconds: float = 30.0,  # Real time; demo mode compresses
        demo_mode: bool = False,
        demo_multiplier: float = 300.0,
    ):
        self.event_log = event_log
        self.timeout_seconds = timeout_seconds
        self.demo_mode = demo_mode
        self.demo_multiplier = demo_multiplier
        
        self.state = ConsentState.IDLE
        self.context: Optional[ConsentContext] = None
        self._timeout_at: Optional[float] = None
    
    def effective_timeout(self) -> float:
        if self.demo_mode:
            return self.timeout_seconds / self.demo_multiplier
        return self.timeout_seconds
    
    @property
    def is_pending(self) -> bool:
        return self.state == ConsentState.PENDING
    
    @property
    def is_active(self) -> bool:
        return self.state in (ConsentState.PENDING, ConsentState.ACCEPTED, ConsentState.DECLINED)
    
    def ask_consent(
        self,
        contact_name: str,
        contact_relationship: str,
        tier: EscalationTier,
        question_text: str,
    ) -> None:
        """Start a consent request. Call this when Sahaara asks the question."""
        if self.state != ConsentState.IDLE:
            # Already have a pending consent - log and override
            if self.event_log:
                self.event_log.log_system_health(
                    message=f"New consent request overrides pending one for {self.context.contact_name if self.context else 'unknown'}",
                    severity="warning",
                )
        
        now = time.time()
        self.state = ConsentState.PENDING
        self.context = ConsentContext(
            contact_name=contact_name,
            contact_relationship=contact_relationship,
            tier_when_asked=tier,
            question_text=question_text,
            asked_at=now,
            timeout_seconds=self.effective_timeout(),
        )
        self._timeout_at = now + self.effective_timeout()
        
        if self.event_log:
            self.event_log.log_consent_asked(
                question=question_text,
                contact_name=contact_name,
                tier=tier,
            )
    
    def check_timeout(self) -> bool:
        """Check if pending consent has timed out. Returns True if timed out."""
        if self.state != ConsentState.PENDING or self._timeout_at is None:
            return False
        
        if time.time() >= self._timeout_at:
            self._handle_timeout()
            return True
        return False
    
    def _handle_timeout(self) -> None:
        """Handle consent timeout - treat as implicit decline for safety."""
        self.state = ConsentState.TIMED_OUT
        
        if self.context and self.event_log:
            self.event_log.log_consent_answered(
                outcome=ConsentOutcome.TIMEOUT,
                contact_name=self.context.contact_name,
            )
            self.event_log.log_system_health(
                message=f"Consent timeout for {self.context.contact_name} - treated as decline",
                severity="info",
            )
        
        self._reset_context()
    
    def _reset_context(self) -> None:
        """Clear consent context after resolution."""
        self.context = None
        self._timeout_at = None
    
    def interpret_response(self, user_message: str) -> ConsentState:
        """
        Interpret user's response to a pending consent question.
        Returns the new consent state.
        
        Only call this when state == PENDING.
        """
        if self.state != ConsentState.PENDING:
            return self.state
        
        # Check timeout first
        if self.check_timeout():
            return ConsentState.TIMED_OUT
        
        text_lower = user_message.lower().strip()
        
        # Check for decline FIRST (more specific)
        import re
        for pattern in self.DECLINE_PATTERNS:
            if re.search(pattern, text_lower):
                self._handle_decline()
                return ConsentState.DECLINED
        
        # Check for accept — but never read an acceptance out of a negated
        # sentence. "I'm not sure" contains "sure" and would otherwise be
        # recorded as her agreeing to a call (this became reachable once the
        # bare \bnot\b decline pattern was removed above). Ambiguous is safer
        # than a false accept: it asks again instead of acting.
        negated = re.search(r"\bnot\b|\bnever\b|n't\b", text_lower) is not None
        if not negated:
            for pattern in self.ACCEPT_PATTERNS:
                if re.search(pattern, text_lower):
                    self._handle_accept()
                    return ConsentState.ACCEPTED
        
        # Ambiguous / not a clear answer - stay pending
        # But check if this looks like a completely new topic (topic shift)
        # If user says something unrelated, it's a timeout/decline for safety
        if self._looks_like_topic_shift(text_lower):
            self._handle_decline()
            return ConsentState.DECLINED
        
        return ConsentState.PENDING
    
    def _looks_like_topic_shift(self, text: str) -> bool:
        """
        Heuristic: if the user's response doesn't contain any yes/no
        markers AND is a substantial new statement, treat as topic shift.
        """
        # Very short responses are likely answers
        if len(text.split()) <= 3:
            return False
        
        # If it contains question words, it's a new question
        question_words = ["where", "when", "what", "who", "why", "how", "is", "are", "do", "can"]
        if any(text.startswith(w + " ") for w in question_words):
            return True
        
        # If it's a statement about something else entirely
        # (This is a loose heuristic for demo purposes)
        return False
    
    def _handle_accept(self) -> None:
        """Handle explicit consent acceptance."""
        if not self.context:
            return
        
        self.state = ConsentState.ACCEPTED
        
        if self.event_log:
            self.event_log.log_consent_answered(
                outcome=ConsentOutcome.ACCEPTED,
                contact_name=self.context.contact_name,
            )
        
        self._reset_context()
    
    def supersede(self, reason: str = "urgent_override") -> bool:
        """Abandon a pending question *without* recording an answer.

        Used when the person says something safety-critical while a consent
        question is still pending. The question was never answered — the
        emergency took priority — so it is logged as ``superseded``, never as
        ``declined``. ``declined`` would record that she refused help, which
        would be false and would make the dashboard lie about what happened.

        Returns True if a pending question was actually abandoned.
        """
        if self.state != ConsentState.PENDING:
            return False

        contact_name = self.context.contact_name if self.context else "caregiver"
        self.state = ConsentState.IDLE

        if self.event_log:
            self.event_log.log_consent_answered(
                outcome=ConsentOutcome.SUPERSEDED,
                contact_name=contact_name,
                reason=reason,
            )
            self.event_log.log_system_health(
                message=(
                    f"Consent question for {contact_name} abandoned by an urgent "
                    f"statement ({reason}) — not recorded as a decline"
                ),
                severity="warning",
            )

        self._reset_context()
        return True

    def _handle_decline(self) -> None:
        """Handle explicit consent decline."""
        if not self.context:
            return
        
        self.state = ConsentState.DECLINED
        
        if self.event_log:
            self.event_log.log_consent_answered(
                outcome=ConsentOutcome.DECLINED,
                contact_name=self.context.contact_name,
            )
        
        self._reset_context()
    
    def get_status(self) -> dict:
        """Get current consent status for dashboard/API."""
        return {
            "state": self.state.value,
            "pending_contact": self.context.contact_name if self.context else None,
            "pending_relationship": self.context.contact_relationship if self.context else None,
            "question_text": self.context.question_text if self.context else None,
            "time_remaining": max(0, self._timeout_at - time.time()) if self._timeout_at else None,
        }
    
    def reset(self) -> None:
        """Full reset to idle."""
        self.state = ConsentState.IDLE
        self._reset_context()


if __name__ == "__main__":
    # Quick manual test
    from events.event_log import EventLog
    from events.event_log import EscalationTier
    
    event_log = EventLog(profile_id="test", demo_mode=True)
    manager = ConsentManager(event_log=event_log, demo_mode=True, demo_multiplier=300.0)
    
    print("=== Consent Manager Demo ===")
    print(f"Initial state: {manager.state}")
    
    # Ask consent
    manager.ask_consent(
        contact_name="Priya",
        contact_relationship="daughter",
        tier=EscalationTier.YELLOW,
        question_text="Would you like me to call Priya?",
    )
    print(f"After ask: {manager.state}, context={manager.context.contact_name}")
    
    # Simulate timeout (demo: 30s/300 = 0.1s)
    import time
    time.sleep(0.15)
    manager.check_timeout()
    print(f"After timeout: {manager.state}")
    
    # Reset and test accept
    manager.reset()
    manager.ask_consent("Rohan", "son", EscalationTier.ORANGE, "Call Rohan?")
    result = manager.interpret_response("Yes, please call him")
    print(f"After 'Yes, please call him': {result}")
    
    # Reset and test decline
    manager.reset()
    manager.ask_consent("Vikram", "husband", EscalationTier.YELLOW, "Call Vikram?")
    result = manager.interpret_response("No, don't call")
    print(f"After 'No, don't call': {result}")
    
    # Reset and test ambiguous
    manager.reset()
    manager.ask_consent("Priya", "daughter", EscalationTier.ORANGE, "Call Priya?")
    result = manager.interpret_response("I'm not sure")
    print(f"After 'I'm not sure': {result} (still pending)")
    manager.check_timeout()
    print(f"After timeout: {manager.state}")