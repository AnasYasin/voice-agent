"""Components 14 and 15.

The token, the playback pacing and the turn loop are testable without a LiveKit
server, and they are where the bugs live. Actually joining a room needs
infrastructure and a browser, so that is a manual step, not a unit test.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

livekit = pytest.importorskip("livekit", reason="livekit not installed")

from livekit import rtc  # noqa: E402
from livekit.agents.vad import VADEventType  # noqa: E402
from test_session import live_session  # noqa: E402

from voice_agent.config import settings  # noqa: E402
from voice_agent.session import Speaking  # noqa: E402
from voice_agent.transport import (  # noqa: E402
    FRAME_MS,
    VAD_SAMPLE_RATE,
    BrowserTransport,
    Transport,
    access_token,
)

SECRET = "devsecret_change_me_in_production"
FRAME_BYTES = settings.audio.sample_rate * FRAME_MS // 1000 * 2


def transport() -> BrowserTransport:
    return BrowserTransport("ws://localhost:7880", "devkey", SECRET, room="test-room")


class FakeSource:
    """Collects what would have gone into the room."""

    def __init__(self) -> None:
        self.frames: list[Any] = []

    async def capture_frame(self, frame: Any) -> None:
        self.frames.append(frame)


def ignore(frame: bytes) -> None:
    """A session that keeps no recording."""


def speaking(*chunks: bytes) -> Speaking:
    async def produce() -> AsyncIterator[bytes]:
        for chunk in chunks:
            yield chunk

    return Speaking(chunks=produce(), state="talk", expects_reply=True)


async def test_every_frame_played_is_handed_back_for_the_recording() -> None:
    """The recording's right channel is whatever went into the room, no more."""
    source = FakeSource()
    handed: list[bytes] = []

    await transport()._play(source, speaking(b"\x01" * (FRAME_BYTES * 3)), handed.append)

    assert len(handed) == len(source.frames) == 3
    assert all(len(frame) == FRAME_BYTES for frame in handed)


# --- tokens ---


def test_a_token_is_issued_for_the_room() -> None:
    token = access_token("devkey", SECRET, "test-room", "agent")

    assert token.count(".") == 2, "not a JWT"


def test_agent_and_caller_get_different_identities() -> None:
    """Two participants with the same identity evict each other from the room."""
    agent = access_token("devkey", SECRET, "r", "agent")
    caller = access_token("devkey", SECRET, "r", "caller")

    assert agent != caller


def test_the_transport_hands_out_a_caller_token() -> None:
    """This is what the browser client needs to join."""
    assert transport().caller_token().count(".") == 2


# --- playback ---


async def test_chunks_are_recut_into_frames() -> None:
    """The voice produces whatever size it likes. LiveKit wants 20 ms."""
    source = FakeSource()

    await transport()._play(source, speaking(b"\x00" * (FRAME_BYTES * 3)), ignore)

    assert len(source.frames) == 3
    assert all(len(bytes(frame.data)) == FRAME_BYTES for frame in source.frames)


async def test_a_frame_can_span_two_chunks() -> None:
    """Sentence boundaries do not land on frame boundaries, and dropping the
    remainder of every chunk would clip a syllable off each sentence."""
    source = FakeSource()
    half = FRAME_BYTES // 2

    await transport()._play(source, speaking(b"\x01" * half, b"\x02" * half), ignore)

    assert len(source.frames) == 1
    assert bytes(source.frames[0].data) == b"\x01" * half + b"\x02" * half


async def test_the_tail_is_padded_rather_than_dropped() -> None:
    """The last few milliseconds of a reply are usually the last word of it."""
    source = FakeSource()

    await transport()._play(source, speaking(b"\x01" * (FRAME_BYTES + 10)), ignore)

    assert len(source.frames) == 2
    assert bytes(source.frames[1].data) == b"\x01" * 10 + b"\x00" * (FRAME_BYTES - 10)


