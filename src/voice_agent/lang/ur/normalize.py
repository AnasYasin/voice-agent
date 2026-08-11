"""Component 3. Urdu normaliser.

Two directions, and they are not symmetric.

from_speech()  A transcript from STT, levelled so it can be compared and
               extracted from. Callers mix Urdu script, Roman Urdu and English
               in one sentence, and the two STT providers punctuate
               differently, so text must be flattened before any comparison
               means anything.

for_speech()   Text on its way to TTS. Azure's Urdu voice only pronounces Urdu
               script correctly, so digits and clock times have to become Urdu
               words first. Roman names are handled in transliterate.py.
"""

from __future__ import annotations

import logging
import re
import unicodedata

log = logging.getLogger(__name__)

# Urdu text uses three digit systems, sometimes in one sentence.
EXTENDED_ARABIC_INDIC = "۰۱۲۳۴۵۶۷۸۹"  # U+06F0..U+06F9, the usual one in Urdu
ARABIC_INDIC = "٠١٢٣٤٥٦٧٨٩"  # U+0660..U+0669
ASCII_DIGITS = "0123456789"

_TO_ASCII_DIGITS = str.maketrans(
    EXTENDED_ARABIC_INDIC + ARABIC_INDIC,
    ASCII_DIGITS + ASCII_DIGITS,
)

# Urdu full stop and comma, plus ordinary Latin punctuation.
_PUNCTUATION = re.compile(r"[۔،؟!?.,;:\"'()\[\]]")
_WHITESPACE = re.compile(r"\s+")

# 0-59, which is every value a clock time can produce. Urdu numbers are
# irregular, so each one is a separate word rather than something you can
# build from tens and units.
#
# NEEDS A NATIVE SPEAKER TO CHECK. 0-19 are safe. 20-59 are the ones most
# likely to have an error, and a wrong number word makes the agent sound
# obviously synthetic.
_URDU_NUMBERS = {
    0: "صفر", 1: "ایک", 2: "دو", 3: "تین", 4: "چار",
    5: "پانچ", 6: "چھ", 7: "سات", 8: "آٹھ", 9: "نو",
    10: "دس", 11: "گیارہ", 12: "بارہ", 13: "تیرہ", 14: "چودہ",
    15: "پندرہ", 16: "سولہ", 17: "سترہ", 18: "اٹھارہ", 19: "انیس",
    20: "بیس", 21: "اکیس", 22: "بائیس", 23: "تیئس", 24: "چوبیس",
    25: "پچیس", 26: "چھببیس", 27: "ستائیس", 28: "اٹھائیس", 29: "انتیس",
    30: "تیس", 31: "اکتیس", 32: "بتیس", 33: "تینتیس", 34: "چونتیس",
    35: "پینتیس", 36: "چھتیس", 37: "سینتیس", 38: "اڑتیس", 39: "انتالیس",
    40: "چالیس", 41: "اکتالیس", 42: "بیالیس", 43: "تینتالیس", 44: "چوالیس",
    45: "پینتالیس", 46: "چھیالیس", 47: "سینتالیس", 48: "اڑتالیس", 49: "انچاس",
    50: "پچاس", 51: "اکاون", 52: "باون", 53: "ترپن", 54: "چون",
    55: "پچپن", 56: "چھپن", 57: "ستاون", 58: "اٹھاون", 59: "انسٹھ",
}

_MORNING = "صبح"
_AFTERNOON = "دوپہر"
_EVENING = "شام"
_NIGHT = "رات"
_OCLOCK = "بجے"
_MINUTES = "منٹ"

_TIME = re.compile(r"\b([01]?\d|2[0-3]):([0-5]\d)\b")


class UrduNormalizer:
    def from_speech(self, text: str) -> str:
        """Flatten an STT transcript so two providers can be compared fairly."""
        text = unicodedata.normalize("NFC", text)
        text = text.translate(_TO_ASCII_DIGITS)
        text = _PUNCTUATION.sub(" ", text)
        text = _WHITESPACE.sub(" ", text)
        # Only affects Roman Urdu and English. Urdu script has no case.
        return text.strip().lower()

    def for_speech(self, text: str) -> str:
        """Replace clock times with Urdu words so the voice reads them properly."""
        return _TIME.sub(lambda m: spoken_time(int(m.group(1)), int(m.group(2))), text)


def spoken_number(value: int) -> str:
    """Urdu word for a number. Covers what clock times need, not all of Urdu."""
    if value in _URDU_NUMBERS:
        return _URDU_NUMBERS[value]
    raise ValueError(f"no Urdu word for {value}, add it to _URDU_NUMBERS")


def spoken_time(hour: int, minute: int = 0) -> str:
    """24-hour clock to spoken Urdu.

    15:00 becomes "شام تین بجے". Urdu says the part of day before the hour,
    and uses a 12-hour clock, so 15 has to become 3.
    """
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError(f"not a valid time: {hour}:{minute:02d}")

    part_of_day = _part_of_day(hour)
    twelve_hour = hour % 12 or 12

    spoken = f"{part_of_day} {spoken_number(twelve_hour)} {_OCLOCK}"
    if minute:
        spoken = f"{spoken} {spoken_number(minute)} {_MINUTES}"

    log.debug("%02d:%02d -> %s", hour, minute, spoken)
    return spoken


def _part_of_day(hour: int) -> str:
    if 4 <= hour < 12:
        return _MORNING
    if 12 <= hour < 16:
        return _AFTERNOON
    if 16 <= hour < 20:
        return _EVENING
    return _NIGHT
