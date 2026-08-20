"""Urdu language settings. The whole manifest for this language.

Read by language.py and nothing else. Components receive the Language
object it builds, so no component ever imports anything Urdu-specific.
Adding a language is a sibling folder, not a code change.
"""

from __future__ import annotations

from voice_agent.lang.ur.normalize import UrduNormalizer

LOCALE = "ur-PK"
STT_LANGUAGE = "ur"
TTS_VOICE = "ur-PK-UzmaNeural"  # female. ur-PK-AsadNeural is the male voice.

# Words the agent actually expects to hear. Deepgram accepts these as keyterms
# and biases recognition toward them, which matters more on a narrow script
# than raw accuracy does.
# fmt: off
KEYTERMS = [
    "جی ہاں", "ہاں", "نہیں", "ٹھیک ہے", "بالکل",
    "بجے", "صبح", "دوپہر", "شام", "رات",
    "آج", "کل", "پرسوں",
    "haan", "nahi", "theek hai", "baje",
]
# fmt: on

# language.py instantiates this. Every language supplies one.
NORMALIZER = UrduNormalizer
