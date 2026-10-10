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

# Fabricated whereabouts patterns — Sahaara cannot know where living
# people are unless it comes from the profile. These are false claims
# about living people's locations/activities.
FABRICATED_WHEREABOUTS_PATTERNS = [
    r"\bwaiting (for you )?(at|in) (home|the house)\b",
    r"\bwaiting at home\b",
    r"\bstepped out\b",
    r"\bjust stepped out\b",
    r"\bback soon\b",
    r"\bhome soon\b",
    r"\bcoming soon\b",
    r"\bshould be home\b",
    r"\bwill be home\b",
    r"\bis at home\b",
    r"\bhe'?s at home\b",
    r"\bshe'?s at home\b",
    r"\bthey'?re at home\b",
    r"\bat home with\b",
    # Pronoun-based patterns (common fabrications)
    r"\bhe'?s (waiting|at home|stepped out|just stepped out|back soon|home soon|coming soon)\b",
    r"\bshe'?s (waiting|at home|stepped out|just stepped out|back soon|home soon|coming soon)\b",
    r"\bthey'?re (waiting|at home|stepped out|just stepped out|back soon|home soon|coming soon)\b",
    # More flexible pronoun patterns
    r"\bhe'?s (waiting|at home|stepped out|just stepped out|back soon|home soon|coming soon)",
    r"\bshe'?s (waiting|at home|stepped out|just stepped out|back soon|home soon|coming soon)",
    r"\bthey'?re (waiting|at home|stepped out|just stepped out|back soon|home soon|coming soon)",
]

# Fabricated schedule patterns — Sahaara cannot know when someone
# will visit unless it comes from the profile.
FABRICATED_SCHEDULE_PATTERNS = [
    r"\bplanning to come\b",
    r"\bscheduled to (come|arrive|visit)\b",
    r"\bon (her|his|their) way\b",
    r"\bwill (arrive|come|visit) (tomorrow|today|this week)\b",
    r"\bcoming (tomorrow|today|next week)\b",
    r"\bvisit (tomorrow|today|next week)\b",
]

# Fabricated weather/surroundings patterns — Sahaara has no camera
# or weather source.
FABRICATED_WEATHER_PATTERNS = [
    r"\blovely (sunny|warm|gentle|beautiful) day\b",
    r"\bgorgeous day\b",
    r"\bsunny day (here|today)\b",
    r"\bgentle day\b",
    r"\bit'?s (a |the )?lovely day\b",
    r"\bit'?s (a |the )?gentle day\b",
    r"\bit'?s (a |the )?sunny day\b",
    r"\bit'?s (a |the )?warm day\b",
    r"\bit'?s (a |the )?pleasant day\b",
    r"\bit'?s (a |the )?beautiful day\b",
]

# Fabricated surroundings patterns — Sahaara has no camera
FABRICATED_SURROUNDINGS_PATTERNS = [
    r"\bgarden (looks|is) (lovely|nice|beautiful|peaceful|gorgeous)\b",
    r"\bflowers (are|look) (blooming|lovely|beautiful|gorgeous)\b",
    r"\bthe view (is|looks) (lovely|nice|beautiful|peaceful|gorgeous)\b",
    r"\bview (is|looks) (lovely|nice|beautiful|peaceful|gorgeous)\b",
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


def _check_fabricated_whereabouts(text_lower: str, profile: Optional[Profile]) -> list[str]:
    """Flags fabricated claims about where living people are.
    Applies to ALL family members, not just deceased.
    """
    if profile is None:
        return []
    
    # Check if any family member is mentioned (not just deceased)
    family_names = [m["name"].lower() for m in profile.get_family()]
    family_relationships = [m["relationship"].lower().replace("late ", "") for m in profile.get_family()]
    
    mentioned_family = any(name in text_lower for name in family_names) or \
                       any(rel in text_lower for rel in family_relationships)
    
    # Also check for gendered pronouns that likely refer to family members
    # (she/her for female family members, he/him for male)
    mentioned_pronoun = any(p in text_lower for p in ["she's", "she is", "he's", "he is", "they're", "they are"])
    
    if not (mentioned_family or mentioned_pronoun):
        return []
    
    hits = _find_matches(text_lower, FABRICATED_WHEREABOUTS_PATTERNS)
    return [f"fabricated_whereabouts: {hit}" for hit in hits]


def _check_fabricated_schedule(text_lower: str, profile: Optional[Profile]) -> list[str]:
    """Flags fabricated claims about when someone will visit/arrive.
    Applies to ALL family members.
    """
    if profile is None:
        return []
    
    family_names = [m["name"].lower() for m in profile.get_family()]
    family_relationships = [m["relationship"].lower().replace("late ", "") for m in profile.get_family()]
    
    mentioned_family = any(name in text_lower for name in family_names) or \
                       any(rel in text_lower for rel in family_relationships)
    
    mentioned_pronoun = any(p in text_lower for p in ["she's", "she is", "he's", "he is", "they're", "they are"])
    
    if not (mentioned_family or mentioned_pronoun):
        return []
    
    hits = _find_matches(text_lower, FABRICATED_SCHEDULE_PATTERNS)
    return [f"fabricated_schedule: {hit}" for hit in hits]


def _check_fabricated_weather(text_lower: str) -> list[str]:
    """Flags fabricated weather claims. Sahaara has no weather source."""
    hits = _find_matches(text_lower, FABRICATED_WEATHER_PATTERNS)
    return [f"fabricated_weather: {hit}" for hit in hits]


def _check_fabricated_surroundings(text_lower: str) -> list[str]:
    """Flags fabricated surroundings claims. Sahaara has no camera."""
    hits = _find_matches(text_lower, FABRICATED_SURROUNDINGS_PATTERNS)
    return [f"fabricated_surroundings: {hit}" for hit in hits]


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
    violations += _check_fabricated_whereabouts(text_lower, profile)
    violations += _check_fabricated_schedule(text_lower, profile)
    violations += _check_fabricated_weather(text_lower)
    violations += _check_fabricated_surroundings(text_lower)

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
        "She's waiting at home for you.",
        "She's just stepped out.",
        "It's a lovely sunny day today.",
        "The garden looks lovely today.",
    ]
    for s in samples:
        result = check_guardrails(s, profile)
        status = "PASS" if result.passed else f"FAIL {result.violations}"
        print(f"{s!r}\n  -> {status}\n")