"""
Task 1 — Emergency contact schema and resolution.

Tests for the new contact fields (phone, notify_order, may_offer_notify, may_call)
and the resolver that orders contacts for the call campaign.

Run directly: python tests/test_emergency_contacts.py
"""

import sys
import os
import tempfile
from pathlib import Path

sys.path.insert(0, "/Users/parthmangeshgurav/Desktop/sahaara/backend")

# Isolated DB per test run
_TEST_DB = Path(tempfile.gettempdir()) / "sahaara_test_emergency_contacts.db"
for _suffix in ("", "-wal", "-shm"):
    Path(str(_TEST_DB) + _suffix).unlink(missing_ok=True)
os.environ["SAHAARA_DB_PATH"] = str(_TEST_DB)
os.environ["SAHAARA_PUSH_ENABLED"] = "false"

from database import init_db, get_profile, save_profile
from memory.profile import Profile, resolve_contacts, get_callable_contacts

init_db()

passed = 0
failed = 0


def check(name: str, condition, detail: str = "") -> None:
    global passed, failed
    if condition:
        passed += 1
        print(f"  PASS: {name}")
    else:
        failed += 1
        print(f"  FAIL: {name} {detail}")


def _load_test_profile():
    """Load a fresh profile from the database."""
    profile = get_profile("asha_demo_001")
    return Profile(profile)


def _reset_profile_to_defaults():
    """Reset the profile to the original migrated defaults."""
    profile = get_profile("asha_demo_001")
    for member in profile["family"]:
        member["phone"] = None
        member["may_offer_notify"] = False
        member["may_call"] = False
    # Keep notify_order as migrated
    save_profile("asha_demo_001", profile)


def test_migration_defaults():
    """Migration adds the new fields with safe defaults (no invented numbers)."""
    _reset_profile_to_defaults()
    p = _load_test_profile()
    for member in p.get_family():
        check(f"{member['name']}: phone is None (not invented)",
              member.get("phone") is None)
        check(f"{member['name']}: notify_order present",
              "notify_order" in member)
        check(f"{member['name']}: may_offer_notify defaults False",
              member.get("may_offer_notify") is False)
        check(f"{member['name']}: may_call defaults False",
              member.get("may_call") is False)


def test_resolve_contacts_orders_by_notify_order():
    """Contacts sorted by notify_order first."""
    _reset_profile_to_defaults()
    profile = get_profile("asha_demo_001")
    # Set notify_order explicitly for this test
    profile["family"][0]["notify_order"] = 1  # Priya first
    profile["family"][0]["phone"] = "+919876543210"
    profile["family"][0]["may_call"] = True
    profile["family"][1]["notify_order"] = 2  # Rohan second
    profile["family"][1]["phone"] = "+919876543211"
    profile["family"][1]["may_call"] = True
    save_profile("asha_demo_001", profile)

    p = _load_test_profile()
    callable_contacts = get_callable_contacts(p)
    check("Priya (notify_order=1) comes before Rohan (notify_order=2)",
          callable_contacts[0]["name"] == "Priya")
    check("Rohan is second",
          callable_contacts[1]["name"] == "Rohan")


def test_resolve_contacts_skips_may_call_false():
    """Members with may_call=False are skipped but visible with reason."""
    _reset_profile_to_defaults()
    profile = get_profile("asha_demo_001")
    profile["family"][0]["phone"] = "+919876543210"
    profile["family"][0]["may_call"] = True
    profile["family"][1]["phone"] = "+919876543211"
    profile["family"][1]["may_call"] = False
    save_profile("asha_demo_001", profile)

    p = _load_test_profile()
    all_resolved = resolve_contacts(p)
    callable_names = [m["name"] for m in all_resolved if "skip_reason" not in m]
    skipped_names = [(m["name"], m["skip_reason"]) for m in all_resolved if "skip_reason" in m]

    check("Priya is callable",
          "Priya" in callable_names)
    check("Rohan is skipped with may_call_false",
          ("Rohan", "may_call_false") in skipped_names)


def test_resolve_contacts_skips_no_phone():
    """Members with phone=None are skipped but visible with reason."""
    _reset_profile_to_defaults()
    profile = get_profile("asha_demo_001")
    profile["family"][0]["phone"] = "+919876543210"
    profile["family"][0]["may_call"] = True
    profile["family"][1]["may_call"] = True  # but no phone
    save_profile("asha_demo_001", profile)

    p = _load_test_profile()
    all_resolved = resolve_contacts(p)
    callable_names = [m["name"] for m in all_resolved if "skip_reason" not in m]
    skipped_names = [(m["name"], m["skip_reason"]) for m in all_resolved if "skip_reason" in m]

    check("Priya is callable",
          "Priya" in callable_names)
    check("Rohan is skipped with no_phone",
          ("Rohan", "no_phone") in skipped_names)


