"""Component 2.

Most tests need no keys. The ones marked `live` hit the real API and are
skipped unless a key is in .env. Run them with `make test-live`.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from voice_agent.stt import DeepgramSTT, ElevenLabsSTT, SpeechToText, Transcript, build

ELEVENLABS_KEY = os.getenv("ELEVENLABS_API_KEY", "")
DEEPGRAM_KEY = os.getenv("DEEPGRAM_API_KEY", "")


# --- no keys needed ---


def test_build_returns_elevenlabs() -> None:
    assert isinstance(build("elevenlabs", "fake-key"), ElevenLabsSTT)


def test_build_returns_deepgram() -> None:
    assert isinstance(build("deepgram", "fake-key"), DeepgramSTT)


def test_build_rejects_unknown_provider() -> None:
    with pytest.raises(ValueError, match="unknown STT provider"):
        build("whisper", "fake-key")


def test_both_providers_satisfy_the_interface() -> None:
    """If this breaks, swapping providers by config stops working."""
    assert isinstance(build("elevenlabs", "fake-key"), SpeechToText)
    assert isinstance(build("deepgram", "fake-key"), SpeechToText)


def test_deepgram_keyterms_default_to_empty() -> None:
    assert DeepgramSTT("fake-key").keyterms == []


def test_both_providers_accept_the_same_keyterms() -> None:
    """Giving keyterms to only one provider would make the accuracy
    comparison meaningless, so build() must pass them to whichever is chosen."""
    terms = ["\u062c\u06cc \u06c1\u0627\u06ba", "\u0646\u06c1\u06cc\u06ba"]
    assert build("elevenlabs", "fake-key", terms).keyterms == terms
    assert build("deepgram", "fake-key", terms).keyterms == terms


def test_word_confidence_and_language_confidence_are_separate_fields() -> None:
    """Regression. ElevenLabs' language-detection probability used to be stored
    in `confidence`, where Deepgram puts word confidence. Two different
    measurements in one field makes the provider comparison meaningless."""
    fields = Transcript.__dataclass_fields__
    assert "confidence" in fields
    assert "language_confidence" in fields


def test_transcript_is_immutable() -> None:
    """Callers must not be able to edit a transcript in place."""
    transcript = Transcript(text="جی ہاں", provider="test")
    with pytest.raises(FrozenInstanceError):
        transcript.text = "نہیں"  # type: ignore[misc]


# --- hits the real API ---


@pytest.fixture
def phone_audio(tmp_path: Path) -> Path:
    """Two seconds of 8 kHz audio. It contains no speech, so the transcript
    will be empty. That is fine: this proves auth, request shape and response
    parsing, which is the part that was uncertain."""
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not installed")

    path = tmp_path / "phone.wav"
    # fmt: off
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi",
            "-i", "sine=frequency=440:duration=2:sample_rate=8000",
            "-ac", "1",
            str(path),
        ],
        check=True,
    )
    # fmt: on
    return path


@pytest.mark.live
@pytest.mark.skipif(not ELEVENLABS_KEY, reason="no ELEVENLABS_API_KEY")
def test_elevenlabs_returns_a_transcript(phone_audio: Path) -> None:
    result = ElevenLabsSTT(ELEVENLABS_KEY).transcribe(phone_audio, language="ur")

    assert isinstance(result, Transcript)
    assert result.provider == "elevenlabs-scribe"
    assert isinstance(result.text, str)


@pytest.mark.live
@pytest.mark.skipif(not DEEPGRAM_KEY, reason="no DEEPGRAM_API_KEY")
def test_deepgram_returns_a_transcript(phone_audio: Path) -> None:
    from voice_agent.lang.ur.voice import KEYTERMS

    result = DeepgramSTT(DEEPGRAM_KEY, keyterms=KEYTERMS).transcribe(phone_audio, language="ur")

    assert isinstance(result, Transcript)
    assert result.provider == "deepgram-nova-3"
    assert isinstance(result.text, str)
