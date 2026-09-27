"""
backend/main.py

FastAPI entrypoint. The actual turn logic (memory -> intent -> prompt ->
LLM -> guardrails) now lives in conversation.py, shared with the voice
pipeline (voice/voice_pipeline.py) — this file is just the HTTP wrapper.

Request/response shape is unchanged from the original prototype's /chat
route, so App.tsx needs no changes.
"""

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from conversation import process_turn, provider

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
    return process_turn(request.message)