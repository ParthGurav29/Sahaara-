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
from llm.nemotron_client import DEFAULT_MAX_TOKENS
from llm.provider import ProviderError, get_provider
from llm.response_policy import build_prompt, check_response_structure
from safety.guardrails import SAFE_FALLBACK_RESPONSE, check_guardrails
from safety.escalation import EscalationTier
from safety.consent import ConsentState, ContinuationState
from events.event_log import EventType, FallbackType
from session_manager import SessionManager
import time
import re
import threading

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


def get_session_for_profile(profile_id: str):
    """Get a session for a specific profile ID."""
    return _session_manager.get(profile_id)


def process_turn(user_message: str, max_tokens: int = DEFAULT_MAX_TOKENS) -> dict:
    """Runs one full turn through the pipeline."""
    s = _get_session()

    # M3: let the proactive scheduler see real conversation activity
    # (drives the "recent conversation" skip rule + response-window tracking).
    if _scheduler is not None:
        try:
            _scheduler.record_user_turn()
        except Exception:
            pass

    if provider is None:
        return {"error": f"LLM provider not configured: {provider_init_error}"}

    # ---- CONSENT HANDLING ----
    s.consent_manager.check_timeout()
    
    if s.consent_manager.state == ConsentState.TIMED_OUT:
        # Consent timed out — treat as implicit decline, clear state, and
        # process this utterance normally. Never block danger/physical-urgent
        # classification. Timeout was already logged by _handle_timeout().
        s.consent_manager.state = ConsentState.IDLE
        s.consent_manager.context = None
        s.consent_manager._timeout_at = None
        # Fall through to normal processing below.
    
    if s.consent_manager.state == ConsentState.PENDING:
        pending_contact = s.consent_manager.context.contact_name if s.consent_manager.context else "caregiver"
        consent_result = s.consent_manager.interpret_response(user_message)
        
        if consent_result == ConsentState.ACCEPTED:
            alert = s.alert_builder.build_consent_alert("accepted", pending_contact, s.escalation_engine.current_tier)

            # Step 8a: attempt the push and wait up to ~1.0 s for the result.
            # If at least one subscription accepted, tell the person the alert
            # was sent — honestly, without claiming delivery or that the
            # caregiver has seen it. Otherwise fall back to the dashboard-only
            # line. Never blocks the turn: daemon thread, bounded join.
            #
            # A lock guards the handoff between the main thread (which chooses
            # the response wording) and the worker thread (which may finish
            # after the 1.0 s window). The worker logs push_sent_late only if
            # it succeeds after the dashboard-only wording was already chosen.
            push_accepted = False
            push_result = {}
            response_chosen = False
            decision_lock = threading.Lock()

            def _do_send():
                push_result.update(send_alert_push(alert, s.event_log))
                accepted = push_result.get("sent", 0) >= 1
                with decision_lock:
                    if accepted and response_chosen:
                        # Push succeeded after we already chose dashboard-only
                        # wording. Log it so the event log reflects reality.
                        try:
                            s.event_log.log_alert_sent(
                                alert_id=getattr(alert, "alert_id", ""),
                                tier=s.escalation_engine.current_tier.value,
                                status="sent",
                                sent=push_result.get("sent", 0),
                                failed=push_result.get("failed", 0),
                                revoked=push_result.get("revoked", 0),
                                reason="push_sent_late",
                                results=push_result.get("results", []),
                            )
                        except Exception:
                            pass

            try:
                from push_notify import send_alert_push
                _t = threading.Thread(target=_do_send, daemon=True, name="sahaara-push-consent")
                _t.start()
                _t.join(timeout=1.0)
                with decision_lock:
                    push_accepted = push_result.get("sent", 0) >= 1
            except Exception:
                push_accepted = False

            if push_accepted:
                response_text = f"I've sent {pending_contact} an alert."
            else:
                response_text = f"I've noted that you'd like to reach out to {pending_contact}. An alert has been logged on Rohan's dashboard."

            with decision_lock:
                response_chosen = True

            return {
                "response": response_text,
                "meta": {"emotion": "neutral", "is_repeated_question": False,
                         "sustained_distress": False, "consent_outcome": "accepted",
                         "contact_called": None, "tier": s.escalation_engine.current_tier.value},
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

    # ---- IDENTITY QUESTIONS ----
    # Handle "who are you" / identity questions with deterministic intro response
    if intent.is_identity_ask:
        from scheduling.models import get_wording_pool
        intro_pool = get_wording_pool("intro")
        if intro_pool:
            import random
            response_text = random.choice(intro_pool)["text"]
        else:
            # Fallback if pool is empty
            response_text = "Hello. I'm Sahaara, a voice companion. I'm not a person, but I'm here to listen and chat."
        
        s.event_log.log_conversation_turn(speaker="user", text=user_message, emotion=intent.emotion)
        s.event_log.log_conversation_turn(speaker="sahaara", text=response_text, emotion="neutral", context_trace={})
        
        return {
            "response": response_text,
            "meta": {
                "emotion": "neutral", "is_repeated_question": False,
                "sustained_distress": False, "memory_box_offered": None,
                "guardrail_fallback_used": False, "guardrail_violations": [],
                "structure_check_passed": True, "model": "deterministic",
                "tier": s.escalation_engine.current_tier.value, "consent_state": s.consent_manager.state.value,
            }
        }

    # ---- MEMORY / CONTEXT ----
    
    # ---- TASK ACTIONS ----
    # Handled further down, after the escalation block, so an emergency turn
    # can never be answered with a timer confirmation. Action turns reply
    # deterministically (no LLM) — a model that paraphrases "I set a timer"
    # will eventually say it when no timer exists.
    injected_facts: list[str] = []
    user_message_with_action = user_message
    
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

    # ---- TASK ACTIONS (deterministic, after escalation) ----
    # Skipped on danger / physical-distress turns: the safety reply must never
    # be replaced by "I've set a timer". Everything else returns a fixed reply
    # built from what actually happened — no LLM, so no invented completions.
    if not (intent.is_danger_statement or intent.is_physical_urgent or intent.is_physical_general):
        action_response = _handle_action(s, user_message, current_tier)
        if action_response is not None:
            return action_response

    # ---- CONTINUATION STATE (after danger/physical-urgent check) ----
    # If a previous answer was truncated with "Would you like me to go on?",
    # interpret this utterance as yes/no/timeout BEFORE normal processing.
    # Danger/physical-urgent classification already ran above, so a danger
    # statement here would have been caught and returned as Red already.
    continuation_response = _handle_continuation(s, user_message, intent, current_tier)
    if continuation_response is not None:
        return continuation_response

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
        result = provider.complete_from_prompt_bundle(bundle, max_tokens=max_tokens)
    except ProviderError as e:
        return {"error": f"LLM request failed: {e}"}
    
    response_text = result.text

    # ---- REPLY-LENGTH RULE (voice-friendly) ----
    # If the LLM reply is long (> 4 sentences), truncate to a short lead,
    # store the rest for continuation, and append a check-in.
    if not intent.is_danger_statement and not intent.is_physical_urgent and not intent.is_physical_general:
        truncated_lead, remaining_text = _truncate_for_voice(response_text)
        if remaining_text:
            response_text = truncated_lead + " Would you like me to go on?"
            s.continuation_manager.ask_continuation(
                remaining_text=remaining_text,
                topic_note="general knowledge answer",
            )

    # ---- SAFETY CHECKS ----
    # Structure check is QA-only (never gates); guardrails below are the
    # runtime gate. Turn type follows the intent so the QA score reflects
    # the right protocol (M1: danger/physical keep the full protocol).
    if intent.is_physical_urgent:
        _structure_turn_type = "physical_urgent"
    elif intent.is_physical_general:
        _structure_turn_type = "physical_general"
    elif intent.is_danger_statement:
        _structure_turn_type = "danger"
    elif intent.is_factual_ask:
        _structure_turn_type = "factual"
    else:
        _structure_turn_type = "emotional"
    structure = check_response_structure(response_text, turn_type=_structure_turn_type)
    guardrail = check_guardrails(response_text, s.profile, injected_facts=injected_facts)
    
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
        # A normal conversation turn never starts or stops playback; these keys
        # keep the shape identical to an action turn so callers can check them
        # without branching.
        "radio_stream_url": None,
        "audio_url": None,
        "stop_media": False,
        "meta": {
            "emotion": intent.emotion, "is_repeated_question": is_repeat,
            "sustained_distress": sustained,
            "memory_box_offered": memory_match.offer_line if memory_match else None,
            "guardrail_fallback_used": used_fallback,
            "guardrail_violations": guardrail.violations if not guardrail.passed else [],
            "structure_check_passed": structure.passes, "model": result.model,
            "tier": current_tier, "consent_state": s.consent_manager.state.value,
            "continuation_state": s.continuation_manager.state.value,
            # Action turns return earlier with their own media fields; ordinary
            # conversation never starts or stops playback.
            "radio_stream_url": None,
            "audio_url": None,
            "stop_media": False,
        },
    }


