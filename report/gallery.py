"""Failure gallery: wrong answers drawn on the scene image next to the reference, grouped by failure type.

Only `image`-condition rows of the first run with score < 1.0 are shown. Geometry answers (point, bbox)
are classified from the seg mask; the rest from the answer itself:

    point   wrong referent (lands on another object) | off-object (table, floor, off the image)
    bbox    wrong referent (box fits another object better) | off-object (fits nothing)
    count   over-count (hallucinated) | under-count (missed under occlusion)
    F4      relation flip            F5  wrong support        F7  unsafe order
    F8      wrong fallout set        F9  false success | false failure
    F10     wrong change             any parse failure: unparseable
"""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from validate.checkers import iou, load_scene, seg_array

THUMB_W = 480
REF = (27, 175, 122)       # reference: aqua
MODEL = (235, 104, 52)     # model answer: orange
RING = (255, 255, 255)

ORDER = ["wrong referent", "off-object", "relation flip", "over-count (hallucinated)",
         "under-count (missed under occlusion)", "wrong support", "unsafe order", "wrong fallout set",
         "false success", "false failure", "wrong change", "other wrong answer", "unparseable"]


def classify(task: dict, row: dict, scene: dict) -> str:
    p, ref, fam = row.get("parsed") or {}, task["reference"], task["family"]
    if not row["parse_ok"]:
        return "unparseable"
    if task["answer_type"] == "point" and "point" in p:
        seg = seg_array(scene)
        x, y = (int(round(v)) for v in p["point"])
        if not (0 <= x < seg.shape[1] and 0 <= y < seg.shape[0]) or seg[y, x] < 2:
            return "off-object"
        return "wrong referent" if seg[y, x] not in ref["seg_ids"] else "other wrong answer"
    if task["answer_type"] == "bbox" and "bbox" in p:
        fits = [(iou(p["bbox"], o["bbox_2d"]), o["seg_id"]) for o in scene["objects"] if o.get("bbox_2d")]
        best, seg_id = max(fits, default=(0.0, None))
        return "wrong referent" if best >= 0.5 and seg_id not in ref.get("seg_ids", []) else "off-object"
    if task["answer_type"] == "count" and "count" in p:
        return "over-count (hallucinated)" if p["count"] > ref["count"] else "under-count (missed under occlusion)"
    if fam == "F9" and "bool" in p:
        return "false success" if p["bool"] else "false failure"
    return {"F4": "relation flip", "F5": "wrong support", "F7": "unsafe order", "F8": "wrong fallout set",
            "F10": "wrong change"}.get(fam, "other wrong answer")


def _outline(mask: np.ndarray) -> np.ndarray:
    inner = mask.copy()
    inner[1:, :] &= mask[:-1, :]
    inner[:-1, :] &= mask[1:, :]
    inner[:, 1:] &= mask[:, :-1]
    inner[:, :-1] &= mask[:, 1:]
    return mask & ~inner


def _label(o: dict) -> str:
    return f"{o['id']} {o['color']} {o['shape']}"


def draw(task: dict, row: dict, scene: dict, data: Path) -> Image.Image:
    """The task's last image with the reference (aqua) and the model's answer (orange) drawn on it."""
    img = Image.open(data / task["images"][-1]).convert("RGB")
    arr = np.asarray(img).copy()
    ref, p = task["reference"], row.get("parsed") or {}
    objs = {o["id"]: o for o in scene["objects"]}
    if ref.get("seg_ids"):
        mask = np.isin(seg_array(scene), ref["seg_ids"])
        arr[mask] = (0.45 * arr[mask] + 0.55 * np.array(REF)).astype(np.uint8)
        edge = _outline(mask)
        for dy in range(-2, 3):   # thick outline, still visible after the thumbnail shrink
            for dx in range(-2, 3):
                arr[np.roll(edge, (dy, dx), axis=(0, 1))] = REF
    img = Image.fromarray(arr)
    d = ImageDraw.Draw(img)
    s = 2 * max(img.width / 1024, 0.5)   # marks are drawn at full size, then shrunk ~2x
    if "bbox" in ref:
        d.rectangle(ref["bbox"], outline=REF, width=round(4 * s))
    if not ref.get("seg_ids"):   # non-geometric: mark the objects the question is about
        for oid in task.get("evidence", []):
            o = objs.get(oid)
            if o and o.get("bbox_2d"):
                d.rectangle(o["bbox_2d"], outline=REF, width=round(3 * s))
                d.text((o["bbox_2d"][0] + 4, o["bbox_2d"][1] + 2), oid, fill=RING, font=ImageFont.load_default(14 * s),
                       stroke_width=2, stroke_fill=(0, 0, 0))
    if "point" in p:
        x, y = p["point"]
        r = 9 * s
        d.ellipse([x - r - 3, y - r - 3, x + r + 3, y + r + 3], fill=RING)
        d.ellipse([x - r, y - r, x + r, y + r], fill=MODEL)
    if "bbox" in p:
        d.rectangle(p["bbox"], outline=RING, width=round(7 * s))
        d.rectangle(p["bbox"], outline=MODEL, width=round(4 * s))
    img.thumbnail((THUMB_W, THUMB_W))
    return img


