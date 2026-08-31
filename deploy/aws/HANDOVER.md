# Handover: the demo on AWS

Updated 31 Aug 2026. The demo is **up and serving**:

    https://13.203.35.195      passcode: change-me

Read `README.md` next to this file for the full deployment guide. This file is
the current state and the five bugs that had to be fixed to get it running.

## What this is

A shared link that lets someone talk to the Urdu voice agent in their browser.
One passcode, one process, a call per visitor.

`main.py` runs a single call and exits, which suits a campaign dialling a list
and does not suit a URL someone opens tomorrow. `demo.py` is the server that
stays up, mints a token per visitor and runs a session for each of them.
`main.py --serve` builds it, so `demo.py` itself reads no environment.

## The box

| | |
|---|---|
| Elastic IP | `13.203.35.195` |
| Region | ap-south-1, Mumbai |
| Type | t3.medium, 2 vCPU, 4 GiB, x86_64 |
| Volume | 30 GiB gp3 |
| Repo | `~/voice-agent`, branch `dev` |
| Deploy dir | `~/voice-agent/deploy/aws` |
| AWS account | not the one the local `aws` CLI is configured for |

Secrets live in `~/voice-agent/.env` and are not in git. `.env.example` lists
every variable. `PUBLIC_IP=13.203.35.195` is now in `.env` too (see bug 2).

## Current state

All three containers run under `docker compose` in `deploy/aws`:

| Service | What it does |
|---|---|
| `caddy` | TLS on 443, redirect on 80, `/rtc*` and `/validate*` to LiveKit, everything else to the demo app |
| `livekit` | media + signalling, embedded TURN on 3478/UDP and 5349/TLS |
| `demo` | the agent, `main.py --serve`, on 8080 |

Verified from the public internet:

- `https://13.203.35.195/` serves the passcode page with a valid Let's Encrypt
  cert (no `-k` needed).
- `https://13.203.35.195/healthz` returns `{"ok": true, ...}`.
- Wrong passcode returns 403, `change-me` returns a room URL and a LiveKit
  token, and the agent joins the room and waits for the caller.
- `/rtc/validate` reaches LiveKit (401 without a token, which is correct).
- Port 80 answers from outside; the ACME HTTP-01 challenge succeeded, which
  proves it. The security group is fine.

Not yet exercised: a real browser call end to end (mic in, voice back). The
signalling and token path are confirmed; the media path is not.

## The certificate

Issued by Let's Encrypt for the IP under the `shortlived` profile. **It lasts
about 7 days.** `acme.sh` is installed with a cron job (`crontab -l`) that
renews every 3 days and runs the reload command itself. If the cert is not
renewing, that cron job is where to look. `acme.sh --list` shows the state.

The ACME account has no contact email. It cannot have one: the only address
available is `admin@<ip>` and Let's Encrypt rejects a contact whose domain is
an IP. Registering without a contact is allowed.

## The five bugs that were in the way, now fixed

### 1. Caddy could not start before the certificate existed  *(fixed)*

`Caddyfile` names `/certs/fullchain.pem`, which does not exist until the cert
is issued, which needs Caddy already serving port 80. Circular. `setup.sh` now
writes a throwaway self-signed cert into `./certs` first; `acme.sh` overwrites
it with the real one.

### 2. Compose did not read the root `.env`  *(fixed)*

Compose interpolates `${...}` from its own directory's `.env`, and this repo
keeps `.env` two levels up. The fix, now applied: `PUBLIC_IP` is in `.env`, and
**every compose command needs `--env-file ../../.env`**:

```bash
docker compose --env-file ../../.env <cmd>
```

`setup.sh`'s cert-reload command carries the same flag.

### 3. Caddy pointed at Docker service names under host networking  *(fixed)*

`reverse_proxy livekit:7880` / `demo:8080` cannot resolve when every service
runs `network_mode: host` — there is no Docker DNS. Changed to `127.0.0.1`.

