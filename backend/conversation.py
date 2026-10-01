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
from safety.escalation import EscalationEngine, EscalationConfig, EscalationTier
from safety.consent import ConsentManager, ConsentState
from dashboard.alerts import AlertBuilder
from events.event_log import (
    EventLog,
    EventType,
    EscalationTier,
    FallbackType,
    ConsentOutcome,
    init_event_log,
    get_event_log,
)
import time
import re

# ---- Loaded once, shared across every /chat request AND every voice turn ----
# Same single-persona / single-global-session scope as before (see
# docs/architecture.md) — voice doesn't change that decision, it just
# shares it instead of duplicating it.

profile = load_profile()
memory_box = MemoryBox(profile)
session_notes = SessionNotes(profile_id=profile.profile_id)

# Initialize event log (persist to JSONL for demo)
event_log = init_event_log(profile, persist=True, demo_mode=True)

# Initialize escalation engine
escalation_config = EscalationConfig(demo_mode=True, demo_multiplier=300.0)
escalation_engine = EscalationEngine(event_log, session_notes, escalation_config)

# Initialize consent manager
consent_manager = ConsentManager(
    event_log=event_log,
    timeout_seconds=30.0,
    demo_mode=True,
    demo_multiplier=300.0,
)

# Initialize alert builder
alert_builder = AlertBuilder(event_log, profile)

