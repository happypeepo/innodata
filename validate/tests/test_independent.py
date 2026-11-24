"""Tests for validate/independent.py (phase 7): it must agree with correct references, catch wrong
ones, and never depend on A's contacts / counterfactuals / goal / pair.moved.

Run from the repo root:  python -m pytest tasks/tests validate/tests
"""

import copy
import json
from pathlib import Path

import pytest

from tasks.__main__ import generate, main
from tasks.io import load_scenes
from validate.independent import NOT_CHECKABLE, SceneGeometry, check_scene_fields, check_task, run

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"


@pytest.fixture(scope="module")
def scenes():
    return {s["scene_id"]: s for s in load_scenes(FIXTURES)}


@pytest.fixture(scope="module")
def tasks():
    return generate(FIXTURES, seed=0)[0]


def verdict(task, scenes):
    scene = scenes[task["scene_id"]]
    return check_task(task, scene, SceneGeometry(scene, FIXTURES), scenes, FIXTURES)[0]


def first(tasks, family, pred=lambda t: True):
    return copy.deepcopy(next(t for t in tasks if t["family"] == family and pred(t)))


def test_support_graph_matches_contacts_from_pixels_alone(scenes):
    for s in scenes.values():
        graph = SceneGeometry(s, FIXTURES).support_graph()
        for c in s["contacts"]:
            obj = next(o for o in s["objects"] if o["id"] == c["top"])
            if obj["pixel_area"] == 0:
                continue  # fully hidden: nothing to see
            assert graph[c["top"]] == c["bottom"], (s["scene_id"], c)


def test_correct_references_agree(tasks, scenes):
    result = run(tasks, scenes, FIXTURES)
    assert result["agreement_pct"] == 100.0
    assert result["disagreements"] == []
    assert result["scene_fields"]["problems"] == 0
    for fam in NOT_CHECKABLE:
        assert result["per_family"][fam]["status"] == "not checkable"


def test_never_reads_relations(tasks, scenes):
    """Same verdicts after deleting contacts, occludes, counterfactuals, goal and pair.moved."""
    stripped = copy.deepcopy(scenes)
    for s in stripped.values():
        s["contacts"], s["occludes"], s["counterfactuals"] = [], [], {}
        s.pop("goal", None)
        if s.get("pair"):
            s["pair"]["moved"] = []
    for t in tasks:
        assert verdict(t, scenes) == verdict(t, stripped), t["task_id"]


def other_letter(ref):
    return next(k for k in ref["options"] if k != ref["choice"])


@pytest.mark.parametrize("family,pred,mutate", [
    ("F2", lambda t: " of the " in t["question"], lambda t: t.update(question=t["question"].replace("left of", "RIGHT").replace("right of", "left of").replace("RIGHT", "right of"))),
    ("F2", lambda t: True, lambda t: t["reference"].update(bbox=[b + 40 for b in t["reference"]["bbox"]])),
    ("F3", lambda t: True, lambda t: t["reference"].update(count=t["reference"]["count"] + 1)),
    ("F4", lambda t: True, lambda t: t["reference"].update(choice=other_letter(t["reference"]))),
    ("F5", lambda t: True, lambda t: t["reference"].update(choice=other_letter(t["reference"]))),
    ("F7", lambda t: True, lambda t: t["reference"].update(precedes=[[b, a] for a, b in t["reference"]["precedes"]])),
    ("F10", lambda t: True, lambda t: t["reference"].update(choice=other_letter(t["reference"]))),
])
def test_wrong_references_disagree(tasks, scenes, family, pred, mutate):
    candidates = [t for t in tasks if t["family"] == family and pred(t) and verdict(t, scenes) == "agree"]
    if not candidates:
        pytest.skip(f"no decided {family} task in fixtures")
    t = copy.deepcopy(candidates[0])
    mutate(t)
    assert verdict(t, scenes) == "disagree", t["task_id"]


def test_f6_disagrees_when_a_supporting_object_is_called_free(tasks, scenes):
    t = first(tasks, "F6")
    scene = scenes[t["scene_id"]]
    bottom = next(o for o in scene["objects"] if o["id"] == "o1")  # the yellow box under the stack
    t["reference"]["seg_ids"].append(bottom["seg_id"])
    assert verdict(t, scenes) == "disagree"


def test_scene_field_bugs_are_caught(scenes):
    s = copy.deepcopy(scenes["s_000001"])
    s["objects"][0]["pixel_area"] += 10
    s["objects"][1]["bbox_2d"] = [0, 0, 5, 5]
    s["objects"][2]["depth_mean_m"] += 0.05
    problems = check_scene_fields(s, SceneGeometry(s, FIXTURES))
    assert len(problems) == 3


def test_report_carries_independent_result(tmp_path):
    out = tmp_path / "tasks.jsonl"
    assert main(["--data", str(FIXTURES), "--out", str(out)]) == 0
    report = json.loads((tmp_path / "validation_report.json").read_text())
    ind = report["independent_check"]
    assert ind["status"] == "run" and ind["agreement_pct"] == 100.0
    assert "never reads contacts" in ind["method"]
