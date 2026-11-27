"""Logic behind the Streamlit demo (report/demo.py), kept free of Streamlit so it can be tested.

Two ways in:
- browse: a scene and task from an existing data folder, with every model's stored answer from results/.
- live: render a new scene from a seed with gen/, make its tasks with tasks/, and ask models now via eval/.
  Live mode needs the gen, tasks and eval packages (each on its own branch until merged) and, for real
  models, API keys in .env. The mock model needs no key.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from report.analyze import read_jsonl
from validate.checkers import load_scene, score

LIVE_MODELS = ("mock", "er2", "gemini-pro", "claude", "gpt")
KEY_FOR = {"er2": "GEMINI_API_KEY", "gemini-pro": "GEMINI_API_KEY", "claude": "ANTHROPIC_API_KEY",
           "gpt": "OPENAI_API_KEY"}


# ---------------------------------------------------------------- browse

def scene_ids(tasks: list[dict]) -> list[str]:
    return sorted({t["scene_id"] for t in tasks})


def tasks_for(tasks: list[dict], scene_id: str) -> list[dict]:
    return [t for t in tasks if t["scene_id"] == scene_id]


def load_results(results_dir: Path) -> dict[str, list[dict]]:
    """task_id -> result rows (all models, conditions, runs)."""
    out: dict[str, list[dict]] = {}
    if results_dir and Path(results_dir).is_dir():
        for path in sorted(Path(results_dir).glob("*.jsonl")):
            for r in read_jsonl(path):
                out.setdefault(r["task_id"], []).append(r)
    return out


def answers(rows: list[dict], condition: str = "image") -> list[dict]:
    """One row per model for this condition: its first run."""
    best: dict[str, dict] = {}
    for r in sorted(rows, key=lambda r: r["run"]):
        if r["condition"] == condition:
            best.setdefault(r["model"], r)
    return [best[m] for m in sorted(best)]


def verdict(row: dict) -> str:
    s = row.get("score") or 0.0
    return "✓" if s >= 1.0 - 1e-9 else ("◐" if s > 0 else "✗")


# ---------------------------------------------------------------- live

def missing_packages() -> list[str]:
    """Packages live mode needs that this checkout doesn't have yet."""
    root = Path(__file__).resolve().parents[1]
    return [p for p in ("gen", "tasks", "eval") if not (root / p / "__main__.py").exists()]


def _run(*args: str) -> None:
    done = subprocess.run([sys.executable, "-m", *args], capture_output=True, text=True)
    if done.returncode != 0:
        raise RuntimeError(f"python -m {' '.join(args)} failed:\n{done.stderr[-2000:]}")


def render(seed: int, live_dir: Path, counterfactuals: bool = True) -> str:
    """Generate scene `seed` into live_dir (once) and fill its F8 labels. Returns the scene id."""
    scene_id = f"s_{seed:06d}"
    if not (live_dir / "scenes" / f"{scene_id}.json").exists():
        _run("gen", "--seed", str(seed), "--n", "1", "--out", str(live_dir), "--workers", "1")
        if counterfactuals:
            _run("gen.counterfactuals", "--data", str(live_dir), "--only", scene_id, "--workers", "1")
    return scene_id


def live_tasks(live_dir: Path, scene_id: str, seed: int = 0) -> list[dict]:
    """The scene's tasks, made by B's generators and kept only if they pass B's validity rules."""
    from tasks.__main__ import generate
    from validate.rules import check

    tasks, _ = generate(live_dir, seed)
    scenes = {scene_id: load_scene(live_dir, scene_id)}
    return [t for t in tasks if t["scene_id"] == scene_id and not check(t, scenes)]


def has_key(alias: str) -> bool:
    _load_env()
    return alias == "mock" or bool(os.environ.get(KEY_FOR.get(alias, ""), ""))


def _load_env() -> None:
    try:
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    except ImportError:
        pass


def ask(alias: str, task: dict, data: Path, condition: str = "image") -> dict:
    """Ask one model one task now; returns a results-style row (parsed, scored with B's checker)."""
    from eval import models, prompt
    from eval.parse import parse

    _load_env()
    model = models.get(alias)
    system, parts = prompt.build(task, data, condition)
    reply = model.ask(system, parts, task=task, condition=condition, run=0)
    scene = load_scene(data, task["scene_id"])
    parsed = parse(reply.text, task["answer_type"], scene["width"], scene["height"])
    return {"task_id": task["task_id"], "model": model.model_id, "run": 0, "condition": condition,
            "raw_response": reply.text, "parsed": parsed, "parse_ok": parsed is not None,
            "score": score(task, parsed, scene) if parsed is not None else 0.0,
            "latency_ms": reply.latency_ms, "tokens_in": reply.tokens_in, "tokens_out": reply.tokens_out}


def dump(row: dict) -> str:
    return json.dumps({k: row[k] for k in ("raw_response", "parsed", "score") if k in row}, indent=1)
