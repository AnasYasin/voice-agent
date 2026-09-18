# Streaming, the 4.1 second problem

## Where it started

Measured warm, per turn, 21 Aug 2026, `eastus`:

| Stage | Time | Why it costs that |
|---|---|---|
| VAD silence | 400 ms | waiting to be sure the caller stopped |
| ffmpeg ×2 | 126 ms | subprocess spawn, in and out |
| STT | 540 ms | whole file uploaded after the caller finishes |
| LLM | 1200 ms | whole reply generated before anything is spoken |
| Azure TTS | 1800 ms | whole reply synthesised, US round trip |
| **Total** | **4100 ms** | every stage waits for the one before it |

Vapi lands near 800 ms. Not because it is parallel, because it is **pipelined**.
Each stage starts on partial output of the one before, so the total is roughly
the longest single stage plus handoffs rather than the sum of all of them.

Threads do not help. There is no independent work to overlap. STT needs the
audio, the model needs the transcript, TTS needs the text. What is needed is
the streaming APIs at each stage.

## Where it is now

All three stages stream. Measured the same way, `eastus`, real APIs, from the
caller falling silent to the first audio frame:

| Stage | Before | After | What changed |
|---|---|---|---|
| ffmpeg in | 63 ms | 0 ms | band-pass is a per-chunk filter, no subprocess |
| STT | 540 ms | 200 ms | audio goes up as they speak, the end is a commit |
| LLM, first sentence | 1200 ms | 990 ms | first sentence out while the rest is written |
| TTS, first chunk | 1800 ms | 750 ms | playback starts on the first chunk |
| ffmpeg out | 63 ms | 0 ms | same filter, the other direction |
| **Caller stops → first audio** | **3700 ms** | **1830 ms** | plus the 400 ms VAD wait either way |

`tests/test_session.py::test_a_real_streamed_turn_end_to_end` is that
measurement, and it prints the number.

### What did not improve as much as expected

**The model, for short replies.** The plan assumed streaming the LLM would take
1200 ms to about 400 ms. It takes it to about 990 ms, because what the voice
needs is not the first token but the first whole *sentence*, and a typical phone
reply is one sentence. First token is around 690 ms; the rest is the model
finishing the thought. Streaming the model pays off on a two or three sentence
answer, where the second sentence is written while the first is being spoken,
and pays nothing on "جی ٹھیک ہے".

Splitting on clause boundaries instead was measured and rejected. The model
puts ۔ before ، often enough that it does not reliably arrive earlier, and
synthesising half a clause hurts the voice more than the milliseconds are worth.

**The STT commit, if audio is not paced.** 200 ms assumes frames arrive in real
time, which is what a live call does. Pushing a buffered file at the recognizer
as fast as the socket takes it leaves it a backlog to work through, and the
commit costs 500 ms instead. Worth knowing when writing tests against it.

### The gap that only showed up on a real call

Time to first audio was right, and the reply still sounded wrong: a pause
before every sentence after the first. Handing chunks straight from the
synthesiser to the transport makes the whole chain pull-based, so the next
sentence was only sent to Azure once the previous one had finished playing, and
the caller heard that round trip.

`Session._chunks` now runs synthesis in its own task behind a bounded queue, so
it works ahead of playback by `tts.lookahead_seconds`. In the log you can see it:
sentences reaching Azure at `:18 :19 :20` and playing at `:18 :21 :23`.

The buffer has to cover a synthesis *and* the model writing the next sentence,
which is the slower half. At 1.5 s a long reply still ran dry once; 3 s is the
default. The only cost of a bigger one is audio thrown away when a caller
interrupts.

The queue carries each sentence ahead of its own chunks, so the transcript
records what the caller heard rather than what the buffer was holding when they
interrupted.

## What was built

### Chunked TTS

`AzureTTS.stream` uses `start_speaking_text_async` and reads the
`AudioDataStream` as it fills, at `Raw8Khz16BitMonoPcm`. Raw rather than RIFF,
because there is no file and the first bytes have to be playable samples.

Band-limiting moved to `audio.Telephone`, a pair of Butterworth biquads that
carry filter state between chunks. Same 300-3400 Hz band as `to_telephone`, no
subprocess, and it runs in both directions.

`Session._chunks` is the new contract: an async iterator of PCM. `Reply.audio`
is still a `Path` for the campaign path and the tests that assert on WAV files.

A fixed script line does **not** stream. It is already a cached WAV, so it goes
through `synthesize` and `to_telephone` exactly as before.

### Streaming LLM

`ClaudeResponder.stream` returns an `Utterance`: iterate it for text, read
`.text` and `.end_call` once it runs out.

No JSON. Structured output is what forces the whole reply to exist before any of
it can be read. `end_call` comes back as a **stop sequence** instead, so the API
stops at the marker and it never reaches the transcript or the voice. That is
cheaper than a sentinel the normaliser has to strip, and it cannot be split
across two deltas.

Sentence splitting is `normalizer.sentences(buffer) -> (finished, tail)`, in
`lang/ur/`, because ۔ is not a full stop and where a sentence ends is language
data. `Session._sentences` drives it.

### Streaming STT

`ScribeStream`, `scribe_v2_realtime`, one websocket for the whole call.
`session.listen(pcm)` pushes caller frames as the transport receives them;
`session.answer()` commits the turn and gets the text.

Endpointing stays with Silero, which the transport already runs for barge-in.
The server can do its own, but two voice detectors disagreeing about where a
turn ended is worse than either alone.

The audio is pushed continuously, silence included. Gating on speech would mean
deciding where a turn starts twice, and the first word is what gets lost when
the two decisions differ.

The event field is `text`, not `transcript` as the SDK docstrings claim.

Deepgram has no streaming mode here. It is in the project to be scored against
ElevenLabs on files, and `session.open()` refuses it up front rather than
failing three seconds into a call.

### The deadlock the buffer brought with it

Raising `lookahead_seconds` to 3 exposed one. Barge-in stops the reader, which
then waits for the writer to finish; the writer was blocked putting into a full
queue nobody was draining. A live call froze on interruption rather than
failing. `Session._fill` no longer puts anything into the queue on the
cancellation path. `test_interrupting_a_full_buffer_does_not_deadlock` pins it,
and fails by timeout rather than hanging.

Worth remembering when touching that queue: the reader waits on the writer, so
the writer must never wait on the reader.

## Still on the table

- **Azure region.** `eastus` → `centralindia` or `uaenorth`. Config only, no
  code. Three round trips per turn now sit on it, so it is worth more than it
  was. Anas is doing this last, deliberately.
- **An intermittent segfault.** The agent died once in six calls, at the moment
  the caller joined, before any streaming code ran. Native, so either the
  LiveKit rtc bindings or Silero. Not reproducible on demand and not chased.
- **The VAD window.** `vad_silence_seconds` is still 0.4 and is now the largest
  single thing left in the wait. Streaming STT means shortening it costs
  accuracy rather than latency, but how short is safe needs real recordings.

## What must not break

- `Session` still knows nothing about its transport. It is handed PCM and hands
  back PCM.
- The campaign path keeps working. Fixed script lines are cached WAVs.
- Barge-in cancels a stream mid-chunk. Closing the iterator stops Azure
  synthesising and abandons the model request behind it.
- Nothing Urdu-specific outside `lang/`.
