"""
voice/tts.py

Cartesia streaming TTS. Exposes `stream_speech`, which takes reply text and
yields raw audio bytes as they're generated — sentence-level streaming, so
playback can start before the full reply is synthesized (Part 2.1: "TTS
should start speaking the first sentence as soon as it's generated").

NOTE: verify the request schema below against Cartesia's current docs before
relying on it — TTS vendor APIs shift fairly often and this is written from
general knowledge of their streaming protocol, not a live check.
"""

from __future__ import annotations

import json
import logging
import os
from typing import AsyncIterator

import websockets

logger = logging.getLogger("voice.tts")

CARTESIA_API_KEY = os.environ.get("CARTESIA_API_KEY")
CARTESIA_VOICE_ID = os.environ.get("CARTESIA_VOICE_ID", "")
CARTESIA_WS_URL = "wss://api.cartesia.ai/tts/websocket"
CARTESIA_VERSION = "2024-06-10"  # verify current value in Cartesia's docs


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
            "model_id": "sonic-2",  # verify current model id in Cartesia's docs
            "transcript": text,
            "voice": {"mode": "id", "id": CARTESIA_VOICE_ID},
            "output_format": {
                "container": "raw",
                "encoding": "pcm_s16le",
                "sample_rate": 22050,
            },
            "context_id": "sahaara-tts",
        }
        logger.info("TTS request: %s", request)
        await ws.send(json.dumps(request))

        async for raw in ws:
            msg = json.loads(raw)
            if msg.get("type") == "chunk" and msg.get("data"):
                import base64
                yield base64.b64decode(msg["data"])
            elif msg.get("type") == "done":
                break
            elif msg.get("type") == "error":
                raise RuntimeError(f"Cartesia TTS error: {msg}")