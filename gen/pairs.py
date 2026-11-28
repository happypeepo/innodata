"""Derived scenes: F10 before/after pairs and F9 goal placements.

F10: rebuild a base scene, move one free object to an empty spot on the table, settle,
     re-render with the same camera. The base scene is the "before"; the new scene
     `<id>b` is the "after". Kept only if exactly that one object moved.
F9:  rebuild a base scene, pick object X and a flat-topped object Y, and place X on Y,
     either centred (aiming for success) or overhanging the edge (aiming for failure).
     Settle, then label success from the final physics state, never from the intent.
     Written as `<id>g` with a `goal` block.

    python -m gen.pairs --data data/ --seed 0 --f10 0.2 --f9 0.2
"""
from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path
from random import Random

import pybullet as p

from gen import _rebuild as rb

MIN_SHIFT_M = 0.08            # an F10 move must be clearly visible
CLEARANCE_M = 0.01
DROP_M = 0.005
MIN_VISIBLE = 0.30
FLAT_TOPS = ("box", "cylinder")


def free_objects(scene: dict) -> list[str]:
    """On the table, nothing on top of them, at least 30% visible."""
    on_table = {c["top"] for c in scene["contacts"] if c["bottom"] == "table"}
    on_other = {c["top"] for c in scene["contacts"] if c["bottom"] != "table"}
    supports = {c["bottom"] for c in scene["contacts"]}
    vis = {o["id"]: o["visible_frac"] for o in scene["objects"]}
    return sorted((o for o in on_table - on_other - supports if vis.get(o, 0) >= MIN_VISIBLE), key=rb.id_key)


def _aabb(sim, body):
    lo, hi = p.getAABB(body, physicsClientId=sim.client)
    return list(lo), list(hi)


def _overlaps(lo, hi, others) -> bool:
    return any(lo[0] < ohi[0] + CLEARANCE_M and hi[0] > olo[0] - CLEARANCE_M and
               lo[1] < ohi[1] + CLEARANCE_M and hi[1] > olo[1] - CLEARANCE_M for olo, ohi in others)


def _teleport(sim, body, xy, z_bottom) -> None:
    """Move a body so its lowest point sits `DROP_M` above z_bottom, keeping its orientation."""
    pos, orn = p.getBasePositionAndOrientation(body, physicsClientId=sim.client)
    lo, _ = _aabb(sim, body)
    z = z_bottom + (pos[2] - lo[2]) + DROP_M
    p.resetBasePositionAndOrientation(body, [xy[0], xy[1], z], orn, physicsClientId=sim.client)
    p.resetBaseVelocity(body, [0, 0, 0], [0, 0, 0], physicsClientId=sim.client)


def _others(sim, *skip):
    return [_aabb(sim, b) for oid, b in rb.bodies(sim).items() if oid not in skip]


# ---------------------------------------------------------------- F10

