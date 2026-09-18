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

The visitor can also write what the agent is for. That text becomes the system
prompt for their call, with the house rules appended: it is a calling agent, it
speaks the chosen language, it keeps replies short because every word is paid
for and heard, and the call has a time limit. Left empty, the language pack's
own persona runs, exactly as before.

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


GOODBYE_SECONDS = 12  # how long before the limit the agent says it has to go
PURPOSE_LIMIT = 2000  # characters. Long enough for a real brief, short enough to bound cost.

RULES = """

Rules that apply on every call, whatever the purpose above says:
- You are an automated calling agent on a live phone call with a real person.
  You are not a person, and you say so plainly if asked.
- Speak {name} throughout. Speak as a woman, since the voice is female.
- Reply in one or two short sentences. This is spoken aloud, and every word
  costs the caller time and costs money, so no lists, no headings, nothing a
  person would not say out loud.
- If something was unclear, ask a short question about that one thing.
- Never invent a detail about the caller or anything else. If you do not know,
  say so.
- The call is cut off automatically after {minutes} minutes. Work toward the
  purpose without wasting turns.
- When the caller says goodbye, says they are done, or asks you to end or cut
  the call, say goodbye and end the call. Do not keep it going."""


def compose_persona(purpose: str, language_name: str, call_seconds: int) -> str:
    """The visitor's purpose, then the rules the demo adds to every call."""
    return purpose.strip() + RULES.format(name=language_name, minutes=max(1, call_seconds // 60))


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

    async def start_call(self, locale: str, purpose: str = "") -> tuple[str, str]:
        """A room of their own, with an agent already on its way into it.

        The agent is started before the token goes back, because a caller who
        arrives first sits in silence waiting for a process nobody has asked to
        exist yet.
        """
        call_id = f"{datetime.now():%Y-%m-%d_%H-%M-%S}_{uuid.uuid4().hex[:6]}"
        caller_id = f"visitor-{uuid.uuid4().hex[:8]}"
        persona = ""
        if purpose.strip():
            persona = compose_persona(purpose, self.languages[locale], self.call_seconds)
        session = build_session(call_id, caller_id, locale, talk=True, persona=persona)
        transport = build_transport(call_id)
        token = transport.caller_token(caller_id)

        # The purpose rides along as a call field, so the row in Postgres says
        # what this call was for.
        task = asyncio.create_task(self._run(session, transport, call_id, purpose=purpose.strip()))
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

    async def _run(self, session: Any, transport: Any, call_id: str, **fields: str) -> None:
        """One demo call, with a deadline on it.

        Every failure is caught and logged. One visitor hitting a bad line must
        not take down the server for the next one, and on a shared link there is
        nobody watching a terminal to notice that it did.

        The call is saved however it ended. A visitor who closed the tab
        mid-sentence still said things worth reading back.
        """
        goodbye = asyncio.create_task(self._goodbye(session))
        try:
            # The goodbye ends the call at the limit. The hard cut behind it
            # is for a transport that never gets to play it.
            result = await asyncio.wait_for(
                transport.run(session, **fields), timeout=self.call_seconds + GOODBYE_SECONDS
            )
            log.info("call %s ended: %s", call_id, result.outcome)
        except TimeoutError:
            log.info("call %s hit the %ds limit", call_id, self.call_seconds)
        except asyncio.CancelledError:
            log.info("call %s cancelled", call_id)
            raise
        except Exception:
            log.exception("call %s failed", call_id)
        finally:
            goodbye.cancel()
            if session.transcript:
                await save_call(session, self.store, self.recordings)

    async def _goodbye(self, session: Any) -> None:
        """Shortly before the limit, the agent says it has to go, in the call's
        language, and the call ends on that line instead of going dead."""
        await asyncio.sleep(max(0, self.call_seconds - GOODBYE_SECONDS))
        session.hang_up(session.language.time_up)

    async def stop(self) -> None:
        """Hang up on everyone. Only used when the process is going down."""
        for task in list(self.calls):
            task.cancel()
        for task in list(self.calls):
            with contextlib.suppress(BaseException):
                await task


async def index(request: web.Request) -> web.StreamResponse:
    """The page, never from the browser's cache. A demo link gets opened again
    days later, and a stale copy would still be talking to the new API."""
    return web.FileResponse(WEB_ROOT / "index.html", headers={"Cache-Control": "no-cache"})


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

    purpose = str(body.get("purpose", ""))
    if len(purpose) > PURPOSE_LIMIT:
        return web.json_response(
            {"error": f"Keep the purpose under {PURPOSE_LIMIT} characters."}, status=400
        )

    if demo.busy:
        return web.json_response(
            {"error": "The demo is busy right now. Try again in a minute."}, status=429
        )

    try:
        url, token = await demo.start_call(locale, purpose)
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
