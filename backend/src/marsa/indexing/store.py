"""Vector storage.

`PgVectorStore` is the spec's target (Postgres + pgvector on Neon/Supabase) and
is the production path. `NumpyVectorStore` is a file-backed exact-search store
for environments with no database.

The numpy store is not a toy: at this corpus size (a few thousand rulings, tens
of thousands of chunks) a brute-force matmul over a 384-dim float32 matrix is
sub-millisecond and returns *exact* nearest neighbours, where HNSW returns
approximate ones. pgvector earns its place through persistence, concurrency and
being able to filter by metadata in SQL — not through speed at this scale, and
saying otherwise would be marketing.
"""

from __future__ import annotations

import json
import pickle
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from marsa.indexing.chunking import Chunk
from marsa.logging import get_logger

log = get_logger(__name__)


@dataclass(frozen=True)
class ScoredChunk:
    """A retrieval hit. `score` is always higher-is-better."""

    chunk: Chunk
    score: float
    #: Which retriever produced this hit — surfaced in the audit log.
    retriever: str = "dense"


class VectorStore(ABC):
    @abstractmethod
    def create(self, dim: int) -> None:
        """(Re)create empty storage for vectors of `dim` dimensions."""

    @abstractmethod
    def upsert(self, chunks: Sequence[Chunk], vectors: np.ndarray) -> int: ...

    @abstractmethod
    def search(self, query: np.ndarray, k: int = 20) -> list[ScoredChunk]: ...

    @abstractmethod
    def count(self) -> int: ...

    def close(self) -> None:  # pragma: no cover - trivial
        return None


def _chunk_from_row(row: dict[str, Any]) -> Chunk:
    return Chunk(
        chunk_id=row["chunk_id"],
        parent_id=row["parent_id"],
        ruling_number=row["ruling_number"],
        section=row["section"],
        ordinal=row["ordinal"],
        text=row["text"],
        hts_codes=tuple(row.get("hts_codes") or ()),
        metadata=row.get("metadata") or {},
    )


# ─────────────────────────────────────────────────────────────────────────────
# pgvector
# ─────────────────────────────────────────────────────────────────────────────

# Statements are issued one at a time: psycopg sends parameterised queries as
# prepared statements, and Postgres rejects multiple commands in one of those.
#
# `dim` is interpolated as a literal rather than bound, because VECTOR(n) is a
# *type modifier* — it is part of the type name, not a value, so it cannot be a
# placeholder. The value is coerced through int() at the call site, which is
# what makes that interpolation safe.
def _schema_statements(table: str, dim: int) -> list[str]:
    return [
        "CREATE EXTENSION IF NOT EXISTS vector",
        f"""CREATE TABLE IF NOT EXISTS {table} (
            chunk_id      TEXT PRIMARY KEY,
            parent_id     TEXT NOT NULL,
            ruling_number TEXT NOT NULL,
            section       TEXT NOT NULL,
            ordinal       INTEGER NOT NULL,
            text          TEXT NOT NULL,
            hts_codes     TEXT[] NOT NULL DEFAULT '{{}}',
            metadata      JSONB NOT NULL DEFAULT '{{}}',
            embedding     VECTOR({dim}) NOT NULL
        )""",
        f"CREATE INDEX IF NOT EXISTS {table}_parent_idx ON {table} (parent_id)",
        f"CREATE INDEX IF NOT EXISTS {table}_hts_idx ON {table} USING GIN (hts_codes)",
    ]

# HNSW over cosine distance. Built *after* bulk load, never before: building an
# HNSW graph incrementally during a large insert is materially slower than
# building it once over the finished table.
def _hnsw_statement(table: str) -> str:
    return (
        f"CREATE INDEX IF NOT EXISTS {table}_embedding_idx "
        f"ON {table} USING hnsw (embedding vector_cosine_ops) "
        "WITH (m = 16, ef_construction = 64)"
    )


