"""Tests for the task pipeline (phase 3 core; phase 4 families are in test_families.py).

Run from the repo root:  python -m pytest tasks/tests validate/tests
"""

import json
from collections import Counter
from pathlib import Path
from random import Random

import numpy as np
import pytest

from schema.check import check_task, load_validator
from tasks.__main__ import generate, main
from tasks.io import load_scenes
from tasks.refexpr import MIN_LR_GAP_PX, describe
from tasks.templates import F4_MIN_GAP_M, PHRASINGS, f4, f9
from validate.checkers import score, seg_array

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"


def obj(i, color, shape, size=(6, 6, 6), cx=100, vis=1.0, depth=0.8):
    hidden = vis == 0
    return {"id": f"o{i}", "seg_id": i + 1, "shape": shape, "color": color, "size_cm": list(size),
            "pose": {"xyz_m": [0, 0, 0], "quat": [0, 0, 0, 1]},
            "bbox_2d": None if hidden else [cx - 10, 90, cx + 10, 110],
            "centroid_2d": None if hidden else [cx, 100], "pixel_area": 0 if hidden else 400,
            "visible_frac": vis, "depth_mean_m": None if hidden else depth}


def scene_of(*objects, contacts=(), goal=None):
    s = {"scene_id": "s_000009", "image_path": "images/s_000009.png", "objects": list(objects),
         "contacts": list(contacts), "difficulty": {"n_objects": len(objects), "occlusion": 0.0, "distractors": 0}}
    if goal:
        s["goal"] = goal
    return s


# ---------------------------------------------------------------- referring expressions

def test_shape_alone_when_unique():
    s = scene_of(obj(1, "red", "cylinder"), obj(2, "red", "box"))
    assert describe(s["objects"][0], s).text == "the cylinder"


def test_colour_added_when_shape_repeats():
    s = scene_of(obj(1, "red", "cylinder"), obj(2, "blue", "cylinder"))
    assert describe(s["objects"][0], s).text == "the red cylinder"


def test_size_word_only_with_clear_gap():
    big, small = obj(1, "red", "box", (10, 10, 10)), obj(2, "red", "box", (5, 5, 5))
    s = scene_of(big, small)
    assert describe(big, s).text == "the large red box"
    assert describe(small, s).text == "the small red box"
    close = scene_of(obj(1, "red", "box", (6, 6, 6)), obj(2, "red", "box", (7, 7, 7)))
    assert describe(close["objects"][0], close) is None  # 1.6x gap: no size word, no anchor


def test_left_right_with_anchor():
    a, b, anchor = obj(1, "red", "box", cx=100), obj(2, "red", "box", cx=400), obj(3, "blue", "sphere", cx=250)
    s = scene_of(a, b, anchor)
    ra, rb = describe(a, s), describe(b, s)
    assert ra.text == "the red box left of the sphere" and ra.evidence == ("o1", "o3") and ra.relational
    assert rb.text == "the red box right of the sphere"


def test_left_right_needs_clear_gap():
    a = obj(1, "red", "box", cx=250 - MIN_LR_GAP_PX + 1)
    s = scene_of(a, obj(2, "red", "box", cx=400), obj(3, "blue", "sphere", cx=250))
    assert describe(a, s) is None


def test_hidden_lookalike_blocks_description():
    s = scene_of(obj(1, "red", "box", cx=100), obj(2, "red", "box", vis=0), obj(3, "blue", "sphere", cx=250))
    assert describe(s["objects"][0], s) is None


def test_low_visibility_target_is_never_described():
    s = scene_of(obj(1, "red", "box", vis=0.29), obj(2, "blue", "sphere"))
    assert describe(s["objects"][0], s) is None


# ---------------------------------------------------------------- families

def test_f4_respects_3cm_margin_and_skips_stacks():
    near = scene_of(obj(1, "red", "box", depth=0.80), obj(2, "blue", "sphere", depth=0.80 + F4_MIN_GAP_M - 0.001))
    rejects = Counter()
    assert f4(near, Random(0), rejects) == [] and rejects["F4: depth gap under 3 cm"] == 1
    stacked = scene_of(obj(1, "red", "box", depth=0.80), obj(2, "blue", "sphere", depth=0.70),
                       contacts=[{"top": "o2", "bottom": "o1"}])
    rejects = Counter()
    assert f4(stacked, Random(0), rejects) == [] and rejects["F4: pair is stacked"] == 1


