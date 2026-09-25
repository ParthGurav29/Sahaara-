"""
backend/tests/run_demo_scenarios.py

Manual, plain-text (no voice, no UI) run-through of the demo scenarios
from Sahaara.txt section 10. This is Phase 1.4 — the point is to read
actual candidate responses and judge whether they FEEL right, not just
whether they pass automated checks. The automated checks
(check_response_structure, check_guardrails) run as a first-pass filter,
but a human should still read every line.

Two modes:
  --interactive   Prints the assembled prompt for each turn, then waits
                   for you to paste in the model's reply (from Nebius
                   playground, or wherever you're running Nemotron).
                   Use this once llm/nemotron_client.py exists, or
                   whenever you want to test with a real model by hand.

  --scripted      Runs against a set of pre-written candidate responses
                   (see SCRIPTED_RESPONSES below) so the pipeline itself
                   — profile, memory box, session notes, prompt assembly,
                   structure check, guardrail check — can be validated
                   end-to-end without needing live model access. Useful
                   right now, and useful later as a regression check.

Either way, output is the same: for each turn, the assembled prompt,
the response, and a PASS/FAIL against both check layers.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from llm.intent_emotion import classify_intent
from llm.response_policy import build_prompt, check_response_structure
from memory.memory_box import MemoryBox
from memory.profile import load_profile
from memory.session_notes import SessionNotes
from safety.guardrails import check_guardrails


def run_turn(profile, box, session, user_message, response_text, label=""):
    intent = classify_intent(user_message)
    memory_match = box.match(user_message)
    is_repeat = session.is_repeated_question(user_message)
    sustained = session.sustained_distress()

    bundle = build_prompt(
        profile, user_message, intent,
        memory_box_match=memory_match,
        is_repeated_question=is_repeat,
        sustained_distress=sustained,
    )

    structure = check_response_structure(response_text)
    guardrail = check_guardrails(response_text, profile)

    session.record_turn(user_message, emotion=intent.emotion)

    print(f"\n{'=' * 70}")
    print(f"SCENE: {label}")
    print(f"{'=' * 70}")
    print(f"ASHA SAYS:      {user_message}")
    print(f"  intent:       emotion={intent.emotion} emotional_need={intent.is_emotional_need}"
          f" factual={intent.is_factual_ask}")
    print(f"  memory box:   {'MATCH -> ' + memory_match.offer_line if memory_match else 'no match'}")
    print(f"  repeated?:    {is_repeat}")
    print(f"  distress?:    {sustained}")
    print(f"\nSAHAARA REPLIES: {response_text}")
    print(f"\n  structure check: {'PASS' if structure.passes else 'FAIL'}"
          f" (validation={structure.has_validation_language},"
          f" open_ended={structure.has_open_ended_question},"
          f" corrective={structure.has_corrective_language})")
    print(f"  guardrail check: {'PASS' if guardrail.passed else 'FAIL ' + str(guardrail.violations)}")

    overall = structure.passes and guardrail.passed
    print(f"  OVERALL: {'✅ PASS' if overall else '❌ NEEDS REVISION'}")
    return overall


# ---- Scripted candidate responses ----------------------------------------
# These are hand-written, manually reviewed stand-ins for a real Nemotron
# reply — written to deliberately follow response_policy.py's instructions,
# so we can validate the SCAFFOLDING (prompt assembly, structure/guardrail
# checks, session state) before live model access exists. Swap
# get_response() below for a real llm/nemotron_client.py call later —
# nothing else in this script needs to change.

SCRIPTED_TURNS = [
    {
        "label": "1. Personal memory / Memory Box (emotional open)",
        "user_message": "I miss my daughter so much today.",
        "response_text": (
            "That sounds like a heavy feeling to carry, I'm right here with you. "
            "You used to tell me about the letters Priya wrote you when she was little. "
            "Would you like to hear something Priya left for you?"
        ),
    },
    {
        "label": "2. Distress without correction",
        "user_message": "Where is my husband? I want to see Vikram.",
        "response_text": (
            "I can hear how much you're missing him right now, that's a lot to feel. "
            "It's a quiet afternoon here at home with me. "
            "Would you like to sit by the window for a bit, or shall I put on some music?"
        ),
    },
    {
        "label": "3a. Repetition — first ask",
        "user_message": "When is my son coming?",
        "response_text": (
            "I know waiting can feel long, I'm here with you in the meantime. "
            "Rohan usually visits on the weekend. "
            "Would you like to look at some photos of him while we wait, or have a cup of tea?"
        ),
    },
    {
        "label": "3b. Repetition — same question, minutes later",
        "user_message": "When is my son coming?",
        "response_text": (
            "I understand, it can feel like time is moving slowly today. "
            "Rohan will be here soon on his usual visit. "
            "Would you like some tea while we wait, or shall we sit outside for a bit?"
        ),
    },
]


def run_scripted():
    profile = load_profile()
    box = MemoryBox(profile)
    session = SessionNotes(profile_id=profile.profile_id)

    results = []
    for turn in SCRIPTED_TURNS:
        ok = run_turn(profile, box, session, turn["user_message"], turn["response_text"], turn["label"])
        results.append((turn["label"], ok))

    print(f"\n{'=' * 70}")
    print("SUMMARY")
    print(f"{'=' * 70}")
    for label, ok in results:
        print(f"  {'✅' if ok else '❌'}  {label}")
    total_pass = sum(1 for _, ok in results if ok)
    print(f"\n{total_pass}/{len(results)} scenes passed automated checks.")
    print("Automated checks are a first-pass filter only — read every reply above "
          "and judge by ear whether it actually feels calm, warm, and dignified.")


def run_interactive():
    profile = load_profile()
    box = MemoryBox(profile)
    session = SessionNotes(profile_id=profile.profile_id)

    print("Interactive mode. For each turn you'll see the assembled prompt.")
    print("Paste it into your model of choice, then paste the reply back here.\n")

    while True:
        user_message = input("Asha says (or 'quit'): ").strip()
        if user_message.lower() in ("quit", "exit", ""):
            break

        intent = classify_intent(user_message)
        memory_match = box.match(user_message)
        is_repeat = session.is_repeated_question(user_message)
        sustained = session.sustained_distress()
        bundle = build_prompt(
            profile, user_message, intent,
            memory_box_match=memory_match,
            is_repeated_question=is_repeat,
            sustained_distress=sustained,
        )

        print("\n--- SYSTEM PROMPT (paste this + user message into your model) ---")
        print(bundle.system)
        print("\n--- USER MESSAGE ---")
        print(bundle.user)
        print("---\n")

        response_text = input("Paste the model's reply: ").strip()
        run_turn(profile, box, session, user_message, response_text, label="interactive turn")
        print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run Sahaara demo scenarios as plain text, no voice/UI.")
    parser.add_argument("--interactive", action="store_true", help="Paste in real model replies by hand.")
    args = parser.parse_args()

    if args.interactive:
        run_interactive()
    else:
        run_scripted()