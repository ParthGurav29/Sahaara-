"""
safety/consent.py

State machine for the "ask before escalating" pattern.
"""

import re
import time
from enum import Enum

from safety.event_log import EventLog

class ConsentState(str, Enum):
    NONE = "none"           # no consent pending
    PENDING = "pending"     # asked "would you like me to call Rohan?"
    ACCEPTED = "accepted"   # user said yes
    DECLINED = "declined"   # user said no
    TIMED_OUT = "timed_out" # no response within timeout

ACCEPT_PATTERNS = [
    r"\byes\b", r"\bplease\b", r"\bokay\b", r"\bok\b", r"\bsure\b", r"\bdo it\b", r"\bcall\b"
]

DECLINE_PATTERNS = [
    r"\bno\b", r"\bdon't\b", r"\bstop\b", r"\bnever mind\b", r"\bnot now\b", r"\blater\b"
]

class ConsentManager:
    def __init__(self, event_log: EventLog, timeout_seconds: float = 60.0):
        self.event_log = event_log
        self.timeout_seconds = timeout_seconds
        self.state = ConsentState.NONE
        self._ask_time = 0.0

    def ask_consent(self, action: str) -> None:
        self.state = ConsentState.PENDING
        self._ask_time = time.time()
        self.event_log.record("consent_asked", {"action": action})

    def interpret_response(self, text: str) -> ConsentState:
        if self.state != ConsentState.PENDING:
            return self.state

        text_lower = text.lower()
        is_accept = any(re.search(p, text_lower) for p in ACCEPT_PATTERNS)
        is_decline = any(re.search(p, text_lower) for p in DECLINE_PATTERNS)

        if is_accept and not is_decline:
            self.state = ConsentState.ACCEPTED
            self.event_log.record("consent_answered", {"accepted": True, "text": text})
        elif is_decline:
            self.state = ConsentState.DECLINED
            self.event_log.record("consent_answered", {"accepted": False, "text": text})
        else:
            # Ambiguous response - treat as not answering the consent directly right now,
            # but leave it pending until timeout.
            pass

        return self.state

    def check_timeout(self) -> ConsentState:
        if self.state == ConsentState.PENDING:
            if time.time() - self._ask_time > self.timeout_seconds:
                self.state = ConsentState.TIMED_OUT
                self.event_log.record("consent_answered", {"timed_out": True})
        return self.state

    def reset(self) -> None:
        self.state = ConsentState.NONE

