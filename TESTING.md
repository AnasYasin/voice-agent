# Testing

Every command below was run and verified. `make` is not installed on this
machine, so raw commands are given. `sudo apt install make` if you want the
Makefile shortcuts.

---

## One-time setup

```bash
conda activate voice-agent
cd ~/projects/voice_agents/urdu_voice_agent
pip install -e .
```

The editable install is what makes `python -m voice_agent.main` work from any
directory. Without it you get `ModuleNotFoundError: No module named 'voice_agent'`.

---

## 1. Tests, no talking required

```bash
pytest -m "not live"      # 132 tests, no keys, no network
pytest -m live            # 23 tests, real APIs, costs a fraction of a cent
```

Green means the pipeline is wired correctly. It does not mean Urdu recognition
is good enough to ship, which is what Phase 0 measures.

---

## 2. Hear the voice

```bash
python scripts/roundtrip.py
python scripts/roundtrip.py --provider deepgram
```

Urdu text goes to Azure, gets band-limited to 8 kHz, comes back through STT,
and is scored. Listen to what a caller would actually hear:

```bash
ffplay -autoexit eval/samples/roundtrip/line_01_8k.wav
```

Second run reports every line as `cached`, which proves the TTS cache works.

---

## 3. Talk to it in a browser

Three terminals.

**Terminal 1 — LiveKit**

```bash
docker compose up -d livekit
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:7880   # expect 200
```

**Terminal 2 — the browser client**

```bash
cd ~/projects/voice_agents/urdu_voice_agent
python -m http.server 8080 --directory web
```

**Terminal 3 — the agent**

```bash
conda activate voice-agent
cd ~/projects/voice_agents/urdu_voice_agent
python -m voice_agent.main --name انس --date کل --time چار
```

It prints a **caller token** and then waits.

**Then:** open <http://localhost:8080>, paste the token, click Connect, allow
the microphone. The agent speaks first. Answer in Urdu.

The call ends when the script reaches a terminal state. Terminal 3 prints the
outcome and slots, and the audio for every turn lands in `calls/demo/`.

### What to try

| Say | Expect |
|---|---|
| `جی ہاں` then `جی ہاں ٹھیک ہے` | outcome `done`, both slots true |
| `نہیں` at the first question | outcome `wrong_person` |
| yes, then `نہیں`, then a new time | outcome `done`, `preferred_time` filled |
| mumble three times | outcome `handoff` |
| `haan ji` / `theek hai` | works, Roman Urdu is handled |

---

## When it misbehaves

**It cuts you off, or waits far too long after you stop speaking.**
That is the voice activity detector. `min_silence_duration=0.8` in
`src/voice_agent/transport.py`. Raise it if it interrupts you, lower it if the
pause feels dead.

**It never hears you.** Check the browser actually granted mic access, and that
Terminal 3 logs `waiting for a caller` before you connect. Reconnect if you
started the browser first.

**Playback sounds fast, choppy or robotic.** Frames are streamed as fast as
LiveKit accepts them rather than paced to wall clock. This is the most likely
rough edge. Tell me what it sounds like.

**`cannot reach LiveKit`.** Terminal 1 is not running.

**`ModuleNotFoundError: voice_agent`.** You skipped `pip install -e .`, or the
conda env is not active.

**The agent answers the wrong thing.** That is prompt tuning, deliberately
left until the architecture is finished.

---

## What this does and does not prove

Proves the whole chain works: browser audio in, band-limited to 8 kHz,
transcribed, normalised, slot extracted by Claude, state machine advanced,
Urdu synthesised, played back.

Does not prove Urdu recognition is good enough. A laptop mic in a quiet room
is nothing like a Pakistani caller on a GSM line. That is Phase 0, and it still
needs 25 to 30 real recordings in `eval/samples/raw/`.
