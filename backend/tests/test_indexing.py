"""Phase B: chunking, embeddings, sparse retrieval, fusion and rollup."""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pytest

from marsa.indexing.chunking import (
    MAX_CHUNK_WORDS,
    MIN_CHUNK_WORDS,
    SECTION_WEIGHTS,
    chunk_ruling,
    min_words_for,
    split_paragraphs,
    split_sections,
)
from marsa.indexing.embeddings import (
    EMBEDDING_DIM,
    HashedNGramEmbedder,
    build_embedder,
)
from marsa.indexing.hybrid import reciprocal_rank_fusion, rollup_to_parents
from marsa.indexing.rerank import LexicalReranker
from marsa.indexing.sparse import Bm25Index, expand_hts_prefixes, tokenize
from marsa.indexing.store import NumpyVectorStore, ScoredChunk
from marsa.ingestion.schemas import CrossRuling, Origin, Provenance, SourceKind

RULING_BODY = """DESCRIPTION OF MERCHANDISE:
The merchandise is a portable lithium-ion power bank with an integrated
charging circuit, imported in retail packaging for onward re-export.

ISSUE:
What is the correct classification of the power bank?

LAW AND ANALYSIS:
The article's principal function is energy storage, which governs
classification under GRI 1. Competing headings covering static converters of
heading 8504 were considered and rejected because the charging circuit is
subsidiary to that principal function. See also NY N298877.

HOLDING:
The applicable subheading will be 8507.60.0020. The rate of duty will be free.
"""


def make_ruling(body: str = RULING_BODY, **overrides) -> CrossRuling:
    defaults = dict(
        ruling_number="NY N302241",
        collection="NY",
        ruling_date=None,
        subject="Tariff classification of a lithium-ion power bank from China",
        body=body,
        hts_codes=["8507.60.0020"],
        related_rulings=["NY N298877"],
        category="electronics",
        url=None,
        provenance=Provenance(
            source=SourceKind.CROSS, origin=Origin.LIVE, retrieved_at=datetime.now(UTC)
        ),
    )
    return CrossRuling(**{**defaults, **overrides})


# ─── Chunking ────────────────────────────────────────────────────────────────


class TestSectionSplitting:
    def test_finds_all_sections(self):
        names = [name for name, _ in split_sections(RULING_BODY)]
        assert names == ["DESCRIPTION OF MERCHANDISE", "ISSUE", "LAW AND ANALYSIS", "HOLDING"]

    def test_text_before_first_header_becomes_preamble(self):
        body = "Dear Sir:\nRE: Tariff classification\n\nISSUE:\nWhat is it?"
        sections = dict(split_sections(body))
        assert "PREAMBLE" in sections
        assert "Dear Sir" in sections["PREAMBLE"]

    def test_unstructured_body_is_one_preamble(self):
        sections = split_sections("Just some prose with no headers at all.")
        assert len(sections) == 1
        assert sections[0][0] == "PREAMBLE"

    def test_empty_body(self):
        assert split_sections("") == []

    def test_headers_are_case_insensitive(self):
        assert [n for n, _ in split_sections("holding:\nSubheading 8507.60.")] == ["HOLDING"]


