# Emergency Call — Spec (Task 0, revised for Vonage)

**Status:** implemented through Task 4, the Vonage provider, the webhook routes
and the extended trigger set. **No real call has ever been placed**:
`EMERGENCY_CALLS_ENABLED=false` and `SAHAARA_EMERGENCY_REAL_CALLS_CONFIRMED=false`,
so every campaign on record ran through `SimulatedCallProvider` and every row
carries `simulated: true`.
**Date:** 2026-10-29, revised 2026-10-30 (Twilio removed, Vonage adopted, triggers widened).
**Track:** Emergency Call (separate from M1/W2c and the UI-split track)
**Owner decisions:** recorded in §2; the 2026-10-30 revision is recorded in §2a.

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
| 4 | **45 s ack window, 2 attempts per contact, then the next contact, 5 min cooldown, max 4 calls per incident** (Rohan ×2 → Priya ×2 → exhausted). The cooldown applies to *the phone campaign only*; a new urgent statement must **still raise the dashboard alert and the Red tier**. Demo override ≈15 s. |
| 5 | Owner supplies the **Vonage** account. **`EMERGENCY_CALLS_ENABLED=false`** until a manual pre-flight passes (verified number, India routing checked against current Vonage docs, public HTTPS webhook reachable, one test call with explicit "go"). |
| 6 | Urgent **overrides** a pending consent question. The consent is logged as **`superseded`**, never `declined`. |
| 7 | `?token=` on the voice WS is out of scope for this track. Keep it for now; tokens must never appear in logs or the event log. Follow-up task: replace it with a short-lived single-use ticket or the cookie. |


### 2a. Revision 2026-10-30 (binding)

| # | Decision |
|---|---|
| 8 | **Vonage is the only real provider.** Twilio is removed outright — `telephony/twilio.py` is deleted, the `twilio` branch is gone from `get_call_provider()`, and `twilio` is removed from `requirements.txt`. Exactly one real route exists. |
| 9 | **Triggers widen to five.** Explicit "call Rohan" + consent-accept after an offer + Red immediately + **Orange after a dwell** + Yellow ask-only. None of them is LLM-decided. |
| 10 | **The Orange dwell runs on its own background tick**, independent of `SAHAARA_PROACTIVE_V2` and of `_paused`. A paused monitoring session must never silence an emergency call. Reversing this is a one-line change. |
| 11 | **An emergency reply never ends with "Would you like me to go on?"** The prompt forbids it on danger/symptom turns and `_strip_continuation_offer()` removes it deterministically. |
| 12 | **A press-1 stops the whole ladder**, not just the current attempt. A late press arriving after the ack window is **accepted**, never discarded — it is the strongest signal the system gets. |
| 13 | **Provider webhooks are parsed from the raw body** (`emergency_routes._webhook_payload`), not `request.form()`, because Starlette requires `python-multipart` for form parsing and would 500 every real callback. |

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

All five are deterministic. **None is decided by the LLM**: the LLM may only
*offer* a call on Yellow, and the offer is answered by the consent machine.

| # | Trigger | Source (deterministic) | Call? | Phone message |
|---|---|---|---|---|
| 1 | **Explicit request** — "call Rohan", "phone my son" | `_EXPLICIT_CALL_RE` in `conversation.py` | **Yes, same turn** | Neutral category: *"asked to speak with you"*. No symptom quote. |
| 2 | **Consent accepted** after Sahaara's offer | `ConsentManager` → `ACCEPTED` | **Yes, same turn** | Consent category, no symptom quote |
| 3 | **Red — danger statement** | `force_tier(RED, "danger_statement")` | **Yes, same turn** | Neutral, **no quote** (decision 3) |
| 4 | **Red — physical urgent** | `PHYSICAL_URGENT_PATTERNS` | **Yes, same turn** | Category + matched trigger phrase |
| 5 | **Orange — physical general, after a dwell** | `PHYSICAL_GENERAL_PATTERNS` + `Scheduler._dwell_loop` | **Yes, after `SAHAARA_ORANGE_CALL_DWELL_SECONDS` (default 120 s)** | Category + matched trigger phrase |
| — | **Yellow** | repetition / sustained distress | **No auto call.** Dashboard alert; Sahaara may **ask once**. | — |
| — | **Silence** | — | **Never** | — |

