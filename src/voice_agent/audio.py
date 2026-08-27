"""Component 1. Audio converter.

A real phone call is 8 kHz mono, band-limited to roughly 300-3400 Hz. Browser
and laptop-mic audio is 48 kHz wideband and sounds far better, which flatters
speech recognition and produces an accuracy number you cannot ship against.

Everything measured or demoed goes through here first, so what you hear is
what a real call will sound like.

Used in both directions: incoming caller audio before STT, and outgoing agent
audio after TTS.

Two ways in, for two shapes of work:

  to_telephone()  a finished file to a finished file. ffmpeg. This is what the
                  campaign path and every accuracy run use.
  Telephone()     one chunk at a time, for audio that does not exist yet. A
                  subprocess costs more than the band-limiting does, and there
                  is no finished file to hand it anyway.

Both apply the same band, so a streamed reply and a cached one sound alike.
"""

from __future__ import annotations

import json
import logging
import math
import subprocess
import wave
from array import array
from dataclasses import dataclass
from pathlib import Path

from voice_agent.config import AudioConfig, settings

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class AudioInfo:
    sample_rate: int
    channels: int
    duration: float


def to_telephone(src: Path, dst: Path, config: AudioConfig | None = None) -> Path:
    """Convert any audio file to mono telephone-band audio.

    Band-limiting runs before the resample, which is what stops frequencies
    above the new Nyquist folding back as aliases.

    To try a different band or codec, pass a modified config rather than
    editing defaults.yaml:

        replace(settings.audio, codec="pcm_mulaw")
    """
    config = config or settings.audio

    if not src.exists():
        raise FileNotFoundError(src)

    dst.parent.mkdir(parents=True, exist_ok=True)
    log.info("converting %s -> %s at %d Hz", src.name, dst.name, config.sample_rate)

    # Flags stay paired with their values, one pair per line.
    # fmt: off
    _run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-i", str(src),
            "-ac", "1",
            "-ar", str(config.sample_rate),
            "-af", f"highpass=f={config.band_low_hz},lowpass=f={config.band_high_hz}",
            "-c:a", config.codec,
            str(dst),
        ]
    )
    # fmt: on
    return dst


def probe(path: Path) -> AudioInfo:
    """Read sample rate, channel count and duration from an audio file."""
    # fmt: off
    output = _run(
        [
            "ffprobe", "-v", "error",
            "-select_streams", "a:0",
            "-show_entries", "stream=sample_rate,channels:format=duration",
            "-of", "json",
            str(path),
        ]
    )
    # fmt: on
    data = json.loads(output)
    if not data.get("streams"):
        raise RuntimeError(f"{path.name} has no audio stream")

    stream = data["streams"][0]

    info = AudioInfo(
        sample_rate=int(stream["sample_rate"]),
        channels=int(stream["channels"]),
        duration=float(data["format"]["duration"]),
    )
    log.debug("%s is %s", path.name, info)
    return info


def _run(args: list[str]) -> str:
    """Run ffmpeg or ffprobe.

    The only place in this module that catches anything. A missing binary and
    a rejected file both produce useless default errors, so both are turned
    into something readable. Everything else is left to raise.
    """
    try:
        result = subprocess.run(args, capture_output=True, text=True, check=True)
    except FileNotFoundError:
        raise RuntimeError(
            f"{args[0]} not found. Install ffmpeg: apt install ffmpeg / brew install ffmpeg"
        ) from None
    except subprocess.CalledProcessError as error:
        raise RuntimeError(f"{args[0]} failed:\n{error.stderr.strip()}") from None

    return result.stdout


class Telephone:
    """The streaming twin of `to_telephone`. Call it with 16-bit mono PCM.

    Two Butterworth biquads, high-pass then low-pass, carrying their filter
    state between chunks. State is what makes this a filter rather than a
    per-chunk effect: reset it between chunks and every boundary clicks.

    One instance per stream, never shared. Resampling is not done here, so the
    audio must already be at the configured rate.
    """

    def __init__(self, config: AudioConfig | None = None) -> None:
        config = config or settings.audio
        self._stages = (
            _highpass(config.band_low_hz, config.sample_rate),
            _lowpass(config.band_high_hz, config.sample_rate),
        )

    def __call__(self, pcm: bytes) -> bytes:
        samples = array("h")
        samples.frombytes(pcm)
        for stage in self._stages:
            stage.run(samples)
        return samples.tobytes()


class Tape:
    """A WAV written while it is still being recorded or played.

    Used by the live call path, where there is no finished file to convert and
    the audio has to reach the caller before it reaches the disk.
    """

    def __init__(self, path: Path, config: AudioConfig | None = None) -> None:
        self.path = path
        self._rate = (config or settings.audio).sample_rate
        path.parent.mkdir(parents=True, exist_ok=True)
        self._wave = wave.open(str(path), "wb")
        self._wave.setnchannels(1)
        self._wave.setsampwidth(2)  # s16
        self._wave.setframerate(self._rate)

    def write(self, pcm: bytes) -> None:
        self._wave.writeframes(pcm)

    def close(self) -> None:
        self._wave.close()

    def __enter__(self) -> Tape:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def pcm(path: Path) -> bytes:
    """The samples in a WAV, without the header. What a transport can play."""
    with wave.open(str(path), "rb") as source:
        return source.readframes(source.getnframes())


class _Biquad:
    """One second-order section, direct form II transposed."""

    __slots__ = ("a1", "a2", "b0", "b1", "b2", "z1", "z2")

    def __init__(self, b0: float, b1: float, b2: float, a1: float, a2: float) -> None:
        self.b0, self.b1, self.b2, self.a1, self.a2 = b0, b1, b2, a1, a2
        self.z1 = self.z2 = 0.0

    def run(self, samples: array) -> None:
        b0, b1, b2, a1, a2 = self.b0, self.b1, self.b2, self.a1, self.a2
        z1, z2 = self.z1, self.z2
        for index, sample in enumerate(samples):
            # The state carries the unrounded value. Feeding the clipped
            # integer back would let rounding error accumulate into a drift.
            out = b0 * sample + z1
            z1 = b1 * sample - a1 * out + z2
            z2 = b2 * sample - a2 * out
            samples[index] = -32768 if out < -32768 else 32767 if out > 32767 else int(out)
        self.z1, self.z2 = z1, z2


# 1/sqrt(2) is the Butterworth Q, the flattest passband a single section gives.
_Q = 2**-0.5


def _highpass(cutoff: int, rate: int) -> _Biquad:
    w0 = 2 * math.pi * cutoff / rate
    cosine, alpha = math.cos(w0), math.sin(w0) / (2 * _Q)
    a0 = 1 + alpha
    return _Biquad(
        b0=(1 + cosine) / 2 / a0,
        b1=-(1 + cosine) / a0,
        b2=(1 + cosine) / 2 / a0,
        a1=-2 * cosine / a0,
        a2=(1 - alpha) / a0,
    )


def _lowpass(cutoff: int, rate: int) -> _Biquad:
    w0 = 2 * math.pi * cutoff / rate
    cosine, alpha = math.cos(w0), math.sin(w0) / (2 * _Q)
    a0 = 1 + alpha
    return _Biquad(
        b0=(1 - cosine) / 2 / a0,
        b1=(1 - cosine) / a0,
        b2=(1 - cosine) / 2 / a0,
        a1=-2 * cosine / a0,
        a2=(1 - alpha) / a0,
    )
