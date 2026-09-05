#!/usr/bin/env bash
#
# A local PostgreSQL, without Docker.
#
# Postgres ships with everything needed to run a private cluster as an unprivileged user:
# initdb creates one in a directory you own, pg_ctl starts it on a port you pick. No daemon,
# no root, no container runtime — and it is the same server the production code will talk to,
# which a SQLite substitute would not be.
#
# The cluster lives in .data/pg and is disposable: `reset` deletes and rebuilds it.
#
#   ./infra/local/pg.sh up | down | status | reset | psql
#
set -euo pipefail

PORT="${LUMINA_PG_PORT:-55432}"
USER_NAME="${LUMINA_PG_USER:-lumina}"
DB_NAME="${LUMINA_PG_DB:-lumina}"
# A separate database for the suite.
#
# Not a nicety: the tests write jobs into the same `jobs` table a running worker claims from,
# so sharing one database means a dev worker picks up a test's job, tries to compose a
# seven-byte "PNG", and fails it. The failures are harmless and completely misleading.
TEST_DB_NAME="${LUMINA_PG_TEST_DB:-${DB_NAME}_test}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PGDATA="${LUMINA_PG_DATA:-$ROOT/.data/pg}"

# Debian and Ubuntu keep the binaries off PATH so several major versions can coexist.
find_bin() {
  if command -v pg_ctl >/dev/null 2>&1; then
    dirname "$(command -v pg_ctl)"
    return
  fi
  local newest
  newest="$(ls -1d /usr/lib/postgresql/*/bin 2>/dev/null | sort -V | tail -1 || true)"
  if [[ -z "$newest" ]]; then
    echo "postgres not found. install it, e.g. sudo apt install postgresql" >&2
    exit 1
  fi
  echo "$newest"
}

BIN="$(find_bin)"

running() { "$BIN/pg_isready" -h 127.0.0.1 -p "$PORT" -q 2>/dev/null; }

case "${1:-up}" in
  up)
    if running; then
      echo "postgres already up on :$PORT"
    else
      if [[ ! -d "$PGDATA" ]]; then
        echo "creating cluster in $PGDATA"
        mkdir -p "$PGDATA"
        "$BIN/initdb" -D "$PGDATA" -U "$USER_NAME" --auth=trust -E UTF8 >/dev/null
      fi
      # Unix sockets go in the data directory rather than /var/run, which needs root.
      "$BIN/pg_ctl" -D "$PGDATA" -l "$PGDATA/server.log" \
        -o "-p $PORT -k $PGDATA -c listen_addresses=127.0.0.1" start >/dev/null
      for _ in $(seq 1 30); do running && break; sleep 0.3; done
      running || { tail -20 "$PGDATA/server.log"; exit 1; }
      echo "postgres up on :$PORT"
    fi
    for db in "$DB_NAME" "$TEST_DB_NAME"; do
      "$BIN/psql" -h 127.0.0.1 -p "$PORT" -U "$USER_NAME" -d postgres -tAc \
        "SELECT 1 FROM pg_database WHERE datname='$db'" | grep -q 1 \
        || "$BIN/createdb" -h 127.0.0.1 -p "$PORT" -U "$USER_NAME" "$db"
    done
    echo "DATABASE_URL=postgresql+asyncpg://$USER_NAME@127.0.0.1:$PORT/$DB_NAME"
    echo "TEST_DATABASE_URL=postgresql+asyncpg://$USER_NAME@127.0.0.1:$PORT/$TEST_DB_NAME"
    ;;
  down)
    running && "$BIN/pg_ctl" -D "$PGDATA" stop -m fast >/dev/null && echo "stopped" || echo "not running"
    ;;
  status)
    running && echo "up on :$PORT" || echo "down"
    ;;
  reset)
    running && "$BIN/pg_ctl" -D "$PGDATA" stop -m immediate >/dev/null 2>&1 || true
    rm -rf "$PGDATA"
    echo "cluster deleted; run 'up' to rebuild"
    ;;
  psql)
    shift
    exec "$BIN/psql" -h 127.0.0.1 -p "$PORT" -U "$USER_NAME" -d "$DB_NAME" "$@"
    ;;
  *)
    echo "usage: $0 {up|down|status|reset|psql}" >&2
    exit 2
    ;;
esac
