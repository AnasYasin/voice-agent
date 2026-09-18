"""Replay a call's caller channel through the voice detector the transport uses.

    python scripts/replay_vad.py calls/<call_id>/call.wav [--model-rate 8000]

Prints every start and end of speech with its time, and when the probability
last moved. Written the day the 8 kHz model went deaf mid-call; the fix was to
run the model at 16 kHz, and this is how to check it stays fixed on real audio.
"""

from __future__ import annotations

import argparse
import array
import asyncio
import wave
from pathlib import Path

from livekit import rtc
from livekit.agents.vad import VADEventType
from livekit.plugins import silero

from voice_agent.config import settings


def caller_channel(path: Path) -> tuple[int, array.array]:
    with wave.open(str(path)) as tape:
        samples = array.array("h", tape.readframes(tape.getnframes()))
        rate, channels = tape.getframerate(), tape.getnchannels()
    return rate, samples[0::channels]


async def replay(rate: int, pcm: array.array, model_rate: int) -> None:
    vad = silero.VAD.load(
        sample_rate=model_rate, min_silence_duration=settings.audio.vad_silence_seconds
    )
    stream = vad.stream()
    frame = rate * settings.audio.frame_ms // 1000

    async def feed() -> None:
        for start in range(0, len(pcm) - frame, frame):
            chunk = pcm[start : start + frame].tobytes()
            stream.push_frame(
                rtc.AudioFrame(
                    data=chunk, sample_rate=rate, num_channels=1, samples_per_channel=frame
                )
            )
            await asyncio.sleep(0)
        stream.end_input()

    feeding = asyncio.create_task(feed())
    last_moving = 0.0
    async for event in stream:
        if event.type == VADEventType.INFERENCE_DONE:
            if event.probability > 0.001:
                last_moving = event.timestamp
            continue
        print(f"  {event.timestamp:7.2f}s  {event.type.name}")
    await feeding
    print(f"\n  audio {len(pcm) / rate:.1f} s, probability last moved at {last_moving:.1f} s")
    if len(pcm) / rate - last_moving > 5:
        print("  the detector went silent well before the audio ended")


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay a recording through the voice detector.")
    parser.add_argument("recording", type=Path, help="call.wav or caller.wav from a call folder")
    parser.add_argument("--model-rate", type=int, default=16000, help="8000 to see the old failure")
    parser.add_argument(
        "--activation", type=float, default=0.5, help="speech probability that starts a turn"
    )
    parser.add_argument(
        "--gain",
        type=float,
        default=1.0,
        help="multiply the audio first, to see what a louder caller would do",
    )
    args = parser.parse_args()

    rate, pcm = caller_channel(args.recording)
    print(
        f"{args.recording.name}: caller channel {len(pcm) / rate:.1f} s at {rate} Hz, model at {args.model_rate} Hz"
    )
    asyncio.run(replay(rate, pcm, args.model_rate))


if __name__ == "__main__":
    main()
