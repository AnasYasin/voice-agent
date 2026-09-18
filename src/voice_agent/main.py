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
import json
import logging
import os
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from livekit.rtc.room import ConnectError

from voice_agent import language as language_module
from voice_agent import llm, stt, tts
from voice_agent import store as store_module
from voice_agent.logging_setup import setup_logging
from voice_agent.session import Session
from voice_agent.transport import BrowserTransport

log = logging.getLogger(__name__)

# Anchored to the repo, not the shell's cwd. Running from ~ used to scatter
# recordings into the home directory, where you never look for them.
CALLS_DIR = Path(__file__).resolve().parents[2] / "calls"


def require(name: str) -> str:
    value = os.getenv(name, "")
    if not value:
        raise SystemExit(f"{name} is empty in .env")
    return value


def build_session(
    call_id: str,
    caller_id: str,
    locale: str,
    chat: bool = False,
    talk: bool = False,
    persona: str = "",
) -> Session:
    """Wire one call. Every provider is chosen here and nowhere else.

    `persona` replaces the language pack's own for this one call. The demo
    page lets a visitor write what the agent is for; the pack's persona is what
    every other run uses.
    """
    language = language_module.load(locale)
    if persona:
        # The pack's greeting goes with its persona. The model opens in character.
        language = replace(language, persona=persona, greeting="")

    voice = tts.build(
        require("AZURE_SPEECH_KEY"),
        require("AZURE_SPEECH_REGION"),
        language.tts_voice,
        cache_dir=Path(os.getenv("TTS_CACHE_DIR", "./audio_cache")),
        locale=language.locale,
    )
    provider = os.getenv("STT_PROVIDER", "elevenlabs")

    return Session(
        language=language,
        stt=stt.build(provider, require(f"{provider.upper()}_API_KEY"), language.keyterms),
        tts=voice,
        extractor=llm.build(require("ANTHROPIC_API_KEY")),
        work_dir=CALLS_DIR / call_id,
        responder=llm.build_responder(require("ANTHROPIC_API_KEY")) if chat or talk else None,
        conversation=talk,
        caller_id=caller_id,
    )


async def connect_store() -> store_module.Store:
    """Postgres, or a clear exit. A call nobody can look up later did not happen."""
    try:
        return await store_module.connect(require("DATABASE_URL"))
    except store_module.ConnectionFailed as error:
        raise SystemExit(
            f"cannot reach Postgres at DATABASE_URL: {error}\n"
            "Start it with:  docker compose up -d postgres"
        ) from None


def build_recordings() -> store_module.Recordings | None:
    """S3 for the call audio. An empty S3_BUCKET keeps recordings on disk only,
    which is how a laptop runs without AWS access."""
    bucket = os.getenv("S3_BUCKET", "")
    if not bucket:
        log.info("S3_BUCKET is empty, recordings stay in %s", CALLS_DIR)
        return None
    return store_module.recordings(bucket, require("AWS_REGION"))


def build_transport(call_id: str) -> BrowserTransport:
    # A room per call, not one shared room. A browser tab left open from an
    # earlier run stays joined, and the agent would greet that ghost instead of
    # waiting for a live caller. The token carries the room name, so a fresh
    # room costs the operator nothing.
    return BrowserTransport(
        url=os.getenv("LIVEKIT_URL", "ws://localhost:7880"),
        api_key=require("LIVEKIT_API_KEY"),
        api_secret=require("LIVEKIT_API_SECRET"),
        room=os.getenv("LIVEKIT_ROOM") or f"urdu-agent-{call_id}",
    )


async def run_once(
    call_id: str,
    caller_id: str,
    fields: dict[str, str],
    chat: bool = False,
    talk: bool = False,
) -> None:
    store = await connect_store()
    recordings = build_recordings()
    session = build_session(
        call_id, caller_id, language_module.default_locale(), chat=chat, talk=talk
    )
    transport = build_transport(call_id)

    print(f"\n  room    {transport.room_name}")
    print(f"  caller  {transport.caller_token()}\n")
    print("  Join that room in a browser, then speak.\n")

    try:
        await transport.run(session, **fields)
    except ConnectError:
        # The usual cause is nobody started the server. The raw error is
        # 'IO error: Connection refused', which says nothing useful.
        raise SystemExit(
            f"cannot reach LiveKit at {transport.url}.\n"
            "Start it with:  docker compose up -d livekit"
        ) from None
    finally:
        # Whatever ended the call, a call with turns in it is a call to keep.
        if session.transcript:
            await save_call(session, store, recordings)
        await store.close()

    result = session.result
    print(f"\n  outcome  {result.outcome}")
    for slot, value in result.slots.items():
        print(f"  {slot:<20} {value}")
    print(f"\n  audio in {session.work_dir}\n")


