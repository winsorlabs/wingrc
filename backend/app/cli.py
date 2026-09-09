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

# reset-dev's production guard (see _reset_dev_guard_error below) allowlists
# exactly this codebase's established two-value WINGRC_ENVIRONMENT
# convention (config.py's Settings.environment default, .env.example,
# auth.py's cookie-Secure check) -- "development" or "production", nothing
# else. No "dev"/"test"/"local"/"staging" has ever been used anywhere in
# this repo, so the allowlist doesn't invent one either.
_RESET_DEV_ALLOWED_ENVIRONMENTS = frozenset({"development"})


def _reset_dev_guard_error(raw_env: str | None) -> str | None:
    """Return an actionable refusal message if `raw_env` -- the raw
    WINGRC_ENVIRONMENT value read straight from the environment, or None if
    the variable is unset -- doesn't clear reset-dev's safety allowlist.
    Returns None if it's safe to proceed.

    Fails CLOSED: unset, "production", and any unrecognized or misspelled
    value all refuse -- only an exact (case/whitespace-insensitive) match
    against _RESET_DEV_ALLOWED_ENVIRONMENTS proceeds. This deliberately
    reads raw os.environ rather than config.get_settings().environment:
    Settings.environment defaults to "development" whenever the variable
    is entirely unset, which would silently treat "nobody configured this
    on a fresh production deploy" -- the most likely real-world way to hit
    this -- as if it were a real dev box.
    """
    normalized = (raw_env or "").strip().lower()
    if normalized in _RESET_DEV_ALLOWED_ENVIRONMENTS:
        return None
    shown = repr(raw_env) if raw_env is not None else "unset"
    allowed = sorted(_RESET_DEV_ALLOWED_ENVIRONMENTS)
    return (
        f"Refusing to run: WINGRC_ENVIRONMENT is {shown}, but reset-dev "
        f"only runs when it's set to one of {allowed!r}. reset-dev "
        "irreversibly deletes assessment data and audit history -- an "
        "unset or unrecognized environment must refuse, not proceed. Set "
        "WINGRC_ENVIRONMENT=development for a normal dev/test setup."
    )


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


_TEST_ORGS = "SELECT id FROM organization WHERE name != 'Acme MSP'"


def _add_where(sql: str, extra: str) -> str:
    """Append an additional WHERE condition to a DELETE/UPDATE statement
    that may or may not already have one. `extra` is a bare boolean
    expression (e.g. "org_id IN (...)"), not a full clause."""
    if not extra:
        return sql
    return f"{sql} AND {extra}" if " WHERE " in sql else f"{sql} WHERE {extra}"


def _org_scope(orgs_only: bool, column: str = "org_id") -> str:
    """Boolean expression restricting a directly org_id-owning row to test
    orgs, or "" (no restriction — every org) when orgs_only is False.
    Passed to _add_where. `column` lets a table whose org_id column is
    reached via a join (rather than being its own column) substitute the
    joined expression, e.g. "control_state_id IN (SELECT id FROM
    control_state WHERE org_id IN (...))"."""
    if not orgs_only:
        return ""
    if column == "org_id":
        return f"org_id IN ({_TEST_ORGS})"
    return f"{column} IN (SELECT id FROM control_state WHERE org_id IN ({_TEST_ORGS}))"


def _as_count_sql(sql: str) -> str:
    """Turn one _reset_dev_plan() DELETE/UPDATE statement into the
    equivalent SELECT COUNT(*) over the same rows it would affect — string
    surgery over the same SQL, not an independently maintained list, so a
    preview count can never drift from what _reset_dev() actually deletes.
    Every current plan entry is a plain `DELETE FROM <table> [WHERE ...]`
    or `UPDATE <table> SET ... [WHERE ...]` — no joins, no RETURNING — so
    splitting on the first " WHERE "/" SET " is unambiguous even though
    some WHERE clauses embed their own nested subquery WHEREs (the first
    occurrence in the string is always the outer one, since it's written
    first)."""
    if sql.startswith("DELETE FROM "):
        rest = sql[len("DELETE FROM ") :]
        table, _, where = rest.partition(" WHERE ")
        return f"SELECT COUNT(*) FROM {table.strip()}" + (f" WHERE {where}" if where else "")
    if sql.startswith("UPDATE "):
        rest = sql[len("UPDATE ") :]
        table = rest.split(" SET ", 1)[0].strip()
        _, _, where = rest.partition(" WHERE ")
        return f"SELECT COUNT(*) FROM {table}" + (f" WHERE {where}" if where else "")
    raise ValueError(f"Don't know how to count for: {sql!r}")


