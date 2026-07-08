"""WinGRC command line.

Lets you run the full pilot loop without a browser:

    wingrc seed --org "GSM Metals" workbook.xlsx     # import (with reconcile)
    wingrc scope --org "GSM Metals" --type device     # inspect source of truth
    wingrc render --org "GSM Metals" 3.1.1c-authorized-devices out.xlsx
"""

from __future__ import annotations

from pathlib import Path

import typer
from sqlalchemy import text

from . import repo
from .catalog import ALL_VIEWS, VIEWS_BY_ID
from .db import SessionLocal
from .domain import ChangeType, EntityType
from .importers.workbook import parse_workbook
from .models import Organization
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


@app.command(name="reset-dev")
def reset_dev(
    yes: bool = typer.Option(False, "--yes", help="Skip confirmation prompt"),
) -> None:
    """Restore the dev DB to a clean state: CMMC L2 catalog + 'Acme MSP' org only.

    Deletes all test-framework rows and test-org rows that integration tests
    leave behind when they commit against the dev database.  Safe to run
    repeatedly; the catalog seed step is idempotent.

    NEVER run this against a production database.
    """
    from sqlalchemy import select

    session = SessionLocal()
    try:
        if not yes:
            db_url = session.get_bind().url  # type: ignore[attr-defined]
            typer.echo(f"Target database: {db_url}")
            typer.echo(
                "This will DELETE all test data, keeping only:\n"
                "  • framework 'nist-800-171-r2' (CMMC L2)\n"
                "  • org 'Acme MSP'"
            )
            typer.confirm("Proceed?", abort=True)

        deleted = _reset_dev(session)

        # Ensure the canonical org exists
        acme = session.scalars(
            select(Organization).where(Organization.name == "Acme MSP")
        ).first()
        if acme is None:
            session.add(Organization(name="Acme MSP"))
            session.flush()
            typer.echo("Created 'Acme MSP' org.")

        # Re-seed catalog (idempotent — updates discussion/guidance text)
        result = seed_catalog(session)
        session.commit()

        typer.echo(
            f"\nDev DB reset complete.\n"
            f"  Catalog: {result['controls']} controls, {result['objectives']} objectives\n"
            f"  Rows deleted: {deleted}"
        )

        # Verification queries
        ctrl_count = session.execute(text("SELECT count(*) FROM control")).scalar()
        fw_count = session.execute(text("SELECT count(*) FROM framework")).scalar()
        org_count = session.execute(text("SELECT count(*) FROM organization")).scalar()
        typer.echo(
            f"\nVerification:\n"
            f"  frameworks : {fw_count}  (expected 1)\n"
            f"  controls   : {ctrl_count}  (expected 110)\n"
            f"  orgs       : {org_count}  (expected 1)"
        )
        if fw_count != 1 or ctrl_count != 110:
            typer.echo("WARNING: counts unexpected — check catalog YAML.")
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _reset_dev(session) -> dict[str, int]:
    """Delete all test-generated rows in FK-safe order.

    Keeps: framework key='nist-800-171-r2' and org name='Acme MSP'.
    Everything else is considered test pollution and removed.

    Returns a dict of {table: rows_deleted} for reporting.
    """
    # Helper that executes a DELETE and returns the rowcount.
    def _del(sql: str, params: dict | None = None) -> int:
        r = session.execute(text(sql), params or {})
        session.flush()
        return r.rowcount

    deleted: dict[str, int] = {}

    # ------------------------------------------------------------------ #
    # Tier 1 — junction/leaf tables: no other table FKs point at them     #
    # ------------------------------------------------------------------ #
    deleted["raci_assignment"] = _del("DELETE FROM raci_assignment")
    deleted["evidence_state_link"] = _del("DELETE FROM evidence_state_link")

    # ------------------------------------------------------------------ #
    # Tier 2 — tables that reference control_state                        #
    # ------------------------------------------------------------------ #
    deleted["control_state_history"] = _del("DELETE FROM control_state_history")
    deleted["evidence_task"] = _del("DELETE FROM evidence_task")
    deleted["finding"] = _del("DELETE FROM finding")
    deleted["poa_m_item"] = _del("DELETE FROM poa_m_item")
    deleted["implementation_statement"] = _del("DELETE FROM implementation_statement")

    # ------------------------------------------------------------------ #
    # Tier 3 — core assessment tables                                     #
    # ------------------------------------------------------------------ #
    deleted["control_state"] = _del("DELETE FROM control_state")
    deleted["assessment"] = _del("DELETE FROM assessment")
    deleted["org_product"] = _del("DELETE FROM org_product")
    deleted["evidence"] = _del("DELETE FROM evidence")

    # ------------------------------------------------------------------ #
    # Tier 4 — baseline library rows tied to non-CMMC-L2 frameworks      #
    # ------------------------------------------------------------------ #
    _KEEP_FW = "SELECT id FROM framework WHERE key = 'nist-800-171-r2'"

    deleted["baseline_evidence_spec (test fw)"] = _del(
        f"DELETE FROM baseline_evidence_spec WHERE baseline_control_id IN ("
        f"  SELECT bc.id FROM baseline_control bc"
        f"  JOIN product p ON bc.product_id = p.id"
        f"  WHERE p.framework_id NOT IN ({_KEEP_FW})"
        f")"
    )
    deleted["baseline_control (test fw)"] = _del(
        f"DELETE FROM baseline_control WHERE product_id IN ("
        f"  SELECT id FROM product WHERE framework_id NOT IN ({_KEEP_FW})"
        f")"
    )
    deleted["product (test fw)"] = _del(
        f"DELETE FROM product WHERE framework_id NOT IN ({_KEEP_FW})"
    )

    # ------------------------------------------------------------------ #
    # Tier 5 — test framework catalog rows                                #
    # ------------------------------------------------------------------ #
    _TEST_FW = "SELECT id FROM framework WHERE key != 'nist-800-171-r2'"

    deleted["assessment_objective (test fw)"] = _del(
        f"DELETE FROM assessment_objective WHERE control_id IN ("
        f"  SELECT id FROM control WHERE framework_id IN ({_TEST_FW})"
        f")"
    )
    deleted["control (test fw)"] = _del(
        f"DELETE FROM control WHERE framework_id IN ({_TEST_FW})"
    )
    deleted["framework (test)"] = _del(
        "DELETE FROM framework WHERE key != 'nist-800-171-r2'"
    )

    # ------------------------------------------------------------------ #
    # Tier 6 — test org data (Contact has ON DELETE CASCADE on org_id,   #
    #           but we delete explicitly for clarity and scope_entity     #
    #           has no FK so must be done manually)                       #
    # ------------------------------------------------------------------ #
    _TEST_ORGS = "SELECT id FROM organization WHERE name != 'Acme MSP'"

    deleted["contact (test orgs)"] = _del(
        f"DELETE FROM contact WHERE org_id IN ({_TEST_ORGS})"
    )
    deleted["scope_entity (test orgs)"] = _del(
        f"DELETE FROM scope_entity WHERE org_id IN ({_TEST_ORGS})"
    )
    deleted["organization (test)"] = _del(
        "DELETE FROM organization WHERE name != 'Acme MSP'"
    )

    return deleted


@app.command()
def views() -> None:
    """List the available CMMC list views."""
    for v in ALL_VIEWS:
        typer.echo(f"{v.id:<28} {','.join(v.control_ids):<26} {v.title}")


if __name__ == "__main__":
    app()
