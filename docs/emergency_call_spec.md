# Emergency Call — Spec (Task 0)

**Status:** spec only. No feature code exists.
**Date:** 2026-10-29
**Track:** Emergency Call (separate from M1/W2c and the UI-split track)
**Owner decisions:** recorded in §2, answered 2026-10-29.

---

## 1. What this feature is

When Asha says something physically urgent ("I can't breathe", "I fell and I
can't get up") — or a danger statement that forces Red — Sahaara places a
phone call to her **caregiver**, plays an automated spoken message, and asks
the caregiver to press **1** to confirm they received it. If the caregiver does
not confirm, Sahaara retries once and then tries the next contact in the saved
order. At every stage the caregiver dashboard records what actually happened.

**Sahaara calls family. She never calls emergency services.**

The feature is an escalation channel for the people who already care for Asha.
It is not a medical device, not a fall detector, and not a substitute for
calling 112.

---

## 2. Owner decisions (binding, 2026-10-29)

| # | Decision |
|---|---|
| 1 | **Simulated mode speaks:** *"This is a demo. In a real situation I would be calling Rohan now."* — **never** "placed"/"calling" wording. Dashboard banner `SIMULATED — no real call was placed` and `simulated: true` on every row stay. |
| 2 | **Danger statements (Red) also place the call.** Sahaara still stays present with Asha and offers the helpline. Caregiver only; never emergency services. |
| 3 | **Phone message = category + the matched trigger phrase** (short, sanitised) for physical symptoms. For danger statements: **neutral category only, no quote.** Numbers masked in logs. |
| 4 | **45 s ack window, 1 retry, then the next contact, 10 min cooldown, max 3 calls per incident** (primary, retry, secondary). The cooldown applies to *repeat triggers during an in-progress or unacknowledged incident*; a new urgent statement must **still raise the dashboard alert and the Red tier**. Demo override ≈15 s. |
| 5 | Owner supplies the Twilio account. **`EMERGENCY_CALLS_ENABLED=false`** until a manual pre-flight passes (verified number, geo permissions checked against current Twilio docs, one test call with explicit "go"). |
| 6 | Urgent **overrides** a pending consent question. The consent is logged as **`superseded`**, never `declined`. |
| 7 | `?token=` on the voice WS is out of scope for this track. Keep it for now; tokens must never appear in logs or the event log. Follow-up task: replace it with a short-lived single-use ticket or the cookie. |

---

## 3. Non-goals and hard limits

These are permanent, not phase-1 simplifications:

- Never dial emergency services (112 / 108 / 911 / any public-safety number).
  The dial target must match a saved contact; there is no free-text number path.
- Never claim detection. No "we detected a fall", no "we sensed you fell".
- Never promise an ambulance, police, or that "help is on the way".
- Never claim a call happened unless the provider accepted it **and** we have a
  provider call id. "Ringing" is not "answered". "Answered" is not "heard".
- Never claim a caregiver *received* the alert. Only a DTMF `1` on our own
  webhook counts as confirmed.
- Never dial a contact whose `may_call` is not `true`.
- Never dial when the profile has no phone number for anyone — alert instead.
- Phone numbers never enter the LLM prompt, never reach `/companion`, and never
  appear unmasked in an event, a log line, or an API response.

---

## 4. Trigger rules

| Trigger | Source | Call? | Reply quote in phone message |
|---|---|---|---|
| **Physical urgent** | `PHYSICAL_URGENT_PATTERNS` in `backend/llm/intent_emotion.py` | **Yes, on the first statement** | Category + matched trigger phrase |
| **Danger statement** | `force_tier(RED, "danger_statement")` | **Yes** | Neutral category only (decision 2 + 3) |
| **Physical general** (Orange) | `PHYSICAL_GENERAL_PATTERNS` | **No** — dashboard + existing consent offer | — |
| **Non-urgent distress → consent accepted** | `ConsentManager` `ACCEPTED` | **Yes**, as today, now through the same orchestrator | Consent category, no symptom quote |
| **Silence / no response** | — | **Never** | — |

Repeated urgent statements during an open incident never open a second
incident. They append a `repeat_report` to the existing one, and they **still**
raise a fresh dashboard alert and keep the Red tier (decision 4).

---

## 5. The safety fix this feature depends on (Task 2)

**Confirmed defect, reproduced by experiment:** `conversation.py` handles the
consent `PENDING` state (`ConsentState.PENDING`, the block near the top of
`process_turn`) **before** `classify_intent()` runs. So while a consent question
is pending:

| Utterance | `interpret_response()` today | Consequence |
|---|---|---|
| `"I can't breathe"` | `pending` | turn consumed by the consent question; never classified, never Red |
| `"chest pain"` | `pending` | same |
| `"I fell and I cant get up"` | `pending` | same |
| `"not able to breathe"` | **`declined`** | `DECLINE_PATTERNS` contains a bare `\bnot\b` — a breathing emergency reads as "no thanks" |

**Required change:** urgent and danger classification must run *before* the
consent `PENDING` block.

1. Classify the turn first (`classify_intent` → urgent / danger).
2. If urgent or danger: abandon the pending consent question, log
   `CONSENT_ANSWERED` with outcome **`superseded`** and reason `urgent_override`,
   then continue to the normal emergency path.
3. `ConsentOutcome` gains a `SUPERSEDED` value. It is **not** a decline and must
   never trigger `_handle_decline()`.
4. The `\bnot\b` pattern in `DECLINE_PATTERNS` is narrowed (e.g. `\bnot now\b`,
   `\bnot really\b`, `\bno\b`) so a symptom phrase cannot be read as a refusal
   even outside the urgent pre-check.

This touches `backend/safety/consent.py` and `backend/conversation.py` only, and
is authorised by decision 6. `guardrails.py`, `escalation.py` and
`response_policy.py` are **not** touched.

---

## 6. Contact data

### 6.1 Fields

`family` entries gain the names already proposed in `docs/ui_split_spec.md`,
plus one addition:

| Field | Type | Default | Caregiver-editable | Visibility |
|---|---|---|---|---|
| `phone` | E.164 string or `null` | `null` | yes | **caregiver_only, hard-locked** |
| `notify_order` | int ≥ 1 | relationship order | yes | caregiver |
| `may_offer_notify` | bool | `false` | yes | caregiver |
| **`may_call`** | bool | **`false`** | yes | caregiver |

`may_call` is separate from `may_offer_notify`: offering a call and *placing*
one are different consents. The default is `false`, so no existing profile can
be dialled by accident.

### 6.2 Resolution order

`resolve_contacts(profile)` returns family members sorted by
`(notify_order, relationship priority, arrival order)`, filtered to
`may_call is true` **and** `phone` present. Members skipped for either reason
appear in the incident as `skipped` with a reason — the dashboard must be able
to say *"No phone number saved for Rohan — no call was placed."*

If no contact resolves: incident ends `failed`, reason `no_callable_contact`,
dashboard alert still raised.

### 6.3 Migration

`backend/memory/data/asha_profile.json` and the DB `profiles.family` blob both
lack these fields today. Migration adds the keys with defaults and **does not**
invent a phone number. Until the owner enters one, the feature can only ever
alert, never dial — which is the correct behaviour for a missing number.

---

## 7. State machine

One **incident** per emergency episode. One **attempt** per phone call.

```
                    ┌──────────────┐
 trigger ──────────►│   triggered  │
                    └──────┬───────┘
                           ▼
                    ┌──────────────┐
                    │   dialing    │  (provider accepted, call id exists)
                    └──────┬───────┘
                           ▼
                    ┌──────────────┐
                    │   ringing    │
                    └──────┬───────┘
              ┌────────────┴─────────────┐
              ▼                          ▼
      ┌──────────────┐          ┌──────────────────┐
      │   answered   │          │    no_answer     │
      └──────┬───────┘          │  busy / failed   │
             ▼                  │  canceled        │
      ┌──────────────┐          └────────┬─────────┘
      │ acknowledged │                   │
      └──────┬───────┘                   │
             │                           │
             │ 45 s ack window           │
             ▼                           │
      ┌───────────────────────┐          │
      │ answered_unacknowledged│         │
      └───────────┬───────────┘          │
                  └──────────┬───────────┘
                             ▼
                   ┌──────────────────┐
                   │ retry same?      │  (attempt < 2 for this contact)
                   └────────┬─────────┘
                            ▼
                   ┌──────────────────┐
                   │ next contact?    │  (attempt < 3 for the incident)
                   └────────┬─────────┘
                            ▼
                   ┌──────────────────┐
                   │    exhausted     │
                   └──────────────────┘

 terminus: acknowledged | exhausted | cancelled | failed
```

| Parameter | Value | Env override |
|---|---|---|
| Ring timeout per call | 25 s (`Timeout=25`) | `SAHAARA_EMERGENCY_RING_SECONDS` |
| Ack window after `answered` | **45 s** (demo **15 s**) | `SAHAARA_EMERGENCY_ACK_SECONDS` |
| Retries per contact | **1** | `SAHAARA_EMERGENCY_RETRIES` |
| Backoff before retry | 20 s | `SAHAARA_EMERGENCY_RETRY_DELAY_SECONDS` |
| Max calls per incident | **3** (primary, primary retry, secondary) | `SAHAARA_EMERGENCY_MAX_ATTEMPTS` |
| Hard incident cap | 5 min | `SAHAARA_EMERGENCY_MAX_SECONDS` |
| Cooldown after an incident | **10 min** | `SAHAARA_EMERGENCY_COOLDOWN_SECONDS` |

**Cooldown semantics (decision 4):** while an incident is `dialing`/`ringing`/
`answered`/`open`, or after it ended `exhausted`/`failed` within the cooldown
window, a new urgent statement **does** raise the dashboard alert and **does**
keep Red — it simply does not start a second phone campaign. Exactly 3 calls,
then quiet until the window expires.

**Demo override:** setting `SAHAARA_EMERGENCY_ACK_SECONDS=15` (and optionally
`SAHAARA_EMERGENCY_DEMO_ACK=1` to make the simulated provider auto-press 1)
gives a demo that finishes in under a minute. Demo values are opt-in and must be
visible in the dashboard when active.

---

## 8. Providers

```python
class CallProvider(Protocol):
    name: str
    simulated: bool
    def place_call(self, *, to_e164: str, message: str,
                   incident_id: str, attempt_id: str) -> CallHandle: ...
    def fetch_status(self, handle: CallHandle) -> str: ...
    def cancel(self, handle: CallHandle) -> None: ...
```

`CallHandle` = `{provider_call_id, status, raw}`.

### 8.1 `SimulatedCallProvider` (default)

- No network. Deterministic script: `dialing` 1 s → `ringing` 3 s →
  `answered` → `acknowledged` (if `SAHAARA_EMERGENCY_DEMO_ACK=1`) else
  `no_answer` after the ack window.
- Every incident and attempt row carries `simulated: true`.
- Dashboard banner: **`SIMULATED — no real call was placed`**, always visible
  while any incident on screen is simulated.
- Spoken line (decision 1): *"This is a demo. In a real situation I would be
  calling Rohan now."* — **never** "I'm calling", never "I've called".

### 8.2 `TwilioCallProvider` (opt-in, flag-gated)

- Constructed only when `EMERGENCY_CALLS_ENABLED=true` **and**
  `SAHAARA_EMERGENCY_PROVIDER=twilio` **and** SID + API key + secret + from
  number + public base URL are all present.
- If any piece is missing the app logs why and **falls back to simulated** — it
  never silently does nothing, and it never claims a real call.
- Places calls with `status_callback` events `initiated,ringing,answered,completed`
  and `timeout=25`.

### 8.3 Selection

```
EMERGENCY_CALLS_ENABLED=false            → no calls at all; alert + Red only
EMERGENCY_CALLS_ENABLED=true, simulated  → SimulatedCallProvider
EMERGENCY_CALLS_ENABLED=true, twilio     → TwilioCallProvider (needs secrets)
```

`EMERGENCY_CALLS_ENABLED=false` means the incident is still recorded with
`status=failed, reason=disabled` and the dashboard says *"Emergency calling is
turned off — dashboard alert only."* It does not pretend.

---

## 9. Wording

All wording is derived from real provider state. None of it comes from the LLM.

### 9.1 Spoken to Asha

Only **three** things are ever said about the call. Everything else is
dashboard-only, because "answered" cannot be distinguished from voicemail or an
IVR.

| Real state | Spoken line |
|---|---|
| Simulated (any stage) | *"This is a demo. In a real situation I would be calling Rohan now."* |
| Provider accepted the call | *"I'm calling Rohan now."* |
| Caregiver pressed 1 | *"Rohan has received the alert."* |
| All attempts ended, nothing confirmed | *"I couldn't reach anyone. I've sent an alert to Rohan's dashboard. Please call 112 if you need help right now."* |
| Disabled / no phone / provider error | *"I've sent an alert to Rohan's dashboard. Please call 112 if you need help right now."* |

**No line exists for `ringing`, `answered` or `no_answer`.** Those are dashboard
states. Sahaara keeps talking to Asha about how she feels; she does not narrate
the phone.

### 9.2 Spoken to the caregiver (automated system voice)

Deliberately **not** Asha's Cartesia voice, so it cannot be mistaken for her.

Physical urgent:
> *"This is an automated call from Sahaara. Asha has reported a physical
> emergency. She said: \"{matched trigger phrase}\". Press 1 to confirm you
> received this call."*

Danger (decision 3 — neutral, no quote):
> *"This is an automated call from Sahaara. Asha has reported that she needs
> help right away. Press 1 to confirm you received this call."*

Consent-accepted:
> *"This is an automated call from Sahaara. Asha asked to speak with you.
> Press 1 to confirm you received this call."*

Rules:
- `{matched trigger phrase}` is the **matched pattern text**, sanitised, capped
  at 15 words, no doses, no medication names.
- Never "an ambulance has been called", never "she has fallen" (unless her own
  words said so), never "we detected".
- Repeated with `<Say loop="2">`? **No.** One pass, then a `<Gather>` with a 15 s
  DTMF window, then a short "Goodbye" and hang up.

### 9.3 Dashboard

Pure module `frontend/src/callStatus.ts`, unit-tested, exactly the pattern used
by `pushStatus.ts`.

| State | Dashboard line |
|---|---|
| simulated | `SIMULATED — no real call was placed` (banner) |
| dialing | `Calling Rohan…` |
| ringing | `Ringing Rohan` |
| answered | `Rohan's phone answered — confirmation not received` |
| acknowledged | `Rohan pressed 1 to confirm` |
| no_answer | `No answer — trying again` |
| next contact | `Trying Priya next` |
| exhausted | `No one could be reached — dashboard alert only` |
| disabled | `Emergency calling is turned off — dashboard alert only` |
| no_phone | `No phone number saved for Rohan — no call was placed` |

**Forbidden words across every reachable string** (test-enforced, same method as
`pushStatus.test.ts`): `delivered`, `delivery`, `seen`, `read`, `received by`,
`on the way`, `coming`, `ambulance`, `help is coming`, `safe`, `detected`.

---

## 10. Events and PII

Five new `EventType` values, appended after `REMINDER_FIRED` (order preserved —
the companion whitelist must not shift):

| Event | Detail keys |
|---|---|
| `EMERGENCY_CALL_STARTED` | `incident_id`, `trigger`, `tier`, `simulated`, `provider` |
| `EMERGENCY_CALL_ATTEMPT` | `incident_id`, `attempt_id`, `contact_order`, `contact_name`, `phone_masked`, `attempt`, `simulated` |
| `EMERGENCY_CALL_STATUS` | `incident_id`, `attempt_id`, `status`, `simulated` |
| `EMERGENCY_CALL_ENDED` | `incident_id`, `status`, `attempts`, `simulated`, `reason` |
| `EMERGENCY_CALL_SKIPPED` | `incident_id`, `reason`, `contact_name` |

PII rules, all test-enforced:
- Phone numbers are stored/emitted **masked**: `+91 **** 3210` (last 4 only).
- The real number lives only in the profile. The attempts table keeps the masked
  form, so a profile edit does not leave a second copy of the number behind.
- The logger has an explicit formatter guard: any `+` followed by 8+ digits in a
  log record is masked before write. One test asserts a deliberately leaky log
  call comes out masked.
- No event in this feature is ever mapped by `companion.py` (see §11).

---

## 11. Visibility per surface

| Surface | Sees | Enforcement |
|---|---|---|
| `/companion` (Asha, no login) | **Nothing.** No call state, no contact name, no phone, no event | `COMPANION_KINDS` unchanged; add `call`, `phone`, `emergency`, `contact`, `dial`, `caregiver` to `FORBIDDEN_KEY_RE`; `map_event()` returns `None` for all five new events |
| Caregiver dashboard | Full incident timeline, masked numbers, simulated banner | `require_caregiver` on every `/emergency/*` route |
| Profile editor | `phone` field, caregiver-only, hard-locked chip | Same pattern as health notes |
| LLM prompt | **Nothing** — no phone, no contact list, no call state | A test asserts the built prompt never contains a phone-like string |
| Push notification | Unchanged fixed body (`Asha needs a check-in`) — the call feature adds no payload | Existing `push_notify` tests still pass |

The companion exclusion is the one that matters most: a screen Asha can see must
never display "Rohan did not answer".

---

## 12. Twilio integration — verified facts

Verified against current official docs on 2026-10-29.

| Fact | Source |
|---|---|
| `POST /Calls` takes `To`, `From`, and `Twiml` (≤4000 chars) or `Url`; Python `client.calls.create(to=…, from_=…, twiml=…, status_callback=…, status_callback_event=[…], timeout=…)` | [Call resource](https://www.twilio.com/docs/voice/api/call-resource) |
| Status values: `queued`, `ringing`, `in-progress`, `completed`, `busy`, `failed`, `no-answer`, `canceled`. Callback events: `initiated`, `ringing`, `answered`, `completed` | same |
| **`completed` / answered means a connection was established and audio transferred — it can be a person, an IVR, or voicemail.** Never treat it as proof a human heard the message | same (explicit note) |
| `Timeout` default 60 s, max 600 s; Twilio may add ≈5 s buffer | same |
| `<Say>` supports `language`, `voice`, `loop`; `man`/`woman` are the free Basic voices, Polly/Google voices are named like `Polly.Joanna-Generative` | [TwiML `<Say>`](https://www.twilio.com/docs/voice/twiml/say) |
| `<Gather>` collects DTMF (`input="dtmf"`) with `numDigits`, `action`, `timeout` — this is the ack keypress | [TwiML `<Gather>`](https://www.twilio.com/docs/voice/twiml/gather) |
| Webhooks must be HTTPS (no self-signed certs) and validated with `X-Twilio-Signature` via `RequestValidator` + auth token | [Secure webhooks](https://www.twilio.com/docs/usage/webhooks/webhooks-security) |
| Trial: 30 days, 75 voice minutes, calls only to **verified** numbers (max 5), voice restricted to sign-up country | [Twilio trial](https://www.twilio.com/docs/usage/trials) · [Trial quickstart](https://www.twilio.com/docs/usage/tutorials/how-to-use-your-free-trial-account) |
| Geo permissions: Console → Voice → Settings → Geo permissions, low-risk/high-risk ranges per country, Owner/Administrator only | [Voice Dialing Geo Permissions](https://www.twilio.com/docs/sip-trunking/voice-dialing-geographic-permissions) |
| **India: outbound calls to India can only be made from international (non-Indian) numbers.** Voice OTP/2FA not permitted and may be blocked. High volumes of short-duration calls may trigger carrier blocks | [India: Voice Guidelines](https://www.twilio.com/en-us/guidelines/in/voice) |
| Default 1 call/second (CPS) via `POST /Calls` | [Call resource](https://www.twilio.com/docs/voice/api/call-resource) |

### 12.1 Unverified — must be checked on the real account (not guessed)

1. **Trial geography vs India rule may conflict.** Trial restricts voice to the
   sign-up country; India requires calls to +91 to originate from a non-Indian
   number. Whether an India-registered trial can dial +91 is unclear from the
   docs. **This is the single riskiest unknown for a live demo.**
2. Whether trial calls play a preamble before the TwiML runs.
3. The exact `<Say>` voice/locale for a neutral Indian-English system voice.
   Do not hardcode a voice name until it is confirmed in the voice list.
4. Current per-minute US→India pricing.

### 12.2 Manual pre-flight (must pass before `EMERGENCY_CALLS_ENABLED=true`)

- [ ] Contact number added as a **verified** caller ID on the account.
- [ ] India + destination country enabled in Voice → Geo permissions.
- [ ] One **manual** test call placed by the owner from the Twilio Console,
      with the owner's explicit "go".
- [ ] Trial preamble / geo behaviour confirmed empirically.
- [ ] Only then: flip the flag, and place one app-driven call to the owner's own
      phone while Asha's number is still unset.

---

## 13. Webhook reachability

Twilio needs a public HTTPS host for `StatusCallback` and the `<Gather action>`.

- **Primary:** Cloudflare quick tunnel → `SAHAARA_EMERGENCY_PUBLIC_BASE_URL`.
  The known tunnel limitation (SSE bodies buffered) does not affect webhooks.
- **Fallback with no public URL:** REST polling (`client.calls(sid).fetch()`)
  every 2 s. This learns `ringing` / `in-progress` / `completed` / `no-answer`
  but **cannot receive DTMF**. In that mode the dashboard must render
  `Rohan's phone answered — confirmation not received` and must never render
  "pressed 1". That degradation is a test case, not a footnote.
- **Every webhook:** validate `X-Twilio-Signature` over the full URL + params →
  `403` on failure. Then require the `CallSid` to match an **open attempt** on an
  **open incident**; an unknown SID is logged and ignored, never trusted.
- Webhook routes are POST-only, never caregiver-authenticated (Twilio cannot send
  a cookie), and are therefore the only unauthenticated writes in this feature.

---

## 14. Secrets and configuration

**`.env` is untracked** — `.gitignore` has `.env` and `.env.*` with
`!.env.example`; `git ls-files` confirms only `.env.example` is tracked. (The
`agents.md` §9 gotcha claiming ".env is committed" was stale and is corrected in
the same commit as this spec.)

Placeholder **names only** go in `.env.example`:

```
EMERGENCY_CALLS_ENABLED=false
SAHAARA_EMERGENCY_PROVIDER=simulated
SAHAARA_EMERGENCY_ACK_SECONDS=45
SAHAARA_EMERGENCY_DEMO_ACK=0
SAHAARA_EMERGENCY_COOLDOWN_SECONDS=600
SAHAARA_EMERGENCY_MAX_ATTEMPTS=3
SAHAARA_EMERGENCY_PUBLIC_BASE_URL=
TWILIO_ACCOUNT_SID=
TWILIO_API_KEY=
TWILIO_API_SECRET=
TWILIO_AUTH_TOKEN=
TWILIO_FROM_NUMBER=
```

- API calls use the **API key/secret**, not the account auth token.
- The auth token is used **only** for webhook signature validation.
- No secret is ever logged, returned by an API, or written to an event.

---

## 15. Data model

```sql
CREATE TABLE IF NOT EXISTS emergency_incidents (
    id              TEXT PRIMARY KEY,      -- emg_<epoch>_<n>
    profile_id      TEXT NOT NULL,
    trigger         TEXT NOT NULL,         -- physical_urgent | danger | consent_accepted
    trigger_phrase  TEXT,                  -- sanitised, <=15 words; NULL for danger
    tier            TEXT NOT NULL,
    simulated       INTEGER NOT NULL DEFAULT 1,
    provider        TEXT NOT NULL DEFAULT 'simulated',
    status          TEXT NOT NULL,         -- open | acknowledged | exhausted | cancelled | failed
    reason          TEXT NOT NULL DEFAULT '',
    attempts        INTEGER NOT NULL DEFAULT 0,
    started_at      REAL NOT NULL,
    ended_at        REAL
);

CREATE TABLE IF NOT EXISTS emergency_attempts (
    id                TEXT PRIMARY KEY,
    incident_id       TEXT NOT NULL,
    contact_order     INTEGER NOT NULL,
    contact_name      TEXT NOT NULL,
    contact_rel       TEXT NOT NULL DEFAULT '',
    phone_masked      TEXT NOT NULL,       -- +91 **** 3210 only
    attempt           INTEGER NOT NULL,    -- 1..3 across the incident
    status            TEXT NOT NULL,       -- queued|dialing|ringing|answered|acknowledged|no_answer|failed|canceled
    provider_call_id  TEXT,
    simulated         INTEGER NOT NULL DEFAULT 1,
    started_at        REAL NOT NULL,
    answered_at       REAL,
    acknowledged_at   REAL,
    ended_at          REAL
);
```

Both tables are additive and idempotent, matching the existing
`CREATE TABLE IF NOT EXISTS` pattern in `database.py`. No existing table changes.
The real phone number is **not** stored here.

---

## 16. API surface

All caregiver-authenticated (`require_caregiver`), all GET/POST only:

| Route | Purpose |
|---|---|
| `GET /emergency/incidents?limit=20` | recent incidents, masked |
| `GET /emergency/incidents/{id}` | incident + attempts + ordered timeline |
| `POST /emergency/incidents/{id}/cancel` | caregiver says "I'm handling it" → stops retries, ends `cancelled` |
| `POST /emergency/twilio/status` | provider status callback (signature-validated) |
| `POST /emergency/twilio/ack` | `<Gather action>` → DTMF `1` = acknowledged |

Deliberately **not** built: `POST /emergency/test-call`. A one-click dial button
is a foot-gun; the pre-flight test is manual and owner-approved.

---

## 17. Voice integration

Two phases, because honesty and latency pull in opposite directions.

**Phase 1 — the turn reply.** The existing LLM-generated emergency reply keeps
its warmth (name the feeling, stay present, urge help). Before it is returned, a
deterministic `_strip_call_claims()` removes any sentence in which the model
claims to have called, be calling, or have contacted anyone. The model is never
allowed to preempt the orchestrator.

**Phase 2 — the deterministic call line.** The orchestrator runs on a daemon
thread (same pattern as `push_notify.notify_alert`) with a **bounded 1.5 s wait**
for the provider's accept. The resulting sentence is delivered through the
existing `_speak_initiative()` path, which closes the mic gate first, so it
cannot be transcribed as a new turn:

- simulated → *"This is a demo. In a real situation I would be calling Rohan now."*
- accepted → *"I'm calling Rohan now."*
- failed / disabled / no phone → *"I've sent an alert to Rohan's dashboard."*

If the provider answers after the 1.5 s window, the sentence is still spoken
once, and the dashboard gets `call_started_late`. No sentence is ever spoken
twice.

**Latency:** `process_turn` gains at most 1.5 s in the worst case and ~0 in the
normal case (the wait is off the LLM path). `MILD_DELAY_THRESHOLD = 3.5` and
`REAL_DELAY_THRESHOLD = 8.0` stay untouched.

**Text chat:** with no `/ws/voice` socket there is no phase 2 audio. The call is
still placed, and the dashboard timeline is the record. This is the same
dashboard-only rule as timers.

---

## 18. Test matrix

| Suite | Must prove |
|---|---|
| `tests/test_emergency_consent_order.py` | all four phrases during `PENDING` → Red; consent logged `superseded`, not `declined`; `\bnot\b` fix; next turn normal |
| `tests/test_emergency_contacts.py` | ordering, `may_call` gating, missing phone skip, migration defaults, no invented number |
| `tests/test_emergency_orchestrator.py` | state machine transitions; 3-attempt cap; retry then next contact; cooldown applies only to the call, alert + Red still raised; repeat appends; cancel |
| `tests/test_emergency_simulated.py` | scripted progression, `simulated: true` everywhere, demo 15 s ack, deterministic demo sentence |
| `tests/test_emergency_pii.py` | masked in every event/API/log line; no phone-like string in the LLM prompt; unknown log leak is masked |
| `tests/test_emergency_wording.py` | every reachable spoken/dashboard line derived from state; forbidden-word list; no "ambulance"/"on the way"/"detected" |
| `tests/test_emergency_companion.py` | model learns nothing about calls |
| `tests/test_emergency_twilio.py` (fake client, no network) | `Twiml` construction, status mapping, signature validation accept/reject, unknown CallSid ignored, polling fallback never says acknowledged, gate-off path |
| `frontend/tests/callStatus.test.ts` | wording table + forbidden words + `null` handling |
| `frontend/tests/emergencyVisibility.test.ts` | companion channel still refuses `call`/`phone`/`emergency` keys |

Regression after every task: `unit_e2e.py`, `test_companion_stream.py`,
`test_action_replies.py`, `test_consent_push.py`, `guardrail_baseline.py
--verify tests/baseline_guardrails_pre.json`, and — when frontend is touched —
`tsc -b`, `npm run lint`, `npm run test:pwa`, `npm run build`.

---

## 19. Task breakdown

One at a time. Each ends with tests, a commit on `main` (never pushed), the
`agents.md` update, a report, then STOP.

| # | Task | Touches safety files? |
|---|---|---|
| 1 | Contact schema (`phone`, `notify_order`, `may_offer_notify`, `may_call`) + migration + resolvers | no |
| 2 | **Safety fix:** urgent/danger classified before the consent block; `SUPERSEDED` outcome; `\bnot\b` narrowing | **yes** — authorised by decision 6 |
| 3 | Orchestrator + `SimulatedCallProvider` + incidents/attempts tables + cooldown/retry/next-contact | no |
| 4 | Dashboard surface (`callStatus.ts`, incident timeline, simulated banner) | no |
| 5 | Companion exclusion + forbidden-key test | no |
| 6 | `TwilioCallProvider` + signed webhooks + polling fallback (flag OFF) | no |
| 7 | Voice integration (claim stripper + deterministic initiative line) | no |
| 8 | Docs, `.env.example`, full sweep, manual pre-flight checklist | no |

Task 6 stays behind `EMERGENCY_CALLS_ENABLED=false` until §12.2 passes with the
owner's explicit "go".

---

## 20. Open follow-ups (not this track)

1. **Voice WS `?token=` → short-lived single-use ticket or cookie** (decision 7).
   Out of scope here; recorded so it is not lost. Tokens must never reach logs or
   the event log.
2. `agents.md` §0 says the companion channel has 5 kinds; the code has 6
   (`reminder_fired` was added). Doc drift, recorded, not fixed in this commit.
3. `overview.md` still describes the pre-auth system; unchanged by this spec.
