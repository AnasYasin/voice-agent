"""Component 20. The demo server.

The call itself is never started here. These cover the gate in front of it,
because that gate is the only thing between a public URL and three metered APIs.

aiohttp ships its own pytest plugin, which runs its own event loop and fights
pytest-asyncio's auto mode. The test server is driven directly instead.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any

import pytest
from aiohttp.test_utils import TestClient, TestServer

from voice_agent.demo import Demo, build_app

CODE = "open-sesame"


class Recording(Demo):
    """A Demo that starts fake calls, so no API is ever touched."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.started = 0

    async def start_call(self) -> tuple[str, str]:
        self.started += 1
        task = asyncio.create_task(asyncio.sleep(3600))
        self.calls.add(task)
        task.add_done_callback(self.calls.discard)
        return self.public_url, f"token-{self.started}"


def make_demo(**kwargs: Any) -> Recording:
    settings: dict[str, Any] = {
        "passcode": CODE,
        "public_url": "wss://example/rtc",
        "max_calls": 2,
    }
    settings.update(kwargs)
    return Recording(**settings)


@asynccontextmanager
async def serving(demo: Demo) -> Any:
    async with TestClient(TestServer(build_app(demo))) as client:
        yield client


def test_a_demo_without_a_passcode_refuses_to_start() -> None:
    """An empty code is not an open demo, it is a mistake that costs money."""
    with pytest.raises(ValueError, match="DEMO_PASSCODE"):
        Demo(passcode="", public_url="wss://example/rtc")


async def test_the_wrong_code_gets_no_token() -> None:
    demo = make_demo()
    async with serving(demo) as client:
        response = await client.post("/api/session", json={"passcode": "guess"})

    assert response.status == 403
    assert demo.started == 0


async def test_a_missing_code_looks_the_same_as_a_wrong_one() -> None:
    """Separating the two tells whoever is probing which half they got right."""
    async with serving(make_demo()) as client:
        missing = await client.post("/api/session", json={})
        missing_body = await missing.json()
        wrong = await client.post("/api/session", json={"passcode": "guess"})
        wrong_body = await wrong.json()

    assert missing.status == wrong.status == 403
    assert missing_body == wrong_body


async def test_the_right_code_gets_a_room_and_a_token() -> None:
    demo = make_demo()
    async with serving(demo) as client:
        response = await client.post("/api/session", json={"passcode": CODE})
        body = await response.json()

    assert response.status == 200
    assert body["url"] == "wss://example/rtc"
    assert body["token"]
    assert demo.started == 1


async def test_every_visitor_gets_their_own_token() -> None:
    """One shared token would put everyone in the same room together."""
    async with serving(make_demo()) as client:
        first = await (await client.post("/api/session", json={"passcode": CODE})).json()
        second = await (await client.post("/api/session", json={"passcode": CODE})).json()

    assert first["token"] != second["token"]


async def test_the_cap_turns_people_away_rather_than_degrading_calls() -> None:
    demo = make_demo(max_calls=1)
    async with serving(demo) as client:
        first = await client.post("/api/session", json={"passcode": CODE})
        second = await client.post("/api/session", json={"passcode": CODE})

    assert first.status == 200
    assert second.status == 429
    assert demo.started == 1


async def test_a_freed_slot_lets_the_next_person_in() -> None:
    demo = make_demo(max_calls=1)
    async with serving(demo) as client:
        await client.post("/api/session", json={"passcode": CODE})
        await demo.stop()
        response = await client.post("/api/session", json={"passcode": CODE})

    assert response.status == 200


async def test_healthz_reports_what_is_in_flight() -> None:
    demo = make_demo(max_calls=1)
    async with serving(demo) as client:
        await client.post("/api/session", json={"passcode": CODE})
        body = await (await client.get("/healthz")).json()

    assert body == {"ok": True, "in_flight": 1, "busy": True}


async def test_the_page_is_served_without_a_code() -> None:
    """The code gates the call, not the page. A login wall on a demo link is
    one more thing to explain to someone you are trying to impress."""
    async with serving(make_demo()) as client:
        response = await client.get("/")
        body = await response.text()

    assert response.status == 200
    assert "Access code" in body
    # The old flow pasted a token into the page. Nothing should ask for one now.
    assert "Caller token" not in body


async def test_a_call_that_dies_does_not_take_the_server_with_it() -> None:
    """On a shared link nobody is watching a terminal to notice it went down."""
    demo = Demo(passcode="x", public_url="wss://example/rtc")

    class Exploding:
        async def run(self, session: Any, **fields: Any) -> Any:
            raise RuntimeError("bad line")

    await demo._run(session=None, transport=Exploding(), call_id="boom")

    assert demo.calls == set()


async def test_a_caller_who_never_hangs_up_is_cut_off() -> None:
    """A demo caller closes the tab instead of saying goodbye, so the deadline
    is what actually ends most of these."""
    demo = Demo(passcode="x", public_url="wss://example/rtc", call_seconds=0)

    class NeverEnds:
        async def run(self, session: Any, **fields: Any) -> Any:
            await asyncio.sleep(3600)

    await demo._run(session=None, transport=NeverEnds(), call_id="forever")

    assert demo.calls == set()


def test_the_app_exposes_only_what_it_means_to() -> None:
    """Every route here is reachable from the public internet. HEAD comes free
    with each GET and is not something the app chose to expose."""
    app = build_app(make_demo())

    routes = {(r.method, r.resource.canonical) for r in app.router.routes() if r.method != "HEAD"}

    assert routes == {("GET", "/"), ("GET", "/healthz"), ("POST", "/api/session")}
