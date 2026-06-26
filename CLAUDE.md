# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

WinGRC is open CMMC scope and documentation tooling for MSPs. The core idea: maintain a **scope graph** (authorized entities in the CUI boundary) as the single source of truth, and project every required CMMC list from it on demand. Entities are never overwritten blindly — every import goes through an explicit reconcile diff that an engineer reviews before applying.

The first vertical slice is the **Scope module** (AC.L2-3.1.1 Authorized Entities), covering users, processes, devices, and external services.

## Commands

### Backend (run from `backend/`)

```bash
# Install with dev deps
pip install -e ".[dev]"

# Run all tests
pytest -q

# Run a single test
pytest -q tests/test_scope_loop.py::test_reconcile_detects_new_and_missing

# Lint
ruff check .

# Apply pending migrations
alembic upgrade head

# Generate a new migration after model changes
alembic revision --autogenerate -m "describe change"
```

### CLI (after `pip install -e .` from `backend/`)

```bash
wingrc seed --org "Demo Co" --apply ../samples/authorized-entities.example.xlsx
wingrc scope --org "Demo Co" --type device
wingrc render --org "Demo Co" 3.1.1c-authorized-devices ./out.xlsx
wingrc views   # list all available CMMC list view IDs
```

### Full stack

```bash
cp .env.example .env
docker compose up --build    # Postgres 18 + MinIO + API (port 8000) + frontend (port 5173)
docker compose down
```

### Frontend (from `frontend/`)

```bash
npm install
npm run dev      # dev server on port 5173
npm run build    # tsc + vite build
```

### Regenerate sample workbook

```bash
python scripts/make_example_workbook.py   # from repo root
```

## Architecture

### Data flow

```
Source (workbook / CSV / Liongard / Datto RMM)
  └─> importer  ->  List[CanonicalEntity]
        └─> reconcile()  ->  ReconcileResult  (reviewed by engineer)
              └─> repo.upsert()  ->  scope_entity table  (Postgres)
                    └─> render_view()  ->  assessor-ready .xlsx
```

### Key modules (`backend/app/`)

| File | Role |
|---|---|
| `domain.py` | Pure domain core — `CanonicalEntity`, `EntityType`, `ScopeCategory`, `ReconcileResult`. **No DB or web imports.** Unit-testable in isolation. |
| `catalog.py` | `ListView` definitions — each represents one CMMC list (a filter + column set). Adding a new required list = adding a `ListView` here. |
| `reconcile.py` | `reconcile(current, incoming)` → `ReconcileResult` diff. Nothing writes to the DB; this is always a pure compare. |
| `render.py` | `render_view(view, entities, out_path)` → `.xlsx` with provenance header. |
| `repo.py` | SQLAlchemy ↔ domain adapter. The only file that maps between `ScopeEntity` rows and `CanonicalEntity` objects. |
| `models.py` | SQLAlchemy 2.0 models: `Organization` and `ScopeEntity`. Everything in one `scope_entity` table — variable attributes in JSONB, structured query fields as real columns. |
| `main.py` | FastAPI app. Exposes: `GET /health`, `GET /catalog/views`, `GET /orgs/{id}/scope`, `POST /orgs/{id}/imports/workbook/dry-run`, `POST /orgs/{id}/exports/{view_id}`. |
| `cli.py` | Typer CLI: `seed`, `scope`, `render`, `views`. Uses the same importers/reconcile/render/repo as the API. |
| `config.py` | Pydantic-settings config. All env vars are `WINGRC_`-prefixed. |
| `importers/workbook.py` | xlsx importer — parses the four Authorized-Entities tabs into `CanonicalEntity` records. Future importers (CSV, Liongard, Datto RMM) follow the same contract: source rows → `List[CanonicalEntity]`. |

### Database schema

Single `scope_entity` table. Structured query fields (`entity_type`, `scope_category`, `status`, `source`, `org_id`) are real columns with indexes. Variable per-entity payload lives in a JSONB `attributes` column. Per-tenant isolation via `org_id` + Postgres Row-Level Security (set in migrations).

### Natural keys

- **Person:** `"First Name Last Name"` (concatenated)
- **Process:** `Process Name`
- **Device:** `Serial # or Asset Tag` (falls back to `Name`)
- **External Service:** `Name`

Natural keys are normalized to lowercase+stripped for reconciler comparison.

### View IDs (for the CLI and API)

- `3.1.1a-authorized-users`
- `3.1.1b-auth-processes`
- `3.1.1c-authorized-devices`
- `external-services`

### Environment variables

All prefixed `WINGRC_`. Defaults target the docker-compose Postgres instance.

| Variable | Default | Purpose |
|---|---|---|
| `WINGRC_DATABASE_URL` | `postgresql+psycopg://wingrc:wingrc@localhost:5432/wingrc` | Connection string |
| `WINGRC_ENVIRONMENT` | `development` | Runtime environment label |
| `WINGRC_AI_PROVIDER` | `none` | `none \| anthropic \| azure_openai \| local` (scope module doesn't use AI yet) |

### CI pipeline (`.github/workflows/ci.yml`)

On push/PR to `main`: lint with ruff → test with pytest → build container image → Trivy vulnerability scan → Syft SBOM generation.

## Important patterns

- **Imports never overwrite blindly.** `reconcile()` always runs first and returns a diff. The CLI `--apply` flag and the API's separate apply endpoint are the only paths that write.
- **Domain core is DB-free.** `domain.py` and `reconcile.py` have no SQLAlchemy or FastAPI imports. Tests for parse/reconcile/render run without a database.
- **`attributes` round-trips faithfully.** Importers store raw workbook column headers as keys in `attributes`. Renderers read those same keys back out. Don't normalize attribute keys — this is intentional.
- **Multi-tenant by `org_id`.** Every query scopes to `org_id`. The `get_or_create_org` helper in `repo.py` resolves name → UUID.
- **Ruff config:** `line-length = 90`, rules `E F I UP B`, target `py313`. Run `ruff check .` before committing.
