"""Reranking.

The spec calls for a cross-encoder before the fast path answers. A cross-encoder
scores (query, passage) *jointly* rather than comparing two independently
computed vectors, so it can weigh term interactions a bi-encoder structurally
cannot. It is far too slow to run over a whole corpus, which is exactly why it
sits after retrieval, over a candidate pool of tens.

`CrossEncoderReranker` is that, and is the intended path. `LexicalReranker` is
the offline fallback for environments that cannot reach huggingface.co, and it
is honest about being a heuristic: coverage of query terms, exact-phrase
credit, tariff-code match, and the section weight from chunking. It reorders
sensibly; it does not model language.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from collections.abc import Sequence

from marsa.indexing.chunking import SECTION_WEIGHTS
from marsa.indexing.sparse import tokenize
from marsa.indexing.store import ScoredChunk

CROSS_ENCODER_MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"

HTS_RE = re.compile(r"\b\d{4}\.\d{2}(?:\.\d{2,4})?(?:\.\d{2})?\b")


class Reranker(ABC):
    name: str = "abstract"
    is_semantic: bool = False

    @abstractmethod
    def rerank(
        self, query: str, candidates: Sequence[ScoredChunk], k: int
    ) -> list[ScoredChunk]: ...

    def describe(self) -> dict[str, object]:
        return {"reranker": self.name, "semantic": self.is_semantic}


class LexicalReranker(Reranker):
    """Heuristic reranker: term coverage, phrase credit, code match, section.

    Scores are in [0, 1] and are *not* comparable to cross-encoder logits. They
    exist to order a candidate pool, nothing more.
    """

    name = "lexical-heuristic"
    is_semantic = False

    def __init__(
        self,
        *,
        coverage_weight: float = 0.50,
        phrase_weight: float = 0.20,
        code_weight: float = 0.20,
        section_weight: float = 0.10,
    ) -> None:
        self.coverage_weight = coverage_weight
        self.phrase_weight = phrase_weight
        self.code_weight = code_weight
        self.section_weight = section_weight

    def score(self, query: str, candidate: ScoredChunk) -> float:
        query_tokens = tokenize(query)
        if not query_tokens:
            return 0.0

        text = candidate.chunk.text.lower()
        chunk_tokens = set(tokenize(text))

        # 1. What fraction of the query's terms appear at all.
        coverage = sum(1 for t in set(query_tokens) if t in chunk_tokens) / len(set(query_tokens))

        # 2. Contiguous bigram credit — word order carries meaning in tariff
        #    language ("parts of general use" is a term of art).
        bigrams = [f"{a} {b}" for a, b in zip(query_tokens, query_tokens[1:], strict=False)]
        phrase = (
            sum(1 for bg in bigrams if bg in text) / len(bigrams) if bigrams else 0.0
        )

        # 3. A tariff code named in the query and present in the chunk is close
        #    to decisive, and is the case dense retrieval handles worst.
        query_codes = set(HTS_RE.findall(query))
        code = 0.0
        if query_codes:
            chunk_codes = set(candidate.chunk.hts_codes) | set(HTS_RE.findall(text))
            if query_codes & chunk_codes:
                code = 1.0
            elif any(
                cc.startswith(qc) or qc.startswith(cc)
                for qc in query_codes
                for cc in chunk_codes
            ):
                code = 0.6

        section = SECTION_WEIGHTS.get(candidate.chunk.section.upper(), 0.6)

        return (
            self.coverage_weight * coverage
            + self.phrase_weight * phrase
            + self.code_weight * code
            + self.section_weight * section
        )

    def rerank(self, query: str, candidates: Sequence[ScoredChunk], k: int) -> list[ScoredChunk]:
        rescored = [
            ScoredChunk(c.chunk, self.score(query, c), f"rerank:{self.name}", arms=c.arms)
            for c in candidates
        ]
        rescored.sort(key=lambda c: -c.score)
        return rescored[:k]


class CrossEncoderReranker(Reranker):
    """`cross-encoder/ms-marco-MiniLM-L-6-v2`. The intended path.

    Lazily imported for the same reason as MiniLMEmbedder: torch has no
    business being loaded when the fallback is in use.
    """

    name = CROSS_ENCODER_MODEL
    is_semantic = True

    def __init__(self, model_name: str = CROSS_ENCODER_MODEL, *, batch_size: int = 32) -> None:
        try:
            from sentence_transformers import CrossEncoder
        except ImportError as exc:  # pragma: no cover - optional extra
            raise RuntimeError(
                'sentence-transformers is not installed. Run pip install -e ".[ml]", '
                "or use the lexical reranker (RERANK_BACKEND=lexical)."
            ) from exc

        try:
            self._model = CrossEncoder(model_name, device="cpu", max_length=512)
        except Exception as exc:  # pragma: no cover - depends on network
            raise RuntimeError(
                f"Could not load {model_name}. Weights come from huggingface.co; "
                "pre-cache into HF_HOME or set RERANK_BACKEND=lexical."
            ) from exc

        self.batch_size = batch_size

    def rerank(self, query: str, candidates: Sequence[ScoredChunk], k: int) -> list[ScoredChunk]:
        if not candidates:
            return []
        pairs = [(query, c.chunk.text) for c in candidates]
        scores = self._model.predict(pairs, batch_size=self.batch_size, show_progress_bar=False)
        rescored = [
            ScoredChunk(c.chunk, float(s), "rerank:cross-encoder", arms=c.arms)
            for c, s in zip(candidates, scores, strict=True)
        ]
        rescored.sort(key=lambda c: -c.score)
        return rescored[:k]


def build_reranker(backend: str = "auto") -> Reranker:
    backend = (backend or "auto").lower()
    if backend in {"lexical", "heuristic", "fallback"}:
        return LexicalReranker()
    if backend in {"cross-encoder", "crossencoder", "ce"}:
        return CrossEncoderReranker()
    if backend == "auto":
        try:
            return CrossEncoderReranker()
        except RuntimeError:
            return LexicalReranker()
    raise ValueError(f"unknown rerank backend: {backend!r}")
