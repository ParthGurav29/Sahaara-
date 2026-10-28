"""
Tests for the timer loop across profiles.

Why this file exists (2026-10-28 bug):
    The live backend started its Scheduler for whichever profile row happened
    to be first in SQLite (a stray `test_profile_001`). Asha's 5-second timer
    was stored under `asha_demo_001`, the timer loop only ever asked for
    `test_profile_001`'s rows, and the timer never fired — Sahaara had said
    "I've set a timer for 5 seconds" and then said nothing at all.

What must hold:
  - A due timer fires even when the running Scheduler belongs to another
    profile.
  - It is routed to the Scheduler instance that owns the timer's profile, so
    that instance's dashboard/voice queues get it.
  - The `reminder_fired` event is logged on the timer's own profile.
  - It still fires exactly once.
  - The default active profile is the demo profile, not "first row wins".
"""

import asyncio
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_TMP_DB = Path(tempfile.mkdtemp()) / "reminders_cross_profile.db"
os.environ["SAHAARA_DB_PATH"] = str(_TMP_DB)

import database  # noqa: E402
database.init_db()

import scheduler as scheduler_module  # noqa: E402
from actions import set_timer  # noqa: E402
from session_manager import SessionManager  # noqa: E402

PROFILE = "asha_demo_001"
OTHER = "test_profile_001"

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


def drain(queue) -> list:
    items = []
    while True:
        try:
            items.append(queue.get_nowait())
        except asyncio.QueueEmpty:
            return items


# A second profile, exactly the kind of row a test leaves behind. `list_profiles`
# sorts by name, so "Test" sorts before the demo name — the old `get_active()`
# therefore picked it at startup.
demo = dict(database.get_profile(PROFILE))
other = {
    **demo,
    "profile_id": OTHER,
    "name": {**demo.get("name", {}), "preferred_address": "Test"},
}
database.save_profile(OTHER, other)

# ============================================================
# Test 1: the default active profile is the demo profile
# ============================================================
print("=== Test 1: Default profile ===")

manager = SessionManager()
active = manager.get_active()
check("active profile defaults to the demo profile", active and active.profile_id == PROFILE,
      active and active.profile_id)
check("a stray test profile is never the default", active and active.profile_id != OTHER,
      active and active.profile_id)

os.environ["SAHAARA_DEFAULT_PROFILE"] = OTHER
try:
    override = SessionManager().get_active()
    check("SAHAARA_DEFAULT_PROFILE overrides the default",
          override and override.profile_id == OTHER, override and override.profile_id)
finally:
    os.environ.pop("SAHAARA_DEFAULT_PROFILE", None)

# ============================================================
# Test 2: a timer fires from a Scheduler built for another profile
# ============================================================
print("\n=== Test 2: Cross-profile firing ===")

scheduler_module._scheduler_instances.clear()
wrong = scheduler_module.get_scheduler_for_profile(OTHER)     # what startup did
right = scheduler_module.get_scheduler_for_profile(PROFILE)   # who owns the timer
drain(wrong._initiative_queue)
drain(right._initiative_queue)
drain(right.get_voice_initiative_queue())

timer = set_timer(0.05, label="tea", profile_id=PROFILE)
timer_id = timer.detail["id"]
time.sleep(0.15)

asyncio.run(wrong._fire_due_reminders())

row = database.get_reminder(timer_id)
check("timer marked fired", row and row["status"] == "fired", row and row["status"])
check("no voice session -> not spoken", row and row["delivered_voice"] == 0,
      row and row["delivered_voice"])

right_dashboard = drain(right._initiative_queue)
wrong_dashboard = drain(wrong._initiative_queue)
check("the timer's own profile got the dashboard line", len(right_dashboard) == 1,
      f"got {len(right_dashboard)}")
check("the other profile's queue stayed empty", len(wrong_dashboard) == 0,
      f"got {len(wrong_dashboard)}")
check("the line is the spoken timer line",
      right_dashboard and "tea" in right_dashboard[0]["data"]["text"],
      right_dashboard and right_dashboard[0]["data"]["text"])

from conversation import get_session_for_profile  # noqa: E402
events = [e for e in get_session_for_profile(PROFILE).event_log.get_all()
          if e.type.value == "reminder_fired"]
check("reminder_fired logged on the timer's profile", len(events) == 1, f"got {len(events)}")
check("event keeps the reminder id", events and events[0].detail.get("reminder_id") == timer_id,
      events and events[0].detail)
check("event records dashboard-only delivery",
      events and events[0].detail.get("delivered_voice") is False,
      events and events[0].detail)

# ============================================================
# Test 3: still exactly once
# ============================================================
print("\n=== Test 3: No double fire ===")

asyncio.run(wrong._fire_due_reminders())
right_dashboard = drain(right._initiative_queue)
check("a later tick does not re-announce the timer", len(right_dashboard) == 0,
      f"got {len(right_dashboard)}")
events = [e for e in get_session_for_profile(PROFILE).event_log.get_all()
          if e.type.value == "reminder_fired"]
check("exactly one reminder_fired event", len(events) == 1, f"got {len(events)}")

# ============================================================
# Test 4: an open voice session gets the spoken queue
# ============================================================
print("\n=== Test 4: Voice delivery goes to the right instance ===")

right.mark_voice_session(True)
try:
    second = set_timer(0.05, label="stretch", profile_id=PROFILE)
    second_id = second.detail["id"]
    time.sleep(0.15)
    asyncio.run(wrong._fire_due_reminders())
finally:
    right.mark_voice_session(False)

row = database.get_reminder(second_id)
check("second timer fired", row and row["status"] == "fired", row and row["status"])
check("marked as delivered by voice", row and row["delivered_voice"] == 1,
      row and row["delivered_voice"])
voice = drain(right.get_voice_initiative_queue())
check("the voice queue of the row's own profile got it", len(voice) == 1, f"got {len(voice)}")
check("voice message carries the timer category",
      voice and voice[0]["category"] == "timer", voice and voice[0].get("category"))

print(f"\n  RESULT: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
