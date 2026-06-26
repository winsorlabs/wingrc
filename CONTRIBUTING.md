# Contributing to WinGRC

WinGRC is built for the MSP community. Contributions — especially curated
stack-library mappings for security products — are welcome.

## Ground rules
- Licensed AGPL-3.0-or-later. By contributing you agree your work is licensed
  the same way.
- Never commit real customer environment data. The only sample data in the repo
  is the fictional `samples/authorized-entities.example.xlsx`.
- Never commit secrets. Use `.env` (gitignored) locally.

## Dev setup
```bash
cp .env.example .env
docker compose up --build         # full stack
# or backend only:
cd backend && pip install -e ".[dev]" && pytest -q && ruff check .
```

## Adding a CMMC list
Add a `ListView` to `backend/app/catalog.py` (filter + ordered columns). No new
table, no new spreadsheet — it renders from the existing scope graph.

## Adding an importer
Implement `source rows -> list[CanonicalEntity]` under
`backend/app/importers/`. Reconciliation and rendering are shared downstream.