Rules that hold for every trigger:

- The dwell is checked by a **background tick** (`SAHAARA_ORANGE_DWELL_POLL_SECONDS`,
  default 5 s), so an Orange tier reached and then left silent still dials.
  It is deliberately **not** gated by `_paused` or `SAHAARA_PROACTIVE_V2`.
- **Step-down before the dwell expires cancels the campaign and logs why**
  (`tier_stepped_down`). No call is placed.
- Repeated urgent statements during an open incident never open a second
  incident inside the cooldown. They still raise a fresh dashboard alert and
  keep the Red tier (decision 4).
- One incident = one active ladder. A duplicate trigger, a tier flicker, or a
  provider retry **must not** place a second call while a campaign is live or
  inside the 5-minute cooldown. A per-profile lock enforces this.

---

## 5. The safety fix this feature depends on (Task 2) — ✅ FIXED `d95803e`

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

**The change that shipped** (`d95803e`, verified live on a throwaway server):
urgent and danger classification runs *before* the consent `PENDING` block.

1. Classify the turn first (`classify_intent` → urgent / danger).
2. If urgent or danger: abandon the pending consent question, log
   `CONSENT_ANSWERED` with outcome **`superseded`** and reason `urgent_override`,
   then continue to the normal emergency path.
3. `ConsentOutcome` gains a `SUPERSEDED` value. It is **not** a decline and must
   never trigger `_handle_decline()`.
4. The `\bnot\b` pattern in `DECLINE_PATTERNS` is narrowed (e.g. `\bnot now\b`,
   `\bnot really\b`, `\bno\b`) so a symptom phrase cannot be read as a refusal
   even outside the urgent pre-check.

That touched `backend/safety/consent.py`, `backend/conversation.py` and one enum
member in `events/event_log.py`, and was authorised by decision 6.
`guardrails.py`, `escalation.py` and `response_policy.py` were **not** touched.

One further fault was found *while* fixing it: narrowing the decline patterns
made **"I'm not sure"** reach the accept list (`\bsure\b`) and record as her
agreeing to a call. The accept check now refuses any negated sentence
(`not` / `never` / `n't`) and re-asks instead. A false accept is worse than a
false decline because it acts.

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
                   │ next contact?    │  (attempt < 4 for the incident)
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
| Attempts per contact | **2** (original + 1 retry) | fixed in the ladder |
| Backoff before retry | 20 s | `SAHAARA_EMERGENCY_RETRY_DELAY_SECONDS` |
| Max calls per incident | **4** (Rohan ×2 → Priya ×2) | `SAHAARA_EMERGENCY_MAX_ATTEMPTS` |
| Cooldown after an incident | **5 min** | `SAHAARA_EMERGENCY_COOLDOWN_SECONDS` |
| Orange dwell before calling | **120 s** | `SAHAARA_ORANGE_CALL_DWELL_SECONDS` |
| Dwell poll interval | 5 s | `SAHAARA_ORANGE_DWELL_POLL_SECONDS` |

**Cooldown semantics (decision 4):** while an incident is `dialing`/`ringing`/
`answered`/`open`, or after it ended `exhausted`/`failed` within the cooldown
window, a new urgent statement **does** raise the dashboard alert and **does**
keep Red — it simply does not start a second phone campaign. At most 4 calls,
then quiet until the window expires. **The cooldown never suppresses the alert
or the tier** (decision 4).

**Demo override:** setting `SAHAARA_EMERGENCY_ACK_SECONDS=15` gives a demo that
finishes in under a minute when the real provider is active. There is no
simulated happy path to demo: a simulated campaign records exactly one
`simulated_not_placed` attempt and stops.

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

- No network, no telephone, no script. `place_call` returns one terminal
  status — **`simulated_not_placed`** — carrying the exact gate that refused
  the real call. The orchestrator records one attempt with that status, logs
  `emergency_call_skipped`, and ends the incident **`simulated`**.
