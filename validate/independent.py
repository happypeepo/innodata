"""Independent re-derivation (AGENTS.md: "re-derives answers from the raw seg/depth arrays without reading
A's contacts/relations and flags any disagreement"). This is our "verified independently" evidence.

What it reads per scene: the seg PNG, the depth .npy, the camera matrices, and each object's identity
(id, seg_id, colour, shape, size_cm). It never reads contacts, occludes, counterfactuals, goal or pair.moved, and
it recomputes 2D fields (area, bbox, centroid, depth) from pixels instead of trusting A's values.

Per family:
  F1  target has pixels; a left/right relation holds on pixel centroids
  F2  same as F1, plus the reference box matches the box of the target's seg pixels (IoU >= 0.9)
  F3  count = matching objects with at least MIN_COUNT_PX visible pixels
  F4  front/behind from the median depth over each object's pixels
  F5  support from 3D points (depth + camera matrices): X rests on Y if X's lowest visible
      points sit on Y's highest points and overlap it in x/y. If X's bottom is hidden, its bottom
      is estimated as its highest visible point minus its height (size_cm, not contacts)
  F6  free = no object rests on it in that 3D support graph
  F7  "rests on" pairs among the options from the same 3D support graph
  F10 the object whose mask changed most between the before and after seg images
  F8, F9 are physics outcomes and can't be recomputed from one image: reported as not checkable.

Each task is "agree", "disagree" or "undetermined" (e.g. an object's bottom is hidden, so its
support can't be seen). Agreement % = agree / (agree + disagree).

    python -m validate.independent --data data/ --tasks data/tasks.jsonl
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image

from validate.checkers import iou

MIN_COUNT_PX = 50          # my take: an object needs this many visible pixels to be countable
BBOX_IOU_AGREE = 0.9       # my take: reference box vs pixel box
SUPPORT_TOL_M = 0.02       # my take: X's lowest point within 2 cm of Y's top counts as "resting on"
OVERLAP_MARGIN_M = 0.01    # my take: X's footprint centre must lie inside Y's footprint (+1 cm)
LOW_Q, HIGH_Q = 2, 98      # percentiles used as "lowest" / "highest" visible points (robust to edge noise)
NOT_CHECKABLE = {
    "F8": "physics outcome (what moves after removal) can't be recomputed from one image",
    "F9": "placement success is a physics outcome of a scripted placement, not visible geometry alone",
}


# ---------------------------------------------------------------- per-scene geometry

@lru_cache(maxsize=32)
def _arrays(data_dir: str, seg_path: str, depth_path: str):
    seg = np.array(Image.open(Path(data_dir) / seg_path))
    depth = np.load(Path(data_dir) / depth_path).astype(np.float64)
    return seg, depth


def _mat(flat):
    return np.array(flat, dtype=np.float64).reshape(4, 4).T  # stored column-major (OpenGL / PyBullet)


class SceneGeometry:
    """Everything derived from raw pixels for one scene."""

    def __init__(self, scene: dict, data_dir):
        self.scene = scene
        self.seg, self.depth = _arrays(str(data_dir), scene["seg_path"], scene["depth_path"])
        self.h, self.w = self.seg.shape
        self.ids = {o["seg_id"]: o["id"] for o in scene["objects"]}
        self.meta = {o["id"]: o for o in scene["objects"]}  # identity only: colour, shape, seg_id
        self._points = {}

    def mask(self, oid):
        return self.seg == self.meta[oid]["seg_id"]

    def area(self, oid):
        return int(self.mask(oid).sum())

    def bbox(self, oid):
        ys, xs = np.nonzero(self.mask(oid))
        return [int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())] if len(xs) else None

    def centroid(self, oid):
        ys, xs = np.nonzero(self.mask(oid))
        return [float(xs.mean()), float(ys.mean())] if len(xs) else None

    def median_depth(self, oid):
        m = self.mask(oid)
        return float(np.median(self.depth[m])) if m.any() else None

    def points(self, key):
        """World-space 3D points for an object's pixels (key = object id) or the table (key = "table")."""
        if key not in self._points:
            m = (self.seg == 1) if key == "table" else self.mask(key)
            ys, xs = np.nonzero(m)
            if not len(xs):
                self._points[key] = np.zeros((0, 3))
            else:
                view, proj = _mat(self.scene["camera"]["view_matrix"]), _mat(self.scene["camera"]["proj_matrix"])
                d = self.depth[ys, xs]
                ndc_x = (xs + 0.5) / self.w * 2 - 1
                ndc_y = 1 - (ys + 0.5) / self.h * 2
                eye = np.stack([ndc_x * d / proj[0, 0], ndc_y * d / proj[1, 1], -d, np.ones_like(d)], 1)
                self._points[key] = (eye @ np.linalg.inv(view).T)[:, :3]
        return self._points[key]

    def table_z(self):
        p = self.points("table")
        return float(np.median(p[:, 2])) if len(p) else None

    def support_graph(self):
        """{top: bottom or "table" or None (bottom hidden / nothing found)} from 3D points alone."""
        table_z = self.table_z()
        stats = {}
        for oid in self.meta:
            p = self.points(oid)
            if len(p) < 20:
                continue
            stats[oid] = {"low": np.percentile(p[:, 2], LOW_Q), "high": np.percentile(p[:, 2], HIGH_Q),
                          "xy": p[:, :2].mean(0), "xmin": p[:, 0].min(), "xmax": p[:, 0].max(),
                          "ymin": p[:, 1].min(), "ymax": p[:, 1].max()}
        def supporters(x, bottom):
            if table_z is not None and abs(bottom - table_z) <= SUPPORT_TOL_M:
                return ["table"]
            sx = stats[x]
            return [y for y, sy in stats.items() if y != x
                    and abs(bottom - sy["high"]) <= SUPPORT_TOL_M
                    and sy["xmin"] - OVERLAP_MARGIN_M <= sx["xy"][0] <= sy["xmax"] + OVERLAP_MARGIN_M
                    and sy["ymin"] - OVERLAP_MARGIN_M <= sx["xy"][1] <= sy["ymax"] + OVERLAP_MARGIN_M]

        graph = {}
        for x, sx in stats.items():
            under = supporters(x, sx["low"])
            if not under:
                # The bottom is often hidden (e.g. a sphere seen from above). Fall back to the
                # highest visible point minus the object's height, from its size in scene.json.
                under = supporters(x, sx["high"] - self.meta[x]["size_cm"][2] / 100)
            graph[x] = under[0] if len(under) == 1 else None
        return graph


