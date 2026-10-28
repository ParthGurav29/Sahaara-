"""
Tests for timers — duration parsing, detection, and real persistence.

Owner decisions under test (2026-10-28):
- Real wall-clock time, and seconds are supported ("30 seconds")
- Timers are stored, not just claimed
- Asking for a timer without a duration asks back instead of guessing
"""

import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Isolated throwaway DB — never touch the live one.
_TMP_DB = Path(tempfile.mkdtemp()) / "actions_timer.db"
os.environ["SAHAARA_DB_PATH"] = str(_TMP_DB)

import database  # noqa: E402
database.init_db()

from actions import (  # noqa: E402
    detect_action,
    format_duration,
    parse_duration_seconds,
    set_timer,
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
# Test 1: Duration parsing (seconds)
# ============================================================
print("=== Test 1: Duration parsing ===")

duration_tests = [
    ("30 seconds", 30),
    ("thirty seconds", 30),
    ("1 minute", 60),
    ("5 minutes", 300),
    ("5 min", 300),
    ("ten minutes", 600),
    ("half an hour", 1800),
    ("half hour", 1800),
    ("an hour", 3600),
    ("one hour", 3600),
    ("an hour and a half", 5400),
    ("two and a half hours", 9000),
    ("2 hours", 7200),
    ("90 sec", 90),
    ("no duration here", None),
]

for text, expected in duration_tests:
    result = parse_duration_seconds(text)
    check(f"parse '{text}' -> {expected}", result == expected, f"got {result}")

# ============================================================
# Test 2: Human-readable duration
# ============================================================
print("\n=== Test 2: format_duration ===")
format_tests = [
    (30, "30 seconds"),
    (60, "1 minute"),
    (300, "5 minutes"),
    (3600, "1 hour"),
    (5400, "1 hour and 30 minutes"),
    (90, "1 minute and 30 seconds"),
]
for seconds, expected in format_tests:
    result = format_duration(seconds)
    check(f"format {seconds} -> '{expected}'", result == expected, f"got {result!r}")

# ============================================================
# Test 3: Detection
# ============================================================
print("\n=== Test 3: Detection ===")

detect_tests = [
    ("set a timer for 30 seconds", 30),
    ("set a timer for 2 minutes", 120),
    ("set a 5 minute timer", 300),
    ("start a countdown for 45 seconds", 45),
    ("put on a 10 minute timer", 600),
    ("timer for 5 minutes", 300),
    ("set a timer for ten minutes", 600),
    ("set a timer for half an hour", 1800),
    ("set the egg timer for 4 minutes", 240),
]

for text, expected_seconds in detect_tests:
    result = detect_action(text)
    ok = result is not None and result["action_type"] == "set_timer"
    check(f"detect '{text}' -> set_timer", ok, f"got {result}")
    if ok:
        check(
            f"  '{text}' duration -> {expected_seconds}s",
            result["params"]["seconds"] == expected_seconds,
            f"got {result['params']['seconds']}",
        )

# A label survives the duration being stripped out.
result = detect_action("set a timer for 10 minutes to steep the tea")
check(
    "detect timer label 'steep the tea'",
    result is not None and result["params"]["label"] == "steep the tea",
    f"got {result['params']['label'] if result else None}",
)

# No duration still counts as a timer request — it asks for one.
result = detect_action("set a timer")
check("detect 'set a timer' with no duration", result is not None and result["params"]["seconds"] is None)

# Not timers.
for text in ("hello how are you", "play some music", "what time is it"):
    check(f"'{text}' is not a timer", (detect_action(text) or {}).get("action_type") != "set_timer")

# ============================================================
# Test 4: set_timer really stores something
# ============================================================
print("\n=== Test 4: set_timer persistence ===")

result = set_timer(30, label="tea", profile_id="asha_demo_001", label_raw="set a timer for 30 seconds to steep the tea")
check("set_timer success", result.success is True)
check("set_timer says 30 seconds", "30 seconds" in result.result_text, result.result_text)
check("set_timer names the label", "tea" in result.result_text, result.result_text)

row = database.get_reminder(result.detail["id"])
check("timer row exists", row is not None)
if row:
    check("timer kind", row["kind"] == "timer")
    check("timer label", row["label"] == "tea")
    check("timer status pending", row["status"] == "pending")
    check(
        "timer fire_at is real time (no 300x compression)",
        25 <= (row["fire_at"] - time.time()) <= 31,
        f"got {row['fire_at'] - time.time():.1f}s away",
    )
    check(
        "timer keeps the person's exact words for the dashboard",
        "steep the tea" in row["label_raw"],
    )

# Zero / missing duration asks instead of creating a 0-second timer.
result = set_timer(0, profile_id="asha_demo_001")
check("set_timer(0) -> success=False", result.success is False)
check("set_timer(0) asks 'How long'", "How long" in result.result_text, result.result_text)
check("set_timer(0) needs a duration", result.detail.get("needs") == "duration")

print(f"\n  RESULT: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
