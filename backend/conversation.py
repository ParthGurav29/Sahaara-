"""
backend/conversation.py

Single source of truth for a conversational turn. Both main.py (/chat)
and voice/voice_pipeline.py call process_turn().

Phase 4: uses SessionManager for per-profile state instead of global singletons.
"""

from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

from llm.intent_emotion import classify_intent
from llm.provider import ProviderError, get_provider
from llm.response_policy import build_prompt, check_response_structure
from safety.guardrails import SAFE_FALLBACK_RESPONSE, check_guardrails
from safety.escalation import EscalationTier
from safety.consent import ConsentState
from events.event_log import EventType, FallbackType
from session_manager import SessionManager
import time
import re

# ---- Single session manager for the app ----
_session_manager = SessionManager()

# Provider loaded once
try:
    provider = get_provider()
    provider_init_error = None
except ProviderError as e:
    provider = None
    provider_init_error = str(e)

# Scheduler reference — set by main.py at startup
_scheduler = None


def _get_session():
    """Get the active profile session. Creates default if none active."""
    session = _session_manager.get_active()
    if session is None:
        raise RuntimeError("No active profile. Initialize the database and activate a profile first.")
    return session


def process_turn(user_message: str) -> dict:
    """Runs one full turn through the pipeline."""
    s = _get_session()
    
    if provider is None:
        return {"error": f"LLM provider not configured: {provider_init_error}"}

    # ---- CONSENT HANDLING ----
    s.consent_manager.check_timeout()
    
    if s.consent_manager.state == ConsentState.TIMED_OUT:
        pending_contact = s.consent_manager.context.contact_name if s.consent_manager.context else "caregiver"
        return {
            "response": "I'll give you some time. I'm here if you need anything.",
            "meta": {"emotion": "neutral", "is_repeated_question": False,
                     "sustained_distress": False, "consent_outcome": "timeout",
                     "contact_called": None, "tier": s.escalation_engine.current_tier.value},
        }
    
    if s.consent_manager.state == ConsentState.PENDING:
        pending_contact = s.consent_manager.context.contact_name if s.consent_manager.context else "caregiver"
        consent_result = s.consent_manager.interpret_response(user_message)
        
        if consent_result == ConsentState.ACCEPTED:
            s.alert_builder.build_consent_alert("accepted", pending_contact, s.escalation_engine.current_tier)
            return {
                "response": f"I've noted that you'd like to reach out to {pending_contact}. An alert has been logged on Rohan's dashboard.",
                "meta": {"emotion": "neutral", "is_repeated_question": False,
                         "sustained_distress": False, "consent_outcome": "accepted",
                         "contact_called": pending_contact, "tier": s.escalation_engine.current_tier.value},
            }
        elif consent_result == ConsentState.DECLINED:
            s.alert_builder.build_consent_alert("declined", pending_contact, s.escalation_engine.current_tier)
            return {
                "response": f"I understand. I won't call {pending_contact} right now. I'm here if you need anything.",
                "meta": {"emotion": "neutral", "is_repeated_question": False,
                         "sustained_distress": False, "consent_outcome": "declined",
                         "contact_called": None, "tier": s.escalation_engine.current_tier.value},
            }
        elif consent_result == ConsentState.TIMED_OUT:
            s.alert_builder.build_consent_alert("timeout", pending_contact, s.escalation_engine.current_tier)
            return {
                "response": "I'll give you some time. I'm here if you need anything.",
                "meta": {"emotion": "neutral", "is_repeated_question": False,
                         "sustained_distress": False, "consent_outcome": "timeout",
                         "contact_called": None, "tier": s.escalation_engine.current_tier.value},
            }
        else:
            return {
                "response": "Would you like me to call them?",
                "meta": {"emotion": "neutral", "is_repeated_question": False,
                         "sustained_distress": False, "consent_pending": True},
            }

    # ---- RED TIER TRIGGERS ----
    intent = classify_intent(user_message)
    if intent.is_danger_statement:
        s.event_log.log_danger_statement(phrase=user_message, matched_pattern="danger_pattern")
        s.escalation_engine.force_tier(EscalationTier.RED, "danger_statement")

    # ---- MEMORY / CONTEXT ----
    
    # Check for safe task actions
    from actions import detect_action, tell_time, play_song, set_reminder
    action_result = detect_action(user_message)
    action_context = ""
    if action_result:
        atype = action_result["action_type"]
        params = action_result.get("params", {})
        if atype == "tell_time":
            result = tell_time()
            if result.success:
                action_context = f"[FACT: {result.result_text}]"
        elif atype == "play_song":
            result = play_song(params.get("song_name", ""))
            if result.success:
                action_context = f"[ACTION PERFORMED: {result.result_text}]"
            else:
                action_context = f"[ACTION FAILED: {result.result_text}]"
        elif atype == "set_reminder":
            result = set_reminder(params.get("text", ""), params.get("minutes", 5), scheduler=_scheduler)
            if result.success:
                action_context = f"[ACTION PERFORMED: {result.result_text}]"
    
    user_message_with_action = f"{action_context}\n\nUser said: {user_message}" if action_context else user_message
    
    memory_match = s.memory_box.match(user_message)
    
    # Log Memory Box play
    if memory_match:
        s.event_log.log_memory_box_played(
            clip_filename=memory_match.clip_filename,
            family_member_name=memory_match.family_member_name,
            triggered_by=memory_match.triggered_by,
        )
    
    is_repeat = s.session_notes.is_repeated_question(user_message)
    sustained = s.session_notes.sustained_distress()
    
    if is_repeat:
        s.event_log.log_repeat(user_message, s.session_notes.repeat_count(user_message), True)
    if sustained:
        s.event_log.log_distress(sustained=True, emotion=intent.emotion, duration_seconds=s.session_notes.duration_seconds())
    
    # ---- ESCALATION ----
    prev_tier = s.escalation_engine.current_tier
    s.escalation_engine.evaluate(user_message, intent)
    current_tier = s.escalation_engine.current_tier.value
    
    if intent and (intent.is_physical_urgent or intent.is_physical_general):
        urgency = "urgent" if intent.is_physical_urgent else "general"
        matched = intent.matched_physical_keywords[0] if intent.matched_physical_keywords else "unknown"
        s.event_log.log_physical_symptom(phrase=user_message, matched_pattern=matched, urgency=urgency,
                                         tier_escalated_to="red" if intent.is_physical_urgent else "orange")
        s.alert_builder.build_physical_symptom_alert(phrase=user_message, matched_pattern=matched,
                                                      urgency=urgency, tier_escalated_to="red" if intent.is_physical_urgent else "orange")
    
    if prev_tier != s.escalation_engine.current_tier:
        tier_changes = s.event_log.get_events(EventType.TIER_CHANGE)
        if tier_changes:
            latest = tier_changes[-1]
            s.alert_builder.build_tier_change_alert(
                tier_from=prev_tier, tier_to=s.escalation_engine.current_tier,
                reason=latest.detail.get("reason", "unknown"), context=latest.detail.get("context", {}),
            )
    
    # ---- BUILD PROMPT ----
    recent_replies = s.session_notes.get_recent_sahaara_replies(4)
    bundle = build_prompt(
        s.profile, user_message_with_action, intent,
        memory_box_match=memory_match, is_repeated_question=is_repeat,
        sustained_distress=sustained, escalation_tier=current_tier,
        recent_replies=recent_replies, session_context_block=s.session_context_block,
    )
    
    # ---- CALL LLM ----
    try:
        result = provider.complete_from_prompt_bundle(bundle)
    except ProviderError as e:
        return {"error": f"LLM request failed: {e}"}
    
    response_text = result.text
    
    # ---- SAFETY CHECKS ----
    structure = check_response_structure(response_text)
    guardrail = check_guardrails(response_text, s.profile)
    
    used_fallback = False
    if not guardrail.passed:
        response_text = SAFE_FALLBACK_RESPONSE
        used_fallback = True
        s.event_log.log_fallback(FallbackType.GUARDRAIL, detail={"violations": guardrail.violations})
    
    # ---- CONTEXT TRACE ----
    context_trace = {
        "profile_facts_used": {"preferred_address": s.profile.preferred_address, "hometown": s.profile.hometown, "work": s.profile.work},
        "intent": {"emotion": intent.emotion, "is_emotional_need": intent.is_emotional_need,
                   "is_danger_statement": intent.is_danger_statement, "is_physical_urgent": intent.is_physical_urgent, "is_physical_general": intent.is_physical_general},
        "memory_box": {"matched": memory_match is not None, "family_member": memory_match.family_member_name if memory_match else None, "clip": memory_match.clip_filename if memory_match else None},
        "escalation": {"tier": current_tier, "is_repeated_question": is_repeat, "sustained_distress": sustained},
        "recent_replies_count": len(recent_replies) if recent_replies else 0,
        "used_fallback": used_fallback,
    }
    
    # ---- DETECT CONSENT ----
    consent_triggered = _detect_consent_request(response_text, current_tier, s)
    if consent_triggered:
        contact_name, relationship, question = consent_triggered
        s.consent_manager.ask_consent(contact_name=contact_name, contact_relationship=relationship,
                                       tier=EscalationTier(current_tier), question_text=question)
    
    # ---- RECORD TURNS ----
    s.session_notes.record_turn(user_message, emotion=intent.emotion, speaker="user")
    s.session_notes.record_turn(response_text, emotion=intent.emotion, speaker="sahaara")
    s.memory_box.record_turn()
    
    s.event_log.log_conversation_turn(speaker="user", text=user_message, emotion=intent.emotion)
    s.event_log.log_conversation_turn(speaker="sahaara", text=response_text, emotion=intent.emotion, context_trace=context_trace)
    
    s.detect_and_queue_suggestions(user_message)
    
    return {
        "response": response_text,
        "meta": {
            "emotion": intent.emotion, "is_repeated_question": is_repeat,
            "sustained_distress": sustained,
            "memory_box_offered": memory_match.offer_line if memory_match else None,
            "guardrail_fallback_used": used_fallback,
            "guardrail_violations": guardrail.violations if not guardrail.passed else [],
            "structure_check_passed": structure.passes, "model": result.model,
            "tier": current_tier, "consent_state": s.consent_manager.state.value,
        },
    }


