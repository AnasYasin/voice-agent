"""Sindhi normaliser. Same three jobs as the Urdu one.

Sindhi is written in an extended Arabic script and shares Urdu's digit systems,
its full stop ۔ and its question mark ؟, so the flattening and the sentence
splitting are the same. Only the spoken clock differs.

NEEDS A NATIVE SPEAKER TO CHECK. The hour words and the parts of the day below
are the author's best knowledge of Sindhi, not a speaker's. Minutes are left as
digits for the voice to read, because the number words past twelve are not
known well enough to ship.
"""

from __future__ import annotations

import re
import unicodedata

EXTENDED_ARABIC_INDIC = "۰۱۲۳۴۵۶۷۸۹"
ARABIC_INDIC = "٠١٢٣٤٥٦٧٨٩"
ASCII_DIGITS = "0123456789"

_TO_ASCII_DIGITS = str.maketrans(EXTENDED_ARABIC_INDIC + ARABIC_INDIC, ASCII_DIGITS + ASCII_DIGITS)
_PUNCTUATION = re.compile(r"[۔،؟!?.,;:\"'()\[\]]")
_WHITESPACE = re.compile(r"\s+")
_TIME = re.compile(r"\b([01]?\d|2[0-3]):([0-5]\d)\b")
_SENTENCE = re.compile(r"[^۔؟!?.]*[۔؟!?.]+")

# fmt: off
_HOURS = {
    1: "هڪ", 2: "ٻه", 3: "ٽي", 4: "چار", 5: "پنج", 6: "ڇهه",
    7: "ست", 8: "اٺ", 9: "نو", 10: "ڏهه", 11: "يارنهن", 12: "ٻارنهن",
}
# fmt: on
_MORNING, _AFTERNOON, _EVENING, _NIGHT = "صبح", "منجهند", "شام", "رات"
_OCLOCK = "وڳي"
_MINUTES = "منٽ"


class SindhiNormalizer:
    def from_speech(self, text: str) -> str:
        text = unicodedata.normalize("NFC", text)
        text = text.translate(_TO_ASCII_DIGITS)
        text = _PUNCTUATION.sub(" ", text)
        return _WHITESPACE.sub(" ", text).strip().lower()

    def for_speech(self, text: str) -> str:
        return _TIME.sub(lambda match: spoken_time(int(match.group(1)), int(match.group(2))), text)

    def sentences(self, text: str) -> tuple[list[str], str]:
        finished = []
        rest = text
        while match := _SENTENCE.match(rest):
            finished.append(match.group().strip())
            rest = rest[match.end() :]
        return [sentence for sentence in finished if sentence], rest


def spoken_time(hour: int, minute: int = 0) -> str:
    """24-hour clock to spoken Sindhi: part of day, hour word, وڳي."""
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError(f"not a valid time: {hour}:{minute:02d}")

    twelve_hour = hour % 12 or 12
    spoken = f"{_part_of_day(hour)} {_HOURS[twelve_hour]} {_OCLOCK}"
    if minute:
        spoken = f"{spoken} {minute} {_MINUTES}"
    return spoken


def _part_of_day(hour: int) -> str:
    if 4 <= hour < 12:
        return _MORNING
    if 12 <= hour < 16:
        return _AFTERNOON
    if 16 <= hour < 20:
        return _EVENING
    return _NIGHT
