"""The backend and worker containers have memory limits and no swap beyond them.

Until uploads stream, each handler holds a whole body, and an upload storm
on a host with no limit leaves the kernel to choose what to kill, which
could be Postgres (docs/roadmap.md, "Every upload handler reads the whole
file into memory"). The limit makes the kill the container's own. The
worker runs the same image, and a path nobody has measured there (the
Liongard sync on a large client) gets the same guard.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

COMPOSE = Path(__file__).resolve().parents[2] / "docker-compose.yml"

# Written out: the service and the limit it must carry.
LIMITS = {
    "backend": "${WINGRC_BACKEND_MEMORY:-1536m}",
    "worker": "${WINGRC_WORKER_MEMORY:-1g}",
}


@pytest.fixture
def services() -> dict:
    if not COMPOSE.exists():
        pytest.skip("docker-compose.yml is outside this tree (the backend image); CI runs this")
    found = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]
    assert set(LIMITS) <= set(found), "fixture must find the backend and worker services"
    return found


@pytest.mark.parametrize("name", sorted(LIMITS))
def test_has_a_memory_limit(services, name):
    assert services[name].get("mem_limit") == LIMITS[name]


@pytest.mark.parametrize("name", sorted(LIMITS))
def test_cannot_swap_past_it(services, name):
    # Equal to the limit: no swap for the container, so it is killed
    # instead of thrashing the host's swap file under the database.
    assert services[name].get("memswap_limit") == services[name].get("mem_limit")
