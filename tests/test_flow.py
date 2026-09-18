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
    assert script.first == "confirm_appointment"
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
    """The happy path. One question, one yes."""
    call = flow(True)

    turn = call.start(**CALLER)
    assert turn.state == "confirm_appointment"
    assert "انس" in turn.say, "the name greets the caller, it no longer checks them"

    turn = call.reply("جی ہاں ٹھیک ہے")
    assert turn.state == "done"
    assert turn.expects_reply is False

    assert call.finished
    assert call.result.outcome == "done"
    assert call.result.slots == {"confirmed": True}


def test_the_call_no_longer_checks_who_answered() -> None:
    """Removed deliberately. The name is spoken as a greeting, and nothing
    routes on whether the right person picked up."""
    script = Script.load(SCRIPT_PATH)

    assert script.first == "confirm_appointment"
    assert "verify" not in " ".join(script.states)
    assert "wrong_person" not in script.states


def test_declining_the_time_asks_for_another_one() -> None:
    call = flow(False, "پرسوں صبح")
    call.start(**CALLER)

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

    assert call.reply("...").state == "confirm_appointment"  # retry 1
    assert call.reply("...").state == "confirm_appointment"  # retry 2
    turn = call.reply("...")  # over the limit

    assert turn.state == "handoff"
    assert call.result.outcome == "handoff"


def test_a_clear_answer_resets_the_retry_count() -> None:
    """Two unclear replies early must not make the next question fragile."""
    call = flow(None, None, False, None, None, "پرسوں صبح")
    call.start(**CALLER)
    call.reply("...")
    call.reply("...")
    call.reply("نہیں")

    call.reply("...")
    call.reply("...")
    turn = call.reply("پرسوں صبح")

    assert turn.state == "done"


# --- the keypad ---


def test_keypad_answers_without_calling_the_model() -> None:
    stub = StubExtractor()
    call = Flow(Script.load(SCRIPT_PATH), stub)
    call.start(**CALLER)

    turn = call.reply(key="1")

    assert turn.state == "done"
    assert stub.calls == [], "a pressed key is unambiguous, do not pay for a model call"


def test_keypad_two_is_a_no() -> None:
    call = flow()
    call.start(**CALLER)

    assert call.reply(key="2").state == "reschedule"


def test_keypad_repeat_asks_the_same_question_again() -> None:
    call = flow(True)
    call.start(**CALLER)

    turn = call.reply(key="3")

    assert turn.state == "confirm_appointment"
    assert not call.finished


def test_an_unmapped_key_counts_as_unclear() -> None:
    call = flow()
    call.start(**CALLER)

    turn = call.reply(key="9")

    assert turn.state == "confirm_appointment"  # re-asked, not routed


def test_the_turn_carries_its_keypad_options() -> None:
    """session.py needs these to configure DTMF capture for the turn."""
    turn = flow().start(**CALLER)

    assert turn.dtmf == {"1": True, "2": False, "3": "repeat"}


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
    call = flow(True)
    call.start(**CALLER)
    call.reply("جی ہاں")

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
    assert turn.state == "confirm_appointment"

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


# --- chat mode, the off-script caller ---


class StubResponder:
    """Records what it was asked to answer and returns a canned line."""

    name = "stub-chat"

    def __init__(self, reply: str = "میں ایک خودکار ایجنٹ ہوں۔", end_call: bool = False) -> None:
        self.reply = reply
        self.end_call = end_call
        self.calls: list[tuple[str, str, str]] = []
        self.histories: list[list[dict[str, str]]] = []

    def respond(self, said: str, asking: str = "", persona: str = "", history: Any = None) -> str:
        self.calls.append((said, asking, persona))
        self.histories.append(list(history or []))
        from voice_agent.llm import Spoken

        return Spoken(self.reply, self.end_call)

    def stream(self, said: str, asking: str = "", persona: str = "", history: Any = None) -> Any:
        """The same reply, one word at a time, the way the real one arrives."""
        self.calls.append((said, asking, persona))
        self.histories.append(list(history or []))
        return StubUtterance(self.reply, self.end_call)


