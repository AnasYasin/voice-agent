"""Component 3. Pure logic, no network, no keys."""

from __future__ import annotations

import pytest

from voice_agent.lang.ur.normalize import UrduNormalizer, spoken_number, spoken_time

n = UrduNormalizer()


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("جی ہاں، تین بجے ٹھیک ہے۔", "جی ہاں تین بجے ٹھیک ہے"),
        ("Haan Ji, 3 baje!", "haan ji 3 baje"),
        ("مجھے ۵ بجے چاہیے", "مجھے 5 بجے چاہیے"),
        ("٧ بجے", "7 بجے"),
        ("  bohat   ziada   space  ", "bohat ziada space"),
        # The normal case on a real call: two scripts in one sentence.
        ("جی haan, 3 baje THEEK hai", "جی haan 3 baje theek hai"),
        ("", ""),
    ],
)
def test_from_speech(raw: str, expected: str) -> None:
    assert n.from_speech(raw) == expected


def test_all_three_digit_systems_collapse_to_one() -> None:
    """Otherwise word error rate punishes a provider for a cosmetic choice."""
    assert n.from_speech("۳ بجے") == n.from_speech("٣ بجے") == n.from_speech("3 بجے")


def test_from_speech_is_idempotent() -> None:
    once = n.from_speech("جی ہاں، ۳ بجے۔")
    assert n.from_speech(once) == once


@pytest.mark.parametrize(
    "hour, minute, expected",
    [
        (9, 0, "صبح نو بجے"),
        (13, 0, "دوپہر ایک بجے"),
        (15, 0, "دوپہر تین بجے"),
        (18, 0, "شام چھ بجے"),
        (21, 0, "رات نو بجے"),
        (0, 0, "رات بارہ بجے"),
        (12, 0, "دوپہر بارہ بجے"),
        (15, 30, "دوپہر تین بجے تیس منٹ"),
    ],
)
def test_spoken_time(hour: int, minute: int, expected: str) -> None:
    assert spoken_time(hour, minute) == expected


# fmt: off
@pytest.mark.parametrize(
    "hour, part",
    [
        (3, "رات"), (4, "صبح"),  # night becomes morning at 4
        (11, "صبح"), (12, "دوپہر"),  # morning becomes noon at 12
        (15, "دوپہر"), (16, "شام"),  # noon becomes evening at 16
        (19, "شام"), (20, "رات"),  # evening becomes night at 20
    ],
)
def test_part_of_day_boundaries(hour: int, part: str) -> None:
    """A native speaker should confirm these cutoffs. They are judgement calls,
    not facts, and getting them wrong makes the agent sound foreign."""
    assert spoken_time(hour, 0).startswith(part)
# fmt: on


def test_for_speech_replaces_clock_times() -> None:
    assert n.for_speech("آپ کی ملاقات 15:00 بجے ہے") == "آپ کی ملاقات دوپہر تین بجے بجے ہے"


def test_for_speech_leaves_other_text_alone() -> None:
    assert n.for_speech("کوئی وقت نہیں") == "کوئی وقت نہیں"


@pytest.mark.parametrize("hour, minute", [(24, 0), (-1, 0), (12, 60), (12, -1)])
def test_invalid_time_raises(hour: int, minute: int) -> None:
    with pytest.raises(ValueError):
        spoken_time(hour, minute)


def test_every_minute_of_the_clock_is_speakable() -> None:
    """Regression. The table used to hold a handful of values, so an ordinary
    time like 14:37 crashed. Any minute a clock can show must work."""
    for minute in range(60):
        assert spoken_time(14, minute)


def test_every_hour_of_the_clock_is_speakable() -> None:
    for hour in range(24):
        assert spoken_time(hour, 0)


def test_for_speech_handles_any_time_in_text() -> None:
    """Regression. This used to raise on most real appointment times."""
    assert "سینتیس" in n.for_speech("ملاقات 14:37 پر ہے")


def test_unknown_number_raises_rather_than_guessing() -> None:
    """Better a loud failure than the voice saying something wrong."""
    with pytest.raises(ValueError, match="no Urdu word"):
        spoken_number(100)


# --- sentence boundaries, for the streaming path ---


def test_a_finished_sentence_comes_out_whole() -> None:
    finished, rest = n.sentences("جی ہاں۔")

    assert finished == ["جی ہاں۔"]
    assert rest == ""


def test_an_unfinished_sentence_is_held_back() -> None:
    """Half a word out of the voice is worse than waiting for the rest of it."""
    finished, rest = n.sentences("جی ہاں۔ میں انس بول")

    assert finished == ["جی ہاں۔"]
    assert rest == " میں انس بول"


def test_several_sentences_at_once() -> None:
    finished, _ = n.sentences("پہلا۔ دوسرا؟ تیسرا!")

    assert finished == ["پہلا۔", "دوسرا؟", "تیسرا!"]


def test_a_latin_full_stop_ends_a_sentence_too() -> None:
    """The model writes Urdu but still reaches for a Latin full stop."""
    finished, rest = n.sentences("theek hai. aur")

    assert finished == ["theek hai."]
    assert rest == " aur"


def test_nothing_written_yet_is_not_a_sentence() -> None:
    assert n.sentences("") == ([], "")


def test_feeding_it_a_letter_at_a_time_gives_the_same_answer() -> None:
    """Which is how it is actually used: the model writes a few characters at
    a time and this is asked after each one."""
    whole = "جی ہاں۔ چار بجے ٹھیک ہے۔"
    buffer = ""
    finished = []

    for letter in whole:
        buffer += letter
        found, buffer = n.sentences(buffer)
        finished += found

    assert finished == ["جی ہاں۔", "چار بجے ٹھیک ہے۔"]
    assert buffer == ""
