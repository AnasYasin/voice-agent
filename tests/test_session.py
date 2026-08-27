"""Component 13. The runtime wiring.

Needs ffmpeg. STT, TTS and the extractor are fakes, so the chain is proved
without any API call. The `live` test at the bottom runs the real thing.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from voice_agent.config import settings
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
    assert reply.state == "confirm_appointment"
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
    session = make_session(tmp_path, hears=("ٹھیک ہے",), extracts=(True,))

    session.start(**CALLER)
    reply = session.hear(make_wav(tmp_path / "a.wav"))

    assert reply.state == "done"
    assert reply.expects_reply is False
    assert session.finished
    assert session.result.outcome == "done"
    assert session.result.slots == {"confirmed": True}


# --- keypad ---


def test_a_keypad_press_skips_stt_and_the_model(tmp_path: Path) -> None:
    session = make_session(tmp_path)
    session.start(**CALLER)

    reply = session.hear(key="1")

    assert reply.state == "done"
    assert session.stt.heard == []
    assert session.flow.extractor.seen == []


def test_the_reply_carries_the_keypad_options(tmp_path: Path) -> None:
    """The transport needs these to configure DTMF capture for the turn."""
    reply = make_session(tmp_path).start(**CALLER)

    assert reply.dtmf == {"1": True, "2": False, "3": "repeat"}


# --- transcript and misuse ---


def test_the_transcript_records_both_sides(tmp_path: Path) -> None:
    """Kept in memory for now. Persisting it is store.py's job."""
    session = make_session(tmp_path, hears=("جی ہاں",), extracts=(True,))
    session.start(**CALLER)
    session.hear(make_wav(tmp_path / "a.wav"))

    assert len(session.transcript) == 2
    assert session.transcript[0].heard == ""
    assert session.transcript[1].heard == "جی ہاں"
    assert session.transcript[1].state == "done"


def test_audio_files_do_not_overwrite_each_other(tmp_path: Path) -> None:
    session = make_session(tmp_path, hears=("جی ہاں",), extracts=(True,))
    first = session.start(**CALLER)
    second = session.hear(make_wav(tmp_path / "a.wav"))

    assert first.audio != second.audio
    assert first.audio.exists() and second.audio.exists()


def test_hearing_after_the_call_ended_raises(tmp_path: Path) -> None:
    session = make_session(tmp_path, extracts=(True,))
    session.start(**CALLER)
    session.hear(key="1")

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

    assert turn.heard, "nothing came back from STT"
    assert turn.state in session.flow.script.states


# --- the live call ---
#
# Same session, same flow engine, driven by streams instead of files. The fakes
# below stand in for the websocket and the synthesiser; everything between them
# is the real thing.


class FakeEars:
    """A recognizer that hands back queued transcripts on commit."""

    def __init__(self, *texts: str) -> None:
        self.queue = list(texts)
        self.heard = bytearray()
        self.opened = False
        self.closed = False

    async def open(self) -> None:
        self.opened = True

    async def push(self, pcm: bytes) -> None:
        self.heard += pcm

    async def commit(self) -> str:
        return self.queue.pop(0) if self.queue else ""

    async def close(self) -> None:
        self.closed = True


class StreamingFakeSTT(FakeSTT):
    def __init__(self, *texts: str) -> None:
        super().__init__(*texts)
        self.ears = FakeEars(*texts)
        self.languages_streamed: list[str] = []

    def stream(self, language: str) -> FakeEars:
        self.languages_streamed.append(language)
        return self.ears


class StreamingFakeTTS(FakeTTS):
    """A sentence's worth of silence per call, so the lookahead is exercised."""

    FRAMES = 40

    async def stream(self, text: str) -> Any:
        self.spoken.append(text)
        for _ in range(self.FRAMES):
            yield b"\x00" * 320


class FakeResponder:
    name = "fake-chat"

    def __init__(self, reply: str, end_call: bool = False) -> None:
        self.reply = reply
        self.end_call = end_call
        self.abandoned = False

    def respond(self, said: str, **_: Any) -> Any:
        from voice_agent.llm import Spoken

        return Spoken(self.reply, self.end_call)

    def stream(self, said: str, **_: Any) -> Any:
        return FakeUtterance(self.reply, self)


