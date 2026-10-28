"""
test_voice_turn_taking.py

Regression guard for the voice orb's turn-taking behaviour.

Three bugs are covered:

1. Sahaara's own TTS was picked up by the mic and sent back to Deepgram,
   which transcribed it as a new user utterance and triggered an immediate
   second response. The fix is a mic gate: the ASR sender drops audio chunks
   while a turn is being handled.
2. The pipeline flipped straight from "speaking" to "listening" with no gap,
   so the person never got a beat to answer. The fix is a deliberate pause
   before the listening state is yielded.
3. Gating the mic can leave Deepgram with no audio for >10s during a slow
   turn, which closes the socket. The fix is a protocol KeepAlive.

Run directly:
    cd backend && .venv/bin/python tests/test_voice_turn_taking.py
"""

import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import voice.asr as asr
import voice.voice_pipeline as vp


# ---------------------------------------------------------------------------
# ASR gate
# ---------------------------------------------------------------------------

class FakeWebSocket:
    """Minimal Deepgram socket: records sends, ends when CloseStream arrives."""

    def __init__(self):
        self.sent = []
        self.done = asyncio.Event()

    async def send(self, payload):
        self.sent.append(payload)
        if isinstance(payload, str) and json.loads(payload).get("type") == "CloseStream":
            self.done.set()

    def __aiter__(self):
        return self._iterate()

    async def _iterate(self):
        await self.done.wait()
        return
        yield  # pragma: no cover — makes this an async generator


class FakeConnect:
    def __init__(self, ws):
        self.ws = ws

    async def __aenter__(self):
        return self.ws

    async def __aexit__(self, *exc):
        return False


async def _run_stream(gate, chunks):
    ws = FakeWebSocket()
    original_connect = asr.websockets.connect
    original_key = asr.DEEPGRAM_API_KEY
    asr.websockets.connect = lambda url, **kwargs: FakeConnect(ws)
    asr.DEEPGRAM_API_KEY = "test-key"

    try:
        async def audio_in():
            if hasattr(chunks, "__aiter__"):
                async for chunk in chunks:
                    yield chunk
            else:
                for chunk in chunks:
                    yield chunk

        async for _ in asr.stream_transcripts(audio_in(), gate=gate):
            pass
    finally:
        asr.websockets.connect = original_connect
        asr.DEEPGRAM_API_KEY = original_key

    return ws.sent


async def _collect_sent(gate, chunks):
    return [p for p in await _run_stream(gate, chunks) if isinstance(p, bytes)]


def test_asr_gate_drops_chunks_while_closed():
    gate = {"enabled": False}
    sent = asyncio.run(_collect_sent(gate, [b"echo-one", b"echo-two"]))
    assert sent == [], f"gated ASR must not send mic audio, got {sent!r}"
    print("PASS: ASR drops mic chunks while the turn gate is closed")


def test_asr_gate_passes_audio_when_open():
    gate = {"enabled": True}
    sent = asyncio.run(_collect_sent(gate, [b"user-one", b"user-two"]))
    assert sent == [b"user-one", b"user-two"], sent
    print("PASS: ASR forwards mic chunks while the turn gate is open")


def test_asr_gate_switches_mid_stream():
    gate = {"enabled": True}

    async def chunks():
        gate["enabled"] = True
        yield b"before"
        gate["enabled"] = False
        yield b"during"
        gate["enabled"] = True
        yield b"after"

    sent = asyncio.run(_collect_sent(gate, chunks()))
    assert sent == [b"before", b"after"], sent
    print("PASS: ASR gate switches on and off mid-stream without buffering")


