"""
Tests for timer cancel/list functionality (Task 4).

Tests that:
- list_timers returns pending timers for a profile
- cancel_timer cancels an active timer
- cancel_timer fails for non-existent timer
- detect_action recognizes cancel timer patterns
- Timer cancellation removes from database
- Multiple timers can be listed
"""

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import database
from actions import detect_action, list_timers, cancel_timer

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
print("=== Test 1: Detect cancel/list patterns ===")

detect_tests = [
    ("cancel timer", "cancel_timer", None),
    ("cancel timer for pasta", "cancel_timer", "pasta"),
    ("timer for 30 minutes called dinner", "set_timer", 30),  # not cancel
    ("list my timers", "list_timers", None),
    ("what timers do i have", "list_timers", None),
    ("cancel timer for dinner", "cancel_timer", "dinner"),
    ("hello how are you", None, None),
]

for text, expected_action, expected_label in detect_tests:
    result = detect_action(text)
    detected_action = result["action_type"] if result else None
    detected_label = result["params"].get("label") if result else None
    check(
        f"detect '{text}' -> {expected_action}",
        detected_action == expected_action,
        f"got {detected_action}",
    )
    if expected_action == "cancel_timer" and expected_label:
        check(f"  label={expected_label}", detected_label == expected_label, f"got {detected_label}")


# ============================================================
# Test 2: list_timers with no timers
# ============================================================
print("\n=== Test 2: List timers - empty ===")

profile_id = "test_profile_timer"
import database
database.save_profile(profile_id, {
    "profile_id": profile_id,
    "name": {"full_name": "Test User", "preferred_address": "Test"},
    "family": [],
    "life_story": {"hometown": "Testville", "work": "tester"},
    "likes": {"music": [], "food": [], "topics": [], "routines": []},
    "avoid": {"topics": []},
    "daily_routine": {"wake_time": "07:00", "meals": {}, "medication_times": []},
})

result = list_timers(profile_id)
check("list_timers returns success", result.success, f"got {result}")
check("list_timers says no timers", "no timers" in result.result_text.lower() or "any timers" in result.result_text.lower() or "don't have" in result.result_text.lower(), f"got {result.result_text}")


# ============================================================
# Test 3: list_timers with active timers
# ============================================================
print("\n=== Test 3: List timers - with timers ===")

# Add two timers directly to the database
database.save_checkin_fired({
    "profile_id": profile_id,
    "slot_id": "timer_pasta",
    "checkin_type": "timer",
    "fired_at": __import__("time").time(),
    "wording_id": "",
    "response_received": False,
    "retry_count": 0,
    "skip_reason": "",
})
database.save_checkin_fired({
    "profile_id": profile_id,
    "slot_id": "timer_yoga",
    "checkin_type": "timer",
    "fired_at": __import__("time").time(),
    "wording_id": "",
    "response_received": False,
    "retry_count": 0,
    "skip_reason": "",
})

result = list_timers(profile_id)
check("list_timers returns success", result.success, f"got {result}")
check("list_timers mentions pasta", "pasta" in result.result_text.lower(), f"got {result.result_text}")
check("list_timers mentions yoga", "yoga" in result.result_text.lower(), f"got {result.result_text}")


# ============================================================
# Test 4: cancel_timer cancels an active timer
# ============================================================
print("\n=== Test 4: Cancel timer ===")

result = cancel_timer("timer_pasta", profile_id)
check("cancel_timer returns success", result.success, f"got {result}")
check("cancel_timer result mentions cancel", "cancel" in result.result_text.lower(), f"got {result.result_text}")

# Verify timer is gone from database
remaining = database.list_timers(profile_id)
remaining_timers = [t for t in remaining if t.get("slot_id") == "timer_pasta"]
check("timer removed from database", len(remaining_timers) == 0, f"got {len(remaining_timers)} timers")

# Verify other timer still exists
remaining_yoga = [t for t in remaining if t.get("slot_id") == "timer_yoga"]
check("other timer still exists", len(remaining_yoga) == 1, f"got {len(remaining_yoga)} timers")


# ============================================================
# Test 5: cancel_timer for non-existent timer
# ============================================================
print("\n=== Test 5: Cancel timer - nonexistent ===")

result = cancel_timer("nonexistent_timer", profile_id)
check("cancel_timer nonexistent returns failure", not result.success, f"got {result}")
check("cancel_timer failure message", "don't have" in result.result_text.lower() or "not found" in result.result_text.lower() or "couldn't" in result.result_text.lower(), f"got {result.result_text}")


# ============================================================
# Cleanup
# ============================================================
print("\n=== Cleanup ===")
conn = database.get_connection()
conn.execute("DELETE FROM checkin_fired_history WHERE profile_id = ?", (profile_id,))
conn.execute("DELETE FROM profiles WHERE profile_id = ?", (profile_id,))
conn.commit()
conn.close()
print("Cleanup done")


# ============================================================
# Summary
# ============================================================
print(f"\n=== Results: {passed} passed, {failed} failed ===")
if failed:
    sys.exit(1)