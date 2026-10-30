"""
llm/intent_emotion.py

Classifies an incoming message along two axes:
  1. Emotional state (anxious, distressed, sad, calm, content...)
  2. Whether the underlying need is emotional or purely factual

This classification feeds response_policy.py, which uses it to decide
HOW to respond (validate-first vs. answer-first), and session_notes.py,
which uses the emotion label for sustained-distress tracking.

Hackathon MVP: keyword/pattern heuristics. Swap-in point for an LLM-based
classifier is `classify_with_llm()` — same output shape, so callers don't
need to change when you upgrade.

Hard rule this module exists to support (see docs/architecture.md):
a factually-phrased question can still be an emotional need. "Where is
my husband?" reads as a factual ask on the surface, but if it touches a
sensitive/avoid topic it should be treated as emotional. This module
flags the surface signal; response_policy.py combines it with the
profile's avoid-topics to make the final call.
"""

import re
from dataclasses import dataclass, field
from typing import Callable

# ---- Emotion keyword sets --------------------------------------------
# Kept small and demo-scenario-focused on purpose. Expand as needed but
# resist over-engineering this for the hackathon timeline.

EMOTION_KEYWORDS: dict[str, list[str]] = {
    "anxious": [
        r"\bworried\b", r"\bnervous\b", r"\bscared\b", r"\bafraid\b",
        r"\bwhere is\b", r"\bwhen is .* coming\b", r"\bwhy (isn'?t|hasn'?t)\b",
    ],
    "distressed": [
        r"\bhelp me\b", r"\bi don'?t know\b", r"\bi'?m lost\b",
        r"\bi want to go home\b", r"\bcrying\b", r"\bupset\b",
    ],
    "sad": [
        r"\bmiss(ing)?\b", r"\blonely\b", r"\bsad\b", r"\bwish\b",
    ],
    "content": [
        r"\bgood morning\b", r"\bthank you\b", r"\bnice\b", r"\bhappy\b",
    ],
}

# Markers that look purely factual/logistical on the surface.
FACTUAL_MARKERS = [
    r"\bwhat time\b", r"\bwhat day\b", r"\bwhat'?s for (breakfast|lunch|dinner)\b",
    r"\bhow do i\b", r"\bcan you turn\b", r"\bwhat'?s the weather\b",
]

# Identity/self-description questions
IDENTITY_PATTERNS = [
    r"\bwho are you\b", r"\bwho am i talking to\b", r"\bwhat are you\b",
    r"\bwhat is your name\b", r"\bidentify yourself\b", r"\bwhat kind of (assistant|bot|ai) are you\b",
    r"\bwho is this\b", r"\bwhat is sahaara\b",
]

# Explicit call request patterns (Trigger 1)
CALL_REQUEST_PATTERNS = [
    r"\bcall (rohan|my son|my daughter|priya|someone|them)\b",
    r"\bplease call\b",
    r"\bI need help,? call\b",
    r"\bcall for help\b",
    r"\bcan you call\b",
    r"\bcould you call\b",
]

# Consent acceptance patterns (after Sahaara asks "Would you like me to call X?")
CONSENT_ACCEPT_PATTERNS = [
    r"\byes\b", r"\byeah\b", r"\byep\b", r"\byup\b",
    r"\bsure\b", r"\bok\b", r"\bokay\b", r"\bplease\b",
    r"\bdo it\b", r"\bgo ahead\b", r"\bcall\b",
]

# ---- Physical distress patterns ----------------------------------------
# Two levels: Urgent (Red) and General (Orange)
# These are PHYSICAL symptoms, not emotional distress.
# They trigger immediate escalation on FIRST mention - no pattern building needed.

