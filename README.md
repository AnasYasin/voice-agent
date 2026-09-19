# Voice Agent

An outbound phone agent for Pakistan. It calls a lead, holds a short spoken
conversation in Urdu, English, German or Sindhi, and records the outcome and
transcript. Today it runs in the browser through a demo page. SIP and a real
phone line are next.

## What works

- A live call streams at every stage: the caller's audio reaches the recognizer
  while they speak, the model's reply is spoken a sentence at a time, and
  playback starts on the first chunk. About two seconds from the caller
  stopping to the agent starting, measured from Pakistan.
- Four languages, one folder each under `lang/`. The demo page has a dropdown,
  and a fifth pack adds itself to it.
- Every call is saved: transcript to Postgres, full-text searchable, and a
  stereo recording to S3, caller on the left and agent on the right.
- A demo server with a passcode, a purpose box that becomes the system prompt
  for that call, a call limit with a spoken goodbye, and a page that shows who
  is talking.

## Stack

| Layer | Choice |
|---|---|
| Media | LiveKit, self-hosted |
| Recognizer | ElevenLabs Scribe, realtime, all four languages |
| Voice | Azure for Urdu, English and German. ElevenLabs `eleven_v3` for Sindhi, which Azure cannot speak. Fixed lines cached |
| Model | Claude Sonnet 5 for conversation at low thinking effort, and for slot extraction |
| Turn taking | Silero voice detector at 16 kHz, half a second of silence ends a turn |
| Storage | Postgres for transcripts, S3 for recordings |
| Telephony, next | SIP softphone, then a GSM gateway and SIMs |

## Run it

```bash
conda activate voice-agent
pip install -r requirements.txt
cp .env.example .env               # fill in the keys
docker compose up -d livekit postgres
DEMO_PASSCODE=<a code> python -m voice_agent.main --serve
```

Open <http://localhost:8080>, pick a language, enter the code, talk.
[TESTING.md](TESTING.md) has the tests, the one-shot campaign call, and what to
do when something breaks. [deploy/aws/](deploy/aws/) has the box.

## Tests

```bash
make lint       # ruff check and format check
make test       # offline tests, no keys, no network
make test-live  # real APIs and Postgres, a fraction of a cent
```

## Layout

```
config/defaults.yaml   every tunable, with the reason for its value
src/voice_agent/
  main.py       startup, the only module that reads .env
  demo.py       the demo server
  session.py    one call, transport-agnostic
  transport.py  LiveKit browser transport and the turn loop
  flow.py       the script state machine, and free conversation
  llm.py        Claude: slot extraction and streamed replies
  stt.py        ElevenLabs and Deepgram recognizers
  tts.py        Azure and ElevenLabs voices, with the cache
  audio.py      telephone band filter, recordings
  store.py      Postgres and S3
  language.py   loads one language pack
  lang/ur/ lang/en/ lang/de/ lang/sd/   voice, normaliser, script, persona per language
web/            the demo page
scripts/        diagnostics, e.g. replay a recording through the voice detector
eval/           the Phase 0 accuracy harness
```

Two rules the tests enforce. Nothing language-specific lives outside `lang/`,
so a new language is a folder. Only `main.py` reads the environment, so every
component is handed what it needs.

## Not done

- Phase 0, the accuracy gate. Recognizer accuracy for Urdu and Sindhi is
  unmeasured on real phone audio. It needs 25 to 30 real recordings.
- Every Sindhi line was written without a native speaker and needs one.
- SIP and PSTN transports, lead import and the dialer.
