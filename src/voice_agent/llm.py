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
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from voice_agent.config import settings

log = logging.getLogger(__name__)

BOOLEAN = "boolean"
DATETIME = "datetime"

# How the model says the caller is hanging up, when it is writing prose rather
# than filling in a field. Sent as a stop sequence, so the API cuts the reply
# off at it and the marker never reaches the transcript or the voice.
#
# Prose is streamed and JSON is not: half an object does not parse, and waiting
# for the closing brace means waiting for the whole reply, which is the delay
# streaming exists to remove.
END_CALL = "<END_CALL>"

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


_SPEECH_RULES = """Everything you write is read aloud by a text-to-speech voice.
Nobody sees it. Write for the ear:

- One sentence. Two only when the second is genuinely needed. Every extra word
  is time the caller spends in silence waiting for you, and time spent speaking
  it back at them.
- Plain everyday spoken words, the way someone talks on the phone. No formal or
  literary vocabulary, no written-language phrasing.
- Words only. No digits, no symbols, no abbreviations, no brackets, no bullet
  points, no emoji. Write numbers out the way you would say them.
- One script throughout. Do not mix Latin letters into a non-Latin script, and
  do not leave English words untranslated unless that is genuinely how people
  say them on the phone.
- No stage directions and no describing your own tone.
- Never invent a fact about the call, the caller, or whoever you are calling
  for. If you do not know, say so and offer to pass them to a person."""


_TALK_SYSTEM = f"""You are on a live phone call, talking to a real person. There
is no form to fill in. Who you are and what the call is for is set out above,
and that comes first; everything here is about how to speak, not what to say.

{_SPEECH_RULES}

Do not ask them to repeat themselves and do not chase them for an answer. If
they said something small, say something small back.

You have already opened the call. Do not greet them again, and do not
reintroduce yourself unless they ask who you are."""


_CHAT_SYSTEM = f"""You are on a live phone call, talking to a real person. They
have said something that is not an answer to the question you asked, usually a
question of their own or just conversation.

Talk to them. Answer what they asked, then put your question to them again.

{_SPEECH_RULES}

- Never say yes or no on the caller's behalf. You are talking, not recording
  their answer.
- Never move on to a different question. The call is a fixed sequence and you
  are not the one advancing it.

End by putting the same question to them again, in the words you were given."""


_HANGING_UP = f"""Write only what you say out loud. Nothing else, no field names
and no quotes around it.

If the caller is ending the call, say your farewell and then write {END_CALL}
after it, on its own.

Ending the call takes a plain goodbye and nothing less: khuda hafiz, allah
hafiz, bye, that is all, or something else that clearly means they are finished
talking to you.

Everything else keeps the call open. A quiet moment is not a hang-up. Neither
is a short answer, a one-word reply, an annoyed one, or a line that came through
garbled, half a word, or cut off partway. Those are a bad phone line, and
hanging up on a bad line is the worst thing you can do on one.

If you cannot tell whether they meant to end the call, ask them, and do not
write {END_CALL} on that turn.

Only what the caller said on this turn can end the call. Anything from an
earlier turn has already been answered, so never end the call on that. If they
asked you to hang up earlier and you are still on the line, they are asking you
something new now, so answer it."""


class _Chat(BaseModel):
    reply: str = Field(description="what to say out loud")
    end_call: bool = Field(
        default=False,
        description=(
            "true only if the caller is ending the call, e.g. goodbye, that is all, "
            "thanks bye, hang up. Say your farewell in reply and set this. Never set "
            "it just because the conversation went quiet."
        ),
    )


@dataclass(frozen=True)
class Spoken:
    """What to say, and whether the caller is done."""

    text: str
    end_call: bool = False


class Utterance:
    """A reply arriving a few words at a time.

    Iterate it for text as the model writes it. `text` and `end_call` describe
    the whole reply and are only right once it has run out, which is the trade
    streaming makes: the first word costs almost nothing, and the last thing
    you learn about the turn costs the whole turn.

    Closing it early abandons the request, which is what barge-in does.
    """

    def __init__(self, client: Any, **request: Any) -> None:
        self._client = client
        self._request = request
        self.text = ""
        self.end_call = False

    async def __aiter__(self) -> AsyncIterator[str]:
        async with self._client.messages.stream(**self._request) as stream:
            async for piece in stream.text_stream:
                self.text += piece
                yield piece
            final = await stream.get_final_message()

        self.end_call = final.stop_sequence == END_CALL


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


@runtime_checkable
class Responder(Protocol):
    """Optional. Answers a caller who asked something back, then re-asks.

    Deliberately separate from SlotExtractor. Extraction decides what goes in a
    slot and must never guess. This only decides what to say while the slot
    stays empty, so a bad reply here costs a wasted turn and nothing more.
    """

    name: str

    def respond(
        self,
        said: str,
        asking: str = "",
        persona: str = "",
        history: list[dict[str, str]] | None = None,
    ) -> Spoken: ...


