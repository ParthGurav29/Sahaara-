"""
Tests for deterministic action replies (no LLM).

Owner decisions under test (2026-10-28):
- Action turns are answered from the action result, never by the model, so
  Sahaara cannot paraphrase "I set a timer" when no timer exists
- A danger / physical-distress turn keeps the safety path — an action phrase
  must never replace the emergency reply
- The dashboard gets a record of what was heard and what was said
"""

import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_TMP_DB = Path(tempfile.mkdtemp()) / "action_replies.db"
os.environ["SAHAARA_DB_PATH"] = str(_TMP_DB)

import database  # noqa: E402
database.init_db()

import conversation  # noqa: E402


class FakeProvider:
    """Records whether the LLM was reached at all."""

    def __init__(self, reply: str = "I'm right here with you."):
        self.calls = 0
        self.reply = reply

    def complete_from_prompt_bundle(self, bundle, max_tokens=300):
        self.calls += 1
        return SimpleNamespace(text=self.reply, latency_ms=1, model="fake")


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
# Test 1: An action turn never reaches the model
# ============================================================
print("=== Test 1: Action turns are deterministic ===")

provider = FakeProvider()
conversation.provider = provider
conversation.reset_pipeline()

result = conversation.process_turn("set a timer for 2 minutes")

check("no error in the result", "error" not in result, result.get("error"))
check("reply is the timer confirmation", "timer" in result["response"].lower(), result["response"])
check("says two minutes", "2 minutes" in result["response"], result["response"])
check("model is 'deterministic'", result["meta"].get("model") == "deterministic", result["meta"].get("model"))
check("the LLM was never called", provider.calls == 0, f"calls={provider.calls}")
check("action recorded in meta", result["meta"].get("action", {}).get("type") == "set_timer")
check("action reported as done", result["meta"].get("action", {}).get("success") is True)

# The timer really exists.
pending = database.list_pending_reminders("asha_demo_001")
check("timer stored", len(pending) == 1, f"got {len(pending)}")

# ============================================================
# Test 2: A failed action says so
# ============================================================
print("\n=== Test 2: Failures are stated honestly ===")

result = conversation.process_turn("cancel the timer for the rice")
check("no error", "error" not in result, result.get("error"))
check(
    "does not claim a cancellation",
    "cancelled" not in result["response"].lower(),
    result["response"],
)
check("says it could not find it", "don't have" in result["response"].lower(), result["response"])
check(
    "failure recorded as failure",
    result["meta"].get("action", {}).get("success") is False,
)
check("still deterministic", result["meta"].get("model") == "deterministic")
check("LLM still not called", provider.calls == 0, f"calls={provider.calls}")

# ============================================================
# Test 3: Asking for a timer with no duration asks back
# ============================================================
print("\n=== Test 3: Missing details are asked for ===")

result = conversation.process_turn("set a timer")
check("no error", "error" not in result, result.get("error"))
check("asks how long", "how long" in result["response"].lower(), result["response"])
check("needs is reported", result["meta"].get("action", {}).get("needs") == "duration")

# ============================================================
# Test 4: Medication reminder is sanitised in the reply
# ============================================================
print("\n=== Test 4: Medication is never dosed back ===")

result = conversation.process_turn("remind me in 5 minutes to take two pills at 8")
check("no error", "error" not in result, result.get("error"))
check("says 'medication'", "take your medication" in result["response"], result["response"])
check("never repeats the count", "two" not in result["response"].lower(), result["response"])
check("never repeats a time-of-dose", "at 8" not in result["response"], result["response"])

# ============================================================
# Test 5: The dashboard record keeps the exact words
# ============================================================
print("\n=== Test 5: Dashboard record ===")

from conversation import _get_session  # noqa: E402

s = _get_session()
actions = [e for e in s.event_log._events if e.type.value == "action"]
check("action events were logged", len(actions) >= 3, f"got {len(actions)}")

med = [e for e in actions if "pills" in (e.detail.get("raw_text") or "")]
check("the person's exact words are recorded", bool(med), f"got {[e.detail for e in actions]}")
if med:
    check("raw text keeps the dose wording", "two pills at 8" in med[-1].detail["raw_text"], med[-1].detail["raw_text"])
    check(
        "the spoken version stays generic",
        "medication" in med[-1].detail["spoken"],
        med[-1].detail["spoken"],
    )

# ============================================================
# Test 6: A danger turn keeps the safety path
# ============================================================
print("\n=== Test 6: Danger beats an action ===")

provider = FakeProvider("I'm right here with you. That sounds frightening.")
conversation.provider = provider
conversation.reset_pipeline()

pending_before = {r["id"] for r in database.list_pending_reminders("asha_demo_001")}

result = conversation.process_turn("set a timer for 5 minutes, I can't breathe")

check("no error", "error" not in result, result.get("error"))
check(
    "reply is not a timer confirmation",
    "I've set a timer" not in result["response"],
    result["response"],
)
check("the safety path ran", provider.calls >= 1, f"calls={provider.calls}")
check(
    "no timer was created on an emergency turn",
    {r["id"] for r in database.list_pending_reminders("asha_demo_001")} == pending_before,
    database.list_pending_reminders("asha_demo_001"),
)

# ============================================================
# Test 7: Media is delivered at the top level, not buried in meta
# ============================================================
print("\n=== Test 7: Media keys are callable ===")

import actions as _actions  # noqa: E402

_real_alive = _actions._stream_alive
_actions._stream_alive = lambda url, timeout=4.0: True
try:
    result = conversation.process_turn("play the radio")
finally:
    _actions._stream_alive = _real_alive

check("radio success", result["meta"]["action"]["success"] is True, result.get("response"))
check("top-level radio_stream_url", bool(result.get("radio_stream_url")), result)
check("radio url is a real url", str(result.get("radio_stream_url")).startswith("https://"))
check("no audio_url for radio", result.get("audio_url") is None, result.get("audio_url"))
check("stop not requested", result.get("stop_media") is False)

result = conversation.process_turn("stop the music")
check("stop requested at top level", result.get("stop_media") is True, result)
check("stop has no media url", result.get("radio_stream_url") is None and result.get("audio_url") is None)

result = conversation.process_turn("what time is it")
check("ordinary action has no media", result.get("radio_stream_url") is None and result.get("audio_url") is None)

# ============================================================
# Test 8: Ordinary chat still uses the model
# ============================================================
print("\n=== Test 8: Ordinary turns still use the model ===")

provider = FakeProvider("It's good to hear your voice.")
conversation.provider = provider
conversation.reset_pipeline()

result = conversation.process_turn("I had a quiet morning today")

check("no error", "error" not in result, result.get("error"))
check("the model answered", provider.calls == 1, f"calls={provider.calls}")
check("not marked deterministic", result["meta"].get("model") != "deterministic")

print(f"\n  RESULT: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
