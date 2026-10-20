"""
memory/suggestions.py

Detects potential profile facts from conversation turns.
Uses regex heuristics — lightweight, runs inline during conversation.
Suggestions are queued for caregiver approval. Nothing enters
the profile without explicit caregiver action.

Rule: The profile is never AI-guessed. Every suggestion requires
caregiver approval before being written.
"""

import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

SUGGESTIONS_PATH = Path(__file__).parent / "data" / "suggested_facts.json"


@dataclass
class Suggestion:
    suggestion_id: str
    timestamp: float
    type: str  # "like", "preference", "memory", "routine", "person"
    text: str  # what Asha said
    suggested_field: str  # e.g. "likes.music", "key_events", "daily_routine"
    suggested_value: str  # what to add
    source_turn: str  # the user message that triggered this
    confidence: str = "medium"  # "high", "medium", "low"
    
    def to_dict(self) -> dict:
        return {
            "suggestion_id": self.suggestion_id,
            "timestamp": self.timestamp,
            "timestamp_iso": datetime.fromtimestamp(self.timestamp).isoformat(),
            "type": self.type,
            "text": self.text,
            "suggested_field": self.suggested_field,
            "suggested_value": self.suggested_value,
            "source_turn": self.source_turn,
            "confidence": self.confidence,
        }


# Patterns for detecting preferences and facts from user messages
PREFERENCE_PATTERNS = [
    # Music likes
    (r"\bi love (?:the song|listening to|hearing) (.+?)(?:\.|,|$|\band\b)", "like", "likes.music"),
    (r"\bmy favorite (?:song|raga|music|tune) is (.+?)(?:\.|,|$|\band\b)", "like", "likes.music"),
    (r"\bi (?:really |so )?love (.+?)(?:\.|,|$|\band\b)", "preference", "likes.topics"),
    # Food/drink
    (r"\bi (?:always |love to |like to )?(?:have|drink) (.+?)(?: every| in the| at |\.|,|$)", "routine", "likes.food"),
    (r"\bmy favorite (?:food|dish|meal) is (.+?)(?:\.|,|$)", "like", "likes.food"),
    # Memories / teaching stories
    (r"\bi (?:used to|once) (?:teach|taught) (.+?)(?:\.|,|$)", "memory", "key_events"),
    (r"\bwhen i was (?:teaching|a teacher) (.+?)(?:\.|,|$)", "memory", "key_events"),
    (r"\bi remember (?:when|teaching) (.+?)(?:\.|,|$)", "memory", "key_events"),
    # Routines
    (r"\bi (?:always|usually|like to) (.+?)(?: every|each| at | in the |\.|,|$)", "routine", "daily_routine"),
    (r"\b(?:every|each) (?:morning|afternoon|evening|day) i (.+?)(?:\.|,|$)", "routine", "daily_routine"),
    # People
    (r"\bmy (?:friend|neighbor|grandchild) (\w+)", "person", "family"),
    # Garden / activities
    (r"\bi (?:love|enjoy) (?:the |my )?(garden|walking|reading|cooking|knitting|painting)", "like", "likes.topics"),
]


class SuggestionQueue:
    """Manages pending profile fact suggestions for caregiver review."""
    
    def __init__(self, path: Path = SUGGESTIONS_PATH):
        self.path = path
        self._suggestions: list[Suggestion] = []
        self._counter: int = 0
        self._load()
    
    def _load(self):
        if self.path.exists():
            try:
                with open(self.path) as f:
                    data = json.load(f)
                self._suggestions = []
                for d in data:
                    s = Suggestion(
                        suggestion_id=d["suggestion_id"],
                        timestamp=d["timestamp"],
                        type=d["type"],
                        text=d["text"],
                        suggested_field=d["suggested_field"],
                        suggested_value=d["suggested_value"],
                        source_turn=d["source_turn"],
                        confidence=d.get("confidence", "medium"),
                    )
                    self._suggestions.append(s)
                if self._suggestions:
                    last_id = self._suggestions[-1].suggestion_id
                    num = int(last_id.split("_")[-1]) if "_" in last_id else 0
                    self._counter = num
            except (json.JSONDecodeError, KeyError):
                self._suggestions = []
    
    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "w") as f:
            json.dump([s.to_dict() for s in self._suggestions], f, indent=2)
    
    def add(self, suggestion: Suggestion) -> Suggestion:
        self._counter += 1
        suggestion.suggestion_id = f"sug_{self._counter}"
        self._suggestions.append(suggestion)
        self._save()
        return suggestion
    
    def get_all(self) -> list[Suggestion]:
        return list(self._suggestions)
    
    def get_by_id(self, suggestion_id: str) -> Optional[Suggestion]:
        for s in self._suggestions:
            if s.suggestion_id == suggestion_id:
                return s
        return None
    
    def remove(self, suggestion_id: str) -> bool:
        for i, s in enumerate(self._suggestions):
            if s.suggestion_id == suggestion_id:
                self._suggestions.pop(i)
                self._save()
                return True
        return False
    
    def clear(self):
        self._suggestions.clear()
        self._save()


def detect_suggestions(user_message: str, existing_likes: dict, existing_family: list) -> list[Suggestion]:
    """Scan a user message for potential profile facts.
    
    Returns list of Suggestion objects (unsaved — caller adds to queue).
    Deduplicates against existing profile data to avoid suggesting
    things already in the profile.
    """
    import re
    
    text_lower = user_message.lower()
    suggestions = []
    
    # Get existing values for dedup
    existing_music = [m.lower() for m in existing_likes.get("music", [])]
    existing_topics = [t.lower() for t in existing_likes.get("topics", [])]
    existing_food = [f.lower() for f in existing_likes.get("food", [])]
    existing_names = [m["name"].lower() for m in existing_family]
    
    for pattern, stype, sfield in PREFERENCE_PATTERNS:
        match = re.search(pattern, text_lower)
        if not match:
            continue
        
        value = match.group(1).strip().rstrip(".").rstrip(",")
        if not value or len(value) < 2:
            continue
        
        # Dedup against existing profile
        if sfield == "likes.music" and any(value.lower() in m for m in existing_music):
            continue
        if sfield == "likes.topics" and any(value.lower() in t for t in existing_topics):
            continue
        if sfield == "likes.food" and any(value.lower() in f for f in existing_food):
            continue
        if sfield == "family" and value.lower() in existing_names:
            continue
        
        suggestions.append(Suggestion(
            suggestion_id="",
            timestamp=time.time(),
            type=stype,
            text=user_message,
            suggested_field=sfield,
            suggested_value=value,
            source_turn=user_message,
            confidence="medium",
        ))
    
    return suggestions


if __name__ == "__main__":
    from memory.profile import load_profile
    
    profile = load_profile()
    likes = profile.get_likes()
    family = profile.get_family()
    
    test_messages = [
        "I love the song Lag Ja Gale.",
        "My favorite raga is Yaman.",
        "I always have masala chai at 4 o'clock.",
        "I used to teach science to little children.",
        "I remember when my students gave me flowers.",
        "Hello, how are you?",
    ]
    
    for msg in test_messages:
        sug = detect_suggestions(msg, likes, family)
        if sug:
            for s in sug:
                print(f"'{msg}' -> {s.suggested_field} = '{s.suggested_value}'")
        else:
            print(f"'{msg}' -> no suggestion")