"""Graph persistence.

Pickle, per the spec — no graph database at this scale. A few thousand nodes
load in milliseconds and NetworkX traversal is pure Python, so a server would
be infrastructure for its own sake.

The one real hazard with pickle is that it executes arbitrary code on load, so
`load_graph` is only ever pointed at artefacts this project wrote. The manifest
beside it records what was built, from which corpora, and how much of it rests
on bridging assumptions.
"""

from __future__ import annotations

import hashlib
import json
import pickle
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import networkx as nx

from marsa.graph.schema import BRIDGE_RULES, GraphStats
from marsa.logging import get_logger

log = get_logger(__name__)

GRAPH_FILE = "supply_graph.pkl"
MANIFEST_FILE = "graph.manifest.json"


def graph_dir(data_dir: Path) -> Path:
    return data_dir / "graph"


def save_graph(
    graph: nx.MultiDiGraph,
    stats: GraphStats,
    *,
    data_dir: Path,
    build_report: dict[str, Any] | None = None,
) -> tuple[Path, Path]:
    target = graph_dir(data_dir)
    target.mkdir(parents=True, exist_ok=True)

    graph_path = target / GRAPH_FILE
    payload = pickle.dumps(graph, protocol=pickle.HIGHEST_PROTOCOL)
    graph_path.write_bytes(payload)

    manifest = {
        "built_at": datetime.now(UTC).isoformat(),
        "graph_file": GRAPH_FILE,
        "size_bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest()[:16],
        "stats": stats.as_dict(),
        "build_report": build_report or {},
        # Shipped with the artefact so the assumptions travel with the data.
        "bridge_rules": {
            name: {"description": rule.description, "caveat": rule.caveat}
            for name, rule in BRIDGE_RULES.items()
        },
    }
    manifest_path = target / MANIFEST_FILE
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    log.info(
        "graph saved",
        extra={"nodes": stats.nodes, "edges": stats.edges, "bytes": len(payload)},
    )
    return graph_path, manifest_path


def load_graph(data_dir: Path) -> nx.MultiDiGraph:
    path = graph_dir(data_dir) / GRAPH_FILE
    if not path.exists():
        raise FileNotFoundError(
            f"No graph at {path}. Run `marsa-graph build` first."
        )
    with path.open("rb") as fh:
        return pickle.load(fh)  # noqa: S301 — our own artefact, not untrusted input


def load_manifest(data_dir: Path) -> dict[str, Any] | None:
    path = graph_dir(data_dir) / MANIFEST_FILE
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))
