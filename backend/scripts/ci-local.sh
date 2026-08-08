#!/usr/bin/env bash
# Run the backend half of .github/workflows/ci.yml, locally, in order.
#
# This exists because of a specific failure: the pipeline smoke test in CI runs
# `marsa-ingest fixtures --orders 200` while every local run used the default
# 5000. At 5000 orders every category appears in both halves of the temporal
# split and a genuine bug in the feature pipeline stayed invisible. At 200 it
# crashed. Five commits were pushed green-locally and red-in-CI before anyone
# looked at the workflow logs.
#
# So: verify with CI's parameters, not with the ones that happen to be handy.
#
#   ./scripts/ci-local.sh
#
# Needs a Postgres 16 with pgvector. Override the DSN if yours differs:
#   MARSA_TEST_DSN=postgresql://user:pass@host:5432/db ./scripts/ci-local.sh
set -euo pipefail

cd "$(dirname "$0")/.."

MARSA_TEST_DSN="${MARSA_TEST_DSN:-postgresql://marsa:marsa@127.0.0.1:5432/marsa}"
export MARSA_TEST_DSN
export DATABASE_URL="$MARSA_TEST_DSN"

# Scratch data directory so a verification run never overwrites the corpora,
# index or model card the repo has committed.
DATA_DIR="${DATA_DIR:-$(mktemp -d)/data}"
export DATA_DIR
mkdir -p "$DATA_DIR"

if [ -d .venv ]; then
    export PATH="$PWD/.venv/bin:$PATH"
fi

step() { printf '\n\033[1m▸ %s\033[0m\n' "$1"; }

step "Lint"
ruff check src tests

step "Test"
pytest -q

step "Pipeline smoke test"
# Same counts as the workflow. Do not "helpfully" raise them.
marsa-ingest fixtures --rulings 50 --orders 200
marsa-ingest status
marsa-index build --embedding-backend hashed
marsa-index stats
marsa-index query "8507.60.0020"
marsa-ml train
marsa-ml congestion
marsa-graph build
marsa-graph stats
marsa-graph query "Which suppliers are exposed if Jebel Ali congestion worsens?"

step "Router smoke test"
python - <<'EOF'
from marsa.router.graph import Router

router = Router("heuristic")
expected = {
    "What HTS code applies to lithium-ion power banks?": "fast",
    "Which of our shipments are exposed if the tariff on HS 8541 takes effect?": "agentic",
    "Which suppliers are exposed if Jebel Ali congestion worsens?": "graph",
}
for query, want in expected.items():
    audit = router.run(query)
    assert audit.path == want, f"{query!r} routed to {audit.path}, expected {want}"
    print(f"OK {audit.path:8s} {audit.latency_ms:7.1f}ms  {query[:50]}")
EOF
python -c "from marsa.api.main import app; print('API imports OK:', app.title)"

step "Validate the labelled routing set"
marsa-eval dataset

step "Evaluation harness"
marsa-eval run --limit 12
python - <<'EOF'
import json, os, pathlib

results = json.loads(
    (pathlib.Path(os.environ["DATA_DIR"]) / "eval" / "results.json").read_text()
)
gate = results["gate"]
assert not gate["mayPublish"], "gate published from synthetic corpora"
assert gate["status"] == "PROVISIONAL", gate["status"]
assert len(gate["blockers"]) == 3, gate["blockers"]
print(f"OK gate held: {len(gate['blockers'])} blockers, status {gate['status']}")
EOF

printf '\n\033[32m✓ all CI steps passed locally\033[0m\n'
printf '  scratch data dir: %s\n' "$DATA_DIR"
