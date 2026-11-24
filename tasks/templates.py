"""One generator per task family. Each reads a scene and returns task dicts (task_id is added later).

Every reference answer comes from scene data (simulator state), never from a model (AGENTS.md rule 4).
A generator that can't make a valid task records why in `rejects` instead.

Implemented so far: F1, F2, F4, F9. F3, F5, F6, F7, F8, F10 come in phase 4.
"""

from __future__ import annotations

from collections import Counter
from random import Random

from .refexpr import MIN_VISIBLE, describe

F4_MIN_GAP_M = 0.03   # AGENTS.md: relation answers need a depth difference >= 3 cm
PER_SCENE_CAP = 2     # my take: at most this many tasks per family per scene (phase 5 balances globally)

ANSWER_FORMAT = {
    "point": 'Return JSON: {"point": [y, x]} with y, x normalised 0-1000.',
    "bbox": 'Return JSON: {"bbox": [ymin, xmin, ymax, xmax]} with values normalised 0-1000.',
    "count": 'Return JSON: {"count": <integer>}.',
    "choice": 'Return JSON: {"choice": "<letter>"} using one letter from the options.',
    "bool": 'Return JSON: {"bool": true} or {"bool": false}.',
    "sequence": 'Return JSON: {"sequence": ["<letter>", ...]} listing every option letter exactly once, in order.',
    "set": 'Return JSON: {"set": ["<letter>", ...]} with every option letter that applies; use [] if none.',
}

PHRASINGS = {
    "F1": ["Point to {ref}.",
           "Where is {ref}? Point to it.",
           "Point at {ref} in the image."],
    "F2": ["Draw a box around {ref}.",
           "Give the bounding box of {ref}.",
           "Put a tight bounding box around {ref}."],
    "F4": ["Is {a} in front of or behind {b}?",
           "From the camera's point of view, is {a} in front of or behind {b}?",
           "Looking from the camera, is {a} in front of {b} or behind it?"],
    "F9": ["Goal: put {obj} {rel} {target}. Did it succeed?",
           "The robot tried to place {obj} {rel} {target}. Did the placement succeed?",
           "Is {obj} now {rel} {target}, as the goal required?"],
}


def options_text(options: dict) -> str:
    return " Options: " + " ".join(f"{k}) {v}" for k, v in options.items())


def by_id(scene):
    return {o["id"]: o for o in scene["objects"]}


def target_distractors(scene, target):
    """Other objects that share the target's colour or shape (the AGENTS.md 'distractors' knob)."""
    return sum(1 for o in scene["objects"]
               if o["id"] != target["id"] and (o["color"] == target["color"] or o["shape"] == target["shape"]))


def make_task(scene, family, question, answer_type, reference, checker, evidence, *,
              targets=(), unique=None, margin=None, distractors=None, images=None):
    objs = by_id(scene)
    vis = [objs[t]["visible_frac"] for t in targets]
    difficulty = dict(scene["difficulty"])
    if distractors is not None:
        difficulty["distractors"] = distractors
    return {
        "task_id": None, "scene_id": scene["scene_id"], "family": family,
        "images": images or [scene["image_path"]], "question": question, "answer_type": answer_type,
        "answer_format": ANSWER_FORMAT[answer_type], "reference": reference, "checker": checker,
        "evidence": list(dict.fromkeys(evidence)),
        "validated": {"unique_referent": unique, "min_visible": min(vis) if vis else None,
                      "margin_ok": margin, "human": None},
        "difficulty": difficulty,
    }


def _pick(rng: Random, items, k=PER_SCENE_CAP):
    items = list(items)
    rng.shuffle(items)
    return items[:k]


def _referable(scene, rejects, family):
    """(object, RefExpr) for every object that has a unique description and is visible enough."""
    out = []
    for o in scene["objects"]:
        if o["visible_frac"] < MIN_VISIBLE or o["centroid_2d"] is None:
            rejects[f"{family}: target under {int(MIN_VISIBLE * 100)}% visible"] += 1
            continue
        ref = describe(o, scene)
        if ref is None:
            rejects[f"{family}: no unique referring expression"] += 1
            continue
        out.append((o, ref))
    return out


# ---------------------------------------------------------------- F1 referring pointing