- It never emits `dialing`, `ringing`, `answered`, `acknowledged` or
  `no_answer`, and `acknowledge()` always returns `False`. A phone that never
  rang must never appear to have been answered.
- Every incident and attempt row carries `simulated: true`.
- Dashboard banner: **`SIMULATED — <the gate that refused the call>`**, always
  visible while any incident on screen is simulated.
- Spoken line: the same honest blocked line as any other refused call — it
  never says "I'm calling", never "I've called", never "demo".

### 8.2 `VonageCallProvider` (opt-in, triple-gated)

`backend/telephony/vonage.py`. Talks to the Vonage Voice API with `requests`
and a JWT signed by **PyJWT** — not the Vonage SDK, so every HTTP call is
mockable and the whole provider suite runs offline.

- Constructed only when **all three** gates are true:
  `EMERGENCY_CALLS_ENABLED=true`, `SAHAARA_EMERGENCY_PROVIDER=vonage`,
  `SAHAARA_EMERGENCY_REAL_CALLS_CONFIRMED=true` — **and** the six Vonage values
  are present (application id, private key, API key/secret, from number, public
  base URL).
- If any piece is missing the app logs the gate reason and **falls back to
  simulated**. It never silently does nothing, and it never claims a real call.
- A contact is only dialled when it has a valid E.164 number and `may_call: true`,
  and — when `ALLOWED_TEST_NUMBERS` is set — its number is on that list. Anything
  else is simulated with the reason logged.
- Places the call with a fixed NCCO (see §9.2) and a 25 s ring timeout.

### 8.3 Selection

```
EMERGENCY_CALLS_ENABLED=false                          → simulated, reason=disabled
EMERGENCY_CALLS_ENABLED=true, provider=simulated        → SimulatedCallProvider
EMERGENCY_CALLS_ENABLED=true, provider=vonage,
  REAL_CALLS_CONFIRMED=true, credentials present,
  number permitted (ALLOWED_TEST_NUMBERS)               → VonageCallProvider
anything else                                          → simulated + reason logged
```

`EMERGENCY_CALLS_ENABLED=false` still records the incident with
`status=failed, reason=disabled`, and the dashboard says *"Emergency calling is
turned off — dashboard alert only."* It does not pretend.


---

## 9. Wording

Every line is built from real provider state and appended **verbatim after the
guardrail check**. The LLM never sees these strings, so no paraphrase can turn
"simulated" into "I'm calling".

### 9.1 Spoken to Asha

Five states, five sentences. The constants live in `conversation.py`
(`CALL_LINE_*`), keyed by `CALL_LINE_STATES`:

| State | When | Line |
|---|---|---|
| `placed` | the provider accepted the call and returned a call id | *"I'm calling {name} now. I'm right here with you."* |
| `answered` | the provider reports the line answered | *"{name} is on the line."* |
| `not_reached` | every permitted contact and attempt ended without an ack | *"I couldn't reach {name} yet. Please call 112 or ask someone nearby. I'm still here."* |
| `blocked` | disabled / no number / gate refused / provider failure | *"I've told {name}'s dashboard. Please call 112 or someone nearby if you need help now."* |
| `simulated` | simulated provider, any stage | *"This is a demo. In a real situation I would be calling {name} now. I've told {name}'s dashboard — if you need help right now, please call 112 or someone nearby."* |

Hard rules, test-enforced in `tests/test_emergency_wording.py` (87 checks):

- **`112` appears only in `not_reached`, `blocked` and `simulated`** — the states
  where no call actually reached a person.
- **No claim words in `simulated`, `blocked` or a failure state**: `i'm calling`,
  `i am calling`, `i've called`, `on the line`, `dialling`, `dialing`,
  `phone is ringing`.
- An emergency reply **never** ends with "Would you like me to go on?" — the
  prompt forbids it on these turns and `_strip_continuation_offer()` strips it.
- `{name}` comes from the resolved contact, never from the LLM.

### 9.2 Spoken to the caregiver (Vonage NCCO)

A **fixed** NCCO built in `VonageCallProvider.build_ncco()`. It is not Asha's
Cartesia voice, so it cannot be mistaken for her.

