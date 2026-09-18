"""English normaliser. Same three jobs as the Urdu one, far less to do.

from_speech()  Flatten an STT transcript: punctuation out, whitespace
               collapsed, lowercased, so two providers compare fairly and the
               extractor sees one shape.

for_speech()   Clock times to words the voice reads right. "15:00" is read as
               "fifteen hundred" by an English voice; "3 PM" is not.

sentences()    Where a sentence ends, for text the model is still writing.
"""

from __future__ import annotations

import re

_PUNCTUATION = re.compile(r"[\"'()\[\],;:!?.]")
_WHITESPACE = re.compile(r"\s+")
_TIME = re.compile(r"\b([01]?\d|2[0-3]):([0-5]\d)\b")
_SENTENCE = re.compile(r"[^.?!]*[.?!]+")


class EnglishNormalizer:
    def from_speech(self, text: str) -> str:
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
    """24-hour clock to the 12-hour form an English voice reads correctly.
    15:00 becomes "3 PM", 09:30 becomes "9:30 AM"."""
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError(f"not a valid time: {hour}:{minute:02d}")

    twelve_hour = hour % 12 or 12
    half = "AM" if hour < 12 else "PM"
    if minute:
        return f"{twelve_hour}:{minute:02d} {half}"
    return f"{twelve_hour} {half}"
