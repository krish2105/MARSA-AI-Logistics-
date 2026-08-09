"""The fast path: hybrid retrieval → fusion → rerank → parent rollup.

Pipeline
--------
    query
      ├─ dense  (embed → pgvector cosine top-N)
      └─ sparse (BM25 top-N)
            ↓
      reciprocal rank fusion
            ↓
      cross-encoder rerank (top-M)
            ↓
      child → parent rollup
            ↓
      whole rulings, with citations

Why RRF rather than weighted score blending
-------------------------------------------
Dense cosine similarity lives in [-1, 1] and clusters tightly around 0.2-0.6 on
real corpora. BM25 is unbounded and scales with corpus statistics and query
length. Blending them requires normalising two distributions whose shapes vary
per query — min-max normalisation in particular is dominated by whichever list
happens to contain an outlier.

Reciprocal rank fusion sidesteps the whole problem by discarding magnitudes and
using only rank:

    RRF(d) = Σ_r  1 / (k + rank_r(d))

It needs no tuning, is robust to either retriever returning garbage scores, and
is the standard for exactly this reason. `k=60` is the value from the original
Cormack et al. paper; it damps the influence of top ranks enough that a single
retriever cannot dominate on its own.

The cost is real and worth naming: RRF throws away confidence. A dense hit at
0.95 and one at 0.35 contribute identically if they rank the same. The rerank
stage is what restores a calibrated ordering.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass, field

from marsa.indexing.chunking import Chunk
from marsa.indexing.embeddings import Embedder
from marsa.indexing.rerank import Reranker
from marsa.indexing.sparse import Bm25Index
from marsa.indexing.store import ScoredChunk, VectorStore

RRF_K = 60


@dataclass
class RetrievedRuling:
    """A parent ruling assembled from its matching child chunks."""

    parent_id: str
    ruling_number: str
    score: float
    #: Best-scoring chunks, in order — these are the quotable spans.
    chunks: list[ScoredChunk] = field(default_factory=list)

    @property
    def hts_codes(self) -> list[str]:
        seen: dict[str, None] = {}
        for scored in self.chunks:
            for code in scored.chunk.hts_codes:
                seen.setdefault(code, None)
        return list(seen)

    @property
    def best_section(self) -> str:
        return self.chunks[0].chunk.section if self.chunks else ""


@dataclass
class RetrievalTrace:
    """Per-stage record, written to the audit log the spec requires."""

    query: str
    dense_hits: int = 0
    sparse_hits: int = 0
    fused_candidates: int = 0
    reranked: int = 0
    parents: int = 0
    latency_ms: float = 0.0
    embedder: str = ""
    reranker: str = ""
    #: False when any stage ran on a non-semantic fallback. Downstream code
    #: uses this to refuse to publish quality numbers.
    fully_semantic: bool = True

    def as_dict(self) -> dict[str, object]:
        return {
            "query": self.query,
            "dense_hits": self.dense_hits,
            "sparse_hits": self.sparse_hits,
            "fused_candidates": self.fused_candidates,
            "reranked": self.reranked,
            "parents": self.parents,
            "latency_ms": round(self.latency_ms, 2),
            "embedder": self.embedder,
            "reranker": self.reranker,
            "fully_semantic": self.fully_semantic,
        }


def reciprocal_rank_fusion(
    ranked_lists: Sequence[Sequence[ScoredChunk]],
    *,
    k: int = RRF_K,
    limit: int = 50,
) -> list[ScoredChunk]:
    """Fuse ranked lists by reciprocal rank. Ranks are 1-based."""
    scores: dict[str, float] = {}
    chunks: dict[str, Chunk] = {}
    sources: dict[str, set[str]] = {}

    for ranked in ranked_lists:
        for rank, scored in enumerate(ranked, start=1):
            cid = scored.chunk.chunk_id
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank)
            chunks[cid] = scored.chunk
            sources.setdefault(cid, set()).add(scored.retriever)

    ordered = sorted(scores, key=lambda cid: -scores[cid])[:limit]
    return [
        ScoredChunk(
            chunks[cid],
            scores[cid],
            "+".join(sorted(sources[cid])),
            arms=tuple(sorted(sources[cid])),
        )
        for cid in ordered
    ]


def rollup_to_parents(
    scored_chunks: Sequence[ScoredChunk],
    *,
    limit: int = 5,
    max_chunks_per_parent: int = 3,
) -> list[RetrievedRuling]:
    """Collapse child chunks onto their parent rulings.

    A parent scores as its **best** chunk, plus a small damped bonus for each
    additional matching chunk. Summing instead would let a long ruling with
    many mediocre chunks outrank a short one that answers the question
    exactly — length bias by another name.

    Section weight is applied here rather than at retrieval so it influences
    which *ruling* wins without distorting the fusion ranks.
    """
    grouped: dict[str, list[ScoredChunk]] = {}
    for scored in scored_chunks:
        grouped.setdefault(scored.chunk.parent_id, []).append(scored)

    rulings: list[RetrievedRuling] = []
    for parent_id, members in grouped.items():
        members.sort(key=lambda c: -(c.score * c.chunk.weight))
        best = members[0].score * members[0].chunk.weight
        # Diminishing returns: 2nd chunk adds 15%, 3rd 7.5%, and so on.
        bonus = sum(
            member.score * member.chunk.weight * (0.15 / (2**i))
            for i, member in enumerate(members[1:max_chunks_per_parent])
        )
        rulings.append(
            RetrievedRuling(
                parent_id=parent_id,
                ruling_number=members[0].chunk.ruling_number,
                score=best + bonus,
                chunks=members[:max_chunks_per_parent],
            )
        )

    rulings.sort(key=lambda r: -r.score)
    return rulings[:limit]


class FastPathRetriever:
    """Hybrid retrieval for the fast path."""

    def __init__(
        self,
        *,
        embedder: Embedder,
        store: VectorStore,
        bm25: Bm25Index,
        reranker: Reranker,
        dense_k: int = 20,
        sparse_k: int = 20,
        fusion_limit: int = 40,
        rerank_k: int = 12,
    ) -> None:
        self.embedder = embedder
        self.store = store
        self.bm25 = bm25
        self.reranker = reranker
        self.dense_k = dense_k
        self.sparse_k = sparse_k
        self.fusion_limit = fusion_limit
        self.rerank_k = rerank_k

    def retrieve(
        self, query: str, *, limit: int = 5
    ) -> tuple[list[RetrievedRuling], RetrievalTrace]:
        started = time.perf_counter()
        trace = RetrievalTrace(
            query=query,
            embedder=self.embedder.name,
            reranker=self.reranker.name,
            fully_semantic=self.embedder.is_semantic and self.reranker.is_semantic,
        )

        dense = self.store.search(self.embedder.encode_one(query), k=self.dense_k)
        trace.dense_hits = len(dense)

        sparse = self.bm25.search(query, k=self.sparse_k)
        trace.sparse_hits = len(sparse)

        fused = reciprocal_rank_fusion([dense, sparse], limit=self.fusion_limit)
        trace.fused_candidates = len(fused)

        reranked = self.reranker.rerank(query, fused, k=self.rerank_k)
        trace.reranked = len(reranked)

        rulings = rollup_to_parents(reranked, limit=limit)
        trace.parents = len(rulings)
        trace.latency_ms = (time.perf_counter() - started) * 1000

        return rulings, trace
