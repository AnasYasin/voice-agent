"""Settings. Nothing else in the package opens defaults.yaml.

Two sources, kept apart on purpose:

  defaults.yaml   tunable parameters. Committed, identical on every machine,
                  reviewable in a diff. Changing one is a decision worth seeing
                  in git history.
  .env            secrets and per-machine values. Never committed.

Point VOICE_AGENT_CONFIG at another YAML to override the defaults, which is how
an experiment runs at 16 kHz without editing a committed file.

A missing or misspelled key raises at import rather than silently falling back,
because a typo'd band edge would quietly invalidate every accuracy number.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

import yaml

log = logging.getLogger(__name__)

DEFAULTS_PATH = Path(__file__).parent / "defaults.yaml"
CONFIG_ENV_VAR = "VOICE_AGENT_CONFIG"


@dataclass(frozen=True)
class AudioConfig:
    sample_rate: int
    band_low_hz: int
    band_high_hz: int
    codec: str


@dataclass(frozen=True)
class SttConfig:
    elevenlabs_model: str
    deepgram_model: str


@dataclass(frozen=True)
class TtsConfig:
    sample_rate: int


@dataclass(frozen=True)
class EvaluationConfig:
    wer_threshold: float


@dataclass(frozen=True)
class Settings:
    audio: AudioConfig
    stt: SttConfig
    tts: TtsConfig
    evaluation: EvaluationConfig


def load(path: Path | None = None) -> Settings:
    """Read a settings YAML. Defaults to the committed one."""
    path = path or Path(os.getenv(CONFIG_ENV_VAR) or DEFAULTS_PATH)
    log.debug("loading settings from %s", path)

    data = yaml.safe_load(path.read_text(encoding="utf-8"))

    return Settings(
        audio=AudioConfig(**data["audio"]),
        stt=SttConfig(**data["stt"]),
        tts=TtsConfig(**data["tts"]),
        evaluation=EvaluationConfig(**data["evaluation"]),
    )


settings = load()
