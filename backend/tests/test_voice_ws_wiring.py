"""
test_voice_ws_wiring.py

Regression guard for the voice orb WebSocket (/ws/voice).

The orb used to die with:
    TypeError: a coroutine was expected, got <async_generator object stream ...>
because main.py passed voice_pipeline.stream() (an async generator) straight
to asyncio.create_task(). These checks fail loudly if that wiring regresses.

Run directly:
    cd backend && .venv/bin/python tests/test_voice_ws_wiring.py
"""

import asyncio
import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import main
from voice.voice_pipeline import VoiceEvent


class FakeWebSocket:
    """Captures frames instead of sending them over a socket."""

    def __init__(self):
        self.json_frames = []
        self.binary_frames = []

    async def send_json(self, payload):
        self.json_frames.append(payload)

    async def send_bytes(self, payload):
        self.binary_frames.append(payload)


def test_stream_is_async_generator():
    """stream() yields events; it is not a coroutine."""
    assert inspect.isasyncgenfunction(main.voice_pipeline.stream), (
        "voice_pipeline.stream must remain an async generator; if it becomes a "
        "coroutine, update _pump_voice_pipeline accordingly"
    )
    print("PASS: voice_pipeline.stream is an async generator")


def test_handler_uses_pump_coroutine():
    """The handler must create_task() a coroutine, never the generator."""
    import re

    src = inspect.getsource(main.voice_ws)
    assert re.search(r"create_task\(\s*_pump_voice_pipeline\(", src), (
        "/ws/voice must start the pump coroutine via create_task()"
    )
    assert not re.search(r"create_task\(\s*voice_pipeline\.stream", src), (
        "create_task() cannot take the async generator returned by stream()"
    )
    print("PASS: /ws/voice wraps stream() in the pump coroutine")


def test_pump_forwards_state_and_audio():
    """State frames become JSON; audio frames become binary."""
    async def fake_stream(audio_in):
        yield VoiceEvent(kind="state", state="listening")
        yield VoiceEvent(kind="audio", audio=b"\x01\x02\x03")
        yield VoiceEvent(kind="state", state="thinking")

    original = main.voice_pipeline.stream
    main.voice_pipeline.stream = fake_stream
    try:
        ws = FakeWebSocket()
        asyncio.run(main._pump_voice_pipeline(ws, None))
    finally:
        main.voice_pipeline.stream = original

    assert ws.json_frames == [
        {"type": "state", "state": "listening"},
        {"type": "state", "state": "thinking"},
    ], ws.json_frames
    assert ws.binary_frames == [b"\x01\x02\x03"], ws.binary_frames
    print("PASS: pump forwards state + audio frames")


if __name__ == "__main__":
    print("Testing voice WebSocket wiring...")
    print()

    test_stream_is_async_generator()
    test_handler_uses_pump_coroutine()
    test_pump_forwards_state_and_audio()

    print("\nAll voice WebSocket wiring tests passed!")
