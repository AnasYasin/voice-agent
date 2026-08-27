# AWS deployment

One EC2 box, one link, one passcode. No domain.

The agent runs as a long-lived server rather than the one-shot `make run`, so
one process serves everyone who opens the link. See `src/voice_agent/demo.py`.

## Why there is no domain here

The microphone is the whole reason HTTPS is needed. Browsers only allow
`getUserMedia` on a secure origin, and `http://<ip>` is not one.

Since 15 January 2026 Let's Encrypt issues certificates for IP addresses, so
the Elastic IP is enough. Three things come with that:

- Only the `shortlived` profile issues them. No other profile will.
- They last 160 hours, so renewal is automatic or it is broken. `setup.sh`
  renews every 3 days.
- Five certificates per identical IP per 168 hours. Do not loop `setup.sh`.

The free EC2 hostname does **not** work. Let's Encrypt forbids issuing for
`*.compute.amazonaws.com` by policy.

## The one thing a domain would buy you

LiveKit's own guide folds TURN/TLS onto 443 next to the web server, multiplexed
by SNI. Clients do not send SNI for a bare IP, so that is not available here and
TURN/TLS sits on 5349 instead.

In practice UDP 3478, UDP 50000-50100 and TCP 7881 cover almost everything,
including mobile data. A network that allows 443 and nothing else will fail to
connect. If you hit that, a domain closes the gap.

## Step 1. The instance

| Setting | Value | Why |
|---|---|---|
| Region | **ap-south-1** (Mumbai) | Closest to Pakistani callers, about 40 ms. Also next door to `centralindia`, where the Azure voice is |
| Type | **t3.medium** | 2 vCPU, 4 GiB. See the sizing note below |
| AMI | **Ubuntu 24.04 LTS, x86_64** | Check the architecture selector. Arm builds exist for every dependency, but nothing has been tested on one |
| Volume | **30 GiB gp3** | The default 8 GiB will not fit. See below |
| Credit specification | **Unlimited** | The default. `Standard` throttles to 0.4 vCPU once credits run out, which sounds like the agent going slow |
| IP | **Elastic IP, allocated and attached** | Not optional. The certificate is issued to the address and dies with it |

**Why t3.medium.** One call was measured at **0.105 vCPU**, because the agent
spends nearly all its time waiting on Azure, ElevenLabs and Anthropic. Call it
0.2 with WebRTC and LiveKit's relay on top. Against t3.medium's 0.4 vCPU
baseline that is two calls free, five or six comfortable, ten at the 2 vCPU
ceiling. Concurrency is what costs, not call length: one long call sits below
baseline and spends nothing however long it runs.

**Why 30 GiB.** The agent image is 1.58 GB built. Add the LiveKit and Caddy
images, Docker's own overhead, the Ubuntu base, and call recordings that grow
with every demo. 8 GiB fills during the first build.

Security group inbound:

| Port | Protocol | Source | Why |
|---|---|---|---|
| 22 | TCP | **My IP** | you |
| 80 | TCP | Anywhere | certificate issuance only |
| 443 | TCP | Anywhere | the page and the signalling socket |
| 7881 | TCP | Anywhere | WebRTC over TCP, when UDP is blocked |
| 3478 | UDP | Anywhere | TURN |
| 5349 | TCP | Anywhere | TURN over TLS |
| 50000-50100 | UDP | Anywhere | media |

Outbound: leave it open. The agent calls Azure, ElevenLabs and Anthropic.

## Step 2. The box

```bash
ssh -i <your-key.pem> ubuntu@<elastic-ip>

sudo apt update
sudo apt install -y docker.io docker-compose-v2 git gettext-base
sudo usermod -aG docker $USER && newgrp docker
```

## Step 3. The code and the keys

```bash
git clone <your repo> && cd urdu_voice_agent
cp .env.example .env && nano .env
```

`.env` needs the API keys plus these. **Generate real LiveKit keys.** The
compose file in the repo root ships `devsecret_change_me_in_production`, and
that must not reach a public box.

```
LIVEKIT_API_KEY=<openssl rand -hex 8>
LIVEKIT_API_SECRET=<openssl rand -hex 32>
DEMO_PASSCODE=<what you give people>
AZURE_SPEECH_REGION=centralindia
```

Then:

```bash
cd deploy/aws
export PUBLIC_IP=<your elastic ip>

docker compose up -d caddy     # port 80, so acme.sh can be answered
./setup.sh                     # renders the config, gets the certificate
docker compose up -d           # everything
```

Check it:

```bash
curl -k https://$PUBLIC_IP/healthz     # {"ok": true, ...}
docker compose logs -f demo
```

Then open `https://<elastic ip>` and enter the passcode.

## Running it

```bash
docker compose logs -f demo        # what callers are saying
docker compose restart demo        # after a code change
docker compose down                # stop everything
```

Recordings and transcripts land in `deploy/aws/calls/`, the same layout as a
local run, so you can hear what people actually said to it.

## Limits

Set in `.env`, read by `demo.py`:

| Variable | Default | Why |
|---|---|---|
| `DEMO_MAX_CALLS` | 3 | above this, callers are told it is busy |
| `DEMO_CALL_SECONDS` | 300 | a demo caller closes the tab instead of hanging up |

The passcode stops strangers. These two stop one person with the code leaving a
tab open overnight on three metered APIs.

## Known

`STREAMING.md` records an intermittent segfault, once in six calls, in native
code as a caller joins. It is not fixed. `restart: unless-stopped` on the demo
service is what stops it taking the link down with it, and a caller who hits it
will need to press connect again.
