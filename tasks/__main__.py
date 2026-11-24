"""Generate tasks.jsonl from scenes.

    python -m tasks --data data/ --seed 0                 # writes data/tasks.jsonl + data/validation_report.json
    python -m tasks --data fixtures/ --out /tmp/t.jsonl   # fixtures/tasks.jsonl is hand-written, so pass --out

Pipeline: generate -> validity rules (validate/rules.py) -> balance + per-family cap (validate/balance.py)
-> schema check -> independent re-derivation (validate/independent.py)
-> write tasks.jsonl and validation_report.json (next to the tasks file).
Same --data and --seed always give byte-identical output (AGENTS.md rule 3).
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path
from random import Random

from schema.check import load_validator, schema_errors

from validate.balance import MAX_PER_FAMILY, balance
from validate.independent import run as run_independent
from validate.report import build_report, write_report
from validate.rules import check as check_rules

from .io import load_scenes, write_jsonl
from .templates import FAMILIES

FIXTURES_DIR = (Path(__file__).resolve().parents[1] / "fixtures").resolve()


def generate(data_dir, seed: int = 0, families=None):
    """Return (tasks, rejects). Deterministic: each scene gets its own RNG seeded from (seed, scene_id)."""
    families = families or list(FAMILIES)
    tasks, rejects = [], Counter()
    counters = Counter()
    scenes = load_scenes(data_dir)
    ctx = {"scenes": {s["scene_id"]: s for s in scenes}}
    for scene in scenes:
        rng = Random(f"{seed}:{scene['scene_id']}")
        pair = scene.get("pair")
        is_after = bool(pair) and scene["scene_id"] == pair["after"]
        for fam in families:
            if is_after and fam != "F10":  # the after-image of a pair would only repeat the before-image tasks
                continue
            for t in FAMILIES[fam](scene, rng, rejects, ctx):
                num = scene["scene_id"][2:8]
                counters[(num, fam)] += 1
                t["task_id"] = f"t_{num}_{fam}_{counters[(num, fam)]:02d}"
                tasks.append(t)
    return tasks, rejects


def main(argv=None):
    ap = argparse.ArgumentParser(description="Generate tasks.jsonl from <data>/scenes/*.json")
    ap.add_argument("--data", type=Path, required=True, help="data folder, e.g. data/ or fixtures/")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", type=Path, help="output file (default: <data>/tasks.jsonl)")
    ap.add_argument("--families", default=",".join(FAMILIES),
                    help=f"comma-separated subset of {','.join(FAMILIES)}")
    ap.add_argument("--max-per-family", type=int, default=MAX_PER_FAMILY)
    ap.add_argument("--no-balance", action="store_true", help="skip balancing and the family cap (debugging)")
    ap.add_argument("--report", type=Path, help="report file (default: validation_report.json next to --out)")
    args = ap.parse_args(argv)

    out = args.out or args.data / "tasks.jsonl"
    if args.out is None and args.data.resolve() == FIXTURES_DIR:
        ap.error("fixtures/tasks.jsonl is hand-written; pass --out to write generated tasks elsewhere")
    families = [f.strip() for f in args.families.split(",") if f.strip()]
    unknown = [f for f in families if f not in FAMILIES]
    if unknown:
        ap.error(f"unknown or not-yet-implemented families: {unknown}")

    tasks, rejects = generate(args.data, args.seed, families)
    scenes = {s["scene_id"]: s for s in load_scenes(args.data)}

    rule_rejects, valid = Counter(), []
    for t in tasks:
        reasons = check_rules(t, scenes)
        rule_rejects.update(reasons)
        if not reasons:
            valid.append(t)
    if args.no_balance:
        final, drops = valid, Counter()
    else:
        final, drops = balance(valid, args.seed, args.max_per_family)

    validator = load_validator("task")
    errors = [e for t in final for e in schema_errors(validator, t, t["task_id"])]
    if errors:
        print("\n".join(errors), file=sys.stderr)
        print(f"FAIL: {len(errors)} schema errors, nothing written", file=sys.stderr)
        return 1

    write_jsonl(out, final)
    independent = run_independent(final, scenes, args.data)
    report = build_report(seed=args.seed, data=args.data, n_scenes=len(scenes), n_generated=len(tasks),
                          n_invalid=len(tasks) - len(valid), rule_rejects=rule_rejects, balance_drops=drops,
                          gen_rejects=rejects, final=final, independent=independent)
    report_path = args.report or out.parent / "validation_report.json"
    write_report(report_path, report)

    per_family = Counter(t["family"] for t in final)
    print(f"{len(final)} tasks -> {out} (seed {args.seed}); report -> {report_path}")
    print(f"  generated {len(tasks)}, failed rules {len(tasks) - len(valid)}, dropped by balancing {sum(drops.values())}")
    print("  by family: " + ", ".join(f"{f}={per_family[f]}" for f in families))
    if rule_rejects:
        print("  rule failures: " + "; ".join(f"{k} x{v}" for k, v in sorted(rule_rejects.items())))
    print(f"  independent check: {independent['agreement_pct']}% agree on {independent['decided_tasks']} checkable "
          f"tasks; {len(independent['disagreements'])} disagreements; "
          f"{independent['scene_fields']['problems']} scene-field problems")
    not_balanced = [f for f, v in report["majority_baseline"].items() if v["near_chance"] is False]
    if not_balanced:
        print(f"  majority baseline above chance for: {', '.join(not_balanced)} (see report)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
