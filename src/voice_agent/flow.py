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
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from voice_agent.llm import SlotExtractor

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
    """What the agent says next, and whether it then waits."""

    say: str
    state: str
    expects_reply: bool
    dtmf: dict[str, Any] = field(default_factory=dict)


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

    def __init__(self, script: Script, extractor: SlotExtractor) -> None:
        self.script = script
        self.extractor = extractor
        self.slots: dict[str, Any] = {}
        self._state: State | None = None
        self._retries = 0
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
            return self._retry(state)

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
