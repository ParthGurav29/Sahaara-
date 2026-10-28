"""
Tests for the weather action (M5 Step 2).

Tests that:
- Weather fetch succeeds with real data
- API timeout is handled gracefully
- API error is handled gracefully
- Weather claim with no action result is blocked by guardrails
- Danger phrase containing "weather" still goes Red (physical distress takes priority)
"""

import sys
import time
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import database
from actions import detect_action, get_weather
from safety.guardrails import check_guardrails
from safety.escalation import EscalationEngine, EscalationConfig, EscalationTier
from events.event_log import EventLog
from safety.consent import ConsentManager
from memory.session_notes import SessionNotes
from memory.profile import Profile
from llm.intent_emotion import classify_intent

# ---- helpers ----

def make_profile() -> Profile:
    data = {
        "profile_id": "asha_demo_001",
        "name": {"full_name": "Asha Kulkarni", "preferred_address": "Asha"},
        "family": [
            {"name": "Priya", "relationship": "daughter"},
            {"name": "Rohan", "relationship": "son"},
        ],
        "life_story": {"hometown": "Pune, Maharashtra", "work": "teacher"},
        "likes": {"music": [], "food": [], "topics": [], "routines": []},
        "avoid": {"topics": []},
        "daily_routine": {"wake_time": "07:00", "meals": {}, "medication_times": []},
    }
    return Profile(data)


def make_session() -> SimpleNamespace:
    """Build a minimal session-like object for process_turn testing."""
    profile = make_profile()
    event_log = EventLog(profile_id="asha_demo_001", demo_mode=True)
    session_notes = SessionNotes(profile_id="asha_demo_001")
    escalation_engine = EscalationEngine(event_log, session_notes, EscalationConfig(demo_mode=True, demo_multiplier=300.0))
    consent_manager = ConsentManager(event_log=event_log, timeout_seconds=30.0, demo_mode=True, demo_multiplier=300.0)
    memory_box = SimpleNamespace(match=lambda text: None, record_turn=lambda: None)
    alert_store = SimpleNamespace(add=lambda alert: None)
    alert_builder = SimpleNamespace(
        build_tier_change_alert=lambda tier_from, tier_to, reason, context: SimpleNamespace(alert_id="alert_1"),
        build_consent_alert=lambda outcome, contact_name, tier: SimpleNamespace(alert_id="alert_1"),
        build_physical_symptom_alert=lambda phrase, matched_pattern, urgency, tier_escalated_to: SimpleNamespace(alert_id="alert_1"),
    )
    return SimpleNamespace(
        profile=profile,
        event_log=event_log,
        session_notes=session_notes,
        escalation_engine=escalation_engine,
        consent_manager=consent_manager,
        memory_box=memory_box,
        alert_store=alert_store,
        alert_builder=alert_builder,
    )


passed = 0
failed = 0


def check(name: str, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  PASS: {name}")
    else:
        failed += 1
        print(f"  FAIL: {name} {detail}")


# ============================================================
# Test 1: Weather detection
# ============================================================
print("=== Test 1: Weather detection patterns ===")

tests = [
    ("how is the weather", True),
    ("what's the weather like", True),
    ("will it rain today", True),
    ("will it snow tomorrow", True),
    ("is it hot today", True),
    ("is it cold outside", True),
    ("hello how are you", False),
    ("remind me to call Rohan", False),
]

for text, expected in tests:
    result = detect_action(text)
    detected = result is not None and result["action_type"] == "get_weather"
    check(f"detect weather: '{text}'", detected == expected, f"got {result}")

# ============================================================
# Test 2: Successful weather fetch
# ============================================================
print("\n=== Test 2: Successful weather fetch ===")

fake_weather_data = {
    "current_weather": {
        "temperature": 24.5,
        "weathercode": 0,
    },
    "daily": {
        "temperature_2m_max": [28.0],
        "temperature_2m_min": [18.0],
    },
}

def fake_urlopen(*args, **kwargs):
    class FakeResponse:
        def __init__(self, data):
            self._data = json.dumps(data).encode()
        def read(self):
            return self._data
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass

    return FakeResponse(fake_weather_data)

import json
from unittest.mock import patch

with patch("actions.urlopen", side_effect=fake_urlopen):
    result = get_weather(18.5204, 73.8567)

check("weather success", result.success, f"got {result}")
check("weather has temperature", result.detail.get("temperature") == 24.5, f"got {result.detail}")
check("weather has condition", result.detail.get("condition") == "clear", f"got {result.detail}")
check("weather has high/low", result.detail.get("high") == 28.0 and result.detail.get("low") == 18.0, f"got {result.detail}")
check("weather result text", "24.5" in result.result_text and "clear" in result.result_text.lower(), f"got {result.result_text}")

# ============================================================
# Test 3: API timeout
# ============================================================
print("\n=== Test 3: API timeout ===")

def fake_timeout(*args, **kwargs):
    import socket
    raise socket.timeout("timed out")

with patch("actions.urlopen", side_effect=fake_timeout):
    result = get_weather(18.5204, 73.8567)

check("weather timeout returns failure", not result.success, f"got {result}")
check("weather timeout message", "can't check" in result.result_text.lower(), f"got {result.result_text}")

# ============================================================
# Test 4: API error (non-200)
# ============================================================
print("\n=== Test 4: API error ===")

def fake_error(*args, **kwargs):
    import urllib.error
    raise urllib.error.HTTPError("http://example.com", 500, "Internal Server Error", {}, None)

with patch("actions.urlopen", side_effect=fake_error):
    result = get_weather(18.5204, 73.8567)

check("weather error returns failure", not result.success, f"got {result}")
check("weather error message", "can't check" in result.result_text.lower(), f"got {result.result_text}")

# ============================================================
# Test 5: Weather claim with no action result -> blocked by guardrails
# ============================================================
print("\n=== Test 5: Weather claim without action result is blocked ===")

profile = make_profile()

# A weather claim without any action result should be blocked
result = check_guardrails("It's a lovely sunny day today.", profile)
check("weather claim blocked without action", not result.passed, f"got {result.violations}")
check("weather violation detected", any("fabricated_weather" in v for v in result.violations), f"got {result.violations}")

# ============================================================
# Test 6: Weather claim WITH action result -> allowed
# ============================================================
print("\n=== Test 6: Weather claim with action result is allowed ===")

injected_fact = "It's 24°C and sunny in Pune right now."
result = check_guardrails("It's 24°C and sunny today.", profile, injected_facts=[injected_fact])
check("weather claim allowed with action result", result.passed, f"got {result.violations}")

# ============================================================
# Test 7: Danger phrase containing "weather" still goes Red
# ============================================================
print("\n=== Test 7: Danger phrase with weather word still goes Red ===")

s = make_session()
intent = classify_intent("I can't breathe and the weather is bad")
check("physical urgent detected with weather word", intent.is_physical_urgent, f"got {intent}")

intent2 = classify_intent("I want to die and the weather is nice")
check("danger statement still detected with weather word", intent2.is_danger_statement, f"got {intent2}")

# ============================================================
# Summary
# ============================================================
print(f"\n=== Results: {passed} passed, {failed} failed ===")
if failed:
    sys.exit(1)