```
talk(message)
talk(message)                     # repeated once, as the brief requires
input{ type: dtmf, maxDigits: 1, timeOut: 10, submitOnHash: true }
talk("Thank you. This message will now end.")
```

Message text by trigger:

- Physical urgent:
  > *"This is an automated message from Sahaara. Asha may need help right now.
  > Please check on her or call her now."*

- Danger (neutral, no quote):
  > *"This is an automated message from Sahaara. Asha may need help right now.
  > Please check on her or call her now."*

- Explicit request / consent-accepted:
  > *"This is an automated message from Sahaara. Asha asked to speak with you.
  > Please call her when you can."*

Rules:

- `{matched trigger phrase}` is the person's **own words**, sanitised, capped at
  15 words, no doses, no medication names. Danger statements carry **no quote**.
- Never "an ambulance has been called", never "she has fallen" unless her own
  words said so, never "we detected".
- One pass, one repeat, then a 10 s DTMF window, then a closing line and hangup.
- The `dtmf` field arrives on the wire as a **JSON string**, so
  `telephony.provider.parse_dtmf_digits()` accepts a nested object, a JSON
  string, flattened digits, and refuses `timed_out`. A plain `payload["digits"]`
  read would have missed every real press.

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

## 12. Vonage integration — verified facts

Checked against the current Vonage docs on 2026-10-29/30. Anything I could not
confirm is in §12.1 and is **not** relied on by the code.

