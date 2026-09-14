# One-off load-test script for the evidence-download-hardening slice
# (docs/roadmap.md, 2026-09-14): routers/evidence.py:download_evidence now
# streams bytes through this process instead of redirecting to a presigned
# MinIO URL. That moves file transfer onto the same anyio worker threadpool
# every other sync endpoint in this app already shares (config.py's own
# docstring: 40 threads, matched to db_pool_size+db_max_overflow) -- worth
# measuring against a real running stack, not assuming, per this task's
# own explicit requirement. Same methodology as the get_current_user
# threadpool-blocking benchmark recorded in docs/roadmap.md: stdlib
# ThreadPoolExecutor client (socket I/O releases the GIL), real uvicorn,
# real Postgres, concurrency 1/10/50, with a concurrent /health watcher to
# catch event-loop starvation, p50/p95/p99 + error rate reported per level.
#
# Run from anywhere with network access to the target stack (the host, not
# necessarily inside the backend container -- this is plain stdlib, no app
# imports, no extra deps):
#
#   python3 scripts/one-off/bench_evidence_download_20260914.py \
#       --base-url http://localhost:8000 \
#       --token wingrc_<a real msp_admin API token, minted via the CLI or UI> \
#       --file-size-mb 5
#
# What it does, in order:
#   1. GET /frameworks -- picks the first framework.
#   2. POST /orgs -- creates a throwaway org ("bench-evidence-download-<ts>").
#   3. POST /orgs/{org_id}/assessments -- creates a throwaway assessment.
#   4. GET .../control-states -- picks the first control_state row.
#   5. POST .../evidence -- uploads one synthetic PDF of --file-size-mb.
#   6. Benchmarks GET /orgs/{org_id}/evidence/{evidence_id}/download at
#      concurrency 1, 10, 50 (--requests-per-level requests each), with a
#      background thread polling /health throughout every level.
#
# Nothing here deletes the throwaway org afterward -- see
# scripts/one-off/cleanup_test_orgs.sql for the pattern to reuse if this
# needs cleaning up later; left alone deliberately so the run is inspectable
# afterward (org name is timestamped and self-describing either way).
#
# Record the resulting table in docs/roadmap.md's evidence-download-
# hardening entry, replacing this comment: **NOT YET RUN** -- no session
# with SSH/network access to a live WinGRC stack was available when this
# slice was implemented; this script is ready to run but its numbers are
# not yet in the roadmap writeup. Do not treat the slice as fully verified
# per its own §4 requirement until this has actually been run and recorded.

from __future__ import annotations

import argparse
import json
import statistics
import threading
import time
import urllib.error
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor


def _req(
    method: str,
    url: str,
    token: str,
    *,
    json_body: dict | None = None,
    multipart: tuple[str, bytes, str] | None = None,
    timeout: float = 60.0,
) -> tuple[int, bytes, dict]:
    headers = {"Authorization": f"Bearer {token}"}
    data: bytes | None = None
    if json_body is not None:
        data = json.dumps(json_body).encode()
        headers["Content-Type"] = "application/json"
    elif multipart is not None:
        filename, content, content_type = multipart
        boundary = f"----wingrcbench{uuid.uuid4().hex}"
        parts = [
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'.encode(),
            f"Content-Type: {content_type}\r\n\r\n".encode(),
            content,
            f"\r\n--{boundary}--\r\n".encode(),
        ]
        data = b"".join(parts)
        headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
            return resp.status, body, dict(resp.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers or {})


