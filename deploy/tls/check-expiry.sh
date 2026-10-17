#!/usr/bin/env bash
# Fail loudly when a certificate nginx serves is inside its last N days.
#
#   deploy/tls/check-expiry.sh [days]    # default 30
#
# Checks what is served, not the files on disk: a renewed file nginx never
# re-read is still an outage. Exit 0 fine, 1 inside the window or expired,
# 2 could not get a certificate at all.
set -uo pipefail

days="${1:-30}"
host="${TLS_CHECK_HOST:-127.0.0.1}"
port="${TLS_CHECK_PORT:-443}"
names="${TLS_CHECK_NAMES:-dev.wingrc.us storage.dev.wingrc.us}"

status=0
for n in $names; do
  pem="$(echo | timeout 10 openssl s_client -connect "$host:$port" -servername "$n" 2>/dev/null \
    | openssl x509 2>/dev/null || true)"
  if [ -z "$pem" ]; then
    echo "TLS CHECK FAILED: no certificate from $host:$port for $n" >&2
    status=2
    continue
  fi
  end="$(openssl x509 -noout -enddate <<<"$pem" | cut -d= -f2)"
  left=$(( ($(date -d "$end" +%s) - $(date +%s)) / 86400 ))
  if openssl x509 -noout -checkend $((days * 86400)) <<<"$pem" >/dev/null; then
    echo "ok: $n, $left days left (notAfter $end)"
  else
    echo "TLS CERTIFICATE EXPIRES IN $left DAYS: $n (notAfter $end). Renew with deploy/tls/renew.sh" >&2
    [ "$status" -eq 0 ] && status=1
  fi
done
exit "$status"