# Urgent symptoms -> RED tier immediately
PHYSICAL_URGENT_PATTERNS = [
    # Breathing difficulties
    r"\bcan'?t breathe\b", r"\bcannot breathe\b", r"\btrouble breathing\b",
    r"\bdifficulty breathing\b", r"\bshort of breath\b", r"\bout of breath\b",
    r"\bcan'?t catch (my )?breath\b",
    r"\bnot able to breathe\b", r"\bnot able to breath\b",
    r"\bunable to breathe\b", r"\bunable to breath\b",
    r"\btrouble breathing\b", r"\bhaving trouble breathing\b",
    r"\bhard to breathe\b", r"\bhard to breath\b",
    # ASR variants for breathing
    r"\bcant breathe\b", r"\bi cant breathe\b", r"\bi cant breath\b",
    r"\bi cant breathe\b", r"\bi cant breath\b",
    # Chest pain / cardiac
    r"\bchest pain\b", r"\bchest hurts\b", r"\bchest (is )?hurting\b",
    r"\bheart (hurts|pain)\b", r"\btight (in|across) (my )?chest\b",
    r"\bchest (feels )?tight\b", r"\bpressure (in|on) (my )?chest\b",
    # ASR variants for chest pain
    r"\bchest pane\b", r"\bchest paint\b",
    # Fall / mobility
    r"\bi fell\b", r"\bi'?ve fallen\b", r"\bfell down\b",
    r"\bcan'?t get up\b", r"\bcannot get up\b", r"\bcan'?t move\b",
    r"\bcannot move\b", r"\bunable to move\b", r"\bcan'?t stand\b",
    # ASR variants for fall/mobility
    r"\bi fel down\b", r"\bi fell down\b", r"\bi cant get up\b",
    r"\bi cant move\b", r"\bi cant stand\b",
    # Fainting / consciousness
    r"\bfeel faint\b", r"\bfeeling faint\b", r"\babout to faint\b",
    r"\bpassing out\b", r"\bpass out\b", r"\bblacking out\b",
    r"\bfeel like (i'?m )?(passing out|fainting)\b",
    # ASR variants for fainting
    r"\bfeel fint\b", r"\bpassin out\b",
    # Severe dizziness
    r"\bvery dizzy\b", r"\bextremely dizzy\b", r"\bso dizzy\b",
    r"\broom (is )?spinning\b", r"\bworld (is )?spinning\b",
    # ASR variants for dizziness
    r"\bverry dizzy\b",
    # Stroke signs (FAST)
    r"\bface droop\b", r"\barm (weak|numb)\b", r"\bspeech (slurred|strange)\b",
]

# General unwellness -> ORANGE tier immediately
PHYSICAL_GENERAL_PATTERNS = [
    # General unwellness
    r"\bnot feeling well\b", r"\bfeel unwell\b", r"\bfeeling unwell\b",
    r"\bdon'?t feel well\b", r"\bdon'?t feel good\b", r"\bfeel terrible\b",
    r"\bfeel awful\b", r"\bfeel horrible\b", r"\bfeel sick\b",
    r"\bfeeling sick\b", r"\bfeel ill\b", r"\bfeeling ill\b",
    # ASR variants for general unwellness
    r"\bnot feelin well\b", r"\bnot feelin good\b",
    # Specific symptoms
    r"\b(dizzy|dizziness)\b", r"\b(dizzy|lightheaded)\b",
    r"\bnausea\b", r"\bnauseous\b", r"\bwant to (throw up|vomit)\b",
    r"\bstomach (hurts|pain|ache)\b", r"\btummy (hurts|pain|ache)\b",
    r"\bheadache\b", r"\bhead hurts\b", r"\bhead (is )?hurting\b",
    r"\bback (hurts|pain|ache)\b",
    r"\b(in )?pain\b", r"\bhurts (all over|everywhere)\b",
    r"\bachy\b", r"\bache\b",
    # ASR variants for specific symptoms
    r"\bdizzie\b", r"\bstomak hurts\b", r"\bhedache\b",
    # Temperature
    r"\bfever\b", r"\b(have a )?temperature\b", r"\bfeel (hot|burning up)\b",
    r"\bchills\b", r"\bshivering\b",
    # ASR variants for temperature
    r"\bfevor\b",
    # Fatigue/weakness
    r"\bweak\b", r"\bweakness\b", r"\bno energy\b",
    r"\bexhausted\b", r"\bcan'?t stay awake\b",
]

DANGER_PATTERNS = [
    r"\bi want to die\b", r"\bkill myself\b", r"\bend it all\b",
    r"\bdon'?t want to live\b", r"\bno reason to live\b",
    r"\bwant to hurt myself\b", r"\bhurt myself\b",
    r"\bnobody would miss me\b", r"\bbetter off without me\b",
]

DEFAULT_EMOTION = "calm"


@dataclass
class IntentResult:
    text: str
    emotion: str                       # best-guess primary emotion label
    is_emotional_need: bool            # should the response-policy validate first?
    is_factual_ask: bool               # does it also carry a literal factual question?
    is_identity_ask: bool = False      # is this a "who are you" / identity question?
    is_danger_statement: bool = False  # Red tier trigger
    matched_emotion_keywords: list[str] = field(default_factory=list)
    # Physical distress flags (new)
    is_physical_urgent: bool = False   # Urgent physical symptom -> RED
    is_physical_general: bool = False  # General physical unwellness -> ORANGE
    matched_physical_keywords: list[str] = field(default_factory=list)
    # Call request flags (Emergency Call)
    is_call_request: bool = False      # Explicit "call someone" request
    is_consent_accepted: bool = False  # Yes after "Would you like me to call?"


