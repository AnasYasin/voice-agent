"""Component 1. Needs ffmpeg, no API keys.

Test audio is generated on the fly, so there are no sample files to commit.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from voice_agent.audio import SAMPLE_RATE, probe, to_telephone

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


def make_tone(path: Path, hz: int, seconds: float = 2.0) -> Path:
    """A 48 kHz stereo tone, like a browser or laptop mic would produce."""
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi",
            "-i", f"sine=frequency={hz}:duration={seconds}:sample_rate=48000",
            "-ac", "2",
            str(path),
        ],
        check=True,
    )
    return path


def mean_volume(path: Path) -> float:
    """Average loudness in dB, used to prove the band-pass actually filters."""
    result = subprocess.run(
        ["ffmpeg", "-i", str(path), "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True,
        text=True,
        check=True,
    )
    for line in result.stderr.splitlines():
        if "mean_volume:" in line:
            return float(line.split("mean_volume:")[1].split("dB")[0])
    raise AssertionError("ffmpeg reported no mean_volume")


def test_output_is_telephone_quality(tmp_path: Path) -> None:
    src = make_tone(tmp_path / "wideband.wav", hz=1000)
    assert probe(src).sample_rate == 48000

    info = probe(to_telephone(src, tmp_path / "phone.wav"))

    assert info.sample_rate == SAMPLE_RATE
    assert info.channels == 1


def test_duration_is_preserved(tmp_path: Path) -> None:
    src = make_tone(tmp_path / "wideband.wav", hz=1000, seconds=2.0)

    info = probe(to_telephone(src, tmp_path / "phone.wav"))

    assert info.duration == pytest.approx(2.0, abs=0.1)


def test_frequencies_above_the_band_are_removed(tmp_path: Path) -> None:
    """The point of the component. A 5 kHz tone is outside the telephone band
    and must come out far quieter than one at 1 kHz, which is inside it."""
    inside = to_telephone(make_tone(tmp_path / "in.wav", hz=1000), tmp_path / "in_phone.wav")
    outside = to_telephone(make_tone(tmp_path / "out.wav", hz=5000), tmp_path / "out_phone.wav")

    assert mean_volume(outside) < mean_volume(inside) - 20


def test_creates_missing_parent_directory(tmp_path: Path) -> None:
    src = make_tone(tmp_path / "wideband.wav", hz=1000)

    dst = to_telephone(src, tmp_path / "nested" / "deeper" / "phone.wav")

    assert dst.exists()


def test_missing_source_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        to_telephone(tmp_path / "nope.wav", tmp_path / "out.wav")


def test_probe_on_file_without_audio_raises_readable_error(tmp_path: Path) -> None:
    """Regression. This used to surface as a bare IndexError."""
    video_only = tmp_path / "blank.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", "color=c=black:s=64x64:d=1",
            str(video_only),
        ],
        check=True,
    )

    with pytest.raises(RuntimeError, match="no audio stream"):
        probe(video_only)


def test_corrupt_source_raises_readable_error(tmp_path: Path) -> None:
    src = tmp_path / "broken.wav"
    src.write_bytes(b"this is not audio")

    with pytest.raises(RuntimeError, match="ffmpeg failed"):
        to_telephone(src, tmp_path / "out.wav")
