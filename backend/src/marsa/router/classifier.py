"""The complexity classifier — the routing decision itself.

This is the component the whole project is an argument about. It decides how
much computation a query deserves, and the spec is explicit that its accuracy
must be *measured* rather than assumed, including any systematic
misclassification pattern.

Two implementations:

* `LLMClassifier` — a single cheap few-shot call, as specified. One call, low
  temperature, tight token budget: the classifier sits on the latency critical
  path of every query including the ones that route to the sub-second fast
  path, so it cannot cost more than the path it selects.

* `HeuristicClassifier` — deterministic, rule-based, for environments with no
  LLM provider. It is **not** a substitute and does not pretend to be: it
  reports `is_llm=False`, and Phase F must report its accuracy separately
  rather than folding it in. Shipping it means the router is testable and
  demonstrable offline, not that the spec's classifier has been built twice.

The heuristic scores weighted signals rather than matching a keyword list,
because the three classes differ in *shape*: a factual lookup names one entity
and asks what it is; a multi-hop question chains a condition to a consequence;
a network question asks who else is affected.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from marsa.logging import get_logger
from marsa.router.llm import LLMClient, LLMUnavailableError, LLMUsage

log = get_logger(__name__)


class QueryClass(StrEnum):
    SIMPLE_FACTUAL = "simple_factual"
    MULTI_HOP = "multi_hop_reasoning"
    RELATIONSHIP = "relationship_network"


#: Which path serves which class.
CLASS_TO_PATH: dict[QueryClass, str] = {
    QueryClass.SIMPLE_FACTUAL: "fast",
    QueryClass.MULTI_HOP: "agentic",
    QueryClass.RELATIONSHIP: "graph",
}


@dataclass
class Classification:
    query_class: QueryClass
    confidence: float
    rationale: str
    #: True only when a real LLM produced this. Phase F must not mix the two.
    is_llm: bool = False
    method: str = "heuristic"
    usage: LLMUsage = field(default_factory=LLMUsage)
    #: Per-class scores, for debugging a misroute.
    scores: dict[str, float] = field(default_factory=dict)

    @property
    def path(self) -> str:
        return CLASS_TO_PATH[self.query_class]

    def as_dict(self) -> dict[str, Any]:
        return {
            "queryClass": self.query_class.value,
            "path": self.path,
            "confidence": round(self.confidence, 4),
            "rationale": self.rationale,
            "isLlm": self.is_llm,
            "method": self.method,
            "usage": self.usage.as_dict(),
            "scores": {k: round(v, 4) for k, v in self.scores.items()},
        }


# ─────────────────────────────────────────────────────────────────────────────
# Few-shot prompt
# ─────────────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are a query router for a trade and logistics copilot.

Classify each query into exactly one class:

- simple_factual: a lookup answerable from one document. Names one product, \
tariff code or ruling and asks what it is or how it is classified.
- multi_hop_reasoning: requires chaining two or more dependent retrievals \
across different datasets — typically a condition ("if this tariff applies") \
joined to a consequence over the user's own records.
- relationship_network: asks which *other* entities are affected through the \
supply network — exposure, dependency, concentration, alternatives.

Reply with JSON only: {"class": "...", "confidence": 0.0-1.0, "reason": "one short sentence"}"""

FEW_SHOT: list[tuple[str, str]] = [
    (
        "What HTS code applies to lithium-ion power banks?",
        '{"class": "simple_factual", "confidence": 0.95, "reason": '
        '"Single-entity classification lookup, answerable from one ruling."}',
    ),
    (
        "Which of our electronics shipments are exposed if the new tariff on HS 8541 takes effect?",
        '{"class": "multi_hop_reasoning", "confidence": 0.9, "reason": '
        '"Resolve affected HS lines, then join to trade flows, then to our own shipment records."}',
    ),
    (
        "Which suppliers are exposed if Jebel Ali congestion worsens?",
        '{"class": "relationship_network", "confidence": 0.93, "reason": '
        '"Asks who else is affected through the port network — a traversal, not a lookup."}',
    ),
    (
        "Is ruling NY N302241 still good law?",
        '{"class": "simple_factual", "confidence": 0.88, "reason": '
        '"Names one ruling and asks about it directly."}',
    ),
]


def build_prompt(query: str) -> str:
    lines = ["Examples:", ""]
    for example, answer in FEW_SHOT:
        lines.append(f"Query: {example}")
        lines.append(f"Answer: {answer}")
        lines.append("")
    lines.append(f"Query: {query}")
    lines.append("Answer:")
    return "\n".join(lines)


