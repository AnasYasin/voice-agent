# Urdu Voice Agent

Outbound voice agent that calls leads in Pakistan and confirms appointments in
Urdu, then records the structured outcome.

## Stack

| Layer | Choice |
|---|---|
| Orchestration | LiveKit Agents, self-hosted |
| Telephony | Asterisk plus GSM gateway, licensed SIP trunk later |
| STT | ElevenLabs Scribe v2 Realtime, comparing against Deepgram Nova-3 |
| TTS | Azure Neural `ur-PK-UzmaNeural`, fixed lines cached as WAV |
| LLM | Claude Haiku 4.5, slot extraction only |
| Storage | S3 for recordings, Postgres for results |

The binding constraint on this project is a Pakistani local caller ID, not AI
quality. Managed platforms cannot provide one, and calls placed from outside
Pakistan pay the international settlement rate instead of the Rs 0.30/min
domestic rate, which is why they cost roughly 12 times more per call.

## Phasing

| Phase | What | Hardware |
|---|---|---|
| **0** | Measure Urdu STT accuracy on 8 kHz phone audio | None |
| 1 | Conversation flow end to end, browser or softphone | None |
| 2 | State machine, DTMF fallback, recording, structured results | None |
| 3 | Pilot on real leads | GSM gateway plus SIMs |
| 4 | Licensed SIP trunk, dialer, answering machine detection | Trunk |

Phases 0 to 2 need no telephony hardware and no purchase decision. A tester in
Pakistan can reach the agent over a softphone at zero cost per call.

## Testing it

See [TESTING.md](TESTING.md) for how to run the tests, hear the Urdu voice,
and talk to the agent in a browser.

## Start here

Phase 0 is the go/no-go gate. Every accuracy figure used in planning was an
estimate, including the published 3.1% Urdu WER, which is measured on clean
studio audio rather than a phone line.

```bash
cp .env.example .env          # add ELEVENLABS_API_KEY and DEEPGRAM_API_KEY
make install-eval
# drop 25-30 Urdu recordings into eval/samples/raw/
make prep                     # band-limit to 8 kHz telephone audio
make eval
```

See `eval/README.md` for how to read the result.

## Layout

```
config/          defaults.yaml, every tunable parameter in one file
src/voice_agent/
  config.py      the only module that reads config/defaults.yaml
  audio.py       telephone-band conversion, both directions
  stt.py         elevenlabs + deepgram behind one protocol
  tts.py         azure voice + the cache for fixed lines
  flow.py        the state machine. knows nothing about telephony
  session.py     wires a call. never knows its transport
  transport.py   browser, SIP, PSTN
  lang/ur/       normalisation, keyterms, call script

eval/            Phase 0 accuracy harness
scripts/         manual tools, compare_stt and roundtrip
telephony/       Asterisk config, including the NAT settings
```

One file per component, promoted to a folder at roughly 300 lines or three
real implementations. Most of the files above are still stubs, see the build
plan for what is actually done.

Two design rules worth keeping. `flow.py` must never import telephony,
which is what lets the same logic run over a browser, a softphone and a real
gateway. And everything language-specific lives under `lang/`, so adding a
language is a folder rather than a code change.

## Not built yet

The dialer (retries, calling hours, lead state) and answering machine
detection. Both are outbound-only and neither is exercised by softphone
testing, so expect real work there when Phase 3 starts.
