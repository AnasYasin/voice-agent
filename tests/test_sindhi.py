"""The Sindhi language pack and the ElevenLabs voice it needs. No network."""

from __future__ import annotations

import json
import wave
from pathlib import Path
from typing import Any

import pytest

from voice_agent import stt, tts
from voice_agent.lang.sd.normalize import SindhiNormalizer, spoken_time
from voice_agent.language import Normalizer, load

normalizer = SindhiNormalizer()


def test_sindhi_loads_on_the_elevenlabs_voice() -> None:
    language = load("sd-PK")

    assert language.locale == "sd-PK"
    assert language.name == "Sindhi"
    assert (language.stt_provider, language.stt_language) == ("gemini", "sd-Arab-IN")
    assert language.tts_provider == "elevenlabs"
    assert language.tts_voice == "QfY4EJSXrwbmN0wR4597"


def test_urdu_and_english_still_name_azure() -> None:
    """The provider is data on the pack, so main.py picks by name."""
    assert load("ur-PK").tts_provider == load("en-US").tts_provider == "azure"


def test_the_script_has_the_same_shape_as_the_urdu_one() -> None:
    sindhi, urdu = load("sd-PK").script, load("ur-PK").script

    assert sindhi.first == urdu.first == "confirm_appointment"
    assert set(sindhi.states) == set(urdu.states)


def test_the_normalizer_satisfies_the_protocol() -> None:
    assert isinstance(load("sd-PK").normalizer, Normalizer)


def test_only_sindhi_names_its_own_recognizer() -> None:
    assert load("ur-PK").stt_provider == "", "every other pack still follows STT_PROVIDER"


# --- the Gemini recognizer, with the websocket faked ---


class FakeSocket:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send(self, raw: str) -> None:
        self.sent.append(json.loads(raw))

    async def close(self) -> None:
        pass


async def test_gemini_commit_joins_the_finals_and_restarts_activity() -> None:
    """The message shapes are the ones the live endpoint sent on 11 Oct 2026."""
    ears = stt.GeminiStream("key", "model", "sd-Arab-IN", [])
    ears._socket = FakeSocket()

    ears._take({"serverContent": {"interimInputTranscription": {"text": "توھان جو"}}})
    ears._take({"serverContent": {"inputTranscription": {"text": "توهان جو نالو"}}})
    ears._take({"serverContent": {"inputTranscription": {"text": "ڇا آهي؟"}}})
    ears._take({"serverContent": {"generationComplete": True}})
    ears._take({"serverContent": {}, "voiceActivity": {"type": "ACTIVITY_END"}})

    assert await ears.commit() == "توهان جو نالو ڇا آهي؟"
    assert ears._socket.sent == [
        {"realtimeInput": {"activityEnd": {}}},
        {"realtimeInput": {"activityStart": {}}},
    ]

    ears._take({"serverContent": {"generationComplete": True}})
    assert await ears.commit() == "", "the next turn starts empty"


async def test_gemini_reopens_when_the_server_drops_an_empty_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ears = stt.GeminiStream("key", "model", "sd-Arab-IN", [])
    ears._socket = FakeSocket()
    reopened = []

    async def reopen() -> None:
        reopened.append(True)

    monkeypatch.setattr(ears, "open", reopen)
    ears._turns.put_nowait(RuntimeError("received 1008 (policy violation)"))

    assert await ears.commit() == ""
    assert reopened == [True]
    assert ears._socket.sent == [{"realtimeInput": {"activityEnd": {}}}], (
        "no start on a dead socket"
    )


def test_build_knows_gemini() -> None:
    assert stt.build("gemini", "key", ["ها"]).name == "gemini-transcribe"


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


def test_for_speech_turns_urdu_letters_into_sindhi_ones() -> None:
    assert normalizer.for_speech("میرو نالو ہے") == "ميرو نالو هي"
    assert normalizer.for_speech("ڪتاب ۾ ڳالهه") == "ڪتاب ۾ ڳالهه", "Sindhi letters pass untouched"


def test_how_to_speak_sindhi_rides_with_the_persona() -> None:
    language = load("sd-PK")

    assert "Vicholi" in language.speech
    assert language.persona.endswith(language.speech)
    assert load("ur-PK").speech == "", "other packs have not written one"


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
    assert client.text_to_speech.requests[0]["model_id"] == "eleven_v4"


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
