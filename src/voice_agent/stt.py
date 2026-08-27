"""Component 2. Speech to text.

Two providers behind one interface, so switching is a config change and the
accuracy gate can score them on identical audio.

ElevenLabs Scribe has the best published Urdu accuracy. Deepgram Nova-3 costs
about the same and supports keyterm prompting, which may win on this
particular script because the set of likely replies is small. Which one is
actually better on 8 kHz Pakistani phone audio is what the gate measures.

There is deliberately no failover between them. If a provider errors, that
error should surface.

Files and streams are separate interfaces, because they are separate jobs. A
file is what the accuracy gate scores: same audio, same conditions, two
providers, one number. A stream is what a live call needs, where the caller is
still talking and waiting for the recording to finish is most of the delay they
hear. Only ElevenLabs streams here; scoring is what Deepgram is in the project
for.
"""

from __future__ import annotations

import asyncio
import base64
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from voice_agent.config import settings

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Transcript:
    """Keep the two confidence numbers apart.

    `confidence` is how sure the provider is about the words. `language_confidence`
    is how sure it is about which language was spoken. They are different
    measurements, and putting one in the other's field makes a provider
    comparison meaningless. ElevenLabs reports only the second.
    """

    text: str
    provider: str
    confidence: float | None = None
    detected_language: str | None = None
    language_confidence: float | None = None


@runtime_checkable
class SpeechToText(Protocol):
    name: str

    def transcribe(self, path: Path, language: str) -> Transcript: ...


@runtime_checkable
class Listening(Protocol):
    """One open recognizer, for the length of one call."""

    async def open(self) -> None: ...
    async def push(self, pcm: bytes) -> None: ...
    async def commit(self) -> str: ...
    async def close(self) -> None: ...


@runtime_checkable
class StreamingSpeechToText(Protocol):
    name: str

    def stream(self, language: str) -> Listening: ...


class ElevenLabsSTT:
    name = "elevenlabs-scribe"

    def __init__(
        self,
        api_key: str,
        keyterms: list[str] | None = None,
        model_id: str | None = None,
        realtime_model_id: str | None = None,
    ) -> None:
        from elevenlabs.client import ElevenLabs

        self.api_key = api_key
        self.client = ElevenLabs(api_key=api_key)
        self.keyterms = keyterms or []
        self.model_id = model_id or settings.stt.elevenlabs_model
        self.realtime_model_id = realtime_model_id or settings.stt.elevenlabs_realtime_model

    def stream(self, language: str) -> ScribeStream:
        """A recognizer for one call. Open it once, push audio, commit a turn."""
        return ScribeStream(
            api_key=self.api_key,
            model_id=self.realtime_model_id,
            language=language,
            keyterms=self.keyterms,
        )

    def transcribe(self, path: Path, language: str) -> Transcript:
        log.info("[%s] transcribing %s", self.name, path.name)

        with path.open("rb") as audio:
            response = self.client.speech_to_text.convert(
                file=audio,
                model_id=self.model_id,
                language_code=language,
                keyterms=self.keyterms or None,
                tag_audio_events=False,
            )

        return Transcript(
            text=response.text.strip(),
            provider=self.name,
            confidence=None,  # Scribe does not report a transcription confidence
            detected_language=response.language_code,
            language_confidence=response.language_probability,
        )


