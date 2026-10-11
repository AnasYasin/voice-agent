#!/usr/bin/env python3
"""Turn raw recordings into phone audio.

Reads anything ffmpeg can open from eval/samples/raw/, writes 8 kHz mono
16-bit WAV with the same stem to eval/samples/prepared/. A phone recording,
a WhatsApp voice note and a laptop microphone all end up the same width, which
is what the recognizers hear on a call.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

RAW = Path(__file__).parent / "samples" / "raw"
PREPARED = Path(__file__).parent / "samples" / "prepared"
RATE = 8000


def main() -> int:
    PREPARED.mkdir(parents=True, exist_ok=True)
    sources = sorted(p for p in RAW.iterdir() if p.is_file() and not p.name.startswith("."))
    if not sources:
        print(f"nothing in {RAW}", file=sys.stderr)
        return 1
    for src in sources:
        dst = PREPARED / f"{src.stem}.wav"
        subprocess.run(
            [
                "ffmpeg",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(src),
                "-ar",
                str(RATE),
                "-ac",
                "1",
                str(dst),
            ],
            check=True,
        )
        print(f"{src.name} -> {dst.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