def _handle_action(s, user_message: str, current_tier: str):
    """Run one fixed task action and return a deterministic reply.

    Returns None when the utterance is not an action. The reply is built from
    the ActionResult and never from the model, so a failed action can only
    ever say that it failed.

    Owner decisions (2026-10-28): no LLM follow-up; timers run on real time;
    reminder text is sanitised (medication never names a dose); weather is not
    an action any more.
    """
    from actions import (
        detect_action, tell_time, set_timer, set_reminder, list_timers,
        cancel_timer, play_song, play_radio, stop_media, mark_media_playing,
    )

    profile_id = s.profile.profile_id
    action = detect_action(user_message, profile_id=profile_id)
    if not action:
        return None

    atype = action["action_type"]
    params = action.get("params", {}) or {}
    media_url = None
    stop_requested = False

    if atype == "tell_time":
        result = tell_time()
    elif atype == "set_timer":
        result = set_timer(
            seconds=params.get("seconds") or 0,
            label=params.get("label") or "",
            profile_id=profile_id,
            label_raw=user_message,
        )
    elif atype == "set_reminder":
        result = set_reminder(
            seconds=params.get("seconds"),
            label=params.get("label") or "",
            profile_id=profile_id,
            label_raw=user_message,
            at=params.get("at"),
        )
    elif atype == "list_timers":
        result = list_timers(profile_id=profile_id)
    elif atype == "cancel_timer":
        result = cancel_timer(profile_id=profile_id, which=params.get("which"))
    elif atype == "play_song":
        result = play_song(params.get("song_name", ""), profile_id=profile_id)
    elif atype == "play_radio":
        result = play_radio(params.get("station_name", ""), profile_id=profile_id)
    elif atype == "stop_media":
        result = stop_media(profile_id=profile_id)
        stop_requested = True
    else:
        return None

    detail = result.detail or {}
    if atype == "play_radio" and result.success:
        media_url = detail.get("stream_url")
        mark_media_playing(profile_id, detail.get("station_name") or "radio")
    elif atype == "play_song" and result.success:
        # A named song can resolve to a local file (audio_url) or, when the
        # folder has no match, to a radio stream (stream_url) — a generic
        # "play some music" does exactly that.
        media_url = detail.get("audio_url") or detail.get("stream_url")
        mark_media_playing(
            profile_id,
            detail.get("title") or detail.get("station_name") or "music",
        )

    text = result.result_text
    guardrail = check_guardrails(text, s.profile, injected_facts=[text])
    if not guardrail.passed:
        # Never speak a reply the guardrail rejects — say something neutral
        # that is still true.
        text = "Alright — that's done." if result.success else "I couldn't do that one."
        s.event_log.log_fallback(
            FallbackType.GUARDRAIL,
            detail={"violations": guardrail.violations, "action": atype},
        )

    # Dashboard record. `raw_text` is the person's exact words and is shown to
    # the caregiver only — it is never spoken back.
    try:
        s.event_log.log_action(
            # `result.action_type` is the action that actually ran, which is
            # not always the one detected: "play some music" with an empty
            # folder falls back to the radio.
            action_type=result.action_type or atype,
            success=result.success,
            spoken=text,
            raw_text=user_message if atype in ("set_timer", "set_reminder") else "",
            detail={k: detail[k] for k in (
                "id", "label", "seconds", "fire_at", "station_name", "title",
                "artist", "medication", "needs", "preview", "source",
            ) if k in detail},
        )
    except Exception:
        pass

    s.event_log.log_conversation_turn(speaker="user", text=user_message, emotion="neutral")
    s.event_log.log_conversation_turn(speaker="sahaara", text=text, emotion="neutral")

    return {
        "response": text,
        # Top-level so both callers can act on it without digging into meta:
        # the browser (text chat) plays it, and the voice pipeline forwards it
        # as a play_stream/stop_media event.
        "radio_stream_url": media_url if atype == "play_radio" else None,
        "audio_url": media_url if atype == "play_song" else None,
        "stop_media": stop_requested,
        "meta": {
            "emotion": "neutral", "is_repeated_question": False,
            "sustained_distress": False, "memory_box_offered": None,
            "guardrail_fallback_used": not guardrail.passed,
            "guardrail_violations": [] if guardrail.passed else guardrail.violations,
            "structure_check_passed": True, "model": "deterministic",
            "tier": current_tier, "consent_state": s.consent_manager.state.value,
            "continuation_state": s.continuation_manager.state.value,
            "radio_stream_url": media_url,
            "audio_url": media_url,
            "stop_media": stop_requested,
            "action": {
                "type": atype,
                "success": result.success,
                "needs": detail.get("needs"),
            },
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


def _handle_continuation(
    s, user_message: str, intent, current_tier: str
) -> dict | None:
    """
    Handle a pending continuation offer ("Would you like me to go on?").

    Safety ordering: this runs AFTER danger/physical-urgent classification,
    so a danger statement here already went to Red before we get here.

    Returns a response dict if the continuation is resolved (yes/no/timeout),
    or None if still pending (re-ask) or not applicable.
    """
    if s.continuation_manager.state != ContinuationState.PENDING:
        return None

    cont_result = s.continuation_manager.interpret_response(user_message)

    if cont_result == ContinuationState.IDLE:
        # User said yes/no or timed out
        remaining = s.continuation_manager.get_remaining_text()
        if remaining:
            # Yes — deliver the remaining chunk through guardrails
            guardrail = check_guardrails(remaining, s.profile)
            if guardrail.passed:
                response_text = remaining
                used_fallback = False
            else:
                response_text = SAFE_FALLBACK_RESPONSE
                used_fallback = True

            # Log both sides of the continuation
            s.event_log.log_conversation_turn(
                speaker="user", text=user_message, emotion=intent.emotion
            )
            s.event_log.log_conversation_turn(
                speaker="sahaara", text=response_text, emotion=intent.emotion,
                context_trace={"continuation": True, "continuation_outcome": "continued"},
            )

            return {
                "response": response_text,
                "meta": {
                    "emotion": "neutral", "is_repeated_question": False,
                    "sustained_distress": False,
                    "continuation_outcome": "continued",
                    "contact_called": None,
                    "tier": current_tier,
                },
            }
        # No remaining text (shouldn't happen, but fall through safely)
        return None

    if cont_result == ContinuationState.PENDING:
        # Unclear / topic shift → re-ask the check-in
        return {
            "response": "Would you like me to go on?",
            "meta": {
                "emotion": "neutral", "is_repeated_question": False,
                "sustained_distress": False,
                "continuation_pending": True,
                "contact_called": None,
                "tier": current_tier,
            },
        }

    # IDLE (timeout/no) with no remaining text → fall through to normal processing
    return None


def _truncate_for_voice(response_text: str, max_sentences: int = 4) -> tuple[str, str | None]:
    """
    Split a long response into a short lead (max_sentences) + remaining text.
    Returns (lead, remaining). If remaining is None, the text is short enough.
    """
    # Split into sentences (simple regex on sentence-ending punctuation)
    sentences = re.split(r'(?<=[.!?])\s+', response_text.strip())
    if len(sentences) <= max_sentences:
        return response_text, None

    lead = " ".join(sentences[:max_sentences]).strip()
    remaining = " ".join(sentences[max_sentences:]).strip()
    return lead, remaining


def reset_pipeline() -> None:
    """Reset all pipeline state for a clean scenario run."""
    s = _get_session()
    _session_manager.reset(s.profile_id)


# ---- Module-level accessors for main.py compatibility ----

def get_session_manager():
    return _session_manager

# Module-level exports — set to None, real values resolved via _get_session()
event_log = None
session_notes = None
escalation_engine = None
consent_manager = None
memory_box = None
alert_store = None
alert_builder = None
suggestion_queue = None
profile = None