def parse_response(text: str) -> tuple[QueryClass, float, str] | None:
    """Pull the classification out of a model response.

    Models wrap JSON in prose and fences no matter how firmly you ask them not
    to, so the first well-formed object anywhere in the text is used.
    """
    match = re.search(r"\{.*?\}", text, re.DOTALL)
    if not match:
        return None
    try:
        payload = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None

    raw = str(payload.get("class", "")).strip().lower()
    try:
        query_class = QueryClass(raw)
    except ValueError:
        return None

    try:
        confidence = float(payload.get("confidence", 0.5))
    except (TypeError, ValueError):
        confidence = 0.5

    return query_class, max(0.0, min(1.0, confidence)), str(payload.get("reason", "")).strip()


# ─────────────────────────────────────────────────────────────────────────────
# Heuristic signals
# ─────────────────────────────────────────────────────────────────────────────

HTS_RE = re.compile(r"\b\d{4}\.\d{2}(?:\.\d{2,4})?\b")
RULING_RE = re.compile(r"\b(?:NY|HQ)\s?[A-Z]?\d{5,6}\b", re.IGNORECASE)
CHAPTER_RE = re.compile(r"\b(?:hs|hts|chapter)\s*\d{2}\b", re.IGNORECASE)

#: (regex, class, weight). Weights are relative; only the ranking matters.
SIGNALS: list[tuple[re.Pattern[str], QueryClass, float]] = [
    # Network: who *else* is affected, and can they route around it.
    (re.compile(r"\bexpos(?:ed|ure)\b", re.I), QueryClass.RELATIONSHIP, 2.2),
    (re.compile(r"\bsuppliers?\b", re.I), QueryClass.RELATIONSHIP, 1.8),
    (
        re.compile(r"\b(?:congestion|congested|disruption|bottleneck)\b", re.I),
        QueryClass.RELATIONSHIP, 2.0,
    ),
    (
        re.compile(r"\b(?:depend|dependenc|knock-on|ripple|cascad)\w*", re.I),
        QueryClass.RELATIONSHIP, 2.0,
    ),
    (
        re.compile(r"\b(?:alternativ|substitut|reroute|re-route)\w*", re.I),
        QueryClass.RELATIONSHIP, 1.8,
    ),
    (
        re.compile(r"\b(?:network|upstream|downstream|connected)\b", re.I),
        QueryClass.RELATIONSHIP, 1.5,
    ),
    (re.compile(r"\b(?:port|ports|terminal)\b", re.I), QueryClass.RELATIONSHIP, 1.0),
    (
        re.compile(r"\bwhich\s+(?:suppliers|ports|countries|partners)\b", re.I),
        QueryClass.RELATIONSHIP, 1.6,
    ),

    # Multi-hop: a condition joined to a consequence over our own records.
    (
        re.compile(
            r"\bif\b.*\b(?:takes effect|comes into force|applies|is imposed"
            r"|rises|increases)\b",
            re.I,
        ),
        QueryClass.MULTI_HOP, 2.4,
    ),
    (
        re.compile(r"\b(?:our|we|us)\b.*\b(?:shipments?|orders?|volumes?|records?)\b", re.I),
        QueryClass.MULTI_HOP, 2.0,
    ),
    (
        re.compile(r"\b(?:tariff|duty|sanction|quota)\b.*\bimpact\b", re.I),
        QueryClass.MULTI_HOP, 2.0,
    ),
    (re.compile(r"\bcompare\b|\bversus\b|\bvs\.?\b", re.I), QueryClass.MULTI_HOP, 1.4),
    (
        re.compile(r"\b(?:trend|over time|year[- ]on[- ]year|growth)\b", re.I),
        QueryClass.MULTI_HOP, 1.3,
    ),
    (
        re.compile(r"\bhow (?:much|many)\b.*\b(?:total|combined|across)\b", re.I),
        QueryClass.MULTI_HOP, 1.5,
    ),

    # Factual: names one thing and asks what it is.
    (
        re.compile(r"\bwhat (?:hts|hs|tariff) code\b", re.I),
        QueryClass.SIMPLE_FACTUAL, 2.6,
    ),
    (re.compile(r"\bclassif\w*\b", re.I), QueryClass.SIMPLE_FACTUAL, 1.4),
    (re.compile(r"\bwhat is the\b", re.I), QueryClass.SIMPLE_FACTUAL, 1.2),
    (
        re.compile(r"\b(?:duty rate|rate of duty|subheading)\b", re.I),
        QueryClass.SIMPLE_FACTUAL, 2.0,
    ),
    (re.compile(r"\bdefine\b|\bdefinition\b", re.I), QueryClass.SIMPLE_FACTUAL, 1.5),
]


