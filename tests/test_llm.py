"""Component 4.

Most tests need no key. The `live` ones call Claude and are skipped without
ANTHROPIC_API_KEY. Run them with `make test-live`.
"""

from __future__ import annotations

import os
from dataclasses import FrozenInstanceError

import pytest

from voice_agent.config import settings
from voice_agent.llm import BOOLEAN, DATETIME, Answer, ClaudeExtractor, SlotExtractor, build

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
