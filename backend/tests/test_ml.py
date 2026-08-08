"""Phase D: leakage guard, temporal split, model comparison, congestion, enrichment."""

from __future__ import annotations

import numpy as np
import pytest

from marsa.graph.build import build_graph
from marsa.graph.schema import EdgeKind, NodeKind, node_id
from marsa.graph.traverse import k_hop_subgraph
from marsa.ingestion import fixtures as fx
from marsa.ml.congestion import (
    CongestionTier,
    score_port,
    score_ports,
    tier_for,
)
from marsa.ml.dataset import Split, split_is_chronological, temporal_split
from marsa.ml.enrich import (
    MIN_SHIPMENTS_FOR_OBSERVED,
    enrich_corridors,
    enrich_ports,
    enrichment_summary,
)
from marsa.ml.features import (
    DEFAULT_SPEC,
    LEAKY_COLUMNS,
    LEAKY_SPEC,
    TARGET,
    LeakageError,
    assert_no_leakage,
    build_matrix,
    fit_category_dtypes,
    orders_to_frame,
)
from marsa.ml.models import calibration_points, evaluate
from marsa.ml.train import predict_corridor_risk, spread, summarise, train_and_compare


@pytest.fixture(scope="module")
def orders():
    return list(fx.generate_dataco_orders(1500))


@pytest.fixture(scope="module")
def frame(orders):
    return orders_to_frame(orders)


@pytest.fixture(scope="module")
def report_and_models(orders):
    return train_and_compare(orders, origin="synthetic", include_leakage_demo=True)


# ─── Leakage ─────────────────────────────────────────────────────────────────


class TestLeakageGuard:
    def test_label_is_literally_real_minus_scheduled(self, frame):
        """The premise of the whole guard, asserted rather than assumed.

        If this ever stops holding, the blocklist is over-strict and should be
        revisited — but while it holds, any model given these columns is
        reading the answer.
        """
        derived = (
            frame["days_for_shipping_real"] > frame["days_for_shipment_scheduled"]
        ).astype(int)
        assert (derived == frame[TARGET]).all()

    def test_delivery_status_is_a_reencoding_of_the_label(self, frame):
        crosstab = frame.groupby("delivery_status")[TARGET].nunique()
        assert (crosstab == 1).all(), "delivery_status maps 1:1 onto the label"

    def test_default_spec_excludes_every_leaky_column(self):
        assert not set(DEFAULT_SPEC.all) & set(LEAKY_COLUMNS)

    def test_assert_no_leakage_raises_with_reason(self):
        with pytest.raises(LeakageError, match="days_for_shipping_real"):
            assert_no_leakage([*DEFAULT_SPEC.all, "days_for_shipping_real"])

    def test_build_matrix_refuses_leaky_spec_by_default(self, frame):
        with pytest.raises(LeakageError):
            build_matrix(frame, LEAKY_SPEC)

    def test_leaky_spec_allowed_only_when_explicit(self, frame):
        X, _y = build_matrix(frame, LEAKY_SPEC, allow_leakage=True)
        assert "days_for_shipping_real" in X.columns

    def test_leakage_demo_reaches_near_perfect_score(self, report_and_models):
        """The demonstration must actually demonstrate something.

        A leaky model scoring ~1.0 against an honest ~0.75 is the concrete
        version of 'this dataset is easy to cheat on'.
        """
        report, _ = report_and_models
        assert report.leakage_demo is not None
        assert report.leakage_demo.metrics["roc_auc"] > 0.98
        assert report.leakage_demo.leaky is True

    def test_leaky_model_is_excluded_from_best(self, report_and_models):
        report, _ = report_and_models
        assert report.best is not None
        assert not report.best.leaky

    def test_every_leaky_column_documents_why(self):
        """The target needs no argument; everything else does."""
        for column, reason in LEAKY_COLUMNS.items():
            assert reason.strip(), f"{column} has no explanation at all"
            if column != TARGET:
                assert len(reason) > 40, f"{column} needs a substantive reason"


# ─── Features ────────────────────────────────────────────────────────────────


class TestFeatures:
    def test_calendar_features_derived(self, frame):
        assert frame["order_month"].between(1, 12).all()
        assert frame["order_dayofweek"].between(0, 6).all()
        assert frame["order_quarter"].between(1, 4).all()

    def test_rows_without_label_or_date_are_dropped(self):
        orders = list(fx.generate_dataco_orders(5))
        orders[0].late_delivery_risk = None
        orders[1].order_date = None
        assert len(orders_to_frame(orders)) == 3

    def test_frame_is_sorted_by_date(self, frame):
        assert frame["order_date"].is_monotonic_increasing

    def test_categoricals_typed_for_native_tree_handling(self, frame):
        X, _ = build_matrix(frame, DEFAULT_SPEC)
        for column in DEFAULT_SPEC.categorical:
            assert str(X[column].dtype) == "category"