async def test_frames_carry_the_right_sample_count() -> None:
    """A wrong samples_per_channel plays back at the wrong speed."""
    source = FakeSource()

    await transport()._play(source, speaking(b"\x00" * FRAME_BYTES), ignore)

    assert source.frames[0].samples_per_channel == FRAME_BYTES // 2
    assert source.frames[0].sample_rate == settings.audio.sample_rate
    assert source.frames[0].num_channels == 1


async def test_playback_is_paced_to_the_clock() -> None:
    """Pushing frames as fast as LiveKit takes them means the agent thinks it
    has finished speaking long before the caller has heard it."""
    source = FakeSource()
    started = asyncio.get_running_loop().time()

    await transport()._play(source, speaking(b"\x00" * (FRAME_BYTES * 10)), ignore)

    assert asyncio.get_running_loop().time() - started >= 9 * FRAME_MS / 1000


async def test_stopping_playback_stops_the_voice_behind_it() -> None:
    """Barge-in has to reach the synthesiser, not just the speaker."""
    closed = asyncio.Event()

    async def voice() -> AsyncIterator[bytes]:
        try:
            while True:
                yield b"\x00" * FRAME_BYTES
        finally:
            closed.set()

    playing = asyncio.create_task(
        transport()._play(FakeSource(), Speaking(voice(), state="talk", expects_reply=True), ignore)
    )
    await asyncio.sleep(FRAME_MS / 1000)
    playing.cancel()

    with pytest.raises(asyncio.CancelledError):
        await playing
    assert closed.is_set()


# --- ending the call ---


async def test_the_call_can_end_while_nobody_is_talking() -> None:
    """A streamed farewell is only known to be one after the model finished
    writing it, which is while the agent is already saying it."""
    hung_up = asyncio.Event()
    never = _Never()

    waiting = asyncio.create_task(transport()._next(never, hung_up))
    await asyncio.sleep(0)
    hung_up.set()

    assert await waiting is None


async def test_a_caller_speaking_wins_over_waiting() -> None:
    assert await transport()._next(_Once("speech"), asyncio.Event()) == "speech"


async def test_the_caller_dropping_the_line_ends_the_call() -> None:
    """The events run out. That is a hangup, not an error to raise out of run()."""
    assert await transport()._next(_Ended(), asyncio.Event()) is None


class _Never:
    async def __anext__(self) -> Any:
        await asyncio.Event().wait()


class _Once:
    def __init__(self, value: Any) -> None:
        self.value = value

    async def __anext__(self) -> Any:
        return self.value


class _Ended:
    async def __anext__(self) -> Any:
        raise StopAsyncIteration


def test_the_whole_path_stays_at_telephone_rate() -> None:
    """A browser demo that sounds better than a phone call is a demo that lies.
    Silero only runs at 8 or 16 kHz, and 8 is what the line gives us."""
    assert VAD_SAMPLE_RATE == settings.audio.sample_rate == 8000


# --- shape ---


def test_browser_transport_satisfies_the_protocol() -> None:
    """If this breaks, swapping in SIP or PSTN stops being a config change."""
    assert isinstance(transport(), Transport)


def test_transport_never_imports_anything_urdu_specific() -> None:
    source = (Path(__file__).resolve().parents[1] / "src/voice_agent/transport.py").read_text()

    assert "lang.ur" not in source
    assert "lang/ur" not in source


def test_main_is_the_only_module_reading_the_environment() -> None:
    """Everything below main.py is handed what it needs. That is what makes
    the language switch and the transport swap work."""
    src = Path(__file__).resolve().parents[1] / "src/voice_agent"
    allowed = {"main.py", "config.py", "language.py"}

    offenders = [
        path.name
        for path in src.rglob("*.py")
        if path.name not in allowed
        and ("os.environ" in (t := path.read_text()) or "os.getenv" in t)
    ]

    assert offenders == [], f"these read the environment directly: {offenders}"


# --- the turn loop ---
#
# The trickiest async in the project: playback in the background, a caller who
# can interrupt it, and a call that can end while nobody is talking. LiveKit is
# faked, the session is real.


