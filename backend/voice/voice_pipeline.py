"""
voice/voice_pipeline.py

Orchestrator only — the actual ASR/TTS logic lives in asr.py / tts.py.
Picks Nemotron 3 VoiceChat if available, otherwise the cascaded path.

Nemotron 3 VoiceChat is gated early access as of writing (no self-serve
API key yet: https://developer.nvidia.com/nemotron-voicechat-early-access).
check_voicechat_availability() returns False until that's wired up for
real, so the cascaded path is what actually runs today.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from typing import AsyncIterator, Optional, Tuple

from voice import asr, tts

logger = logging.getLogger("voice.pipeline")

NVIDIA_API_KEY = os.environ.get("NVIDIA_API_KEY")
VOICECHAT_AVAILABILITY_TTL_SECONDS = 300

_voicechat_cache: Tuple[Optional[bool], float] = (None, 0.0)


class VoiceBackendUnavailable(Exception):
    pass


async def check_voicechat_availability() -> bool:
    global _voicechat_cache
    available, checked_at = _voicechat_cache
    now = time.time()
    if available is not None and (now - checked_at) < VOICECHAT_AVAILABILITY_TTL_SECONDS:
        return available

    if not NVIDIA_API_KEY:
        logger.info("VoiceChat: no NVIDIA_API_KEY set.")
        _voicechat_cache = (False, now)
        return False

    # TODO: once your early-access application is approved, replace this
    # with a real lightweight probe against the NIM endpoint NVIDIA gives
    # you — open the connection, send a short silence frame, confirm a
    # response frame comes back within budget, close.
    logger.warning("VoiceChat: no confirmed endpoint wired up yet.")
    _voicechat_cache = (False, now)
    return False


async def _llm_reply(user_text: str) -> str:
    """
    Runs the exact same pipeline /chat uses: intent classification, memory
    box matching, repeated-question/sustained-distress checks, prompt
    assembly, the LLM call, and post-generation guardrails. This is the
    whole point of extracting conversation.py — voice mode gets the same
    safety behavior as text mode, not a second unguarded path.

    process_turn() is synchronous (same as main.py's usage), so it's run
    in a thread to avoid blocking the event loop — audio streaming for
    other things shouldn't stall on one LLM call.
    """
    from conversation import process_turn

    result = await asyncio.to_thread(process_turn, user_text)

    if "error" in result:
        # Mirrors main.py's behavior: provider not configured, or the LLM
        # call itself failed. Surface it as an exception so run_cascaded's
        # caller can decide whether to hit the standby ladder (Part 2.3)
        # rather than silently speaking nothing.
        raise RuntimeError(result["error"])

    # meta (emotion, guardrail_fallback_used, etc.) is available on `result`
    # too, if voice mode ever wants to log/surface it the way the text
    # frontend can — not used here, just noting it's not being thrown away.
    return result["response"]


async def run_cascaded(audio_in: AsyncIterator[bytes]) -> AsyncIterator[bytes]:
    async for text, is_utterance_end in asr.stream_transcripts(audio_in):
        if not is_utterance_end:
            continue  # interim result, not a finished turn

        logger.info("User said: %s", text)
        reply_text = await _llm_reply(text)
        logger.info("Agent reply: %s", reply_text)

        async for audio_chunk in tts.stream_speech(reply_text):
            yield audio_chunk


async def get_active_backend_name() -> str:
    if await check_voicechat_availability():
        return "nemotron-3-voicechat"
    if asr.is_configured() and tts.is_configured():
        return "cascaded"
    raise VoiceBackendUnavailable(
        "Neither VoiceChat nor the cascaded backend is ready. "
        "Check NVIDIA_API_KEY / DEEPGRAM_API_KEY / CARTESIA_API_KEY / CARTESIA_VOICE_ID."
    )


async def stream(audio_in: AsyncIterator[bytes]) -> AsyncIterator[bytes]:
    backend = await get_active_backend_name()
    logger.info("Active voice backend: %s", backend)

    if backend == "nemotron-3-voicechat":
        raise VoiceBackendUnavailable("VoiceChat streaming not implemented yet.")

    async for chunk in run_cascaded(audio_in):
        yield chunk


async def _run_check():
    vc_ok = await check_voicechat_availability()
    casc_ok = asr.is_configured() and tts.is_configured()

    print("\n--- Voice pipeline availability check ---")
    print(f"Nemotron 3 VoiceChat : {'AVAILABLE' if vc_ok else 'unavailable'}")
    print(f"Cascaded fallback    : {'AVAILABLE' if casc_ok else 'unavailable (check env vars)'}")

    try:
        backend = await get_active_backend_name()
        print(f"\n=> Will use: {backend}\n")
    except VoiceBackendUnavailable as e:
        print(f"\n=> {e}\n")
        sys.exit(1)


if __name__ == "__main__":
    if "--check" in sys.argv:
        asyncio.run(_run_check())
    else:
        print("Usage: python -m voice.voice_pipeline --check")