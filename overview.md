# Sahaara — Project Overview & Analysis

> Generated from a full read of the codebase on 2026-10-02.
> **Last verified: 2026-10-23 @ `6cb0cca`** — every factual claim in §1–§6 and the
> appendix was re-checked against source files or re-run on that date; §7–§10 carry a
> status tag per item. Claims that could not be confirmed are marked **UNCLEAR**.
> **Project status, milestones, and the change log live in `agents.md` (§0)** — this
> document is analysis, not status.

---

## 1. What the project is

**Sahaara** is a consent-based AI voice companion for people living with dementia, paired
with a quiet, meaningful escalation channel for family caregivers.

- **The person** (demo persona: *Asha Kulkarni*, `asha_demo_001`) talks to Sahaara by voice
  (or text) — it listens, validates emotion, gently grounds, and offers one small choice.
- **The caregiver** (demo persona: *Rohan*, Asha's son) gets a dashboard with a
  Green/Yellow/Orange/Red escalation ladder, context-rich alerts, a live event feed, and a
  daily summary — **noise-reduced by design** ("3 repeated questions → 1 quiet summary line").

Core product principles (source of truth: `agents.md` §6):

- Does **not** diagnose, treat, or replace human care.
- **Never** impersonates a family member or claims to be human.
- **Asks consent before escalating** — it never silently calls a caregiver (danger/Red is
  the exception by design; an unresponsive-detection path exists but is disabled).
- Repetition is handled **silently** — no "I already told you," ever.
- Escalation framing is "notify a human when a pattern crosses a threshold" — never
  "detects falls" / "detects wandering" (clinical-claim avoidance is a product feature).

Built as a hackathon-grade system: SQLite + JSONL persistence, single-process in-memory
state (per-profile objects via `session_manager.py`), demo time compression hardcoded on,
no authentication.

---

## 2. What it currently does (implemented features)

*(All items re-verified 2026-10-23 unless explicitly noted.)*

### Conversation (text + voice, one shared pipeline)
- **Text chat** via `POST /chat` — full turn pipeline (see §3).
- **Live voice** via `WS /ws/voice` — real mic → real ASR → real LLM → real TTS loop:
  - Browser captures mic (16 kHz PCM16), streams to server.
  - **Deepgram Nova-3** streaming ASR with `endpointing=300` + `utterance_end_ms=1000` —
    UtteranceEnd is the real "they're done" signal, so a mid-thought pause does **not**
    end the turn. *(Live Deepgram not re-tested in this pass — last known working.)*
  - **Instant "Mm." ack** played *before* the LLM call starts (pre-rendered filler, cached
    on disk in `backend/voice/assets/*.raw`).
  - **Standby ladder** (`MILD_DELAY_THRESHOLD=2.5`, `REAL_DELAY_THRESHOLD=5.0`): at 2.5 s a
    mild-delay filler plays and the turn closes; at 5 s the safe fallback line plays. Late
    LLM responses are discarded. The person never hears dead air.
  - **Cartesia TTS** streaming (sentence-level chunks, 22.05 kHz PCM) with gapless
    playback in the browser. **Model: `sonic-2` — sunsetted Oct 20 2026, pinned only for
    demo voice continuity.** Request schema modernized to API `2026-08-14` (commit
    `259a438`). A blind A/B (`tests/voice_ab.py`, 6 replies × 3 models) decides migration
    to `sonic-3.6-2026-08-27`.
- **Nemotron 3 VoiceChat** path: scaffolded and gated — `check_voicechat_availability()`
  returns `False` (no NVIDIA key → False; with key → still False, probe is a TODO). The
  cascaded path is what runs.

### Intent & emotion understanding
- Rule-based (regex) classification: emotion (anxious/distressed/sad/content/calm/neutral),
  emotional-need vs factual-ask, **danger statements** → instant RED, and **physical
  distress** (added Oct 19): urgent ("can't breathe", "chest pain", "I fell") → **Red** on
  turn 1; general ("not feeling well", "dizzy") → **Orange** on turn 1, logged as
  `PHYSICAL_SYMPTOM` with the exact phrase.
- An LLM-based classifier (`classify_with_llm()`, same output shape) exists — **not wired**
  to any caller.

### Memory system
- **"This Is Me" profile** (`backend/memory/data/asha_profile.json`): name/preferred
  address, family (relationships incl. "late husband"), life story, likes, caregiver-set
  **avoid topics** — now tri-state per topic: `avoid` / `redirect` / `allow_with_care`
  with an optional redirect target — and daily routine (meals, medication times).
  `"editable": true`.
- **Memory Box**: matches longing phrases + a family member's name/relationship to a
  recorded clip (`priya_hello.mp3` exists on disk; labeled a placeholder in `README.txt`)
  and injects an *offer* line into the prompt. Re-offer suppression: **5 turns AND 10
  minutes** (both must pass); an explicit "play it again" bypasses it. **The clip itself
  is never actually played** — there is no playback endpoint (see §6).
- **Session notes**: fuzzy repetition detection (`SequenceMatcher` ratio ≥ 0.6, 15-minute
  window) and sustained-distress detection (≥ 60% of ≥ 3 recent turns anxious/distressed).
- **Session summaries** (`memory/summarizer.py`): 2–3 sentence recap of the previous
  session injected into the system prompt as `{session_context_block}` ("yesterday"
  context).
- **Suggested Facts** (`memory/suggestions.py`): detects music/memories/routines/food from
  conversation into a pending queue; the caregiver approves/rejects in the dashboard;
  approved items write to the profile. **Nothing is auto-added.**

### Proactive behaviors (Phase 3)
- **Scheduler** (`scheduler.py`): background task reads profile routines — morning
  greeting at wake time, evening check-in, medication reminders (**reminders only, never
  dosing**), meal nudges; dedup per day+category; caregiver toggle (`/scheduler/toggle`);
  pushes `sahaara_initiative` events to the dashboard timeline.
- **Safe actions** (`actions.py`): `tell_time()` (gentle clock reading), `play_song()`
  (fuzzy-matches files in `voice/assets/music/`), `set_reminder()` (one-shot into the
  scheduler). Action results are injected into the prompt as facts — **Sahaara only claims
  an action that actually succeeded.**

### Safety
- **Pre-generation policy** (`llm/response_policy.py`): one system-prompt template
  enforcing Listen → Validate → Ground → Offer one choice → Escalate-by-asking, plus
  conditional blocks for avoid-topics, Memory Box offer, repetition, sustained distress,
  physical distress, and RED/danger.
- **Post-generation hard gate** (`safety/guardrails.py`): **8 pattern families** —
  diagnosis, dosing advice, impersonation, imminent-event ("on his way", "will call
  tonight"), and fabricated **whereabouts / schedule / weather / surroundings** — plus a
  **profile-aware deceased check** (`_is_deceased()`: the relationship field contains
  "late"), so "Vikram is on his way" fails while grounded reassurance about living family
  passes. Any violation → `SAFE_FALLBACK_RESPONSE` + logged.
- **Consent state machine** (`safety/consent.py`): "Would you like me to call X?" → the
  next utterance is interpreted as accept / decline / **timeout = decline** (30 s real
  time; compressed in demo mode). Topic-shift heuristics treat an unrelated new question
  as a decline.
- **Escalation engine** (`safety/escalation.py`): deterministic threshold logic, no ML —
  fully reproducible. Hysteresis, deliberate step-downs, RED terminal for the session;
  `force_tier()` bypasses hysteresis for danger/physical triggers.

### Caregiver dashboard (backend + frontend)
- **Event log** (`events/event_log.py`): append-only JSONL
  (`backend/events/data/asha_demo_001.jsonl` — **6,372 events** as of 2026-10-23) plus an
  in-memory list for streaming. Every repetition, distress signal, tier change, consent
  ask/answer, fallback, danger statement, physical symptom, memory-box play, and
  conversation turn (both the user message and Sahaara's reply) is recorded. Single source
  of truth for the dashboard.
- **Real-time streaming**: SSE (`/stream/events`) + WebSocket (`/stream/ws/events`) + two
  polling WebSockets (`/ws/alerts`, `/ws/escalation`). `dashboard/stream.py` wraps the
  session's `event_log.append` (and consent/alert-builder methods) to broadcast — installed
  by `setup_stream_hooks()` **at app startup** (Phase 4 moved it off import time).
- **Alerts**: `AlertStore` with unread/acknowledged state; `GET /alerts` returns the store
  (it was an empty stub before Phase 1); `POST /alerts/{id}/acknowledge` ("Seen by Rohan"
  only shows after ack); `GET /alerts/{id}/context` returns the alert plus surrounding
  conversation turns, the trigger event, the tier path, and a recommended action. Alert
  copy itself passes guardrail checks.
- **Daily summary** (`dashboard/summary.py`): aggregates the event log with the
  noise-reduction framing; optional LLM wording enhancement (**off by default**,
  `use_llm=False`).
- **Caregiver controls**: monitoring `pause`/`resume`, a visible **Reset Demo** button, and
  the scheduler toggle.
- **Demo time compression**: 300× — `EscalationConfig(demo_mode=True, demo_multiplier=300.0)`
  is hardcoded in `session_manager.py:99`. A real 15-minute escalation pattern completes
  in ~30 seconds on stage. Demo-only, never production.

### Frontend
- Four tabs: **Live** (VoiceOrb + text chat fallback), **Daily Summary**, **Suggested
  Facts** (approve/reject), **Profile** ("About" editor: name, family, likes, routines,
  per-topic settings — saved via `PUT /profile/{id}`, effective immediately).
- **VoiceOrb**: tap-to-talk orb with low-arousal state colors, pulse animations, full mic +
  playback audio engineering (downsampling, PCM conversion, gapless chunk scheduling). The
  echo bug is **fixed** (zero-gain GainNode, `VoiceOrb.tsx:177`). Still uses the deprecated
  `ScriptProcessorNode` — in-code comment flags the AudioWorklet swap as a later upgrade.
- **EscalationLadder**: live status cards (tier, time-in-tier, repeat count vs threshold,
  distress duration), the 4-tier ladder with reason + hold duration, the **Day Timeline**
  (conversation turns, memory-box plays, scheduler initiatives), a system-health panel, and
  the **AlertCenter** bell (unread badge, tier colors, ack button, red-alert flash).
- UI **professionally redesigned** Oct 20 (commit `5fdab86`, six CSS files).
- Lint: `oxlint` (`npm run lint`); typecheck: `tsc --noEmit` — **clean as of 2026-10-23**.
  No frontend test setup.

### Tests
- `tests/unit_e2e.py` — **143/143 pass (run 2026-10-23)**; the main suite. *(Still
  untracked in git — see `agents.md` §9.)*
- `tests/test_offer_rate.py` — **23/23 pass**; `tests/test_guardrails.py` — **13/13
  adversarial pass**; `tests/test_policy_engine.py`, `tests/test_repetition_handling.py` —
  pass.
- `tests/harness.py` — 10-scenario live-LLM harness with regression thresholds (see
  `agents.md` §10). **Live runs currently blocked by Nebius 402 credit exhaustion.**
- `tests/voice_ab.py` — blind 3-model TTS A/B renderer; `tests/analyze_voice_timing.py` —
  voice latency analysis.
- The 3 stale files (`test_escalation`, `test_consent`, `test_alerts`) were **deleted**
  Oct 20. **pytest is not installed** — by design now; suites run directly with Python.

---

## 3. How it works (end-to-end flow)

```
┌──────────────────────────────  PERSON SIDE  ──────────────────────────────┐
│  Browser (VoiceOrb.tsx)                                                  │
│  mic → ScriptProcessorNode → downsample to 16 kHz PCM16 → WS binary     │
└──────────────────────────────────┬────────────────────────────────────────┘
                                   ▼
                         WS /ws/voice (main.py)
                                   ▼
               voice_pipeline.run_cascaded()
                                   │
        ┌──────────────────────────┴──────────────────────────┐
        │ Deepgram Nova-3 streaming ASR (asr.py)             │
        │ interim results → UI only                          │
        │ UtteranceEnd → finished turn                       │
        └──────────────────────────┬──────────────────────────┘
                                   │
                    state → "thinking" + instant "Mm." ack
                                   │
        ┌──────────────────────────┴──────────────────────────┐
        │ Standby ladder (asyncio race):                     │
        │   LLM reply  vs  2.5 s mild filler  vs  5 s hard  │
        └──────────────────────────┬──────────────────────────┘
                                   │
                    process_turn()  (conversation.py)
                    — SAME pipeline as POST /chat —
                                   │
   1. Consent check: pending consent? interpret this utterance
      (timeout / topic-shift → decline)
   2. classify_intent(): emotion / emotional-need / factual / DANGER
      / physical distress (urgent → RED, general → ORANGE)
      └─ danger statement → force RED tier
   3. Safe actions: tell_time / play_song / set_reminder
      → results injected as prompt FACTS (only if they succeeded)
   4. Memory: MemoryBox.match() · SessionNotes.is_repeated_question()
      · sustained_distress()
   5. EscalationEngine.evaluate() — deterministic thresholds + hysteresis
      └─ tier change → AlertBuilder builds context-rich alert → AlertStore
   6. build_prompt() — Listen/Validate/Ground/Offer/Escalate policy
      + profile context + tri-state topic settings + Memory Box offer +
      repetition/distress/RED blocks + {session_context_block} ("yesterday")
   7. LLM: NemotronClient → Nebius Token Factory (OpenAI-compatible)
      default model: Nemotron-3-Nano-30B-A3B, thinking disabled,
      reasoning_content fallback for empty-content gotcha
   8. Post-generation: check_response_structure() (QA heuristic)
      + check_guardrails() (HARD GATE: 8 pattern families — diagnosis /
      dosing / impersonation / imminent-event / fabricated whereabouts,
      schedule, weather, surroundings — vs profile)
      └─ violation → SAFE_FALLBACK_RESPONSE, logged
   9. Consent detection in response ("Would you like me to call X?")
      → ConsentManager.ask_consent() (yellow/orange only)
   10. SessionNotes.record_turn() + suggestion detection + context trace
                                   │
        ┌──────────────────────────┴──────────────────────────┐
        │ LLM won → state "speaking" → Cartesia TTS          │
        │ (sonic-2 pinned, sunsetted — see agents.md §0)     │
        │ streaming TTS → PCM chunks → WS binary → playback  │
        │ Filler won → pre-cached filler audio plays         │
        └──────────────────────────┬──────────────────────────┘
                                   ▼
                    state → "listening" (next turn)

   Every notable occurrence → EventLog (in-memory + JSONL + SQLite copy)
                                   │
        ┌──────────────────────────┼──────────────────────────────┐
        ▼                          ▼                              ▼
  SSE /stream/events        WS /stream/ws/events         REST: /escalation/*,
  (hooks installed at       (event-driven,               /daily-summary,
   startup by               bidirectional)              /alerts (+ack,
   setup_stream_hooks())                               /suggestions,
        │                          │                    /turn/context)
        └──────────────────────────┴──────────────────────────────┘
                                   ▼
              Caregiver dashboard (EscalationLadder.tsx)
              tier ladder · AlertCenter · Day Timeline · system health
              + Suggested Facts tab · Profile tab · monitoring pause/reset
```

**Key architectural decision** (`docs/architecture.md`): the safety/policy layer is a hard
boundary *before* generation (prompt constraints), with `guardrails.py` as an independent
post-generation gate — two layers, because a model can drift from its instructions.

**Model tiering** (actual status, re-verified 2026-10-23):
| Model | Intended use | Actual status |
|---|---|---|
| Nemotron Nano 30B-A3B | Fast routine turns | **Active** (default in `NemotronClient`) |
| Nemotron 3 Ultra 550B | Hard escalation judgment calls | Client method `judge_escalation()` exists, tested, **never called in production** |
| Nemotron 3.5 Lightning | Fast fallback | Available via env, not on the hot path |
| Nemotron 3 VoiceChat | Native live voice loop | Gated stub (`check_voicechat_availability()` → False), **never active** |
| Deepgram Nova-3 | ASR | **Active** (streaming WS) |
| Cartesia sonic-2 | TTS | **Active but sunsetted** (Oct 20 2026) — pinned for demo continuity only; migration target `sonic-3.6-2026-08-27` pending blind A/B |

---

## 4. What the user experience looks like

### For Asha (the person)
1. Tap the orb → it turns slate-blue ("Listening") and pulses.
2. She says, e.g., *"Where is my daughter?"* → a soft "Mm." is heard almost instantly,
   the orb turns lavender ("Thinking").
3. Within ~2.5 s she hears a warm reply (amber, "Speaking") — validating the worry,
   gently grounding, and offering one concrete choice (*"Would you like to sit by the
   window, or hear some music?"* — exact wording is LLM-dependent).
4. If she asks about Priya again and again, Sahaara answers **with the same warmth every
   time** — no trace of repetition, and never "I already told you."
5. If she says *"I miss my daughter so much"*, the reply may offer: *"Would you like to
   hear something Priya left for you?"* — the clip itself cannot be played yet (§6).
6. If she says something like *"I want to die"*, Sahaara shifts to emergency framing —
   names the feeling, stays present, offers to call Rohan — and the caregiver side goes
   RED immediately. *(The exact wording is LLM-generated within the danger prompt block;
   the system never claims a call was placed, because none is.)*
7. If she's physically unwell ("I can't breathe"), the reply stays calm and the ladder
   jumps to RED/Orange on that same turn (§2, physical distress).
8. If the system is slow, she hears a natural filler instead of silence. If it fails,
   she hears the safe fallback line. She is never told "I'm sorry, the AI is busy."
9. On her routine schedule, Sahaara may proactively say good morning, nudge a medication
   *reminder* (never dosing), or offer to tell the time — all caregiver-toggleable.

### For Rohan (the caregiver)
1. **Live view**: status cards (current tier, time in tier, repeat count 2/3, distress
   duration), the 4-rung ladder lighting up as tiers change, with the *reason*
   ("Repeated questions" / "Sustained distress" / physical symptom).
2. **AlertCenter** (bell, unread badge): specific, actionable alerts with tier color,
   timestamp, and reason; **acknowledge** turns on "Seen by Rohan"; red alerts flash.
   Alert detail shows the surrounding conversation, the trigger event, and the tier path.
3. **Day Timeline**: every conversation turn (her words + Sahaara's reply), repeat /
   distress / tier / consent / fallback / danger / memory-box / scheduler event,
   color-coded with timestamps.
4. **System health panel**: fallback triggers and standby warnings.
5. **Daily Summary tab**: one calm paragraph + metric cards + consent breakdown + the
   explicit design note that this is *noise reduction by design, not a measured clinical
   result*.
6. **Suggested Facts tab**: approve/reject facts Sahaara noticed ("she asked about Lata
   Mangeshkar music") before they ever reach the profile — nothing auto-writes.
7. **Profile tab**: edit name, family, likes, routines, and per-topic settings
   (`avoid` / `redirect` / `allow_with_care`) — effective immediately.
8. **Controls**: pause/resume monitoring, toggle the scheduler, one-click Reset Demo.

### What the caregiver cannot do yet
- No authentication/authorization (any browser on localhost is the caregiver).
- No real alert delivery outside the web app — **no SMS, no phone call, no webhook.**
  Acknowledgement is in-app only.
- No Memory Box playback and no way to record clips in-app (file must be placed manually).
- No session export/replay UI (the event log exists; the view does not).

---

## 5. Key components & how they connect

| Component | File(s) | Responsibility | Connects to |
|---|---|---|---|
| **HTTP/WS wrapper** | `backend/main.py` | All routes (27 app endpoints); owns nothing, delegates; startup wires stream hooks + scheduler | `conversation.process_turn`, `voice_pipeline.stream`, dashboard router |
| **Turn pipeline** | `backend/conversation.py` | Single source of truth for one conversational turn (text and voice) | session manager, everything below |
| **Session manager** | `backend/session_manager.py` | Per-profile `ProfileSession` state (escalation, consent, session notes, event log, alert store, provider); constructs escalation config with hardcoded demo 300× | `conversation`, `main`, `stream` |
| **Database** | `backend/database.py` | SQLite (`sahaara.db`): profiles, events, session_summaries, suggestions; auto-migrates the flat JSON profile | profile loader, event log, summarizer, suggestions |
| **Intent/emotion** | `llm/intent_emotion.py` | Regex classification → `IntentResult` (emotion, emotional-need, factual, danger, physical distress) | `response_policy`, `session_notes`, RED/ORANGE triggers |
| **Response policy** | `llm/response_policy.py` | Prompt template (Listen/Validate/Ground/Offer/Escalate + conditional blocks) + `check_response_structure` QA heuristic + tri-state topic instructions | LLM call, tests |
| **LLM provider** | `llm/provider.py`, `llm/nemotron_client.py` | Provider interface + Nebius client (OpenAI-compatible), thinking-mode handling, reasoning fallback, `judge_escalation()` (unused) | `conversation` |
| **Safe actions** | `backend/actions.py` | `tell_time` / `play_song` / `set_reminder`; results injected as prompt facts | scheduler, profile, prompt |
| **Scheduler** | `backend/scheduler.py` | Proactive initiative timing from profile routines (greetings, check-ins, medication reminders — no dosing) | actions, SSE, dashboard |
| **Voice pipeline** | `voice/voice_pipeline.py` | Orchestrator: backend selection, standby ladder (2.5 s / 5 s), state machine | `asr`, `tts`, `fillers`, `conversation` |
| **ASR** | `voice/asr.py` | Deepgram streaming WS, UtteranceEnd detection | pipeline |
| **TTS** | `voice/tts.py` | Cartesia streaming WS (schema 2026-08-14, sonic-2 pinned), PCM chunks | pipeline, fillers |
| **Fillers** | `voice/fillers.py` | Pre-rendered + disk-cached ack / mild-delay / hard-failure audio (`.raw`) | pipeline |
| **Profile** | `memory/profile.py`, `memory/data/asha_profile.json` | Load/validate/serve the "This Is Me" profile (tri-state topic settings) | policy, memory box, guardrails, alerts, scheduler |
| **Memory Box** | `memory/memory_box.py` | Longing-trigger → clip match → offer line; 5-turn AND 10-min re-offer suppression, replay bypass | prompt, event log |
| **Session notes** | `memory/session_notes.py` | Repetition (0.6 / 15 min) + sustained-distress (60% of ≥3) detection (ephemeral) | escalation engine, prompt |
| **Summarizer** | `memory/summarizer.py` | Previous-session recap → `{session_context_block}` | prompt, `/daily-summary` |
| **Suggestions** | `memory/suggestions.py` | Pending "Suggested Facts" queue; approve/reject → profile | prompt, `/suggestions`, dashboard |
| **Guardrails** | `safety/guardrails.py` | Hard post-generation gate (8 pattern families + profile-aware deceased check) + `SAFE_FALLBACK_RESPONSE` | conversation, alerts, summary |
| **Escalation** | `safety/escalation.py` | Deterministic Green/Yellow/Orange/Red state machine + hysteresis | event log, alerts, dashboard |
| **Consent** | `safety/consent.py` | Ask-before-escalate state machine (pending/accepted/declined/timed-out) | conversation, event log, dashboard |
| **Event log** | `events/event_log.py` | Append-only session timeline (JSONL + in-memory) | dashboard (all), summary, stream |
| **Real-time stream** | `dashboard/stream.py` | SSE + WS endpoints; broadcast hooks installed at startup (`setup_stream_hooks`) | event log, consent, alerts |
| **Alerts** | `dashboard/alerts.py` | Context-rich, guardrail-checked alert text + `AlertStore` (ack/unread) | tier changes, consent outcomes, AlertCenter |
| **Daily summary** | `dashboard/summary.py` | Event-log aggregation → `DailySummary` | `/daily-summary`, frontend |
| **Frontend app** | `frontend/src/App.tsx` | 4 tabs (Live / Summary / Suggestions / Profile) + text chat fallback | `/chat`, `/profile/*`, `/suggestions/*` |
| **Voice orb** | `frontend/src/VoiceOrb.tsx` | Mic capture, WS streaming, gapless playback, state UI | `/ws/voice` |
| **Caregiver dashboard** | `frontend/src/EscalationLadder.tsx` + `AlertCenter.tsx` | Tier ladder, Day Timeline, system health, alert bell/ack | `/escalation/*`, `/alerts/*`, SSE |
| **Profile editor** | `frontend/src/ProfileForm.tsx` | Caregiver "About" form incl. per-topic settings | `PUT /profile/{id}` |
| **Suggested facts UI** | `frontend/src/SuggestedFacts.tsx` | Approve/reject queue | `/suggestions/*` |
| **Tests** | `backend/tests/*` | 143-test unit suite, 10-scenario harness, guardrail adversarial, offer-rate metric, A/B renderer | — |

**Data flow invariants**: one pipeline for text and voice (no drift); profile is
caregiver-populated, never AI-guessed (suggestions require approval); every response passes
guardrails before audio; every notable event lands in the event log; the dashboard reads
only from the event log.

---

## 6. Capabilities vs. limitations (the honest split)

### ✅ Fully implemented & working
- Full conversational turn pipeline (intent → actions → memory → policy → LLM → guardrails).
- Live voice loop: Deepgram ASR + Nemotron (Nebius) + Cartesia TTS over WebSocket.
- Instant ack + standby ladder (2.5 s / 5 s fillers, late-LLM discard).
- Deterministic escalation engine with hysteresis + demo time compression (300×, now
  profile-scoped through `SessionManager`).
- Consent state machine (accept/decline/timeout-as-decline, topic-shift heuristic).
- Guardrail gate: 8 pattern families + profile-aware deceased/avoid-list checks.
- Memory Box *matching and offering* + 5-turn/10-min suppression + replay bypass;
  repetition + sustained-distress detection.
- Physical-distress triggers (urgent → Red, general → Orange) with anti-false-positive
  scenarios in the harness.
- Proactive scheduler + safe actions (time, music, reminders) with "only claim what
  succeeded" semantics.
- Session summaries feeding "yesterday" context into the prompt; Suggested Facts
  approve/reject loop; per-turn context trace (`GET /turn/context`, "Why did Sahaara say
  this?").
- Multi-profile storage (SQLite) + per-profile session state.
- Alert store with acknowledge flow + context endpoint; Day Timeline with conversation
  turns; monitoring pause/resume; Reset Demo button.
- Event log with JSONL persistence; SSE/WS real-time dashboard; daily summary; profile
  editor with tri-state topic settings.
- Frontend echo bug fixed (zero-gain node); blank-screen tier bug fixed
  (`data.detail.new_tier`); UI redesigned for demo polish.
- **209 passing local tests** (143 unit_e2e + 23 offer_rate + 13 guardrails + policy +
  repetition suites), all re-run 2026-10-23.

### 🟡 Partially implemented / scaffolded
- **Memory Box playback** — matching + offer line work; there is **no endpoint or
  frontend path that actually plays `priya_hello.mp3`**. The offer can dangle. (`pydub` +
  `ffmpeg` are now in requirements, ready for when it's built.)
- **Alert dispatch** — alerts are persisted, acknowledged, and context-rich *inside the
  app*, but **no call or message is ever sent**. No Twilio/webhook integration.
  (Consent-accepted wording was fixed Oct 20 to say an alert was *logged on Rohan's
  dashboard* — it no longer claims a call is happening.)
- **Nemotron 3 VoiceChat** — availability probe always returns False; path raises
  "not implemented yet."
- **Nemotron Ultra judgment** — `judge_escalation()` exists and is unit-tested, but
  nothing calls it; escalation is 100% threshold logic.
- **LLM intent classification** — `classify_with_llm()` written, not wired.
- **LLM-enhanced daily summary** — implemented but off by default (`use_llm=False`).
- **Unresponsive → RED detection** — code path exists (`session_manager.py:
  _unresponsive_enabled`) but is **False by default and deliberately rolled back**
  (Oct 19: "silence alone should not mean emergency — do not rebuild without discussion").
- **Filler prewarm** — `prewarm_all()` exists, never called at startup (assets are already
  cached; mostly moot).
- **Alert WebSockets** — `/ws/alerts` and `/ws/escalation` are 1 s polling loops, not
  event-driven (SSE is the real event path).
- **Frontend audio** — `ScriptProcessorNode` still in use (deprecated; AudioWorklet swap
  noted in-code). Echo is fixed, the deprecation is not addressed.

### ❌ Not implemented (planned / documented only)
- **`safety/policy_engine.py` is an empty file (0 bytes)** — the "policy engine as hard
  boundary before the LLM" lives in the prompt template + `check_response_structure`, not
  in a separate engine module. `tests/test_policy_engine.py` tests the *response policy*,
  not this file.
- **`llm/gemini_client.py` is empty (0 bytes)** — `LLM_PROVIDER=gemini` would crash; only
  `nemotron` works (default).
- **`dashboard/summary_generator.py` is empty (0 bytes)** — the real implementation is
  `summary.py`.
- **`docs/open-questions.md`, `demo/script.md`, `demo/scenario_data.json`,
  `demo/slides/vision_roadmap_slide.md` — all 0 bytes.** (`README.md` and
  `.env.example` were filled Oct 20 — that claim is no longer true.)
- **No session lifecycle**: no `session_end` handling, no JSONL rotation — the log
  accumulates across server restarts (6,372 events, multiple `session_start` lines).
- **No authentication**, no multi-user authorization.
- **Vision/camera monitoring and voice cloning** — ❌ **decided against** (Oct 2026); see
  §9/§10. Fall detection and geofencing remain roadmap-only and must not be scaffolded.
- **Phase 5** — planned, not built (scope owned outside this file — see `agents.md` §0).

### ⚠️ Notable technical caveats (verified 2026-10-23)
- `demo_mode=True` + `demo_multiplier=300.0` hardcoded in **`session_manager.py:99`**
  (moved from `conversation.py`) — production behavior needs this off.
- Frontend hardcodes `http://127.0.0.1:8000` in **7 files**; CORS allows only
  `localhost:5173` / `127.0.0.1:5173`.
- YELLOW→GREEN step-down still checks `repeat_count("")` (`escalation.py:219` — an empty
  string never matches → always "cleared"), so step-down is effectively driven by
  distress-clearing only.
- `DailySummary.total_turns` counts REPEAT+DISTRESS+TIER_CHANGE events, **not turns**
  (`summary.py:53`).
- `safety/event_log.py` is a **dead 2.2 KB file** — nothing imports it (the real log is
  `events/event_log.py`). Its former test consumers were deleted Oct 20.
- The **Nebius 402** (credit exhaustion) makes harness metrics read 0.000 — an API
  failure masquerading as a regression. Live LLM evaluation is currently blocked.
- **sonic-2 is past its published sunset date** (Oct 20 2026): still serves, but one
  render took 29.3 s vs ~1 s — degraded, demo-only, must migrate before any real deploy.
- `.env` (with real API keys) is tracked in git; `.env.example` exists alongside it.
- `backend/data/sahaara.db` and `session_summaries.json` are tracked and mutate at
  runtime; `backend/tests/unit_e2e.py` is **untracked** (should be committed); commit
  `55d69b2` added hundreds of stray `.djcache` + backup files. Repo hygiene deferred —
  see `agents.md` §9.

---

## 7. Interesting capabilities (not obvious from the UI)

1. **The person never experiences failure.** Every failure mode (LLM down, slow, guardrail
   violation) has a pre-rendered, natural-sounding voice fallback. The standby ladder means
   even a *successful-but-slow* LLM call is replaced by a filler — the conversation never
   stalls, and she is never told "the AI is busy."
2. **Profile-aware lying detection.** The guardrail doesn't just block "he's on his way" in
   general — it blocks it *specifically when the person is deceased or on the avoid-list*
   ("late husband" → Vikram, via `_is_deceased()`), and the fabricated-*whereabouts* check
   applies to **all** family members, living or not.
3. **Repetition is invisible by construction.** The repeat count never changes the response
   wording; a prompt block instructs "same warmth as the first time, never reference the
   repetition." The count feeds only the escalation engine.
4. **Demo time compression.** A 15-minute real-world escalation pattern plays out in ~30
   seconds on stage (`demo_multiplier=300`) — the whole ladder is demoable live and
   reproducibly (deterministic engine, no ML in the tier logic).
5. **The event log is a full audit trail** — 6,372 JSONL events (2026-10-23), covering
   every repeat, distress, tier change, consent ask/answer, fallback, physical symptom,
   memory-box play, and full conversation turns (both sides). Raw material for replay,
   analytics, and the "noise reduction" story.
6. **Caregiver copy is also guardrailed.** Alert messages run through the same
   diagnosis/dosing checks before display — the product applies its own safety rules to
   its own dashboard text.
7. **Consent timeout is a safety default**: silence after "Would you like me to call
   Priya?" is treated as *decline*, not accept.
8. **Nemotron gotcha handling**: the client explicitly disables thinking mode and falls
   back to `reasoning_content` if the reasoning trace ate the token budget — a real,
   documented failure mode with a graceful path.
9. **Triple-redundant dashboard delivery** (SSE + WS + polling WS) — built so the
   caregiver view survives whatever the network allows. (Honest note: SSE is the real
   event path; the two WebSockets poll at 1 s.)
10. **The noise-reduction framing is explicit in the product itself**: the daily summary
    literally renders "DESIGN ILLUSTRATION: N repeated questions → 1 quiet summary line.
    This demonstrates noise reduction — not a measured clinical result." Honest claims,
    built into the UI.
11. **Actions are honesty-checked at generation time.** `tell_time` / `play_song` /
    `set_reminder` results are injected into the prompt as *facts only when they
    succeeded* — so Sahaara structurally cannot announce a song it failed to find.

---

## 8. Potential use cases (supported by the current architecture)

1. **Evening loneliness / sundowning window** — a calm companion for the hours when family
   can't be present; repeated "when is Priya coming?" handled with warmth and quietly
   escalated once it becomes a pattern.
2. **Caregiver at work** — meaningful alerts instead of a pager: "asked about Priya 3×, a
   call might help" arrives only when a threshold crosses, plus one daily digest; the
   caregiver can acknowledge ("Seen by Rohan") and open the full context.
3. **Long-distance family** — a daughter in another city records a Memory Box clip that is
   offered when her mother asks about her (offer flow works; **playback is the missing
   piece**).
4. **Grief-adjacent moments** — asking for a deceased spouse: validate the longing, gently
   redirect, never lie, never bludgeon with the fact; the profile's tri-state topic
   settings drive this per person.
5. **Medication-time check-ins** — the profile carries `medication_times`; the scheduler
   fires reminders-only (the guardrail explicitly allows reminders, forbids dosing
   decisions).
6. **Hackathon / investor demo** — deterministic escalation + 300× time compression +
   `/demo/reset` make the full Green→Red arc repeatable on stage in under a minute.
7. **Safety-incident triage** — danger statement or urgent physical symptom → Red/Orange
   → contextual alert with the exact phrase logged for the caregiver (no diagnosis
   language, by guardrail).
8. **Multi-person households** — profile + event log are keyed by `profile_id` and Phase 4
   added SQLite + per-profile sessions, so a second person is an architectural go; no auth
   and no operator-facing multi-tenant UI yet (**UNCLEAR** how many profiles the current
   UI can switch between — only one active session is exercised).

---

## 9. Expansion opportunities — status-tagged

*Legend: ✅ Built · 🔨 In progress · 🛣 Roadmap · ❌ Decided against. Re-verified
2026-10-23. Current milestone status for the in-progress items lives in `agents.md` §0.*

**Close the loops that were 80% done:**
1. **Memory Box playback** — 🛣 **Roadmap.** Still the highest-leverage gap: add a clip
   endpoint (or WS "play clip" action) and trigger it on consent. Clip, match, and offer
   line all exist; `pydub` + `ffmpeg` are now in requirements. Mic-mute-during-playback
   comment already sits in `VoiceOrb.tsx`.
2. **Real alert dispatch** — 🛣 **Roadmap.** Twilio SMS/voice or a generic webhook;
   `Alert.to_dict()` is already shaped for it. In-app acknowledge exists; nothing leaves
   the browser.
3. **Make consent-accept true** — 🛣 **Roadmap.** Ties #1 + #2 together; requires #2 first
   so no call is ever claimed without being placed.
4. **Caregiver controls** — ✅ **Built** (Oct 20, Phase 1 + 4): profile editor
   (`ProfileForm.tsx`), tri-state topic management, monitoring pause/resume, Reset Demo,
   scheduler toggle.
5. **Fix the test suite** — ✅ **Built** (Oct 20): 3 stale files deleted, `unit_e2e.py`
   (143 tests) created, requirements.txt completed (`sse-starlette`, `pydub`), `.env.example`
   + `README.md` filled. pytest intentionally absent — suites run directly.
6. **Multi-profile / multi-session** — ✅ **Built** (Oct 20, Phase 4): `database.py`
   (SQLite, auto-migrates the JSON profile) + `session_manager.py` (per-profile
   `ProfileSession`) replaced the module-level singletons.

**Evolve the brain:**
7. **Wire Nemotron Ultra** — 🛣 **Roadmap.** `judge_escalation()` ready + tested; nothing
   calls it (escalation stays deterministic by design — wiring it is a product decision,
   not a plumbing gap).
8. **LLM intent classification** — 🛣 **Roadmap.** `classify_with_llm()` written, unwired.
9. **Embedding similarity for repetition** — 🛣 **Roadmap.** `SequenceMatcher` (0.6 / 15 min)
   still in use; code comments still flag the embeddings upgrade path.
10. **Session lifecycle** — 🛣 **Roadmap.** No `session_end`, no JSONL rotation — confirmed
    absent 2026-10-23. The JSONL grows across restarts.

**Evolve the body:**
11. **Nemotron 3 VoiceChat** — 🛣 **Roadmap.** Probe is a TODO; pipeline already selects
    between backends.
12. **AudioWorklet** — 🛣 **Roadmap.** `ScriptProcessorNode` still used; in-code comment
    notes the swap. Echo path already fixed with the zero-gain node.
13. **Unresponsive detection** — 🛣 **Roadmap (frozen).** Flag exists
    (`_unresponsive_enabled=False`) and was deliberately rolled back Oct 19: *"silence
    alone should not mean emergency — do not rebuild without discussion."*
14. **Proactive mode** — ✅ **Built** (Oct 20, Phase 3): morning greeting, evening
    check-in, medication reminders (no dosing), time/music/reminder actions.

**Decided against (do not build without explicit owner approval):**
- ❌ **Voice cloning** — the companion stays a single approved Cartesia voice; distress
  changes delivery (calmer/slower), never identity. (Memory Box clips, if ever re-made,
  are *recorded humans*, not clones.)
- ❌ **Continuous camera monitoring** — no ongoing video sensing of the person. (Fall
  detection and geofencing additionally remain roadmap-only and must not be scaffolded.)

---

## 10. Brainstorming opportunities — status-tagged

1. **"The voicemail that isn't a voicemail"** — 🛣 **Roadmap.** Needs alert dispatch (#2
   above) + playback (#1) first; still the single most demo-able emotional payoff.
2. **Consent as a first-class dashboard object** — 🛣 **Roadmap.** Partially covered by
   AlertCenter + Day Timeline consent events; no dedicated Consent History panel yet.
3. **Replay the day** — 🛣 **Roadmap.** The JSONL (6,372 events) is still the raw material;
   no scrubber UI exists.
4. **A "calmness score"** — 🛣 **Roadmap.** Signals are computed; no normalized line in the
   summary yet (framing rules in `docs/guardrails.md` remain the template).
5. **Memory Box onboarding flow** (browser clip recorder → upload) — 🛣 **Roadmap.** Clip
   dir + profile schema + matching all exist; recording UI does not.
6. **Guardrail test-suite as a product page** — 🛣 **Roadmap.** The 13 adversarial tests
   exist and run; no "Safety" tab renders them.
7. **Sensitive-topic tri-state** — ✅ **Built** (Oct 20, Phase 4 F4.2): per-topic
   `avoid` / `redirect` / `allow_with_care` with redirect target, editable in ProfileForm,
   rendered into the prompt by `response_policy.py`.
8. **Standby-ladder telemetry** — 🔨 **In progress.** Fallback events are logged and the
   System Health panel shows current state; the per-session *trend chart* is not built.
9. **Two-way consent for RED (caregiver acknowledge)** — ✅ **Built** (Oct 20, Phase 1
   F1.1): ack button + `ALERT_ACKNOWLEDGED` event + "Seen by Rohan" — the alert now has a
   true closure state (though the claim still never says a *call* was placed, because none
   is).
10. **Language/persona depth from the profile** — 🛣 **Roadmap.** Profile carries
    preferred address, likes (Lata Mangeshkar, chai), routines; no per-profile TTS/persona
    kit yet.
11. **Demo-mode as a user-facing "fast-forward"** — 🛣 **Roadmap.** 300× is still a stage
    trick, not labeled in the UI for judges.
12. **Event-log-driven prompt context ("yesterday Asha asked about Priya 4 times")** —
    ✅ **Built** (Oct 20, Phase 2 F2.1): `memory/summarizer.py` writes session summaries
    into `{session_context_block}` in the system prompt.

---

## Appendix: verified environment facts (2026-10-23 @ `6cb0cca`)

- Python venv: **3.14.2** (`backend/.venv`); FastAPI 0.141.1, uvicorn 0.53.0, websockets
  16.1.1, openai SDK 3.19.0, sse-starlette 3.5.0 (**now in requirements.txt**), pydub
  (**now in requirements.txt**; needs system `ffmpeg`), **pytest absent** (by design).
- `.env` vars: `LLM_PROVIDER`, `NEBIUS_API_KEY`, `DEEPGRAM_API_KEY`, `CARTESIA_API_KEY`,
  `CARTESIA_VOICE_ID`. `.env.example` exists (580 bytes, placeholder names).
- FastAPI app imports cleanly. **31 route objects on import** = 4 FastAPI docs routes + 26
  from `main.py` + one nested router wrapper holding the 2 `/stream` routes → **28 app
  endpoints**. The `/stream` router is included at import (`main.py:35`); broadcast hooks
  are installed at startup (`setup_stream_hooks`, `main.py:44`).
- Event log: `backend/events/data/asha_demo_001.jsonl` — **6,372 events**, append-only
  across restarts (no session_end/rotation — confirmed absent).
- Test runs on 2026-10-23: `unit_e2e.py` **143/143**, `test_offer_rate.py` **23/23**,
  `test_guardrails.py` **13/13**; frontend `tsc --noEmit` **clean**. Live harness runs
  **blocked** (Nebius 402 credit exhaustion).
- Git: **32 commits**, HEAD `6cb0cca`. Working tree: modified `backend/data/sahaara.db` +
  `backend/memory/data/session_summaries.json` (tracked, runtime-mutated); untracked
  `.kilo/plans/`, `snehapracticals.md`, and **`backend/tests/unit_e2e.py` (should be
  committed)**.
- Frontend: React 19, Vite 8, TypeScript 6.0, oxlint; no router, no state library, no
  test setup; **7 files** hardcode `http://127.0.0.1:8000`; CORS allows only
  `localhost:5173` / `127.0.0.1:5173`.
- Confirmed **0 bytes** on 2026-10-23: `safety/policy_engine.py`, `llm/gemini_client.py`,
  `dashboard/summary_generator.py`, `docs/open-questions.md`, `demo/script.md`,
  `demo/scenario_data.json`, `demo/slides/vision_roadmap_slide.md`.
  (`README.md` = 551 bytes and `.env.example` = 580 bytes — both filled Oct 20; the old
  "all empty" claim is no longer true for those two.)
