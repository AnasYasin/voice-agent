"""Sindhi language settings. The whole manifest for this language.

Read by language.py and nothing else, like the Urdu and English ones.

Azure has no Sindhi voice (737 voices on our account, none for sd, checked
18 Sep 2026) and neither does Google. ElevenLabs speaks it, so this is the
first pack on the ElevenLabs voice.

Recognition is Gemini 3.5 Transcribe Live, not Scribe. Scribe returned one
10 Oct 2026 call entirely in Devanagari, the Indian script, and the rest in
garbled Perso-Arabic, and has no setting to pin the script. Gemini takes
`sd-Arab-IN`, Sindhi in Arabic script, so the script is fixed by the code. The
`IN` is Google's region label; on the 10 Oct recordings it heard Pakistani
callers coherently. Both ran side by side as Sindhi and Sindhi-1 on 11 Oct
2026 and Anas kept this one.
"""

from __future__ import annotations

from voice_agent.lang.sd.normalize import SindhiNormalizer

LOCALE = "sd-PK"
NAME = "Sindhi"  # what the caller sees on the language toggle
ORDER = 4  # where it sits on the language toggle
STT_PROVIDER = "gemini"  # instead of STT_PROVIDER in .env, see the docstring
STT_LANGUAGE = "sd-Arab-IN"
TTS_PROVIDER = "elevenlabs"
# An ElevenLabs voice id, not a name. Marvi, "Warm, Proud Narrator", a library
# voice tagged Sindhi. Chosen by a native speaker on 11 Oct 2026 from the twelve
# Sindhi-tagged library voices plus Sarah, the default: "closest, pronunciation
# still not good but better than the others". Library voices need the Starter
# plan over the API, which the account is on. Swap the id here, nothing else.
TTS_VOICE = "QfY4EJSXrwbmN0wR4597"

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
