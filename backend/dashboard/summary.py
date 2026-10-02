"""
dashboard/summary.py

Generate daily summary from the real event log.
Frames the "3 repeated questions → 1 quiet summary line" contrast
as a design illustration of noise reduction.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from events.event_log import EventLog, EventType, EscalationTier
from memory.profile import Profile
from safety.guardrails import check_guardrails, SAFE_FALLBACK_RESPONSE
from llm.provider import get_provider, ProviderError


@dataclass
class DailySummary:
    """Structured daily summary for the caregiver dashboard."""
    profile_id: str
    date: str  # ISO date string
    total_turns: int
    repeat_count: int
    distress_episodes: int
    tier_changes: int
    highest_tier: EscalationTier
    consent_requests: int
    consent_accepted: int
    consent_declined: int
    consent_timeout: int
    fallbacks_triggered: int
    summary_text: str  # Human-readable summary
    noise_reduction_note: str  # The "3 repeated questions → 1 summary line" framing


def _format_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{int(seconds)}s"
    mins = int(seconds / 60)
    secs = int(seconds % 60)
    return f"{mins}m {secs}s"


def _build_raw_summary(event_log: EventLog, profile: Profile) -> DailySummary:
    """Build summary from raw event log data."""
    events = event_log.get_all()
    
    # Count events by type
    total_turns = len([e for e in events if e.type in (EventType.REPEAT, EventType.DISTRESS, EventType.TIER_CHANGE)])
    repeat_events = [e for e in events if e.type == EventType.REPEAT]
    distress_events = [e for e in events if e.type == EventType.DISTRESS]
    tier_change_events = [e for e in events if e.type == EventType.TIER_CHANGE]
    consent_asked = [e for e in events if e.type == EventType.CONSENT_ASKED]
    consent_answered = [e for e in events if e.type == EventType.CONSENT_ANSWERED]
    fallback_events = [e for e in events if e.type == EventType.FALLBACK]
    
    # Repeat count (max count across all repeated questions)
    repeat_count = max([e.detail.get("count", 0) for e in repeat_events], default=0)
    
    # Distress episodes (count of sustained distress events)
    distress_episodes = len([e for e in distress_events if e.detail.get("sustained", False)])
    
    # Tier changes
    tier_changes = len(tier_change_events)
    
    # Highest tier reached
    tiers = [e.detail.get("new_tier", "green") for e in tier_change_events]
    tier_order = {"green": 0, "yellow": 1, "orange": 2, "red": 3}
    highest_tier = max(tiers, key=lambda t: tier_order.get(t, 0)) if tiers else "green"
    
    # Consent stats
    consent_requests = len(consent_asked)
    consent_outcomes = [e.detail.get("outcome", "") for e in consent_answered]
    consent_accepted = consent_outcomes.count("accepted")
    consent_declined = consent_outcomes.count("declined")
    consent_timeout = consent_outcomes.count("timeout")
    
    # Fallbacks
    fallbacks_triggered = len(fallback_events)
    
    # Build human-readable summary
    parts = []
    
    if repeat_count >= 3:
        parts.append(f"{profile.preferred_address} repeated a question {repeat_count} times")
    
    if distress_episodes > 0:
        parts.append(f"showed sustained distress {distress_episodes} time{'s' if distress_episodes > 1 else ''}")
    
    if tier_changes > 0:
        parts.append(f"escalation tier reached {highest_tier.upper()}")
    
    if consent_requests > 0:
        parts.append(f"consent was asked {consent_requests} time{'s' if consent_requests > 1 else ''} ({consent_accepted} accepted, {consent_declined} declined, {consent_timeout} timeout)")
    
    if fallbacks_triggered > 0:
        parts.append(f"{fallbacks_triggered} system fallback{'s' if fallbacks_triggered > 1 else ''} triggered")
    
    summary_text = ". ".join(parts) + "." if parts else f"{profile.preferred_address} had a calm session with no escalations."
    
    # Noise reduction framing
    noise_reduction_note = (
        f"DESIGN ILLUSTRATION: {repeat_count} repeated questions → 1 quiet summary line. "
        f"This demonstrates noise reduction — not a measured clinical result."
    )
    
    return DailySummary(
        profile_id=event_log.profile_id,
        date=datetime.now().date().isoformat(),
        total_turns=total_turns,
        repeat_count=repeat_count,
        distress_episodes=distress_episodes,
        tier_changes=tier_changes,
        highest_tier=EscalationTier(highest_tier),
        consent_requests=consent_requests,
        consent_accepted=consent_accepted,
        consent_declined=consent_declined,
        consent_timeout=consent_timeout,
        fallbacks_triggered=fallbacks_triggered,
        summary_text=summary_text,
        noise_reduction_note=noise_reduction_note,
    )


async def _enhance_with_llm(summary: DailySummary, profile: Profile) -> DailySummary:
    """Optionally enhance summary wording with LLM (off critical path)."""
    try:
        provider = get_provider()
        if not provider:
            return summary
        
        prompt = f"""
