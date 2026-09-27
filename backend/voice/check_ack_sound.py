"""
voice/check_ack_sound.py

Standalone sanity check for the instant-ack filler (Part 2.2 #1). Renders
(or loads the cached) "Mm." through Cartesia and writes it out as a
playable .wav so you can actually listen to it, separate from the full
mic -> ASR -> LLM -> TTS loop which needs a WebSocket UI that doesn't
exist yet (Part 2.3).

Run from backend/:
    python -m voice.check_ack_sound

Output:
    backend/voice/assets/ack_short_preview.wav
"""

from __future__ import annotations

import asyncio
import logging
import wave
from pathlib import Path

from dotenv import load_dotenv

logging.basicConfig(level=logging.DEBUG)

BASE_DIR = Path(__file__).resolve().parent.parent.parent
load_dotenv(BASE_DIR / ".env")

from voice import fillers, tts  # noqa: E402  (must come after load_dotenv)

# Must match tts.py's stream_speech() output_format exactly, or the .wav
# header will lie about the data and it'll play as noise/garbage.
SAMPLE_RATE = 22050
SAMPLE_WIDTH_BYTES = 2  # pcm_s16le = 16-bit
CHANNELS = 1


async def main():
    if not tts.is_configured():
        print(
            "Cartesia isn't configured — set CARTESIA_API_KEY and "
            "CARTESIA_VOICE_ID in your .env first."
        )
        return

    print("Fetching ack audio (renders via Cartesia on first run, cached after)...")
    pcm_bytes = await fillers.get_ack_audio()
    print(f"Got {len(pcm_bytes)} bytes of raw PCM audio.")

    out_path = Path(__file__).resolve().parent / "assets" / "ack_short_preview.wav"
    out_path.parent.mkdir(exist_ok=True)

    with wave.open(str(out_path), "wb") as wav_file:
        wav_file.setnchannels(CHANNELS)
        wav_file.setsampwidth(SAMPLE_WIDTH_BYTES)
        wav_file.setframerate(SAMPLE_RATE)
        wav_file.writeframes(pcm_bytes)

    print(f"\nWrote {out_path}")
    print("Open it with any player, e.g.:")
    print(f"  afplay {out_path}   # macOS")


if __name__ == "__main__":
    asyncio.run(main())