class TestSharedCategoryDtypes:
    """A category's integer code must mean the same thing on both sides of the
    split.

    Deriving the dtype per slice looks equivalent and is not: pandas builds the
    level list from the values a slice happens to contain and assigns codes by
    sorted position, so one category missing from one side shifts every code
    after it. The model then predicts with a mapping it was not trained on and
    returns confident nonsense rather than raising.
    """

    def test_codes_are_stable_across_slices(self, frame):
        dtypes = fit_category_dtypes(frame, DEFAULT_SPEC)
        early = frame.iloc[: len(frame) // 2]
        late = frame.iloc[len(frame) // 2 :]

        X_early, _ = build_matrix(early, DEFAULT_SPEC, dtypes=dtypes)
        X_late, _ = build_matrix(late, DEFAULT_SPEC, dtypes=dtypes)

        for column in DEFAULT_SPEC.categorical:
            assert list(X_early[column].cat.categories) == list(
                X_late[column].cat.categories
            ), f"{column} category mapping differs between slices"

    def test_unshared_dtypes_would_drift(self, frame):
        """Pins the failure mode itself, so the fix cannot be quietly removed."""
        import pandas as pd

        column = "order_country"
        values = sorted(frame[column].dropna().unique())
        if len(values) < 2:
            pytest.skip("needs at least two categories to show drift")

        full = pd.Series(values).astype("category")
        # Drop the alphabetically-first value, as a temporal split routinely
        # does for a category that only trades in one period.
        missing_first = pd.Series(values[1:]).astype("category")

        survivor = values[1]
        assert list(full.cat.categories).index(survivor) != list(
            missing_first.cat.categories
        ).index(survivor), "per-slice dtypes no longer drift; this test is stale"

    def test_category_absent_from_a_slice_keeps_its_level(self, frame):
        dtypes = fit_category_dtypes(frame, DEFAULT_SPEC)
        column = "order_country"
        dropped = frame[column].dropna().iloc[0]
        subset = frame[frame[column] != dropped]
        if subset.empty:
            pytest.skip("only one country in this corpus")

        X, _ = build_matrix(subset, DEFAULT_SPEC, dtypes=dtypes)
        # Present as a level with zero rows — which is what lets a model fit on
        # one slice score another without an unseen-category error.
        assert dropped in list(X[column].cat.categories)
        assert (X[column] == dropped).sum() == 0

    def test_small_corpus_trains_without_unseen_category_error(self):
        """The exact CI configuration. 200 orders is small enough that rare
        countries land in only one slice; 5000 hides the bug entirely, which is
        why it reached CI and not local runs."""
        orders = list(fx.generate_dataco_orders(200, seed=42))
        report, _artifacts = train_and_compare(orders)
        assert report.results, "no models trained"
        assert report.best is not None

    def test_empty_frame_rejected(self):
        import pandas as pd

        with pytest.raises(ValueError, match="no rows"):
            build_matrix(pd.DataFrame(), DEFAULT_SPEC)


# ─── Temporal split ──────────────────────────────────────────────────────────


class TestTemporalSplit:
    def test_slices_do_not_overlap_in_time(self, frame):
        """A random split scores the model on a period it has already seen."""
        split = temporal_split(frame)
        assert split_is_chronological(split)
        assert split.train["order_date"].max() <= split.valid["order_date"].min()
        assert split.valid["order_date"].max() <= split.test["order_date"].min()

    def test_all_rows_are_kept(self, frame):
        split = temporal_split(frame)
        assert sum(split.sizes.values()) == len(frame)

    def test_fractions_respected(self, frame):
        split = temporal_split(frame, train_frac=0.6, valid_frac=0.2)
        assert abs(split.sizes["train"] / len(frame) - 0.6) < 0.02

    def test_invalid_fractions_rejected(self, frame):
        with pytest.raises(ValueError, match="invalid fractions"):
            temporal_split(frame, train_frac=0.9, valid_frac=0.2)

    def test_empty_frame_rejected(self):
        import pandas as pd

        with pytest.raises(ValueError, match="empty frame"):
            temporal_split(pd.DataFrame())

    def test_chronology_check_catches_shuffling(self, frame):
        shuffled = frame.sample(frac=1.0, random_state=0).reset_index(drop=True)
        n = len(shuffled)
        bad = Split(
            train=shuffled.iloc[: int(n * 0.7)],
            valid=shuffled.iloc[int(n * 0.7) : int(n * 0.85)],
            test=shuffled.iloc[int(n * 0.85) :],
        )
        assert not split_is_chronological(bad)


# ─── Metrics ─────────────────────────────────────────────────────────────────


class TestMetrics:
    def test_pr_auc_baseline_is_the_base_rate(self):
        """Without it a PR-AUC number is uninterpretable."""
        y = np.array([1] * 70 + [0] * 30)
        metrics = evaluate(y, np.random.default_rng(0).random(100))
        assert metrics["pr_auc_baseline"] == pytest.approx(0.70)

    def test_majority_accuracy_reported(self):
        y = np.array([1] * 80 + [0] * 20)
        metrics = evaluate(y, np.full(100, 0.9))
        assert metrics["majority_accuracy"] == pytest.approx(0.80)

    def test_perfect_predictions_score_perfectly(self):
        y = np.array([0, 0, 1, 1])
        metrics = evaluate(y, np.array([0.01, 0.02, 0.98, 0.99]))
        assert metrics["roc_auc"] == pytest.approx(1.0)
        assert metrics["brier"] < 0.01

    def test_lift_is_pr_auc_minus_baseline(self):
        y = np.array([1] * 60 + [0] * 40)
        metrics = evaluate(y, np.random.default_rng(1).random(100))
        assert metrics["pr_auc_lift"] == pytest.approx(
            metrics["pr_auc"] - metrics["pr_auc_baseline"]
        )

    def test_calibration_points_are_pairs(self):
        rng = np.random.default_rng(2)
        probs = rng.random(500)
        y = (rng.random(500) < probs).astype(int)
        points = calibration_points(y, probs)
        assert points
        assert all(0 <= p <= 1 and 0 <= o <= 1 for p, o in points)


# ─── Model comparison ────────────────────────────────────────────────────────


class TestComparison:
    def test_all_three_models_trained(self, report_and_models):
        report, fitted = report_and_models
        names = {r.name for r in report.results}
        assert names == {"logistic_regression", "xgboost", "lightgbm"}
        assert set(fitted) == names

    def test_every_model_beats_the_no_skill_baseline(self, report_and_models):
        report, _ = report_and_models
        for result in report.results:
            assert result.metrics["pr_auc"] > result.metrics["pr_auc_baseline"], (
                f"{result.name} does not beat predicting the base rate"
            )

    def test_probabilities_are_calibratable(self, report_and_models):
        """Brier matters because Phase C multiplies these into edge conductance."""
        report, _ = report_and_models
        for result in report.results:
            assert 0.0 <= result.metrics["brier"] <= 0.25

    def test_summary_is_ordered_by_pr_auc(self, report_and_models):
        report, _ = report_and_models
        frame = summarise(report)
        assert frame["pr_auc"].is_monotonic_decreasing

    def test_spread_is_reported(self, report_and_models):
        report, _ = report_and_models
        assert spread(report) >= 0.0

    def test_split_metadata_recorded(self, report_and_models):
        report, _ = report_and_models
        payload = report.as_dict()
        assert payload["splitSizes"]["train"] > payload["splitSizes"]["test"]
        assert "→" in payload["splitBoundaries"]["train"]

    def test_corridor_risk_is_probabilities(self, report_and_models, orders):
        report, fitted = report_and_models
        risk = predict_corridor_risk(fitted[report.best.name], orders)
        assert risk
        assert all(0.0 <= v <= 1.0 for v in risk.values())
        assert all(len(k) == 2 for k in risk)


# ─── Congestion ──────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def scored():
    return score_ports(fx.generate_ports(), fx.generate_country_logistics(years=(2023,)))


class TestCongestion:
    def test_ranked_worst_first(self, scored):
        assert scored == sorted(scored, key=lambda p: -p.score)

    def test_los_angeles_is_the_worst(self, scored):
        """62.4h average vessel time — by far the outlier in the port set."""
        assert scored[0].unlocode == "USLAX"

    def test_scores_bounded(self, scored):
        assert all(0.0 <= p.score <= 1.0 for p in scored)

    def test_tier_thresholds(self):
        assert tier_for(0.95) is CongestionTier.HIGH
        assert tier_for(0.60) is CongestionTier.ELEVATED
        assert tier_for(0.35) is CongestionTier.MODERATE
        assert tier_for(0.05) is CongestionTier.LOW

    def test_missing_signal_renormalises_rather_than_imputing_zero(self):
        """Imputing zero would flatter ports with poor reporting — exactly the
        ports most likely to have a problem."""
        from datetime import UTC, datetime

        from marsa.ingestion.schemas import Origin, PortPerformance, Provenance, SourceKind

        prov = Provenance(
            source=SourceKind.WORLDBANK, origin=Origin.LIVE, retrieved_at=datetime.now(UTC)
        )
        busy = PortPerformance(
            port_name="Busy", unlocode="XXBSY", country_iso3="XXX", year=2024,
            avg_vessel_hours=68.0, cppi_rank=None, provenance=prov,
        )
        result = score_port(busy, None)
        # Only vessel hours available, and it is near the ceiling — the score
        # must reflect that rather than being diluted by two absent signals.
        assert result.score > 0.85
        assert result.components["cppi_rank"] is None

    def test_components_always_explain_the_score(self, scored):
        for port in scored:
            assert set(port.components) == {
                "vessel_hours", "import_dwell_days", "cppi_rank"
            }

    def test_serialises(self, scored):
        payload = scored[0].as_dict()
        assert payload["tier"] in {t.value for t in CongestionTier}
        assert 0.0 <= payload["score"] <= 1.0


# ─── Graph enrichment (spec item 12) ─────────────────────────────────────────


@pytest.fixture(scope="module")
def enriched(orders):
    graph, _ = build_graph(
        orders=orders,
        countries=list(fx.generate_country_logistics(years=(2023,))),
        ports=list(fx.generate_ports()),
    )
    congestion = [
        p.as_dict()
        for p in score_ports(
            fx.generate_ports(), fx.generate_country_logistics(years=(2023,))
        )
    ]
    enrich_ports(graph, congestion)
    return graph


class TestEnrichment:
    def test_ports_carry_congestion(self, enriched):
        data = enriched.nodes[node_id(NodeKind.PORT, "AEJEA")]
        assert "congestion_score" in data
        assert data["congestion_tier"] in {t.value for t in CongestionTier}

    def test_congested_seed_produces_stronger_exposure(self, enriched):
        """Before enrichment every port injected a flat 1.0, so 'if Jebel Ali
        congests' and 'if Los Angeles congests' gave identical answers."""
        quiet = k_hop_subgraph(enriched, [node_id(NodeKind.PORT, "AEJEA")], hops=2)
        busy = k_hop_subgraph(enriched, [node_id(NodeKind.PORT, "USLAX")], hops=2)
        assert busy.exposure[busy.seeds[0]] > quiet.exposure[quiet.seeds[0]]

    def test_unscored_port_defaults_to_full_severity(self, orders):
        graph, _ = build_graph(orders=orders, ports=list(fx.generate_ports()))
        result = k_hop_subgraph(graph, [node_id(NodeKind.PORT, "AEJEA")], hops=1)
        assert result.exposure[result.seeds[0]] == 1.0

    def test_corridors_get_predicted_risk(self, enriched, report_and_models, orders):
        report, fitted = report_and_models
        risk = predict_corridor_risk(fitted[report.best.name], orders)
        updated = enrich_corridors(enriched, risk)
        assert updated > 0

        edges = [
            d for _, _, d in enriched.edges(data=True)
            if d.get("kind") == EdgeKind.SHIPS_FROM.value and "predicted_late_rate" in d
        ]
        assert edges
        assert all(0.0 <= d["predicted_late_rate"] <= 1.0 for d in edges)

    def test_sparse_corridors_defer_to_the_model(self, enriched, report_and_models, orders):
        """An observed rate over three shipments is a coin flip, not a measurement."""
        report, fitted = report_and_models
        enrich_corridors(enriched, predict_corridor_risk(fitted[report.best.name], orders))

        for _, _, data in enriched.edges(data=True):
            if data.get("kind") != EdgeKind.SHIPS_FROM.value:
                continue
            if "risk_source" not in data:
                continue
            shipments = int(data.get("shipments") or 0)
            if shipments < MIN_SHIPMENTS_FOR_OBSERVED:
                assert data["risk_source"] == "model"

    def test_observed_rate_is_preserved_alongside_prediction(
        self, enriched, report_and_models, orders
    ):
        """Neither number overwrites the other, so the two stay comparable."""
        report, fitted = report_and_models
        enrich_corridors(enriched, predict_corridor_risk(fitted[report.best.name], orders))
        for _, _, data in enriched.edges(data=True):
            if (
                data.get("kind") == EdgeKind.SHIPS_FROM.value
                and "predicted_late_rate" in data
            ):
                assert "late_rate" in data, "observed rate was overwritten"
                assert "effective_late_rate" in data

    def test_summary_reports_what_changed(self, enriched):
        summary = enrichment_summary(enriched)
        assert summary["portsScored"] > 0
        assert set(summary["tiers"]) <= {t.value for t in CongestionTier}

    def test_enrichment_is_optional(self, orders):
        """The graph must build before any model exists."""
        graph, _ = build_graph(orders=orders, ports=list(fx.generate_ports()))
        result = k_hop_subgraph(graph, [node_id(NodeKind.PORT, "AEJEA")], hops=2)
        assert result.node_count > 0
