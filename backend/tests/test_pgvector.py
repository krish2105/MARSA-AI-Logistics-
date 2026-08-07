"""pgvector integration tests.

These run against a **real** Postgres with the vector extension, not a mock —
mocking a vector database mostly tests the mock. Set `MARSA_TEST_DSN` (or
`DATABASE_URL`) to enable them; they skip cleanly otherwise so CI without a
database still passes.

    createdb marsa && psql marsa -c 'CREATE EXTENSION vector'
    MARSA_TEST_DSN=postgresql://localhost/marsa pytest tests/test_pgvector.py
"""

from __future__ import annotations

import os

import numpy as np
import pytest

from marsa.indexing.chunking import Chunk
from marsa.indexing.embeddings import EMBEDDING_DIM, HashedNGramEmbedder

DSN = os.environ.get("MARSA_TEST_DSN") or os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DSN or "user:password@host" in (DSN or ""),
    reason="no Postgres DSN configured (set MARSA_TEST_DSN)",
)

TEST_TABLE = "test_cross_chunks"


def make_chunks() -> list[Chunk]:
    return [
        Chunk(
            chunk_id="cross::NYN302241::0",
            parent_id="cross::NYN302241",
            ruling_number="NY N302241",
            section="HOLDING",
            ordinal=0,
            text="The applicable subheading will be 8507.60.0020, lithium-ion accumulators.",
            hts_codes=("8507.60.0020",),
            metadata={"collection": "NY", "category": "electronics"},
        ),
        Chunk(
            chunk_id="cross::NYN999999::0",
            parent_id="cross::NYN999999",
            ruling_number="NY N999999",
            section="HOLDING",
            ordinal=0,
            text="The applicable subheading will be 9403.30.8000, wooden office furniture.",
            hts_codes=("9403.30.8000",),
            metadata={"collection": "NY", "category": "furniture"},
        ),
        Chunk(
            chunk_id="cross::NYN302241::1",
            parent_id="cross::NYN302241",
            ruling_number="NY N302241",
            section="LAW AND ANALYSIS",
            ordinal=1,
            text="The principal function is energy storage, which governs classification.",
            hts_codes=(),
            metadata={},
        ),
    ]


@pytest.fixture
def store():
    from marsa.indexing.store import PgVectorStore

    store = PgVectorStore(DSN, table=TEST_TABLE)
    store.create(EMBEDDING_DIM)
    yield store
    with store.conn.cursor() as cur:
        cur.execute(f"DROP TABLE IF EXISTS {TEST_TABLE}")
    store.close()


@pytest.fixture
def populated(store):
    embedder = HashedNGramEmbedder()
    chunks = make_chunks()
    store.upsert(chunks, embedder.encode([c.text for c in chunks]))
    return store, embedder, chunks


class TestSchema:
    def test_extension_and_table_exist(self, store):
        with store.conn.cursor() as cur:
            cur.execute("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
            assert cur.fetchone() is not None
            cur.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name = %s",
                (TEST_TABLE,),
            )
            columns = {r["column_name"] for r in cur.fetchall()}
        assert {"chunk_id", "parent_id", "embedding", "hts_codes", "metadata"} <= columns

    def test_multi_statement_schema_applies(self, store):
        """Regression: the schema was one multi-statement string with a bound
        parameter. psycopg sends parameterised queries as prepared statements,
        and Postgres rejects multiple commands in those — so `create()` failed
        outright. Statements are now issued one at a time.
        """
        assert store.count() == 0  # reaching here at all means create() worked

    def test_vector_dimension_enforced_by_the_database(self, store):
        chunk = make_chunks()[0]
        wrong = np.ones((1, 128), dtype=np.float32)  # not 384
        with pytest.raises(Exception, match="(?i)dimension|expected"):
            store.upsert([chunk], wrong)


class TestUpsert:
    def test_inserts_all_rows(self, populated):
        store, _, chunks = populated
        assert store.count() == len(chunks)

    def test_is_idempotent(self, populated):
        store, embedder, chunks = populated
        store.upsert(chunks, embedder.encode([c.text for c in chunks]))
        assert store.count() == len(chunks)

    def test_array_and_jsonb_round_trip(self, populated):
        """hts_codes is TEXT[] and metadata is JSONB — both must survive."""
        store, embedder, _ = populated
        hits = store.search(embedder.encode_one("lithium-ion accumulators 8507.60.0020"), k=1)
        assert hits[0].chunk.hts_codes == ("8507.60.0020",)
        assert hits[0].chunk.metadata["category"] == "electronics"

    def test_mismatched_lengths_rejected(self, store):
        with pytest.raises(ValueError, match="chunks vs"):
            store.upsert(make_chunks(), np.zeros((1, EMBEDDING_DIM), np.float32))

    def test_empty_upsert_is_a_noop(self, store):
        assert store.upsert([], np.zeros((0, EMBEDDING_DIM), np.float32)) == 0


