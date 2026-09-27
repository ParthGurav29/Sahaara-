"""
voice/fillers.py

Pre-rendered filler audio. Rendered through Cartesia exactly ONCE per line,
then cached to disk (voice/assets/*.raw) and to memory — never generated
live, since the whole point (Part 2.2 #1) is that these are instant.

get_ack_audio() is the Part 2.2 piece: call it the moment UtteranceEnd
lands, BEFORE the LLM call starts. It buys 1-2s of real generation time
without the person perceiving any gap.

The other lines here are for the Part 2.3 standby ladder — defined now so
every filler line lives in one place, wired into voice_pipeline in the
next block.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Dict

from voice import tts

logger = logging.getLogger("voice.fillers")

ASSETS_DIR = Path(__file__).resolve().parent / "assets"
ASSETS_DIR.mkdir(exist_ok=True)

# Part 2.2 #1 — keep this SHORT. A sound, not a phrase — "I heard you",
# not a sentence that itself takes time to speak.
ACK_FILLER = {"key": "ack_short", "text": "Mm."}

# Part 2.3 standby ladder tiers (wired into the pipeline in a later block).
MILD_DELAY_FILLER = {
    "key": "mild_delay",
    "text": "Let me think about that for a second.",
}
# The "real delay" and "hard failure" tiers reuse
# safety.guardrails.SAFE_FALLBACK_RESPONSE and a dedicated hard-failure
# line — rendered the same way, added when Part 2.3 is built.

_cache: Dict[str, bytes] = {}
_locks: Dict[str, asyncio.Lock] = {}


async def _load_or_render(key: str, text: str) -> bytes:
    if key in _cache:
        return _cache[key]

    lock = _locks.setdefault(key, asyncio.Lock())
    async with lock:
        if key in _cache:  # re-check after acquiring the lock
            return _cache[key]

        path = ASSETS_DIR / f"{key}.raw"
        if path.exists():
            data = path.read_bytes()
            _cache[key] = data
            return data

        if not tts.is_configured():
            raise RuntimeError(
                f"Cannot render filler '{key}': Cartesia isn't configured "
                "(CARTESIA_API_KEY / CARTESIA_VOICE_ID)."
            )

        logger.info("Rendering filler '%s' for the first time — will be cached from now on.", key)
        chunks = []
        async for chunk in tts.stream_speech(text):
            chunks.append(chunk)
        data = b"".join(chunks)

        path.write_bytes(data)
        _cache[key] = data
        return data


async def get_ack_audio() -> bytes:
    return await _load_or_render(ACK_FILLER["key"], ACK_FILLER["text"])


async def get_mild_delay_audio() -> bytes:
    return await _load_or_render(MILD_DELAY_FILLER["key"], MILD_DELAY_FILLER["text"])


async def prewarm_all() -> None:
    """Call once at server startup so the FIRST real conversation doesn't
    pay the one-time render cost live. Safe to call even before Cartesia
    is configured — logs and skips rather than raising."""
    if not tts.is_configured():
        logger.warning("Skipping filler prewarm: Cartesia not configured yet.")
        return
    await get_ack_audio()
    await get_mild_delay_audio()
    logger.info("Filler audio prewarmed and cached in %s", ASSETS_DIR)