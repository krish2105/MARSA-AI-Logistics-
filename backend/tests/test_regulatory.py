"""Phase G — decision gate G1.

The gate's only job is to answer a question honestly, so the tests are mostly
about what it must refuse to say.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from marsa.regulatory.probe import (
    REQUIRED,
    GateResult,
    SourceProbe,
    probe_federal_register,
    run_gate,
)

FR_URL = "https://www.federalregister.gov/api/v1/documents.json"


def _probe(name: str, **fields: str) -> SourceProbe:
    return SourceProbe(
        name=name, url="https://example.test", why="test",
        reachable=bool(fields), fields=dict(fields),
    )


def _complete(name: str) -> SourceProbe:
    return _probe(name, **{k: k for k in REQUIRED})


class TestVerdict:
    def test_unreachable_source_scores_zero_not_none(self):
        p = _probe("down")
        assert p.verdict == "UNREACHABLE"
        assert p.coverage == 0.0

    def test_all_fields_present_is_complete(self):
        assert _complete("good").verdict == "COMPLETE"

    def test_half_the_fields_is_partial(self):
        p = _probe("half", identity="a", effective="b")
        assert p.verdict == "PARTIAL"

    def test_missing_effective_date_is_the_fatal_one(self):
        """Everything else can be worked around; a point-in-time query cannot
        be built over instruments with no date."""
        p = _probe("undated", identity="a", scope="b", source_url="c")
        assert "effective" not in p.satisfied


class TestGate:
    def test_no_reachable_source_is_not_evaluated_never_a_pass(self):
        """The whole point of the gate. Silence must not read as success."""
        gate = GateResult(probes=[_probe("a"), _probe("b")])
        assert gate.verdict == "NOT_EVALUATED"
        assert gate.verdict != "PASS"
        assert "NOT a pass" in gate.action

    def test_unreachable_sources_drag_the_average_down(self):
        """Averaging only reachable sources would let 1-of-4 report full marks —
        exactly the self-flattery this gate exists to prevent."""
        gate = GateResult(probes=[_complete("up"), _probe("down"),
                                  _probe("down2"), _probe("down3")])
        assert gate.coverage == pytest.approx(0.25)
        assert gate.verdict == "STOP"

    def test_all_sources_complete_passes(self):
        gate = GateResult(probes=[_complete(n) for n in "abcd"])
        assert gate.coverage == 1.0
        assert gate.verdict == "PASS"

    def test_marginal_sits_between_the_thresholds(self):
        # Two complete + two half = 0.75, which is inside [0.60, 0.80).
        gate = GateResult(probes=[
            _complete("a"), _complete("b"),
            _probe("c", identity="x", effective="y"),
            _probe("d", identity="x", effective="y"),
        ])
        assert gate.coverage == pytest.approx(0.75)
        assert gate.stop_below <= gate.coverage < gate.pass_at
        assert gate.verdict == "MARGINAL"
        assert "narrowed" in gate.action

    def test_thresholds_match_the_roadmap(self):
        """Pinned so the gate cannot quietly be made easier to pass."""
        gate = GateResult()
        assert (gate.pass_at, gate.stop_below) == (0.80, 0.60)


class TestFederalRegisterProbe:
    @respx.mock
    def test_reads_the_fields_the_instrument_model_needs(self):
        respx.get(url__startswith=FR_URL).mock(
            return_value=httpx.Response(200, json={"results": [{
                "document_number": "2026-11542",
                "effective_on": "2026-06-08",
                "publication_date": "2026-06-02",
                "html_url": "https://www.federalregister.gov/d/2026-11542",
                "full_text_xml_url": "https://.../full_text.xml",
                "correction_of": None,
            }]})
        )
        with _fetcher() as f:
            p = probe_federal_register(f)
        assert p.reachable
        assert p.fields["identity"] == "document_number"
        assert p.fields["effective"] == "effective_on"
        assert p.verdict == "COMPLETE"

    @respx.mock
    def test_falls_back_to_publication_date_and_says_so(self):
        """publication_date is not the effective date. Using it silently would
        put a wrong window on an instrument."""
        respx.get(url__startswith=FR_URL).mock(
            return_value=httpx.Response(200, json={"results": [{
                "document_number": "2026-1", "publication_date": "2026-06-02",
                "html_url": "https://x", "full_text_xml_url": "https://y",
            }]})
        )
        with _fetcher() as f:
            p = probe_federal_register(f)
        assert p.fields["effective"] == "publication_date"
        assert any("0/1 carry an explicit effective_on" in n for n in p.notes)
        assert any("inferred" in n for n in p.notes)

    @respx.mock
    def test_an_unreachable_source_is_recorded_not_raised(self):
        respx.get(url__startswith=FR_URL).mock(
            return_value=httpx.Response(403, text="blocked")
        )
        with _fetcher() as f:
            p = probe_federal_register(f)
        assert not p.reachable
        assert p.error
        assert p.verdict == "UNREACHABLE"

    @respx.mock
    def test_run_gate_survives_every_source_failing(self):
        """What actually happens in this build sandbox."""
        respx.route(host__in=[
            "www.federalregister.gov", "hts.usitc.gov",
            "www.dhs.gov", "taxation-customs.ec.europa.eu",
        ]).mock(return_value=httpx.Response(403, text="blocked"))
        gate = run_gate(use_cache=False)
        assert gate.verdict == "NOT_EVALUATED"
        assert len(gate.reachable) == 0
        assert len(gate.probes) == 4


def _fetcher():
    import tempfile
    from pathlib import Path

    from marsa.ingestion.fetcher import Fetcher

    return Fetcher(
        requests_per_second=100.0,
        cache_dir=Path(tempfile.mkdtemp()),
        use_cache=False,
        max_retries=1,
    )
