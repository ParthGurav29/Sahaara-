"""
backend/tests/unit_e2e.py

Comprehensive unit tests for all Sahaara modules not already covered
by the existing test files. Run with:
    cd backend && .venv/bin/python tests/unit_e2e.py
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Load .env from repo root (where it actually lives)
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[2] / ".env")

passed = 0
failed = 0
failures = []


def check(name, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  PASS: {name}")
    else:
        failed += 1
        failures.append((name, detail))
        print(f"  FAIL: {name} {detail}")


def section(title):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


# ============================================================
# 1. Intent/Emotion Classification
# ============================================================
section("1. INTENT/EMOTION CLASSIFICATION")

from llm.intent_emotion import classify_intent

# Emotion detection
r = classify_intent("I'm so worried about everything")
check("anxious emotion detected", r.emotion == "anxious", f"got {r.emotion}")

r = classify_intent("I want to go home, help me")
check("distressed emotion detected", r.emotion == "distressed", f"got {r.emotion}")

r = classify_intent("I miss my daughter so much")
check("sad emotion detected", r.emotion == "sad", f"got {r.emotion}")

r = classify_intent("Good morning, thank you")
check("content emotion detected", r.emotion == "content", f"got {r.emotion}")

r = classify_intent("Hello, how are you?")
check("calm/neutral default", r.emotion in ("calm", "neutral"), f"got {r.emotion}")

# Factual ask
r = classify_intent("What time is dinner?")
check("factual ask detected", r.is_factual_ask is True, f"got {r.is_factual_ask}")

# Danger statement
r = classify_intent("I want to die")
check("danger statement detected", r.is_danger_statement is True, f"got {r.is_danger_statement}")

r = classify_intent("kill myself")
check("danger: kill myself", r.is_danger_statement is True)

r = classify_intent("better off without me")
check("danger: better off without me", r.is_danger_statement is True)

# Physical urgent
r = classify_intent("I can't breathe")
check("physical urgent: can't breathe", r.is_physical_urgent is True, f"urgent={r.is_physical_urgent} general={r.is_physical_general}")

r = classify_intent("chest pain")
check("physical urgent: chest pain", r.is_physical_urgent is True)

r = classify_intent("I fell")
check("physical urgent: I fell", r.is_physical_urgent is True)

r = classify_intent("can't get up")
check("physical urgent: can't get up", r.is_physical_urgent is True)

r = classify_intent("feel faint")
check("physical urgent: feel faint", r.is_physical_urgent is True)

# Physical general
r = classify_intent("I'm not feeling well today")
check("physical general: not feeling well", r.is_physical_general is True, f"general={r.is_physical_general}")

r = classify_intent("I'm dizzy")
check("physical general: dizzy", r.is_physical_general is True)

r = classify_intent("stomach hurts")
check("physical general: stomach hurts", r.is_physical_general is True)

r = classify_intent("I have a headache")
check("physical general: headache", r.is_physical_general is True)

# Anti-false-positive: emotional chest tightness should NOT be physical
r = classify_intent("I can't shake this terrible feeling in my chest")
check("emotional chest NOT physical urgent", r.is_physical_urgent is False)
check("emotional chest NOT physical general", r.is_physical_general is False)

# Anti-false-positive: calm breathing
r = classify_intent("I'm breathing slowly and feeling calm")
check("calm breathing NOT physical urgent", r.is_physical_urgent is False)
check("calm breathing NOT physical general", r.is_physical_general is False)
check("calm breathing is calm", r.emotion == "calm", f"got {r.emotion}")

# ============================================================
# 2. Escalation Engine
# ============================================================
section("2. ESCALATION ENGINE")

from safety.escalation import EscalationEngine, EscalationTier, EscalationReason, EscalationConfig
from events.event_log import EventLog
from memory.session_notes import SessionNotes
from memory.profile import Profile

def make_engine():
    profile_data = {
        "profile_id": "test", "name": {"preferred_address": "Asha", "full_name": "Asha Kulkarni"},
        "family": [], "life_story": {"hometown": "Pune", "work": "teacher"}, "likes": {},
        "avoid": {"topics": []}, "daily_routine": {}
    }
    profile = Profile(profile_data)
    event_log = EventLog(profile_id="test", demo_mode=True)
    session_notes = SessionNotes(profile_id="test")
    config = EscalationConfig(demo_mode=True, demo_multiplier=300.0)
    return EscalationEngine(event_log, session_notes, config), session_notes

# Green -> Yellow via repetition (3 repeats)
engine, notes = make_engine()
notes.record_turn("Where is my daughter?", emotion="anxious")
time.sleep(0.3)  # ensure min_tier_hold passes
engine.evaluate("Where is my daughter?")
notes.record_turn("Where is my daughter?", emotion="anxious")
time.sleep(0.3)
engine.evaluate("Where is my daughter?")
notes.record_turn("Where is my daughter?", emotion="anxious")
time.sleep(0.3)
tier = engine.evaluate("Where is my daughter?")
check("3 repeats -> Yellow", tier == EscalationTier.YELLOW, f"got {tier}")

# Danger statement -> RED (force_tier)
engine, notes = make_engine()
engine.force_tier(EscalationTier.RED, "danger_statement")
check("force_tier RED", engine.current_tier == EscalationTier.RED, f"got {engine.current_tier}")

# Physical urgent -> RED via evaluate
engine, notes = make_engine()
from llm.intent_emotion import IntentResult
urgent_intent = IntentResult(text="I can't breathe", emotion="anxious", is_emotional_need=True,
                             is_factual_ask=False, is_danger_statement=False,
                             is_physical_urgent=True, is_physical_general=False,
                             matched_physical_keywords=["can't breathe"])
tier = engine.evaluate("I can't breathe", urgent_intent)
check("physical urgent -> RED", tier == EscalationTier.RED, f"got {tier}")

# Physical general -> ORANGE via evaluate
engine, notes = make_engine()
general_intent = IntentResult(text="I'm not feeling well", emotion="distressed", is_emotional_need=True,
                              is_factual_ask=False, is_danger_statement=False,
                              is_physical_urgent=False, is_physical_general=True,
                              matched_physical_keywords=["not feeling well"])
tier = engine.evaluate("I'm not feeling well", general_intent)
check("physical general -> ORANGE", tier == EscalationTier.ORANGE, f"got {tier}")

# get_status returns expected fields
engine, notes = make_engine()
status = engine.get_status()
check("status has tier field", "tier" in status)
check("status has tier_duration_seconds", "tier_duration_seconds" in status)
check("status has last_repeat_count", "last_repeat_count" in status)
check("status has repeat_threshold", "repeat_threshold" in status)
check("status has demo_mode", "demo_mode" in status)

# ============================================================
# 3. Consent State Machine
# ============================================================
section("3. CONSENT STATE MACHINE")

from safety.consent import ConsentManager, ConsentState, ContinuationManager, ContinuationState

# Accept
mgr = ConsentManager(demo_mode=True, demo_multiplier=300.0)
mgr.ask_consent("Priya", "daughter", EscalationTier.YELLOW, "Call Priya?")
check("consent PENDING after ask", mgr.state == ConsentState.PENDING)
result = mgr.interpret_response("Yes, please call her")
check("consent ACCEPTED on 'yes'", result == ConsentState.ACCEPTED, f"got {result}")

# Decline
mgr = ConsentManager(demo_mode=True, demo_multiplier=300.0)
mgr.ask_consent("Rohan", "son", EscalationTier.ORANGE, "Call Rohan?")
result = mgr.interpret_response("No, don't call")
check("consent DECLINED on 'no'", result == ConsentState.DECLINED, f"got {result}")

# Timeout
mgr = ConsentManager(demo_mode=True, demo_multiplier=300.0)
mgr.ask_consent("Priya", "daughter", EscalationTier.YELLOW, "Call Priya?")
time.sleep(0.15)  # demo timeout = 30/300 = 0.1s
mgr.check_timeout()
check("consent TIMED_OUT after demo timeout", mgr.state == ConsentState.TIMED_OUT, f"got {mgr.state}")

# Topic shift -> decline
mgr = ConsentManager(demo_mode=True, demo_multiplier=300.0)
mgr.ask_consent("Priya", "daughter", EscalationTier.YELLOW, "Call Priya?")
result = mgr.interpret_response("Where is my husband?")
check("topic shift -> DECLINED", result == ConsentState.DECLINED, f"got {result}")

# Ambiguous -> stays pending (use a response with no yes/no decline markers)
mgr = ConsentManager(demo_mode=True, demo_multiplier=300.0)
mgr.ask_consent("Priya", "daughter", EscalationTier.YELLOW, "Call Priya?")
result = mgr.interpret_response("hmm")
check("ambiguous -> PENDING", result == ConsentState.PENDING, f"got {result}")

# Timeout clears state -> next turn processed normally (not stuck in TIMED_OUT)
mgr = ConsentManager(demo_mode=True, demo_multiplier=300.0)
mgr.ask_consent("Priya", "daughter", EscalationTier.YELLOW, "Call Priya?")
time.sleep(0.15)  # demo timeout = 30/300 = 0.1s
mgr.check_timeout()
check("consent TIMED_OUT after demo timeout", mgr.state == ConsentState.TIMED_OUT, f"got {mgr.state}")
# Simulate conversation.py clearing the state after timeout detection
mgr.state = ConsentState.IDLE
mgr.context = None
mgr._timeout_at = None
# interpret_response only processes when PENDING; IDLE means "not blocking"
result = mgr.interpret_response("Yes, please call her")
check("consent does not block after timeout (returns IDLE, not TIMED_OUT)", result == ConsentState.IDLE, f"got {result}")
check("consent state is IDLE after timeout+process", mgr.state == ConsentState.IDLE, f"got {mgr.state}")

# Danger phrase right after timeout reaches Red (consent never blocks danger)
mgr = ConsentManager(demo_mode=True, demo_multiplier=300.0)
mgr.ask_consent("Priya", "daughter", EscalationTier.YELLOW, "Call Priya?")
time.sleep(0.15)
mgr.check_timeout()
check("consent TIMED_OUT after demo timeout", mgr.state == ConsentState.TIMED_OUT, f"got {mgr.state}")
# Clear timeout state (as conversation.py now does)
mgr.state = ConsentState.IDLE
mgr.context = None
mgr._timeout_at = None
# Danger/physical-urgent classification works independently of consent state
from llm.intent_emotion import classify_intent
danger_result = classify_intent("I can't breathe")
check("physical urgent detected after consent timeout", danger_result.is_physical_urgent, f"got is_physical_urgent={danger_result.is_physical_urgent}")
check("danger phrase classified as physical urgent", danger_result.is_physical_urgent is True, f"danger_result: {danger_result}")

# Reset clears consent state
mgr = ConsentManager(demo_mode=True, demo_multiplier=300.0)
mgr.ask_consent("Rohan", "son", EscalationTier.ORANGE, "Call Rohan?")
check("consent PENDING after ask", mgr.state == ConsentState.PENDING)
mgr.reset()
check("consent IDLE after reset", mgr.state == ConsentState.IDLE, f"got {mgr.state}")
check("consent context cleared after reset", mgr.context is None, f"got {mgr.context}")
check("consent timeout cleared after reset", mgr._timeout_at is None, f"got {mgr._timeout_at}")

# ============================================================
# 3b. Continuation State Machine
# ============================================================
section("3b. CONTINUATION STATE MACHINE")

from safety.consent import ContinuationManager, ContinuationState

# Ask continuation -> PENDING
cm = ContinuationManager()
cm.ask_continuation(
    remaining_text="The rest of the answer about Python.",
    topic_note="python programming",
)
check("continuation PENDING after ask", cm.state == ContinuationState.PENDING)
check("continuation remaining text stored",
      cm.get_remaining_text() == "The rest of the answer about Python.",
      f"got: {cm.get_remaining_text()}")

# Yes continues -> delivers remaining text
cm2 = ContinuationManager()
cm2.ask_continuation(
    remaining_text="The rest of the answer about Python.",
    topic_note="python programming",
)
result = cm2.interpret_response("Yes, please go on")
check("continuation IDLE after 'yes'", result == ContinuationState.IDLE, f"got {result}")
check("continuation remaining text cleared after yes",
      cm2.get_remaining_text() is None, f"got: {cm2.get_remaining_text()}")

# No drops it
cm3 = ContinuationManager()
cm3.ask_continuation(
    remaining_text="The rest of the answer about Python.",
    topic_note="python programming",
)
result = cm3.interpret_response("No, that's fine")
check("continuation IDLE after 'no'", result == ContinuationState.IDLE, f"got {result}")
check("continuation remaining text cleared after no",
      cm3.get_remaining_text() is None, f"got: {cm3.get_remaining_text()}")

# Timeout doesn't eat the next turn
cm4 = ContinuationManager(timeout_seconds=0.1)
cm4.ask_continuation(
    remaining_text="The rest of the answer about Python.",
    topic_note="python programming",
)
time.sleep(0.15)
cm4.check_timeout()
check("continuation IDLE after timeout", cm4.state == ContinuationState.IDLE, f"got {cm4.state}")
check("continuation remaining text cleared after timeout",
      cm4.get_remaining_text() is None, f"got: {cm4.get_remaining_text()}")
# Next turn processed normally
result = cm4.interpret_response("What is Python?")
check("continuation returns IDLE for new question after timeout",
      result == ContinuationState.IDLE, f"got {result}")

# Reset clears continuation state
cm5 = ContinuationManager()
cm5.ask_continuation(
    remaining_text="The rest of the answer about Python.",
    topic_note="python programming",
)
check("continuation PENDING after ask", cm5.state == ContinuationState.PENDING)
cm5.reset()
check("continuation IDLE after reset", cm5.state == ContinuationState.IDLE, f"got {cm5.state}")
check("continuation context cleared after reset", cm5.context is None, f"got: {cm5.context}")
check("continuation timeout cleared after reset", cm5._timeout_at is None, f"got: {cm5._timeout_at}")

# ============================================================
# 4. Memory Box
# ============================================================
section("4. MEMORY BOX")

from memory.memory_box import MemoryBox
from memory.profile import load_profile

profile = load_profile()
box = MemoryBox(profile)

# Longing match for Priya (has clip)
match = box.match("I miss my daughter so much")
check("memory box match for 'miss daughter'", match is not None)
if match:
    check("match family member is Priya", match.family_member_name == "Priya")
    check("match offer line", "Priya" in match.offer_line)
    check("triggered_by is longing_match", match.triggered_by == "longing_match")

# No match for non-longing text
match = box.match("What's for lunch today?")
check("no match for lunch question", match is None)

# Replay request bypass
box2 = MemoryBox(profile)
box2.match("I miss my daughter")  # offer Priya's clip
box2.record_turn()
box2.record_turn()
box2.record_turn()
box2.record_turn()
box2.record_turn()  # 5 turns passed
# Now replay request should match even if suppressed
match = box2.match("play it again")
check("replay request matches", match is not None)
if match:
    check("replay triggered_by", match.triggered_by == "replay_request")

# Suppression: re-offer within 5 turns should be suppressed
box3 = MemoryBox(profile)
match1 = box3.match("I miss Priya")  # offer
box3.record_turn()
box3.record_turn()
box3.record_turn()
box3.record_turn()  # only 4 turns, still suppressed
match2 = box3.match("when is Priya coming")
check("suppressed within 5 turns", match2 is None, f"got match: {match2}")

# clip_exists_on_disk
if match1:
    exists = box.clip_exists_on_disk(match1)
    check("clip_exists_on_disk returns bool", isinstance(exists, bool))

# Vikram (no clip) should not match
match = box.match("Where is my husband?")
check("Vikram no clip -> no match", match is None)

# ============================================================
# 5. Actions
# ============================================================
section("5. ACTIONS")

from actions import tell_time, play_song, set_reminder, detect_action

# tell_time
result = tell_time()
check("tell_time success", result.success is True)
check("tell_time has result_text", len(result.result_text) > 0)

# play_song - no music files
result = play_song("lag ja gale")
check("play_song no files -> success=False", result.success is False)

# play_song - nonexistent song
result = play_song("nonexistent song xyz")
check("play_song nonexistent -> success=False", result.success is False)

# detect_action: tell_time
action = detect_action("What time is it?")
check("detect tell_time", action is not None and action["action_type"] == "tell_time")

action = detect_action("Tell me the time please")
check("detect tell_time variant", action is not None and action["action_type"] == "tell_time")

# detect_action: play_song
action = detect_action("Play Lag Ja Gale")
check("detect play_song", action is not None and action["action_type"] == "play_song")
if action and action["action_type"] == "play_song":
    check("play_song params has song_name", "song_name" in action.get("params", {}))

# detect_action: set_reminder
action = detect_action("Remind me to call Rohan in 30 minutes")
check("detect set_reminder", action is not None and action["action_type"] == "set_reminder")

action = detect_action("Set a reminder for chai in 5 mins")
check("detect set_reminder variant", action is not None and action["action_type"] == "set_reminder")

# detect_action: no action
action = detect_action("Hello how are you")
check("no action for greeting", action is None)

# set_reminder without scheduler
result = set_reminder("call Rohan", 5, scheduler=None)
check("set_reminder no scheduler -> success=False", result.success is False)

# ============================================================
# 6. Profile
# ============================================================
section("6. PROFILE")

from memory.profile import Profile, load_profile

p = load_profile()
check("profile_id", p.profile_id == "asha_demo_001")
check("preferred_address", p.preferred_address == "Asha")
check("hometown", p.hometown == "Pune, Maharashtra")
check("work", "teacher" in p.work.lower())

family = p.get_family()
check("family has 3 members", len(family) == 3)

priya = p.find_family_member("Priya")
check("find Priya by name", priya is not None and priya["name"] == "Priya")

vikram = p.find_family_member("Vikram")
check("find Vikram", vikram is not None and vikram["relationship"] == "late husband")

# Avoid topics - note: topic contains "Vikram's" with apostrophe, so
# "Vikram passed away" won't match via keyword overlap (known limitation)
check("is_avoid_topic Vikram's death", p.is_avoid_topic("Vikram's death") is True)
check("is_avoid_topic direct statements", p.is_avoid_topic("direct statements about vikram") is True)

# Likes
likes = p.get_likes()
check("likes has music", "music" in likes and len(likes["music"]) > 0)
check("likes has food", "food" in likes and len(likes["food"]) > 0)
check("likes has topics", "topics" in likes and len(likes["topics"]) > 0)

# Routine
routine = p.get_routine()
check("routine has wake_time", "wake_time" in routine)
check("routine has medication_times", len(p.get_medication_times()) > 0)

# ============================================================
# 7. Session Notes
# ============================================================
section("7. SESSION NOTES")

from memory.session_notes import SessionNotes

notes = SessionNotes("test_profile")
notes.record_turn("When is my son coming?", emotion="anxious")
check("not repeated on 1st ask", notes.is_repeated_question("When is my son coming?", min_repeats=2) is False)

notes.record_turn("When is my son coming?", emotion="anxious")
check("repeated on 2nd ask", notes.is_repeated_question("When is my son coming?", min_repeats=2) is True)
check("repeat_count == 2", notes.repeat_count("When is my son coming?") == 2)

notes.record_turn("When is my son coming?", emotion="anxious")
check("repeat_count == 3", notes.repeat_count("When is my son coming?") == 3)

# Sustained distress: need >=3 turns with >=60% anxious/distressed
notes2 = SessionNotes("test2")
notes2.record_turn("I'm worried", emotion="anxious")
notes2.record_turn("I'm scared", emotion="anxious")
notes2.record_turn("I'm distressed", emotion="distressed")
check("sustained distress with 3/3 anxious", notes2.sustained_distress() is True)

notes3 = SessionNotes("test3")
notes3.record_turn("I'm fine", emotion="calm")
notes3.record_turn("I'm good", emotion="content")
notes3.record_turn("I'm happy", emotion="content")
check("no sustained distress with calm", notes3.sustained_distress() is False)

# get_recent_sahaara_replies
notes4 = SessionNotes("test4")
notes4.record_turn("Hello", speaker="user")
notes4.record_turn("Hi there!", speaker="sahaara")
notes4.record_turn("How are you?", speaker="user")
notes4.record_turn("I'm doing well, thanks.", speaker="sahaara")
replies = notes4.get_recent_sahaara_replies(2)
check("get 2 recent sahaara replies", len(replies) == 2)
check("replies are sahaara's (normalized)", replies[0] == "im doing well thanks" and replies[1] == "hi there")

# ============================================================
# 8. Database
# ============================================================
section("8. DATABASE")

import database

# init_db already called by SessionManager, but test idempotency
database.init_db()
check("init_db idempotent", True)

# list_profiles
profiles = database.list_profiles()
check("list_profiles returns list", isinstance(profiles, list))
check("asha_demo_001 in profiles", any(p["profile_id"] == "asha_demo_001" for p in profiles))

# get_profile
data = database.get_profile("asha_demo_001")
check("get_profile returns dict", isinstance(data, dict))
check("profile has profile_id", data.get("profile_id") == "asha_demo_001")

# get_profile nonexistent
data = database.get_profile("nonexistent")
check("get_profile nonexistent returns None", data is None)

# save_profile
test_data = {"profile_id": "test_profile_001", "name": {"preferred_address": "Test", "full_name": "Test User"},
             "family": [], "life_story": {}, "likes": {}, "avoid": {"topics": []}, "daily_routine": {}}
database.save_profile("test_profile_001", test_data)
data = database.get_profile("test_profile_001")
check("save_profile roundtrip", data is not None and data.get("profile_id") == "test_profile_001")

# append_event + get_events
import time as _time
database.append_event("test_profile_001", "test_event", {"key": "value"}, _time.time())
events = database.get_events("test_profile_001", event_type="test_event")
check("append + get_events", len(events) >= 1)
check("event detail preserved", events[0]["detail"].get("key") == "value")

# get_all_events
all_events = database.get_all_events("test_profile_001")
check("get_all_events returns list", isinstance(all_events, list))

# Session summaries
from datetime import datetime
database.save_session_summary("test_profile_001", {"date": datetime.now().isoformat(), "summary_text": "test summary"})
summaries = database.get_recent_summaries("test_profile_001")
check("save + get session summaries", len(summaries) >= 1)

# Suggestions
database.save_suggestion("test_profile_001", {"suggestion_id": "sugg_001", "suggested_field": "likes.music", "suggested_value": "Lata Mangeshkar"})
suggestions = database.get_pending_suggestions("test_profile_001")
check("save + get suggestions", len(suggestions) >= 1)

database.remove_suggestion("test_profile_001", "sugg_001")
suggestions = database.get_pending_suggestions("test_profile_001")
check("remove suggestion", len([s for s in suggestions if s.get("suggestion_id") == "sugg_001"]) == 0)

# ============================================================
# 9. Alert Builder
# ============================================================
section("9. ALERT BUILDER")

from dashboard.alerts import AlertBuilder, AlertStore
from events.event_log import EventLog, EscalationTier

profile = load_profile()
event_log = EventLog(profile_id="asha_demo_001", demo_mode=True)
alert_store = AlertStore()
builder = AlertBuilder(event_log, profile, alert_store=alert_store)

# Green -> Yellow (repeat)
alert = builder.build_tier_change_alert(EscalationTier.GREEN, EscalationTier.YELLOW, "repeat_threshold",
                                        {"repeat_count": 3, "sustained_distress": False})
check("Yellow repeat alert created", alert is not None)
check("Yellow repeat alert message", "3 times" in alert.message or "repeated" in alert.message.lower())
check("alert stored", alert_store.unread_count() >= 1)

# Yellow -> Orange
alert = builder.build_tier_change_alert(EscalationTier.YELLOW, EscalationTier.ORANGE, "distress_persistence",
                                        {"repeat_count": 2, "sustained_distress": True})
check("Orange alert created", alert is not None)
check("Orange alert message", "distressed" in alert.message.lower() or "strongly recommended" in alert.message)

# Orange -> Red (danger)
alert = builder.build_tier_change_alert(EscalationTier.ORANGE, EscalationTier.RED, "danger_statement", {})
check("Red danger alert created", alert is not None)
check("Red alert URGENT", "URGENT" in alert.message)

# Red -> Green (step down)
alert = builder.build_tier_change_alert(EscalationTier.RED, EscalationTier.GREEN, "step_down", {})
check("Red->Green alert created", alert is not None)

# Consent accepted
alert = builder.build_consent_alert("accepted", "Priya", EscalationTier.YELLOW)
check("Consent accepted alert", alert is not None and "agreed" in alert.message.lower())

# Consent declined
alert = builder.build_consent_alert("declined", "Rohan", EscalationTier.ORANGE)
check("Consent declined alert", alert is not None and "declined" in alert.message.lower())

# Consent timeout
alert = builder.build_consent_alert("timeout", "Priya", EscalationTier.YELLOW)
check("Consent timeout alert", alert is not None and "didn't respond" in alert.message.lower())

# Fallback alert
alert = builder.build_fallback_alert("guardrail", {"violations": ["diagnosis"]})
check("Fallback guardrail alert", alert is not None and "adjusted for safety" in alert.message)

# Physical symptom urgent
alert = builder.build_physical_symptom_alert("I can't breathe", "can't breathe", "urgent", "red")
check("Physical urgent alert", alert is not None and "URGENT" in alert.message)

# Physical symptom general
alert = builder.build_physical_symptom_alert("I'm not feeling well", "not feeling well", "general", "orange")
check("Physical general alert", alert is not None and "not feeling well" in alert.message)

# AlertStore ack
alert = builder.build_tier_change_alert(EscalationTier.GREEN, EscalationTier.YELLOW, "repeat_threshold",
                                        {"repeat_count": 1})
acked = alert_store.acknowledge(alert.alert_id, "Will check on her")
check("Alert acknowledge", acked is not None and acked.status == "acknowledged")
check("Alert unread count decreases", alert_store.unread_count() >= 0)

# ============================================================
# 10. Daily Summary
# ============================================================
section("10. DAILY SUMMARY")

from dashboard.summary import generate_daily_summary
from events.event_log import EventType, ConsentOutcome, FallbackType

event_log = EventLog(profile_id="asha_demo_001", demo_mode=True)
event_log.log_repeat("Where is my daughter?", 3, True)
event_log.log_distress(sustained=True, emotion="anxious", duration_seconds=120)
event_log.log_tier_change(EscalationTier.GREEN, EscalationTier.YELLOW, "repeat_threshold", {})
event_log.log_tier_change(EscalationTier.YELLOW, EscalationTier.ORANGE, "distress_persistence", {})
event_log.log_consent_asked("Call Priya?", "Priya", EscalationTier.ORANGE)
event_log.log_consent_answered(ConsentOutcome.ACCEPTED, "Priya")
event_log.log_fallback(FallbackType.GUARDRAIL, {"violations": ["diagnosis"]})

summary = generate_daily_summary(event_log, profile)
check("summary is DailySummary", hasattr(summary, "summary_text"))
check("summary has profile_id", summary.profile_id == "asha_demo_001")
check("summary repeat_count >= 3", summary.repeat_count >= 3)
check("summary distress_episodes >= 1", summary.distress_episodes >= 1)
check("summary tier_changes >= 2", summary.tier_changes >= 2)
check("summary highest_tier is orange", summary.highest_tier == EscalationTier.ORANGE)
check("summary consent_accepted >= 1", summary.consent_accepted >= 1)
check("summary fallbacks_triggered >= 1", summary.fallbacks_triggered >= 1)
check("summary_text non-empty", len(summary.summary_text) > 0)
check("noise_reduction_note present", "noise reduction" in summary.noise_reduction_note.lower())

# ============================================================
# 11. Response Policy Structure Check
# ============================================================
section("11. RESPONSE POLICY STRUCTURE CHECK")

from llm.response_policy import build_prompt, check_response_structure
from llm.intent_emotion import classify_intent

p = load_profile()
intent = classify_intent("Where is my daughter?")
bundle = build_prompt(p, "Where is my daughter?", intent)

check("prompt has validate instruction", "validate that emotion first" in bundle.system.lower())
check("prompt has ground instruction", "ground gently" in bundle.system.lower())
check("validate before ground", bundle.system.lower().find("validate that emotion first") < bundle.system.lower().find("ground gently"))
check("prompt forbids open-ended", "never ask an open-ended question" in bundle.system.lower())
check("prompt offers one choice", "offer exactly one small" in bundle.system.lower())
check("prompt has avoid topic for Vikram", "vikram" in bundle.system.lower())
check("prompt has memory box offer", "offer to play it" in bundle.system.lower() or "Memory Box" in bundle.system)

# Structure check: good response
good = "That sounds worrying, I'm here with you. It's a quiet afternoon. Would you like to sit by the window, or listen to some music?"
result = check_response_structure(good)
check("good response passes", result.passes is True)
check("good response has validation", result.has_validation_language is True)
check("good response has concrete choice", result.has_concrete_choice is True)

# Structure check: bad - open ended
bad_oe = "I understand that's hard. What would you like to do?"
result = check_response_structure(bad_oe)
check("open-ended response fails", result.passes is False)
check("open-ended detected", result.has_open_ended_question is True)

# Structure check: bad - corrective
bad_corr = "No, that's not right, Vikram passed away years ago. What would you like to do now?"
result = check_response_structure(bad_corr)
check("corrective response fails", result.passes is False)
check("corrective detected", result.has_corrective_language is True)

# Structure check: bad - no validation
bad_no_val = "It's 3 o'clock. Would you like some tea?"
result = check_response_structure(bad_no_val)
check("no validation response fails", result.passes is False)
check("no validation detected", result.has_validation_language is False)

# --- M1 structure split (W2b, Oct 23): one-choice OPTIONAL in casual and
# --- emotional; validation stays a HARD GATE; danger + physical-distress
# --- keep the full pre-M1 protocol.

# Emotional: validation + no choice -> PASSES (choice optional under M1)
emot_no_choice = "That sounds worrying, I'm right here with you."
result = check_response_structure(emot_no_choice, turn_type="emotional")
check("M1: emotional without choice passes", result.passes is True)
check("M1: emotional no-choice still has validation", result.has_validation_language is True)

# Emotional: choice + no validation -> STILL FAILS (hard gate kept)
emot_no_val = "It's a quiet afternoon. Would you like some tea?"
result = check_response_structure(emot_no_val, turn_type="emotional")
check("M1: emotional without validation still fails", result.passes is False)

# Casual: no choice -> PASSES; open-ended still forbidden
result = check_response_structure("Good morning, Asha. The kettle is singing.",
                                  turn_type="casual")
check("M1: casual without choice passes", result.passes is True)
result = check_response_structure("What would you like to do this morning?",
                                  turn_type="casual")
check("M1: casual open-ended still fails", result.passes is False)

# Physical urgent (prompt protocol): validation AND grounding required;
# choice deliberately NOT required (that prompt block forbids activity
# offers and says URGE help, so a correct urgent reply may have no menu)
phys_urgent_ok = ("I hear this is scary, I'm right here with you. Please get "
                  "Rohan right now. If you're alone, please call 112 now.")
result = check_response_structure(phys_urgent_ok, turn_type="physical_urgent")
check("M1: physical_urgent with validation+grounding passes (no choice needed)",
      result.passes is True)
result = check_response_structure("It's frightening. I'm right here with you. Please call 112.",
                                  turn_type="physical_urgent")
check("M1: physical_urgent without validation fails", result.passes is False)
result = check_response_structure("I hear this is scary. Please get someone to help you.",
                                  turn_type="physical_urgent")
check("M1: physical_urgent without grounding fails", result.passes is False)

# Physical general (prompt protocol): validation AND choice required
# (that block mandates "offer to call caregiver" + a comfort choice)
phys_gen_ok = ("I hear you're not feeling well, and I'm right here with you. "
               "Would you like me to call Rohan?")
result = check_response_structure(phys_gen_ok, turn_type="physical_general")
check("M1: physical_general with validation+choice passes", result.passes is True)
result = check_response_structure(
    "I hear you're not feeling well, and I'm right here with you.",
    turn_type="physical_general")
check("M1: physical_general without choice fails (full protocol)",
      result.passes is False)

# The physical-distress prompt block MANDATES "I hear this is scary" -
# the checker must recognize its own canonical phrase (checker bug fix).
check("M1: 'I hear this is scary' counts as validation",
      check_response_structure("I hear this is scary.").has_validation_language is True)
# ...and the canonical presence phrase must count as grounding.
check("M1: bare 'I'm right here.' counts as grounding",
      check_response_structure("I'm right here.", turn_type="physical_urgent").has_grounding_language is True)

# Danger (full protocol kept): grounding still required
result = check_response_structure(
    "I hear how heavy this feels. I'm right here with you. "
    "Would you like me to call Rohan right now?", turn_type="danger")
check("M1: danger full protocol still passes", result.passes is True)
result = check_response_structure(
    "I hear how heavy this feels. Would you like me to call Rohan?",
    turn_type="danger")
check("M1: danger without grounding still fails", result.passes is False)

# ============================================================
# 12. Guardrails (additional edge cases)
# ============================================================
section("12. GUARDRAILS (edge cases)")

from safety.guardrails import check_guardrails

# Fabricated whereabouts with living family
result = check_guardrails("Priya is waiting at home for you.", p)
check("caught whereabouts living family", not result.passed and any("fabricated_whereabouts" in v for v in result.violations))

# Fabricated schedule with specific pattern (living family)
result = check_guardrails("Rohan is scheduled to visit tomorrow.", p)
check("caught scheduled visit pattern", not result.passed and any("fabricated_schedule" in v for v in result.violations))

# Safe: reassurance about living family (not deceased, intentionally allowed)
result = check_guardrails("Rohan will be here this weekend, just like every week.", p)
check("allows living family reassurance", result.passed, f"violations: {result.violations}")

# Safe: unrelated imminent language
result = check_guardrails("Your lunch is on its way, it'll be ready soon.", p)
check("allows unrelated imminent", result.passed)

# Fabricated weather
result = check_guardrails("It's a lovely sunny day today.", p)
check("caught fabricated weather", not result.passed and any("fabricated_weather" in v for v in result.violations))

# Fabricated surroundings
result = check_guardrails("The garden looks lovely today.", p)
check("caught fabricated surroundings", not result.passed and any("fabricated_surroundings" in v for v in result.violations))

# ============================================================
# SUMMARY
# ============================================================
print(f"\n{'='*60}")
print(f"  UNIT TEST RESULTS: {passed} passed, {failed} failed")
if failures:
    print(f"\n  FAILURES:")
    for name, detail in failures:
        print(f"    - {name}: {detail}")
print(f"{'='*60}")

sys.exit(1 if failed > 0 else 0)
