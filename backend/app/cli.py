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
from .importers.workbook import parse_workbook, resolve_canonical_device_attributes
from .models import Organization
from .reconcile import reconcile
from .render import render_view
from .seeds.baselines import seed_baselines
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
        attr_warnings = resolve_canonical_device_attributes(session, org_row.id, incoming)
        current = repo.list_entities(session, org_row.id)
        result = reconcile(current, incoming)
        typer.echo(f"Reconcile summary: {result.summary()}")
        for c in result.of(ChangeType.NEW, ChangeType.CHANGED, ChangeType.MISSING):
            typer.echo(f"  [{c.change_type.value:<8}] {c.entity_type.value}: {c.natural_key}")
        for msgs in attr_warnings.values():
            for msg in msgs:
                typer.echo(f"  [warning ] {msg}")
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

    NEVER run this against a production database. Enforced below, not just
    documented — `--yes` skips the confirmation prompt but never bypasses
    the WINGRC_ENVIRONMENT=production check.
    """
    from sqlalchemy import select

    from .config import get_settings

    if get_settings().environment == "production":
        typer.echo(
            "Refusing to run: WINGRC_ENVIRONMENT=production. reset-dev deletes "
            "assessment data and audit history — this must never run against "
            "a production database."
        )
        raise typer.Exit(code=1)

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
    Everything else is considered test pollution and removed — including
    the assessment-layer data (control_state, evidence, findings, ...) of
    the *kept* org too: tiers 1-3 below are unconditional, not scoped to
    "test orgs", so Acme MSP gets a fresh assessment-layer state alongside
    everyone else. Only Tier 6 (contact/scope_entity/organization/
    audit_log) is scoped to non-Acme-MSP orgs, since those are the rows
    that represent the org's identity/profile rather than in-progress
    assessment work.

    Returns a dict of {table: rows_affected} for reporting (a couple of
    entries are UPDATEs that clear a dangling FK pointer rather than
    DELETEs — see Tier 0 — but the reporting shape is the same).
    """
    # Helper that executes a DELETE/UPDATE and returns the rowcount.
    def _del(sql: str, params: dict | None = None) -> int:
        r = session.execute(text(sql), params or {})
        session.flush()
        return r.rowcount

    deleted: dict[str, int] = {}

    # ------------------------------------------------------------------ #
    # Tier 0 — clear FK pointers that would block Tier 3's evidence wipe. #
    # system_description's pinned diagram slots (migration 0029) point at #
    # evidence.id with no ON DELETE action, and system_description itself #
    # is never deleted here (it only cascades away when its owning org is #
    # deleted — which never happens for Acme MSP, and Tier 3 wipes        #
    # `evidence` unconditionally, for every org). Null the pointers first #
    # so the evidence delete below doesn't hit a foreign-key violation.   #
    # ------------------------------------------------------------------ #
    deleted["system_description (diagram slots cleared)"] = _del(
        "UPDATE system_description SET network_diagram_evidence_id = NULL, "
        "data_flow_diagram_evidence_id = NULL "
        "WHERE network_diagram_evidence_id IS NOT NULL "
        "OR data_flow_diagram_evidence_id IS NOT NULL"
    )

    # ------------------------------------------------------------------ #
    # Tier 1 — junction/leaf tables: no other table FKs point at them     #
    # ------------------------------------------------------------------ #
    deleted["raci_assignment"] = _del("DELETE FROM raci_assignment")
    deleted["evidence_state_link"] = _del("DELETE FROM evidence_state_link")
    # References evidence_task.id AND control_state.id, neither ON DELETE
    # CASCADE — must go before both (Tier 2's evidence_task, Tier 3's
    # control_state) or their deletes below raise a foreign-key violation.
    deleted["evidence_task_state_link"] = _del("DELETE FROM evidence_task_state_link")

    # ------------------------------------------------------------------ #
    # Tier 2 — tables that reference control_state                        #
    # ------------------------------------------------------------------ #
    deleted["control_state_history"] = _del("DELETE FROM control_state_history")
    deleted["evidence_task"] = _del("DELETE FROM evidence_task")
    # poa_m_item.finding_id -> finding.id has no ON DELETE action, so
    # poa_m_item must be deleted before finding, not after (the reverse of
    # the order these two used to run in — deleting finding first raised a
    # foreign-key violation the moment a poa_m_item referenced it).
    deleted["poa_m_item"] = _del("DELETE FROM poa_m_item")
    deleted["finding"] = _del("DELETE FROM finding")
    deleted["implementation_statement"] = _del("DELETE FROM implementation_statement")
    deleted["sprs_snapshot"] = _del("DELETE FROM sprs_snapshot")

    # ------------------------------------------------------------------ #
    # Tier 3 — core assessment tables                                     #
    # ------------------------------------------------------------------ #
    deleted["control_state"] = _del("DELETE FROM control_state")
    deleted["assessment"] = _del("DELETE FROM assessment")
    deleted["org_product"] = _del("DELETE FROM org_product")
    deleted["evidence"] = _del("DELETE FROM evidence")

    # ------------------------------------------------------------------ #
    # Tier 4 — FK safety: remove baseline rows and test products before   #
    # deleting test framework catalog rows in Tier 5.                     #
    # "Test products" = any product whose framework_id points at a non-   #
    # production framework (created by integration tests).                #
    # ------------------------------------------------------------------ #
    _KEEP_FW = "SELECT id FROM framework WHERE key = 'nist-800-171-r2'"
    _TEST_FW = "SELECT id FROM framework WHERE key != 'nist-800-171-r2'"
    _TEST_CTRL = f"SELECT id FROM control WHERE framework_id NOT IN ({_KEEP_FW})"
    _TEST_PRODS = f"SELECT id FROM product WHERE framework_id IN ({_TEST_FW})"

    deleted["baseline_evidence_spec (test)"] = _del(
        f"DELETE FROM baseline_evidence_spec WHERE baseline_control_id IN ("
        f"  SELECT id FROM baseline_control"
        f"  WHERE control_id IN ({_TEST_CTRL}) OR product_id IN ({_TEST_PRODS})"
        f")"
    )
    deleted["baseline_control (test)"] = _del(
        f"DELETE FROM baseline_control"
        f" WHERE control_id IN ({_TEST_CTRL}) OR product_id IN ({_TEST_PRODS})"
    )
    # Products that reference test frameworks must go before the framework rows.
    deleted["product (test fw)"] = _del(
        f"DELETE FROM product WHERE framework_id IN ({_TEST_FW})"
    )

    # ------------------------------------------------------------------ #
    # Tier 5 — test framework catalog rows                                #
    # ------------------------------------------------------------------ #

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
    # Tier 6 — test org data, scoped to non-Acme-MSP orgs only (unlike     #
    # Tiers 0-5 above, which are unconditional).                          #
    #                                                                      #
    # contact: has ON DELETE CASCADE on org_id, so this delete is          #
    #   belt-and-suspenders for clarity, not load-bearing.                 #
    # scope_entity: org_id is a plain column, not a foreign key at all —   #
    #   must be done manually or it would silently survive with a          #
    #   dangling org_id once the organization row is gone.                 #
    # audit_log: org_id has NO ON DELETE action (no CASCADE, no SET        #
    #   NULL) — deliberately: an append-only audit log must never let a    #
    #   row disappear as a side effect of deleting the org it references   #
    #   in production. That's exactly why this dev-only wipe utility is    #
    #   the right place to delete audit rows explicitly rather than        #
    #   adding a schema-level cascade — see the reset-dev command's        #
    #   docstring. Scoped to test orgs only: Acme MSP's own audit_log      #
    #   rows (org_id NOT IN this set) are never touched, and rows with     #
    #   org_id IS NULL (system-level events with no org) are untouched     #
    #   too, since NULL never matches an IN() list.                        #
    #                                                                      #
    # user / user_session / api_token / org_membership / system_description #
    #   all carry ON DELETE CASCADE on their org_id (or, for               #
    #   org_membership/api_token/user_session, transitively via user_id -> #
    #   user.id which itself cascades from organization) — verified        #
    #   2026-09-09 against the current schema. No explicit delete needed;  #
    #   they disappear for free the moment the organization row below is   #
    #   deleted. (system_description's own org_id cascades fine — its      #
    #   *diagram* FKs to evidence.id are the part that doesn't, handled    #
    #   in Tier 0 above.)                                                  #
    #                                                                      #
    # deployment_settings.msp_org_id has no ON DELETE action either, but   #
    #   is deliberately NOT handled here: it's a singleton naming which    #
    #   org IS this deployment's own MSP (set once at bootstrap), and on   #
    #   a correctly-bootstrapped dev box that's always 'Acme MSP' — never  #
    #   one of the test orgs this tier deletes. If that invariant is ever  #
    #   violated, the DELETE FROM organization below should fail loudly    #
    #   rather than this function silently reassigning deployment          #
    #   identity to paper over it.                                         #
    # ------------------------------------------------------------------ #
    _TEST_ORGS = "SELECT id FROM organization WHERE name != 'Acme MSP'"

    deleted["audit_log (test orgs)"] = _del(
        f"DELETE FROM audit_log WHERE org_id IN ({_TEST_ORGS})"
    )
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


@app.command(name="seed-baselines")
def seed_baselines_cmd(
    db_url: str = typer.Option(None, "--db-url", help="Override DATABASE_URL"),
) -> None:
    """Load product baselines from baselines/*.yaml into the database (idempotent)."""
    import os

    if db_url:
        os.environ["DATABASE_URL"] = db_url

    from .db import SessionLocal as _SL

    session = _SL()
    try:
        result = seed_baselines(session)
        session.commit()
        typer.echo(
            f"Baselines seeded: {result['products']} products, "
            f"{result['baseline_controls']} baseline controls, "
            f"{result['evidence_specs']} evidence specs."
        )
        if result["missing_controls"]:
            typer.echo(f"  Missing controls (not in catalog): {result['missing_controls']}")
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
