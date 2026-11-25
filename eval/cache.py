"""Append-only response cache = resume (eval/PLAN.md §3). One line per finished API call:
{"key": [task_id, condition, run], "prompt_hash", "raw_response", "tokens_in", "tokens_out", "latency_ms"}.
Only successful calls are cached, so failed ones are retried on the next run."""

from __future__ import annotations

import json
import threading
from pathlib import Path


class Cache:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.entries: dict[tuple, dict] = {}
        self._lock = threading.Lock()
        if self.path.is_file():
            for line in self.path.read_text().splitlines():
                if line.strip():
                    e = json.loads(line)
                    self.entries[tuple(e["key"])] = e  # later lines win (a re-asked prompt)

    def get(self, key: tuple, prompt_hash: str):
        e = self.entries.get(key)
        return e if e and e["prompt_hash"] == prompt_hash else None

    def put(self, entry: dict) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a") as f:
                f.write(json.dumps(entry) + "\n")  # written and closed per call: a crash loses nothing
            self.entries[tuple(entry["key"])] = entry
