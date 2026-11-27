"""Accuracy breakdowns from results.jsonl files: model x family, model x difficulty knob,
image vs text-only gap, run-to-run spread, cost and latency. Bootstrap 95% CIs over tasks.

    python -m report --results data/results/ [--tasks data/tasks.jsonl] [--out report/out]

Headline accuracy counts a task as right only when score == 1.0, so F8's partial-credit
set-F1 counts only when exact (Decisions table, 2026-11-24). `mean_score` keeps the partial credit.
With several runs, each task's runs are averaged first and the bootstrap resamples tasks.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

from report import charts

N_BOOT = 2000
CONDITIONS = ("image", "text_only")
# Model colour slots follow the entity, never its rank, so known models keep a fixed order.
MODEL_ORDER = ("gemini-robotics-er-2-preview", "gemini-3.1-pro-preview", "claude-opus-5-5", "gpt-6.1-sol", "mock")
# Bins for the numeric difficulty knobs; any other knob is sliced by its raw value.
BINS = {
    "n_objects": [(3, 7, "3–6"), (7, 11, "7–10"), (11, 16, "11–15"), (16, 21, "16–20")],
    "occlusion": [(0.0, 0.2, "0–0.2"), (0.2, 0.4, "0.2–0.4"), (0.4, 0.6, "0.4–0.6"), (0.6, 1.01, "0.6–1")],
    "distractors": [(0, 1, "0"), (1, 2, "1"), (2, 3, "2"), (3, 99, "3+")],
}
FAMILY_RE = re.compile(r"_(F\d+)_")


# ---------------------------------------------------------------- loading

def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def load_results(results_dir: Path) -> list[dict]:
    rows = []
    for path in sorted(results_dir.glob("*.jsonl")):
        rows += read_jsonl(path)
    return rows


def find_tasks(results_dir: Path, tasks: Path | None) -> Path | None:
    """--tasks, else the tasks file eval recorded in <model>.meta.json, else <results>/../tasks.jsonl."""
    if tasks:
        return tasks
    for meta in sorted(results_dir.glob("*.meta.json")):
        recorded = json.loads(meta.read_text()).get("tasks_file")
        if recorded and Path(recorded).exists():
            return Path(recorded)
    guess = results_dir.parent / "tasks.jsonl"
    return guess if guess.exists() else None


def join(rows: list[dict], tasks: list[dict]) -> list[dict]:
    """Attach family, answer_type and difficulty from the task to every result row."""
    by_id = {t["task_id"]: t for t in tasks}
    out = []
    for r in rows:
        t = by_id.get(r["task_id"], {})
        m = FAMILY_RE.search(r["task_id"])
        out.append({**r, "family": t.get("family") or (m.group(1) if m else "?"),
                    "answer_type": t.get("answer_type"), "difficulty": t.get("difficulty") or {}})
    return out


# ---------------------------------------------------------------- stats

def correct(row: dict) -> float:
    return 1.0 if row["score"] is not None and row["score"] >= 1.0 - 1e-9 else 0.0


def per_task(rows: list[dict], value=correct) -> dict[str, float]:
    """task_id -> value averaged over runs."""
    acc = defaultdict(list)
    for r in rows:
        acc[r["task_id"]].append(value(r))
    return {k: float(np.mean(v)) for k, v in acc.items()}


def bootstrap_ci(values, rng: np.random.Generator, n_boot: int = N_BOOT) -> tuple[float, float]:
    v = np.asarray(values, dtype=float)
    if len(v) < 2:
        return (float(v.mean()), float(v.mean())) if len(v) else (float("nan"), float("nan"))
    means = v[rng.integers(0, len(v), size=(n_boot, len(v)))].mean(axis=1)
    return float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


def summarise(rows: list[dict], rng, n_boot: int) -> dict:
    tasks = per_task(rows)
    lo, hi = bootstrap_ci(list(tasks.values()), rng, n_boot)
    return {"n_tasks": len(tasks), "n_rows": len(rows),
            "accuracy": float(np.mean(list(tasks.values()))) if tasks else float("nan"),
            "ci_low": lo, "ci_high": hi,
            "mean_score": float(np.mean([r["score"] or 0.0 for r in rows])) if rows else float("nan"),
            "parse_fail_rate": float(np.mean([not r["parse_ok"] for r in rows])) if rows else float("nan")}


def knob_bin(knob: str, value) -> tuple[int, str] | None:
    """(sort key, label) for a difficulty value, or None if it is missing."""
    if value is None:
        return None
    for i, (lo, hi, label) in enumerate(BINS.get(knob, [])):
        if lo <= value < hi:
            return i, label
    if knob in BINS:
        return len(BINS[knob]), str(value)
    return 0, str(value)


def family_key(f: str):
    return (0, int(f[1:])) if re.fullmatch(r"F\d+", f) else (1, f)


def model_key(m: str):
    return (MODEL_ORDER.index(m), m) if m in MODEL_ORDER else (len(MODEL_ORDER), m)


def group(rows, *keys):
    out = defaultdict(list)
    for r in rows:
        out[tuple(k(r) if callable(k) else r[k] for k in keys)].append(r)
    return out


def analyse(rows: list[dict], seed: int = 0, n_boot: int = N_BOOT) -> dict:
    rng = np.random.default_rng(seed)
    models = sorted({r["model"] for r in rows}, key=model_key)
    conditions = [c for c in CONDITIONS if any(r["condition"] == c for r in rows)]
    conditions += sorted({r["condition"] for r in rows} - set(conditions))
    families = sorted({r["family"] for r in rows}, key=family_key)

    overall = [{"model": m, "condition": c, **summarise(g, rng, n_boot)}
               for (m, c), g in sorted(group(rows, "model", "condition").items(),
                                       key=lambda kv: (model_key(kv[0][0]), conditions.index(kv[0][1])))]

    by_family = [{"model": m, "condition": c, "family": f, **summarise(g, rng, n_boot)}
                 for (m, c, f), g in sorted(group(rows, "model", "condition", "family").items(),
                                            key=lambda kv: (model_key(kv[0][0]), conditions.index(kv[0][1]),
                                                            family_key(kv[0][2])))]

    knobs = sorted({k for r in rows for k in r["difficulty"]})
    by_knob = []
    for knob in knobs:
        binned = [(r, knob_bin(knob, r["difficulty"].get(knob))) for r in rows]
        groups = defaultdict(list)
        for r, b in binned:
            if b is not None:
                groups[(r["model"], r["condition"], b)].append(r)
        for (m, c, (order, label)), g in sorted(groups.items(),
                                                key=lambda kv: (model_key(kv[0][0]), conditions.index(kv[0][1]),
                                                                kv[0][2][0], kv[0][2][1])):
            by_knob.append({"model": m, "condition": c, "knob": knob, "bin": label, "bin_order": order,
                            **summarise(g, rng, n_boot)})

    gap = []   # image minus text_only, over tasks answered in both conditions
    for (m, f), g in sorted(group(rows, "model", "family").items(), key=lambda kv: (model_key(kv[0][0]), family_key(kv[0][1]))):
        img = per_task([r for r in g if r["condition"] == "image"])
        txt = per_task([r for r in g if r["condition"] == "text_only"])
        both = sorted(img.keys() & txt.keys())
        if both:
            diffs = [img[t] - txt[t] for t in both]
            lo, hi = bootstrap_ci(diffs, rng, n_boot)
            gap.append({"model": m, "family": f, "n_tasks": len(both), "image": float(np.mean([img[t] for t in both])),
                        "text_only": float(np.mean([txt[t] for t in both])), "gap": float(np.mean(diffs)),
                        "ci_low": lo, "ci_high": hi})

    runs = []   # spread of per-run accuracy, on tasks every run answered
    for (m, c), g in group(rows, "model", "condition").items():
        by_run = {k: per_task(v) for k, v in group(g, "run").items()}
        if len(by_run) < 2:
            continue
        shared = set.intersection(*(set(v) for v in by_run.values()))
        accs = [float(np.mean([v[t] for t in shared])) for v in by_run.values()] if shared else []
        if accs:
            runs.append({"model": m, "condition": c, "n_runs": len(accs), "n_tasks": len(shared),
                         "mean": float(np.mean(accs)), "std": float(np.std(accs, ddof=1)),
                         "min": min(accs), "max": max(accs)})
    runs.sort(key=lambda r: (model_key(r["model"]), r["condition"]))

    cost = []
    for m, g in sorted(group(rows, "model").items(), key=lambda kv: model_key(kv[0][0])):
        lat = [r["latency_ms"] for r in g if r.get("latency_ms")]
        cost.append({"model": m[0], "calls": len(g), "errors": sum(r.get("error") is not None for r in g),
                     "cost_usd": float(sum(r.get("cost_usd") or 0.0 for r in g)),
                     "tokens_in": int(sum(r.get("tokens_in") or 0 for r in g)),
                     "tokens_out": int(sum(r.get("tokens_out") or 0 for r in g)),
                     "latency_ms_median": float(np.median(lat)) if lat else None})

    return {"models": models, "conditions": conditions, "families": families, "knobs": knobs,
            "seed": seed, "n_boot": n_boot, "overall": overall, "by_family": by_family,
            "by_knob": by_knob, "gap": gap, "runs": runs, "cost": cost}


# ---------------------------------------------------------------- output

def _pct(x) -> str:
    return "–" if x is None or x != x else f"{100 * x:.1f}%"


def _ms(x) -> str:
    return "–" if x is None else f"{x:.0f} ms"


def _ci(r) -> str:
    return f"{_pct(r['accuracy'])} [{_pct(r['ci_low'])}, {_pct(r['ci_high'])}]"


def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        path.write_text("")
        return
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def markdown(s: dict, inputs: dict) -> str:
    lines = ["# RoboSpatial-Synth results", "",
             f"{inputs['n_rows']} result rows from {', '.join(inputs['results_files'])}; "
             f"tasks from `{inputs['tasks_file'] or 'task ids only (no difficulty)'}`. "
             f"Headline accuracy = score 1.0; 95% bootstrap CI over tasks ({s['n_boot']} resamples, seed {s['seed']}).", "",
             "## Overall", "", "| Model | Condition | Tasks | Accuracy [95% CI] | Mean score | Parse failures |",
             "|---|---|---|---|---|---|"]
    lines += [f"| {r['model']} | {r['condition']} | {r['n_tasks']} | {_ci(r)} | {_pct(r['mean_score'])} | "
              f"{_pct(r['parse_fail_rate'])} |" for r in s["overall"]]
    for cond in s["conditions"]:
        rows = {(r["model"], r["family"]): r for r in s["by_family"] if r["condition"] == cond}
        if not rows:
            continue
        lines += ["", f"## By family ({cond})", "", "| Family | " + " | ".join(s["models"]) + " |",
                  "|---" * (len(s["models"]) + 1) + "|"]
        for f in (f for f in s["families"] if any(k[1] == f for k in rows)):
            cells = [f"{_ci(rows[(m, f)])} (n={rows[(m, f)]['n_tasks']})" if (m, f) in rows else "–" for m in s["models"]]
            lines.append(f"| {f} | " + " | ".join(cells) + " |")
    if s["gap"]:
        lines += ["", "## Image vs text-only", "", "Positive gap = the image helps. Tasks answered in both conditions only.", "",
                  "| Model | Family | Tasks | Image | Text-only | Gap [95% CI] |", "|---|---|---|---|---|---|"]
        lines += [f"| {g['model']} | {g['family']} | {g['n_tasks']} | {_pct(g['image'])} | {_pct(g['text_only'])} | "
                  f"{100 * g['gap']:+.1f} pts [{100 * g['ci_low']:+.1f}, {100 * g['ci_high']:+.1f}] |" for g in s["gap"]]
    for knob in s["knobs"]:
        rows = [r for r in s["by_knob"] if r["knob"] == knob and r["condition"] == "image"]
        if not rows:
            continue
        bins = sorted({(r["bin_order"], r["bin"]) for r in rows})
        cell = {(r["model"], r["bin"]): r for r in rows}
        lines += ["", f"## By {knob} (image)", "", f"| {knob} | " + " | ".join(s["models"]) + " |",
                  "|---" * (len(s["models"]) + 1) + "|"]
        for _, b in bins:
            cells = [f"{_pct(cell[(m, b)]['accuracy'])} (n={cell[(m, b)]['n_tasks']})" if (m, b) in cell else "–"
                     for m in s["models"]]
            lines.append(f"| {b} | " + " | ".join(cells) + " |")
    if s["runs"]:
        lines += ["", "## Run-to-run spread", "", "| Model | Condition | Runs | Tasks | Mean | Std | Min–max |",
                  "|---|---|---|---|---|---|---|"]
        lines += [f"| {r['model']} | {r['condition']} | {r['n_runs']} | {r['n_tasks']} | {_pct(r['mean'])} | "
                  f"{100 * r['std']:.1f} pts | {_pct(r['min'])}–{_pct(r['max'])} |" for r in s["runs"]]
    lines += ["", "## Cost and latency", "", "| Model | Calls | Errors | Cost (USD) | Tokens in | Tokens out | Median latency |",
              "|---|---|---|---|---|---|---|"]
    lines += [f"| {c['model']} | {c['calls']} | {c['errors']} | ${c['cost_usd']:.2f} | {c['tokens_in']:,} | "
              f"{c['tokens_out']:,} | {_ms(c['latency_ms_median'])} |" for c in s["cost"]]
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--results", type=Path, required=True, help="folder of <model>.jsonl files")
    ap.add_argument("--tasks", type=Path, help="tasks.jsonl (default: the one in <model>.meta.json)")
    ap.add_argument("--out", type=Path, default=Path("report/out"))
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--n-boot", type=int, default=N_BOOT)
    args = ap.parse_args(argv)

    raw = load_results(args.results)
    if not raw:
        print(f"no results in {args.results}", file=sys.stderr)
        return 1
    tasks_file = find_tasks(args.results, args.tasks)
    rows = join(raw, read_jsonl(tasks_file) if tasks_file else [])
    s = analyse(rows, args.seed, args.n_boot)
    inputs = {"results_files": [p.name for p in sorted(args.results.glob("*.jsonl"))],
              "tasks_file": str(tasks_file) if tasks_file else None, "n_rows": len(rows)}

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "summary.json").write_text(json.dumps({"inputs": inputs, **s}, indent=2) + "\n")
    for name in ("overall", "by_family", "by_knob", "gap", "runs", "cost"):
        write_csv(args.out / f"{name}.csv", s[name])
    md = markdown(s, inputs)
    (args.out / "summary.md").write_text(md)
    (args.out / "index.html").write_text(charts.page(s, md))
    best = max((r for r in s["overall"] if r["condition"] == "image"), key=lambda r: r["accuracy"], default=None)
    print(f"report: {len(rows)} rows, {len(s['models'])} models -> {args.out}/"
          + (f" (best image accuracy: {best['model']} {_pct(best['accuracy'])})" if best else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