class ScribeStream:
    """Scribe v2 realtime, over one websocket for the whole call.

    Audio goes up as the caller speaks, so the transcript is nearly finished by
    the time they stop. `commit` closes off a turn and returns what was heard,
    which is a round trip rather than an upload.

    Endpointing stays with the caller of this class. The server can do its own
    voice detection, but a live call already runs a local VAD to know when to
    stop talking over someone, and two of them disagreeing about where a turn
    ended is worse than either one alone.
    """

    name = "elevenlabs-scribe-realtime"

    def __init__(self, api_key: str, model_id: str, language: str, keyterms: list[str]) -> None:
        self.api_key = api_key
        self.model_id = model_id
        self.language = language
        self.keyterms = keyterms
        self._connection: Any = None
        self._turns: asyncio.Queue[str | Exception] = asyncio.Queue()

    async def open(self) -> None:
        from elevenlabs.client import AsyncElevenLabs
        from elevenlabs.realtime import AudioFormat, CommitStrategy, RealtimeEvents

        client = AsyncElevenLabs(api_key=self.api_key)
        log.info("[%s] opening for %s", self.name, self.language)

        self._connection = await client.speech_to_text.realtime.connect(
            {
                "model_id": self.model_id,
                "audio_format": AudioFormat(f"pcm_{settings.audio.sample_rate}"),
                "sample_rate": settings.audio.sample_rate,
                "commit_strategy": CommitStrategy.MANUAL,
                "language_code": self.language,
                "keyterms": self.keyterms or None,
            }
        )
        self._connection.on(RealtimeEvents.COMMITTED_TRANSCRIPT, self._heard)
        self._connection.on(RealtimeEvents.ERROR, self._failed)

    async def push(self, pcm: bytes) -> None:
        """Caller audio, as it arrives. Silence goes up too: gating on speech
        would mean deciding where a turn starts twice, and the first word is
        the one that gets lost when the two decisions differ."""
        await self._connection.send({"audio_base_64": base64.b64encode(pcm).decode()})

    async def commit(self) -> str:
        """End the turn and return what was said."""
        await self._connection.commit()
        heard = await self._turns.get()
        if isinstance(heard, Exception):
            raise heard
        return heard.strip()

    async def close(self) -> None:
        await self._connection.close()

    def _heard(self, data: dict[str, Any]) -> None:
        # The field is `text`. The SDK's own docstrings say `transcript`, which
        # is what the server used to send and no longer does.
        self._turns.put_nowait(data.get("text", ""))

    def _failed(self, data: dict[str, Any]) -> None:
        """The websocket answers a commit or it does not answer at all, so an
        error has to be handed to whoever is waiting rather than logged."""
        self._turns.put_nowait(RuntimeError(f"{self.name} failed: {data}"))


class DeepgramSTT:
    name = "deepgram-nova-3"

    def __init__(
        self, api_key: str, keyterms: list[str] | None = None, model: str | None = None
    ) -> None:
        from deepgram import DeepgramClient

        self.client = DeepgramClient(api_key=api_key)
        self.keyterms = keyterms or []
        self.model = model or settings.stt.deepgram_model

    def transcribe(self, path: Path, language: str) -> Transcript:
        log.info("[%s] transcribing %s", self.name, path.name)

        response = self.client.listen.v1.media.transcribe_file(
            request=path.read_bytes(),
            model=self.model,
            language=language,
            keyterm=self.keyterms or None,
            smart_format=True,
        )

        # Nested four deep, and an empty result gives a bare IndexError that
        # says nothing useful. This is the only place worth guarding.
        channels = response.results.channels if response.results else []
        if not channels or not channels[0].alternatives:
            raise RuntimeError(f"{self.name} returned no transcript for {path.name}")

        channel = channels[0]
        best = channel.alternatives[0]

        return Transcript(
            text=best.transcript.strip(),
            provider=self.name,
            confidence=best.confidence,
            detected_language=channel.detected_language,
        )


def build(provider: str, api_key: str, keyterms: list[str] | None = None) -> SpeechToText:
    """Pick a provider by name. Called by language.py and the eval harness.

    Both providers take the same keyterms. Giving them to only one would make
    the accuracy comparison meaningless.
    """
    if provider == "elevenlabs":
        return ElevenLabsSTT(api_key, keyterms)
    if provider == "deepgram":
        return DeepgramSTT(api_key, keyterms)
    raise ValueError(f"unknown STT provider {provider!r}, expected elevenlabs or deepgram")
