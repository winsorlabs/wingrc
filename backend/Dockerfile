FROM python:3.13-slim AS base
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

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
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
