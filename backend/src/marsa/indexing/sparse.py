"""BM25 sparse index.

Why sparse retrieval is not optional here
-----------------------------------------
Half the questions this corpus answers are *exact-identifier* questions: "what
does ruling NY N302241 say", "what falls under 8507.60.0020". Dense embeddings
are systematically bad at those — every tariff code occupies roughly the same
neighbourhood in embedding space, because they are near-identical strings of
digits with no distinguishing semantics. BM25 nails them, because it matches
the literal token.

That complementarity is the entire argument for hybrid retrieval, and it only
holds if the tokenizer keeps identifiers intact.
"""

from __future__ import annotations

import pickle
import re
from collections.abc import Sequence
from pathlib import Path

from rank_bm25 import BM25Okapi

from marsa.indexing.chunking import Chunk
from marsa.indexing.store import ScoredChunk

# Ruling numbers appear as "NY N302241" — two whitespace-separated tokens that
# only mean something together. Glue them into one before generic tokenizing.
RULING_RE = re.compile(r"\b(NY|HQ)\s+([A-Z]?\d{5,6})\b", re.IGNORECASE)

# Keeps dotted tariff codes whole: 8507.60.0020 stays one token.
TOKEN_RE = re.compile(r"[a-z0-9]+(?:\.[0-9]+)*")

# Deliberately tiny. Aggressive stopword lists strip "parts", "other" and "of",
# which are load-bearing words in tariff language ("parts of general use",
# "other than"). These are the ones that carry no discriminative signal at all.
STOPWORDS = frozenset(
    {
        "a", "an", "the", "of", "for", "to", "in", "on", "at", "by",
        "is", "are", "was", "were", "be", "been", "being",
        "and", "or", "as", "that", "this", "these", "those",
        "it", "its", "from", "with",
    }
)


def tokenize(text: str) -> list[str]:
    """Tokenizer shared by indexing and querying — they must never diverge."""
    glued = RULING_RE.sub(lambda m: f"{m.group(1).lower()}{m.group(2).lower()}", text)
    return [t for t in TOKEN_RE.findall(glued.lower()) if t not in STOPWORDS]


def expand_hts_prefixes(tokens: Sequence[str]) -> list[str]:
    """Add shorter prefixes of any tariff code found.

    A query for `8507.60` should reach a chunk holding `8507.60.0020`. Indexing
    the prefixes alongside the full code makes that a lexical match rather than
    something the dense side has to rescue.
    """
    expanded = list(tokens)
    for token in tokens:
        if "." in token and token.replace(".", "").isdigit():
            parts = token.split(".")
            for cut in range(1, len(parts)):
                expanded.append(".".join(parts[:cut]))
    return expanded


class Bm25Index:
    """BM25Okapi over child chunks."""

    def __init__(self) -> None:
        self._bm25: BM25Okapi | None = None
        self._chunks: list[Chunk] = []

    def build(self, chunks: Sequence[Chunk]) -> None:
        self._chunks = list(chunks)
        corpus = [
            expand_hts_prefixes(tokenize(f"{c.ruling_number} {c.section} {c.text}"))
            for c in self._chunks
        ]
        # BM25Okapi divides by corpus stats; an empty corpus would raise here.
        self._bm25 = BM25Okapi(corpus) if corpus else None

    def search(self, query: str, k: int = 20) -> list[ScoredChunk]:
        if self._bm25 is None or not self._chunks:
            return []

        tokens = expand_hts_prefixes(tokenize(query))
        if not tokens:
            return []

        scores = self._bm25.get_scores(tokens)
        ranked = sorted(range(len(scores)), key=lambda i: -scores[i])[:k]

        # BM25 returns 0.0 for documents sharing no query term. Those are not
        # weak matches, they are non-matches, and letting them into the fusion
        # pool would give them rank credit they have not earned.
        return [
            ScoredChunk(self._chunks[i], float(scores[i]), "sparse")
            for i in ranked
            if scores[i] > 0.0
        ]

    def count(self) -> int:
        return len(self._chunks)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as fh:
            pickle.dump({"bm25": self._bm25, "chunks": self._chunks}, fh)

    @classmethod
    def load(cls, path: Path) -> Bm25Index:
        if not path.exists():
            raise FileNotFoundError(
                f"No BM25 index at {path}. Run `marsa-index build` first."
            )
        index = cls()
        with path.open("rb") as fh:
            payload = pickle.load(fh)  # noqa: S301 — our own artefact
        index._bm25 = payload["bm25"]
        index._chunks = payload["chunks"]
        return index
