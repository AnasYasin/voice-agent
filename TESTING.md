# Testing

All commands verified. `make` is **not** installed here, so raw commands are
given (`sudo apt install make` for the Makefile shortcuts).

## Setup, once

```bash
conda activate voice-agent
cd ~/projects/voice_agents/urdu_voice_agent
pip install -e .          # else: ModuleNotFoundError: voice_agent
```

## 1. Tests

```bash
docker compose up -d postgres   # the store tests and the agent both need it
pytest -m "not live"            # 228 tests, no keys, no network
pytest -m live                  # 45 tests, real APIs and Postgres, fraction of a cent
S3_BUCKET=<bucket> pytest -m live -k real_upload   # the upload test, against the real bucket
```

One live test runs the whole agent with no microphone: the caller is synthesised
in the male voice, the recognizer hears it, Claude fills the slot, and the call
is saved to Postgres and read back.

```bash
pytest -m live -s -k real_call_lands   # heard: جی ہاں ٹھیک ہے / outcome: done
pytest -m live -s -k real_english      # the same call in English
```

Green means the pipeline is wired right. It does not mean Urdu recognition is
good enough to ship — that is Phase 0.

Three of the live flow tests are red and have nothing to do with any of this.
They were written against the old two-question script, and `verify_identity`
and `wrong_person` no longer exist. They need a decision about where a wrong
number routes now, not a fix.

The live session test prints the number this whole thing is about:

```bash
pytest -m live -s -k streamed_turn     # caller stopped -> first audio: ~2000 ms
```

## 2. Hear the voice

```bash
python scripts/roundtrip.py
python scripts/roundtrip.py --provider deepgram
ffplay -autoexit eval/samples/roundtrip/line_01_8k.wav
```

Urdu → Azure → 8 kHz → STT, scored. Second run reports every line `cached`,
which proves the TTS cache.

## 3. Talk to it

Three terminals.

```bash
# 1
docker compose up -d livekit postgres

# 2
python -m http.server 8081 --directory web

# 3
conda activate voice-agent && cd ~/projects/voice_agents/urdu_voice_agent
python -m voice_agent.main --phone 03001234567 --name انس --date کل --time چار
```

`--phone` is the caller id the call is stored under. Terminal 3 exits with
`cannot reach Postgres` if terminal 1 did not start it. That is deliberate: a
call nobody can look up later did not happen.

Terminal 3 prints a **caller token** and waits. Open
<http://localhost:8081/dev.html>, paste it, Connect, allow the mic.
That page exists for this flow. The demo server in section 4 mints its own. The agent speaks first. Answer in Urdu.

Outcome and slots print in terminal 3. Audio lands under `calls/`: one WAV per
line the agent said, and one stereo `call.wav` for the whole call, caller on
the left and agent on the right. Play it and both sides are where they were.
`transcript.json` sits next to them and is rewritten after every turn, so it is
there even if the process dies mid-call.

The same transcript is in Postgres once the call ends:

```bash
make calls     # the last ten calls
make db        # psql. Then, for one caller:
#   select * from calls where caller_id = '03001234567' order by started_at desc;
#   select turn, heard, said from turns where call_id = '<call_id>' order by turn;
```

The agent should start talking about a second and a half after you stop, and
should stop talking the moment you start. See `STREAMING.md` for where that
time goes.

| Say | Expect |
|---|---|
| `جی ہاں ٹھیک ہے` | `done`, `confirmed` true |
| `نہیں`, then a new time | `done` + `preferred_time` |
| mumble 3x | `handoff` |
| `haan ji` / `theek hai` | works, Roman Urdu handled |
| anything, over the agent | it stops mid-word |

### Which mode you are in

The command above runs the appointment form. There are three modes and the
flag is the only difference. Same two servers in terminals 1 and 2 either way.

| Command | Behaviour |
|---|---|
| `python -m voice_agent.main --name … --date … --time …` | The form. Asks the script, fills slots. An off-script reply gets the question repeated. |
| `python -m voice_agent.main --chat --name … --date … --time …` | The form, but off-script asides get answered in character before it returns to the question. |
| `make talk` | No script and no slots. Just the persona, talking. Hang up to end it. |

