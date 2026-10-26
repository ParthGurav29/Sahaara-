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
import time
from typing import AsyncIterator, Dict, Optional, Tuple

import websockets

logger = logging.getLogger("voice.asr")

DEEPGRAM_API_KEY = os.environ.get("DEEPGRAM_API_KEY")
# Deepgram closes a streaming socket after ~10s with no audio or text.
# A protocol-level KeepAlive is sent when the mic has been gated/idle for
# this long, so slow LLM/TTS turns can't kill the session.
KEEPALIVE_INTERVAL_SECONDS = 5.0
KEEPALIVE_IDLE_THRESHOLD_SECONDS = 5.0
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
    gate: Optional[Dict[str, bool]] = None,
) -> AsyncIterator[Tuple[str, bool]]:
    """Stream transcripts from Deepgram.

    ``gate`` is an optional mutable dict with an ``enabled`` key. When the
    pipeline is handling a turn (LLM/TTS), it flips the gate off so
    Sahaara's own speaker audio cannot be captured by the mic, re-sent
    here, and mistaken for a new user turn. Chunks are *dropped* while the
    gate is closed — never buffered — so no stale echo can surface later.
    """
    if not DEEPGRAM_API_KEY:
        raise RuntimeError("DEEPGRAM_API_KEY is not set.")

    headers = {"Authorization": f"Token {DEEPGRAM_API_KEY}"}
    accumulated = ""

    # websockets 14+ renamed extra_headers -> additional_headers.
    _major = int(websockets.__version__.split(".")[0])
    header_kwarg = "additional_headers" if _major >= 14 else "extra_headers"

    async with websockets.connect(DEEPGRAM_WS_URL, **{header_kwarg: headers}) as ws:

        last_audio_sent = time.monotonic()

        async def sender():
            nonlocal last_audio_sent
            try:
                async for chunk in audio_in:
                    if gate is not None and not gate.get("enabled", True):
                        continue
                    await ws.send(chunk)
                    last_audio_sent = time.monotonic()
            except asyncio.CancelledError:
                pass
            finally:
                try:
                    await ws.send(json.dumps({"type": "CloseStream"}))
                except Exception:
                    pass

        async def keepalive():
            # Deepgram closes a streaming socket after ~10s with no audio or
            # text. While a turn is being handled the gate is closed (and the
            # browser stops sending), so send a protocol KeepAlive instead of
            # audio: no echo risk, and the socket survives slow LLM/TTS turns.
            try:
                while True:
                    await asyncio.sleep(KEEPALIVE_INTERVAL_SECONDS)
                    if (
                        time.monotonic() - last_audio_sent
                        >= KEEPALIVE_IDLE_THRESHOLD_SECONDS
                    ):
                        await ws.send(json.dumps({"type": "KeepAlive"}))
            except asyncio.CancelledError:
                pass
            except Exception:
                pass

        send_task = asyncio.create_task(sender())
        keepalive_task = asyncio.create_task(keepalive())
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
            keepalive_task.cancel()
            await asyncio.gather(send_task, keepalive_task, return_exceptions=True)
