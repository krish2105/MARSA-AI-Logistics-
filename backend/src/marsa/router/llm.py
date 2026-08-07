"""LLM access, via LiteLLM, with cost accounting.

Provider order follows the spec: Groq first (the classifier is on the latency
critical path and Groq is the fastest of the three), then Gemini, then Cerebras.
All three have free tiers; none needs a paid key.

Cost accounting uses **published per-token rates even though the free tier bills
nothing**. That is deliberate — the project's whole thesis is that routing is
cost-aware, and a cost counter that reads $0.00000 forever makes the claim
untestable. Pricing a free-tier call at its published rate is what turns "we
route to the cheapest path that works" from a slogan into a number you can
check.

Every response carries token counts and an estimated cost, which the audit log
records per query and the Route Badge shows in the UI.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from marsa.config import settings
from marsa.logging import get_logger

log = get_logger(__name__)


@dataclass(frozen=True)
class ModelSpec:
    """A model and its published price, in USD per million tokens."""

    litellm_id: str
    input_per_mtok: float
    output_per_mtok: float
    api_key_setting: str
    label: str


#: Published rates at time of writing. Free tiers bill $0; these exist so the
#: cost-aware claim is measurable rather than rhetorical.
MODELS: dict[str, ModelSpec] = {
    "groq": ModelSpec(
        litellm_id="groq/llama-3.3-70b-versatile",
        input_per_mtok=0.59,
        output_per_mtok=0.79,
        api_key_setting="groq_api_key",
        label="Groq Llama 3.3 70B",
    ),
    "gemini": ModelSpec(
        litellm_id="gemini/gemini-2.0-flash",
        input_per_mtok=0.10,
        output_per_mtok=0.40,
        api_key_setting="gemini_api_key",
        label="Gemini 2.0 Flash",
    ),
    "cerebras": ModelSpec(
        litellm_id="cerebras/llama-3.3-70b",
        input_per_mtok=0.85,
        output_per_mtok=1.20,
        api_key_setting="cerebras_api_key",
        label="Cerebras Llama 3.3 70B",
    ),
}

#: Spec order: Groq for classifier latency, then the others as fallback.
PROVIDER_PRIORITY: tuple[str, ...] = ("groq", "gemini", "cerebras")


@dataclass
class LLMUsage:
    """Token accounting for one call."""

    provider: str = "none"
    model: str = "none"
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "inputTokens": self.input_tokens,
            "outputTokens": self.output_tokens,
            "costUsd": round(self.cost_usd, 8),
            "latencyMs": round(self.latency_ms, 2),
        }


@dataclass
class LLMResponse:
    text: str
    usage: LLMUsage = field(default_factory=LLMUsage)


class LLMUnavailableError(RuntimeError):
    """No provider is configured or reachable."""


def available_providers() -> list[str]:
    """Providers with an API key configured, in priority order."""
    return [
        name
        for name in PROVIDER_PRIORITY
        if getattr(settings, MODELS[name].api_key_setting, None)
    ]


def estimate_cost(spec: ModelSpec, input_tokens: int, output_tokens: int) -> float:
    return (
        input_tokens * spec.input_per_mtok + output_tokens * spec.output_per_mtok
    ) / 1_000_000


class LLMClient:
    """Thin LiteLLM wrapper.

    Tries each configured provider in priority order and falls through on
    failure, so a rate-limited Groq key degrades to Gemini rather than taking
    the whole router down.
    """

    def __init__(self, providers: list[str] | None = None, *, timeout: float = 20.0) -> None:
        self.providers = providers if providers is not None else available_providers()
        self.timeout = timeout

    @property
    def is_available(self) -> bool:
        return bool(self.providers)

    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        max_tokens: int = 512,
        temperature: float = 0.0,
    ) -> LLMResponse:
        if not self.providers:
            raise LLMUnavailableError(
                "No LLM provider configured. Set GROQ_API_KEY, GEMINI_API_KEY or "
                "CEREBRAS_API_KEY in .env — all three have free tiers."
            )

        import litellm

        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        last_error: Exception | None = None

        for name in self.providers:
            spec = MODELS[name]
            api_key = getattr(settings, spec.api_key_setting, None)
            started = time.perf_counter()
            try:
                response = litellm.completion(
                    model=spec.litellm_id,
                    messages=messages,
                    api_key=api_key,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    timeout=self.timeout,
                )
            except Exception as exc:  # noqa: BLE001 — try the next provider
                last_error = exc
                log.warning(
                    "llm provider failed; falling through",
                    extra={"provider": name, "error": str(exc)[:200]},
                )
                continue

            elapsed = (time.perf_counter() - started) * 1000
            usage_obj = getattr(response, "usage", None)
            input_tokens = int(getattr(usage_obj, "prompt_tokens", 0) or 0)
            output_tokens = int(getattr(usage_obj, "completion_tokens", 0) or 0)

            return LLMResponse(
                text=(response.choices[0].message.content or "").strip(),
                usage=LLMUsage(
                    provider=name,
                    model=spec.litellm_id,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cost_usd=estimate_cost(spec, input_tokens, output_tokens),
                    latency_ms=elapsed,
                ),
            )

        raise LLMUnavailableError(
            f"All {len(self.providers)} provider(s) failed. Last error: {last_error}"
        )


def describe_llm_status() -> dict[str, Any]:
    """What the router can actually reach — surfaced in /health and the UI."""
    providers = available_providers()
    return {
        "available": bool(providers),
        "providers": providers,
        "priority": list(PROVIDER_PRIORITY),
        "configured": {
            name: bool(getattr(settings, spec.api_key_setting, None))
            for name, spec in MODELS.items()
        },
        "note": (
            "No provider configured — the router falls back to a deterministic "
            "heuristic classifier, which is NOT the few-shot LLM the spec "
            "describes. Routing accuracy from a heuristic run is not comparable."
            if not providers
            else f"Using {providers[0]} first, falling through to {providers[1:]}."
        ),
    }