| Fact | Source |
|---|---|
| Create a call: `POST https://api.nexmo.com/v1/calls` with a JWT in `Authorization: Bearer`, body `{to:[{type:"phone",number}], from:{type:"phone",number}, ncco:[...]}` **or** `answer_url` — never both | [Create a call](https://developer.vonage.com/en/api/voice#createCall) |
| `ringing_timer` is the ring timeout field (**not** `ringing_timeout`), in seconds | same |
| Status values: `started`, `ringing`, `answered`, `busy`, `cancelled`, `unanswered`, `disconnected`, `rejected`, `failed`, `timeout`, `completed`, `human`, `machine` | [Call status](https://developer.vonage.com/en/voice/voice-api/webhook-reference#call-status) |
| **`answered`/`completed` means a connection was established — it can be a person, an IVR, or voicemail.** `machine` says a machine answered. Never treat any of these as proof a human heard the message | webhook reference (explicit note) |
| NCCO `talk` supports `text`, `voiceName`, `language`; `input` with `type:["dtmf"]`, `maxDigits`, `timeOut`, `submitOnHash`, `eventUrl` | [NCCO reference](https://developer.vonage.com/en/voice/voice-api/ncco-reference) |
| DTMF webhook body carries `{"dtmf": {"digits": "1", "timed_out": false}}`, and on the wire `dtmf` is a **JSON string** | [DTMF webhook](https://developer.vonage.com/en/voice/voice-api/webhook-reference#dtmf) |
| Webhooks are signed with a **JWT in the `Authorization: Bearer` header**; validate it with the application's signature secret/public key | [Signed webhooks](https://developer.vonage.com/en/getting-started/concepts/authentication) |
| Hang up an in-progress call: `PUT /v1/calls/{uuid}` with `{"action":"hangup"}` | [Update a call](https://developer.vonage.com/en/api/voice#updateCall) |
| JWT auth uses the **Application ID** as `application_id` and the application's **private key** (RSA), `exp` ≤ 24 h | [JWT auth](https://developer.vonage.com/en/getting-started/concepts/authentication#json-web-tokens) |

### 12.1 Unverified — must be checked on the real account (not guessed)

1. **India destination rules.** Vonage requires a **non-Indian origin number**
   for calls to +91. Whether a trial account can do this at all is the single
   riskiest unknown for a live demo.
2. **Trial restrictions.** Trial credit, verified-destination rules, and any
   spoken trial preamble are **not** assumed by the code.
3. **The exact `voiceName`/`language` pair** for a neutral Indian-English
   system voice. The code defaults to `Salli` / `en-IN`; do not treat that as
   confirmed until it is heard on a real call.
4. **Current per-minute pricing** for the origin→India route.
5. **Webhook signature verification against a live call.** The verifier is
   unit-tested against a locally signed JWT, never against Vonage's own signer.

### 12.2 Manual pre-flight (must pass before `EMERGENCY_CALLS_ENABLED=true`)

- [ ] Vonage application created; Application ID + private key in `backend/.env`.
- [ ] A number **you own** is set as `VONAGE_FROM_NUMBER`, and it is a
      non-Indian number (India destination rule).
- [ ] `SAHAARA_EMERGENCY_PUBLIC_BASE_URL` is a public HTTPS URL that Vonage can
      actually reach (a Cloudflare quick tunnel is enough).
- [ ] Both webhooks answer `200` to a hand-crafted request from `curl` **before**
      a real call is attempted.
- [ ] `ALLOWED_TEST_NUMBERS` contains **only your own phone**, and that phone is
      the only permitted contact in the profile.
- [ ] One manual call placed by the owner from the Vonage dashboard, with an
      explicit "go".
- [ ] Then one app-driven call, and the dashboard state compared line by line
      with what actually happened on the phone (see the checklist in `agents.md`).

---

## 13. Webhook reachability

Vonage needs a public HTTPS host for the event webhook and the DTMF webhook.
Both land on `backend/emergency_routes.py`.

| Route | Purpose |
|---|---|
| `POST /emergency/vonage/event?attempt_id=…` | call lifecycle (`started` → `ringing` → `answered` → `completed`/`failed`/…) |
| `POST /emergency/vonage/ack?attempt_id=…` | DTMF result — `digits: "1"` acknowledges |

- **Primary:** Cloudflare quick tunnel → `SAHAARA_EMERGENCY_PUBLIC_BASE_URL`.
  The known tunnel limitation (SSE bodies buffered) does not affect webhooks.
- **Fallback with no public URL:** REST polling every 2 s. This learns
  `ringing` / `answered` / `completed` but **cannot receive DTMF**. In that mode
  the dashboard renders `answered — confirmation not received` and must never
  render "pressed 1". That degradation is a test case, not a footnote.
- **Every webhook** is signature-checked when a signature secret is configured →
  `403` on failure. Then the `attempt_id` must match an **open attempt** on an
  **open incident**; an unknown id changes nothing and is never trusted.
- Webhook routes are POST-only and never caregiver-authenticated (Vonage cannot
  send a cookie), so they are the only unauthenticated writes in this feature.
- The body is decoded by `_webhook_payload()` straight from the raw bytes —
  **`request.form()` is not used**, because Starlette needs `python-multipart`
  for form parsing and would 500 every real callback.
- An unknown or absent `attempt_id` is a `400`, not a crash (test-covered).

### 13.1 A late press stops the ladder

`no_answer` only means *no keypress arrived inside the window*. A DTMF `1` that
arrives afterwards is still a human pressing the key, so
`acknowledge_attempt()` accepts it, upgrades the attempt row, and stops the
campaign **even if the ladder has already moved on**. Refusing it would keep
dialling while the caregiver is already on the way. Only a duplicate,
`canceled` or `failed` row is refused.

---

## 14. Secrets and configuration

**`.env` is untracked** — `.gitignore` has `.env` and `.env.*` with
`!.env.example`; `git ls-files` confirms only `.env.example` is tracked.

Placeholder **names only** go in `.env.example`:

```
EMERGENCY_CALLS_ENABLED=false
SAHAARA_EMERGENCY_PROVIDER=simulated
SAHAARA_EMERGENCY_REAL_CALLS_CONFIRMED=false
ALLOWED_TEST_NUMBERS=
SAHAARA_EMERGENCY_ACK_SECONDS=45
SAHAARA_EMERGENCY_RING_SECONDS=25
SAHAARA_EMERGENCY_RETRY_DELAY_SECONDS=20
SAHAARA_EMERGENCY_COOLDOWN_SECONDS=300
SAHAARA_EMERGENCY_MAX_ATTEMPTS=4
SAHAARA_EMERGENCY_PUBLIC_BASE_URL=
VONAGE_APPLICATION_ID=
VONAGE_PRIVATE_KEY=
VONAGE_API_KEY=
VONAGE_API_SECRET=
VONAGE_FROM_NUMBER=
```

- **Three real-call gates** must all be true, plus credentials, plus (when set)
  the destination number being in `ALLOWED_TEST_NUMBERS`. Anything short of that
  runs simulated with the reason logged.
- The JWT is signed with the application **private key**; the API key/secret are
  sent as HTTP basic auth on the call-creation request.
- No secret is ever logged, returned by an API, or written to an event.
- `EMERGENCY_CALLS_ENABLED` deliberately has **no** `SAHAARA_` prefix; every other
  emergency variable does.

---

## 15. Data model

```sql
CREATE TABLE IF NOT EXISTS emergency_incidents (
    id              TEXT PRIMARY KEY,      -- emg_<epoch>_<n>
    profile_id      TEXT NOT NULL,
    trigger         TEXT NOT NULL,         -- explicit_request | consent_accepted |
                                           -- physical_urgent | danger | physical_general_dwell
    trigger_phrase  TEXT,                  -- the person's own words, <=15 words;
                                           -- NULL for danger and explicit requests
    tier            TEXT NOT NULL,
    simulated       INTEGER NOT NULL DEFAULT 1,
    provider        TEXT NOT NULL DEFAULT 'simulated',   -- simulated | vonage   -- simulated | vonage
    status          TEXT NOT NULL,         -- open | acknowledged | answered_unacknowledged |
                                           -- exhausted | cancelled | failed
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
    attempt           INTEGER NOT NULL,    -- 1..4 across the incident
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

Caregiver routes are `require_caregiver` and profile-scoped. Webhook routes are
unauthenticated **by necessity** and signature-checked instead.

| Route | Auth | Purpose |
|---|---|---|
| `GET /emergency/incidents?limit=20` | caregiver | recent incidents with masked attempts |
| `POST /emergency/incidents/{id}/cancel` | caregiver | "I'm on it" → stops the ladder, ends `cancelled` |
| `POST /emergency/vonage/event?attempt_id=…` | signed webhook | call lifecycle |
| `POST /emergency/vonage/ack?attempt_id=…` | signed webhook | DTMF `1` → acknowledged |

Cross-profile access is a **404, not a 403** — a 403 would confirm the incident
exists. Cancel is idempotent and never rewrites a finished incident.

Deliberately **not** built: `POST /emergency/test-call`. A one-click dial button
is a foot-gun; the pre-flight test is manual and owner-approved.

---

## 17. Voice integration

**The reply.** The LLM keeps its warmth (name the feeling, stay present, urge
help). It is **not** told that a call was placed — the call line is appended
after `check_guardrails()`, verbatim, from `CALL_LINE_STATES`. That ordering is
the whole safety property: a model that hallucinates "I'm calling Rohan" has
no way to have that sentence survive, because the sentence we append comes from
provider state, not from the model.

Three deterministic guards run on every triggering turn:

1. `_strip_continuation_offer()` removes a trailing "Would you like me to go
   on?" — on a symptom turn that invites explanation instead of getting a person.
2. `_handle_action()` is **skipped** when `should_trigger_call`, so "call Rohan"
   is never answered with a timer confirmation.
3. The call line is appended only for the state the gates actually produced;
   `meta.emergency_call_line` carries it so the voice path and the text path say
   the same thing.

**Voice delivery.** `emergency_call_line` is spoken through the existing
initiative path, which closes the mic gate first, so it cannot be transcribed as
a new turn. If the provider answers after the turn has been sent, the line is
still spoken **once**; it is never repeated.

**Latency.** `process_turn` gains ~0 in the normal case — the orchestrator runs
on a daemon thread and the turn reply does not wait for the provider.
`MILD_DELAY_THRESHOLD = 3.5` and `REAL_DELAY_THRESHOLD = 8.0` are untouched.

**Text chat:** with no `/ws/voice` socket there is no spoken line. The call is
still placed and the dashboard timeline is the record — the same dashboard-only
rule as timers.

---

## 18. Test matrix

Every suite below exists and passes. Backend runs directly with
`.venv/bin/python tests/<file>.py` (pytest is not installed).

| Suite | Checks | Proves |
|---|---|---|
| `test_emergency_contacts.py` | 29 | ordering, `may_call` gating, missing phone skip, migration defaults, no invented number |
| `test_emergency_consent_order.py` | 72 | urgent beats a pending question; `superseded`, never `declined`; negated sentence never accepted |
| `test_emergency_orchestrator.py` | 47 | ladder Rohan→Priya, 2 attempts each, cooldown, cancel, exhausted, **late press-1 stops the ladder** |
| `test_emergency_dashboard.py` | 28 | REST surface, 404 isolation, idempotent cancel, disabled provider, reason vocabulary |
| `test_emergency_triggers.py` | 52 | all five triggers; step-down before dwell = no call; **dwell fires with the check-in flag off and monitoring paused** |
| `test_emergency_wording.py` | 87 | state→line matrix, claim-word gate, 112 placement, continuation-offer strip |
| `test_emergency_webhooks.py` | 36 | urlencoded + JSON bodies, JSON-string `dtmf`, status recording, 400/404 cases, no full number |
| `test_vonage_provider.py` | 74 | mocked HTTP: JWT auth, NCCO shape, ringing_timer, status map, signature accept/reject, hangup, gate refusal |
| `test_companion_stream.py` | 78 | all five emergency event types map to `None`; hostile details never leak |
| `frontend/tests/callStatus.test.ts` | 27 assertions | dashboard wording + 12 forbidden words by word boundary |

Regression after every task: `unit_e2e.py` (189), `test_guardrails.py` (13),
`test_offer_rate.py` (23), `test_policy_engine.py` (9), `test_wording_pools.py`,
`test_simulated_day.py`, `test_response_tracking.py`, `test_consent_push.py` (16),
`test_action_replies.py` (40), `guardrail_baseline.py --verify`, and — when the
frontend is touched — `tsc -b`, `npm run lint`, `npm run test:pwa`, `npm run build`.

**No test in this feature touches the network.** The Vonage suite mocks every
HTTP call; the webhook suite drives the routes directly.

---

## 19. Task breakdown

One at a time. Each ends with tests, a commit on `main` (never pushed), the
`agents.md` update, a report, then STOP.

| # | Task | Status |
|---|---|---|
| 1 | Contact schema (`phone`, `notify_order`, `may_offer_notify`, `may_call`) + migration + resolvers | ✅ `6cd2ab4` |
| 2 | **Safety fix:** urgent/danger classified before the consent block; `SUPERSEDED` outcome; `\bnot\b` narrowing | ✅ `d95803e` |
| 3 | Orchestrator + `SimulatedCallProvider` + incidents/attempts tables | ✅ `8a36966` |
| 4 | Dashboard surface (`callStatus.ts`, incident timeline, simulated banner) | ✅ `68f7da4` |
| 5 | Companion exclusion + forbidden-key test | ✅ in `test_companion_stream.py` |
| 6 | `VonageCallProvider` + signed webhooks + polling fallback | ✅ implemented, **never used against a live account** |
| 7 | Voice integration (claim stripper + deterministic call line) | ✅ |
| 8 | Docs, `.env.example`, full sweep, manual pre-flight checklist | ✅ this commit |
| 9 | **First live call** | ⛔ not started — needs the owner's "go" and §12.2 |

Task 6 stays behind `EMERGENCY_CALLS_ENABLED=false` until §12.2 passes with the
owner's explicit "go". The checklist the owner runs is in `agents.md`.

---

## 20. Open follow-ups (not this track)

1. **Voice WS `?token=` → short-lived single-use ticket or cookie** (decision 7).
   Out of scope here; recorded so it is not lost. Tokens must never reach logs or
   the event log.
2. `agents.md` §0 says the companion channel has 5 kinds; the code has 6
   (`reminder_fired` was added). Doc drift, recorded, not fixed in this commit.
3. `overview.md` still describes the pre-auth system; unchanged by this spec.
