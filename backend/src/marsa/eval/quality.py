"""Answer-quality metrics, split by what can be measured without a judge.

RAGAS defines four metrics for a RAG system. Two of them need an LLM judge and
two do not, and conflating that distinction is how evaluation harnesses end up
reporting numbers nobody can reproduce:

| Metric | Needs a judge? | Why |
|---|---|---|
| context precision | no | retrieved vs. relevant, both known |
| context recall | no | same |
| faithfulness | **yes** | needs an answer split into claims, each checked |
| answer relevance | **yes** | needs a judgement of whether the answer replies |

So the retrieval metrics are computed always, from ground-truth relevance
labels attached to the test set. The generation metrics are computed **only**
when an LLM judge is configured, and are otherwise reported as
`not_measured` — never as zero, and never silently omitted. A missing metric
that reads as 0.0 is worse than no metric at all.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from marsa.logging import get_logger
from marsa.router.llm import LLMClient

log = get_logger(__name__)

NOT_MEASURED = "not_measured"


@dataclass
class RetrievalScores:
    """Deterministic retrieval quality — no judge required."""

    context_precision: float = 0.0
    context_recall: float = 0.0
    retrieved: int = 0
    relevant: int = 0
    hits: int = 0

    @property
    def f1(self) -> float:
        p, r = self.context_precision, self.context_recall
        return 2 * p * r / (p + r) if (p + r) else 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "contextPrecision": round(self.context_precision, 4),
            "contextRecall": round(self.context_recall, 4),
            "contextF1": round(self.f1, 4),
            "retrieved": self.retrieved,
            "relevant": self.relevant,
            "hits": self.hits,
        }


def score_retrieval(retrieved: list[str], relevant: set[str]) -> RetrievalScores:
    """Precision and recall of retrieved sources against a relevance set.

    Comparison is case- and whitespace-insensitive because source refs arrive
    from three different paths with three different formatting conventions.
    """
    norm = lambda s: s.strip().upper().replace(" ", "")  # noqa: E731
    got = {norm(r) for r in retrieved}
    want = {norm(r) for r in relevant}
    hits = got & want

    return RetrievalScores(
        context_precision=len(hits) / len(got) if got else 0.0,
        context_recall=len(hits) / len(want) if want else 0.0,
        retrieved=len(got),
        relevant=len(want),
        hits=len(hits),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Judge-dependent metrics
# ─────────────────────────────────────────────────────────────────────────────

FAITHFULNESS_PROMPT = """You are evaluating whether an answer is supported by its context.

Break the ANSWER into individual factual claims. For each, decide whether the \
CONTEXT supports it. Reply with JSON only:
{"claims": <int>, "supported": <int>}

CONTEXT:
{context}

ANSWER:
{answer}"""

RELEVANCE_PROMPT = """You are evaluating whether an answer addresses a question.

Score from 0.0 (does not address it at all) to 1.0 (fully addresses it). \
Ignore whether the answer is correct — judge only relevance. Reply with JSON only:
{"relevance": <float>}

QUESTION:
{question}

ANSWER:
{answer}"""


@dataclass
class JudgeScores:
    """Generation quality. `None` means not measured, which is not zero."""

    faithfulness: float | None = None
    answer_relevance: float | None = None
    judged: bool = False
    reason_unavailable: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "faithfulness": self.faithfulness if self.judged else NOT_MEASURED,
            "answerRelevance": self.answer_relevance if self.judged else NOT_MEASURED,
            "judged": self.judged,
            "reasonUnavailable": self.reason_unavailable,
        }


class RagasJudge:
    """LLM-as-judge for faithfulness and answer relevance.

    Deliberately a separate client from the router's: judging with the same
    model that generated the answer is a known failure mode — the model rates
    its own output generously. When more than one provider is configured, the
    judge takes the *second* one so generator and judge differ.
    """

    def __init__(self, client: LLMClient | None = None) -> None:
        if client is not None:
            self.client = client
        else:
            available = LLMClient().providers
            # Prefer a different provider from the generator's first choice.
            self.client = LLMClient(providers=available[1:] or available)

    @property
    def is_available(self) -> bool:
        return self.client.is_available

    def score(self, question: str, answer: str, context: str) -> JudgeScores:
        if not self.is_available:
            return JudgeScores(
                judged=False,
                reason_unavailable=(
                    "No LLM provider configured. Faithfulness and answer relevance "
                    "require a judge and are therefore not measured — not zero."
                ),
            )
        if not answer.strip():
            return JudgeScores(judged=False, reason_unavailable="Empty answer.")

        import json
        import re

        def ask(prompt: str) -> dict[str, Any] | None:
            try:
                response = self.client.complete(prompt, max_tokens=200, temperature=0.0)
            except Exception as exc:  # noqa: BLE001
                log.warning("judge call failed", extra={"error": str(exc)[:200]})
                return None
            match = re.search(r"\{.*?\}", response.text, re.DOTALL)
            if not match:
                return None
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                return None

        faith_payload = ask(
            FAITHFULNESS_PROMPT.replace("{context}", context[:6000]).replace(
                "{answer}", answer[:3000]
            )
        )
        rel_payload = ask(
            RELEVANCE_PROMPT.replace("{question}", question).replace("{answer}", answer[:3000])
        )

        if faith_payload is None and rel_payload is None:
            return JudgeScores(
                judged=False, reason_unavailable="Judge returned no parseable score."
            )

        faithfulness = None
        if faith_payload:
            claims = float(faith_payload.get("claims", 0) or 0)
            supported = float(faith_payload.get("supported", 0) or 0)
            faithfulness = (supported / claims) if claims else None

        relevance = None
        if rel_payload:
            try:
                relevance = max(0.0, min(1.0, float(rel_payload.get("relevance", 0))))
            except (TypeError, ValueError):
                relevance = None

        return JudgeScores(
            faithfulness=round(faithfulness, 4) if faithfulness is not None else None,
            answer_relevance=round(relevance, 4) if relevance is not None else None,
            judged=faithfulness is not None or relevance is not None,
        )


@dataclass
class QualityReport:
    per_path_retrieval: dict[str, list[RetrievalScores]] = field(default_factory=dict)
    per_path_judge: dict[str, list[JudgeScores]] = field(default_factory=dict)
    judge_available: bool = False
    judge_note: str = ""

    def summary(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for path, scores in self.per_path_retrieval.items():
            if not scores:
                continue
            mean = lambda xs: sum(xs) / len(xs)  # noqa: E731
            entry: dict[str, Any] = {
                "n": len(scores),
                "contextPrecision": round(mean([s.context_precision for s in scores]), 4),
                "contextRecall": round(mean([s.context_recall for s in scores]), 4),
            }

            judged = [
                j for j in self.per_path_judge.get(path, []) if j.judged
            ]
            if judged:
                faith = [j.faithfulness for j in judged if j.faithfulness is not None]
                rel = [j.answer_relevance for j in judged if j.answer_relevance is not None]
                entry["faithfulness"] = round(mean(faith), 4) if faith else NOT_MEASURED
                entry["answerRelevance"] = round(mean(rel), 4) if rel else NOT_MEASURED
            else:
                entry["faithfulness"] = NOT_MEASURED
                entry["answerRelevance"] = NOT_MEASURED

            out[path] = entry
        return out

    def as_dict(self) -> dict[str, Any]:
        return {
            "byPath": self.summary(),
            "judgeAvailable": self.judge_available,
            "judgeNote": self.judge_note,
        }
