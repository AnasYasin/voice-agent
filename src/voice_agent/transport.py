"""Components 15, 16, 17. Transports.

A transport moves audio between the outside world and a Session. It knows
nothing about Urdu, the script, or what the call is for. Session knows nothing
about WebRTC or SIP. That split is why one session gives you a browser tab, a
softphone and a real phone line.

Only the browser transport (15) exists so far. SIP (16) and PSTN (17) reuse the
same LiveKit room from the other side, so they land here too.

Everything runs at 8 kHz end to end, including in the browser. A demo that
sounds better than the phone call it stands in for is a demo that lies.
"""

from __future__ import annotations

import asyncio
import logging
import wave
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from livekit import api, rtc
from livekit.agents.vad import VADEventType

from voice_agent.config import settings
from voice_agent.flow import Result
from voice_agent.session import Session

log = logging.getLogger(__name__)

# Silero only runs at 8 kHz or 16 kHz. 8 is what a phone line gives us anyway.
VAD_SAMPLE_RATE = 8000
FRAME_MS = 20


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


def frames_to_wav(frames: list[rtc.AudioFrame], dst: Path, sample_rate: int) -> Path:
    """Buffered speech to a file the STT client can post."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(dst), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)  # s16
        out.setframerate(sample_rate)
        for frame in frames:
            out.writeframes(bytes(frame.data))
    return dst


def wav_to_frames(path: Path, frame_ms: int = FRAME_MS) -> tuple[list[rtc.AudioFrame], int]:
    """A WAV split into frames small enough to stream without stuttering."""
    with wave.open(str(path), "rb") as source:
        rate = source.getframerate()
        channels = source.getnchannels()
        pcm = source.readframes(source.getnframes())

    per_frame = int(rate * frame_ms / 1000)
    step = per_frame * channels * 2
    frames = [
        rtc.AudioFrame(
            data=pcm[offset : offset + step],
            sample_rate=rate,
            num_channels=channels,
            samples_per_channel=len(pcm[offset : offset + step]) // (channels * 2),
        )
        for offset in range(0, len(pcm) - step + 1, step)
    ]
    return frames, rate


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
        vad = silero.VAD.load(sample_rate=VAD_SAMPLE_RATE, min_silence_duration=0.8)

        try:
            await room.connect(
                self.url, access_token(self.api_key, self.api_secret, self.room_name, self.identity)
            )
            log.info("agent joined %s as %s", self.room_name, self.identity)

            track = rtc.LocalAudioTrack.create_audio_track("agent", source)
            await room.local_participant.publish_track(track)

            caller = await self._await_caller(room)
            await self._drive(session, caller, source, vad, **fields)
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
        """Greet, then answer each utterance until the script ends."""
        await self._play(source, session.start(**fields).audio)

        audio = rtc.AudioStream(track, sample_rate=VAD_SAMPLE_RATE, num_channels=1)
        vad_stream = vad.stream()
        pump = asyncio.create_task(self._pump(audio, vad_stream))

        try:
            async for event in vad_stream:
                if event.type != VADEventType.END_OF_SPEECH:
                    continue

                heard = frames_to_wav(
                    event.frames,
                    session.work_dir / f"caller_{len(session.transcript):02d}.wav",
                    VAD_SAMPLE_RATE,
                )
                reply = session.hear(heard)
                await self._play(source, reply.audio)

                if session.finished:
                    break
        finally:
            pump.cancel()
            await vad_stream.aclose()

    async def _pump(self, audio: rtc.AudioStream, vad_stream: Any) -> None:
        async for event in audio:
            vad_stream.push_frame(event.frame)

    async def _play(self, source: rtc.AudioSource, path: Path) -> None:
        """Stream a WAV into the room in real time."""
        frames, rate = wav_to_frames(path)
        log.info("playing %s (%d frames at %d Hz)", path.name, len(frames), rate)

        for frame in frames:
            await source.capture_frame(frame)
