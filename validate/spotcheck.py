"""Human spot-check (AGENTS.md: "a tiny script that shows 50 random tasks with the image and reference
overlaid and records agree/disagree -> reference error rate").

    python -m validate.spotcheck --data data/ --tasks data/tasks.jsonl --seed 0 --reviewer Dhruvk2004

For each sampled task it draws the reference onto the image (highlighted masks, box, option letters,
the answer in words), opens it, and asks: does the reference answer look right? y / n / s(kip) / q(uit).
Every answer is saved immediately to validate/evidence/human_check_seed<N>.jsonl (lead-approved
location; data/ is gitignored), so you can stop and resume. `python -m tasks` then copies the verdicts
into validated.human and the agreement % into validation_report.json.

Overlay images go to <data>/spotcheck_seed<N>/ and are not committed. D can reuse draw_overlay() for
the failure gallery.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import textwrap
from collections import Counter, defaultdict
from pathlib import Path
from random import Random

import numpy as np
from PIL import Image, ImageDraw, ImageFont

N_SAMPLES = 50                          # AGENTS.md: 50 tasks
EVIDENCE_DIR = Path(__file__).resolve().parent / "evidence"
OPTION_RE = re.compile(r"([A-Z])\) (.*?)(?= [A-Z]\) |$)")
ANSWER_RGB = (40, 220, 90)              # reference answer
CONTEXT_RGB = (255, 200, 0)             # other objects the question mentions
BOX_RGB = (230, 40, 40)
BAND_H = 170                            # text band above the image


def evidence_path(seed: int) -> Path:
    return EVIDENCE_DIR / f"human_check_seed{seed}.jsonl"


# ---------------------------------------------------------------- sampling

def sample(tasks, n=N_SAMPLES, seed=0):
    """n tasks spread across families (round-robin), chosen with a seeded RNG."""
    rng = Random(f"spotcheck:{seed}")
    by_family = defaultdict(list)
    for t in tasks:
        by_family[t["family"]].append(t)
    queues = []
    for fam in sorted(by_family, key=lambda f: int(f[1:])):
        q = list(by_family[fam])
        rng.shuffle(q)
        queues.append(q)
    picked, i = [], 0
    while len(picked) < n and any(i < len(q) for q in queues):
        for q in queues:
            if i < len(q) and len(picked) < n:
                picked.append(q[i])
        i += 1
    return picked


# ---------------------------------------------------------------- answer in words

def option_labels(task):
    _, _, opts = task["question"].rpartition(" Options: ")
    return dict(OPTION_RE.findall(opts)) if opts else {}


def answer_text(task, scene):
    ref, atype = task["reference"], task["answer_type"]
    labels = option_labels(task)
    names = {o["seg_id"]: f"{o['color']} {o['shape']}" for o in scene["objects"]}
    if atype == "point":
        objs = [names.get(s, f"seg {s}") for s in ref["seg_ids"]]
        return "Point on: " + (objs[0] if len(objs) == 1 else "any of " + ", ".join(objs)) + " (green outline)"
    if atype == "bbox":
        return f"Box around: {names.get(ref['seg_ids'][0])} (red box; object has a green outline)"
    if atype == "count":
        return f"Count: {ref['count']}"
    if atype == "bool":
        return f"Answer: {'yes' if ref['bool'] else 'no'}"
    if atype == "choice":
        return f"Answer: {ref['choice']}) {labels.get(ref['choice'], ref['options'][ref['choice']])}"
    if atype == "set":
        return "Answer: " + (", ".join(f"{x}) {labels.get(x, '')}" for x in ref["set"]) or "none")
    if atype == "sequence":
        return "One valid order: " + " -> ".join(f"{x}) {labels.get(x, '')}" for x in ref["sequence"])
    return json.dumps(ref)


# ---------------------------------------------------------------- overlay drawing

def _font(size):
    return ImageFont.load_default(size=size)


def _ring(mask, width):
    """Pixels just outside the mask, `width` px thick. Outlines never recolour the object itself,
    so the reviewer still sees its true colour."""
    grown = mask.copy()
    for _ in range(width):
        grown = grown | np.roll(grown, 1, 0) | np.roll(grown, -1, 0) | np.roll(grown, 1, 1) | np.roll(grown, -1, 1)
    return grown & ~mask


def _outline(img, mask, rgb, width):
    arr = np.array(img)
    arr[_ring(mask, width)] = rgb
    return Image.fromarray(arr)


def _tag(draw, xy, text, font):
    x, y = xy
    l, t, r, b = draw.textbbox((x, y), text, font=font)
    draw.rectangle((l - 4, t - 2, r + 4, b + 2), fill=(0, 0, 0))
    draw.text((x, y), text, fill=(255, 255, 255), font=font)


def _annotate(img, task, scene, seg):
    """Masks, box and option letters for one image."""
    ref = task["reference"]
    by_id = {o["id"]: o for o in scene["objects"]}
    answer_ids = set()
    if "seg_ids" in ref:
        answer_ids |= {o["id"] for o in scene["objects"] if o["seg_id"] in ref["seg_ids"]}
    options = ref.get("options", {})
    if "choice" in ref:
        answer_ids.add(options.get(ref["choice"]))
    for x in ref.get("set", []):
        answer_ids.add(options.get(x))
    if task["answer_type"] == "count":
        answer_ids |= {e for e in task["evidence"] if e in by_id and by_id[e]["visible_frac"] >= 0.10}
    context = {e for e in task["evidence"] if e in by_id} | {v for v in options.values() if v in by_id}
    for oid in sorted(context - answer_ids):
        img = _outline(img, seg == by_id[oid]["seg_id"], CONTEXT_RGB, 2)
    for oid in sorted(answer_ids & set(by_id)):
        img = _outline(img, seg == by_id[oid]["seg_id"], ANSWER_RGB, 4)
    draw = ImageDraw.Draw(img)
    if "bbox" in ref:
        draw.rectangle(ref["bbox"], outline=BOX_RGB, width=3)
    font = _font(20)
    for letter, oid in options.items():
        o = by_id.get(oid)
        if o and o["centroid_2d"]:
            _tag(draw, (o["centroid_2d"][0] - 8, o["centroid_2d"][1] - 12), letter, font)
        elif oid == "table":
            _tag(draw, (12, img.height - 34), f"{letter}) table", font)
    return img


def draw_overlay(task, scene, data_dir, scenes=None):
    """One PNG-ready image: question and reference answer on top, annotated image(s) below."""
    data_dir = Path(data_dir)
    panels = []
    for i, rel in enumerate(task["images"]):
        sc = scene
        if i > 0 and scenes:  # F10 after-image: annotate with the after-scene's masks
            sc = next((s for s in scenes.values() if s["image_path"] == rel), scene)
        img = Image.open(data_dir / rel).convert("RGB")
        seg = np.array(Image.open(data_dir / sc["seg_path"]))
        panels.append(_annotate(img, task, sc, seg))
    if len(panels) > 1:  # F10: before | after, full size, side by side
        w, h = panels[0].size
        body = Image.new("RGB", (w * len(panels) + 20 * (len(panels) - 1), h), (255, 255, 255))
        for i, p in enumerate(panels):
            body.paste(p, (i * (w + 20), 0))
            _tag(ImageDraw.Draw(body), (i * (w + 20) + 12, 12), "BEFORE" if i == 0 else "AFTER", _font(24))
    else:
        body = panels[0]
    canvas = Image.new("RGB", (body.width, body.height + BAND_H), (250, 250, 250))
    canvas.paste(body, (0, BAND_H))
    draw = ImageDraw.Draw(canvas)
    lines = [f"{task['task_id']}  ({task['family']}, {task['answer_type']})"]
    lines += textwrap.wrap("Q: " + task["question"], 100)
    lines += textwrap.wrap(answer_text(task, scene) + "   [green outline = answer, yellow = mentioned]", 100)
    y = 10
    for j, line in enumerate(lines[:6]):
        draw.text((14, y), line, fill=(20, 20, 20) if j != len(lines) - 1 else (10, 110, 40), font=_font(20))
        y += 26
    return canvas


# ---------------------------------------------------------------- recording

def load_results(path: Path) -> dict:
    if not path.is_file():
        return {}
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    return {r["task_id"]: r for r in rows}


def append_result(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(row) + "\n")


def summarise(results: dict, tasks=None) -> dict:
    """Human-check summary for validation_report.json. A verdict whose question no longer matches the
    current task (e.g. templates changed) is counted as stale and left out of the agreement %."""
    current = {t["task_id"]: t for t in tasks} if tasks is not None else None
    agree = disagree = stale = unmatched = 0
    for r in results.values():
        if current is not None and r["task_id"] not in current:
            unmatched += 1  # e.g. verdicts from another data folder that uses the same seed
        elif current is not None and current[r["task_id"]]["question"] != r["question"]:
            stale += 1
        elif r["verdict"] == "agree":
            agree += 1
        elif r["verdict"] == "disagree":
            disagree += 1
    n = agree + disagree
    return {
        "status": "run" if n else "not run yet (phase 8)",
        "checked": n,
        "agree": agree,
        "disagree": disagree,
        "stale": stale,
        "not_in_this_task_set": unmatched,
        "agreement_pct": round(100 * agree / n, 1) if n else None,
        "reference_error_rate_pct": round(100 * disagree / n, 1) if n else None,
    }


def apply_to_tasks(tasks, results: dict) -> None:
    """Copy each verdict into validated.human (True = agree, False = disagree) when the question still matches."""
    for t in tasks:
        r = results.get(t["task_id"])
        if r and r["question"] == t["question"] and r["verdict"] in ("agree", "disagree"):
            t["validated"]["human"] = r["verdict"] == "agree"


# ---------------------------------------------------------------- interactive loop

def review(tasks, scenes, data_dir, seed, reviewer, n=N_SAMPLES, out_path=None, ask=input, show=None,
           overlay_dir=None):
    out_path = Path(out_path) if out_path else evidence_path(seed)
    overlay_dir = Path(overlay_dir) if overlay_dir else Path(data_dir) / f"spotcheck_seed{seed}"
    overlay_dir.mkdir(parents=True, exist_ok=True)
    done = load_results(out_path)
    todo = [t for t in sample(tasks, n, seed) if t["task_id"] not in done]
    print(f"{len(done)} already checked, {len(todo)} to go. y = reference is right, n = wrong, s = skip, q = quit")
    for k, t in enumerate(todo, 1):
        scene = scenes[t["scene_id"]]
        png = overlay_dir / f"{t['task_id']}.png"
        draw_overlay(t, scene, data_dir, scenes).save(png)
        if show:
            show(png)
        print(f"\n[{k}/{len(todo)}] {t['task_id']}\n  Q: {t['question']}\n  {answer_text(t, scene)}\n  image: {png}")
        while True:
            key = ask("  Is the reference answer correct? [y/n/s/q] ").strip().lower()
            if key in ("y", "n", "s", "q"):
                break
        if key == "q":
            break
        if key == "s":
            continue
        note = ask("  What's wrong? (one line) ").strip() if key == "n" else ""
        append_result(out_path, {"task_id": t["task_id"], "family": t["family"], "question": t["question"],
                                 "reference": t["reference"], "verdict": "agree" if key == "y" else "disagree",
                                 "note": note, "reviewer": reviewer, "seed": seed})
    summary = summarise(load_results(out_path), tasks)
    print(f"\nSaved to {out_path}: {summary['checked']} checked, {summary['agreement_pct']}% agree, "
          f"{summary['disagree']} disagreements")
    return summary


def _open_file(path):
    opener = "open" if sys.platform == "darwin" else "xdg-open"
    subprocess.run([opener, str(path)], check=False)


def main(argv=None):
    from tasks.io import load_scenes

    ap = argparse.ArgumentParser(description="Human spot-check of reference answers")
    ap.add_argument("--data", type=Path, required=True)
    ap.add_argument("--tasks", type=Path, required=True)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--n", type=int, default=N_SAMPLES)
    ap.add_argument("--reviewer", required=True, help="your GitHub handle, recorded with each verdict")
    ap.add_argument("--no-open", action="store_true", help="don't open each image automatically")
    ap.add_argument("--out", type=Path, help=f"results file (default: {evidence_path(0).parent}/human_check_seed<N>.jsonl)")
    args = ap.parse_args(argv)
    scenes = {s["scene_id"]: s for s in load_scenes(args.data)}
    tasks = [json.loads(line) for line in args.tasks.read_text().splitlines() if line.strip()]
    review(tasks, scenes, args.data, args.seed, args.reviewer, args.n, args.out,
           show=None if args.no_open else _open_file)
    return 0


if __name__ == "__main__":
    sys.exit(main())
