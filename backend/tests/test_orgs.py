"""Integration tests for POST /orgs and GET /orgs.

Uses FastAPI's TestClient with a dependency override so every request runs
inside the same savepoint-wrapped session as the fixture — all writes roll
back after each test.

Org names are randomised per-run so they never clash with rows that already
exist in the shared development database.

Run in-container:
    docker compose exec backend pytest tests/test_orgs.py -m integration -v
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.db import get_session
from app.main import app


@pytest.fixture
def client(db_session):
    app.dependency_overrides[get_session] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def org_name():
    """Return a name that is unique per test run."""
    return f"TestOrg-{uuid.uuid4().hex}"


# ---------------------------------------------------------------------------
# POST /orgs
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_create_org_returns_201_with_id(client, org_name):
    r = client.post("/orgs", json={"name": org_name})
    assert r.status_code == 201
    data = r.json()
    assert data["name"] == org_name
    assert "id" in data
    assert "created_at" in data


@pytest.mark.integration
def test_create_org_id_is_uuid(client, org_name):
    r = client.post("/orgs", json={"name": org_name})
    assert r.status_code == 201
    uuid.UUID(r.json()["id"])  # raises ValueError if not a valid UUID


@pytest.mark.integration
def test_create_org_duplicate_returns_409(client, org_name):
    client.post("/orgs", json={"name": org_name})
    r = client.post("/orgs", json={"name": org_name})
    assert r.status_code == 409


# ---------------------------------------------------------------------------
# GET /orgs
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_list_orgs_returns_200_and_list(client):
    r = client.get("/orgs")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


@pytest.mark.integration
def test_list_orgs_returns_created_orgs(client):
    prefix = uuid.uuid4().hex
    name_a = f"Alpha-{prefix}"
    name_b = f"Beta-{prefix}"
    client.post("/orgs", json={"name": name_a})
    client.post("/orgs", json={"name": name_b})
    names = [o["name"] for o in client.get("/orgs").json()]
    assert name_a in names
    assert name_b in names


@pytest.mark.integration
def test_list_orgs_ordered_by_name(client):
    prefix = uuid.uuid4().hex
    client.post("/orgs", json={"name": f"Zulu-{prefix}"})
    client.post("/orgs", json={"name": f"Alpha-{prefix}"})
    client.post("/orgs", json={"name": f"Mike-{prefix}"})
    names = [o["name"] for o in client.get("/orgs").json() if o["name"].endswith(f"-{prefix}")]
    assert names == sorted(names)


# ---------------------------------------------------------------------------
# Cross-endpoint: id from POST usable in scope endpoint
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_new_org_id_accepted_by_scope_endpoint(client, org_name):
    r = client.post("/orgs", json={"name": org_name})
    org_id = r.json()["id"]
    r2 = client.get(f"/orgs/{org_id}/scope")
    assert r2.status_code == 200
    assert r2.json() == []  # new org has empty scope
