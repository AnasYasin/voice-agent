"""Component 13. The runtime wiring.

Needs ffmpeg. STT, TTS and the extractor are fakes, so the chain is proved
without any API call. The `live` test at the bottom runs the real thing.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from voice_agent.language import load as load_language
from voice_agent.llm import Answer
from voice_agent.session import Session
from voice_agent.tts import Speech

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")

CALLER = {"name": "انس", "date": "کل", "time": "چار"}


def make_wav(path: Path, seconds: float = 1.0, rate: int = 48000) -> Path:
    """Wideband, like a browser mic would hand us."""
    # fmt: off
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi",
            "-i", f"sine=frequency=440:duration={seconds}:sample_rate={rate}",
            "-ac", "1",
            str(path),
        ],
        check=True,
    )
    # fmt: on
    return path


@dataclass
class FakeTranscript:
    text: str


class FakeSTT:
    """Returns queued transcripts and records what it was asked to hear."""

    name = "fake-stt"

    def __init__(self, *texts: str) -> None:
        self.queue = list(texts)
        self.heard: list[Path] = []
        self.languages: list[str] = []

    def transcribe(self, path: Path, language: str) -> FakeTranscript:
        self.heard.append(path)
        self.languages.append(language)
        return FakeTranscript(self.queue.pop(0) if self.queue else "")


class FakeTTS:
    """Writes a real WAV so the audio chain is genuinely exercised."""

    name = "fake-tts"
    voice = "fake-voice"

    def __init__(self, tmp_path: Path) -> None:
        self.tmp_path = tmp_path
        self.spoken: list[str] = []

    def synthesize(self, text: str, dst: Path | None = None) -> Speech:
        self.spoken.append(text)
        path = dst or self.tmp_path / f"tts_{len(self.spoken):02d}.wav"
        make_wav(path, seconds=0.5, rate=24000)
        return Speech(path=path, voice=self.voice, cached=False)


class FakeExtractor:
    name = "fake-llm"

    def __init__(self, *values: Any) -> None:
        self.queue = list(values)
        self.seen: list[str] = []

    def extract(self, said: str, slot_type: str, guidance: str = "") -> Answer:
        self.seen.append(said)
        value = self.queue.pop(0) if self.queue else None
        return Answer(value=value, slot_type=slot_type, said=said, model="fake")


def make_session(tmp_path: Path, *, hears: tuple = (), extracts: tuple = ()) -> Session:
    return Session(
        language=load_language("ur-PK"),
        stt=FakeSTT(*hears),
        tts=FakeTTS(tmp_path),
        extractor=FakeExtractor(*extracts),
        work_dir=tmp_path / "call",
    )


# --- the chain ---


def test_start_returns_playable_8khz_audio(tmp_path: Path) -> None:
    session = make_session(tmp_path)

    reply = session.start(**CALLER)

    assert reply.audio.exists()
    assert reply.state == "verify_identity"
    assert reply.expects_reply is True
    info = __import__("voice_agent.audio", fromlist=["probe"]).probe(reply.audio)
    assert info.sample_rate == 8000
    assert info.channels == 1


def test_the_caller_name_reaches_the_voice(tmp_path: Path) -> None:
    session = make_session(tmp_path)

    reply = session.start(**CALLER)

    assert "انس" in reply.say
    assert "انس" in session.tts.spoken[0]


def test_incoming_audio_is_band_limited_before_stt(tmp_path: Path) -> None:
    """A 48 kHz browser recording would flatter the STT and give an accuracy
    number we cannot ship against."""
    session = make_session(tmp_path, hears=("جی ہاں",), extracts=(True,))
    session.start(**CALLER)

    session.hear(make_wav(tmp_path / "caller.wav", rate=48000))

    handed_to_stt = session.stt.heard[0]
    info = __import__("voice_agent.audio", fromlist=["probe"]).probe(handed_to_stt)
    assert info.sample_rate == 8000


def test_stt_is_asked_for_the_right_language(tmp_path: Path) -> None:
    session = make_session(tmp_path, hears=("جی ہاں",), extracts=(True,))
    session.start(**CALLER)

    session.hear(make_wav(tmp_path / "caller.wav"))

    assert session.stt.languages == ["ur"]


def test_transcripts_are_normalised_before_the_model_sees_them(tmp_path: Path) -> None:
    """Punctuation and mixed digit systems must not reach slot extraction."""
    session = make_session(tmp_path, hears=("جی ہاں، ۳ بجے۔",), extracts=(True,))
    session.start(**CALLER)

    session.hear(make_wav(tmp_path / "caller.wav"))

    assert session.flow.extractor.seen == ["جی ہاں 3 بجے"]


def test_a_whole_call_runs_and_produces_a_result(tmp_path: Path) -> None:
    """The component's reason to exist."""
    session = make_session(tmp_path, hears=("جی ہاں", "ٹھیک ہے"), extracts=(True, True))

    session.start(**CALLER)
    session.hear(make_wav(tmp_path / "a.wav"))
    reply = session.hear(make_wav(tmp_path / "b.wav"))

    assert reply.state == "done"
    assert reply.expects_reply is False
    assert session.finished
    assert session.result.outcome == "done"
    assert session.result.slots == {"identity_confirmed": True, "confirmed": True}