def f1(scene, rng: Random, rejects: Counter):
    tasks = []
    for o, ref in _pick(rng, _referable(scene, rejects, "F1")):
        q = rng.choice(PHRASINGS["F1"]).format(ref=ref.text)
        tasks.append(make_task(scene, "F1", q, "point", {"seg_ids": [o["seg_id"]]}, "point_in_mask",
                               ref.evidence, targets=ref.evidence, unique=True,
                               margin=True if ref.relational else None,
                               distractors=target_distractors(scene, o)))
    return tasks


# ---------------------------------------------------------------- F2 referring detection

def f2(scene, rng: Random, rejects: Counter):
    tasks = []
    for o, ref in _pick(rng, _referable(scene, rejects, "F2")):
        q = rng.choice(PHRASINGS["F2"]).format(ref=ref.text)
        tasks.append(make_task(scene, "F2", q, "bbox", {"bbox": o["bbox_2d"], "seg_ids": [o["seg_id"]]},
                               "bbox_iou", ref.evidence, targets=ref.evidence, unique=True,
                               margin=True if ref.relational else None,
                               distractors=target_distractors(scene, o)))
    return tasks


# ---------------------------------------------------------------- F4 front / behind

def f4(scene, rng: Random, rejects: Counter):
    referable = []
    for o, ref in _referable(scene, rejects, "F4"):
        if ref.relational:  # "is the X left of the Y in front of the Z?" reads ambiguously
            rejects["F4: only a left/right description exists"] += 1
            continue
        referable.append((o, ref))
    touching = {frozenset((c["top"], c["bottom"])) for c in scene["contacts"]}
    pairs = []
    for i, (a, ra) in enumerate(referable):
        for b, rb in referable[i + 1:]:
            if frozenset((a["id"], b["id"])) in touching:  # one rests on the other: front/behind is unclear
                rejects["F4: pair is stacked"] += 1
                continue
            if abs(a["depth_mean_m"] - b["depth_mean_m"]) < F4_MIN_GAP_M:
                rejects["F4: depth gap under 3 cm"] += 1
                continue
            pairs.append(((a, ra), (b, rb)))
    tasks = []
    for (a, ra), (b, rb) in _pick(rng, pairs):
        if rng.random() < 0.5:
            (a, ra), (b, rb) = (b, rb), (a, ra)
        words = ["in front of", "behind"]
        rng.shuffle(words)
        options = {"A": words[0], "B": words[1]}
        truth = "in front of" if a["depth_mean_m"] < b["depth_mean_m"] else "behind"  # nearer = in front
        answer = next(k for k, v in options.items() if v == truth)
        q = rng.choice(PHRASINGS["F4"]).format(a=ra.text, b=rb.text) + options_text(options)
        tasks.append(make_task(scene, "F4", q, "choice", {"choice": answer, "options": options}, "exact",
                               [*ra.evidence, *rb.evidence], targets=[a["id"], b["id"]], unique=True,
                               margin=True, distractors=target_distractors(scene, a)))
    return tasks


# ---------------------------------------------------------------- F9 placement success

RELATION_WORDS = {"on": "on"}


def f9(scene, rng: Random, rejects: Counter):
    goal = scene.get("goal")
    if not goal:
        return []
    rel = RELATION_WORDS.get(goal["relation"])
    if rel is None:
        rejects[f"F9: unsupported relation '{goal['relation']}'"] += 1
        return []
    objs = by_id(scene)
    obj = objs[goal["object"]]
    ref_obj = describe(obj, scene)
    if goal["target"] == "table":
        ref_target, target_ids = None, []
    else:
        ref_target, target_ids = describe(objs[goal["target"]], scene), [goal["target"]]
    if ref_obj is None or (goal["target"] != "table" and ref_target is None):
        rejects["F9: goal object or target not uniquely describable or under 30% visible"] += 1
        return []
    target_text = ref_target.text if ref_target else "the table"
    q = rng.choice(PHRASINGS["F9"]).format(obj=ref_obj.text, rel=rel, target=target_text)
    evidence = [*ref_obj.evidence, *(ref_target.evidence if ref_target else ())]
    return [make_task(scene, "F9", q, "bool", {"bool": goal["success"]}, "exact", evidence,
                      targets=[goal["object"], *target_ids], unique=True,
                      distractors=target_distractors(scene, obj))]


FAMILIES = {"F1": f1, "F2": f2, "F4": f4, "F9": f9}
