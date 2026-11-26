"""Test stand-in for gen.scene, mirroring the parts of Raj's API that gen/_rebuild.py uses.
Not the real generator: tests need tiny, hand-placed layouts with known physics.

ranges["layout"] lists the objects explicitly, so tests control the physics:
    {"shape": "box", "half": [hx, hy, hz], "xy": [x, y], "z": bottom height above the top, "color": "red"}
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from types import SimpleNamespace
from pathlib import Path

import numpy as np
import pybullet as p
from PIL import Image

TOP_Z = 0.625
W, H = 160, 120
SPAWN_HALF_X_M = 0.30
SPAWN_HALF_Y_M = 0.20


@dataclass
class Sim:
    seed: int
    client: int
    table_id: int
    top_z: float
    objects: list = field(default_factory=list)

    @property
    def scene_id(self):
        return f"s_{self.seed:06d}"

    def close(self):
        p.disconnect(physicsClientId=self.client)


def load_ranges(data_dir):
    return json.loads((Path(data_dir) / "manifest.json").read_text())["ranges"]


def _shape(c, spec):
    hx, hy, hz = spec["half"]
    if spec["shape"] == "box":
        return p.createCollisionShape(p.GEOM_BOX, halfExtents=spec["half"], physicsClientId=c), hz
    if spec["shape"] == "cylinder":
        return p.createCollisionShape(p.GEOM_CYLINDER, radius=hx, height=2 * hz, physicsClientId=c), hz
    return p.createCollisionShape(p.GEOM_SPHERE, radius=hx, physicsClientId=c), hx


def build_scene(seed, ranges):
    c = p.connect(p.DIRECT)
    p.setGravity(0, 0, -9.81, physicsClientId=c)
    col = p.createCollisionShape(p.GEOM_BOX, halfExtents=[0.75, 0.5, 0.025], physicsClientId=c)
    table = p.createMultiBody(0, col, basePosition=[0, 0, TOP_Z - 0.025], physicsClientId=c)
    p.changeDynamics(table, -1, lateralFriction=0.8, physicsClientId=c)
    sim = Sim(seed, c, table, TOP_Z)
    for i, spec in enumerate(ranges["layout"]):
        shape, half_h = _shape(c, spec)
        body = p.createMultiBody(0.2, shape, basePosition=[*spec["xy"], TOP_Z + spec["z"] + half_h],
                                 physicsClientId=c)
        p.changeDynamics(body, -1, lateralFriction=0.8, physicsClientId=c)
        sim.objects.append(SimpleNamespace(id=f"o{i + 1}", seg_id=i + 2, body=body, shape=spec["shape"],
                                           color=spec.get("color", "red"), size_cm=[200 * h for h in spec["half"]]))
    settle(sim)
    return sim


def settle(sim, cap=2400):
    for _ in range(cap):
        p.stepSimulation(physicsClientId=sim.client)
        vels = [p.getBaseVelocity(o.body, physicsClientId=sim.client) for o in sim.objects]
        if all(np.linalg.norm(v) < 1e-3 and np.linalg.norm(w) < 1e-2 for v, w in vels):
            return True
    return False


def _camera():
    view = p.computeViewMatrixFromYawPitchRoll([0, 0, TOP_Z], 0.9, 30, -45, 0, 2)
    proj = p.computeProjectionMatrixFOV(50, W / H, 0.05, 3.0)
    return view, proj


def render(sim, camera=None):
    view, proj = camera or _camera()
    _, _, rgb, depth, seg = p.getCameraImage(W, H, view, proj, renderer=p.ER_TINY_RENDERER,
                                             physicsClientId=sim.client)
    rgb = np.reshape(rgb, (H, W, 4))[:, :, :3].astype(np.uint8)
    seg = np.reshape(seg, (H, W))
    out = np.zeros_like(seg, dtype=np.uint8)
    out[seg == sim.table_id] = 1
    for o in sim.objects:
        out[seg == o.body] = o.seg_id
    return rgb, np.reshape(depth, (H, W)), out


def write_scene(sim, data_dir):
    data_dir, scene_id = Path(data_dir), sim.scene_id
    rgb, depth, seg = render(sim)
    for sub in ("images", "depth", "seg", "scenes"):
        (data_dir / sub).mkdir(parents=True, exist_ok=True)
    Image.fromarray(rgb).save(data_dir / f"images/{scene_id}.png")
    Image.fromarray(seg).save(data_dir / f"seg/{scene_id}.png")
    np.save(data_dir / f"depth/{scene_id}.npy", depth)

    objects, c = [], sim.client
    for o in sim.objects:
        pos, orn = p.getBasePositionAndOrientation(o.body, physicsClientId=c)
        ys, xs = np.nonzero(seg == o.seg_id)
        seen = len(xs) > 0
        objects.append({
            "id": o.id, "seg_id": o.seg_id, "shape": o.shape, "color": o.color, "size_cm": o.size_cm,
            "pose": {"xyz_m": list(pos), "quat": list(orn)},
            "bbox_2d": [int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())] if seen else None,
            "centroid_2d": [int(xs.mean()), int(ys.mean())] if seen else None,
            "pixel_area": int(len(xs)), "visible_frac": 1.0 if seen else 0.0,
            "depth_mean_m": float(depth[ys, xs].mean()) if seen else None,
        })
    contacts = []
    for o in sim.objects:
        b = o.body
        if p.getContactPoints(b, sim.table_id, physicsClientId=c):
            contacts.append({"top": o.id, "bottom": "table"})
        for other in sim.objects:
            ob = other.body
            if ob != b and p.getContactPoints(b, ob, physicsClientId=c) and \
                    p.getBasePositionAndOrientation(b, physicsClientId=c)[0][2] > \
                    p.getBasePositionAndOrientation(ob, physicsClientId=c)[0][2]:
                contacts.append({"top": o.id, "bottom": other.id})
    seed = sim.seed
    scene = {
        "scene_id": scene_id, "seed": seed, "generator_version": "fake@test",
        "image_path": f"images/{scene_id}.png", "depth_path": f"depth/{scene_id}.npy",
        "seg_path": f"seg/{scene_id}.png", "width": W, "height": H,
        "camera": {"view_matrix": list(_camera()[0]), "proj_matrix": list(_camera()[1]),
                   "elevation_deg": 45, "azimuth_deg": 30},
        "objects": objects, "contacts": contacts, "occludes": [], "counterfactuals": {},
        "pair": None, "difficulty": {"n_objects": len(objects), "occlusion": 0.0, "distractors": 0},
    }
    (data_dir / f"scenes/{scene_id}.json").write_text(json.dumps(scene, indent=2) + "\n")
    return scene
