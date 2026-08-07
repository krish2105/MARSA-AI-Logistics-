"""Embedding backends.

The spec's choice is `all-MiniLM-L6-v2`: 22M parameters, CPU-only, no GPU or
MPS required. That is `MiniLMEmbedder` below and it is the intended production
path.

`HashedNGramEmbedder` exists because model weights come from huggingface.co,
which is not reachable from every environment (it is blocked in this project's
build sandbox). It is a real, classical technique — signed feature hashing over
word and character n-grams, L2-normalised — not a stub returning zeros. It
produces a genuine vector space with genuine lexical similarity, so the entire
pipeline (dimensionality, cosine distance, HNSW indexing, fusion, rollup) is
exercised faithfully end to end.

What it is **not** is semantic. It cannot match "power bank" to "portable
battery charger" unless they share character n-grams. Any retrieval quality
number produced with this backend measures lexical overlap, not the system the
spec describes, and `EmbeddingBackend.is_semantic` exists so downstream code
can refuse to publish such a number.

Both backends emit **384 dimensions**. That is deliberate: the pgvector column,
the HNSW index and every stored row are then identical across backends, so
swapping the fallback for real MiniLM is a re-embed, not a schema migration.
"""

from __future__ import annotations

import hashlib
import re
from abc import ABC, abstractmethod
from collections.abc import Sequence

import numpy as np

EMBEDDING_DIM = 384
MINILM_MODEL = "sentence-transformers/all-MiniLM-L6-v2"

_TOKEN_RE = re.compile(r"[a-z0-9]+(?:\.[0-9]+)*")


def tokenize(text: str) -> list[str]:
    """Lowercase word tokens, keeping dotted numeric codes intact.

    `8507.60.0020` must survive as one token. Splitting on punctuation shatters
    every tariff code in the corpus into meaningless number fragments, which is
    fatal for a classification-lookup corpus.
    """
    return _TOKEN_RE.findall(text.lower())


class Embedder(ABC):
    """Anything that turns text into vectors of a fixed dimension."""

    dim: int = EMBEDDING_DIM
    name: str = "abstract"
    #: True only for backends that model meaning rather than surface form.
    is_semantic: bool = False

    @abstractmethod
    def encode(self, texts: Sequence[str]) -> np.ndarray:
        """Return an (len(texts), dim) float32 array of unit-norm rows."""

    def encode_one(self, text: str) -> np.ndarray:
        return self.encode([text])[0]

    def describe(self) -> dict[str, object]:
        return {"backend": self.name, "dim": self.dim, "semantic": self.is_semantic}


def _l2_normalise(matrix: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    # A chunk can hash to the zero vector only if it tokenises to nothing;
    # guard rather than emitting NaN and poisoning every cosine comparison.
    np.maximum(norms, 1e-12, out=norms)
    return (matrix / norms).astype(np.float32)


class HashedNGramEmbedder(Embedder):
    """Deterministic signed feature hashing over word + character n-grams.

    Signed hashing (each feature contributes ±1 by a second hash bit) keeps
    collisions from systematically inflating similarity: two unrelated features
    landing in the same bucket cancel as often as they reinforce.

    Character n-grams give sub-word robustness, so `batteries` and `battery`
    still land near each other despite being different word tokens.
    """

    name = "hashed-ngram"
    is_semantic = False

    def __init__(
        self,
        dim: int = EMBEDDING_DIM,
        *,
        word_ngrams: tuple[int, ...] = (1, 2),
        char_ngrams: tuple[int, ...] = (4, 5),
    ) -> None:
        self.dim = dim
        self.word_ngrams = word_ngrams
        self.char_ngrams = char_ngrams

    @staticmethod
    def _hash(feature: str) -> tuple[int, float]:
        # blake2b keyed by content: stable across processes and Python runs,
        # unlike hash(), which is randomised per interpreter by PYTHONHASHSEED.
        digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
        value = int.from_bytes(digest, "big")
        return value >> 1, (1.0 if value & 1 else -1.0)

    def _features(self, text: str) -> list[str]:
        tokens = tokenize(text)
        features: list[str] = []

        for n in self.word_ngrams:
            for i in range(len(tokens) - n + 1):
                features.append("w:" + " ".join(tokens[i : i + n]))

        # Character n-grams run over the token stream, not raw text, so
        # whitespace and punctuation noise does not generate features.
        joined = " ".join(tokens)
        for n in self.char_ngrams:
            for i in range(len(joined) - n + 1):
                features.append("c:" + joined[i : i + n])

        return features

    def encode(self, texts: Sequence[str]) -> np.ndarray:
        matrix = np.zeros((len(texts), self.dim), dtype=np.float32)
        for row, text in enumerate(texts):
            for feature in self._features(text):
                bucket, sign = self._hash(feature)
                matrix[row, bucket % self.dim] += sign
        return _l2_normalise(matrix)


class MiniLMEmbedder(Embedder):
    """`all-MiniLM-L6-v2` via sentence-transformers. The intended path.

    Imported lazily: sentence-transformers pulls in torch, which is a large
    dependency that has no business being imported when the fallback is in use.
    Install with `pip install -e ".[ml]"`.
    """

    name = MINILM_MODEL
    is_semantic = True

    def __init__(self, model_name: str = MINILM_MODEL, *, batch_size: int = 64) -> None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:  # pragma: no cover - depends on optional extra
            raise RuntimeError(
                "sentence-transformers is not installed. Run "
                'pip install -e ".[ml]", or use the hashed-ngram backend '
                "(EMBEDDING_BACKEND=hashed)."
            ) from exc

        try:
            self._model = SentenceTransformer(model_name, device="cpu")
        except Exception as exc:  # pragma: no cover - depends on network
            raise RuntimeError(
                f"Could not load {model_name}. Weights are fetched from "
                "huggingface.co; if that host is unreachable, either pre-cache "
                "the model into HF_HOME or set EMBEDDING_BACKEND=hashed."
            ) from exc

        self.model_name = model_name
        self.batch_size = batch_size
        self.dim = int(self._model.get_sentence_embedding_dimension())

    def encode(self, texts: Sequence[str]) -> np.ndarray:
        vectors = self._model.encode(
            list(texts),
            batch_size=self.batch_size,
            convert_to_numpy=True,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return np.asarray(vectors, dtype=np.float32)


def build_embedder(backend: str = "auto", *, dim: int = EMBEDDING_DIM) -> Embedder:
    """Resolve a backend by name.

    `auto` prefers MiniLM and falls back to hashing if the model cannot be
    loaded — but never silently: the caller is expected to surface
    `is_semantic` wherever quality is being claimed.
    """
    backend = (backend or "auto").lower()

    if backend in {"hashed", "hashed-ngram", "fallback"}:
        return HashedNGramEmbedder(dim=dim)

    if backend in {"minilm", "local", "sentence-transformers"}:
        return MiniLMEmbedder()

    if backend == "auto":
        try:
            return MiniLMEmbedder()
        except RuntimeError:
            return HashedNGramEmbedder(dim=dim)

    raise ValueError(f"unknown embedding backend: {backend!r}")
