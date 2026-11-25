"""One prompt for every model (eval/PLAN.md §3). Change PROMPT_VERSION whenever wording changes."""

from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path

PROMPT_VERSION = "v1"
SYSTEM = "You are looking at a photo of a tabletop. Answer with JSON only."
NO_IMAGE = "(The image is not available. Give your best guess in the required format.)"


def build(task: dict, data_dir, condition: str) -> tuple[str, list]:
    """-> (system, parts). text_only drops the images and says so, so models guess instead of refusing."""
    parts = []
    if condition == "image":
        imgs = [Path(data_dir) / rel for rel in task["images"]]
        if len(imgs) == 1:
            parts.append(("image", imgs[0]))
        else:  # F10: before / after
            for i, p in enumerate(imgs, 1):
                parts += [("text", f"Image {i} ({'before' if i == 1 else 'after'}):"), ("image", p)]
    elif condition == "text_only":
        parts.append(("text", NO_IMAGE))
    else:
        raise ValueError(f"unknown condition {condition!r}")
    parts.append(("text", f"{task['question']}\n{task['answer_format']}"))
    return SYSTEM, parts


@lru_cache(maxsize=4096)
def _file_hash(path: str) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def prompt_hash(system: str, parts: list) -> str:
    """Changes when wording, version or any image byte changes, so stale cache entries are never reused."""
    h = hashlib.sha256(f"{PROMPT_VERSION}\0{system}".encode())
    for kind, val in parts:
        h.update(f"\0{kind}\0".encode())
        h.update((_file_hash(str(val)) if kind == "image" else val).encode())
    return h.hexdigest()[:16]
