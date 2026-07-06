"""Integration tests for POST /orgs and GET /orgs.

Uses FastAPI's TestClient with a dependency override so every request runs
inside the same savepoint-wrapped session as the fixture — all writes roll
back after each test.

Run in-container:
    docker compose exec backend pytest tests/test_orgs.py -m integration -v
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.db import get_session
from app.main import app


@pytest.fixture
def client(db_session):
    app.dependency_overrides[get_session] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# POST /orgs
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_create_org_returns_201_with_id(client):
    r = client.post("/orgs", json={"name": "Acme MSP"})
    assert r.status_code == 201
    data = r.json()
    assert data["name"] == "Acme MSP"
    assert "id" in data
    assert "created_at" in data


@pytest.mark.integration
def test_create_org_id_is_uuid(client):
    import uuid

    r = client.post("/orgs", json={"name": "UUIDCheck"})
    assert r.status_code == 201
    uuid.UUID(r.json()["id"])  # raises ValueError if not a valid UUID


@pytest.mark.integration
def test_create_org_duplicate_returns_409(client):
    client.post("/orgs", json={"name": "DupeOrg"})
    r = client.post("/orgs", json={"name": "DupeOrg"})
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
    client.post("/orgs", json={"name": "Beta Corp"})
    client.post("/orgs", json={"name": "Alpha Inc"})
    r = client.get("/orgs")
    assert r.status_code == 200
    names = [o["name"] for o in r.json()]
    assert "Alpha Inc" in names
    assert "Beta Corp" in names


@pytest.mark.integration
def test_list_orgs_ordered_by_name(client):
    client.post("/orgs", json={"name": "Zulu"})
    client.post("/orgs", json={"name": "Alpha"})
    client.post("/orgs", json={"name": "Mike"})
    names = [o["name"] for o in client.get("/orgs").json()]
    assert names == sorted(names)


# ---------------------------------------------------------------------------
# Cross-endpoint: id from POST usable in scope endpoint
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_new_org_id_accepted_by_scope_endpoint(client):
    r = client.post("/orgs", json={"name": "ScopeTestOrg"})
    org_id = r.json()["id"]
    r2 = client.get(f"/orgs/{org_id}/scope")
    assert r2.status_code == 200
    assert r2.json() == []  # new org has empty scope
