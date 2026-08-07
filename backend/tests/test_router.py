"""Phase E: classifier, LLM layer, path nodes, LangGraph router and the API."""

from __future__ import annotations

import json

import pytest

from marsa.router.classifier import (
    CLASS_TO_PATH,
    FEW_SHOT,
    HeuristicClassifier,
    LLMClassifier,
    QueryClass,
    build_classifier,
    build_prompt,
    parse_response,
)
from marsa.router.llm import (
    MODELS,
    PROVIDER_PRIORITY,
    LLMClient,
    LLMResponse,
    LLMUnavailableError,
    LLMUsage,
    describe_llm_status,
    estimate_cost,
)
from marsa.router.state import AuditRecord, RetrievalStep, Source

FACTUAL = "What HTS code applies to lithium-ion power banks?"
MULTI_HOP = (
    "Which of our electronics shipments are exposed if the new tariff on "
    "HS 8541 takes effect next quarter?"
)
NETWORK = "Which suppliers are exposed if Jebel Ali congestion worsens?"


# ─── Classifier ──────────────────────────────────────────────────────────────


class TestHeuristicClassifier:
    @pytest.fixture
    def classifier(self):
        return HeuristicClassifier()

    def test_routes_the_three_flagship_queries(self, classifier):
        """The spec's own worked examples, one per path."""
        assert classifier.classify(FACTUAL).query_class is QueryClass.SIMPLE_FACTUAL
        assert classifier.classify(MULTI_HOP).query_class is QueryClass.MULTI_HOP
        assert classifier.classify(NETWORK).query_class is QueryClass.RELATIONSHIP

    def test_class_maps_to_path(self, classifier):
        for query in (FACTUAL, MULTI_HOP, NETWORK):
            result = classifier.classify(query)
            assert result.path == CLASS_TO_PATH[result.query_class]

    def test_declares_itself_not_llm(self, classifier):
        """Phase F must not fold heuristic routing accuracy into the LLM's."""
        assert classifier.classify(FACTUAL).is_llm is False

    def test_deterministic(self, classifier):
        a = classifier.classify(NETWORK)
        b = classifier.classify(NETWORK)
        assert a.query_class is b.query_class
        assert a.confidence == b.confidence

    def test_confidence_bounded(self, classifier):
        for query in (FACTUAL, MULTI_HOP, NETWORK, "asdf", ""):
            assert 0.0 <= classifier.classify(query).confidence <= 1.0

    def test_unmatched_query_defaults_to_the_cheap_path(self, classifier):
        """A wrong fast-path answer costs a second and looks thin; a wrong
        agentic route burns the budget the project exists to conserve."""
        result = classifier.classify("qwertyuiop zxcvbnm")
        assert result.query_class is QueryClass.SIMPLE_FACTUAL
        assert result.confidence < 0.5
        assert "cheapest" in result.rationale

    def test_tariff_code_alone_reads_as_a_lookup(self, classifier):
        assert classifier.classify("8507.60.0020").query_class is QueryClass.SIMPLE_FACTUAL

    def test_code_plus_network_language_stays_network(self, classifier):
        """'exposure under 8541' names a code and is still a traversal."""
        result = classifier.classify("What is our exposure under HS 8541 across suppliers?")
        assert result.query_class is QueryClass.RELATIONSHIP

    def test_scores_are_reported_for_debugging(self, classifier):
        scores = classifier.classify(NETWORK).scores
        assert set(scores) == {c.value for c in QueryClass}
        assert scores[QueryClass.RELATIONSHIP.value] > 0


class TestPromptAndParsing:
    def test_prompt_contains_every_example(self):
        prompt = build_prompt("test query")
        for example, _ in FEW_SHOT:
            assert example in prompt
        assert "test query" in prompt

    def test_few_shot_covers_all_three_classes(self):
        classes = {json.loads(answer)["class"] for _, answer in FEW_SHOT}
        assert classes == {c.value for c in QueryClass}

    def test_parses_bare_json(self):
        parsed = parse_response('{"class": "simple_factual", "confidence": 0.9, "reason": "x"}')
        assert parsed == (QueryClass.SIMPLE_FACTUAL, 0.9, "x")

    def test_parses_json_inside_prose_and_fences(self):
        """Models wrap JSON no matter how firmly you ask them not to."""
        text = (
            'Sure!\n```json\n'
            '{"class": "relationship_network", "confidence": 0.8, "reason": "y"}\n```'
        )
        parsed = parse_response(text)
        assert parsed is not None
        assert parsed[0] is QueryClass.RELATIONSHIP

    def test_rejects_unknown_class(self):
        assert parse_response('{"class": "banana", "confidence": 0.9}') is None

    def test_rejects_non_json(self):
        assert parse_response("I think it is a simple factual query.") is None

    def test_clamps_out_of_range_confidence(self):
        parsed = parse_response('{"class": "simple_factual", "confidence": 4.2}')
        assert parsed is not None and parsed[1] == 1.0

    def test_tolerates_missing_confidence(self):
        parsed = parse_response('{"class": "simple_factual"}')
        assert parsed is not None and 0.0 <= parsed[1] <= 1.0