# --- keypad ---


def test_a_keypad_press_skips_stt_and_the_model(tmp_path: Path) -> None:
    session = make_session(tmp_path)
    session.start(**CALLER)

    reply = session.hear(key="1")

    assert reply.state == "confirm_appointment"
    assert session.stt.heard == []
    assert session.flow.extractor.seen == []


def test_the_reply_carries_the_keypad_options(tmp_path: Path) -> None:
    """The transport needs these to configure DTMF capture for the turn."""
    reply = make_session(tmp_path).start(**CALLER)

    assert reply.dtmf == {"1": True, "2": False}


# --- transcript and misuse ---


def test_the_transcript_records_both_sides(tmp_path: Path) -> None:
    """Kept in memory for now. Persisting it is store.py's job."""
    session = make_session(tmp_path, hears=("جی ہاں",), extracts=(True,))
    session.start(**CALLER)
    session.hear(make_wav(tmp_path / "a.wav"))

    assert len(session.transcript) == 2
    assert session.transcript[0].heard == ""
    assert session.transcript[1].heard == "جی ہاں"
    assert session.transcript[1].state == "confirm_appointment"


def test_audio_files_do_not_overwrite_each_other(tmp_path: Path) -> None:
    session = make_session(tmp_path, hears=("جی ہاں",), extracts=(True,))
    first = session.start(**CALLER)
    second = session.hear(make_wav(tmp_path / "a.wav"))

    assert first.audio != second.audio
    assert first.audio.exists() and second.audio.exists()


def test_hearing_after_the_call_ended_raises(tmp_path: Path) -> None:
    session = make_session(tmp_path, extracts=(False,))
    session.start(**CALLER)
    session.hear(key="2")

    with pytest.raises(RuntimeError, match="ended"):
        session.hear(key="1")


def test_session_never_imports_anything_urdu_specific() -> None:
    """If this fails, adding English stops being a folder."""
    source = (Path(__file__).resolve().parents[1] / "src/voice_agent/session.py").read_text()

    assert "lang.ur" not in source
    assert "lang/ur" not in source


# --- the real thing ---

LIVE = all(os.getenv(k) for k in ("ANTHROPIC_API_KEY", "AZURE_SPEECH_KEY", "ELEVENLABS_API_KEY"))


@pytest.mark.live
@pytest.mark.skipif(not LIVE, reason="needs Anthropic, Azure and ElevenLabs keys")
def test_a_real_spoken_turn_end_to_end(tmp_path: Path) -> None:
    """Real Azure voice, real band-limiting, real Claude extraction. The caller
    audio is synthesised rather than recorded, so this proves the wiring, not
    the accuracy."""
    from voice_agent import llm, stt, tts

    language = load_language("ur-PK")
    voice = tts.build(
        os.environ["AZURE_SPEECH_KEY"],
        os.environ["AZURE_SPEECH_REGION"],
        language.tts_voice,
        cache_dir=tmp_path / "cache",
    )
    session = Session(
        language=language,
        stt=stt.build("elevenlabs", os.environ["ELEVENLABS_API_KEY"], language.keyterms),
        tts=voice,
        extractor=llm.build(os.environ["ANTHROPIC_API_KEY"]),
        work_dir=tmp_path / "call",
    )

    opening = session.start(**CALLER)
    assert opening.audio.exists()

    # Speak a reply the way a caller would, then feed it back in as audio.
    reply_audio = voice.synthesize("جی ہاں، میں انس بول رہا ہوں").path
    turn = session.hear(reply_audio)

    assert turn.state == "confirm_appointment"
    assert turn.heard, "nothing came back from STT"
