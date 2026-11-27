"""Build, settle and render one tabletop scene in PyBullet.

Conventions (see the Decisions table in context/handoff.md):
- seg PNG: 8-bit, 0 = nothing or floor, 1 = table, objects 2, 3, ...
- colours, shapes and size ranges below are the agreed lists.
- paths in scene.json are relative to the data folder.

Rebuilding a scene (e.g. for counterfactuals or before/after pairs):

    with build_scene(seed, load_ranges("data/")) as sim:   # the settled world scene.json describes
        ...                                                # sim.objects carry scene.json's ids
        settle(sim)                                        # False if it hit the step cap
        rgb, depth, seg = render(sim)                      # same camera and light as the scene
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
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
MAX_BUILD_ATTEMPTS = 10

# Support: averaged contact normal within ~72 degrees of vertical. Occlusion: at least 1% hidden.
SUPPORT_MIN_NZ = 0.3
OCCLUSION_MIN_FRAC = 0.01
# Counterfactuals: after removing a supporting object, anything whose centre moved more than this.
MOVED_MIN_M = 0.02

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

# Lighting presets for the main render. Any light parameter switches PyBullet's CPU renderer to its
# shadowed path, so all presets set shadow=1 and share one light direction.
LIGHT_DIRECTION = [0.4, 0.6, 1.0]
LIGHTING = {
    "bright": dict(lightAmbientCoeff=0.75, lightDiffuseCoeff=0.5, lightSpecularCoeff=0.05),
    "normal": dict(lightAmbientCoeff=0.6, lightDiffuseCoeff=0.35, lightSpecularCoeff=0.05),
    "dim": dict(lightAmbientCoeff=0.3, lightDiffuseCoeff=0.3, lightSpecularCoeff=0.02),
}

# Difficulty knobs: each scene draws its values from these ranges with its own rng.
DEFAULT_RANGES = {
    "n_objects": (3, 20),
    "elevation_deg": (30.0, 70.0),
    "azimuth_deg": (0.0, 360.0),
    "distractors": (0, 3),
    "lighting": ("bright", "normal", "dim"),
    "scale": (1.0, 1.0),
    "stack_frac": (0.0, 0.35),
}

# A planted (stacked) object lands within this distance of its base object's centre.
STACK_JITTER_M = 0.01


@dataclass
class SceneObject:
    id: str
    body: int
    shape: str
    color: str
    size_cm: list[float]
    seg_id: int = 0
    anchor: bool = False  # the object the distractors are planted around


@dataclass
class Knobs:
    n_objects: int
    elevation_deg: float
    azimuth_deg: float
    distractors: int
    lighting: str
    scale: float
    stack_frac: float


@dataclass
class Sim:
    """A built, settled scene: what build_scene returns and settle/render/write_scene take.

    objects carry the ids and seg_ids scene.json uses (fallen objects already removed).
    knobs=None means a hand-built scene: default camera and light, no planted distractors.
    Use it as a context manager, or call close(), to disconnect the physics client.
    """
    seed: int
    client: int
    table_id: int
    top_z: float
    objects: list[SceneObject]
    knobs: Knobs | None = None
    settled: bool = True  # False if the last settle hit MAX_SETTLE_STEPS

    @property
    def scene_id(self) -> str:
        return f"s_{self.seed:06d}"

    def close(self) -> None:
        p.disconnect(physicsClientId=self.client)

    def __enter__(self) -> Sim:
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def draw_knobs(rng: np.random.Generator, ranges: dict) -> Knobs:
    """One scene's knob values. Angles are rounded to 0.1 deg and scale to 0.01 before use."""
    lo, hi = ranges["n_objects"]
    n = int(rng.integers(lo, hi + 1))
    elevation = round(float(rng.uniform(*ranges["elevation_deg"])), 1)
    azimuth = round(float(rng.uniform(*ranges["azimuth_deg"])), 1) % 360.0
    lo, hi = ranges["distractors"]
    k = min(int(rng.integers(lo, hi + 1)), n - 1)
    lighting = ranges["lighting"][rng.integers(len(ranges["lighting"]))]
    scale = round(float(rng.uniform(*ranges["scale"])), 2)
    stack_frac = round(float(rng.uniform(*ranges["stack_frac"])), 2)
    return Knobs(n, elevation, azimuth, k, lighting, scale, stack_frac)


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


