"""Entry point for `python -m gen`: generate N scenes into a data folder.

Scene i (0-based) uses seed = --seed + i. Each knob flag is a range; every scene draws its own
values from it (pass the same number twice to pin a value). Scenes are independent, so they run
in parallel worker processes; the output does not depend on --workers.
"""

import argparse
import multiprocessing
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed

from gen import scene


def main() -> None:
    d = scene.DEFAULT_RANGES
    ap = argparse.ArgumentParser(prog="python -m gen", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seed", type=int, default=0, help="seed of the first scene (default 0)")
    ap.add_argument("--n", type=int, default=1, help="number of scenes (default 1)")
    ap.add_argument("--out", default="data/", help="data folder to write into (default data/)")
    ap.add_argument("--workers", type=int, default=os.cpu_count() or 1,
                    help="parallel worker processes (default: CPU count)")
    ap.add_argument("--n-objects", type=int, nargs=2, metavar=("LO", "HI"), default=d["n_objects"])
    ap.add_argument("--elevation", type=float, nargs=2, metavar=("LO", "HI"), default=d["elevation_deg"],
                    help="camera elevation in degrees")
    ap.add_argument("--azimuth", type=float, nargs=2, metavar=("LO", "HI"), default=d["azimuth_deg"],
                    help="camera azimuth in degrees")
    ap.add_argument("--distractors", type=int, nargs=2, metavar=("LO", "HI"), default=d["distractors"])
    ap.add_argument("--lighting", nargs="+", choices=sorted(scene.LIGHTING), default=d["lighting"])
    ap.add_argument("--scale-range", type=float, nargs=2, metavar=("LO", "HI"), default=d["scale"],
                    help="multiplier on object sizes")
    args = ap.parse_args()

    for name, (lo, hi) in [("--n-objects", args.n_objects), ("--elevation", args.elevation),
                           ("--azimuth", args.azimuth), ("--distractors", args.distractors),
                           ("--scale-range", args.scale_range)]:
        if lo > hi:
            ap.error(f"{name}: LO must be <= HI")
    if not 1 <= args.n_objects[0] <= args.n_objects[1] <= 254:
        ap.error("--n-objects must lie in 1..254 (8-bit seg PNG)")
    if args.distractors[0] < 0:
        ap.error("--distractors must be >= 0")
    if args.workers < 1:
        ap.error("--workers must be >= 1")

    ranges = {
        "n_objects": tuple(args.n_objects),
        "elevation_deg": tuple(args.elevation),
        "azimuth_deg": tuple(args.azimuth),
        "distractors": tuple(args.distractors),
        "lighting": tuple(args.lighting),
        "scale": tuple(args.scale_range),
    }
    start = time.time()
    seeds = [args.seed + i for i in range(args.n)]
    ctx = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(max_workers=min(args.workers, args.n), mp_context=ctx) as pool:
        futures = {pool.submit(scene.generate_scene_timed, seed, args.out, ranges): seed for seed in seeds}
        for done, future in enumerate(as_completed(futures), 1):
            scene_id, n_objects, secs = future.result()
            print(f"[{done}/{args.n}] {scene_id} {n_objects} objects {secs:.1f}s", file=sys.stderr)
    print(f"done: {args.n} scenes in {time.time() - start:.1f}s -> {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