def _reset_dev_plan(orgs_only: bool = False) -> list[tuple[str, str, str]]:
    """The single source of truth for what reset-dev does: a list of
    (label, kind, sql) tuples in FK-safe order, where kind is "DELETE" or
    "UPDATE". Both _reset_dev() (executes them) and _preview_counts()
    (counts what they would affect, via _as_count_sql) build from this one
    plan, so the numbers a user confirms against are mechanically
    guaranteed to match what actually runs — not a hand-maintained
    parallel description that can go stale (see this command's own
    misleading-confirmation incident).

    Keeps: framework key='nist-800-171-r2' and org name='Acme MSP'.
    Tiers 0-3 (the assessment-layer tables) are unconditional by default —
    Acme MSP's assessment data is wiped alongside every test org's, which
    is exactly the behavior that surprised the person running this. Pass
    orgs_only=True to scope Tiers 0-3 to test orgs only instead (see
    --orgs-only on the reset-dev command), leaving every org's assessment
    layer — including the kept org's — untouched. Tiers 4-5 (test
    framework/product/baseline catalog rows) and Tier 6 (test org
    identity: contact/scope_entity/organization/audit_log) are unaffected
    by orgs_only — they were already scoped to "not Acme MSP" (Tier 6) or
    org-independent (Tiers 4-5) before this option existed.
    """
    scope_org = _org_scope(orgs_only)  # system_description has org_id directly
    scope_cs = _org_scope(orgs_only, "control_state_id")  # via control_state join
    plan: list[tuple[str, str, str]] = []

    # ------------------------------------------------------------------ #
    # Tier 0 — clear FK pointers that would block Tier 3's evidence wipe. #
    # system_description's pinned diagram slots (migration 0029) point at #
    # evidence.id with no ON DELETE action, and system_description itself #
    # is never deleted here (it only cascades away when its owning org is #
    # deleted — which never happens for Acme MSP, and Tier 3 wipes        #
    # `evidence` for every affected org). Null the pointers first so the  #
    # evidence delete below doesn't hit a foreign-key violation.          #
    # ------------------------------------------------------------------ #
    plan.append((
        "system_description (diagram slots cleared)", "UPDATE",
        _add_where(
            "UPDATE system_description SET network_diagram_evidence_id = NULL, "
            "data_flow_diagram_evidence_id = NULL "
            "WHERE network_diagram_evidence_id IS NOT NULL "
            "OR data_flow_diagram_evidence_id IS NOT NULL",
            scope_org,
        ),
    ))

    # ------------------------------------------------------------------ #
    # Tier 1 — junction/leaf tables: no other table FKs point at them     #
    # ------------------------------------------------------------------ #
    plan.append(("raci_assignment", "DELETE",
        _add_where("DELETE FROM raci_assignment", scope_cs)))
    plan.append(("evidence_state_link", "DELETE",
        _add_where("DELETE FROM evidence_state_link", scope_cs)))
    # References evidence_task.id AND control_state.id, neither ON DELETE
    # CASCADE — must go before both (Tier 2's evidence_task, Tier 3's
    # control_state) or their deletes below raise a foreign-key violation.
    plan.append(("evidence_task_state_link", "DELETE",
        _add_where("DELETE FROM evidence_task_state_link", scope_cs)))

    # ------------------------------------------------------------------ #
    # Tier 2 — tables that reference control_state                        #
    # ------------------------------------------------------------------ #
    plan.append(("control_state_history", "DELETE",
        _add_where("DELETE FROM control_state_history", scope_cs)))
    plan.append(("evidence_task", "DELETE",
        _add_where("DELETE FROM evidence_task", scope_org)))
    # poa_m_item.finding_id -> finding.id has no ON DELETE action, so
    # poa_m_item must be deleted before finding, not after (the reverse of
    # the order these two used to run in — deleting finding first raised a
    # foreign-key violation the moment a poa_m_item referenced it).
    plan.append(("poa_m_item", "DELETE",
        _add_where("DELETE FROM poa_m_item", scope_org)))
    plan.append(("finding", "DELETE",
        _add_where("DELETE FROM finding", scope_org)))
    plan.append(("implementation_statement", "DELETE",
        _add_where("DELETE FROM implementation_statement", scope_org)))
    plan.append(("sprs_snapshot", "DELETE",
        _add_where("DELETE FROM sprs_snapshot", scope_org)))

    # ------------------------------------------------------------------ #
    # Tier 3 — core assessment tables                                     #
    # ------------------------------------------------------------------ #
    plan.append(("control_state", "DELETE",
        _add_where("DELETE FROM control_state", scope_org)))
    plan.append(("assessment", "DELETE",
        _add_where("DELETE FROM assessment", scope_org)))
    plan.append(("org_product", "DELETE",
        _add_where("DELETE FROM org_product", scope_org)))
    plan.append(("evidence", "DELETE",
        _add_where("DELETE FROM evidence", scope_org)))

    # ------------------------------------------------------------------ #
    # Tier 4 — FK safety: remove baseline rows and test products before   #
    # deleting test framework catalog rows in Tier 5. Org-independent —   #
    # unaffected by orgs_only. "Test products" = any product whose        #
    # framework_id points at a non-production framework (created by       #
    # integration tests).                                                 #
    # ------------------------------------------------------------------ #
    _KEEP_FW = "SELECT id FROM framework WHERE key = 'nist-800-171-r2'"
    _TEST_FW = "SELECT id FROM framework WHERE key != 'nist-800-171-r2'"
    _TEST_CTRL = f"SELECT id FROM control WHERE framework_id NOT IN ({_KEEP_FW})"
    _TEST_PRODS = f"SELECT id FROM product WHERE framework_id IN ({_TEST_FW})"

    plan.append(("baseline_evidence_spec (test)", "DELETE",
        f"DELETE FROM baseline_evidence_spec WHERE baseline_control_id IN ("
        f"  SELECT id FROM baseline_control"
        f"  WHERE control_id IN ({_TEST_CTRL}) OR product_id IN ({_TEST_PRODS})"
        f")"))
    plan.append(("baseline_control (test)", "DELETE",
        f"DELETE FROM baseline_control"
        f" WHERE control_id IN ({_TEST_CTRL}) OR product_id IN ({_TEST_PRODS})"))
    # Products that reference test frameworks must go before the framework rows.
    plan.append(("product (test fw)", "DELETE",
        f"DELETE FROM product WHERE framework_id IN ({_TEST_FW})"))

    # ------------------------------------------------------------------ #
    # Tier 5 — test framework catalog rows. Org-independent.               #
    # ------------------------------------------------------------------ #
    plan.append(("assessment_objective (test fw)", "DELETE",
        f"DELETE FROM assessment_objective WHERE control_id IN ("
        f"  SELECT id FROM control WHERE framework_id IN ({_TEST_FW})"
        f")"))
    plan.append(("control (test fw)", "DELETE",
        f"DELETE FROM control WHERE framework_id IN ({_TEST_FW})"))
    plan.append(("framework (test)", "DELETE",
        "DELETE FROM framework WHERE key != 'nist-800-171-r2'"))

    # ------------------------------------------------------------------ #
    # Tier 6 — test org data, scoped to non-Acme-MSP orgs only regardless #
    # of orgs_only (that's already exactly what orgs_only asks for: test  #
    # orgs go away entirely; the kept org's identity never has).          #
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
    plan.append(("audit_log (test orgs)", "DELETE",
        f"DELETE FROM audit_log WHERE org_id IN ({_TEST_ORGS})"))
    plan.append(("contact (test orgs)", "DELETE",
        f"DELETE FROM contact WHERE org_id IN ({_TEST_ORGS})"))
    plan.append(("scope_entity (test orgs)", "DELETE",
        f"DELETE FROM scope_entity WHERE org_id IN ({_TEST_ORGS})"))
    plan.append(("organization (test)", "DELETE",
        "DELETE FROM organization WHERE name != 'Acme MSP'"))

    return plan


