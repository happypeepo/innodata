"""Unit tests for the phase 4 families (F3, F5, F6, F7, F8, F10) and the lettered-option builder.

Run from the repo root:  python -m pytest tasks/tests validate/tests
"""

from collections import Counter
from random import Random

from tasks.options import build_options, letters_for
from tasks.templates import F3_COUNT_MIN, f3, f5, f6, f7, f8, f10
from validate.checkers import set_f1, topo_order_valid

from .test_tasks import obj, scene_of


def run(fn, scene, seed=0, ctx=None):
    rejects = Counter()
    return fn(scene, Random(seed), rejects, ctx), rejects


def letters_to_ids(task, letters):
    return {task["reference"]["options"][x] for x in letters}


def stack_scene():
    """o1 yellow box on table, o2 blue box on o1, o3 red cylinder on o2, plus two free objects."""
    s = scene_of(obj(1, "yellow", "box", cx=300), obj(2, "blue", "box", cx=300), obj(3, "red", "cylinder", cx=300),
                 obj(4, "green", "sphere", cx=100), obj(5, "purple", "cylinder", cx=600),
                 contacts=[{"top": "o1", "bottom": "table"}, {"top": "o2", "bottom": "o1"},
                           {"top": "o3", "bottom": "o2"}, {"top": "o4", "bottom": "table"},
                           {"top": "o5", "bottom": "table"}])
    s["counterfactuals"] = {"o1": {"moved": ["o2", "o3"]}, "o2": {"moved": ["o3"]}}
    return s


# ---------------------------------------------------------------- options

def test_options_contain_correct_answers_and_unique_labels():
    s = stack_scene()
    for seed in range(10):
        options, labels = build_options(Random(seed), s, ["o3", "table"], ["o1", "o2", "o4", "o5"])
        assert {"o3", "table"} <= set(options.values())
        assert len(options) == 4 and len(set(labels.values())) == 4
        assert list(options) == ["A", "B", "C", "D"]


def test_options_fail_when_a_correct_answer_is_not_describable():
    s = stack_scene()
    s["objects"][2]["visible_frac"] = 0.1
    assert build_options(Random(0), s, ["o3"], ["o1"]) is None


# ---------------------------------------------------------------- F3

def test_f3_counts_only_objects_at_least_10_percent_visible():
    s = scene_of(obj(1, "red", "sphere"), obj(2, "blue", "sphere", vis=F3_COUNT_MIN), obj(3, "green", "sphere", vis=0))
    spheres = [t for seed in range(20) for t in run(f3, s, seed)[0] if "spheres" in t["question"]]
    assert spheres and all(t["reference"] == {"count": 2} for t in spheres)


def test_f3_drops_counts_with_a_borderline_object():
    s = scene_of(obj(1, "red", "sphere"), obj(2, "red", "sphere", vis=0.05))
    tasks, rejects = run(f3, s)
    assert not any("spheres" in t["question"] or "red objects" in t["question"] for t in tasks)
    assert rejects["F3: a matching object is between 0% and 10% visible"] == 2


# ---------------------------------------------------------------- F5

def test_f5_answer_matches_contacts_and_prefers_stacks():
    s = stack_scene()
    tasks, _ = run(f5, s)
    assert len(tasks) == 2
    under = {"o2": "o1", "o3": "o2", "o1": "table", "o4": "table", "o5": "table"}
    for t in tasks:
        subject = t["evidence"][0]
        assert t["reference"]["options"][t["reference"]["choice"]] == under[subject]
    assert sum(t["reference"]["options"][t["reference"]["choice"]] == "table" for t in tasks) <= 1


def test_f5_skips_object_resting_on_two_things():
    s = stack_scene()
    s["contacts"].append({"top": "o3", "bottom": "o5"})
    _, rejects = run(f5, s)
    assert rejects["F5: object rests on more than one thing"] == 1


# ---------------------------------------------------------------- F6