class TestChunking:
    def test_holding_is_always_indexed(self):
        """Regression: a uniform 25-word minimum deleted every HOLDING.

        Real holdings are routinely two sentences and under 20 words, and they
        are the operative text of the entire ruling. Dropping them left the
        index unable to answer the question the corpus exists for.
        """
        sections = {c.section for c in chunk_ruling(make_ruling())}
        assert "HOLDING" in sections

    def test_short_high_value_sections_survive(self):
        assert min_words_for("HOLDING") < MIN_CHUNK_WORDS
        assert min_words_for("ISSUE") < MIN_CHUNK_WORDS
        assert min_words_for("LAW AND ANALYSIS") == MIN_CHUNK_WORDS

    def test_terse_holding_survives(self):
        ruling = make_ruling(body="HOLDING:\nThe subheading will be 8507.60.0020.")
        assert any(c.section == "HOLDING" for c in chunk_ruling(ruling))

    def test_subject_becomes_its_own_chunk(self):
        chunks = chunk_ruling(make_ruling())
        subject = [c for c in chunks if c.section == "SUBJECT"]
        assert len(subject) == 1
        assert "power bank" in subject[0].text

    def test_every_chunk_points_at_its_parent(self):
        ruling = make_ruling()
        for chunk in chunk_ruling(ruling):
            assert chunk.parent_id == ruling.doc_id
            assert chunk.ruling_number == ruling.ruling_number

    def test_chunk_ids_are_unique(self):
        ids = [c.chunk_id for c in chunk_ruling(make_ruling())]
        assert len(ids) == len(set(ids))

    def test_hts_codes_extracted_into_chunk(self):
        holding = next(c for c in chunk_ruling(make_ruling()) if c.section == "HOLDING")
        assert "8507.60.0020" in holding.hts_codes

    def test_holding_outweighs_description(self):
        chunks = {c.section: c for c in chunk_ruling(make_ruling())}
        assert chunks["HOLDING"].weight > chunks["ISSUE"].weight

    def test_oversized_paragraph_is_split_with_overlap(self):
        long_text = " ".join(f"word{i}" for i in range(700))
        ruling = make_ruling(body=f"LAW AND ANALYSIS:\n{long_text}")
        chunks = [c for c in chunk_ruling(ruling) if c.section == "LAW AND ANALYSIS"]
        assert len(chunks) > 1
        assert all(c.word_count <= MAX_CHUNK_WORDS for c in chunks)
        # Overlap: the tail of chunk n must reappear at the head of chunk n+1.
        assert set(chunks[0].text.split()[-20:]) & set(chunks[1].text.split())

    def test_ruling_with_unparseable_body_still_indexed(self):
        """A ruling must never silently vanish from the index."""
        chunks = chunk_ruling(make_ruling(body="Tiny."))
        assert any(c.section != "SUBJECT" for c in chunks)

    def test_all_sections_have_declared_weights(self):
        for chunk in chunk_ruling(make_ruling()):
            assert chunk.section in SECTION_WEIGHTS, f"{chunk.section} has no declared weight"


class TestParagraphs:
    def test_blank_line_split(self):
        assert len(split_paragraphs("one\n\ntwo\n\nthree")) == 3

    def test_wrapped_text_without_blank_lines_is_split(self):
        wrapped = "\n".join(" ".join(f"w{i}" for i in range(30)) for _ in range(12))
        assert len(split_paragraphs(wrapped)) > 1


# ─── Embeddings ──────────────────────────────────────────────────────────────


class TestHashedEmbedder:
    @pytest.fixture
    def embedder(self):
        return HashedNGramEmbedder()

    def test_shape_and_unit_norm(self, embedder):
        vectors = embedder.encode(["alpha beta", "gamma delta"])
        assert vectors.shape == (2, EMBEDDING_DIM)
        assert np.allclose(np.linalg.norm(vectors, axis=1), 1.0, atol=1e-5)

    def test_deterministic_across_calls(self, embedder):
        a = embedder.encode_one("lithium-ion power bank")
        b = embedder.encode_one("lithium-ion power bank")
        assert np.array_equal(a, b)

    def test_deterministic_across_instances(self):
        """blake2b, not hash() — the latter is randomised per interpreter."""
        a = HashedNGramEmbedder().encode_one("power bank")
        b = HashedNGramEmbedder().encode_one("power bank")
        assert np.array_equal(a, b)

    def test_lexical_overlap_beats_no_overlap(self):
        e = HashedNGramEmbedder()
        v = e.encode(["lithium-ion battery cell", "lithium-ion battery pack", "wooden desk"])
        assert float(v[0] @ v[1]) > float(v[0] @ v[2])

    def test_empty_text_does_not_produce_nan(self, embedder):
        vector = embedder.encode_one("")
        assert not np.isnan(vector).any()

    def test_declares_itself_non_semantic(self, embedder):
        assert embedder.is_semantic is False

    def test_dimension_matches_minilm(self):
        """Identical dim means swapping backends is a re-embed, not a migration."""
        assert HashedNGramEmbedder().dim == 384

    def test_auto_falls_back_when_weights_unavailable(self):
        embedder = build_embedder("auto")
        assert embedder.dim == EMBEDDING_DIM

    def test_unknown_backend_rejected(self):
        with pytest.raises(ValueError, match="unknown embedding backend"):
            build_embedder("nonsense")


# ─── Sparse ──────────────────────────────────────────────────────────────────


class TestTokenizer:
    def test_tariff_code_survives_intact(self):
        """Splitting on punctuation shatters every code in the corpus."""
        assert "8507.60.0020" in tokenize("subheading 8507.60.0020 applies")

    def test_ruling_number_is_glued(self):
        assert "nyn302241" in tokenize("See NY N302241 for treatment")

    def test_stopwords_removed_but_tariff_terms_kept(self):
        tokens = tokenize("parts of general use and other articles")
        assert "of" not in tokens and "and" not in tokens
        # "parts", "other", "general" are terms of art in tariff language.
        assert {"parts", "other", "general"} <= set(tokens)

    def test_prefix_expansion(self):
        assert set(expand_hts_prefixes(["8507.60.0020"])) == {
            "8507.60.0020", "8507", "8507.60",
        }

    def test_non_code_tokens_untouched(self):
        assert expand_hts_prefixes(["battery"]) == ["battery"]


