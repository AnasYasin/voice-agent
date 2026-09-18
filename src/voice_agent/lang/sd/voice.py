"""Sindhi language settings. The whole manifest for this language.

Read by language.py and nothing else, like the Urdu and English ones.

Azure has no Sindhi voice (737 voices on our account, none for sd, checked
18 Sep 2026) and neither does Google. ElevenLabs eleven_v3 lists Sindhi, so
this is the first pack on the ElevenLabs voice. Recognition stays on ElevenLabs
Scribe, whose published Sindhi accuracy sits in the same moderate tier as Urdu.
Like Urdu, nothing here is proven until real Sindhi recordings say so.
"""

from __future__ import annotations

from voice_agent.lang.sd.normalize import SindhiNormalizer

LOCALE = "sd-PK"
NAME = "سنڌي"  # what the caller sees on the language toggle
STT_LANGUAGE = "snd"
TTS_PROVIDER = "elevenlabs"
# An ElevenLabs voice id, not a name. Sarah, a default voice every account has,
# because the free plan refuses Voice Library voices over the API. She is an
# American voice speaking Sindhi. The library has female voices by Urdu and
# Hindi speakers (Reva UT6USLtoAlXHj5k4sOLY, used 16,000 times, is the obvious
# pick) and any of them needs the Starter plan. Swap the id here, nothing else.
TTS_VOICE = "EXAVITQu4vr4xnSDxMaL"

# Words the agent expects on the appointment script, plus how a caller ends a
# call. NEEDS A NATIVE SPEAKER TO CHECK, like the script and the number words.
# fmt: off
KEYTERMS = [
    "ها", "جي ها", "نه", "ٺيڪ آهي", "بلڪل",
    "وڳي", "صبح", "منجهند", "شام", "رات",
    "اڄ", "سڀاڻي",
    "haan", "na", "theek aa",
]
# fmt: on

NORMALIZER = SindhiNormalizer
