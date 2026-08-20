"""Components 14 and 15.

The audio conversions and the token are testable without a LiveKit server, and
they are where the bugs live. Actually joining a room needs infrastructure and
a browser, so that is a manual step, not a unit test.
"""

from __future__ import annotations

import shutil
import subprocess
import wave
from pathlib import Path

import pytest

livekit = pytest.importorskip("livekit", reason="livekit not installed")

from voice_agent.config import settings  # noqa: E402
from voice_agent.transport import (  # noqa: E402
    VAD_SAMPLE_RATE,
    BrowserTransport,
    Transport,
    access_token,
    frames_to_wav,
    wav_to_frames,
)

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")

SECRET = "devsecret_change_me_in_production"


def make_wav(path: Path, seconds: float = 1.0, rate: int = 8000) -> Path:
    # fmt: off
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi",
            "-i", f"sine=frequency=440:duration={seconds}:sample_rate={rate}",
            "-ac", "1",
            str(path),
        ],
        check=True,
    )
    # fmt: on
    return path


def transport() -> BrowserTransport:
    return BrowserTransport("ws://localhost:7880", "devkey", SECRET, room="test-room")


# --- tokens ---


def test_a_token_is_issued_for_the_room() -> None:
    token = access_token("devkey", SECRET, "test-room", "agent")

    assert token.count(".") == 2, "not a JWT"


def test_agent_and_caller_get_different_identities() -> None:
    """Two participants with the same identity evict each other from the room."""
    agent = access_token("devkey", SECRET, "r", "agent")
    caller = access_token("devkey", SECRET, "r", "caller")

    assert agent != caller


def test_the_transport_hands_out_a_caller_token() -> None:
    """This is what the browser client needs to join."""
    assert transport().caller_token().count(".") == 2


# --- audio conversion, both directions ---


def test_a_wav_becomes_streamable_frames(tmp_path: Path) -> None:
    frames, rate = wav_to_frames(make_wav(tmp_path / "a.wav", seconds=1.0, rate=8000))

    assert rate == 8000
    assert len(frames) == pytest.approx(50, abs=2), "1 s at 20 ms should be ~50 frames"
    assert frames[0].sample_rate == 8000
    assert frames[0].num_channels == 1


def test_frames_carry_the_right_sample_count(tmp_path: Path) -> None:
    """A wrong samples_per_channel plays back at the wrong speed."""
    frames, _ = wav_to_frames(make_wav(tmp_path / "a.wav", rate=8000))

    assert frames[0].samples_per_channel == 160  # 20 ms at 8 kHz


def test_frames_round_trip_back_to_a_wav(tmp_path: Path) -> None:
    frames, rate = wav_to_frames(make_wav(tmp_path / "a.wav", seconds=1.0, rate=8000))

    out = frames_to_wav(frames, tmp_path / "back.wav", rate)

    with wave.open(str(out)) as handle:
        assert handle.getframerate() == 8000
        assert handle.getnchannels() == 1
        assert handle.getsampwidth() == 2
        assert handle.getnframes() == pytest.approx(8000, rel=0.05)


def test_frames_to_wav_creates_missing_directories(tmp_path: Path) -> None:
    frames, rate = wav_to_frames(make_wav(tmp_path / "a.wav"))

    out = frames_to_wav(frames, tmp_path / "nested" / "deeper" / "x.wav", rate)

    assert out.exists()


def test_the_whole_path_stays_at_telephone_rate() -> None:
    """A browser demo that sounds better than a phone call is a demo that lies.
    Silero only runs at 8 or 16 kHz, and 8 is what the line gives us."""
    assert VAD_SAMPLE_RATE == settings.audio.sample_rate == 8000


# --- shape ---


def test_browser_transport_satisfies_the_protocol() -> None:
    """If this breaks, swapping in SIP or PSTN stops being a config change."""
    assert isinstance(transport(), Transport)


def test_transport_never_imports_anything_urdu_specific() -> None:
    source = (Path(__file__).resolve().parents[1] / "src/voice_agent/transport.py").read_text()

    assert "lang.ur" not in source
    assert "lang/ur" not in source


def test_main_is_the_only_module_reading_the_environment() -> None:
    """Everything below main.py is handed what it needs. That is what makes
    the language switch and the transport swap work."""
    src = Path(__file__).resolve().parents[1] / "src/voice_agent"
    allowed = {"main.py", "config.py", "language.py"}

    offenders = [
        path.name
        for path in src.rglob("*.py")
        if path.name not in allowed
        and ("os.environ" in (t := path.read_text()) or "os.getenv" in t)
    ]

    assert offenders == [], f"these read the environment directly: {offenders}"
