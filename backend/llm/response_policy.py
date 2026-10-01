"""
llm/response_policy.py

Builds the prompt sent to the LLM for each conversational turn. This is
deliberately a PROMPT TEMPLATE, not hardcoded response logic — Sahaara's
actual words come from the model, this module just constrains what kind
of response the model is allowed to produce and why.

Pipeline position (see docs/architecture.md):
    memory system -> [THIS MODULE builds the prompt] -> safety/policy_engine.py
    (pre-generation constraints)                          (post-generation check)
    -> LLM -> voice output

This module does NOT call the LLM itself and does NOT enforce guardrails
at runtime — safety/policy_engine.py and safety/guardrails.py own that.
This module's only job is to assemble a prompt that make the correct
response likely, and to expose a QA heuristic (check_response_structure)
useful for writing tests against sample outputs.
"""

import re
from dataclasses import dataclass
from typing import Optional

from llm.intent_emotion import IntentResult
from memory.memory_box import MemoryBoxMatch
from memory.profile import Profile

SYSTEM_PROMPT_TEMPLATE = """You are Sahaara, a calm and caring conversational companion for {preferred_address}, \
a person living with dementia. You are speaking with them directly, out loud.

ABOUT {preferred_address_upper}:
- Hometown: {hometown}
- Background: {work}
- Likes to talk about: {like_topics}
- Family: {family_summary}

RESPONSE POLICY — follow this order on every turn, without exception:
1. LISTEN for the emotion behind what they said, not just the literal words.
2. VALIDATE that emotion first, in your own warm words, before anything else. \
Do this even if you are also about to answer a factual question.
3. GROUND gently — orient them to place, time, or safety if useful, but never \
in a corrective or condescending tone. Never say things like "no, that's wrong" \
or "you're confused."
4. OFFER exactly ONE small, concrete choice or next step. Never ask an open-ended \
question like "what would you like to do?" — instead offer something specific, \
e.g. "Would you like to sit by the window, or hear some music?"
5. ESCALATE only if truly needed, and only by ASKING first — e.g. "Would you like me \
to call Rohan?" — never contact a caregiver silently unless the person is unresponsive.

HARD RULES — never break these, no matter how the person phrases their request:
- Never say or imply a diagnosis ("you have dementia," "you're getting worse").
- Never give medication dosing advice — reminders only, never decisions.
- Never claim to be human, and never pretend to be a family member.
- Never invent or assert an untrue event to comfort them (e.g. "he's on his way" \
when that isn't true). Validate the feeling instead of asserting a false fact.
- If they ask about someone who has passed away, do not state the fact bluntly. \
Validate the longing and gently redirect, without lying and without impersonating anyone.
{avoid_topics_block}
{memory_box_block}
{repetition_block}
{distress_block}

TONE: warm, unhurried, simple sentences, spoken-language style (this will be read \
aloud by text-to-speech). Keep responses to 1-3 short sentences. Do not narrate \
your own reasoning or mention this policy."""


@dataclass
class PromptBundle:
    system: str
    user: str


