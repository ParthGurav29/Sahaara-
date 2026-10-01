"""
backend/tests/test_repetition_handling.py

Tests for Block 0: Repetition check.
Verifies that repetitions are tracked and the LLM is instructed to handle them with the same warmth without saying "I already told you".
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from memory.session_notes import SessionNotes
from llm.intent_emotion import classify_intent
from llm.response_policy import build_prompt
from memory.profile import load_profile

def _profile():
    return load_profile()

def test_session_notes_repetition_detection():
    notes = SessionNotes("test_profile")
    
    # 1st time
    notes.record_turn("When is my son coming?")
    assert not notes.is_repeated_question("When is my son coming?", min_repeats=2)
    
    # 2nd time
    notes.record_turn("When is my son coming?")
    assert notes.is_repeated_question("When is my son coming?", min_repeats=2)
    
    # 3rd time
    notes.record_turn("When is my son coming?")
    assert notes.is_repeated_question("When is my son coming?", min_repeats=2)
    assert notes.repeat_count("When is my son coming?") == 3

def test_repetition_instruction_in_prompt():
    profile = _profile()
    intent = classify_intent("When is my son coming?")
    
    # Not repeated
    bundle_first = build_prompt(profile, "When is my son coming?", intent, is_repeated_question=False)
    assert "asked something like this before" not in bundle_first.system.lower()
    
    # Repeated
    bundle_repeat = build_prompt(profile, "When is my son coming?", intent, is_repeated_question=True)
    system = bundle_repeat.system.lower()
    assert "asked something like this before" in system
    assert "same warmth" in system
    assert "never say" in system and "already told you" in system

if __name__ == "__main__":
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
    if failed > 0:
        sys.exit(1)
