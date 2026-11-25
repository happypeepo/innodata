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
import random
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import jsonschema
from dotenv import load_dotenv

from validate.checkers import load_scene, score

from . import pricing, prompt
from .cache import Cache
from .models import DEFAULT_IDS, get as get_model
from .parse import parse

CONDITIONS = ("image", "text_only")
RETRY_CODES = {408, 429, 500, 502, 503, 504}
MAX_TRIES = 5


class BudgetExceeded(RuntimeError):
    pass


def retryable(exc) -> bool:
    """Rate limits, server errors, timeouts, dropped connections. Auth / bad request / 404 fail at once."""
    code = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    if code in RETRY_CODES:
        return True
    return isinstance(exc, (TimeoutError, ConnectionError)) or any(
        w in type(exc).__name__ for w in ("Timeout", "Connection", "RateLimit", "ServerError", "Unavailable"))


def ask_with_retry(model, *args, sleep=time.sleep, **kw):
    for attempt in range(1, MAX_TRIES + 1):
        try:
            return model.ask(*args, **kw)
        except Exception as exc:  # noqa: BLE001 - adapters raise SDK-specific types
            if attempt == MAX_TRIES or not retryable(exc):
                raise
            sleep(min(60, 2 ** attempt) + random.random())  # exponential backoff + jitter


RESULT_SCHEMA = json.loads((Path(__file__).resolve().parents[1] / "schema" / "result.schema.json").read_text())


def load_tasks(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _git_sha() -> str | None:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def plan_calls(tasks, data, conditions, runs, cache):
    """Every (task, condition, run) not already cached under the current prompt."""
    todo = []
    for t in tasks:
        for cond in conditions:
            system, parts = prompt.build(t, data, cond)
            h = prompt.prompt_hash(system, parts)
            for run in range(runs):
                key = (t["task_id"], cond, run)
                if not cache.get(key, h):
                    todo.append((t, cond, run, key, system, parts, h))
    return todo


def ask_missing(model, todo, cache, workers, budget_usd=None, sleep=time.sleep):
    """Ask every planned call. -> {key: error} for failed calls (not cached, so retried next run).
    Once this invocation's spend reaches budget_usd, the remaining calls fail with BudgetExceeded."""
    spent, lock, warned = [0.0], threading.Lock(), [False]

    def call(item):
        t, cond, run, key, system, parts, h = item
        if budget_usd is not None and spent[0] >= budget_usd:
            raise BudgetExceeded(f"spent ${spent[0]:.2f} of ${budget_usd:.2f}")
        t0 = time.monotonic()
        reply = ask_with_retry(model, system, parts, task=t, condition=cond, run=run, sleep=sleep)
        with lock:
            spent[0] += pricing.cost(model.model_id, reply.tokens_in, reply.tokens_out)
            if budget_usd and not warned[0] and spent[0] >= 0.7 * budget_usd:
                warned[0] = True
                print(f"WARNING: 70% of --budget-usd spent (${spent[0]:.2f}); post in context/handoff.md",
                      file=sys.stderr)
        cache.put({"key": list(key), "prompt_hash": h, "raw_response": reply.text,
                   "tokens_in": reply.tokens_in, "tokens_out": reply.tokens_out,
                   "latency_ms": reply.latency_ms or int((time.monotonic() - t0) * 1000)})

    errors = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(call, item): item[3] for item in todo}
        for fut in as_completed(futures):
            if (exc := fut.exception()) is not None:
                errors[futures[fut]] = f"{type(exc).__name__}: {exc}"[:300]
    return errors, spent[0]


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
                    "cost_usd": pricing.cost(model_name, e["tokens_in"], e["tokens_out"]) if e else 0.0,
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
    ap.add_argument("--model-id", help="override the alias's default model id (eval/models/__init__.py)")
    ap.add_argument("--thinking-budget", type=int, help="Gemini/ER 2 only; default = API default (PLAN D6)")
    ap.add_argument("--budget-usd", type=float, help="stop asking once this run has spent this much")
    ap.add_argument("--dry-run", action="store_true", help="print calls + estimated cost, call nothing")
    args = ap.parse_args(argv)
    load_dotenv()  # API keys from .env (gitignored); real environment variables win

    out = args.out or args.data / "results"
    conditions = args.condition or ["image"]
    all_tasks = load_tasks(args.tasks)
    tasks = all_tasks
    if args.subset:
        keep = {line.strip() for line in args.subset.read_text().splitlines() if line.strip()}
        tasks = [t for t in tasks if t["task_id"] in keep]
    if args.limit:
        tasks = tasks[: args.limit]

    if args.dry_run:  # no SDK, no key, no client needed
        mid = args.model_id or DEFAULT_IDS.get(args.model, args.model)
        todo = plan_calls(tasks, args.data, conditions, args.runs, Cache(out / ".cache" / f"{mid}.jsonl"))
        est = pricing.estimate(mid, [sum(k == "image" for k, _ in item[5]) for item in todo])
        print(f"{mid}: {len(todo)} calls to make, estimated ${est:.2f} "
              f"(rough: {pricing.EST_TOKENS_PER_IMAGE} tok/image, {pricing.EST_TOKENS_OUT} tok out; prices in eval/pricing.py)")
        return 0

    model = get_model(args.model, model_id=args.model_id, seed=args.seed, thinking_budget=args.thinking_budget)
    name = model.model_id
    pricing.cost(name, 0, 0)  # fail before any call if the model has no price
    cache = Cache(out / ".cache" / f"{name}.jsonl")
    todo = plan_calls(tasks, args.data, conditions, args.runs, cache)
    errors, spent = ask_missing(model, todo, cache, args.workers, args.budget_usd)
    asked = len(todo)
    rows = build_rows(name, all_tasks, args.data, cache, errors)

    out.mkdir(parents=True, exist_ok=True)
    (out / f"{name}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    meta = {
        "model": name, "alias": args.model, "thinking_budget": args.thinking_budget,
        "sampling": "provider defaults (PLAN D12)", "prompt_version": prompt.PROMPT_VERSION, "system_prompt": prompt.SYSTEM,
        "tasks_file": str(args.tasks), "tasks_sha256": hashlib.sha256(args.tasks.read_bytes()).hexdigest(),
        "data": str(args.data), "git_sha": _git_sha(), "seed": args.seed,
        "updated_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "last_invocation": {"conditions": conditions, "runs": args.runs, "tasks": len(tasks),
                            "asked": asked, "errors": len(errors), "spent_usd": round(spent, 4)},
        "rows": len(rows), "parse_failures": sum(not r["parse_ok"] for r in rows),
        "total_cost_usd": round(sum(r["cost_usd"] for r in rows), 4),
    }
    (out / f"{args.model}.meta.json").write_text(json.dumps(meta, indent=2) + "\n")

    scored = [r for r in rows if r["error"] is None]
    acc = sum(r["score"] == 1.0 for r in scored) / len(scored) if scored else 0.0
    print(f"{name}: asked {asked}, {len(errors)} errors, spent ${spent:.2f}; {len(rows)} rows -> "
          f"{out / (name + '.jsonl')} (accuracy {acc:.1%}, parse failures {meta['parse_failures']})")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
