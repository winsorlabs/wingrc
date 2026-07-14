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
"""
from __future__ import annotations

import typer
from sqlalchemy import select, text

from .auth import hash_password, validate_password_policy
from .db import SessionLocal
from .models import Organization, User

app = typer.Typer(help="WinGRC admin management CLI.")


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
        # Find or create org
        org_row = db.execute(
            select(Organization).where(Organization.name == org)
        ).scalar_one_or_none()
        if org_row is None:
            org_row = Organization(name=org)
            db.add(org_row)
            db.flush()
            typer.echo(f"Created org: {org_row.name} ({org_row.id})")
        else:
            typer.echo(f"Found existing org: {org_row.name} ({org_row.id})")

        # Check for existing user
        existing = db.execute(
            select(User).where(User.org_id == org_row.id, User.email == email)
        ).scalar_one_or_none()
        if existing is not None:
            typer.echo(f"Error: user {email!r} already exists in this org.", err=True)
            raise typer.Exit(1)

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
        db.commit()

        typer.echo(f"Created user: {email} ({user.id})")
        typer.echo("MFA enrollment required on first web login.")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    app()