def describe(task: dict, row: dict, scene: dict) -> tuple[str, str]:
    """(model answer, reference) as short text, with option letters spelled out."""
    ref, p = task["reference"], row.get("parsed")
    objs = {o["id"]: o for o in scene["objects"]}
    opts = ref.get("options", {})
    name = lambda letter: f"{letter} ({_label(objs[opts[letter]]) if opts.get(letter) in objs else opts.get(letter, '?')})"
    if p is None:
        return f"unparseable: {str(row.get('raw_response'))[:80]!r}", _short(ref, name, objs)
    return _short(p, name, objs), _short(ref, name, objs)


def _short(ans: dict, name, objs: dict) -> str:
    for key in ("choice", "sequence", "set", "count", "bool", "point", "bbox"):
        if key in ans:
            v = ans[key]
            if key == "choice":
                return name(v)
            if key in ("sequence", "set"):
                return ", ".join(name(x) for x in v) or "(empty)"
            if key == "point":
                return f"point at ({v[0]:.0f}, {v[1]:.0f}) px"
            if key == "bbox":
                return "box " + ", ".join(f"{c:.0f}" for c in v) + " px"
            return str(v).lower()
    by_seg = {o["seg_id"]: o for o in objs.values()}
    targets = [_label(by_seg[i]) for i in ans.get("seg_ids", []) if i in by_seg]
    return (" or ".join(targets) + " (aqua)") if targets else "?"


def build(rows: list[dict], tasks: dict, data: Path, out: Path, per_type: int = 6) -> tuple[list[dict], list[dict]]:
    """Write thumbnails to out/gallery/. Returns (gallery entries in type order, counts per model x type)."""
    wrong = sorted((r for r in rows if r["condition"] == "image" and r["run"] == min(x["run"] for x in rows)
                    and (r["score"] or 0) < 1.0 and r["task_id"] in tasks),
                   key=lambda r: (r["model"], r["task_id"]))
    by_type, scenes = defaultdict(list), {}
    for r in wrong:
        t = tasks[r["task_id"]]
        if t["scene_id"] not in scenes:
            scenes[t["scene_id"]] = load_scene(data, t["scene_id"])
        by_type[classify(t, r, scenes[t["scene_id"]])].append(r)

    (out / "gallery").mkdir(parents=True, exist_ok=True)
    entries = []
    for kind in sorted(by_type, key=lambda k: ORDER.index(k) if k in ORDER else len(ORDER)):
        for r in by_type[kind][:per_type]:
            t, scene = tasks[r["task_id"]], scenes[tasks[r["task_id"]]["scene_id"]]
            path = f"gallery/{r['task_id']}__{r['model']}.jpg"
            draw(t, r, scene, data).save(out / path, quality=85)
            model_ans, ref_ans = describe(t, r, scene)
            entries.append({"type": kind, "count": len(by_type[kind]), "task_id": r["task_id"], "model": r["model"],
                            "family": t["family"], "question": t["question"], "answer": model_ans,
                            "reference": ref_ans, "score": r["score"], "image": path})
    counts = defaultdict(int)
    for kind, rs in by_type.items():
        for r in rs:
            counts[(r["model"], kind)] += 1
    return entries, [{"model": m, "type": k, "n": n} for (m, k), n in counts.items()]
