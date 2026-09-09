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
cp docker-compose.override.yml.example docker-compose.override.yml   # once
docker compose up --build         # full stack, live-reloading frontend
# or backend only:
cd backend && pip install -e ".[dev]" && pytest -q && ruff check .
```

`docker-compose.override.yml` (gitignored, auto-loaded by `docker compose up`
on top of `docker-compose.yml` — no extra flags needed) adds a Vite dev-server
frontend with your source bind-mounted in, so edits hot-reload. It's a local
contributor convenience only. A real deployment (e.g. wl-util-1) runs
`docker-compose.yml` alone, with no override — there the frontend is a static
production build served by nginx (see `docs/deployment.md`). Don't expect the
override to be present outside your own machine.

## Adding a CMMC list
Add a `ListView` to `backend/app/catalog.py` (filter + ordered columns). No new
table, no new spreadsheet — it renders from the existing scope graph.

## Adding an importer
Implement `source rows -> list[CanonicalEntity]` under
`backend/app/importers/`. Reconciliation and rendering are shared downstream.

## Branch hygiene

Delete a working branch — local and remote — in the same step as merging
it, not as a separate cleanup round later. This project merges most work
directly to `main` rather than through a PR (see the root `CLAUDE.md`'s
commit/push policy), so **GitHub's "automatically delete head branches"
setting won't catch these** — that only fires on PR merges, and most
branches here never go through one. Nothing else cleans them up, which is
exactly how five merged branches (`perf/get-current-user-threadpool`,
`fix/reset-dev-guard-fail-closed`, `fix/reset-dev-fk-order`,
`feature/device-asset-fields`, `network-data-flow-diagrams`) sat on GitHub
for weeks doing nothing before a dedicated cleanup pass found them.

Once a branch is merged into `main` and pushed:
```bash
git checkout main
git branch -d the-working-branch          # -d, not -D: refuses if not merged
git push origin --delete the-working-branch
```
If `-d` refuses, the branch has commits `main` doesn't — that's unmerged
work needing a decision, not something to force through.