class FakeUtterance:
    def __init__(self, reply: str, responder: FakeResponder) -> None:
        self.reply = reply
        self.responder = responder
        self.text = ""
        self.end_call = False

    async def __aiter__(self) -> Any:
        try:
            for letter in self.reply:
                self.text += letter
                yield letter
        except GeneratorExit:
            self.responder.abandoned = True
            raise
        self.end_call = self.responder.end_call


def live_session(
    tmp_path: Path, *, hears: tuple = (), talk: str = "", ends: bool = False
) -> Session:
    return Session(
        language=load_language("ur-PK"),
        stt=StreamingFakeSTT(*hears),
        tts=StreamingFakeTTS(tmp_path),
        extractor=FakeExtractor(),
        work_dir=tmp_path / "call",
        responder=FakeResponder(talk, end_call=ends) if talk else None,
        conversation=bool(talk),
    )


async def played(speaking: Any) -> bytes:
    return b"".join([chunk async for chunk in speaking.chunks])


async def test_a_script_line_is_played_from_the_cache_not_streamed(tmp_path: Path) -> None:
    """Fixed text is already a WAV on disk. Streaming it would be work with
    nothing to wait for."""
    session = live_session(tmp_path)

    audio = await played(session.greet(**CALLER))

    assert audio, "the greeting has to be playable"
    assert (session.work_dir / "said_01.wav").exists()
    assert session.transcript[0].said == session.flow.script.states[
        "confirm_appointment"
    ].ask.format(**CALLER)


async def test_the_greeting_is_8khz_telephone_audio(tmp_path: Path) -> None:
    session = live_session(tmp_path)

    await played(session.greet(**CALLER))

    from voice_agent.audio import probe

    assert probe(session.work_dir / "said_01.wav").sample_rate == 8000


async def test_a_model_reply_is_spoken_a_sentence_at_a_time(tmp_path: Path) -> None:
    """The point of the whole thing. The voice starts on the first sentence
    instead of waiting for the paragraph."""
    session = live_session(tmp_path, hears=("کیا حال ہے",), talk="پہلا۔ دوسرا۔")
    await session.open()
    session.greet()

    await played(await session.answer())

    assert session.tts.spoken == ["پہلا۔", "دوسرا۔"]


async def test_the_transcript_records_a_streamed_turn(tmp_path: Path) -> None:
    session = live_session(tmp_path, hears=("کیا حال ہے",), talk="پہلا۔ دوسرا۔")
    await session.open()
    session.greet()

    await played(await session.answer())

    assert session.transcript[-1].heard == "کیا حال ہے"
    assert session.transcript[-1].said == "پہلا۔ دوسرا۔"


async def test_a_turn_cut_short_is_recorded_as_what_was_heard(tmp_path: Path) -> None:
    """Barge-in. The caller heard the first sentence and nothing after it, and
    that is what the transcript should say."""
    session = live_session(tmp_path, hears=("کیا",), talk="پہلا۔ دوسرا۔")
    await session.open()
    session.greet()

    speaking = await session.answer()
    chunks = speaking.chunks.__aiter__()
    await anext(chunks)
    await chunks.aclose()

    assert session.transcript[-1].said == "پہلا۔"


async def test_streamed_audio_is_recorded_next_to_the_transcript(tmp_path: Path) -> None:
    session = live_session(tmp_path, hears=("کیا",), talk="جی۔")
    await session.open()
    session.greet()

    await played(await session.answer())

    from voice_agent.audio import probe

    assert probe(session.work_dir / "said_02.wav").sample_rate == 8000


async def test_caller_audio_is_band_limited_before_the_recognizer(tmp_path: Path) -> None:
    """A 48 kHz browser mic would flatter the STT exactly as it does on the
    file path, and the accuracy number would be one we cannot ship against."""
    session = live_session(tmp_path)
    await session.open()

    await session.listen(b"\x40\x00" * 800)

    assert bytes(session.stt.ears.heard) != b"\x40\x00" * 800
    assert len(session.stt.ears.heard) == 1600


