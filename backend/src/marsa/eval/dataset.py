"""The labelled routing test set — 60 queries, 20 per class.

Labels are hand-assigned, and the assignment rule is stated so a reader can
disagree with a specific label rather than the whole set:

* **simple_factual** — one document can answer it. Names a product, code or
  ruling and asks what it is or how it is classified.
* **multi_hop_reasoning** — needs two or more *dependent* retrievals across
  different corpora. A condition joined to a consequence over our own records.
* **relationship_network** — asks which *other* entities are affected through
  the supply network. Exposure, dependency, concentration, alternatives.

Roughly a quarter are tagged `ambiguous`, deliberately. A test set of only
clear-cut cases measures nothing interesting: every classifier scores well and
the systematic misrouting the spec asks to be reported never surfaces. The
ambiguous cases are where the interesting failures live, and they are labelled
with *why* they are hard.

Difficulty is recorded per query so accuracy can be reported split by it — an
aggregate number that mixes trivial and genuinely hard cases hides more than it
shows.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from marsa.router.classifier import QueryClass


class Difficulty(StrEnum):
    CLEAR = "clear"
    MODERATE = "moderate"
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True)
class LabelledQuery:
    query: str
    expected: QueryClass
    difficulty: Difficulty
    #: Why this label, in one line. The audit trail for a human judgement.
    rationale: str
    #: Free-text tags for slicing the results.
    tags: tuple[str, ...] = ()

    @property
    def expected_path(self) -> str:
        return {
            QueryClass.SIMPLE_FACTUAL: "fast",
            QueryClass.MULTI_HOP: "agentic",
            QueryClass.RELATIONSHIP: "graph",
        }[self.expected]

    def as_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "expected": self.expected.value,
            "expectedPath": self.expected_path,
            "difficulty": self.difficulty.value,
            "rationale": self.rationale,
            "tags": list(self.tags),
        }


F = QueryClass.SIMPLE_FACTUAL
M = QueryClass.MULTI_HOP
R = QueryClass.RELATIONSHIP
C, MO, A = Difficulty.CLEAR, Difficulty.MODERATE, Difficulty.AMBIGUOUS


# ─────────────────────────────────────────────────────────────────────────────
# simple_factual — 20
# ─────────────────────────────────────────────────────────────────────────────

FACTUAL: list[LabelledQuery] = [
    LabelledQuery("What HTS code applies to lithium-ion power banks?", F, C,
                  "Single-entity classification lookup.", ("classification",)),
    LabelledQuery("Under which subheading are knitted cotton t-shirts classified?", F, C,
                  "Names one article, asks for its subheading.", ("classification",)),
    LabelledQuery("What is the duty rate for 8507.60.0020?", F, C,
                  "Explicit code, single attribute requested.", ("code",)),
    LabelledQuery("What does ruling NY N302241 hold?", F, C,
                  "Names one ruling directly.", ("ruling",)),
    LabelledQuery("How are semiconductor light-emitting diodes classified?", F, C,
                  "One product, one classification question.", ("classification",)),
    LabelledQuery("Define 'parts of general use' under Section XV.", F, C,
                  "Definitional lookup from the tariff notes.", ("definition",)),
    LabelledQuery("What is the applicable subheading for a brushless DC motor?", F, C,
                  "Single article classification.", ("classification",)),
    LabelledQuery("Which heading covers wooden office furniture?", F, C,
                  "One article, one heading.", ("classification",)),
    LabelledQuery("What HTS code applies to printed circuit assemblies?", F, C,
                  "Single-entity lookup.", ("classification",)),
    LabelledQuery("Is a power bank classified under 8507 or 8504?", F, MO,
                  "Names two competing headings but still resolves from one ruling.",
                  ("classification", "competing-headings")),
    LabelledQuery("What is the rate of duty on woven polyester trousers?", F, C,
                  "One article, one rate.", ("duty",)),
    LabelledQuery("Which subheading applies to a stainless steel vacuum flask?", F, C,
                  "Single article.", ("classification",)),
    LabelledQuery("What does HQ H289765 say about essential character?", F, C,
                  "Names one ruling and a topic within it.", ("ruling",)),
    LabelledQuery("How is an air conditioning split system classified?", F, C,
                  "One article.", ("classification",)),
    LabelledQuery("What HS chapter covers vehicles other than railway?", F, C,
                  "Chapter-level lookup, answerable from the schedule.", ("chapter",)),
    LabelledQuery("What is the classification of leather upper footwear?", F, C,
                  "One article.", ("classification",)),
    # ── Ambiguous: reads factual but the phrasing invites a broader answer ──
    LabelledQuery("What tariff treatment applies to our power bank imports?", F, A,
                  "Says 'our', which usually signals multi-hop, but the question is "
                  "still a single classification lookup — the possessive is incidental.",
                  ("classification", "possessive-trap")),
    LabelledQuery("Which rulings cover lithium-ion batteries?", F, A,
                  "Plural 'rulings' hints at traversal, but this is a corpus search, "
                  "not a network question.", ("ruling", "plural-trap")),
    LabelledQuery("Is 8541.41.0000 the right code for LEDs?", F, C,
                  "Verification of one code against one article.", ("code",)),
    LabelledQuery("What is the general rate of duty for HS chapter 61?", F, MO,
                  "Chapter-level, but still a single lookup rather than a join.",
                  ("chapter",)),
]


# ─────────────────────────────────────────────────────────────────────────────
# multi_hop_reasoning — 20
# ─────────────────────────────────────────────────────────────────────────────

MULTI_HOP: list[LabelledQuery] = [
    LabelledQuery(
        "Which of our electronics shipments are exposed if the new tariff on HS 8541 "
        "takes effect next quarter?", M, C,
        "Resolve affected lines, join to trade flows, intersect with our shipments.",
        ("tariff-impact",)),
    LabelledQuery(
        "How much of our HS 85 volume comes from partners covered by the proposed measure?",
        M, C, "Volume aggregation joined to a policy condition.", ("tariff-impact",)),
    LabelledQuery(
        "Compare our import values for HS 84 and HS 85 over the last three years.",
        M, C, "Two aggregations across a time dimension.", ("comparison",)),
    LabelledQuery(
        "What is the total value of goods we import from China under chapter 61?",
        M, MO, "Aggregation across a corpus, not a single-document lookup.",
        ("aggregation",)),
    LabelledQuery(
        "If duties on Chinese electronics rise, how many of our orders are affected?",
        M, C, "Condition joined to a consequence over our records.", ("tariff-impact",)),
    LabelledQuery(
        "Which product categories show the steepest growth in import value year on year?",
        M, C, "Trend analysis across periods.", ("trend",)),
    LabelledQuery(
        "How does our late-delivery rate compare between expedited and standard shipping?",
        M, C, "Comparison across a segmentation of our own records.", ("comparison",)),
    LabelledQuery(
        "What share of our HS 62 imports would be affected by a quota on Vietnam?",
        M, C, "Policy condition joined to trade flows and our records.", ("tariff-impact",)),
    LabelledQuery(
        "Across all markets, how many shipments missed their scheduled delivery window?",
        M, C, "Aggregation with a computed condition.", ("aggregation",)),
    LabelledQuery(
        "Compare UAE import volumes from India versus Korea for machinery.",
        M, C, "Two-way comparison over bilateral flows.", ("comparison",)),
    LabelledQuery(
        "What is the combined trade value across chapters 84 and 85 for 2023?",
        M, C, "Multi-chapter aggregation.", ("aggregation",)),
    LabelledQuery(
        "If Jebel Ali dwell time doubles, how much of our order value is at risk?",
        M, A,
        "Names a port, which pulls toward the graph path, but the question asks for "
        "a *value* aggregation over our own records — the port is a filter, not the "
        "subject.", ("port-trap", "tariff-impact")),
    LabelledQuery(
        "How has our average shipping slack changed since 2022?", M, C,
        "Trend over our own records.", ("trend",)),
    LabelledQuery(
        "What proportion of electronics orders ship via Same Day and how often are they late?",
        M, C, "Two dependent aggregations.", ("aggregation",)),
    LabelledQuery(
        "Which of our top five categories has the worst on-time performance?",
        M, C, "Ranking over an aggregation of our records.", ("ranking",)),
    LabelledQuery(
        "How would a 10% duty increase on chapter 94 affect our landed costs?",
        M, C, "Policy condition applied to our cost base.", ("tariff-impact",)),
    LabelledQuery(
        "Compare trade flows into the UAE before and after 2022 for apparel.",
        M, C, "Temporal comparison over bilateral flows.", ("comparison",)),
    LabelledQuery(
        "What is our exposure by value to partners outside the GCC?", M, A,
        "Uses 'exposure', which strongly signals the graph path, but asks for a "
        "monetary aggregation rather than a network traversal.",
        ("exposure-trap", "aggregation")),
    LabelledQuery(
        "How many distinct HS chapters do our shipments span, and which dominates?",
        M, MO, "Aggregation plus a ranking.", ("aggregation",)),
    LabelledQuery(
        "Given rising freight costs, which shipping modes remain economical for us?",
        M, MO, "Condition applied across our own mode-level records.", ("comparison",)),
]


# ─────────────────────────────────────────────────────────────────────────────
# relationship_network — 20
# ─────────────────────────────────────────────────────────────────────────────

NETWORK: list[LabelledQuery] = [
    LabelledQuery("Which suppliers are exposed if Jebel Ali congestion worsens?", R, C,
                  "Asks who else is affected through the port network.", ("port",)),
    LabelledQuery("What depends on Singapore as a transhipment point?", R, C,
                  "Dependency traversal from one node.", ("port", "dependency")),
    LabelledQuery("If Shanghai congests, which of our product categories are hit?", R, C,
                  "Disruption propagating across the network.", ("port",)),
    LabelledQuery("Which countries have no alternative gateway port?", R, C,
                  "Concentration/substitutability question.", ("concentration",)),
    LabelledQuery("Which trading partners are most connected to the UAE?", R, C,
                  "Connectivity over the trade graph.", ("connectivity",)),
    LabelledQuery("What is downstream of Rotterdam in our supply network?", R, C,
                  "Explicit downstream traversal.", ("port", "dependency")),
    LabelledQuery("Which ports would absorb traffic if Nhava Sheva closed?", R, C,
                  "Alternative-routing question.", ("port", "alternatives")),
    LabelledQuery("Which categories share a single point of failure in their routing?", R, C,
                  "Concentration risk across the network.", ("concentration",)),
    LabelledQuery("How does congestion at Los Angeles ripple through our suppliers?", R, C,
                  "Explicit ripple/cascade language.", ("port", "cascade")),
    LabelledQuery("Which suppliers depend on more than one congested corridor?", R, C,
                  "Multi-dependency traversal.", ("dependency",)),
    LabelledQuery("What is connected to HS chapter 85 across the supply graph?", R, MO,
                  "Names a chapter but asks for its network neighbourhood.",
                  ("chapter", "connectivity")),
    LabelledQuery("Which countries are upstream of our electronics shipments?", R, C,
                  "Upstream traversal.", ("dependency",)),
    LabelledQuery("If Busan is disrupted, what is the knock-on effect on our lanes?", R, C,
                  "Knock-on effect through the network.", ("port", "cascade")),
    LabelledQuery("Which ports serve the same countries as Jebel Ali?", R, C,
                  "Sibling/alternative discovery.", ("port", "alternatives")),
    LabelledQuery("Which of our suppliers cannot reroute around a Gulf disruption?", R, C,
                  "Substitutability across a region.", ("alternatives", "concentration")),
    LabelledQuery("What ruling network surrounds NY N302241?", R, A,
                  "Names a ruling, which pulls toward the fast path, but asks for its "
                  "citation *network* — a traversal over cites edges.",
                  ("ruling-trap", "connectivity")),
    LabelledQuery("Which countries share dependency on Tanger Med?", R, C,
                  "Shared-dependency traversal.", ("port", "dependency")),
    LabelledQuery("Show the supplier network behind our footwear category.", R, C,
                  "Explicit network request.", ("connectivity",)),
    LabelledQuery("Which corridors are most exposed to a simultaneous Asia disruption?", R, MO,
                  "Multi-node exposure question.", ("cascade",)),
    LabelledQuery("What else is affected if Khalifa Port loses capacity?", R, C,
                  "'What else is affected' is the network question in plain words.",
                  ("port", "cascade")),
]


LABELLED_QUERIES: list[LabelledQuery] = [*FACTUAL, *MULTI_HOP, *NETWORK]


def by_class(query_class: QueryClass) -> list[LabelledQuery]:
    return [q for q in LABELLED_QUERIES if q.expected is query_class]


def by_difficulty(difficulty: Difficulty) -> list[LabelledQuery]:
    return [q for q in LABELLED_QUERIES if q.difficulty is difficulty]


def dataset_summary() -> dict[str, Any]:
    return {
        "total": len(LABELLED_QUERIES),
        "byClass": {c.value: len(by_class(c)) for c in QueryClass},
        "byDifficulty": {d.value: len(by_difficulty(d)) for d in Difficulty},
    }


def validate_dataset() -> list[str]:
    """Structural checks on the test set itself.

    A test set with duplicates or an unbalanced class distribution produces
    accuracy figures that are wrong in ways nobody notices, so this runs in CI.
    """
    problems: list[str] = []

    counts = {c: len(by_class(c)) for c in QueryClass}
    if len(set(counts.values())) != 1:
        problems.append(f"classes are unbalanced: {counts}")

    seen: set[str] = set()
    for item in LABELLED_QUERIES:
        key = item.query.strip().lower()
        if key in seen:
            problems.append(f"duplicate query: {item.query!r}")
        seen.add(key)
        if not item.rationale.strip():
            problems.append(f"no rationale for {item.query!r}")

    ambiguous = len(by_difficulty(Difficulty.AMBIGUOUS))
    if ambiguous < 4:
        problems.append(
            f"only {ambiguous} ambiguous queries — a set of clear-cut cases "
            "cannot surface systematic misrouting"
        )

    return problems
