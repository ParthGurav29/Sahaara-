"""
test_emergency_dashboard.py

Emergency Call Task 4 — the caregiver dashboard surface.

Covers the two REST routes in `emergency_routes.py` plus the orchestrator
behaviour the dashboard depends on being honest about:

  * history is scoped to the caregiver's own profile
  * only masked numbers ever leave the server
  * `simulated` is a real boolean, not a SQLite 0/1
  * cancel is idempotent and cannot overwrite a finished incident
  * with calling switched off the incident ends `simulated` with the gate that
    refused it — and its attempt says `simulated_not_placed`, never "dialing"
    forever and never a fabricated "answered"

Run directly: python tests/test_emergency_dashboard.py
"""

import sys
sys.path.insert(0, "/Users/parthmangeshgurav/Desktop/sahaara/backend")

import os
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

_TEST_DB = Path(tempfile.gettempdir()) / f"sahaara_test_emergency_dashboard_{os.getpid()}.db"
for _suffix in ("", "-wal", "-shm"):
    Path(str(_TEST_DB) + _suffix).unlink(missing_ok=True)
os.environ["SAHAARA_DB_PATH"] = str(_TEST_DB)
os.environ["SAHAARA_PUSH_ENABLED"] = "false"
os.environ["EMERGENCY_CALLS_ENABLED"] = "true"
os.environ["SAHAARA_EMERGENCY_PROVIDER"] = "simulated"
os.environ["SAHAARA_EMERGENCY_COOLDOWN_SECONDS"] = "0"
os.environ["SAHAARA_EMERGENCY_MAX_ATTEMPTS"] = "3"
os.environ["SAHAARA_EMERGENCY_ACK_SECONDS"] = "0.1"

os.environ["SAHAARA_EMERGENCY_RETRY_DELAY_SECONDS"] = "0.05"


from fastapi import FastAPI
from fastapi.testclient import TestClient

import database
database.init_db()

from auth_routes import require_caregiver
from emergency_routes import router as emergency_router

PROFILE = "asha_demo_001"
OTHER = "other_profile_777"
FULL_PHONE = "+919876543210"

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


def _client(profile_id: str = PROFILE) -> TestClient:
    app = FastAPI()
    app.include_router(emergency_router)
    app.dependency_overrides[require_caregiver] = lambda: {
        "user_id": 1,
        "username": "caregiver",
        "role": "caregiver",
        "profile_id": profile_id,
    }
    return TestClient(app)


def _wipe() -> None:
    conn = database.get_connection()
    try:
        conn.execute("DELETE FROM emergency_attempts")
        conn.execute("DELETE FROM emergency_incidents")
        conn.commit()
    finally:
        conn.close()


def _incident(incident_id: str, profile_id: str = PROFILE, simulated: bool = True) -> None:
    database.create_emergency_incident(
        incident_id=incident_id,
        profile_id=profile_id,
        trigger="physical_urgent",
        trigger_phrase="I can't breathe",
        tier="red",
        simulated=simulated,
        provider="simulated" if simulated else "vonage",
    )


def _attempt(attempt_id: str, incident_id: str, status: str = "ringing") -> None:
    database.create_emergency_attempt(
        attempt_id=attempt_id,
        incident_id=incident_id,
        contact_order=0,
        contact_name="Rohan",
        contact_rel="son",
        phone_masked=database.mask_phone_number(FULL_PHONE),
        attempt=1,
        status=status,
        simulated=True,
    )


# ---------------------------------------------------------------------------
# 1. Empty history
# ---------------------------------------------------------------------------
print("\n[1] Empty history")
_wipe()
r = _client().get("/emergency/incidents")
check("200 with no incidents", r.status_code == 200 and r.json()["incidents"] == [])

# ---------------------------------------------------------------------------
# 2. Masked numbers only
# ---------------------------------------------------------------------------
print("\n[2] Phone numbers are masked")
_incident("emg_mask_1")
_attempt("emg_mask_1_a1", "emg_mask_1")
body = _client().get("/emergency/incidents").text
check("full number never appears", FULL_PHONE not in body, body[:200])
check("last four digits are shown", "3210" in body)
parsed = _client().get("/emergency/incidents").json()["incidents"][0]
check("attempt carries phone_masked", parsed["attempts_detail"][0]["phone_masked"] == "+91 **** 3210")
check("attempt has no phone field", "phone" not in parsed["attempts_detail"][0])
check("incident has no phone field", "phone" not in parsed)

# ---------------------------------------------------------------------------
# 3. Booleans are real booleans
# ---------------------------------------------------------------------------
print("\n[3] simulated is a boolean")
check("incident simulated is bool", isinstance(parsed["simulated"], bool))
check("incident simulated is True", parsed["simulated"] is True)
check("attempt simulated is bool", isinstance(parsed["attempts_detail"][0]["simulated"], bool))

# ---------------------------------------------------------------------------
# 4. Scoped to the caregiver's profile
# ---------------------------------------------------------------------------
print("\n[4] Per-profile scoping")
_wipe()
_incident("emg_mine", PROFILE)
_incident("emg_theirs", OTHER)
mine = _client(PROFILE).get("/emergency/incidents").json()["incidents"]
theirs = _client(OTHER).get("/emergency/incidents").json()["incidents"]
check("own incident visible", [i["id"] for i in mine] == ["emg_mine"])
check("other profile's incident not leaked", [i["id"] for i in theirs] == ["emg_theirs"])

