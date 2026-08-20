"""Component 12, and the step 2 gate.

Pure logic. The extractor is a stub, so a whole conversation runs as typed text
with no API calls and no audio. That is the point of keeping flow.py ignorant
of telephony.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from voice_agent.flow import Flow, Script, State
from voice_agent.llm import Answer, build

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "src/voice_agent/lang/ur/script.yaml"

CALLER = {"name": "انس", "date": "کل", "time": "چار"}


class StubExtractor:
    """Returns whatever the test queued, so flow logic is tested on its own."""

    name = "stub"

    def __init__(self, *values: Any) -> None:
        self.queue = list(values)
        self.calls: list[tuple[str, str]] = []

    def extract(self, said: str, slot_type: str, guidance: str = "") -> Answer:
        self.calls.append((said, slot_type))
        value = self.queue.pop(0) if self.queue else None
        return Answer(value=value, slot_type=slot_type, said=said, model="stub")


def flow(*values: Any) -> Flow:
    return Flow(Script.load(SCRIPT_PATH), StubExtractor(*values))


# --- the script itself, component 6 ---


def test_the_urdu_script_parses() -> None:
    script = Script.load(SCRIPT_PATH)

    assert script.language == "ur-PK"
    assert script.first == "verify_identity"
    assert script.guidance


def test_every_question_has_a_keypad_fallback() -> None:
    """A build-plan rule. Urdu recognition will fail on a bad line, and a
    landline caller needs a way through when it does."""
    script = Script.load(SCRIPT_PATH)

    for state in script.states.values():
        if state.terminal or state.type != "boolean":
            continue
        assert state.dtmf, f"{state.id} has no keypad fallback"


def test_every_transition_points_somewhere_real() -> None:
    Script.load(SCRIPT_PATH)  # raises on a dangling target


def test_a_dangling_transition_is_caught_at_load() -> None:
    with pytest.raises(ValueError, match="unknown state"):
        Script(
            {
                "name": "t",
                "language": "ur-PK",
                "states": [
                    {"id": "a", "ask": "?", "slot": "s", "type": "boolean", "on_true": "nowhere"}
                ],
            }
        )


# --- whole conversations, typed ---


def test_confirms_the_appointment() -> None:
    """The happy path. Yes, then yes."""
    call = flow(True, True)

    turn = call.start(**CALLER)
    assert turn.state == "verify_identity"
    assert "انس" in turn.say

    turn = call.reply("جی ہاں")
    assert turn.state == "confirm_appointment"

    turn = call.reply("جی ہاں ٹھیک ہے")
    assert turn.state == "done"
    assert turn.expects_reply is False

    assert call.finished
    assert call.result.outcome == "done"
    assert call.result.slots == {"identity_confirmed": True, "confirmed": True}


def test_wrong_person_ends_the_call_politely() -> None:
    call = flow(False)
    call.start(**CALLER)

    turn = call.reply("نہیں")

    assert turn.state == "wrong_person"
    assert call.result.outcome == "wrong_person"
    assert call.result.slots == {"identity_confirmed": False}


def test_declining_the_time_asks_for_another_one() -> None:
    call = flow(True, False, "پرسوں صبح")
    call.start(**CALLER)
    call.reply("جی ہاں")

    turn = call.reply("نہیں")
    assert turn.state == "reschedule"

    turn = call.reply("پرسوں صبح")
    assert turn.state == "done"
    assert call.result.slots["preferred_time"] == "پرسوں صبح"


def test_unclear_answers_retry_then_hand_off() -> None:
    """max_retries is 2, so the third unclear reply gives up. Guessing here
    books a patient who never agreed."""
    call = flow(None, None, None)
    call.start(**CALLER)

    assert call.reply("...").state == "verify_identity"  # retry 1
    assert call.reply("...").state == "verify_identity"  # retry 2
    turn = call.reply("...")  # over the limit

    assert turn.state == "handoff"
    assert call.result.outcome == "handoff"


def test_a_clear_answer_resets_the_retry_count() -> None:
    """Two unclear replies early must not make the next question fragile."""
    call = flow(None, None, True, None, None, True)
    call.start(**CALLER)
    call.reply("...")
    call.reply("...")
    call.reply("جی ہاں")

    call.reply("...")
    call.reply("...")
    turn = call.reply("جی ہاں")

    assert turn.state == "done"


# --- the keypad ---


def test_keypad_answers_without_calling_the_model() -> None:
    stub = StubExtractor()
    call = Flow(Script.load(SCRIPT_PATH), stub)
    call.start(**CALLER)

    turn = call.reply(key="1")

    assert turn.state == "confirm_appointment"
    assert stub.calls == [], "a pressed key is unambiguous, do not pay for a model call"


def test_keypad_two_is_a_no() -> None:
    call = flow()
    call.start(**CALLER)

    assert call.reply(key="2").state == "wrong_person"


def test_keypad_repeat_asks_the_same_question_again() -> None:
    call = flow(True)
    call.start(**CALLER)
    call.reply(key="1")

    turn = call.reply(key="3")

    assert turn.state == "confirm_appointment"
    assert not call.finished


def test_an_unmapped_key_counts_as_unclear() -> None:
    call = flow()
    call.start(**CALLER)

    turn = call.reply(key="9")

    assert turn.state == "verify_identity"  # re-asked, not routed


def test_the_turn_carries_its_keypad_options() -> None:
    """session.py needs these to configure DTMF capture for the turn."""
    turn = flow().start(**CALLER)

    assert turn.dtmf == {"1": True, "2": False}


# --- placeholders and misuse ---


def test_a_missing_placeholder_fails_loudly() -> None:
    """Better than reading "{name}" aloud to a patient."""
    with pytest.raises(ValueError, match="name"):
        flow().start(date="کل", time="چار")


def test_result_is_not_available_mid_call() -> None:
    call = flow()
    call.start(**CALLER)

    with pytest.raises(RuntimeError, match="still running"):
        _ = call.result


def test_replying_before_start_raises() -> None:
    with pytest.raises(RuntimeError, match="not started"):
        flow().reply("جی ہاں")


def test_replying_after_the_call_ended_raises() -> None:
    call = flow(False)
    call.start(**CALLER)
    call.reply("نہیں")

    with pytest.raises(RuntimeError, match="ended"):
        call.reply("ہیلو")


def test_a_state_that_is_neither_terminal_nor_a_question_is_rejected() -> None:
    with pytest.raises(ValueError, match="asks nothing"):
        Script({"name": "t", "language": "ur-PK", "states": [{"id": "a"}]})


def test_state_defaults_are_safe() -> None:
    """An under-specified state must not silently inherit another one's keypad."""
    assert State(id="x").dtmf == {}
    assert State(id="x").max_retries == 2


