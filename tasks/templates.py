"""One generator per task family. Each reads a scene and returns task dicts (task_id is added later).

Every reference answer comes from scene data (simulator state), never from a model (AGENTS.md rule 4).
A generator that can't make a valid task records why in `rejects` instead.

Every generator has the signature (scene, rng, rejects, ctx=None); ctx["scenes"] maps scene_id -> scene
for families that need a second scene (F10).
"""

from __future__ import annotations

from collections import Counter
from random import Random

from .options import build_options, letters_for, options_suffix
from .refexpr import MIN_VISIBLE, describe

F4_MIN_GAP_M = 0.03   # AGENTS.md: relation answers need a depth difference >= 3 cm
F3_COUNT_MIN = 0.10   # lead-approved: F3 counts visible_frac >= 0.10, drops tasks with a match in (0, 0.10)
F6_MIN_VISIBLE = 0.30 # lead-approved: free-to-pick needs nothing resting on it and visible_frac >= 0.30
F5_TABLE_CAP = 1      # my take: at most one "resting on the table" F5 per scene, so "table" doesn't dominate
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
    "F3": ["How many {what} can you see?",
           "Count the {what} visible in the image.",
           "How many {what} are visible on the table?"],
    "F5": ["What is {ref} resting on?",
           "What is directly underneath {ref}, holding it up?",
           "{Ref} is sitting on top of something. What is it?"],
    "F6": ["Point to an object you can pick up without moving anything else.",
           "Point to one object a robot could grab right now without disturbing any other object.",
           "Which object can be lifted straight away without moving anything else? Point to it."],
    "F7": ["List these objects in an order that clears the stack safely, one at a time.",
           "A robot must remove these objects one by one without anything falling. In what order should it pick them?",
           "Give a safe order for removing these stacked objects, one at a time."],
    "F8": ["If {ref} is removed, which objects move?",
           "Suppose a robot takes away {ref}. Which of these objects would move?",
           "Which objects would fall or shift if {ref} were removed?"],
    "F10": ["The first image is before and the second is after. Which object moved?",
            "Compare the two images. Which object changed position?",
            "Between the first and the second image, one object was moved. Which one?"],
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

def f1(scene, rng: Random, rejects: Counter, ctx=None):
    tasks = []
    for o, ref in _pick(rng, _referable(scene, rejects, "F1")):
        q = rng.choice(PHRASINGS["F1"]).format(ref=ref.text)
        tasks.append(make_task(scene, "F1", q, "point", {"seg_ids": [o["seg_id"]]}, "point_in_mask",
                               ref.evidence, targets=ref.evidence, unique=True,
                               margin=True if ref.relational else None,
                               distractors=target_distractors(scene, o)))
    return tasks


# ---------------------------------------------------------------- F2 referring detection

def f2(scene, rng: Random, rejects: Counter, ctx=None):
    tasks = []
    for o, ref in _pick(rng, _referable(scene, rejects, "F2")):
        q = rng.choice(PHRASINGS["F2"]).format(ref=ref.text)
        tasks.append(make_task(scene, "F2", q, "bbox", {"bbox": o["bbox_2d"], "seg_ids": [o["seg_id"]]},
                               "bbox_iou", ref.evidence, targets=ref.evidence, unique=True,
                               margin=True if ref.relational else None,
                               distractors=target_distractors(scene, o)))
    return tasks


# ---------------------------------------------------------------- F4 front / behind

def f4(scene, rng: Random, rejects: Counter, ctx=None):
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


def f9(scene, rng: Random, rejects: Counter, ctx=None):
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


# ---------------------------------------------------------------- F3 counting under occlusion

PLURAL = {"box": "boxes", "cylinder": "cylinders", "sphere": "spheres"}


def f3(scene, rng: Random, rejects: Counter, ctx=None):
    """Count a shape or a colour. Exempt from the 30% rule; uses the lead-approved 0.10 rule instead."""
    objs = scene["objects"]
    candidates = [(PLURAL[s], [o for o in objs if o["shape"] == s]) for s in sorted({o["shape"] for o in objs})]
    candidates += [(f"{c} objects", [o for o in objs if o["color"] == c]) for c in sorted({o["color"] for o in objs})]
    valid = []
    for what, matching in candidates:
        if any(0 < o["visible_frac"] < F3_COUNT_MIN for o in matching):
            rejects["F3: a matching object is between 0% and 10% visible"] += 1
            continue
        valid.append((what, matching))
    tasks = []
    for what, matching in _pick(rng, valid):
        counted = [o for o in matching if o["visible_frac"] >= F3_COUNT_MIN]
        q = rng.choice(PHRASINGS["F3"]).format(what=what)
        tasks.append(make_task(scene, "F3", q, "count", {"count": len(counted)}, "exact",
                               [o["id"] for o in matching], distractors=max(len(matching) - 1, 0)))
    return tasks