class TestSearch:
    def test_returns_similarity_not_distance(self, populated):
        """`<=>` yields cosine *distance*; the store's contract is that higher
        is better, so the SELECT must convert. Getting this backwards inverts
        the entire ranking silently."""
        store, embedder, _ = populated
        hits = store.search(embedder.encode_one("lithium-ion accumulators"), k=3)
        assert hits == sorted(hits, key=lambda h: -h.score)
        assert all(-1.0001 <= h.score <= 1.0001 for h in hits)

    def test_finds_the_semantically_closest_row(self, populated):
        store, embedder, _ = populated
        hits = store.search(embedder.encode_one("wooden office furniture 9403.30.8000"), k=1)
        assert hits[0].chunk.ruling_number == "NY N999999"

    def test_self_similarity_is_near_one(self, populated):
        store, embedder, chunks = populated
        hits = store.search(embedder.encode_one(chunks[0].text), k=1)
        assert hits[0].score == pytest.approx(1.0, abs=1e-3)

    def test_k_is_respected(self, populated):
        store, embedder, _ = populated
        assert len(store.search(embedder.encode_one("subheading"), k=2)) == 2

    def test_search_on_empty_table(self, store):
        assert store.search(np.zeros(EMBEDDING_DIM, np.float32), k=5) == []

    def test_retriever_label_is_dense(self, populated):
        store, embedder, _ = populated
        assert store.search(embedder.encode_one("subheading"), k=1)[0].retriever == "dense"


class TestHnsw:
    def test_index_is_created(self, populated):
        store, _, _ = populated
        store.build_ann_index()
        with store.conn.cursor() as cur:
            cur.execute("SELECT indexname FROM pg_indexes WHERE tablename = %s", (TEST_TABLE,))
            names = {r["indexname"] for r in cur.fetchall()}
        assert f"{TEST_TABLE}_embedding_idx" in names

    def test_results_survive_index_creation(self, populated):
        """HNSW is approximate. On a corpus this small it must still return the
        exact top hit, so a regression in the index build is visible."""
        store, embedder, _ = populated
        query = embedder.encode_one("lithium-ion accumulators 8507.60.0020")
        before = store.search(query, k=1)[0].chunk.chunk_id
        store.build_ann_index()
        assert store.search(query, k=1)[0].chunk.chunk_id == before


class TestEndToEnd:
    def test_full_fast_path_over_pgvector(self, populated):
        """Hybrid retrieval end to end against the real database."""
        from marsa.indexing.hybrid import FastPathRetriever
        from marsa.indexing.rerank import LexicalReranker
        from marsa.indexing.sparse import Bm25Index

        store, embedder, chunks = populated
        bm25 = Bm25Index()
        bm25.build(chunks)

        retriever = FastPathRetriever(
            embedder=embedder, store=store, bm25=bm25, reranker=LexicalReranker()
        )
        rulings, trace = retriever.retrieve("8507.60.0020", limit=2)

        assert rulings
        assert rulings[0].ruling_number == "NY N302241"
        assert "8507.60.0020" in rulings[0].hts_codes
        assert trace.dense_hits > 0
        assert trace.sparse_hits > 0
        # Both stages ran on offline fallbacks here, and the trace must say so.
        assert trace.fully_semantic is False

    def test_children_roll_up_to_one_parent(self, populated):
        """NY N302241 has two chunks; it must appear once, not twice."""
        from marsa.indexing.hybrid import FastPathRetriever
        from marsa.indexing.rerank import LexicalReranker
        from marsa.indexing.sparse import Bm25Index

        store, embedder, chunks = populated
        bm25 = Bm25Index()
        bm25.build(chunks)
        retriever = FastPathRetriever(
            embedder=embedder, store=store, bm25=bm25, reranker=LexicalReranker()
        )
        rulings, _ = retriever.retrieve("energy storage subheading", limit=5)
        numbers = [r.ruling_number for r in rulings]
        assert len(numbers) == len(set(numbers))
