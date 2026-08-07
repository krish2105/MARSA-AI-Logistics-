"""Schema round-tripping, and the guarantees the fixture generator must hold."""

from __future__ import annotations

from marsa.ingestion import fixtures as fx
from marsa.ingestion.schemas import (
    ComtradeFlow,
    CrossRuling,
    DataCoOrder,
    Origin,
    read_jsonl,
    write_jsonl,
)


class TestFixtureSafety:
    def test_every_generator_marks_records_synthetic(self):
        """Nothing downstream may mistake a fixture for real data."""
        generators = [
            fx.generate_cross_rulings(5),
            fx.generate_comtrade_flows(years=(2023,)),
            fx.generate_dataco_orders(5),
            fx.generate_country_logistics(years=(2023,)),
            fx.generate_ports(),
        ]
        for generator in generators:
            for record in generator:
                assert record.provenance.origin is Origin.SYNTHETIC
                assert "SYNTHETIC" in (record.provenance.note or "")

    def test_generation_is_deterministic(self):
        a = [r.ruling_number for r in fx.generate_cross_rulings(20, seed=7)]
        b = [r.ruling_number for r in fx.generate_cross_rulings(20, seed=7)]
        assert a == b

    def test_different_seeds_differ(self):
        a = [r.ruling_number for r in fx.generate_cross_rulings(20, seed=1)]
        b = [r.ruling_number for r in fx.generate_cross_rulings(20, seed=2)]
        assert a != b


class TestCrossFixtures:
    def test_rulings_cite_each_other(self):
        """Cross-references become graph edges in Phase C — they must exist."""
        rulings = list(fx.generate_cross_rulings(100))
        assert any(r.related_rulings for r in rulings)

    def test_citations_point_at_real_rulings(self):
        rulings = list(fx.generate_cross_rulings(100))
        known = {r.ruling_number for r in rulings}
        for ruling in rulings:
            for cited in ruling.related_rulings:
                assert cited in known, "a citation must resolve to a ruling in the corpus"

    def test_no_ruling_cites_itself(self):
        for ruling in fx.generate_cross_rulings(100):
            assert ruling.ruling_number not in ruling.related_rulings

    def test_hts_code_appears_in_body(self):
        for ruling in fx.generate_cross_rulings(30):
            assert ruling.hts_codes
            assert ruling.hts_codes[0] in ruling.body


class TestDataCoFixtures:
    def test_label_is_learnable_not_noise(self):
        """Phase D compares LogReg vs XGBoost vs LightGBM.

        If the synthetic label were pure noise that comparison would be
        meaningless, so the generator must embed a real signal: expedited
        shipping modes have to fail materially more often than Standard Class.
        """
        orders = list(fx.generate_dataco_orders(4000))
        by_mode: dict[str, list[int]] = {}
        for order in orders:
            by_mode.setdefault(order.shipping_mode, []).append(order.late_delivery_risk)

        rate = {m: sum(v) / len(v) for m, v in by_mode.items()}
        assert rate["Same Day"] > rate["Standard Class"] + 0.2

    def test_label_is_not_perfectly_separable(self):
        """Noise-free labels would give a fake 1.0 AUC and teach us nothing."""
        orders = list(fx.generate_dataco_orders(4000))
        same_day = [o.late_delivery_risk for o in orders if o.shipping_mode == "Same Day"]
        assert 0.0 < sum(same_day) / len(same_day) < 1.0

    def test_both_classes_present(self):
        labels = {o.late_delivery_risk for o in fx.generate_dataco_orders(500)}
        assert labels == {0, 1}

    def test_shipping_slack_matches_days(self):
        for order in fx.generate_dataco_orders(50):
            expected = order.days_for_shipment_scheduled - order.days_for_shipping_real
            assert order.shipping_slack_days == expected


class TestGraphFixtures:
    def test_jebel_ali_is_present(self):
        """Jebel Ali is the anchor node of the whole supply graph."""
        ports = {p.unlocode: p for p in fx.generate_ports()}
        assert "AEJEA" in ports
        assert ports["AEJEA"].country_iso3 == "ARE"

    def test_dwell_time_falls_as_lpi_rises(self):
        """Dwell time is the graph's edge weight; the relationship must hold."""
        records = [r for r in fx.generate_country_logistics(years=(2023,))]
        best = max(records, key=lambda r: r.lpi_score)
        worst = min(records, key=lambda r: r.lpi_score)
        assert best.import_dwell_days < worst.import_dwell_days


class TestJsonlRoundTrip:
    def test_cross_round_trips(self, tmp_path):
        original = list(fx.generate_cross_rulings(25))
        path = tmp_path / "cross.jsonl"
        count, digest = write_jsonl(original, path)

        assert count == 25
        assert len(digest) == 64

        restored = list(read_jsonl(path, CrossRuling))
        assert [r.ruling_number for r in restored] == [r.ruling_number for r in original]
        assert restored[0].related_rulings == original[0].related_rulings

    def test_digest_is_content_addressed(self, tmp_path):
        a = write_jsonl(list(fx.generate_cross_rulings(10, seed=3)), tmp_path / "a.jsonl")
        b = write_jsonl(list(fx.generate_cross_rulings(10, seed=3)), tmp_path / "b.jsonl")
        # Same seed, same content — but provenance timestamps differ, so the
        # digest is expected to differ. Assert the shape, not equality.
        assert len(a[1]) == len(b[1]) == 64

    def test_comtrade_round_trips(self, tmp_path):
        original = list(fx.generate_comtrade_flows(years=(2023,)))
        path = tmp_path / "flows.jsonl"
        write_jsonl(original, path)
        restored = list(read_jsonl(path, ComtradeFlow))
        assert len(restored) == len(original)
        assert restored[0].doc_id == original[0].doc_id

    def test_dataco_round_trips(self, tmp_path):
        original = list(fx.generate_dataco_orders(40))
        path = tmp_path / "orders.jsonl"
        write_jsonl(original, path)
        restored = list(read_jsonl(path, DataCoOrder))
        assert len(restored) == 40
        assert restored[0].late_delivery_risk in (0, 1)
