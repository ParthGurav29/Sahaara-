"""
backend/tests/test_consent.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from safety.event_log import EventLog
from safety.consent import ConsentManager, ConsentState
import time

def test_consent_accept():
    log = EventLog("test")
    manager = ConsentManager(log, timeout_seconds=10.0)
    
    manager.ask_consent("call rohan")
    assert manager.state == ConsentState.PENDING
    
    state = manager.interpret_response("Yes, please call him.")
    assert state == ConsentState.ACCEPTED
    
    events = log.events_of_type("consent_answered")
    assert len(events) == 1
    assert events[0].detail.get("accepted") is True

def test_consent_decline():
    log = EventLog("test")
    manager = ConsentManager(log, timeout_seconds=10.0)
    
    manager.ask_consent("call rohan")
    assert manager.state == ConsentState.PENDING
    
    state = manager.interpret_response("No, don't do that.")
    assert state == ConsentState.DECLINED
    
    events = log.events_of_type("consent_answered")
    assert len(events) == 1
    assert events[0].detail.get("accepted") is False

def test_consent_timeout():
    log = EventLog("test")
    # Small timeout for test
    manager = ConsentManager(log, timeout_seconds=0.1)
    
    manager.ask_consent("call rohan")
    assert manager.state == ConsentState.PENDING
    
    time.sleep(0.15)
    
    state = manager.check_timeout()
    assert state == ConsentState.TIMED_OUT
    
    events = log.events_of_type("consent_answered")
    assert len(events) == 1
    assert events[0].detail.get("timed_out") is True

def test_ambiguous_response_leaves_pending():
    log = EventLog("test")
    manager = ConsentManager(log, timeout_seconds=10.0)
    
    manager.ask_consent("call rohan")
    
    # "I want an apple" is neither accept nor decline
    state = manager.interpret_response("I want an apple")
    assert state == ConsentState.PENDING
    
    # No answer logged yet
    events = log.events_of_type("consent_answered")
    assert len(events) == 0

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

