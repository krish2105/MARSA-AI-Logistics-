"""The abstention head: turn measured confidence into a product behaviour.

Phase F measured +0.274 separation between the confidence attached to correct
routes and to incorrect ones. Until now that was a fact in a report. Here it
becomes the thing that decides whether the system answers.

The threshold is a shipped default, not a tuned optimum. It is set at the point
where the curve in `curve.py` stops buying accuracy cheaply, and it is exposed
as a parameter so a caller with a different tolerance for wrong answers can
move it — a customs broker and a browsing user do not want the same trade.
"""

from __future__ import annotations

from marsa.classify.citations import Citation, InsufficientEvidence, Outcome, Suggestion
from marsa.classify.evidence import EvidenceSignals, assess, confidence
from marsa.indexing.hybrid import RetrievedRuling

#: Shipped default. Chosen from the measured curve, and stated here rather than
#: buried in a call site so that changing it is a visible decision.
DEFAULT_THRESHOLD = 0.45

REFUSAL = (
    "I cannot support a classification from the rulings I have. The closest "
    "rulings are shown below so you can judge them yourself."
)

#: How many near misses to hand back with a refusal.
NEAREST_SHOWN = 3


def _cite(ruling: RetrievedRuling) -> Citation:
    quote = ruling.chunks[0].chunk.text.strip() if ruling.chunks else ""
    return Citation(
        ruling_number=ruling.ruling_number,
        assigned_codes=tuple(ruling.hts_codes),
        quote=quote[:400],
    )


def _supporting(rulings: list[RetrievedRuling], subheading: str) -> list[RetrievedRuling]:
    """Rulings CBP actually classified under the suggested subheading.

    Only these may be cited in support. A ruling that merely ranked well is not
    evidence for the answer it failed to be about.
    """
    return [
        ruling
        for ruling in rulings
        if any(code.startswith(subheading) for code in ruling.hts_codes)
    ]


def decide(
    query: str,
    rulings: list[RetrievedRuling],
    *,
    threshold: float = DEFAULT_THRESHOLD,
    signals: EvidenceSignals | None = None,
) -> Outcome:
    """Classify, or decline and say why.

    Two independent ways to end up declining, and they are different failures:
    the evidence is too thin (confidence below threshold), or the evidence is
    strong on average but nothing actually supports the specific subheading the
    set points at. The second is rarer and more embarrassing, which is why it
    is checked separately rather than folded into the score.
    """
    signals = signals if signals is not None else assess(query, rulings)
    score = confidence(signals)
    nearest = tuple(_cite(r) for r in rulings[:NEAREST_SHOWN])

    if score < threshold or not signals.modal_subheading:
        reasons = tuple(signals.notes) or (
            f"Confidence {score:.2f} is below the {threshold:.2f} threshold.",
        )
        return InsufficientEvidence(
            message=REFUSAL, confidence=score, nearest=nearest, reasons=reasons
        )

    supporting = _supporting(rulings, signals.modal_subheading)
    if not supporting:
        return InsufficientEvidence(
            message=REFUSAL,
            confidence=score,
            nearest=nearest,
            reasons=(
                f"The retrieved set points at {signals.modal_subheading}, but no "
                "retrieved ruling is classified under it, so nothing can be cited "
                "in support.",
            ),
        )

    return Suggestion(
        subheading=signals.modal_subheading,
        confidence=score,
        citations=tuple(_cite(r) for r in supporting[:NEAREST_SHOWN]),
        rationale=(
            f"{len(supporting)} of {len(rulings)} retrieved rulings are classified "
            f"by CBP under {signals.modal_subheading}."
        ),
    )
