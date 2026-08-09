"""Retrieval relevance ground truth for the fast path.

Why this file exists
--------------------
Context precision and recall need to know which documents *should* have come
back. The obvious shortcut — treat whatever the retriever ranked first as the
relevant one — makes the metric circular: precision collapses to ``1/k`` and
measures the value of ``k``, not the quality of retrieval. An earlier revision
of the harness did exactly that, and reported 0.200 for every path because
every path returned five sources.

The labels here are independent of the retriever in the way that matters.
Relevance is decided in two steps, and only the first involves judgement:

1. **By hand, once per query:** which tariff provision does this question
   actually ask about? That is a reading of the *question*, recorded below with
   a rationale, and it never consults what the system retrieved.
2. **Mechanically, from the corpus:** a ruling is relevant iff **CBP** assigned
   it a code under that provision. The ``hts_codes`` field is CBP's own
   classification, carried through ingestion unmodified — 403 of the 416
   ingested rulings have one. Nothing in this project decides it.

So the ground truth comes from the source authority, not from the system under
test. A reviewer who disagrees can argue with a specific ``target`` line rather
than with the whole metric.

Prefix granularity is deliberate
--------------------------------
Targets are HTS prefixes matched against the start of CBP's assigned codes, and
the prefix length mirrors what the question asks. "Which *heading* covers
wooden office furniture" is a heading-level question; "the duty rate for
8507.60.0020" is a subheading-level one. Labelling everything at six digits
would mark chapter-level questions wrong for returning the right chapter.

Deliberately empty label sets
-----------------------------
Five queries have ``target=()`` with ``unanswerable=True``. These are not
oversights — the corpus genuinely contains no document that answers them (two
name rulings that were never ingested; three ask about provisions no ingested
ruling classifies under). Their correct behaviour is abstention, so they are
excluded from precision and recall and reported separately. They are also the
evaluation substrate for the Phase J abstention head.

Two queries carry ``target=()`` with ``unanswerable=False`` — meaning *not
labelled*, because no defensible single provision exists (a printed circuit
assembly is classified as a part of whatever machine it serves). They are
excluded from every retrieval figure and counted in the coverage line, rather
than being given a generous label that would flatter the score.

Coverage: 13 of 20 fast-path queries scored, 5 unanswerable, 2 unlabelled.
"""

from __future__ import annotations

from dataclasses import dataclass

# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RelevanceLabel:
    """Which tariff provision a query asks about, and why."""

    #: HTS prefixes. A ruling is relevant iff any CBP-assigned code starts with
    #: one of these. Empty means "no relevant document" — see `unanswerable`.
    target: tuple[str, ...]
    #: The hand judgement, in one line. The audit trail for step 1.
    rationale: str
    #: True when the corpus genuinely cannot answer, so abstention is correct.
    #: False with an empty target means "not labelled" — excluded, not scored.
    unanswerable: bool = False

    @property
    def is_scored(self) -> bool:
        return bool(self.target)


U = True  # unanswerable, for readability in the table below


