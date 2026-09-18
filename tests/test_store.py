"""Component 7. Transcripts in Postgres.

These run against a real database, so they carry the `live` mark and skip when
DATABASE_URL is unset. Locally that is `docker compose up -d postgres`.

Each test uses its own call id and deletes it at the end, so the suite can run
against a database that already holds real calls.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

import pytest

from voice_agent import store as store_module

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(not os.getenv("DATABASE_URL"), reason="needs DATABASE_URL in .env"),
]


def make_record(caller_id: str = "03001234567", **changes: Any) -> dict[str, Any]:
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    record: dict[str, Any] = {
        "call_id": f"test_{uuid.uuid4().hex[:8]}",
        "caller_id": caller_id,
        "language": "ur-PK",
        "started_at": now,
        "ended_at": now,
        "fields": {"name": "انس", "date": "کل", "time": "چار"},
        "outcome": "done",
        "slots": {"confirmed": True},
        "audio_dir": "/tmp/calls/test",
        "recording": "",
        "turns": [
            {
                "turn": 1,
                "state": "confirm_appointment",
                "seconds_from_start": 0.0,
                "heard": "",
                "said": "کیا آپ کل چار بجے آ رہے ہیں؟",
            },
            {
                "turn": 2,
                "state": "done",
                "seconds_from_start": 4.2,
                "heard": "جی ہاں theek hai",
                "said": "شکریہ، خدا حافظ۔",
            },
        ],
    }
    record.update(changes)
    return record


@pytest.fixture
async def store() -> AsyncIterator[store_module.Store]:
    connected = await store_module.connect(os.environ["DATABASE_URL"])
    yield connected
    await connected.close()


@pytest.fixture
async def saved(store: store_module.Store) -> AsyncIterator[dict[str, Any]]:
    record = make_record()
    await store.save(record)
    yield record
    await store._pool.execute("delete from calls where call_id = $1", record["call_id"])


async def test_a_call_is_saved_with_every_turn(store: store_module.Store, saved: dict) -> None:
    call = await store._pool.fetchrow("select * from calls where call_id = $1", saved["call_id"])
    turns = await store._pool.fetch(
        "select * from turns where call_id = $1 order by turn", saved["call_id"]
    )

    assert call["caller_id"] == saved["caller_id"]
    assert call["language"] == "ur-PK"
    assert call["outcome"] == "done"
    assert [turn["said"] for turn in turns] == [turn["said"] for turn in saved["turns"]]
    assert turns[1]["heard"] == "جی ہاں theek hai"


async def test_slots_and_fields_survive_as_json(store: store_module.Store, saved: dict) -> None:
    """A second language brings different slot names. JSONB takes them without a
    schema change, and Postgres can still filter on the keys."""
    confirmed = await store._pool.fetchval(
        "select slots->>'confirmed' from calls where call_id = $1", saved["call_id"]
    )
    name = await store._pool.fetchval(
        "select fields->>'name' from calls where call_id = $1", saved["call_id"]
    )

    assert confirmed == "true"
    assert name == "انس"


async def test_calls_are_found_by_caller_id(store: store_module.Store, saved: dict) -> None:
    """The reason caller_id exists. Everything one number ever said, in order."""
    calls = await store._pool.fetch(
        "select call_id from calls where caller_id = $1 order by started_at desc",
        saved["caller_id"],
    )

    assert saved["call_id"] in [call["call_id"] for call in calls]


async def test_urdu_words_are_searchable(store: store_module.Store, saved: dict) -> None:
    hits = await store.search("چار بجے")

    assert saved["call_id"] in [hit["call_id"] for hit in hits]
    assert any(hit["turn"] == 1 for hit in hits if hit["call_id"] == saved["call_id"])


async def test_roman_urdu_is_searchable_regardless_of_case(
    store: store_module.Store, saved: dict
) -> None:
    hits = await store.search("THEEK")

    assert [hit["turn"] for hit in hits if hit["call_id"] == saved["call_id"]] == [2]


async def test_deleting_a_call_takes_its_turns_with_it(store: store_module.Store) -> None:
    record = make_record()
    await store.save(record)

    await store._pool.execute("delete from calls where call_id = $1", record["call_id"])

    left = await store._pool.fetchval(
        "select count(*) from turns where call_id = $1", record["call_id"]
    )
    assert left == 0


async def test_the_same_call_cannot_be_saved_twice(store: store_module.Store, saved: dict) -> None:
    """A duplicate is a bug upstream. Silently overwriting would hide it."""
    import asyncpg

    with pytest.raises(asyncpg.UniqueViolationError):
        await store.save(saved)


async def test_a_wrong_url_fails_with_the_error_main_catches() -> None:
    with pytest.raises(store_module.ConnectionFailed):
        await store_module.connect("postgresql://nobody:wrong@127.0.0.1:1/none")


def test_recording_keys_are_by_day_then_call() -> None:
    """One folder per day, so a bucket listing reads like a calendar."""
    key = store_module.Recordings.key("2026-09-17_22-41-31_a126bd", "2026-09-17T22:41:32+00:00")

    assert key == "calls/2026/09/17/2026-09-17_22-41-31_a126bd.wav"


async def test_an_upload_hands_the_file_to_s3_as_audio(tmp_path: Any) -> None:
    class FakeClient:
        def __init__(self) -> None:
            self.calls: list[Any] = []

        def upload_file(self, *args: Any, **kwargs: Any) -> None:
            self.calls.append((args, kwargs))

    client = FakeClient()
    recording = tmp_path / "call.wav"
    recording.write_bytes(b"RIFF")

    key = await store_module.Recordings("bucket", client).upload(recording, "calls/x.wav")

    assert key == "calls/x.wav"
    assert client.calls == [
        ((str(recording), "bucket", "calls/x.wav"), {"ExtraArgs": {"ContentType": "audio/wav"}})
    ]


@pytest.mark.skipif(not os.getenv("S3_BUCKET"), reason="needs S3_BUCKET and AWS credentials")
async def test_a_real_upload_lands_in_the_bucket(tmp_path: Any) -> None:
    """Against the real bucket, then deleted. Proves the credentials and the
    bucket policy, which no fake can."""
    import wave

    recordings = store_module.recordings(os.environ["S3_BUCKET"], os.environ["AWS_REGION"])
    path = tmp_path / "call.wav"
    with wave.open(str(path), "wb") as tape:
        tape.setnchannels(2)
        tape.setsampwidth(2)
        tape.setframerate(8000)
        tape.writeframes(b"\x00" * 3200)
    key = recordings.key(f"test_{uuid.uuid4().hex[:8]}", datetime.now().astimezone().isoformat())

    await recordings.upload(path, key)
    try:
        head = recordings._client.head_object(Bucket=recordings.bucket, Key=key)
    finally:
        recordings._client.delete_object(Bucket=recordings.bucket, Key=key)

    assert head["ContentType"] == "audio/wav"
    assert head["ContentLength"] == path.stat().st_size


KEYS = ("ANTHROPIC_API_KEY", "AZURE_SPEECH_KEY", "ELEVENLABS_API_KEY")


@pytest.mark.skipif(not all(os.getenv(key) for key in KEYS), reason="needs all three API keys")
async def test_a_real_call_lands_in_postgres(store: store_module.Store, tmp_path: Any) -> None:
    """The whole agent, then the whole store. Real recognizer, real Claude, real
    voice, the caller synthesised in the male voice, then `save_call` exactly as
    main.py and demo.py call it. Proves the record the session builds is one
    the store accepts, which no fake can."""
    import asyncio

    from voice_agent import llm, stt, tts
    from voice_agent.config import settings
    from voice_agent.language import load as load_language
    from voice_agent.main import save_call
    from voice_agent.session import Session

    language = load_language("ur-PK")
    voice = tts.build(
        os.environ["AZURE_SPEECH_KEY"],
        os.environ["AZURE_SPEECH_REGION"],
        language.tts_voice,
        cache_dir=tmp_path / "cache",
        locale=language.locale,
    )
    caller = tts.build(
        os.environ["AZURE_SPEECH_KEY"],
        os.environ["AZURE_SPEECH_REGION"],
        "ur-PK-AsadNeural",
        cache_dir=tmp_path / "caller_cache",
    )
    call_id = f"test_{uuid.uuid4().hex[:8]}"
    session = Session(
        language=language,
        stt=stt.build("elevenlabs", os.environ["ELEVENLABS_API_KEY"], language.keyterms),
        tts=voice,
        extractor=llm.build(os.environ["ANTHROPIC_API_KEY"]),
        work_dir=tmp_path / call_id,
        caller_id="03001234567",
    )

    await session.open()
    try:
        async for _ in session.greet(name="انس", date="کل", time="چار").chunks:
            pass
        async for chunk in caller.tts.stream("جی ہاں، ٹھیک ہے"):
            await session.listen(chunk)
            await asyncio.sleep(settings.audio.frame_ms / 1000)
        async for _ in (await session.answer()).chunks:
            pass
    finally:
        await session.close()

    await save_call(session, store, None)
    try:
        call = await store._pool.fetchrow("select * from calls where call_id = $1", call_id)
        turns = await store._pool.fetch(
            "select * from turns where call_id = $1 order by turn", call_id
        )
    finally:
        await store._pool.execute("delete from calls where call_id = $1", call_id)

    print(f"\n  heard: {turns[1]['heard']}\n  outcome: {call['outcome']} {call['slots']}")
    assert call["caller_id"] == "03001234567"
    assert call["language"] == "ur-PK"
    assert len(turns) == 2
    assert turns[1]["heard"], "nothing came back from the recognizer"
    assert turns[1]["seconds_from_start"] > turns[0]["seconds_from_start"]
    assert (session.work_dir / "transcript.txt").exists()
    assert call["outcome"] == "done", f"the caller agreed, got {call['outcome']}"
