"""
Tests for the radio action (Task 5).

Tests that:
- play_radio returns success when a valid subscription is found
- play_radio returns failure for unknown stations
- stop_media always succeeds
- Detection patterns work for radio-related phrases
"""

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from actions import detect_action, play_radio, stop_media

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
# Test 1: Detection patterns
# ============================================================
print("=== Test 1: Detection patterns ===")

detect_tests = [
    ("play radio", "play_radio", None),
    ("play radio All India Radio Pune", "play_radio", "all india radio pune"),
    ("play radio BBC World Service", "play_radio", "bbc world service"),
    ("play radio Radio Mirchi Pune", "play_radio", "radio mirchi pune"),
    ("start radio BBC World Service", "play_radio", "bbc world service"),
    ("stop radio", "stop_media", None),
    ("stop music", "stop_media", None),
    ("stop playing", "stop_media", None),
    ("stop radio for dinner", None, None),  # Should NOT match - "for dinner" not in pattern
    ("stop music playing", None, None),  # Should NOT match - "playing" at end not matched by pattern
    ("hello how are you", None, None),
    ("what time is it", "tell_time", None),  # Correctly matches tell_time, not play_radio
]

for text, expected_action, expected_label in detect_tests:
    result = detect_action(text)
    detected_action = result["action_type"] if result else None
    detected_label = result["params"].get("station_name") if result else None
    check(
        f"detect '{text}' -> {expected_action}",
        detected_action == expected_action,
        f"got {detected_action}",
    )
    if expected_action == "play_radio" and expected_label:
        check(f"  station={expected_label}", detected_label == expected_label.lower(), f"got {detected_label}")


# ============================================================
# Test 2: play_radio with valid station
# ============================================================
print("\n=== Test 2: play_radio with valid station ===")

# Create a mock scheduler with profile
class FakeProfile:
    profile_id = "asha_demo_001"


class FakeScheduler:
    profile = FakeProfile()


result = play_radio("All India Radio Pune", scheduler=FakeScheduler())
check("play_radio success", result.success, f"got {result}")
check("play_radio result_text", "I'll play All India Radio Pune for you." in result.result_text, f"got {result.result_text}")
check("play_radio has stream_url", "stream_url" in result.detail, f"got {result.detail}")
if result.success:
    check("stream_url is valid URL", result.detail["stream_url"].startswith("http"), f"got {result.detail.get('stream_url')}")
    check("stream_url contains air.pc", "air.pc.cdn.bitgravity.com" in result.detail["stream_url"], f"got {result.detail.get('stream_url')}")


# ============================================================
# Test 3: play_radio with unknown station
# ============================================================
print("\n=== Test 3: play_radio with unknown station ===")

result = play_radio("Non-existent Radio", scheduler=FakeScheduler())
check("play_radio unknown station fails", not result.success, f"got {result}")
check("play_radio unknown message", "I don't have that station saved" in result.result_text, f"got {result.result_text}")


# ============================================================
# Test 4: stop_media
# ============================================================
print("\n=== Test 4: stop_media ===")

result = stop_media()
check("stop_media returns success", result.success, f"got {result}")
check("stop_media result_text", "I've stopped the audio." in result.result_text, f"got {result.result_text}")


# ============================================================
# Summary
# ============================================================
print(f"\n=== Results: {passed} passed, {failed} failed ===")
if failed:
    sys.exit(1)
