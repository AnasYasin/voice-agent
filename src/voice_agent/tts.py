"""Components 5 and 11. Azure Neural Urdu voice, plus the cache that replays
fixed script lines from disk instead of calling the API.

Synthesis produces clean wideband audio. It is deliberately not band-limited
here: `audio.to_telephone` is the single place that makes something sound like
a phone call, and keeping that a separate step means one cached WAV serves both
a browser demo and a real 8 kHz call.

Most lines in an appointment script are fixed. Only the name and the time
change, so the cache turns almost every call into a disk read.

Two ways out, and which one you want follows from whether the text is known
in advance:

  synthesize()  a whole WAV, cached. Fixed script lines. The file already
                exists on the second call, so there is nothing to wait for.
  stream()      raw PCM as it is produced, uncached. Model prose, which is
                different every turn. Playback starts on the first chunk
                instead of on the last one.

Streaming synthesises a sentence at a time, so the voice cannot carry
intonation across a sentence boundary the way it does over a whole paragraph.
That is the price of not making the caller wait for the paragraph.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable
from xml.sax.saxutils import escape

from voice_agent.config import settings

log = logging.getLogger(__name__)

# Azure names its WAV formats rather than taking a rate, so the rate in
# defaults.yaml has to map onto one. Only these three are useful here.
_AZURE_FORMATS = {
    8000: "Riff8Khz16BitMonoPcm",
    16000: "Riff16Khz16BitMonoPcm",
    24000: "Riff24Khz16BitMonoPcm",
}

# Streaming skips the RIFF header, because there is no file and the first bytes
# have to be playable samples. It also comes back at the line's rate rather than
# wideband: nothing downstream is going to cache it, so there is no second use
# to keep the extra bandwidth for.
_AZURE_RAW_FORMATS = {
    8000: "Raw8Khz16BitMonoPcm",
    16000: "Raw16Khz16BitMonoPcm",
    24000: "Raw24Khz16BitMonoPcm",
}


@dataclass(frozen=True)
class Speech:
    path: Path
    voice: str
    cached: bool


@runtime_checkable
class TextToSpeech(Protocol):
    name: str

    def synthesize(self, text: str, dst: Path) -> Speech: ...


@runtime_checkable
class StreamingTextToSpeech(Protocol):
    """Chunks of raw PCM at the line's sample rate, as they are produced."""

    name: str

    def stream(self, text: str) -> AsyncIterator[bytes]: ...