You are writing a brief, warm daily summary for a family caregiver.
The person is {profile.preferred_address} ({profile.profile_id}).
Raw facts: {summary.summary_text}
Noise reduction framing: {summary.noise_reduction_note}

Write a 2-3 sentence summary that:
- Uses warm, non-clinical language
- Avoids diagnostic claims (no "decline", "dementia symptoms", etc.)
- Mentions the noise reduction framing naturally
- Passes safety guardrails

Return ONLY the enhanced summary text.
"""
        from llm.response_policy import build_prompt
        from memory.profile import load_profile
        from llm.intent_emotion import classify_intent
        from memory.memory_box import MemoryBox
        
        bundle = build_prompt(
            profile,
            prompt,
            classify_intent("summarize"),
            memory_box_match=None,
            is_repeated_question=False,
            sustained_distress=False,
            escalation_tier="green",
        )
        
        result = provider.complete_from_prompt_bundle(bundle)
        enhanced = result.text.strip()
        
        # Guardrail check
        guardrail = check_guardrails(enhanced, profile)
        if guardrail.passed:
            summary.summary_text = enhanced
        else:
            summary.summary_text = SAFE_FALLBACK_RESPONSE
            
    except Exception:
        # Silently fall back to raw summary
        pass
    
    return summary


def generate_daily_summary(
    event_log: EventLog,
    profile: Profile,
    use_llm: bool = False,
) -> DailySummary:
    """
    Generate a daily summary from the event log.
    
    Args:
        event_log: The session's event log
        profile: The user profile
        use_llm: If True, enhance wording with LLM (off critical path, with guardrails)
    
    Returns:
        DailySummary with structured data and human-readable text
    """
    summary = _build_raw_summary(event_log, profile)
    
    if use_llm:
        # Run LLM enhancement off the critical path (fire and forget style)
        # In production, this would be a background task
        import asyncio
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # Schedule as background task
                loop.create_task(_enhance_with_llm(summary, profile))
            else:
                asyncio.run(_enhance_with_llm(summary, profile))
        except Exception:
            pass
    
    return summary


if __name__ == "__main__":
    # Quick test
    from memory.profile import load_profile
    from events.event_log import EventLog, EventType, EscalationTier
    from pathlib import Path
    from dotenv import load_dotenv
    
    load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")
    profile = load_profile()
    
    # Create test event log
    event_log = EventLog(profile_id=profile.profile_id, demo_mode=True)
    
    # Simulate events
    event_log.log_repeat("Where is my daughter?", 3, True)
    event_log.log_distress(sustained=True, emotion="anxious", duration_seconds=120)
    event_log.log_tier_change(EscalationTier.GREEN, EscalationTier.YELLOW, "repeat_threshold", {})
    event_log.log_tier_change(EscalationTier.YELLOW, EscalationTier.ORANGE, "distress_persistence", {})
    event_log.log_consent_asked("Call Priya?", "Priya", EscalationTier.ORANGE)
    event_log.log_consent_answered("accepted", "Priya")
    event_log.log_fallback("guardrail", {"violations": ["diagnosis"]})
    
    summary = generate_daily_summary(event_log, profile)
    print(f"Summary: {summary.summary_text}")
    print(f"Noise reduction: {summary.noise_reduction_note}")
    print(f"Highest tier: {summary.highest_tier.value}")