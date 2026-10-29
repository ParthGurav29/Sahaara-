"""
Task 2 — an urgent statement must never be read as an answer to a pending
consent question.

THE DEFECT (confirmed before the fix)
-------------------------------------
`conversation.py` handled the consent `PENDING` state *before* it called
`classify_intent()`. So while Sahaara was waiting for a yes/no about calling
the caregiver, an emergency was swallowed by the consent interpreter:

    "I can't breathe"                  -> pending    (never classified, never Red)
    "chest pain"                       -> pending
    "I fell and I cant get up"         -> pending
    "not able to breathe"              -> declined   (bare \\bnot\\b in DECLINE_PATTERNS)
    "Can you help me, I can't breathe" -> declined   (topic-shift heuristic)

Each of those silently drops the emergency. The first three never reach the
escalation engine at all; the last two are recorded as her *refusing* help.

THESE TESTS
-----------
A. urgent while a consent question is pending
B. urgent immediately after a consent question timed out

Plus the behaviour that must NOT change: an ordinary "no" still declines, an
ordinary "yes" still accepts, an ambiguous answer still re-asks, and a
non-urgent physical complaint never gets logged as a decline.

Scope note: there is no call system yet. These tests assert Red tier + the
dashboard alert only — no wording may imply anyone is being called.

Run directly: python tests/test_emergency_consent_order.py
"""

import sys

sys.path.insert(0, "/Users/parthmangeshgurav/Desktop/sahaara/backend")

import os
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

# Isolated DB: never touches the live database.
_TEST_DB = Path(tempfile.gettempdir()) / "sahaara_test_emergency_consent_order.db"
for _suffix in ("", "-wal", "-shm"):
    Path(str(_TEST_DB) + _suffix).unlink(missing_ok=True)
os.environ["SAHAARA_DB_PATH"] = str(_TEST_DB)
# No push during these tests — a live gate would try to talk to a push service.
os.environ["SAHAARA_PUSH_ENABLED"] = "false"

from database import init_db

init_db()

from safety.consent import ConsentState
from safety.escalation import EscalationTier
from events.event_log import EventType
import conversation

# ---------------------------------------------------------------------------
# Test double — the consent and emergency paths return before any real LLM use,
# but process_turn still builds a prompt for the urgent turn, so it needs one.
# ---------------------------------------------------------------------------


class FakeProvider:
    def complete_from_prompt_bundle(self, bundle, max_tokens=None):
        return SimpleNamespace(text="I'm right here with you.", model="fake")


conversation.provider = FakeProvider()

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


def _reset():
    """Clear per-session state without generating a summary (no LLM, no disk)."""
    s = conversation._session_manager.get_active()
    s.event_log.clear()
    s.session_notes.reset()
    s.escalation_engine.reset()
    s.consent_manager.reset()
    s.continuation_manager.reset()
    return s


def _ask_consent(s, contact_name: str = "Rohan") -> None:
    s.consent_manager.ask_consent(
        contact_name=contact_name,
        contact_relationship="son",
        tier=EscalationTier.YELLOW,
        question_text="Would you like me to call Rohan?",
    )
    assert s.consent_manager.state == ConsentState.PENDING


def _consent_outcomes(s) -> list:
    return [e.detail.get("outcome") for e in s.event_log.get_events(EventType.CONSENT_ANSWERED)]


# Consent-branch wording that must NOT be returned for an emergency turn.
_FORBIDDEN_RESPONSES = (
    "i won't call",            # the decline reply
    "would you like me to call them",  # the re-ask
    "i'll give you some time",  # the timeout reply
    "i've sent",               # consent-accepted push wording
)


def _assert_not_consent_branch(name: str, response: str) -> None:
    low = (response or "").lower()
    hit = next((p for p in _FORBIDDEN_RESPONSES if p in low), None)
    check(f"{name}: not answered with consent-branch wording", hit is None, f"got: {response!r}")


# ---------------------------------------------------------------------------
# A. urgent while a consent question is pending
# ---------------------------------------------------------------------------

PENDING_URGENT_CASES = [
    ("I can't breathe", "classic urgent"),
    ("chest pain", "urgent + general overlap"),
    ("I fell and I cant get up", "fall"),
    ("not able to breathe", "the bare-'not' false decline"),
    ("Can you help me, I can't breathe", "topic-shift false decline"),
]


