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
pytest -m "not live"   # 132 tests, no keys, no network
pytest -m live         # 23 tests, real APIs, fraction of a cent
```

Green means the pipeline is wired right. It does not mean Urdu recognition is
good enough to ship — that is Phase 0.

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
python -m http.server 8080 --directory web

# 3
conda activate voice-agent && cd ~/projects/voice_agents/urdu_voice_agent
python -m voice_agent.main --name انس --date کل --time چار
```

Terminal 3 prints a **caller token** and waits. Open <http://localhost:8080>,
paste it, Connect, allow the mic. The agent speaks first. Answer in Urdu.

Outcome and slots print in terminal 3. Per-turn audio lands in `calls/demo/`.

| Say | Expect |
|---|---|
| `جی ہاں` then `جی ہاں ٹھیک ہے` | `done`, both slots true |
| `نہیں` first question | `wrong_person` |
| yes, `نہیں`, then a new time | `done` + `preferred_time` |
| mumble 3x | `handoff` |
| `haan ji` / `theek hai` | works, Roman Urdu handled |

## When it breaks

| Symptom | Cause |
|---|---|
| Cuts you off, or long dead pause | VAD. `min_silence_duration=0.8` in `transport.py` |
| Playback fast/choppy | Frames stream unpaced, not wall-clock. Known rough edge |
| Never hears you | Mic permission, or you connected before terminal 3 said `waiting` |
| `cannot reach LiveKit` | Terminal 1 not running |
| `ModuleNotFoundError` | Skipped `pip install -e .`, or env not active |
| Answers the wrong thing | Prompt tuning, deliberately deferred |

## What this proves

The chain works: browser audio → 8 kHz → transcribed → normalised → slot
extracted → state machine → Urdu synthesised → played back.

It does **not** prove Urdu recognition is good enough. A laptop mic in a quiet
room is nothing like a Pakistani caller on a GSM line. That is Phase 0, and it
needs 25-30 real recordings.
