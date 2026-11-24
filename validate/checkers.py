"""Deterministic checkers for RoboSpatial-Synth (Role B). C imports `score()`.

    from validate.checkers import load_scene, score
    scene = load_scene("data/", task["scene_id"])
    s = score(task, parsed, scene)          # 0.0 .. 1.0

Contract (AGENTS.md):
- `parsed` is already in storage format: points `[x, y]` pixels, boxes `[x1, y1, x2, y2]` pixels.
- `parsed` and `task["reference"]` use the same key per `answer_type`
  (point, bbox, count, choice, bool, sequence, set).
- choice / sequence / set answers are option letters.
- F8 (`set_f1`) returns the raw F1 as partial credit; headline accuracy counts only score == 1.0.

Failure policy:
- A bad *model answer* (missing key, wrong type, point off the image, unknown letter) scores 0.0.
  It never raises, so one odd response can't stop C's run.
- A bad *task or scene* (unknown checker, missing seg file) raises, because that's our bug and
  must not be hidden as a wrong answer.
"""

from __future__ import annotations

import json
import math
from functools import lru_cache
from numbers import Real
from pathlib import Path

import numpy as np
from PIL import Image

BBOX_IOU_PASS = 0.5  # AGENTS.md: iou >= 0.5


# ---------------------------------------------------------------- loading

def load_scene(data_dir, scene_id: str) -> dict:
    """Read <data_dir>/scenes/<scene_id>.json and remember where it came from,
    so mask checkers can find the seg PNG (paths in scene.json are relative to the data folder)."""
    data_dir = Path(data_dir)
    scene = json.loads((data_dir / "scenes" / f"{scene_id}.json").read_text())
    scene["_data_dir"] = str(data_dir)
    return scene


@lru_cache(maxsize=64)
def _read_seg(path: str) -> np.ndarray:
    return np.array(Image.open(path))


def seg_array(scene: dict) -> np.ndarray:
    """The scene's seg mask as an (H, W) array; pixel value = seg_id (0 nothing/floor, 1 table)."""
    if "_seg" in scene:
        return scene["_seg"]
    path = Path(scene.get("_data_dir", ".")) / scene["seg_path"]
    if not path.is_file():
        raise FileNotFoundError(f"seg mask not found: {path} (use load_scene(data_dir, scene_id))")
    return _read_seg(str(path.resolve()))


# ---------------------------------------------------------------- pure checkers

def _is_num(v) -> bool:
    return isinstance(v, Real) and not isinstance(v, bool) and math.isfinite(v)


def point_in_mask(point, seg: np.ndarray, seg_ids) -> float:
    """1.0 if pixel [x, y] lands on any of seg_ids. numpy indexes row (y) first."""
    if not (isinstance(point, (list, tuple)) and len(point) == 2 and all(map(_is_num, point))):
        return 0.0
    x, y = int(math.floor(point[0])), int(math.floor(point[1]))
    h, w = seg.shape[:2]
    if not (0 <= x < w and 0 <= y < h):
        return 0.0
    return 1.0 if int(seg[y, x]) in set(seg_ids) else 0.0


def point_in_any_mask(point, seg: np.ndarray, seg_ids) -> float:
    """F6: any of several valid objects counts. Same test as point_in_mask, named for clarity."""
    return point_in_mask(point, seg, seg_ids)


def iou(a, b) -> float:
    """Intersection over union of two [x1, y1, x2, y2] boxes. Corners may come in either order."""
    ax1, ax2 = sorted((a[0], a[2]))
    ay1, ay2 = sorted((a[1], a[3]))
    bx1, bx2 = sorted((b[0], b[2]))
    by1, by2 = sorted((b[1], b[3]))
    iw = max(0.0, min(ax2, bx2) - max(ax1, bx1))
    ih = max(0.0, min(ay2, by2) - max(ay1, by1))
    inter = iw * ih
    union = (ax2 - ax1) * (ay2 - ay1) + (bx2 - bx1) * (by2 - by1) - inter
    return inter / union if union > 0 else 0.0


def bbox_iou(pred, ref, threshold: float = BBOX_IOU_PASS) -> float:
    """1.0 if IoU(pred, ref) >= threshold, else 0.0."""
    if not (isinstance(pred, (list, tuple)) and len(pred) == 4 and all(map(_is_num, pred))):
        return 0.0
    return 1.0 if iou(pred, ref) >= threshold else 0.0


def _norm(v):
    """Normalise for exact matching: letters/words ignore case and spaces; whole numbers compare as ints."""
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return " ".join(v.split()).upper()
    if _is_num(v) and float(v).is_integer():
        return int(v)
    return v


def exact(pred, ref) -> float:
    """count / choice / bool. A bool never equals a number (True is not 1 here)."""
    if isinstance(pred, bool) != isinstance(ref, bool):
        return 0.0
    if pred is None or isinstance(pred, (list, dict)):
        return 0.0
    return 1.0 if _norm(pred) == _norm(ref) else 0.0


def _letters(v):
    if not isinstance(v, (list, tuple)) or not all(isinstance(x, str) for x in v):
        return None
    return [_norm(x) for x in v]


def topo_order_valid(seq, options, precedes) -> float:
    """F7: every option letter exactly once, and for each [a, b] in precedes, a comes before b."""
    seq = _letters(seq)
    if seq is None:
        return 0.0
    letters = sorted(_norm(k) for k in options)
    if sorted(seq) != letters:  # missing, extra or repeated letters
        return 0.0
    pos = {x: i for i, x in enumerate(seq)}
    return 1.0 if all(pos[_norm(a)] < pos[_norm(b)] for a, b in precedes) else 0.0


def set_f1(pred, ref) -> float:
    """F8: F1 between predicted and true letter sets. Both empty = 1.0. Duplicates are ignored."""
    p = _letters(pred)
    if p is None:
        return 0.0
    p, r = set(p), {_norm(x) for x in ref}
    if not p and not r:
        return 1.0
    tp = len(p & r)
    if tp == 0:
        return 0.0
    precision, recall = tp / len(p), tp / len(r)
    return 2 * precision * recall / (precision + recall)


# ---------------------------------------------------------------- dispatcher

def score(task: dict, parsed: dict, scene: dict) -> float:
    """Score one parsed model answer. 0.0..1.0, coords already in pixels."""
    if not isinstance(parsed, dict):
        return 0.0
    checker, ref, key = task["checker"], task["reference"], task["answer_type"]
    if key not in parsed:
        return 0.0
    ans = parsed[key]

    if checker in ("point_in_mask", "point_in_any_mask"):
        return point_in_mask(ans, seg_array(scene), ref["seg_ids"])
    if checker == "bbox_iou":
        return bbox_iou(ans, ref["bbox"])
    if checker == "exact":
        return exact(ans, ref[key])
    if checker == "topo_order_valid":
        return topo_order_valid(ans, ref["options"], ref["precedes"])
    if checker == "set_f1":
        return set_f1(ans, ref["set"])
    raise ValueError(f"unknown checker {checker!r} in task {task.get('task_id')}")
