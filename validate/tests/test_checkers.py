"""Unit tests for validate/checkers.py: a right answer, a wrong answer and edge cases per checker,
plus score() on the fixtures and a re-score of fixtures/results/mock.jsonl.

Run from the repo root:  python -m pytest validate/tests
"""

import json
from pathlib import Path

import numpy as np
import pytest

from validate.checkers import (bbox_iou, exact, iou, load_scene, point_in_any_mask, point_in_mask,
                               score, seg_array, set_f1, topo_order_valid)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"


@pytest.fixture
def seg():
    """4 rows x 6 cols. Table = 1, object 3 at columns 1-2, object 5 at column 4, row 0 is floor (0)."""
    s = np.ones((4, 6), np.uint8)
    s[0, :] = 0
    s[1:, 1:3] = 3
    s[1:, 4] = 5
    return s


# ---------------------------------------------------------------- point_in_mask

def test_point_hit(seg):
    assert point_in_mask([1, 2], seg, [3]) == 1.0          # [x, y]


def test_point_uses_x_then_y(seg):
    # [x=4, y=1] is object 5; the swapped [1, 4] would be off the image (only 4 rows).
    assert point_in_mask([4, 1], seg, [5]) == 1.0
    assert point_in_mask([1, 4], seg, [5]) == 0.0


def test_point_on_table_or_floor_scores_zero(seg):
    assert point_in_mask([0, 2], seg, [3]) == 0.0          # table (1)
    assert point_in_mask([1, 0], seg, [3]) == 0.0          # floor/nothing (0)


def test_point_on_other_object_scores_zero(seg):
    assert point_in_mask([4, 2], seg, [3]) == 0.0


def test_point_fractional_pixels_floor(seg):
    assert point_in_mask([2.99, 3.5], seg, [3]) == 1.0
    assert point_in_mask([3.0, 3.5], seg, [3]) == 0.0


@pytest.mark.parametrize("bad", [[-1, 2], [6, 2], [1, 4], [1], [1, 2, 3], "1,2", None,
                                 [float("nan"), 1], [True, 1], ["1", "2"]])
def test_point_bad_answers_score_zero(seg, bad):
    assert point_in_mask(bad, seg, [3]) == 0.0


def test_point_in_any_mask(seg):
    assert point_in_any_mask([4, 3], seg, [3, 5]) == 1.0
    assert point_in_any_mask([1, 3], seg, [3, 5]) == 1.0
    assert point_in_any_mask([0, 3], seg, [3, 5]) == 0.0


# ---------------------------------------------------------------- bbox_iou

def test_iou_values():
    assert iou([0, 0, 10, 10], [0, 0, 10, 10]) == 1.0
    assert iou([0, 0, 10, 10], [10, 10, 20, 20]) == 0.0
    assert iou([0, 0, 10, 10], [5, 0, 15, 10]) == pytest.approx(50 / 150)


def test_bbox_threshold_is_half():
    ref = [0, 0, 10, 10]
    assert bbox_iou([0, 0, 10, 5], ref) == 1.0            # IoU exactly 0.5 passes
    assert bbox_iou([0, 0, 10, 4.9], ref) == 0.0


def test_bbox_corner_order_does_not_matter():
    assert bbox_iou([10, 10, 0, 0], [0, 0, 10, 10]) == 1.0


@pytest.mark.parametrize("bad", [[0, 0, 10], None, "0,0,10,10", [0, 0, 0, 0], [0, 0, "a", 1]])
def test_bbox_bad_answers_score_zero(bad):
    assert bbox_iou(bad, [0, 0, 10, 10]) == 0.0


# ---------------------------------------------------------------- exact

@pytest.mark.parametrize("pred,ref,want", [
    (4, 4, 1.0), (3, 4, 0.0), (4.0, 4, 1.0), (4.5, 4, 0.0), ("4", 4, 0.0),
    ("b", "B", 1.0), (" B ", "B", 1.0), ("A", "B", 0.0),
    (True, True, 1.0), (False, True, 0.0), (1, True, 0.0), (True, 1, 0.0), ("true", True, 0.0),
    (None, 4, 0.0), ([4], 4, 0.0),
])
def test_exact(pred, ref, want):
    assert exact(pred, ref) == want


