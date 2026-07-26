#!/bin/sh
# Entrypoint guard: block until PostgreSQL accepts connections, then exec the
# real command. Compose healthchecks cover the normal case; this covers the
# host-network and CI cases where they do not apply.
set -e

HOST="${DB_HOST:-db}"
PORT="${DB_PORT:-5432}"
TIMEOUT="${DB_WAIT_TIMEOUT:-60}"

echo "waiting for postgres at ${HOST}:${PORT} (timeout ${TIMEOUT}s)"

elapsed=0
until python -c "
import socket, sys
s = socket.socket()
s.settimeout(2)
try:
    s.connect(('${HOST}', ${PORT}))
except OSError:
    sys.exit(1)
finally:
    s.close()
" 2>/dev/null; do
  elapsed=$((elapsed + 1))
  if [ "$elapsed" -ge "$TIMEOUT" ]; then
    echo "postgres not reachable after ${TIMEOUT}s — giving up" >&2
    exit 1
  fi
  sleep 1
done

echo "postgres is up"
exec "$@"
