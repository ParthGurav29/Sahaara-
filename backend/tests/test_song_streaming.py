"""
Tests for internet song streaming.

Why this file exists (2026-10-28): "asked to play a song, it didn't, the reason
was no song was loaded". The local music folder ships empty, so every named
song failed even though the person clearly expected music.

Owner rules that stay intact:
  - Never claim an action that did not happen. A lookup that fails, a network
    error, or an empty result all say "I couldn't find that song".
  - Apple only hands out a short preview. Sahaara says "a short clip of ...",
    so nobody is told the whole song is playing.
  - A local file always wins when one matches.

No test here touches the network: the lookup is either injected or `urlopen`
is patched.
"""

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_TMP_DB = Path(tempfile.mkdtemp()) / "song_streaming.db"
os.environ["SAHAARA_DB_PATH"] = str(_TMP_DB)

import database  # noqa: E402
database.init_db()

import actions  # noqa: E402
from actions import play_song  # noqa: E402

PROFILE = "asha_demo_001"

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


class _FakeResponse:
    def __init__(self, payload: bytes):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self._payload


STREAM = {
    "title": "Lag Ja Gale Se Phir",
    "artist": "Lata Mangeshkar",
    "url": "https://example.test/preview.m4a",
    "source": "itunes",
}

# ============================================================
# Test 1: a named song with an empty folder streams a preview
# ============================================================
print("=== Test 1: Named song -> streamed clip ===")

empty_dir = Path(tempfile.mkdtemp())
original_dir = actions.MUSIC_DIR
actions.MUSIC_DIR = empty_dir
try:
    asked = []

    def fake_lookup(query):
        asked.append(query)
        return STREAM

    result = play_song("lag ja gale", profile_id=PROFILE, lookup=fake_lookup)
    check("success", result.success is True, result.result_text)
    check("the query reached the lookup", asked == ["lag ja gale"], asked)
    check("says it is a clip, not the whole song", "short clip" in result.result_text.lower(),
          result.result_text)
    check("names the track", "Lag Ja Gale" in result.result_text, result.result_text)
    check("names the singer", "Lata Mangeshkar" in result.result_text, result.result_text)
    check("returns the preview url", result.detail.get("audio_url") == STREAM["url"],
          result.detail)
    check("flags it as a preview", result.detail.get("preview") is True, result.detail)
    check("never claims a full song", "playing lag" not in result.result_text.lower(),
          result.result_text)

    # ---- nothing found -----------------------------------------------------
    result = play_song("some imaginary tune", profile_id=PROFILE, lookup=lambda _q: None)
    check("unknown song -> success=False", result.success is False, result.result_text)
    check("says it could not find it", "couldn't find" in result.result_text.lower(),
          result.result_text)
    check("offers the radio", "radio" in result.result_text.lower(), result.result_text)
    check("no audio url when nothing was found", "audio_url" not in result.detail, result.detail)

    # ---- the lookup itself blowing up is still honest ----------------------
    def exploding_lookup(_query):
        raise RuntimeError("network down")

    result = play_song("another tune", profile_id=PROFILE, lookup=exploding_lookup)
    check("a crashed lookup is reported as not found", result.success is False,
          result.result_text)
    check("and no url is invented", "audio_url" not in result.detail, result.detail)

    # ---- local files always win -------------------------------------------
    local_dir = Path(tempfile.mkdtemp())
    (local_dir / "lag_ja_gale.mp3").write_bytes(b"ID3fake")
    actions.MUSIC_DIR = local_dir
    calls = []
    result = play_song("lag ja gale", profile_id=PROFILE,
                       lookup=lambda q: calls.append(q) or STREAM)
    check("local file wins", result.success is True, result.result_text)
    check("plays the local url", result.detail.get("audio_url", "").startswith("/media/"),
          result.detail)
    check("the internet was not asked", calls == [], calls)
finally:
    actions.MUSIC_DIR = original_dir

# ============================================================
# Test 2: generic "play some music" with no folder falls back to radio
# ============================================================
print("\n=== Test 2: Generic music -> radio ===")


class _RadioResult:
    def __init__(self, success, text, detail):
        self.success = success
        self.action_type = "play_radio"
        self.result_text = text
        self.detail = detail


original_radio = actions.play_radio
try:
    actions.play_radio = lambda name="", profile_id=None: _RadioResult(
        True, "Playing BBC World Service.", {"station_name": "BBC World Service",
                                             "stream_url": "https://example.test/radio"}
    )
    result = play_song("", profile_id=PROFILE, lookup=lambda _q: None)
    check("generic music starts the radio", result.success is True, result.result_text)
    check("the action type is honest about the radio", result.action_type == "play_radio",
          result.action_type)
    check("returns the stream url", result.detail.get("stream_url") == "https://example.test/radio",
          result.detail)

    actions.play_radio = lambda name="", profile_id=None: _RadioResult(
        False, "The radio isn't answering right now.", {}
    )
    result = play_song("", profile_id=PROFILE, lookup=lambda _q: None)
    check("dead radio -> honest failure", result.success is False, result.result_text)
    check("mentions the empty music folder", "don't have any music" in result.result_text.lower(),
          result.result_text)
finally:
    actions.play_radio = original_radio

# ============================================================
# Test 3: the iTunes parser
# ============================================================
print("\n=== Test 3: iTunes lookup parsing ===")

original_urlopen = actions.urlopen
try:
    payload = json.dumps({
        "resultCount": 2,
        "results": [
            {"trackName": "No Preview", "artistName": "Nobody"},
            {"trackName": "Lag Ja Gale Se Phir", "artistName": "Lata Mangeshkar",
             "previewUrl": "https://audio.example.test/lag.m4a"},
        ],
    }).encode()
    actions.urlopen = lambda request, timeout=0: _FakeResponse(payload)
    actions._SONG_CACHE.clear()

    found = actions._itunes_lookup("lag ja gale")
    check("skips results without a preview", found and found["url"] == "https://audio.example.test/lag.m4a",
          found)
    check("keeps the track title", found and found["title"] == "Lag Ja Gale Se Phir", found)
    check("keeps the artist", found and found["artist"] == "Lata Mangeshkar", found)
    check("marks the source", found and found["source"] == "itunes", found)

    calls = []
    actions.urlopen = lambda request, timeout=0: calls.append(request) or _FakeResponse(payload)
    again = actions._itunes_lookup("lag ja gale")
    check("second lookup is served from cache", again == found, again)
    check("and does not hit the network twice", calls == [], calls)

    actions._SONG_CACHE.clear()
    actions.urlopen = lambda request, timeout=0: _FakeResponse(b"not json")
    check("malformed payload -> None", actions._itunes_lookup("broken") is None)

    def boom(request, timeout=0):
        raise OSError("no route")

    actions.urlopen = boom
    check("network error -> None", actions._itunes_lookup("offline") is None)
    check("an empty query is never looked up", actions._itunes_lookup("   ") is None)
finally:
    actions.urlopen = original_urlopen
    actions._SONG_CACHE.clear()

print(f"\n  RESULT: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
