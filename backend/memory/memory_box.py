"""
memory/memory_box.py

Maps conversation triggers ("I miss my daughter") to a specific
pre-recorded Memory Box clip from a family member, and generates the
offer line Sahaara uses to introduce it.

Hard rule (see docs/guardrails.md): Sahaara always OFFERS the clip
("Would you like to hear something Priya left for you?") — it never
plays it unprompted, and it never pretends to BE the family member.
The clip is clearly framed as something the family member recorded.

Replay rule: Sahaara will not re-offer a clip on her own within
REOFFER_AFTER_TURNS of the last offer. But if Asha explicitly asks
for it again ("play it again", "can I hear Priya's voice"), the
match is returned immediately regardless of suppression.
"""

import re
import time
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

# Explicit replay requests — bypasses the re-offer suppression.
# These match when Asha directly asks to hear the clip again.
REPLAY_REQUEST_PATTERNS = [
    r"\bplay (it|that|that clip|the clip|the message) again\b",
    r"\bcan (you|i|we) play (it|that|that clip|the clip) again\b",
    r"\bhear (it|that|that clip|the clip|the message|the voice) again\b",
    r"\b(play|hear) (priya'?s|her|his|their) (voice|clip|message|recording)\b",
    r"\bcan i (hear|listen to) (it|that|that clip|the clip|the message) again\b",
    r"\bi want to hear (it|that|that clip|the clip|the message|the voice) again\b",
]

REOFFER_AFTER_TURNS = 5
REOFFER_AFTER_SECONDS = 600  # 10 minutes


@dataclass
class MemoryBoxMatch:
    family_member_name: str
    relationship: str
    clip_filename: str
    clip_path: Path
    offer_line: str
    triggered_by: str = "longing_match"  # "longing_match" or "replay_request"


class MemoryBox:
    """Reads Memory Box clip references off the loaded profile and
    matches incoming conversation text against them.

    Tracks which clips have been offered to avoid repeating the same
    offer too soon. Explicit replay requests from the user bypass
    the suppression window.

    Re-offer rule: Sahaara re-offers on her own only after BOTH
    REOFFER_AFTER_TURNS turns AND REOFFER_AFTER_SECONDS have passed.
    """

    def __init__(self, profile: Profile, clip_dir: Path = CLIP_DIR):
        self.profile = profile
        self.clip_dir = clip_dir
        self._longing_re = re.compile("|".join(LONGING_PATTERNS), re.IGNORECASE)
        self._replay_re = re.compile("|".join(REPLAY_REQUEST_PATTERNS), re.IGNORECASE)
        # clip_filename -> (turn_count, timestamp) when last offered
        self._offered_clips: dict[str, tuple[int, float]] = {}
        self._turn_count: int = 0

    def has_clip_for(self, family_member: dict) -> bool:
        return bool(family_member.get("memory_box_clip"))

    def available_clips(self) -> list[dict]:
        """All family members who have a recorded clip, per the profile."""
        return [m for m in self.profile.get_family() if self.has_clip_for(m)]

    def _is_replay_request(self, text: str) -> bool:
        """Check if the user is explicitly asking to replay a clip."""
        return bool(self._replay_re.search(text.lower()))

    def _find_most_recent_clip(self) -> Optional[dict]:
        """Find the most recently offered clip's family member."""
        if not self._offered_clips:
            return None
        most_recent_clip = max(self._offered_clips, key=lambda k: self._offered_clips[k][0])
        for member in self.available_clips():
            if member.get("memory_box_clip") == most_recent_clip:
                return member
        return None

    def _is_suppressed(self, clip_filename: str) -> bool:
        """Check if a clip offer should be suppressed.
        Returns True if BOTH the turn count AND time thresholds are unmet.
        """
        entry = self._offered_clips.get(clip_filename)
        if entry is None:
            return False
        last_turn, last_time = entry
        turns_since = self._turn_count - last_turn
        time_since = time.time() - last_time
        # Suppress only if BOTH thresholds are unmet (whichever is longer)
        return turns_since < REOFFER_AFTER_TURNS and time_since < REOFFER_AFTER_SECONDS

    def match(self, text: str) -> Optional[MemoryBoxMatch]:
        """Check if the given text is a trigger for a Memory Box clip.

        Two paths:
        1. Explicit replay request ("play it again") — matches the most
           recently offered clip regardless of suppression window.
        2. Standard longing/missing pattern + family member reference —
           subject to re-offer suppression (5 turns AND 10 minutes).

        Returns None if no match or if suppressed.
        """
        # Path 1: explicit replay request
        if self._is_replay_request(text) and self._offered_clips:
            member = self._find_most_recent_clip()
            if member:
                return self._build_match(member, triggered_by="replay_request")

        # Path 2: standard longing + family reference matching
        if not self._longing_re.search(text):
            return None

        text_lower = text.lower()
        for member in self.available_clips():
            name = member["name"].lower()
            relationship = member["relationship"].lower()
            if name in text_lower or relationship in text_lower:
                match = self._build_match(member, triggered_by="longing_match")
                # Check re-offer suppression
                if self._is_suppressed(match.clip_filename):
                    return None  # Suppressed — too soon
                # Track the offer for this turn
                self._offered_clips[match.clip_filename] = (self._turn_count, time.time())
                return match

        return None

    def _build_match(self, member: dict, triggered_by: str = "longing_match") -> MemoryBoxMatch:
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
            triggered_by=triggered_by,
        )

    def record_turn(self) -> None:
        """Advance the turn counter. Call once per conversation turn."""
        self._turn_count += 1

    def reset(self) -> None:
        """Clear all tracking state for a fresh session."""
        self._offered_clips.clear()
        self._turn_count = 0

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