# ---------------------------------------------------------------- topo_order_valid

OPTS = {"A": "o1", "B": "o3", "C": "o2"}
PRECEDES = [["B", "C"], ["C", "A"]]  # o3 on o2 on o1: clear top first


def test_topo_valid_order():
    assert topo_order_valid(["B", "C", "A"], OPTS, PRECEDES) == 1.0
    assert topo_order_valid(["b", "c", "a"], OPTS, PRECEDES) == 1.0


def test_topo_any_valid_order_counts():
    # D is a free object with no constraint, so it can go anywhere.
    opts = {**OPTS, "D": "o4"}
    for seq in (["D", "B", "C", "A"], ["B", "D", "C", "A"], ["B", "C", "A", "D"]):
        assert topo_order_valid(seq, opts, PRECEDES) == 1.0


@pytest.mark.parametrize("seq", [["A", "C", "B"], ["C", "B", "A"], ["B", "C"], ["B", "C", "A", "A"],
                                 ["B", "C", "A", "Z"], "BCA", None, [1, 2, 3]])
def test_topo_invalid(seq):
    assert topo_order_valid(seq, OPTS, PRECEDES) == 0.0


# ---------------------------------------------------------------- set_f1

@pytest.mark.parametrize("pred,ref,want", [
    (["A", "B"], ["A", "B"], 1.0), (["b", "a"], ["A", "B"], 1.0),
    (["A"], ["A", "B"], 2 / 3), (["A", "B", "C"], ["A", "B"], 0.8),
    (["C"], ["A", "B"], 0.0), ([], ["A"], 0.0), (["A"], [], 0.0), ([], [], 1.0),
    (["A", "A"], ["A"], 1.0), (None, ["A"], 0.0), ("AB", ["A", "B"], 0.0),
])
def test_set_f1(pred, ref, want):
    assert set_f1(pred, ref) == pytest.approx(want)


# ---------------------------------------------------------------- score() on fixtures

def tasks():
    return [json.loads(l) for l in (FIXTURES / "tasks.jsonl").read_text().splitlines()]


def test_score_reference_answers_score_one():
    """Turning each fixture reference into a model answer must score 1.0 (point: a pixel inside the mask)."""
    for t in tasks():
        scene = load_scene(FIXTURES, t["scene_id"])
        ref, typ = t["reference"], t["answer_type"]
        if typ == "point":
            obj = next(o for o in scene["objects"] if o["seg_id"] == ref["seg_ids"][0])
            ys, xs = np.nonzero(seg_array(scene) == obj["seg_id"])
            ans = [int(xs[len(xs) // 2]), int(ys[len(ys) // 2])]
        else:
            ans = ref[typ]
        assert score(t, {typ: ans}, scene) == 1.0, t["task_id"]


def test_score_bad_parsed_is_zero_not_crash():
    t = tasks()[0]
    scene = load_scene(FIXTURES, t["scene_id"])
    for parsed in (None, {}, {"bbox": [0, 0, 1, 1]}, {"point": "middle"}, "point"):
        assert score(t, parsed, scene) == 0.0


def test_score_unknown_checker_raises():
    t = dict(tasks()[0], checker="vibes")
    with pytest.raises(ValueError):
        score(t, {"point": [0, 0]}, load_scene(FIXTURES, t["scene_id"]))


def test_score_missing_seg_raises():
    t = tasks()[0]
    scene = load_scene(FIXTURES, t["scene_id"])
    scene["_data_dir"] = "/nonexistent"
    with pytest.raises(FileNotFoundError):
        score(t, {"point": [0, 0]}, scene)


def test_rescore_mock_results_matches_stored_scores():
    by_id = {t["task_id"]: t for t in tasks()}
    rows = [json.loads(l) for l in (FIXTURES / "results" / "mock.jsonl").read_text().splitlines()]
    for r in rows:
        t = by_id[r["task_id"]]
        got = score(t, r["parsed"], load_scene(FIXTURES, t["scene_id"])) if r["parse_ok"] else 0.0
        assert got == pytest.approx(r["score"], abs=1e-4), r["task_id"]
