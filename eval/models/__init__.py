"""Model registry: --model <alias> -> adapter (eval/PLAN.md D1). --model-id overrides the default id.

Default ids other than ER 2 come from third-party listings (2026-11-26): VERIFY them against each
provider's model list before the paid run. A wrong id fails loudly on the first call (404, not retried).
"""

from .mock import Mock

DEFAULT_IDS = {
    "mock": "mock",
    "er2": "gemini-robotics-er-2-preview",      # primary; confirmed on ai.google.dev
    "gemini-pro": "gemini-3.1-pro-preview",     # VERIFY
    "claude": "claude-opus-5-5",
    "gpt": "gpt-6.1-sol",                       # VERIFY
}
PROVIDER = {"er2": "gemini", "gemini-pro": "gemini", "claude": "anthropic", "gpt": "openai"}
REGISTRY = {"mock": Mock}  # extra adapters (tests) can be registered by name


def get(alias: str, model_id: str | None = None, seed: int = 0, thinking_budget: int | None = None):
    if alias in REGISTRY:
        return REGISTRY[alias](seed=seed) if alias == "mock" else REGISTRY[alias]()
    if alias not in PROVIDER:
        raise SystemExit(f"unknown model {alias!r}; known: {', '.join(sorted(set(DEFAULT_IDS) | set(REGISTRY)))}")
    mid = model_id or DEFAULT_IDS[alias]
    provider = PROVIDER[alias]
    if provider == "gemini":
        from .gemini_adapter import Gemini
        return Gemini(mid, thinking_budget=thinking_budget)
    if provider == "anthropic":
        from .anthropic_adapter import Anthropic
        return Anthropic(mid)
    from .openai_adapter import OpenAI
    return OpenAI(mid)
