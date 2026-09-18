"""Component 20. The demo server, for a link you can send someone.

`main.py` runs one call and exits. That is right for a campaign working down a
list of leads, and wrong for a URL sitting in someone's inbox until they get
round to opening it. This is the same session and the same transport, wrapped
in a process that stays up, gives each visitor a room of their own, and runs a
call for each of them.

Three things it does that a local `make run` never had to:

  A token per visitor. Nobody is going to paste one, and a single token on a
  public page would put everybody in the same room together.

  A passcode. The keys behind this page bill by the minute to Azure,
  ElevenLabs and Anthropic, so a link anyone can find is a bill anyone can run
  up. It is one shared code, not accounts, because this is a demo and not a
  product.

  Limits. `max_calls` at once and `call_seconds` each. A demo caller closes the
  tab instead of saying goodbye, so without a deadline the room, the recognizer
  socket and the concurrency slot all stay taken by someone who left.

The agent reaches LiveKit on the internal address and the browser reaches it on
the public one, which is why `public_url` is handed in separately. Inside a
compose network those really are different hosts.

Every visitor gets an identity of their own, minted with their token. It is the
caller id on their call, which is what lets one person's calls be found again
in Postgres before there is a phone number to find them by.

The visitor also picks the language. The page asks `/api/languages` for the
packs on disk, shows one button each, and sends the chosen locale when it
starts the call, so two visitors can be on the line in two languages at once.

Nothing here reads the environment. `main.py` builds this and runs it, the same
way it builds the session, the transport and the store.
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import logging
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from aiohttp import web

from voice_agent.main import build_session, build_transport, save_call
from voice_agent.store import Recordings, Store

log = logging.getLogger(__name__)

WEB_ROOT = Path(__file__).resolve().parents[2] / "web"

# A typed key rather than a bare string, which is what aiohttp wants for
# anything stored on the app.
DEMO = web.AppKey("demo", "Demo")


class Demo:
    """The running demo. One per process."""

    def __init__(
        self,
        passcode: str,
        public_url: str,
        store: Store,
        languages: dict[str, str],
        default_language: str,
        recordings: Recordings | None = None,
        max_calls: int = 3,
        call_seconds: int = 300,
    ) -> None:
        if not passcode:
            raise ValueError(
                "DEMO_PASSCODE is empty. Refusing to start, because the link "
                "would be open to anyone who found it, spending real money."
            )
        if default_language not in languages:
            raise ValueError(f"AGENT_LANGUAGE={default_language!r} has no pack under lang/")

        self.passcode = passcode
        self.public_url = public_url
        self.store = store
        self.languages = languages
        self.default_language = default_language
        self.recordings = recordings
        self.max_calls = max_calls
        self.call_seconds = call_seconds
        self.calls: set[asyncio.Task] = set()

    @property
    def busy(self) -> bool:
        return len(self.calls) >= self.max_calls

    def correct(self, given: str) -> bool:
        """Constant time, so the code cannot be worked out a character at a
        time by watching how long the answer takes to come back."""
        return hmac.compare_digest(given.strip(), self.passcode)

    async def start_call(self, locale: str) -> tuple[str, str]:
        """A room of their own, with an agent already on its way into it.

        The agent is started before the token goes back, because a caller who
        arrives first sits in silence waiting for a process nobody has asked to
        exist yet.
        """
        call_id = f"{datetime.now():%Y-%m-%d_%H-%M-%S}_{uuid.uuid4().hex[:6]}"
        caller_id = f"visitor-{uuid.uuid4().hex[:8]}"
        session = build_session(call_id, caller_id, locale, talk=True)
        transport = build_transport(call_id)
        token = transport.caller_token(caller_id)

        task = asyncio.create_task(self._run(session, transport, call_id))
        self.calls.add(task)
        task.add_done_callback(self.calls.discard)

        log.info(
            "call %s started in %s, %d of %d in flight",
            call_id,
            locale,
            len(self.calls),
            self.max_calls,
        )
        return self.public_url, token

    async def _run(self, session: Any, transport: Any, call_id: str) -> None:
        """One demo call, with a deadline on it.

        Every failure is caught and logged. One visitor hitting a bad line must
        not take down the server for the next one, and on a shared link there is
        nobody watching a terminal to notice that it did.

        The call is saved however it ended. A visitor who closed the tab
        mid-sentence still said things worth reading back.
        """
        try:
            result = await asyncio.wait_for(transport.run(session), timeout=self.call_seconds)
            log.info("call %s ended: %s", call_id, result.outcome)
        except TimeoutError:
            log.info("call %s hit the %ds limit", call_id, self.call_seconds)
        except asyncio.CancelledError:
            log.info("call %s cancelled", call_id)
            raise
        except Exception:
            log.exception("call %s failed", call_id)
        finally:
            if session.transcript:
                await save_call(session, self.store, self.recordings)

    async def stop(self) -> None:
        """Hang up on everyone. Only used when the process is going down."""
        for task in list(self.calls):
            task.cancel()
        for task in list(self.calls):
            with contextlib.suppress(BaseException):
                await task


async def index(request: web.Request) -> web.StreamResponse:
    return web.FileResponse(WEB_ROOT / "index.html")


async def healthz(request: web.Request) -> web.Response:
    """For the load balancer, and for you checking the box is alive."""
    demo = request.app[DEMO]
    return web.json_response({"ok": True, "in_flight": len(demo.calls), "busy": demo.busy})


async def languages(request: web.Request) -> web.Response:
    """What the toggle shows, default first, and which one starts selected."""
    demo = request.app[DEMO]
    ordered = sorted(demo.languages, key=lambda locale: locale != demo.default_language)
    return web.json_response(
        {
            "languages": [{"locale": locale, "name": demo.languages[locale]} for locale in ordered],
            "default": demo.default_language,
        }
    )


async def start_session(request: web.Request) -> web.Response:
    """Trade a passcode and a language for a room and a token."""
    demo = request.app[DEMO]

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Expected JSON."}, status=400)

    if not demo.correct(str(body.get("passcode", ""))):
        # Deliberately one message for both wrong and missing. Telling someone
        # which half they got right is telling them how to guess the other.
        return web.json_response({"error": "That code is not right."}, status=403)

    locale = str(body.get("language", demo.default_language))
    if locale not in demo.languages:
        return web.json_response({"error": f"No such language: {locale}."}, status=400)

    if demo.busy:
        return web.json_response(
            {"error": "The demo is busy right now. Try again in a minute."}, status=429
        )

    try:
        url, token = await demo.start_call(locale)
    except Exception:
        log.exception("could not start a call")
        return web.json_response({"error": "Could not start the call."}, status=500)

    return web.json_response({"url": url, "token": token})


def build_app(demo: Demo) -> web.Application:
    app = web.Application()
    app[DEMO] = demo
    app.router.add_get("/", index)
    app.router.add_get("/healthz", healthz)
    app.router.add_get("/api/languages", languages)
    app.router.add_post("/api/session", start_session)

    async def hang_up_everyone(app: web.Application) -> None:
        await app[DEMO].stop()

    app.on_cleanup.append(hang_up_everyone)
    return app
