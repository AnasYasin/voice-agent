"""English language settings. The whole manifest for this language.

Read by language.py and nothing else, exactly like the Urdu one next to it.
"""

from __future__ import annotations

from voice_agent.lang.en.normalize import EnglishNormalizer

# Plain US English. Anas heard the Indian English HD voice the Urdu pack
# borrows and asked for a neutral accent instead. Jenny is the standard
# Neural-tier voice, so it is also the cheaper of the two.
LOCALE = "en-US"
NAME = "English"  # what the caller sees on the language toggle
ORDER = 2  # where it sits on the language toggle
STT_LANGUAGE = "en"
TTS_PROVIDER = "azure"
TTS_VOICE = "en-US-JennyNeural"

# Words the agent expects to hear on the appointment script, plus the ways a
# caller ends a call. The recognizer is biased toward these.
# fmt: off
KEYTERMS = [
    "yes", "yeah", "no", "okay", "sure", "that works", "not possible",
    "o'clock", "morning", "afternoon", "evening", "tonight",
    "today", "tomorrow", "day after tomorrow",
    "reschedule", "cancel", "hang up", "goodbye", "bye",
]
# fmt: on

NORMALIZER = EnglishNormalizer
