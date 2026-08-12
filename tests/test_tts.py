"""Components 5 and 11.

The cache tests use a stub voice, so they need no key and no network. The
`live` tests call Azure and are skipped without AZURE_SPEECH_KEY.
"""

from __future__ import annotations

import os
import wave
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from voice_agent.config import settings
from voice_agent.lang.ur.voice import TTS_VOICE
from voice_agent.tts import AzureTTS, CachedTTS, Speech, TextToSpeech, build

AZURE_KEY = os.getenv("AZURE_SPEECH_KEY", "")
AZURE_REGION = os.getenv("AZURE_SPEECH_REGION", "centralindia")


class StubTTS:
    """Counts calls, so a cache hit is provable rather than assumed."""

    name = "stub"
    voice = "test-voice"

    def __init__(self) -> None:
        self.calls = 0

    def synthesize(self, text: str, dst: Path) -> Speech:
        self.calls += 1
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(b"RIFF____WAVE")
        return Speech(path=dst, voice=self.voice, cached=False)


# --- no keys needed ---


def test_stub_satisfies_the_interface() -> None:
    assert isinstance(StubTTS(), TextToSpeech)


def test_second_call_is_a_disk_read(tmp_path: Path) -> None:
    """The whole point of component 11."""
    stub = StubTTS()
    cache = CachedTTS(stub, tmp_path / "cache")

    first = cache.synthesize("جی ہاں")
    second = cache.synthesize("جی ہاں")

    assert stub.calls == 1
    assert first.cached is False
    assert second.cached is True
    assert second.path == first.path


def test_different_text_is_a_different_file(tmp_path: Path) -> None:
    stub = StubTTS()
    cache = CachedTTS(stub, tmp_path / "cache")

    a = cache.synthesize("جی ہاں")
    b = cache.synthesize("نہیں")

    assert a.path != b.path
    assert stub.calls == 2


def test_changing_voice_does_not_replay_the_old_one(tmp_path: Path) -> None:
    """Switching to the male voice must not serve the female recording."""
    female = StubTTS()
    cache_dir = tmp_path / "cache"
    first = CachedTTS(female, cache_dir).synthesize("جی ہاں")

    male = StubTTS()
    male.voice = "ur-PK-AsadNeural"
    second = CachedTTS(male, cache_dir).synthesize("جی ہاں")

    assert first.path != second.path
    assert male.calls == 1


def test_cache_directory_is_created(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "deeper"
    CachedTTS(StubTTS(), target)
    assert target.is_dir()


def test_speech_is_immutable() -> None:
    speech = Speech(path=Path("a.wav"), voice="v", cached=False)
    with pytest.raises(FrozenInstanceError):
        speech.cached = True  # type: ignore[misc]


# --- hits the real API ---


@pytest.mark.live
@pytest.mark.skipif(not AZURE_KEY, reason="no AZURE_SPEECH_KEY")
def test_azure_writes_a_real_wav(tmp_path: Path) -> None:
    dst = tmp_path / "line.wav"

    result = AzureTTS(AZURE_KEY, AZURE_REGION, TTS_VOICE).synthesize("جی ہاں ٹھیک ہے", dst)

    assert result.path.exists()
    with wave.open(str(dst)) as handle:
        assert handle.getnchannels() == 1
        assert handle.getframerate() == settings.tts.sample_rate
        assert handle.getnframes() > 0


@pytest.mark.live
@pytest.mark.skipif(not AZURE_KEY, reason="no AZURE_SPEECH_KEY")
def test_bad_voice_raises_rather_than_writing_an_empty_file(tmp_path: Path) -> None:
    """Azure reports this in the result instead of raising, so without the
    check in synthesize() it leaves a 0-byte WAV and looks like success."""
    voice = AzureTTS(AZURE_KEY, AZURE_REGION, voice="ur-PK-NotARealVoice")

    with pytest.raises(RuntimeError, match="did not synthesize"):
        voice.synthesize("جی ہاں", tmp_path / "bad.wav")


@pytest.mark.live
@pytest.mark.skipif(not AZURE_KEY, reason="no AZURE_SPEECH_KEY")
def test_build_wraps_in_cache_when_given_a_directory(tmp_path: Path) -> None:
    voice = build(AZURE_KEY, AZURE_REGION, TTS_VOICE, cache_dir=tmp_path / "cache")

    first = voice.synthesize("شکریہ")
    second = voice.synthesize("شکریہ")

    assert first.cached is False
    assert second.cached is True


def test_empty_text_raises_without_calling_the_api(tmp_path: Path) -> None:
    voice = AzureTTS.__new__(AzureTTS)  # no key needed, the guard runs first
    with pytest.raises(ValueError, match="nothing to synthesize"):
        AzureTTS.synthesize(voice, "   ", tmp_path / "x.wav")
