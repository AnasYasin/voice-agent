"""Component 20. The demo server.

The call itself is never started here. These cover the gate in front of it,
because that gate is the only thing between a public URL and three metered APIs.

aiohttp ships its own pytest plugin, which runs its own event loop and fights
pytest-asyncio's auto mode. The test server is driven directly instead.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from aiohttp.test_utils import TestClient, TestServer

from voice_agent.demo import Demo, build_app

CODE = "open-sesame"
LANGUAGES = {"languages": {"ur-PK": "اردو", "en-IN": "English"}, "default_language": "ur-PK"}


class FakeStore:
    """Remembers what it was asked to save. No database."""

    def __init__(self) -> None:
        self.saved: list[dict[str, Any]] = []

    async def save(self, record: dict[str, Any]) -> None:
        self.saved.append(record)


class FakeRecordings:
    """Remembers what it was asked to upload. No bucket."""

    def __init__(self) -> None:
        self.uploaded: list[tuple[str, str]] = []

    @staticmethod
    def key(call_id: str, started_at: str) -> str:
        return f"calls/test/{call_id}.wav"

    async def upload(self, path: Path, key: str) -> str:
        self.uploaded.append((path.name, key))
        return key


class FakeSession:
    """Just enough of a Session for `save_call`: a transcript and a record."""

    def __init__(self, work_dir: Path, turns: int) -> None:
        self.work_dir = work_dir
        self.transcript = ["turn"] * turns
        work_dir.mkdir(parents=True, exist_ok=True)

    def record(self) -> dict[str, Any]:
        return {
            "call_id": self.work_dir.name,
            "caller_id": "visitor-abc12345",
            "started_at": "2026-09-18T12:00:00+05:00",
            "outcome": "cut_off",
            "slots": {},
            "turns": [
                {"state": "talk", "heard": "", "said": "السلام علیکم"} for _ in self.transcript
            ],
        }


class Recording(Demo):
    """A Demo that starts fake calls, so no API is ever touched."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.started = 0
        self.locales: list[str] = []

    async def start_call(self, locale: str) -> tuple[str, str]:
        self.started += 1
        self.locales.append(locale)
        task = asyncio.create_task(asyncio.sleep(3600))
        self.calls.add(task)
        task.add_done_callback(self.calls.discard)
        return self.public_url, f"token-{self.started}"


