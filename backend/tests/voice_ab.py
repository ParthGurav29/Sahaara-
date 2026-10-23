"""
backend/tests/voice_ab.py

Blind A/B(/C) voice comparison across Cartesia models. Renders the same set
of Sahaara-style replies through multiple model_ids and writes WAV files with
neutral labels, plus a separate answer key that must NOT be opened until after
listeners have rated.

Why this exists (2026-10-23):
  - sonic-2 sunsetted Oct 20 2026 but is still pinned in voice/tts.py for
    demo voice continuity. We must pick a migration target by ear.
  - "sonic-latest" turned out to be a DEPRECATED alias for "sonic-preview"
    (a beta). So we compare against the recommended stable snapshot too.

Models compared:
  1. sonic-2                        (current pin, sunsetted, still serving)
  2. sonic-latest                   (deprecated alias -> sonic-preview, beta)
  3. sonic-3.6-2026-08-27           (dated snapshot, never changes — recommended)

Run:
    cd backend && .venv/bin/python -m tests.voice_ab

Output:
    voice/assets/ab_test/<NN>_<LABEL>.wav     <- give these to listeners
    voice/assets/ab_test/AB_KEY.json          <- do not open until rated
    voice/assets/ab_test/AB_KEY.txt           <- human-readable, same rule

Each sample is rendered with the SAME voice id, language, output format, and
schema as production — only model_id varies, so any perceived difference is
attributable to the model.
"""

from __future__ import annotations

import asyncio
import base64
import json
import random
import struct
import time
import uuid
import wave
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[2] / ".env")

from voice import tts

SAMPLE_RATE = 22050  # must match tts.py output_format.sample_rate

# Models to compare. Order here is the canonical order; display labels are
# shuffled per-sample so listeners can't learn "A = sonic-2" across samples.
MODELS = [
    "sonic-2",
    "sonic-latest",
    "sonic-3.6-2026-08-27",
]

# 6 replies spanning the registers Sahaara actually uses. Short, spoken,
# contractions — mirrors the post-M1 target style so we're tuning the voice
# on the text it will really say, not on formal test sentences.
REPLIES = [
    # (id, register, text)
    ("r1_warm_validate", "warm validation",
     "Oh, that does sound heavy. I'm right here with you, Asha."),
    ("r2_gentle_offer", "gentle offer",
     "Would you like to sit by the window for a bit, or shall we put on some music?"),
    ("r3_memory_box", "memory box offer",
     "I have a little clip of Priya's voice saved. Would you like to hear it?"),
    ("r4_calm_ground", "calm grounding",
     "It's a quiet afternoon. You're safe, and I'm not going anywhere."),
    ("r5_serious_present", "serious, staying present (orange/red register)",
     "I hear how scared you are. I'm right here. Let's call Rohan together, alright?"),
    ("r6_short_ack", "short acknowledgement (filler register)",
     "Mm. I heard you."),
]


def pcm_to_wav(pcm: bytes, sample_rate: int) -> bytes:
    """Wrap raw PCM s16le mono in a WAV header."""
    import io
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)  # s16le
        w.setframerate(sample_rate)
        w.writeframes(pcm)
    return buf.getvalue()