class TestLLMClassifierFallback:
    def test_falls_back_when_no_provider(self):
        classifier = LLMClassifier(LLMClient(providers=[]))
        result = classifier.classify(NETWORK)
        assert result.query_class is QueryClass.RELATIONSHIP
        assert result.is_llm is False
        assert "LLM unavailable" in result.rationale

    def test_falls_back_on_unparseable_response(self, monkeypatch):
        client = LLMClient(providers=["groq"])
        monkeypatch.setattr(
            client, "complete", lambda *a, **k: LLMResponse("total gibberish", LLMUsage())
        )
        result = LLMClassifier(client).classify(FACTUAL)
        assert result.is_llm is False
        assert "unparseable" in result.rationale

    def test_uses_the_llm_when_it_answers(self, monkeypatch):
        client = LLMClient(providers=["groq"])
        monkeypatch.setattr(
            client,
            "complete",
            lambda *a, **k: LLMResponse(
                '{"class": "multi_hop_reasoning", "confidence": 0.91, "reason": "chained"}',
                LLMUsage(provider="groq", input_tokens=210, output_tokens=25, cost_usd=0.0001),
            ),
        )
        result = LLMClassifier(client).classify(MULTI_HOP)
        assert result.is_llm is True
        assert result.query_class is QueryClass.MULTI_HOP
        assert result.usage.cost_usd > 0

    def test_auto_backend_degrades_without_keys(self):
        assert isinstance(build_classifier("auto"), HeuristicClassifier | LLMClassifier)

    def test_unknown_backend_rejected(self):
        with pytest.raises(ValueError, match="unknown classifier backend"):
            build_classifier("telepathy")


# ─── LLM layer ───────────────────────────────────────────────────────────────


class TestLLMLayer:
    def test_groq_is_tried_first(self):
        """The classifier is on every query's critical path, including the
        sub-second one, so the fastest provider leads."""
        assert PROVIDER_PRIORITY[0] == "groq"

    def test_every_model_has_a_published_price(self):
        for name, spec in MODELS.items():
            assert spec.input_per_mtok > 0, f"{name} has no input price"
            assert spec.output_per_mtok > 0, f"{name} has no output price"

    def test_cost_estimate_is_per_million_tokens(self):
        spec = MODELS["gemini"]  # $0.10 in, $0.40 out
        assert estimate_cost(spec, 1_000_000, 0) == pytest.approx(0.10)
        assert estimate_cost(spec, 0, 1_000_000) == pytest.approx(0.40)

    def test_zero_tokens_costs_nothing(self):
        assert estimate_cost(MODELS["groq"], 0, 0) == 0.0

    def test_client_without_providers_raises_actionably(self):
        with pytest.raises(LLMUnavailableError, match="GROQ_API_KEY"):
            LLMClient(providers=[]).complete("hi")

    def test_status_is_honest_about_the_fallback(self):
        status = describe_llm_status()
        if not status["available"]:
            assert "NOT the few-shot LLM" in status["note"]

    def test_usage_serialises(self):
        payload = LLMUsage(provider="groq", input_tokens=10, output_tokens=5).as_dict()
        assert payload["provider"] == "groq"
        assert payload["inputTokens"] == 10


# ─── Audit record ────────────────────────────────────────────────────────────


class TestAuditRecord:
    def test_carries_everything_spec_item_17_requires(self):
        audit = AuditRecord(
            query="q",
            path="fast",
            query_class="simple_factual",
            confidence=0.9,
            steps=[RetrievalStep("Classify", "x", 1.0)],
            sources=[Source("NY N1", "cbp_cross_ruling")],
            latency_ms=120.0,
            cost_usd=0.0002,
        )
        payload = audit.as_dict()
        # query, chosen path, sources, confidence, latency, estimated cost
        for key in ("query", "path", "sources", "confidence", "latencyMs", "costUsd"):
            assert key in payload

    def test_query_ids_are_unique(self):
        assert AuditRecord().query_id != AuditRecord().query_id

    def test_serialises_to_json(self):
        json.dumps(AuditRecord(query="q").as_dict())


