"""Run one model over a tasks file and write results/<model>.jsonl (contract 4) + <model>.meta.json.

    python -m eval --model mock --data fixtures/ --tasks fixtures/tasks.jsonl --out .smoke/results
    python -m eval --model <name> --data data/ --tasks data/tasks.jsonl --condition text_only --runs 3 --subset data/subset_100.txt

Calls go through the cache (<out>/.cache/<model>.jsonl), so a rerun only asks what is missing. The results
file is then rebuilt from every cached answer for this tasks file (all conditions and runs so far), parsed
and scored fresh, so a parser fix needs no new API calls. Every row is checked against
schema/result.schema.json before anything is written.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import jsonschema

from validate.checkers import load_scene, score

from . import prompt
from .cache import Cache
from .models import get as get_model
from .parse import parse

CONDITIONS = ("image", "text_only")
RESULT_SCHEMA = json.loads((Path(__file__).resolve().parents[1] / "schema" / "result.schema.json").read_text())


def load_tasks(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _git_sha() -> str | None:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def ask_missing(model, tasks, data, conditions, runs, cache, workers):
    """Ask the model every (task, condition, run) not already cached. -> {key: error} for failed calls."""
    todo = []
    for t in tasks:
        for cond in conditions:
            system, parts = prompt.build(t, data, cond)
            h = prompt.prompt_hash(system, parts)
            for run in range(runs):
                key = (t["task_id"], cond, run)
                if not cache.get(key, h):
                    todo.append((t, cond, run, key, system, parts, h))

    def call(item):
        t, cond, run, key, system, parts, h = item
        t0 = time.monotonic()
        reply = model.ask(system, parts, task=t, condition=cond, run=run)
        cache.put({"key": list(key), "prompt_hash": h, "raw_response": reply.text,
                   "tokens_in": reply.tokens_in, "tokens_out": reply.tokens_out,
                   "latency_ms": reply.latency_ms or int((time.monotonic() - t0) * 1000)})

    errors = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(call, item): item[3] for item in todo}
        for fut in as_completed(futures):
            if (exc := fut.exception()) is not None:
                errors[futures[fut]] = f"{type(exc).__name__}: {exc}"[:300]
    return len(todo), errors


def build_rows(model_name, tasks, data, cache, errors):
    """Every cached answer for these tasks (current prompts only) + this run's failures, parsed and scored."""
    scenes, rows = {}, []
    for t in tasks:
        for cond in CONDITIONS:
            system, parts = prompt.build(t, data, cond)
            h = prompt.prompt_hash(system, parts)
            runs = sorted(k[2] for k in set(cache.entries) | set(errors) if k[:2] == (t["task_id"], cond))
            for run in runs:
                key = (t["task_id"], cond, run)
                e = cache.get(key, h)
                if e is None and key not in errors:
                    continue  # cached under an older prompt: not a result for this prompt
                scene = scenes.get(t["scene_id"]) or scenes.setdefault(t["scene_id"], load_scene(data, t["scene_id"]))
                parsed = parse(e["raw_response"], t["answer_type"], scene["width"], scene["height"]) if e else None
                rows.append({
                    "task_id": t["task_id"], "model": model_name, "run": run, "condition": cond,
                    "raw_response": e["raw_response"] if e else "",
                    "parsed": parsed, "parse_ok": parsed is not None,
                    "score": score(t, parsed, scene) if parsed is not None else 0.0,
                    "latency_ms": e["latency_ms"] if e else 0,
                    "tokens_in": e["tokens_in"] if e else 0, "tokens_out": e["tokens_out"] if e else 0,
                    "cost_usd": 0.0,  # shortcut: real per-token pricing lands with the paid adapters (Phase 2)
                    "error": None if e else errors[key],
                })
    for r in rows:
        jsonschema.validate(r, RESULT_SCHEMA)  # contract with D: fail loudly, never write a bad file
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description="Role C eval harness")
    ap.add_argument("--model", required=True)
    ap.add_argument("--tasks", type=Path, required=True)
    ap.add_argument("--data", type=Path, required=True, help="folder the task image/scene paths are relative to")
    ap.add_argument("--out", type=Path, help="results folder (default: <data>/results)")
    ap.add_argument("--condition", choices=CONDITIONS, action="append",
                    help="repeatable; default: image")
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--limit", type=int, help="only the first N tasks (pilots)")
    ap.add_argument("--subset", type=Path, help="file with one task_id per line")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--seed", type=int, default=0, help="mock answers")
    args = ap.parse_args(argv)

    out = args.out or args.data / "results"
    conditions = args.condition or ["image"]
    all_tasks = load_tasks(args.tasks)
    tasks = all_tasks
    if args.subset:
        keep = {line.strip() for line in args.subset.read_text().splitlines() if line.strip()}
        tasks = [t for t in tasks if t["task_id"] in keep]
    if args.limit:
        tasks = tasks[: args.limit]

    model = get_model(args.model, **({"seed": args.seed} if args.model == "mock" else {}))
    cache = Cache(out / ".cache" / f"{args.model}.jsonl")
    asked, errors = ask_missing(model, tasks, args.data, conditions, args.runs, cache, args.workers)
    rows = build_rows(args.model, all_tasks, args.data, cache, errors)

    out.mkdir(parents=True, exist_ok=True)
    (out / f"{args.model}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    meta = {
        "model": args.model, "prompt_version": prompt.PROMPT_VERSION, "system_prompt": prompt.SYSTEM,
        "tasks_file": str(args.tasks), "tasks_sha256": hashlib.sha256(args.tasks.read_bytes()).hexdigest(),
        "data": str(args.data), "git_sha": _git_sha(), "seed": args.seed,
        "updated_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "last_invocation": {"conditions": conditions, "runs": args.runs, "tasks": len(tasks),
                            "asked": asked, "errors": len(errors)},
        "rows": len(rows), "parse_failures": sum(not r["parse_ok"] for r in rows),
    }
    (out / f"{args.model}.meta.json").write_text(json.dumps(meta, indent=2) + "\n")

    scored = [r for r in rows if r["error"] is None]
    acc = sum(r["score"] == 1.0 for r in scored) / len(scored) if scored else 0.0
    print(f"{args.model}: asked {asked}, {len(errors)} errors; {len(rows)} rows -> {out / (args.model + '.jsonl')} "
          f"(accuracy {acc:.1%}, parse failures {meta['parse_failures']})")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
