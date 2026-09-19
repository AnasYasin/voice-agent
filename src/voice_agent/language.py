"""Component 10. The language switch.

Loads everything language-specific into one object: the voice, the STT
language code, the keyterms, the normaliser and the call script.

Every other component receives this object rather than reading config or
importing anything Urdu-specific. That is the whole reason adding English is a
sibling folder under lang/ rather than an edit to session.py, and the reason
changing AGENT_LANGUAGE in .env is enough to switch.

    language = load()          # from AGENT_LANGUAGE
    language = load("ur-PK")   # or explicitly
    available()                # {"ur-PK": "اردو", "en-US": "English"}
"""

from __future__ import annotations

import importlib
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import yaml

from voice_agent.flow import Script

log = logging.getLogger(__name__)

LANGUAGE_ENV_VAR = "AGENT_LANGUAGE"
DEFAULT_LANGUAGE = "ur-PK"
SCRIPT_FILE = "script.yaml"
AGENT_FILE = "agent.yaml"
PACKS_DIR = Path(__file__).resolve().parent / "lang"


def default_locale() -> str:
    return os.getenv(LANGUAGE_ENV_VAR) or DEFAULT_LANGUAGE


def available() -> dict[str, str]:
    """Every language pack on disk, locale to display name, in the order each
    pack asks for. This is what a language toggle shows, so adding a folder
    adds a button and its ORDER decides where it lands."""
    manifests = [
        importlib.import_module(f"voice_agent.lang.{path.parent.name}.voice")
        for path in sorted(PACKS_DIR.glob("*/voice.py"))
    ]
    manifests.sort(key=lambda manifest: (getattr(manifest, "ORDER", 99), manifest.LOCALE))
    return {manifest.LOCALE: manifest.NAME for manifest in manifests}


@runtime_checkable
class Normalizer(Protocol):
    def from_speech(self, text: str) -> str: ...
    def for_speech(self, text: str) -> str: ...
    def sentences(self, text: str) -> tuple[list[str], str]: ...


@dataclass(frozen=True)
class Language:
    code: str
    locale: str
    name: str
    stt_language: str
    tts_voice: str
    tts_provider: str
    keyterms: list[str]
    normalizer: Any
    script: Script
    persona: str = ""
    greeting: str = ""
    time_up: str = ""

    def __repr__(self) -> str:  # keeps logs readable, the script is large
        return f"Language({self.locale}, voice={self.tts_voice}, script={self.script.name})"


def load(locale: str | None = None) -> Language:
    """Build the Language for `locale`, or whatever AGENT_LANGUAGE says.

    Accepts either form. "ur-PK" and "ur" both resolve to lang/ur/, because the
    folder is per language and the locale only narrows the voice and script
    inside it.
    """
    locale = locale or default_locale()
    code = locale.split("-")[0].lower()

    try:
        manifest = importlib.import_module(f"voice_agent.lang.{code}.voice")
    except ModuleNotFoundError:
        raise ValueError(
            f"no language pack for {locale!r}. Add src/voice_agent/lang/{code}/ with "
            f"a voice.py manifest and a {SCRIPT_FILE}."
        ) from None

    pack = Path(manifest.__file__).parent
    script_path = pack / SCRIPT_FILE
    if not script_path.exists():
        raise ValueError(f"language {code!r} has a manifest but no {SCRIPT_FILE}")

    # The agent profile is optional. Without it, chat mode simply has no
    # persona to speak from and the call runs exactly as it always did.
    agent_path = pack / AGENT_FILE
    persona = ""
    greeting = ""
    time_up = ""
    if agent_path.exists():
        profile = yaml.safe_load(agent_path.read_text(encoding="utf-8"))
        persona = profile.get("persona", "")
        greeting = profile.get("greeting", "")
        time_up = profile.get("time_up", "")

    language = Language(
        code=code,
        locale=manifest.LOCALE,
        name=manifest.NAME,
        stt_language=manifest.STT_LANGUAGE,
        tts_voice=manifest.TTS_VOICE,
        tts_provider=manifest.TTS_PROVIDER,
        keyterms=list(manifest.KEYTERMS),
        normalizer=manifest.NORMALIZER(),
        script=Script.load(script_path),
        persona=persona,
        greeting=greeting,
        time_up=time_up,
    )
    log.info("loaded %s", language)
    return language
