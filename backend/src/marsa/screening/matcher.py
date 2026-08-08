"""Company-name matching for screening.

The lookup is not the hard part. This is:

    "Hefei Bitland Information Technology Co., Ltd."
    "Bitland (Hefei) Information Tech"
    "HEFEI BITLAND INFORMATION TECHNOLOGY CO LTD"

Three spellings of one company, and a screening tool that matches only the
first has told an importer their supplier is fine.

The normalisation below strips the parts that carry no identity — legal-form
suffixes, punctuation, corporate filler — and keeps the parts that do. It is
deliberately conservative about what it strips: removing a word that turns out
to be distinguishing merges two different companies, and a false merge is a
false HIT, which costs credibility. A false *miss* costs a detained container,
so where the two trade off this errs toward matching.

Scores are similarity, not probability. They are shown to a human who decides.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher

#: Legal forms and corporate filler. Stripped because they are shared by
#: thousands of unrelated companies and so carry no identifying signal.
NOISE_TOKENS = frozenset({
    "co", "company", "corp", "corporation", "inc", "incorporated",
    "ltd", "limited", "llc", "lp", "llp", "plc", "gmbh", "ag", "sa", "srl",
    "bv", "nv", "pte", "pty", "kk", "kabushiki", "kaisha", "oyj", "ab", "as",
    "group", "holdings", "holding", "international", "intl", "industries",
    "industrial", "enterprise", "enterprises", "trading", "import", "export",
    "manufacturing", "mfg", "technology", "technologies", "tech",
    "the", "and", "of",
})

#: Below this a pair is not reported at all — the noise floor. Chosen so that
#: two unrelated Chinese manufacturers sharing "Jiangsu" do not surface.
FLOOR = 0.62

#: At or above this the names are treated as the same entity.
HIT = 0.92


def normalise(name: str) -> str:
    """Reduce a company name to its identifying tokens.

    Unicode is folded first: the same company appears with full-width Latin,
    accented characters, and CJK punctuation across sources, and comparing the
    raw forms silently fails.
    """
    folded = unicodedata.normalize("NFKD", name)
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    lowered = folded.lower()
    # Parenthesised qualifiers usually hold the city — "Bitland (Hefei)" — which
    # is identifying, so the brackets go and the contents stay.
    stripped = re.sub(r"[^\w\s]", " ", lowered)
    tokens = [t for t in stripped.split() if t and t not in NOISE_TOKENS]
    return " ".join(sorted(tokens))


@dataclass
class Match:
    """One candidate, with everything a human needs to judge it."""

    query: str
    listed_name: str
    score: float
    normalised_query: str
    normalised_listed: str
    shared_tokens: list[str]

    @property
    def is_hit(self) -> bool:
        return self.score >= HIT

    def as_dict(self) -> dict[str, object]:
        return {
            "query": self.query,
            "listedName": self.listed_name,
            "score": round(self.score, 4),
            "normalisedQuery": self.normalised_query,
            "normalisedListed": self.normalised_listed,
            "sharedTokens": self.shared_tokens,
            "isHit": self.is_hit,
        }


def similarity(left: str, right: str) -> float:
    """Similarity of two company names, after normalisation.

    Combines sequence similarity with token overlap. Sequence alone is fooled by
    reordering — "Bitland Hefei" against "Hefei Bitland" — and token overlap
    alone is fooled by a single shared word. Taking the higher of the two, then
    requiring a shared token to score at all, keeps both failure modes out.
    """
    a, b = normalise(left), normalise(right)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0

    a_tokens, b_tokens = set(a.split()), set(b.split())
    shared = a_tokens & b_tokens
    if not shared:
        # No identifying token in common. Sequence similarity between two
        # unrelated names can still reach 0.6 on shared letters alone, which is
        # how a screening list starts producing noise nobody reads.
        return 0.0

    sequence = SequenceMatcher(None, a, b).ratio()
    overlap = len(shared) / max(len(a_tokens), len(b_tokens))
    return max(sequence, overlap)


def match(query: str, listed_names: list[str], *, floor: float = FLOOR) -> list[Match]:
    """Every listed name that could plausibly be `query`, best first."""
    out: list[Match] = []
    normalised_query = normalise(query)
    for listed in listed_names:
        score = similarity(query, listed)
        if score < floor:
            continue
        normalised_listed = normalise(listed)
        out.append(
            Match(
                query=query,
                listed_name=listed,
                score=score,
                normalised_query=normalised_query,
                normalised_listed=normalised_listed,
                shared_tokens=sorted(
                    set(normalised_query.split()) & set(normalised_listed.split())
                ),
            )
        )
    return sorted(out, key=lambda m: m.score, reverse=True)
