#!/usr/bin/env bash
# Drop the per-session test stores the suites create automatically.
#
# `shared_db.testing` gives every Claude Code session its own databases and Qdrant
# collections, named `<base>_test_cc<session>`, so an agent's run and your own never
# clear each other's rows. Nothing reaps them: they are created on demand and outlive
# the session that made them, so this drops the lot.
#
# Only the automatic ones. `visitdoc_chat_test`, `visitdoc_scheduler_test` and the
# `faq_chunks_test` collection are what a plain shell uses and are never touched, nor
# is any dev database.
set -euo pipefail

POSTGRES_CONTAINER="${POSTGRES_CONTAINER:-visitdoc-postgres}"
POSTGRES_USER="${POSTGRES_USER:-visitdoc}"
QDRANT_URL="${QDRANT_URL:-http://localhost:6333}"

psql_do() {
    docker exec "$POSTGRES_CONTAINER" psql -U "$POSTGRES_USER" -d postgres "$@"
}

echo "Postgres databases:"
databases=$(psql_do -tAc \
    "SELECT datname FROM pg_database WHERE datname ~ '_test_cc[a-z0-9]+' ORDER BY 1")
if [ -z "$databases" ]; then
    echo "  none to drop"
else
    while IFS= read -r database; do
        # Quoted, so a name Postgres would otherwise fold is still the one that is
        # dropped; FORCE detaches any session still connected to it.
        psql_do -c "DROP DATABASE IF EXISTS \"$database\" WITH (FORCE)" >/dev/null
        echo "  dropped $database"
    done <<< "$databases"
fi

echo "Qdrant collections:"
collections=$(curl -fsS "$QDRANT_URL/collections" | python3 -c '
import json, re, sys

pattern = re.compile(r"_test_cc[a-z0-9]+")
for collection in json.load(sys.stdin)["result"]["collections"]:
    if pattern.search(collection["name"]):
        print(collection["name"])
')
if [ -z "$collections" ]; then
    echo "  none to delete"
else
    while IFS= read -r collection; do
        curl -fsS -X DELETE "$QDRANT_URL/collections/$collection" >/dev/null
        echo "  deleted $collection"
    done <<< "$collections"
fi