async def test_the_caller_is_recorded_once_for_the_whole_call(tmp_path: Path) -> None:
    """There is no file per turn any more. The recognizer stays open, so the
    caller's audio never stops to become one."""
    session = live_session(tmp_path)
    await session.open()
    await session.listen(b"\x00\x00" * 800)
    await session.close()

    assert (session.work_dir / "caller.wav").exists()
    assert session.stt.ears.closed


async def test_a_keypad_press_skips_the_recognizer_and_the_model(tmp_path: Path) -> None:
    session = live_session(tmp_path)
    await session.open()
    session.greet(**CALLER)

    speaking = await session.answer(key="1")

    assert speaking.state == "done"
    assert speaking.expects_reply is False
    assert session.stt.ears.queue == [], "no commit, so nothing was consumed"


async def test_the_recognizer_is_asked_for_the_right_language(tmp_path: Path) -> None:
    session = live_session(tmp_path)
    await session.open()

    assert session.stt.languages_streamed == ["ur"]
    assert session.stt.ears.opened


async def test_a_streamed_transcript_is_normalised_too(tmp_path: Path) -> None:
    session = live_session(tmp_path, hears=("جی ہاں، ۳ بجے۔",), talk="جی۔")
    await session.open()
    session.greet()

    await played(await session.answer())

    assert session.transcript[-1].heard == "جی ہاں 3 بجے"


async def test_a_file_only_recognizer_is_rejected_before_the_call_starts(tmp_path: Path) -> None:
    """Better here than three seconds into a real call."""
    session = make_session(tmp_path)

    with pytest.raises(TypeError, match="no streaming mode"):
        await session.open()


@pytest.mark.live
@pytest.mark.skipif(not LIVE, reason="needs Anthropic, Azure and ElevenLabs keys")
async def test_a_real_streamed_turn_end_to_end(tmp_path: Path) -> None:
    """Every stage real and every stage streaming: the recognizer hears the
    caller as they speak, the model is read as it writes, and the voice is
    played as it synthesises.

    The caller audio is synthesised rather than recorded, so this proves the
    pipeline, not the accuracy.
    """
    import time

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
        responder=llm.build_responder(os.environ["ANTHROPIC_API_KEY"]),
        conversation=True,
    )

    await session.open()
    try:
        assert await played(session.greet())

        # Speak at the agent the way a caller would, while it listens. Paced to
        # the clock, because that is what makes the commit at the end cheap: an
        # unpaced push leaves the recognizer with a backlog to work through.
        async for chunk in voice.tts.stream("السلام علیکم، آپ کیسی ہیں؟"):
            await session.listen(chunk)
            await asyncio.sleep(settings.audio.frame_ms / 1000)

        started = time.monotonic()
        speaking = await session.answer()
        chunks = speaking.chunks.__aiter__()
        first = await anext(chunks)
        to_first_audio = time.monotonic() - started

        rest = b"".join([chunk async for chunk in chunks])
    finally:
        await session.close()

    assert first and rest, "the reply has to keep coming after the first chunk"
    assert session.transcript[-1].heard, "nothing came back from the recognizer"
    assert session.transcript[-1].said, "nothing came back from the model"

    # A guard against the pipeline silently going back to waiting stage by
    # stage, not a benchmark. The number this actually lands on is mostly the
    # three round trips to whichever region AZURE_SPEECH_REGION names.
    print(f"\n  caller stopped -> first audio: {to_first_audio * 1000:.0f} ms")
    assert to_first_audio < 3.0, f"first audio took {to_first_audio:.2f}s"


