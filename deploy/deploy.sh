#!/usr/bin/env bash
# Deploy one exact commit to this host's compose stack, from images.
#
#   deploy/deploy.sh              # origin/main
#   deploy/deploy.sh <commit-sha> # a specific commit
#
# Replaces `git pull && docker compose build && up`. The stack runs the code
# baked into its images (docker-compose.yml no longer bind-mounts the
# checkout), so the checkout is build context only and the deployed commit
# is whatever /health reports as `build`. See docs/deployment.md.
set -euo pipefail

cd "$(dirname "$0")/.."

if [ -e docker-compose.override.yml ]; then
  echo "refusing: docker-compose.override.yml exists -- it is for local development" >&2
  echo "and would put a bind-mounted checkout back under the running backend." >&2
  exit 1
fi
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  echo "refusing: the working tree has uncommitted changes; an image built from it" >&2
  echo "would not correspond to any commit." >&2
  git status --short --untracked-files=no >&2
  exit 1
fi

git fetch --quiet origin
ref="${1:-origin/main}"
sha="$(git rev-parse --verify "${ref}^{commit}")"
git checkout --quiet --detach "$sha"
echo "deploying $sha"

export WINGRC_BUILD_SHA="$sha"
docker compose build backend worker nginx
# --no-deps is not optional: see docs/deployment.md (it once recreated db).
docker compose up -d --no-deps backend worker nginx

echo "waiting for /health to report $sha ..."
for _ in $(seq 60); do
  body="$(docker compose exec -T backend python -c \
    'import urllib.request
try: print(urllib.request.urlopen("http://localhost:8000/health", timeout=3).read().decode())
except Exception as e: print(getattr(e, "read", lambda: str(e).encode())().decode())' 2>/dev/null || true)"
  if printf '%s' "$body" | grep -q '"status":"ok"' \
     && printf '%s' "$body" | grep -q "\"build\":\"$sha\""; then
    echo "$body"
    echo "deployed $sha"
    exit 0
  fi
  sleep 5
done
echo "deploy did not become healthy at $sha within 5 minutes; last /health:" >&2
echo "$body" >&2
exit 1
