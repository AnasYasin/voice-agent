"""Component 4. Claude. Answer extraction only, fixed schema, never prose.

The caller says something. This turns it into the one value the current
question is asking for, or reports that it could not tell.

Two rules the whole design rests on:

  Unclear is a real answer. Guessing "yes" from an ambiguous reply books a
  patient who never agreed. `value is None` means ask again or fall back to
  the keypad, and the flow engine treats it that way.

  It never sees the whole conversation. One utterance, one slot, one answer.
  That keeps the prompt at a few hundred tokens, keeps latency off the call,
  and stops a bad turn from poisoning later ones.

Language lives under lang/<code>/. The extraction guidance is passed in from
script.yaml rather than written here, so this module never learns any Urdu.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field

from voice_agent.config import settings

log = logging.getLogger(__name__)

BOOLEAN = "boolean"
DATETIME = "datetime"

_SYSTEM = """You extract one answer from one thing a caller said on a phone call.

Return only the value asked for. Never explain, never add prose, never answer a
question the caller did not answer.

Set understood to false when the reply is ambiguous, off-topic, empty, or a
question back to you. A wrong guess is worse than admitting you could not tell,
because the caller has to live with the appointment you book."""


class _Boolean(BaseModel):
    understood: bool = Field(description="false if you cannot tell what they meant")
    value: bool | None = Field(description="true for yes, false for no, null if not understood")


class _Datetime(BaseModel):
    understood: bool = Field(description="false if no day or time was given")
    value: str | None = Field(
        description=(
            "the day or time the caller asked for, as they said it, e.g. '15:00', "
            "'tomorrow', 'Monday morning'. null if not understood"
        )
    )


_SCHEMAS = {BOOLEAN: _Boolean, DATETIME: _Datetime}


@dataclass(frozen=True)
class Answer:
    """`value is None` means the model could not tell. It is not a 'no'."""

    value: bool | str | None
    slot_type: str
    said: str
    model: str

    @property
    def understood(self) -> bool:
        return self.value is not None


@runtime_checkable
class SlotExtractor(Protocol):
    name: str

    def extract(self, said: str, slot_type: str, guidance: str = "") -> Answer: ...


class ClaudeExtractor:
    name = "claude"

    def __init__(
        self, api_key: str, model: str | None = None, max_tokens: int | None = None
    ) -> None:
        import anthropic

        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model or settings.llm.model
        self.max_tokens = max_tokens or settings.llm.max_tokens

    def extract(self, said: str, slot_type: str, guidance: str = "") -> Answer:
        """Pull one slot value out of one caller utterance."""
        schema = _SCHEMAS.get(slot_type)
        if schema is None:
            raise ValueError(f"unknown slot type {slot_type!r}, expected one of {sorted(_SCHEMAS)}")

        # Silence on the line is not worth an API call.
        if not said.strip():
            return Answer(value=None, slot_type=slot_type, said=said, model=self.model)

        log.info("[%s] extracting %s from %d chars", self.name, slot_type, len(said))

        response = self.client.messages.parse(
            model=self.model,
            max_tokens=self.max_tokens,
            system=f"{_SYSTEM}\n\n{guidance}".strip(),
            messages=[{"role": "user", "content": said}],
            output_format=schema,
        )
        parsed = response.parsed_output

        # understood=false alongside a non-null value is a contradiction. Trust
        # the flag, because the failure it guards against is the expensive one.
        value = parsed.value if parsed.understood else None

        return Answer(value=value, slot_type=slot_type, said=said, model=self.model)


def build(api_key: str, model: str | None = None) -> SlotExtractor:
    """Called by session.py. Model defaults to config/defaults.yaml."""
    return ClaudeExtractor(api_key, model)
