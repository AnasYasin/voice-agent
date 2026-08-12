"""Settings loading. Pure logic, no network, no keys."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest
import yaml

from voice_agent.config import DEFAULTS_PATH, Settings, load, settings


def write_config(path: Path, overrides: dict) -> Path:
    """Copy the committed defaults, apply overrides, write it out."""
    data = yaml.safe_load(DEFAULTS_PATH.read_text(encoding="utf-8"))
    for section, values in overrides.items():
        data[section].update(values)
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def test_defaults_file_is_shipped_inside_the_package() -> None:
    """If this fails, an installed wheel loses its settings."""
    assert DEFAULTS_PATH.exists()
    assert DEFAULTS_PATH.parent.name == "voice_agent"


def test_defaults_load() -> None:
    assert isinstance(settings, Settings)
    assert settings.audio.sample_rate == 8000
    assert settings.audio.band_low_hz == 300
    assert settings.audio.band_high_hz == 3400


def test_band_is_inside_nyquist() -> None:
    """A band edge above half the sample rate cannot survive the resample, so
    the configured values would be quietly meaningless."""
    assert settings.audio.band_high_hz < settings.audio.sample_rate / 2
    assert settings.audio.band_low_hz < settings.audio.band_high_hz


def test_an_override_file_is_read(tmp_path: Path) -> None:
    path = write_config(tmp_path / "wideband.yaml", {"audio": {"sample_rate": 16000}})

    loaded = load(path)

    assert loaded.audio.sample_rate == 16000
    assert loaded.audio.band_low_hz == settings.audio.band_low_hz


def test_a_missing_key_raises_rather_than_defaulting(tmp_path: Path) -> None:
    """A silently defaulted band edge would invalidate every accuracy number."""
    data = yaml.safe_load(DEFAULTS_PATH.read_text(encoding="utf-8"))
    del data["audio"]["band_high_hz"]
    path = tmp_path / "broken.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")

    with pytest.raises(TypeError, match="band_high_hz"):
        load(path)


def test_an_unknown_key_raises(tmp_path: Path) -> None:
    """Catches a typo instead of ignoring it."""
    path = write_config(tmp_path / "typo.yaml", {"audio": {"band_hi_hz": 3400}})

    with pytest.raises(TypeError, match="band_hi_hz"):
        load(path)


def test_a_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load(tmp_path / "nope.yaml")


def test_settings_are_immutable() -> None:
    """Nothing may retune the band at runtime and invalidate a comparison."""
    with pytest.raises(FrozenInstanceError):
        settings.audio.sample_rate = 16000  # type: ignore[misc]


def test_a_config_can_be_varied_without_touching_the_committed_file() -> None:
    """How an experiment overrides one value, as documented in audio.py."""
    mulaw = replace(settings.audio, codec="pcm_mulaw")

    assert mulaw.codec == "pcm_mulaw"
    assert settings.audio.codec == "pcm_s16le"