async def render_one(model_id: str, text: str) -> tuple[bytes, float]:
    """Render text through a specific model, bypassing the module default."""
    saved = tts.CARTESIA_MODEL_ID
    tts.CARTESIA_MODEL_ID = model_id
    try:
        url = (f"{tts.CARTESIA_WS_URL}?api_key={tts.CARTESIA_API_KEY}"
               f"&cartesia_version={tts.CARTESIA_VERSION}")
        import websockets
        t0 = time.time()
        chunks: list[bytes] = []
        async with websockets.connect(url, open_timeout=20) as ws:
            request = {
                "model_id": model_id,
                "transcript": text,
                "voice": {"id": tts.CARTESIA_VOICE_ID},
                "output_format": {
                    "container": "raw",
                    "encoding": "pcm_s16le",
                    "sample_rate": SAMPLE_RATE,
                },
                "language": tts.CARTESIA_LANGUAGE,
                "context_id": str(uuid.uuid4()),
            }
            await ws.send(json.dumps(request))
            async for raw in ws:
                msg = json.loads(raw)
                if msg.get("type") == "chunk" and msg.get("data"):
                    chunks.append(base64.b64decode(msg["data"]))
                elif msg.get("type") == "done":
                    break
                elif msg.get("type") == "error":
                    raise RuntimeError(f"Cartesia error ({model_id}): {msg}")
        elapsed = time.time() - t0
        return b"".join(chunks), elapsed
    finally:
        tts.CARTESIA_MODEL_ID = saved


async def main() -> None:
    if not tts.is_configured():
        raise SystemExit("CARTESIA_API_KEY / CARTESIA_VOICE_ID not set")

    out_dir = Path(__file__).resolve().parents[1] / "voice" / "assets" / "ab_test"
    out_dir.mkdir(parents=True, exist_ok=True)

    # Clear any stale files from a previous run so listeners don't mix sets.
    for old in out_dir.glob("*.wav"):
        old.unlink()

    key: dict[str, dict] = {}
    rng = random.Random()  # no fixed seed — genuinely random label assignment

    print(f"Rendering {len(REPLIES)} samples x {len(MODELS)} models")
    print(f"  models : {', '.join(MODELS)}")
    print(f"  voice  : {tts.CARTESIA_VOICE_ID[:8]}...")
    print(f"  output : {out_dir}\n")

    for idx, (rid, register, text) in enumerate(REPLIES, start=1):
        labels = ["A", "B", "C"][: len(MODELS)]
        rng.shuffle(labels)
        key[str(idx)] = {
            "reply_id": rid,
            "register": register,
            "text": text,
            "label_to_model": {},
        }
        print(f"[{idx}/{len(REPLIES)}] {rid} ({register})")
        for model, label in zip(MODELS, labels):
            try:
                pcm, elapsed = await render_one(model, text)
            except Exception as e:
                print(f"    {label} <- {model}: FAILED {type(e).__name__}: {str(e)[:90]}")
                key[str(idx)]["label_to_model"][label] = {"model": model, "status": f"FAILED: {e}"}
                continue
            dur = len(pcm) / 2 / SAMPLE_RATE
            wav = pcm_to_wav(pcm, SAMPLE_RATE)
            fname = f"{idx:02d}_{label}.wav"
            (out_dir / fname).write_bytes(wav)
            key[str(idx)]["label_to_model"][label] = {
                "model": model,
                "status": "ok",
                "audio_seconds": round(dur, 2),
                "wall_seconds": round(elapsed, 2),
                "file": fname,
            }
            print(f"    {label} <- {model}: {dur:.2f}s audio in {elapsed:.2f}s")

    (out_dir / "AB_KEY.json").write_text(json.dumps(key, indent=2))

    # Human-readable key
    lines = [
        "BLIND A/B KEY — do not open until all listeners have rated.",
        "=" * 60,
        "",
    ]
    for idx in sorted(key, key=int):
        entry = key[idx]
        lines.append(f"Sample {idx}: {entry['reply_id']} ({entry['register']})")
        lines.append(f"  text: {entry['text']}")
        for label in sorted(entry["label_to_model"]):
            info = entry["label_to_model"][label]
            lines.append(f"  {label} = {info['model']}  [{info['status']}]")
        lines.append("")
    (out_dir / "AB_KEY.txt").write_text("\n".join(lines))

    print(f"\nWrote {len(list(out_dir.glob('*.wav')))} WAV files + key to {out_dir}")
    print("Open AB_KEY.txt only AFTER listeners have rated.")


if __name__ == "__main__":
    asyncio.run(main())
