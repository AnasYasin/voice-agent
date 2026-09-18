"""Components 7 and 8. Call transcripts to Postgres, recordings to S3.

One row per call, one row per turn. The turn text is indexed for full-text
search with the `simple` configuration, which splits on whitespace and
punctuation and lowercases. Urdu script has no stemmer in Postgres and needs
none for this; Roman Urdu and English words in the same line match too.

The recording is the stereo `call.wav` the session wrote, uploaded under
`calls/YYYY/MM/DD/<call_id>.wav`. Its key goes on the call row, so a call in
Postgres leads to its audio. The bucket deletes recordings after 90 days; the
transcript stays.

Written once, when the call ends. Nothing here runs while a caller is waiting.
The transcript JSON in the call folder is the record until then.

Reads its connection URL and bucket from whoever builds it. main.py reads the
environment.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import asyncpg
import boto3

ConnectionFailed = (OSError, asyncpg.PostgresError)

SCHEMA = """
create table if not exists calls (
    call_id     text primary key,
    caller_id   text not null,
    language    text not null,
    started_at  timestamptz not null,
    ended_at    timestamptz not null,
    outcome     text not null,
    slots       jsonb not null,
    fields      jsonb not null,
    audio_dir   text not null,
    recording   text
);
create index if not exists calls_by_caller on calls (caller_id, started_at desc);
create index if not exists calls_by_language on calls (language, started_at desc);

create table if not exists turns (
    call_id             text not null references calls on delete cascade,
    turn                integer not null,
    state               text not null,
    seconds_from_start  real not null,
    heard               text not null,
    said                text not null,
    search              tsvector generated always as
                        (to_tsvector('simple', heard || ' ' || said)) stored,
    primary key (call_id, turn)
);
create index if not exists turns_search on turns using gin (search);
"""

INSERT_CALL = """
insert into calls
    (call_id, caller_id, language, started_at, ended_at, outcome, slots, fields, audio_dir,
     recording)
values ($1, $2, $3, $4, $5, $6, $7::jsonb, $8::jsonb, $9, $10)
"""

INSERT_TURN = """
insert into turns (call_id, turn, state, seconds_from_start, heard, said)
values ($1, $2, $3, $4, $5, $6)
"""

SEARCH = """
select turns.call_id, calls.caller_id, calls.language, calls.started_at,
       turns.turn, turns.heard, turns.said
from turns join calls using (call_id)
where turns.search @@ websearch_to_tsquery('simple', $1)
order by calls.started_at desc, turns.turn
limit $2
"""


class Store:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def save(self, record: dict[str, Any]) -> None:
        """A finished call, as `Session.record()` shapes it. One transaction."""
        turns = [
            (
                record["call_id"],
                turn["turn"],
                turn["state"],
                turn["seconds_from_start"],
                turn["heard"],
                turn["said"],
            )
            for turn in record["turns"]
        ]
        async with self._pool.acquire() as connection, connection.transaction():
            await connection.execute(
                INSERT_CALL,
                record["call_id"],
                record["caller_id"],
                record["language"],
                datetime.fromisoformat(record["started_at"]),
                datetime.fromisoformat(record["ended_at"]),
                record["outcome"],
                json.dumps(record["slots"], ensure_ascii=False),
                json.dumps(record["fields"], ensure_ascii=False),
                record["audio_dir"],
                record["recording"],
            )
            await connection.executemany(INSERT_TURN, turns)

    async def search(self, words: str, limit: int = 20) -> list[asyncpg.Record]:
        """Turns where the caller or the agent said these words, newest call first."""
        return await self._pool.fetch(SEARCH, words, limit)

    async def close(self) -> None:
        await self._pool.close()


async def connect(url: str) -> Store:
    """Connect and make sure the tables exist. Raises `ConnectionFailed`."""
    pool = await asyncpg.create_pool(url, min_size=1, max_size=2)
    async with pool.acquire() as connection:
        await connection.execute(SCHEMA)
    return Store(pool)


class Recordings:
    """Component 8. One stereo WAV per call, in S3.

    Credentials come from wherever boto3 finds them. On the box that is the
    instance role, so no key is ever written to disk there.
    """

    def __init__(self, bucket: str, client: Any) -> None:
        self.bucket = bucket
        self._client = client

    @staticmethod
    def key(call_id: str, started_at: str) -> str:
        day = datetime.fromisoformat(started_at)
        return f"calls/{day:%Y/%m/%d}/{call_id}.wav"

    async def upload(self, path: Path, key: str) -> str:
        """Blocking network work, moved off the loop so other calls keep going."""
        await asyncio.to_thread(
            self._client.upload_file,
            str(path),
            self.bucket,
            key,
            ExtraArgs={"ContentType": "audio/wav"},
        )
        return key


def recordings(bucket: str, region: str) -> Recordings:
    return Recordings(bucket, boto3.client("s3", region_name=region))
