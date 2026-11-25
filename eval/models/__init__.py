"""Model registry: --model <name> -> adapter. Real adapters arrive in Phase 2."""

from .mock import Mock

REGISTRY = {"mock": Mock}


def get(name: str, **kw):
    if name not in REGISTRY:
        raise SystemExit(f"unknown model {name!r}; known: {', '.join(sorted(REGISTRY))}")
    return REGISTRY[name](**kw)