async def test_barge_in_stops_the_voice_and_the_model(tmp_path: Path) -> None:
    """The point of streaming the model at all is that there is a request still
    open when the caller interrupts. It has to be abandoned, not left running."""
    session = live_session(tmp_path, hears=("کچھ",), talk="پہلا۔ دوسرا۔ تیسرا۔ چوتھا۔")
    await session.open()
    session.greet()

    speaking = await session.answer()
    chunks = speaking.chunks.__aiter__()
    await anext(chunks)
    await chunks.aclose()

    assert session.flow.responder.abandoned, "the model request was left open"


async def test_synthesis_runs_ahead_of_playback_but_not_far(tmp_path: Path) -> None:
    """Far enough that the next sentence is ready when the current one ends,
    which is what stops a pause between every sentence. Not so far that an
    interruption throws away a whole reply that was already paid for.

    A long reply, so the buffer fills whatever `lookahead_seconds` is set to.
    """
    reply = " ".join(f"جملہ{n}۔" for n in range(20))
    session = live_session(tmp_path, hears=("کچھ",), talk=reply)
    await session.open()
    session.greet()

    speaking = await session.answer()
    chunks = speaking.chunks.__aiter__()
    await anext(chunks)

    assert len(session.tts.spoken) > 1, "did not run ahead, every sentence would pause"
    assert len(session.tts.spoken) < 20, "ran away with the whole reply"
    await chunks.aclose()


async def test_interrupting_a_full_buffer_does_not_deadlock(tmp_path: Path) -> None:
    """Barge-in while synthesis is blocked on a full queue. The reader stops
    draining and waits for the writer; the writer must not then wait for the
    reader. Regression: this hung a real call rather than failing it."""
    reply = " ".join(f"جملہ{n}۔" for n in range(20))
    session = live_session(tmp_path, hears=("رکیں",), talk=reply)
    await session.open()
    session.greet()

    speaking = await session.answer()
    chunks = speaking.chunks.__aiter__()
    await anext(chunks)

    await asyncio.wait_for(chunks.aclose(), timeout=5)
    assert session.flow.responder.abandoned


