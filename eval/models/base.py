"""Adapter interface every model implements (eval/PLAN.md §2)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class Reply:
    text: str
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: int = 0


class Adapter:
    """`parts` is the user turn in order: ("text", str) or ("image", Path). Same for every model."""

    name = "base"
    model_id = "base"  # written to results rows and used as the results file name

    def ask(self, system: str, parts: list[tuple[str, str | Path]], *, task: dict, condition: str,
            run: int) -> Reply:
        raise NotImplementedError
