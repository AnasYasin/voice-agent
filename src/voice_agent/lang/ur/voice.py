"""Urdu language settings. The whole manifest for this language.

Read by language.py and nothing else. Components receive the Language
object it builds, so no component ever imports anything Urdu-specific.
Adding a language is a sibling folder, not a code change.
"""

from __future__ import annotations

from voice_agent.lang.ur.normalize import UrduNormalizer

LOCALE = "ur-PK"
NAME = "اردو"  # what the caller sees on the language toggle
STT_LANGUAGE = "ur"
# Female. An Indian English Dragon HD voice speaking Urdu as a secondary
# locale, which carries a South Asian accent the ur-PK voices do not.
# Azure has only four native Urdu voices and all are the older Neural tier:
# ur-PK-UzmaNeural, ur-PK-AsadNeural, ur-IN-GulNeural, ur-IN-SalmanNeural.
#
# An HD voice picks its own language when it is handed bare text, so LOCALE
# above has to reach TTS as well. tts.py wraps the line in <lang> to pin it.
TTS_VOICE = "en-IN-Neerja:DragonHDLatestNeural"

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
