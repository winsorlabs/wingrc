"""WinGRC command line.

Lets you run the full pilot loop without a browser:

    wingrc seed --org "GSM Metals" workbook.xlsx     # import (with reconcile)
    wingrc scope --org "GSM Metals" --type device     # inspect source of truth
    wingrc render --org "GSM Metals" 3.1.1c-authorized-devices out.xlsx
"""

from __future__ import annotations

from pathlib import Path

import typer

from . import repo
from .catalog import ALL_VIEWS, VIEWS_BY_ID
from .db import SessionLocal
from .domain import ChangeType, EntityType
from .importers.workbook import parse_workbook
from .reconcile import reconcile
from .render import render_view
from .seeds.catalog import seed_catalog

app = typer.Typer(help="WinGRC — open CMMC scope and documentation tooling.")


@app.command()
def seed(
    workbook: Path = typer.Argument(..., help="Authorized-Entities .xlsx to import"),
    org: str = typer.Option(..., help="Organization (tenant) name"),
    apply: bool = typer.Option(
        False, help="Apply the reconcile result (default: dry-run only)"
    ),
) -> None:
    """Import a workbook, show the reconcile diff, and optionally apply it."""
    incoming = parse_workbook(workbook)
    session = SessionLocal()
    try:
        org_row = repo.get_or_create_org(session, org)
        current = repo.list_entities(session, org_row.id)
        result = reconcile(current, incoming)
        typer.echo(f"Reconcile summary: {result.summary()}")
        for c in result.of(ChangeType.NEW, ChangeType.CHANGED, ChangeType.MISSING):
            typer.echo(f"  [{c.change_type.value:<8}] {c.entity_type.value}: {c.natural_key}")
        if apply:
            for c in result.of(ChangeType.NEW, ChangeType.CHANGED):
                repo.upsert(session, org_row.id, c.incoming)
            session.commit()
            typer.echo("Applied. Scope graph is now the source of truth.")
        else:
            typer.echo("Dry-run only. Re-run with --apply to write.")
    finally:
        session.close()


@app.command()
def scope(
    org: str = typer.Option(..., help="Organization (tenant) name"),
    type: str = typer.Option(None, "--type", help="Filter by entity type"),
) -> None:
    """Print the current scope graph (the live source of truth)."""
    session = SessionLocal()
    try:
        org_row = repo.get_or_create_org(session, org)
        et = EntityType(type) if type else None
        for e in repo.list_entities(session, org_row.id, et):
            cat = e.scope_category.value if e.scope_category else "-"
            typer.echo(f"{e.entity_type.value:<18} {cat:<12} {e.natural_key}")
    finally:
        session.close()


@app.command()
def render(
    view_id: str = typer.Argument(..., help="View id, e.g. 3.1.1c-authorized-devices"),
    out: Path = typer.Argument(..., help="Output .xlsx path"),
    org: str = typer.Option(..., help="Organization (tenant) name"),
) -> None:
    """Generate an assessor-ready list from the scope graph."""
    view = VIEWS_BY_ID.get(view_id)
    if view is None:
        ids = ", ".join(v.id for v in ALL_VIEWS)
        raise typer.BadParameter(f"Unknown view. Available: {ids}")
    session = SessionLocal()
    try:
        org_row = repo.get_or_create_org(session, org)
        entities = repo.list_entities(session, org_row.id, view.entity_type)
        path = render_view(view, entities, out)
        typer.echo(f"Wrote {path}")
    finally:
        session.close()


@app.command(name="seed-catalog")
def seed_catalog_cmd(
    db_url: str = typer.Option(None, "--db-url", help="Override DATABASE_URL"),
) -> None:
    """Load the CMMC L2 control catalog into the database (idempotent)."""
    import os

    if db_url:
        os.environ["DATABASE_URL"] = db_url

    from .db import SessionLocal as _SL  # re-import to pick up env override

    session = _SL()
    try:
        result = seed_catalog(session)
        session.commit()
        typer.echo(
            f"Catalog seeded: framework {result['framework_id']}, "
            f"{result['controls']} controls, {result['objectives']} objectives."
        )
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@app.command()
def views() -> None:
    """List the available CMMC list views."""
    for v in ALL_VIEWS:
        typer.echo(f"{v.id:<28} {','.join(v.control_ids):<26} {v.title}")


if __name__ == "__main__":
    app()
