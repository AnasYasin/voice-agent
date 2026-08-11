"""Urdu language settings. Read by language.py, never imported by components."""

from __future__ import annotations

LOCALE = "ur-PK"
STT_LANGUAGE = "ur"
TTS_VOICE = "ur-PK-UzmaNeural"  # female. ur-PK-AsadNeural is the male voice.

# Words the agent actually expects to hear. Deepgram accepts these as keyterms
# and biases recognition toward them, which matters more on a narrow script
# than raw accuracy does.
KEYTERMS = [
    "جی ہاں", "ہاں", "نہیں", "ٹھیک ہے", "بالکل",
    "بجے", "صبح", "دوپہر", "شام", "رات",
    "آج", "کل", "پرسوں",
    "haan", "nahi", "theek hai", "baje",
]
