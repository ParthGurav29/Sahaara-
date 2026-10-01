"""
backend/conversation.py

Single source of truth for a conversational turn: intent classification,
memory/session context, prompt assembly, LLM call, and post-generation
guardrails. Both main.py (/chat, text mode) and voice/voice_pipeline.py
(voice mode) call process_turn() so text and voice can never drift into
two different conversational behaviors — one pipeline, two front ends.

This is a straight extraction of what main.py's /chat route used to do
inline — same steps, same order, same fallback behavior. No logic changes.
"""

from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

from llm.intent_emotion import classify_intent
from llm.provider import ProviderError, get_provider
from llm.response_policy import build_prompt, check_response_structure
from memory.memory_box import MemoryBox
from memory.profile import load_profile
from memory.session_notes import SessionNotes
from safety.guardrails import SAFE_FALLBACK_RESPONSE, check_guardrails
from events.event_log import (
    EventLog,
    EventType,
    EscalationTier,
    FallbackType,
    ConsentOutcome,
    init_event_log,
    get_event_log,
)

# ---- Loaded once, shared across every /chat request AND every voice turn ----
# Same single-persona / single-global-session scope as before (see
# docs/architecture.md) — voice doesn't change that decision, it just
# shares it instead of duplicating it.

profile = load_profile()
memory_box = MemoryBox(profile)
session_notes = SessionNotes(profile_id=profile.profile_id)

# Initialize event log (persist to JSONL for demo)
event_log = init_event_log(profile, persist=True, demo_mode=True)

try:
    provider = get_provider()
    provider_init_error = None
except ProviderError as e:
    provider = None
    provider_init_error = str(e)


def process_turn(user_message: str) -> dict:
    """
    Runs one full turn through the Phase 1 pipeline. Returns
    {"response": str, "meta": {...}} on success, or {"error": str} if the
    provider isn't configured or the LLM call fails — same shape /chat has
    always returned, so main.py can keep returning this directly.
    """
    if provider is None:
        return {"error": f"LLM provider not configured: {provider_init_error}"}

    # ---- 1. Memory / context gathering ----
    intent = classify_intent(user_message)
    memory_match = memory_box.match(user_message)
    is_repeat = session_notes.is_repeated_question(user_message)
    sustained = session_notes.sustained_distress()

    # Log repetition event
    if is_repeat:
        event_log.log_repeat(user_message, session_notes.repeat_count(user_message), True)

    # Log distress
    if sustained:
        event_log.log_distress(
            sustained=True,
            emotion=intent.emotion,
            duration_seconds=session_notes.duration_seconds(),
        )

    # ---- 2. Build the prompt (Listen/Validate/Ground/Offer policy) ----
    bundle = build_prompt(
        profile,
        user_message,
        intent,
        memory_box_match=memory_match,
        is_repeated_question=is_repeat,
        sustained_distress=sustained,
    )

    # ---- 3. Call the LLM ----
    try:
        result = provider.complete_from_prompt_bundle(bundle)
    except ProviderError as e:
        return {"error": f"LLM request failed: {e}"}

    response_text = result.text

    # ---- 4. Post-generation safety checks ----
    structure = check_response_structure(response_text)
    guardrail = check_guardrails(response_text, profile)

    used_fallback = False
    if not guardrail.passed:
        # Hard rule, unchanged: a guardrail violation is never sent to the
        # person, voice or text. No retry-live, straight to the safe line.
        # Hard rule: a guardrail violation is never sent to the person
        response_text = SAFE_FALLBACK_RESPONSE
        used_fallback = True
        event_log.log_fallback(
            FallbackType.GUARDRAIL,
            detail={"violations": guardrail.violations},
        )

    # ---- 5. Update session state for future turns ----
    session_notes.record_turn(user_message, emotion=intent.emotion)

    return {
        "response": response_text,
        "meta": {
            "emotion": intent.emotion,
            "is_repeated_question": is_repeat,
            "sustained_distress": sustained,
            "memory_box_offered": memory_match.offer_line if memory_match else None,
            "guardrail_fallback_used": used_fallback,
            "guardrail_violations": guardrail.violations if not guardrail.passed else [],
            "structure_check_passed": structure.passes,
            "model": result.model,
        },
    }