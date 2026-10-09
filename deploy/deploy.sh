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

# A record per deploy, next to nothing else: what was deployed, from which
# images, and a vulnerability scan of *those* images. CI scans an image it
# builds and discards; this attaches a scan to the artifact actually serving
# requests. Report-only, like CI's own Trivy step -- a scan that cannot run
# is noted in the record, never a reason to undo a healthy deploy.
TRIVY_IMAGE="aquasec/trivy:0.69.3"
record_deploy() {
  local dir
  dir="${WINGRC_DEPLOY_RECORD_DIR:-$HOME/wingrc-deploys}/$(date -u +%Y%m%dT%H%M%SZ)-${sha:0:12}"
  mkdir -p "$dir"
  {
    echo "commit:   $sha"
    echo "deployed: $(date -u +%Y-%m-%dT%H:%M:%SZ) by $(id -un)"
    echo "health:   $1"
    for svc in backend worker nginx; do
      echo "image:    $svc $(docker compose images -q "$svc" 2>/dev/null | head -1)"
    done
  } > "$dir/deploy.txt"
  local project
  project="$(docker compose config --format json 2>/dev/null \
    | python3 -c 'import json,sys; print(json.load(sys.stdin)["name"])' 2>/dev/null || echo wingrc)"
  for svc in backend nginx; do
    if docker run --rm -v /var/run/docker.sock:/var/run/docker.sock -v trivy-cache:/root/.cache/ \
         "$TRIVY_IMAGE" image --quiet --scanners vuln --format table "${project}-${svc}:latest" \
         > "$dir/trivy-$svc.txt" 2>&1; then
      echo "scan $svc: $(grep -c 'CVE-\|GHSA-' "$dir/trivy-$svc.txt" || true) findings -> $dir/trivy-$svc.txt"
    else
      echo "scan $svc: did not run (see $dir/trivy-$svc.txt); deploy stands" | tee -a "$dir/deploy.txt"
    fi
  done
  echo "deploy record: $dir"
}

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
    record_deploy "$body"
    exit 0
  fi
  sleep 5
done
echo "deploy did not become healthy at $sha within 5 minutes; last /health:" >&2
echo "$body" >&2
exit 1