# Unresponsive detection: track when Sahaara last spoke
UNRESPONSIVE_THRESHOLD_SECONDS = 90.0  # 1.5 minutes real time
_last_sahaara_prompt_time: float = 0.0
_unresponsive_enabled: bool = False  # Set to True for voice mode

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
    global _last_sahaara_prompt_time
    
    if provider is None:
        return {"error": f"LLM provider not configured: {provider_init_error}"}

    # ---- CONSENT HANDLING (must be first) ----
    # Check for pending consent and interpret user's response
    consent_manager.check_timeout()
    
    # Handle already-timed-out consent
    if consent_manager.state == ConsentState.TIMED_OUT:
        pending_contact = consent_manager.context.contact_name if consent_manager.context else "caregiver"
        return {
            "response": f"I'll give you some time. I'm here if you need anything.",
            "meta": {
                "emotion": "neutral",
                "is_repeated_question": False,
                "sustained_distress": False,
                "consent_outcome": "timeout",
                "contact_called": None,
                "tier": escalation_engine.current_tier.value,
            },
        }
    
    if consent_manager.state == ConsentState.PENDING:
        # Capture contact name before it gets reset by interpret_response
        pending_contact = consent_manager.context.contact_name if consent_manager.context else "caregiver"
        consent_result = consent_manager.interpret_response(user_message)
        
        if consent_result == ConsentState.ACCEPTED:
            # User accepted - proceed with escalation (in real implementation,
            # this would trigger the actual call; for now we acknowledge)
            alert_builder.build_consent_alert("accepted", pending_contact, escalation_engine.current_tier)
            return {
                "response": f"Calling {pending_contact} now. They'll be on the line in a moment.",
                "meta": {
                    "emotion": "neutral",
                    "is_repeated_question": False,
                    "sustained_distress": False,
                    "consent_outcome": "accepted",
                    "contact_called": pending_contact,
                    "tier": escalation_engine.current_tier.value,
                },
            }
        elif consent_result == ConsentState.DECLINED:
            # User declined - respect their choice, log quietly
            alert_builder.build_consent_alert("declined", pending_contact, escalation_engine.current_tier)
            return {
                "response": f"I understand. I won't call {pending_contact} right now. I'm here if you need anything.",
                "meta": {
                    "emotion": "neutral",
                    "is_repeated_question": False,
                    "sustained_distress": False,
                    "consent_outcome": "declined",
                    "contact_called": None,
                    "tier": escalation_engine.current_tier.value,
                },
            }
        elif consent_result == ConsentState.TIMED_OUT:
            # Timed out - treat as decline for safety
            alert_builder.build_consent_alert("timeout", pending_contact, escalation_engine.current_tier)
            return {
                "response": f"I'll give you some time. I'm here if you need anything.",
                "meta": {
                    "emotion": "neutral",
                    "is_repeated_question": False,
                    "sustained_distress": False,
                    "consent_outcome": "timeout",
                    "contact_called": None,
                    "tier": escalation_engine.current_tier.value,
                },
            }
        else:
            # Still pending (ambiguous response) - don't process as normal turn
            # Re-ask or wait
            return {
                "response": "Would you like me to call them?",
                "meta": {
                    "emotion": "neutral",
                    "is_repeated_question": False,
                    "sustained_distress": False,
                    "consent_pending": True,
                },
            }

    # ---- RED tier triggers ----
    # 1. Danger statement detection
    intent = classify_intent(user_message)
    if intent.is_danger_statement:
        event_log.log_danger_statement(
            phrase=user_message,
            matched_pattern="danger_pattern",
        )
        escalation_engine.force_tier(EscalationTier.RED, "danger_statement")

    # Unresponsive detection (prolonged silence after Sahaara prompt)
    # NOTE: Only applies in voice mode where actual silence occurs.
    # In text mode / tests, disabled by default. Voice pipeline should
    # call a separate function or set a flag to enable it.
    global _unresponsive_enabled
    if _unresponsive_enabled and _last_sahaara_prompt_time > 0:
        silence_duration = time.time() - _last_sahaara_prompt_time
        # Apply demo mode compression if enabled
        threshold = UNRESPONSIVE_THRESHOLD_SECONDS
        if escalation_engine.config.demo_mode:
            threshold = UNRESPONSIVE_THRESHOLD_SECONDS / escalation_engine.config.demo_multiplier
        if silence_duration > threshold:
            event_log.log_system_health(
                message=f"Unresponsive: {silence_duration:.1f}s silence after prompt (threshold: {threshold:.1f}s)",
                severity="warning",
            )
            escalation_engine.force_tier(EscalationTier.RED, "unresponsive")

    # ---- 1. Memory / context gathering ----
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

    # Evaluate escalation tier
    prev_tier = escalation_engine.current_tier
    escalation_engine.evaluate(user_message)
    current_tier_enum = escalation_engine.current_tier
    current_tier = current_tier_enum.value
    
    # Trigger alert on tier change
    if prev_tier != current_tier_enum:
        tier_change_events = event_log.get_events(EventType.TIER_CHANGE)
        if tier_change_events:
            latest_change = tier_change_events[-1]
            alert = alert_builder.build_tier_change_alert(
                tier_from=prev_tier,
                tier_to=current_tier_enum,
                reason=latest_change.detail.get("reason", "unknown"),
                context=latest_change.detail.get("context", {}),
            )
            # Alert is built but we don't send it anywhere yet - 
            # dashboard endpoints will fetch it

    # ---- 2. Build the prompt (Listen/Validate/Ground/Offer policy) ----
    bundle = build_prompt(
        profile,
        user_message,
        intent,
        memory_box_match=memory_match,
        is_repeated_question=is_repeat,
        sustained_distress=sustained,
        escalation_tier=current_tier,
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
        response_text = SAFE_FALLBACK_RESPONSE
        used_fallback = True
        event_log.log_fallback(
            FallbackType.GUARDRAIL,
            detail={"violations": guardrail.violations},
        )

    # ---- 5. Detect consent request in response ----
    # If Sahaara asks "Would you like me to call [Name]?", start consent flow
    consent_triggered = _detect_consent_request(response_text, current_tier)
    if consent_triggered:
        # consent_triggered is (contact_name, relationship, question_text)
        contact_name, relationship, question = consent_triggered
        consent_manager.ask_consent(
            contact_name=contact_name,
            contact_relationship=relationship,
            tier=EscalationTier(current_tier),
            question_text=question,
        )

    # ---- 6. Update session state for future turns ----
    session_notes.record_turn(user_message, emotion=intent.emotion)
    _last_sahaara_prompt_time = time.time()

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
            "tier": current_tier,
            "consent_state": consent_manager.state.value,
        },
    }


def _detect_consent_request(response_text: str, tier: str) -> tuple[str, str, str] | None:
    """
    Detect if the response contains a consent question.
    Returns (contact_name, relationship, question_text) if found.
    
    Looks for patterns like:
    - "Would you like me to call Priya?"
    - "Should I call your daughter?"
    - "May I call Rohan?"
    """
    text_lower = response_text.lower()
    
    # Only check for consent in Yellow/Orange tiers
    if tier not in ("yellow", "orange"):
        return None
    
    # Patterns for consent questions
    call_patterns = [
        (r"would you like me to call (\w+)\??", "call"),
        (r"should i call (\w+)\??", "call"),
        (r"may i call (\w+)\??", "call"),
        (r"can i call (\w+)\??", "call"),
        (r"do you want me to call (\w+)\??", "call"),
        (r"would you like me to reach out to (\w+)\??", "reach out to"),
        (r"shall i call (\w+)\??", "call"),
    ]
    
    for pattern, verb in call_patterns:
        match = re.search(pattern, text_lower)
        if match:
            contact_name = match.group(1).capitalize()
            # Get relationship from profile
            relationship = "caregiver"
            for member in profile.get_family():
                if member["name"].lower() == contact_name.lower():
                    relationship = member["relationship"]
                    break
            return (contact_name, relationship, match.group(0))
    
    return None