def test_f4_answer_is_nearer_in_front_and_options_shuffle():
    s = scene_of(obj(1, "red", "box", depth=0.70), obj(2, "blue", "sphere", depth=0.90))
    seen_orders = set()
    for seed in range(20):
        (t,) = f4(s, Random(seed), Counter())
        ref = t["reference"]
        truth = ref["options"][ref["choice"]]
        subject = t["evidence"][0]  # the question asks about evidence[0] relative to evidence[1]
        assert truth == ("in front of" if subject == "o1" else "behind")  # o1 is nearer
        seen_orders.add(ref["options"]["A"])
    assert seen_orders == {"in front of", "behind"}


def test_f9_reference_is_goal_success():
    for success in (True, False):
        s = scene_of(obj(1, "green", "box"), obj(2, "red", "sphere"),
                     goal={"object": "o2", "relation": "on", "target": "o1", "success": success})
        (t,) = f9(s, Random(0), Counter())
        assert t["reference"] == {"bool": success} and t["checker"] == "exact"


def test_every_family_has_three_phrasings():
    assert all(len(set(p)) >= 3 for p in PHRASINGS.values())


# ---------------------------------------------------------------- whole pipeline on fixtures

@pytest.fixture(scope="module")
def generated():
    tasks, _ = generate(FIXTURES, seed=0)
    return tasks


def test_generated_tasks_are_schema_valid_and_consistent(generated):
    v = load_validator("task")
    scenes = {s["scene_id"]: s for s in load_scenes(FIXTURES)}
    for t in generated:
        assert not list(v.iter_errors(t)), t["task_id"]
        assert check_task(t, scenes, FIXTURES, t["task_id"]) == []
    assert len({t["task_id"] for t in generated}) == len(generated)
    assert {t["family"] for t in generated} == {f"F{i}" for i in range(1, 11)}


def test_reference_answers_score_one(generated):
    """Each reference turned into a model answer must score 1.0 with the real checkers."""
    scenes = {s["scene_id"]: s for s in load_scenes(FIXTURES)}
    for t in generated:
        scene, ref, typ = scenes[t["scene_id"]], t["reference"], t["answer_type"]
        if typ == "point":
            ys, xs = np.nonzero(seg_array(scene) == ref["seg_ids"][0])
            ans = [int(xs[len(xs) // 2]), int(ys[len(ys) // 2])]
        else:
            ans = ref[typ]
        assert score(t, {typ: ans}, scene) == 1.0, t["task_id"]


def test_targets_meet_visibility_rule(generated):
    for t in generated:
        if t["family"] != "F3":  # F3 is exempt (AGENTS.md); it uses the 0.10 counting rule instead
            assert t["validated"]["min_visible"] >= 0.30, t["task_id"]


def test_no_tasks_from_after_image_of_a_pair(generated):
    assert not any(t["scene_id"] == "s_000004b" for t in generated)
    (f10,) = [t for t in generated if t["family"] == "F10"]
    assert f10["scene_id"] == "s_000004a" and f10["images"] == ["images/s_000004a.png", "images/s_000004b.png"]


def test_same_seed_same_tasks_different_seed_differs():
    a, _ = generate(FIXTURES, seed=0)
    b, _ = generate(FIXTURES, seed=0)
    c, _ = generate(FIXTURES, seed=1)
    assert json.dumps(a) == json.dumps(b)
    assert json.dumps(a) != json.dumps(c)


def test_cli_refuses_to_overwrite_hand_written_fixtures():
    with pytest.raises(SystemExit):
        main(["--data", str(FIXTURES)])


def test_cli_writes_file(tmp_path):
    out = tmp_path / "tasks.jsonl"
    assert main(["--data", str(FIXTURES), "--out", str(out), "--seed", "3"]) == 0
    assert len(out.read_text().splitlines()) > 0