def setup(base_url: str, token: str, file_size_mb: int) -> tuple[str, str]:
    """Returns (org_id, evidence_id) for a freshly-created throwaway org."""
    status, body, _ = _req("GET", f"{base_url}/frameworks", token)
    assert status == 200, f"GET /frameworks failed: {status} {body!r}"
    frameworks = json.loads(body)
    assert frameworks, "No frameworks seeded -- run `wingrc seed-catalog` first."
    framework_id = frameworks[0]["id"]

    org_name = f"bench-evidence-download-{int(time.time())}"
    status, body, _ = _req("POST", f"{base_url}/orgs", token, json_body={"name": org_name})
    assert status == 201, f"POST /orgs failed: {status} {body!r}"
    org_id = json.loads(body)["id"]
    print(f"Created org {org_id} ({org_name})")

    status, body, _ = _req(
        "POST",
        f"{base_url}/orgs/{org_id}/assessments",
        token,
        json_body={"framework_id": framework_id, "name": "Bench Assessment"},
    )
    assert status == 201, f"POST .../assessments failed: {status} {body!r}"
    assessment_id = json.loads(body)["id"]

    status, body, _ = _req(
        "GET", f"{base_url}/orgs/{org_id}/assessments/{assessment_id}/control-states", token
    )
    assert status == 200, f"GET .../control-states failed: {status} {body!r}"
    control_states = json.loads(body)
    assert control_states, "Assessment has no control_state rows -- unexpected."
    cs_id = control_states[0]["id"]

    payload = b"%PDF-1.4 bench file\n" + b"\x00" * (file_size_mb * 1024 * 1024)
    status, body, _ = _req(
        "POST",
        f"{base_url}/orgs/{org_id}/assessments/{assessment_id}/control-states/{cs_id}/evidence",
        token,
        multipart=("bench.pdf", payload, "application/pdf"),
    )
    assert status == 201, f"POST .../evidence failed: {status} {body!r}"
    evidence_id = json.loads(body)["id"]
    print(f"Uploaded {file_size_mb} MB evidence file: {evidence_id}")

    return org_id, evidence_id


def _percentile(values: list[float], pct: float) -> float:
    values = sorted(values)
    k = (len(values) - 1) * (pct / 100)
    f, c = int(k), min(int(k) + 1, len(values) - 1)
    if f == c:
        return values[f]
    frac = k - f
    return values[f] + frac * (values[c] - values[f])


def bench_one_download(base_url: str, token: str, org_id: str, evidence_id: str) -> tuple[float, bool]:
    start = time.monotonic()
    status, _, _ = _req(
        "GET", f"{base_url}/orgs/{org_id}/evidence/{evidence_id}/download", token, timeout=120.0
    )
    elapsed = time.monotonic() - start
    return elapsed, status == 200


def watch_health(base_url: str, stop: threading.Event, failures: list[str]) -> None:
    while not stop.is_set():
        try:
            status, _, _ = _req("GET", f"{base_url}/health", token="")
            if status != 200:
                failures.append(f"/health returned {status}")
        except Exception as e:  # noqa: BLE001 -- any failure here is the signal
            failures.append(f"/health raised {e!r}")
        time.sleep(0.5)


def run_level(
    base_url: str, token: str, org_id: str, evidence_id: str, concurrency: int, n_requests: int
) -> None:
    stop = threading.Event()
    health_failures: list[str] = []
    watcher = threading.Thread(target=watch_health, args=(base_url, stop, health_failures), daemon=True)
    watcher.start()

    latencies: list[float] = []
    errors = 0
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = [
            pool.submit(bench_one_download, base_url, token, org_id, evidence_id)
            for _ in range(n_requests)
        ]
        for fut in futures:
            elapsed, ok = fut.result()
            latencies.append(elapsed * 1000)  # ms
            if not ok:
                errors += 1

    stop.set()
    watcher.join(timeout=2)

    p50 = _percentile(latencies, 50)
    p95 = _percentile(latencies, 95)
    p99 = _percentile(latencies, 99)
    print(
        f"concurrency={concurrency:>3}  n={n_requests:>4}  "
        f"p50={p50:7.1f}ms  p95={p95:7.1f}ms  p99={p99:7.1f}ms  "
        f"errors={errors}  mean={statistics.mean(latencies):7.1f}ms  "
        f"health_failures={len(health_failures)}"
    )
    if health_failures:
        print(f"  !! /health degraded during this level: {health_failures[:5]}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base-url", default="http://localhost:8000")
    ap.add_argument("--token", required=True, help="wingrc_<...> API token, msp_admin role")
    ap.add_argument("--file-size-mb", type=int, default=5)
    ap.add_argument("--requests-per-level", type=int, default=150)
    ap.add_argument(
        "--levels", type=int, nargs="+", default=[1, 10, 50], help="Concurrency levels to test"
    )
    args = ap.parse_args()

    org_id, evidence_id = setup(args.base_url, args.token, args.file_size_mb)

    print(f"\nBenchmarking GET .../evidence/{evidence_id}/download ({args.file_size_mb} MB file):")
    for level in args.levels:
        run_level(args.base_url, args.token, org_id, evidence_id, level, args.requests_per_level)


if __name__ == "__main__":
    main()
