.PHONY: up down test lint sample fmt lock
up:        ## start full stack
	docker compose up --build
down:
	docker compose down
test:
	cd backend && pytest -q
lint:
	cd backend && ruff check .
sample:    ## regenerate the sanitized example workbook
	cd backend/.. && python scripts/make_example_workbook.py
lock:      ## re-resolve backend/requirements.lock: keeps every pin pyproject.toml still allows; UPGRADE="--upgrade-package X" moves one
	docker run --rm -v "$(CURDIR)/backend:/src" -w /src python:3.13-slim sh -c 'pip install -q uv==0.13.6 && uv pip compile pyproject.toml --extra dev --extra ai --group build --universal --python-version 3.13 --generate-hashes -o requirements.lock $(UPGRADE)'
