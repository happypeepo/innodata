"""Generate tasks.jsonl from scenes.

    python -m tasks --data data/ --seed 0                 # writes data/tasks.jsonl
    python -m tasks --data fixtures/ --out /tmp/t.jsonl   # fixtures/tasks.jsonl is hand-written, so pass --out

Same --data and --seed always give byte-identical output (AGENTS.md rule 3).
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path
from random import Random

from schema.check import load_validator, schema_errors

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
    args = ap.parse_args(argv)

    out = args.out or args.data / "tasks.jsonl"
    if args.out is None and args.data.resolve() == FIXTURES_DIR:
        ap.error("fixtures/tasks.jsonl is hand-written; pass --out to write generated tasks elsewhere")
    families = [f.strip() for f in args.families.split(",") if f.strip()]
    unknown = [f for f in families if f not in FAMILIES]
    if unknown:
        ap.error(f"unknown or not-yet-implemented families: {unknown}")

    tasks, rejects = generate(args.data, args.seed, families)

    validator = load_validator("task")
    errors = [e for t in tasks for e in schema_errors(validator, t, t["task_id"])]
    if errors:
        print("\n".join(errors), file=sys.stderr)
        print(f"FAIL: {len(errors)} schema errors, nothing written", file=sys.stderr)
        return 1

    write_jsonl(out, tasks)
    per_family = Counter(t["family"] for t in tasks)
    print(f"{len(tasks)} tasks -> {out} (seed {args.seed})")
    print("  by family: " + ", ".join(f"{f}={per_family[f]}" for f in families))
    if rejects:
        print("  rejected: " + "; ".join(f"{k} x{v}" for k, v in sorted(rejects.items())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