@pytest.mark.live
@pytest.mark.skipif(not LIVE, reason="needs Anthropic, Azure and ElevenLabs keys")
async def test_a_real_three_turn_conversation_end_to_end(tmp_path: Path) -> None:
    """Three caller turns in talk mode, every stage real.

    The one-turn test proves the pipeline moves. This proves the parts of a
    conversation that only exist across turns: that the recognizer survives
    being committed three times on one socket, that the queue drains clean
    between turns, and that the model is still holding the earlier turns.

    The caller is synthesised in the male voice so it is never the agent's own
    audio being fed back, and paced to the clock because an unpaced push leaves
    the recognizer a backlog and makes the commit look slow.
    """
    import time

    from voice_agent import llm, stt, tts

    language = load_language("ur-PK")
    voice = tts.build(
        os.environ["AZURE_SPEECH_KEY"],
        os.environ["AZURE_SPEECH_REGION"],
        language.tts_voice,
        cache_dir=tmp_path / "cache",
        locale=language.locale,
    )
    # A second voice, so the caller does not sound like the agent.
    caller = tts.build(
        os.environ["AZURE_SPEECH_KEY"],
        os.environ["AZURE_SPEECH_REGION"],
        "ur-PK-AsadNeural",
        cache_dir=tmp_path / "caller_cache",
    )
    session = Session(
        language=language,
        stt=stt.build("elevenlabs", os.environ["ELEVENLABS_API_KEY"], language.keyterms),
        tts=voice,
        extractor=llm.build(os.environ["ANTHROPIC_API_KEY"]),
        work_dir=tmp_path / "call",
        responder=llm.build_responder(os.environ["ANTHROPIC_API_KEY"]),
        conversation=True,
    )

    # The last line asks for something only the first line supplied, so a model
    # that lost the history cannot answer it.
    turns = [
        "السلام علیکم، میرا نام انس ہے۔",
        "مجھے کل ڈاکٹر سے ملاقات کا وقت لینا ہے۔",
        "اچھا، آپ کو میرا نام یاد ہے؟",
    ]

    await session.open()
    latencies = []
    try:
        assert await played(session.greet())

        for line in turns:
            async for chunk in caller.tts.stream(line):
                await session.listen(chunk)
                await asyncio.sleep(settings.audio.frame_ms / 1000)

            started = time.monotonic()
            speaking = await session.answer()
            chunks = speaking.chunks.__aiter__()
            first = await anext(chunks)
            latencies.append(time.monotonic() - started)
            rest = b"".join([chunk async for chunk in chunks])

            assert first and rest, "the reply has to keep coming after the first chunk"
    finally:
        await session.close()

    spoken = [entry for entry in session.transcript if entry.said]
    print("\n  turn  to first audio   heard / said")
    for i, (entry, seconds) in enumerate(zip(spoken[-3:], latencies, strict=False), 1):
        print(f"  {i}     {seconds * 1000:6.0f} ms       {entry.heard}")
        print(f"                         {entry.said}")

    assert len(spoken) >= 3, "every turn should have produced a reply"
    # The point of the third turn. The name was only ever said in the first.
    assert "انس" in spoken[-1].said, f"the model lost the history: {spoken[-1].said}"
    # Median, not max. Six turns measured straight through the stages sat in a
    # 2.1-2.5 s band, but one turn through the full session path came back at
    # 8.4 s and has not reproduced. Asserting on max here would make this test
    # fail for a reason it cannot diagnose; the loose ceiling still catches the
    # pipeline going back to waiting stage by stage.
    median = sorted(latencies)[len(latencies) // 2]
    assert median < 3.0, f"median turn was {median:.2f}s of {latencies}"
    assert max(latencies) < 10.0, f"slowest turn was {max(latencies):.2f}s"


async def test_barge_in_does_not_leave_the_model_remembering_unheard_speech(
    tmp_path: Path,
) -> None:
    """What the model thinks it said has to match what the caller heard.

    The transcript is written from the queue, so it records only sentences that
    reached the transport. `Conversation._remember` is written from the model's
    own accumulated text, which runs ahead by the whole lookahead buffer. On a
    barge-in those two disagree, and the model then builds its next reply on
    sentences nobody heard.
    """
    session = live_session(tmp_path, hears=("سوال",), talk="پہلا جملہ۔ دوسرا جملہ۔ تیسرا جملہ۔")
    await session.open()
    try:
        await played(session.greet())

        speaking = await session.answer()
        chunks = speaking.chunks.__aiter__()
        await anext(chunks)  # one chunk, then the caller talks over it
        await chunks.aclose()
    finally:
        await session.close()

    heard_by_caller = session.transcript[-1].said
    remembered = [entry for entry in session.flow._history if entry["role"] == "assistant"][-1]

    assert heard_by_caller, "the caller heard at least the first sentence"
    assert remembered["content"] == heard_by_caller, (
        f"the model remembers saying {remembered['content']!r} "
        f"but the caller only heard {heard_by_caller!r}"
    )


async def test_an_abandoned_turn_is_joined_to_the_next_one() -> None:
    """A caller talked over before hearing a word has not had a turn.

    Their words and whatever they say next are one utterance split by the
    interruption. Left as two exchanges, the older half looks answered and the
    model keeps acting on an intent the caller has already moved past, which is
    what made a live call hang up on someone asking whether it was still there.
    """
    from voice_agent.flow import Conversation

    talk = Conversation(FakeResponder("پہلا۔ دوسرا۔"), greeting="ہیلو")
    talk.start()

    # Turn one: the model wrote a whole reply, the caller heard none of it.
    first = talk.reply_stream(said="کال کاٹ دیں")
    async for _ in first.stream:
        pass
    talk.spoken("")

    # Turn two: heard in full.
    second = talk.reply_stream(said="کیا آپ ہیں")
    async for _ in second.stream:
        pass
    talk.spoken("پہلا۔")

    callers = [entry for entry in talk._history if entry["role"] == "user"]
    assert callers[-1]["content"] == "کال کاٹ دیں کیا آپ ہیں", (
        f"the two halves should be one turn, got {callers[-1]['content']!r}"
    )
    assert len(callers) == 1, f"the abandoned turn should not stand alone: {callers}"
