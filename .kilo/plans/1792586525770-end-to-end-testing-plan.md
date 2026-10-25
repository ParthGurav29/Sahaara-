# Sahaara — End-to-End Testing Plan

**Goal:** Cross-verify every function of the project end-to-end, produce a findings report, then fix issues.

**Context:** Full backend + frontend system described in AGENTS.md. Backend uses Python 3.14 venv at `backend/.venv/`. Frontend is React 19 + Vite on port 5173. Both must run for full E2E. Backend listens on `localhost:8000`.

**Constraint:** This is a plan only. No source files are modified by this plan. An implementation-capable agent must execute it.

---

## 1. Test Scope — Functions to Verify

### Core Pipeline (`backend/conversation.py`)
| Function | What it does | Test approach |
|---|---|---|
| `process_turn(user_message)` | Full turn: consent → intent → memory → escalation → prompt → LLM → guardrails → consent → record | Run each scenario from `tests/scenarios.py`, verify meta fields |
| `reset_pipeline()` | Reset session state via SessionManager | Call between scenarios, verify state is clean |
| `_detect_consent_request(text, tier, s)` | Regex detect "call X" in LLM response (yellow/orange only) | Feed sample responses, verify contact extraction |

### Intent & Emotion (`backend/llm/intent_emotion.py`)
| Function | Test approach |
|---|---|
| `classify_intent(text)` | Unit test with keyword samples: anxious, distressed, sad, content, calm, factual, danger, physical urgent, physical general |
| `classify_with_llm(text, llm_call)` | Verify stub returns correct shape with injected callable |

### Response Policy (`backend/llm/response_policy.py`)
| Function | Test approach |
|---|---|
| `build_prompt(...)` | Verify template contains validate-before-ground, one-choice rule, avoid-topic block, memory box block, repetition block, distress block, physical blocks, reuse block |
| `check_response_structure(text, turn_type)` | Test good/bad responses for emotional, casual, danger turn types |

### Guardrails (`backend/safety/guardrails.py`)
| Function | Test approach |
|---|---|
| `check_guardrails(text, profile)` | Adversarial: diagnosis, dosing, impersonation, fabricated reassurance, whereabouts, schedule, weather, surroundings. Safe cases: warm validation, living family reassurance, lunch, medication reminder |

### Escalation Engine (`backend/safety/escalation.py`)
| Function | Test approach |
|---|---|
| `EscalationEngine.evaluate(message, intent)` | Green→Yellow (repeat threshold), Yellow→Orange (distress persistence), step-down, force_tier RED (danger), physical urgent→Red, physical general→Orange, hysteresis/min hold |
| `force_tier(tier, reason)` | Direct tier override |
| `get_status()` | Verify tier, duration, repeat count fields |