def test_f6_any_free_visible_object_counts():
    s = stack_scene()
    s["objects"][4]["visible_frac"] = 0.2  # o5 is free but too hidden
    (t,), _ = run(f6, s)
    assert sorted(t["reference"]["seg_ids"]) == sorted([s["objects"][2]["seg_id"], s["objects"][3]["seg_id"]])
    assert t["checker"] == "point_in_any_mask"


def test_f6_skips_scene_with_no_stacks():
    s = scene_of(obj(1, "red", "box"), obj(2, "blue", "sphere"))
    tasks, rejects = run(f6, s)
    assert tasks == [] and rejects["F6: nothing is stacked, so every object is free (trivial)"] == 1


# ---------------------------------------------------------------- F7

def test_f7_reference_is_a_valid_top_first_order():
    s = stack_scene()
    (t,), _ = run(f7, s)
    ref = t["reference"]
    assert letters_to_ids(t, ref["sequence"][:1]) == {"o3"}
    assert topo_order_valid(ref["sequence"], ref["options"], ref["precedes"]) == 1.0
    reversed_order = list(reversed(ref["sequence"]))
    assert topo_order_valid(reversed_order, ref["options"], ref["precedes"]) == 0.0
    assert set(ref["options"].values()) == {"o1", "o2", "o3"}


def test_f7_rejects_cycles():
    s = scene_of(obj(1, "red", "box"), obj(2, "blue", "box"),
                 contacts=[{"top": "o1", "bottom": "o2"}, {"top": "o2", "bottom": "o1"}])
    tasks, rejects = run(f7, s)
    assert tasks == [] and rejects["F7: support graph has a cycle"] == 1


# ---------------------------------------------------------------- F8

def test_f8_labels_come_from_counterfactuals_only():
    s = stack_scene()
    tasks, _ = run(f8, s)
    assert len(tasks) == 2
    for t in tasks:
        removed = t["evidence"][0]
        assert letters_to_ids(t, t["reference"]["set"]) == set(s["counterfactuals"][removed]["moved"])
        assert set_f1(t["reference"]["set"], t["reference"]["set"]) == 1.0
        assert len(t["reference"]["options"]) > len(t["reference"]["set"])  # at least one wrong option


# ---------------------------------------------------------------- F10

def pair_scenes(moved=("o3",), after_vis=1.0):
    before = scene_of(obj(1, "blue", "cylinder"), obj(2, "orange", "box"), obj(3, "red", "sphere"))
    after = scene_of(obj(1, "blue", "cylinder"), obj(2, "orange", "box"), obj(3, "red", "sphere", vis=after_vis))
    before.update(scene_id="s_000007a", image_path="images/s_000007a.png")
    after.update(scene_id="s_000007b", image_path="images/s_000007b.png")
    pair = {"before": "s_000007a", "after": "s_000007b", "moved": list(moved)}
    before["pair"] = after["pair"] = pair
    return before, after, {"scenes": {"s_000007a": before, "s_000007b": after}}


def test_f10_uses_both_images_and_pair_moved():
    before, after, ctx = pair_scenes()
    (t,), _ = run(f10, before, ctx=ctx)
    assert t["images"] == ["images/s_000007a.png", "images/s_000007b.png"]
    assert letters_to_ids(t, [t["reference"]["choice"]]) == {"o3"}
    assert run(f10, after, ctx=ctx)[0] == []  # only generated from the before scene


def test_f10_rejects_hidden_after_and_multiple_moves():
    before, _, ctx = pair_scenes(after_vis=0.2)
    assert run(f10, before, ctx=ctx)[1]["F10: moved object under 30% visible in the after image"] == 1
    before, _, ctx = pair_scenes(moved=("o2", "o3"))
    assert run(f10, before, ctx=ctx)[1]["F10: more than one object moved"] == 1


def test_letters_for():
    assert letters_for({"A": "o1", "B": "table", "C": "o3"}, ["o3", "table"]) == ["B", "C"]
