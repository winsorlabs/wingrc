"""Runtime configuration, read from the environment (12-factor)."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="WINGRC_", env_file=".env")

    # Default points at the docker-compose Postgres service.
    database_url: str = "postgresql+psycopg://wingrc:wingrc@localhost:5432/wingrc"
    app_name: str = "WinGRC"
    environment: str = "development"

    # The URL a browser uses to reach this deployment -- the backend has no
    # other way to know it (nginx terminates the real hostname; there's no
    # request in scope when building an invite/reset email). Only consumed
    # by routers/users.py to build the link in those two emails
    # (email_service.py itself is link-agnostic). Unset means invite/reset
    # emails fall back to the token-only response the admin already
    # delivers by hand today -- see routers/users.py's own comment -- since
    # a link-less email would violate the "link, not a bare token" rule.
    public_url: str | None = None

    # SQLAlchemy connection pool, sized deliberately rather than left at
    # SQLAlchemy's own defaults (pool_size=5, max_overflow=10 -> 15 total,
    # unmodified since this app's first commit). Every sync endpoint/
    # dependency FastAPI runs (which is nearly all of them — see
    # get_current_user's docstring for the one deliberate exception) is
    # dispatched onto anyio's worker threadpool, whose own default capacity
    # is 40 concurrent threads per process. A DB pool smaller than that cap
    # doesn't remove the queue, it just relocates it from the threadpool to
    # pool checkout — so size the pool to match the threadpool instead of
    # guessing: 20 + 20 = 40. Postgres 18's own default max_connections is
    # 100, and docker-compose.yml runs exactly one uvicorn worker (no
    # --workers flag), so 40 leaves headroom for `alembic upgrade head` at
    # startup, admin psql sessions, and a future increase in worker count.
    # A deployment that runs multiple workers must lower these (or raise
    # Postgres's max_connections) so worker_count * db_pool_size stays under
    # it — that math is not automatic.
    db_pool_size: int = 20
    db_max_overflow: int = 20

    # AI provider abstraction — pluggable so CUI-sensitive tenants can keep
    # generation local. Not exercised by the scope module yet.
    ai_provider: str = "none"  # one of: none | anthropic | azure_openai | local

    # S3-compatible object storage for evidence artifacts.
    # Set storage_endpoint to activate MinIOClient; leave unset to use NullStorageClient.
    storage_endpoint: str | None = None
    # Public/browser-facing endpoint for presigned URLs.  The backend reaches MinIO
    # via storage_endpoint (internal Docker DNS); browsers need the LAN/public address.
    # If unset, presigned URLs use storage_endpoint (fine for same-host dev).
    storage_public_endpoint: str | None = None
    storage_access_key: str = "wingrc"
    storage_secret_key: str = "wingrc-dev-secret"
    storage_bucket: str = "evidence"
    storage_region: str = "us-east-1"

    # Auth: Microsoft Entra ID SSO (optional — omit to disable SSO)
    entra_tenant_id: str | None = None
    entra_client_id: str | None = None
    entra_client_secret: str | None = None
    entra_redirect_uri: str = "http://localhost:8000/api/auth/callback"

    # Auth: cookie signing for auth_flow + mfa_pending state cookies
    auth_flow_secret: str = "dev-auth-flow-secret-change-in-production"

    # Auth: session expiry and password policy
    session_expiry_hours: int = 8
    # 3.1.11 inactivity timeout. 15 min is the common DoD-aligned default;
    # 800-171 leaves the period org-defined, so this must stay configurable
    # and the configured value must appear in the SSP for this control.
    session_idle_minutes: int = 15
    # Concurrent session cap per user. 0 = unlimited (default) — optional
    # hardening, not required by any specific 800-171 control on its own.
    max_sessions_per_user: int = 0
    pwned_passwords_check: bool = True
    # Password reuse window (I.5): reject any of the last N passwords on
    # set/reset. Enforced only on that path — see auth.check_password_reuse.
    password_history_generations: int = 5

    # Third-party integration credentials (D.1 — Liongard first): symmetric
    # encryption key(s) for crypto.py's encrypt_credential/decrypt_credential.
    # Never persisted in the database — deploy-time config only, and
    # deliberately has no default: crypto.py fails closed (refuses to
    # store/read a credential) when this is unset rather than falling back
    # to plaintext. Format: "label1:fernetkey1,label2:fernetkey2,..." — see
    # crypto.py's module docstring for the rotation story.
    credential_encryption_keys: str | None = None

    # Allowed CORS origins.  In production set WINGRC_CORS_ORIGINS to a JSON
    # array of the exact origins that should be permitted, e.g.:
    #   WINGRC_CORS_ORIGINS='["https://app.example.com"]'
    # The defaults cover local and LAN dev (frontend :5173) plus the API
    # server itself (:8000) so Swagger /docs try-it-out works from any of
    # those origins without extra config.
    cors_origins: list[str] = [
        "http://localhost:5173",
        "http://10.10.24.35:5173",
        "http://localhost:8000",
        "http://10.10.24.35:8000",
    ]


@lru_cache
def get_settings() -> Settings:
    return Settings()
