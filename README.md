# WinGRC

**Open, community-owned CMMC scope and documentation tooling for MSPs.**

WinGRC flips the GRC-tool experience: instead of staring at a control catalog
and writing narratives by hand, you maintain your **scope** — the authorized
entities in the CUI boundary (who, what, where) — as a living source of truth,
and the required CMMC **lists** are projected from it on demand. Import from
Liongard, Datto RMM, or a spreadsheet; reconcile against reality; generate
assessor-ready artifacts with provenance.

It is a free, AGPL-licensed answer to the high-priced commercial GRC platforms.
Feature-matching, MSP-first, and built to run anywhere your data needs to live —
including GCC High and fully air-gapped.

> Status: early pilot. The **Scope module** (AC.L2-3.1.1 Authorized Entities)
> is the first vertical slice and the foundation everything else builds on.

## Why it's different

- **Scope graph as source of truth.** One normalized inventory of entities;
  every CMMC list is a *view*, not a hand-maintained spreadsheet. Maintain a
  device once and it flows everywhere.
- **Curated stack library.** Per-product control mappings (Heimdal, Datto,
  ThreatLocker, DUO, …) so selecting your stack pre-populates controls. The
  device "agents installed" columns are the join.
- **ESP responsibility matrix.** The external-services scope doubles as the
  MSP-to-client shared-responsibility documentation.
- **Import → reconcile → render.** Automated feeds never overwrite blindly —
  an engineer reviews the diff. Every record carries source + last-verified, so
  generated lists are defensible.
- **Deploy anywhere.** One container image: Docker, Azure Container Apps, GCC
  High, or air-gapped. Pluggable AI provider (Claude / Azure OpenAI / local) so
  CUI-sensitive tenants can keep generation in-house.

## Stack

React 19 (Vite) · FastAPI (Python 3.13) · PostgreSQL 18 + pgvector · SQLAlchemy
2.0 + Alembic · S3-compatible object storage (MinIO/Azure Blob/S3). See
[`docs/adr`](docs/adr) for the decisions and their rationale.

## Quickstart

### Run the stack
```bash
cp .env.example .env
docker compose up --build         # Postgres 18 + MinIO + API + frontend
# API:        http://localhost:8000/health
# API docs:   http://localhost:8000/docs
```

### Try the scope loop with the CLI (against the sample workbook)
```bash
cd backend
pip install -e .
# Import the sanitized sample, see the reconcile diff, apply it:
wingrc seed --org "Demo Co" --apply ../samples/authorized-entities.example.xlsx
# Inspect the live source of truth:
wingrc scope --org "Demo Co" --type device
# Generate an assessor-ready list from the scope graph:
wingrc render --org "Demo Co" 3.1.1c-authorized-devices ./devices.xlsx
```

`samples/authorized-entities.example.xlsx` contains fictional data only. Point
the importer at your own workbook to populate a real environment.

## Development

### First run
```bash
docker compose up --build   # builds the image, starts Postgres + MinIO + backend + frontend
```

The backend container mounts `./backend` into `/app`, so Python edits on the
host are live inside the container without a rebuild.  Uvicorn runs with
`--reload`, so it picks up file changes automatically.

Migrations run automatically at container startup (`alembic upgrade head` is
baked into the CMD).  After adding a new migration file, restart the backend
service to apply it:

```bash
docker compose restart backend
```

Or apply it without restarting:

```bash
docker compose exec backend alembic upgrade head
```

### Day-to-day
```bash
docker compose up           # no rebuild needed for Python/YAML changes
docker compose exec backend pytest -q                    # unit tests
docker compose exec backend pytest -q -m integration    # requires DB
docker compose exec backend wingrc seed-catalog         # load CMMC catalog
docker compose exec backend ruff check .                # lint
```

### Adding a migration
```bash
# Edit models.py, then autogenerate a revision:
docker compose exec backend alembic revision --autogenerate -m "describe the change"
# Review the generated file in backend/alembic/versions/, then restart to apply.
docker compose restart backend
```

### Running without Docker (local Postgres)
```bash
cd backend
pip install -e ".[dev]"
export WINGRC_DATABASE_URL=postgresql+psycopg://wingrc:wingrc@localhost:5432/wingrc
alembic upgrade head
uvicorn app.main:app --reload
```

## Repo layout
```
backend/      FastAPI app, domain core, importers, reconcile, render, CLI, migrations
frontend/     React 19 + Vite SPA (stub)
docs/adr/     Architecture decision records
samples/      Sanitized example workbook (import fixture)
scripts/      Tooling (example-workbook generator)
```

## License

AGPL-3.0-or-later. WinGRC is free to self-host; a hosted/sponsored tier and
vendor-sponsored stack-library mappings fund ongoing development. See
[`LICENSE`](LICENSE).
