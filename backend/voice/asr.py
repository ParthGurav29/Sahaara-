"""
voice/asr.py

Deepgram streaming ASR. Exposes one function, `stream_transcripts`, which
takes an async iterator of raw audio bytes and yields (text, is_utterance_end).

is_utterance_end is True only on Deepgram's UtteranceEnd event — the real
"they're done talking" signal, driven by actual speech patterns rather than
a flat silence timer (see docs/architecture.md, Part 2.2). Interim results
are yielded too, with is_utterance_end=False, so the caller can drive live
UI feedback (VoiceOrb's "listening" pulse reacting to real speech) without
treating them as a finished turn.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import AsyncIterator, Tuple

import websockets

logger = logging.getLogger("voice.asr")

DEEPGRAM_API_KEY = os.environ.get("DEEPGRAM_API_KEY")
DEEPGRAM_WS_URL = (
    "wss://api.deepgram.com/v1/listen"
    "?model=nova-3&encoding=linear16&sample_rate=16000&interim_results=true"
    "&endpointing=300&utterance_end_ms=1000&vad_events=true&punctuate=true"
)
# endpointing=300: Deepgram's speech-pattern-based endpointing, marks a
#   Results message is_final — used here only for interim UI updates.
# utterance_end_ms=1000: the real "they're done" signal via UtteranceEnd —
#   THIS triggers the LLM call, not the first is_final, so a mid-thought
#   pause doesn't get treated as a finished turn.


def is_configured() -> bool:
    return bool(DEEPGRAM_API_KEY)


async def stream_transcripts(
    audio_in: AsyncIterator[bytes],
) -> AsyncIterator[Tuple[str, bool]]:
    if not DEEPGRAM_API_KEY:
        raise RuntimeError("DEEPGRAM_API_KEY is not set.")

    headers = {"Authorization": f"Token {DEEPGRAM_API_KEY}"}
    accumulated = ""

    async with websockets.connect(DEEPGRAM_WS_URL, extra_headers=headers) as ws:

        async def sender():
            try:
                async for chunk in audio_in:
                    await ws.send(chunk)
            except asyncio.CancelledError:
                pass
            finally:
                try:
                    await ws.send(json.dumps({"type": "CloseStream"}))
                except Exception:
                    pass

        send_task = asyncio.create_task(sender())
        try:
            async for raw in ws:
                msg = json.loads(raw)
                msg_type = msg.get("type")

                if msg_type == "Results":
                    alt = msg["channel"]["alternatives"][0]
                    transcript = alt.get("transcript", "")
                    if not transcript:
                        continue
                    if msg.get("is_final"):
                        accumulated = (accumulated + " " + transcript).strip()
                    yield transcript, False

                elif msg_type == "UtteranceEnd":
                    if accumulated:
                        final_text, accumulated = accumulated, ""
                        yield final_text, True
        finally:
            send_task.cancel()
            await asyncio.gather(send_task, return_exceptions=True)