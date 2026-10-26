"""
voice/tts.py

Cartesia streaming TTS. Exposes `stream_speech`, which takes reply text and
yields raw audio bytes as they're generated — sentence-level streaming, so
playback can start before the full reply is synthesized (Part 2.1: "TTS
should start speaking the first sentence as soon as it's generated").

Request schema verified against Cartesia's live AsyncAPI spec on 2026-10-23
(API version 2026-08-14). Key changes from the original 2024-era code:
  - cartesia_version bumped 2024-06-10 -> 2026-08-14
  - model_id: see the MODEL PINNING note below. Currently pinned to the
    sunsetted sonic-2 for demo voice continuity only. sonic-2 is past its
    Oct 20 2026 sunset date and must be migrated before any real deploy.
  - voice: the old {"mode": "id", "id": ...} shape used a "mode" field that
    no longer appears in the schema. Current spec accepts either a plain ID
    string or an object with a required "id". We use the object form for
    forward-compatibility (spec notes additional fields may be added).
  - context_id: now a per-call UUID rather than a static string, per the
    spec's "A unique identifier for the context."
  - language: explicitly set to "en" (optional in spec, but improves
    normalization reliability).

Voice tuning (speed / emotion / volume) is deliberately NOT set here — that
lands in W4b behind a separate change so API-modernization regressions can
be isolated from tuning regressions.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import uuid
from typing import AsyncIterator

import websockets

logger = logging.getLogger("voice.tts")

CARTESIA_API_KEY = os.environ.get("CARTESIA_API_KEY")
CARTESIA_VOICE_ID = os.environ.get("CARTESIA_VOICE_ID", "")
CARTESIA_WS_URL = "wss://api.cartesia.ai/tts/websocket"
CARTESIA_VERSION = "2026-08-14"  # verified against docs.cartesia.ai on 2026-10-23

# MODEL PINNING — read before changing this.
#
# As of 2026-10-23, per docs.cartesia.ai/build-with-cartesia/tts-models/older-models:
#   - "sonic-2 and all sonic-2-* snapshots will stop working after October 20, 2026."
#     We are past that date. sonic-2 still returns audio (Cartesia appears to be
#     enforcing the sunset lazily), but it is DEAD IN THE WATER and can fail at any
#     moment. Pinned to sonic-2 only for demo voice continuity until the A/B
#     listening test decides the migration target. Do not ship on sonic-2.
#   - "sonic-latest" is a DEPRECATED ALIAS for "sonic-preview" (a beta model,
#     "not intended for production usage, can change without notice"). Do not use it.
#
# Recommended migration target (dated snapshot, "never changes once released"):
#   sonic-3.6-2026-08-27
# Continuous-update alias (tracks newest stable 3.6):
#   sonic-3.6
CARTESIA_MODEL_ID = os.environ.get("CARTESIA_MODEL_ID", "sonic-2")
CARTESIA_LANGUAGE = os.environ.get("CARTESIA_LANGUAGE", "en")


def is_configured() -> bool:
    return bool(CARTESIA_API_KEY and CARTESIA_VOICE_ID)


async def stream_speech(text: str) -> AsyncIterator[bytes]:
    if not CARTESIA_API_KEY:
        raise RuntimeError("CARTESIA_API_KEY is not set.")
    if not CARTESIA_VOICE_ID:
        raise RuntimeError("CARTESIA_VOICE_ID is not set.")

    url = f"{CARTESIA_WS_URL}?api_key={CARTESIA_API_KEY}&cartesia_version={CARTESIA_VERSION}"

    async with websockets.connect(url) as ws:
        request = {
            "model_id": CARTESIA_MODEL_ID,
            "transcript": text,
            "voice": {"id": CARTESIA_VOICE_ID},
            "output_format": {
                "container": "raw",
                "encoding": "pcm_s16le",
                "sample_rate": 22050,
            },
            "language": CARTESIA_LANGUAGE,
            "context_id": str(uuid.uuid4()),
        }
        await ws.send(json.dumps(request))

        async for raw in ws:
            msg = json.loads(raw)
            if msg.get("type") == "chunk" and msg.get("data"):
                yield base64.b64decode(msg["data"])
            elif msg.get("type") == "done":
                break
            elif msg.get("type") == "error":
                error_code = msg.get("error_code", "")
                error_message = msg.get("message", str(msg))
                if error_code == "quota_exceeded":
                    raise RuntimeError(
                        f"Cartesia TTS quota exceeded: {error_message}. "
                        f"Please upgrade your Cartesia subscription at https://play.cartesia.ai/subscription "
                        f"or use a different voice provider."
                    )
                raise RuntimeError(f"Cartesia TTS error: {error_message} (code: {error_code})")