# ---------------------------------------------------------------- F5 support & stacking

def _supports(scene):
    """top id -> list of what it rests on (object ids or "table")."""
    under = {}
    for c in scene["contacts"]:
        under.setdefault(c["top"], []).append(c["bottom"])
    return under


def f5(scene, rng: Random, rejects: Counter, ctx=None):
    objs = by_id(scene)
    under = _supports(scene)
    stacked, on_table = [], []
    for top, bottoms in sorted(under.items()):
        if len(set(bottoms)) != 1:
            rejects["F5: object rests on more than one thing"] += 1
            continue
        (stacked if bottoms[0] != "table" else on_table).append(top)
    # stacked objects first, so "table" doesn't dominate the answers
    rng.shuffle(stacked)
    rng.shuffle(on_table)
    tasks, table_answers = [], 0
    for top in stacked + on_table:
        if len(tasks) >= PER_SCENE_CAP:
            break
        bottom = under[top][0]
        if bottom == "table" and table_answers >= F5_TABLE_CAP:
            continue
        target = objs[top]
        ref = describe(target, scene)
        if ref is None:
            rejects["F5: object not uniquely describable or under 30% visible"] += 1
            continue
        if ref.relational:
            rejects["F5: only a left/right description exists"] += 1
            continue
        pool = ["table"] + [o["id"] for o in scene["objects"] if o["id"] not in (top, bottom)]
        built = build_options(rng, scene, [bottom], pool)
        if built is None or len(built[0]) < 2:
            rejects["F5: support not describable as an option"] += 1
            continue
        options, labels = built
        table_answers += bottom == "table"
        phrase = rng.choice(PHRASINGS["F5"])
        q = phrase.format(ref=ref.text, Ref=ref.text[0].upper() + ref.text[1:]) + options_suffix(labels)
        evidence = [*ref.evidence] + ([] if bottom == "table" else [bottom])
        tasks.append(make_task(scene, "F5", q, "choice",
                               {"choice": letters_for(options, [bottom])[0], "options": options}, "exact",
                               evidence, targets=[top] + ([] if bottom == "table" else [bottom]), unique=True,
                               distractors=target_distractors(scene, target)))
    return tasks


# ---------------------------------------------------------------- F6 free to pick

def f6(scene, rng: Random, rejects: Counter, ctx=None):
    """Any object with nothing resting on it and visible_frac >= 0.30 is a correct answer."""
    supporting = {c["bottom"] for c in scene["contacts"]}
    free = [o for o in scene["objects"] if o["id"] not in supporting and o["visible_frac"] >= F6_MIN_VISIBLE]
    blocked = [o for o in scene["objects"] if o["id"] in supporting]
    if not free:
        rejects["F6: no free object"] += 1
        return []
    if not blocked:
        rejects["F6: nothing is stacked, so every object is free (trivial)"] += 1
        return []
    q = rng.choice(PHRASINGS["F6"])
    return [make_task(scene, "F6", q, "point", {"seg_ids": sorted(o["seg_id"] for o in free)},
                      "point_in_any_mask", [o["id"] for o in free + blocked],
                      targets=[o["id"] for o in free], distractors=len(blocked))]


# ---------------------------------------------------------------- F7 clearing order

def _stacks(scene):
    """Groups of objects connected by object-on-object contacts (the table is left out)."""
    edges = [(c["top"], c["bottom"]) for c in scene["contacts"] if c["bottom"] != "table"]
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in edges:
        parent[find(a)] = find(b)
    groups = {}
    for a, b in edges:
        groups.setdefault(find(a), set()).update((a, b))
    return [(sorted(g), [(a, b) for a, b in edges if a in g]) for g in groups.values()]


