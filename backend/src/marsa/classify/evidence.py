"""Deterministic grounding signals, read off what retrieval actually returned.

Nothing here calls a model. That is deliberate on two counts: it costs nothing
against a free tier that rate-limits long before it bills, and a hallucinating
judge of whether the evidence is sufficient would be the one failure this whole
phase exists to prevent.

Four weighted signals, each answering a different way the top-*k* can look
convincing while meaning nothing, plus one veto:

* **provision agreement** — five rulings all classified under 8507.60 say
  something; five rulings under five different subheadings say only that the
  index is non-empty.
* **arm agreement** — reciprocal rank fusion exists to paper over the sparse
  and dense arms disagreeing, and it throws the disagreement away. Both arms
  independently surfacing the same ruling is a stronger claim than either
  ranking it first, so the residual is kept rather than discarded.
* **lexical anchoring** — a ruling that answers a question about power banks
  should contain the words. Cheap, and it catches the case where semantic
  similarity has drifted onto a neighbouring commodity.
* **code presence** — when the question names a code, whether any retrieved
  ruling actually carries it. Only sometimes applicable, and decisive when it
  is.

The veto — **named ruling missing** — was not in the original design. It was
added because the first measured curve exposed the gap: "What does HQ H289765
say about essential character?" scored 0.688 and answered confidently, because
the five rulings it retrieved all sat under one subheading and all discussed
essential character at length. Every weighted signal read as strong evidence,
and every one of them was evidence about the wrong documents.

The weights below are fixed and stated rather than fitted. Tuning them on the
same thirteen queries the abstention curve is measured over would report the
fit, not the behaviour.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from marsa.indexing.hybrid import RetrievedRuling

#: Words that carry no commodity meaning. Kept small on purpose: an aggressive
#: list starts deleting the terms that distinguish one subheading from another.
STOPWORDS = frozenset(
    """
    a an the what which how is are was were do does did can could would should
    of for to under over from with without and or but not no if then than that
    this these those our your their its it as at by on in into about between
    apply applies applied classify classified classification code codes rate
    rates duty duties subheading heading chapter tariff hts hs treatment right
    covered covers cover hold holds held say says said define defined general
    """.split()  # noqa: SIM905 — one word per column reads better than a list literal
)

#: A six-digit subheading is the finest granularity that is stable across
#: national tariff schedules, so agreement is measured there rather than on the
#: eight- or ten-digit statistical suffixes.
SUBHEADING = 7  # "8507.60"

CODE_IN_QUERY = re.compile(r"\b\d{4}\.\d{2}(?:\.\d{2,4})?\b")

#: CROSS ruling identifiers: an optional NY/HQ collection prefix, a letter, and
#: five or six digits. `N302241`, `HQ H289765`, `NY N302241`.
RULING_IN_QUERY = re.compile(r"\b(?:NY|HQ)?\s*([A-Z]\d{5,6})\b")


def content_terms(text: str) -> set[str]:
    """Meaning-bearing words, lowercased."""
    words = re.findall(r"[a-z][a-z\-]{2,}", text.lower())
    return {w for w in words if w not in STOPWORDS}


def _subheadings(ruling: RetrievedRuling) -> set[str]:
    return {code[:SUBHEADING] for code in ruling.hts_codes if len(code) >= SUBHEADING}


# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class EvidenceSignals:
    """What the retrieved set looks like, before any judgement about it."""

    provision_agreement: float = 0.0
    arm_agreement: float = 0.0
    lexical_anchoring: float = 0.0
    #: None when the query names no code, so the signal does not apply. A
    #: not-applicable signal is dropped from the weighting rather than scored
    #: zero — scoring it zero would penalise every query that happens not to
    #: quote a number.
    code_presence: float | None = None

    retrieved: int = 0
    #: The subheading the retrieved set points at, when it points anywhere.
    modal_subheading: str = ""
    #: A ruling the query named that retrieval did not return. Set means the
    #: evidence is disqualified outright, not merely weak — see `confidence`.
    missing_ruling: str = ""
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "provisionAgreement": round(self.provision_agreement, 4),
            "armAgreement": round(self.arm_agreement, 4),
            "lexicalAnchoring": round(self.lexical_anchoring, 4),
            "codePresence": (
                None if self.code_presence is None else round(self.code_presence, 4)
            ),
            "retrieved": self.retrieved,
            "modalSubheading": self.modal_subheading,
            "missingRuling": self.missing_ruling,
            "notes": list(self.notes),
        }


#: Deliberately round numbers. See the module docstring on why these are not
#: fitted. Provision agreement leads because it is the only signal that speaks
#: to whether the rulings agree on an *answer* rather than on a document.
WEIGHTS: dict[str, float] = {
    "provision_agreement": 0.40,
    "arm_agreement": 0.25,
    "lexical_anchoring": 0.15,
    "code_presence": 0.20,
}


def provision_agreement(rulings: list[RetrievedRuling]) -> tuple[float, str]:
    """How concentrated the retrieved set is on one subheading.

    Returns the modal subheading's share of the rulings that carry any code at
    all, plus the subheading itself. Rulings with no assigned code are excluded
    from the denominator rather than counted against agreement: they are silent,
    not dissenting.
    """
    counts: Counter[str] = Counter()
    with_codes = 0
    for ruling in rulings:
        subs = _subheadings(ruling)
        if not subs:
            continue
        with_codes += 1
        # A ruling classifying one article under several subheadings votes
        # fractionally for each, so a multi-article ruling cannot manufacture
        # agreement on its own.
        for sub in subs:
            counts[sub] += 1 / len(subs)

    if not with_codes:
        return 0.0, ""
    top, share = counts.most_common(1)[0]
    return share / with_codes, top


def arm_agreement(rulings: list[RetrievedRuling]) -> float:
    """Share of retrieved rulings that both retrieval arms found.

    `reciprocal_rank_fusion` records the contributing arms on each fused chunk,
    and `ScoredChunk.arms` carries them through reranking — which overwrites
    `retriever` and would otherwise erase the provenance. A ruling counts as
    corroborated when any of its chunks was found by more than one arm.
    """
    if not rulings:
        return 0.0
    corroborated = sum(
        1 for ruling in rulings if any(len(chunk.arms) > 1 for chunk in ruling.chunks)
    )
    return corroborated / len(rulings)


def lexical_anchoring(query: str, rulings: list[RetrievedRuling]) -> float:
    """Share of the query's content terms present in the best-ranked ruling.

    Measured against the top ruling only. Averaging across the set would let
    four irrelevant rulings dilute a first-place exact match, which is the
    opposite of what the signal is for.
    """
    terms = content_terms(query)
    if not terms or not rulings:
        return 0.0
    text = " ".join(chunk.chunk.text for chunk in rulings[0].chunks).lower()
    return sum(1 for term in terms if term in text) / len(terms)


def code_presence(query: str, rulings: list[RetrievedRuling]) -> float | None:
    """Whether a code named in the query appears in the retrieved set.

    None when the query names no code — the signal does not apply and is
    dropped from the weighting rather than scored as a failure.
    """
    named = CODE_IN_QUERY.findall(query)
    if not named:
        return None
    wanted = {code[:SUBHEADING] for code in named}
    found = set()
    for ruling in rulings:
        found |= _subheadings(ruling)
    return 1.0 if wanted & found else 0.0


def named_ruling_missing(query: str, rulings: list[RetrievedRuling]) -> str:
    """A ruling the query names by number that retrieval did not return.

    This is not a matter of degree. "What does HQ H289765 say about essential
    character?" is a question about one document; an answer assembled from five
    other documents is not a worse answer to it, it is an answer to a different
    question. Without this check the query scores 0.688 — the five rulings it
    retrieves all sit under one subheading and all discuss essential character
    at length, so agreement and anchoring both read as strong evidence.

    Returns the missing identifier, or an empty string when the query names no
    ruling or names one that was retrieved.
    """
    named = {m.upper() for m in RULING_IN_QUERY.findall(query.upper())}
    if not named:
        return ""
    got = {ruling.ruling_number.upper() for ruling in rulings}
    missing = sorted(named - got)
    return missing[0] if missing else ""


def assess(query: str, rulings: list[RetrievedRuling]) -> EvidenceSignals:
    """Score every applicable signal over one retrieved set."""
    agreement, modal = provision_agreement(rulings)
    signals = EvidenceSignals(
        provision_agreement=agreement,
        arm_agreement=arm_agreement(rulings),
        lexical_anchoring=lexical_anchoring(query, rulings),
        code_presence=code_presence(query, rulings),
        retrieved=len(rulings),
        modal_subheading=modal,
        missing_ruling=named_ruling_missing(query, rulings),
    )

    if not rulings:
        signals.notes.append("Retrieval returned nothing.")
    elif not modal:
        signals.notes.append(
            "No retrieved ruling carries a CBP-assigned code, so there is "
            "nothing to agree on."
        )
    if signals.code_presence == 0.0:
        signals.notes.append(
            "The query names a code that no retrieved ruling is classified under."
        )
    if signals.missing_ruling:
        signals.notes.append(
            f"The query asks about ruling {signals.missing_ruling}, which is not "
            "in the ingested corpus. Other rulings cannot answer a question about "
            "that one."
        )
    return signals


def confidence(signals: EvidenceSignals) -> float:
    """Weighted mean over the signals that apply, renormalised.

    Renormalising rather than treating a missing signal as zero keeps a query
    that quotes no code on the same scale as one that does.

    A named-but-missing ruling is a veto rather than a weighted term. Weighting
    it would let strong agreement and anchoring outvote it, which is precisely
    the failure observed: five rulings that all discuss essential character
    under one subheading scored 0.688 for a question about a sixth ruling that
    was never ingested. No amount of evidence about other documents is evidence
    about that one.
    """
    if signals.missing_ruling:
        return 0.0

    applicable = {
        name: getattr(signals, name)
        for name in WEIGHTS
        if getattr(signals, name) is not None
    }
    total = sum(WEIGHTS[name] for name in applicable)
    if not total:
        return 0.0
    return sum(WEIGHTS[name] * value for name, value in applicable.items()) / total
