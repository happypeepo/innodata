"""Validate a data folder (fixtures/ or data/) against the v1 contracts.

    python -m schema.check --data fixtures/
    python -m schema.check --data data/

Checks every scenes/*.json, tasks.jsonl and results/*.jsonl against schema/*.schema.json,
then the cross-file rules a JSON Schema can't express (files exist, seg ids match the PNG,
hidden objects have nulls, ids referenced by contacts/tasks/results exist).
Exit code 0 = all good, 1 = problems (each printed on its own line).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from jsonschema import Draft202012Validator
from PIL import Image

SCHEMA_DIR = Path(__file__).resolve().parent


def load_validator(name: str) -> Draft202012Validator:
    return Draft202012Validator(json.loads((SCHEMA_DIR / f"{name}.schema.json").read_text()))


def schema_errors(validator, doc, where):
    return [f"{where}: {'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.message}"
            for e in sorted(validator.iter_errors(doc), key=lambda e: list(e.absolute_path))]


def read_jsonl(path: Path):
    for n, line in enumerate(path.read_text().splitlines(), 1):
        if line.strip():
            yield n, json.loads(line)


def check_scene(scene, data: Path, where: str):
    errs = []
    for key in ("image_path", "depth_path", "seg_path"):
        if not (data / scene[key]).is_file():
            errs.append(f"{where}: {key} {scene[key]} not found under {data}")
    if errs:
        return errs
    w, h = scene["width"], scene["height"]
    rgb = Image.open(data / scene["image_path"])
    if rgb.size != (w, h):
        errs.append(f"{where}: image is {rgb.size}, expected {(w, h)}")
    seg_img = Image.open(data / scene["seg_path"])
    if seg_img.mode != "L":
        errs.append(f"{where}: seg PNG mode {seg_img.mode}, expected 8-bit 'L'")
    seg = np.array(seg_img)
    if seg.shape != (h, w):
        errs.append(f"{where}: seg is {seg.shape[::-1]}, expected {(w, h)}")
    depth = np.load(data / scene["depth_path"])
    if depth.shape != (h, w) or not np.issubdtype(depth.dtype, np.floating):
        errs.append(f"{where}: depth is {depth.dtype}{depth.shape}, expected float ({h}, {w})")
    elif not np.isfinite(depth).all() or (depth <= 0).any():
        errs.append(f"{where}: depth has non-finite or non-positive values")

    ids = [o["id"] for o in scene["objects"]]
    seg_ids = [o["seg_id"] for o in scene["objects"]]
    if len(set(ids)) != len(ids):
        errs.append(f"{where}: duplicate object ids")
    if len(set(seg_ids)) != len(seg_ids):
        errs.append(f"{where}: duplicate seg_ids")
    extra = set(np.unique(seg).tolist()) - {0, 1} - set(seg_ids)
    if extra:
        errs.append(f"{where}: seg PNG has values {sorted(extra)} that match no object")
    for o in scene["objects"]:
        area = int((seg == o["seg_id"]).sum())
        if area != o["pixel_area"]:
            errs.append(f"{where}: {o['id']} pixel_area {o['pixel_area']} but seg PNG has {area}")
        hidden = o["visible_frac"] == 0
        nulls = [o["bbox_2d"] is None, o["centroid_2d"] is None, o["depth_mean_m"] is None]
        if hidden and (o["pixel_area"] != 0 or not all(nulls)):
            errs.append(f"{where}: {o['id']} is fully hidden, so pixel_area must be 0 and bbox_2d/centroid_2d/depth_mean_m null")
        if not hidden and any(nulls):
            errs.append(f"{where}: {o['id']} is visible but has null bbox_2d/centroid_2d/depth_mean_m")

    known = set(ids)
    for c in scene["contacts"]:
        for k in ("top", "bottom"):
            if c[k] != "table" and c[k] not in known:
                errs.append(f"{where}: contacts names unknown object {c[k]}")
    for oc in scene["occludes"]:
        for k in ("front", "back"):
            if oc[k] not in known:
                errs.append(f"{where}: occludes names unknown object {oc[k]}")
    for k, v in scene["counterfactuals"].items():
        for oid in [k, *v["moved"]]:
            if oid not in known:
                errs.append(f"{where}: counterfactuals names unknown object {oid}")
    if scene.get("goal"):
        for k in ("object", "target"):
            if scene["goal"][k] != "table" and scene["goal"][k] not in known:
                errs.append(f"{where}: goal names unknown object {scene['goal'][k]}")
    if scene["pair"] and scene["scene_id"] not in (scene["pair"]["before"], scene["pair"]["after"]):
        errs.append(f"{where}: pair does not include this scene_id")
    return errs


def check_task(task, scenes, data: Path, where: str):
    errs = []
    scene = scenes.get(task["scene_id"])
    if scene is None:
        return [f"{where}: scene_id {task['scene_id']} has no scenes/{task['scene_id']}.json"]
    if task["task_id"][2:8] != task["scene_id"][2:8]:
        errs.append(f"{where}: task_id number does not match scene_id")
    for img in task["images"]:
        if not (data / img).is_file():
            errs.append(f"{where}: image {img} not found under {data}")
    objs = {o["id"]: o for o in scene["objects"]}
    seg_ids = {o["seg_id"] for o in scene["objects"]}
    for e in task["evidence"]:
        if e != "table" and e not in objs:
            errs.append(f"{where}: evidence names unknown object {e}")
    ref = task["reference"]
    for s in ref.get("seg_ids", []):
        if s not in seg_ids:
            errs.append(f"{where}: reference seg_id {s} is not an object in {task['scene_id']}")
    opts = ref.get("options", {})
    for letter, target in opts.items():
        if target in objs or target == "table":
            continue
        if task["answer_type"] != "choice":
            errs.append(f"{where}: option {letter} -> {target} is not an object id or 'table'")
    letters = set(opts)
    used = ([ref["choice"]] if "choice" in ref else []) + ref.get("sequence", []) + ref.get("set", []) \
        + [x for pair in ref.get("precedes", []) for x in pair]
    for letter in used:
        if letter not in letters:
            errs.append(f"{where}: answer letter {letter} is not one of the options {sorted(letters)}")
    if "sequence" in ref and sorted(ref["sequence"]) != sorted(letters):
        errs.append(f"{where}: reference sequence must list every option letter once")
    if "options" in ref and task["answer_type"] in ("choice", "sequence", "set"):
        for letter in letters:
            if f"{letter})" not in task["question"]:
                errs.append(f"{where}: option {letter} is not listed in the question text")
    return errs


def check(data: Path) -> list[str]:
    errs: list[str] = []
    v_scene, v_task, v_result = load_validator("scene"), load_validator("task"), load_validator("result")

    scenes = {}
    scene_files = sorted((data / "scenes").glob("*.json"))
    if not scene_files:
        errs.append(f"{data}: no scenes/*.json found")
    for f in scene_files:
        scene = json.loads(f.read_text())
        where = f"scenes/{f.name}"
        e = schema_errors(v_scene, scene, where)
        if not e and f.stem != scene["scene_id"]:
            e.append(f"{where}: file name does not match scene_id {scene['scene_id']}")
        errs += e or check_scene(scene, data, where)
        if not e:
            scenes[scene["scene_id"]] = scene

    tasks = {}
    tasks_file = data / "tasks.jsonl"
    if tasks_file.is_file():
        for n, task in read_jsonl(tasks_file):
            where = f"tasks.jsonl:{n}"
            e = schema_errors(v_task, task, where)
            if not e and task["task_id"] in tasks:
                e.append(f"{where}: duplicate task_id {task['task_id']}")
            errs += e or check_task(task, scenes, data, where)
            tasks.setdefault(task["task_id"], task)
    else:
        errs.append(f"{data}: tasks.jsonl not found")

    for f in sorted((data / "results").glob("*.jsonl")):
        for n, row in read_jsonl(f):
            where = f"results/{f.name}:{n}"
            e = schema_errors(v_result, row, where)
            if not e and row["task_id"] not in tasks:
                e.append(f"{where}: task_id {row['task_id']} is not in tasks.jsonl")
            if not e and row["parsed"] is not None:
                key = next(iter(row["parsed"]))
                want = tasks[row["task_id"]]["answer_type"]
                if key != want:
                    e.append(f"{where}: parsed uses '{key}' but the task's answer_type is '{want}'")
            errs += e
    return errs


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data", type=Path, required=True, help="data folder, e.g. fixtures/ or data/")
    args = ap.parse_args(argv)
    errs = check(args.data)
    for e in errs:
        print(e)
    n_scenes = len(list((args.data / "scenes").glob("*.json")))
    print(f"{'FAIL' if errs else 'OK'}: {args.data} ({n_scenes} scenes, {len(errs)} problems)")
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
