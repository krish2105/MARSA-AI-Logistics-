#!/bin/sh
# MARSA AI — backend container entrypoint.
#
# The corpora, supply graph and risk models are baked into the image at build
# time (they need no database and are deterministic from a seed). The fast-path
# index is not, and cannot be: half of it is rows in pgvector, which only exists
# at deploy time. The other half — the BM25 pickle and the manifest — is written
# to local disk, which is ephemeral in a container and does not survive a
# restart. So the index is rebuilt on boot. It takes about two seconds.
set -e

BOOTSTRAP="${MARSA_BOOTSTRAP_INDEX:-auto}"

placeholder_dsn() {
    # The .env template ships `user:password@host`; treat that as unset rather
    # than spending the boot window resolving a hostname that does not exist.
    case "$DATABASE_URL" in
        *"user:password@host"*) return 0 ;;
        *) return 1 ;;
    esac
}

should_bootstrap() {
    [ "$BOOTSTRAP" = "never" ] && return 1
    [ -z "$DATABASE_URL" ] && return 1
    placeholder_dsn && return 1
    return 0
}

if should_bootstrap; then
    echo "→ building fast-path index (embedding backend: ${EMBEDDING_BACKEND:-hashed})"
    # A failed index build must not crash-loop the container. The graph and
    # agentic paths do not depend on it, and /health reports `fastPathIndex:
    # false` so the degradation is visible rather than silent. Crash-looping
    # would take down two working paths to punish the failure of a third.
    if marsa-index build --embedding-backend "${EMBEDDING_BACKEND:-hashed}"; then
        echo "✓ fast-path index ready"
    else
        echo "⚠ INDEX BUILD FAILED — the fast path will be unavailable."
        echo "  The graph and agentic paths still work; /health reports this."
    fi

    # A build against an unreachable database does NOT fail. It falls back to
    # an in-process numpy store and prints a tick, which is the right call for
    # local development and a trap in production: a typo'd DSN yields a green
    # container that never touches pgvector and loses every vector on restart.
    # DATABASE_URL was set, so numpy here means the database was not reached.
    STORE=$(python -c "
import json, os, pathlib
p = pathlib.Path(os.environ.get('DATA_DIR', 'data')) / 'index' / 'index.manifest.json'
print(json.loads(p.read_text())['vector_store'] if p.exists() else 'MISSING')
" 2>/dev/null || echo UNKNOWN)

    case "$STORE" in
        PgVectorStore) echo "✓ vector store: pgvector" ;;
        *)
            echo "⚠ DATABASE_URL is set but the index built into '$STORE', not pgvector."
            echo "  The database was not reachable. Vectors are in process memory"
            echo "  and will be lost on restart. Check DATABASE_URL and network rules."
            if [ "${MARSA_REQUIRE_PGVECTOR:-false}" = "true" ]; then
                echo "✗ MARSA_REQUIRE_PGVECTOR=true — refusing to start on a silent fallback."
                exit 1
            fi
            ;;
    esac
else
    echo "→ skipping index build (MARSA_BOOTSTRAP_INDEX=$BOOTSTRAP, DATABASE_URL set: $([ -n "$DATABASE_URL" ] && echo yes || echo no))"
fi

# Render, Fly and Cloud Run all inject the port to bind. Defaulting to 8000
# keeps `docker run -p 8000:8000` working with no environment at all.
exec uvicorn marsa.api.main:app --host 0.0.0.0 --port "${PORT:-8000}" "$@"
