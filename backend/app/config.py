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
    pwned_passwords_check: bool = True

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