def draw_attributes(rng: np.random.Generator, n: int, k: int) -> tuple[list[tuple[str, str]], int]:
    """(shape, colour) per object with exactly k distractors around one anchor.

    The k distractors share the anchor's colour (different shape) or its shape (different colour),
    50/50. Every other object shares neither, so the anchor's colour+shape is unique.
    Returns (attributes in spawn order, index of the anchor).
    """
    colors = list(COLORS)
    shape0 = SHAPES[rng.integers(len(SHAPES))]
    color0 = colors[rng.integers(len(colors))]
    other_shapes = [s for s in SHAPES if s != shape0]
    other_colors = [c for c in colors if c != color0]
    attrs = [(shape0, color0)]
    for i in range(1, n):
        if i <= k and rng.random() < 0.5:
            attrs.append((other_shapes[rng.integers(len(other_shapes))], color0))
        elif i <= k:
            attrs.append((shape0, other_colors[rng.integers(len(other_colors))]))
        else:
            attrs.append((other_shapes[rng.integers(len(other_shapes))],
                          other_colors[rng.integers(len(other_colors))]))
    order = rng.permutation(n)
    return [attrs[j] for j in order], int(np.flatnonzero(order == 0)[0])


def draw_size_cm(rng: np.random.Generator, shape: str, scale: float = 1.0) -> list[float]:
    """[x, y, z] extent in cm times scale, rounded to 0.1 cm so the stored size is the simulated size."""
    if shape == "box":
        size = rng.uniform(4.0, 10.0, size=3)
    elif shape == "cylinder":
        d, h = rng.uniform(4.0, 8.0), rng.uniform(5.0, 15.0)
        size = np.array([d, d, h])
    else:
        d = rng.uniform(4.0, 8.0)
        size = np.array([d, d, d])
    return [round(float(v) * scale, 1) for v in size]


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


def spawn_objects(client: int, rng: np.random.Generator, attrs: list[tuple[str, str]], anchor: int,
                  top_z: float, scale: float = 1.0, n_stacked: int = 0) -> list[SceneObject]:
    """Drop one object per (shape, colour) over the spawn area, at random positions and sizes.

    Each object starts just above the table, or just above the highest earlier object whose
    footprint it overlaps, so nothing starts interpenetrating and drops stay short.
    The last n_stacked objects are moved over an earlier box or cylinder so they land on it.
    """
    gap = 0.01
    n = len(attrs)
    objects = []
    placed = []  # (x, y, footprint radius, top z, shape) of earlier spawns
    for i, (shape, color) in enumerate(attrs):
        size_cm = draw_size_cm(rng, shape, scale)
        x = rng.uniform(-SPAWN_HALF_X_M, SPAWN_HALF_X_M)
        y = rng.uniform(-SPAWN_HALF_Y_M, SPAWN_HALF_Y_M)
        yaw = rng.uniform(-np.pi, np.pi)
        bases = [j for j, pl in enumerate(placed) if pl[4] != "sphere"]
        if i >= n - n_stacked and bases:
            base = placed[bases[rng.integers(len(bases))]]
            x = base[0] + rng.uniform(-STACK_JITTER_M, STACK_JITTER_M)
            y = base[1] + rng.uniform(-STACK_JITTER_M, STACK_JITTER_M)
        sx, sy, sz = (v / 100.0 for v in size_cm)
        radius = float(np.hypot(sx, sy)) / 2
        bottom = top_z + gap
        for px, py, pr, ptop, _ in placed:
            if np.hypot(x - px, y - py) < radius + pr:
                bottom = max(bottom, ptop + gap)
        z = bottom + sz / 2
        quat = p.getQuaternionFromEuler([0.0, 0.0, yaw])
        body = spawn_object(client, shape, size_cm, color, [x, y, z], quat)
        placed.append((x, y, radius, bottom + sz, shape))
        objects.append(SceneObject(id="", body=body, shape=shape, color=color, size_cm=size_cm,
                                   anchor=i == anchor))
    return objects


def settle_objects(client: int, objects: list[SceneObject]) -> tuple[int, bool]:
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


def settle(sim: Sim) -> bool:
    """Settle the scene with the agreed stillness rule; False if it hit the step cap."""
    _, sim.settled = settle_objects(sim.client, sim.objects)
    return sim.settled


def assign_ids(objects: list[SceneObject]) -> None:
    """o1, o2, ... and seg_ids 2, 3, ... in list order, as written to scene.json."""
    for i, o in enumerate(objects):
        o.id = f"o{i + 1}"
        o.seg_id = SEG_FIRST_OBJECT + i


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


