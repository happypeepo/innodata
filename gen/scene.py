"""Build, settle and render one tabletop scene in PyBullet.

Conventions (see the Decisions table in context/handoff.md):
- seg PNG: 8-bit, 0 = nothing or floor, 1 = table, objects 2, 3, ...
- colours, shapes and size ranges below are the agreed lists.
- paths in scene.json are relative to the data folder.
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pybullet as p
import pybullet_data
from PIL import Image

GRAVITY = -9.81
SIM_HZ = 240

WIDTH, HEIGHT = 1024, 768

# Camera: looks at the table-top centre.
CAM_DISTANCE_M = 0.9
CAM_FOV_DEG = 50.0
CAM_NEAR_M = 0.05
CAM_FAR_M = 3.0
DEFAULT_ELEVATION_DEG = 45.0
DEFAULT_AZIMUTH_DEG = 30.0

# Objects spawn inside this area, centred on the table.
SPAWN_HALF_X_M = 0.30
SPAWN_HALF_Y_M = 0.20

# Settling: every object below both speeds, capped at 10 s simulated.
STILL_LIN_MPS = 0.001
STILL_ANG_RADPS = 0.01
STILL_STEPS = 24  # must hold for 0.1 s, so a bounce apex or the spawn instant doesn't count
MAX_SETTLE_STEPS = 2400

SEG_BACKGROUND = 0
SEG_TABLE = 1
SEG_FIRST_OBJECT = 2

COLORS = {
    "red": (0.85, 0.10, 0.10),
    "green": (0.10, 0.60, 0.20),
    "blue": (0.12, 0.30, 0.85),
    "yellow": (0.95, 0.85, 0.05),
    "orange": (0.95, 0.45, 0.05),
    "purple": (0.50, 0.15, 0.70),
}
SHAPES = ("box", "cylinder", "sphere")

DENSITY_KG_M3 = 500.0


@dataclass
class SceneObject:
    id: str
    body: int
    shape: str
    color: str
    size_cm: list[float]
    seg_id: int = 0


def make_world() -> tuple[int, int, float]:
    """Connect headless, load the floor and table.

    Returns (client, table_id, table_top_z).
    """
    client = p.connect(p.DIRECT)
    p.setAdditionalSearchPath(pybullet_data.getDataPath(), physicsClientId=client)
    p.setGravity(0, 0, GRAVITY, physicsClientId=client)
    p.setTimeStep(1.0 / SIM_HZ, physicsClientId=client)
    p.loadURDF("plane.urdf", physicsClientId=client)
    table_id = p.loadURDF("table/table.urdf", physicsClientId=client)
    # getAABB pads by the collision margin (~1 mm), so read the top off the collision box itself.
    shape = p.getCollisionShapeData(table_id, -1, physicsClientId=client)[0]
    dims, local_pos = shape[3], shape[5]
    top_z = local_pos[2] + dims[2] / 2
    return client, table_id, top_z


def spawn_box(client: int, half_extents_m, xyz_m, rgba, mass_kg: float = 0.1) -> int:
    """Create a box body and return its PyBullet body id."""
    col = p.createCollisionShape(p.GEOM_BOX, halfExtents=half_extents_m, physicsClientId=client)
    vis = p.createVisualShape(p.GEOM_BOX, halfExtents=half_extents_m, rgbaColor=rgba, physicsClientId=client)
    return p.createMultiBody(mass_kg, col, vis, basePosition=xyz_m, physicsClientId=client)


def make_rng(seed: int) -> np.random.Generator:
    """The only random source in gen/: create once per scene and pass it everywhere."""
    return np.random.default_rng(seed)


def draw_size_cm(rng: np.random.Generator, shape: str) -> list[float]:
    """[x, y, z] extent in cm, rounded to 0.1 cm so the stored size is the simulated size."""
    if shape == "box":
        size = rng.uniform(4.0, 10.0, size=3)
    elif shape == "cylinder":
        d, h = rng.uniform(4.0, 8.0), rng.uniform(5.0, 15.0)
        size = np.array([d, d, h])
    else:
        d = rng.uniform(4.0, 8.0)
        size = np.array([d, d, d])
    return [round(float(v), 1) for v in size]


def spawn_object(client: int, shape: str, size_cm: list[float], color: str, xyz_m, quat) -> int:
    sx, sy, sz = (v / 100.0 for v in size_cm)
    rgba = [*COLORS[color], 1.0]
    if shape == "box":
        kw = dict(halfExtents=[sx / 2, sy / 2, sz / 2])
        col = p.createCollisionShape(p.GEOM_BOX, physicsClientId=client, **kw)
        vis = p.createVisualShape(p.GEOM_BOX, rgbaColor=rgba, physicsClientId=client, **kw)
        volume = sx * sy * sz
    elif shape == "cylinder":
        r = sx / 2
        col = p.createCollisionShape(p.GEOM_CYLINDER, radius=r, height=sz, physicsClientId=client)
        vis = p.createVisualShape(p.GEOM_CYLINDER, radius=r, length=sz, rgbaColor=rgba, physicsClientId=client)
        volume = np.pi * r * r * sz
    else:
        r = sx / 2
        col = p.createCollisionShape(p.GEOM_SPHERE, radius=r, physicsClientId=client)
        vis = p.createVisualShape(p.GEOM_SPHERE, radius=r, rgbaColor=rgba, physicsClientId=client)
        volume = 4.0 / 3.0 * np.pi * r**3
    body = p.createMultiBody(DENSITY_KG_M3 * volume, col, vis, basePosition=xyz_m,
                             baseOrientation=quat, physicsClientId=client)
    # Rolling friction stops spheres and tipped cylinders from rolling off the table.
    p.changeDynamics(body, -1, lateralFriction=0.8, rollingFriction=0.002, spinningFriction=0.002,
                     physicsClientId=client)
    return body


def spawn_objects(client: int, rng: np.random.Generator, n: int, top_z: float) -> list[SceneObject]:
    """Drop n random objects over the spawn area.

    Each object starts just above the table, or just above the highest earlier object whose
    footprint it overlaps, so nothing starts interpenetrating and drops stay short.
    """
    gap = 0.01
    objects = []
    placed = []  # (x, y, footprint radius, top z) of earlier spawns
    colors = list(COLORS)
    for _ in range(n):
        shape = SHAPES[rng.integers(len(SHAPES))]
        color = colors[rng.integers(len(colors))]
        size_cm = draw_size_cm(rng, shape)
        x = rng.uniform(-SPAWN_HALF_X_M, SPAWN_HALF_X_M)
        y = rng.uniform(-SPAWN_HALF_Y_M, SPAWN_HALF_Y_M)
        yaw = rng.uniform(-np.pi, np.pi)
        sx, sy, sz = (v / 100.0 for v in size_cm)
        radius = float(np.hypot(sx, sy)) / 2
        bottom = top_z + gap
        for px, py, pr, ptop in placed:
            if np.hypot(x - px, y - py) < radius + pr:
                bottom = max(bottom, ptop + gap)
        z = bottom + sz / 2
        quat = p.getQuaternionFromEuler([0.0, 0.0, yaw])
        body = spawn_object(client, shape, size_cm, color, [x, y, z], quat)
        placed.append((x, y, radius, bottom + sz))
        objects.append(SceneObject(id="", body=body, shape=shape, color=color, size_cm=size_cm))
    return objects


def settle(client: int, objects: list[SceneObject]) -> tuple[int, bool]:
    """Step until every object is still for STILL_STEPS in a row. Returns (steps, settled)."""
    still_for = 0
    for step in range(1, MAX_SETTLE_STEPS + 1):
        p.stepSimulation(physicsClientId=client)
        moving = False
        for o in objects:
            lin, ang = p.getBaseVelocity(o.body, physicsClientId=client)
            if np.linalg.norm(lin) >= STILL_LIN_MPS or np.linalg.norm(ang) >= STILL_ANG_RADPS:
                moving = True
                break
        still_for = 0 if moving else still_for + 1
        if still_for >= STILL_STEPS:
            return step, True
    return MAX_SETTLE_STEPS, False


def remove_fallen(client: int, objects: list[SceneObject], top_z: float) -> list[SceneObject]:
    """Drop objects whose centre ended below the table top (they fell off)."""
    kept = []
    for o in objects:
        (_, _, z), _ = p.getBasePositionAndOrientation(o.body, physicsClientId=client)
        if z < top_z:
            p.removeBody(o.body, physicsClientId=client)
        else:
            kept.append(o)
    return kept


def camera_matrices(top_z: float, elevation_deg: float, azimuth_deg: float) -> tuple[list[float], list[float]]:
    view = p.computeViewMatrixFromYawPitchRoll(
        cameraTargetPosition=[0.0, 0.0, top_z], distance=CAM_DISTANCE_M,
        yaw=azimuth_deg, pitch=-elevation_deg, roll=0.0, upAxisIndex=2)
    proj = p.computeProjectionMatrixFOV(CAM_FOV_DEG, WIDTH / HEIGHT, CAM_NEAR_M, CAM_FAR_M)
    return list(view), list(proj)


def render(client: int, view, proj):
    """Return (rgb uint8 HxWx3, depth metres float32 HxW, raw body-id seg int32 HxW)."""
    _, _, rgba, zbuf, seg = p.getCameraImage(WIDTH, HEIGHT, view, proj, renderer=p.ER_TINY_RENDERER,
                                             physicsClientId=client)
    rgb = np.reshape(np.asarray(rgba, dtype=np.uint8), (HEIGHT, WIDTH, 4))[:, :, :3]
    zbuf = np.reshape(np.asarray(zbuf, dtype=np.float64), (HEIGHT, WIDTH))
    depth = (CAM_FAR_M * CAM_NEAR_M / (CAM_FAR_M - (CAM_FAR_M - CAM_NEAR_M) * zbuf)).astype(np.float32)
    seg = np.reshape(np.asarray(seg, dtype=np.int32), (HEIGHT, WIDTH))
    return rgb, depth, seg


def remap_seg(seg_raw: np.ndarray, table_id: int, objects: list[SceneObject]) -> np.ndarray:
    """Body ids -> our seg values: nothing/floor 0, table 1, objects their seg_id."""
    out = np.full(seg_raw.shape, SEG_BACKGROUND, dtype=np.uint8)
    out[seg_raw == table_id] = SEG_TABLE
    for o in objects:
        out[seg_raw == o.body] = o.seg_id
    return out


def git_version() -> str:
    try:
        sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                             check=True, cwd=Path(__file__).parent).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        sha = "unknown"
    return f"gen@{sha}"


def generate_scene(seed: int, out_dir: str | Path, n_objects: int,
                   elevation_deg: float = DEFAULT_ELEVATION_DEG,
                   azimuth_deg: float = DEFAULT_AZIMUTH_DEG) -> dict:
    """Build, settle and render one scene; write its files under out_dir and return scene.json."""
    out = Path(out_dir)
    scene_id = f"s_{seed:06d}"
    rng = make_rng(seed)
    client, table_id, top_z = make_world()
    try:
        objects = spawn_objects(client, rng, n_objects, top_z)
        _, settled = settle(client, objects)
        if not settled:
            print(f"warning: {scene_id} hit the {MAX_SETTLE_STEPS}-step settle cap", file=sys.stderr)
        objects = remove_fallen(client, objects, top_z)
        for i, o in enumerate(objects):
            o.id = f"o{i + 1}"
            o.seg_id = SEG_FIRST_OBJECT + i

        view, proj = camera_matrices(top_z, elevation_deg, azimuth_deg)
        rgb, depth, seg_raw = render(client, view, proj)
        seg = remap_seg(seg_raw, table_id, objects)

        rel = {
            "image_path": f"images/{scene_id}.png",
            "depth_path": f"depth/{scene_id}.npy",
            "seg_path": f"seg/{scene_id}.png",
        }
        for path in [*rel.values(), f"scenes/{scene_id}.json"]:
            (out / path).parent.mkdir(parents=True, exist_ok=True)
        Image.fromarray(rgb).save(out / rel["image_path"])
        np.save(out / rel["depth_path"], depth)
        Image.fromarray(seg, mode="L").save(out / rel["seg_path"])

        scene_objects = []
        for o in objects:
            xyz, quat = p.getBasePositionAndOrientation(o.body, physicsClientId=client)
            scene_objects.append({
                "id": o.id, "seg_id": o.seg_id, "shape": o.shape, "color": o.color, "size_cm": o.size_cm,
                "pose": {"xyz_m": [round(v, 5) for v in xyz], "quat": [round(v, 5) for v in quat]},
            })
        scene = {
            "scene_id": scene_id, "seed": seed, "generator_version": git_version(),
            **rel,
            "width": WIDTH, "height": HEIGHT,
            "camera": {"view_matrix": view, "proj_matrix": proj,
                       "elevation_deg": elevation_deg, "azimuth_deg": azimuth_deg},
            "objects": scene_objects,
        }
        (out / f"scenes/{scene_id}.json").write_text(json.dumps(scene, indent=2) + "\n")
        return scene
    finally:
        p.disconnect(physicsClientId=client)
