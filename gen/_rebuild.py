"""Shared helpers for the modules that re-simulate a scene (counterfactuals, pairs).

Everything that depends on gen.scene (Raj's module) lives here, so if its API changes
this is the only file to touch. What we use from it:

    build_scene(seed, ranges) -> Sim      # Sim: seed, client, table_id, top_z, objects[.id, .body]
    settle(sim) -> bool                   # False if it hit the step cap
    write_scene(sim, out) -> dict         # renders and writes files named after sim.scene_id
    load_ranges(data_dir), SPAWN_HALF_X_M, SPAWN_HALF_Y_M
"""
from __future__ import annotations

import importlib
import json
import math
import shutil
import tempfile
from pathlib import Path

import pybullet as p

MOVE_THRESH_M = 0.02      # "moved" = displaced more than 2 cm (AGENTS.md)
STILL_LIN_MPS, STILL_ANG_RADPS = 0.001, 0.01   # the settle rule's stillness thresholds
REBUILD_TOL_M = 0.005     # a rebuilt scene must match scene.json within 5 mm


class RebuildMismatch(RuntimeError):
    """build_scene(seed, ranges) did not give back the scene recorded in scene.json."""


def scene_api(api=None):
    return api if api is not None else importlib.import_module("gen.scene")


def _field(o, *names):
    for n in names:
        if isinstance(o, dict) and n in o:
            return o[n]
        if hasattr(o, n):
            return getattr(o, n)
    raise KeyError(f"Sim object has none of {names}")


def _entries(sim):
    return list(sim.objects.values()) if isinstance(sim.objects, dict) else list(sim.objects)


def bodies(sim) -> dict[str, int]:
    """Object id ("o1") -> PyBullet body id."""
    return {_field(o, "id"): _field(o, "body_id", "body") for o in _entries(sim)}


def remove_object(sim, obj_id: str) -> None:
    """Delete the body and drop it from sim.objects so settle() never touches it."""
    p.removeBody(bodies(sim)[obj_id], physicsClientId=sim.client)
    if isinstance(sim.objects, dict):
        del sim.objects[obj_id]
    else:
        sim.objects[:] = [o for o in sim.objects if _field(o, "id") != obj_id]


def positions(sim) -> dict[str, tuple[float, float, float]]:
    return {oid: p.getBasePositionAndOrientation(b, physicsClientId=sim.client)[0]
            for oid, b in bodies(sim).items()}


def moving(sim) -> set[str]:
    """Ids still moving faster than the settle rule allows."""
    out = set()
    for oid, b in bodies(sim).items():
        lin, ang = p.getBaseVelocity(b, physicsClientId=sim.client)
        if math.hypot(*lin) >= STILL_LIN_MPS or math.hypot(*ang) >= STILL_ANG_RADPS:
            out.add(oid)
    return out


def id_key(obj_id: str) -> int:
    return int(obj_id[1:])


def displaced(before: dict, after: dict, thresh: float = MOVE_THRESH_M) -> list[str]:
    """Ids present in both whose position changed by more than `thresh`, in id order."""
    return sorted((k for k in before.keys() & after.keys() if math.dist(before[k], after[k]) > thresh), key=id_key)


def close(sim) -> None:
    p.disconnect(physicsClientId=sim.client)


def check_rebuild(sim, scene: dict, tol: float = REBUILD_TOL_M) -> None:
    """Fail loudly if the rebuilt Sim is not the scene we are labelling."""
    pos = positions(sim)
    recorded = {o["id"]: o["pose"]["xyz_m"] for o in scene["objects"]}
    if pos.keys() != recorded.keys():
        raise RebuildMismatch(f"{scene['scene_id']}: ids differ {sorted(pos)} vs {sorted(recorded)}")
    off = [k for k in recorded if math.dist(pos[k], recorded[k]) > tol]
    if off:
        raise RebuildMismatch(f"{scene['scene_id']}: {off} not where scene.json says (tol {tol} m)")


def load_ranges(data_dir: Path, api=None) -> dict:
    """The run's knob ranges, which build_scene needs to rebuild an identical scene."""
    return scene_api(api).load_ranges(data_dir)


def export_variant(sim, scene_id: str, data_dir: Path, api=None) -> dict:
    """Write a modified Sim as a new scene `scene_id` (e.g. s_000123b) and return its scene.json.

    write_scene names everything after the seed, so write to a scratch folder and rename.
    """
    api, data_dir = scene_api(api), Path(data_dir)
    with tempfile.TemporaryDirectory(dir=data_dir) as tmp:
        scene = api.write_scene(sim, Path(tmp))
        seed_id = scene["scene_id"]
        for key in ("image_path", "depth_path", "seg_path"):
            rel = scene[key].replace(seed_id, scene_id)
            (data_dir / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.move(Path(tmp) / scene[key], data_dir / rel)
            scene[key] = rel
    scene["scene_id"] = scene_id
    write_scene(data_dir / "scenes" / f"{scene_id}.json", scene)
    return scene


def base_scenes(data_dir: Path):
    """(path, scene) for scenes rebuilt straight from their seed, i.e. not a/b/g variants."""
    for path in sorted((Path(data_dir) / "scenes").glob("s_*.json")):
        scene = json.loads(path.read_text())
        if scene["scene_id"] == f"s_{scene['seed']:06d}":
            yield path, scene


def write_scene(path: Path, scene: dict) -> None:
    path.write_text(json.dumps(scene, indent=2) + "\n")
