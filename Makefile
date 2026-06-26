.PHONY: up down test lint sample fmt
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