class StubUtterance:
    def __init__(self, reply: str, end_call: bool) -> None:
        self.reply = reply
        self.text = ""
        self.end_call = False
        self._ends = end_call

    async def __aiter__(self) -> Any:
        for word in self.reply.split(" "):
            piece = f"{word} "
            self.text += piece
            yield piece
        self.end_call = self._ends


async def drain(turn: Any) -> str:
    return "".join([piece async for piece in turn.stream]).strip()


def chatty(*values: Any, reply: str = "میں ایک خودکار ایجنٹ ہوں۔", max_chat: int = 3) -> Flow:
    return Flow(
        Script.load(SCRIPT_PATH),
        StubExtractor(*values),
        responder=StubResponder(reply),
        max_chat=max_chat,
    )


def test_the_agent_profile_is_separate_from_the_campaign() -> None:
    """agent.yaml is who the agent is, script.yaml is what this call is about.
    Swapping the campaign must not rewrite the agent."""
    from voice_agent import language as language_module

    assert language_module.load("ur-PK").persona
    assert "persona" not in SCRIPT_PATH.read_text(encoding="utf-8")


def test_an_off_script_question_gets_answered_not_repeated() -> None:
    call = chatty(None, True, True)
    call.start(**CALLER)

    turn = call.reply(said="آپ کون ہیں؟")

    assert turn.say == "میں ایک خودکار ایجنٹ ہوں۔"
    assert turn.expects_reply
    assert turn.state == "confirm_appointment"


def test_chatting_does_not_fill_the_slot() -> None:
    """The whole point. Answering a question is not answering the question."""
    call = chatty(None)
    call.start(**CALLER)

    call.reply(said="آپ کون ہیں؟")

    assert "identity_confirmed" not in call.slots
    assert not call.finished


def test_chat_does_not_burn_a_retry() -> None:
    """Three questions back then three real failures should still be three
    retries, not one. Curiosity is not the same as a bad line."""
    call = chatty(None, None, None, None, None, None)
    call.start(**CALLER)

    for _ in range(3):
        call.reply(said="یہ کیسے کام کرتا ہے؟")

    assert not call.finished


def test_chat_budget_runs_out_and_the_call_still_ends() -> None:
    call = chatty(*[None] * 12, max_chat=2)
    call.start(**CALLER)

    for _ in range(12):
        if call.finished:
            break
        call.reply(said="یہ کیسے کام کرتا ہے؟")

    assert call.finished
    assert call.result.outcome == "handoff"


def test_the_responder_is_told_which_question_is_waiting() -> None:
    responder = StubResponder()
    call = Flow(
        Script.load(SCRIPT_PATH), StubExtractor(None), responder=responder, persona="I am an agent."
    )
    call.start(**CALLER)

    call.reply(said="آپ کون ہیں؟")

    said, asking, persona = responder.calls[0]
    assert said == "آپ کون ہیں؟"
    assert "انس" in asking  # placeholders already filled
    assert persona == "I am an agent."


def test_chat_is_off_by_default() -> None:
    """No responder means the old behaviour, unchanged."""
    call = flow(None, None, None)
    call.start(**CALLER)

    turn = call.reply(said="آپ کون ہیں؟")

    assert turn.say.startswith("السلام علیکم انس")


def test_silence_never_reaches_the_responder() -> None:
    """An empty line is a bad connection, not a question. Do not pay for it."""
    responder = StubResponder()
    call = Flow(Script.load(SCRIPT_PATH), StubExtractor(None), responder=responder)
    call.start(**CALLER)

    call.reply(said="   ")

    assert responder.calls == []


# --- talk mode, no form at all ---


def talk(reply: str = "جی بالکل۔") -> Any:
    from voice_agent.flow import Conversation

    return Conversation(StubResponder(reply), persona="I am an agent.", greeting="السلام علیکم۔")


def test_talk_mode_greets_then_just_talks() -> None:
    call = talk()

    assert call.start().say == "السلام علیکم۔"
    assert call.reply(said="آپ کیسی ہیں").say == "جی بالکل۔"


