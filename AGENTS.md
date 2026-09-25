# Sahaara — Agent Context

## Purpose
Consent-based AI companion for people living with dementia and their family caregivers. Calm, validating voice conversation for the person; meaningful (not noisy) escalation and daily summaries for the caregiver. Does not diagnose, treat, or replace human care.

## Tech Stack
- **Backend:** FastAPI (Python 3.11), WebSocket for the live voice loop
- **Frontend:** React + Vite + TypeScript, Tailwind
- **LLM / Voice:** Nebius Token Factory — Nemotron Nano/Lightning (fast turn-taking), Nemotron 3 Ultra (escalation judgment calls), Nemotron 3 VoiceChat if available day-one, else cascaded ASR → LLM → TTS fallback. `llm/gemini_client.py` exists only as a local dev fallback behind the same provider interface — not the intended stack, don't build features against it.
- **Data:** JSON profile store for hackathon (`backend/memory/data/`), no DB this weekend
- **Alerts:** webhook/SMS stub for caregiver notifications

## Folder Structure
```
backend/
  voice/       - ASR, TTS, voice pipeline
  llm/         - Nemotron client, intent/emotion detection, response policy
  memory/      - profile, memory box, session notes (+ data/)
  safety/      - policy engine, guardrails, escalation tiers
  dashboard/   - daily summary generator, alert dispatch
  tests/
frontend/
  src/voice-ui/    - Asha-facing conversation UI
  src/dashboard/   - caregiver dashboard, escalation ladder
demo/          - scripted scenario data, demo script, slides
docs/          - architecture, guardrails, open questions
```

## Commands
- `uvicorn backend.main:app --reload` — start backend
- `npm run dev` (in `frontend/`) — start frontend
- `pytest backend/tests/` — run backend tests

## Conventions
- Python: type hints on all function signatures, snake_case
- TypeScript: components PascalCase, hooks/utils camelCase
- One module = one responsibility (e.g. `escalation.py` never calls the LLM directly, only reads classified state)
- Every LLM response passes through `safety/policy_engine.py` and `safety/guardrails.py` BEFORE being sent to voice output — this order is non-negotiable, not a lint suggestion
- Prompt templates for the response policy (Listen → Validate → Ground → Offer one choice → Escalate only if needed) live in `llm/response_policy.py`, not scattered inline

## Architecture Decision: Policy Before Generation
The safety/policy engine sits as a hard boundary *before* the LLM call, not as a post-hoc filter on its output. See `docs/architecture.md` for the full pipeline diagram. Do not restructure this without updating that doc.

## Gotchas
- Never let the LLM assert unverified events to comfort someone (e.g. "he's on his way") — this is a guardrail test case, not a style preference
- Repetition handling must stay silent to the user — no "I already told you," ever, even if session_notes shows a high repeat count
- Escalation tiers (Green/Yellow/Orange/Red) are defined in `docs/architecture.md` — don't invent new tiers ad hoc
- Camera/vision is roadmap only — do not scaffold live camera code this weekend, it's a slide, not a feature
- API keys go in `.env` only, never committed — see `.env.example` for required vars

## Current Phase
Phase 0.2 (context files) — see `docs/` for architecture, guardrails, and open questions docs being written alongside this file.