def test_asr_keepalive_when_idle():
    async def idle_stream():
        await asyncio.sleep(0.3)
        return
        yield  # pragma: no cover — makes this an async generator

    async def run():
        original_interval = asr.KEEPALIVE_INTERVAL_SECONDS
        original_threshold = asr.KEEPALIVE_IDLE_THRESHOLD_SECONDS
        asr.KEEPALIVE_INTERVAL_SECONDS = 0.05
        asr.KEEPALIVE_IDLE_THRESHOLD_SECONDS = 0.0
        try:
            return await _run_stream({"enabled": True}, idle_stream())
        finally:
            asr.KEEPALIVE_INTERVAL_SECONDS = original_interval
            asr.KEEPALIVE_IDLE_THRESHOLD_SECONDS = original_threshold

    sent = asyncio.run(run())
    keepalives = [
        json.loads(payload)
        for payload in sent
        if isinstance(payload, str) and json.loads(payload).get("type") == "KeepAlive"
    ]
    assert keepalives, f"expected KeepAlive frames while idle, got {sent!r}"
    print("PASS: ASR sends KeepAlive control frames while the mic is idle")


# ---------------------------------------------------------------------------
# Pipeline turn pause + gate state
# ---------------------------------------------------------------------------

def test_run_cascaded_pauses_before_listening():
    observed = {}

    async def fake_stream(audio_in, gate=None):
        observed["gate"] = gate
        observed["gate_at_first_turn"] = gate["enabled"]
        yield "what is my name", True
        # Resumes only after the turn finished and listening was yielded.
        observed["gate_after_turn"] = gate["enabled"]

    async def fake_llm(text):
        # _llm_reply returns (reply_text, radio_stream_url)
        return "Your name is Asha.", None

    async def fake_ack():
        return b"ack"

    async def fake_tts(text):
        observed["gate_during_tts"] = observed["gate"]["enabled"]
        yield b"speech"

    originals = {
        "stream_transcripts": vp.asr.stream_transcripts,
        "_llm_reply": vp._llm_reply,
        "get_ack_audio": vp.fillers.get_ack_audio,
        "stream_speech": vp.tts.stream_speech,
        "write_timing_log": vp._write_timing_log,
        "get_event_log": vp.get_event_log,
        "turn_pause": vp.TURN_PAUSE_SECONDS,
    }

    vp.asr.stream_transcripts = fake_stream
    vp._llm_reply = fake_llm
    vp.fillers.get_ack_audio = fake_ack
    vp.tts.stream_speech = fake_tts
    vp._write_timing_log = lambda *a, **k: None
    vp.get_event_log = lambda: None
    vp.TURN_PAUSE_SECONDS = 0.15

    async def drive():
        last_audio_at = None
        listening_at = None
        states = []
        async for event in vp.run_cascaded(None):
            if event.kind == "audio" and event.audio:
                last_audio_at = time.time()
            elif event.kind == "state":
                states.append(event.state)
                if event.state == "listening":
                    listening_at = time.time()
        return states, last_audio_at, listening_at

    try:
        states, last_audio_at, listening_at = asyncio.run(drive())
    finally:
        vp.asr.stream_transcripts = originals["stream_transcripts"]
        vp._llm_reply = originals["_llm_reply"]
        vp.fillers.get_ack_audio = originals["get_ack_audio"]
        vp.tts.stream_speech = originals["stream_speech"]
        vp._write_timing_log = originals["write_timing_log"]
        vp.get_event_log = originals["get_event_log"]
        vp.TURN_PAUSE_SECONDS = originals["turn_pause"]

    assert observed["gate_at_first_turn"] is True, observed
    assert observed["gate_during_tts"] is False, observed
    assert observed["gate_after_turn"] is True, observed
    assert states[-1] == "listening", states
    assert last_audio_at is not None and listening_at is not None
    assert listening_at - last_audio_at >= 0.10, (
        f"expected a turn pause before listening, got "
        f"{listening_at - last_audio_at:.3f}s"
    )
    print(
        "PASS: turn closes the mic gate, pauses "
        f"{listening_at - last_audio_at:.2f}s, then reopens it"
    )


if __name__ == "__main__":
    print("Testing voice turn-taking...")
    print()

    test_asr_gate_drops_chunks_while_closed()
    test_asr_gate_passes_audio_when_open()
    test_asr_gate_switches_mid_stream()
    test_asr_keepalive_when_idle()
    test_run_cascaded_pauses_before_listening()

    print("\nAll voice turn-taking tests passed!")
