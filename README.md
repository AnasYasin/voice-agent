# Voice Agent

A browser demo of a voice agent. The main focus is Sindhi. The goal is the
first voice agent that holds a spoken conversation in Sindhi.

You open a page, pick a language and talk. The agent replies in speech. Each
call is saved with its transcript and a recording. Urdu, English and German
work too.

## Sindhi

- STT is Gemini 3.5 Transcribe Live. It can be pinned to the Perso-Arabic
  script, so Sindhi comes back in Sindhi script.
- TTS is ElevenLabs `eleven_v4` with the voice Marvi. Azure has no Sindhi
  voice.
- The pack lives in `lang/sd/`, with its own normaliser, script and persona.

## What works

- The call streams at every stage. The caller's audio reaches the STT while
  they speak. The reply is spoken a sentence at a time, and playback starts on
  the first chunk. The agent starts about two seconds after the caller stops,
  measured from Pakistan.
- Four languages, one folder each under `lang/`. The demo page has a dropdown,
  and a new pack adds itself to it.
- Every call is saved. The transcript goes to Postgres and is full-text
  searchable. A stereo recording goes to S3, caller on the left and agent on
  the right.
- The demo server has a passcode and a purpose box that becomes the system
  prompt for that call. It ends a call at a time limit with a spoken goodbye,
  and the page shows who is talking.

## Stack

| Layer | Choice |
|---|---|
| Media | LiveKit, self-hosted |
| STT | Gemini 3.5 Transcribe Live for Sindhi. ElevenLabs Scribe realtime for Urdu, English and German |
| TTS | ElevenLabs `eleven_v4`, voice Marvi, for Sindhi. Azure for Urdu, English and German. Fixed lines cached |
| LLM | Claude Sonnet 5 at low thinking effort, for the conversation and for slot extraction |
| VAD | Silero at 16 kHz, half a second of silence ends a turn |
| Storage | Postgres for transcripts, S3 for recordings |

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