# ---------------------------------------------------------------------------
# 5/6. Cancel: unknown and cross-profile
# ---------------------------------------------------------------------------
print("\n[5] Cancel refuses unknown ids")
check("unknown id -> 404", _client().post("/emergency/incidents/nope/cancel").status_code == 404)

print("\n[6] Cancel refuses another profile's incident")
r = _client(PROFILE).post("/emergency/incidents/emg_theirs/cancel")
check("cross-profile -> 404", r.status_code == 404)
check("cross-profile incident untouched", database.get_emergency_incident("emg_theirs")["status"] == "triggered")

# ---------------------------------------------------------------------------
# 7. Cancel an active incident
# ---------------------------------------------------------------------------
print("\n[7] Cancel an active incident")
_wipe()
_incident("emg_active")
_attempt("emg_active_a1", "emg_active")
r = _client().post("/emergency/incidents/emg_active/cancel")
check("cancel returns 200", r.status_code == 200)
check("cancel reports ok", r.json()["status"] == "ok")
row = database.get_emergency_incident("emg_active")
check("incident marked cancelled", row["status"] == "cancelled")
check("cancel is recorded with a reason", bool(row["reason"]))
# One vocabulary for one event: the orchestrator also writes `caregiver_cancelled`.
# Two spellings broke the dashboard's plain-language reason map once already.
check("cancel reason matches the orchestrator's", row["reason"] == "caregiver_cancelled")
check("ended_at is set", row["ended_at"] is not None)

# ---------------------------------------------------------------------------
# 8. Cancel never rewrites a finished incident
# ---------------------------------------------------------------------------
print("\n[8] Cancel cannot overwrite a finished incident")
database.update_emergency_incident_status("emg_active", "acknowledged", reason="acknowledged", ended_at=time.time())
_client().post("/emergency/incidents/emg_active/cancel")
row = database.get_emergency_incident("emg_active")
check("acknowledged incident stays acknowledged", row["status"] == "acknowledged")

# ---------------------------------------------------------------------------
# 9. Calling switched off ends the incident honestly
# ---------------------------------------------------------------------------
print("\n[9] Calling switched off ends simulated, not answered")
_wipe()
import telephony.orchestrator as orch_mod
from events.event_log import EventLog, EventType
from memory.profile import Profile

os.environ["EMERGENCY_CALLS_ENABLED"] = "false"
database.save_profile(PROFILE, {
    **database.get_profile(PROFILE),
    "family": [{
        "name": "Rohan", "relationship": "son", "notes": "",
        "phone": FULL_PHONE, "notify_order": 1,
        "may_offer_notify": True, "may_call": True,
    }],
})

log = EventLog(PROFILE)
profile = Profile(database.get_profile(PROFILE))
incident_id = orch_mod.trigger_emergency_call(
    profile_id=PROFILE,
    trigger="physical_urgent",
    trigger_phrase="I can't breathe",
    tier="red",
    event_log=log,
    profile=profile,
)

deadline = time.time() + 10
while time.time() < deadline:
    row = database.get_emergency_incident(incident_id)
    if row and row["status"] == "simulated":
        break
    time.sleep(0.05)

row = database.get_emergency_incident(incident_id)
check("incident ended simulated, not failed", row["status"] == "simulated", row["status"])
# The gate that refused the real call is stored on the incident. Until the
# factory names it explicitly this is the generic marker.
check("reason names a gate", bool(row["reason"]), row["reason"])
attempts = database.get_emergency_attempts(incident_id)
check("one attempt recorded", len(attempts) == 1, str(len(attempts)))
check("attempt is simulated_not_placed",
      attempts[0]["status"] == "simulated_not_placed", attempts[0]["status"])
check("no answered_at timestamp", attempts[0]["answered_at"] is None)

ended = log.get_events(EventType.EMERGENCY_CALL_ENDED)
check("ENDED event logged", len(ended) == 1)
check("ENDED status is simulated", ended[0].detail.get("status") == "simulated")
check("ENDED reason matches the incident", ended[0].detail.get("reason") == row["reason"])

# ---------------------------------------------------------------------------
# 10. Dashboard vocabulary contract
# ---------------------------------------------------------------------------
print("\n[10] Status vocabulary the dashboard renders")
from telephony.orchestrator import AttemptStatus

known = {
    "dialing", "ringing", "answered", "acknowledged", "no_answer",
    "failed", "canceled", "cancelled", "queued", "simulated_not_placed",
}
check(
    "dashboard handles every orchestrator attempt status",
    {s.value for s in AttemptStatus}.issubset(known),
    sorted({s.value for s in AttemptStatus} - known),
)

os.environ["EMERGENCY_CALLS_ENABLED"] = "true"

print("\n" + "=" * 74)
print(f"  RESULTS: {passed} passed, {failed} failed")
print("=" * 74)
sys.exit(1 if failed else 0)
