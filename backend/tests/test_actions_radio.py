"""
Tests for radio, local music, and stopping playback.

Owner decisions under test (2026-10-28):
- Music source is radio PLUS local MP3s
- An empty music folder gets an honest "I couldn't find that song", never a
  claim that something is playing
- "stop" only claims to have stopped something when something was playing
"""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_TMP_DB = Path(tempfile.mkdtemp()) / "actions_radio.db"
os.environ["SAHAARA_DB_PATH"] = str(_TMP_DB)

import database  # noqa: E402
database.init_db()

import actions  # noqa: E402
from actions import (  # noqa: E402
    DEFAULT_RADIO_STATIONS,
    detect_action,
    mark_media_playing,
    play_radio,
    play_song,
    stop_media,
)

# Keep this file offline: the real lookup calls Apple. The streaming path has
# its own suite (tests/test_song_streaming.py) with an injected lookup.
actions._itunes_lookup = lambda query: None

passed = 0
failed = 0


def check(name: str, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  PASS: {name}")
    else:
        failed += 1
        print(f"  FAIL: {name} {detail}")


# ============================================================
# Test 1: Detection
# ============================================================
print("=== Test 1: Detection ===")

detect_tests = [
    ("play the radio", "play_radio"),
    ("turn on the radio", "play_radio"),
    ("play some music", "play_song"),
    ("can you play some music", "play_song"),
    ("play lag ja gale", "play_song"),
    ("stop the music", "stop_media"),
    ("stop the radio", "stop_media"),
    ("pause the music", "stop_media"),
    ("turn off the radio", "stop_media"),
]

for text, expected in detect_tests:
    result = detect_action(text)
    got = result["action_type"] if result else None
    check(f"detect '{text}' -> {expected}", got == expected, f"got {got}")

# ============================================================
# Test 2: Radio stations
# ============================================================
print("\n=== Test 2: Radio ===")

# Detection must not write to the profile.
check(
    "detection does not seed stations",
    database.get_radio_stations("asha_demo_001") == [],
)

# No network in tests: a stream probe is simulated here, and the honesty rules
# are exercised explicitly further down.
import actions as _actions

_real_alive = _actions._stream_alive
_actions._stream_alive = lambda url, timeout=4.0: True

try:
    result = play_radio(profile_id="asha_demo_001")
    check("play_radio succeeds", result.success is True)
    check("names the default station", DEFAULT_RADIO_STATIONS[0]["name"] in result.result_text, result.result_text)
    check("has a stream url", bool(result.detail.get("stream_url")))
    check(
        "seeded the caregiver-editable station list",
        len(database.get_radio_stations("asha_demo_001")) == len(DEFAULT_RADIO_STATIONS),
    )

    result = play_radio("bbc", profile_id="asha_demo_001")
    check("matches a station by short name", result.success is True)
    check("picked BBC", "BBC" in result.result_text, result.result_text)

    # A named station in the utterance is detected as radio, not as a song.
    result = detect_action("play hindi gold radio")
    check(
        "named station detected as radio",
        result is not None and result["action_type"] == "play_radio",
        f"got {result}",
    )
finally:
    _actions._stream_alive = _real_alive


# ============================================================
# Test 2b: A dead stream is never announced as playing
# ============================================================
print("\n=== Test 2b: Dead streams are reported honestly ===")

_actions._stream_alive = lambda url, timeout=4.0: False
try:
    result = play_radio("bbc", profile_id="asha_demo_001")
    check("dead named station -> success=False", result.success is False, result.result_text)
    check("says it isn't answering", "isn't answering" in result.result_text, result.result_text)
    check("claims nothing about playing", "playing" not in result.result_text.lower(), result.result_text)
    check("no stream url on failure", "stream_url" not in result.detail, result.detail)

    result = play_radio(profile_id="asha_demo_001")
    check("all stations dead -> success=False", result.success is False, result.result_text)
    check("says the radio isn't answering", "isn't answering" in result.result_text, result.result_text)

    # Unknown station name says so instead of silently playing something else.
    result = play_radio("radio atlantis", profile_id="asha_demo_001")
    check("unknown station -> success=False", result.success is False, result.result_text)
    check("says it does not have it", "don't have that station" in result.result_text, result.result_text)
finally:
    _actions._stream_alive = _real_alive

# With the first station dead the next live one is chosen.
_actions._stream_alive = lambda url, timeout=4.0: url == DEFAULT_RADIO_STATIONS[1]["url"]
try:
    result = play_radio(profile_id="asha_demo_001")
    check("skips a dead station", result.success is True, result.result_text)
    check("plays the live one", DEFAULT_RADIO_STATIONS[1]["name"] in result.result_text, result.result_text)
finally:
    _actions._stream_alive = _real_alive

# ============================================================
# Test 2c: The stream probe itself
# ============================================================
print("\n=== Test 2c: Stream probe ===")

from unittest.mock import patch  # noqa: E402


class _FakeAudio:
    status = 200
    headers = {"Content-Type": "audio/mpeg"}

    def read(self, n=None):
        return b"\xff" * 1024

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class _FakeHtml:
    status = 200
    headers = {"Content-Type": "text/html; charset=utf-8"}

    def read(self, n=None):
        return b"<html>" * 200

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class _FakeShort:
    status = 200
    headers = {"Content-Type": "audio/mpeg"}

    def read(self, n=None):
        return b""

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


with patch("actions.urlopen", return_value=_FakeAudio()):
    check("audio stream probes alive", _actions._stream_alive("https://example.test/a") is True)

with patch("actions.urlopen", return_value=_FakeHtml()):
    check("html page is not a stream", _actions._stream_alive("https://example.test/b") is False)

with patch("actions.urlopen", return_value=_FakeShort()):
    check("empty stream is not a stream", _actions._stream_alive("https://example.test/c") is False)

with patch("actions.urlopen", side_effect=OSError("no route")):
    check("network error probes dead", _actions._stream_alive("https://example.test/d") is False)

print("\n=== Test 3: Local music ===")

# The shipped repo has an empty music folder. A named song is now looked up
# online first; this file stubs the lookup out (top of file), so the honest
# failure is "couldn't find that song", not "no music is loaded".
result = play_song("lag ja gale", profile_id="asha_demo_001")
check("empty folder -> success=False", result.success is False)
check("says it could not find it", "couldn't find" in result.result_text, result.result_text)
check("offers the radio", "radio" in result.result_text.lower(), result.result_text)
check("no audio url on failure", "audio_url" not in result.detail)

# With a real file present.
music_dir = Path(tempfile.mkdtemp())
(music_dir / "lag_ja_gale.mp3").write_bytes(b"ID3fake")
original_dir = actions.MUSIC_DIR
actions.MUSIC_DIR = music_dir

try:
    result = play_song("lag ja gale", profile_id="asha_demo_001")
    check("finds the file", result.success is True, result.result_text)
    check("says what is playing", "Lag Ja Gale" in result.result_text, result.result_text)
    check("returns a /media url", result.detail.get("audio_url", "").startswith("/media/"))
    check("url points at the real file", "lag_ja_gale.mp3" in result.detail.get("audio_url", ""))

    result = play_song("something that does not exist", profile_id="asha_demo_001")
    check("unknown song -> success=False", result.success is False)
    check("says it could not find it", "couldn't find" in result.result_text, result.result_text)
    check("lists what it does have", "Lag Ja Gale" in result.result_text, result.result_text)

    result = play_song("", profile_id="asha_demo_001")
    check("'play some music' plays the first track", result.success is True, result.result_text)
finally:
    actions.MUSIC_DIR = original_dir

# ============================================================
# Test 4: Stop is honest
# ============================================================
print("\n=== Test 4: Stopping ===")

result = stop_media(profile_id="asha_demo_001")
check("nothing playing -> success=False", result.success is False)
check("says nothing is playing", "nothing is playing" in result.result_text.lower(), result.result_text)

mark_media_playing("asha_demo_001", "All India Radio Pune")
result = stop_media(profile_id="asha_demo_001")
check("stop after playback -> success=True", result.success is True)
check("says it stopped", "stopped" in result.result_text.lower(), result.result_text)

result = stop_media(profile_id="asha_demo_001")
check("stopping twice does not re-claim", result.success is False)

print(f"\n  RESULT: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
