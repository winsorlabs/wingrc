"""docker-compose.yml supplies no credential: each is required, by name.

Until 2026-11-17 compose wrote the MinIO keys, the owner's password and
WINGRC_ENVIRONMENT as literals no .env could reach, and gave the Fernet key
a public default (docs/roadmap.md, "The compose file ships default
credentials"). A `:-default` or a literal for any of these is that defect
again, and fails here.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

COMPOSE = Path(__file__).resolve().parents[2] / "docker-compose.yml"

# Written out: where each required name must appear, as a whole value.
REQUIRED = {
    ("x-backend-env", "WINGRC_ENVIRONMENT"): "WINGRC_ENVIRONMENT",
    ("x-backend-env", "WINGRC_STORAGE_ACCESS_KEY"): "WINGRC_STORAGE_ACCESS_KEY",
    ("x-backend-env", "WINGRC_STORAGE_SECRET_KEY"): "WINGRC_STORAGE_SECRET_KEY",
    ("x-backend-env", "WINGRC_AUTH_FLOW_SECRET"): "WINGRC_AUTH_FLOW_SECRET",
    ("x-backend-env", "WINGRC_CREDENTIAL_ENCRYPTION_KEYS"): "WINGRC_CREDENTIAL_ENCRYPTION_KEYS",
    ("db", "POSTGRES_PASSWORD"): "POSTGRES_PASSWORD",
    ("minio", "MINIO_ROOT_USER"): "MINIO_ROOT_USER",
    ("minio", "MINIO_ROOT_PASSWORD"): "MINIO_ROOT_PASSWORD",
}


@pytest.fixture
def compose() -> tuple[dict, str]:
    if not COMPOSE.exists():
        pytest.skip("docker-compose.yml is outside this tree (the backend image); CI runs this")
    text = COMPOSE.read_text(encoding="utf-8")
    return yaml.safe_load(text), text


def _env(doc: dict, where: str) -> dict:
    if where == "x-backend-env":
        return doc["x-backend-env"]
    return doc["services"][where]["environment"]


@pytest.mark.parametrize(("where", "key"), sorted(REQUIRED))
def test_each_is_required_with_no_default(compose, where, key):
    doc, _ = compose
    name = REQUIRED[(where, key)]
    value = _env(doc, where).get(key)
    assert value is not None, f"{where} does not set {key}"
    pattern = r"\$\{" + name + r":\?" + name + r" is required[^}]*\}"
    assert re.fullmatch(pattern, value), value


def test_the_database_url_carries_no_password_literal(compose):
    doc, _ = compose
    url = doc["x-backend-env"]["WINGRC_DATABASE_URL"]
    assert "${WINGRC_APP_DB_PASSWORD:?WINGRC_APP_DB_PASSWORD is required" in url, url
    assert "wingrc:wingrc@" not in url, url


def test_requests_are_served_as_the_app_role_by_default(compose):
    """Until 2026-11-23 the default was the owner, a superuser, for whom
    Postgres skips every policy (docs/roadmap.md, "A fresh install runs the
    app as a superuser"). The default must name wingrc_app, and only it."""
    doc, _ = compose
    url = doc["x-backend-env"]["WINGRC_DATABASE_URL"]
    default = re.fullmatch(r"\$\{WINGRC_DATABASE_URL:-(.*)\}", url, re.S)
    assert default, url
    assert re.match(r"postgresql\+psycopg://wingrc_app:\$\{", default.group(1)), default.group(1)


def test_migrations_default_to_the_owner(compose):
    doc, _ = compose
    url = doc["x-backend-env"]["WINGRC_MIGRATION_DATABASE_URL"]
    default = re.fullmatch(r"\$\{WINGRC_MIGRATION_DATABASE_URL:-(.*)\}", url, re.S)
    assert default, url
    assert default.group(1).startswith(
        "postgresql+psycopg://wingrc:${POSTGRES_PASSWORD:?POSTGRES_PASSWORD is required"
    ), default.group(1)


def test_generate_env_fills_the_app_role_password():
    script = COMPOSE.parent / "deploy" / "generate-env.sh"
    if not script.exists():
        pytest.skip("deploy/ is outside this tree (the backend image); CI runs this")
    assert '"WINGRC_APP_DB_PASSWORD": token' in script.read_text(encoding="utf-8")


def test_no_test_database_url_in_any_service(compose):
    """The test URL is the owner's, a superuser, and nothing at runtime
    reads it, so no deployment's environment carries it (removed
    2026-11-18). Tests inside a container get it for that run only."""
    doc, text = compose
    assert "WINGRC_TEST_DATABASE_URL" not in doc["x-backend-env"]
    for name, svc in doc["services"].items():
        env = svc.get("environment") or {}
        keys = env if isinstance(env, dict) else [e.split("=", 1)[0] for e in env]
        assert "WINGRC_TEST_DATABASE_URL" not in keys, name
    assert "wingrc_test" not in text


@pytest.mark.parametrize("literal", ["wingrc-dev-secret", "DFKuK2mEIJ", ":-dev:"])
def test_no_old_default_survives_anywhere_in_the_file(compose, literal):
    _, text = compose
    assert literal not in text


# --- The app's storage account is not MinIO's root (2026-11-23) ---


def test_only_provision_holds_minios_root(compose):
    """Root reaches MinIO and the one-shot `provision` container, and no
    serving container: the app's storage keys are its own account's."""
    doc, _ = compose
    prov = doc["services"]["provision"]["environment"]
    assert prov["WINGRC_STORAGE_ACCESS_KEY"].startswith("${MINIO_ROOT_USER:?")
    assert prov["WINGRC_STORAGE_SECRET_KEY"].startswith("${MINIO_ROOT_PASSWORD:?")
    assert prov["WINGRC_PROVISION_APP_ACCESS_KEY"].startswith("${WINGRC_STORAGE_ACCESS_KEY:?")
    assert prov["WINGRC_PROVISION_APP_SECRET_KEY"].startswith("${WINGRC_STORAGE_SECRET_KEY:?")
    for name in ("backend", "worker"):
        env = doc["services"][name]["environment"]
        assert env["WINGRC_STORAGE_ACCESS_KEY"].startswith("${WINGRC_STORAGE_ACCESS_KEY:?"), name
        assert "MINIO_ROOT" not in str(env), name
        assert not any(k.startswith("WINGRC_PROVISION_") for k in env), name


def test_provision_runs_once_and_before_the_backend(compose):
    doc, _ = compose
    prov = doc["services"]["provision"]
    assert prov["restart"] == "no"
    assert prov["command"] == "python -m app.provision"
    assert doc["services"]["backend"]["depends_on"]["provision"] == {
        "condition": "service_completed_successfully"
    }


def test_deploy_runs_provision_before_recreating_the_app():
    """deploy.sh's `up` is --no-deps, so the backend's depends_on never
    runs provisioning there; deploy.sh has to."""
    script = COMPOSE.parent / "deploy" / "deploy.sh"
    if not script.exists():
        pytest.skip("deploy/ is outside this tree (the backend image); CI runs this")
    lines = script.read_text(encoding="utf-8").splitlines()
    provision = "docker compose run --rm --no-deps -T provision"
    run = next(i for i, ln in enumerate(lines) if ln.startswith(provision))
    up = next(i for i, ln in enumerate(lines) if ln.startswith("docker compose up -d --no-deps"))
    assert run < up


def test_snapshots_run_in_provision_not_the_backend():
    """A snapshot lists the bucket, which the app's account may not."""
    script = COMPOSE.parent / "deploy" / "snapshot.sh"
    if not script.exists():
        pytest.skip("deploy/ is outside this tree (the backend image); CI runs this")
    runs = [ln for ln in script.read_text(encoding="utf-8").splitlines()
            if ln.startswith("docker compose run")]
    assert len(runs) == 2, runs
    assert all(ln.rstrip(" \\").endswith(" provision") for ln in runs), runs


def test_generate_env_makes_the_app_its_own_storage_account():
    script = COMPOSE.parent / "deploy" / "generate-env.sh"
    if not script.exists():
        pytest.skip("deploy/ is outside this tree (the backend image); CI runs this")
    text = script.read_text(encoding="utf-8")
    assert '"WINGRC_STORAGE_ACCESS_KEY": lambda: "wingrc-app"' in text
    assert '"WINGRC_STORAGE_SECRET_KEY": token' in text
    assert '"MINIO_ROOT_USER")' not in text, "the storage keys must not be copied from root"
