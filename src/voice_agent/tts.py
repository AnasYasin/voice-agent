"""Components 5 and 11. Azure Neural Urdu voice, plus the cache that replays
fixed script lines from disk instead of calling the API.

Synthesis produces clean wideband audio. It is deliberately not band-limited
here: `audio.to_telephone` is the single place that makes something sound like
a phone call, and keeping that a separate step means one cached WAV serves both
a browser demo and a real 8 kHz call.

Most lines in an appointment script are fixed. Only the name and the time
change, so the cache turns almost every call into a disk read.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

log = logging.getLogger(__name__)

DEFAULT_VOICE = "ur-PK-UzmaNeural"
SAMPLE_RATE = 24000


@dataclass(frozen=True)
class Speech:
    path: Path
    voice: str
    cached: bool


@runtime_checkable
class TextToSpeech(Protocol):
    name: str

    def synthesize(self, text: str, dst: Path) -> Speech: ...


class AzureTTS:
    name = "azure-neural"

    def __init__(self, api_key: str, region: str, voice: str = DEFAULT_VOICE) -> None:
        import azure.cognitiveservices.speech as speechsdk

        self._sdk = speechsdk
        self.voice = voice

        self.config = speechsdk.SpeechConfig(subscription=api_key, region=region)
        self.config.speech_synthesis_voice_name = voice
        self.config.set_speech_synthesis_output_format(
            speechsdk.SpeechSynthesisOutputFormat.Riff24Khz16BitMonoPcm
        )

    def synthesize(self, text: str, dst: Path) -> Speech:
        """Write `text` to `dst` as a mono 24 kHz WAV."""
        if not text.strip():
            raise ValueError("nothing to synthesize, text is empty")

        dst.parent.mkdir(parents=True, exist_ok=True)
        log.info("[%s] synthesizing %d chars as %s", self.name, len(text), self.voice)

        synthesizer = self._sdk.SpeechSynthesizer(
            speech_config=self.config,
            audio_config=self._sdk.audio.AudioOutputConfig(filename=str(dst)),
        )
        result = synthesizer.speak_text_async(text).get()

        # Azure reports refusals in the result rather than raising, so without
        # this check a bad key or an unknown voice silently leaves a 0-byte WAV.
        if result.reason != self._sdk.ResultReason.SynthesizingAudioCompleted:
            details = self._sdk.SpeechSynthesisCancellationDetails(result)
            raise RuntimeError(
                f"{self.name} did not synthesize: {details.reason}. {details.error_details}"
            )

        return Speech(path=dst, voice=self.voice, cached=False)


class CachedTTS:
    """Component 11. Wraps any TextToSpeech and keeps results on disk.

    The key covers the voice as well as the text, so switching to the male
    voice does not replay the female one.
    """

    def __init__(self, tts: TextToSpeech, cache_dir: Path) -> None:
        self.tts = tts
        self.name = f"cached-{tts.name}"
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def synthesize(self, text: str, dst: Path | None = None) -> Speech:
        voice = getattr(self.tts, "voice", self.tts.name)
        cached_path = self.cache_dir / f"{self._key(text, voice)}.wav"

        if cached_path.exists():
            log.info("[%s] hit %s", self.name, cached_path.name)
            return Speech(path=cached_path, voice=voice, cached=True)

        log.info("[%s] miss %s", self.name, cached_path.name)
        self.tts.synthesize(text, cached_path)
        return Speech(path=cached_path, voice=voice, cached=False)

    @staticmethod
    def _key(text: str, voice: str) -> str:
        digest = hashlib.sha256(f"{voice}\n{text}".encode()).hexdigest()
        return digest[:16]


def build(api_key: str, region: str, voice: str = DEFAULT_VOICE, cache_dir: Path | None = None):
    """Build the Azure voice, wrapped in the cache unless `cache_dir` is None."""
    tts = AzureTTS(api_key, region, voice)
    if cache_dir is None:
        return tts
    return CachedTTS(tts, cache_dir)
