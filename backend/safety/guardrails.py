"""
safety/guardrails.py

Hard checks that run on a CANDIDATE response before it is accepted and
sent to voice output. This is the safety net described in AGENTS.md:
"Every LLM response passes through safety/policy_engine.py and
safety/guardrails.py BEFORE being sent to voice output."

This module is deliberately independent of response_policy.py's prompt
template. The prompt tells the model what to do; this module verifies
what it actually did. Both layers matter — a model can drift from
instructions, especially under an adversarial or unusual user message.

If check_guardrails() returns any violations, the caller (safety/policy_engine.py,
built alongside this) should NOT send the response to TTS. It should fall
back to a safe canned response and/or regenerate.
"""

import re
from dataclasses import dataclass, field
from typing import Optional

from memory.profile import Profile

# ---- Pattern sets ------------------------------------------------------

DIAGNOSIS_PATTERNS = [
    r"\byou have dementia\b",
    r"\byou'?re getting worse\b",
    r"\byour condition (is|has)\b",
    r"\bdiagnosed with\b",
    r"\byour memory (is|has been) (declining|deteriorating|failing)\b",
    r"\bthis is a symptom of\b",
]

DOSING_ADVICE_PATTERNS = [
    r"\btake (another|two|three|an extra)\b.*\b(pill|tablet|dose|medicine|medication)\b",
    r"\byou should (take|increase|decrease|skip|stop)\b.*\b(pill|tablet|dose|medicine|medication)\b",
    r"\bincrease your dose\b",
    r"\bdouble (the|your) dose\b",
]

IMPERSONATION_PATTERNS = [
    r"\bi am (your )?(daughter|son|husband|wife|mother|father)\b",
    r"\bthis is (priya|rohan|vikram) speaking\b",
    r"\bi'?m priya\b", r"\bi'?m rohan\b", r"\bi'?m vikram\b",
    r"\byes,? i'?m (really )?here (in person|with you)\b",
]

# Reassurance language that asserts a specific event is imminent/true.
# On its own this is fine (e.g. "your lunch is on its way"). It becomes
# a guardrail violation when combined with a reference to a family
# member who is deceased or on the avoid-topics list — see
# _check_fabricated_reassurance below.
IMMINENT_EVENT_PATTERNS = [
    r"\bon (his|her|their) way\b",
    r"\bwill (be here|call|arrive|come)\b",
    r"\b(is|are) coming (soon|today|now)\b",
    r"\bwill call (you )?(tonight|today|soon)\b",
]


@dataclass
class GuardrailResult:
    text: str
    violations: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return len(self.violations) == 0


def _find_matches(text_lower: str, patterns: list[str]) -> list[str]:
    return [p for p in patterns if re.search(p, text_lower)]


def _is_deceased(member: dict) -> bool:
    """Loose heuristic: relationship field containing 'late' (e.g. 'late
    husband') marks someone as deceased. Good enough for the hackathon
    profile schema; a production version would use an explicit field.
    """
    return "late" in member.get("relationship", "").lower()


def _check_fabricated_reassurance(text_lower: str, profile: Optional[Profile]) -> list[str]:
    """Flags reassurance language ('he's on his way') when it appears
    alongside a mention of a deceased or avoid-topic family member —
    that combination is very likely a fabricated comfort, which
    docs/guardrails.md explicitly forbids.
    """
    if profile is None:
        return []

    imminent_hits = _find_matches(text_lower, IMMINENT_EVENT_PATTERNS)
    if not imminent_hits:
        return []

    risky_people = [
        m["name"].lower() for m in profile.get_family()
        if _is_deceased(m)
    ]
    mentioned_risky_person = any(name in text_lower for name in risky_people)

    # Also treat a generic "husband"/"wife" mention as risky if that
    # relationship maps to a deceased family member in the profile.
    risky_relationships = [
        m["relationship"].lower().replace("late ", "") for m in profile.get_family()
        if _is_deceased(m)
    ]
    mentioned_risky_relationship = any(rel in text_lower for rel in risky_relationships)

    if mentioned_risky_person or mentioned_risky_relationship:
        return [f"fabricated_reassurance: {hit}" for hit in imminent_hits]

    return []


def check_guardrails(response_text: str, profile: Optional[Profile] = None) -> GuardrailResult:
    """Run all hard guardrail checks against a candidate response.
    Pass the active Profile when available so the fabricated-reassurance
    check can cross-reference deceased/avoid-topic family members.
    """
    text_lower = response_text.lower()
    violations: list[str] = []

    violations += [f"diagnosis: {p}" for p in _find_matches(text_lower, DIAGNOSIS_PATTERNS)]
    violations += [f"dosing_advice: {p}" for p in _find_matches(text_lower, DOSING_ADVICE_PATTERNS)]
    violations += [f"impersonation: {p}" for p in _find_matches(text_lower, IMPERSONATION_PATTERNS)]
    violations += _check_fabricated_reassurance(text_lower, profile)

    return GuardrailResult(text=response_text, violations=violations)


# Safe fallback used by policy_engine.py when a response fails guardrails
# and there isn't time/budget to regenerate.
SAFE_FALLBACK_RESPONSE = (
    "I'm here with you. Would you like to sit for a moment, or would you like some tea?"
)


if __name__ == "__main__":
    # Quick manual check: python -m safety.guardrails
    from memory.profile import load_profile

    profile = load_profile()
    samples = [
        "That sounds hard. Would you like to sit by the window, or hear some music?",
        "You have dementia, so it's normal to forget things like this.",
        "You should take another pill to help you feel calmer.",
        "I'm Priya, I'm right here with you.",
        "Don't worry, Vikram is on his way home now.",
        "Your lunch is on its way, it'll be ready soon.",  # not risky, no deceased mention
    ]
    for s in samples:
        result = check_guardrails(s, profile)
        status = "PASS" if result.passed else f"FAIL {result.violations}"
        print(f"{s!r}\n  -> {status}\n")