### 4. `setup.sh` gave acme.sh an `admin@<ip>` email  *(fixed)*

Let's Encrypt rejected the account registration outright. `setup.sh` now
installs acme.sh with no email and registers the account without a contact.

### 5. LiveKit refused to start: "TURN domain required"  *(fixed)*

TURN was enabled with no `turn.domain`. LiveKit requires one even on a bare IP.
`livekit.yaml` now sets `domain: ${PUBLIC_IP}`, rendered by `setup.sh` (which
now passes `${PUBLIC_IP}` to `envsubst`). It is only the TURN realm string; the
IP cert covers it and clients send no SNI for an address anyway.

## Bring-up from scratch (if you ever rebuild the box)

```bash
cd ~/voice-agent/deploy/aws
export PUBLIC_IP=13.203.35.195

docker compose --env-file ../../.env up -d caddy   # port 80 for the challenge
./setup.sh                                         # cert + render livekit config
docker compose --env-file ../../.env up -d --build # build the image, start all

curl -k https://$PUBLIC_IP/healthz
docker compose --env-file ../../.env logs -f demo
```

The image build is ~3 minutes on this box.

## Running it

```bash
cd ~/voice-agent/deploy/aws
docker compose --env-file ../../.env logs -f demo     # what callers are saying
docker compose --env-file ../../.env restart demo     # after a code change
docker compose --env-file ../../.env down             # stop everything
```

Recordings and transcripts land in `deploy/aws/calls/`.

## Things that will show up in a demo

**`DEMO_PASSCODE` is `change-me`.** Deliberately kept. It is the only thing
between strangers and three metered APIs. Change it in `.env` and
`docker compose --env-file ../../.env up -d demo` to pick it up.

**`vad_silence_seconds` is 0.25**, in `config/defaults.yaml`. Three real calls
showed it cutting callers off mid-word. 0.4 is the safer value and is one line.
Left at 0.25 deliberately.

**An intermittent segfault**, recorded in `STREAMING.md`. The agent died once
in six calls, in native code, as the caller joined. `restart: unless-stopped`
stops it taking the link down, but that caller has to press connect again.

**A network allowing only port 443 will fail.** TURN over TLS sits on 5349, not
443, because sharing 443 needs SNI and browsers do not send SNI for a bare IP.
Home wifi and mobile data are fine. Some corporate and hotel wifi is not. A
domain would close this.

## Tested, and not

Verified on a laptop against real APIs:

- 216 offline tests pass. 30 live tests pass.
- The demo server end to end: wrong passcode gives 403, right one returns a
  token, the agent joins LiveKit, the cap returns 429, the deadline frees the
  slot, and it refuses to start with no `DEMO_PASSCODE`.
- The Docker image builds and contains ffmpeg.

Verified on the box: everything under "Current state" above. Still not
exercised on the box: a full browser call with audio both ways.

## Three live tests still fail

`test_a_real_confirmation_call_end_to_end`, `..._reschedule_...` and
`..._wrong_number_...` in `tests/test_flow.py`. They predate the streaming work
and were written against the old two-question script. They need a routing
decision, not a fix. Unrelated to deployment.

## Where the current latency comes from

Measured from Pakistan, per turn, after the region and model changes:

| Stage | Time |
|---|---|
| VAD silence | 250 ms |
| STT commit | 380-490 ms |
| LLM first sentence | 790-1160 ms |
| TTS first chunk | 590-1100 ms |
| **Total** | **~2.1 s** |

It was 4.8 s. Two config changes fixed it: `AZURE_SPEECH_REGION` from `eastus`
to `centralindia` (~500 ms), and `chat_model` from `claude-sonnet-5` to
`claude-haiku-4-5` (~1.4 s).

`STREAMING.md` still quotes 1830 ms, measured from Germany. Check where a number
was measured before treating it as a regression.
