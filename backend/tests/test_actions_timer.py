"""
Tests for the timer action (Task 3).

Tests that:
- set_timer detection works for various phrasings
- set_timer action succeeds with valid input
- set_timer fails gracefully without scheduler
- timer duration parsing works for spoken durations
- Timer action returns proper result text
"""

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from actions import detect_action, set_timer, parse_duration

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
# Test 1: Duration parsing
# ============================================================
print("=== Test 1: Duration parsing ===")

duration_tests = [
    ("10 minutes", 10),
    ("5 min", 5),
    ("half an hour", 30),
    ("half hour", 30),
    ("an hour", 60),
    ("one hour", 60),
    ("ten minutes", 10),
    ("twenty minutes", 20),
    ("thirty minutes", 30),
    ("sixty minutes", 60),
    ("forty-five minutes", None),  # Hyphenated numbers not required
]

for text, expected in duration_tests:
    result = parse_duration(text)
    check(f"parse '{text}' -> {expected}", result == expected, f"got {result}")

# ============================================================
# Test 2: Detection patterns
# ============================================================
print("\n=== Test 2: Detection patterns ===")

detect_tests = [
    ("set a timer for 10 minutes", "set_timer", 10),
    ("set timer for 5 min", "set_timer", 5),
    ("timer for 30 minutes called dinner", "set_timer", 30),
    ("timer for half an hour", "set_timer", 30),
    ("set a timer for ten minutes", "set_timer", 10),
    ("set a timer for twenty minutes", "set_timer", 20),
    ("set timer for an hour", "set_timer", 60),
    ("timer for one hour", "set_timer", 60),
    ("set timer for ten minutes called pasta", "set_timer", 10),
    ("I want to set a timer for 5 minutes", "set_timer", 5),
    ("set a timer for twenty minutes called yoga", "set_timer", 20),
    ("hello how are you", None, None),
    ("what time is it", "tell_time", None),
    ("play some music", None, None),
]

for text, expected_action, expected_minutes in detect_tests:
    result = detect_action(text)
    detected_action = result["action_type"] if result else None
    detected_minutes = (
        result["params"]["minutes"] if result and result["action_type"] == "set_timer" else None
    )
    check(
        f"detect '{text}' -> {expected_action}",
        detected_action == expected_action,
        f"got {detected_action}",
    )
    if expected_action == "set_timer":
        check(f"  minutes={expected_minutes}", detected_minutes == expected_minutes, f"got {detected_minutes}")

# ============================================================
# Test 3: set_timer action (no scheduler)
# ============================================================
print("\n=== Test 3: set_timer without scheduler ===")

result = set_timer(10, "pasta")
check("set_timer without scheduler returns failure", not result.success, f"got {result}")
check("set_timer failure message", "can't set timers" in result.result_text.lower(), f"got {result.result_text}")

# ============================================================
# Test 4: set_timer action with scheduler (mock)
# ============================================================
print("\n=== Test 4: set_timer with scheduler ===")

class FakeScheduler:
    def __init__(self):
        self.added_reminders = []

    def add_one_time_reminder(self, text, fire_at):
        self.added_reminders.append((text, fire_at))


scheduler = FakeScheduler()
result = set_timer(5, "pasta", scheduler=scheduler)
check("set_timer with scheduler returns success", result.success, f"got {result}")
check("set_timer result text", "5 minutes" in result.result_text or "in 5 minutes" in result.result_text, f"got {result.result_text}")
check("scheduler.add_one_time_reminder called", len(scheduler.added_reminders) == 1, f"got {len(scheduler.added_reminders)} calls")
check("scheduler text contains 'Timer: pasta'", "Timer: pasta" in scheduler.added_reminders[0][0], f"got {scheduler.added_reminders[0][0]}")

# ============================================================
# Summary
# ============================================================
print(f"\n=== Results: {passed} passed, {failed} failed ===")
if failed:
    sys.exit(1)
