"""
Tests for listing and cancelling timers/reminders.

Owner decisions under test (2026-10-28):
- "what timers do I have" answers from the new one-off table, not from the
  recurring check-in history (which used to make it list yesterday's greeting)
- "cancel the timer" really cancels it
- More than one pending timer asks which one instead of guessing
"""

import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_TMP_DB = Path(tempfile.mkdtemp()) / "actions_cancel.db"
os.environ["SAHAARA_DB_PATH"] = str(_TMP_DB)

import database  # noqa: E402
database.init_db()

from actions import cancel_timer, detect_action, list_timers, set_reminder, set_timer  # noqa: E402

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
# Test 1: Detection
# ============================================================
print("=== Test 1: Detect list/cancel patterns ===")

detect_tests = [
    ("what timers do i have", "list_timers"),
    ("list my timers", "list_timers"),
    ("show my alarms", "list_timers"),
    ("cancel the timer", "cancel_timer"),
    ("cancel that timer", "cancel_timer"),
    ("cancel the tea timer", "cancel_timer"),
    ("stay healthy and drink water", None),
]

for text, expected in detect_tests:
    result = detect_action(text)
    got = result["action_type"] if result else None
    check(f"detect '{text}' -> {expected}", got == expected, f"got {got}")

# "stop the timer" cancels; "stop the music" stops playback.
check("'stop the timer' cancels", (detect_action("stop the timer") or {}).get("action_type") == "cancel_timer")
check("'stop the music' stops media", (detect_action("stop the music") or {}).get("action_type") == "stop_media")

# ============================================================
# Test 2: Empty state
# ============================================================
print("\n=== Test 2: Nothing pending ===")

result = list_timers(profile_id="asha_demo_001")
check("list_timers on an empty profile succeeds", result.success is True)
check("says there are no timers", "don't have any timers" in result.result_text, result.result_text)
check("empty timer list", result.detail["timers"] == [])

result = cancel_timer(profile_id="asha_demo_001")
check("cancel with nothing pending -> success=False", result.success is False)
check("cancel says there is nothing to cancel", "no timer" in result.result_text.lower(), result.result_text)

# ============================================================
# Test 3: One timer, then list it
# ============================================================
print("\n=== Test 3: Listing ===")

created = set_timer(600, label="tea", profile_id="asha_demo_001")
check("created a timer", created.success is True)

result = list_timers(profile_id="asha_demo_001")
check("list_timers success", result.success is True)
check("lists one timer", len(result.detail["timers"]) == 1)
check("names the timer label", "tea" in result.result_text, result.result_text)
check(
    "reports roughly ten minutes left",
    "9 minutes" in result.result_text or "10 minutes" in result.result_text,
    result.result_text,
)
check(
    "does not call the recurring check-in history a timer",
    "morning" not in result.result_text.lower(),
    result.result_text,
)

# ============================================================
# Test 4: A reminder shows up too
# ============================================================
print("\n=== Test 4: Reminders are listed ===")

set_reminder(1200, label="take my pills", profile_id="asha_demo_001")
result = list_timers(profile_id="asha_demo_001")
check("lists both", len(result.detail["timers"]) == 2)
check("medication is spoken generically", "take your medication" in result.result_text, result.result_text)
check("never speaks the raw pill wording", "pills" not in result.result_text.lower(), result.result_text)

# ============================================================
# Test 5: Cancel needs to know which one
# ============================================================
print("\n=== Test 5: Cancel with ambiguity ===")

result = cancel_timer(profile_id="asha_demo_001")
check("ambiguous cancel -> success=False", result.success is False)
check("asks which one", "which one" in result.result_text.lower(), result.result_text)
check("offers the candidates", len(result.detail["candidates"]) == 2)

# Cancelling by name works.
result = cancel_timer(profile_id="asha_demo_001", which="tea")
check("cancel by label succeeds", result.success is True)
check("says it cancelled the timer", "cancelled" in result.result_text.lower(), result.result_text)
check("names the cancelled timer", "tea" in result.result_text, result.result_text)

remaining = list_timers(profile_id="asha_demo_001")
check("one timer left", len(remaining.detail["timers"]) == 1)
check("the reminder survived", remaining.detail["timers"][0]["kind"] == "reminder")

# Cancelling the last one needs no name.
result = cancel_timer(profile_id="asha_demo_001")
check("cancel the only remaining timer", result.success is True)

result = list_timers(profile_id="asha_demo_001")
check("nothing pending again", result.detail["timers"] == [])

# A cancelled timer cannot be cancelled twice.
check("second cancel fails", cancel_timer(profile_id="asha_demo_001").success is False)

# ============================================================
# Test 6: Cancelling keeps the row (audit trail)
# ============================================================
print("\n=== Test 6: Cancelled rows are kept ===")

created = set_timer(300, label="rice", profile_id="asha_demo_001")
row_id = created.detail["id"]
cancel_timer(profile_id="asha_demo_001")
row = database.get_reminder(row_id)
check("row still exists", row is not None)
check("row status is cancelled", row and row["status"] == "cancelled")
check("row is not served as pending", database.get_due_reminders("asha_demo_001", time.time() + 10_000) == [])

print(f"\n  RESULT: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
