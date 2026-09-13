#!/usr/bin/env bash
# Cloud Agent start: per-boot reconciliation. Ensures the local PostgreSQL
# cluster is running and accepting connections, then returns. The backend and
# frontend dev servers run as `terminals` (see .cursor/environment.json).
set -euo pipefail

PG_VERSION=16

sudo pg_ctlcluster "$PG_VERSION" main start 2>/dev/null || true

for _ in $(seq 1 30); do
  if pg_isready -h 127.0.0.1 -p 5432 >/dev/null 2>&1; then
    echo "[start] PostgreSQL is accepting connections on 127.0.0.1:5432"
    exit 0
  fi
  sleep 1
done

echo "[start] PostgreSQL did not become ready in time" >&2
exit 1