class TestBm25:
    @pytest.fixture
    def index(self):
        chunks = chunk_ruling(make_ruling()) + chunk_ruling(
            make_ruling(
                ruling_number="NY N999999",
                subject="Tariff classification of a wooden desk from Germany",
                body="HOLDING:\nThe applicable subheading will be 9403.30.8000.",
            )
        )
        idx = Bm25Index()
        idx.build(chunks)
        return idx

    def test_exact_code_lookup(self, index):
        hits = index.search("8507.60.0020", k=5)
        assert hits
        assert hits[0].chunk.ruling_number == "NY N302241"

    def test_prefix_query_reaches_full_code(self, index):
        assert any("8507.60.0020" in h.chunk.text for h in index.search("8507.60", k=5))

    def test_non_matching_documents_are_excluded(self, index):
        """BM25 scores 0.0 for no shared terms. Those are non-matches, and
        letting them into fusion would give them unearned rank credit."""
        assert all(h.score > 0 for h in index.search("power bank", k=50))

    def test_empty_query_returns_nothing(self, index):
        assert index.search("of the and", k=5) == []

    def test_round_trip(self, index, tmp_path):
        path = tmp_path / "bm25.pkl"
        index.save(path)
        assert Bm25Index.load(path).count() == index.count()

    def test_missing_file_is_actionable(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="marsa-index build"):
            Bm25Index.load(tmp_path / "absent.pkl")


# ─── Fusion + rollup ─────────────────────────────────────────────────────────


def scored(chunk_id: str, parent: str, score: float, section: str = "HOLDING", retriever="dense"):
    from marsa.indexing.chunking import Chunk

    return ScoredChunk(
        Chunk(
            chunk_id=chunk_id,
            parent_id=parent,
            ruling_number=parent.replace("cross::", ""),
            section=section,
            ordinal=0,
            text=f"text for {chunk_id}",
        ),
        score,
        retriever,
    )


class TestRrf:
    def test_agreed_document_wins(self):
        """A doc both retrievers rank highly beats one only either likes."""
        dense = [scored("a", "p1", 0.9), scored("b", "p2", 0.8)]
        sparse = [
            scored("b", "p2", 12.0, retriever="sparse"),
            scored("c", "p3", 9.0, retriever="sparse"),
        ]
        fused = reciprocal_rank_fusion([dense, sparse])
        assert fused[0].chunk.chunk_id == "b"

    def test_magnitudes_are_ignored(self):
        """Only rank matters — that is the entire point of RRF."""
        a = reciprocal_rank_fusion([[scored("x", "p", 0.99), scored("y", "p", 0.98)]])
        b = reciprocal_rank_fusion([[scored("x", "p", 0.11), scored("y", "p", 0.02)]])
        assert [c.chunk.chunk_id for c in a] == [c.chunk.chunk_id for c in b]
        assert a[0].score == pytest.approx(b[0].score)

    def test_retriever_provenance_is_merged(self):
        fused = reciprocal_rank_fusion(
            [[scored("a", "p1", 0.9)], [scored("a", "p1", 5.0, retriever="sparse")]]
        )
        assert fused[0].retriever == "dense+sparse"

    def test_limit_respected(self):
        many = [scored(f"c{i}", f"p{i}", 1.0) for i in range(100)]
        assert len(reciprocal_rank_fusion([many], limit=10)) == 10

    def test_empty_input(self):
        assert reciprocal_rank_fusion([]) == []


class TestRollup:
    def test_groups_children_under_parent(self):
        rulings = rollup_to_parents(
            [scored("a", "p1", 0.9), scored("b", "p1", 0.7), scored("c", "p2", 0.8)]
        )
        assert len(rulings) == 2
        assert rulings[0].parent_id == "p1"

    def test_best_chunk_plus_bonus_not_sum(self):
        """Summing would let a long ruling of mediocre chunks beat a short
        ruling that answers the question exactly — length bias by another name.
        """
        many_weak = [scored(f"w{i}", "long", 0.30) for i in range(8)]
        one_strong = [scored("s", "short", 0.85)]
        rulings = rollup_to_parents(many_weak + one_strong)
        assert rulings[0].parent_id == "short"

    def test_section_weight_breaks_ties(self):
        rulings = rollup_to_parents(
            [
                scored("h", "p_hold", 0.60, section="HOLDING"),
                scored("d", "p_desc", 0.60, section="DESCRIPTION OF MERCHANDISE"),
            ]
        )
        assert rulings[0].parent_id == "p_hold"

    def test_chunks_per_parent_capped(self):
        rulings = rollup_to_parents([scored(f"c{i}", "p", 0.5) for i in range(10)])
        assert len(rulings[0].chunks) <= 3

    def test_hts_codes_deduped_across_chunks(self):
        from marsa.indexing.chunking import Chunk

        chunk = Chunk("a", "p", "NY N1", "HOLDING", 0, "t", ("8507.60", "8507.60"))
        rulings = rollup_to_parents([ScoredChunk(chunk, 0.9)])
        assert rulings[0].hts_codes == ["8507.60"]


