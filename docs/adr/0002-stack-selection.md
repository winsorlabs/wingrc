# 2. Application stack

Date: 2026-06-26
Status: Accepted

## Context
WinGRC must be maintainable by a small team, secure, deployable anywhere
(commercial Azure, GCC High, on-prem, air-gapped), and easy to keep current as
component versions ship. It is AI- and data-pipeline-heavy.

## Decision
- **Backend:** Python 3.13 + FastAPI, SQLAlchemy 2.0, Alembic. Plays to the
  team's Python strength and the RAG/parsing workload; auto-generates the
  OpenAPI contract that types the frontend client.
- **Frontend:** React 19 + Vite (static SPA), shadcn/ui. Compiles to static
  files that run behind any server or fully air-gapped — no Node runtime
  required. (Next.js was the alternative; SSR/SEO buys nothing in an
  authenticated tool and would couple us to a Node server.)
- **Database:** PostgreSQL 18 + pgvector. JSONB for variable attributes,
  full-text search, vector search for grounded generation, Row-Level Security
  for tenant isolation — one engine instead of bolting on a vector store.
- **Object storage:** S3-compatible interface (MinIO self-host / Azure Blob /
  S3 cloud), same code path.
- **Packaging:** one container image; Azure Container Apps for the cloud
  "deploy to your tenant" story, Docker/compose for self-host. Not Azure
  Functions — that fits an API-proxy workload (CIPP), not our stateful one.

## Consequences
Boring, long-support tech with one update path. Renovate + Alembic + pinned LTS
runtimes (Node 24, Python 3.13, Postgres 18) keep upkeep low.
