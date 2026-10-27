#!/usr/bin/env bash
# Renew the dev.wingrc.us certificate, then reload nginx.
#
#   deploy/tls/renew.sh            # renew if due (within certbot's 30 days)
#   TLS_FORCE=1 deploy/tls/renew.sh  # renew now regardless
#
# Manual DNS-01, by decision: the deployment is LAN-only, so HTTP-01 can
# never validate, and DNSimple's personal plan has only full-account
# tokens, so no DNS credential is kept on this host (see
# docs/wl-util-1-worked-example-deployment.md, "Renewing the certificate").
# The cost is that a person adds two TXT records per renewal. This prints
# them -- they are public by design, not credentials -- waits until every
# authoritative nameserver serves them, and then checks that nginx is
# serving the new certificate. check-expiry.sh is what says it is time.
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
compose_dir="${WINGRC_COMPOSE_DIR:-$here/../..}"
CERTBOT_IMAGE="certbot/certbot:v5.8.0"
host="${TLS_CHECK_HOST:-127.0.0.1}"
names="dev.wingrc.us storage.dev.wingrc.us"

served() {
  echo | openssl s_client -connect "$host:443" -servername "$1" 2>/dev/null \
    | openssl x509 -noout -fingerprint -sha256 2>/dev/null | cut -d= -f2
}

# Under pipefail a failed handshake fails the assignment, and set -e would
# then exit with nothing printed: both openssl calls discard stderr.
before="$(served dev.wingrc.us)" || {
  echo "nothing is served on $host:443 for dev.wingrc.us; this script compares" >&2
  echo "what nginx serves before and after, so nginx must be up first" >&2
  exit 1
}
echo "serving now: $before"

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
touch "$work/records.txt"

docker run --rm \
  -v wingrc_certs:/etc/letsencrypt \
  -v "$work":/tls \
  -v "$here/auth-hook.sh":/hooks/auth-hook.sh:ro \
  -e TLS_WAIT_SECONDS="${TLS_WAIT_SECONDS:-3600}" \
  "$CERTBOT_IMAGE" renew --cert-name dev.wingrc.us --non-interactive \
  --no-random-sleep-on-renew \
  --manual-auth-hook /hooks/auth-hook.sh ${TLS_FORCE:+--force-renewal} \
  > "$work/certbot.log" 2>&1 &
certbot_pid=$!

shown=0
while kill -0 "$certbot_pid" 2>/dev/null; do
  n="$(wc -l < "$work/records.txt")"
  if [ "$n" -gt "$shown" ]; then
    if [ "$shown" -eq 0 ]; then
      echo "Add these TXT records in DNSimple (wingrc.us zone); waiting for them:"
    fi
    sed -n "$((shown + 1)),${n}p" "$work/records.txt" | sed 's/^/  /'
    shown="$n"
  fi
  sleep 2
done
if ! wait "$certbot_pid"; then
  echo "certbot failed:" >&2
  cat "$work/certbot.log" >&2
  exit 1
fi
cat "$work/certbot.log"

# A renewed file nginx never re-reads is the same outage, so reload and
# check what is served, not what is on disk.
docker compose --project-directory "$compose_dir" exec -T nginx nginx -s reload
sleep 2
for n in $names; do
  after="$(served "$n")" || {
    echo "renewed, but nothing is served on $host:443 for $n after the reload" >&2
    exit 1
  }
  echo "serving $n: $after"
  if [ "$after" = "$before" ] && [ "$shown" -gt 0 ]; then
    echo "renewed, but nginx still serves the old certificate for $n" >&2
    exit 1
  fi
done
if [ "$shown" -gt 0 ]; then
  echo "The TXT records can be deleted from DNSimple now."
fi
"$here/check-expiry.sh"