def _topo(nodes, edges):
    """One order where every top comes before what it rests on; None if there's a cycle."""
    indeg = {n: 0 for n in nodes}
    for a, b in edges:
        indeg[b] += 1
    ready = sorted(n for n in nodes if indeg[n] == 0)
    order = []
    while ready:
        n = ready.pop(0)
        order.append(n)
        for a, b in edges:
            if a == n:
                indeg[b] -= 1
                if indeg[b] == 0:
                    ready.append(b)
                    ready.sort()
    return order if len(order) == len(nodes) else None


def f7(scene, rng: Random, rejects: Counter, ctx=None):
    tasks = []
    for nodes, edges in _pick(rng, _stacks(scene)):
        order = _topo(nodes, edges)
        if order is None:
            rejects["F7: support graph has a cycle"] += 1
            continue
        built = build_options(rng, scene, nodes, [], n_total=len(nodes))
        if built is None:
            rejects["F7: a stacked object is not describable as an option"] += 1
            continue
        options, labels = built
        letter = {v: k for k, v in options.items()}
        q = rng.choice(PHRASINGS["F7"]) + options_suffix(labels)
        ref = {"sequence": [letter[n] for n in order], "options": options,
               "precedes": [[letter[a], letter[b]] for a, b in edges]}
        tasks.append(make_task(scene, "F7", q, "sequence", ref, "topo_order_valid", nodes,
                               targets=nodes, unique=True, distractors=0))
    return tasks


# ---------------------------------------------------------------- F8 counterfactual

def f8(scene, rng: Random, rejects: Counter, ctx=None):
    """Labels come only from scene["counterfactuals"], which A fills by re-simulating (AGENTS.md)."""
    objs = by_id(scene)
    tasks = []
    for removed in _pick(rng, sorted(scene["counterfactuals"])):
        ref = describe(objs[removed], scene)
        if ref is None:
            rejects["F8: removed object not uniquely describable or under 30% visible"] += 1
            continue
        if ref.relational:
            rejects["F8: only a left/right description exists"] += 1
            continue
        moved = scene["counterfactuals"][removed]["moved"]
        pool = [o["id"] for o in scene["objects"] if o["id"] != removed and o["id"] not in moved]
        built = build_options(rng, scene, moved, pool, n_total=max(4, len(moved) + 1))
        if built is None:
            rejects["F8: a moved object is not describable as an option"] += 1
            continue
        options, labels = built
        q = rng.choice(PHRASINGS["F8"]).format(ref=ref.text) + options_suffix(labels)
        tasks.append(make_task(scene, "F8", q, "set", {"set": letters_for(options, moved), "options": options},
                               "set_f1", [*ref.evidence, *moved], targets=[removed, *moved], unique=True,
                               distractors=target_distractors(scene, objs[removed])))
    return tasks


# ---------------------------------------------------------------- F10 before / after

def f10(scene, rng: Random, rejects: Counter, ctx=None):
    pair = scene.get("pair")
    if not pair or scene["scene_id"] != pair["before"]:
        return []
    after = (ctx or {}).get("scenes", {}).get(pair["after"])
    if after is None:
        rejects["F10: after-scene missing"] += 1
        return []
    if len(pair["moved"]) != 1:
        rejects["F10: more than one object moved"] += 1
        return []
    moved = pair["moved"][0]
    after_obj = next((o for o in after["objects"] if o["id"] == moved), None)
    if after_obj is None or after_obj["visible_frac"] < MIN_VISIBLE:
        rejects["F10: moved object under 30% visible in the after image"] += 1
        return []
    after_ids = {o["id"] for o in after["objects"]}
    pool = [o["id"] for o in scene["objects"] if o["id"] != moved and o["id"] in after_ids]
    built = build_options(rng, scene, [moved], pool)
    if built is None:
        rejects["F10: moved object not describable as an option"] += 1
        return []
    options, labels = built
    q = rng.choice(PHRASINGS["F10"]) + options_suffix(labels)
    objs = by_id(scene)
    return [make_task(scene, "F10", q, "choice", {"choice": letters_for(options, [moved])[0], "options": options},
                      "exact", [moved], targets=[moved], unique=True,
                      distractors=target_distractors(scene, objs[moved]),
                      images=[scene["image_path"], after["image_path"]])]


FAMILIES = {"F1": f1, "F2": f2, "F3": f3, "F4": f4, "F5": f5, "F6": f6, "F7": f7, "F8": f8, "F9": f9, "F10": f10}
