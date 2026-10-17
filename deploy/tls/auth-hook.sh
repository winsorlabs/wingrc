#!/bin/sh
# certbot --manual-auth-hook, run inside the certbot container by renew.sh.
# Records each TXT record for a person to add; on the last challenge, waits
# until every authoritative nameserver for the zone serves all of them.
set -eu

echo "_acme-challenge.${CERTBOT_DOMAIN} TXT ${CERTBOT_VALIDATION}" >> /tls/records.txt
[ "${CERTBOT_REMAINING_CHALLENGES:-0}" -gt 0 ] && exit 0

deadline=$(( $(date +%s) + ${TLS_WAIT_SECONDS:-3600} ))
nameservers="$(nslookup -type=NS wingrc.us 1.1.1.1 | sed -n 's/.*nameserver = //p')"
[ -n "$nameservers" ] || { echo "no nameservers found for wingrc.us" >&2; exit 1; }
for ns in $nameservers; do
  while read -r name _ value; do
    until nslookup -type=TXT "$name" "$ns" 2>/dev/null | grep -qF -- "$value"; do
      [ "$(date +%s)" -lt "$deadline" ] || { echo "timed out: $name not on $ns" >&2; exit 1; }
      sleep 15
    done
  done < /tls/records.txt
done