def test_urgent_overrides_pending_consent():
    for phrase, label in PENDING_URGENT_CASES:
        print(f"\n[A] {label}: {phrase!r}")
        s = _reset()
        _ask_consent(s)

        result = conversation.process_turn(phrase)

        check(f"'{phrase}': tier is RED",
              result.get("meta", {}).get("tier") == "red",
              f"got tier={result.get('meta', {}).get('tier')!r} response={result.get('response')!r}")
        check(f"'{phrase}': consent logged superseded",
              _consent_outcomes(s) == ["superseded"],
              f"got outcomes={_consent_outcomes(s)!r}")
        check(f"'{phrase}': consent never logged declined",
              "declined" not in _consent_outcomes(s),
              f"got outcomes={_consent_outcomes(s)!r}")
        check(f"'{phrase}': consent state is IDLE, not DECLINED",
              s.consent_manager.state == ConsentState.IDLE,
              f"got state={s.consent_manager.state!r}")
        check(f"'{phrase}': PHYSICAL_SYMPTOM event logged",
              len(s.event_log.get_events(EventType.PHYSICAL_SYMPTOM)) >= 1,
              "no physical_symptom event")
        _assert_not_consent_branch(phrase, result.get("response", ""))
        check(f"'{phrase}': no claim that anyone was called",
              "contact_called" not in result.get("meta", {})
              or not result.get("meta", {}).get("contact_called"),
              f"got meta={result.get('meta')!r}")


def test_danger_overrides_pending_consent():
    print("\n[A] danger statement while consent pending")
    s = _reset()
    _ask_consent(s)

    result = conversation.process_turn("I want to die")

    check("danger: tier is RED",
          result.get("meta", {}).get("tier") == "red",
          f"got tier={result.get('meta', {}).get('tier')!r}")
    check("danger: consent logged superseded",
          _consent_outcomes(s) == ["superseded"],
          f"got outcomes={_consent_outcomes(s)!r}")
    check("danger: consent never logged declined",
          "declined" not in _consent_outcomes(s),
          f"got outcomes={_consent_outcomes(s)!r}")
    check("danger: DANGER_STATEMENT event logged",
          len(s.event_log.get_events(EventType.DANGER_STATEMENT)) >= 1,
          "no danger_statement event")
    _assert_not_consent_branch("danger", result.get("response", ""))


# ---------------------------------------------------------------------------
# B. urgent immediately after a consent question timed out
# ---------------------------------------------------------------------------


def test_urgent_after_consent_timeout():
    print("\n[B] urgent right after a consent timeout")
    s = _reset()
    _ask_consent(s)
    # Force the pending question past its deadline (demo timeout is 0.1 s real).
    s.consent_manager._timeout_at = time.time() - 1.0

    result = conversation.process_turn("I can't breathe")

    check("after timeout: tier is RED",
          result.get("meta", {}).get("tier") == "red",
          f"got tier={result.get('meta', {}).get('tier')!r} response={result.get('response')!r}")
    check("after timeout: consent recorded as timeout, not declined",
          _consent_outcomes(s) == ["timeout"],
          f"got outcomes={_consent_outcomes(s)!r}")
    check("after timeout: consent state is not left TIMED_OUT",
          s.consent_manager.state == ConsentState.IDLE,
          f"got state={s.consent_manager.state!r}")
    check("after timeout: PHYSICAL_SYMPTOM event logged",
          len(s.event_log.get_events(EventType.PHYSICAL_SYMPTOM)) >= 1,
          "no physical_symptom event")
    _assert_not_consent_branch("after timeout", result.get("response", ""))


def test_urgent_twice_after_timeout():
    """A second emergency turn must still be classified after the first one."""
    print("\n[B] second urgent turn after a timeout")
    s = _reset()
    _ask_consent(s)
    s.consent_manager._timeout_at = time.time() - 1.0

    first = conversation.process_turn("I can't breathe")
    second = conversation.process_turn("I fell and I cant get up")

    check("first urgent turn: RED", first.get("meta", {}).get("tier") == "red",
          f"got {first.get('meta', {}).get('tier')!r}")
    check("second urgent turn: RED", second.get("meta", {}).get("tier") == "red",
          f"got {second.get('meta', {}).get('tier')!r}")
    check("second urgent turn: two PHYSICAL_SYMPTOM events",
          len(s.event_log.get_events(EventType.PHYSICAL_SYMPTOM)) >= 2,
          f"got {len(s.event_log.get_events(EventType.PHYSICAL_SYMPTOM))}")


# ---------------------------------------------------------------------------
# Behaviour that must NOT change
# ---------------------------------------------------------------------------


