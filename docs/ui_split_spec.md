# UI Split Spec — Companion View / Caregiver View

**Status:** Step 1 (recon + spec) and Step 2 (route split + companion channel) committed. Step 2 delivered: `/companion`, `/caregiver`, hidden `/?demo=split`, whitelisted companion WebSocket, caregiver-stream role check, `VITE_DEV_API_TARGET`. The full companion activity strip is Step 6; Step 2 ships the avatar, real clock and live caption.
**Date:** 2026-10-29
**Owner:** project owner
**Track:** UI/product structure. **Separate from M1/W2c.** This track does not change prompt policy text, `response_policy.py` checker logic, `guardrails.py`, or harness thresholds.

---

## 1. Goal

Split the single side-by-side page into two distinct experiences:

- **Companion view (Asha):** minimal, full screen, no login. Avatar + one small activity strip with real, working items. No tiers, alerts, timelines, "caregiver notified", or clinical language.
- **Caregiver view (Rohan):** behind login. Alert bell, live status/tier ladder, day timeline, Daily Summary, Review inbox, structured Profile form.

The current side-by-side layout is preserved behind a hidden `?demo=split` route so judges can watch both panes at once.

## 2. Non-goals

- No change to the conversation pipeline, escalation rules, guardrails, consent flow, or push send path.
- No new dependency (no router package, no state library).
- No iOS work.
- Step 7 (Memory Box playback) is **held** until explicit owner approval.
- Radio playback is not analysed for lip-sync (unchanged from A9).

---

## 3. Verified current state (2026-10-29, HEAD `fd6ee5a`)

### 3.1 Auth is real and enabled

`overview.md` is stale (lines 35, 318, 438, 531 still say "no authentication"). The code is the source of truth:

| Fact | Evidence |
|---|---|
| Enabled | `backend/.env` → `SAHAARA_AUTH_ENABLED=true` |
| Code default | `false` (`backend/auth.py:242`) |
| Seed account | `caregiver` / `demo123` from `SAHAARA_SEED_USER` / `SAHAARA_SEED_PASSWORD` |
| Password storage | scrypt N=16384 |
| Session | opaque 32-byte token, SHA-256 stored, 12 h expiry, HttpOnly `auth_token` cookie, `SameSite=Lax`, `Secure` via `SAHAARA_COOKIE_SECURE` |
| Roles | `caregiver`, `person`; only caregiver has a login UI (`App.tsx:156`) |
| Protected | caregiver dashboard/control routes, `/push/*`, `/auth/scheduler/*`, `/profile/*`, `/demo/reset` — all `Depends(require_caregiver)` |
| Open | `/`, `/health`, `/config`, `/chat`, `/ws/voice`, `/media/{filename}`, `/auth/login|logout|me|status` |
| Streams | token required, **role not checked** (`dashboard/stream.py:64-87`, `159-190`) |

Two consequences for this track:

1. `/chat` and `/ws/voice` carry no identity and always use the default profile. This is what makes a no-login companion possible; it also means anyone who can reach the port/tunnel can talk to Asha.
2. A `person`-role token can currently subscribe to the caregiver stream. **Fix in Step 2** with a role check and a test.

### 3.2 API base

No `127.0.0.1:8000` remains in `frontend/src` or `frontend/public`. `frontend/src/api.ts` is the single module:

- `API_BASE` defaults to `/api`, override `VITE_API_BASE_URL`
- `apiFetch` adds `credentials: "include"`
- `wsUrl` derives from the page origin, override `VITE_VOICE_WS_URL`

The only hardcode is the Vite **dev proxy target** (`frontend/vite.config.ts:6`). Step 2 makes it `VITE_DEV_API_TARGET` with the same default.

### 3.3 Streams the frontend uses

| Stream | Where | Carries |
|---|---|---|
| `/stream/ws/events` | `eventStream.ts:72` (shared, ref-counted; `EscalationLadder`, `TaskAlarm`) | every `EventType`: tier_change, alert, alert_sent, conversation_turn (both speakers), repeat, distress, consent_*, fallback, danger_statement, physical_symptom, memory_box_played, action, reminder_fired, session_*, timing, system_health |
| `/ws/voice` | `useVoiceSession.ts:30,383` | state, audio, play_stream, stop_media |

No SSE subscription remains in the frontend (`openEventSource` exists but is unused). **The companion must not subscribe to `/stream/ws/events`.**

