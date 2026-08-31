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

# Caddy and LiveKit both refuse to start if the cert files named in their
# config do not exist, but the real certificate cannot be issued until Caddy
# is already serving port 80 for the ACME challenge. Break the circle with a
# throwaway self-signed cert; acme.sh overwrites it with the real one below.
if [ ! -s certs/fullchain.pem ]; then
  echo "writing a self-signed bootstrap certificate"
  openssl req -x509 -newkey rsa:2048 -nodes -days 2 \
    -subj "/CN=$PUBLIC_IP" \
    -addext "subjectAltName=IP:$PUBLIC_IP" \
    -keyout certs/key.pem -out certs/fullchain.pem
fi

# LiveKit reads a file, not the environment, so the keys go in here. The
# rendered file holds a secret and is gitignored.
echo "rendering livekit.rendered.yaml"
LIVEKIT_API_KEY="$LIVEKIT_API_KEY" LIVEKIT_API_SECRET="$LIVEKIT_API_SECRET" PUBLIC_IP="$PUBLIC_IP" \
  envsubst '${LIVEKIT_API_KEY} ${LIVEKIT_API_SECRET} ${PUBLIC_IP}' \
  < livekit.yaml > livekit.rendered.yaml

if [ ! -x "$HOME/.acme.sh/acme.sh" ]; then
  echo "installing acme.sh"
  # No contact email: it would have to be admin@<ip>, and Let's Encrypt
  # rejects a contact whose domain is an IP address ("invalid domain").
  # Let's Encrypt has made the ACME contact optional, so register without one.
  curl -fsS https://get.acme.sh | sh -s -- --no-color
fi

# Register the ACME account once, with no contact email (see above).
"$HOME/.acme.sh/acme.sh" --register-account --server letsencrypt >/dev/null 2>&1 || true

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
  --reloadcmd "docker compose -f $PWD/docker-compose.yml --env-file $PWD/../../.env restart caddy livekit"

echo
echo "done. bring it up with:  PUBLIC_IP=$PUBLIC_IP docker compose up -d"
