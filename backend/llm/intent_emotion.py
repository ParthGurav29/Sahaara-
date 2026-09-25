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

DEFAULT_EMOTION = "calm"


@dataclass
class IntentResult:
    text: str
    emotion: str                       # best-guess primary emotion label
    is_emotional_need: bool            # should the response-policy validate first?
    is_factual_ask: bool               # does it also carry a literal factual question?
    matched_emotion_keywords: list[str] = field(default_factory=list)


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

    is_factual = any(re.search(p, text_lower) for p in FACTUAL_MARKERS)
    is_emotional = matched_emotion is not None and matched_emotion != "content"

    emotion = matched_emotion or (DEFAULT_EMOTION if not is_factual else "neutral")

    return IntentResult(
        text=text,
        emotion=emotion,
        is_emotional_need=is_emotional,
        is_factual_ask=is_factual,
        matched_emotion_keywords=matched_keywords,
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

    is_factual = any(re.search(p, text.lower()) for p in FACTUAL_MARKERS)
    return IntentResult(
        text=text,
        emotion=emotion,
        is_emotional_need=is_emotional_need,
        is_factual_ask=is_factual,
        matched_emotion_keywords=[],
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