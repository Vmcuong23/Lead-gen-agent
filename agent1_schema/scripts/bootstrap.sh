#!/usr/bin/env bash
# Bootstrap: bring up Postgres in Docker and apply migrations.
set -euo pipefail

cd "$(dirname "$0")/.."

# 1. Start Postgres if not already running
if ! docker ps --format '{{.Names}}' | grep -q '^fdi-postgres$'; then
  echo "Starting fdi-postgres..."
  docker run -d --name fdi-postgres \
    -e POSTGRES_USER=fdi \
    -e POSTGRES_PASSWORD=fdi \
    -e POSTGRES_DB=fdi_agent \
    -p 5432:5432 \
    pgvector/pgvector:pg16
  echo "Waiting for Postgres to be ready..."
  sleep 5
fi

export DATABASE_URL="${DATABASE_URL:-postgresql+psycopg://fdi:fdi@localhost:5432/fdi_agent}"

# 2. Apply SQL migrations in order
PSQL_URL="$(echo "$DATABASE_URL" | sed 's/+psycopg//')"
for sql in migrations/*.sql; do
  echo "Applying $sql ..."
  psql "$PSQL_URL" -f "$sql"
done

echo "Schema bootstrap complete."
