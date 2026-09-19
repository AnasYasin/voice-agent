"""The German language pack. No network."""

from __future__ import annotations

import pytest

from voice_agent.lang.de.normalize import GermanNormalizer, spoken_time
from voice_agent.language import Normalizer, load

normalizer = GermanNormalizer()


def test_german_loads_on_an_azure_voice() -> None:
    language = load("de-DE")

    assert language.locale == "de-DE"
    assert language.name == "Deutsch"
    assert language.stt_language == "de"
    assert language.tts_provider == "azure"
    assert language.tts_voice == "de-DE-KatjaNeural"


def test_the_script_has_the_same_shape_as_the_urdu_one() -> None:
    german, urdu = load("de-DE").script, load("ur-PK").script

    assert german.first == urdu.first == "confirm_appointment"
    assert set(german.states) == set(urdu.states)
    assert german.states["confirm_appointment"].dtmf == urdu.states["confirm_appointment"].dtmf


def test_the_greeting_takes_the_caller_fields() -> None:
    ask = load("de-DE").script.states["confirm_appointment"].ask

    assert ask.format(name="Anas", date="morgen", time="16 Uhr").startswith("Guten Tag Anas.")


def test_the_normalizer_satisfies_the_protocol() -> None:
    assert isinstance(load("de-DE").normalizer, Normalizer)


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Ja, das passt!", "ja das passt"),
        ("Nein, leider nicht.", "nein leider nicht"),
        ("  zu   viel   Platz ", "zu viel platz"),
        ("Grüße aus der Straße", "grüsse aus der strasse"),
        ("", ""),
    ],
)
def test_from_speech(raw: str, expected: str) -> None:
    assert normalizer.from_speech(raw) == expected


def test_the_two_spellings_of_sharp_s_compare_equal() -> None:
    """A recognizer may write either. Folding them stops a cosmetic choice
    counting as a different answer."""
    assert normalizer.from_speech("Straße") == normalizer.from_speech("Strasse")


@pytest.mark.parametrize(
    "hour, minute, expected",
    [(15, 0, "15 Uhr"), (9, 30, "9 Uhr 30"), (0, 0, "0 Uhr"), (23, 5, "23 Uhr 5")],
)
def test_spoken_time_uses_the_24_hour_clock(hour: int, minute: int, expected: str) -> None:
    """German speakers say the 24-hour clock out loud, unlike English."""
    assert spoken_time(hour, minute) == expected


def test_for_speech_rewrites_clock_times_and_nothing_else() -> None:
    assert (
        normalizer.for_speech("Ihr Termin um 15:00, Raum 302.") == "Ihr Termin um 15 Uhr, Raum 302."
    )


def test_sentences_hold_back_the_unfinished_tail() -> None:
    finished, rest = normalizer.sentences("Guten Tag. Passt Ihnen das? Ich")

    assert finished == ["Guten Tag.", "Passt Ihnen das?"]
    assert rest == " Ich"


def test_an_invalid_time_is_refused() -> None:
    with pytest.raises(ValueError, match="not a valid time"):
        spoken_time(24, 0)
