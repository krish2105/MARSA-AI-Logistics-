"""Per-path cost and latency — the three-way comparison table.

The spec calls this the deliverable that proves the router earns its complexity
rather than merely adding it, and asks for it to be published exactly as
measured *including anywhere the simple path outperforms the smarter ones*.

Two measurement decisions that matter:

**Every query runs down every path.** Routing each query only to its assigned
path would measure "how fast is the path we happened to pick", which is not a
comparison. Forcing all three gives the counterfactual: what would this query
have cost on the paths we did *not* choose?

**Cold and warm runs are separated.** The first call to any path pays to load
an index, a graph, or a corpus — hundreds of milliseconds that have nothing to
do with retrieval. Folding that into the mean makes whichever path ran first
look slowest, which is a measurement artefact rather than a property of the
system.
"""

from __future__ import annotations

import statistics
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from marsa.eval.dataset import LABELLED_QUERIES, LabelledQuery
from marsa.logging import get_logger
from marsa.router.paths import run_agentic_path, run_fast_path, run_graph_path

log = get_logger(__name__)

PATH_RUNNERS: dict[str, Callable[[str], dict[str, Any]]] = {
    "fast": run_fast_path,
    "agentic": run_agentic_path,
    "graph": run_graph_path,
}


@dataclass
class PathRun:
    path: str
    query: str
    latency_ms: float
    sources: int
    steps: int
    answer_chars: int
    cold: bool = False
    warnings: list[str] = field(default_factory=list)


@dataclass
class PathStats:
    path: str
    runs: list[PathRun] = field(default_factory=list)
    cost_per_query_usd: float = 0.0

    @property
    def warm(self) -> list[PathRun]:
        return [r for r in self.runs if not r.cold]

    def _latencies(self) -> list[float]:
        return sorted(r.latency_ms for r in self.warm)

    def as_dict(self) -> dict[str, Any]:
        lat = self._latencies()
        if not lat:
            return {"path": self.path, "n": 0}

        cold = [r.latency_ms for r in self.runs if r.cold]
        return {
            "path": self.path,
            "n": len(lat),
            "medianLatencyMs": round(statistics.median(lat), 2),
            "meanLatencyMs": round(statistics.fmean(lat), 2),
            "p95LatencyMs": round(lat[min(len(lat) - 1, int(len(lat) * 0.95))], 2),
            "minLatencyMs": round(lat[0], 2),
            "maxLatencyMs": round(lat[-1], 2),
            "coldStartMs": round(cold[0], 2) if cold else None,
            "costPerQueryUsd": round(self.cost_per_query_usd, 8),
            "meanSources": round(statistics.fmean([r.sources for r in self.warm]), 2),
            "meanSteps": round(statistics.fmean([r.steps for r in self.warm]), 2),
            "meanAnswerChars": round(statistics.fmean([r.answer_chars for r in self.warm]), 1),
            "warningRate": round(
                sum(1 for r in self.warm if r.warnings) / len(self.warm), 4
            ),
        }


@dataclass
class BenchmarkReport:
    stats: dict[str, PathStats] = field(default_factory=dict)
    queries_run: int = 0
    repeats: int = 1

    def comparison(self) -> list[dict[str, Any]]:
        rows = [s.as_dict() for s in self.stats.values() if s.runs]
        rows.sort(key=lambda r: r.get("medianLatencyMs", 0))
        return rows

    def findings(self) -> list[str]:
        """Observations the numbers actually support.

        Written as statements a reader can check against the table, not as
        conclusions about the thesis — which this run cannot support.
        """
        rows = self.comparison()
        if len(rows) < 2:
            return []

        out: list[str] = []
        fastest, slowest = rows[0], rows[-1]
        ratio = (
            slowest["medianLatencyMs"] / fastest["medianLatencyMs"]
            if fastest["medianLatencyMs"]
            else 0
        )
        out.append(
            f"`{slowest['path']}` is {ratio:.0f}× slower than `{fastest['path']}` at the "
            f"median ({slowest['medianLatencyMs']:.1f}ms vs "
            f"{fastest['medianLatencyMs']:.1f}ms)."
        )

        costs = {r["path"]: r["costPerQueryUsd"] for r in rows}
        no_llm = all(c == 0 for c in costs.values())

        # The spec asks for it to be reported when the ordering defies the
        # naming. Stating *that* is a measurement; explaining *why* is not, so
        # the explanation is confined to what this run can actually support.
        if rows[0]["path"] != "fast":
            note = (
                f"The path named `fast` is NOT the fastest here — `{rows[0]['path']}` is."
            )
            if no_llm and rows[0]["path"] == "agentic":
                note += (
                    " That ordering is an artefact, not a finding: with no LLM "
                    "configured the agentic path never makes a model call, so it "
                    "degrades to in-memory list filtering and does far less work "
                    "than its design intends. `fast` meanwhile pays a real pgvector "
                    "round trip. Configure a provider and this ordering should invert."
                )
            else:
                note += " The cause is not established by this run."
            out.append(note)

        if no_llm:
            out.append(
                "Cost is $0.00000 on every path because no LLM provider is configured "
                "— the classifier ran as a heuristic and no path invoked a model. **The "
                "cost comparison this project exists to make is not measured by this "
                "run.**"
            )

        thin = [r["path"] for r in rows if r["meanAnswerChars"] < 120]
        if thin:
            out.append(
                f"Answers from `{'`, `'.join(thin)}` average under 120 characters. That "
                "usually means a missing resource rather than a concise path — check "
                "the warning rate before reading it as brevity."
            )

        sparse = [r["path"] for r in rows if r["meanSources"] < 1.0]
        if sparse:
            out.append(
                f"`{'`, `'.join(sparse)}` returns under one source per query on average, "
                "so its citation coverage is materially weaker than the other paths' "
                "regardless of answer length."
            )

        return out

    def as_dict(self) -> dict[str, Any]:
        return {
            "queriesRun": self.queries_run,
            "repeats": self.repeats,
            "byPath": self.comparison(),
            "findings": self.findings(),
        }


def run_benchmark(
    queries: list[LabelledQuery] | None = None,
    *,
    repeats: int = 1,
    limit: int | None = None,
) -> BenchmarkReport:
    """Run every query down every path and time it."""
    items = list(queries if queries is not None else LABELLED_QUERIES)
    if limit:
        items = items[:limit]

    report = BenchmarkReport(queries_run=len(items), repeats=repeats)

    for path, runner in PATH_RUNNERS.items():
        stats = PathStats(path=path)
        first = True

        for _ in range(repeats):
            for item in items:
                started = time.perf_counter()
                try:
                    result = runner(item.query)
                except Exception as exc:  # noqa: BLE001 — a failing path is a datum
                    log.warning(
                        "path failed during benchmark",
                        extra={"path": path, "error": str(exc)[:200]},
                    )
                    continue
                elapsed = (time.perf_counter() - started) * 1000

                stats.runs.append(
                    PathRun(
                        path=path,
                        query=item.query,
                        latency_ms=elapsed,
                        sources=len(result.get("sources", [])),
                        steps=len(result.get("steps", [])),
                        answer_chars=len(result.get("answer", "")),
                        cold=first,
                        warnings=list(result.get("warnings", [])),
                    )
                )
                first = False

        report.stats[path] = stats
        summary = stats.as_dict()
        log.info(
            "path benchmarked",
            extra={
                "path": path,
                "median_ms": summary.get("medianLatencyMs"),
                "n": summary.get("n"),
            },
        )

    return report