### 3.4 The two screenshot bugs (diagnosed, not fixed)

**Bug 1 — song request routed to radio, then hard fallback.**

Chain:

1. `detect_action("Can you play a song")` → `play_song("")`.
2. `backend/voice/assets/music/` is empty.
3. `play_song` falls through to `play_radio("")` by design (`actions.py:637-645`).
4. `play_radio("")` probes stations **sequentially** with `_stream_alive(url, timeout=4.0)` (`actions.py:727-747`, `807-818`). BBC is third; worst case ≈12 s.
5. `_llm_reply` runs `process_turn` in `asyncio.to_thread` (`voice_pipeline.py:177`), so the filler timers fire on time. The action runs before the LLM call, so the turn exceeds `REAL_DELAY_THRESHOLD = 8.0` (`voice_pipeline.py:85-89`).
6. The ladder logs `STANDBY_HARD_FAILURE` and plays the hard-failure filler (`voice_pipeline.py:368-380`). Cancelling the asyncio task does not stop the worker thread, so `process_turn` still logs `ACTION play_radio success=true` and `CONVERSATION_TURN "Playing BBC World Service"`.

**Worse:** `play_stream` is only yielded with the successful reply, so no audio reached the browser — yet the dashboard records a successful action and a spoken reply, and `mark_media_playing()` set server-side playback state. The reply text in the screenshot is a log entry, not something Asha heard.

**Bug 2 — "Why did Sahaara say this?" is generic.**

The panel wiring is fine (`EscalationLadder.tsx:352` refetches on every Sahaara `conversation_turn`). The trace is empty:

- Action path logs the turn with **no** `context_trace` (`conversation.py:482-483`).
- Identity (`conversation.py:194`) and continuation (`conversation.py:575`) also log `{}`.
- `_build_explanation_text({})` returns the generic sentence (`main.py:514-527`).
- The trace has no action/fallback/delivery fields, and missing profile facts produce fabricated defaults ("from home and was a teacher").

---

## 4. Route and role model

| Route | Access | Renders |
|---|---|---|
| `/` | none | logged in → `/caregiver`; else caregiver login |
| `/caregiver` | caregiver session | tabs: Live, Daily Summary, Review, Profile + bell + push + logout |
| `/companion` | **none** | full-screen avatar + one activity strip |
| `/?demo=split` | caregiver session | current side-by-side layout; no nav link |
| anything else | — | redirect `/` |

- A ~40-line `useRoute` hook (`history.pushState` + `popstate`), no new dependency.
- `App.tsx` becomes a shell; new `CompanionView.tsx`, `CaregiverView.tsx`, `DemoSplitView.tsx`.
- `AuthContext` is unchanged.
- The companion view makes **no** auth, push, escalation, alert, summary, suggestion, profile or `/turn/context` calls.
- `/companion` is reachable without login; `/caregiver` and `?demo=split` keep the existing login gate.

### Proposed decision D1 — companion exposure

The companion is unauthenticated by design. Locally and over `adb reverse` this is acceptable. Over a public tunnel, anyone with the URL could read captions and talk to Asha. **Recommended:** accept for the demo, document it, and demo over `adb reverse`. If a public tunnel is required, add a non-login device secret (URL segment or `SAHAARA_COMPANION_KEY`) — not a user account. Step 2 implements the recommendation unless overridden.

### Proposed decision D2 — stream role check

Add `role == "caregiver"` to `/stream/events` and `/stream/ws/events`. A `person` token must receive 403. **Recommended: yes, in Step 2.**

---

## 5. Data visibility contract