def render_raw(client: int, view, proj, lighting: str | None = None):
    """Return (rgb uint8 HxWx3, depth metres float32 HxW, raw body-id seg int32 HxW).

    lighting=None uses the renderer's cheap unshadowed default (enough for seg-only renders).
    """
    light = {} if lighting is None else dict(LIGHTING[lighting], lightDirection=LIGHT_DIRECTION, shadow=1)
    _, _, rgba, zbuf, seg = p.getCameraImage(WIDTH, HEIGHT, view, proj, renderer=p.ER_TINY_RENDERER,
                                             physicsClientId=client, **light)
    rgb = np.reshape(np.asarray(rgba, dtype=np.uint8), (HEIGHT, WIDTH, 4))[:, :, :3]
    zbuf = np.reshape(np.asarray(zbuf, dtype=np.float64), (HEIGHT, WIDTH))
    depth = (CAM_FAR_M * CAM_NEAR_M / (CAM_FAR_M - (CAM_FAR_M - CAM_NEAR_M) * zbuf)).astype(np.float32)
    seg = np.reshape(np.asarray(seg, dtype=np.int32), (HEIGHT, WIDTH))
    return rgb, depth, seg


def sim_knobs(sim: Sim) -> Knobs:
    if sim.knobs is not None:
        return sim.knobs
    return Knobs(len(sim.objects), DEFAULT_ELEVATION_DEG, DEFAULT_AZIMUTH_DEG, 0, "normal", 1.0, 0.0)


def render(sim: Sim, camera: dict | None = None):
    """Return (rgb uint8 HxWx3, depth metres float32 HxW, seg uint8 HxW in our seg values).

    camera=None uses the scene's own camera; otherwise a dict with elevation_deg and azimuth_deg
    (scene.json's "camera" block works). The light is always the scene's own preset.
    """
    knobs = sim_knobs(sim)
    cam = camera or {"elevation_deg": knobs.elevation_deg, "azimuth_deg": knobs.azimuth_deg}
    view, proj = camera_matrices(sim.top_z, cam["elevation_deg"], cam["azimuth_deg"])
    rgb, depth, seg_raw = render_raw(sim.client, view, proj, knobs.lighting)
    return rgb, depth, remap_seg(seg_raw, sim.table_id, sim.objects)


def remap_seg(seg_raw: np.ndarray, table_id: int, objects: list[SceneObject]) -> np.ndarray:
    """Body ids -> our seg values: nothing/floor 0, table 1, objects their seg_id."""
    out = np.full(seg_raw.shape, SEG_BACKGROUND, dtype=np.uint8)
    out[seg_raw == table_id] = SEG_TABLE
    for o in objects:
        out[seg_raw == o.body] = o.seg_id
    return out


def mask_facts(mask: np.ndarray, depth: np.ndarray) -> dict:
    """bbox (inclusive max), centroid, area and mean depth of one object's full-scene mask.

    A fully hidden object gets pixel_area 0 and nulls for the rest.
    """
    area = int(mask.sum())
    if area == 0:
        return {"bbox_2d": None, "centroid_2d": None, "pixel_area": 0, "depth_mean_m": None}
    ys, xs = np.nonzero(mask)
    return {
        "bbox_2d": [int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())],
        "centroid_2d": [int(round(float(xs.mean()))), int(round(float(ys.mean())))],
        "pixel_area": area,
        "depth_mean_m": round(float(depth[mask].mean(dtype=np.float64)), 4),
    }


def crop_projection(proj, x0: int, y0: int, w: int, h: int) -> list[float]:
    """Off-axis projection that renders only pixels [x0, x0+w) x [y0, y0+h) of the full image.

    Pixel (j, i) of a w x h render at this projection is pixel (x0+j, y0+i) of the full render.
    """
    P = np.array(proj).reshape(4, 4).T  # PyBullet matrices are column-major
    A = np.array([[WIDTH / w, 0, 0, (WIDTH - 2 * x0 - w) / w],
                  [0, HEIGHT / h, 0, -(HEIGHT - 2 * y0 - h) / h],
                  [0, 0, 1, 0],
                  [0, 0, 0, 1]])
    return list((A @ P).T.reshape(-1))


