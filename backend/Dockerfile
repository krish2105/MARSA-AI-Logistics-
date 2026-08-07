# MARSA AI — backend production image (FastAPI + LangGraph).
#
# `sentence-transformers` is deliberately NOT installed: it pulls torch, which
# would add well over a gigabyte for an optional embedding backend that also
# needs huggingface.co to be reachable. The image uses the hashed n-gram
# fallback, and /health plus every index manifest report `semantic: false` so
# nobody mistakes its retrieval scores for the real thing. Install the `ml`
# extra and set EMBEDDING_BACKEND=local to change that.

# ── builder ─────────────────────────────────────────────────────────────────
FROM python:3.11-slim AS builder

# LightGBM needs libgomp to build and to run; the runtime stage installs it too.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
ENV PIP_NO_CACHE_DIR=1

# Dependencies resolve from pyproject alone, so this layer caches across source
# edits. README.md is copied because the project metadata references it.
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --prefix=/install .

# ── runtime ─────────────────────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

RUN apt-get update && apt-get install -y --no-install-recommends \
        libgomp1 curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /install /usr/local

WORKDIR /app
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

# DATA_DIR is not optional here. `settings.data_dir` defaults to
# `Path(__file__).parents[3]/data`, which is correct from a source checkout and
# resolves to /usr/local/lib/data from an installed package — a path that is
# both wrong and not writable by the non-root user. Setting it explicitly is
# what makes the image work at all.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    ENVIRONMENT=production \
    EMBEDDING_BACKEND=hashed \
    DATA_DIR=/app/data

# Bake the artifacts that need no database. All three are deterministic from a
# seed, so the image is reproducible; none of them is real data, and every
# manifest they write says so (origin=SYNTHETIC). Takes ~30s.
RUN marsa-ingest fixtures --rulings 400 --orders 5000 \
    && marsa-ml train \
    && marsa-ml congestion \
    && marsa-graph build

# Non-root. The entrypoint writes the index to data/, so that must be writable.
RUN useradd --create-home --uid 1001 marsa && chown -R marsa:marsa /app
USER marsa

EXPOSE 8000

# `curl -f` so a 5xx is a failed check rather than a successful fetch of an
# error page. Start period covers the boot-time index build.
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${PORT:-8000}/health" || exit 1

ENTRYPOINT ["docker-entrypoint.sh"]
