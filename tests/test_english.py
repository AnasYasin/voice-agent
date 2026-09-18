"""The English language pack. Adding a language is a folder, and this is the
proof: nothing outside lang/en/ changed to make these pass."""

from __future__ import annotations

import pytest

from voice_agent.lang.en.normalize import EnglishNormalizer, spoken_time
from voice_agent.language import Normalizer, load

normalizer = EnglishNormalizer()


def test_english_loads_from_the_bare_code_and_the_locale() -> None:
    assert load("en").locale == load("en-US").locale == "en-US"


def test_english_has_a_us_voice_and_the_recognizer_is_told_english() -> None:
    english, urdu = load("en-US"), load("ur-PK")

    assert english.tts_voice == "en-US-JennyNeural"
    assert english.stt_language == "en"
    assert urdu.stt_language == "ur"


def test_the_script_has_the_same_shape_as_the_urdu_one() -> None:
    """A result row looks the same whichever language the call ran in."""
    english, urdu = load("en-US").script, load("ur-PK").script

    assert english.first == urdu.first == "confirm_appointment"
    assert set(english.states) == set(urdu.states)
    assert english.states["confirm_appointment"].dtmf == urdu.states["confirm_appointment"].dtmf


def test_the_greeting_takes_the_caller_fields() -> None:
    ask = load("en-US").script.states["confirm_appointment"].ask

    assert ask.format(name="Anas", date="tomorrow", time="4 PM").startswith("Hello Anas.")


def test_the_normalizer_satisfies_the_protocol() -> None:
    assert isinstance(load("en-US").normalizer, Normalizer)


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Yes, that works. 3 PM!", "yes that works 3 pm"),
        ("  too   much   space ", "too much space"),
        ("No, haan theek hai", "no haan theek hai"),
        ("", ""),
    ],
)
def test_from_speech(raw: str, expected: str) -> None:
    assert normalizer.from_speech(raw) == expected


@pytest.mark.parametrize(
    "hour, minute, expected",
    [(15, 0, "3 PM"), (9, 30, "9:30 AM"), (0, 0, "12 AM"), (12, 0, "12 PM"), (23, 5, "11:05 PM")],
)
def test_spoken_time(hour: int, minute: int, expected: str) -> None:
    assert spoken_time(hour, minute) == expected


def test_for_speech_rewrites_clock_times_and_nothing_else() -> None:
    assert normalizer.for_speech("See you at 15:00, room 302.") == "See you at 3 PM, room 302."


def test_sentences_hold_back_the_unfinished_tail() -> None:
    finished, rest = normalizer.sentences("Hello there. Does that work? Yes it")

    assert finished == ["Hello there.", "Does that work?"]
    assert rest == " Yes it"


def test_an_invalid_time_is_refused() -> None:
    with pytest.raises(ValueError, match="not a valid time"):
        spoken_time(24, 0)