def classify_intent(text: str) -> IntentResult:
    """Rule-based classification. Fast, no API call, good enough for
    the hackathon MVP and for scripted demo inputs where phrasing is
    controlled. See classify_with_llm() for the upgrade path.
    """
    text_lower = text.lower()

    matched_emotion = None
    matched_keywords: list[str] = []
    for emotion, patterns in EMOTION_KEYWORDS.items():
        hits = [p for p in patterns if re.search(p, text_lower)]
        if hits:
            matched_keywords.extend(hits)
            # First match wins by dict insertion order (anxious > distressed > sad > content),
            # i.e. we bias toward NOT under-reacting to a distress signal.
            if matched_emotion is None:
                matched_emotion = emotion

    # Check physical distress patterns
    physical_urgent_hits = [p for p in PHYSICAL_URGENT_PATTERNS if re.search(p, text_lower)]
    physical_general_hits = [p for p in PHYSICAL_GENERAL_PATTERNS if re.search(p, text_lower)]
    is_physical_urgent = len(physical_urgent_hits) > 0
    is_physical_general = len(physical_general_hits) > 0
    matched_physical = physical_urgent_hits + physical_general_hits

    is_danger = any(re.search(p, text_lower) for p in DANGER_PATTERNS)
    is_factual = any(re.search(p, text_lower) for p in FACTUAL_MARKERS)
    is_identity = any(re.search(p, text_lower) for p in IDENTITY_PATTERNS)
    is_emotional = matched_emotion is not None and matched_emotion != "content"

    # Call request detection (Trigger 1)
    is_call_request = any(re.search(p, text_lower) for p in CALL_REQUEST_PATTERNS)

    # Consent acceptance detection (Trigger 2)
    is_consent_accepted = any(re.search(p, text_lower) for p in CONSENT_ACCEPT_PATTERNS)

    emotion = matched_emotion or (DEFAULT_EMOTION if not is_factual else "neutral")

    return IntentResult(
        text=text,
        emotion=emotion,
        is_emotional_need=is_emotional,
        is_factual_ask=is_factual,
        is_identity_ask=is_identity,
        is_danger_statement=is_danger,
        matched_emotion_keywords=matched_keywords,
        is_physical_urgent=is_physical_urgent,
        is_physical_general=is_physical_general,
        matched_physical_keywords=matched_physical,
        is_call_request=is_call_request,
        is_consent_accepted=is_consent_accepted,
    )


def classify_with_llm(text: str, llm_call: Callable[[str], str]) -> IntentResult:
    """Upgrade path: delegate classification to an LLM via an injected
    callable (e.g. a Nemotron Nano call) instead of keyword rules.
    `llm_call` should take a classification prompt and return raw text;
    this function parses that into an IntentResult.

    Not wired to a live model yet — stubbed so response_policy.py can
    depend on IntentResult without caring which classifier produced it.
    """
    prompt = (
        "Classify the emotional state and need behind this message from a "
        "person with dementia. Respond with exactly two lines:\n"
        "emotion: <one of anxious, distressed, sad, calm, content, neutral>\n"
        "emotional_need: <true or false>\n\n"
        f"Message: \"{text}\""
    )
    raw = llm_call(prompt)
    emotion = DEFAULT_EMOTION
    is_emotional_need = False
    for line in raw.strip().splitlines():
        if line.lower().startswith("emotion:"):
            emotion = line.split(":", 1)[1].strip().lower()
        elif line.lower().startswith("emotional_need:"):
            is_emotional_need = line.split(":", 1)[1].strip().lower() == "true"

    # Check physical distress patterns even for LLM path
    text_lower = text.lower()
    physical_urgent_hits = [p for p in PHYSICAL_URGENT_PATTERNS if re.search(p, text_lower)]
    physical_general_hits = [p for p in PHYSICAL_GENERAL_PATTERNS if re.search(p, text_lower)]
    is_physical_urgent = len(physical_urgent_hits) > 0
    is_physical_general = len(physical_general_hits) > 0
    matched_physical = physical_urgent_hits + physical_general_hits

    is_danger = any(re.search(p, text_lower) for p in DANGER_PATTERNS)
    is_factual = any(re.search(p, text_lower) for p in FACTUAL_MARKERS)

    # Call request and consent acceptance for LLM path
    is_call_request = any(re.search(p, text_lower) for p in CALL_REQUEST_PATTERNS)
    is_consent_accepted = any(re.search(p, text_lower) for p in CONSENT_ACCEPT_PATTERNS)

    return IntentResult(
        text=text,
        emotion=emotion,
        is_emotional_need=is_emotional_need,
        is_factual_ask=is_factual,
        is_danger_statement=is_danger,
        matched_emotion_keywords=[],
        is_physical_urgent=is_physical_urgent,
        is_physical_general=is_physical_general,
        matched_physical_keywords=matched_physical,
        is_call_request=is_call_request,
        is_consent_accepted=is_consent_accepted,
    )


if __name__ == "__main__":
    # Quick manual check: python -m llm.intent_emotion
    samples = [
        "Where is my husband?",
        "When is my son coming?",
        "What time is dinner?",
        "I miss my daughter so much",
        "Good morning!",
        "I'm scared, I don't know where I am",
    ]
    for s in samples:
        r = classify_intent(s)
        print(f"{s!r:45} -> emotion={r.emotion:10} emotional_need={r.is_emotional_need}"
              f" factual={r.is_factual_ask}")