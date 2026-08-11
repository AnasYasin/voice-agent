#!/usr/bin/env python3
"""Run one recording through both STT providers and compare them.

The manual counterpart to the eval harness. Use it on your own voice to get a
feel for how each provider handles Urdu before scoring anything properly.

    python scripts/compare_stt.py myvoice.m4a

Writes the 8 kHz telephone-band version next to the original so you can listen
to what the providers actually hear.
"""

from __future__ import annotations

import logging
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from dotenv import load_dotenv  # noqa: E402

from voice_agent.audio import probe, to_telephone  # noqa: E402
from voice_agent.lang.ur.normalize import UrduNormalizer  # noqa: E402
from voice_agent.lang.ur.voice import KEYTERMS, STT_LANGUAGE  # noqa: E402
from voice_agent.logging_setup import setup_logging  # noqa: E402
from voice_agent.stt import DeepgramSTT, ElevenLabsSTT  # noqa: E402


def main() -> int:
    setup_logging(logging.WARNING)  # quiet, the output below is the point
    load_dotenv()

    if len(sys.argv) < 2:
        print(__doc__)
        return 1

    source = Path(sys.argv[1])
    if not source.exists():
        print(f"not found: {source}")
        return 1

    phone = source.with_name(f"{source.stem}_8khz.wav")
    to_telephone(source, phone)

    print(f"\noriginal   {source}")
    print(f"           {probe(source)}")
    print(f"telephone  {phone}")
    print(f"           {probe(phone)}")
    print("\nplay the second one to hear what the providers get.\n")

    normalizer = UrduNormalizer()
    providers = []

    if os.getenv("ELEVENLABS_API_KEY"):
        providers.append(ElevenLabsSTT(os.environ["ELEVENLABS_API_KEY"], keyterms=KEYTERMS))
    if os.getenv("DEEPGRAM_API_KEY"):
        providers.append(DeepgramSTT(os.environ["DEEPGRAM_API_KEY"], keyterms=KEYTERMS))

    if not providers:
        print("No keys in .env, so nothing to transcribe.")
        print("Set ELEVENLABS_API_KEY and/or DEEPGRAM_API_KEY and run again.")
        return 1

    for provider in providers:
        started = time.perf_counter()
        result = provider.transcribe(phone, language=STT_LANGUAGE)
        elapsed = time.perf_counter() - started

        print(f"--- {provider.name}  ({elapsed:.1f}s) ---")
        print(f"  raw         {result.text or '(nothing recognised)'}")
        print(f"  normalised  {normalizer.from_speech(result.text)}")
        if result.confidence is not None:
            print(f"  confidence  {result.confidence:.2f}")
        if result.detected_language:
            print(f"  language    {result.detected_language}", end="")
            if result.language_confidence is not None:
                print(f"  ({result.language_confidence:.2f})", end="")
            print()
        print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
