"""
backend/tests/test_offer_rate.py

Verifies the offer_rate (FREQUENCY) metric added to the harness, and pins
down the distinction between it and top_offer_share (REPETITION).

Run directly (pytest is NOT installed in this project):
    cd backend && .venv/bin/python tests/test_offer_rate.py

Why this test exists: the original offer classifier only recognised a fixed
menu of offers (sit by the window, hear music, call her, ...). A generic
"would you like some chai?" matched nothing and was counted as "no offer",
which would have badly UNDERSTATED offer_rate — the very metric M1 gates on.
offer_rate therefore uses a BROAD detector, and this test proves both that
unlisted offers count AND that the old repetition metric is unchanged.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.harness import extract_offers_and_openers

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
    print(f"\n{'='*60}\n  {title}\n{'='*60}")


# ---------------------------------------------------------------- 1. all offers
section("1. ALL REPLIES CONTAIN AN OFFER -> rate 1.0")

all_offers = [
    "Would you like to sit by the window?",
    "Shall we put on some music?",
    "Would you like some chai?",
    "How about we listen to that old song?",
    "Can I play something nice for you?",
    "Let's call Rohan together, alright?",
]
m = extract_offers_and_openers(all_offers)
check("offer_rate == 1.0", m["offer_rate"] == 1.0, f"got {m['offer_rate']}")
check("offer_count == 6", m["offer_count"] == 6, f"got {m['offer_count']}")
check("no_offer_count == 0", m["no_offer_count"] == 0, f"got {m['no_offer_count']}")
check("total_replies == 6", m["total_replies"] == 6, f"got {m['total_replies']}")

# ------------------------------------------------- 2. no replies offer anything
section("2. NO REPLIES OFFER ANYTHING -> rate 0.0")

no_offers = [
    "I'm right here with you, Asha.",
    "That does sound heavy. I'm so sorry.",
    "You're safe, and I'm not going anywhere.",
    "I hear how much you miss her.",
]
m = extract_offers_and_openers(no_offers)
check("offer_rate == 0.0", m["offer_rate"] == 0.0, f"got {m['offer_rate']}")
check("offer_count == 0", m["offer_count"] == 0, f"got {m['offer_count']}")
check("no_offer_count == 4", m["no_offer_count"] == 4, f"got {m['no_offer_count']}")

# ------------------------------------------------------ 3. known exact ratio
section("3. KNOWN MIXED RATIO -> exact rate (3 of 7 = 0.429)")

mixed = [
    "Would you like some music?",        # offer
    "I'm right here with you.",          # no offer
    "That sounds so hard.",              # no offer
    "Shall we sit together a while?",    # offer
    "You're safe with me.",              # no offer
    "Let's call Priya, shall we?",       # offer
    "I hear you, Asha.",                 # no offer
]
m = extract_offers_and_openers(mixed)
check("offer_rate == 0.429 (3/7)", m["offer_rate"] == 0.429, f"got {m['offer_rate']}")
check("offer_count == 3", m["offer_count"] == 3, f"got {m['offer_count']}")
check("no_offer_count == 4", m["no_offer_count"] == 4, f"got {m['no_offer_count']}")

# ------------------------------------- 4. THE GAP CASE: unlisted offer wording
section("4. UNLISTED OFFER WORDING STILL COUNTS (the understatement bug)")

# These match NO entry in the old specific-offer menu, but are unambiguously
# offers. Before offer_rate existed, all of these would have been filed as
# "other" (no offer).
unlisted = [
    "Would you like some chai?",
    "How about a nice cup of tea?",
    "Can I get you a blanket?",
    "Shall I read you that poem?",
]
m = extract_offers_and_openers(unlisted)
check("unlisted offers -> rate 1.0", m["offer_rate"] == 1.0, f"got {m['offer_rate']}")
check("unlisted offers -> count 4", m["offer_count"] == 4, f"got {m['offer_count']}")
# demonstrate the old classifier saw none of them (all filed as "other")
check("old specific classifier saw none (all 'other')",
      m["offer_counts"] == {"other": 4},
      f"got {m['offer_counts']}")

# ------------------------------ 5. repetition metric UNCHANGED by the addition
section("5. top_offer_share (REPETITION) BEHAVIOUR UNCHANGED")

# Two replies using the same specific offer, one with no offer.
rep_replies = [
    "Would you like to sit by the window?",   # specific: sit by the window
    "Would you like to sit by the window?",   # specific: sit by the window
    "I'm right here with you.",               # no offer
]
m = extract_offers_and_openers(rep_replies)
check("top_offer_share == 0.667 (2/3)", m["top_offer_share"] == 0.667, f"got {m['top_offer_share']}")
check("top_offer is 'sit by the window'", m["top_offer"][0] == "sit by the window", f"got {m['top_offer']}")
check("offer_rate == 0.667 (2 of 3 offered)", m["offer_rate"] == 0.667, f"got {m['offer_rate']}")

# Now the key semantic difference: as the model offers LESS, top_offer_share
# RISES (because "other" becomes top) while offer_rate FALLS. Prove it.
shrinking = [
    "I'm right here with you.",
    "That sounds so hard.",
    "You're safe with me.",
    "Would you like to sit by the window?",
]
m = extract_offers_and_openers(shrinking)
check("fewer offers -> offer_rate == 0.25", m["offer_rate"] == 0.25, f"got {m['offer_rate']}")
check("fewer offers -> top_offer_share == 0.75 ('other' dominates)",
      m["top_offer_share"] == 0.75, f"got {m['top_offer_share']}")
check("top_offer label is 'other' when most replies offer nothing",
      m["top_offer"][0] == "other", f"got {m['top_offer']}")

# ------------------------------------------------------------ 6. empty input
section("6. EDGE CASES")

m = extract_offers_and_openers([])
check("empty replies -> offer_rate == 0.0", m["offer_rate"] == 0.0, f"got {m['offer_rate']}")
check("empty replies -> total_replies == 0", m["total_replies"] == 0, f"got {m['total_replies']}")

# single reply
m = extract_offers_and_openers(["Would you like some chai?"])
check("single offer reply -> rate 1.0", m["offer_rate"] == 1.0, f"got {m['offer_rate']}")

m = extract_offers_and_openers(["I'm right here."])
check("single no-offer reply -> rate 0.0", m["offer_rate"] == 0.0, f"got {m['offer_rate']}")

# ------------------------------------------------------------------- SUMMARY
print(f"\n{'='*60}")
print(f"  OFFER_RATE TESTS: {passed} passed, {failed} failed")
if failures:
    print(f"\n  FAILURES:")
    for name, detail in failures:
        print(f"    - {name}: {detail}")
print(f"{'='*60}")

sys.exit(1 if failed > 0 else 0)
