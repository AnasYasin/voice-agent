"""Component 1. Needs ffmpeg, no API keys.

Test audio is generated on the fly, so there are no sample files to commit.
"""

from __future__ import annotations

import math
import shutil
import subprocess
import wave
from array import array
from pathlib import Path

import pytest

from voice_agent.audio import Tape, Telephone, pcm, probe, to_telephone
from voice_agent.config import settings

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


def make_tone(path: Path, hz: int, seconds: float = 2.0) -> Path:
    """A 48 kHz stereo tone, like a browser or laptop mic would produce."""
    # fmt: off
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
    # fmt: on
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

    assert info.sample_rate == settings.audio.sample_rate
    assert info.channels == 1


def test_duration_is_preserved(tmp_path: Path) -> None:
    src = make_tone(tmp_path / "wideband.wav", hz=1000, seconds=2.0)

    info = probe(to_telephone(src, tmp_path / "phone.wav"))

    assert info.duration == pytest.approx(2.0, abs=0.1)


def test_frequencies_above_the_band_are_removed(tmp_path: Path) -> None:
    """The point of the component. Both tones are derived from the configured
    band, so changing defaults.yaml cannot leave this test quietly asserting
    the wrong thing."""
    band = settings.audio
    inside_hz = (band.band_low_hz + band.band_high_hz) // 2
    outside_hz = band.sample_rate  # above the band and above Nyquist

    inside = to_telephone(make_tone(tmp_path / "in.wav", hz=inside_hz), tmp_path / "in_phone.wav")
    outside = to_telephone(
        make_tone(tmp_path / "out.wav", hz=outside_hz), tmp_path / "out_phone.wav"
    )

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
    # fmt: off
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi",
            "-i", "color=c=black:s=64x64:d=1",
            str(video_only),
        ],
        check=True,
    )
    # fmt: on

    with pytest.raises(RuntimeError, match="no audio stream"):
        probe(video_only)


def test_corrupt_source_raises_readable_error(tmp_path: Path) -> None:
    src = tmp_path / "broken.wav"
    src.write_bytes(b"this is not audio")

    with pytest.raises(RuntimeError, match="ffmpeg failed"):
        to_telephone(src, tmp_path / "out.wav")


# --- the streaming filter ---
#
# Pure Python, no subprocess and no file. It has to land on the same band as
# to_telephone, or a caller hears the agent change voice between a cached
# script line and an answer it is making up.


def tone(hz: int, seconds: float = 0.5, rate: int | None = None) -> bytes:
    """A pure tone as 16-bit PCM, the shape a synthesiser hands back."""
    rate = rate or settings.audio.sample_rate
    samples = array(
        "h",
        (int(12000 * math.sin(2 * math.pi * hz * n / rate)) for n in range(int(rate * seconds))),
    )
    return samples.tobytes()


def loudness(pcm: bytes) -> float:
    """Root mean square of a PCM buffer. Not dB, just something to compare."""
    samples = array("h")
    samples.frombytes(pcm)
    return (sum(float(s) * s for s in samples) / len(samples)) ** 0.5


def test_a_tone_inside_the_band_survives() -> None:
    passed = Telephone()(tone(1000))

    assert loudness(passed) > 0.7 * loudness(tone(1000))


def test_a_tone_below_the_band_is_cut() -> None:
    """The low rumble a phone line does not carry."""
    passed = Telephone()(tone(80))

    assert loudness(passed) < 0.3 * loudness(tone(80))


def test_the_filter_carries_its_state_between_chunks() -> None:
    """Reset it per chunk and every chunk boundary clicks, which is exactly
    what a streamed reply is made of."""
    whole = tone(1000)
    step = len(whole) // 8

    chunked = Telephone()
    piecewise = b"".join(chunked(whole[at : at + step]) for at in range(0, len(whole), step))

    assert piecewise == Telephone()(whole)


def test_filtering_does_not_change_the_length() -> None:
    """A filter that dropped or added samples would drift the playback clock."""
    pcm = tone(1000)

    assert len(Telephone()(pcm)) == len(pcm)


# --- recording while playing ---


def test_a_tape_is_a_readable_wav(tmp_path: Path) -> None:
    with Tape(tmp_path / "nested" / "said.wav") as tape:
        tape.write(tone(440, seconds=0.25))

    with wave.open(str(tmp_path / "nested" / "said.wav")) as handle:
        assert handle.getnchannels() == 1
        assert handle.getsampwidth() == 2
        assert handle.getframerate() == settings.audio.sample_rate
        assert handle.getnframes() == int(settings.audio.sample_rate * 0.25)


def test_pcm_reads_back_what_the_tape_wrote(tmp_path: Path) -> None:
    written = tone(440, seconds=0.25)
    with Tape(tmp_path / "said.wav") as tape:
        tape.write(written)

    assert pcm(tmp_path / "said.wav") == written


def test_a_stereo_tape_interleaves_left_and_right(tmp_path: Path) -> None:
    """Caller left, agent right, sample by sample. The shorter side is padded
    with silence so a frame with nobody speaking on one side still lines up."""
    import wave
    from array import array

    from voice_agent.audio import Tape

    with Tape(tmp_path / "call.wav", channels=2) as tape:
        tape.write_stereo(array("h", [1, 2, 3]).tobytes(), array("h", [9]).tobytes())

    with wave.open(str(tmp_path / "call.wav")) as recorded:
        assert recorded.getnchannels() == 2
        samples = array("h", recorded.readframes(recorded.getnframes()))

    assert list(samples) == [1, 9, 2, 0, 3, 0]