def pixel_box(client: int, body: int, view, proj, pad: int = 2) -> tuple[int, int, int, int]:
    """Image rectangle (x0, y0, x1, y1), max exclusive, that contains the body's projected AABB."""
    lo, hi = p.getAABB(body, physicsClientId=client)
    corners = np.array([[x, y, z, 1.0] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])]).T
    clip = np.array(proj).reshape(4, 4).T @ np.array(view).reshape(4, 4).T @ corners
    xs = (clip[0] / clip[3] + 1) / 2 * WIDTH
    ys = (1 - clip[1] / clip[3]) / 2 * HEIGHT
    x0 = max(int(np.floor(xs.min())) - pad, 0)
    y0 = max(int(np.floor(ys.min())) - pad, 0)
    x1 = min(int(np.ceil(xs.max())) + pad, WIDTH)
    y1 = min(int(np.ceil(ys.max())) + pad, HEIGHT)
    return x0, y0, x1, y1


def render_alone_masks(client: int, objects: list[SceneObject], view, proj) -> dict[int, np.ndarray]:
    """Each object's mask with every other object moved out of view (same camera, table stays).

    Only the object's own image rectangle is rendered (render cost scales with pixels); the masks
    are bit-identical to full-frame renders. shadow=0 is explicit because once a lit render has
    run, PyBullet's CPU renderer keeps using its slower shadowed path.
    No stepSimulation happens here. Restoring uses saveState/restoreState because feeding a pose back
    through resetBasePositionAndOrientation re-normalises the quaternion (~1e-17 drift).
    """
    saved = {o.body: p.getBasePositionAndOrientation(o.body, physicsClientId=client) for o in objects}
    parked = {o.body: ([0.0, 0.0, -10.0 - i], [0.0, 0.0, 0.0, 1.0]) for i, o in enumerate(objects)}
    state = p.saveState(physicsClientId=client)
    masks = {}
    try:
        for o in objects:
            p.resetBasePositionAndOrientation(o.body, *parked[o.body], physicsClientId=client)
        for o in objects:
            p.resetBasePositionAndOrientation(o.body, *saved[o.body], physicsClientId=client)
            mask = np.zeros((HEIGHT, WIDTH), dtype=bool)
            x0, y0, x1, y1 = pixel_box(client, o.body, view, proj)
            if x1 > x0 and y1 > y0:
                w, h = x1 - x0, y1 - y0
                seg = p.getCameraImage(w, h, view, crop_projection(proj, x0, y0, w, h),
                                       renderer=p.ER_TINY_RENDERER, shadow=0, physicsClientId=client)[4]
                mask[y0:y1, x0:x1] = np.reshape(np.asarray(seg), (h, w)) == o.body
            masks[o.body] = mask
            p.resetBasePositionAndOrientation(o.body, *parked[o.body], physicsClientId=client)
    finally:
        p.restoreState(stateId=state, physicsClientId=client)
        p.removeState(state, physicsClientId=client)
    return masks


def contact_graph(client: int, table_id: int, objects: list[SceneObject]) -> list[dict]:
    """Support pairs from the contacts of the last simulation step.

    The pair's averaged contact normal decides: within ~72 degrees of vertical (|n_z| >= 0.3), the
    upper body is "top", so a box leaning on another counts as supported by it; flatter
    (side-by-side) contacts are skipped. The table is named "table".
    """
    names = {o.body: o.id for o in objects}
    names[table_id] = "table"
    bodies = [o.body for o in objects]
    pairs = []
    for i, a in enumerate(bodies):
        pairs.append((a, table_id))
        pairs.extend((a, b) for b in bodies[i + 1:])
    contacts = []
    for a, b in pairs:
        points = p.getContactPoints(bodyA=a, bodyB=b, physicsClientId=client)
        if not points:
            continue
        normal = np.mean([pt[7] for pt in points], axis=0)  # contactNormalOnB points from b toward a
        nz = normal[2] / np.linalg.norm(normal)
        if nz >= SUPPORT_MIN_NZ:
            contacts.append({"top": names[a], "bottom": names[b]})
        elif nz <= -SUPPORT_MIN_NZ:
            contacts.append({"top": names[b], "bottom": names[a]})
    return contacts


def occlusion_pairs(seg: np.ndarray, objects: list[SceneObject], alone: dict[int, np.ndarray]) -> list[dict]:
    """Which objects hide part of which, from the full-scene seg under each object's alone-mask."""
    by_seg = {o.seg_id: o.id for o in objects}
    occludes = []
    for back in objects:
        alone_mask = alone[back.body]
        alone_px = int(alone_mask.sum())
        if alone_px == 0:
            continue
        values, counts = np.unique(seg[alone_mask], return_counts=True)
        for value, count in zip(values.tolist(), counts.tolist()):
            if value == back.seg_id or value not in by_seg:
                continue
            frac = count / alone_px
            if frac >= OCCLUSION_MIN_FRAC:
                occludes.append({"front": by_seg[value], "back": back.id, "frac": round(frac, 3)})
    return occludes


