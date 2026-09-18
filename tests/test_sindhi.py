"""The Sindhi language pack and the ElevenLabs voice it needs. No network."""

from __future__ import annotations

import wave
from pathlib import Path
from typing import Any

import pytest

from voice_agent import tts
from voice_agent.lang.sd.normalize import SindhiNormalizer, spoken_time
from voice_agent.language import Normalizer, load

normalizer = SindhiNormalizer()


def test_sindhi_loads_on_the_elevenlabs_voice() -> None:
    language = load("sd-PK")

    assert language.locale == "sd-PK"
    assert language.name == "سنڌي"
    assert language.stt_language == "snd"
    assert language.tts_provider == "elevenlabs"
    assert language.tts_voice == "21m00Tcm4TlvDq8ikWAM"


def test_urdu_and_english_still_name_azure() -> None:
    """The provider is data on the pack, so main.py picks by name."""
    assert load("ur-PK").tts_provider == load("en-US").tts_provider == "azure"


def test_the_script_has_the_same_shape_as_the_urdu_one() -> None:
    sindhi, urdu = load("sd-PK").script, load("ur-PK").script

    assert sindhi.first == urdu.first == "confirm_appointment"
    assert set(sindhi.states) == set(urdu.states)


def test_the_normalizer_satisfies_the_protocol() -> None:
    assert isinstance(load("sd-PK").normalizer, Normalizer)


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("ها، ٺيڪ آهي۔", "ها ٺيڪ آهي"),
        ("۳ وڳي", "3 وڳي"),
        ("Haan, theek aa!", "haan theek aa"),
        ("", ""),
    ],
)
def test_from_speech(raw: str, expected: str) -> None:
    assert normalizer.from_speech(raw) == expected


def test_spoken_time_uses_part_of_day_hour_word_and_wagi() -> None:
    assert spoken_time(15, 0) == "منجهند ٽي وڳي"
    assert spoken_time(9, 30) == "صبح نو وڳي 30 منٽ"
    assert spoken_time(0, 0) == "رات ٻارنهن وڳي"


def test_sentences_split_on_the_urdu_full_stop_too() -> None:
    finished, rest = normalizer.sentences("سلام۔ ڪيئن آهيو؟ مان")

    assert finished == ["سلام۔", "ڪيئن آهيو؟"]
    assert rest == " مان"


# --- the ElevenLabs voice, with the network faked ---


class FakeVoiceApi:
    """Stands in for the SDK's text_to_speech: records the request, returns audio."""

    def __init__(self, audio: bytes) -> None:
        self.audio = audio
        self.requests: list[dict[str, Any]] = []

    def convert(self, voice_id: str, **kwargs: Any) -> Any:
        self.requests.append({"voice_id": voice_id, **kwargs})
        yield self.audio[: len(self.audio) // 2]
        yield self.audio[len(self.audio) // 2 :]

    async def stream(self, voice_id: str, **kwargs: Any) -> Any:
        self.requests.append({"voice_id": voice_id, **kwargs})
        yield self.audio[: len(self.audio) // 2]
        yield self.audio[len(self.audio) // 2 :]


class FakeClient:
    def __init__(self, audio: bytes) -> None:
        self.text_to_speech = FakeVoiceApi(audio)


def test_ulaw_decodes_exactly_like_the_standard_library() -> None:
    """All 256 byte values against audioop, which Python 3.13 removes and is
    why the table exists. 0xFF and 0x7F are both zero, positive and negative."""
    import array

    audioop = pytest.importorskip("audioop")
    every_byte = bytes(range(256))

    mine = array.array("h", tts.ulaw_to_pcm(every_byte))
    reference = array.array("h", audioop.ulaw2lin(every_byte, 2))

    assert mine == reference
    assert (mine[0xFF], mine[0x7F], mine[0x80], mine[0x00]) == (0, 0, 32124, -32124)


def test_synthesize_writes_a_16khz_wav_from_pcm(tmp_path: Path) -> None:
    client = FakeClient(b"\x01\x00" * 1600)
    voice = tts.ElevenLabsTTS("key", "voice-id", client=client)

    speech = voice.synthesize("سلام", tmp_path / "line.wav")

    with wave.open(str(speech.path)) as tape:
        assert (tape.getframerate(), tape.getnchannels(), tape.getnframes()) == (16000, 1, 1600)
    assert client.text_to_speech.requests[0]["output_format"] == "pcm_16000"
    assert client.text_to_speech.requests[0]["model_id"] == "eleven_v3"


async def test_stream_asks_for_ulaw_and_hands_back_pcm() -> None:
    client = FakeClient(bytes([0xFF]) * 320)
    voice = tts.ElevenLabsTTS("key", "voice-id", client=client)

    chunks = [chunk async for chunk in voice.stream("سلام")]

    assert b"".join(chunks) == b"\x00\x00" * 320
    assert client.text_to_speech.requests[0]["output_format"] == "ulaw_8000"


async def test_streaming_nothing_is_refused() -> None:
    voice = tts.ElevenLabsTTS("key", "voice-id", client=FakeClient(b""))

    with pytest.raises(ValueError, match="empty"):
        async for _ in voice.stream("   "):
            pass


def test_the_cache_wraps_the_elevenlabs_voice_too(tmp_path: Path) -> None:
    client = FakeClient(b"\x00\x00" * 160)
    voice = tts.CachedTTS(tts.ElevenLabsTTS("key", "voice-id", client=client), tmp_path / "cache")

    first = voice.synthesize("سلام")
    second = voice.synthesize("سلام")

    assert (first.cached, second.cached) == (False, True)
    assert len(client.text_to_speech.requests) == 1
