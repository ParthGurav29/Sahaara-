"""
test_wording_pools.py

Tests for the wording pool guardrail validation.
All lines in WORDING_POOLS must pass check_guardrails() before use.
"""
import sys
sys.path.insert(0, "/Users/parthmangeshgurav/Desktop/sahaara/backend")

from scheduling.models import WORDING_POOLS, get_wording_pool
from safety.guardrails import check_guardrails


def test_all_wording_pools_pass_guardrails():
    """Every line in every wording pool must pass guardrails."""
    violations = []
    
    for pool_key, lines in WORDING_POOLS.items():
        for line in lines:
            if not line.get("draft", True):
                continue
            
            text = line["text"]
            result = check_guardrails(text)
            
            if result.violations:
                violations.append({
                    "pool": pool_key,
                    "wording_id": line["id"],
                    "text": text,
                    "violations": result.violations
                })
    
    if violations:
        print("GUARDRAIL VIOLATIONS FOUND:")
        for v in violations:
            print(f"  Pool: {v['pool']}, ID: {v['wording_id']}")
            print(f"  Text: {v['text']}")
            print(f"  Violations: {v['violations']}")
        assert False, f"Found {len(violations)} guardrail violations in wording pools"
    
    print(f"All {sum(len(lines) for lines in WORDING_POOLS.values())} wording pool lines pass guardrails.")


def test_wording_pools_exist():
    """Test that all required pools exist and have content."""
    required_pools = [
        "intro",
        "morning_greeting",
        "daytime_checkin",
        "evening_checkin",
        "medication",
        "meal",
        "retry",
    ]
    
    for pool in required_pools:
        assert pool in WORDING_POOLS, f"Missing required pool: {pool}"
        lines = WORDING_POOLS[pool]
        assert len(lines) >= 4, f"Pool {pool} should have at least 4 lines, got {len(lines)}"
        
        # Each line should have required fields
        for line in lines:
            assert "id" in line, f"Line missing id: {line}"
            assert "text" in line, f"Line missing text: {line}"
            assert "draft" in line, f"Line missing draft flag: {line}"
            assert line["draft"] is True, f"Line not marked draft: {line}"
    
    print("All required wording pools exist with proper structure.")


def test_get_wording_pool():
    """Test the get_wording_pool helper."""
    morning = get_wording_pool("morning_greeting")
    assert len(morning) == 8
    assert morning[0]["id"] == "mg_1"
    
    missing = get_wording_pool("nonexistent")
    assert missing == []
    
    print("get_wording_pool helper works correctly.")


def test_no_duplicate_ids():
    """Ensure no duplicate wording IDs across pools."""
    all_ids = []
    for pool_key, lines in WORDING_POOLS.items():
        for line in lines:
            all_ids.append((pool_key, line["id"]))
    
    unique_ids = set(all_ids)
    assert len(all_ids) == len(unique_ids), "Found duplicate wording IDs"
    print("No duplicate wording IDs across pools.")


if __name__ == "__main__":
    print("Testing wording pools...")
    test_wording_pools_exist()
    test_get_wording_pool()
    test_no_duplicate_ids()
    test_all_wording_pools_pass_guardrails()
    print("\nAll wording pool tests passed!")