def move_to_free_spot(sim, obj_id: str, rng: Random, spawn_half: tuple[float, float], tries: int = 40) -> bool:
    """Teleport obj to an empty spot in the spawn area (centred on the origin, like gen.scene)."""
    body = rb.bodies(sim)[obj_id]
    lo, hi = _aabb(sim, body)
    hx, hy = (hi[0] - lo[0]) / 2, (hi[1] - lo[1]) / 2
    old = ((lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2)
    others = _others(sim, obj_id)
    for _ in range(tries):
        x = rng.uniform(-spawn_half[0] + hx, spawn_half[0] - hx)
        y = rng.uniform(-spawn_half[1] + hy, spawn_half[1] - hy)
        if math.dist((x, y), old) < MIN_SHIFT_M:
            continue
        if _overlaps([x - hx, y - hy], [x + hx, y + hy], others):
            continue
        pos = p.getBasePositionAndOrientation(body, physicsClientId=sim.client)[0]
        _teleport(sim, body, (x + pos[0] - old[0], y + pos[1] - old[1]), sim.top_z)
        return True
    return False


def make_f10(scene: dict, ranges: dict, rng: Random, data_dir: Path, api=None, tries: int = 5) -> dict | None:
    """Write the after-scene and return the `pair` block, or None if no clean move was found."""
    api = rb.scene_api(api)
    candidates = free_objects(scene)
    if not candidates:
        return None
    after_id = scene["scene_id"] + "b"
    spawn_half = (api.SPAWN_HALF_X_M, api.SPAWN_HALF_Y_M)
    for _ in range(tries):
        sim = api.build_scene(scene["seed"], ranges)
        try:
            obj_id = rng.choice(candidates)
            before = rb.positions(sim)
            if not move_to_free_spot(sim, obj_id, rng, spawn_half) or not api.settle(sim):
                continue
            after = rb.positions(sim)
            others_moved = [o for o in rb.displaced(before, after) if o != obj_id]
            # It must have landed upright on the table, and nothing else may have shifted.
            if others_moved or abs(after[obj_id][2] - before[obj_id][2]) > 0.01:
                continue
            after_scene = rb.export_variant(sim, after_id, data_dir, api)
        finally:
            rb.close(sim)
        moved_rec = next(o for o in after_scene["objects"] if o["id"] == obj_id)
        if moved_rec["visible_frac"] < MIN_VISIBLE:
            continue
        pair = {"before": scene["scene_id"], "after": after_id, "moved": [obj_id]}
        after_scene["pair"] = pair
        rb.write_scene(Path(data_dir) / "scenes" / f"{after_id}.json", after_scene)
        return pair
    return None


# ---------------------------------------------------------------- F9

def place_on(sim, obj_id: str, target_id: str, aim_success: bool, rng: Random) -> bool:
    """Drop obj onto target: centred, or overhanging the edge so it should fall off."""
    bodies = rb.bodies(sim)
    tlo, thi = _aabb(sim, bodies[target_id])
    olo, ohi = _aabb(sim, bodies[obj_id])
    tc = ((tlo[0] + thi[0]) / 2, (tlo[1] + thi[1]) / 2)
    th = ((thi[0] - tlo[0]) / 2, (thi[1] - tlo[1]) / 2)
    oh = ((ohi[0] - olo[0]) / 2, (ohi[1] - olo[1]) / 2)
    others = _others(sim, obj_id, target_id)
    pos = p.getBasePositionAndOrientation(bodies[obj_id], physicsClientId=sim.client)[0]
    off_c = (pos[0] - (olo[0] + ohi[0]) / 2, pos[1] - (olo[1] + ohi[1]) / 2)  # base origin vs AABB centre
    for _ in range(12):
        if aim_success:
            x, y = tc
        else:
            a = rng.uniform(0, 2 * math.pi)
            # Centre of mass past the target's edge (measured along the drop direction), so it should tip off.
            edge = min(th[0] / max(abs(math.cos(a)), 1e-6), th[1] / max(abs(math.sin(a)), 1e-6))
            r = edge + 0.25 * min(oh)
            x, y = tc[0] + r * math.cos(a), tc[1] + r * math.sin(a)
        if _overlaps([x - oh[0], y - oh[1]], [x + oh[0], y + oh[1]], others):
            continue
        _teleport(sim, bodies[obj_id], (x + off_c[0], y + off_c[1]), thi[2])
        return True
    return False


def rests_on(sim, obj_id: str, target_id: str) -> bool:
    """obj touches target, does not touch the table, and sits above the target's top."""
    bodies, c = rb.bodies(sim), sim.client
    obj, tgt = bodies[obj_id], bodies[target_id]
    if not p.getContactPoints(obj, tgt, physicsClientId=c):
        return False
    if p.getContactPoints(obj, sim.table_id, physicsClientId=c):
        return False
    return _aabb(sim, obj)[0][2] >= _aabb(sim, tgt)[1][2] - 0.01


def make_f9(scene: dict, ranges: dict, rng: Random, data_dir: Path, api=None, tries: int = 6) -> dict | None:
    """Write the goal scene and return its `goal` block, or None if no usable placement was found."""
    api = rb.scene_api(api)
    free = free_objects(scene)
    objs = {o["id"]: o for o in scene["objects"]}
    flat = [i for i in free if objs[i]["shape"] in FLAT_TOPS]   # spheres roll off anything
    # Only place a flat-bottomed object that is narrower than the target's narrowest side.
    candidates = [(o, t) for t in flat for o in flat
                  if o != t and max(objs[o]["size_cm"][:2]) < min(objs[t]["size_cm"][:2])]
    if not candidates:
        return None
    aim = rng.random() < 0.5
    goal_id = scene["scene_id"] + "g"
    for attempt in range(tries):
        obj, target = rng.choice(candidates)
        sim = api.build_scene(scene["seed"], ranges)
        try:
            if not place_on(sim, obj, target, aim, rng) or not api.settle(sim):
                continue
            success = rests_on(sim, obj, target)   # the label is what physics did
            if success != aim and attempt < tries - 1:
                continue
            goal_scene = rb.export_variant(sim, goal_id, data_dir, api)
        finally:
            rb.close(sim)
        vis = {o["id"]: o["visible_frac"] for o in goal_scene["objects"]}
        if min(vis.get(obj, 0), vis.get(target, 0)) < MIN_VISIBLE:
            continue
        goal = {"object": obj, "relation": "on", "target": target, "success": success}
        goal_scene["goal"] = goal
        rb.write_scene(Path(data_dir) / "scenes" / f"{goal_id}.json", goal_scene)
        return goal
    return None


# ---------------------------------------------------------------- CLI

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data", type=Path, required=True)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--f10", type=float, default=0.2, help="fraction of base scenes that get an after-scene")
    ap.add_argument("--f9", type=float, default=0.2, help="fraction of base scenes that get a goal scene")
    args = ap.parse_args(argv)

    ranges = rb.load_ranges(args.data)
    counts = {"f10": 0, "f10_failed": 0, "f9": 0, "f9_success": 0, "f9_failed": 0}
    for path, scene in rb.base_scenes(args.data):
        rng = Random(f"pairs:{args.seed}:{scene['seed']}")
        if rng.random() < args.f10 and scene.get("pair") is None:
            pair = make_f10(scene, ranges, rng, args.data)
            if pair:
                scene["pair"] = pair
                rb.write_scene(path, scene)
                counts["f10"] += 1
            else:
                counts["f10_failed"] += 1
        if rng.random() < args.f9:
            goal = make_f9(scene, ranges, rng, args.data)
            if goal:
                counts["f9"] += 1
                counts["f9_success"] += goal["success"]
            else:
                counts["f9_failed"] += 1
    print("pairs:", counts)
    return 0


if __name__ == "__main__":
    sys.exit(main())
