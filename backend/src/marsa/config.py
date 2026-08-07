"""Runtime configuration, loaded from the environment / .env."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/src/marsa/config.py -> repo root
REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: str = "development"
    log_level: str = "INFO"

    # ─── Data layout ────────────────────────────────────────────────────────
    data_dir: Path = REPO_ROOT / "data"

    @property
    def raw_dir(self) -> Path:
        """Untouched upstream payloads, exactly as fetched."""
        return self.data_dir / "raw"

    @property
    def processed_dir(self) -> Path:
        """Normalised, schema-validated corpora consumed by later phases."""
        return self.data_dir / "processed"

    @property
    def cache_dir(self) -> Path:
        """HTTP response cache — makes re-runs free and ingestion resumable."""
        return self.data_dir / ".http-cache"

    # ─── Ingestion politeness ───────────────────────────────────────────────
    # CROSS is a public government service with no published rate limit. Two
    # requests per second with a real User-Agent is deliberately conservative;
    # the goal is a working research subset, not to stress someone's server.
    cross_requests_per_second: float = 2.0
    cross_max_rulings: int = 3000

    # UN Comtrade's free preview tier caps a single response at 500 records and
    # is rate-limited per IP. One request per second stays well inside it.
    comtrade_requests_per_second: float = 1.0
    comtrade_max_records: int = 500

    http_timeout_seconds: float = 30.0
    http_max_retries: int = 5

    # ─── Indexing (Phase B) ─────────────────────────────────────────────────
    # Postgres + pgvector. Unset (or left as the .env.example placeholder)
    # falls back to the local numpy store, which does exact search and is
    # genuinely fine at this corpus size.
    database_url: str | None = None

    # `auto` prefers the spec's MiniLM and degrades to hashed n-grams when the
    # weights cannot be fetched. Retrieval quality differs enormously between
    # the two, so the active backend is recorded in every index manifest.
    embedding_backend: str = "auto"
    rerank_backend: str = "auto"

    # ─── Router + API (Phase E) ─────────────────────────────────────────────
    # LLM providers for the complexity classifier. All three have free tiers;
    # `auto` prefers whichever is configured, in the order Groq → Gemini →
    # Cerebras, and degrades to a heuristic classifier when none is.
    groq_api_key: str | None = None
    gemini_api_key: str | None = None
    cerebras_api_key: str | None = None
    classifier_backend: str = "auto"

    # CORS is an allowlist, never "*" — the gateway is public and the browser
    # is its only legitimate caller.
    cors_allowed_origins: str = "http://localhost:3000"
    rate_limit_per_minute: int = 60

    # Kaggle needs credentials; DataCo cannot be fetched anonymously.
    kaggle_username: str | None = None
    kaggle_key: str | None = None


settings = Settings()
