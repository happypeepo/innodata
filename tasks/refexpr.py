"""Referring-expression builder: colour / shape / size / relative position, with a uniqueness check.

Tries the shortest description first and keeps the first one that matches exactly one object:
  "the cylinder" -> "the red cylinder" -> "the large red cylinder" -> "the red cylinder left of the blue box"
Returns None when nothing is unique, and the caller skips that object.

Uniqueness is checked against *every* object in the scene, hidden ones included: a fully hidden
red cylinder still makes "the red cylinder" ambiguous in the real scene.
"""

from __future__ import annotations

from dataclasses import dataclass, field

MIN_VISIBLE = 0.30        # AGENTS.md: a referred object must be >= 30% visible
SIZE_RATIO = 2.0          # my take: say "large"/"small" only when the volume gap is at least 2x
MIN_LR_GAP_PX = 40        # my take: "left of/right of" needs a clear horizontal gap in the image


@dataclass(frozen=True)
class RefExpr:
    text: str                       # e.g. "the red cylinder left of the blue box"
    evidence: tuple = field(default=())   # object ids the description relies on (target first)
    relational: bool = False


def volume(o) -> float:
    a, b, c = o["size_cm"]
    return a * b * c


def visible(o, min_visible=MIN_VISIBLE) -> bool:
    return o["visible_frac"] >= min_visible and o["centroid_2d"] is not None


def _size_word(target, group):
    others = [volume(o) for o in group if o is not target]
    if not others:
        return None
    v = volume(target)
    if v >= SIZE_RATIO * max(others):
        return "large"
    if v * SIZE_RATIO <= min(others):
        return "small"
    return None


def simple(target, objects):
    """Shape, then colour + shape, then size + colour + shape. No spatial relation."""
    same_shape = [o for o in objects if o["shape"] == target["shape"]]
    if len(same_shape) == 1:
        return RefExpr(f"the {target['shape']}", (target["id"],))
    group = [o for o in same_shape if o["color"] == target["color"]]
    if len(group) == 1:
        return RefExpr(f"the {target['color']} {target['shape']}", (target["id"],))
    word = _size_word(target, group)
    if word:
        return RefExpr(f"the {word} {target['color']} {target['shape']}", (target["id"],))
    return None


def relational(target, objects):
    """colour + shape + 'left of / right of <anchor>', where the anchor has its own simple description."""
    group = [o for o in objects if o["shape"] == target["shape"] and o["color"] == target["color"]]
    if any(o["centroid_2d"] is None for o in group):
        return None  # a hidden look-alike: we can't tell which side it is on
    tx = target["centroid_2d"][0]
    anchors = [o for o in objects if o not in group and visible(o)]
    anchors.sort(key=lambda a: (abs(a["centroid_2d"][0] - tx) + abs(a["centroid_2d"][1] - target["centroid_2d"][1]), a["id"]))
    for a in anchors:
        a_ref = simple(a, objects)
        if a_ref is None:
            continue
        ax = a["centroid_2d"][0]
        for rel, sign in (("left of", -1), ("right of", 1)):
            if sign * (tx - ax) < MIN_LR_GAP_PX:
                continue
            if all(sign * (o["centroid_2d"][0] - ax) <= -MIN_LR_GAP_PX for o in group if o is not target):
                text = f"the {target['color']} {target['shape']} {rel} {a_ref.text}"
                return RefExpr(text, (target["id"], a["id"]), relational=True)
    return None


def describe(target, scene) -> RefExpr | None:
    """Shortest unique description of `target`, or None. The target itself must be visible enough."""
    if not visible(target):
        return None
    objects = scene["objects"]
    return simple(target, objects) or relational(target, objects)
