FROM python:3.13-slim AS base
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
# WeasyPrint (SSP PDF export, docs/pdf_ssp_template_spec.md) needs Pango/
# cairo/gdk-pixbuf at import time — not pure-Python wheels, so they must be
# system packages. Installed on `base` (not just `deps`) because `runtime`
# is FROM base and only inherits the deps stage's Python site-packages/bin,
# not its apt state.
RUN apt-get update && apt-get install --no-install-recommends -y \
    libpango-1.0-0 libpangocairo-1.0-0 libcairo2 libgdk-pixbuf-2.0-0 \
    libffi8 shared-mime-info libpq5 \
    && rm -rf /var/lib/apt/lists/*

# Source of a `pg_dump` binary that actually matches docker-compose.yml's
# pinned `postgres:18` — Debian trixie's own apt repos only carry
# postgresql-client 17 (confirmed via apt-cache madison), and pg_dump
# refuses to run against a server newer than itself. Adding the PGDG apt
# repo would fix the version but needs network access to postgresql.org at
# build time, which air-gapped deployments (a hard requirement — see
# CLAUDE.md) can't do. postgres:18 is already a required image for this
# stack (the `db` service), so copying its pg_dump out introduces no new
# external dependency. libpq5 above (Debian's own, still version 17) is
# still ABI-compatible — libpq's soname has been stable across major PG
# versions for a long time; verified by running the copied binary.
FROM postgres:18 AS pgtools

FROM base AS deps
COPY backend/pyproject.toml ./
RUN pip install --upgrade pip && pip install ".[dev]"

FROM base AS runtime
COPY --from=pgtools /usr/lib/postgresql/18/bin/pg_dump /usr/local/bin/pg_dump
# pg_restore too, verified working the same way (real dump, real restore
# into a scratch database) -- NOT psql: it links against newer libpq
# symbols (PQfullProtocolVersion) that Debian trixie's own libpq5 package
# doesn't export, and fails at startup with a symbol-lookup error. pg_dump
# and pg_restore don't hit that gap in practice (verified), so they stay;
# psql's absence is why reset-dev's own recovery instructions below use
# the `db` service's own bundled psql (an exact-version match, since it's
# the same postgres:18 image) for the one step that needs it.
COPY --from=pgtools /usr/lib/postgresql/18/bin/pg_restore /usr/local/bin/pg_restore
# Non-root user
RUN useradd --create-home --uid 10001 wingrc
COPY --from=deps /usr/local/lib/python3.13/site-packages /usr/local/lib/python3.13/site-packages
COPY --from=deps /usr/local/bin /usr/local/bin
COPY backend/ .
# Fixture data referenced by tests via parents[2] from /app/tests/
COPY samples/ /samples/
COPY baselines/ /baselines/
# reset-dev's pre-flight pg_dump lands here (WINGRC_RESET_DEV_BACKUP_DIR
# overrides for tests) — a dedicated, non-source directory so backup files
# never land inside the bind-mounted, git-tracked ./backend on a dev box.
RUN mkdir -p /backups && chown wingrc:wingrc /backups
# Let the non-root user write cache files inside /app
RUN chown -R wingrc:wingrc /app
USER wingrc
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health').status==200 else 1)" || exit 1
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000"]