class HeuristicClassifier:
    """Deterministic fallback. Not the spec's classifier, and says so."""

    is_llm = False
    method = "heuristic"

    def classify(self, query: str) -> Classification:
        scores = dict.fromkeys((c.value for c in QueryClass), 0.0)
        matched: list[str] = []

        # ── Evidence: the query actually said something routable ────────────
        evidence_total = 0.0
        for pattern, query_class, weight in SIGNALS:
            if pattern.search(query):
                scores[query_class.value] += weight
                evidence_total += weight
                matched.append(pattern.pattern[:40])

        # ── Priors: weak structural hints, deliberately kept out of the
        #    confidence calculation. They break ties between classes but must
        #    never make the router *sound* certain — a query that matched no
        #    signal at all is a query the router does not understand, and
        #    reporting 85% confidence on it would make the Route Badge a
        #    decoration rather than an audit surface.
        identifiers = bool(
            HTS_RE.search(query) or RULING_RE.search(query) or CHAPTER_RE.search(query)
        )
        if identifiers and scores[QueryClass.RELATIONSHIP.value] == 0:
            # An identifier with no network language is strong evidence of a
            # lookup, so this one does count.
            scores[QueryClass.SIMPLE_FACTUAL.value] += 1.6
            evidence_total += 1.6
            matched.append("explicit identifier")

        if len(query.split()) <= 8 and scores[QueryClass.MULTI_HOP.value] == 0:
            # Brevity is a hint, not evidence: it breaks ties without adding
            # to the confidence denominator.
            scores[QueryClass.SIMPLE_FACTUAL.value] += 0.8
            matched.append("short query (prior)")

        if evidence_total == 0:
            # Nothing routable fired. Defaulting to the fast path is the cheap,
            # safe choice — a wrong fast-path answer costs a second and is
            # visibly thin, where a wrong agentic route burns the budget the
            # whole project exists to conserve. Confidence stays low so the
            # badge shows the router guessed.
            return Classification(
                query_class=QueryClass.SIMPLE_FACTUAL,
                confidence=0.25,
                rationale=(
                    "No routing signal matched; defaulting to the fast path as "
                    "the cheapest option that can still answer."
                ),
                is_llm=False,
                method=self.method,
                scores=scores,
            )

        best = max(scores, key=lambda k: scores[k])
        # Confidence is the share of *evidence* behind the winner, capped: a
        # rule-based classifier should never claim near-certainty.
        share = min(1.0, scores[best] / evidence_total)
        confidence = min(0.85, 0.35 + 0.5 * share)

        return Classification(
            query_class=QueryClass(best),
            confidence=confidence,
            rationale=(
                f"Heuristic match on {len(matched)} signal(s); "
                f"strongest evidence for {best}."
            ),
            is_llm=False,
            method=self.method,
            scores=scores,
        )


class LLMClassifier:
    """The spec's classifier: one cheap few-shot call."""

    is_llm = True
    method = "llm_few_shot"

    def __init__(
        self,
        client: LLMClient | None = None,
        *,
        fallback: HeuristicClassifier | None = None,
    ) -> None:
        self.client = client or LLMClient()
        self.fallback = fallback or HeuristicClassifier()

    def classify(self, query: str) -> Classification:
        try:
            response = self.client.complete(
                build_prompt(query),
                system=SYSTEM_PROMPT,
                # The classifier must not cost more than the path it selects,
                # so the output budget is deliberately tiny.
                max_tokens=120,
                temperature=0.0,
            )
        except LLMUnavailableError as exc:
            log.warning("llm classifier unavailable; using heuristic", extra={"error": str(exc)})
            result = self.fallback.classify(query)
            result.rationale = f"{result.rationale} (LLM unavailable)"
            return result

        parsed = parse_response(response.text)
        if parsed is None:
            log.warning(
                "llm returned unparseable classification",
                extra={"text": response.text[:200]},
            )
            result = self.fallback.classify(query)
            result.rationale = f"{result.rationale} (LLM response unparseable)"
            result.usage = response.usage
            return result

        query_class, confidence, reason = parsed
        return Classification(
            query_class=query_class,
            confidence=confidence,
            rationale=reason or "Classified by few-shot LLM call.",
            is_llm=True,
            method=self.method,
            usage=response.usage,
        )


def build_classifier(backend: str = "auto"):
    """`auto` prefers the LLM and degrades to the heuristic."""
    backend = (backend or "auto").lower()
    if backend in {"heuristic", "rules", "offline"}:
        return HeuristicClassifier()
    if backend in {"llm", "few_shot", "few-shot"}:
        return LLMClassifier()
    if backend == "auto":
        client = LLMClient()
        return LLMClassifier(client) if client.is_available else HeuristicClassifier()
    raise ValueError(f"unknown classifier backend: {backend!r}")
