"""Build the hand-made fixtures that A, C and D code against until real data exists.

Run from the repo root:  python fixtures/make_fixtures.py

No PyBullet needed. Each scene is hand-placed in a table-top frame (metres, z = 0 is
the table surface, x to the right, y away from the default camera). A small numpy
renderer projects every primitive through real OpenGL-style view/projection matrices,
so pixels, depth, masks and the camera matrices in scene.json all agree with each other.

Outputs, laid out exactly like data/ so `--data fixtures/` and `--data data/` behave the same:
  fixtures/scenes/<scene_id>.json   scene.json contract
  fixtures/images/<scene_id>.png    RGB, 1024x768
  fixtures/seg/<scene_id>.png       8-bit, pixel = seg_id (0 nothing/floor, 1 table, objects from 2)
  fixtures/depth/<scene_id>.npy     float32 metres along the camera's viewing axis
  fixtures/tasks.jsonl              ~15 hand-written tasks covering F1-F10
  fixtures/results/mock.jsonl       fake results for a "mock" model (right, wrong, partial, parse failure, text_only)

Everything is deterministic: running it twice gives byte-identical JSON.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent
W, H = 1024, 768

# Camera (Raj's Role A page, Q5 option A): look at the table-top centre from 0.9 m,
# vertical FOV 50 deg, near 0.05 m, far 3.0 m, elevation 45 deg, azimuth 30 deg.
CAM_DIST, FOV_DEG, NEAR, FAR = 0.9, 50.0, 0.05, 3.0
ELEV_DEG, AZIM_DEG = 45.0, 30.0
TABLE_HALF = (0.75, 0.50)  # 1.5 x 1.0 m table top
TABLE_TOP_Z_WORLD = 0.625  # floor is this far below the table surface

COLORS = {
    "red": (200, 45, 45), "green": (45, 155, 70), "blue": (45, 85, 205),
    "yellow": (235, 205, 45), "orange": (240, 135, 35), "purple": (135, 65, 175),
}
TABLE_RGB = (196, 160, 118)
FLOOR_RGB = ((215, 215, 222), (185, 185, 195))

ANSWER_FORMAT = {
    "point": 'Return JSON: {"point": [y, x]} with y, x normalised 0-1000.',
    "bbox": 'Return JSON: {"bbox": [ymin, xmin, ymax, xmax]} with values normalised 0-1000.',
    "count": 'Return JSON: {"count": <integer>}.',
    "choice": 'Return JSON: {"choice": "<letter>"} using one letter from the options.',
    "bool": 'Return JSON: {"bool": true} or {"bool": false}.',
    "sequence": 'Return JSON: {"sequence": ["<letter>", ...]} listing every option letter exactly once, in order.',
    "set": 'Return JSON: {"set": ["<letter>", ...]} with every option letter that applies; use [] if none.',
}
CHECKER = {"bbox": "bbox_iou", "count": "exact", "choice": "exact", "bool": "exact",
           "sequence": "topo_order_valid", "set": "set_f1"}

# Validity rules (AGENTS.md + lead-approved defaults, 24 Nov 2026).
MIN_VISIBLE = 0.30        # docs: target >= 30% visible (not F3)
F3_COUNT_MIN = 0.10       # lead-approved: F3 counts visible_frac >= 0.10
F4_MIN_GAP_M = 0.03       # docs: depth difference >= 3 cm


# ---------------------------------------------------------------- camera maths

def camera_matrices():
    el, az = math.radians(ELEV_DEG), math.radians(AZIM_DEG)
    eye = np.array([CAM_DIST * math.cos(el) * math.sin(az),
                    -CAM_DIST * math.cos(el) * math.cos(az),
                    CAM_DIST * math.sin(el)])
    target, up = np.zeros(3), np.array([0.0, 0.0, 1.0])
    f = target - eye
    f /= np.linalg.norm(f)
    s = np.cross(f, up)
    s /= np.linalg.norm(s)
    u = np.cross(s, f)
    view = np.eye(4)
    view[0, :3], view[1, :3], view[2, :3] = s, u, -f
    view[:3, 3] = -view[:3, :3] @ eye
    t = 1.0 / math.tan(math.radians(FOV_DEG) / 2)
    aspect = W / H
    proj = np.zeros((4, 4))
    proj[0, 0], proj[1, 1] = t / aspect, t
    proj[2, 2] = (FAR + NEAR) / (NEAR - FAR)
    proj[2, 3] = 2 * FAR * NEAR / (NEAR - FAR)
    proj[3, 2] = -1.0
    return eye, view, proj


EYE, VIEW, PROJ = camera_matrices()


def project(points: np.ndarray):
    """World points (N, 3) -> pixel [x, y] (N, 2) and eye-space depth in metres (N,)."""
    hom = np.c_[points, np.ones(len(points))]
    eye_pts = hom @ VIEW.T
    clip = eye_pts @ PROJ.T
    ndc = clip[:, :3] / clip[:, 3:4]
    px = np.c_[(ndc[:, 0] + 1) / 2 * W, (1 - ndc[:, 1]) / 2 * H]
    return px, -eye_pts[:, 2]


def background():
    """Per-pixel seg (table 1 / floor 0), depth and RGB from ray-plane intersection."""
    xs, ys = np.meshgrid(np.arange(W) + 0.5, np.arange(H) + 0.5)
    ndc_x, ndc_y = xs / W * 2 - 1, 1 - ys / H * 2
    tan_half = math.tan(math.radians(FOV_DEG) / 2)
    dir_eye = np.stack([ndc_x * tan_half * W / H, ndc_y * tan_half, -np.ones_like(ndc_x)], -1)
    dir_world = dir_eye @ VIEW[:3, :3]  # R^T applied per pixel
    t_table = -EYE[2] / dir_world[..., 2]
    hit = EYE + t_table[..., None] * dir_world
    on_table = (np.abs(hit[..., 0]) <= TABLE_HALF[0]) & (np.abs(hit[..., 1]) <= TABLE_HALF[1])
    t_floor = (-TABLE_TOP_Z_WORLD - EYE[2]) / dir_world[..., 2]
    depth = np.where(on_table, t_table, t_floor).astype(np.float32)  # dir_eye z = -1, so t = eye depth
    seg = np.where(on_table, 1, 0).astype(np.uint8)
    floor_hit = EYE + t_floor[..., None] * dir_world
    checker = ((np.floor(floor_hit[..., 0] / 0.25) + np.floor(floor_hit[..., 1] / 0.25)) % 2).astype(bool)
    rgb = np.where(checker[..., None], FLOOR_RGB[0], FLOOR_RGB[1]).astype(np.uint8)
    grain = (np.sin(hit[..., 0] * 90 + np.sin(hit[..., 1] * 7) * 3) * 6).astype(np.int16)
    table_rgb = np.clip(np.array(TABLE_RGB, np.int16) + grain[..., None], 0, 255).astype(np.uint8)
    rgb[on_table] = table_rgb[on_table]
    return seg, depth, rgb


# ---------------------------------------------------------------- objects

def yaw_matrix(yaw_deg: float):
    c, s = math.cos(math.radians(yaw_deg)), math.sin(math.radians(yaw_deg))
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])


def surface_points(o):
    sx, sy, sz = (v / 100 for v in o["size_cm"])
    if o["shape"] == "box":
        pts = np.array([[x, y, z] for x in (-sx / 2, sx / 2) for y in (-sy / 2, sy / 2) for z in (-sz / 2, sz / 2)])
    elif o["shape"] == "cylinder":
        a = np.linspace(0, 2 * math.pi, 64, endpoint=False)
        ring = np.c_[np.cos(a) * sx / 2, np.sin(a) * sy / 2]
        pts = np.r_[np.c_[ring, np.full(64, -sz / 2)], np.c_[ring, np.full(64, sz / 2)]]
    else:  # sphere: Fibonacci points on the surface
        n = 400
        i = np.arange(n) + 0.5
        phi, theta = np.arccos(1 - 2 * i / n), math.pi * (1 + 5 ** 0.5) * i
        pts = np.c_[np.cos(theta) * np.sin(phi) * sx / 2, np.sin(theta) * np.sin(phi) * sy / 2, np.cos(phi) * sz / 2]
    return pts @ yaw_matrix(o["yaw"]).T + np.array(o["xyz"])


def convex_hull(points):
    pts = sorted(map(tuple, points))
    if len(pts) <= 2:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower, upper = [], []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def silhouette(o):
    px, _ = project(surface_points(o))
    poly = convex_hull(px)
    img = Image.new("L", (W, H), 0)
    ImageDraw.Draw(img).polygon([tuple(p) for p in poly], fill=1)
    return np.array(img, bool), poly


def quat_from_yaw(yaw_deg):
    h = math.radians(yaw_deg) / 2
    return [0.0, 0.0, round(math.sin(h), 6), round(math.cos(h), 6)]


def render(scene_id, seed, objects, contacts, counterfactuals, extra=None, lighting="normal"):
    """objects: dicts with id, shape, color, size_cm, xyz (table frame, m), yaw (deg)."""
    seg, depth, rgb = background()
    centre_depth = {o["id"]: float(project(np.array([o["xyz"]]))[1][0]) for o in objects}
    solo = {}
    polys = {}
    for o in objects:
        solo[o["id"]], polys[o["id"]] = silhouette(o)
    order = sorted(objects, key=lambda o: -centre_depth[o["id"]])  # painter's: far to near
    seg_ids = {o["id"]: i + 2 for i, o in enumerate(objects)}
    img = Image.fromarray(rgb)
    draw = ImageDraw.Draw(img)
    for o in order:
        m = solo[o["id"]]
        seg[m] = seg_ids[o["id"]]
        depth[m] = centre_depth[o["id"]]
        base = np.array(COLORS[o["color"]])
        draw.polygon([tuple(p) for p in polys[o["id"]]], fill=tuple(int(v) for v in base),
                     outline=tuple(int(v) for v in base * 0.55))
    rgb = np.array(img)

    out_objects = []
    for o in objects:
        sid = seg_ids[o["id"]]
        vis = seg == sid
        area, solo_area = int(vis.sum()), int(solo[o["id"]].sum())
        if area:
            ys, xs = np.nonzero(vis)
            bbox = [int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())]
            centroid = [int(round(xs.mean())), int(round(ys.mean()))]
            dmean = round(float(depth[vis].mean()), 4)
        else:
            bbox = centroid = dmean = None
        out_objects.append({
            "id": o["id"], "seg_id": sid, "shape": o["shape"], "color": o["color"],
            "size_cm": o["size_cm"],
            "pose": {"xyz_m": [round(v, 4) for v in o["xyz"]], "quat": quat_from_yaw(o["yaw"])},
            "bbox_2d": bbox, "centroid_2d": centroid, "pixel_area": area,
            "visible_frac": round(area / solo_area, 3), "depth_mean_m": dmean,
        })

    occludes = []
    for a in objects:
        for b in objects:
            if a is b or centre_depth[a["id"]] >= centre_depth[b["id"]]:
                continue  # a must be nearer than b
            overlap = int((solo[a["id"]] & solo[b["id"]]).sum())
            if overlap:
                occludes.append({"front": a["id"], "back": b["id"],
                                 "frac": round(overlap / int(solo[b["id"]].sum()), 3)})

    scene = {
        "scene_id": scene_id, "seed": seed, "generator_version": "fixtures@hand-made-v1",
        "image_path": f"images/{scene_id}.png", "depth_path": f"depth/{scene_id}.npy",
        "seg_path": f"seg/{scene_id}.png", "width": W, "height": H,
        "camera": {"view_matrix": [round(float(v), 6) for v in VIEW.T.flatten()],
                   "proj_matrix": [round(float(v), 6) for v in PROJ.T.flatten()],
                   "elevation_deg": ELEV_DEG, "azimuth_deg": AZIM_DEG},
        "objects": out_objects, "contacts": contacts, "occludes": occludes,
        "counterfactuals": counterfactuals, "pair": None,
        "difficulty": {"n_objects": len(objects),
                       "occlusion": round(max(1 - ob["visible_frac"] for ob in out_objects), 3),
                       "distractors": scene_distractors(out_objects), "lighting": lighting},
    }
    if extra:
        scene.update(extra)
    for sub in ("scenes", "images", "seg", "depth"):
        (OUT / sub).mkdir(exist_ok=True)
    Image.fromarray(rgb).save(OUT / scene["image_path"])
    Image.fromarray(seg, mode="L").save(OUT / scene["seg_path"])
    np.save(OUT / scene["depth_path"], depth.astype(np.float32))
    return scene


def scene_distractors(objs):
    """Most objects that share a colour or a shape with any single object."""
    return max(sum(1 for b in objs if b is not a and (b["color"] == a["color"] or b["shape"] == a["shape"]))
               for a in objs)


def write_scene(scene):
    (OUT / "scenes" / f"{scene['scene_id']}.json").write_text(json.dumps(scene, indent=2) + "\n")


# ---------------------------------------------------------------- scenes

def obj(i, shape, color, size_cm, xy, yaw=0.0, z=None):
    h = size_cm[2] / 100
    return {"id": f"o{i}", "shape": shape, "color": color, "size_cm": size_cm,
            "xyz": [xy[0], xy[1], z if z is not None else h / 2], "yaw": yaw}


def build_scenes():
    scenes = {}

    # s_000001: clutter with a same-colour, same-shape distractor and a fully hidden sphere.
    o7 = obj(7, "box", "purple", [10, 10, 12], (0.04, 0.12), yaw=30)
    away = np.array([-math.sin(math.radians(AZIM_DEG)), math.cos(math.radians(AZIM_DEG))])
    hidden_xy = tuple(np.array([0.04, 0.12]) + away * 0.085)
    objs1 = [
        obj(1, "cylinder", "red", [6, 6, 10], (-0.20, 0.02)),
        obj(2, "box", "blue", [8, 8, 6], (-0.06, 0.03), yaw=15),
        obj(3, "cylinder", "red", [5, 5, 8], (0.20, 0.03)),
        obj(4, "sphere", "green", [8, 8, 8], (0.16, -0.12)),
        obj(5, "box", "green", [5, 5, 5], (-0.22, -0.13), yaw=40),
        obj(6, "sphere", "yellow", [6, 6, 6], (-0.02, -0.14)),
        o7,
        obj(8, "sphere", "orange", [4, 4, 4], (float(hidden_xy[0]), float(hidden_xy[1]))),
    ]
    contacts1 = [{"top": o["id"], "bottom": "table"} for o in objs1]
    scenes["s_000001"] = render("s_000001", 1, objs1, contacts1, {})

    # s_000002: a three-high stack plus free objects (F5-F8).
    objs2 = [
        obj(1, "box", "yellow", [10, 10, 6], (0.0, 0.02)),
        obj(2, "box", "blue", [8, 8, 5], (0.0, 0.02), yaw=20, z=0.06 + 0.025),
        obj(3, "cylinder", "red", [5, 5, 7], (0.0, 0.02), z=0.11 + 0.035),
        obj(4, "sphere", "green", [6, 6, 6], (-0.22, -0.06)),
        obj(5, "cylinder", "purple", [6, 6, 10], (0.22, 0.10)),
        obj(6, "box", "orange", [6, 6, 6], (0.18, -0.13), yaw=10),
    ]
    contacts2 = [{"top": "o1", "bottom": "table"}, {"top": "o2", "bottom": "o1"},
                 {"top": "o3", "bottom": "o2"}, {"top": "o4", "bottom": "table"},
                 {"top": "o5", "bottom": "table"}, {"top": "o6", "bottom": "table"}]
    cf2 = {"o1": {"moved": ["o2", "o3"]}, "o2": {"moved": ["o3"]}}
    scenes["s_000002"] = render("s_000002", 2, objs2, contacts2, cf2, lighting="normal")

    # s_000003: F9 goal scene. Goal was "red box on the green box"; it landed beside it.
    objs3 = [
        obj(1, "box", "green", [10, 10, 5], (-0.04, 0.03)),
        obj(2, "box", "red", [6, 6, 6], (0.09, 0.0), yaw=25),
        obj(3, "sphere", "blue", [7, 7, 7], (-0.20, -0.10)),
        obj(4, "cylinder", "yellow", [5, 5, 12], (0.20, 0.12)),
    ]
    contacts3 = [{"top": o["id"], "bottom": "table"} for o in objs3]
    scenes["s_000003"] = render("s_000003", 3, objs3, contacts3, {},
                                extra={"goal": {"object": "o2", "relation": "on", "target": "o1", "success": False}})

    # s_000004a / s_000004b: F10 before/after pair, same camera; only o3 moves.
    def objs4(o3_xy):
        return [
            obj(1, "cylinder", "blue", [6, 6, 9], (-0.18, 0.05)),
            obj(2, "box", "orange", [7, 7, 7], (0.0, 0.10), yaw=10),
            obj(3, "sphere", "red", [7, 7, 7], o3_xy),
            obj(4, "box", "purple", [8, 8, 5], (0.20, -0.05), yaw=35),
        ]
    pair = {"before": "s_000004a", "after": "s_000004b", "moved": ["o3"]}
    for sid, xy in (("s_000004a", (-0.05, -0.12)), ("s_000004b", (0.08, -0.08))):
        objs = objs4(xy)
        scenes[sid] = render(sid, 4, objs, [{"top": o["id"], "bottom": "table"} for o in objs], {},
                             extra={"pair": pair})
    return scenes


# ---------------------------------------------------------------- tasks

def by_id(scene):
    return {o["id"]: o for o in scene["objects"]}


def name(o):
    return f"{o['color']} {o['shape'] if o['shape'] != 'box' else 'box'}"


def options_text(options, scene):
    objs = by_id(scene)
    parts = [f"{k}) {name(objs[v]) if v in objs else v}" for k, v in options.items()]
    return " Options: " + " ".join(parts)


def task(scene, family, n, question, answer_type, reference, evidence, *, checker=None,
         unique=None, margin=None, targets=(), images=None, distractors=None):
    objs = by_id(scene)
    vis = [objs[t]["visible_frac"] for t in targets]
    for t in targets:
        assert objs[t]["visible_frac"] >= MIN_VISIBLE, (scene["scene_id"], family, t, objs[t]["visible_frac"])
    if distractors is None:
        distractors = scene["difficulty"]["distractors"]
    num = scene["scene_id"][2:8]
    return {
        "task_id": f"t_{num}_{family}_{n:02d}", "scene_id": scene["scene_id"], "family": family,
        "images": images or [scene["image_path"]], "question": question, "answer_type": answer_type,
        "answer_format": ANSWER_FORMAT[answer_type], "reference": reference,
        "checker": checker or CHECKER[answer_type], "evidence": list(evidence),
        "validated": {"unique_referent": unique, "min_visible": min(vis) if vis else None,
                      "margin_ok": margin, "human": None},
        "difficulty": {"n_objects": scene["difficulty"]["n_objects"],
                       "occlusion": scene["difficulty"]["occlusion"], "distractors": distractors},
    }


def target_distractors(scene, oid):
    t = by_id(scene)[oid]
    return sum(1 for o in scene["objects"] if o["id"] != oid and (o["color"] == t["color"] or o["shape"] == t["shape"]))


def matches(scene, color=None, shape=None):
    return [o for o in scene["objects"]
            if (color is None or o["color"] == color) and (shape is None or o["shape"] == shape)]


def build_tasks(sc):
    s1, s2, s3, s4a, s4b = sc["s_000001"], sc["s_000002"], sc["s_000003"], sc["s_000004a"], sc["s_000004b"]
    o1, o2, o3 = by_id(s1), by_id(s2), by_id(s3)
    tasks = []

    # F1: two red cylinders; only one is left of the blue box (image x).
    reds = matches(s1, "red", "cylinder")
    blue_x = o1["o2"]["centroid_2d"][0]
    left = [o for o in reds if o["centroid_2d"][0] < blue_x]
    assert len(reds) == 2 and len(left) == 1, "F1 fixture needs exactly one red cylinder left of the blue box"
    tasks.append(task(s1, "F1", 1, "Point to the red cylinder left of the blue box.", "point",
                      {"seg_ids": [left[0]["seg_id"]]}, [left[0]["id"], "o2"], checker="point_in_mask",
                      unique=True, targets=[left[0]["id"]], distractors=target_distractors(s1, left[0]["id"])))

    # F2: smallest green object (by volume).
    greens = matches(s1, "green")
    vol = lambda o: o["size_cm"][0] * o["size_cm"][1] * o["size_cm"][2]
    small = min(greens, key=vol)
    assert sorted(map(vol, greens))[1] > 2 * vol(small), "size gap must be clear"
    tasks.append(task(s1, "F2", 1, "Draw a box around the smallest green object.", "bbox",
                      {"bbox": small["bbox_2d"], "seg_ids": [small["seg_id"]]}, [o["id"] for o in greens],
                      unique=True, targets=[small["id"]], distractors=target_distractors(s1, small["id"])))

    # F3: spheres you can see. o8 is fully hidden (visible_frac 0) -> not counted; none in (0, 0.10).
    spheres = matches(s1, shape="sphere")
    assert not any(0 < o["visible_frac"] < F3_COUNT_MIN for o in spheres)
    assert any(o["visible_frac"] == 0 for o in spheres), "fixture should include a fully hidden sphere"
    counted = [o for o in spheres if o["visible_frac"] >= F3_COUNT_MIN]
    tasks.append(task(s1, "F3", 1, "How many spheres can you see on the table?", "count",
                      {"count": len(counted)}, [o["id"] for o in spheres],
                      distractors=len(matches(s1, shape="sphere")) - 1))

    # F4: in front of / behind (nearer the camera = in front), with the 3 cm margin.
    def f4(scene, n, a, b):
        objs = by_id(scene)
        gap = objs[a]["depth_mean_m"] - objs[b]["depth_mean_m"]
        assert abs(gap) >= F4_MIN_GAP_M, (a, b, gap)
        opts = {"A": "in front of", "B": "behind"}
        q = f"Is the {name(objs[a])} in front of or behind the {name(objs[b])}?" + options_text(opts, scene)
        return task(scene, "F4", n, q, "choice", {"choice": "A" if gap < 0 else "B", "options": opts}, [a, b],
                    unique=True, margin=True, targets=[a, b])
    tasks.append(f4(s1, 1, "o6", "o2"))
    tasks.append(f4(s1, 2, "o7", "o4"))  # answer "behind", so F4 answers are not all "A"

    # F1 on the stack scene.
    tasks.append(task(s2, "F1", 1, "Point to the purple cylinder.", "point", {"seg_ids": [o2["o5"]["seg_id"]]},
                      ["o5"], checker="point_in_mask", unique=True, targets=["o5"],
                      distractors=target_distractors(s2, "o5")))

    # F5: what is X resting on (from contacts).
    def f5(n, top, opts):
        bottom = next(c["bottom"] for c in s2["contacts"] if c["top"] == top)
        answer = next(k for k, v in opts.items() if v == bottom)
        q = f"What is the {name(o2[top])} resting on?" + options_text(opts, s2)
        return task(s2, "F5", n, q, "choice", {"choice": answer, "options": opts}, [top, bottom],
                    unique=True, targets=[top])
    tasks.append(f5(1, "o2", {"A": "o1", "B": "o3", "C": "table", "D": "o4"}))
    tasks.append(f5(2, "o3", {"A": "o5", "B": "table", "C": "o2", "D": "o1"}))

    # F6: free to pick = nothing rests on it and visible_frac >= 0.30.
    supports = {c["bottom"] for c in s2["contacts"]}
    free = [o for o in s2["objects"] if o["id"] not in supports and o["visible_frac"] >= MIN_VISIBLE]
    tasks.append(task(s2, "F6", 1, "Point to an object you can pick up without moving anything else.", "point",
                      {"seg_ids": [o["seg_id"] for o in free]}, [o["id"] for o in free],
                      checker="point_in_any_mask", targets=[o["id"] for o in free]))

    # F7: clearing order for the stack (top before bottom).
    opts7 = {"A": "o1", "B": "o3", "C": "o2"}
    inv = {v: k for k, v in opts7.items()}
    precedes = [[inv[c["top"]], inv[c["bottom"]]] for c in s2["contacts"]
                if c["top"] in inv and c["bottom"] in inv]
    q7 = "List the stacked objects in an order that clears the stack safely, one at a time." + options_text(opts7, s2)
    tasks.append(task(s2, "F7", 1, q7, "sequence",
                      {"sequence": ["B", "C", "A"], "options": opts7, "precedes": precedes},
                      ["o1", "o2", "o3"]))

    # F8: counterfactual "if X is removed, which objects move?" (labels from scene counterfactuals).
    def f8(n, removed, opts):
        moved = set(s2["counterfactuals"][removed]["moved"])
        ans = sorted(k for k, v in opts.items() if v in moved)
        q = f"If the {name(o2[removed])} is removed, which objects move?" + options_text(opts, s2)
        return task(s2, "F8", n, q, "set", {"set": ans, "options": opts}, [removed, *sorted(moved)],
                    unique=True, targets=[removed])
    tasks.append(f8(1, "o1", {"A": "o2", "B": "o3", "C": "o4", "D": "o5"}))
    tasks.append(f8(2, "o2", {"A": "o1", "B": "o3", "C": "o6"}))

    # F9: placement success from the goal spec.
    g = s3["goal"]
    q9 = f"Goal: put the {name(o3[g['object']])} on the {name(o3[g['target']])}. Did it succeed?"
    tasks.append(task(s3, "F9", 1, q9, "bool", {"bool": g["success"]}, [g["object"], g["target"]],
                      unique=True, targets=[g["object"], g["target"]]))

    # F2 + F3 on the goal scene.
    tasks.append(task(s3, "F2", 1, "Draw a box around the yellow cylinder.", "bbox",
                      {"bbox": o3["o4"]["bbox_2d"], "seg_ids": [o3["o4"]["seg_id"]]}, ["o4"],
                      unique=True, targets=["o4"], distractors=target_distractors(s3, "o4")))
    boxes = matches(s3, shape="box")
    assert all(o["visible_frac"] >= F3_COUNT_MIN for o in boxes)
    tasks.append(task(s3, "F3", 1, "How many boxes can you see?", "count", {"count": len(boxes)},
                      [o["id"] for o in boxes], distractors=len(boxes) - 1))

    # F10: which object moved between the two images.
    opts10 = {"A": "o1", "B": "o2", "C": "o3", "D": "o4"}
    moved = s4a["pair"]["moved"][0]
    q10 = "The first image is before and the second is after. Which object moved?" + options_text(opts10, s4a)
    tasks.append(task(s4a, "F10", 1, q10, "choice",
                      {"choice": next(k for k, v in opts10.items() if v == moved), "options": opts10},
                      [moved], unique=True, targets=[moved],
                      images=[s4a["image_path"], s4b["image_path"]]))
    return tasks


# ---------------------------------------------------------------- fake results

def inner_pixel(seg, seg_id, toward):
    """A pixel of seg_id at least 4 px from the mask edge, nearest to `toward` [x, y]."""
    m = seg == seg_id
    core = m.copy()
    for _ in range(4):
        core = core & np.roll(core, 1, 0) & np.roll(core, -1, 0) & np.roll(core, 1, 1) & np.roll(core, -1, 1)
    ys, xs = np.nonzero(core if core.any() else m)
    i = int(np.argmin((xs - toward[0]) ** 2 + (ys - toward[1]) ** 2))
    return [int(xs[i]), int(ys[i])]


def to_model_point(p):
    return [round(p[1] / H * 1000), round(p[0] / W * 1000)]  # [y, x] 0-1000


def from_model_point(q):
    return [round(q[1] / 1000 * W), round(q[0] / 1000 * H)]  # back to pixel [x, y]


def iou(a, b):
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union else 0.0


def fake_score(task, parsed, seg):
    """Mini scorer for the fixtures only. The real one is validate/checkers.score() (Phase 2)."""
    ref, t = task["reference"], task["answer_type"]
    if t == "point":
        x, y = parsed["point"]
        return 1.0 if 0 <= x < W and 0 <= y < H and int(seg[int(y), int(x)]) in ref["seg_ids"] else 0.0
    if t == "bbox":
        return 1.0 if iou(parsed["bbox"], ref["bbox"]) >= 0.5 else 0.0
    if t == "sequence":
        seq = parsed["sequence"]
        if sorted(seq) != sorted(ref["options"]):
            return 0.0
        return 1.0 if all(seq.index(a) < seq.index(b) for a, b in ref["precedes"]) else 0.0
    if t == "set":
        p, r = set(parsed["set"]), set(ref["set"])
        if not p and not r:
            return 1.0
        tp = len(p & r)
        return round(2 * tp / (len(p) + len(r)), 4)
    return 1.0 if parsed[t] == ref[t] else 0.0


def build_results(tasks, scenes):
    segs = {sid: np.array(Image.open(OUT / s["seg_path"])) for sid, s in scenes.items()}
    rows = []

    def row(t, parsed, raw, condition="image", parse_ok=True, error=None, latency=900):
        score = fake_score(t, parsed, segs[t["scene_id"]]) if parse_ok else 0.0
        rows.append({"task_id": t["task_id"], "model": "mock", "run": 0, "condition": condition,
                     "raw_response": raw, "parsed": parsed if parse_ok else None, "parse_ok": parse_ok,
                     "score": score, "latency_ms": latency, "tokens_in": 1200 if condition == "image" else 150,
                     "tokens_out": 20, "cost_usd": 0.0, "error": error})

    def wrong_letter(t):
        return next(k for k in t["reference"]["options"] if k != t["reference"]["choice"])

    wrong = {"t_000001_F4_02", "t_000002_F5_02", "t_000003_F2_01"}  # mock gets these wrong
    for i, t in enumerate(tasks):
        scene = scenes[t["scene_id"]]
        objs = by_id(scene)
        ref, typ = t["reference"], t["answer_type"]
        latency = 700 + 37 * i
        if t["task_id"] == "t_000002_F6_01":
            row(t, None, "I think the red cylinder on top is easiest to grab.", parse_ok=False,
                error=None, latency=latency)
            continue
        if typ == "point":
            seg_id = ref["seg_ids"][0]
            target = next(o for o in scene["objects"] if o["seg_id"] == seg_id)
            if t["task_id"] == "t_000001_F1_01":  # points at the distractor red cylinder instead
                target = next(o for o in matches(scene, "red", "cylinder") if o["seg_id"] != seg_id)
            pix = inner_pixel(segs[t["scene_id"]], target["seg_id"], target["centroid_2d"])
            q = to_model_point(pix)
            row(t, {"point": from_model_point(q)}, json.dumps({"point": q}), latency=latency)
        elif typ == "bbox":
            b = ref["bbox"]
            if t["task_id"] in wrong:
                other = next(o for o in scene["objects"] if o["bbox_2d"] and iou(o["bbox_2d"], b) == 0)
                b = other["bbox_2d"]
            q = [round(b[1] / H * 1000), round(b[0] / W * 1000), round(b[3] / H * 1000), round(b[2] / W * 1000)]
            back = [round(q[1] / 1000 * W), round(q[0] / 1000 * H), round(q[3] / 1000 * W), round(q[2] / 1000 * H)]
            row(t, {"bbox": back}, json.dumps({"bbox": q}), latency=latency)
        elif typ == "count":
            c = ref["count"] + (1 if t["family"] == "F3" and t["scene_id"] == "s_000001" else 0)
            row(t, {"count": c}, f"I can see {c}. " + json.dumps({"count": c}), latency=latency)
        elif typ == "choice":
            c = wrong_letter(t) if t["task_id"] in wrong else ref["choice"]
            row(t, {"choice": c}, json.dumps({"choice": c}), latency=latency)
        elif typ == "bool":
            row(t, {"bool": ref["bool"]}, json.dumps({"bool": ref["bool"]}), latency=latency)
        elif typ == "sequence":
            row(t, {"sequence": ref["sequence"]}, json.dumps({"sequence": ref["sequence"]}), latency=latency)
        elif typ == "set":
            s = ref["set"][:1] if t["task_id"] == "t_000002_F8_01" else ref["set"]  # partial credit case
            row(t, {"set": s}, json.dumps({"set": s}), latency=latency)

    # text_only condition: no image, so the mock guesses.
    for t in tasks[:5]:
        typ = t["answer_type"]
        guess = {"point": {"point": [W // 2, H // 2]}, "bbox": {"bbox": [400, 300, 600, 450]},
                 "count": {"count": 2}, "choice": {"choice": "A"}}[typ]
        raw = {"point": {"point": [500, 500]}, "bbox": {"bbox": [391, 391, 586, 586]}}.get(typ, guess)
        row(t, guess, json.dumps(raw), condition="text_only", latency=300)
    return rows


def main():
    scenes = build_scenes()
    for s in scenes.values():
        write_scene(s)
    tasks = build_tasks(scenes)
    (OUT / "tasks.jsonl").write_text("".join(json.dumps(t) + "\n" for t in tasks))
    (OUT / "results").mkdir(exist_ok=True)
    rows = build_results(tasks, scenes)
    (OUT / "results" / "mock.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    print(f"{len(scenes)} scene files, {len(tasks)} tasks, {len(rows)} result rows -> {OUT}")
    for s in scenes.values():
        vis = ", ".join(f"{o['id']}:{o['visible_frac']}" for o in s["objects"])
        print(f"  {s['scene_id']}: {vis}")


if __name__ == "__main__":
    main()
