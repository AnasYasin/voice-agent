"""English language settings. The whole manifest for this language.

Read by language.py and nothing else, exactly like the Urdu one next to it.
"""

from __future__ import annotations

from voice_agent.lang.en.normalize import EnglishNormalizer

# Indian English rather than US or UK. The callers are in Pakistan and this is
# the accent they hear every day. It is also the native locale of the voice
# below, which the Urdu pack borrows, so the agent is the same person in both
# languages. en-US-JennyNeural is the cheaper Neural-tier alternative if the
# HD voice ever matters on the bill.
LOCALE = "en-IN"
NAME = "English"  # what the caller sees on the language toggle
STT_LANGUAGE = "en"
TTS_VOICE = "en-IN-Neerja:DragonHDLatestNeural"

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