### Consent State Machine (`backend/safety/consent.py`)
| Function | Test approach |
|---|---|
| `ask_consent(...)` | Transition IDLE→PENDING, log consent_asked event |
| `interpret_response(text)` | Accept patterns (yes/ok/sure), decline patterns (no/nope/don't), topic shift, timeout |
| `check_timeout()` | TIMED_OUT after effective_timeout (compressed in demo mode) |

### Memory Box (`backend/memory/memory_box.py`)
| Function | Test approach |
|---|---|
| `match(text)` | Longing + family member match, replay request bypass, suppression (5 turns AND 600s), no match for non-longing text |
| `record_turn()` | Turn counter advances |
| `reset()` | Clears offered clips and turn counter |
| `clip_exists_on_disk(match)` | Verify priya_hello.mp3 exists (placeholder, 0 bytes) |

### Session Notes (`backend/memory/session_notes.py`)
| Function | Test approach |
|---|---|
| `record_turn(text, emotion, speaker)` | Turn stored with normalization |
| `repeat_count(text, window)` | Similarity-based count within window |
| `is_repeated_question(text, min_repeats)` | Threshold-based repetition |
| `sustained_distress(window, labels)` | 60% of >=3 recent turns anxious/distressed |

### Profile (`backend/memory/profile.py`)
| Function | Test approach |
|---|---|
| `Profile.load(path)` | Load asha_profile.json, validate required keys |
| `get_family()`, `find_family_member()` | Lookup by name/relationship |
| `get_avoid_topics()`, `is_avoid_topic(text)` | Vikram death topic detection |
| `get_likes()`, `get_routine()` | Likes and daily routine accessors |

### Database (`backend/database.py`)
| Function | Test approach |
|---|---|
| `init_db()` | Create tables, migrate JSON profile |
| `get_profile(profile_id)`, `list_profiles()` | Read |
| `save_profile(profile_id, data)` | Upsert |
| `append_event/get_events/get_all_events/clear_events` | Event CRUD |
| `save_session_summary/get_recent_summaries` | Summary CRUD |
| `save_suggestion/get_pending_suggestions/remove_suggestion/clear_suggestions` | Suggestion CRUD |

### Event Log (`backend/events/event_log.py`)
| Function | Test approach |
|---|---|
| `append(event)` | In-memory + JSONL persistence |
| `get_events/get_recent/get_all/get_all` | Query by type/time |
| `log_repeat/log_distress/log_tier_change/log_consent_asked/log_consent_answered/log_fallback/log_danger_statement/log_physical_symptom/log_memory_box_played/log_conversation_turn/log_alert_acknowledged/log_system_health/log_session_start/log_session_end/log_timing` | All convenience log methods |
| `summary()` | Event counts, current tier, duration |

### Actions (`backend/actions.py`)
| Function | Test approach |
|---|---|
| `tell_time()` | Gentle time format |
| `play_song(song_name)` | Fuzzy match, no-match fallback, empty dir |
| `set_reminder(text, minutes, scheduler)` | With/without scheduler |
| `detect_action(user_message)` | tell_time, play_song, set_reminder pattern matching |

### Scheduler (`backend/scheduler.py`)
| Function | Test approach |
|---|---|
| `Scheduler(profile)` | Load routine from profile (wake, evening, medication, meals, likes routines) |
| `start(llm_call)`/`stop()` | Background loop |
| `toggle()` | Pause/resume |
| `add_one_time_reminder(text, fire_at)` | One-time reminders |
| `get_status()` | Paused, active reminders, sent today |
| `_check_reminders()` | Window-based firing, dedup per day, expiry |
| `_check_one_time()` | One-time reminder firing |

### Alerts (`backend/dashboard/alerts.py`)
| Function | Test approach |
|---|---|
| `AlertStore.add/get_all/get_by_id/acknowledge/unread_count/clear` | Alert CRUD |
| `AlertBuilder.build_tier_change_alert(...)` | Green→Yellow→Orange→Red→Green messages, guardrail-safe |
| `AlertBuilder.build_consent_alert(...)` | Accepted/declined/timeout messages |
| `AlertBuilder.build_fallback_alert(...)` | Guardrail/standby messages |
| `AlertBuilder.build_physical_symptom_alert(...)` | Urgent/general messages |

### Daily Summary (`backend/dashboard/summary.py`)
| Function | Test approach |
|---|---|
| `generate_daily_summary(event_log, profile, use_llm)` | Structured summary from events, noise reduction note |
| `_build_raw_summary(...)` | Counts: turns, repeats, distress, tier changes, consent, fallbacks |

### Summarizer (`backend/memory/summarizer.py`)
| Function | Test approach |
|---|---|
| `generate_summary_from_events(events, profile_name)` | Session summary from events |
| `build_session_context_block(summaries, profile_name)` | Previous session context for prompt |
| `save_summary/load_recent_summaries` | JSON persistence |

### Suggestions (`backend/memory/suggestions.py`)
| Function | Test approach |
|---|---|
| `detect_suggestions(text, likes, family)` | Detect music/memories/routines/food from conversation |
| `SuggestionQueue.add/get_all/get_by_id/remove/clear` | Queue CRUD |

### Voice Pipeline (`backend/voice/voice_pipeline.py`)
| Function | Test approach |
|---|---|
| `run_cascaded(audio_in)` | ASR → ack → standby ladder → LLM → TTS → listening |
| `check_voicechat_availability()` | Returns False (not wired) |
| `get_active_backend_name()` | Returns "cascaded" or raises VoiceBackendUnavailable |
| `stream(audio_in)` | Top-level entry point |

### Nemotron Client (`backend/llm/nemotron_client.py`)
| Function | Test approach |
|---|---|
| `NemotronClient.complete(system, user, ...)` | LLM call with thinking disabled, reasoning_content fallback |
| `complete_from_prompt_bundle(bundle)` | Adapter for PromptBundle |
| `judge_escalation(system, user)` | Ultra model with thinking ON (never called in production) |

### Provider (`backend/llm/provider.py`)
| Function | Test approach |
|---|---|
| `get_provider(name)` | Factory: nemotron default, gemini option, error on unknown |

### Main API (`backend/main.py`)
| Endpoint | Test approach |
|---|---|
| `GET /` | Status, provider |
| `GET /health` | Provider ready |
| `POST /chat` | Text turn via process_turn |
| `GET /escalation/status` | Current tier |
| `GET /escalation/events` | Filtered events |
| `GET /escalation/summary` | Session summary |
| `GET /daily-summary` | Daily summary |
| `POST /demo/reset` | Reset all state |
| `GET /alerts`, `POST /alerts/{id}/acknowledge`, `GET /alerts/test`, `GET /alerts/{id}/context` | Alert CRUD |
| `GET /suggestions`, `POST /suggestions/{id}/approve/reject` | Suggestion CRUD |
| `GET /turn/context` | Explainability |
| `POST /monitoring/pause/resume` | Monitoring toggle |
| `GET /profiles`, `GET /profile/current`, `PUT /profile/{id}` | Profile CRUD |
| `POST /scheduler/toggle`, `GET /scheduler/status` | Scheduler control |
| WebSocket `/ws/voice` | Voice pipeline |
| WebSocket `/ws/alerts`, `/ws/escalation` | Polling WS |
| SSE `/stream/events` | Event broadcasting |

---

## 2. Test Phases

### Phase 1: Environment Verification (prerequisites)
1. Verify `backend/.venv/` exists and Python 3.14 works
2. Verify `.env` has NEBIUS_API_KEY, DEEPGRAM_API_KEY, CARTESIA_API_KEY, CARTESIA_VOICE_ID
3. Verify `sse_starlette` and `pydub` availability (missing from requirements.txt)
4. Verify `ffmpeg` installed system-wide (needed by pydub)
5. Verify `sahaara.db` exists at `backend/data/sahaara.db`
6. Run existing unit tests: `test_guardrails.py`, `test_policy_engine.py`, `test_repetition_handling.py`

### Phase 2: Unit Tests (each module in isolation)
Run each module's `if __name__ == "__main__"` block, plus write targeted tests for:
- `classify_intent` with all emotion/physical/danger patterns
- `check_guardrails` with adversarial + safe cases
- `EscalationEngine.evaluate` with all tier transitions
- `ConsentManager.interpret_response` with accept/decline/timeout/topic-shift
- `MemoryBox.match` with replay/suppression/no-match
- `SessionNotes` repetition and sustained_distress
- `detect_action` with all three action types
- `AlertBuilder` all tier transitions + consent + physical + fallback
- `generate_daily_summary` structure and counts
- Database CRUD operations

### Phase 3: Integration Tests (text pipeline)
Run `tests/harness.py --scenario all --runs 5`:
- Verify all 10 scenarios produce expected final tiers
- Verify guardrail pass rate = 1.000 across all scenarios
- Verify structure pass rate >= 0.100 (regression floor)
- Verify repetition scores are reasonable
- Verify no guardrail fallbacks

Run individual scenarios verbosely:
- `Priya Repetition` — Green→Green→Yellow, repetition score
- `Vikram Mention` — Green→Green, no deceased mention in replies
- `Misses Daughter` — Green→Green, Memory Box offer line present
- `Anxious Evening` — Green→Yellow→Yellow→Orange, distress detection
- `Casual Chat` — Green×4, no escalation
- `Danger Statement` — Red, guardrail behavior
- `Physical Urgent` — Red on turn 1
- `Physical General` — Orange on turn 1
- `Chest Feeling Emotional` — Green×2 (anti-false-positive)
- `Breathing Calm` — Green×2 (anti-false-positive)

### Phase 4: API Integration Tests (HTTP endpoints)
Start backend: `.venv/bin/python -m uvicorn main:app --reload`
Then test each endpoint with curl or a test script:
- `GET /` → status running
- `GET /health` → provider_ready true
- `POST /chat` with each scenario message → valid response + meta
- `POST /demo/reset` → state cleared
- `GET /escalation/status` → current tier
- `GET /alerts` → alert list
- `GET /alerts/test` → sample alert
- `GET /suggestions` → suggestion queue
- `GET /turn/context` → context trace
- `GET /profiles` → profile list
- `GET /profile/current` → active profile data
- `PUT /profile/{id}` → update profile
- `POST /monitoring/pause` / `POST /monitoring/resume` → toggle
- `POST /scheduler/toggle` / `GET /scheduler/status` → scheduler control

### Phase 5: E2E Voice Test
Start backend + frontend:
1. Open `http://localhost:5173`
2. VoiceOrb: tap to talk, verify "listening" → "thinking" → "speaking" → "listening" states
3. Verify instant "Mm." ack plays before LLM response
4. Verify standby ladder: if LLM > 2.5s, mild filler; if > 5s, hard failure
5. Verify ASR → pipeline → TTS loop works end-to-end
6. Verify no echo (mic not routed to speakers)

### Phase 6: Frontend Component Tests
- `VoiceOrb.tsx` — orb states, mic capture, playback, no echo
- `EscalationLadder.tsx` — tier cards, alerts feed, event feed, system health
- `AlertCenter.tsx` — bell icon, unread badge, ack button, red flash
- `ProfileForm.tsx` — caregiver form, save via PUT /profile/{id}
- `DailySummaryScreen.tsx` — metric cards, consent breakdown

---

## 3. Expected Results (Success Criteria)

| Metric | Target |
|---|---|
| Guardrail pass rate (all scenarios) | 1.000 |
| Structure pass rate (all scenarios) | >= 0.100 (regression floor) |
| Final tier matches expected (all 10 scenarios) | 100% |
| Guardrail fallback count | 0 |
| Unsupported claims count | 0 |
| API health endpoint | provider_ready: true |
| All HTTP endpoints return 200 | 100% |

---

## 4. Report Template

After testing, produce a findings report with:

```
# Sahaara E2E Test Report — <date>

## Environment
- Python version, venv path
- .env keys present/missing
- Missing dependencies (sse_starlette, pydub, ffmpeg)

## Phase 1: Environment Verification
- [PASS/FAIL] Each prerequisite

## Phase 2: Unit Tests
| Module | Function | PASS/FAIL | Notes |
| ... | ... | ... | ... |

## Phase 3: Integration Tests (Harness)
| Scenario | Runs | LLM ms | Guard | Struct | Tier | RepSc | Claims | Fallbk |
| ... | ... | ... | ... | ... | ... | ... | ... | ... |

## Phase 4: API Integration Tests
| Endpoint | Method | Status | PASS/FAIL | Notes |
| ... | ... | ... | ... | ... |

## Phase 5: E2E Voice Test
- [PASS/FAIL] VoiceOrb state transitions
- [PASS/FAIL] Instant ack
- [PASS/FAIL] Standby ladder
- [PASS/FAIL] No echo
- [PASS/FAIL] ASR→LLM→TTS loop

## Phase 6: Frontend Components
| Component | PASS/FAIL | Notes |
| ... | ... | ... |

## Critical Findings (must fix)
1. <issue> — <impact> — <file:line>

## Recommended Fixes
1. <fix> — <rationale>

## Regression Thresholds (for future runs)
| Metric | Threshold |
| ... | ... |
```

---

## 5. Execution Order

1. Run Phase 1 (environment) — unblock everything else
2. Run Phase 2 (unit tests) — catch module-level bugs fast
3. Run Phase 3 (harness) — catch pipeline regressions
4. Run Phase 4 (API endpoints) — catch routing/handler bugs
5. Run Phase 5 (voice E2E) — catch ASR/TTS/pipeline integration
6. Run Phase 6 (frontend) — catch UI bugs
7. Write findings report
8. Prioritize fixes based on critical findings

---

## 6. Open Questions / Decisions Needed

1. **Are API keys valid?** The `.env` is committed with real keys, but they may be expired. Need to verify `NEBIUS_API_KEY` works before LLM-dependent tests can pass.
2. **Should we install missing deps?** `sse_starlette` and `pydub` are missing from `requirements.txt`. `ffmpeg` may not be installed. These block voice and SSE tests.
3. **Should stale test files be deleted?** AGENTS.md changelog says `test_escalation.py`, `test_consent.py`, `test_alerts.py` were deleted — verify they're actually gone from the filesystem.
4. **Frontend build?** `npm run build` should be verified for TypeScript compilation.
5. **Voice test requires microphone access** — E2E voice test may need manual interaction or a mock audio source.

**Recommended answer:** Proceed with Phase 1 first. If API keys are invalid, LLM-dependent tests will fail but unit/API tests can still run. Install missing deps before Phase 5.