The demo page has a language dropdown and each caller picks their own.
`AGENT_LANGUAGE` in `.env` only decides which one starts selected, and on the
box that is `de-DE`. Urdu, English and German speak through Azure. Sindhi
speaks through ElevenLabs, which needs the `text_to_speech` permission on
`ELEVENLABS_API_KEY`. The order on the dropdown is each pack's own `ORDER`.

Talk mode is the one that exercises `lang/<code>/agent.yaml`. If you edited the
persona and the agent still asks about an appointment, you are missing
`--talk`. `--name`, `--date` and `--time` are ignored in talk mode, so
`make talk` needs no arguments.

## 4. The demo server

For a link you send someone. One process answers everyone, mints a token per
visitor, and needs no token pasted anywhere.

```bash
docker compose up -d livekit
DEMO_PASSCODE=<a code> make serve
```

Open <http://localhost:8080>, pick a language, enter the code, allow the mic.
`localhost` counts as a secure origin, so the microphone works without HTTPS
here and will not on a public IP. See `deploy/aws/README.md`.

The language toggle shows one button per folder under `lang/`, fetched from
`/api/languages`. `AGENT_LANGUAGE` only decides which one starts selected.
Each call runs in the language its caller picked, so two visitors can be on
the line in two languages at once.

The purpose box is the system prompt for that one call. Whatever the visitor
writes, the demo appends the house rules: it is a calling agent, it speaks the
chosen language, it keeps replies to a sentence or two, the call has a time
limit, and it ends the call when asked. The purpose leads the prompt and
there is no fixed greeting on such a call: the model writes the opening line
in character. Left empty, the pack's own persona and greeting in
`lang/<code>/agent.yaml` run. The purpose is saved on the call row under
`fields`, so a call in Postgres says what it was for. The orb on the page
follows the audio itself: it grows with the agent's voice and rings with the
caller's.

It refuses to start without `DEMO_PASSCODE`, on purpose. `DEMO_MAX_CALLS`
defaults to 3 and `DEMO_CALL_SECONDS` to 300. Twelve seconds before the limit the
agent says its time is up, in the call's language, and hangs up on that line.
The line is `time_up` in `lang/<code>/agent.yaml`.

```bash
curl localhost:8080/healthz     # {"ok": true, "in_flight": 0, "busy": false}
```

The old paste-a-token client is still there for `make run`, at
`make web` then <http://localhost:8081/dev.html>.

## When it breaks

| Symptom | Cause |
|---|---|
| Cuts you off, or long dead pause | VAD. `vad_silence_seconds` in `config/defaults.yaml` |
| Long pause between sentences | The model is still writing the next one. Expected |
| A click between sentences | The filter state, or a torn frame. Not expected, report it |
| Agent dies the moment you connect | Native segfault, seen once in six runs, cause unknown. Restart and re-paste the token |
| Agent never hears you | An expired token from an earlier run. Each run prints a new one |
| Never hears you | Mic permission, or you connected before terminal 3 said `waiting` |
| Answers two turns, then goes deaf | The voice detector died. `python scripts/replay_vad.py calls/<id>/call.wav` shows where. Fixed once by running the model at 16 kHz |
| `cannot reach LiveKit` | Terminal 1 not running |
| `cannot reach Postgres` | `docker compose up -d postgres`, or `DATABASE_URL` in `.env` is wrong |
| `ModuleNotFoundError` | Skipped `pip install -e .`, or env not active |
| Answers the wrong thing | Prompt tuning, deliberately deferred |

## What this proves

The chain works, and it pipelines: browser audio streams to the recognizer as
you speak, the model is read as it writes, and the voice plays as it
synthesises. Nothing waits for the stage before it to finish.

It does **not** prove Urdu recognition is good enough. A laptop mic in a quiet
room is nothing like a Pakistani caller on a GSM line. That is Phase 0, and it
needs 25-30 real recordings.
