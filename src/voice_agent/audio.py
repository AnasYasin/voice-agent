"""Component 1. Audio converter.

A real phone call is 8 kHz mono, band-limited to roughly 300-3400 Hz. Browser
and laptop-mic audio is 48 kHz wideband and sounds far better, which flatters
speech recognition and produces an accuracy number you cannot ship against.

Everything measured or demoed goes through here first, so what you hear is
what a real call will sound like.

Used in both directions: incoming caller audio before STT, and outgoing agent
audio after TTS.
"""

from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

SAMPLE_RATE = 8000
BAND_LOW_HZ = 300
BAND_HIGH_HZ = 3400


@dataclass(frozen=True)
class AudioInfo:
    sample_rate: int
    channels: int
    duration: float


def to_telephone(src: Path, dst: Path, codec: str = "pcm_s16le") -> Path:
    """Convert any audio file to 8 kHz mono telephone-band audio.

    `pcm_s16le` is 16-bit PCM, which every speech API accepts. Pass
    `pcm_mulaw` for a closer match to what a GSM gateway actually delivers.
    """
    if not src.exists():
        raise FileNotFoundError(src)

    dst.parent.mkdir(parents=True, exist_ok=True)
    log.info("converting %s -> %s", src.name, dst.name)

    _run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-i", str(src),
            "-ac", "1",
            "-ar", str(SAMPLE_RATE),
            "-af", f"highpass=f={BAND_LOW_HZ},lowpass=f={BAND_HIGH_HZ}",
            "-c:a", codec,
            str(dst),
        ]
    )
    return dst


def probe(path: Path) -> AudioInfo:
    """Read sample rate, channel count and duration from an audio file."""
    output = _run(
        [
            "ffprobe", "-v", "error",
            "-select_streams", "a:0",
            "-show_entries", "stream=sample_rate,channels:format=duration",
            "-of", "json",
            str(path),
        ]
    )
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