class FakeVadStream:
    """Emits queued VAD events, then waits like a live line with nobody on it."""

    def __init__(self, *events: Any) -> None:
        self.queue = list(events)
        self.frames: list[Any] = []
        self.closed = False

    def push_frame(self, frame: Any) -> None:
        self.frames.append(frame)

    def __aiter__(self) -> FakeVadStream:
        return self

    async def __anext__(self) -> Any:
        if not self.queue:
            await asyncio.Event().wait()
        await asyncio.sleep(0)
        return self.queue.pop(0)

    async def aclose(self) -> None:
        self.closed = True


class FakeVad:
    def __init__(self, stream: FakeVadStream) -> None:
        self._stream = stream

    def stream(self) -> FakeVadStream:
        return self._stream


class FakeAudioStream:
    """Caller frames, then silence for the rest of the call."""

    def __init__(self, frames: int) -> None:
        self.frames = frames

    async def __aiter__(self) -> AsyncIterator[Any]:
        for _ in range(self.frames):
            yield SimpleNamespace(
                frame=SimpleNamespace(data=b"\x00" * FRAME_BYTES),
            )
            await asyncio.sleep(0)
        await asyncio.Event().wait()


def vad_event(kind: Any) -> Any:
    return SimpleNamespace(type=kind, frames=[])


async def drive(session: Any, events: list[Any], caller_frames: int = 5) -> FakeVadStream:
    """Run one call through the transport with LiveKit faked out."""
    vad_stream = FakeVadStream(*events)
    source = FakeSource()

    with patch.object(rtc, "AudioStream", lambda *a, **k: FakeAudioStream(caller_frames)):
        await session.open()
        try:
            await transport()._drive(session, track=None, source=source, vad=FakeVad(vad_stream))
        finally:
            await session.close()
    return vad_stream


async def test_a_whole_talk_call_runs_to_a_hangup(tmp_path: Path) -> None:
    """Greeting, one caller turn, a farewell, and the loop stops on its own."""
    session = live_session(tmp_path, hears=("خدا حافظ",), talk="خدا حافظ۔", ends=True)

    vad_stream = await drive(session, [vad_event(VADEventType.END_OF_SPEECH)])

    assert session.transcript[0].said == session.language.greeting
    assert session.transcript[1].said == "خدا حافظ۔"
    assert session.finished
    assert session.result.outcome == "hung_up"
    assert vad_stream.closed


async def test_caller_frames_reach_both_the_vad_and_the_recognizer(tmp_path: Path) -> None:
    """One pump, two consumers. Silero decides when they stopped; the recognizer
    has been transcribing all along, which is why deciding costs nothing."""
    session = live_session(tmp_path, hears=("جی",), talk="جی۔", ends=True)

    vad_stream = await drive(session, [vad_event(VADEventType.END_OF_SPEECH)], caller_frames=5)

    assert len(vad_stream.frames) == 5
    assert len(session.stt.ears.heard) == 5 * FRAME_BYTES


async def test_the_caller_interrupting_stops_the_agent(tmp_path: Path) -> None:
    session = live_session(tmp_path, hears=("رکیں",), talk="ایک۔ دو۔ تین۔", ends=True)

    await drive(
        session,
        [
            vad_event(VADEventType.START_OF_SPEECH),
            vad_event(VADEventType.END_OF_SPEECH),
        ],
    )

    assert session.transcript[-1].heard == "رکیں"


async def test_the_greeting_is_not_cut_off_by_its_own_first_frames(tmp_path: Path) -> None:
    """Barge-in inside the grace window is our own turn coming back, not a
    caller. Cancelling on it makes every reply start a word or two in."""
    session = live_session(tmp_path, hears=("جی",), talk="جی۔", ends=True)

    await drive(
        session,
        [
            vad_event(VADEventType.START_OF_SPEECH),
            vad_event(VADEventType.END_OF_SPEECH),
        ],
    )

    assert session.transcript[0].said, "the greeting was cancelled before it played"
