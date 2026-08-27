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
pytest -m "not live"   # 216 tests, no keys, no network
pytest -m live         # 33 tests, real APIs, fraction of a cent
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
docker compose up -d livekit

# 2
python -m http.server 8081 --directory web

# 3
conda activate voice-agent && cd ~/projects/voice_agents/urdu_voice_agent
python -m voice_agent.main --name انس --date کل --time چار
```

Terminal 3 prints a **caller token** and waits. Open
<http://localhost:8081/dev.html>, paste it, Connect, allow the mic.
That page exists for this flow. The demo server in section 4 mints its own. The agent speaks first. Answer in Urdu.

Outcome and slots print in terminal 3. Audio lands under `calls/`: one WAV per
line the agent said, and one `caller.wav` for the whole call, because the
recognizer stays open and the caller's audio never stops to become a file.

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

Talk mode is the one that exercises `lang/ur/agent.yaml`. If you edited the
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

Open <http://localhost:8080>, enter the code, allow the mic. `localhost` counts
as a secure origin, so the microphone works without HTTPS here and will not on
a public IP. See `deploy/aws/README.md`.

It refuses to start without `DEMO_PASSCODE`, on purpose. `DEMO_MAX_CALLS`
defaults to 3 and `DEMO_CALL_SECONDS` to 300.

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
| `cannot reach LiveKit` | Terminal 1 not running |
| `ModuleNotFoundError` | Skipped `pip install -e .`, or env not active |
| Answers the wrong thing | Prompt tuning, deliberately deferred |

## What this proves

The chain works, and it pipelines: browser audio streams to the recognizer as
you speak, the model is read as it writes, and the voice plays as it
synthesises. Nothing waits for the stage before it to finish.

It does **not** prove Urdu recognition is good enough. A laptop mic in a quiet
room is nothing like a Pakistani caller on a GSM line. That is Phase 0, and it
needs 25-30 real recordings.
