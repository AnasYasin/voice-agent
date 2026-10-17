"""German normaliser. Same three jobs as the other packs.

from_speech()  Flatten an STT transcript: punctuation out, whitespace
               collapsed, lowercased, and ß folded to ss so the two spellings
               of a word compare equal.

for_speech()   Clock times to what a German voice reads correctly. German
               speakers use the 24-hour clock in speech, so 15:00 is "15 Uhr"
               and 09:30 is "9 Uhr 30".

sentences()    Where a sentence ends, for text the model is still writing.
"""

from __future__ import annotations

import re
import unicodedata

_PUNCTUATION = re.compile(r"[\"'„“”»«()\[\],;:!?.]")
_WHITESPACE = re.compile(r"\s+")
_TIME = re.compile(r"\b([01]?\d|2[0-3]):([0-5]\d)\b")
_SENTENCE = re.compile(r"[^.?!]*[.?!]+")


class GermanNormalizer:
    def from_speech(self, text: str) -> str:
        text = unicodedata.normalize("NFC", text)
        text = _PUNCTUATION.sub(" ", text)
        text = _WHITESPACE.sub(" ", text)
        return text.strip().lower().replace("ß", "ss")

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
    """24-hour clock to spoken German. 15:00 is "15 Uhr", 09:30 is "9 Uhr 30"."""
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError(f"not a valid time: {hour}:{minute:02d}")

    if minute:
        return f"{hour} Uhr {minute}"
    return f"{hour} Uhr"
