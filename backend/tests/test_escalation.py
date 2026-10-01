"""
backend/tests/test_escalation.py
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from memory.session_notes import SessionNotes
from safety.event_log import EventLog
from safety.escalation import EscalationEngine, EscalationTier

def setup_engine(demo_time_scale: float = 1.0):
    session_id = "test_session"
    log = EventLog(session_id)
    notes = SessionNotes(session_id)
    engine = EscalationEngine(log, notes, demo_time_scale=demo_time_scale, min_hold_seconds=2.0, orange_hold_seconds=5.0)
    return log, notes, engine

def test_green_to_yellow_on_repeats():
    log, notes, engine = setup_engine()
    
    assert engine.current_tier == EscalationTier.GREEN
    
    # Repeat 1
    notes.record_turn("Where is Priya?")
    decision = engine.evaluate("Where is Priya?")
    assert decision.tier == EscalationTier.GREEN
    
    # Repeat 2
    notes.record_turn("Where is Priya?")
    decision = engine.evaluate("Where is Priya?")
    assert decision.tier == EscalationTier.GREEN
    
    # Repeat 3 - should trigger Yellow
    notes.record_turn("Where is Priya?")
    decision = engine.evaluate("Where is Priya?")
    assert decision.tier == EscalationTier.YELLOW
    assert decision.changed is True
    assert "repeated 3 times" in decision.reason.lower()

def test_green_to_yellow_to_orange_on_sustained_distress():
    log, notes, engine = setup_engine(demo_time_scale=10.0)
    # orange_hold_seconds is 5.0, so with scale 10.0 it's 0.5s
    
    notes.record_turn("Help me", emotion="anxious")
    notes.record_turn("I don't know", emotion="anxious")
    notes.record_turn("I'm lost", emotion="anxious")
    
    decision = engine.evaluate("I'm lost")
    assert decision.tier == EscalationTier.YELLOW
    assert decision.changed is True
    
    # Wait for scaled hold period (0.5s)
    time.sleep(0.6)
    
    notes.record_turn("Still lost", emotion="anxious")
    decision = engine.evaluate("Still lost")
    assert decision.tier == EscalationTier.ORANGE
    assert decision.changed is True
    assert "persisted" in decision.reason.lower()

def test_hysteresis_prevents_flicker():
    log, notes, engine = setup_engine(demo_time_scale=1.0) # min_hold_seconds is 2.0
    
    # Force yellow
    engine.force_tier(EscalationTier.YELLOW, "Forced")
    
    # Now evaluate without any yellow conditions (no distress, no repeats)
    notes.record_turn("I'm fine now", emotion="calm")
    notes.record_turn("All good", emotion="calm")
    notes.record_turn("Thanks", emotion="calm")
    
    # Should stay yellow because of min_hold_seconds
    decision = engine.evaluate("Thanks")
    assert decision.tier == EscalationTier.YELLOW
    assert decision.changed is False
    assert "hysteresis" in decision.reason.lower()
    
    # Wait out hysteresis
    time.sleep(2.1)
    
    decision = engine.evaluate("Thanks")
    assert decision.tier == EscalationTier.GREEN
    assert decision.changed is True

if __name__ == "__main__":
    import inspect
    current_module = sys.modules[__name__]
    test_fns = [obj for name, obj in inspect.getmembers(current_module)
                if name.startswith("test_") and callable(obj)]
    passed, failed = 0, 0
    for fn in test_fns:
        try:
            fn()
            print(f"PASS: {fn.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"FAIL: {fn.__name__} -> {e}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    if failed > 0:
        sys.exit(1)