| Companion may see | Caregiver may see | Companion must never see |
|---|---|---|
| avatar state (`idle/listening/thinking/speaking/error`) | everything current | tier / escalation state |
| assistant caption (Sahaara's own reply) | alerts + push record | alerts, alert_sent, acknowledgements |
| time and date | day timeline (both speakers) | timeline / event log |
| now playing (real playback) | Daily Summary | daily summary |
| reminder countdown (real pending reminder) | Review inbox | suggestions / review items |
| next routine (profile meals/wake/sleep) | structured profile + health notes | profile facts, health notes, diagnosis |
| — | scheduler controls, push controls | consent state, danger/repetition/distress flags |
| — | — | the user's transcribed speech (caption is assistant-only) |

The companion caption is **assistant text only**. `conversation_turn` for `speaker="user"` is never mapped.

---

## 6. Companion channel — privacy by construction

New `backend/dashboard/companion.py` + `/stream/ws/companion` (WebSocket, because the Quick Tunnel buffers SSE bodies; `docs` and M5 history record that).

### 6.1 Allowed kinds (the complete list)

| Kind | Payload | Source |
|---|---|---|
| `caption` | `{text, at}` | `CONVERSATION_TURN` with `speaker="sahaara"` only |
| `now_playing` | `{title, kind: "song"\|"radio", playing: bool}` | successful media `ACTION` (client verifies against its own `<audio>`) |
| `reminder_countdown` | `{id, label, fire_at, seconds_remaining}` | `one_time_reminders` (`list_pending_reminders`) |
| `next_routine` | `{label, at}` | profile `daily_routine` (wake/meals/sleep) |
| `clock` | `{now, date}` | server snapshot; client uses its own clock for display |

No other kind exists. There is no generic `event` envelope and no passthrough of raw event-log details.

### 6.2 Privacy invariants

1. The channel never forwards `Event` objects. `observe(event)` maps to a typed `CompanionEvent` or returns `None`.
2. `caption` rejects `speaker != "sahaara"`.
3. Per-kind payload key whitelist; unknown keys are dropped.
4. No payload may contain keys matching `tier|alert|consent|danger|symptom|emotion|severity|distress|repeat`.
5. The companion endpoint has no handler that reads `/escalation`, `/alerts`, `/suggestions`, `/turn/context`, or the caregiver stream.
6. Snapshot sends only items with real data; absent data is omitted, never faked.

### 6.3 Test (`backend/tests/test_companion_stream.py`)

- Feed **all 17 `EventType`s** with hostile details (red tier, alert, consent, danger, physical symptom, fallback, user text) → only the allowed mapping may be produced.
- `tier_change`, `alert`, `alert_sent`, `consent_asked`, `consent_answered`, `danger_statement`, `physical_symptom`, `repeat`, `distress`, `fallback` → **zero** companion events.
- `conversation_turn` speaker `user` → zero; speaker `sahaara` → one `caption`, no emotion/tier.
- Payload key whitelist and forbidden-key scan.
- `snapshot()` omits missing reminders/routine.
- `/stream/ws/events` role test: caregiver accepted, `person` 403.

---

## 7. Step-by-step plan

### Step 1 — Recon + spec (this document)

Commit this file plus the `agents.md` maintenance entry. No feature code. Stop.

### Step 2 — Route split + companion channel

**Files:** `frontend/src/useRoute.ts` (new), `CompanionView.tsx`, `CaregiverView.tsx`, `DemoSplitView.tsx`, `App.tsx`, `App.css`, `frontend/vite.config.ts`; `backend/dashboard/companion.py`, `backend/dashboard/stream.py`, `backend/main.py`, `backend/tests/test_companion_stream.py`.

**Acceptance:**

- `/companion` renders avatar + strip only; no caregiver fetch on load.
- `/caregiver` requires login; `/` goes to login.
- `?demo=split` renders the old layout; no nav link.
- Companion WS emits only the five kinds.
- All 18 event types proved unable to leak tier/alert/consent/danger.
- `person` token cannot read `/stream/*`.
- `VITE_DEV_API_TARGET` replaces the hardcoded dev proxy target.
- Manual: devtools network tab on `/companion` shows only `/stream/ws/companion`, `/ws/voice`, static assets. No `/escalation/*`, `/alerts*`, `/daily-summary`, `/suggestions*`, `/turn/context`, `/stream/ws/events`, `/auth/me`, `/push/*`.
- Gates: `tsc -b`, lint, `unit_e2e`.

**Delivered (2026-10-29, commit recorded in `agents.md`):** the route split lives in `App.tsx` + `route.ts` / `useRoute.ts` rather than three view shells; the hidden split view reuses the existing `AshaVisual` + caregiver section. The caregiver route is now single-column with no avatar. The companion view is avatar + real clock + live caption; the channel already carries now-playing/reminder/next-routine for Step 6. Companion smoke: network tab showed **zero `/api/*` requests** on load, no auth form/tabs/header, and only `/api/stream/ws/companion` sockets (plus Vite's own HMR socket, which uses a `?token=` HMR token — not an app session). `person` SSE/WS → **403** live; caregiver → 200. `test_companion_stream.py` **73/73**.

### Step 3 — Caregiver reorganisation

- Daily Summary only under `/caregiver` (already tab-gated; keep it).
- Rename "Suggested Facts" → **Review** with a count badge next to the alert bell.
- Explainer at the top: *"While chatting, Sahaara notices things about Asha (a song she likes, a memory she shared). Nothing is added to her profile until you approve it."*
- Empty state: *"Nothing to review right now."*
- Approve/reject behaviour and "nothing auto-added" unchanged.
- Badge count from `GET /suggestions`; refresh on mount and every 30 s while the caregiver view is visible.
- Gates as Step 2.

### Step 4 — Fix the two bugs

**Routing**

- Generic "play a song" with no local match: `play_song` returns a **failure** ("I couldn't find that song in your music. I can play the radio instead — just say 'play the radio'."). It must **not** call `play_radio` and must not claim playback.
- Named song: local fuzzy match only by default. The existing iTunes preview path stays behind `SAHAARA_SONG_PREVIEW` (default **off**) and, when enabled, still says "a short clip", never "the song".
- Radio plays only when explicitly asked.

**Timing**

- `_stream_alive` probes run **in parallel** (max 3 workers) with a 1.5 s timeout, and the result is cached for 60 s per URL. Worst case ≈2 s.
- No generic song request probes stations at all (it no longer calls radio).
- Add a per-action elapsed log; if the action exceeds 2.5 s, log it and return an honest "that took too long" failure instead of claiming success.

**Delivery honesty**

- `process_turn` returns a `turn_id` and includes it in the turn event.
- The voice pipeline logs `TURN_DELIVERY` (`spoken` / `fallback` / `error`) after the audio path resolves; the timeline shows "Reply replaced by fallback — Asha heard the standby message."
- Action events record `delivered` so a discarded reply cannot look spoken.

**Context trace**

- Every return path logs a trace: `reply_source` (`llm`, `action`, `identity`, `consent`, `continuation`), `action` (`{type, success, detail}` or `null`), `profile_facts_used`, `intent`, `memory_box`, `escalation`, `guardrail`, `fallback`, `tier`, `turn_id`, `at`.
- `/turn/context` returns `turn_id`, `at`, `at_iso`, `turn_text`, `context_trace`, `explanation`.
- `_build_explanation_text` handles action/identity/consent/continuation and never fabricates missing hometown/work.
- The panel shows "Latest reply · HH:MM", refreshes on `conversation_turn`, and shows "No explanation available yet." on error instead of rendering nothing.

**Tests**

- Generic song + empty folder → failure, radio not called, no "playing" claim.
- Named song no local match → failure, iTunes not called with the gate off.
- Radio only on explicit request.
- Parallel probe under 2.5 s with fake slow probes; cache hit avoids a second probe.
- Action path trace present; explanation names the failed action and the offered alternative.
- Identity/consent/continuation traces present.
- `_build_explanation_text({})` no longer says "from home / was a teacher".
- `guardrail_baseline.py --verify tests/baseline_guardrails_pre.json` → no drift.

### Step 5 — Structured Profile form

Sections, all optional except the existing required keys:

1. **Basics**
2. **Family & contacts**
3. **Life story**
4. **Likes & what soothes her**
5. **Sensitive topics**
6. **Daily routine**
7. **Health notes (caregiver record)**
8. **Communication tips**

Full schema in §8.

**Prompt rule:** `build_prompt` reads only shared fields. Health details never enter the prompt. Medication times feed the scheduler only. Do not edit policy wording or the structure checker; only render new shared fields into the existing profile context block.

**Tests:** sentinel strings for caregiver-only fields absent from `build_prompt`; old-format profile loads; `PUT /profile/{id}` round-trips every new field.

**Before/after:** `guardrail_baseline.py --verify` (no drift) and `harness --scenario all --runs 5` (guardrails 1.000, zero fallbacks). Save the post run as `baseline_post_profile_form.json`, labeled; report the table and 5-6 untruncated replies.

### Step 6 — Companion activity strip

- Only real data: time/date (client clock), now playing (the page's own `<audio>` state), reminder countdown (`one_time_reminders`), next routine (profile meals/wake/sleep). Hide any item with no data.
- No medication items on the companion (health is caregiver-only by default).
- Captions of Sahaara's last reply, with a toggle that persists in `localStorage`.
- Calm palette, large tap target (≥96 px), no tier colours, no alert language.
- Mic muted while the `<audio>` element is playing, not only while TTS speaks. Note the limitation: the server `mic_gate` covers TTS, not radio; the client-side gate covers both.
- Tests: pure `companionStrip.ts` visibility/formatting tests; `shouldMuteMic(state, playback)` test; manual script (set a reminder, play a song, watch the strip).

### Step 7 — Memory Box playback (HOLD)

Do not start. When approved: playback endpoint, consent-gated trigger, mic muted during playback, never offered for deceased family (Vikram), replay rule, honest placeholder labeling.

---

## 8. Profile schema v2 (pinned)

All new keys are **optional**. Existing keys keep their meaning. No DB migration: the profile is stored as one JSON document in `profiles.data`.

```jsonc
{
  "profile_id": "asha_demo_001",

  "name": { "full_name": "...", "preferred_address": "...", "notes": "..." },   // unchanged

  "basics": {
    "preferred_name": "Asha",                 // mirrors name.preferred_address
    "language_mix": ["Marathi", "Hindi", "English"],
    "age": 78,                                // integer or null
    "city": "Pune"
  },

  "family": [
    {
      "name": "Priya",
      "relationship": "daughter",
      "living_status": "living",              // "living" | "late"
      "phone": "+91...",                      // caregiver-only, never in prompt
      "may_offer_notify": true,               // may Sahaara offer to notify?
      "notify_order": 1,                      // integer or null; lower = first
      "notes": "...",
      "memory_box_clip": "priya_hello.mp3"    // app-only; never in prompt
    }
  ],

  "life_story": {
    "hometown": "Pune, Maharashtra",
    "work": "Retired schoolteacher...",       // "career" in the form maps here
    "key_events": ["..."],                    // existing
    "key_memories": ["..."],
    "proud_moments": ["..."]
  },

  "likes": {
    "music": ["..."],
    "food": ["..."],
    "topics": ["..."],
    "routines": ["..."],
    "activities": ["..."],
    "calming_strategies": ["..."]             // "what calms her when anxious"
  },

  "avoid": {
    "topics": ["..."],                        // string or {topic, setting, redirect_to, notes}
    "notes": "...",
    "when_asking_for_deceased": "..."          // shared wording guidance
  },

  "daily_routine": {
    "wake_time": "07:00",
    "meals": { "breakfast": "08:00", "lunch": "13:00", "dinner": "19:30" },
    "sleep_time": "21:30",
    "restless_window": { "start": "17:00", "end": "20:00" },
    "medication_times": ["08:30", "20:00"],   // scheduler source, unchanged
    "notes": "..."
  },

  "health_notes": {
    "conditions": ["..."],
    "medications": [{ "name": "...", "time": "08:30", "notes": "..." }],
    "allergies": ["..."],
    "doctor_contact": { "name": "...", "phone": "..." },
    "hearing": "...",
    "vision": "...",
    "mobility": "..."
  },

  "communication_tips": ["Speak slowly", "Use her name", "Don't correct her"],

  "meta": {
    "visibility": {
      "basics": "shared",
      "family.phone": "caregiver_only",
      "health_notes": "caregiver_only"
      // any path may be overridden; missing = default below
    }
  }
}
```

### 8.1 Visibility defaults

| Path | Default | Flips allowed? |
|---|---|---|
| `basics.*` | shared | yes |
| `family.name`, `relationship`, `living_status`, `notes` | shared | yes |
| `family.may_offer_notify`, `notify_order` | shared | yes |
| `family.phone` | caregiver_only | **no (hard-locked)** |
| `family.memory_box_clip` | caregiver_only | **no (app-only)** |
| `life_story.*` | shared | yes |
| `likes.*` (incl. `calming_strategies`) | shared | yes |
| `avoid.*` (incl. `when_asking_for_deceased`) | shared | yes |
| `daily_routine.wake_time`, `meals`, `sleep_time`, `restless_window` | shared | yes |
| `daily_routine.medication_times` | caregiver_only | **no (scheduler-only)** |
| `health_notes.conditions` | caregiver_only | **no (hard-locked)** |
| `health_notes.medications` | caregiver_only | **no (hard-locked)** |
| `health_notes.allergies` | caregiver_only | **no (hard-locked)** |
| `health_notes.doctor_contact` | caregiver_only | **no (hard-locked)** |
| `health_notes.hearing`, `vision`, `mobility` | caregiver_only | yes (practical communication aids) |
| `communication_tips` | shared | yes |

"Shared" means **may enter the prompt**. The caregiver still sees everything in the form.

### 8.2 Prompt rendering

`Profile.shared_view()` deep-copies the profile and removes every caregiver-only path. `build_prompt` renders only from that view:

- existing block: preferred address, hometown, work, likes topics, family summary, avoid topics
- new shared lines:
  - `Calming strategies: …` (max 5)
  - `Communication tips: …` (max 5)
  - `People Sahaara may offer to notify: Priya (daughter), Rohan (son)` (ordered by `notify_order`; names/relationships only, no phone)
  - `When she asks for someone who has died: …` (from `avoid.when_asking_for_deceased`)

No health key, phone number, medication, condition, allergy, or doctor contact can appear. `build_prompt` never reads `health_notes` directly.

### 8.3 Migration and round-trip

- `Profile._validate` keeps the existing required keys; new keys are optional with defaults.
- Old JSON documents load unchanged; `visibility()` computes defaults when `meta.visibility` is absent.
- `GET /profile/current` returns the stored document; the form fills defaults client-side.
- `PUT /profile/{id}` replaces the document and reloads the session (existing behaviour). The form sends the full document, so every new field round-trips.
- No automatic write-back of defaults (avoids churn on old profiles).
- No new SQLite tables.

---

## 9. Test matrix

| Step | Backend | Frontend | Safety |
|---|---|---|---|
| 2 | `test_companion_stream.py`; role check on `/stream/*` | `useRoute` tests; no caregiver fetch on `/companion` | whitelist proof for all event types |
| 3 | — | Review badge/empty state | approve/reject unchanged |
| 4 | song routing, probe timing, trace, delivery | why-panel text | `guardrail_baseline --verify` no drift |
| 5 | sentinel prompt test, old-profile load, PUT round-trip | form sections + visibility toggles | harness 5-run pre/post, `baseline_post_profile_form.json` |
| 6 | companion snapshot/reminder data | strip visibility, caption toggle, mic gate | no clinical/tier data |

Standard gates after each step: `npx tsc -b`, `npm run lint` (baseline 9 warnings), `npm run test:pwa` (baseline 65/65), `unit_e2e.py`, and `guardrail_baseline.py --verify` where Step 4/5 says so.

---

## 10. Decisions requested

| # | Decision | Recommended |
|---|---|---|
| D1 | Companion exposure over a public tunnel | accept + document; demo over `adb reverse`; add a device secret only if a public tunnel is required |
| D2 | Role check on `/stream/*` in Step 2 | yes |
| D3 | `?demo=split` behind caregiver login | yes (it shows caregiver data) |
| D4 | Named-song fallback | local-only by default; iTunes preview behind `SAHAARA_SONG_PREVIEW=off` |

Step 2 implements the recommendations unless the owner overrides before saying "go".

**Recorded 2026-10-29:** the owner said **"proceed"**; no D1-D4 override arrived, so Step 2 implemented the recommended column. D1 is accepted **for the demo**: document the exposure and demo over `adb reverse`; add a device secret only if a public tunnel is required. D4 is not touched yet (Step 4).

---

## 11. Risks

- **Companion is unauthenticated.** Captions and chat are reachable by anyone who can reach the server. Mitigation: same-origin demo, `adb reverse`, documented; no caregiver data on the channel.
- **Two voice sessions.** `/companion` and `?demo=split` in two tabs would compete for the mic. Use one at a time; the split view is the single-page demo path.
- **Profile blob replacement.** `PUT` replaces the whole JSON. The form must send the complete document; a stale client could drop new fields. Mitigation: fetch before edit, round-trip test.
- **Prompt growth.** New shared fields change the prompt and may move harness numbers. Mitigation: cap list lengths, pre/post 5-run comparison, no policy/checker edits.
- **Radio probes.** Even parallel probes can be slow on a bad network. Mitigation: 1.5 s per station, 60 s cache, no probing on generic song requests.
- **Tunnel SSE.** Still buffered. The companion channel is WebSocket-only.

---

## 12. What this spec deliberately does not change

- `backend/safety/guardrails.py`
- `backend/llm/response_policy.py` checker logic or policy wording
- harness thresholds
- escalation tiers/rules
- consent flow
- push send path
- M1/W2c
- Step 7 / Memory Box playback
