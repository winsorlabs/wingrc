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


def test_the_database_urls_carry_no_password_literal(compose):
    doc, _ = compose
    env = doc["x-backend-env"]
    for key in ("WINGRC_DATABASE_URL", "WINGRC_TEST_DATABASE_URL"):
        assert "${POSTGRES_PASSWORD:?" in env[key], f"{key}: {env[key]}"
        assert "wingrc:wingrc@" not in env[key], env[key]


@pytest.mark.parametrize("literal", ["wingrc-dev-secret", "DFKuK2mEIJ", ":-dev:"])
def test_no_old_default_survives_anywhere_in_the_file(compose, literal):
    _, text = compose
    assert literal not in text
