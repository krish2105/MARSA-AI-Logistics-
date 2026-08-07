"""Entity resolution: query text → graph nodes.

This is the step that decides whether the graph path can answer anything at
all. "Which suppliers are exposed if Jebel Ali congestion worsens" is only
answerable once "Jebel Ali" becomes `port:AEJEA`.

Resolution is deliberately lexical and layered, strongest signal first:

1. **Tariff codes** — an HTS code in the query is unambiguous, so it short-
   circuits everything else.
2. **Exact alias** — a curated table plus every node label, normalised.
3. **Longest-phrase match** — n-grams from the query, longest first, so
   "United Arab Emirates" wins over "United".
4. **Fuzzy fallback** — `difflib` above a strict cutoff, for typos only.

No embeddings. That is a deliberate choice, not a limitation of the
environment: node labels are proper nouns and codes, where surface form *is*
the signal, and a semantic model would happily match "Singapore" to "Malaysia"
because they are conceptually similar — which is exactly wrong here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

import networkx as nx

from marsa.graph.bridges import country_to_iso3, normalise_country
from marsa.graph.schema import NodeKind, node_id, node_kind_of

HTS_RE = re.compile(r"\b\d{4}\.\d{2}(?:\.\d{2,4})?(?:\.\d{2})?\b")
CHAPTER_RE = re.compile(r"\b(?:hs|hts|chapter)\s*(\d{2})\b", re.IGNORECASE)
RULING_RE = re.compile(r"\b((?:NY|HQ)\s?[A-Z]?\d{5,6})\b", re.IGNORECASE)

FUZZY_CUTOFF = 0.86
MAX_PHRASE_WORDS = 5

#: Names people actually use that are not node labels.
PORT_ALIASES: dict[str, str] = {
    "jebel ali": "AEJEA",
    "jebel ali port": "AEJEA",
    "dp world": "AEJEA",
    "khalifa port": "AEKHL",
    "port rashid": "AEJEA",
    "shanghai port": "CNSHA",
    "port of singapore": "SGSIN",
    "psa singapore": "SGSIN",
    "rotterdam port": "NLRTM",
    "nhava sheva": "INNSA",
    "jnpt": "INNSA",
    "la port": "USLAX",
    "port of los angeles": "USLAX",
}

#: Words that would otherwise fuzzy-match a node label and add noise.
STOP_PHRASES = frozenset(
    {"the", "and", "for", "our", "which", "what", "who", "how", "are", "is", "if",
     "port", "ports", "country", "countries", "supplier", "suppliers", "product",
     "products", "shipment", "shipments", "risk", "exposure", "exposed"}
)


@dataclass(frozen=True)
class ResolvedEntity:
    node_id: str
    label: str
    kind: str
    #: 1.0 for an exact/code match, lower for fuzzy.
    confidence: float
    #: What in the query produced this match — surfaced in the audit log.
    matched_on: str
    method: str


class EntityResolver:
    """Resolves query text against a built graph."""

    def __init__(self, graph: nx.MultiDiGraph) -> None:
        self.graph = graph
        self._alias: dict[str, str] = {}
        self._build_alias_table()

    def _register(self, text: str, nid: str) -> None:
        key = normalise_country(text)
        # First registration wins, so curated aliases are not overwritten by a
        # later node whose label happens to normalise the same way.
        if key and key not in self._alias:
            self._alias[key] = nid

    def _build_alias_table(self) -> None:
        for alias, unlocode in PORT_ALIASES.items():
            nid = node_id(NodeKind.PORT, unlocode)
            if self.graph.has_node(nid):
                self._register(alias, nid)

        for nid, data in self.graph.nodes(data=True):
            label = str(data.get("label") or "")
            if label:
                self._register(label, nid)

            kind = data.get("kind")
            if kind == NodeKind.PORT.value and data.get("unlocode"):
                self._register(str(data["unlocode"]), nid)
            elif kind == NodeKind.COUNTRY.value:
                # Countries are addressed by ISO3 and by full name in several
                # languages; route both through the shared lookup.
                _, _, iso3 = nid.partition(":")
                self._register(iso3, nid)

    # ── resolution ─────────────────────────────────────────────────────────
    def resolve(self, query: str, *, limit: int = 6) -> list[ResolvedEntity]:
        found: dict[str, ResolvedEntity] = {}

        def offer(entity: ResolvedEntity) -> None:
            existing = found.get(entity.node_id)
            if existing is None or entity.confidence > existing.confidence:
                found[entity.node_id] = entity

        for entity in self._match_codes(query):
            offer(entity)
        for entity in self._match_phrases(query):
            offer(entity)

        # Fuzzy is a last resort: only consult it when nothing solid matched,
        # otherwise a typo-tolerant match can outrank an exact one.
        if not found:
            for entity in self._match_fuzzy(query):
                offer(entity)

        ranked = sorted(found.values(), key=lambda e: (-e.confidence, e.label))
        return ranked[:limit]

    def _match_codes(self, query: str) -> list[ResolvedEntity]:
        out: list[ResolvedEntity] = []

        for code in HTS_RE.findall(query):
            nid = node_id(NodeKind.HTS_CODE, code)
            if self.graph.has_node(nid):
                out.append(
                    ResolvedEntity(
                        nid, code, NodeKind.HTS_CODE.value, 1.0, code, "hts_code"
                    )
                )
            chapter_nid = node_id(NodeKind.HTS_CHAPTER, code.replace(".", "")[:2])
            if self.graph.has_node(chapter_nid):
                out.append(
                    ResolvedEntity(
                        chapter_nid, f"HS {code.replace('.', '')[:2]}",
                        NodeKind.HTS_CHAPTER.value, 0.9, code, "hts_chapter",
                    )
                )

        for chapter in CHAPTER_RE.findall(query):
            nid = node_id(NodeKind.HTS_CHAPTER, chapter)
            if self.graph.has_node(nid):
                out.append(
                    ResolvedEntity(nid, f"HS {chapter}", NodeKind.HTS_CHAPTER.value,
                                   1.0, f"chapter {chapter}", "hts_chapter")
                )

        for raw in RULING_RE.findall(query):
            normalised = re.sub(r"\s+", " ", raw.strip().upper())
            nid = node_id(NodeKind.RULING, normalised)
            if self.graph.has_node(nid):
                out.append(
                    ResolvedEntity(
                        nid, normalised, NodeKind.RULING.value, 1.0, raw, "ruling_number"
                    )
                )

        return out

    def _match_phrases(self, query: str) -> list[ResolvedEntity]:
        """Longest-phrase-first matching over query n-grams."""
        words = normalise_country(query).split()
        out: list[ResolvedEntity] = []
        consumed: set[int] = set()

        for size in range(min(MAX_PHRASE_WORDS, len(words)), 0, -1):
            for start in range(len(words) - size + 1):
                span = range(start, start + size)
                if any(i in consumed for i in span):
                    continue

                phrase = " ".join(words[start : start + size])
                if size == 1 and phrase in STOP_PHRASES:
                    continue

                nid = self._alias.get(phrase)
                if nid is None:
                    iso3 = country_to_iso3(phrase)
                    if iso3:
                        candidate = node_id(NodeKind.COUNTRY, iso3)
                        nid = candidate if self.graph.has_node(candidate) else None

                if nid:
                    consumed.update(span)
                    data = self.graph.nodes[nid]
                    out.append(
                        ResolvedEntity(
                            nid,
                            str(data.get("label") or nid),
                            str(data.get("kind") or node_kind_of(nid)),
                            # Longer phrases are stronger evidence.
                            min(1.0, 0.80 + 0.05 * size),
                            phrase,
                            "alias",
                        )
                    )
        return out

    def _match_fuzzy(self, query: str) -> list[ResolvedEntity]:
        words = [w for w in normalise_country(query).split() if w not in STOP_PHRASES]
        out: list[ResolvedEntity] = []

        for size in (2, 1):
            for start in range(max(0, len(words) - size + 1)):
                phrase = " ".join(words[start : start + size])
                if len(phrase) < 4:
                    continue
                best_key, best_score = None, 0.0
                for alias in self._alias:
                    score = SequenceMatcher(None, phrase, alias).ratio()
                    if score > best_score:
                        best_key, best_score = alias, score
                if best_key and best_score >= FUZZY_CUTOFF:
                    nid = self._alias[best_key]
                    data = self.graph.nodes[nid]
                    out.append(
                        ResolvedEntity(
                            nid,
                            str(data.get("label") or nid),
                            str(data.get("kind") or node_kind_of(nid)),
                            round(best_score * 0.8, 3),
                            phrase,
                            "fuzzy",
                        )
                    )
        return out
