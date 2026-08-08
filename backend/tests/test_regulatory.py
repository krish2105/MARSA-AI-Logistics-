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


# ─── Phase G core ────────────────────────────────────────────────────────────

from datetime import UTC, date, datetime  # noqa: E402

from marsa.ingestion.schemas import Origin  # noqa: E402
from marsa.regulatory import fixtures as fx  # noqa: E402
from marsa.regulatory.schema import (  # noqa: E402
    DateBasis,
    Effect,
    EffectKind,
    Instrument,
    InstrumentKind,
    Issuer,
    Programme,
    Scope,
)
from marsa.regulatory.store import InstrumentStore  # noqa: E402
from marsa.regulatory.supersede import find_contradictions, resolve  # noqa: E402

JUNE = date(2026, 6, 8)
MARCH = date(2026, 3, 1)
SEPT = date(2026, 9, 1)


def _tariff(id, rate, programme, *, frm, to=None, supersedes=None, hts=("72", "73"),
            origins=(), basis=DateBasis.EXPLICIT):
    return Instrument(
        id=id, issuer=Issuer.USTR, kind=InstrumentKind.TARIFF, programme=programme,
        scope=Scope(hts_prefixes=list(hts), origin_countries=list(origins)),
        effect=Effect(kind=EffectKind.AD_VALOREM, rate_percent=rate, additive=True),
        effective_from=frm, effective_to=to, date_basis=basis,
        supersedes=list(supersedes or []),
        retrieved_at=datetime.now(UTC), origin=Origin.SYNTHETIC,
    )


class TestScope:
    def test_hts_dots_are_presentational(self):
        assert Scope(hts_prefixes=["7326.90"]).matches(hts="73269086")
        assert Scope(hts_prefixes=["732690"]).matches(hts="7326.90.86")

    def test_empty_axis_means_unrestricted_not_matches_nothing(self):
        """Getting this backwards silently narrows every instrument to nothing."""
        assert Scope(hts_prefixes=["72"]).matches(hts="7208", origin="CN")
        assert Scope().matches(hts="anything", origin="XX")

    def test_a_restricted_axis_needs_a_value_to_match(self):
        assert not Scope(hts_prefixes=["72"]).matches(hts=None)
        assert not Scope(origin_countries=["CN"]).matches(origin=None)


class TestPointInTime:
    def test_instrument_is_not_in_force_before_it_commences(self):
        i = _tariff("a", 50.0, Programme.SECTION_232, frm=JUNE)
        assert not i.in_force_on(MARCH)
        assert i.in_force_on(SEPT)

    def test_open_ended_window_stays_in_force(self):
        assert _tariff("a", 25.0, Programme.SECTION_301, frm=MARCH).in_force_on(
            date(2030, 1, 1)
        )

    def test_window_must_be_ordered(self):
        with pytest.raises(ValueError, match="precedes"):
            _tariff("a", 25.0, Programme.MFN, frm=JUNE, to=MARCH)


class TestSupersession:
    def test_superseded_instrument_drops_out_even_though_its_window_is_open(self):
        """The February 232's own window never closes. It stops applying only
        because June replaces it — a resolver checking dates alone double-counts."""
        old = _tariff("old", 25.0, Programme.SECTION_232, frm=date(2026, 2, 1))
        new = _tariff("new", 50.0, Programme.SECTION_232, frm=JUNE, supersedes=["old"])
        report = resolve([old, new], on=SEPT, hts="7326")
        ids = {i.id for i in report.effective}
        assert ids == {"new"}
        assert "old" in report.superseded
        assert old.in_force_on(SEPT), "the old window is still open; that is the point"

    def test_a_future_instrument_does_not_void_the_past(self):
        """Answering for March must not change because a June rule was ingested."""
        old = _tariff("old", 25.0, Programme.SECTION_232, frm=date(2026, 2, 1))
        new = _tariff("new", 50.0, Programme.SECTION_232, frm=JUNE, supersedes=["old"])
        report = resolve([old, new], on=MARCH, hts="7326")
        assert {i.id for i in report.effective} == {"old"}


class TestContradiction:
    def test_different_programmes_stack_rather_than_conflict(self):
        """232 and 301 are both TARIFF and are designed to stack. Reporting that
        as a conflict trains a reader to ignore the warning that matters."""
        s232 = _tariff("232", 50.0, Programme.SECTION_232, frm=JUNE)
        s301 = _tariff("301", 25.0, Programme.SECTION_301, frm=JUNE)
        assert find_contradictions([s232, s301]) == []

    def test_same_programme_different_rate_is_a_contradiction(self):
        a = _tariff("a", 50.0, Programme.SECTION_232, frm=JUNE)
        b = _tariff("b", 35.0, Programme.SECTION_232, frm=JUNE)
        found = find_contradictions([a, b])
        assert len(found) == 1
        assert {found[0].left_rate, found[0].right_rate} == {50.0, 35.0}

    def test_an_explicit_supersession_resolves_rather_than_conflicts(self):
        a = _tariff("a", 25.0, Programme.SECTION_232, frm=date(2026, 2, 1))
        b = _tariff("b", 50.0, Programme.SECTION_232, frm=JUNE, supersedes=["a"])
        assert find_contradictions([a, b]) == []

    def test_unknown_programme_never_asserts_a_conflict(self):
        """Federal Register metadata does not carry the programme, so an
        unlabelled pair must not be claimed to conflict."""
        a = _tariff("a", 50.0, Programme.UNKNOWN, frm=JUNE)
        b = _tariff("b", 35.0, Programme.UNKNOWN, frm=JUNE)
        assert find_contradictions([a, b]) == []

    def test_non_overlapping_scopes_do_not_conflict(self):
        a = _tariff("a", 50.0, Programme.SECTION_232, frm=JUNE, hts=("72",))
        b = _tariff("b", 35.0, Programme.SECTION_232, frm=JUNE, hts=("85",))
        assert find_contradictions([a, b]) == []


