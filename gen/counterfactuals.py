"""F8 counterfactuals: "if X is removed, which objects move?"

For every object that supports something (it is a `bottom` in contacts), rebuild the
scene from its seed, delete that object, settle again and record which objects moved
more than 2 cm. Labels come from re-simulating, never from heuristics (AGENTS.md).

    python -m gen.counterfactuals --data data/
"""
from __future__ import annotations

import argparse
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from gen import _rebuild as rb


def supporters(scene: dict) -> list[str]:
    return sorted({c["bottom"] for c in scene["contacts"] if c["bottom"] != "table"}, key=rb.id_key)


def counterfactual(seed: int, ranges: dict, removed: str, api=None) -> list[str] | None:
    """Ids that move when `removed` is deleted, or None if the answer is still open.

    If the rerun hits the settle cap (e.g. a sphere still rolling on the floor), the label is
    kept only when everything still moving has already moved > 2 cm, so more steps can't change it.
    """
    api = rb.scene_api(api)
    sim = api.build_scene(seed, ranges)
    try:
        before = rb.positions(sim)
        rb.remove_object(sim, removed)
        settled = api.settle(sim)
        moved = rb.displaced(before, rb.positions(sim))
        if not settled and rb.moving(sim) - set(moved):
            return None
        return moved
    finally:
        rb.close(sim)


def compute(scene: dict, ranges: dict, api=None) -> tuple[dict, list[str]]:
    """(counterfactuals dict for scene.json, supporters skipped because the rerun never settled)."""
    api = rb.scene_api(api)
    sim = api.build_scene(scene["seed"], ranges)
    try:
        rb.check_rebuild(sim, scene)
    finally:
        rb.close(sim)

    out, unsettled = {}, []
    for obj_id in supporters(scene):
        moved = counterfactual(scene["seed"], ranges, obj_id, api)
        if moved is None:
            unsettled.append(obj_id)   # an open answer gets no label; F8 just skips it
        else:
            out[obj_id] = {"moved": moved}
    return out, unsettled


def _compute_job(job):
    scene, ranges = job
    try:
        return compute(scene, ranges), None
    except rb.RebuildMismatch as e:
        return None, str(e)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data", type=Path, required=True)
    ap.add_argument("--only", nargs="*", help="scene ids to process (default: all base scenes)")
    ap.add_argument("--workers", type=int, default=os.cpu_count(),
                    help="parallel worker processes (default: CPU count); output does not depend on it")
    args = ap.parse_args(argv)

    ranges = rb.load_ranges(args.data)
    todo = [(path, scene) for path, scene in rb.base_scenes(args.data)
            if not args.only or scene["scene_id"] in args.only]
    jobs = [(scene, ranges) for _, scene in todo]
    if args.workers > 1:
        with ProcessPoolExecutor(args.workers) as pool:
            results = list(pool.map(_compute_job, jobs))
    else:
        results = [_compute_job(j) for j in jobs]

    n_done = n_failed = 0
    for (path, scene), (result, error) in zip(todo, results):
        if error:
            print(f"SKIP {error}", file=sys.stderr)
            n_failed += 1
            continue
        scene["counterfactuals"], unsettled = result
        if unsettled:
            print(f"{scene['scene_id']}: rerun did not settle after removing {unsettled}", file=sys.stderr)
        rb.write_scene(path, scene)
        n_done += 1
    print(f"counterfactuals: {n_done} scenes written, {n_failed} rebuild mismatches")
    return 1 if n_failed else 0


if __name__ == "__main__":
    sys.exit(main())