def test_silence_gets_silence() -> None:
    """The rule that makes it feel like a phone call. Nothing heard, nothing
    said. No prompting, no 'are you still there'."""
    call = talk()
    call.start()

    assert call.reply(said="").say == ""
    assert call.reply(said="   ").say == ""


def test_silence_never_reaches_the_model() -> None:
    from voice_agent.flow import Conversation

    responder = StubResponder()
    call = Conversation(responder, greeting="hi")
    call.start()
    call.reply(said="")

    assert responder.calls == []


def test_talk_mode_has_no_questions_and_no_slots() -> None:
    call = talk()
    call.start()
    call.reply(said="آپ کیسی ہیں")

    assert call.slots == {}
    assert not call.finished, "only the caller ends a conversation"


def test_talk_mode_asks_the_model_nothing_to_steer_back_to() -> None:
    from voice_agent.flow import Conversation

    responder = StubResponder()
    call = Conversation(responder, greeting="hi")
    call.start()
    call.reply(said="آپ کیسی ہیں")

    _, asking, _ = responder.calls[0]
    assert asking == "", "an empty question is what switches the prompt to talk mode"


def test_talk_mode_remembers_the_conversation() -> None:
    from voice_agent.flow import Conversation

    responder = StubResponder("جی۔")
    call = Conversation(responder, greeting="السلام علیکم۔")
    call.start()
    call.reply(said="آپ کون ہیں")
    call.reply(said="اچھا")

    _, _, history = responder.calls[1][0], responder.calls[1][1], responder.histories[1]
    assert history[0] == {"role": "assistant", "content": "السلام علیکم۔"}
    assert {"role": "user", "content": "آپ کون ہیں"} in history


def test_memory_is_capped() -> None:
    from voice_agent.flow import Conversation

    responder = StubResponder("جی۔")
    call = Conversation(responder, greeting="hi", memory_turns=2)
    call.start()
    for n in range(6):
        call.reply(said=f"turn {n}")

    assert len(responder.histories[-1]) <= 4


def test_saying_goodbye_ends_the_call() -> None:
    from voice_agent.flow import Conversation

    call = Conversation(StubResponder("خدا حافظ۔", end_call=True), greeting="hi")
    call.start()

    turn = call.reply(said="اچھا خدا حافظ")

    assert turn.say == "خدا حافظ۔", "the farewell still gets spoken"
    assert call.finished
    assert call.result.outcome == "hung_up"


def test_a_normal_reply_does_not_end_the_call() -> None:
    from voice_agent.flow import Conversation

    call = Conversation(StubResponder("جی۔"), greeting="hi")
    call.start()
    call.reply(said="آپ کیسی ہیں")

    assert not call.finished


# --- streaming, the live call path ---


async def test_an_off_script_answer_streams() -> None:
    call = chatty(None, True, True)
    call.start(**CALLER)

    turn = call.reply_stream(said="آپ کون ہیں؟")

    assert turn.say == "", "the words are not written yet"
    assert turn.state == "confirm_appointment"
    assert turn.expects_reply
    assert await drain(turn) == "میں ایک خودکار ایجنٹ ہوں۔"


async def test_a_script_line_does_not_stream() -> None:
    """It is fixed text with a cached WAV behind it. There is nothing to wait
    for, so there is nothing to stream."""
    call = chatty(True)
    call.start(**CALLER)

    turn = call.reply_stream(said="جی ہاں")

    assert turn.stream is None
    assert turn.say


def test_streaming_spends_the_chat_budget_the_same_way() -> None:
    """A stream the caller talked over still cost an API call."""
    call = chatty(None, None, None, None, max_chat=1)
    call.start(**CALLER)

    first = call.reply_stream(said="آپ کون ہیں؟")
    second = call.reply_stream(said="اور آپ کہاں سے ہیں؟")

    assert first.stream is not None
    assert second.stream is None, "budget spent, back to retries"


async def test_talk_mode_streams_a_reply() -> None:
    call = talk("جی بالکل۔")
    call.start()

    turn = call.reply_stream(said="کیا حال ہے")

    assert await drain(turn) == "جی بالکل۔"


