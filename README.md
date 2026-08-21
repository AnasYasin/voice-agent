# Urdu Voice Agent

Outbound agent that calls leads in Pakistan, confirms appointments in Urdu, and
records the structured outcome.

Working end to end: browser → 8 kHz → STT → Claude → state machine → Azure TTS →
browser. See [TESTING.md](TESTING.md) to run it.

## Stack

| Layer | Choice |
|---|---|
| Orchestration | LiveKit, self-hosted |
| Telephony | Asterisk + GSM gateway, licensed SIP trunk later |
| STT | ElevenLabs Scribe, compared against Deepgram Nova-3 |
| TTS | Azure Neural `ur-PK-UzmaNeural`, fixed lines cached |
| LLM | Claude Sonnet 5, slot extraction only |
| Storage | Postgres + S3 (not built yet) |

The binding constraint is a Pakistani local caller ID, not AI quality. Managed
platforms cannot provide one, and calls from outside Pakistan pay international
settlement instead of the Rs 0.30/min domestic rate, which is why they cost
roughly 15x more.

## Phases

| Phase | What | Hardware |
|---|---|---|
| **0** | Measure Urdu STT accuracy on 8 kHz audio | none |
| 1 | Conversation end to end, browser or softphone | none |
| 2 | State machine, DTMF, recordings, results | none |
| 3 | Pilot on real leads | GSM gateway + SIMs |
| 4 | Licensed trunk, dialer, answering machine detection | trunk |

Phases 0 to 2 need no hardware and no purchase decision.

**Phase 0 is the go/no-go gate and has not run.** It needs 25-30 real Urdu
recordings in `eval/samples/raw/` plus `eval/expected.csv`. Every accuracy
figure used in planning is an estimate, including the published 3.1% Urdu WER,
which was measured on studio audio rather than a phone line.

```bash
cp .env.example .env          # keys
pip install -e .
python eval/prepare_audio.py  # band-limit to 8 kHz   (stub)
python eval/run_stt_eval.py   # score both providers  (stub)
```

## Layout

```
config/defaults.yaml   tunable parameters
src/voice_agent/
  config.py     the only module that reads defaults.yaml
  audio.py      telephone-band conversion
  stt.py        elevenlabs + deepgram
  llm.py        claude slot extraction
  tts.py        azure voice + cache
  flow.py       state machine over script.yaml
  session.py    wires one call, transport-agnostic
  transport.py  livekit browser (SIP and PSTN later)
  main.py       process startup, the only module reading env
  lang/ur/      normalisation, keyterms, call script
web/            browser test client
eval/           Phase 0 accuracy harness
telephony/      Asterisk config
```

Two rules the tests enforce. `session.py` never imports telephony, which is
what lets one session serve browser, softphone and a real gateway. Nothing
Urdu-specific lives outside `lang/`, so adding a language is a folder.

## Not built

SIP and PSTN transports, Postgres and S3 stores, answering machine detection,
lead import and the dialer. Prompt tuning, transcripts and appointment handling
are deliberately deferred until the architecture is finished.
