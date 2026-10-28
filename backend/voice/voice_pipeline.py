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
import json
import logging
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator, Literal, Optional, Tuple

from dotenv import load_dotenv

# Must run BEFORE importing asr/tts below — they read os.environ.get(...)
# at import time, so if .env isn't loaded yet those values come back None
# even when the file has real keys in it. This matches BASE_DIR in
# conversation.py / main.py: backend/voice/voice_pipeline.py -> parent
# (voice) -> parent (backend) -> parent (repo root, where .env lives).
BASE_DIR = Path(__file__).resolve().parent.parent.parent
load_dotenv(BASE_DIR / ".env")

from voice import asr, fillers, tts

from events.event_log import EventType, FallbackType, get_event_log

logger = logging.getLogger("voice.pipeline")

# Timing log file path
TIMING_LOG_PATH = BASE_DIR / "backend" / "logs" / "voice_timing.jsonl"
# Ensure log directory exists
TIMING_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

VoiceOrbState = Literal["listening", "thinking", "speaking"]


@dataclass
class InitiativeEvent:
    """Proactive initiative from scheduler to be delivered to the person."""
    text: str
    slot_id: str
    wording_id: str
    category: str


@dataclass
class VoiceEvent:
    """
    One item from the pipeline's output stream. `kind` says which field is
    populated:
      - "state": orb-facing state change (listening/thinking/speaking) —
        the WebSocket handler forwards this as a JSON text frame.
      - "audio": raw PCM bytes to play — forwarded as a binary frame.
      - "initiative": proactive initiative from scheduler — JSON text frame
    Two kinds in one stream (rather than two separate streams) because
    order matters: the state change has to reach the client before the
    audio it explains, and a single ordered stream guarantees that.
    """
    kind: Literal["state", "audio", "initiative"]
    state: Optional[VoiceOrbState] = None
    audio: Optional[bytes] = None
    initiative: Optional[InitiativeEvent] = None

# Standby ladder timing thresholds (seconds after ack sound fires)
# MILD_DELAY_THRESHOLD: when to play the "let me think" filler — but KEEP
# the LLM task running; if it completes before REAL_DELAY_THRESHOLD, speak it
# right after the filler. Set high enough that normal replies (<3s) don't
# trigger the filler.
MILD_DELAY_THRESHOLD = 5.0
# REAL_DELAY_THRESHOLD: if the LLM hasn't answered by this point, play the
# hard-failure fallback and cancel the LLM task. Danger and Red tier alerts
# are not affected by this ladder (they bypass the standby logic).
REAL_DELAY_THRESHOLD = 10.0

# After Sahaara finishes speaking, hold the "speaking" state for a beat
# before handing the turn back. The mic gate stays closed during this pause,
# so the speaker tail decays with no chance of becoming a new turn, and the
# person gets a clear gap instead of an instant flip to "listening".
TURN_PAUSE_SECONDS = float(os.environ.get("SAHAARA_TURN_PAUSE_SECONDS", "1.0"))

NVIDIA_API_KEY = os.environ.get("NVIDIA_API_KEY")
VOICECHAT_AVAILABILITY_TTL_SECONDS = 300

_voicechat_cache: Tuple[Optional[bool], float] = (None, 0.0)


class VoiceBackendUnavailable(Exception):
    pass