class PgVectorStore(VectorStore):
    """Postgres + pgvector. The spec's production target."""

    def __init__(self, dsn: str, *, table: str = "cross_chunks") -> None:
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError('psycopg is required: pip install "psycopg[binary]"') from exc

        self.table = table
        self._psycopg = psycopg
        self.conn = psycopg.connect(dsn, row_factory=dict_row, autocommit=True)

    def create(self, dim: int) -> None:
        dim = int(dim)  # the only sanitisation VECTOR(n) interpolation needs
        with self.conn.cursor() as cur:
            cur.execute(f"DROP TABLE IF EXISTS {self.table}")
            for statement in _schema_statements(self.table, dim):
                cur.execute(statement)
        log.info("pgvector schema ready", extra={"table": self.table, "dim": dim})

    def upsert(self, chunks: Sequence[Chunk], vectors: np.ndarray) -> int:
        if len(chunks) != len(vectors):
            raise ValueError(f"{len(chunks)} chunks vs {len(vectors)} vectors")
        if not chunks:
            return 0

        rows = [
            (
                c.chunk_id,
                c.parent_id,
                c.ruling_number,
                c.section,
                c.ordinal,
                c.text,
                list(c.hts_codes),
                json.dumps(c.metadata),
                # pgvector's text input format is a bracketed list.
                "[" + ",".join(f"{v:.6f}" for v in vectors[i]) + "]",
            )
            for i, c in enumerate(chunks)
        ]

        with self.conn.cursor() as cur:
            cur.executemany(
                f"""INSERT INTO {self.table}
                    (chunk_id, parent_id, ruling_number, section, ordinal,
                     text, hts_codes, metadata, embedding)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (chunk_id) DO UPDATE SET
                        text = EXCLUDED.text,
                        embedding = EXCLUDED.embedding,
                        metadata = EXCLUDED.metadata""",
                rows,
            )
        return len(rows)

    def build_ann_index(self) -> None:
        with self.conn.cursor() as cur:
            cur.execute(_hnsw_statement(self.table))
        log.info("hnsw index built", extra={"table": self.table})

    def search(self, query: np.ndarray, k: int = 20) -> list[ScoredChunk]:
        literal = "[" + ",".join(f"{v:.6f}" for v in query) + "]"
        with self.conn.cursor() as cur:
            cur.execute(
                f"""SELECT chunk_id, parent_id, ruling_number, section, ordinal,
                           text, hts_codes, metadata,
                           1 - (embedding <=> %s::vector) AS similarity
                    FROM {self.table}
                    ORDER BY embedding <=> %s::vector
                    LIMIT %s""",
                (literal, literal, k),
            )
            rows = cur.fetchall()

        # `<=>` is cosine *distance*; the SELECT converts to similarity so the
        # store's contract (higher is better) holds across backends.
        return [ScoredChunk(_chunk_from_row(r), float(r["similarity"]), "dense") for r in rows]

    def count(self) -> int:
        with self.conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) AS n FROM {self.table}")
            row = cur.fetchone()
        return int(row["n"]) if row else 0

    def close(self) -> None:
        self.conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# numpy
# ─────────────────────────────────────────────────────────────────────────────


class NumpyVectorStore(VectorStore):
    """File-backed exact nearest-neighbour search.

    Vectors are unit-norm, so cosine similarity is a plain dot product and the
    whole search is one matmul.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._chunks: list[Chunk] = []
        self._matrix: np.ndarray | None = None
        self._index: dict[str, int] = {}

    def create(self, dim: int) -> None:
        self._chunks = []
        self._matrix = np.zeros((0, dim), dtype=np.float32)
        self._index = {}

    def upsert(self, chunks: Sequence[Chunk], vectors: np.ndarray) -> int:
        if len(chunks) != len(vectors):
            raise ValueError(f"{len(chunks)} chunks vs {len(vectors)} vectors")
        if self._matrix is None:
            self.create(vectors.shape[1] if len(vectors) else 0)

        fresh_chunks: list[Chunk] = []
        fresh_vectors: list[np.ndarray] = []

        for chunk, vector in zip(chunks, vectors, strict=True):
            existing = self._index.get(chunk.chunk_id)
            if existing is not None:
                self._chunks[existing] = chunk
                self._matrix[existing] = vector
            else:
                self._index[chunk.chunk_id] = len(self._chunks) + len(fresh_chunks)
                fresh_chunks.append(chunk)
                fresh_vectors.append(vector)

        if fresh_chunks:
            self._chunks.extend(fresh_chunks)
            self._matrix = np.vstack([self._matrix, np.array(fresh_vectors, dtype=np.float32)])

        return len(chunks)

    def search(self, query: np.ndarray, k: int = 20) -> list[ScoredChunk]:
        if self._matrix is None or len(self._chunks) == 0:
            return []

        similarities = self._matrix @ query.astype(np.float32)
        k = min(k, len(self._chunks))
        # argpartition finds the top-k without fully sorting the array.
        top = np.argpartition(-similarities, k - 1)[:k]
        top = top[np.argsort(-similarities[top])]

        return [ScoredChunk(self._chunks[i], float(similarities[i]), "dense") for i in top]

    def count(self) -> int:
        return len(self._chunks)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("wb") as fh:
            pickle.dump({"chunks": self._chunks, "matrix": self._matrix}, fh)

    def load(self) -> None:
        if not self.path.exists():
            raise FileNotFoundError(
                f"No vector store at {self.path}. Run `marsa-index build` first."
            )
        with self.path.open("rb") as fh:
            payload = pickle.load(fh)  # noqa: S301 — our own artefact, not untrusted input
        self._chunks = payload["chunks"]
        self._matrix = payload["matrix"]
        self._index = {c.chunk_id: i for i, c in enumerate(self._chunks)}


def build_store(dsn: str | None, fallback_path: Path) -> VectorStore:
    """pgvector when a DSN is configured, numpy otherwise."""
    if dsn:
        try:
            return PgVectorStore(dsn)
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "pgvector unavailable; falling back to the local numpy store",
                extra={"error": str(exc)},
            )
    return NumpyVectorStore(fallback_path)
