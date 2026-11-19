"""The backend container has a memory limit and no swap beyond it.

Until uploads stream, each handler holds a whole body, and an upload storm
on a host with no limit leaves the kernel to choose what to kill, which
could be Postgres (docs/roadmap.md, "Every upload handler reads the whole
file into memory"). The limit makes the kill the backend's own.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

COMPOSE = Path(__file__).resolve().parents[2] / "docker-compose.yml"


@pytest.fixture
def backend() -> dict:
    if not COMPOSE.exists():
        pytest.skip("docker-compose.yml is outside this tree (the backend image); CI runs this")
    services = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]
    assert "backend" in services, "fixture must find the backend service"
    return services["backend"]


def test_backend_has_a_memory_limit(backend):
    assert backend.get("mem_limit") == "${WINGRC_BACKEND_MEMORY:-1g}"


def test_backend_cannot_swap_past_it(backend):
    # Equal to the limit: no swap for the container, so it is killed
    # instead of thrashing the host's swap file under the database.
    assert backend.get("memswap_limit") == backend.get("mem_limit")