class TestTrustworthiness:
    def test_an_inferred_date_blocks_publication(self):
        i = _tariff("a", 10.0, Programme.IEEPA, frm=JUNE,
                    basis=DateBasis.INFERRED_FROM_PUBLICATION)
        report = resolve([i], on=SEPT, hts="7326")
        assert report.inferred_dates == ["a"]
        assert not report.is_trustworthy

    def test_a_contradiction_blocks_publication(self):
        a = _tariff("a", 50.0, Programme.SECTION_232, frm=JUNE)
        b = _tariff("b", 35.0, Programme.SECTION_232, frm=JUNE)
        assert not resolve([a, b], on=SEPT, hts="7326").is_trustworthy

    def test_a_clean_resolution_is_publishable(self):
        assert resolve(
            [_tariff("a", 50.0, Programme.SECTION_232, frm=JUNE)], on=SEPT, hts="7326"
        ).is_trustworthy


class TestStore:
    def test_add_replaces_rather_than_duplicating(self):
        """A re-ingest must update in place; duplicates would read as a
        contradiction of themselves."""
        store = InstrumentStore()
        store.add(_tariff("a", 25.0, Programme.SECTION_232, frm=JUNE))
        store.add(_tariff("a", 50.0, Programme.SECTION_232, frm=JUNE))
        assert len(store.instruments) == 1
        assert store.instruments["a"].effect.rate_percent == 50.0

    def test_round_trips_through_json(self, tmp_path):
        store = InstrumentStore()
        store.extend(fx.generate_instruments())
        path = tmp_path / "instruments.json"
        store.save(path)
        assert InstrumentStore.load(path).all() and len(
            InstrumentStore.load(path).instruments
        ) == len(store.instruments)

    def test_missing_file_loads_empty_rather_than_raising(self, tmp_path):
        assert InstrumentStore.load(tmp_path / "nope.json").instruments == {}

    def test_diff_detects_a_silent_upstream_edit(self):
        """A source that rewrites text without changing id or dates has
        rewritten history, and nothing else would notice."""
        before, after = InstrumentStore(), InstrumentStore()
        a = _tariff("a", 25.0, Programme.SECTION_232, frm=JUNE)
        a.text_hash = "aaa"
        b = a.model_copy(update={"text_hash": "bbb"})
        before.add(a)
        after.add(b)
        assert before.diff(after)["edited"] == ["a"]

    def test_staleness_reports_the_oldest_not_the_newest(self):
        store = InstrumentStore()
        old = _tariff("old", 25.0, Programme.MFN, frm=JUNE)
        old.retrieved_at = datetime(2020, 1, 1, tzinfo=UTC)
        store.add(old)
        store.add(_tariff("new", 25.0, Programme.MFN, frm=JUNE))
        assert store.is_stale
        assert store.staleness()["ageDays"] > 1000


class TestFixtures:
    def test_every_fixture_is_marked_synthetic(self):
        """The Phase A rule, enforced rather than documented."""
        assert all(i.origin is Origin.SYNTHETIC for i in fx.generate_instruments())

    def test_fixtures_reproduce_supersession(self):
        report = resolve(fx.generate_instruments(), on=SEPT, hts="7326.90.86")
        assert "FR-2026-02-232-STEEL" in report.superseded

    def test_fixtures_reproduce_stacking_without_false_conflict(self):
        report = resolve(fx.generate_instruments(), on=SEPT, hts="7326.90.86", origin="CN")
        programmes = {i.programme for i in report.effective}
        assert Programme.SECTION_232 in programmes
        assert Programme.SECTION_301 in programmes
        assert report.contradictions == []

    def test_injected_contradiction_is_detected(self):
        report = resolve(
            fx.generate_instruments(with_contradiction=True), on=SEPT, hts="7326.90.86"
        )
        assert len(report.contradictions) == 1
        assert not report.is_trustworthy


class TestEntityScoping:
    """An entity listing must not answer a goods question.

    `Scope` treats an empty axis as unrestricted, which is right — but a
    *populated* axis the caller gave no value for is a miss, not a pass. Without
    that, every duty lookup returns all 187 UFLPA listings as applicable and
    forced-labour prohibitions land in the duty stack.
    """

    def _listing(self):
        return Instrument(
            id="UFLPA-x", issuer=Issuer.DHS, kind=InstrumentKind.ENTITY_LISTING,
            programme=Programme.UFLPA, scope=Scope(entity_names=["Acme Textiles Ltd"]),
            effect=Effect(kind=EffectKind.PROHIBITION, additive=False),
            effective_from=JUNE, retrieved_at=datetime.now(UTC), origin=Origin.SYNTHETIC,
        )

    def test_hts_query_does_not_return_entity_listings(self):
        report = resolve([self._listing()], on=SEPT, hts="7326.90.86", origin="CN")
        assert report.effective == []

    def test_entity_query_finds_the_listing(self):
        report = resolve([self._listing()], on=SEPT, entity="Acme Textiles Ltd")
        assert [i.id for i in report.effective] == ["UFLPA-x"]

    def test_entity_match_ignores_case_and_padding(self):
        report = resolve([self._listing()], on=SEPT, entity="  acme textiles ltd ")
        assert len(report.effective) == 1

    def test_fixture_hts_query_returns_no_uflpa_listings(self):
        report = resolve(fx.generate_instruments(), on=SEPT, hts="7326.90.86", origin="CN")
        assert not [i for i in report.effective if i.programme is Programme.UFLPA]
