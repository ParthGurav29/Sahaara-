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
        name = self._data["name"]
        if isinstance(name, dict):
            return name.get("preferred_address", name.get("full_name", "Asha"))
        # Extract first name from string like "Asha Kulkarni"
        if isinstance(name, str):
            return name.split()[0] if name else "Asha"
        return "Asha"

    @property
    def full_name(self) -> str:
        name = self._data["name"]
        if isinstance(name, dict):
            return name.get("full_name", name.get("preferred_address", "Asha"))
        return str(name) if name else "Asha"

    @property
    def hometown(self) -> Optional[str]:
        ls = self._data.get("life_story")
        if isinstance(ls, dict):
            return ls.get("hometown")
        if isinstance(ls, str):
            # Extract hometown from string like "Born in Pune, 1952..."
            import re
            match = re.search(r"Born in ([^,]+)", ls)
            if match:
                return match.group(1).strip()
            # Try to find city names
            for city in ["Pune", "Mumbai", "Delhi", "Bangalore", "Chennai", "Kolkata", "Hyderabad"]:
                if city.lower() in ls.lower():
                    return city
        return None

    @property
    def work(self) -> Optional[str]:
        ls = self._data.get("life_story")
        if isinstance(ls, dict):
            return ls.get("work")
        if isinstance(ls, str):
            # Extract work from string like "Retired school teacher"
            import re
            # Look for patterns like "retired X", "worked as X", "was a X"
            patterns = [
                r"retired\s+([^.]+)",
                r"worked as\s+([^.]+)",
                r"was a\s+([^.]+)",
                r"(school teacher|teacher|doctor|engineer|nurse|farmer|shopkeeper)",
            ]
            for pattern in patterns:
                match = re.search(pattern, ls, re.IGNORECASE)
                if match:
                    return match.group(1).strip() if match.lastindex else match.group(0).strip()
        return None

    @property
    def key_events(self) -> list[str]:
        ls = self._data.get("life_story")
        if isinstance(ls, dict):
            return ls.get("key_events", [])
        return []

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
        # Strip possessive 's from words (e.g., "vikram's" -> "vikram")
        import re
        topic_words = {re.sub(r"'s$", "", w) for w in topic.lower().split() if len(w) > 3}
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


def resolve_contacts(profile: Profile) -> list[dict[str, Any]]:
    """Return family members sorted by (notify_order, relationship priority, arrival order),
    filtered to may_call=true AND phone present.
    
    Members skipped for either reason appear in the incident as 'skipped' with a reason
    — the dashboard must be able to say "No phone number saved for Rohan — no call was placed."
    """
    members = profile.get_family()
    
    # Relationship priority: primary caregivers first
    def relationship_priority(rel: str) -> int:
        rel_lower = rel.lower()
        if "caregiver" in rel_lower or "son" in rel_lower or "daughter" in rel_lower:
            return 1
        if "spouse" in rel_lower or "husband" in rel_lower or "wife" in rel_lower or "partner" in rel_lower:
            return 2
        return 3
    
    # Filter and sort
    callable_members = []
    skipped = []
    
    for i, member in enumerate(members):
        may_call = member.get("may_call", False)
        phone = member.get("phone")
        
        if not may_call:
            skipped.append({
                **member,
                "skip_reason": "may_call_false",
            })
        elif not phone:
            skipped.append({
                **member,
                "skip_reason": "no_phone",
            })
        else:
            callable_members.append({
                **member,
                "original_index": i,
            })
    
    # Sort: notify_order first, then relationship priority, then arrival order
    callable_members.sort(key=lambda m: (
        m.get("notify_order", 99),
        relationship_priority(m.get("relationship", "")),
        m.get("original_index", 99),
    ))
    
    # Add skipped members at the end for dashboard visibility
    for member in skipped:
        callable_members.append(member)
    
    return callable_members


def get_callable_contacts(profile: Profile) -> list[dict[str, Any]]:
    """Return only contacts that can actually be called (may_call=true AND phone present)."""
    return [m for m in resolve_contacts(profile) if m.get("may_call") and m.get("phone")]


if __name__ == "__main__":
    # Quick manual check: python -m memory.profile
    p = load_profile()
    print(f"Loaded profile for {p.preferred_address} ({p.profile_id})")
    print(f"Hometown: {p.hometown}, Work: {p.work}")
    print(f"Family: {[m['name'] for m in p.get_family()]}")
    print(f"Avoid topics: {p.get_avoid_topics()}")