class AzureTTS:
    name = "azure-neural"

    def __init__(
        self,
        api_key: str,
        region: str,
        voice: str,
        sample_rate: int | None = None,
        locale: str | None = None,
    ) -> None:
        """`voice` is required and comes from lang/<code>/, never from here.
        A default would put the Urdu voice name in a component that is supposed
        to be language-agnostic, and give the project two places to change it.

        `locale` is optional and also comes from lang/<code>/. Set it for a
        multilingual or HD voice, which reads bare text in whatever language it
        decides the text is. It guesses per request, so the same line can come
        back in Urdu twice and open with an English word on the third try.
        With a locale set, the text goes out as SSML pinned to that language.
        A single-language voice such as ur-PK-UzmaNeural has nothing to guess,
        so leaving this None keeps it on the cheaper plain-text call.
        """
        import azure.cognitiveservices.speech as speechsdk

        sample_rate = sample_rate or settings.tts.sample_rate
        stream_rate = settings.audio.sample_rate
        for rate, formats in ((sample_rate, _AZURE_FORMATS), (stream_rate, _AZURE_RAW_FORMATS)):
            if rate not in formats:
                raise ValueError(
                    f"no Azure format for {rate} Hz, expected one of {sorted(formats)}"
                )

        self._sdk = speechsdk
        self.voice = voice
        self.locale = locale
        self.sample_rate = sample_rate
        self.stream_rate = stream_rate
        self._chunk_bytes = stream_rate * settings.audio.frame_ms // 1000 * 2

        self.config = self._speech_config(api_key, region, _AZURE_FORMATS[sample_rate])
        self.stream_config = self._speech_config(api_key, region, _AZURE_RAW_FORMATS[stream_rate])

    def _speech_config(self, api_key: str, region: str, output_format: str) -> Any:
        config = self._sdk.SpeechConfig(subscription=api_key, region=region)
        config.speech_synthesis_voice_name = self.voice
        config.set_speech_synthesis_output_format(
            getattr(self._sdk.SpeechSynthesisOutputFormat, output_format)
        )
        return config

    def _ssml(self, text: str) -> str:
        """Wrap `text` so the voice speaks `self.locale` and not its own.

        The <lang> element is what pins it. Setting xml:lang on <speak> alone
        is not enough, because a multilingual voice treats that as the document
        language and still picks the speaking language off the text.
        """
        return (
            "<speak version='1.0' "
            "xmlns='http://www.w3.org/2001/10/synthesis' "
            f"xml:lang='{self.locale}'>"
            f"<voice name='{self.voice}'>"
            f"<lang xml:lang='{self.locale}'>{escape(text)}</lang>"
            "</voice></speak>"
        )

    def synthesize(self, text: str, dst: Path) -> Speech:
        """Write `text` to `dst` as a mono WAV at the configured rate."""
        if not text.strip():
            raise ValueError("nothing to synthesize, text is empty")

        dst.parent.mkdir(parents=True, exist_ok=True)
        log.info("[%s] synthesizing %d chars as %s", self.name, len(text), self.voice)

        synthesizer = self._sdk.SpeechSynthesizer(
            speech_config=self.config,
            audio_config=self._sdk.audio.AudioOutputConfig(filename=str(dst)),
        )
        if self.locale:
            result = synthesizer.speak_ssml_async(self._ssml(text)).get()
        else:
            result = synthesizer.speak_text_async(text).get()

        # Azure reports refusals in the result rather than raising, so without
        # this check a bad key or an unknown voice silently leaves a 0-byte WAV.
        if result.reason != self._sdk.ResultReason.SynthesizingAudioCompleted:
            details = self._sdk.SpeechSynthesisCancellationDetails(result)
            raise RuntimeError(
                f"{self.name} did not synthesize: {details.reason}. {details.error_details}"
            )

        return Speech(path=dst, voice=self.voice, cached=False)

    async def stream(self, text: str) -> AsyncIterator[bytes]:
        """Raw PCM, a frame at a time, while Azure is still synthesising.

        `start_speaking_text_async` returns as soon as the first audio exists
        rather than when the last does, which is the whole difference between
        a caller waiting for a sentence and a caller hearing one.

        Closing the iterator early stops the synthesis. Barge-in happens on
        most turns of a real conversation, and the audio nobody heard is still
        billed and still occupies the connection.
        """
        if not text.strip():
            raise ValueError("nothing to synthesize, text is empty")

        log.info("[%s] streaming %d chars as %s", self.name, len(text), self.voice)

        # audio_config=None means "give me the bytes". Leaving it out sends the
        # reply to the machine's speakers instead.
        synthesizer = self._sdk.SpeechSynthesizer(
            speech_config=self.stream_config, audio_config=None
        )
        if self.locale:
            begin = synthesizer.start_speaking_ssml_async(self._ssml(text))
        else:
            begin = synthesizer.start_speaking_text_async(text)
        result = await asyncio.to_thread(begin.get)
        audio = self._sdk.AudioDataStream(result)
        buffer = bytes(self._chunk_bytes)

        try:
            while filled := await asyncio.to_thread(audio.read_data, buffer):
                yield buffer[:filled]

            # A refusal arrives here rather than at the call above, which always
            # says synthesis started. Without this, a bad key or an unknown voice
            # is a reply the caller simply could not hear.
            if audio.status == self._sdk.StreamStatus.Canceled:
                details = audio.cancellation_details
                raise RuntimeError(
                    f"{self.name} did not synthesize: {details.reason}. {details.error_details}"
                )
        finally:
            synthesizer.stop_speaking_async()


class CachedTTS:
    """Component 11. Wraps any TextToSpeech and keeps results on disk.

    The key covers the voice and its locale as well as the text, so switching
    to the male voice does not replay the female one, and pinning a
    multilingual voice to a new language does not replay the old one.
    """

    def __init__(self, tts: TextToSpeech, cache_dir: Path) -> None:
        self.tts = tts
        self.name = f"cached-{tts.name}"
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def synthesize(self, text: str, dst: Path | None = None) -> Speech:
        voice = getattr(self.tts, "voice", self.tts.name)
        locale = getattr(self.tts, "locale", None) or ""
        cached_path = self.cache_dir / f"{self._key(text, f'{voice}|{locale}')}.wav"

        if cached_path.exists():
            log.info("[%s] hit %s", self.name, cached_path.name)
            return Speech(path=cached_path, voice=voice, cached=True)

        log.info("[%s] miss %s", self.name, cached_path.name)
        self.tts.synthesize(text, cached_path)
        return Speech(path=cached_path, voice=voice, cached=False)

    def stream(self, text: str) -> AsyncIterator[bytes]:
        """Straight through, deliberately.

        The cache pays off on text that repeats, and streaming exists for the
        text that never does. Caching a sentence the model wrote once would
        fill the disk with files that are never read a second time.
        """
        return self.tts.stream(text)

    @staticmethod
    def _key(text: str, voice: str) -> str:
        digest = hashlib.sha256(f"{voice}\n{text}".encode()).hexdigest()
        return digest[:16]


def build(
    api_key: str,
    region: str,
    voice: str,
    cache_dir: Path | None = None,
    locale: str | None = None,
):
    """Build the Azure voice, wrapped in the cache unless `cache_dir` is None."""
    tts = AzureTTS(api_key, region, voice, locale=locale)
    if cache_dir is None:
        return tts
    return CachedTTS(tts, cache_dir)
