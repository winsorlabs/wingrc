"""WinGRC admin management CLI.

Used for first-boot bootstrap and other privileged operations that
run outside the HTTP request cycle.

Usage (in container):
    python -m app.manage bootstrap-admin \\
        --org "Winsor Labs" \\
        --email admin@example.com \\
        --display-name "Admin User" \\
        --role msp_admin

The command creates the org if it does not exist, creates the user,
and prompts for a password. MFA enrollment happens on first web login.

Also grants the new user an `org_membership` row for that org (see
docs/adr/0009-multi-org-user-access.md), and — only on the very first
bootstrap this database has ever seen — anchors `deployment_settings`
to this org as the deployment's own MSP org. Neither of those is read by
any request-time authorization check yet (M.1 is schema-only); this
command populates them now so the data is already correct by the time a
later slice starts trusting it.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass

import typer
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from .auth import hash_password, validate_password_policy
from .db import SessionLocal
from .models import DeploymentSettings, Organization, OrgMembership, User

app = typer.Typer(help="WinGRC admin management CLI.")


@dataclass
class BootstrapResult:
    org_id: uuid.UUID
    org_name: str
    org_created: bool
    user_id: uuid.UUID
    user_email: str
    deployment_settings_anchored: bool


def _bootstrap_admin_core(
    db: Session,
    *,
    org: str,
    email: str,
    display_name: str,
    role: str,
    password: str,
) -> BootstrapResult:
    """Create (or reuse) an org, create the admin user, grant them an
    org_membership for it, and anchor deployment_settings if this is the
    first bootstrap this database has seen.

    Pure logic, no CLI I/O — split out from bootstrap_admin() so it's
    directly testable against a real Session rather than only reachable
    through the Typer command's own SessionLocal(). Caller is responsible
    for commit/rollback; this function only flushes. Raises ValueError
    (not typer.Exit) on the "user already exists" case, so callers other
    than the CLI command can handle it without importing typer.
    """
    org_row = db.execute(
        select(Organization).where(Organization.name == org)
    ).scalar_one_or_none()
    org_created = org_row is None
    if org_row is None:
        org_row = Organization(name=org)
        db.add(org_row)
        db.flush()

    existing = db.execute(
        select(User).where(User.org_id == org_row.id, User.email == email)
    ).scalar_one_or_none()
    if existing is not None:
        raise ValueError(f"user {email!r} already exists in this org")

    # RLS: set current_org before any RLS-gated operations
    db.execute(text(f"SET LOCAL app.current_org = '{org_row.id}'"))

    user = User(
        org_id=org_row.id,
        email=email,
        display_name=display_name,
        login_method="local",
        role=role,
        is_active=True,
        password_hash=hash_password(password),
        mfa_enrolled=False,
    )
    db.add(user)
    db.flush()

    db.add(OrgMembership(user_id=user.id, org_id=org_row.id, role=role))

    # Anchor deployment_settings on the very first bootstrap for this
    # database only. Never overwritten once set — per ADR 0009's Boundary
    # section, changing it later is a deliberate DBA/migration action, not
    # something this command (or any runtime code) should do implicitly.
    deployment_settings_anchored = False
    has_settings = db.execute(select(DeploymentSettings.id)).first()
    if has_settings is None:
        db.add(DeploymentSettings(id=1, msp_org_id=org_row.id))
        deployment_settings_anchored = True

    return BootstrapResult(
        org_id=org_row.id,
        org_name=org_row.name,
        org_created=org_created,
        user_id=user.id,
        user_email=user.email,
        deployment_settings_anchored=deployment_settings_anchored,
    )


@app.command("bootstrap-admin")
def bootstrap_admin(
    org: str = typer.Option(..., "--org", help="Organization name to create or find"),
    email: str = typer.Option(..., "--email", help="Admin user email"),
    display_name: str = typer.Option(..., "--display-name", help="Admin display name"),
    role: str = typer.Option("msp_admin", "--role", help="User role"),
    password: str = typer.Option(
        None, "--password",
        help="Password (omit to be prompted interactively)",
    ),
) -> None:
    """Create bootstrap admin user. MFA enrollment is required on first web login."""
    if password is None:
        password = typer.prompt("Password", hide_input=True, confirmation_prompt=True)

    errors = validate_password_policy(password)
    if errors:
        for e in errors:
            typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)

    db = SessionLocal()
    try:
        result = _bootstrap_admin_core(
            db, org=org, email=email, display_name=display_name,
            role=role, password=password,
        )
        db.commit()
    except ValueError as e:
        db.rollback()
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1) from e
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    if result.org_created:
        typer.echo(f"Created org: {result.org_name} ({result.org_id})")
    else:
        typer.echo(f"Found existing org: {result.org_name} ({result.org_id})")
    typer.echo(f"Created user: {result.user_email} ({result.user_id})")
    typer.echo(f"Granted org_membership for {result.org_name}.")
    if result.deployment_settings_anchored:
        typer.echo(f"Anchored deployment_settings.msp_org_id = {result.org_id}")
    typer.echo("MFA enrollment required on first web login.")


if __name__ == "__main__":
    app()
