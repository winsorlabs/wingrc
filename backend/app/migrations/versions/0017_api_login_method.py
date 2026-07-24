"""Add 'api' to user.login_method — enables dedicated API-user (service
account) rows that authenticate only via ApiToken, never a session/cookie
login.

Revision ID: 0017_api_login_method
Revises: 0016_app_role
Create Date: 2026-07-24
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0017_api_login_method"
down_revision: str | None = "0016_app_role"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_user_login_method", "user", type_="check")
    op.create_check_constraint(
        "ck_user_login_method",
        "user",
        "login_method IN ('sso','local','api')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_user_login_method", "user", type_="check")
    op.create_check_constraint(
        "ck_user_login_method",
        "user",
        "login_method IN ('sso','local')",
    )
