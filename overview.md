# Sahaara — Project Overview & Analysis

> Generated from a full read of the codebase on 2026-10-02. Every claim below was verified
> against actual source files, the venv, the test suite, and the persisted event log.
> Anything that could not be confirmed is explicitly marked **UNCLEAR**.

---

## 1. What the project is

**Sahaara** is a consent-based AI voice companion for people living with dementia, paired
with a quiet, meaningful escalation channel for family caregivers.

- **The person** (demo persona: *Asha Kulkarni*, `asha_demo_001`) talks to Sahaara by voice
  (or text) — it listens, validates emotion, gently grounds, and offers one small choice.
- **The caregiver** (demo persona: *Rohan*, Asha's son) gets a dashboard with a
  Green/Yellow/Orange/Red escalation ladder, context-rich alerts, a live event feed, and a
  daily summary — **noise-reduced by design** ("3 repeated questions → 1 quiet summary line").

Core product principles (from `AGENTS.md` + `docs/guardrails.md` + `docs/architecture.md`):

- Does **not** diagnose, treat, or replace human care.
- **Never** asserts unverified events to comfort ("he's on his way" is a guardrail violation).
- **Never** impersonates a family member or claims to be human.
- **Asks consent before escalating** — it never silently calls a caregiver (except unresponsive).
- Repetition is handled **silently** — no "I already told you," ever.
- Escalation framing is "notify a human when a pattern crosses a threshold" — never
  "detects falls" / "detects wandering" (clinical-claim avoidance is a product feature).

Built as a hackathon-grade system: JSON file persistence, single-persona scope, no DB.

---

## 2. What it currently does (implemented features)

### Conversation (text + voice, one shared pipeline)
- **Text chat** via `POST /chat` — full turn pipeline (see §3).
- **Live voice** via `WS /ws/voice` — real mic → real ASR → real LLM → real TTS loop:
  - Browser captures mic (16 kHz PCM16), streams to server.
  - **Deepgram Nova-3** streaming ASR with speech-pattern endpointing
    (`utterance_end_ms=1000`) — a mid-thought pause does **not** end the turn.
  - **Instant "Mm." ack** played *before* the LLM call starts (pre-rendered filler, cached
    on disk in `backend/voice/assets/*.raw`).
  - **Standby ladder**: the LLM call is raced against two timers — at 2.5 s a mild-delay
    filler ("Let me think about that for a second.") plays and the turn closes; at 5 s a
    hard-failure filler (the safe fallback line) plays. Late LLM responses are discarded.
    The person never hears dead air.
  - **Cartesia Sonic-2** streaming TTS (sentence-level chunks, 22.05 kHz PCM) with
    gapless playback scheduling in the browser.
- **Nemotron 3 VoiceChat** path: scaffolded and gated, but `check_voicechat_availability()`
  always returns `False` (early-access probe is a TODO) — the cascaded path is what runs.

### Intent & emotion understanding
- Rule-based (regex/keyword) classification: emotion (anxious/distressed/sad/content/
  calm/neutral), emotional-need vs factual-ask, and **danger statements**
  ("I want to die", "better off without me", …) → instant RED tier.
- An LLM-based classifier stub (`classify_with_llm`) exists with the same output shape,
  not wired to a live model.

### Memory system
- **"This Is Me" profile** (`backend/memory/data/asha_profile.json`): name/preferred
  address, family (with relationships incl. "late husband"), life story, likes,
  caregiver-set **avoid topics**, daily routine (meals, medication times).
- **Memory Box**: matches longing phrases ("I miss my daughter") + a family member's
  name/relationship to a recorded clip (`priya_hello.mp3` exists on disk) and injects an
  *offer* line into the prompt ("Would you like to hear something Priya left for you?").
  **The clip itself is never actually played** — there is no playback endpoint (see §6).
- **Session notes**: in-memory turn history with fuzzy repetition detection
  (`SequenceMatcher` ratio ≥ 0.6, 15-min window) and sustained-distress detection
  (≥ 60% of ≥ 3 recent turns anxious/distressed).

### Safety
- **Pre-generation policy** (`llm/response_policy.py`): one system-prompt template enforcing
  Listen → Validate → Ground → Offer one choice → Escalate-by-asking, plus conditional
  blocks for avoid-topics, Memory Box offer, repetition, sustained distress, and RED tier.
- **Post-generation hard gate** (`safety/guardrails.py`): regex checks for diagnosis
  language, dosing advice, impersonation, and **fabricated reassurance** — the last one
  cross-references the profile: "Vikram is on his way" fails (Vikram is marked `late`
  husband), while "Rohan will be here this weekend" passes. Any violation → the response
  is replaced with a safe canned line and logged.
- **Consent state machine** (`safety/consent.py`): when Sahaara's response contains
  "Would you like me to call X?" (yellow/orange tier), the next user utterance is
  interpreted as accept / decline / timeout (timeout = treated as decline, for safety).
  Topic-shift heuristics treat an unrelated new question as a decline.
- **Escalation engine** (`safety/escalation.py`): deterministic threshold logic, no ML —
  fully reproducible. Hysteresis (min tier hold), deliberate step-downs, RED is terminal
  for the session.

### Caregiver dashboard (backend + frontend)
- **Event log** (`events/event_log.py`): append-only, persisted to JSONL
  (`backend/events/data/asha_demo_001.jsonl` — currently 963 events from demo runs).
  Every repetition, distress signal, tier change, consent ask/answer, fallback, and danger
  statement is recorded. This is the single source of truth for the dashboard.
- **Real-time streaming**: SSE (`/stream/events`) + WebSocket (`/stream/ws/events`) +
  two polling WebSockets (`/ws/alerts`, `/ws/escalation`). `dashboard/stream.py`
  monkey-patches `event_log.append`, `consent_manager.ask_consent/interpret_response`, and
  `alert_builder.build_tier_change_alert` to broadcast to subscribers.
- **Alerts** (`dashboard/alerts.py`): context-rich, actionable alert text
  ("Asha has asked about Priya 3 times… A call might help reassure them"), and the
  alert text itself passes guardrail checks so caregiver-facing copy never uses
  diagnostic language.
- **Daily summary** (`dashboard/summary.py`): aggregates the event log into a
  `DailySummary` (turns, repeats, distress episodes, tier changes, consent outcomes,
  fallbacks, highest tier) with the noise-reduction framing; optional LLM wording
  enhancement (off by default).
- **Demo tooling**: `POST /demo/reset` wipes all in-memory state for a clean re-run.
  **Demo time compression**: 300× multiplier — a real 15-minute escalation pattern
  completes in ~30 seconds on stage.

### Frontend
- **Asha's view** — `VoiceOrb`: a tap-to-talk orb with low-arousal state colors
  (idle/listening/thinking/speaking/error), pulse animations, and full mic + playback
  audio engineering (downsampling, PCM conversion, gapless chunk scheduling).
- **Rohan's view** — `EscalationLadder`: live status cards (tier, time-in-tier,
  repeat count vs threshold, distress duration), the 4-tier ladder with reason + hold
  duration, an alerts feed, a color-coded session event feed, and a system-health panel.
