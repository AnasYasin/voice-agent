"""Sindhi-1: the Sindhi pack heard through Gemini instead of Scribe.

Same script, persona, voice, keyterms and normaliser as lang/sd. Only the
recognizer differs. Scribe returned one 10 Oct 2026 call entirely in
Devanagari, the Indian script, and the rest in garbled Perso-Arabic, and it
has no setting to pin the script. Gemini 3.5 Transcribe takes `sd-Arab-IN`,
Sindhi in Arabic script, so the script is fixed by the language code. The
`IN` is Google's region label; whether the model fits Pakistani speakers is
what putting both packs on the dropdown is meant to show.

Sits beside the Scribe pack rather than replacing it so the two can be
compared on the same callers. Drop whichever loses.
"""

from __future__ import annotations

from voice_agent.lang.sd.voice import KEYTERMS, NORMALIZER, TTS_PROVIDER, TTS_VOICE

__all__ = ["KEYTERMS", "NORMALIZER", "TTS_PROVIDER", "TTS_VOICE"]

LOCALE = "sd1-PK"
NAME = "Sindhi-1"
ORDER = 5
STT_PROVIDER = "gemini"
STT_LANGUAGE = "sd-Arab-IN"
