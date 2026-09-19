"""German language settings. The whole manifest for this language.

Read by language.py and nothing else, like the other packs beside it.

Azure speaks German natively, so this pack stays on Azure rather than the
ElevenLabs voice Sindhi needs. Recognition is ElevenLabs Scribe, which rates
German in its top accuracy tier, unlike Urdu and Sindhi.
"""

from __future__ import annotations

from voice_agent.lang.de.normalize import GermanNormalizer

LOCALE = "de-DE"
NAME = "Deutsch"  # what the caller sees on the language toggle
STT_LANGUAGE = "de"
TTS_PROVIDER = "azure"
# Female, the standard German Neural voice. de-DE-Seraphina:DragonHDLatestNeural
# is the HD alternative and costs about half as much again per character.
TTS_VOICE = "de-DE-KatjaNeural"

# Words the agent expects on the appointment script, plus how a caller ends a
# call. "morgen" is both tomorrow and morning, which is why "morgens" is here
# separately.
# fmt: off
KEYTERMS = [
    "ja", "nein", "genau", "passt", "in Ordnung", "klar",
    "Uhr", "morgens", "vormittags", "mittags", "nachmittags", "abends",
    "heute", "morgen", "übermorgen",
    "verschieben", "absagen", "auf Wiederhören", "tschüss",
]
# fmt: on

NORMALIZER = GermanNormalizer