FAST_PATH_RELEVANCE: dict[str, RelevanceLabel] = {
    "What HTS code applies to lithium-ion power banks?": RelevanceLabel(
        ("8507.60", "8504.40"),
        "Power banks sit on a real classification fault line: a lithium-ion "
        "accumulator (8507.60) or a static converter (8504.40). Both are "
        "defensible and CBP has ruled both ways, so both are relevant.",
    ),
    "Under which subheading are knitted cotton t-shirts classified?": RelevanceLabel(
        ("6109.10",),
        "6109.10 is T-shirts, singlets and other vests, knitted, of cotton — "
        "the subheading the question names in words.",
    ),
    "What is the duty rate for 8507.60.0020?": RelevanceLabel(
        ("8507.60",),
        "The question states the subheading; relevance is any ruling CBP "
        "classified under it.",
    ),
    "What does ruling NY N302241 hold?": RelevanceLabel(
        (), "N302241 was not returned by any search term and is absent from the "
            "ingested subset. No document can answer this.", U,
    ),
    "How are semiconductor light-emitting diodes classified?": RelevanceLabel(
        ("8541.40",),
        "LEDs fall in 8541.40 under the nomenclature these rulings were issued "
        "against. (8541.41 is the post-2022 split — see the LED query below.)",
    ),
    "Define 'parts of general use' under Section XV.": RelevanceLabel(
        (), "A Section XV legal-note definition, not a commodity. No "
            "classification ruling is on point; the notes are not in this corpus.", U,
    ),
    "What is the applicable subheading for a brushless DC motor?": RelevanceLabel(
        ("8501.10", "8501.31"),
        "DC motors split on output: 8501.10 at or below 37.5 W, 8501.31 above "
        "it to 750 W. The question fixes neither, so both are relevant.",
    ),
    "Which heading covers wooden office furniture?": RelevanceLabel(
        ("9403.30",),
        "9403.30 is wooden furniture of a kind used in offices. Labelled at "
        "subheading rather than the whole 9403 heading: a bedroom-furniture "
        "ruling does not establish where office furniture goes.",
    ),
    "What HTS code applies to printed circuit assemblies?": RelevanceLabel(
        (), "Not labelled. A PCA is classified as a part of whatever machine it "
            "serves, so there is no single defensible target — the corpus has "
            "them under 8473.30, 8517.90, 8529.90 and 8538.90 alike. Scoring "
            "this against any one of those would be arbitrary.",
    ),
    "Is a power bank classified under 8507 or 8504?": RelevanceLabel(
        ("8507.60", "8504.40"),
        "The question names the fault line explicitly. Same target as the power "
        "bank query above.",
    ),
    "What is the rate of duty on woven polyester trousers?": RelevanceLabel(
        (), "Synthetic-fibre woven trousers are 6203.43 (men's) or 6204.63 "
            "(women's); the corpus has neither. The one 6204 ruling covers "
            "cotton and 'other textile materials', not polyester.", U,
    ),
    "Which subheading applies to a stainless steel vacuum flask?": RelevanceLabel(
        (), "Vacuum flasks are heading 9617, which no ingested ruling touches.", U,
    ),
    "What does HQ H289765 say about essential character?": RelevanceLabel(
        (), "H289765 is absent from the ingested subset.", U,
    ),
    "How is an air conditioning split system classified?": RelevanceLabel(
        ("8415.10",),
        "8415.10 covers window or wall air conditioning machines including the "
        "split-system type, which is what the question names.",
    ),
    "What HS chapter covers vehicles other than railway?": RelevanceLabel(
        ("87",),
        "A chapter-level question, so a chapter-level target: chapter 87 is "
        "vehicles other than railway or tramway rolling stock.",
    ),
    "What is the classification of leather upper footwear?": RelevanceLabel(
        ("6403",),
        "Heading 6403 is footwear with uppers of leather. Heading-level because "
        "the question does not narrow beyond the upper material.",
    ),
    "What tariff treatment applies to our power bank imports?": RelevanceLabel(
        (), "Not labelled. 'Tariff treatment' for 'our imports' spans the "
            "classification, the Section 301 measures in chapter 99 and any "
            "exclusions, with no principled boundary. Left out rather than "
            "given a broad label that would inflate precision.",
    ),
    "Which rulings cover lithium-ion batteries?": RelevanceLabel(
        ("8507.60",),
        "Lithium-ion accumulators are 8507.60 specifically, not the wider 8507.",
    ),
    "Is 8541.41.0000 the right code for LEDs?": RelevanceLabel(
        ("8541.40",),
        "8541.41 is the correct current code, but it entered the nomenclature "
        "in the 2022 revision and no ingested ruling carries it — they classify "
        "LEDs under the predecessor 8541.40. Relevance follows the corpus.",
    ),
    "What is the general rate of duty for HS chapter 61?": RelevanceLabel(
        ("61",),
        "A chapter-level question about knitted apparel; chapter-level target.",
    ),
}


# ─────────────────────────────────────────────────────────────────────────────
# Graph path
# ─────────────────────────────────────────────────────────────────────────────

#: Ports the relationship queries name. Kept explicit so the "this query is
#: about a port" test does not depend on a substring heuristic that would also
#: fire on ordinary words. Every one of these is a real container port; the
#: point is that none of them is a node in the graph.
PORTS_NAMED_IN_QUERIES: tuple[str, ...] = (
    "jebel ali", "singapore", "shanghai", "rotterdam", "nhava sheva",
    "los angeles", "busan", "tanger med", "khalifa port",
)

#: Country names as they appear in queries, mapped to the ISO3 keys the graph
#: uses for its country nodes. Only the countries the graph actually holds.
COUNTRY_ALIASES: dict[str, str] = {
    "uae": "ARE", "united arab emirates": "ARE", "emirates": "ARE",
    "china": "CHN", "chinese": "CHN",
    "india": "IND", "indian": "IND",
    "vietnam": "VNM", "vietnamese": "VNM",
    "united states": "USA", "usa": "USA", "america": "USA",
    "germany": "DEU", "japan": "JPN", "korea": "KOR", "south korea": "KOR",
    "singapore": "SGP", "netherlands": "NLD", "belgium": "BEL",
    "turkey": "TUR", "egypt": "EGY", "brazil": "BRA", "mexico": "MEX",
    "saudi arabia": "SAU", "oman": "OMN", "qatar": "QAT", "kuwait": "KWT",
    "bahrain": "BHR", "morocco": "MAR", "pakistan": "PAK",
    "united kingdom": "GBR", "britain": "GBR", "france": "FRA", "italy": "ITA",
    "spain": "ESP", "canada": "CAN", "australia": "AUS", "south africa": "ZAF",
}


def names_a_port(query: str) -> bool:
    """Whether the question is about a port the graph has no node for."""
    low = query.lower()
    return any(port in low for port in PORTS_NAMED_IN_QUERIES)


def relevant_rulings(
    label: RelevanceLabel, corpus: dict[str, list[str]]
) -> set[str]:
    """Rulings CBP classified under the label's target provisions.

    `corpus` maps ruling number -> the HTS codes CBP assigned to it. The
    membership test is a prefix match, so the caller's chosen granularity is
    honoured exactly as written.
    """
    if not label.target:
        return set()
    return {
        number
        for number, codes in corpus.items()
        if any(code.startswith(prefix) for code in codes for prefix in label.target)
    }


def coverage() -> dict[str, int]:
    """How much of the fast-path set carries a scoreable label."""
    labels = FAST_PATH_RELEVANCE.values()
    return {
        "total": len(FAST_PATH_RELEVANCE),
        "scored": sum(1 for x in labels if x.is_scored),
        "unanswerable": sum(1 for x in labels if x.unanswerable),
        "unlabelled": sum(1 for x in labels if not x.is_scored and not x.unanswerable),
    }