async def test_talk_mode_remembers_a_streamed_turn() -> None:
    """The model must not be told it said nothing, or it repeats itself."""
    from voice_agent.flow import Conversation

    responder = StubResponder("جی۔")
    call = Conversation(responder, greeting="السلام علیکم۔")
    call.start()

    await drain(call.reply_stream(said="کیا حال ہے"))
    await drain(call.reply_stream(said="اور کیا ہو رہا ہے"))

    assert responder.histories[1] == [
        {"role": "assistant", "content": "السلام علیکم۔"},
        {"role": "user", "content": "کیا حال ہے"},
        {"role": "assistant", "content": "جی۔"},
    ]


async def test_a_streamed_goodbye_ends_the_call_only_once_it_is_said() -> None:
    """Whether the caller hung up is the last thing the model writes, so the
    transport cannot be told until the farewell has been handed over."""
    from voice_agent.flow import Conversation

    call = Conversation(StubResponder("خدا حافظ۔", end_call=True), greeting="hi")
    call.start()

    turn = call.reply_stream(said="بس یہی تھا شکریہ")
    assert not call.finished, "not until the farewell has been spoken"

    await drain(turn)
    assert call.finished
    assert call.result.outcome == "hung_up"


async def test_a_streamed_turn_the_caller_talked_over_is_still_remembered() -> None:
    """Barge-in closes the stream early. The agent said part of it out loud,
    so the model has to be told what that part was."""
    from voice_agent.flow import Conversation

    responder = StubResponder("پہلا حصہ دوسرا حصہ")
    call = Conversation(responder, greeting="hi")
    call.start()

    stream = call.reply_stream(said="کچھ").stream.__aiter__()
    heard = await anext(stream)
    await stream.aclose()

    assert responder.histories == [[{"role": "assistant", "content": "hi"}]]
    call.reply_stream(said="اور")
    assert responder.histories[-1][-1] == {"role": "assistant", "content": heard.strip()}


def test_silence_never_reaches_a_streaming_model() -> None:
    from voice_agent.flow import Conversation

    responder = StubResponder()
    call = Conversation(responder, greeting="hi")
    call.start()

    turn = call.reply_stream(said="   ")

    assert turn.stream is None
    assert responder.calls == []


async def test_without_a_greeting_the_model_opens_the_call_in_character() -> None:
    """A purpose written for one call has no fixed greeting. The opener comes
    from the model, is remembered as the agent's line, and the cue that asked
    for it never becomes a caller turn."""
    from voice_agent.flow import Conversation

    responder = StubResponder("Hi, this is City Dental calling about tomorrow.")
    call = Conversation(responder, persona="You confirm dental appointments.", greeting="")

    turn = call.start()
    spoken = await drain(turn)

    assert turn.say == ""
    assert spoken == "Hi, this is City Dental calling about tomorrow."
    assert call._history == [
        {"role": "assistant", "content": "Hi, this is City Dental calling about tomorrow."}
    ]
    from voice_agent.flow import OPENING_CUE

    said, asking, persona = responder.calls[-1]
    assert (said, asking, persona) == (OPENING_CUE, "", "You confirm dental appointments.")
    assert responder.histories[-1] == [], "the opener starts from a clean history"


def test_a_script_call_can_be_ended_from_outside_with_a_last_line() -> None:
    call = flow()
    call.start(**CALLER)

    turn = call.end("My time is up. Goodbye.")

    assert turn.say == "My time is up. Goodbye."
    assert turn.expects_reply is False
    assert call.finished
    assert call.result.outcome == "time_limit"


def test_a_conversation_can_be_ended_from_outside_and_remembers_the_line() -> None:
    from voice_agent.flow import Conversation

    call = Conversation(StubResponder("hi"), greeting="hello")
    call.start()

    call.end("Goodbye.")

    assert call.finished
    assert call.result.outcome == "time_limit"
    assert call._history[-1] == {"role": "assistant", "content": "Goodbye."}
