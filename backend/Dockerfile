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
    libffi8 shared-mime-info \
    && rm -rf /var/lib/apt/lists/*

FROM base AS deps
COPY backend/pyproject.toml ./
RUN pip install --upgrade pip && pip install ".[dev]"

FROM base AS runtime
# Non-root user
RUN useradd --create-home --uid 10001 wingrc
COPY --from=deps /usr/local/lib/python3.13/site-packages /usr/local/lib/python3.13/site-packages
COPY --from=deps /usr/local/bin /usr/local/bin
COPY backend/ .
# Fixture data referenced by tests via parents[2] from /app/tests/
COPY samples/ /samples/
COPY baselines/ /baselines/
# Let the non-root user write cache files inside /app
RUN chown -R wingrc:wingrc /app
USER wingrc
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health').status==200 else 1)" || exit 1
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000"]
