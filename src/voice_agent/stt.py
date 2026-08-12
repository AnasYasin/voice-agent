"""Component 2. Speech to text.

Two providers behind one interface, so switching is a config change and the
accuracy gate can score them on identical audio.

ElevenLabs Scribe has the best published Urdu accuracy. Deepgram Nova-3 costs
about the same and supports keyterm prompting, which may win on this
particular script because the set of likely replies is small. Which one is
actually better on 8 kHz Pakistani phone audio is what the gate measures.

There is deliberately no failover between them. If a provider errors, that
error should surface.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

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


class ElevenLabsSTT:
    name = "elevenlabs-scribe"

    def __init__(
        self,
        api_key: str,
        keyterms: list[str] | None = None,
        model_id: str | None = None,
    ) -> None:
        from elevenlabs.client import ElevenLabs

        self.client = ElevenLabs(api_key=api_key)
        self.keyterms = keyterms or []
        self.model_id = model_id or settings.stt.elevenlabs_model

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