# --- the step 2 gate: a whole conversation through real Claude ---

LIVE_KEY = os.getenv("ANTHROPIC_API_KEY", "")
live = pytest.mark.skipif(not LIVE_KEY, reason="no ANTHROPIC_API_KEY")


def real_flow() -> Flow:
    return Flow(Script.load(SCRIPT_PATH), build(LIVE_KEY))


@pytest.mark.live
@live
def test_a_real_confirmation_call_end_to_end() -> None:
    """Typed replies, real extraction, right outcome. No audio, no telephony."""
    call = real_flow()

    turn = call.start(**CALLER)
    assert turn.state == "verify_identity"

    turn = call.reply("جی ہاں، میں انس بول رہا ہوں")
    assert turn.state == "confirm_appointment"

    turn = call.reply("جی بالکل، چار بجے ٹھیک ہے")

    assert turn.state == "done"
    assert call.result.outcome == "done"
    assert call.result.slots["identity_confirmed"] is True
    assert call.result.slots["confirmed"] is True


@pytest.mark.live
@live
def test_a_real_reschedule_call_end_to_end() -> None:
    call = real_flow()
    call.start(**CALLER)
    call.reply("haan ji")

    turn = call.reply("نہیں، وہ وقت مناسب نہیں ہے")
    assert turn.state == "reschedule"

    turn = call.reply("پرسوں صبح دس بجے")

    assert turn.state == "done"
    assert call.result.outcome == "done"
    assert call.result.slots["confirmed"] is False
    assert call.result.slots["preferred_time"]


@pytest.mark.live
@live
def test_a_real_wrong_number_call_end_to_end() -> None:
    call = real_flow()
    call.start(**CALLER)

    turn = call.reply("نہیں، آپ نے غلط نمبر ملایا ہے")

    assert turn.state == "wrong_person"
    assert call.result.outcome == "wrong_person"


@pytest.mark.live
@live
def test_a_confused_caller_is_handed_to_a_human() -> None:
    """Three unclear replies, so we transfer rather than guess."""
    call = real_flow()
    call.start(**CALLER)
    call.reply("کیا؟")
    call.reply("کون بول رہا ہے؟")
    turn = call.reply("مجھے سمجھ نہیں آیا")

    assert turn.state == "handoff"
    assert call.result.outcome == "handoff"
