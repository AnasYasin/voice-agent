"""Component 13. One call, wired end to end.

    caller audio -> 8 kHz -> stt -> normalise -> llm -> flow
                                                          |
    caller audio <- 8 kHz <- tts <- normalise <-----------+

It never learns which transport it is on. A browser tab, a softphone and a
real GSM line all hand it a WAV and play back the WAV it returns, which is why
one session gives you all three for free.

Drive it like this:

    reply = session.start(name="انس", date="کل", time="چار")
    play(reply.audio)
    while not session.finished:
        reply = session.hear(record())      # or hear(key="1")
        play(reply.audio)
    store(session.result)

Audio for the call lands in `work_dir`. Nothing is persisted beyond it; that
is store.py's job, later.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from voice_agent import audio as audio_module
from voice_agent.flow import Flow, Result, Turn
from voice_agent.language import Language

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Exchange:
    """One turn of the call, kept for the transcript."""

    state: str
    heard: str
    said: str


@dataclass(frozen=True)
class Reply:
    """What the transport should play, plus what led to it."""

    audio: Path
    say: str
    state: str
    expects_reply: bool
    dtmf: dict[str, Any] = field(default_factory=dict)
    heard: str = ""


class Session:
    def __init__(
        self,
        language: Language,
        stt: Any,
        tts: Any,
        extractor: Any,
        work_dir: Path,
    ) -> None:
        self.language = language
        self.stt = stt
        self.tts = tts
        self.work_dir = work_dir
        self.flow = Flow(language.script, extractor)
        self.transcript: list[Exchange] = []
        self._turns = 0

        self.work_dir.mkdir(parents=True, exist_ok=True)

    @property
    def finished(self) -> bool:
        return self.flow.finished

    @property
    def result(self) -> Result:
        return self.flow.result

    def start(self, **fields: Any) -> Reply:
        """Open the call. `fields` fill the script placeholders."""
        log.info("call starting in %s", self.language.locale)
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
        self.transcript.append(Exchange(state=turn.state, heard=heard, said=turn.say))

        # Digits and clock times have to become Urdu words before synthesis,
        # or the voice reads "15:00" as a numeral.
        spoken = self.language.normalizer.for_speech(turn.say)
        wide = self.tts.synthesize(spoken)

        phone = self.work_dir / f"said_{self._turns:02d}.wav"
        audio_module.to_telephone(wide.path, phone)

        log.info("[%s] saying: %s", turn.state, turn.say)
        return Reply(
            audio=phone,
            say=turn.say,
            state=turn.state,
            expects_reply=turn.expects_reply,
            dtmf=turn.dtmf,
            heard=heard,
        )
