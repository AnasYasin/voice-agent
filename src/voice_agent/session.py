"""Component 13. One call, wired end to end.

    caller audio -> 8 kHz -> stt -> normalise -> llm -> flow
                                                          |
    caller audio <- 8 kHz <- tts <- normalise <-----------+

It never learns which transport it is on. A browser tab, a softphone and a
real GSM line all hand it audio and play back the audio it returns, which is
why one session gives you all three for free.

There are two ways to drive it, and the difference is whether each stage waits
for the one before it to finish.

By file, a turn at a time. Simple, synchronous, and what the campaign and every
test use:

    reply = session.start(name="انس", date="کل", time="چار")
    play(reply.audio)
    while not session.finished:
        reply = session.hear(record())      # or hear(key="1")
        play(reply.audio)
    store(session.result)

By stream, for a live call. Audio goes to the recognizer while the caller is
still speaking, the model's reply is spoken a sentence at a time while the rest
is still being written, and playback starts on the first chunk of the first
sentence:

    await session.open()
    speaking = session.greet(name="انس", date="کل", time="چار")
    play(speaking.chunks)               # cancellable mid-chunk
    while not session.finished:
        await session.listen(frame)     # continuously, as audio arrives
        speaking = await session.answer()
        play(speaking.chunks)
    await session.close()

Same flow engine, same language pack, same transcript. Streaming does not make
the call smarter, it stops the caller waiting for four stages in a row.

Audio for the call lands in `work_dir`: one WAV per line the agent said, and on
a live call one continuous `caller.wav`, because a caller streaming into an
open recognizer never produces a file per turn. `transcript.json` sits next to
them and is rewritten after every exchange, so a call that dies mid-sentence
still leaves its transcript on disk. Postgres gets the same record once, at the
end; that is store.py.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterable, AsyncIterator
from contextlib import aclosing, suppress
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from voice_agent import audio as audio_module
from voice_agent.config import settings
from voice_agent.flow import Conversation, Flow, Result, Turn
from voice_agent.language import Language
from voice_agent.stt import StreamingSpeechToText

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Exchange:
    """One turn of the call, kept for the transcript."""

    state: str
    heard: str
    said: str
    seconds_from_start: float = 0.0


@dataclass(frozen=True)
class Reply:
    """What the transport should play, plus what led to it."""

    audio: Path | None
    say: str
    state: str
    expects_reply: bool
    dtmf: dict[str, Any] = field(default_factory=dict)
    heard: str = ""


@dataclass(frozen=True)
class Speaking:
    """The same thing on a live call, except the audio does not exist yet.

    `chunks` is 16-bit mono PCM at the line's rate. Stop iterating it to stop
    the agent talking; that cancels the synthesis and, if the words were still
    being written, the model as well.

    There is no `say` and no `heard` here. What was said is only known once the
    stream has run out, and by then it is in `session.transcript`.
    """

    chunks: AsyncIterator[bytes]
    state: str
    expects_reply: bool
    dtmf: dict[str, Any] = field(default_factory=dict)


class Session:
    def __init__(
        self,
        language: Language,
        stt: Any,
        tts: Any,
        extractor: Any,
        work_dir: Path,
        responder: Any = None,
        conversation: bool = False,
        caller_id: str = "",
    ) -> None:
        self.language = language
        self.stt = stt
        self.tts = tts
        self.work_dir = work_dir
        self.caller_id = caller_id
        self.started_at = datetime.now().astimezone()
        self._clock_start = time.monotonic()
        self._fields: dict[str, Any] = {}
        # Talk mode swaps the engine, not the plumbing. Everything below this
        # line, and every transport above it, is unchanged either way.
        self.flow: Any
        if conversation:
            self.flow = Conversation(responder, language.persona, language.greeting)
        else:
            self.flow = Flow(
                language.script, extractor, responder=responder, persona=language.persona
            )
        self.transcript: list[Exchange] = []
        self._turns = 0
        self._ears: Any = None
        self._caller: Any = None
        self._band: Any = None
        self._lookahead = int(settings.tts.lookahead_seconds * 1000 / settings.audio.frame_ms)

        self.work_dir.mkdir(parents=True, exist_ok=True)

    @property
    def finished(self) -> bool:
        return self.flow.finished

    @property
    def result(self) -> Result:
        """The outcome so far. A call that ended before the script did is `cut_off`
        with whatever slots were filled by then."""
        if self.finished:
            return self.flow.result
        return Result(outcome="cut_off", slots=dict(self.flow.slots))

    def record(self) -> dict[str, Any]:
        """Everything known about the call, in the shape the JSON file and the
        store both take. Valid mid-call, so it can be written after every turn."""
        result = self.result
        return {
            "call_id": self.work_dir.name,
            "caller_id": self.caller_id,
            "language": self.language.locale,
            "started_at": self.started_at.isoformat(timespec="seconds"),
            "ended_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "fields": self._fields,
            "outcome": result.outcome,
            "slots": result.slots,
            "audio_dir": str(self.work_dir),
            "turns": [
                {
                    "turn": number,
                    "state": exchange.state,
                    "seconds_from_start": exchange.seconds_from_start,
                    "heard": exchange.heard,
                    "said": exchange.said,
                }
                for number, exchange in enumerate(self.transcript, start=1)
            ],
        }

    def _note(self, state: str, heard: str, said: str) -> None:
        """One exchange into the transcript, and the transcript onto disk."""
        elapsed = round(time.monotonic() - self._clock_start, 2)
        self.transcript.append(
            Exchange(state=state, heard=heard, said=said, seconds_from_start=elapsed)
        )
        (self.work_dir / "transcript.json").write_text(
            json.dumps(self.record(), ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def _begin(self, fields: dict[str, Any]) -> None:
        """The call starts when the greeting does, which is when the recording
        does. The session may have sat waiting for the caller before this."""
        self._fields = fields
        self.started_at = datetime.now().astimezone()
        self._clock_start = time.monotonic()

    def start(self, **fields: Any) -> Reply:
        """Open the call. `fields` fill the script placeholders."""
        log.info("call starting in %s", self.language.locale)
        self._begin(fields)
        return self._speak(self.flow.start(**fields))

    def hear(self, recording: Path | None = None, key: str = "") -> Reply:
        """One caller turn. A keypad press skips STT and the model entirely."""
        if self.finished:
            raise RuntimeError("call has ended")

        heard = "" if key else self._listen(recording)
        turn = self.flow.reply(said=heard, key=key)
        return self._speak(turn, heard=heard)

    def _listen(self, recording: Path | None) -> str:
        """Caller audio to text the flow engine can use."""
        if recording is None:
            return ""

        # Browser audio is 48 kHz and would flatter the STT. Everything gets
        # band-limited so a demo sounds like the phone call it is standing in for.
        phone = self.work_dir / f"heard_{self._turns:02d}.wav"
        audio_module.to_telephone(recording, phone)

        transcript = self.stt.transcribe(phone, language=self.language.stt_language)
        heard = self.language.normalizer.from_speech(transcript.text)
        log.info("heard: %s", heard)
        return heard

    def _speak(self, turn: Turn, heard: str = "") -> Reply:
        """Flow text to audio the transport can play."""
        self._turns += 1
        self._note(turn.state, heard, turn.say)

        # Nothing to say means say nothing. Synthesising an empty string would
        # cost an API call to produce a WAV of silence, and the transport would
        # then dutifully play it.
        if not turn.say.strip():
            return Reply(
                audio=None,
                say="",
                state=turn.state,
                expects_reply=turn.expects_reply,
                dtmf=turn.dtmf,
                heard=heard,
            )

        log.info("[%s] saying: %s", turn.state, turn.say)
        return Reply(
            audio=self._scripted(turn.say),
            say=turn.say,
            state=turn.state,
            expects_reply=turn.expects_reply,
            dtmf=turn.dtmf,
            heard=heard,
        )

    def _scripted(self, say: str) -> Path:
        """A line of fixed text to a playable 8 kHz WAV.

        Digits and clock times have to become Urdu words before synthesis, or
        the voice reads "15:00" as a numeral. The result is cached, so on the
        second call this is a disk read and an ffmpeg pass.
        """
        wide = self.tts.synthesize(self.language.normalizer.for_speech(say))
        phone = self.work_dir / f"said_{self._turns:02d}.wav"
        return audio_module.to_telephone(wide.path, phone)

    # --- the live call ---

    async def open(self) -> None:
        """Start streaming. One recognizer and one recording for the whole call.

        The recognizer stays open because the caller does: closing it between
        turns would put a connection handshake in front of every reply, which
        is the cost streaming was supposed to remove.
        """
        if not isinstance(self.stt, StreamingSpeechToText):
            raise TypeError(
                f"{self.stt.name} has no streaming mode. A live call needs one; "
                "file transcription is for the accuracy gate."
            )

        self._ears = self.stt.stream(self.language.stt_language)
        await self._ears.open()
        self._band = audio_module.Telephone()
        self._caller = audio_module.Tape(self.work_dir / "caller.wav")

    async def close(self) -> None:
        await self._ears.close()
        self._caller.close()

    async def listen(self, pcm: bytes) -> None:
        """Caller audio, as the transport receives it.

        Band-limited once, then it goes to the recognizer and to the recording
        and nowhere else. Browser audio would otherwise flatter the STT, and a
        demo that sounds better than the phone call it stands in for is a demo
        that lies.
        """
        pcm = self._band(pcm)
        self._caller.write(pcm)
        await self._ears.push(pcm)

    def greet(self, **fields: Any) -> Speaking:
        """Open the call. `fields` fill the script placeholders."""
        log.info("call starting in %s", self.language.locale)
        self._begin(fields)
        return self._speaking(self.flow.start(**fields))

    async def answer(self, key: str = "") -> Speaking:
        """One caller turn. A keypad press skips the recognizer and the model."""
        if self.finished:
            raise RuntimeError("call has ended")

        heard = "" if key else await self._heard()
        return self._speaking(self.flow.reply_stream(said=heard, key=key), heard)

    async def _heard(self) -> str:
        """What the caller just said. Their audio went up while they were saying
        it, so this is a round trip rather than an upload."""
        heard = self.language.normalizer.from_speech(await self._ears.commit())
        log.info("heard: %s", heard)
        return heard

    def _speaking(self, turn: Turn, heard: str = "") -> Speaking:
        self._turns += 1
        return Speaking(
            chunks=self._chunks(turn, heard),
            state=turn.state,
            expects_reply=turn.expects_reply,
            dtmf=turn.dtmf,
        )

    async def _chunks(self, turn: Turn, heard: str) -> AsyncIterator[bytes]:
        """Everything the transport plays for one turn, as soon as it exists.

        The transcript entry is written at the end rather than the start,
        because until then there may be nothing to write. A turn the caller
        talked over is recorded as the part of it they actually heard.
        """
        said: list[str] = []
        try:
            if turn.stream is None:
                said.append(turn.say)
                # Nothing to say means say nothing. Synthesising an empty string
                # would cost an API call to produce a WAV of silence, and the
                # transport would then dutifully play it.
                if turn.say.strip():
                    log.info("[%s] saying: %s", turn.state, turn.say)
                    yield audio_module.pcm(await asyncio.to_thread(self._scripted, turn.say))
                return

            # Synthesis runs ahead of playback rather than taking turns with it.
            # Handing chunks straight to the transport reads well, but means the
            # next sentence is only sent to Azure once the previous one has
            # finished playing, and the caller hears that round trip as a pause
            # between every sentence.
            queue: asyncio.Queue[str | bytes | None] = asyncio.Queue(maxsize=self._lookahead)
            filling = asyncio.create_task(self._fill(turn, queue))
            try:
                while (item := await queue.get()) is not None:
                    if isinstance(item, bytes):
                        yield item
                        continue
                    # A sentence, reaching the transport ahead of its own audio.
                    # Recorded here rather than where it was synthesised, so the
                    # transcript says what the caller heard and not what the
                    # buffer happened to be holding when they interrupted.
                    said.append(item)
                    log.info("[%s] saying: %s", turn.state, item)
                await filling  # empty because it finished, or because it failed
            finally:
                # Awaited, not just cancelled. Cancelling only schedules the
                # teardown, and the far ends of it are a synthesiser and a model
                # request that should be released before the next turn opens
                # its own.
                filling.cancel()
                with suppress(asyncio.CancelledError):
                    await filling
                # The queue is the only place that knows which sentences got
                # out. The flow wrote its own memory from the model's text,
                # which runs a whole lookahead buffer ahead of playback, so
                # tell it what the caller actually heard.
                self.flow.spoken(" ".join(said))
        finally:
            self._note(turn.state, heard, " ".join(said))

    async def _fill(self, turn: Turn, queue: asyncio.Queue) -> None:
        """Sentences to audio, as far ahead as the queue allows.

        Each sentence goes in ahead of its own chunks, so the reader knows what
        it is about to play. Everything else in the queue is PCM.

        aclosing, here and below, is what makes barge-in reach the far end
        rather than waiting for the garbage collector to notice. Closing this
        stops Azure synthesising and abandons the model request behind it.
        """
        band = audio_module.Telephone()
        try:
            with audio_module.Tape(self.work_dir / f"said_{self._turns:02d}.wav") as tape:
                async with aclosing(self._sentences(turn.stream)) as sentences:
                    async for sentence in sentences:
                        await queue.put(sentence)
                        spoken = self.language.normalizer.for_speech(sentence)
                        async with aclosing(self.tts.stream(spoken)) as voice:
                            async for chunk in voice:
                                chunk = band(chunk)
                                tape.write(chunk)
                                await queue.put(chunk)
        except asyncio.CancelledError:
            # Barge-in. The reader has stopped draining and is waiting on this
            # task to end, so the queue it walked away from is probably full.
            # Putting anything into it here would block on a reader that is
            # blocked on us.
            raise
        except Exception:
            await queue.put(None)  # so the reader stops, then re-raises this
            raise
        else:
            await queue.put(None)

    async def _sentences(self, stream: AsyncIterable[str]) -> AsyncIterator[str]:
        """Whole sentences out of a reply that is still being written.

        Where a sentence ends is language data, so it comes from the normaliser
        rather than a regex in here. Whatever is left over when the model stops
        is spoken as it is; a last sentence often has no full stop on it.
        """
        buffer = ""
        async with aclosing(stream.__aiter__()) as pieces:
            async for piece in pieces:
                buffer += piece
                finished, buffer = self.language.normalizer.sentences(buffer)
                for sentence in finished:
                    yield sentence

        if buffer.strip():
            yield buffer.strip()
