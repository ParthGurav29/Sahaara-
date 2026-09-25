"""
memory/profile.py

Loads and queries the caregiver-populated "This Is Me" profile.
This module only reads/serves profile data — it never guesses or
generates profile content. Editing is a caregiver action (out of
scope for this file; see dashboard/ for any future edit endpoints).
"""

import json
from pathlib import Path
from typing import Any, Optional

DEFAULT_PROFILE_PATH = Path(__file__).parent / "data" / "asha_profile.json"


class Profile:
    """Wraps a single person's "This Is Me" profile and exposes
    convenience lookups used by the response policy and memory box.
    """

    def __init__(self, data: dict[str, Any]):
        self._data = data

    # ---- Loading ----------------------------------------------------

    @classmethod
    def load(cls, path: Path = DEFAULT_PROFILE_PATH) -> "Profile":
        """Load a profile from disk. Raises if the file is missing or
        malformed — a missing profile should fail loudly, not silently
        fall back to an empty persona.
        """
        if not path.exists():
            raise FileNotFoundError(f"Profile not found at {path}")

        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        cls._validate(data)
        return cls(data)

    @staticmethod
    def _validate(data: dict[str, Any]) -> None:
        required_keys = ["profile_id", "name", "family", "life_story", "likes", "avoid", "daily_routine"]
        missing = [k for k in required_keys if k not in data]
        if missing:
            raise ValueError(f"Profile is missing required keys: {missing}")

    # ---- Basic accessors ---------------------------------------------

    @property
    def profile_id(self) -> str:
        return self._data["profile_id"]

    @property
    def preferred_address(self) -> str:
        return self._data["name"].get("preferred_address", self._data["name"]["full_name"])

    @property
    def full_name(self) -> str:
        return self._data["name"]["full_name"]

    @property
    def hometown(self) -> Optional[str]:
        return self._data["life_story"].get("hometown")

    @property
    def work(self) -> Optional[str]:
        return self._data["life_story"].get("work")

    @property
    def key_events(self) -> list[str]:
        return self._data["life_story"].get("key_events", [])

    # ---- Family --------------------------------------------------------

    def get_family(self) -> list[dict[str, Any]]:
        return self._data.get("family", [])

    def find_family_member(self, name_or_relationship: str) -> Optional[dict[str, Any]]:
        """Look up a family member by name or relationship, case-insensitive.
        Used by intent detection when Asha mentions someone by name
        ("Priya") or by role ("my daughter").
        """
        needle = name_or_relationship.strip().lower()
        for member in self.get_family():
            if member["name"].lower() == needle or member["relationship"].lower() == needle:
                return member
        return None

    # ---- Likes / avoid --------------------------------------------------

    def get_likes(self) -> dict[str, list[str]]:
        return self._data.get("likes", {})

    def get_avoid_topics(self) -> list[str]:
        return self._data.get("avoid", {}).get("topics", [])

    def is_avoid_topic(self, text: str) -> bool:
        """Loose containment check — good enough for the hackathon MVP.
        A production version would use semantic matching, not substring.
        """
        text_lower = text.lower()
        return any(topic.lower() in text_lower or self._keyword_overlap(topic, text_lower)
                    for topic in self.get_avoid_topics())

    @staticmethod
    def _keyword_overlap(topic: str, text_lower: str) -> bool:
        topic_words = {w for w in topic.lower().split() if len(w) > 3}
        return any(w in text_lower for w in topic_words)

    # ---- Routine -------------------------------------------------------

    def get_routine(self) -> dict[str, Any]:
        return self._data.get("daily_routine", {})

    def get_medication_times(self) -> list[str]:
        return self.get_routine().get("medication_times", [])

    # ---- Raw access (escape hatch) --------------------------------------

    def raw(self) -> dict[str, Any]:
        """Full underlying dict, for callers that need something not yet
        exposed as a convenience method (e.g. dashboard display)."""
        return self._data


def load_profile(path: Path = DEFAULT_PROFILE_PATH) -> Profile:
    """Module-level convenience wrapper, so callers can do:
    from memory.profile import load_profile
    """
    return Profile.load(path)


if __name__ == "__main__":
    # Quick manual check: python -m memory.profile
    p = load_profile()
    print(f"Loaded profile for {p.preferred_address} ({p.profile_id})")
    print(f"Hometown: {p.hometown}, Work: {p.work}")
    print(f"Family: {[m['name'] for m in p.get_family()]}")
    print(f"Avoid topics: {p.get_avoid_topics()}")