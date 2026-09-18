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

from voice_agent.config import settings
from voice_agent.stt import (
    DeepgramSTT,
    ElevenLabsSTT,
    SpeechToText,
    StreamingSpeechToText,
    Transcript,
    build,
)

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


# --- streaming, for the live call ---


def test_build_gives_elevenlabs_a_streaming_mode() -> None:
    assert isinstance(build("elevenlabs", "fake-key"), StreamingSpeechToText)


def test_deepgram_has_no_streaming_mode() -> None:
    """Deliberate. Deepgram is here to be scored against ElevenLabs on files,
    and a session refuses it for a live call rather than failing mid-turn."""
    assert not isinstance(build("deepgram", "fake-key"), StreamingSpeechToText)


def test_the_realtime_model_is_not_the_file_model() -> None:
    """scribe_v1 cannot stream. Pointing the websocket at it would fail on
    connect, in a place that says nothing about why."""
    stt = ElevenLabsSTT("fake-key")

    assert stt.realtime_model_id == settings.stt.elevenlabs_realtime_model
    assert stt.realtime_model_id != stt.model_id


@pytest.mark.live
@pytest.mark.skipif(not ELEVENLABS_KEY, reason="no ELEVENLABS_API_KEY")
async def test_a_committed_turn_comes_back(phone_audio: Path) -> None:
    """No speech in it, so the transcript is empty. This proves the handshake,
    the audio framing and the commit, which is the part that was uncertain."""
    from voice_agent.audio import pcm

    ears = ElevenLabsSTT(ELEVENLABS_KEY).stream(language="ur")
    await ears.open()
    try:
        audio = pcm(phone_audio)
        step = settings.audio.sample_rate * settings.audio.frame_ms // 1000 * 2
        for at in range(0, len(audio), step):
            await ears.push(audio[at : at + step])

        assert isinstance(await ears.commit(), str)
    finally:
        await ears.close()


@pytest.mark.live
@pytest.mark.skipif(
    not (ELEVENLABS_KEY and os.getenv("AZURE_SPEECH_KEY")),
    reason="needs ElevenLabs and Azure keys",
)
async def test_real_urdu_speech_streams_back_as_text() -> None:
    """Spoken by Azure, heard by Scribe, over the websocket a live call uses.
    Synthetic speech, so this proves the plumbing rather than the accuracy."""
    from voice_agent.lang.ur.voice import KEYTERMS, TTS_VOICE
    from voice_agent.tts import AzureTTS

    voice = AzureTTS(
        os.environ["AZURE_SPEECH_KEY"],
        os.getenv("AZURE_SPEECH_REGION", "centralindia"),
        TTS_VOICE,
    )
    ears = ElevenLabsSTT(ELEVENLABS_KEY, keyterms=KEYTERMS).stream(language="ur")
    await ears.open()
    try:
        async for chunk in voice.stream("جی ہاں ٹھیک ہے"):
            await ears.push(chunk)
        heard = await ears.commit()
    finally:
        await ears.close()

    assert heard, "nothing came back from the realtime recognizer"
