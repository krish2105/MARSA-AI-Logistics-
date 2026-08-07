"""Phase B index CLI.

    marsa-index build              # chunk + embed + BM25 from the CROSS corpus
    marsa-index query "..."        # run the fast path and show the trace
    marsa-index stats              # what's indexed, and with which backends
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from marsa.config import settings
from marsa.indexing.chunking import SECTION_WEIGHTS, chunk_ruling
from marsa.indexing.embeddings import EMBEDDING_DIM, build_embedder
from marsa.indexing.hybrid import FastPathRetriever
from marsa.indexing.rerank import build_reranker
from marsa.indexing.sparse import Bm25Index
from marsa.indexing.store import NumpyVectorStore, PgVectorStore, build_store
from marsa.ingestion.schemas import CrossRuling, read_jsonl
from marsa.logging import configure, get_logger

app = typer.Typer(add_completion=False, help="MARSA AI — Phase B fast-path index")
console = Console()
log = get_logger(__name__)

CORPUS = "cross_rulings.jsonl"
BM25_FILE = "bm25.pkl"
VECTORS_FILE = "vectors.pkl"
INDEX_MANIFEST = "index.manifest.json"


def _index_dir() -> Path:
    return settings.data_dir / "index"


def _load_rulings() -> list[CrossRuling]:
    path = settings.processed_dir / CORPUS
    if not path.exists():
        console.print(
            f"[red]✗ No CROSS corpus at {path}.[/]\n"
            "  Run [bold]marsa-ingest fixtures[/] (offline) or "
            "[bold]marsa-ingest cross[/] (live) first."
        )
        raise typer.Exit(code=2)
    return list(read_jsonl(path, CrossRuling))


@app.command()
def build(
    embedding_backend: str = typer.Option(
        "auto", help="auto | minilm | hashed — 'auto' prefers MiniLM, falls back"
    ),
    batch_size: int = typer.Option(256, help="Chunks embedded per batch"),
    no_ann: bool = typer.Option(False, "--no-ann", help="Skip the HNSW index (pgvector only)"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Chunk the CROSS corpus, embed it, and build both indexes."""
    configure(level="DEBUG" if verbose else settings.log_level, human=True)
    started = datetime.now(UTC)

    rulings = _load_rulings()
    console.print(f"[dim]Loaded {len(rulings):,} rulings[/]")

    chunks = [c for ruling in rulings for c in chunk_ruling(ruling)]
    if not chunks:
        console.print("[red]✗ Chunking produced nothing — is the corpus empty?[/]")
        raise typer.Exit(code=1)

    words = [c.word_count for c in chunks]
    console.print(
        f"[dim]Chunked into {len(chunks):,} children "
        f"({len(chunks) / len(rulings):.1f} per ruling, "
        f"median {sorted(words)[len(words) // 2]} words)[/]"
    )

    embedder = build_embedder(embedding_backend)
    if not embedder.is_semantic:
        console.print(
            "[yellow]⚠ Using the non-semantic hashed-ngram backend.[/] "
            "MiniLM weights are unavailable (huggingface.co unreachable). "
            "Retrieval will match lexically, not semantically — do not report "
            "quality figures from this index."
        )

    store = build_store(_dsn(), _index_dir() / VECTORS_FILE)
    store.create(embedder.dim)

    total = 0
    for start in range(0, len(chunks), batch_size):
        batch = chunks[start : start + batch_size]
        vectors = embedder.encode([c.text for c in batch])
        total += store.upsert(batch, vectors)
        console.print(f"[dim]  embedded {total:,}/{len(chunks):,}[/]", end="\r")

    console.print(f"[green]✓[/] embedded {total:,} chunks with {embedder.name}     ")

    if isinstance(store, PgVectorStore) and not no_ann:
        store.build_ann_index()
        console.print("[green]✓[/] HNSW index built (m=16, ef_construction=64)")
    elif isinstance(store, NumpyVectorStore):
        store.save()
        console.print(f"[green]✓[/] vectors saved → {store.path.name}")

    bm25 = Bm25Index()
    bm25.build(chunks)
    bm25.save(_index_dir() / BM25_FILE)
    console.print(f"[green]✓[/] BM25 index built over {bm25.count():,} chunks")

    manifest: dict[str, Any] = {
        "built_at": datetime.now(UTC).isoformat(),
        "duration_seconds": round((datetime.now(UTC) - started).total_seconds(), 2),
        "rulings": len(rulings),
        "chunks": len(chunks),
        "chunks_per_ruling": round(len(chunks) / len(rulings), 2),
        "median_chunk_words": sorted(words)[len(words) // 2],
        "vector_store": type(store).__name__,
        "embedding": embedder.describe(),
        "sections": _section_counts(chunks),
        "semantic_embeddings": embedder.is_semantic,
    }
    path = _index_dir() / INDEX_MANIFEST
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    store.close()
    console.print(f"[green]✓[/] manifest → {path.name}")


@app.command()
def query(
    text: str = typer.Argument(..., help="The question to run through the fast path"),
    limit: int = typer.Option(5, help="Rulings to return"),
    embedding_backend: str = typer.Option("auto"),
    rerank_backend: str = typer.Option("auto", help="auto | cross-encoder | lexical"),
    show_text: bool = typer.Option(False, "--show-text", help="Print matching chunk text"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Run one query through the full fast path and print the trace."""
    configure(level="DEBUG" if verbose else "WARNING", human=True)

    retriever = _build_retriever(embedding_backend, rerank_backend)
    rulings, trace = retriever.retrieve(text, limit=limit)

    console.print(f"\n[bold]{text}[/]\n")

    if not rulings:
        console.print("[yellow]No matches.[/]")
    else:
        table = Table(header_style="bold", show_lines=show_text)
        table.add_column("#", width=3)
        table.add_column("Ruling")
        table.add_column("Score", justify="right")
        table.add_column("Section")
        table.add_column("HTS codes")
        for i, ruling in enumerate(rulings, 1):
            table.add_row(
                str(i),
                ruling.ruling_number,
                f"{ruling.score:.4f}",
                ruling.best_section,
                ", ".join(ruling.hts_codes[:3]) or "—",
            )
        console.print(table)

        if show_text:
            for ruling in rulings:
                console.print(f"\n[bold]{ruling.ruling_number}[/]")
                for scored in ruling.chunks:
                    console.print(
                        f"  [dim]{scored.chunk.section} · {scored.retriever} "
                        f"· {scored.score:.4f}[/]"
                    )
                    console.print(f"  {scored.chunk.text[:320]}…\n")

    console.print("\n[dim]Trace:[/]")
    console.print_json(json.dumps(trace.as_dict()))

    if not trace.fully_semantic:
        console.print(
            "\n[yellow]⚠ At least one stage ran on a non-semantic fallback — "
            "these results reflect lexical overlap, not the system as specified.[/]"
        )


@app.command()
def stats() -> None:
    """Show what is indexed and with which backends."""
    path = _index_dir() / INDEX_MANIFEST
    if not path.exists():
        console.print("[yellow]No index built yet. Run [bold]marsa-index build[/].[/]")
        raise typer.Exit(code=0)

    manifest = json.loads(path.read_text(encoding="utf-8"))

    table = Table(title="Phase B — fast-path index", header_style="bold")
    table.add_column("Property")
    table.add_column("Value", justify="right")
    for key in (
        "rulings", "chunks", "chunks_per_ruling", "median_chunk_words",
        "vector_store", "duration_seconds", "built_at",
    ):
        table.add_row(key.replace("_", " "), str(manifest.get(key, "—")))
    table.add_row("embedding", str(manifest["embedding"]["backend"]))
    table.add_row("dimensions", str(manifest["embedding"]["dim"]))
    console.print(table)

    sections = Table(title="Chunks by section", header_style="bold")
    sections.add_column("Section")
    sections.add_column("Chunks", justify="right")
    sections.add_column("Rollup weight", justify="right")
    for name, count in sorted(manifest["sections"].items(), key=lambda kv: -kv[1]):
        sections.add_row(name, f"{count:,}", f"{SECTION_WEIGHTS.get(name, 0.6):.2f}")
    console.print(sections)

    if not manifest.get("semantic_embeddings", False):
        console.print(
            "[yellow]⚠ Index built with non-semantic embeddings — "
            "no retrieval-quality figure from it is publishable.[/]"
        )


# ─────────────────────────────────────────────────────────────────────────────


def _dsn() -> str | None:
    dsn = getattr(settings, "database_url", None)
    # The .env template ships a placeholder; treat it as unset rather than
    # spending 30 seconds timing out against a host that does not exist.
    if not dsn or "user:password@host" in dsn:
        return None
    return dsn


def _section_counts(chunks: list[Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for chunk in chunks:
        counts[chunk.section] = counts.get(chunk.section, 0) + 1
    return counts


def _build_retriever(embedding_backend: str, rerank_backend: str) -> FastPathRetriever:
    embedder = build_embedder(embedding_backend, dim=EMBEDDING_DIM)

    dsn = _dsn()
    if dsn:
        store = build_store(dsn, _index_dir() / VECTORS_FILE)
    else:
        store = NumpyVectorStore(_index_dir() / VECTORS_FILE)
        try:
            store.load()
        except FileNotFoundError as exc:
            console.print(f"[red]✗ {exc}[/]")
            raise typer.Exit(code=2) from exc

    try:
        bm25 = Bm25Index.load(_index_dir() / BM25_FILE)
    except FileNotFoundError as exc:
        console.print(f"[red]✗ {exc}[/]")
        raise typer.Exit(code=2) from exc

    return FastPathRetriever(
        embedder=embedder,
        store=store,
        bm25=bm25,
        reranker=build_reranker(rerank_backend),
    )


if __name__ == "__main__":
    app()
