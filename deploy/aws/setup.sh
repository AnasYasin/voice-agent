#!/usr/bin/env bash
# First-time setup on the EC2 box, and safe to re-run.
#
#   PUBLIC_IP=<elastic ip> ./setup.sh
#
# Two jobs: put the API keys into the LiveKit config, and get the certificate.
set -euo pipefail

cd "$(dirname "$0")"

: "${PUBLIC_IP:?set PUBLIC_IP to the Elastic IP}"
[ -f ../../.env ] || { echo "no .env at repo root"; exit 1; }
set -a && . ../../.env && set +a
: "${LIVEKIT_API_KEY:?missing from .env}"
: "${LIVEKIT_API_SECRET:?missing from .env}"

mkdir -p certs acme-webroot calls

# LiveKit reads a file, not the environment, so the keys go in here. The
# rendered file holds a secret and is gitignored.
echo "rendering livekit.rendered.yaml"
LIVEKIT_API_KEY="$LIVEKIT_API_KEY" LIVEKIT_API_SECRET="$LIVEKIT_API_SECRET" \
  envsubst '${LIVEKIT_API_KEY} ${LIVEKIT_API_SECRET}' \
  < livekit.yaml > livekit.rendered.yaml

if [ ! -x "$HOME/.acme.sh/acme.sh" ]; then
  echo "installing acme.sh"
  curl -fsS https://get.acme.sh | sh -s email="admin@$PUBLIC_IP"
fi

# Let's Encrypt only issues certificates for an IP under the shortlived
# profile, and those last 160 hours. --days 3 renews well inside that, leaving
# room for one failed attempt before anything expires.
#
# The webroot is served by Caddy on port 80, so Caddy has to be up for this.
echo "requesting a certificate for $PUBLIC_IP"
"$HOME/.acme.sh/acme.sh" --issue \
  --server letsencrypt \
  --cert-profile shortlived \
  --days 3 \
  -d "$PUBLIC_IP" \
  --webroot "$PWD/acme-webroot"

"$HOME/.acme.sh/acme.sh" --install-cert -d "$PUBLIC_IP" \
  --fullchain-file "$PWD/certs/fullchain.pem" \
  --key-file "$PWD/certs/key.pem" \
  --reloadcmd "docker compose -f $PWD/docker-compose.yml restart caddy livekit"

echo
echo "done. bring it up with:  PUBLIC_IP=$PUBLIC_IP docker compose up -d"
