"""Citations, and why a suggestion cannot exist without one.

The roadmap picks one axis to compete on against commoditised classification
tooling: every suggestion cites the binding ruling that supports it. A rule
enforced by a check somewhere downstream is a rule some future caller routes
around. So the constraint lives in the type — `Suggestion` requires a
`Citation`, and there is no constructor that produces one without.

The citation records the code **CBP** assigned to the cited ruling, not the code
this system inferred. Those differ exactly when the system is wrong, which is
the case the citation exists to make visible.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

CROSS_URL = "https://rulings.cbp.gov/ruling/{number}"


@dataclass(frozen=True)
class Citation:
    """A ruling that supports a suggestion, and what CBP classified it under."""

    ruling_number: str
    #: Codes CBP assigned to this ruling, carried through ingestion unmodified.
    assigned_codes: tuple[str, ...]
    #: The passage the suggestion rests on, so a reader can check it directly.
    quote: str = ""

    def __post_init__(self) -> None:
        if not self.ruling_number.strip():
            raise ValueError("a citation must name a ruling")

    @property
    def url(self) -> str:
        return CROSS_URL.format(number=self.ruling_number)

    def as_dict(self) -> dict[str, Any]:
        return {
            "rulingNumber": self.ruling_number,
            "assignedCodes": list(self.assigned_codes),
            "quote": self.quote,
            "url": self.url,
        }


@dataclass(frozen=True)
class Suggestion:
    """A classification the system is prepared to stand behind.

    Constructing one without a citation raises. That is the point: an uncited
    suggestion is not rejected later, it is unrepresentable.
    """

    subheading: str
    confidence: float
    citations: tuple[Citation, ...]
    rationale: str = ""

    def __post_init__(self) -> None:
        if not self.citations:
            raise ValueError(
                "a suggestion must cite at least one ruling — an uncited "
                "classification is the failure this phase exists to prevent"
            )
        if not self.subheading.strip():
            raise ValueError("a suggestion must name a subheading")

    def as_dict(self) -> dict[str, Any]:
        return {
            "outcome": "classified",
            "subheading": self.subheading,
            "confidence": round(self.confidence, 4),
            "rationale": self.rationale,
            "citations": [c.as_dict() for c in self.citations],
        }


@dataclass(frozen=True)
class InsufficientEvidence:
    """The fourth outcome: the rulings on hand do not support a classification.

    Carries the closest rulings anyway. A refusal that returns nothing is worse
    than one that returns the near misses and lets the reader judge — and on a
    reasonable-care standard, a documented near miss is worth more than a
    confident guess.
    """

    message: str
    confidence: float
    #: The rulings that came closest, uncited-as-support but shown.
    nearest: tuple[Citation, ...] = ()
    reasons: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "outcome": "insufficient_evidence",
            "message": self.message,
            "confidence": round(self.confidence, 4),
            "nearest": [c.as_dict() for c in self.nearest],
            "reasons": list(self.reasons),
        }


Outcome = Suggestion | InsufficientEvidence
