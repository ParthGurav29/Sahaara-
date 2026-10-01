"""
backend/tests/test_alerts.py
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from memory.profile import load_profile
from safety.event_log import EventLog
from safety.escalation import TierDecision, EscalationTier
from dashboard.alerts import AlertBuilder

def test_alert_builder_generates_context():
    profile = load_profile()
    log = EventLog(profile.profile_id)
    builder = AlertBuilder(log, profile)
    
    # Fake some events
    log.record("turn", {"user_message": "Hello", "memory_box_offered": "Would you like..."})
    
    now = time.time()
    decision = TierDecision(
        tier=EscalationTier.ORANGE,
        previous_tier=EscalationTier.YELLOW,
        reason="Distress persisted past yellow hold period",
        changed=True,
        timestamp=now
    )
    
    alert = builder.build_on_tier_change(decision)
    
    assert alert is not None
    assert alert.tier == "orange"
    assert profile.preferred_address in alert.headline
    assert "Memory Box" in alert.sahaara_tried

def test_alert_builder_guardrails_pass():
    profile = load_profile()
    log = EventLog(profile.profile_id)
    builder = AlertBuilder(log, profile)
    
    now = time.time()
    decision = TierDecision(
        tier=EscalationTier.RED,
        previous_tier=EscalationTier.ORANGE,
        reason="Danger statement detected",
        changed=True,
        timestamp=now
    )
    
    alert = builder.build_on_tier_change(decision)
    
    assert alert is not None
    # We should not see "Notification regarding" generic fallback unless guardrail failed.
    assert "A danger pattern was detected" in alert.headline

def test_alert_ignores_unchanged_or_green():
    profile = load_profile()
    log = EventLog(profile.profile_id)
    builder = AlertBuilder(log, profile)
    
    now = time.time()
    
    # Unchanged
    decision_unchanged = TierDecision(
        tier=EscalationTier.YELLOW,
        previous_tier=EscalationTier.YELLOW,
        reason="Same",
        changed=False,
        timestamp=now
    )
    assert builder.build_on_tier_change(decision_unchanged) is None
    
    # Green change
    decision_green = TierDecision(
        tier=EscalationTier.GREEN,
        previous_tier=EscalationTier.YELLOW,
        reason="Calmed down",
        changed=True,
        timestamp=now
    )
    assert builder.build_on_tier_change(decision_green) is None

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

