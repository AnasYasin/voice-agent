"""Component 4.

Most tests need no key. The `live` ones call Claude and are skipped without
ANTHROPIC_API_KEY. Run them with `make test-live`.
"""

from __future__ import annotations

import os
import time
from dataclasses import FrozenInstanceError
from types import SimpleNamespace
from typing import Any

import pytest

from voice_agent.config import settings
from voice_agent.llm import (
    BOOLEAN,
    DATETIME,
    END_CALL,
    Answer,
    ClaudeExtractor,
    ClaudeResponder,
    SlotExtractor,
    Utterance,
    build,
)

ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY", "")


# --- no key needed ---


def test_build_returns_an_extractor() -> None:
    assert isinstance(build("fake-key"), SlotExtractor)


def test_model_defaults_to_config() -> None:
    assert build("fake-key").model == settings.llm.model


def test_model_can_be_overridden() -> None:
    """So the eval harness can score two models on identical utterances."""
    assert build("fake-key", "claude-haiku-4-5").model == "claude-haiku-4-5"


def test_unknown_slot_type_raises_rather_than_guessing() -> None:
    with pytest.raises(ValueError, match="unknown slot type"):
        build("fake-key").extract("جی ہاں", "colour")


def test_silence_is_unclear_and_costs_no_api_call(monkeypatch: pytest.MonkeyPatch) -> None:
    """A dead line must not bill us. If this reaches the client it will fail,
    because the fake key would be rejected."""
    answer = build("fake-key").extract("   ", BOOLEAN)

    assert answer.value is None
    assert answer.understood is False


def test_unclear_is_not_the_same_as_no() -> None:
    """The distinction the whole component exists for. Booking a patient who
    never agreed is the failure that matters."""
    unclear = Answer(value=None, slot_type=BOOLEAN, said="...", model="m")
    said_no = Answer(value=False, slot_type=BOOLEAN, said="نہیں", model="m")

    assert unclear.understood is False
    assert said_no.understood is True
    assert said_no.value is False


def test_answer_is_immutable() -> None:
    answer = Answer(value=True, slot_type=BOOLEAN, said="جی ہاں", model="m")
    with pytest.raises(FrozenInstanceError):
        answer.value = False  # type: ignore[misc]


# --- hits the real API ---


@pytest.fixture
def extractor() -> ClaudeExtractor:
    return build(ANTHROPIC_KEY)  # type: ignore[return-value]


live = pytest.mark.skipif(not ANTHROPIC_KEY, reason="no ANTHROPIC_API_KEY")


@pytest.mark.live
@live
@pytest.mark.parametrize(
    "said, expected",
    [
        ("جی ہاں", True),
        ("نہیں", False),
        ("جی ہاں ٹھیک ہے", True),
        ("haan ji", True),
        ("nahi", False),
        # Code switching, which is how people actually talk on these calls.
        ("جی haan, theek hai", True),
        ("no, that does not work for me", False),
    ],
)
def test_yes_and_no_in_urdu_roman_and_english(
    extractor: ClaudeExtractor, said: str, expected: bool
) -> None:
    assert extractor.extract(said, BOOLEAN).value is expected


@pytest.mark.live
@live
@pytest.mark.parametrize(
    "said",
    [
        "پتہ نہیں",  # "I don't know"
        "کیا؟",  # "What?"
        "کون بول رہا ہے",  # "Who is speaking" — a question back at us
    ],
)
def test_ambiguous_replies_come_back_unclear(extractor: ClaudeExtractor, said: str) -> None:
    """Better to ask again or offer the keypad than to book the wrong thing."""
    assert extractor.extract(said, BOOLEAN).value is None


@pytest.mark.live
@live
def test_extracts_a_time(extractor: ClaudeExtractor) -> None:
    answer = extractor.extract("کل شام چار بجے", DATETIME)

    assert answer.understood
    assert isinstance(answer.value, str)


@pytest.mark.live
@live
def test_no_time_given_comes_back_unclear(extractor: ClaudeExtractor) -> None:
    assert extractor.extract("مجھے نہیں معلوم", DATETIME).value is None


@pytest.mark.live
@live
def test_guidance_from_the_script_is_applied(extractor: ClaudeExtractor) -> None:
    """script.yaml carries the Roman-Urdu hints. Without them "bilkul" is not
    obviously a yes to a model reading it cold."""
    guidance = 'Treat "bilkul" and "theek hai" as yes.'

    assert extractor.extract("bilkul", BOOLEAN, guidance).value is True


# --- streaming, for the live call ---