def test_decline_still_declines():
    print("\n[KEEP] an ordinary no still declines")
    s = _reset()
    _ask_consent(s)

    result = conversation.process_turn("no thank you")

    check("decline: consent_outcome declined",
          result.get("meta", {}).get("consent_outcome") == "declined",
          f"got {result.get('meta')!r}")
    check("decline: logged as declined",
          _consent_outcomes(s) == ["declined"],
          f"got outcomes={_consent_outcomes(s)!r}")
    check("decline: never logged superseded",
          "superseded" not in _consent_outcomes(s),
          f"got outcomes={_consent_outcomes(s)!r}")


def test_accept_still_accepts():
    print("\n[KEEP] an ordinary yes still accepts")
    s = _reset()
    _ask_consent(s)

    result = conversation.process_turn("yes please")

    check("accept: consent_outcome accepted",
          result.get("meta", {}).get("consent_outcome") == "accepted",
          f"got {result.get('meta')!r}")
    check("accept: logged as accepted",
          _consent_outcomes(s) == ["accepted"],
          f"got outcomes={_consent_outcomes(s)!r}")


def test_ambiguous_still_reasks():
    print("\n[KEEP] an ambiguous answer still re-asks")
    s = _reset()
    _ask_consent(s)

    result = conversation.process_turn("maybe")

    check("ambiguous: consent_pending true",
          result.get("meta", {}).get("consent_pending") is True,
          f"got {result.get('meta')!r}")
    check("ambiguous: state stays PENDING",
          s.consent_manager.state == ConsentState.PENDING,
          f"got {s.consent_manager.state!r}")


def test_negated_sentence_is_never_an_accept():
    """'I'm not sure' contains 'sure'. Narrowing the decline patterns made this
    reachable, so the accept check must refuse negated sentences outright —
    a false accept would tell the dashboard she agreed to a call."""
    print("\n[KEEP] a negated sentence is never read as accepting")
    for phrase in ("I'm not sure", "I am not ok"):
        s = _reset()
        _ask_consent(s)

        result = conversation.process_turn(phrase)

        check(f"{phrase!r}: consent_pending true (re-asks)",
              result.get("meta", {}).get("consent_pending") is True,
              f"got {result.get('meta')!r}")
        check(f"{phrase!r}: never logged accepted",
              "accepted" not in _consent_outcomes(s),
              f"got outcomes={_consent_outcomes(s)!r}")
        check(f"{phrase!r}: never logged declined",
              "declined" not in _consent_outcomes(s),
              f"got outcomes={_consent_outcomes(s)!r}")


def test_physical_general_is_not_a_decline():
    """'I'm not feeling well' is not an answer to the question, but it is
    certainly not a refusal either. The bare \\bnot\\b pattern used to log it
    as a decline."""
    print("\n[KEEP] a non-urgent physical complaint is never logged as a decline")
    s = _reset()
    _ask_consent(s)

    result = conversation.process_turn("I am not feeling well")

    check("general: never logged as declined",
          "declined" not in _consent_outcomes(s),
          f"got outcomes={_consent_outcomes(s)!r}")
    check("general: not the decline reply",
          "i won't call" not in (result.get("response") or "").lower(),
          f"got {result.get('response')!r}")


def test_no_call_system_yet():
    """The call system does not exist. No emergency turn may imply otherwise."""
    print("\n[KEEP] no wording implies a call is being placed")
    s = _reset()
    _ask_consent(s)

    result = conversation.process_turn("I can't breathe")
    response = (result.get("response") or "").lower()

    for forbidden in ("i'm calling", "i am calling", "i've called", "i have called",
                      "dialling", "dialing", "on the line", "phone is ringing"):
        check(f"no {forbidden!r} in the emergency reply",
              forbidden not in response,
              f"got {result.get('response')!r}")
    check("no consent_outcome on an emergency turn",
          "consent_outcome" not in result.get("meta", {}),
          f"got {result.get('meta')!r}")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> int:
    print("=" * 74)
    print("  Task 2 — urgent statements must beat a pending consent question")
    print("=" * 74)

    test_urgent_overrides_pending_consent()
    test_danger_overrides_pending_consent()
    test_urgent_after_consent_timeout()
    test_urgent_twice_after_timeout()
    test_decline_still_declines()
    test_accept_still_accepts()
    test_ambiguous_still_reasks()
    test_negated_sentence_is_never_an_accept()
    test_physical_general_is_not_a_decline()
    test_no_call_system_yet()

    print("\n" + "=" * 74)
    print(f"  RESULTS: {passed} passed, {failed} failed")
    print("=" * 74)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
