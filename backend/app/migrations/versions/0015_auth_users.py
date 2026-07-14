"""Auth: users, sessions, MFA backup codes, and API tokens.

Creates the user authentication infrastructure:
  - user table (local + SSO, per-org, RLS-gated)
  - user_session table (org_id denormalized for RLS bootstrap)
  - mfa_backup_code table (one-time TOTP recovery codes)
  - api_token table (machine-to-machine Bearer tokens)

RLS policies applied to all four tables using the existing app.current_org
GUC pattern.

Four SECURITY DEFINER functions in the auth schema provide narrow,
search_path-pinned RLS bypass for pre-auth lookups where org_id is
not yet known:
  auth.resolve_session     -- session middleware
  auth.find_user_for_login -- Entra callback + local login
  auth.find_user_for_invite -- invite-token lookup during set-password
  auth.resolve_api_token   -- Bearer token middleware

The functions are owned by the migration user (wingrc, a PostgreSQL superuser
in the Docker setup) and therefore carry BYPASSRLS implicitly. The search_path
is pinned to prevent search_path-hijacking privilege escalation.

Revision ID: 0015_auth_users
Revises: 0014_evidence_sha256
Create Date: 2026-07-13
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0015_auth_users"
down_revision: str | None = "0014_evidence_sha256"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS auth")

    # --- user ---
    op.create_table(
        "user",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("org_id", UUID(as_uuid=True),
                  sa.ForeignKey("organization.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("contact_id", UUID(as_uuid=True),
                  sa.ForeignKey("contact.id", ondelete="SET NULL"),
                  nullable=True),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("entra_oid", sa.String(100), unique=True, nullable=True),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("login_method", sa.String(10), nullable=False,
                  server_default=sa.text("'local'")),
        sa.Column("role", sa.String(40), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False,
                  server_default=sa.text("false")),
        # Local auth fields
        sa.Column("password_hash", sa.Text(), nullable=True),
        sa.Column("invite_token_hash", sa.String(64), nullable=True),
        sa.Column("invite_expires_at", sa.DateTime(timezone=True), nullable=True),
        # MFA
        sa.Column("totp_secret", sa.Text(), nullable=True),
        sa.Column("mfa_enrolled", sa.Boolean(), nullable=False,
                  server_default=sa.text("false")),
        # Lockout (local accounts)
        sa.Column("failed_login_count", sa.Integer(), nullable=False,
                  server_default=sa.text("0")),
        sa.Column("lockout_count", sa.Integer(), nullable=False,
                  server_default=sa.text("0")),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("requires_admin_reset", sa.Boolean(), nullable=False,
                  server_default=sa.text("false")),
        # Timestamps
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.CheckConstraint("login_method IN ('sso','local')",
                           name="ck_user_login_method"),
        sa.CheckConstraint(
            "role IN ('msp_admin','msp_engineer','customer_poc','c3pao_assessor')",
            name="ck_user_role",
        ),
        sa.UniqueConstraint("org_id", "email", name="uq_user_org_email"),
    )
    op.execute('ALTER TABLE "user" ENABLE ROW LEVEL SECURITY')
    op.execute(
        """CREATE POLICY user_org ON "user"
           USING (org_id = current_setting('app.current_org', true)::uuid)"""
    )

    # --- user_session ---
    op.create_table(
        "user_session",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", UUID(as_uuid=True),
                  sa.ForeignKey("user.id", ondelete="CASCADE"), nullable=False),
        sa.Column("org_id", UUID(as_uuid=True),
                  sa.ForeignKey("organization.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "idx_user_session_active", "user_session", ["token_hash"],
        postgresql_where=sa.text("revoked_at IS NULL"),
    )
    op.execute("ALTER TABLE user_session ENABLE ROW LEVEL SECURITY")
    op.execute(
        """CREATE POLICY session_org ON user_session
           USING (org_id = current_setting('app.current_org', true)::uuid)"""
    )

    # --- mfa_backup_code ---
    op.create_table(
        "mfa_backup_code",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", UUID(as_uuid=True),
                  sa.ForeignKey("user.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("code_hash", sa.String(64), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute("ALTER TABLE mfa_backup_code ENABLE ROW LEVEL SECURITY")
    op.execute(
        """CREATE POLICY mfa_code_org ON mfa_backup_code
           USING (user_id IN (
               SELECT id FROM "user"
               WHERE org_id = current_setting('app.current_org', true)::uuid
           ))"""
    )

    # --- api_token ---
    op.create_table(
        "api_token",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("org_id", UUID(as_uuid=True),
                  sa.ForeignKey("organization.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("user_id", UUID(as_uuid=True),
                  sa.ForeignKey("user.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("role", sa.String(40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "role IN ('msp_admin','msp_engineer','customer_poc','c3pao_assessor')",
            name="ck_api_token_role",
        ),
    )
    op.create_index(
        "idx_api_token_active", "api_token", ["token_hash"],
        postgresql_where=sa.text("revoked_at IS NULL"),
    )
    op.execute("ALTER TABLE api_token ENABLE ROW LEVEL SECURITY")
    op.execute(
        """CREATE POLICY token_org ON api_token
           USING (org_id = current_setting('app.current_org', true)::uuid)"""
    )

    # -------------------------------------------------------------------------
    # SECURITY DEFINER functions for pre-auth lookups.
    # search_path pinned to prevent search_path-hijacking privilege escalation.
    # All four functions are owned by the migration user (superuser → BYPASSRLS).
    # -------------------------------------------------------------------------
    op.execute("""
        CREATE FUNCTION auth.resolve_session(p_hash VARCHAR)
        RETURNS TABLE (user_id UUID, org_id UUID, expires_at TIMESTAMPTZ)
        SECURITY DEFINER
        SET search_path = public, pg_catalog
        LANGUAGE sql STABLE AS $$
            SELECT s.user_id, s.org_id, s.expires_at
            FROM public.user_session s
            WHERE s.token_hash = p_hash
              AND s.revoked_at IS NULL
              AND s.expires_at > now()
            LIMIT 1;
        $$
    """)

    op.execute("""
        CREATE FUNCTION auth.find_user_for_login(p_oid VARCHAR, p_email VARCHAR)
        RETURNS SETOF public."user"
        SECURITY DEFINER
        SET search_path = public, pg_catalog
        LANGUAGE sql STABLE AS $$
            SELECT * FROM public."user"
            WHERE (p_oid IS NOT NULL AND entra_oid = p_oid)
               OR (p_oid IS NULL AND email = p_email)
            LIMIT 1;
        $$
    """)

    op.execute("""
        CREATE FUNCTION auth.find_user_for_invite(p_token_hash VARCHAR)
        RETURNS SETOF public."user"
        SECURITY DEFINER
        SET search_path = public, pg_catalog
        LANGUAGE sql STABLE AS $$
            SELECT * FROM public."user"
            WHERE invite_token_hash = p_token_hash
              AND invite_expires_at > now()
              AND is_active = FALSE
            LIMIT 1;
        $$
    """)

    op.execute("""
        CREATE FUNCTION auth.resolve_api_token(p_hash VARCHAR)
        RETURNS TABLE (id UUID, org_id UUID, user_id UUID, role VARCHAR,
                       expires_at TIMESTAMPTZ, revoked_at TIMESTAMPTZ)
        SECURITY DEFINER
        SET search_path = public, pg_catalog
        LANGUAGE sql STABLE AS $$
            SELECT t.id, t.org_id, t.user_id, t.role, t.expires_at, t.revoked_at
            FROM public.api_token t
            WHERE t.token_hash = p_hash
            LIMIT 1;
        $$
    """)


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS auth.resolve_api_token(VARCHAR)")
    op.execute("DROP FUNCTION IF EXISTS auth.find_user_for_invite(VARCHAR)")
    op.execute("DROP FUNCTION IF EXISTS auth.find_user_for_login(VARCHAR, VARCHAR)")
    op.execute("DROP FUNCTION IF EXISTS auth.resolve_session(VARCHAR)")
    op.drop_index("idx_api_token_active", table_name="api_token")
    op.drop_table("api_token")
    op.drop_table("mfa_backup_code")
    op.drop_index("idx_user_session_active", table_name="user_session")
    op.drop_table("user_session")
    op.drop_table("user")
    op.execute("DROP SCHEMA IF EXISTS auth CASCADE")
