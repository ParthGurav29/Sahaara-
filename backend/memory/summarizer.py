"""
memory/summarizer.py

Generates brief session summaries from the event log.
Used to provide "yesterday" context in the next session's prompt.
Summaries are 2-3 sentences — never full conversation replays.
"""

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

SUMMARY_PATH = Path(__file__).parent / "data" / "session_summaries.json"


def load_recent_summaries(path: Path = SUMMARY_PATH, limit: int = 3) -> list[dict]:
    """Load the most recent session summaries."""
    if not path.exists():
        return []
    try:
        with open(path) as f:
            summaries = json.load(f)
    except (json.JSONDecodeError, FileNotFoundError):
        return []
    return summaries[-limit:]


def save_summary(summary: dict, path: Path = SUMMARY_PATH) -> None:
    """Append a session summary to the summaries file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    summaries = []
    if path.exists():
        try:
            with open(path) as f:
                summaries = json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            summaries = []
    summaries.append(summary)
    with open(path, "w") as f:
        json.dump(summaries, f, indent=2)


def generate_summary_from_events(events: list, profile_name: str = "Asha") -> dict:
    """Generate a session summary from the event log.
    
    Uses simple heuristics for the hackathon — swap in an LLM call
    for richer summaries when latency budget allows.
    """
    if not events:
        return _empty_summary()
    
    # Extract key signals from events
    turn_count = 0
    user_messages: list[str] = []
    emotions: list[str] = []
    themes: set[str] = set()
    highest_tier = "green"
    memory_box_plays = 0
    tier_changes = 0
    consent_asks = 0
    distress_count = 0
    repeat_count = 0
    
    for event in events:
        detail = getattr(event, "detail", {})
        event_type = getattr(event, "type", None)
        type_val = event_type.value if hasattr(event_type, "value") else str(event_type)
        
        if type_val == "conversation_turn":
            turn_count += 1
            if detail.get("speaker") == "user":
                text = detail.get("text", "")
                user_messages.append(text)
                emotion = detail.get("emotion", "neutral")
                emotions.append(emotion)
                # Detect themes from user messages
                for keyword, theme in _THEME_KEYWORDS:
                    if keyword in text.lower():
                        themes.add(theme)
        
        elif type_val == "memory_box_played":
            memory_box_plays += 1
            themes.add("memory box")
        
        elif type_val == "tier_change":
            tier_changes += 1
            new_tier = detail.get("new_tier", "green")
            if _tier_rank(new_tier) > _tier_rank(highest_tier):
                highest_tier = new_tier
        
        elif type_val == "consent_asked":
            consent_asks += 1
        
        elif type_val == "distress":
            distress_count += 1
        
        elif type_val == "repeat":
            repeat_count += 1
    
    # Build summary text
    summary_parts = []
    
    # Dominant emotion
    if emotions:
        dominant = max(set(emotions), key=emotions.count) if emotions else "neutral"
        if dominant == "anxious":
            summary_parts.append(f"{profile_name} seemed anxious or worried")
        elif dominant == "distressed":
            summary_parts.append(f"{profile_name} was distressed")
        elif dominant == "sad":
            summary_parts.append(f"{profile_name} was feeling sad")
        elif dominant == "content":
            summary_parts.append(f"{profile_name} was calm and content")
        else:
            summary_parts.append(f"{profile_name} had a calm session")
    else:
        summary_parts.append(f"{profile_name} had a brief session")
    
    # Themes (who she asked about)
    if themes:
        theme_list = sorted(themes)
        if len(theme_list) <= 3:
            summary_parts.append(f"she talked about {', '.join(theme_list)}")
        else:
            summary_parts.append(f"she talked about {', '.join(theme_list[:3])} and more")
    
    # Notable events
    if repeat_count >= 2:
        summary_parts.append(f"she repeated questions {repeat_count} times")
    if tier_changes > 0:
        summary_parts.append(f"escalation reached {highest_tier}")
    if memory_box_plays > 0:
        summary_parts.append(f"listened to Memory Box clip{'s' if memory_box_plays > 1 else ''}")
    
    # Tier summary
    tier_label = _tier_label(highest_tier)
    
    summary_text = ". ".join(summary_parts) + "."
    
    return {
        "session_id": f"session_{int(time.time())}",
        "date": datetime.now().isoformat(),
        "summary_text": summary_text,
        "key_themes": sorted(themes),
        "emotion_summary": max(set(emotions), key=emotions.count) if emotions else "neutral",
        "turn_count": turn_count,
        "highest_tier": highest_tier,
        "tier_label": tier_label,
        "memory_box_plays": memory_box_plays,
        "repeat_count": repeat_count,
        "consent_asks": consent_asks,
    }


def build_session_context_block(summaries: list[dict], profile_name: str = "Asha") -> str:
    """Build the prompt block that injects previous session context.
    Returns empty string if no summaries available.
    """
    if not summaries:
        return ""
    
    lines = ["PREVIOUS SESSION CONTEXT — what happened recently, for awareness:"]
    for i, s in enumerate(summaries):
        date = s.get("date", "unknown date")[:10]
        text = s.get("summary_text", "")
        # Format: "Oct 19: Asha seemed anxious. she talked about priya, memory box. ..."
        lines.append(f"- {date}: {text}")
    
    lines.append("Use this context gently. Do not announce it or say 'I remember from yesterday.' "
                  "If it helps you understand her emotional state, let it inform your warmth. "
                  "Never mention the summary directly.")
    
    return "\n".join(lines)


# ---- Internal helpers ----

_THEME_KEYWORDS: list[tuple[str, str]] = [
    ("priya", "priya"),
    ("daughter", "her daughter"),
    ("rohan", "rohan"),
    ("son", "her son"),
    ("vikram", "vikram"),
    ("husband", "her husband"),
    ("garden", "her garden"),
    ("chai", "chai"),
    ("tea", "tea"),
    ("music", "music"),
    ("song", "music"),
    ("students", "teaching"),
    ("teaching", "teaching"),
    ("school", "teaching"),
    ("pune", "home"),
    ("walk", "walks"),
    ("window", "the window"),
]


def _tier_rank(tier: str) -> int:
    return {"green": 0, "yellow": 1, "orange": 2, "red": 3}.get(tier, 0)


def _tier_label(tier: str) -> str:
    return {"green": "calm (green)", "yellow": "elevated (yellow)", 
            "orange": "concerning (orange)", "red": "urgent (red)"}.get(tier, tier)


def _empty_summary() -> dict:
    return {
        "session_id": f"session_{int(time.time())}",
        "date": datetime.now().isoformat(),
        "summary_text": "Brief session with no notable events.",
        "key_themes": [],
        "emotion_summary": "neutral",
        "turn_count": 0,
        "highest_tier": "green",
        "tier_label": "calm (green)",
        "memory_box_plays": 0,
        "repeat_count": 0,
        "consent_asks": 0,
    }