def _reset_dev(session, orgs_only: bool = False) -> dict[str, int]:
    """Execute _reset_dev_plan() in order, returning {label: rows_affected}."""
    deleted: dict[str, int] = {}
    for label, _kind, sql in _reset_dev_plan(orgs_only):
        r = session.execute(text(sql))
        session.flush()
        deleted[label] = r.rowcount
    return deleted


def _preview_counts(session, orgs_only: bool = False) -> dict[str, int]:
    """SELECT COUNT(*) for every _reset_dev_plan() entry, without executing
    it — what the operator sees before confirming. See _as_count_sql for
    why this can't drift from what _reset_dev() actually deletes."""
    return {
        label: session.execute(text(_as_count_sql(sql))).scalar()
        for label, _kind, sql in _reset_dev_plan(orgs_only)
    }


def _keep_summary(session) -> dict[str, int]:
    """Counts backing the 'Will KEEP' line — Acme MSP's own identity data,
    which no tier ever touches. Computed fresh each run, same reasoning as
    _preview_counts: a hand-written description of "what's safe" is
    exactly the kind of thing that goes stale (see this command's own
    misleading confirmation-wording incident)."""
    acme_id = session.execute(
        text("SELECT id FROM organization WHERE name = 'Acme MSP'")
    ).scalar()
    if acme_id is None:
        return {"contacts": 0, "scope_entities": 0}
    return {
        "contacts": session.execute(
            text("SELECT count(*) FROM contact WHERE org_id = :id"), {"id": acme_id}
        ).scalar(),
        "scope_entities": session.execute(
            text("SELECT count(*) FROM scope_entity WHERE org_id = :id"), {"id": acme_id}
        ).scalar(),
    }


