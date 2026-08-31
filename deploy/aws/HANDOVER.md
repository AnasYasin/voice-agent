# Handover: deploying the demo on AWS

Written 31 Aug 2026, for picking this up on the box itself.

Read `README.md` next to this file for the full deployment guide. This file is
the current state, what is broken, and what has and has not been tested.

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
every variable.

## Where it got to

Done:

- Instance up, security group correct, Elastic IP attached.
- Repo cloned on the box via a read-only GitHub deploy key.
- `.env` filled in.
- `docker compose up -d caddy` ran and the container started.

Stuck here:

- **Port 80 does not answer from outside.** Verified from a machine in
  Pakistan: no response on 80, and no response on the ACME challenge path.
  Caddy reports as started, so it is almost certainly loading its config and
  failing, not failing to launch.

`setup.sh` cannot work until port 80 answers, because that is how Let's
Encrypt proves the address belongs to you.

## Two known bugs, both mine, neither fixed

### 1. Caddy cannot start before the certificate exists

`Caddyfile` has an `https://{$PUBLIC_IP}:443` block pointing at
`/certs/fullchain.pem` and `/certs/key.pem`. Neither file exists until
`setup.sh` has run, and `setup.sh` needs Caddy already serving port 80. Circular.

Confirm with:

```bash
docker compose logs caddy | tail -30
```

An error naming `/certs/fullchain.pem` is this bug.

**The fix**, not yet applied: `setup.sh` should write a throwaway self-signed
certificate into `./certs` before Caddy starts, so the config is valid, then
let acme.sh overwrite it with the real one. Something like:

```bash
mkdir -p certs
openssl req -x509 -newkey rsa:2048 -nodes -days 1 \
  -subj "/CN=$PUBLIC_IP" \
  -keyout certs/key.pem -out certs/fullchain.pem
```

Run that by hand and restart Caddy to get unblocked today.

### 2. Compose does not read the root .env

Docker Compose interpolates `${...}` from its own directory's `.env`, and this
repo keeps `.env` two levels up at the root. The `env_file:` line in
`docker-compose.yml` passes variables into containers, which is a different
thing and does not help interpolation.

Symptom:

```
required variable LIVEKIT_API_KEY is missing a value
```

**Workaround**, needed in every new SSH session:

```bash
cd ~/voice-agent/deploy/aws
set -a && . ../../.env && set +a
export PUBLIC_IP=13.203.35.195
```

**The fix**, not yet applied: use `--env-file ../../.env` on every compose
command, move `PUBLIC_IP` into `.env` so it stops being a separate export, and
correct the README.

## Bring-up, once those are dealt with

```bash
cd ~/voice-agent/deploy/aws
set -a && . ../../.env && set +a
export PUBLIC_IP=13.203.35.195

docker compose up -d caddy     # port 80 must answer before the next line
./setup.sh                     # renders livekit config, gets the certificate
docker compose up -d           # builds the image, starts everything

curl -k https://$PUBLIC_IP/healthz
docker compose logs -f demo
```

Then open `https://13.203.35.195` and enter `DEMO_PASSCODE`.

The image build takes several minutes on a t3.medium. It was 4 minutes on a
laptop.

## Tested, and not

Verified on a laptop against real APIs:

- 216 offline tests pass. 30 live tests pass.
- The demo server end to end: wrong passcode gives 403, right one returns a
  token, the agent joins LiveKit, the cap returns 429, the deadline frees the
  slot, and it refuses to start with no `DEMO_PASSCODE`.
- The Docker image builds and contains ffmpeg. All imports work inside it.

**Never run anywhere:** every file in `deploy/aws`. The compose file, the
Caddyfile, the LiveKit config and `setup.sh` are all untested. Both bugs above
were found in the first ten minutes of trying to use them, so expect more.

## Three live tests still fail

`test_a_real_confirmation_call_end_to_end`, `..._reschedule_...` and
`..._wrong_number_...` in `tests/test_flow.py`. They predate the streaming work
and were written against the old two-question script. Removing `verify_identity`
and `wrong_person` left a wrong number with nowhere to route. They need a
routing decision, not a fix. Unrelated to deployment.

## Things that will show up in a demo

**`vad_silence_seconds` is 0.25**, in `config/defaults.yaml`. Three real calls
showed it cutting callers off mid-word and producing turns where the agent said
nothing. A stranger will read that as broken. Putting it back to 0.4 is one
line and is the highest-value change available. It was left at 0.25 deliberately.

**An intermittent segfault**, recorded in `STREAMING.md`. The agent died once in
six calls, in native code, as the caller joined. `restart: unless-stopped` on
the demo service stops it taking the link down, but that caller has to press
connect again.

**A network allowing only port 443 will fail.** TURN over TLS sits on 5349
rather than 443, because sharing 443 needs SNI and browsers do not send SNI for
a bare IP. Home wifi and mobile data are fine. Some corporate and hotel wifi is
not. A domain would close this.

## Where the current latency comes from

Measured from Pakistan, per turn, after the region and model changes:

| Stage | Time |
|---|---|
| VAD silence | 250 ms |
| STT commit | 380-490 ms |
| LLM first sentence | 790-1160 ms |
| TTS first chunk | 590-1100 ms |
| **Total** | **~2.1 s** |

It was 4.8 s. Two things fixed it, both config. `AZURE_SPEECH_REGION` moved
from `eastus` to `centralindia`, worth about 500 ms. And `chat_model` moved
from `claude-sonnet-5` to `claude-haiku-4-5`, worth about 1.4 s.

`STREAMING.md` still quotes 1830 ms. That was measured from Germany and does not
apply here. Check where a number was measured before treating it as a regression.