# ─── Reranking ───────────────────────────────────────────────────────────────


class TestLexicalReranker:
    @pytest.fixture
    def reranker(self):
        return LexicalReranker()

    def test_term_coverage_orders_results(self, reranker):
        candidates = [
            scored("miss", "p1", 0.5),
            scored("hit", "p2", 0.5),
        ]
        candidates[1] = ScoredChunk(
            candidates[1].chunk.__class__(
                "hit", "p2", "NY N2", "HOLDING", 0, "lithium-ion power bank subheading"
            ),
            0.5,
        )
        ranked = reranker.rerank("lithium-ion power bank", candidates, k=2)
        assert ranked[0].chunk.chunk_id == "hit"

    def test_tariff_code_match_is_strong(self, reranker):
        from marsa.indexing.chunking import Chunk

        with_code = ScoredChunk(
            Chunk("c1", "p1", "NY N1", "HOLDING", 0, "subheading 8507.60.0020 applies",
                  ("8507.60.0020",)), 0.5
        )
        without = ScoredChunk(
            Chunk("c2", "p2", "NY N2", "HOLDING", 0, "subheading 9403.30.8000 applies",
                  ("9403.30.8000",)), 0.5
        )
        ranked = reranker.rerank("8507.60.0020", [without, with_code], k=2)
        assert ranked[0].chunk.chunk_id == "c1"

    def test_empty_candidates(self, reranker):
        assert reranker.rerank("anything", [], k=5) == []

    def test_declares_itself_non_semantic(self, reranker):
        assert reranker.is_semantic is False

    def test_scores_bounded(self, reranker):
        ranked = reranker.rerank("power bank", [scored("a", "p", 0.5)], k=1)
        assert 0.0 <= ranked[0].score <= 1.0


# ─── Numpy store ─────────────────────────────────────────────────────────────


class TestNumpyStore:
    @pytest.fixture
    def populated(self, tmp_path):
        store = NumpyVectorStore(tmp_path / "vectors.pkl")
        store.create(EMBEDDING_DIM)
        embedder = HashedNGramEmbedder()
        chunks = chunk_ruling(make_ruling())
        store.upsert(chunks, embedder.encode([c.text for c in chunks]))
        return store, embedder

    def test_search_returns_ranked_hits(self, populated):
        store, embedder = populated
        hits = store.search(embedder.encode_one("8507.60.0020 subheading"), k=3)
        assert hits
        assert hits[0].score >= hits[-1].score

    def test_exact_search_finds_indexed_text(self, populated):
        store, embedder = populated
        target = store._chunks[0]
        hits = store.search(embedder.encode_one(target.text), k=1)
        assert hits[0].chunk.chunk_id == target.chunk_id

    def test_upsert_updates_rather_than_duplicates(self, populated):
        store, embedder = populated
        before = store.count()
        chunks = chunk_ruling(make_ruling())
        store.upsert(chunks, embedder.encode([c.text for c in chunks]))
        assert store.count() == before

    def test_mismatched_lengths_rejected(self, populated):
        store, _ = populated
        with pytest.raises(ValueError, match="chunks vs"):
            store.upsert(chunk_ruling(make_ruling()), np.zeros((1, EMBEDDING_DIM), np.float32))

    def test_round_trip(self, populated, tmp_path):
        store, _ = populated
        store.save()
        reloaded = NumpyVectorStore(tmp_path / "vectors.pkl")
        reloaded.load()
        assert reloaded.count() == store.count()

    def test_search_on_empty_store(self, tmp_path):
        store = NumpyVectorStore(tmp_path / "v.pkl")
        store.create(EMBEDDING_DIM)
        assert store.search(np.zeros(EMBEDDING_DIM, np.float32), k=5) == []

    def test_missing_file_is_actionable(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="marsa-index build"):
            NumpyVectorStore(tmp_path / "absent.pkl").load()