def _format_preview(counts: dict[str, int], keep: dict[str, int], orgs_only: bool) -> str:
    """The dry-run-style preview shown before the confirm prompt (or, under
    --yes, this is skipped — but _format_result below still prints
    unconditionally). Real counts from the actual database, not a static
    description — see this command's own incident for why that matters:
    a static "keeping org X" line reads as "X is safe," which was false."""
    width = max((len(label) for label, _ in counts.items()), default=0)
    lines = ["Will DELETE:"]
    lines += [f"  {label:<{width}}  {n}" for label, n in counts.items()]
    lines.append("")
    if orgs_only:
        lines.append(
            "--orgs-only: this deletes test orgs entirely (identity + their own "
            "assessment layer) but does NOT touch any other org's assessments, "
            "control states, evidence, or findings — including the kept org's."
        )
    else:
        lines.append(
            "This deletes assessments, control states, evidence, findings, and "
            "RACI/diagram data for EVERY org, including 'Acme MSP' — not just "
            "test orgs. Acme MSP's identity is kept; its assessment-layer work "
            "is not. Use --orgs-only to keep every org's assessment layer and "
            "remove only test orgs."
        )
    lines.append("")
    lines.append("Will KEEP:")
    lines.append("  framework 'nist-800-171-r2' (CMMC L2 catalog, re-seeded after wipe)")
    lines.append(
        f"  org 'Acme MSP' (profile, {keep['contacts']} contacts, "
        f"{keep['scope_entities']} scope entities)"
    )
    return "\n".join(lines)


def _format_result(deleted: dict[str, int]) -> str:
    width = max((len(label) for label in deleted), default=0)
    lines = ["Rows affected:"]
    lines += [f"  {label:<{width}}  {n}" for label, n in deleted.items()]
    return "\n".join(lines)


def _backup_dir() -> Path:
    import os

    return Path(os.environ.get("WINGRC_RESET_DEV_BACKUP_DIR", "/backups"))


