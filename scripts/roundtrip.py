#!/usr/bin/env python3
"""Plumbing test for components 1, 2, 3 and 5.

Urdu text -> Azure TTS -> 8 kHz telephone band -> STT -> normalise -> compare.

READ THIS BEFORE TRUSTING THE NUMBER. Synthetic speech is clean, evenly paced
and has no background noise, no GSM codec and no Pakistani accent variation.
STT scores far better on it than on a real call. This proves the four pieces
are wired together and that the Urdu comes back readable. It is not the
Phase 0 accuracy gate, which needs real recordings of real people.

    python scripts/roundtrip.py
    python scripts/roundtrip.py --provider deepgram

Synthesised WAVs stay in the cache dir, so a second run is a disk read and
costs nothing. Delete that directory to force fresh synthesis.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from jiwer import wer

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from voice_agent import audio, stt, tts  # noqa: E402
from voice_agent.config import settings  # noqa: E402
from voice_agent.lang.ur.normalize import UrduNormalizer  # noqa: E402
from voice_agent.lang.ur.voice import KEYTERMS, LOCALE, STT_LANGUAGE, TTS_VOICE  # noqa: E402
from voice_agent.logging_setup import setup_logging  # noqa: E402

# Lines the agent actually says or expects to hear on an appointment call.
LINES = [
    "السلام علیکم، میں ڈاکٹر صاحب کے کلینک سے بات کر رہی ہوں",
    "آپ کی ملاقات کل شام چار بجے ہے",
    "کیا آپ اس وقت آ سکیں گے",
    "جی ہاں ٹھیک ہے",
    "نہیں مجھے کل چاہیے",
    "شکریہ، آپ کا دن اچھا گزرے",
]

OUT = ROOT / "eval" / "samples" / "roundtrip"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", default=os.getenv("STT_PROVIDER", "elevenlabs"))
    parser.add_argument("--voice", default=TTS_VOICE)
    parser.add_argument(
        "--locale",
        default=LOCALE,
        help="language to pin a multilingual or HD voice to. Empty sends bare text.",
    )
    args = parser.parse_args()

    setup_logging(logging.WARNING)  # quiet, the report below is the point
    load_dotenv(ROOT / ".env")

    azure_key = os.getenv("AZURE_SPEECH_KEY", "")
    azure_region = os.getenv("AZURE_SPEECH_REGION", "")
    stt_key = os.getenv(f"{args.provider.upper()}_API_KEY", "")

    if not azure_key:
        parser.error("AZURE_SPEECH_KEY is empty in .env")
    if not stt_key:
        parser.error(f"{args.provider.upper()}_API_KEY is empty in .env")

    voice = tts.build(
        azure_key, azure_region, args.voice, cache_dir=OUT / "cache", locale=args.locale
    )
    ears = stt.build(args.provider, stt_key, keyterms=KEYTERMS)
    normalizer = UrduNormalizer()

    OUT.mkdir(parents=True, exist_ok=True)
    rows = []

    for i, line in enumerate(LINES, 1):
        spoken = normalizer.for_speech(line)
        wide = voice.synthesize(spoken)

        phone = OUT / f"line_{i:02d}_8k.wav"
        audio.to_telephone(wide.path, phone)

        info = audio.probe(phone)
        heard = ears.transcribe(phone, language=STT_LANGUAGE)

        reference = normalizer.from_speech(spoken)
        hypothesis = normalizer.from_speech(heard.text)
        score = wer(reference, hypothesis) if reference else 0.0

        rows.append((i, line, heard.text, score, info, wide.cached, phone))

    report(rows, args.provider)
    return 0


def report(rows, provider: str) -> None:
    print()
    print(f"  round trip via {provider}")
    print(f"  {'-' * 68}")

    for i, said, heard, score, info, cached, _phone in rows:
        flag = "OK " if score <= settings.evaluation.wer_threshold else "!! "
        print(
            f"  {flag}line {i}   WER {score:6.1%}   {info.sample_rate} Hz  "
            f"{info.channels}ch  {info.duration:.1f}s  {'cached' if cached else 'synth'}"
        )
        print(f"      said  {said}")
        print(f"      heard {heard}")
        print()

    scores = [r[3] for r in rows]
    print(f"  {'-' * 68}")
    print(f"  mean WER {sum(scores) / len(scores):6.1%} over {len(scores)} lines")
    print(f"  wav files in {rows[0][6].parent}")
    print()
    print("  Synthetic speech. Not the Phase 0 gate. Listen to a file with:")
    print(f"    ffplay -autoexit {rows[0][6]}")
    print()


if __name__ == "__main__":
    raise SystemExit(main())