# ---------------------------------------------------------------- per-family checks

def _verdict(ok, reason=""):
    return ("agree" if ok else "disagree"), reason


REL_RE = re.compile(r"\b(left of|right of)\b")


def check_task(task, scene, geo: SceneGeometry, scenes, data_dir):
    """Returns (verdict, reason) with verdict in agree / disagree / undetermined / not_checkable."""
    fam, ref = task["family"], task["reference"]
    if fam in NOT_CHECKABLE:
        return "not_checkable", NOT_CHECKABLE[fam]

    if fam in ("F1", "F2"):
        target = geo.ids.get(ref["seg_ids"][0])
        if target is None or geo.area(target) == 0:
            return _verdict(False, "target has no pixels in the seg image")
        rel = REL_RE.search(task["question"])
        if rel and len(task["evidence"]) >= 2:
            anchor = task["evidence"][1]
            tx, ax = geo.centroid(target)[0], (geo.centroid(anchor) or [None])[0]
            if ax is None:
                return _verdict(False, "anchor has no pixels")
            if (tx < ax) != (rel.group(1) == "left of"):
                return _verdict(False, f"pixel centroids say the target is not {rel.group(1)} the anchor")
        if fam == "F1":
            return _verdict(True)
        score = iou(ref["bbox"], geo.bbox(target))
        return _verdict(score >= BBOX_IOU_AGREE, f"IoU with pixel box {score:.2f}")

    if fam == "F3":
        matching = task["evidence"]
        n = sum(geo.area(o) >= MIN_COUNT_PX for o in matching if o in geo.meta)
        return _verdict(n == ref["count"], f"pixel count gives {n}, reference {ref['count']}")

    if fam == "F4":
        a, b = task["evidence"][0], task["evidence"][-1]
        da, db = geo.median_depth(a), geo.median_depth(b)
        if da is None or db is None:
            return "undetermined", "an object has no pixels"
        truth = "in front of" if da < db else "behind"
        return _verdict(ref["options"][ref["choice"]] == truth, f"median depths {da:.3f} vs {db:.3f} m")

    if fam in ("F5", "F6", "F7"):
        graph = geo.support_graph()
        if fam == "F5":
            subject = task["evidence"][0]
            found = graph.get(subject)
            if found is None:
                return "undetermined", "bottom of the object not visible enough to find its support"
            return _verdict(ref["options"][ref["choice"]] == found, f"3D points say it rests on {found}")
        if fam == "F6":
            if any(v is None for k, v in graph.items() if geo.area(k) >= MIN_COUNT_PX):
                return "undetermined", "some object's support can't be seen"
            supporting = {v for v in graph.values() if v not in (None, "table")}
            free_ref = {geo.ids[s] for s in ref["seg_ids"]}
            wrong = free_ref & supporting
            return _verdict(not wrong, f"3D points show something resting on {sorted(wrong)}" if wrong else "")
        ids = set(ref["options"].values())
        if any(graph.get(o) is None for o in ids):
            return "undetermined", "an option's support can't be seen"
        letter = {v: k for k, v in ref["options"].items()}
        derived = {(letter[t], letter[b]) for t, b in graph.items() if t in ids and b in ids}
        return _verdict(derived == {tuple(p) for p in ref["precedes"]}, f"3D points give {sorted(derived)}")

    if fam == "F10":
        pair = scene.get("pair") or {}
        after = scenes.get(pair.get("after"))
        if after is None:
            return "undetermined", "after-scene missing"
        geo_after = SceneGeometry(after, data_dir)
        change = {}
        for oid in geo.meta:
            if oid not in geo_after.meta:
                continue
            m1, m2 = geo.mask(oid), geo_after.mask(oid)
            union = (m1 | m2).sum()
            change[oid] = 1 - (m1 & m2).sum() / union if union else 0
        moved = max(change, key=change.get)
        return _verdict(ref["options"][ref["choice"]] == moved, f"largest mask change: {moved}")

    return "undetermined", f"no independent check for {fam}"