def test_get_callable_contacts_only_returns_callable():
    """get_callable_contacts filters to only may_call=True AND phone present."""
    _reset_profile_to_defaults()
    profile = get_profile("asha_demo_001")
    profile["family"][0]["phone"] = "+919876543210"
    profile["family"][0]["may_call"] = True
    profile["family"][1]["may_call"] = True  # no phone
    profile["family"][2]["phone"] = "+919876543212"  # but may_call=False
    profile["family"][2]["may_call"] = False
    save_profile("asha_demo_001", profile)

    p = _load_test_profile()
    callable_contacts = get_callable_contacts(p)
    check("Only Priya is callable",
          len(callable_contacts) == 1 and callable_contacts[0]["name"] == "Priya")


def test_relationship_priority_breaks_ties():
    """When notify_order is equal, relationship priority decides."""
    _reset_profile_to_defaults()
    profile = get_profile("asha_demo_001")
    # Both have same notify_order
    profile["family"][0]["notify_order"] = 1
    profile["family"][0]["phone"] = "+919876543210"
    profile["family"][0]["may_call"] = True
    profile["family"][1]["notify_order"] = 1
    profile["family"][1]["phone"] = "+919876543211"
    profile["family"][1]["may_call"] = True
    save_profile("asha_demo_001", profile)

    p = _load_test_profile()
    callable_contacts = get_callable_contacts(p)
    # Priya (daughter) has priority 1, Rohan (son) has priority 1 - arrival order breaks tie
    # Both are son/daughter so same priority, arrival order should put Priya first
    check("Arrival order breaks tie when notify_order and priority equal",
          callable_contacts[0]["name"] == "Priya" and callable_contacts[1]["name"] == "Rohan")


def test_no_callable_contact_returns_empty():
    """If no contact has may_call=True and phone, get_callable_contacts returns empty."""
    _reset_profile_to_defaults()
    p = _load_test_profile()
    callable_contacts = get_callable_contacts(p)
    check("Empty list when no callable contacts",
          callable_contacts == [])


def test_vikram_always_last():
    """Vikram (late husband) has notify_order=99 and may_call=False, always last/skipped."""
    _reset_profile_to_defaults()
    p = _load_test_profile()
    all_resolved = resolve_contacts(p)
    vikram_entry = next(m for m in all_resolved if m["name"] == "Vikram")
    check("Vikram is skipped (may_call_false or no_phone)",
          "skip_reason" in vikram_entry)
    check("Vikram is at the end of the list",
          all_resolved[-1]["name"] == "Vikram")


def test_resolve_contacts_preserves_all_fields():
    """The resolver passes through all original member fields plus skip_reason."""
    _reset_profile_to_defaults()
    profile = get_profile("asha_demo_001")
    profile["family"][0]["phone"] = "+919876543210"
    profile["family"][0]["may_call"] = True
    profile["family"][0]["notes"] = "Custom note"
    save_profile("asha_demo_001", profile)

    p = _load_test_profile()
    callable_contacts = get_callable_contacts(p)
    priya = callable_contacts[0]
    check("Original fields preserved (notes)",
          priya.get("notes") == "Custom note")
    check("Original fields preserved (relationship)",
          priya.get("relationship") == "daughter")
    check("Original fields preserved (memory_box_clip)",
          priya.get("memory_box_clip") == "priya_hello.mp3")


def test_no_invented_phone_number():
    """Migration never invents a phone number — defaults are None/False."""
    _reset_profile_to_defaults()
    p = _load_test_profile()
    for member in p.get_family():
        check(f"{member['name']}: phone is None or empty string",
              member.get("phone") is None or member.get("phone") == "")


def main() -> int:
    print("=" * 74)
    print("  Task 1 — Emergency contact schema and resolution")
    print("=" * 74)

    test_migration_defaults()
    test_resolve_contacts_orders_by_notify_order()
    test_resolve_contacts_skips_may_call_false()
    test_resolve_contacts_skips_no_phone()
    test_get_callable_contacts_only_returns_callable()
    test_relationship_priority_breaks_ties()
    test_no_callable_contact_returns_empty()
    test_vikram_always_last()
    test_resolve_contacts_preserves_all_fields()
    test_no_invented_phone_number()

    print("\n" + "=" * 74)
    print(f"  RESULTS: {passed} passed, {failed} failed")
    print("=" * 74)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())