# ─── Router ──────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def router():
    from marsa.router.graph import Router

    return Router("heuristic")


class TestRouter:
    def test_routes_each_query_to_its_path(self, router):
        assert router.run(FACTUAL).path == "fast"
        assert router.run(MULTI_HOP).path == "agentic"
        assert router.run(NETWORK).path == "graph"

    def test_every_path_emits_the_same_audit_shape(self, router):
        """One record type across all three paths is what makes the Phase F
        comparison possible without reconciliation."""
        shapes = [set(router.run(q).as_dict()) for q in (FACTUAL, MULTI_HOP, NETWORK)]
        assert shapes[0] == shapes[1] == shapes[2]

    def test_classify_step_always_comes_first(self, router):
        for query in (FACTUAL, MULTI_HOP, NETWORK):
            assert router.run(query).steps[0].label == "Classify"

    def test_latency_is_wall_clock(self, router):
        audit = router.run(NETWORK)
        assert audit.latency_ms > 0

    def test_heuristic_run_is_flagged_not_fully_specified(self, router):
        audit = router.run(FACTUAL)
        assert audit.fully_specified is False
        assert "classifier_not_llm" in audit.warnings

    def test_agentic_path_records_retries(self, router):
        assert router.run(MULTI_HOP).retries >= 0


class TestStreaming:
    def test_classification_is_emitted_before_any_step(self, router):
        """Showing *why* a path was chosen before what it found is the demo."""
        events = [e["event"] for e in router.stream(NETWORK)]
        assert events[0] == "classified"
        assert "step" in events
        assert events[-1] == "audit"

    def test_answer_precedes_audit(self, router):
        events = [e["event"] for e in router.stream(FACTUAL)]
        assert events.index("answer") < events.index("audit")

    def test_steps_are_not_re_emitted(self, router):
        steps = [e["data"]["label"] for e in router.stream(NETWORK) if e["event"] == "step"]
        assert len(steps) == len(set(steps)), "a step was streamed twice"

    def test_every_event_is_json_serialisable(self, router):
        for event in router.stream(MULTI_HOP):
            json.dumps(event["data"])


# ─── API ─────────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient

    from marsa.api.main import app

    with TestClient(app) as test_client:
        yield test_client


class TestApi:
    def test_health_reports_resources_and_llm(self, client):
        payload = client.get("/health").json()
        assert payload["status"] in {"ok", "degraded"}
        assert "fastPathIndex" in payload["resources"]
        assert "available" in payload["llm"]

    def test_query_returns_the_audit_record(self, client):
        response = client.post("/query", json={"query": NETWORK})
        assert response.status_code == 200
        payload = response.json()
        assert payload["path"] == "graph"
        assert payload["steps"]

    def test_blank_query_rejected(self, client):
        assert client.post("/query", json={"query": "   "}).status_code == 422

    def test_short_query_rejected(self, client):
        assert client.post("/query", json={"query": "ab"}).status_code == 422

    def test_oversized_query_rejected(self, client):
        """An unbounded query is a DoS vector against a per-token-billed LLM."""
        assert client.post("/query", json={"query": "x" * 5000}).status_code == 422

    def test_missing_query_rejected(self, client):
        assert client.post("/query", json={}).status_code == 422

    def test_metrics_are_prometheus_format(self, client):
        body = client.get("/metrics").text
        assert "marsa_queries_total" in body
        assert "marsa_query_latency_seconds" in body

    def test_cost_endpoint_explains_the_pricing(self, client):
        client.post("/query", json={"query": FACTUAL})
        payload = client.get("/cost").json()
        assert payload["totalQueries"] >= 1
        assert "published per-token rates" in payload["note"]

    def test_root_lists_endpoints(self, client):
        assert "/query/stream" in client.get("/").json()["endpoints"]

    def test_stream_emits_sse_events(self, client):
        with client.stream(
            "POST", "/query/stream", json={"query": NETWORK}
        ) as response:
            assert response.status_code == 200
            body = "".join(response.iter_text())
        assert "event: classified" in body
        assert "event: audit" in body
        assert body.index("event: classified") < body.index("event: audit")
