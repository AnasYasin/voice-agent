"""Components 15, 16, 17. Transports.

A transport moves audio between the outside world and a Session. It knows
nothing about Urdu, the script, or what the call is for. Session knows nothing
about WebRTC or SIP. That split is why one session gives you a browser tab, a
softphone and a real phone line.

Only the browser transport (15) exists so far. SIP (16) and PSTN (17) reuse the
same LiveKit room from the other side, so they land here too.

Everything runs at 8 kHz end to end, including in the browser. A demo that
sounds better than the phone call it stands in for is a demo that lies.

Audio moves in both directions at once and neither side waits for the other.
Caller frames go to the session as they arrive, so the recognizer has heard
most of a sentence before it ends, and the agent's reply is played chunk by
chunk as it is synthesised. Stopping playback stops the synthesis behind it.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from contextlib import aclosing
from typing import Any, Protocol, runtime_checkable

from livekit import api, rtc
from livekit.agents.vad import VADEventType

from voice_agent.config import settings
from voice_agent.flow import Result
from voice_agent.session import Session, Speaking

log = logging.getLogger(__name__)

# Silero runs at 8 kHz or 16 kHz. The frames stay at the line rate, 8 kHz, and
# the model runs at 16 kHz with the plugin resampling in between. Its 8 kHz
# model went deaf on a real call on 18 Sep 2026: replayed offline, the speech
# probability dropped to exactly zero 19 s in and never came back, so the agent
# answered two turns and then nothing. The same recording at 16 kHz is detected
# throughout. scripts/replay_vad.py reproduces this on any call.wav.
VAD_SAMPLE_RATE = 8000
VAD_MODEL_SAMPLE_RATE = 16000
FRAME_MS = settings.audio.frame_ms

# How long after playback starts to ignore a speech-start event.
#
# The reply now begins while the caller's own last breath is still on the line,
# and the agent's voice comes back through an open laptop speaker. Either one
# reads as speech to the VAD, and cancelling on it makes the reply start a word
# or two in. Anything this close to the start is our own turn, not a caller.
BARGE_IN_GRACE_SECONDS = 0.35


@runtime_checkable
class Transport(Protocol):
    async def run(self, session: Session, **fields: Any) -> Result: ...


def access_token(api_key: str, api_secret: str, room: str, identity: str) -> str:
    """A join token for `room`. The browser client needs one of these too."""
    return (
        api.AccessToken(api_key, api_secret)
        .with_identity(identity)
        .with_name(identity)
        .with_grants(api.VideoGrants(room_join=True, room=room))
        .to_jwt()
    )


class BrowserTransport:
    """Component 15. Talk to the agent in a browser tab, over LiveKit."""

    name = "browser"

    def __init__(
        self,
        url: str,
        api_key: str,
        api_secret: str,
        room: str = "urdu-agent",
        identity: str = "agent",
    ) -> None:
        self.url = url
        self.api_key = api_key
        self.api_secret = api_secret
        self.room_name = room
        self.identity = identity

    def caller_token(self, identity: str = "caller") -> str:
        """Hand this to the browser client so it can join the same room."""
        return access_token(self.api_key, self.api_secret, self.room_name, identity)

    async def run(self, session: Session, **fields: Any) -> Result:
        from livekit.plugins import silero

        room = rtc.Room()
        source = rtc.AudioSource(settings.audio.sample_rate, 1)
        vad = silero.VAD.load(
            sample_rate=VAD_MODEL_SAMPLE_RATE,
            min_silence_duration=settings.audio.vad_silence_seconds,
        )

        try:
            await room.connect(
                self.url, access_token(self.api_key, self.api_secret, self.room_name, self.identity)
            )
            log.info("agent joined %s as %s", self.room_name, self.identity)

            track = rtc.LocalAudioTrack.create_audio_track("agent", source)
            await room.local_participant.publish_track(track)

            caller = await self._await_caller(room)

            # Opened once the caller is actually there. A recognizer held open
            # while nobody is on the line is billed for the silence.
            await session.open()
            try:
                await self._drive(session, caller, source, vad, **fields)
            finally:
                await session.close()
        finally:
            await room.disconnect()

        return session.result

    async def _await_caller(self, room: rtc.Room) -> rtc.Track:
        """Block until someone joins and starts sending audio."""
        arrived: asyncio.Future[rtc.Track] = asyncio.get_running_loop().create_future()

        def on_subscribed(track: rtc.Track, *_: Any) -> None:
            if track.kind == rtc.TrackKind.KIND_AUDIO and not arrived.done():
                arrived.set_result(track)

        room.on("track_subscribed", on_subscribed)

        for participant in room.remote_participants.values():
            for publication in participant.track_publications.values():
                if publication.track and publication.track.kind == rtc.TrackKind.KIND_AUDIO:
                    # Says out loud which participant we picked up. Silence here
                    # used to mean a tab left open from an earlier run, and the
                    # call looked hung with nothing in the log to explain it.
                    log.info("caller %r was already in the room", participant.identity)
                    return publication.track

        log.info("waiting for a caller to join %s", self.room_name)
        return await arrived

    async def _drive(
        self,
        session: Session,
        track: rtc.Track,
        source: rtc.AudioSource,
        vad: Any,
        **fields: Any,
    ) -> None:
        """Greet, then answer each utterance until the call ends.

        Playback runs as a task rather than inline so the caller can interrupt.
        A person who has to wait for a recording to finish before speaking is
        talking to a machine, and knows it.
        """
        audio = rtc.AudioStream(track, sample_rate=VAD_SAMPLE_RATE, num_channels=1)
        vad_stream = vad.stream()
        pump = asyncio.create_task(self._pump(audio, vad_stream, session))
        hung_up = asyncio.Event()
        clock = asyncio.get_running_loop().time

        speaking = self._say(source, session.greet(**fields), session, hung_up)
        started_speaking = clock()
        events = vad_stream.__aiter__()
        # Diagnosis, 18 Sep 2026: two real calls went deaf mid-call. This says,
        # every five seconds, whether the detector is still running inference
        # and what the highest speech probability it saw was, so a dead
        # detector and a detector that hears no speech look different in the log.
        window_started = clock()
        inferences = 0
        loudest = 0.0

        try:
            while True:
                event = await self._next(events, hung_up)
                if event is None:
                    break
                if event.type == VADEventType.INFERENCE_DONE:
                    inferences += 1
                    loudest = max(loudest, event.probability)
                    if clock() - window_started >= 5:
                        log.info(
                            "vad 5s: %d inferences, max speech probability %.2f, speaking=%s",
                            inferences,
                            loudest,
                            event.speaking,
                        )
                        window_started, inferences, loudest = clock(), 0, 0.0
                else:
                    log.debug("vad %s", event.type)

                if event.type == VADEventType.START_OF_SPEECH:
                    # Barge-in. They started talking, so stop talking over them.
                    if speaking.done():
                        continue
                    if clock() - started_speaking < BARGE_IN_GRACE_SECONDS:
                        continue  # the tail of our own turn, not a caller
                    log.info("caller interrupted, stopping playback")
                    speaking.cancel()
                    continue

                if event.type != VADEventType.END_OF_SPEECH:
                    continue

                # The audio is already at the recognizer, pushed frame by frame
                # by the pump, so this commits a turn rather than uploading one.
                speaking.cancel()
                speaking = self._say(source, await session.answer(), session, hung_up)
                started_speaking = clock()

                if session.finished:
                    await asyncio.shield(speaking)
                    break
        finally:
            speaking.cancel()
            pump.cancel()
            await vad_stream.aclose()

    async def _next(self, events: Any, hung_up: asyncio.Event) -> Any | None:
        """The next VAD event, or None once the call is over.

        Waiting on the caller is not enough on its own. In talk mode a farewell
        is only known to be a farewell after the model has finished writing it,
        which is while the agent is already speaking it, so the call can end
        with nobody about to say anything. The caller dropping the line ends it
        the other way, by running the event stream out.
        """
        speech = asyncio.ensure_future(events.__anext__())
        ended = asyncio.ensure_future(hung_up.wait())
        done, _ = await asyncio.wait({speech, ended}, return_when=asyncio.FIRST_COMPLETED)
        ended.cancel()

        if speech not in done:
            speech.cancel()
            return None
        try:
            return speech.result()
        except StopAsyncIteration:
            return None

    def _say(
        self, source: rtc.AudioSource, speaking: Speaking, session: Session, hung_up: asyncio.Event
    ) -> asyncio.Task[None]:
        """Start playback in the background so the caller can interrupt it."""

        async def play() -> None:
            await self._play(source, speaking, session.played)
            if session.finished:
                hung_up.set()

        return asyncio.create_task(play())

    async def _pump(self, audio: rtc.AudioStream, vad_stream: Any, session: Session) -> None:
        """Every caller frame, to the two things that need it.

        Silero decides when they stopped talking. The session's recognizer is
        transcribing meanwhile, which is why deciding costs nothing extra.
        """
        async for event in audio:
            vad_stream.push_frame(event.frame)
            await session.listen(bytes(event.frame.data))

    async def _play(
        self, source: rtc.AudioSource, speaking: Speaking, played: Callable[[bytes], None]
    ) -> None:
        """Stream the agent's voice into the room, paced to the clock.

        Every frame that goes out is also handed to `played`, which is how the
        session records the agent's side of the call in step with the caller's.

        Pacing matters for more than smoothness. Pushing frames as fast as
        LiveKit accepts them means playback finishes in the agent's mind long
        before the caller has heard it, so their reply arrives against the wrong
        turn, and cancelling playback cancels audio that already went out.

        Chunks arrive at whatever size the voice produces them, so they are
        recut to frames here. Falling behind resets the clock rather than
        catching up in a burst: a gap while the model writes the next sentence
        is a gap the caller already heard, and replaying it faster does not
        give it back.
        """
        step = settings.audio.sample_rate * FRAME_MS // 1000 * 2
        frame_seconds = FRAME_MS / 1000
        clock = asyncio.get_running_loop().time
        deadline = clock()
        pending = b""
        sent = 0

        async def send(frame: bytes) -> None:
            nonlocal deadline
            await source.capture_frame(
                rtc.AudioFrame(
                    data=frame,
                    sample_rate=settings.audio.sample_rate,
                    num_channels=1,
                    samples_per_channel=len(frame) // 2,
                )
            )
            played(frame)
            deadline += frame_seconds
            ahead = deadline - clock()
            if ahead > 0:
                await asyncio.sleep(ahead)
            else:
                deadline = clock()

        try:
            async with aclosing(speaking.chunks) as chunks:
                async for chunk in chunks:
                    pending += chunk
                    while len(pending) >= step:
                        await send(pending[:step])
                        pending = pending[step:]
                        sent += 1

            if pending:
                await send(pending.ljust(step, b"\x00"))
                sent += 1
        except asyncio.CancelledError:
            log.info("[%s] playback cut short after %d frames", speaking.state, sent)
            raise