def make_demo(**kwargs: Any) -> Recording:
    settings: dict[str, Any] = {
        "passcode": CODE,
        "public_url": "wss://example/rtc",
        "store": FakeStore(),
        **LANGUAGES,
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
        Demo(passcode="", public_url="wss://example/rtc", store=FakeStore(), **LANGUAGES)


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


async def test_the_page_can_ask_which_languages_exist() -> None:
    """One button per pack on disk, and which one starts selected."""
    async with serving(make_demo()) as client:
        body = await (await client.get("/api/languages")).json()

    assert body == {
        "languages": [{"locale": "ur-PK", "name": "اردو"}, {"locale": "en-IN", "name": "English"}],
        "default": "ur-PK",
    }


async def test_the_chosen_language_reaches_the_call() -> None:
    demo = make_demo()
    async with serving(demo) as client:
        response = await client.post("/api/session", json={"passcode": CODE, "language": "en-IN"})

    assert response.status == 200
    assert demo.locales == ["en-IN"]


async def test_no_language_means_the_default_one() -> None:
    demo = make_demo()
    async with serving(demo) as client:
        await client.post("/api/session", json={"passcode": CODE})

    assert demo.locales == ["ur-PK"]


async def test_an_unknown_language_is_refused_before_a_call_starts() -> None:
    demo = make_demo()
    async with serving(demo) as client:
        response = await client.post("/api/session", json={"passcode": CODE, "language": "fr-FR"})

    assert response.status == 400
    assert demo.started == 0


def test_a_default_language_without_a_pack_refuses_to_start() -> None:
    """AGENT_LANGUAGE pointing at nothing would fail on the first caller instead."""
    with pytest.raises(ValueError, match="no pack"):
        Demo(
            passcode="x",
            public_url="wss://example/rtc",
            store=FakeStore(),
            languages={"ur-PK": "اردو"},
            default_language="fr-FR",
        )


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


class Exploding:
    async def run(self, session: Any, **fields: Any) -> Any:
        raise RuntimeError("bad line")


class NeverEnds:
    async def run(self, session: Any, **fields: Any) -> Any:
        await asyncio.sleep(3600)


async def test_a_call_that_dies_does_not_take_the_server_with_it(tmp_path: Path) -> None:
    """On a shared link nobody is watching a terminal to notice it went down."""
    demo = Demo(passcode="x", public_url="wss://example/rtc", **LANGUAGES, store=FakeStore())

    await demo._run(FakeSession(tmp_path / "boom", turns=0), Exploding(), call_id="boom")

    assert demo.calls == set()


async def test_a_caller_who_never_hangs_up_is_cut_off(tmp_path: Path) -> None:
    """A demo caller closes the tab instead of saying goodbye, so the deadline
    is what actually ends most of these."""
    store = FakeStore()
    demo = Demo(
        passcode="x", public_url="wss://example/rtc", **LANGUAGES, store=store, call_seconds=0
    )

    await demo._run(FakeSession(tmp_path / "forever", turns=3), NeverEnds(), call_id="forever")

    assert demo.calls == set()
    assert [record["call_id"] for record in store.saved] == ["forever"]
    assert (tmp_path / "forever" / "transcript.txt").exists()


async def test_a_call_that_failed_mid_way_is_still_saved(tmp_path: Path) -> None:
    """Three turns happened before the bad line. They are the record."""
    store = FakeStore()
    demo = Demo(passcode="x", public_url="wss://example/rtc", **LANGUAGES, store=store)

    await demo._run(FakeSession(tmp_path / "boom", turns=3), Exploding(), call_id="boom")

    assert len(store.saved) == 1
    assert store.saved[0]["caller_id"] == "visitor-abc12345"


async def test_a_call_nobody_spoke_on_is_not_saved(tmp_path: Path) -> None:
    """A visitor who got a token and never connected did not make a call."""
    store = FakeStore()
    demo = Demo(
        passcode="x", public_url="wss://example/rtc", **LANGUAGES, store=store, call_seconds=0
    )

    await demo._run(FakeSession(tmp_path / "ghost", turns=0), NeverEnds(), call_id="ghost")

    assert store.saved == []


def test_the_app_exposes_only_what_it_means_to() -> None:
    """Every route here is reachable from the public internet. HEAD comes free
    with each GET and is not something the app chose to expose."""
    app = build_app(make_demo())

    routes = {(r.method, r.resource.canonical) for r in app.router.routes() if r.method != "HEAD"}

    assert routes == {
        ("GET", "/"),
        ("GET", "/healthz"),
        ("GET", "/api/languages"),
        ("POST", "/api/session"),
    }


async def test_the_recording_is_uploaded_and_its_key_saved(tmp_path: Path) -> None:
    """The row points at the audio. Upload first, so the key exists to save."""
    store, recordings = FakeStore(), FakeRecordings()
    demo = Demo(
        passcode="x",
        public_url="wss://example/rtc",
        **LANGUAGES,
        store=store,
        recordings=recordings,
    )

    await demo._run(FakeSession(tmp_path / "taped", turns=2), Exploding(), call_id="taped")

    assert recordings.uploaded == [("call.wav", "calls/test/taped.wav")]
    assert store.saved[0]["recording"] == "calls/test/taped.wav"


async def test_without_a_bucket_the_recording_stays_on_disk(tmp_path: Path) -> None:
    store = FakeStore()
    demo = Demo(passcode="x", public_url="wss://example/rtc", **LANGUAGES, store=store)

    await demo._run(FakeSession(tmp_path / "local", turns=2), Exploding(), call_id="local")

    assert store.saved[0]["recording"] == ""
