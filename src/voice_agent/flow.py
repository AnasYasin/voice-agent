"""Component 12. The call as a state machine, driven by lang/<code>/script.yaml.

Knows nothing about telephony, audio, or which language it is speaking. It is
handed text and hands back text, which is why the whole conversation can be
tested by typing at it.

Three ways a question can end:

  on_true / on_false   the caller answered, and the answer routes the call
  on_fail              max_retries of unclear answers. Hand to a human rather
                       than guess. A wrong booking costs more than a transfer.

The keypad is checked before the model. A pressed key is unambiguous, costs
nothing, and is the fallback when Urdu recognition fails on a bad line.

Both engines answer in two shapes. `reply` hands back finished text, which is
what a script line always is and what the tests type at. `reply_stream` hands
back a Turn whose words are still being written, for the parts of the call the
model composes: playback starts on the first sentence instead of the last one.
Routing, retries and the keypad are the same code either way.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterable, AsyncIterator, Callable
from contextlib import aclosing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from voice_agent.config import settings
from voice_agent.llm import Responder, SlotExtractor

log = logging.getLogger(__name__)

REPEAT = "repeat"


@dataclass(frozen=True)
class State:
    id: str
    ask: str = ""
    say: str = ""
    slot: str = ""
    type: str = ""
    dtmf: dict[str, Any] = field(default_factory=dict)
    on_true: str = ""
    on_false: str = ""
    on_fail: str = ""
    max_retries: int = 2
    terminal: bool = False


@dataclass(frozen=True)
class Turn:
    """What the agent says next, and whether it then waits.

    `say` is the whole line when it is known. `stream` is set instead when the
    model is still writing it, and then `say` is empty: whoever speaks the turn
    is the one that finds out what it said.
    """

    say: str
    state: str
    expects_reply: bool
    dtmf: dict[str, Any] = field(default_factory=dict)
    stream: AsyncIterable[str] | None = None


@dataclass(frozen=True)
class Result:
    outcome: str
    slots: dict[str, Any]


class Script:
    """A parsed script.yaml. Validated at load, so a broken script fails on
    startup rather than halfway through a real call."""

    def __init__(self, data: dict[str, Any]) -> None:
        self.name: str = data["name"]
        self.language: str = data["language"]
        self.consent: str = data.get("consent", "")
        self.guidance: str = data.get("extraction_guidance", "")
        self.states: dict[str, State] = {}

        for raw in data["states"]:
            state = State(**raw)
            self.states[state.id] = state

        if not self.states:
            raise ValueError("script has no states")

        self.first: str = data["states"][0]["id"]
        self._check_transitions()

    @classmethod
    def load(cls, path: Path) -> Script:
        return cls(yaml.safe_load(path.read_text(encoding="utf-8")))

    def _check_transitions(self) -> None:
        """Every destination must exist, and every question must have a keypad
        route out. Both are cheap to check here and painful to discover live."""
        for state in self.states.values():
            for name in ("on_true", "on_false", "on_fail"):
                target = getattr(state, name)
                if target and target not in self.states:
                    raise ValueError(f"{state.id}.{name} points at unknown state {target!r}")

            if state.terminal:
                continue

            if not state.ask:
                raise ValueError(f"{state.id} is not terminal but asks nothing")
            if state.type and not state.slot:
                raise ValueError(f"{state.id} has a type but no slot to store it in")


class Flow:
    """One call. Build it, start it, feed it replies until `finished`."""

    def __init__(
        self,
        script: Script,
        extractor: SlotExtractor,
        responder: Responder | None = None,
        persona: str = "",
        max_chat: int = 6,
    ) -> None:
        self.script = script
        self.extractor = extractor
        self.responder = responder
        self.persona = persona
        self.max_chat = max_chat
        self.slots: dict[str, Any] = {}
        self._state: State | None = None
        self._retries = 0
        self._chats = 0
        self._fields: dict[str, Any] = {}
        self._outcome = ""

    @property
    def finished(self) -> bool:
        return bool(self._outcome)

    @property
    def result(self) -> Result:
        if not self.finished:
            raise RuntimeError("call is still running, no result yet")
        return Result(outcome=self._outcome, slots=dict(self.slots))

    def start(self, **fields: Any) -> Turn:
        """`fields` fill the {placeholders} in the script, e.g. name, date, time."""
        self._fields = fields
        return self._enter(self.script.first)

    def reply(self, said: str = "", key: str = "") -> Turn:
        """One caller turn. `key` is a DTMF press and wins over speech."""
        return self._route(said, key, self._chat)

    def reply_stream(self, said: str = "", key: str = "") -> Turn:
        """The same turn, except an off-script answer comes back as it is written.

        Only the chat reply differs. A script line is fixed text with a WAV
        already on disk, so there is nothing about it to stream.
        """
        return self._route(said, key, self._chat_stream)

    def spoken(self, text: str) -> None:
        """What the caller actually heard, once the turn has been played.

        Nothing to do here. Extraction is deliberately memoryless, so a script
        run keeps no history for an interrupted reply to corrupt. Conversation
        does, and overrides this. The method exists on both so session.py can
        call it without knowing which engine it is driving.
        """

    def _route(self, said: str, key: str, chat: Callable[[State, str], Turn | None]) -> Turn:
        if self.finished:
            raise RuntimeError("call has ended")
        if self._state is None:
            raise RuntimeError("call has not started, call start() first")

        state = self._state
        value = self._from_keypad(state, key) if key else self._from_speech(state, said)

        if value is REPEAT:
            log.info("[%s] caller asked us to repeat", state.id)
            return self._ask(state)

        if value is None:
            return chat(state, said) or self._retry(state)

        if state.slot:
            self.slots[state.slot] = value

        if value is False and state.on_false:
            return self._enter(state.on_false)
        return self._enter(state.on_true or state.on_fail)

    def _from_keypad(self, state: State, key: str) -> Any:
        value = state.dtmf.get(key)
        if value is None:
            log.info("[%s] key %r is not an option here", state.id, key)
        return REPEAT if value == REPEAT else value

    def _from_speech(self, state: State, said: str) -> Any:
        answer = self.extractor.extract(said, state.type, self.script.guidance)
        return answer.value

    def _chat(self, state: State, said: str) -> Turn | None:
        """Answer a caller who asked something back, then put the question again.

        Returns None when chat is off, the line was silent, or the caller has
        used up the budget. The caller then falls through to _retry, so an
        endless exchange still ends at handoff instead of running forever.

        The slot stays empty either way. Nothing said here books anything.
        """
        if not self._may_chat(state, said):
            return None

        self._chats += 1
        reply = self.responder.respond(
            said, asking=self._fill(state.ask), persona=self.persona
        ).text
        if not reply:
            return None

        log.info("[%s] chat %d of %d: %s", state.id, self._chats, self.max_chat, reply)
        return Turn(say=reply, state=state.id, expects_reply=True, dtmf=dict(state.dtmf))

    def _chat_stream(self, state: State, said: str) -> Turn | None:
        """The same answer, arriving as the model writes it.

        The budget is spent here rather than when the reply lands, because a
        stream that the caller talks over still cost a call.
        """
        if not self._may_chat(state, said):
            return None

        self._chats += 1
        log.info("[%s] chat %d of %d, streaming", state.id, self._chats, self.max_chat)
        return Turn(
            say="",
            state=state.id,
            expects_reply=True,
            dtmf=dict(state.dtmf),
            stream=self.responder.stream(said, asking=self._fill(state.ask), persona=self.persona),
        )

    def _may_chat(self, state: State, said: str) -> bool:
        if self.responder is None or not said.strip():
            return False
        if self._chats >= self.max_chat:
            log.info("[%s] chat budget spent, back to retries", state.id)
            return False
        return True

    def _retry(self, state: State) -> Turn:
        self._retries += 1
        if self._retries > state.max_retries:
            log.info("[%s] gave up after %d unclear replies", state.id, self._retries)
            return self._enter(state.on_fail)
        log.info("[%s] unclear, retry %d of %d", state.id, self._retries, state.max_retries)
        return self._ask(state)

    def _enter(self, state_id: str) -> Turn:
        state = self.script.states[state_id]
        self._state = state
        self._retries = 0

        if state.terminal:
            self._outcome = state.id
            log.info("call ended: %s", state.id)
            return Turn(say=self._fill(state.say), state=state.id, expects_reply=False)

        return self._ask(state)

    def _ask(self, state: State) -> Turn:
        return Turn(
            say=self._fill(state.ask),
            state=state.id,
            expects_reply=True,
            dtmf=dict(state.dtmf),
        )

    def _fill(self, text: str) -> str:
        """Missing placeholders are a scripting bug, not something to paper over
        by reading '{name}' aloud to a patient."""
        try:
            return text.format(**self._fields)
        except KeyError as error:
            raise ValueError(f"script needs {error} but it was not passed to start()") from None


class Conversation:
    """No script, no slots, no questions. Just talk.

    Drop-in for Flow, so session.py and every transport work unchanged. The
    campaign lives in script.yaml and is loaded by Flow; this class deliberately
    never touches it, which is how a demo call and a real campaign stay separate
    things rather than two modes of one tangled object.

    Two rules that make it feel like a phone call rather than a kiosk:

      Silence is an answer. Nothing heard means nothing said. It does not
      prompt, repeat, or ask whether you are still there.

      It never ends itself. There is no outcome to reach, so the call is over
      when the caller hangs up and not before.
    """

    def __init__(
        self,
        responder: Responder,
        persona: str = "",
        greeting: str = "",
        memory_turns: int | None = None,
    ) -> None:
        self.responder = responder
        self.persona = persona
        self.greeting = greeting
        self.memory_turns = memory_turns if memory_turns is not None else settings.llm.memory_turns
        self.slots: dict[str, Any] = {}
        self._started = False
        self._ended = False
        self._history: list[dict[str, str]] = []

    @property
    def finished(self) -> bool:
        return self._ended

    @property
    def result(self) -> Result:
        return Result(outcome="hung_up" if self._ended else "talk", slots={})

    def start(self, **fields: Any) -> Turn:
        self._started = True
        if self.greeting:
            self._history.append({"role": "assistant", "content": self.greeting})
        return Turn(say=self.greeting, state="talk", expects_reply=True)

    def reply(self, said: str = "", key: str = "") -> Turn:
        if not self._started:
            raise RuntimeError("call has not started, call start() first")

        # An empty transcript is a cough, a car, or a bad line. Answering it
        # would make the agent talk to itself, which is what makes these things
        # feel broken.
        if not said.strip():
            log.info("[talk] nothing heard, staying quiet")
            return Turn(say="", state="talk", expects_reply=True)

        spoken = self.responder.respond(
            said, asking="", persona=self.persona, history=list(self._history)
        )
        reply = spoken.text

        # The caller said goodbye. Say it back, then stop. An agent you cannot
        # get off the phone is worse than one that hangs up early.
        if spoken.end_call:
            log.info("[talk] caller ended the call")
            self._ended = True

        self._remember(said, reply)
        return Turn(say=reply, state="talk", expects_reply=True)

    def reply_stream(self, said: str = "", key: str = "") -> Turn:
        """The same turn, arriving as the model writes it.

        Whether the caller said goodbye is the last thing the model produces,
        so the call cannot be marked over here. That happens once the reply has
        run out, inside the stream, which is also when the transport learns it.
        """
        if not self._started:
            raise RuntimeError("call has not started, call start() first")

        if not said.strip():
            log.info("[talk] nothing heard, staying quiet")
            return Turn(say="", state="talk", expects_reply=True)

        utterance = self.responder.stream(
            said, asking="", persona=self.persona, history=list(self._history)
        )
        return Turn(say="", state="talk", expects_reply=True, stream=self._closing(said, utterance))

    async def _closing(self, said: str, utterance: Any) -> AsyncIterator[str]:
        """Pass the reply through, then do the bookkeeping it made possible.

        A reply the caller talked over is still a reply they heard part of, so
        it is remembered either way and the model is not left thinking it said
        something nobody heard.

        aclosing is what carries barge-in through to the model. Without it the
        request stays open until the garbage collector gets round to it.
        """
        try:
            async with aclosing(utterance.__aiter__()) as pieces:
                async for piece in pieces:
                    yield piece
        finally:
            self._remember(said, utterance.text.strip())
            if utterance.end_call:
                # The caller said goodbye. Say it back, then stop. An agent you
                # cannot get off the phone is worse than one that hangs up early.
                log.info("[talk] caller ended the call")
                self._ended = True

    def _remember(self, said: str, reply: str) -> None:
        """Capped. The whole point of a short window is that a long call does not
        slowly turn into a long prompt, which costs latency the caller hears.

        A history ending in the caller means the turn before this one was
        abandoned: they talked over the reply before a word of it was played,
        and `spoken` dropped it. What they said then and what they are saying
        now are one utterance that an interruption split in two, so they are
        joined back into one rather than left as two exchanges. Left apart, the
        older half reads as a turn the agent already answered, and the model
        keeps acting on an intent the caller has moved past.
        """
        if self._history and self._history[-1]["role"] == "user":
            joined = f"{self._history[-1]['content']} {said}".strip()
            self._history[-1]["content"] = joined
        else:
            self._history.append({"role": "user", "content": said})
        self._history.append({"role": "assistant", "content": reply})
        self._history = self._history[-self.memory_turns * 2 :]

    def spoken(self, text: str) -> None:
        """Cut the last reply down to what the caller actually heard.

        `_closing` remembers the reply the model wrote, because that is all it
        can see. Synthesis runs `tts.lookahead_seconds` ahead of playback, so a
        caller who interrupts stops sentences that were already written and
        already synthesised. Left alone, the model spends the rest of the call
        believing it said them, and answers as though the caller had heard.

        The session calls this once the queue has drained, because the queue is
        the only place that knows which sentences reached the transport.

        An empty `text` means they heard none of it. The reply is dropped and
        the caller's own words are kept, which leaves two caller turns in a row.
        The API takes that, and it is honest: they said something twice and got
        no answer in between.
        """
        if not self._history or self._history[-1]["role"] != "assistant":
            return  # nothing was remembered for this turn, so nothing to fix
        if text:
            self._history[-1]["content"] = text
        else:
            self._history.pop()