class FakeStream:
    """Stands in for the SDK's stream helper. Records the request it was given."""

    def __init__(self, pieces: list[str], stop_sequence: str | None) -> None:
        self.pieces = pieces
        self.stop_sequence = stop_sequence
        self.closed = False

    async def __aenter__(self) -> FakeStream:
        return self

    async def __aexit__(self, *_: object) -> None:
        self.closed = True

    @property
    def text_stream(self) -> Any:
        async def pieces() -> Any:
            for piece in self.pieces:
                yield piece

        return pieces()

    async def get_final_message(self) -> Any:
        return SimpleNamespace(stop_sequence=self.stop_sequence)


class FakeClient:
    def __init__(self, pieces: list[str], stop_sequence: str | None = None) -> None:
        self.stream = FakeStream(pieces, stop_sequence)
        self.request: dict[str, Any] = {}
        self.messages = SimpleNamespace(stream=self._stream)

    def _stream(self, **request: Any) -> FakeStream:
        self.request = request
        return self.stream


async def test_the_text_arrives_before_the_reply_is_finished() -> None:
    client = FakeClient(["پہلا", " دوسرا"])
    utterance = Utterance(client, model="m")

    assert [piece async for piece in utterance] == ["پہلا", " دوسرا"]
    assert utterance.text == "پہلا دوسرا"


async def test_a_stop_sequence_is_how_a_hangup_is_reported() -> None:
    """Not JSON. Half an object does not parse, so a structured reply cannot be
    read until it is complete, which is the wait streaming exists to remove."""
    utterance = Utterance(FakeClient(["خدا حافظ۔"], stop_sequence=END_CALL), model="m")

    assert not utterance.end_call, "not known until the reply has run out"
    async for _ in utterance:
        pass

    assert utterance.end_call


async def test_a_normal_reply_does_not_end_the_call() -> None:
    utterance = Utterance(FakeClient(["جی۔"], stop_sequence=None), model="m")
    async for _ in utterance:
        pass

    assert utterance.end_call is False


async def test_abandoning_the_reply_closes_the_request() -> None:
    """Barge-in. Nobody is listening, so stop paying for the rest of it."""
    client = FakeClient(["پہلا", " دوسرا", " تیسرا"])
    pieces = Utterance(client, model="m").__aiter__()

    await anext(pieces)
    await pieces.aclose()

    assert client.stream.closed


async def test_the_hangup_marker_is_sent_as_a_stop_sequence() -> None:
    responder = ClaudeResponder("fake-key")
    client = FakeClient([])
    responder.async_client = client

    async for _ in responder.stream("کیا حال ہے", persona="I am an agent."):
        pass

    assert client.request["stop_sequences"] == [END_CALL]
    assert END_CALL in client.request["system"][0]["text"]
    assert "I am an agent." in client.request["system"][0]["text"]


async def test_the_streamed_prompt_is_still_cached() -> None:
    """The rules and the persona are identical on every turn and they are long.
    Losing the breakpoint would put them back in front of every reply."""
    responder = ClaudeResponder("fake-key")
    responder.async_client = client = FakeClient([])

    async for _ in responder.stream("کیا حال ہے"):
        pass

    assert client.request["system"][0]["cache_control"] == {"type": "ephemeral"}


@pytest.mark.live
@live
async def test_a_real_reply_arrives_in_pieces() -> None:
    responder = ClaudeResponder(ANTHROPIC_KEY)

    utterance = responder.stream("السلام علیکم، آپ کون ہیں؟", persona="آپ ایک کلینک کی ایجنٹ ہیں۔")
    pieces = [piece async for piece in utterance]

    assert len(pieces) > 1, "one piece is not a stream"
    assert "".join(pieces).strip() == utterance.text.strip()
    assert not utterance.end_call


@pytest.mark.live
@live
async def test_a_real_goodbye_ends_the_call() -> None:
    """And the marker itself never reaches the voice, because the API stops at
    it rather than writing it."""
    responder = ClaudeResponder(ANTHROPIC_KEY)

    utterance = responder.stream(
        "بس یہی تھا، شکریہ، خدا حافظ", persona="آپ ایک کلینک کی ایجنٹ ہیں۔"
    )
    async for _ in utterance:
        pass

    assert utterance.end_call
    assert END_CALL not in utterance.text


@pytest.mark.live
@live
async def test_the_first_words_arrive_before_the_last_ones() -> None:
    """The point of streaming the model at all. A caller waits for the first
    sentence, not the paragraph."""
    responder = ClaudeResponder(ANTHROPIC_KEY)

    started = time.monotonic()
    pieces = responder.stream("مجھے اپنی ملاقات کے بارے میں بتائیں", persona="ایجنٹ").__aiter__()
    await anext(pieces)
    first = time.monotonic() - started

    async for _ in pieces:
        pass
    whole = time.monotonic() - started

    assert first < whole, f"first piece {first:.2f}s, whole reply {whole:.2f}s"