def _write_timing_log(turn_id: str, timings: dict) -> None:
    """Write a single JSON line to the timing log file."""
    try:
        # Compute derived gaps
        gaps = {}
        t = timings
        if "t_speech_end" in t and "t_llm_request" in t:
            gaps["asr_end_to_llm_request_ms"] = round((t["t_llm_request"] - t["t_speech_end"]) * 1000, 1)
        if "t_llm_request" in t and "t_first_llm_token" in t:
            gaps["llm_time_ms"] = round((t["t_first_llm_token"] - t["t_llm_request"]) * 1000, 1)
        if "t_first_llm_token" in t and "t_first_tts_byte" in t:
            gaps["llm_token_to_tts_first_byte_ms"] = round((t["t_first_tts_byte"] - t["t_first_llm_token"]) * 1000, 1)
        if "t_first_tts_byte" in t and "t_first_audio_sent" in t:
            gaps["tts_first_byte_to_audio_sent_ms"] = round((t["t_first_audio_sent"] - t["t_first_tts_byte"]) * 1000, 1)
        if "t_speech_end" in t and "t_first_audio_sent" in t:
            gaps["speech_end_to_first_audio_ms"] = round((t["t_first_audio_sent"] - t["t_speech_end"]) * 1000, 1)
        if "t_speech_end" in t and "t_ack_started" in t:
            gaps["speech_end_to_ack_started_ms"] = round((t["t_ack_started"] - t["t_speech_end"]) * 1000, 1)
        
        log_entry = {
            "turn_id": turn_id,
            "timestamp": time.time(),
            "timings": t,
            "gaps": gaps,
        }
        with open(TIMING_LOG_PATH, "a") as f:
            f.write(json.dumps(log_entry) + "\n")
    except Exception as e:
        logger.warning("Failed to write timing log: %s", e)


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

    Voice turns use max_tokens=150 (~120-150 words) to keep replies short
    enough for TTS — this matches the 2-4 sentence reply-length rule.
    """
    from conversation import process_turn

    t_start = time.time()
    result = await asyncio.to_thread(process_turn, user_text, max_tokens=150)
    t_elapsed = time.time() - t_start
    logger.info(f"[voice] LLM reply took {t_elapsed:.2f}s for: {user_text[:60]}...")

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


async def _delayed_filler(seconds: float, filler_fn):
    """Helper: wait `seconds`, then return pre-rendered filler audio."""
    await asyncio.sleep(seconds)
    return await filler_fn()


async def run_cascaded(audio_in: AsyncIterator[bytes]) -> AsyncIterator[VoiceEvent]:
    turn_counter = 0
    # Turn-taking gate: closed while we think/speak so the mic cannot feed
    # Sahaara's own TTS back into Deepgram as a new user turn.
    mic_gate: dict[str, bool] = {"enabled": True}
    async for text, is_utterance_end in asr.stream_transcripts(audio_in, gate=mic_gate):
        if not is_utterance_end:
            continue  # interim result, not a finished turn — orb stays "listening"

        turn_counter += 1
        mic_gate["enabled"] = False
        turn_id = f"turn_{turn_counter}"
        timings: dict[str, float] = {}
        
        # t_speech_end: ASR utterance_end received
        t_speech_end = time.time()
        timings["t_speech_end"] = t_speech_end

        logger.info("User said: %s", text)

        # Part 2.4 state machine: listening -> thinking
        yield VoiceEvent(kind="state", state="thinking")

        # Part 2.2 #1 — instant ack, fires before the LLM call even starts.
        # Pre-rendered and cached (fillers.py), so this is genuinely
        # instant, not just fast.
        try:
            t_ack_started = time.time()
            timings["t_ack_started"] = t_ack_started
            yield VoiceEvent(kind="audio", audio=await fillers.get_ack_audio())
        except Exception as e:
            logger.warning("Ack filler unavailable, continuing without it: %s", e)

        # --- Standby Ladder (Part 2.3) ---
        # Race the LLM call against delay thresholds. Key design: mild_delay
        # fires a filler but does NOT cancel the LLM task — if the reply
        # arrives before REAL_DELAY_THRESHOLD, it's spoken right after the
        # filler. Only REAL_DELAY_THRESHOLD discards the LLM result.
        # Danger/Red turns are not affected by this ladder (they bypass it).
        
        t_llm_request = time.time()
        timings["t_llm_request"] = t_llm_request
        
        llm_task = asyncio.create_task(_llm_reply(text))
        mild_delay_task = asyncio.create_task(
            _delayed_filler(MILD_DELAY_THRESHOLD, fillers.get_mild_delay_audio)
        )
        real_delay_task = asyncio.create_task(
            _delayed_filler(REAL_DELAY_THRESHOLD, fillers.get_hard_failure_audio)
        )
        
        done, pending = await asyncio.wait(
            [llm_task, mild_delay_task, real_delay_task],
            return_when=asyncio.FIRST_COMPLETED,
        )
        
        if llm_task in done:
            # LLM completed first (before any filler) — normal path
            for t in pending:
                t.cancel()
            try:
                reply_text = llm_task.result()
                t_first_llm_token = time.time()
                timings["t_first_llm_token"] = t_first_llm_token
                logger.info("Agent reply: %s", reply_text)
                yield VoiceEvent(kind="state", state="speaking")
                first_tts = True
                async for audio_chunk in tts.stream_speech(reply_text):
                    if first_tts:
                        timings["t_first_tts_byte"] = time.time()
                        first_tts = False
                    yield VoiceEvent(kind="audio", audio=audio_chunk)
                    if "t_first_audio_sent" not in timings:
                        timings["t_first_audio_sent"] = time.time()
                _write_timing_log(turn_id, timings)
                event_log = get_event_log()
                if event_log:
                    event_log.log_timing(turn_id, timings)
            except Exception as e:
                error_msg = str(e)
                if "quota_exceeded" in error_msg.lower() or "quota exceeded" in error_msg.lower() or "402" in str(e):
                    logger.error("TTS quota exceeded: %s", e)
                    logger.info("TTS quota exceeded, playing quota exceeded fallback")
                    yield VoiceEvent(kind="audio", audio=await fillers.get_quota_exceeded_audio())
                else:
                    logger.error("LLM call failed, playing hard failure fallback: %s", e)
                    logger.info("standby_ladder_fallback tier=hard_failure latency_ms=%d", int(REAL_DELAY_THRESHOLD * 1000))
                    event_log = get_event_log()
                    if event_log:
                        event_log.log_fallback(FallbackType.STANDBY_HARD_FAILURE)
                    yield VoiceEvent(kind="audio", audio=await fillers.get_hard_failure_audio())
        
        elif mild_delay_task in done:
            # Mild delay fired — play filler but KEEP LLM running.
            # If LLM completes before REAL_DELAY_THRESHOLD, speak it after
            # the filler.
            yield VoiceEvent(kind="audio", audio=mild_delay_task.result())
            
            remaining = REAL_DELAY_THRESHOLD - MILD_DELAY_THRESHOLD
            done2, _ = await asyncio.wait(
                [llm_task, real_delay_task],
                timeout=remaining,
                return_when=asyncio.FIRST_COMPLETED,
            )
            
            if llm_task in done2:
                # LLM completed before real delay — speak it after the filler
                for t in pending:
                    if t is not llm_task:
                        t.cancel()
                try:
                    reply_text = llm_task.result()
                    t_first_llm_token = time.time()
                    timings["t_first_llm_token"] = t_first_llm_token
                    logger.info("Agent reply (after mild delay): %s", reply_text)
                    yield VoiceEvent(kind="state", state="speaking")
                    first_tts = True
                    async for audio_chunk in tts.stream_speech(reply_text):
                        if first_tts:
                            timings["t_first_tts_byte"] = time.time()
                            first_tts = False
                        yield VoiceEvent(kind="audio", audio=audio_chunk)
                        if "t_first_audio_sent" not in timings:
                            timings["t_first_audio_sent"] = time.time()
                    _write_timing_log(turn_id, timings)
                    event_log = get_event_log()
                    if event_log:
                        event_log.log_timing(turn_id, timings)
                except Exception as e:
                    error_msg = str(e)
                    if "quota_exceeded" in error_msg.lower() or "quota exceeded" in error_msg.lower() or "402" in str(e):
                        logger.error("TTS quota exceeded: %s", e)
                        yield VoiceEvent(kind="audio", audio=await fillers.get_quota_exceeded_audio())
                    else:
                        logger.error("LLM call failed after mild delay: %s", e)
                        event_log = get_event_log()
                        if event_log:
                            event_log.log_fallback(FallbackType.STANDBY_HARD_FAILURE)
                        yield VoiceEvent(kind="audio", audio=await fillers.get_hard_failure_audio())
            elif real_delay_task in done2:
                # Real delay fired before LLM — hard failure fallback
                for t in pending:
                    t.cancel()
                logger.warning("LLM did not respond within %.0fs — hard fallback", REAL_DELAY_THRESHOLD)
                event_log = get_event_log()
                if event_log:
                    event_log.log_fallback(FallbackType.STANDBY_HARD_FAILURE)
                yield VoiceEvent(kind="audio", audio=await real_delay_task)
        
        else:
            # real_delay_task fired first (before mild_delay) — hard fallback
            for t in pending:
                t.cancel()
            logger.warning("LLM did not respond within %.0fs — hard fallback", REAL_DELAY_THRESHOLD)
            event_log = get_event_log()
            if event_log:
                event_log.log_fallback(FallbackType.STANDBY_HARD_FAILURE)
            yield VoiceEvent(kind="audio", audio=await real_delay_task)

        # Hold the turn briefly after the reply audio. The orb stays
        # "speaking" here, so the frontend keeps the mic gated while the
        # speaker tail decays — that tail is what previously got picked up
        # as an immediate second user turn. Then hand the turn back with a
        # deliberate gap, rather than the old instant flip to "listening".
        await asyncio.sleep(TURN_PAUSE_SECONDS)

        # Reopen the mic only when it is genuinely the person's turn.
        mic_gate["enabled"] = True
        yield VoiceEvent(kind="state", state="listening")


async def get_active_backend_name() -> str:
    if await check_voicechat_availability():
        return "nemotron-3-voicechat"
    if asr.is_configured() and tts.is_configured():
        return "cascaded"
    raise VoiceBackendUnavailable(
        "Neither VoiceChat nor the cascaded backend is ready. "
        "Check NVIDIA_API_KEY / DEEPGRAM_API_KEY / CARTESIA_API_KEY / CARTESIA_VOICE_ID."
    )


async def stream(audio_in: AsyncIterator[bytes]) -> AsyncIterator[VoiceEvent]:
    backend = await get_active_backend_name()
    logger.info("Active voice backend: %s", backend)

    if backend == "nemotron-3-voicechat":
        raise VoiceBackendUnavailable("VoiceChat streaming not implemented yet.")

    yield VoiceEvent(kind="state", state="listening")

    async for event in run_cascaded(audio_in):
        yield event


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