"""Component 14. Process startup.

Reads .env, builds the language, builds the providers, builds a session, hands
it to a transport, runs one call.

This is the only file that reads os.environ. Everything below it is handed what
it needs, which is why changing AGENT_LANGUAGE in .env is enough to switch
language and why swapping transport does not touch the session.

    make run
    python -m voice_agent.main --name انس --date کل --time چار
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from livekit.rtc.room import ConnectError

from voice_agent import language as language_module
from voice_agent import llm, stt, tts
from voice_agent.logging_setup import setup_logging
from voice_agent.session import Session
from voice_agent.transport import BrowserTransport

log = logging.getLogger(__name__)

CALLS_DIR = Path("calls")


def require(name: str) -> str:
    value = os.getenv(name, "")
    if not value:
        raise SystemExit(f"{name} is empty in .env")
    return value


def build_session(call_id: str) -> Session:
    """Wire one call. Every provider is chosen here and nowhere else."""
    language = language_module.load()

    voice = tts.build(
        require("AZURE_SPEECH_KEY"),
        require("AZURE_SPEECH_REGION"),
        language.tts_voice,
        cache_dir=Path(os.getenv("TTS_CACHE_DIR", "./audio_cache")),
    )
    provider = os.getenv("STT_PROVIDER", "elevenlabs")

    return Session(
        language=language,
        stt=stt.build(provider, require(f"{provider.upper()}_API_KEY"), language.keyterms),
        tts=voice,
        extractor=llm.build(require("ANTHROPIC_API_KEY")),
        work_dir=CALLS_DIR / call_id,
    )


def build_transport() -> BrowserTransport:
    return BrowserTransport(
        url=os.getenv("LIVEKIT_URL", "ws://localhost:7880"),
        api_key=require("LIVEKIT_API_KEY"),
        api_secret=require("LIVEKIT_API_SECRET"),
        room=os.getenv("LIVEKIT_ROOM", "urdu-agent"),
    )


async def run_once(call_id: str, fields: dict[str, str]) -> None:
    session = build_session(call_id)
    transport = build_transport()

    print(f"\n  room    {transport.room_name}")
    print(f"  caller  {transport.caller_token()}\n")
    print("  Join that room in a browser, then speak.\n")

    try:
        result = await transport.run(session, **fields)
    except ConnectError:
        # The usual cause is nobody started the server. The raw error is
        # 'IO error: Connection refused', which says nothing useful.
        raise SystemExit(
            f"cannot reach LiveKit at {transport.url}.\n"
            "Start it with:  docker compose up -d livekit"
        ) from None

    print(f"\n  outcome  {result.outcome}")
    for slot, value in result.slots.items():
        print(f"  {slot:<20} {value}")
    print(f"\n  audio in {session.work_dir}\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one outbound appointment call.")
    parser.add_argument("--name", required=True, help="who we are calling")
    parser.add_argument("--date", required=True, help="appointment day, as it should be spoken")
    parser.add_argument("--time", required=True, help="appointment time, as it should be spoken")
    parser.add_argument("--call-id", default="demo", help="folder name under calls/")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    setup_logging(logging.DEBUG if args.verbose else logging.INFO)
    load_dotenv()

    asyncio.run(run_once(args.call_id, {"name": args.name, "date": args.date, "time": args.time}))


if __name__ == "__main__":
    main()