def _preflight_backup(db_url) -> Path:
    """pg_dump the target database to a timestamped file before
    _reset_dev() touches anything. Raises RuntimeError on any failure
    (including pg_dump not being on PATH) — the caller must treat that as
    fatal and skip the wipe entirely: no backup, no wipe.

    Custom format (pg_restore-loadable), not plain SQL: the documented
    recovery path for a bad reset-dev run is "load it into a scratch
    database, then selectively copy the kept org's rows across" — never a
    wholesale restore over the live dev DB, which would roll back
    unrelated work and clobber the append-only audit log — and
    pg_restore's -t/-n filtering makes that far more tractable than
    grepping a plain-text dump.
    """
    import subprocess
    from datetime import UTC, datetime

    backup_dir = _backup_dir()
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    dest = backup_dir / f"reset-dev-{timestamp}.dump"
    pg_url = db_url.set(drivername="postgresql").render_as_string(hide_password=False)

    try:
        result = subprocess.run(
            ["pg_dump", "--format=custom", "--file", str(dest), pg_url],
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as e:
        raise RuntimeError(f"pg_dump not found on PATH: {e}") from e

    if result.returncode != 0 or not dest.exists():
        raise RuntimeError(
            f"pg_dump failed (exit {result.returncode}): {result.stderr.strip()}"
        )
    return dest


@app.command(name="reset-dev")
def reset_dev(
    yes: bool = typer.Option(False, "--yes", help="Skip confirmation prompt"),
    orgs_only: bool = typer.Option(
        False,
        "--orgs-only",
        help=(
            "Delete test orgs (identity + their own assessment layer) only — "
            "every other org's assessments/control states/evidence/findings, "
            "including the kept org's, are left untouched. Use this for the "
            "common case (cleaning up a stray verification org) instead of "
            "the full reset."
        ),
    ),
) -> None:
    """Restore the dev DB to a clean state: CMMC L2 catalog + 'Acme MSP' org,
    with Acme MSP's own assessment layer wiped alongside every test org's
    unless --orgs-only is given.

    Deletes all test-framework rows and test-org rows that integration tests
    leave behind when they commit against the dev database.  Safe to run
    repeatedly; the catalog seed step is idempotent.

    A pre-flight `pg_dump` of the whole target database is taken before
    anything is deleted, every time, regardless of --yes or --orgs-only —
    see _preflight_backup. If the dump fails, this command aborts without
    deleting anything.

    NEVER run this against a production database. Enforced below, not just
    documented — `--yes` skips the confirmation prompt but never bypasses
    the environment allowlist check (fails closed: unset, "production", or
    any unrecognized value all refuse — see _reset_dev_guard_error).
    """
    import os

    from sqlalchemy import select

    guard_error = _reset_dev_guard_error(os.environ.get("WINGRC_ENVIRONMENT"))
    if guard_error is not None:
        typer.echo(guard_error)
        raise typer.Exit(code=1)

    session = SessionLocal()
    try:
        # .engine.url, not .url directly: get_bind() returns an Engine in
        # production (SessionLocal is Engine-bound), but a Connection when
        # a caller binds a Session directly to one (as the test suite's
        # db_session fixture does, for its per-test savepoint isolation) --
        # Connection has no .url of its own, only .engine.url. Both Engine
        # and Connection expose .engine (an Engine's .engine is itself), so
        # this one line handles both without caring which it got.
        db_url = session.get_bind().engine.url  # type: ignore[attr-defined]
        counts = _preview_counts(session, orgs_only)
        keep = _keep_summary(session)

        if not yes:
            typer.echo(f"Target database: {db_url}")
            typer.echo(_format_preview(counts, keep, orgs_only))
            typer.confirm("Proceed?", abort=True)

        try:
            backup_path = _preflight_backup(db_url)
        except RuntimeError as e:
            typer.echo(f"Pre-flight backup failed — aborting WITHOUT deleting anything: {e}")
            raise typer.Exit(code=1) from e
        # The password is echoed here (unlike the masked "Target database"
        # line above) because these commands are unusable without it and
        # reset-dev only ever runs in "development" -- the guard above
        # refuses everywhere else -- where this is docker-compose.yml's
        # fixed, already-in-source-control default credential, not a
        # per-user secret.
        typer.echo(
            f"Pre-flight backup written to {backup_path}\n"
            f"  To inspect or selectively recover from it, load it into a NEW,\n"
            f"  explicitly-named scratch database first -- never restore it over\n"
            f"  this live one (that would roll back everything committed since the\n"
            f"  dump and clobber the append-only audit log), and don't pass -C to\n"
            f"  pg_restore for this either: -C creates/targets the dump's own\n"
            f"  embedded database name, not whatever scratch name you ask for.\n"
            f"  From the host (this container has pg_restore but not psql):\n"
            f"    docker compose exec db psql -U {db_url.username} -d postgres"
            f" -c 'CREATE DATABASE wingrc_scratch'\n"
            f"  Then, from inside this container:\n"
            f"    PGPASSWORD={db_url.password} pg_restore -h {db_url.host}"
            f" -p {db_url.port} -U {db_url.username} -d wingrc_scratch {backup_path}"
        )

        deleted = _reset_dev(session, orgs_only)

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

        # Printed unconditionally — --yes skips the confirmation prompt,
        # not the record of what this run actually destroyed.
        typer.echo(
            f"\nDev DB reset complete.\n"
            f"  Catalog: {result['controls']} controls, {result['objectives']} objectives\n"
            + _format_result(deleted)
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