def check_scene_fields(scene, geo: SceneGeometry):
    """A's per-object 2D fields vs the same values recomputed from pixels. Catches generator bugs."""
    problems = []
    for o in scene["objects"]:
        area = geo.area(o["id"])
        if area != o["pixel_area"]:
            problems.append(f"{scene['scene_id']} {o['id']}: pixel_area {o['pixel_area']} vs {area} from seg")
        if area == 0:
            continue
        if o["bbox_2d"] != geo.bbox(o["id"]):
            problems.append(f"{scene['scene_id']} {o['id']}: bbox_2d differs from seg")
        cx, cy = geo.centroid(o["id"])
        if o["centroid_2d"] and (abs(o["centroid_2d"][0] - cx) > 1 or abs(o["centroid_2d"][1] - cy) > 1):
            problems.append(f"{scene['scene_id']} {o['id']}: centroid_2d off by more than 1 px")
        mean_d = float(geo.depth[geo.mask(o["id"])].mean())
        if o["depth_mean_m"] is not None and abs(o["depth_mean_m"] - mean_d) > 0.01:
            problems.append(f"{scene['scene_id']} {o['id']}: depth_mean_m off by more than 1 cm")
    return problems


# ---------------------------------------------------------------- run over a task set

def run(tasks, scenes: dict, data_dir) -> dict:
    """Independent-check summary for the validation report."""
    geos = {sid: SceneGeometry(s, data_dir) for sid, s in scenes.items()}
    per = defaultdict(Counter)
    disagreements = []
    for t in tasks:
        verdict, reason = check_task(t, scenes[t["scene_id"]], geos[t["scene_id"]], scenes, data_dir)
        per[t["family"]][verdict] += 1
        if verdict == "disagree":
            disagreements.append({"task_id": t["task_id"], "reason": reason})
    per_family = {}
    for fam in sorted(per, key=lambda f: int(f[1:])):
        c = per[fam]
        if fam in NOT_CHECKABLE:
            per_family[fam] = {"status": "not checkable", "reason": NOT_CHECKABLE[fam], "tasks": sum(c.values())}
            continue
        decided = c["agree"] + c["disagree"]
        per_family[fam] = {"agree": c["agree"], "disagree": c["disagree"], "undetermined": c["undetermined"],
                           "agreement_pct": round(100 * c["agree"] / decided, 1) if decided else None}
    agree = sum(v.get("agree", 0) for v in per_family.values())
    decided = agree + sum(v.get("disagree", 0) for v in per_family.values())
    field_problems = [p for sid, s in scenes.items() for p in check_scene_fields(s, geos[sid])]
    n_objects = sum(len(s["objects"]) for s in scenes.values())
    return {
        "status": "run",
        "agreement_pct": round(100 * agree / decided, 1) if decided else None,
        "decided_tasks": decided,
        "per_family": per_family,
        "scene_fields": {"objects_checked": n_objects, "problems": len(field_problems),
                         "examples": field_problems[:20]},
        "disagreements": disagreements[:50],
        "method": "seg PNG + depth .npy + camera matrices only; never reads contacts, occludes, "
                  "counterfactuals, goal or pair.moved",
    }


def main(argv=None):
    from tasks.io import load_scenes

    ap = argparse.ArgumentParser(description="Independently re-derive task answers from seg/depth")
    ap.add_argument("--data", type=Path, required=True)
    ap.add_argument("--tasks", type=Path, required=True)
    args = ap.parse_args(argv)
    scenes = {s["scene_id"]: s for s in load_scenes(args.data)}
    tasks = [json.loads(line) for line in args.tasks.read_text().splitlines() if line.strip()]
    result = run(tasks, scenes, args.data)
    print(json.dumps({k: result[k] for k in ("agreement_pct", "decided_tasks", "per_family", "scene_fields")},
                     indent=2))
    for d in result["disagreements"]:
        print(f"  DISAGREE {d['task_id']}: {d['reason']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
