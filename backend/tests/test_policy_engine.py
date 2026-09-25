"""
backend/tests/test_policy_engine.py

Tests for llm/response_policy.py and llm/intent_emotion.py.

Two things are being tested here, deliberately kept separate:
1. The PROMPT TEMPLATE contains the right instructions in the right
   order (validate before ground, offer one choice not an open question).
   This tests what we ask the model to do.
2. The check_response_structure() heuristic correctly scores SAMPLE
   response text against that policy shape. This tests our ability to
   QA actual model output later — since we can't unit-test the live
   LLM's behavior deterministically, this is the next best thing:
   confirm the checker itself is reliable against known-good and
   known-bad examples.
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from llm.intent_emotion import classify_intent
from llm.response_policy import build_prompt, check_response_structure
from memory.profile import load_profile


# ---- Fixtures --------------------------------------------------------

def _profile():
    return load_profile()


# ---- 1. Prompt template structure -------------------------------------

def test_prompt_instructs_validate_before_ground():
    profile = _profile()
    intent = classify_intent("Where is my husband?")
    bundle = build_prompt(profile, "Where is my husband?", intent)

    system = bundle.system
    validate_pos = system.lower().find("validate that emotion first")
    ground_pos = system.lower().find("ground gently")

    assert validate_pos != -1, "prompt must instruct validation"
    assert ground_pos != -1, "prompt must instruct grounding"
    assert validate_pos < ground_pos, "VALIDATE step must be instructed before GROUND step"


def test_prompt_forbids_open_ended_questions():
    profile = _profile()
    intent = classify_intent("What time is dinner?")
    bundle = build_prompt(profile, "What time is dinner?", intent)

    assert "never ask an open-ended question" in bundle.system.lower()
    assert "offer exactly one small" in bundle.system.lower()


def test_prompt_includes_avoid_topic_warning_when_relevant():
    profile = _profile()
    intent = classify_intent("Where is my husband?")
    bundle = build_prompt(profile, "Where is my husband?", intent)

    assert "sensitive topic" in bundle.system.lower()
    assert "vikram" in bundle.system.lower()


def test_prompt_includes_memory_box_offer_language_when_match_given():
    from memory.memory_box import MemoryBox

    profile = _profile()
    box = MemoryBox(profile)
    match = box.match("I miss my daughter")
    assert match is not None, "test setup: expected a Memory Box match for this profile"

    intent = classify_intent("I miss my daughter")
    bundle = build_prompt(profile, "I miss my daughter", intent, memory_box_match=match)

    assert "priya" in bundle.system.lower()
    assert "offer to play it" in bundle.system.lower()


def test_prompt_includes_repetition_instruction_without_calling_it_out_to_model_as_shameful():
    profile = _profile()
    intent = classify_intent("When is my son coming?")
    bundle = build_prompt(profile, "When is my son coming?", intent, is_repeated_question=True)

    assert "asked something like this before" in bundle.system.lower()
    assert "never say" in bundle.system.lower() and "already told you" in bundle.system.lower()


# ---- 2. Structural QA heuristic on sample responses ----------------------

GOOD_RESPONSE_VALIDATE_THEN_CHOICE = (
    "That sounds worrying, I'm here with you. It's a quiet afternoon here at home. "
    "Would you like to sit by the window, or listen to some music?"
)

BAD_RESPONSE_OPEN_ENDED = (
    "I understand that's hard. What would you like to do?"
)

BAD_RESPONSE_CORRECTIVE = (
    "No, that's not right, Vikram passed away years ago. What would you like to do now?"
)

BAD_RESPONSE_NO_VALIDATION = (
    "It's 3 o'clock. Would you like some tea?"
)


def test_structure_check_passes_good_response():
    result = check_response_structure(GOOD_RESPONSE_VALIDATE_THEN_CHOICE)
    assert result.has_validation_language is True
    assert result.has_open_ended_question is False
    assert result.has_corrective_language is False
    assert result.passes is True


def test_structure_check_flags_open_ended_question():
    result = check_response_structure(BAD_RESPONSE_OPEN_ENDED)
    assert result.has_open_ended_question is True
    assert result.passes is False


def test_structure_check_flags_corrective_language():
    result = check_response_structure(BAD_RESPONSE_CORRECTIVE)
    assert result.has_corrective_language is True
    assert result.passes is False


def test_structure_check_flags_missing_validation():
    result = check_response_structure(BAD_RESPONSE_NO_VALIDATION)
    assert result.has_validation_language is False
    assert result.passes is False


if __name__ == "__main__":
    # Allow running directly without pytest for a quick manual check:
    # python -m tests.test_policy_engine
    import inspect
    current_module = sys.modules[__name__]
    test_fns = [obj for name, obj in inspect.getmembers(current_module)
                if name.startswith("test_") and callable(obj)]
    passed, failed = 0, 0
    for fn in test_fns:
        try:
            fn()
            print(f"PASS: {fn.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"FAIL: {fn.__name__} -> {e}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed")