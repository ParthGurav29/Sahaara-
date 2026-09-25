"""
memory/memory_box.py

Maps conversation triggers ("I miss my daughter") to a specific
pre-recorded Memory Box clip from a family member, and generates the
offer line Sahaara uses to introduce it.

Hard rule (see docs/guardrails.md): Sahaara always OFFERS the clip
("Would you like to hear something Priya left for you?") — it never
plays it unprompted, and it never pretends to BE the family member.
The clip is clearly framed as something the family member recorded.
"""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from memory.profile import Profile

CLIP_DIR = Path(__file__).parent / "data" / "memory_box"

# Generic longing/missing phrases. Combined with a family member's name
# or relationship (pulled from the profile) to detect a trigger.
LONGING_PATTERNS = [
    r"\bmiss(ing)?\b",
    r"\bwhere is\b",
    r"\bwhen is .* coming\b",
    r"\bwant to (see|talk to|call)\b",
    r"\bhaven'?t (seen|heard from)\b",
]


@dataclass
class MemoryBoxMatch:
    family_member_name: str
    relationship: str
    clip_filename: str
    clip_path: Path
    offer_line: str


class MemoryBox:
    """Reads Memory Box clip references off the loaded profile and
    matches incoming conversation text against them.
    """

    def __init__(self, profile: Profile, clip_dir: Path = CLIP_DIR):
        self.profile = profile
        self.clip_dir = clip_dir
        self._longing_re = re.compile("|".join(LONGING_PATTERNS), re.IGNORECASE)

    def has_clip_for(self, family_member: dict) -> bool:
        return bool(family_member.get("memory_box_clip"))

    def available_clips(self) -> list[dict]:
        """All family members who have a recorded clip, per the profile."""
        return [m for m in self.profile.get_family() if self.has_clip_for(m)]

    def match(self, text: str) -> Optional[MemoryBoxMatch]:
        """Check if the given text is a trigger for a Memory Box clip.
        Looks for a longing/missing pattern AND a reference to a family
        member (by name or relationship) who has a clip on file.
        Returns None if no match — caller should fall through to the
        normal response policy in that case, this is not a required step.
        """
        if not self._longing_re.search(text):
            return None

        text_lower = text.lower()
        for member in self.available_clips():
            name = member["name"].lower()
            relationship = member["relationship"].lower()
            if name in text_lower or relationship in text_lower:
                return self._build_match(member)

        return None

    def _build_match(self, member: dict) -> MemoryBoxMatch:
        clip_filename = member["memory_box_clip"]
        clip_path = self.clip_dir / clip_filename
        offer_line = (
            f"Would you like to hear something {member['name']} left for you?"
        )
        return MemoryBoxMatch(
            family_member_name=member["name"],
            relationship=member["relationship"],
            clip_filename=clip_filename,
            clip_path=clip_path,
            offer_line=offer_line,
        )

    def clip_exists_on_disk(self, match: MemoryBoxMatch) -> bool:
        """Sanity check before trying to play a clip — a missing audio
        file should degrade gracefully (fall back to a text-only
        response), not crash the voice pipeline mid-conversation.
        """
        return match.clip_path.exists()


if __name__ == "__main__":
    # Quick manual check: python -m memory.memory_box
    from memory.profile import load_profile

    profile = load_profile()
    box = MemoryBox(profile)

    test_lines = [
        "I miss my daughter so much",
        "I miss Priya",
        "when is Priya coming",
        "what's for lunch today",
    ]
    for line in test_lines:
        result = box.match(line)
        if result:
            print(f"'{line}' -> MATCH: {result.offer_line} "
                  f"(clip on disk: {box.clip_exists_on_disk(result)})")
        else:
            print(f"'{line}' -> no match")