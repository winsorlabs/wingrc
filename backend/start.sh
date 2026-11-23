#!/bin/sh
# The backend's start, in the only order that works on a fresh install
# (app/app_role.py's docstring says why each step is here):
#   1. migrations, as the owner (WINGRC_MIGRATION_DATABASE_URL); migration
#      0016 creates wingrc_app with no password;
#   2. set wingrc_app's password from WINGRC_DATABASE_URL, as the owner,
#      only if the app role cannot already connect;
#   3. serve, as the app role.
# Extra arguments go to uvicorn (docker-compose.override.yml passes --reload).
set -eu
alembic upgrade head
python -m app.app_role
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 "$@"
