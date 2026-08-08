"""In-memory instrument store with JSON persistence.

Chosen over Postgres `daterange` + GiST deliberately. At the scale this corpus
will reach — a few thousand instruments, since tariff proclamations are counted
in hundreds per year, not millions — an indexed overlap query buys nothing a
linear scan does not already deliver in under a millisecond, and it costs a
migration, a second schema and a live database on every path that wants to read
a rate.

The trade-off that is real: nothing survives a container restart. That is
acceptable because the store is rebuilt from a JSON file at boot, which the
image already does for the BM25 index. If the corpus ever reaches a size where
this is slow, the interface here is narrow enough to swap the backend behind
it without touching callers.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from marsa.ingestion.schemas import Origin
from marsa.logging import get_logger
from marsa.regulatory.schema import Instrument
from marsa.regulatory.supersede import ResolutionReport, resolve

log = get_logger(__name__)

#: An instrument set older than this cannot be trusted for a current answer.
#: Section 232 changed twice in 2026; a fortnight is already generous.
STALE_AFTER = timedelta(days=14)


@dataclass
class InstrumentStore:
    instruments: dict[str, Instrument] = field(default_factory=dict)

    # ── mutation ────────────────────────────────────────────────────────────

    def add(self, instrument: Instrument) -> None:
        """Insert or replace by id.

        Replacement is by design: re-ingesting a source should update an
        instrument in place rather than accumulate duplicates that would then
        read as contradictions of themselves.
        """
        self.instruments[instrument.id] = instrument

    def extend(self, instruments: list[Instrument]) -> None:
        for instrument in instruments:
            self.add(instrument)

    # ── query ───────────────────────────────────────────────────────────────

    def all(self) -> list[Instrument]:
        return list(self.instruments.values())

    def asof(
        self,
        on: date,
        *,
        hts: str | None = None,
        origin: str | None = None,
    ) -> ResolutionReport:
        """The instruments in force on a date, with what could not be resolved.

        This is the only query Phase G exists to answer.
        """
        return resolve(self.all(), on=on, hts=hts, origin=origin)

    # ── provenance ──────────────────────────────────────────────────────────

    @property
    def origins(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for i in self.instruments.values():
            counts[i.origin.value] = counts.get(i.origin.value, 0) + 1
        return counts

    @property
    def any_synthetic(self) -> bool:
        return any(i.origin is Origin.SYNTHETIC for i in self.instruments.values())

    @property
    def retrieved_at(self) -> datetime | None:
        """Oldest retrieval in the set — the set is only as fresh as its stalest."""
        stamps = [i.retrieved_at for i in self.instruments.values()]
        return min(stamps) if stamps else None

    @property
    def age(self) -> timedelta | None:
        oldest = self.retrieved_at
        if oldest is None:
            return None
        if oldest.tzinfo is None:
            oldest = oldest.replace(tzinfo=UTC)
        return datetime.now(UTC) - oldest

    @property
    def is_stale(self) -> bool:
        age = self.age
        return age is None or age > STALE_AFTER

    def staleness(self) -> dict[str, Any]:
        """What every answer has to disclose about how old its rules are."""
        age = self.age
        return {
            "instruments": len(self.instruments),
            "oldestRetrievedAt": self.retrieved_at.isoformat() if self.retrieved_at else None,
            "ageDays": round(age.total_seconds() / 86400, 2) if age else None,
            "staleAfterDays": STALE_AFTER.days,
            "isStale": self.is_stale,
            "origins": self.origins,
            "anySynthetic": self.any_synthetic,
            "note": (
                "SYNTHETIC instruments present — no figure derived from this set "
                "may be published."
                if self.any_synthetic
                else "All instruments are live."
            ),
        }

    # ── persistence ─────────────────────────────────────────────────────────

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "savedAt": datetime.now(UTC).isoformat(),
            "instruments": [i.as_dict() for i in self.instruments.values()],
        }
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        log.info("instruments saved", extra={"path": str(path), "n": len(self.instruments)})

    @classmethod
    def load(cls, path: Path) -> InstrumentStore:
        if not path.exists():
            return cls()
        payload = json.loads(path.read_text(encoding="utf-8"))
        store = cls()
        for raw in payload.get("instruments", []):
            store.add(Instrument.model_validate(raw))
        log.info("instruments loaded", extra={"n": len(store.instruments)})
        return store

    def diff(self, other: InstrumentStore) -> dict[str, Any]:
        """What changed between two ingests.

        The interesting row is `edited`: a source that changed an instrument's
        text without changing its id or dates has silently rewritten history,
        and nothing else in the pipeline would notice.
        """
        mine, theirs = set(self.instruments), set(other.instruments)
        edited = [
            i
            for i in mine & theirs
            if self.instruments[i].text_hash
            and self.instruments[i].text_hash != other.instruments[i].text_hash
        ]
        return {
            "added": sorted(theirs - mine),
            "removed": sorted(mine - theirs),
            "edited": sorted(edited),
        }
