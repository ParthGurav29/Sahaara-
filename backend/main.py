"""
backend/main.py

FastAPI entrypoint. /chat runs the full Phase 1 pipeline:

    memory (profile, memory box, session notes)
        -> intent/emotion classification
        -> response_policy prompt assembly
        -> provider.complete() (Nebius/Nemotron — see llm/nemotron_client.py)
        -> guardrails + structure check
        -> (fallback to a safe canned line if guardrails fail)

Request/response shape matches the original prototype's /chat route,
so the React frontend (App.tsx) needs no changes regardless of which
provider is active under the hood.

Session handling: this hackathon MVP demos a single persona (Asha) for
a single active caregiver/session, so session state is one global
in-memory SessionNotes instance, not per-request or per-user. That's a
deliberate scope decision, not an oversight — see docs/architecture.md.
Multi-session support is out of scope for this weekend.
"""

import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

from llm.intent_emotion import classify_intent
from llm.provider import ProviderError, get_provider
from llm.response_policy import build_prompt, check_response_structure
from memory.memory_box import MemoryBox
from memory.profile import load_profile
from memory.session_notes import SessionNotes
from safety.guardrails import SAFE_FALLBACK_RESPONSE, check_guardrails

app = FastAPI(title="Sahaara API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---- Startup: load once, reuse across requests ----------------------------
# Profile is static reference data; provider is a reusable HTTP client;
# memory box just wraps the profile; session_notes IS mutable per-turn
# state, kept global for this single-persona hackathon scope (see above).

profile = load_profile()
memory_box = MemoryBox(profile)
session_notes = SessionNotes(profile_id=profile.profile_id)

try:
    provider = get_provider()
except ProviderError as e:
    # Don't crash the whole app at import time if the key is missing —
    # let /health and / still respond, and surface the real error from
    # /chat when it's actually called. Easier to debug from the frontend.
    provider = None
    _provider_init_error = str(e)
else:
    _provider_init_error = None


class ChatRequest(BaseModel):
    message: str


@app.get("/")
def root():
    return {
        "name": "Sahaara",
        "status": "running",
        "provider": os.environ.get("LLM_PROVIDER", "nemotron"),
    }


@app.get("/health")
def health():
    return {
        "status": "ok",
        "provider_ready": provider is not None,
    }


@app.post("/chat")
async def chat(request: ChatRequest):
    if provider is None:
        return {"error": f"LLM provider not configured: {_provider_init_error}"}

    user_message = request.message

    # ---- 1. Memory / context gathering ----
    intent = classify_intent(user_message)
    memory_match = memory_box.match(user_message)
    is_repeat = session_notes.is_repeated_question(user_message)
    sustained = session_notes.sustained_distress()

    # ---- 2. Build the prompt (Listen/Validate/Ground/Offer policy) ----
    bundle = build_prompt(
        profile,
        user_message,
        intent,
        memory_box_match=memory_match,
        is_repeated_question=is_repeat,
        sustained_distress=sustained,
    )

    # ---- 3. Call the LLM ----
    try:
        result = provider.complete_from_prompt_bundle(bundle)
    except ProviderError as e:
        return {"error": f"LLM request failed: {e}"}

    response_text = result.text

    # ---- 4. Post-generation safety checks ----
    structure = check_response_structure(response_text)
    guardrail = check_guardrails(response_text, profile)

    used_fallback = False
    if not guardrail.passed:
        # Hard rule: a guardrail violation is never sent to the person,
        # no exceptions. Fall back to a safe canned line instead of
        # retrying live during a request (regeneration is a future
        # improvement, not required for the hackathon MVP).
        response_text = SAFE_FALLBACK_RESPONSE
        used_fallback = True

    # ---- 5. Update session state for future turns ----
    session_notes.record_turn(user_message, emotion=intent.emotion)

    return {
        "response": response_text,
        # Debug/demo metadata — the frontend can ignore these fields
        # entirely, or surface them later for the caregiver dashboard.
        "meta": {
            "emotion": intent.emotion,
            "is_repeated_question": is_repeat,
            "sustained_distress": sustained,
            "memory_box_offered": memory_match.offer_line if memory_match else None,
            "guardrail_fallback_used": used_fallback,
            "guardrail_violations": guardrail.violations if not guardrail.passed else [],
            "structure_check_passed": structure.passes,
            "model": result.model,
        },
    }