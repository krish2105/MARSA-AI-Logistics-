"""Is this a duty-calculation question?

The deterministic half of the routing decision. Gate G4 says a duty question
reaching the agentic path produces hallucinated arithmetic — the
highest-severity failure available in this system — so the answer must not
depend on a language model being right.

This runs *before* the classifier and short-circuits it. The classifier is
still asked, and its answer recorded, so the routing thesis can be measured
against a ground truth this filter provides. Safety by construction;
measurement alongside.

Deliberately conservative on both sides. A duty question needs a real signal —
a specific HTS code, or explicit duty language plus something to price. "How
much will this cost?" alone is not enough; it is more likely a shipping
question, and stealing it from the other paths costs an answer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: 4, 6, 8 or 10 digits, dotted or not: 7326, 7326.90, 7326.90.86, 7326908600.
HTS_PATTERN = re.compile(r"\b\d{4}(?:\.?\d{2}){0,3}\b")

DUTY_TERMS = (
    "duty", "duties", "tariff", "tariffs", "landed cost", "landed-cost",
    "section 232", "section 301", "ieepa", "customs value", "ad valorem",
    "how much will it cost to import", "import cost",
)

#: Words implying arithmetic over a value rather than a lookup of a rate.
VALUE_TERMS = ("$", "usd", "worth", "value", "shipment of", "consignment", "entry")

#: Phrasing that makes a question about *which things* rather than *how much*.
#:
#: "Which of our shipments are exposed if the tariff on HS 8541 takes effect?"
#: contains duty vocabulary and an HTS-shaped number, and is not a duty
#: calculation — it is an exposure question over a portfolio, which the agentic
#: and graph paths answer. Capturing it costs a real answer and returns a
#: calculator's shrug instead.
#:
#: The discriminator is grammatical rather than topical: a duty question asks
#: *how much* for one entry; these ask *which* across many.
EXPOSURE_TERMS = (
    "which of our", "our shipments", "our suppliers", "are exposed", "exposure",
    "affected by", "which suppliers", "which shipments", "how many of",
    "at risk", "takes effect",
)


@dataclass
class Detection:
    is_duty_question: bool
    reasons: list[str]
    hts: str | None = None

    @property
    def confidence(self) -> float:
        """Deterministic, so this is evidence count rather than a probability.

        Reported as a number only because the Route Badge shows one; it is not
        comparable to the classifier's confidence and is labelled as such.
        """
        return min(1.0, 0.5 + 0.25 * len(self.reasons))


def detect(query: str) -> Detection:
    text = query.lower()
    reasons: list[str] = []

    hts_match = HTS_PATTERN.search(query)
    # A bare year reads as an HTS code. Excluding plausible years costs a few
    # genuine codes in that range and avoids routing "trade in 2026" to a
    # calculator.
    hts = None
    if hts_match:
        candidate = hts_match.group(0)
        digits = candidate.replace(".", "")
        if not (len(digits) == 4 and 1900 <= int(digits) <= 2100):
            hts = candidate
            reasons.append(f"HTS-shaped code {candidate!r}")

    duty_hits = [term for term in DUTY_TERMS if term in text]
    if duty_hits:
        reasons.append(f"duty language: {', '.join(duty_hits[:3])}")

    value_hits = [term for term in VALUE_TERMS if term in text]
    if value_hits:
        reasons.append("a value to price against")

    # Either a code plus duty language, or duty language plus something to
    # price. A code alone is a classification question, not a duty one.
    is_duty = bool(duty_hits) and (hts is not None or bool(value_hits))

    # An exposure question vetoes, however much duty vocabulary it carries.
    # Deliberately a veto rather than a weight: the cost of stealing one of
    # these is a wrong path with no way back, and the cost of missing a duty
    # question is that it goes to the fast path, which cites rulings rather
    # than inventing sums.
    exposure_hits = [term for term in EXPOSURE_TERMS if term in text]
    if exposure_hits and is_duty:
        reasons.append(
            f"vetoed — asks which things, not how much: {', '.join(exposure_hits[:2])}"
        )
        is_duty = False

    return Detection(is_duty_question=is_duty, reasons=reasons, hts=hts)
