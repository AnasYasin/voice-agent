#!/usr/bin/env python3
"""Score recognizers on recordings with known text.

    python eval/run_stt_eval.py --pack sd-PK --expected eval/expected_sd.csv

Every prepared sample goes through every recognizer that has a key. Both sides
are flattened by the pack's normalizer, so a stray full stop or a Latin digit
does not count as a wrong word. Two numbers per recognizer, lower is better:

  WER   words wrong, out of the words in the reference
  CER   characters wrong, which is fairer to a language whose words are long

Then every transcript next to its reference, so a speaker can see what each
one actually heard. Writes eval/results_<pack>.md.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import httpx
import jiwer
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from voice_agent import language as language_module
from voice_agent import stt
from voice_agent.audio import pcm
from voice_agent.config import settings

PREPARED = Path(__file__).parent / "samples" / "prepared"

# Each recognizer spells the language its own way.
LANGUAGE_CODES = {
    "sd": {"scribe": "snd", "gemini": "sd-Arab-IN", "deepgram": "sd"},
    "ur": {"scribe": "urd", "gemini": "ur-PK", "deepgram": "ur"},
}


def stream_file(listener: stt.Listening, path: Path) -> str:
    """One file as one turn through a streaming recognizer."""

    async def run() -> str:
        await listener.open()
        try:
            audio = pcm(path)
            step = settings.audio.sample_rate * 2 // 10
            for at in range(0, len(audio), step):
                await listener.push(audio[at : at + step])
            return await listener.commit()
        finally:
            await listener.close()

    return asyncio.run(run())


def gemini_batch(path: Path, code: str, key: str) -> str:
    """The non-live Gemini model, through the Files API. Paid tier is
    $0.003 a minute; the free tier allows 25 requests a day."""
    base = "https://generativelanguage.googleapis.com"
    headers = {"x-goog-api-key": key}
    with httpx.Client(timeout=180) as http:
        upload = http.post(
            f"{base}/upload/v1beta/files",
            headers={**headers, "X-Goog-Upload-Protocol": "multipart"},
            files={
                "metadata": (
                    None,
                    json.dumps({"file": {"display_name": path.name}}),
                    "application/json",
                ),
                "file": (path.name, path.read_bytes(), "audio/wav"),
            },
        )
        upload.raise_for_status()
        info = upload.json()["file"]
        while info.get("state") == "PROCESSING":
            time.sleep(2)
            info = http.get(f"{base}/v1beta/{info['name']}", headers=headers).json()
        response = http.post(
            f"{base}/v1beta/interactions",
            headers=headers,
            json={
                "model": "gemini-3.5-transcribe",
                "input": [{"type": "audio", "uri": info["uri"], "mime_type": "audio/wav"}],
                "generation_config": {"transcription_config": {"language_codes": [code]}},
            },
        )
        response.raise_for_status()
        return " ".join(
            c["text"] for step in response.json()["steps"] for c in step["content"] if "text" in c
        )


def recognizers(code: str) -> dict[str, Any]:
    """Name to a callable(path) -> transcript, for every recognizer with a key."""
    codes = LANGUAGE_CODES[code]
    found: dict[str, Any] = {}
    if key := os.getenv("ELEVENLABS_API_KEY"):
        scribe = stt.ElevenLabsSTT(key)
        found["scribe_v1_file"] = lambda p: scribe.transcribe(p, codes["scribe"]).text
        found["scribe_v2_realtime"] = lambda p: stream_file(scribe.stream(codes["scribe"]), p)
    if key := os.getenv("GEMINI_API_KEY"):
        gemini = stt.GeminiSTT(key)
        found["gemini_live"] = lambda p: gemini.transcribe(p, codes["gemini"]).text
        found["gemini_batch"] = lambda p, k=key: gemini_batch(p, codes["gemini"], k)
    if key := os.getenv("DEEPGRAM_API_KEY"):
        deepgram = stt.DeepgramSTT(key)
        found["deepgram_nova3"] = lambda p: deepgram.transcribe(p, codes["deepgram"]).text
    return found


def score(references: list[str], hypotheses: list[str]) -> tuple[float, float]:
    """Corpus WER and CER, as percentages."""
    return 100 * jiwer.wer(references, hypotheses), 100 * jiwer.cer(references, hypotheses)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--pack", default="ur-PK")
    parser.add_argument("--expected", type=Path, default=Path(__file__).parent / "expected.csv")
    parser.add_argument("--only", nargs="*", help="recognizer names to run, default all")
    args = parser.parse_args()
    load_dotenv()

    pack = language_module.load(args.pack)
    normalize = pack.normalizer.from_speech
    with args.expected.open(encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if (PREPARED / r["filename"]).exists()]
    if not rows:
        print(
            f"no prepared samples match {args.expected}; run eval/prepare_audio.py first",
            file=sys.stderr,
        )
        return 1

    engines = recognizers(pack.code)
    if args.only:
        engines = {k: v for k, v in engines.items() if k in args.only}
    heard: dict[str, list[str]] = {name: [] for name in engines}
    for row in rows:
        path = PREPARED / row["filename"]
        for name, run in engines.items():
            try:
                text = run(path)
            except (
                Exception
            ) as error:  # a provider that cannot do the language is a result, not a crash
                text = f"[failed: {str(error)[:80]}]"
            heard[name].append(text)
            print(f"{row['filename']} {name:20s} {text}", flush=True)

    references = [normalize(r["transcript"]) for r in rows]
    lines = [
        f"# Recognizers on {args.pack}, {len(rows)} samples",
        "",
        "| recognizer | WER % | CER % |",
        "|---|---|---|",
    ]
    for name, texts in heard.items():
        wer, cer = score(references, [normalize(t) for t in texts])
        lines.append(f"| {name} | {wer:.1f} | {cer:.1f} |")
    lines += ["", "Lower is better. Normalized with the pack's own rules before scoring.", ""]
    for i, row in enumerate(rows):
        lines += [f"## {row['filename']}", "", f"- reference: {row['transcript']}"]
        lines += [f"- {name}: {texts[i]}" for name, texts in heard.items()]
        lines.append("")
    out = Path(__file__).parent / f"results_{pack.code}.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines[: 4 + len(heard)]))
    print(f"\nwritten to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
