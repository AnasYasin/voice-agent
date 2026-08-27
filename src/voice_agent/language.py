"""Component 10. The language switch.

Loads everything language-specific into one object: the voice, the STT
language code, the keyterms, the normaliser and the call script.

Every other component receives this object rather than reading config or
importing anything Urdu-specific. That is the whole reason adding English is a
sibling folder under lang/ rather than an edit to session.py, and the reason
changing AGENT_LANGUAGE in .env is enough to switch.

    language = load()          # from AGENT_LANGUAGE
    language = load("ur-PK")   # or explicitly
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


@runtime_checkable
class Normalizer(Protocol):
    def from_speech(self, text: str) -> str: ...
    def for_speech(self, text: str) -> str: ...
    def sentences(self, text: str) -> tuple[list[str], str]: ...


@dataclass(frozen=True)
class Language:
    code: str
    locale: str
    stt_language: str
    tts_voice: str
    keyterms: list[str]
    normalizer: Any
    script: Script
    persona: str = ""
    greeting: str = ""

    def __repr__(self) -> str:  # keeps logs readable, the script is large
        return f"Language({self.locale}, voice={self.tts_voice}, script={self.script.name})"


def load(locale: str | None = None) -> Language:
    """Build the Language for `locale`, or whatever AGENT_LANGUAGE says.

    Accepts either form. "ur-PK" and "ur" both resolve to lang/ur/, because the
    folder is per language and the locale only narrows the voice and script
    inside it.
    """
    locale = locale or os.getenv(LANGUAGE_ENV_VAR) or DEFAULT_LANGUAGE
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
    if agent_path.exists():
        profile = yaml.safe_load(agent_path.read_text(encoding="utf-8"))
        persona = profile.get("persona", "")
        greeting = profile.get("greeting", "")

    language = Language(
        code=code,
        locale=manifest.LOCALE,
        stt_language=manifest.STT_LANGUAGE,
        tts_voice=manifest.TTS_VOICE,
        keyterms=list(manifest.KEYTERMS),
        normalizer=manifest.NORMALIZER(),
        script=Script.load(script_path),
        persona=persona,
        greeting=greeting,
    )
    log.info("loaded %s", language)
    return language