def counterfactuals(sim: Sim, contacts: list[dict]) -> dict:
    """F8 labels: for each object that supports another, what moves more than 2 cm without it.

    Each removal starts from the saved settled state (the world scene.json describes): the supporting
    object is moved far below the floor, which takes it out of the world like deleting it, the rest
    re-settle with the agreed stillness rule, and the state is restored exactly afterwards.
    """
    client = sim.client
    supporters = {c["bottom"] for c in contacts if c["bottom"] != "table"}
    start = {o.body: np.array(p.getBasePositionAndOrientation(o.body, physicsClientId=client)[0])
             for o in sim.objects}
    out = {}
    for removed in sim.objects:
        if removed.id not in supporters:
            continue
        rest = [o for o in sim.objects if o is not removed]
        state = p.saveState(physicsClientId=client)
        try:
            p.resetBasePositionAndOrientation(removed.body, [0.0, 0.0, -10.0], [0.0, 0.0, 0.0, 1.0],
                                              physicsClientId=client)
            _, settled = settle_objects(client, rest)
            if not settled:
                print(f"warning: {sim.scene_id} counterfactual without {removed.id} hit the "
                      f"{MAX_SETTLE_STEPS}-step settle cap; measured at the cap", file=sys.stderr)
            moved = []
            for o in rest:
                xyz = np.array(p.getBasePositionAndOrientation(o.body, physicsClientId=client)[0])
                if np.linalg.norm(xyz - start[o.body]) > MOVED_MIN_M:
                    moved.append(o.id)
            out[removed.id] = {"moved": moved}
        finally:
            p.restoreState(stateId=state, physicsClientId=client)
            p.removeState(state, physicsClientId=client)
    return out


def git_version() -> str:
    try:
        sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                             check=True, cwd=Path(__file__).parent).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        sha = "unknown"
    return f"gen@{sha}"


MANIFEST = "manifest.json"


def ranges_json(ranges: dict | None) -> dict:
    """The full knob ranges (defaults filled in) as JSON lists, in DEFAULT_RANGES order."""
    full = {**DEFAULT_RANGES, **(ranges or {})}
    return {k: list(full[k]) for k in DEFAULT_RANGES}


def load_ranges(data_dir: str | Path) -> dict:
    """The knob ranges a data folder was generated with, from its manifest.json."""
    manifest = json.loads((Path(data_dir) / MANIFEST).read_text())
    return {k: tuple(v) for k, v in manifest["ranges"].items()}


def check_manifest(data_dir: str | Path, ranges: dict | None) -> None:
    """Raise if the folder was generated with other knob ranges: one scene_id must mean one scene."""
    path = Path(data_dir) / MANIFEST
    if path.is_file():
        existing = json.loads(path.read_text())["ranges"]
        if existing != ranges_json(ranges):
            raise ValueError(f"{path} was generated with other knob ranges: {existing}. "
                             "Use another --out folder, or delete this one first.")


def record_run(data_dir: str | Path, ranges: dict | None, seed: int, n: int) -> None:
    """Add a finished run (first seed, count, generator version) to the folder's manifest.json."""
    check_manifest(data_dir, ranges)
    path = Path(data_dir) / MANIFEST
    manifest = json.loads(path.read_text()) if path.is_file() else {"ranges": ranges_json(ranges), "runs": []}
    manifest["runs"].append({"seed": seed, "n": n, "generator_version": git_version()})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2) + "\n")


def build_scene(seed: int, ranges: dict | None = None) -> Sim:
    """Rebuild the settled world for one seed, exactly as scene.json describes it.

    ranges must be the run's knob ranges (load_ranges(data_dir) reads them from manifest.json):
    the knobs are drawn from the scene's rng before the objects, so other ranges give a different
    scene. The caller owns the returned Sim and must close it.
    """
    scene_id = f"s_{seed:06d}"
    rng = make_rng(seed)
    knobs = draw_knobs(rng, {**DEFAULT_RANGES, **(ranges or {})})
    # If the distractor anchor falls off the table, rebuild from the same rng stream (next draws).
    for _ in range(MAX_BUILD_ATTEMPTS):
        attrs, anchor = draw_attributes(rng, knobs.n_objects, knobs.distractors)
        client, table_id, top_z = make_world()
        try:
            objects = spawn_objects(client, rng, attrs, anchor, top_z, knobs.scale,
                                    int(round(knobs.stack_frac * knobs.n_objects)))
            sim = Sim(seed, client, table_id, top_z, objects, knobs)
            if not settle(sim):
                print(f"warning: {scene_id} hit the {MAX_SETTLE_STEPS}-step settle cap", file=sys.stderr)
            sim.objects = remove_fallen(client, objects, top_z)
            if any(o.anchor for o in sim.objects):
                assign_ids(sim.objects)
                return sim
        except BaseException:
            p.disconnect(physicsClientId=client)
            raise
        sim.close()
        print(f"note: {scene_id} distractor anchor fell off the table; rebuilding", file=sys.stderr)
    raise RuntimeError(f"{scene_id}: distractor anchor fell off the table {MAX_BUILD_ATTEMPTS} times")