@runtime_checkable
class StreamingResponder(Protocol):
    """The same reply, handed over as it is written rather than when it is done."""

    name: str

    def stream(
        self,
        said: str,
        asking: str = "",
        persona: str = "",
        history: list[dict[str, str]] | None = None,
    ) -> Utterance: ...


def _pace() -> dict[str, Any]:
    """How hard the chat model thinks before it speaks.

    Sonnet 5 thinks by default and at the default effort that measured 3.96 s a
    turn against Haiku's ~2 s, which a caller hears as the agent being slow. A
    spoken reply of one or two sentences has nothing to deliberate over, so
    thinking is off by default and the effort knob is there if it is turned on.
    """
    if not settings.llm.chat_thinking:
        return {"thinking": {"type": "disabled"}}
    return {"output_config": {"effort": settings.llm.chat_effort}}


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


class ClaudeResponder:
    """Small talk, so an off-script caller gets an answer instead of the same
    question a third time. The persona is passed in from script.yaml, so this
    module still knows no Urdu and nothing about any particular clinic."""

    name = "claude-chat"

    def __init__(
        self, api_key: str, model: str | None = None, max_tokens: int | None = None
    ) -> None:
        import anthropic

        self.client = anthropic.Anthropic(api_key=api_key)
        self.async_client = anthropic.AsyncAnthropic(api_key=api_key)
        self.model = model or settings.llm.chat_model or settings.llm.model
        self.max_tokens = max_tokens or settings.llm.chat_max_tokens

    def respond(
        self,
        said: str,
        asking: str = "",
        persona: str = "",
        history: list[dict[str, str]] | None = None,
    ) -> Spoken:
        """`asking` is the question still waiting for an answer, if there is one.

        Empty means talk mode: no form, nothing to steer back to, so the reply
        is just a reply.

        `history` is the last few turns, and only talk mode sends any. Extraction
        stays deliberately memoryless; a conversation cannot be, or the agent
        greets you every time you say "right".
        """
        if not said.strip():
            return Spoken("")

        log.info("[%s] answering off-script reply (%d chars)", self.name, len(said))

        response = self.client.messages.parse(
            model=self.model,
            max_tokens=self.max_tokens,
            system=self._system(asking, persona),
            messages=self._messages(said, asking, history),
            output_format=_Chat,
        )
        parsed = response.parsed_output
        return Spoken(parsed.reply.strip(), parsed.end_call)

    def stream(
        self,
        said: str,
        asking: str = "",
        persona: str = "",
        history: list[dict[str, str]] | None = None,
    ) -> Utterance:
        """The same reply as `respond`, handed over while it is still being written.

        No JSON. Structured output is what forces the whole reply to exist
        before any of it can be read, and the only field worth keeping is
        `end_call`, which arrives as a stop sequence instead.

        Unlike `respond` this does not check for silence, because both callers
        decide whether there is anything to answer before they get here.
        """
        log.info("[%s] streaming a reply to %d chars", self.name, len(said))

        return Utterance(
            self.async_client,
            model=self.model,
            max_tokens=self.max_tokens,
            system=self._system(asking, persona, _HANGING_UP),
            messages=self._messages(said, asking, history),
            stop_sequences=[END_CALL],
            **_pace(),
        )

    def _system(self, asking: str, persona: str, extra: str = "") -> list[dict[str, Any]]:
        """The rules and the persona are identical on every turn of every call,
        and they are long. Caching them means the model re-reads only what the
        caller just said, which the caller hears as speed."""
        rules = _CHAT_SYSTEM if asking else _TALK_SYSTEM
        # The persona goes first. What the agent is for outranks how it talks,
        # and a model reads the opening of a prompt as the point of it.
        parts = [
            f"Who you are and what this call is for:\n{persona}" if persona else "",
            rules,
            extra,
        ]
        return [
            {
                "type": "text",
                "text": "\n\n".join(part for part in parts if part),
                "cache_control": {"type": "ephemeral"},
            }
        ]

    def _messages(
        self, said: str, asking: str, history: list[dict[str, str]] | None
    ) -> list[dict[str, Any]]:
        return [
            *(history or []),
            {
                "role": "user",
                "content": (
                    f"The question you asked: {asking}\nWhat the caller said instead: {said}"
                    if asking
                    else said
                ),
            },
        ]


def build(api_key: str, model: str | None = None) -> SlotExtractor:
    """Called by session.py. Model defaults to config/defaults.yaml."""
    return ClaudeExtractor(api_key, model)


def build_responder(api_key: str, model: str | None = None) -> Responder:
    """Optional companion to build(). Only wired in when chat mode is on."""
    return ClaudeResponder(api_key, model)