def _detect_consent_request(response_text: str, tier: str, s) -> tuple | None:
    text_lower = response_text.lower()
    if tier not in ("yellow", "orange"):
        return None
    patterns = [
        (r"would you like me to call (\w+)\??", "call"),
        (r"should i call (\w+)\??", "call"),
        (r"may i call (\w+)\??", "call"),
        (r"can i call (\w+)\??", "call"),
        (r"do you want me to call (\w+)\??", "call"),
        (r"would you like me to reach out to (\w+)\??", "reach out to"),
        (r"shall i call (\w+)\??", "call"),
    ]
    for pattern, verb in patterns:
        match = re.search(pattern, text_lower)
        if match:
            contact_name = match.group(1).capitalize()
            relationship = "caregiver"
            for member in s.profile.get_family():
                if member["name"].lower() == contact_name.lower():
                    relationship = member["relationship"]
                    break
            return (contact_name, relationship, match.group(0))
    return None


def reset_pipeline() -> None:
    """Reset all pipeline state for a clean scenario run."""
    s = _get_session()
    _session_manager.reset(s.profile_id)


# ---- Module-level accessors for main.py compatibility ----

@property
def _profile(self=None):
    s = _get_session()
    return s.profile

# For main.py access
def get_session_manager():
    return _session_manager

# Convenience: expose common state for main.py endpoints
# These are lazy — they resolve from the active session each call

def _get_attr(name):
    s = _get_session()
    return getattr(s, name)

# Don't break existing imports from main.py
# main.py imports: event_log, session_notes, escalation_engine, consent_manager,
# memory_box, alert_store, suggestion_queue, profile

class _LazyAttr:
    def __getattr__(self, name):
        s = _get_session()
        return getattr(s, name)

# Create lazy proxy for main.py compatibility
# This allows `from conversation import event_log` to work
import sys
_lazy = _LazyAttr()
_mod = sys.modules[__name__]
for _attr_name in ['event_log', 'session_notes', 'escalation_engine', 'consent_manager',
                    'memory_box', 'alert_store', 'alert_builder', 'suggestion_queue', 'profile']:
    setattr(_mod, _attr_name, property(lambda self, n=_attr_name: getattr(_get_session(), n)))