def generate_scene(seed: int, out_dir: str | Path, ranges: dict | None = None) -> dict:
    """Build, settle and render one scene; write its files under out_dir and return scene.json."""
    with build_scene(seed, ranges) as sim:
        return write_scene(sim, Path(out_dir))


def count_distractors(objects: list[SceneObject]) -> int | None:
    """Objects sharing the anchor's colour or shape, recounted after fallen objects were removed.

    None when there is no anchor (hand-built scenes, or the anchor fell off the table).
    """
    anchor = next((o for o in objects if o.anchor), None)
    if anchor is None:
        return None
    return sum(1 for o in objects if o is not anchor and (o.color == anchor.color or o.shape == anchor.shape))


def generate_scene_timed(seed: int, out_dir: str | Path, ranges: dict | None = None) -> tuple[str, int, float]:
    """generate_scene for a worker process: returns (scene_id, n_objects, seconds)."""
    t0 = time.time()
    scene = generate_scene(seed, out_dir, ranges)
    return scene["scene_id"], len(scene["objects"]), time.time() - t0


def write_scene(sim: Sim, out: Path) -> dict:
    """Render a settled world, compute per-object facts, write the files and return scene.json."""
    client, table_id, objects, seed, scene_id = sim.client, sim.table_id, sim.objects, sim.seed, sim.scene_id
    knobs = sim_knobs(sim)
    elevation_deg, azimuth_deg = knobs.elevation_deg, knobs.azimuth_deg
    assign_ids(objects)

    view, proj = camera_matrices(sim.top_z, elevation_deg, azimuth_deg)
    rgb, depth, seg_raw = render_raw(client, view, proj, knobs.lighting)
    seg = remap_seg(seg_raw, table_id, objects)
    contacts = contact_graph(client, table_id, objects)  # before render_alone_masks moves bodies around
    alone = render_alone_masks(client, objects, view, proj)
    cf = counterfactuals(sim, contacts)  # restores the settled state, so poses below are unchanged

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
        facts = mask_facts(seg == o.seg_id, depth)
        alone_px = int(alone[o.body].sum())
        visible_frac = round(facts["pixel_area"] / alone_px, 3) if alone_px else 0.0
        scene_objects.append({
            "id": o.id, "seg_id": o.seg_id, "shape": o.shape, "color": o.color, "size_cm": o.size_cm,
            "pose": {"xyz_m": [round(v, 5) for v in xyz], "quat": [round(v, 5) for v in quat]},
            "bbox_2d": facts["bbox_2d"], "centroid_2d": facts["centroid_2d"],
            "pixel_area": facts["pixel_area"], "visible_frac": visible_frac,
            "depth_mean_m": facts["depth_mean_m"],
        })
    scene = {
        "scene_id": scene_id, "seed": seed, "generator_version": git_version(),
        **rel,
        "width": WIDTH, "height": HEIGHT,
        "camera": {"view_matrix": view, "proj_matrix": proj,
                   "elevation_deg": elevation_deg, "azimuth_deg": azimuth_deg},
        "objects": scene_objects,
        "contacts": contacts,
        "occludes": occlusion_pairs(seg, objects, alone),
        "counterfactuals": cf,
        "pair": None,           # set only for F10 before/after scenes
        "difficulty": {
            "n_objects": len(objects),
            "occlusion": round(max((1.0 - o["visible_frac"] for o in scene_objects), default=0.0), 3),
            "distractors": count_distractors(objects),
            "lighting": knobs.lighting,
            "scale": knobs.scale,
            "stacked": len({c["top"] for c in contacts if c["bottom"] != "table"}),
        },
    }
    (out / f"scenes/{scene_id}.json").write_text(json.dumps(scene, indent=2) + "\n")
    return scene