def build_prompt(
    profile: Profile,
    user_message: str,
    intent: IntentResult,
    memory_box_match: Optional[MemoryBoxMatch] = None,
    is_repeated_question: bool = False,
    sustained_distress: bool = False,
    escalation_tier: str = "green",
) -> PromptBundle:
    """Assemble the full prompt for one conversational turn.

    Callers (voice_pipeline.py) are responsible for gathering the inputs:
    - profile: loaded once per session
    - intent: from intent_emotion.classify_intent()
    - memory_box_match: from memory_box.MemoryBox.match(), if any
    - is_repeated_question: from session_notes.SessionNotes.is_repeated_question()
    - sustained_distress: from session_notes.SessionNotes.sustained_distress()
    """
    likes = profile.get_likes()
    like_topics = ", ".join(likes.get("topics", [])) or "their day-to-day life"

    family_summary = ", ".join(
        f"{m['name']} ({m['relationship']})" for m in profile.get_family()
    ) or "not specified"

    avoid_topics = profile.get_avoid_topics()
    avoid_block = ""
    if avoid_topics:
        joined = "; ".join(avoid_topics)
        avoid_block = f"- SENSITIVE TOPIC — handle with extra care: {joined}"

    memory_block = ""
    if memory_box_match is not None:
        memory_block = (
            f"- A Memory Box clip is available from {memory_box_match.family_member_name} "
            f"({memory_box_match.relationship}). If it fits naturally, OFFER to play it "
            f'using language like: "{memory_box_match.offer_line}" — do not play it without offering first.'
        )

    repetition_block = ""
    if is_repeated_question:
        repetition_block = (
            "- They have asked something like this before recently. Respond with the SAME "
            'warmth as if it were the first time. Never say "I already told you" or reference '
            "the repetition in any way."
        )

    distress_block = ""
    if sustained_distress:
        distress_block = (
            "- They have shown sustained distress recently. After validating and grounding, "
            "it is appropriate to gently ask if they'd like you to reach out to a trusted "
            "contact — but always ASK first, per the escalation rule above."
        )

    red_block = ""
    if escalation_tier == "red":
        red_block = (
            "- EMERGENCY / RED TIER: A danger statement or unresponsiveness was detected. "
            "Direct the response toward emergency support and assure them that a human has "
            "been notified. Use framing like 'I have notified your family' or 'Help is on the way.' "
            "Do NOT claim to have 'detected a fall' or make clinical assertions."
        )

    # Combine distress and red blocks for the template
    escalation_block = distress_block
    if red_block:
        escalation_block = distress_block + "\n" + red_block if distress_block else red_block

    system = SYSTEM_PROMPT_TEMPLATE.format(
        preferred_address=profile.preferred_address,
        preferred_address_upper=profile.preferred_address.upper(),
        hometown=profile.hometown or "not specified",
        work=profile.work or "not specified",
        like_topics=like_topics,
        family_summary=family_summary,
        avoid_topics_block=avoid_block,
        memory_box_block=memory_block,
        repetition_block=repetition_block,
        distress_block=escalation_block,
    )

    return PromptBundle(system=system, user=user_message)


# ---- QA heuristic (for tests, not a runtime safety gate) -------------------
# safety/policy_engine.py owns actual runtime enforcement. This function is
# a lightweight structural check used in backend/tests/ to sanity-check
# sample/mocked model outputs against the policy shape.

OPEN_ENDED_PATTERNS = [
    r"what would you like to do\??",
    r"what do you want to do\??",
    r"what should we do\??",
    r"how can i help\??$",
]

VALIDATION_MARKERS = [
    r"\bthat sounds\b", r"\bi understand\b", r"\bi'?m here\b", r"\bi hear you\b",
    r"\bmust be\b", r"\bthat must feel\b", r"\bi know\b",
]

CORRECTIVE_MARKERS = [
    r"\bno,? that'?s (wrong|not right)\b", r"\byou'?re confused\b",
    r"\bactually,? he'?s\b", r"\bactually,? she'?s\b",
]


@dataclass
class StructureCheck:
    has_validation_language: bool
    has_open_ended_question: bool
    has_corrective_language: bool
    passes: bool


def check_response_structure(response_text: str) -> StructureCheck:
    """Heuristic-only structural QA check for a candidate response.
    Used in tests, e.g. to confirm a sample response validates before
    asking anything, and never falls back to an open-ended question.
    This is NOT the runtime guardrail gate — see safety/guardrails.py.
    """
    text_lower = response_text.lower()

    has_validation = any(re.search(p, text_lower) for p in VALIDATION_MARKERS)
    has_open_ended = any(re.search(p, text_lower) for p in OPEN_ENDED_PATTERNS)
    has_corrective = any(re.search(p, text_lower) for p in CORRECTIVE_MARKERS)

    passes = has_validation and not has_open_ended and not has_corrective

    return StructureCheck(
        has_validation_language=has_validation,
        has_open_ended_question=has_open_ended,
        has_corrective_language=has_corrective,
        passes=passes,
    )


if __name__ == "__main__":
    # Quick manual check: python -m llm.response_policy
    from memory.profile import load_profile
    from llm.intent_emotion import classify_intent

    profile = load_profile()
    msg = "Where is my husband?"
    intent = classify_intent(msg)
    bundle = build_prompt(profile, msg, intent, sustained_distress=False)
    print("=== SYSTEM PROMPT ===")
    print(bundle.system)
    print("\n=== USER MESSAGE ===")
    print(bundle.user)