"""Validity rules (AGENTS.md "Validity rules", B enforces them).

The generators in tasks/ already try to follow these rules. This module re-checks every finished
task against its scene, independently of how it was generated, and returns a named reason for each
rule it breaks. An empty list means the task is valid.

Rules:
  visibility   targets >= 30% visible (F3 exempt; F3 uses the lead-approved 0.10 rule instead)
  unique       every referring expression in the question still matches exactly one object
  margin       F4 depth difference >= 3 cm
  label        F8 answers equal scene.counterfactuals; F9 answer equals goal.success (re-simulation labels)
  support      F5 / F6 / F7 answers agree with scene.contacts
  count        F3: count equals objects >= 10% visible; no match between 0% and 10%
  options      every object option is shown with its unique description
"""

from __future__ import annotations

from tasks.options import option_label
from tasks.refexpr import MIN_VISIBLE, describe
from tasks.templates import F3_COUNT_MIN, F4_MIN_GAP_M, F6_MIN_VISIBLE

SUBJECTS = {"F1": 1, "F2": 1, "F4": 2, "F5": 1, "F8": 1, "F9": 2}  # referring expressions per question


def _objs(scene):
    return {o["id"]: o for o in scene["objects"]}


def _subjects(task, scene):
    """Walk evidence: each subject's description covers its own anchor ids, then the next subject starts."""
    objs, out, skip = _objs(scene), [], set()
    for oid in task["evidence"]:
        if len(out) == SUBJECTS.get(task["family"], 0):
            break
        if oid in skip or oid not in objs:
            continue
        ref = describe(objs[oid], scene)
        out.append((oid, ref))
        skip.update(ref.evidence if ref else (oid,))
    return out


def _targets(task, scene):
    fam, ref = task["family"], task["reference"]
    if fam == "F3":
        return []
    if fam == "F6":
        by_seg = {o["seg_id"]: o["id"] for o in scene["objects"]}
        return [by_seg[s] for s in ref["seg_ids"] if s in by_seg]
    if fam == "F7":
        return [v for v in ref["options"].values() if v != "table"]
    return [e for e in task["evidence"] if e != "table"]


def check(task: dict, scenes: dict) -> list[str]:
    """Reasons this task is invalid ([] = valid). `scenes` maps scene_id -> scene dict."""
    scene = scenes[task["scene_id"]]
    objs, fam, ref = _objs(scene), task["family"], task["reference"]
    reasons = []

    for oid in _targets(task, scene):
        o = objs.get(oid)
        if o is None:
            reasons.append(f"{fam} unknown object: {oid}")
        elif o["visible_frac"] < MIN_VISIBLE:
            reasons.append(f"{fam} visibility: target under 30% visible")

    for oid, r in _subjects(task, scene):
        if r is None or r.text.lower() not in task["question"].lower():
            reasons.append(f"{fam} unique: referring expression no longer matches exactly one object")

    if "options" in ref and fam != "F4":
        for letter, oid in ref["options"].items():
            label = option_label(oid, scene) if (oid == "table" or oid in objs) else None
            if label is None or f"{letter}) {label}" not in task["question"]:
                reasons.append(f"{fam} options: option {letter} is not shown with a unique description")

    if fam == "F4":
        a, b = task["evidence"][0], task["evidence"][-1]
        gap = objs[a]["depth_mean_m"] - objs[b]["depth_mean_m"]
        if abs(gap) < F4_MIN_GAP_M:
            reasons.append("F4 margin: depth gap under 3 cm")
        elif ref["options"][ref["choice"]] != ("in front of" if gap < 0 else "behind"):
            reasons.append("F4 label: answer disagrees with depth")

    if fam == "F3":
        matching = [objs[e] for e in task["evidence"] if e in objs]
        if any(0 < o["visible_frac"] < F3_COUNT_MIN for o in matching):
            reasons.append("F3 count: a matching object is between 0% and 10% visible")
        if ref["count"] != sum(o["visible_frac"] >= F3_COUNT_MIN for o in matching):
            reasons.append("F3 count: answer disagrees with visible objects")

    if fam == "F5":
        subject = task["evidence"][0]
        bottoms = {c["bottom"] for c in scene["contacts"] if c["top"] == subject}
        if bottoms != {ref["options"][ref["choice"]]}:
            reasons.append("F5 support: answer disagrees with contacts")

    if fam == "F6":
        supporting = {c["bottom"] for c in scene["contacts"]}
        free = {o["seg_id"] for o in scene["objects"]
                if o["id"] not in supporting and o["visible_frac"] >= F6_MIN_VISIBLE}
        if set(ref["seg_ids"]) != free:
            reasons.append("F6 support: free-to-pick set disagrees with contacts")

    if fam == "F7":
        ids = set(ref["options"].values())
        letter = {v: k for k, v in ref["options"].items()}
        want = {(letter[c["top"]], letter[c["bottom"]]) for c in scene["contacts"]
                if c["top"] in ids and c["bottom"] in ids}
        if {tuple(p) for p in ref["precedes"]} != want:
            reasons.append("F7 support: precedes disagrees with contacts")

    if fam == "F8":
        removed = task["evidence"][0]
        moved = set(scene.get("counterfactuals", {}).get(removed, {}).get("moved", []))
        if removed not in scene.get("counterfactuals", {}):
            reasons.append("F8 label: no re-simulation result for the removed object")
        elif {ref["options"][x] for x in ref["set"]} != moved:
            reasons.append("F8 label: answer disagrees with counterfactuals")

    if fam == "F9":
        goal = scene.get("goal")
        if not goal or ref["bool"] != goal["success"]:
            reasons.append("F9 label: answer disagrees with goal.success")

    if fam == "F10":
        pair = scene.get("pair") or {}
        after = scenes.get(pair.get("after"))
        if after is None:
            reasons.append("F10 label: after-scene missing")
        else:
            if {ref["options"][ref["choice"]]} != set(pair["moved"]):
                reasons.append("F10 label: answer disagrees with pair.moved")
            moved_after = next((o for o in after["objects"] if o["id"] in pair["moved"]), None)
            if moved_after is None or moved_after["visible_frac"] < MIN_VISIBLE:
                reasons.append("F10 visibility: moved object under 30% visible in the after image")

    return list(dict.fromkeys(reasons))