- **Daily Summary tab** — metric cards, consent breakdown, and the design-philosophy note.
- **Text chat fallback** — a simple input box wired to `POST /chat`.

### Tests
- Passing: `test_guardrails` (13), `test_policy_engine` (9), `test_repetition_handling` (2)
  — adversarial guardrail cases, prompt-template structure, and repetition behavior.
- `run_demo_scenarios.py` — manual end-to-end scripted run-through (4 scenes) with
  structure + guardrail verdicts per turn.
- Stale/broken: `test_escalation`, `test_consent`, `test_alerts` (written against an older
  API — `safety.event_log`, `TierDecision`, old signatures; all fail on import/call).
  `test_nemotron_client.PY` (uppercase extension — invisible to pytest; content matches
  the current API).

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
        ┌─────────────────────────┴──────────────────────────┐
        │ Deepgram Nova-3 streaming ASR (asr.py)             │
        │ interim results → UI only                          │
        │ UtteranceEnd → finished turn                       │
        └─────────────────────────┬──────────────────────────┘
                                   │
                    state → "thinking" + instant "Mm." ack
                                   │
        ┌─────────────────────────┴──────────────────────────┐
        │ Standby ladder (asyncio race):                     │
        │   LLM reply  vs  2.5 s mild filler  vs  5 s hard  │
        └─────────────────────────┬──────────────────────────┘
                                   │
                    process_turn()  (conversation.py)
                    — SAME pipeline as POST /chat —
                                   │
   1. Consent check: pending consent? interpret this utterance
   2. classify_intent(): emotion / emotional-need / factual / DANGER
      └─ danger statement → force RED tier
   3. Memory: MemoryBox.match() · SessionNotes.is_repeated_question()
      · sustained_distress()
   4. EscalationEngine.evaluate() — deterministic thresholds + hysteresis
      └─ tier change → AlertBuilder builds context-rich alert
   5. build_prompt() — Listen/Validate/Ground/Offer/Escalate policy
      + profile context + avoid-topics + Memory Box offer +
      repetition/distress/RED blocks
   6. LLM: NemotronClient → Nebius Token Factory (OpenAI-compatible)
      default model: Nemotron-3-Nano-30B-A3B, thinking disabled,
      reasoning_content fallback for empty-content gotcha
   7. Post-generation: check_response_structure() (QA heuristic)
      + check_guardrails() (HARD GATE:3 diagnosis / dosing /
      impersonation / fabricated reassurance vs profile)
      └─ violation → SAFE_FALLBACK_RESPONSE, logged
   8. Consent detection in response ("Would you like me to call X?")
      → ConsentManager.ask_consent() (yellow/orange only)
   9. SessionNotes.record_turn()
                                   │
        ┌─────────────────────────┴──────────────────────────┐
        │ LLM won → state "speaking" → Cartesia Sonic-2      │
        │ streaming TTS → PCM chunks → WS binary → playback  │
        │ Filler won → pre-cached filler audio plays         │
        └─────────────────────────┬──────────────────────────┘
                                   ▼
                    state → "listening" (next turn)

   Every notable occurrence → EventLog (in-memory + JSONL)
                                   │
        ┌──────────────────────────┼──────────────────────────────┐
        ▼                          ▼                              ▼
  SSE /stream/events        WS /stream/ws/events         REST: /escalation/*
  (monkey-patched           (event-driven,               /daily-summary
   broadcast hooks)          bidirectional)              /alerts (stub)
        │                          │                              │
        └──────────────────────────┴──────────────────────────────┘
                                   ▼
              Caregiver dashboard (EscalationLadder.tsx)
              tier ladder · alerts · event feed · system health
                                   ▼
              Daily Summary tab (DailySummaryScreen.tsx)
```

**Key architectural decision** (`docs/architecture.md`): the safety/policy layer is a hard
boundary *before* generation (prompt constraints), with `guardrails.py` as an independent
post-generation gate — two layers, because a model can drift from its instructions.

**Model tiering** (planned vs actual):
| Model | Intended use | Actual status |
|---|---|---|
| Nemotron Nano 30B-A3B | Fast routine turns | **Active** (default in `NemotronClient`) |
| Nemotron 3 Ultra 550B | Hard escalation judgment calls | Client method `judge_escalation()` exists, **never called** |
| Nemotron 3 VoiceChat | Native live voice loop | Gated stub, **never active** |
| Deepgram Nova-3 | ASR | **Active** (not in AGENTS.md's stack list, but is the real ASR) |
| Cartesia Sonic-2 | TTS | **Active** |

---

## 4. What the user experience looks like

### For Asha (the person)
1. Tap the orb → it turns slate-blue ("Listening") and pulses.
2. She says, e.g., *"Where is my daughter?"* → a soft "Mm." is heard almost instantly,
   the orb turns lavender ("Thinking").
3. Within ~2.5 s she hears a warm reply (amber, "Speaking"), e.g. validating the worry,
   gently grounding ("It's a quiet afternoon here at home"), and offering one concrete
   choice ("Would you like to sit by the window, or hear some music?").
4. If she asks about Priya again and again, Sahaara answers **with the same warmth every
   time** — no trace of repetition.
5. If she says *"I miss my daughter so much"*, the reply may offer: *"Would you like to
   hear something Priya left for you?"* — (the clip itself cannot be played yet, §6).
6. If she says something like *"I want to die"*, Sahaara immediately shifts to
   emergency framing ("I have notified your family…") and the caregiver side goes RED.
7. If the system is slow, she hears a natural filler instead of silence. If it fails,
   she hears the safe fallback line. She is never told "I'm sorry, the AI is busy."

### For Rohan (the caregiver)
1. **Live view**: status cards (current tier, time in tier, repeat count 2/3, distress
   duration), the 4-rung ladder lighting up as tiers change, with the *reason*
   ("Repeated questions" / "Sustained distress").
2. **Alerts feed**: specific, actionable lines ("Asha has asked about Priya 3 times in
   the last few minutes. A call might help reassure them.") — not "check on Asha."
3. **Session events feed**: every repeat/distress/tier/consent/fallback/danger event with
   timestamps, color-coded.
4. **System health panel**: fallback triggers and system warnings (e.g., "Sahaara used a
   standby response because the AI was taking longer than expected").
5. **Daily Summary tab**: one calm paragraph + metric cards (total turns, repeated
   questions, distress episodes, tier changes, highest tier, fallbacks) + consent
   breakdown (accepted/declined/timeout) + the explicit design note that this is
   *noise reduction by design, not a measured clinical result*.
6. **Demo reset**: one endpoint wipes state for a clean stage re-run.

### What the caregiver cannot do yet
- No authentication, no profile editing UI, no "pause monitoring" toggle (guardrails.md
  promises human override — not implemented), no way to actually call anyone,
  no alert delivery to a phone (webhook/SMS is a stub — alerts exist only in the UI).

---

## 5. Key components & how they connect

| Component | File(s) | Responsibility | Connects to |
|---|---|---|---|
| **HTTP/WS wrapper** | `backend/main.py` | All routes; owns nothing, delegates | `conversation.process_turn`, `voice_pipeline.stream`, dashboard router |
| **Turn pipeline** | `backend/conversation.py` | Single source of truth for one conversational turn; owns all shared singletons (profile, memory box, session notes, event log, escalation engine, consent manager, alert builder, provider) | everything below |
| **Intent/emotion** | `llm/intent_emotion.py` | Regex classification → `IntentResult` (emotion, emotional-need, factual, danger) | `response_policy`, `session_notes`, RED trigger |
| **Response policy** | `llm/response_policy.py` | Prompt template (Listen/Validate/Ground/Offer/Escalate + conditional blocks) + `check_response_structure` QA heuristic | LLM call, tests |
| **LLM provider** | `llm/provider.py`, `llm/nemotron_client.py` | Provider interface + Nebius Token Factory client (OpenAI-compatible), thinking-mode handling, reasoning fallback | `conversation` |
| **Voice pipeline** | `voice/voice_pipeline.py` | Orchestrator: backend selection, standby ladder, state machine | `asr`, `tts`, `fillers`, `conversation` |
| **ASR** | `voice/asr.py` | Deepgram streaming WS, utterance-end detection | pipeline |
| **TTS** | `voice/tts.py` | Cartesia streaming WS, PCM chunks | pipeline, fillers |
| **Fillers** | `voice/fillers.py` | Pre-rendered + disk-cached ack / mild-delay / hard-failure audio | pipeline |
| **Profile** | `memory/profile.py`, `memory/data/asha_profile.json` | Load/validate/serve the "This Is Me" profile | policy, memory box, guardrails, alerts |
| **Memory Box** | `memory/memory_box.py` | Longing-trigger → clip match → offer line | prompt |
| **Session notes** | `memory/session_notes.py` | Repetition + sustained-distress detection (ephemeral) | escalation engine, prompt |
| **Guardrails** | `safety/guardrails.py` | Hard post-generation gate + `SAFE_FALLBACK_RESPONSE` | conversation, alerts, summary |
| **Escalation** | `safety/escalation.py` | Deterministic Green/Yellow/Orange/Red state machine | event log, alerts, dashboard |
| **Consent** | `safety/consent.py` | Ask-before-escalate state machine (pending/accepted/declined/timed-out) | conversation, event log, dashboard |
| **Event log** | `events/event_log.py` | Append-only session timeline, JSONL persistence | dashboard (all), summary |
| **Real-time stream** | `dashboard/stream.py` | SSE + WS endpoints; monkey-patch broadcast hooks | event log, consent, alerts |
| **Alerts** | `dashboard/alerts.py` | Context-rich, guardrail-checked alert messages | tier changes, consent outcomes |
| **Daily summary** | `dashboard/summary.py` | Event-log aggregation → `DailySummary` | `/daily-summary`, frontend |
| **Frontend app** | `frontend/src/App.tsx` | Tabs (Live / Daily Summary), text chat fallback | `/chat` |
| **Voice orb** | `frontend/src/VoiceOrb.tsx` | Mic capture, WS streaming, gapless playback, state UI | `/ws/voice` |
| **Caregiver dashboard** | `frontend/src/EscalationLadder.tsx` | Tier ladder, alerts, event feed, system health | `/escalation/*`, SSE |
| **Daily summary UI** | `frontend/src/DailySummaryScreen.tsx` | Metrics + consent + design note | `/daily-summary` |
| **Tests** | `backend/tests/*` | Adversarial guardrails, prompt structure, repetition, (stale) escalation/consent/alerts, scripted demo runner | — |

**Data flow invariants**: one pipeline for text and voice (no drift); profile is
caregiver-populated, never AI-guessed; every response passes guardrails before audio;
every notable event lands in the event log; the dashboard reads only from the event log.

---

## 6. Capabilities vs. limitations (the honest split)

### ✅ Fully implemented & working
- Full conversational turn pipeline (intent → memory → policy → LLM → guardrails).
- Live voice loop: Deepgram ASR + Nemotron (Nebius) + Cartesia TTS over WebSocket.
- Instant ack + standby ladder (2.5 s / 5 s fillers, late-LLM discard).
- Deterministic escalation engine with hysteresis + demo time compression (300×).
- Consent state machine (accept/decline/timeout-as-decline, topic-shift heuristic).
- Guardrail gate incl. profile-aware fabricated-reassurance detection.
- Memory Box *matching and offering*; repetition + sustained-distress detection.
- Event log with JSONL persistence; SSE/WS real-time dashboard; alerts; daily summary.
- Demo reset endpoint; low-arousal orb UI; caregiver dashboard UI; text chat fallback.
- 24 passing unit tests (guardrails/policy/repetition) + scripted demo runner.

### 🟡 Partially implemented / scaffolded
- **Memory Box playback** — matching + offer line work; there is **no endpoint or
  frontend path that actually plays `priya_hello.mp3`**. The offer can dangle.
- **Phone/SMS escalation** — consent "accepted" produces "Calling Priya now…" and the
  RED alert says "Sahaara has notified emergency contacts," but **no call or message is
  ever sent**. `AlertBuilder` output is not persisted or dispatched anywhere
  (`GET /alerts` returns an empty stub).
- **Nemotron 3 VoiceChat** — availability probe is a TODO; path raises
  "not implemented yet."
- **Nemotron Ultra judgment** — `judge_escalation()` exists (thinking ON, Ultra model)
  but nothing calls it; escalation is 100% threshold logic.
- **LLM intent classification** — `classify_with_llm()` stub, not wired.
- **LLM-enhanced daily summary** — implemented but off by default (`use_llm=False`).
- **Unresponsive → RED detection** — code exists in `conversation.py` but
  `_unresponsive_enabled` is never set `True` by the voice pipeline; **dead at runtime**.
- **Filler prewarm** — `prewarm_all()` exists but is never called at startup (assets are
  already cached on disk, so it's mostly moot).
- **Tests** — 3 of 7 test files are stale (old API, fail on import); pytest is not
  installed in the venv; tests run via a `__main__` fallback runner.
- **`/alerts`, `/ws/alerts`, `/ws/escalation`** — endpoints exist; `/alerts` is an
  explicit stub; the two WebSockets are 1 s polling loops, not event-driven.

### ❌ Not implemented (planned / documented only)
- **`safety/policy_engine.py` is an empty file** — AGENTS.md says every response passes
  through it; in reality only `guardrails.py` is enforced. The "policy engine as hard
  boundary before the LLM" lives in the prompt template, not in a separate engine module.
- **`llm/gemini_client.py` is empty** — `LLM_PROVIDER=gemini` would crash; only
  `nemotron` works (default).
- **`dashboard/summary_generator.py`** — empty; the real implementation is `summary.py`.
- **README.md, docs/open-questions.md, demo/script.md, demo/scenario_data.json,
  demo/slides/vision_roadmap_slide.md — all 0 bytes.** AGENTS.md references `.env.example`
  (required vars) — **it does not exist**.
- **Caregiver controls**: no profile editing UI, no pause/disable toggle, no auth.
- **Multi-person / multi-session**: everything is a module-level singleton for one
  profile; a second user would collide with the first.
- **Vision/camera** — explicitly roadmap-only (slide, not code).
- **Session boundaries**: no `session_end` handling; the JSONL accumulates across server
  restarts (963 events, multiple `session_start` lines).

### ⚠️ Notable technical caveats (verified)
- `sse-starlette` is imported by `dashboard/stream.py` but **missing from
  `requirements.txt`** (present in the venv — a fresh install would crash at import).
- `VoiceOrb.tsx` connects the mic `ScriptProcessorNode` to `audioContext.destination`
  (`VoiceOrb.tsx:174`) — mic audio is routed to the speakers, an echo/feedback risk.
- Frontend hardcodes `http://127.0.0.1:8000` in four files; CORS is localhost-only.
- `demo_mode=True` is hardcoded in `conversation.py` — production behavior would need
  the 300× compression turned off.
- YELLOW→GREEN step-down checks `repeat_count("")` (empty string never matches → always
  "cleared"), so step-down is effectively driven by distress-clearing only.
- `DailySummary.total_turns` actually counts REPEAT+DISTRESS+TIER_CHANGE events, not turns.
- TTS request schema carries "verify against Cartesia docs" notes — written from
  general knowledge, not a live check.
- AGENTS.md folder structure is stale: `frontend/src/voice-ui/` and `frontend/src/dashboard/`
  don't exist (files live directly in `src/`); the `backend/events/` module isn't mentioned.
- Python venv is 3.14 (AGENTS.md says 3.11).

---

## 7. Interesting capabilities (not obvious from the UI)

1. **The person never experiences failure.** Every failure mode (LLM down, slow,
   guardrail violation) has a pre-rendered, natural-sounding voice fallback. The standby
   ladder means even a *successful-but-slow* LLM call is replaced by a filler — the
   conversation never stalls.
2. **Profile-aware lying detection.** The guardrail doesn't just block "he's on his way"
   in general — it blocks it *specifically when the person is deceased or on the
   avoid-list in the profile* ("late husband" → Vikram). Generic reassurance about lunch
   passes.
3. **Repetition is invisible by construction.** The repeat count never changes the
   response wording; instead a prompt block instructs "same warmth as the first time,
   never reference the repetition." The count feeds only the escalation engine.
4. **Demo time compression.** A 15-minute real-world escalation pattern plays out in
   ~30 seconds on stage (`demo_multiplier=300`) — the whole ladder can be demoed live
   and reproducibly (deterministic engine, no ML in the tier logic).
5. **The event log is a full audit trail** — 963 JSONL events from demo runs, covering
   every repeat, distress, tier change, consent ask/answer, and fallback. It's the raw
   material for replay, analytics, and the "noise reduction" story.
6. **Caregiver copy is also guardrailed.** Alert messages run through the same
   diagnosis/dosing checks before display — the product applies its own safety rules to
   its own dashboard text.
7. **Consent timeout is a safety default**: silence after "Would you like me to call
   Priya?" is treated as *decline*, not accept.
8. **Nemotron gotcha handling**: the client explicitly disables thinking mode for
   conversational turns and falls back to `reasoning_content` if the reasoning trace
   ate the token budget — a real, documented failure mode with a graceful path.
9. **Triple-redundant dashboard delivery** (SSE + WS + polling WS) — built so the
   caregiver view survives whatever the network allows.
10. **The noise-reduction framing is explicit in the product itself**: the daily summary
    literally renders "DESIGN ILLUSTRATION: N repeated questions → 1 quiet summary line.
    This demonstrates noise reduction — not a measured clinical result." Honest claims,
    built into the UI.

---

## 8. Potential use cases (supported by the current architecture)

1. **Evening loneliness / sundowning window** — a calm companion for the hours when
   family can't be present; repeated "when is Priya coming?" handled with warmth and
   quietly escalated once it becomes a pattern.
2. **Caregiver at work** — meaningful alerts instead of a pager: "asked about Priya 3×,
   a call might help" arrives only when a threshold crosses, plus one daily digest.
3. **Long-distance family** — a daughter in another city records a Memory Box clip that
   plays when her mother asks about her (offer flow works; playback is the missing piece).
4. **Grief-adjacent moments** — asking for a deceased spouse: validate the longing,
   gently redirect, never lie, never bludgeon with the fact; the profile's avoid-topics
   drive this per person.
5. **Medication-time check-ins** — the profile already carries `medication_times`;
   reminders-only (the guardrail explicitly allows reminders, forbids dosing decisions).
6. **Hackathon / investor demo** — deterministic escalation + 300× time compression +
   `/demo/reset` make the full Green→Red arc repeatable on stage in under a minute.
7. **Safety-incident triage** — danger statement → RED → urgent contextual alert, with
   the exact phrase logged for the caregiver.
8. **Multi-generational home** — the single-persona scope is a demo constraint, not an
   architectural one: profile + event log are already keyed by `profile_id`.

---

## 9. Expansion opportunities (logical next steps)

**Close the loops that are 80% done (highest leverage):**
1. **Memory Box playback** — add `GET /clips/{filename}` (or a WS "play clip" action) and
   trigger it when the person accepts the offer. The clip, the match, and the offer line
   all already exist.
2. **Real alert dispatch** — Twilio SMS/voice stub or a generic webhook; `Alert.to_dict()`
   is already shaped for it. Persist alerts (the `/alerts` stub says so itself).
3. **Make consent-accept true** — wire "Calling Priya now" to an actual Twilio call with
   the recorded clip as the voicemail (ties #1 and #2 into one emotional moment).
4. **Caregiver controls** — profile editor (the JSON already says `"editable": true`),
   avoid-topics management, contact list, and the "pause monitoring" toggle that
   `docs/guardrails.md` promises as a hard requirement.
5. **Fix the test suite** — rewrite the 3 stale test files against the current API, add
   `pytest` + `sse-starlette` to requirements, add `.env.example`, fill the empty
   README/docs/demo files.
6. **Multi-profile / multi-session** — parameterize the singletons in `conversation.py`
   by `profile_id` (event log and profile are already per-profile; session notes,
   escalation, and consent are the remaining globals).

**Evolve the brain:**
7. **Wire Nemotron Ultra** — use `judge_escalation()` for the genuinely ambiguous
   yellow/orange calls (the method, model ID, and thinking-mode config are all ready).
8. **LLM intent classification** — swap the regex classifier for `classify_with_llm`
   (same `IntentResult` shape, drop-in).
9. **Embedding similarity** — replace `SequenceMatcher` repetition detection with
   embeddings (the code comments flag this exact upgrade path).
10. **Session lifecycle** — `session_end`, JSONL rotation, and a "day in the life" replay
    view built from the existing event log.

**Evolve the body:**
11. **Nemotron 3 VoiceChat** — implement the marked TODO probe when early access lands;
    the pipeline already selects between backends.
12. **AudioWorklet** — replace the deprecated `ScriptProcessorNode` and remove the
    mic→destination echo path in `VoiceOrb.tsx`.
13. **Unresponsive detection** — enable `_unresponsive_enabled` in the voice pipeline
    (the code path is written; it just never turns on).
14. **Proactive mode** — Sahaara initiates: morning greeting, medication-time check-in,
    "would you like to hear something Priya left for you?" at her usual evening asking
    window (profile has the routine data).

---

## 10. Brainstorming opportunities (building specifically on what exists)

1. **"The voicemail that isn't a voicemail"** — consent accepted → Twilio call plays
   Priya's *actual recorded clip* (`priya_hello.mp3`) as the "message." The entire
   pipeline (clip storage, offer, consent, alert) already exists; only the call leg is
   missing. This is the single most demo-able emotional payoff in the system.
2. **Consent as a first-class dashboard object** — the consent state machine already
   logs ask/answer with outcomes; build a "Consent History" panel so the caregiver sees
   *when* Sahaara asked, *who* for, and *how* it was resolved — a transparency feature
   that directly supports the "person is the user, not the subject" principle.
3. **Replay the day** — the JSONL event log (963 events) is a complete session recording
   of *patterns* (not audio). Build a scrubber: drag through the day, watch the tier
   ladder move, see which repeats triggered which alerts. Great for a caregiver
   reviewing "what happened while I was at work" — and a killer demo feature since the
   data is already there.
4. **A "calmness score" derived from existing signals** — repeat rate, distress
   duration, and tier time-in are already computed; a single normalized "today was
   calmer/busier than yesterday" line in the daily summary (carefully framed, no
   clinical claims — the framing rules in `docs/guardrails.md` are the template).
5. **Memory Box onboarding flow** — caregiver records clips in the browser
   (`MediaRecorder` → upload → `memory_box/` + profile update). The clip directory,
   profile schema (`memory_box_clip` field), and matching logic all exist; only the
   recording UI is missing.
6. **Guardrail test-suite as a product page** — the 13 adversarial guardrail tests are
   literally the product's safety story. A "Safety" tab in the dashboard (or a printed
   one-pager) that shows live PASS/FAIL against the current response corpus turns a
   technical artifact into a trust feature for families.
7. **The open question, made visible** — `docs/guardrails.md` names the unresolved
   tension (caregiver says "never mention X" but the person needs to process it).
   Surface this in the UI as a caregiver-configurable "sensitive topic mode"
   (avoid / redirect / allow-with-care) — the profile schema already carries avoid
   topics; the tri-state is a small extension with a big product-ethics payoff.
8. **Standby-ladder telemetry** — fallback events are already logged
   (`STANDBY_MILD_DELAY` / `REAL_DELAY` / `HARD_FAILURE`); a "System Health" trend
   (fallbacks per session) tells the team when the LLM backend is degrading —
   the dashboard panel exists, the data exists, the chart is missing.
9. **Two-way consent for RED** — today RED alerts claim "Sahaara has notified emergency
   contacts" without a call. Add an explicit caregiver "acknowledge" button on RED
   alerts (WS round-trip already exists) so the claim becomes true: "Rohan acknowledged
   at 14:32."
10. **Language/persona depth from the profile** — the profile already encodes "responds
    warmly to her name said softly," preferred address, likes (Lata Mangeshkar, masala
    chai). A "persona kit" per profile (name-softening in TTS, music offers from
    `likes.music`, chai offers near `daily_routine` times) makes the same engine feel
    individually tuned — all inputs already exist in `asha_profile.json`.
11. **Demo-mode as a user-facing "fast-forward"** — the 300× compression is currently a
    stage trick; expose it honestly in the demo UI ("Demo time compression: ON") so
    judges understand the on-stage 30-second escalation is a 15-minute pattern.
12. **Event-log-driven prompt context** — instead of only in-session state, feed the
    *previous session's* summary (from the persisted JSONL) into the morning prompt:
    "Yesterday Asha asked about Priya 4 times in the evening" — cross-session warmth
    with zero new infrastructure.

---

## Appendix: verified environment facts
9
- Python venv: **3.14** (`backend/.venv`), FastAPI 0.141.1, uvicorn 0.53.0, websockets
  16.1.1, openai SDK 3.19.0, sse-starlette 3.5.0 (not in requirements.txt), **pytest absent**.
- `.env` vars present: `LLM_PROVIDER`, `NEBIUS_API_KEY`, `DEEPGRAM_API_KEY`,
  `CARTESIA_API_KEY`, `CARTESIA_VOICE_ID`. No `.env.example`.
- FastAPI app imports cleanly; 17 routes mounted (incl. lazy-included `/stream` router).
- Event log: `backend/events/data/asha_demo_001.jsonl` — 963 events, append-only across
  restarts.
- Git: 14 commits, last: "summary ui implemented"; working tree has uncommitted changes
  to `main.py` and the event log.
- Frontend: React 19.2, Vite 8.3, TypeScript 6.0, oxlint; no router, no state library,
  no test setup.
