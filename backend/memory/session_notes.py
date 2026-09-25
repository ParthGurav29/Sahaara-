"""
memory/session_notes.py

Tracks short-term state for the CURRENT conversation session only —
not persisted profile data. This is where repetition detection and
sustained-mood tracking live, both of which feed the escalation ladder
in safety/escalation.py.

Session notes are intentionally ephemeral: they reset when a session
ends. Long-term patterns belong in the caregiver dashboard's summary
job (dashboard/summary_generator.py), not here.
"""

import re
import time
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Optional

# Below this similarity ratio, two turns are considered different questions.
# Tuned loosely for the hackathon MVP — a production version would use
# embedding similarity instead of string matching.
SIMILARITY_THRESHOLD = 0.6

# Repeats within this window count toward "sustained" patterns for escalation.
REPEAT_WINDOW_SECONDS = 15 * 60  # 10-15 min per the escalation spec


@dataclass
class Turn:
    text: str
    emotion: Optional[str]  # e.g. "anxious", "calm", "distressed" — set by intent_emotion.py
    timestamp: float = field(default_factory=time.time)


class SessionNotes:
    """Holds the running state of one conversation session with one person.
    One instance per active session — not shared across people or across
    separate sessions of the same person.
    """

    def __init__(self, profile_id: str):
        self.profile_id = profile_id
        self.turns: list[Turn] = []
        self.session_start: float = time.time()

    # ---- Recording -------------------------------------------------

    def record_turn(self, text: str, emotion: Optional[str] = None) -> Turn:
        turn = Turn(text=_normalize(text), emotion=emotion)
        self.turns.append(turn)
        return turn

    # ---- Repetition detection ----------------------------------------

    def repeat_count(self, text: str, window_seconds: int = REPEAT_WINDOW_SECONDS) -> int:
        """How many times something similar to `text` has been asked within
        the given window (including this ask, if it were recorded).
        Used to decide whether to silently note a repetition — response
        wording itself must NEVER change based on this count (no "I already
        told you"), per docs/guardrails.md.
        """
        normalized = _normalize(text)
        cutoff = time.time() - window_seconds
        count = 0
        for turn in self.turns:
            if turn.timestamp < cutoff:
                continue
            if _similar(normalized, turn.text):
                count += 1
        return count

    def is_repeated_question(self, text: str, min_repeats: int = 2) -> bool:
        """True if this question (or something close to it) has already
        been asked at least `min_repeats` times in the current window.
        """
        return self.repeat_count(text) >= min_repeats

    # ---- Mood / distress tracking --------------------------------------

    def recent_emotions(self, window_seconds: int = REPEAT_WINDOW_SECONDS) -> list[str]:
        cutoff = time.time() - window_seconds
        return [t.emotion for t in self.turns if t.emotion and t.timestamp >= cutoff]

    def sustained_distress(self, window_seconds: int = REPEAT_WINDOW_SECONDS,
                            distress_labels: tuple[str, ...] = ("anxious", "distressed")) -> bool:
        """True if recent emotional turns have been consistently distressed
        for the whole window — this is the Yellow-tier trigger. Escalation
        tier decisions themselves live in safety/escalation.py; this method
        only reports the raw pattern.
        """
        emotions = self.recent_emotions(window_seconds)
        if not emotions:
            return False
        distressed = [e for e in emotions if e in distress_labels]
        # Require most of the recent window to be distressed, not just one turn
        return len(distressed) / len(emotions) >= 0.6 and len(emotions) >= 3

    # ---- Session-level info -----------------------------------------

    def duration_seconds(self) -> float:
        return time.time() - self.session_start

    def turn_count(self) -> int:
        return len(self.turns)

    def reset(self) -> None:
        self.turns.clear()
        self.session_start = time.time()


# ---- Helpers -------------------------------------------------------------

def _normalize(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^\w\s]", "", text)
    text = re.sub(r"\s+", " ", text)
    return text


def _similar(a: str, b: str) -> bool:
    return SequenceMatcher(None, a, b).ratio() >= SIMILARITY_THRESHOLD


if __name__ == "__main__":
    # Quick manual check: python -m memory.session_notes
    notes = SessionNotes(profile_id="asha_demo_001")
    notes.record_turn("When is my son coming?", emotion="anxious")
    notes.record_turn("When will Rohan be here?", emotion="anxious")
    notes.record_turn("Is Rohan coming soon?", emotion="anxious")

    print("Repeat count:", notes.repeat_count("When is my son coming?"))
    print("Is repeated:", notes.is_repeated_question("When is my son coming?"))
    print("Sustained distress:", notes.sustained_distress())