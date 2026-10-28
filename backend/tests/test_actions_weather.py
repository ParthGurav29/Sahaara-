"""
Tests for the weather action.

Owner decision (2026-10-28): weather is dropped. Sahaara must not offer or
answer weather through the action layer until a real source is explicitly
approved, so `detect_action()` never returns `get_weather`.

`get_weather()` itself is kept (it talks to Open-Meteo and is fully tested
below) but nothing calls it — wiring it back is a one-line change in
`detect_action()`.
"""

import json
import socket
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import actions  # noqa: E402
from actions import detect_action, get_weather  # noqa: E402

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
# Test 1: Weather is not a trigger any more
# ============================================================
print("=== Test 1: Weather is not wired to any trigger ===")

weather_phrases = [
    "how is the weather",
    "what's the weather like",
    "will it rain today",
    "will it snow tomorrow",
    "is it hot today",
    "is it cold outside",
]

for text in weather_phrases:
    result = detect_action(text)
    detected = result is not None and result["action_type"] == "get_weather"
    check(f"'{text}' does not trigger weather", detected is False, f"got {result}")

# ...and the phrases it must not hijack still work.
check(
    "time still detected",
    (detect_action("what time is it") or {}).get("action_type") == "tell_time",
)

# ============================================================
# Test 2: The dormant helper still works against its real source
# ============================================================
print("\n=== Test 2: get_weather (dormant helper) ===")

fake_weather_data = {
    "current_weather": {"temperature": 24.5, "weathercode": 0},
    "daily": {"temperature_2m_max": [28.0], "temperature_2m_min": [18.0]},
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


with patch("actions.urlopen", side_effect=fake_urlopen):
    result = get_weather(18.5204, 73.8567)

check("weather success", result.success, f"got {result}")
check("weather has temperature", result.detail.get("temperature") == 24.5, f"got {result.detail}")
check("weather has condition", result.detail.get("condition") == "clear", f"got {result.detail}")
check(
    "weather result text names the temperature and condition",
    "24.5" in result.result_text and "clear" in result.result_text.lower(),
    f"got {result.result_text}",
)

# ============================================================
# Test 3: Timeout is honest
# ============================================================
print("\n=== Test 3: Timeout ===")


def fake_timeout(*args, **kwargs):
    raise socket.timeout("timed out")


with patch("actions.urlopen", side_effect=fake_timeout):
    result = get_weather(18.5204, 73.8567)

check("timeout -> success=False", result.success is False, f"got {result}")
check("timeout says it cannot check", "can't check" in result.result_text.lower(), result.result_text)

# ============================================================
# Test 4: Source error is honest
# ============================================================
print("\n=== Test 4: Source error ===")


def fake_error(*args, **kwargs):
    raise OSError("connection refused")


with patch("actions.urlopen", side_effect=fake_error):
    result = get_weather(18.5204, 73.8567)

check("error -> success=False", result.success is False, f"got {result}")
check("error is never a fabricated answer", "24.5" not in result.result_text, result.result_text)

# ============================================================
# Test 5: Guardrails still catch a fabricated weather claim
# ============================================================
print("\n=== Test 5: Guardrail still blocks fabricated weather ===")

from memory.profile import load_profile  # noqa: E402
from safety.guardrails import check_guardrails  # noqa: E402

profile = load_profile()
guard = check_guardrails("It's a lovely sunny day today.", profile)
check("fabricated weather is caught", guard.passed is False, f"got {guard.violations}")

# Known gap, recorded rather than papered over: the weather patterns only cover
# specific phrasings ("lovely sunny day", "it's a sunny day"), so a claim like
# "it's a beautiful sunny day outside, about 28 degrees" passes. guardrails.py
# is a safety file and is not changed without owner approval.
loose = check_guardrails("It's a beautiful sunny day outside, about 28 degrees.", profile)
print(f"  NOTE: loose weather phrasing caught = {not loose.passed} (documented gap)")

print(f"\n  RESULT: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