async def save_call(
    session: Session,
    store: store_module.Store,
    recordings: store_module.Recordings | None,
) -> None:
    """The finished call to disk, to S3 and to Postgres, in that order.

    The JSON was already there, rewritten after every turn. This writes it a
    last time with the outcome and the recording's key, renders the text
    version for reading a call back without a tool, then hands the same record
    to the store. The upload goes first so the row can point at the audio.
    """
    record = session.record()
    record["recording"] = ""
    if recordings:
        key = recordings.key(record["call_id"], record["started_at"])
        record["recording"] = await recordings.upload(session.work_dir / "call.wav", key)

    (session.work_dir / "transcript.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    lines = [
        f"call {record['call_id']}  ({record['started_at']})  caller {record['caller_id']}",
        "",
    ]
    for turn in record["turns"]:
        lines.append(f"[{turn['state']}]")
        lines.append(f"  agent   {turn['said']}")
        if turn["heard"]:
            lines.append(f"  caller  {turn['heard']}")
        lines.append("")
    lines.append(f"outcome  {record['outcome']}")
    lines += [f"{slot:<20} {value}" for slot, value in record["slots"].items()]
    (session.work_dir / "transcript.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")

    await store.save(record)


async def serve() -> None:
    """The demo server. One process, a call per visitor, until you stop it.

    `run_once` dials one caller and exits. This stays up and answers whoever
    opens the link, which is the whole difference between testing it yourself
    and sending it to someone.
    """
    from aiohttp import web

    from voice_agent import demo as demo_module

    store = await connect_store()
    server = demo_module.Demo(
        passcode=require("DEMO_PASSCODE"),
        # What the browser dials, which is not what the agent dials. Behind a
        # proxy these are genuinely different hosts.
        public_url=os.getenv("DEMO_PUBLIC_LIVEKIT_URL", "ws://localhost:7880"),
        store=store,
        # Every pack on disk is offered on the page. AGENT_LANGUAGE picks the
        # one selected when it loads.
        languages=language_module.available(),
        default_language=language_module.default_locale(),
        recordings=build_recordings(),
        max_calls=int(os.getenv("DEMO_MAX_CALLS", "3")),
        call_seconds=int(os.getenv("DEMO_CALL_SECONDS", "300")),
    )
    port = int(os.getenv("DEMO_PORT", "8080"))

    log.info(
        "demo on :%d, browser dials %s, %d calls at once, %ds each",
        port,
        server.public_url,
        server.max_calls,
        server.call_seconds,
    )

    runner = web.AppRunner(demo_module.build_app(server), access_log=None)
    await runner.setup()
    await web.TCPSite(runner, port=port).start()
    try:
        await asyncio.Event().wait()  # until someone stops the process
    finally:
        await runner.cleanup()  # hangs up on everyone, which saves their calls
        await store.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one outbound appointment call.")
    # Required by the campaign, meaningless in talk mode, so they default
    # instead of being mandatory. --talk with no other arguments should work.
    parser.add_argument("--name", default="", help="who we are calling")
    parser.add_argument("--date", default="", help="appointment day, as it should be spoken")
    parser.add_argument("--time", default="", help="appointment time, as it should be spoken")
    parser.add_argument(
        "--phone",
        default="",
        help="the number being called. Stored as the caller id so the call can be looked up",
    )
    parser.add_argument(
        "--call-id",
        default=datetime.now().strftime("%Y-%m-%d_%H-%M-%S"),
        help="folder name under calls/, defaults to a timestamp so runs are kept",
    )
    parser.add_argument(
        "--talk",
        action="store_true",
        help="no script and no questions, just a conversation. Hang up to end it.",
    )
    parser.add_argument(
        "--chat",
        action="store_true",
        help="answer off-script questions instead of just repeating the question",
    )
    parser.add_argument(
        "--serve",
        action="store_true",
        help="run the demo server instead of one call. Needs DEMO_PASSCODE.",
    )
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    setup_logging(logging.DEBUG if args.verbose else logging.INFO)
    load_dotenv()

    if args.serve:
        asyncio.run(serve())
        return

    asyncio.run(
        run_once(
            args.call_id,
            args.phone,
            {"name": args.name, "date": args.date, "time": args.time},
            chat=args.chat,
            talk=args.talk,
        )
    )


if __name__ == "__main__":
    main()
