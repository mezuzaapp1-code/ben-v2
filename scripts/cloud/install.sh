#!/usr/bin/env bash
# Cloud Agent install: idempotent bootstrap for the BEN dev environment.
# Prepares system packages, a local PostgreSQL, the Python venv + deps,
# the frontend npm deps, a local .env, and the database schema.
# Safe to run repeatedly (converges without duplicating state).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

PG_VERSION=16
LOCAL_DB_URL="postgresql+asyncpg://ben:ben@127.0.0.1:5432/ben"
ALEMBIC="alembic -c database/migrations/alembic.ini"

echo "[install] repo root: $ROOT"

# --- System packages (only if missing) ---
if ! command -v pg_ctlcluster >/dev/null 2>&1; then
  echo "[install] installing system packages (postgresql, python venv, build tools)"
  sudo apt-get update -qq
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
    postgresql postgresql-contrib python3-venv python3-dev libpq-dev build-essential
fi

# --- PostgreSQL cluster + role/database (idempotent) ---
sudo pg_ctlcluster "$PG_VERSION" main start 2>/dev/null || true
for _ in $(seq 1 30); do
  pg_isready -h 127.0.0.1 -p 5432 >/dev/null 2>&1 && break
  sleep 1
done
sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='ben'" | grep -q 1 \
  || sudo -u postgres psql -c "CREATE USER ben WITH PASSWORD 'ben' SUPERUSER;"
sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='ben'" | grep -q 1 \
  || sudo -u postgres psql -c "CREATE DATABASE ben OWNER ben;"

# --- Python virtualenv + dependencies ---
[ -d .venv ] || python3 -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install --upgrade pip -q
pip install -q -r requirements.txt -r requirements-dev.txt

# --- Local .env (created once; real secrets injected as env vars still win) ---
if [ ! -f .env ]; then
  echo "[install] writing local .env"
  cat > .env <<'ENVEOF'
# Local Cloud Agent dev defaults. Real values provided as Cloud secrets
# (env vars) take precedence — python-dotenv does not override existing env.
DATABASE_URL=postgresql+asyncpg://ben:ben@127.0.0.1:5432/ben
# Startup requires these to be present (not validated for correctness locally).
OPENAI_API_KEY=sk-local-placeholder
ANTHROPIC_API_KEY=sk-ant-local-placeholder
ANTHROPIC_MODEL=claude-opus-4.8
# Auth disabled for local dev (documented default).
ENFORCE_AUTH=false
AUTH_SHADOW_MODE=true
CLERK_SECRET_KEY=sk_test_clerk_local
BEN_ANONYMOUS_ORG_ID=00000000-0000-0000-0000-000000000001
BEN_LOCAL_BETA_MODE=false
ENVEOF
fi

# --- Database schema (idempotent baseline) ---
# NOTE: migration 001_initial_schema calls Base.metadata.create_all(), which
# builds the FULL current model. The later incremental migrations therefore
# collide on a fresh DB (`upgrade head` fails with DuplicateTableError). The
# correct fresh-DB initialization is: apply 001 (builds the complete current
# schema) and then stamp the DB at head.
(
  export DATABASE_URL="$LOCAL_DB_URL"
  # `alembic heads` loads the version files (which import `database.models`)
  # without running env.py, so the repo root must be on PYTHONPATH.
  export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
  HEAD="$($ALEMBIC heads 2>/dev/null | awk 'NR==1{print $1}')"
  CUR="$($ALEMBIC current 2>/dev/null | awk 'NR==1{print $1}')"
  if [ -n "$HEAD" ] && [ "$CUR" = "$HEAD" ]; then
    echo "[install] database already at head ($HEAD)"
  else
    SCHEMA_READY="$(PGPASSWORD=ben psql -h 127.0.0.1 -U ben -d ben -tAc \
      "SELECT to_regclass('ben.threads') IS NOT NULL" 2>/dev/null || echo f)"
    if [ "$SCHEMA_READY" = "t" ]; then
      echo "[install] schema present; stamping alembic head"
      $ALEMBIC stamp head
    else
      echo "[install] initializing fresh schema (upgrade 001 + stamp head)"
      $ALEMBIC upgrade 001_initial_schema
      $ALEMBIC stamp head
    fi
  fi
)

# --- Frontend dependencies ---
( cd frontend && npm ci )

echo "[install] done"
