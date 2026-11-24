"""Tests for validate/rules.py, validate/balance.py and the validation report (phase 5).

Run from the repo root:  python -m pytest tasks/tests validate/tests
"""

import copy
import json
from collections import Counter
from pathlib import Path

import pytest

from tasks.__main__ import generate, main
from tasks.io import load_scenes
from validate.balance import MAX_PER_FAMILY, answer_key, balance, baselines, spread_letters
from validate.rules import check

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"


@pytest.fixture(scope="module")
def scenes():
    return {s["scene_id"]: s for s in load_scenes(FIXTURES)}


@pytest.fixture(scope="module")
def tasks():
    return generate(FIXTURES, seed=0)[0]


def first(tasks, family):
    return copy.deepcopy(next(t for t in tasks if t["family"] == family))


# ---------------------------------------------------------------- rules

def test_every_generated_task_passes_the_rules(tasks, scenes):
    for t in tasks:
        assert check(t, scenes) == [], t["task_id"]


def test_visibility_rule(tasks, scenes):
    t = first(tasks, "F1")
    s = copy.deepcopy(scenes)
    target = next(o for o in s[t["scene_id"]]["objects"] if o["seg_id"] == t["reference"]["seg_ids"][0])
    target["visible_frac"] = 0.2
    assert "F1 visibility: target under 30% visible" in check(t, s)


def test_unique_rule_catches_a_new_lookalike(tasks, scenes):
    t = first(tasks, "F1")
    s = copy.deepcopy(scenes)
    scene = s[t["scene_id"]]
    target = next(o for o in scene["objects"] if o["id"] == t["evidence"][0])
    twin = dict(copy.deepcopy(target), id="o99", seg_id=99)
    scene["objects"].append(twin)
    assert any("unique" in r for r in check(t, s))


def test_f4_margin_and_label(tasks, scenes):
    t = first(tasks, "F4")
    s = copy.deepcopy(scenes)
    objs = {o["id"]: o for o in s[t["scene_id"]]["objects"]}
    objs[t["evidence"][-1]]["depth_mean_m"] = objs[t["evidence"][0]]["depth_mean_m"] + 0.01
    assert "F4 margin: depth gap under 3 cm" in check(t, s)
    t2 = first(tasks, "F4")
    ref = t2["reference"]
    ref["choice"] = next(k for k in ref["options"] if k != ref["choice"])
    assert "F4 label: answer disagrees with depth" in check(t2, scenes)


@pytest.mark.parametrize("family,mutate,reason", [
    ("F3", lambda r: r.update(count=r["count"] + 1), "F3 count: answer disagrees with visible objects"),
    ("F5", lambda r: r.update(choice=next(k for k in r["options"] if k != r["choice"])),
     "F5 support: answer disagrees with contacts"),
    ("F6", lambda r: r["seg_ids"].pop(), "F6 support: free-to-pick set disagrees with contacts"),
    ("F7", lambda r: r.update(precedes=[[b, a] for a, b in r["precedes"]]),
     "F7 support: precedes disagrees with contacts"),
    ("F8", lambda r: r.update(set=[]), "F8 label: answer disagrees with counterfactuals"),
    ("F9", lambda r: r.update(bool=not r["bool"]), "F9 label: answer disagrees with goal.success"),
    ("F10", lambda r: r.update(choice=next(k for k in r["options"] if k != r["choice"])),
     "F10 label: answer disagrees with pair.moved"),
])
def test_label_rules(tasks, scenes, family, mutate, reason):
    t = first(tasks, family)
    mutate(t["reference"])
    assert reason in check(t, scenes)


# ---------------------------------------------------------------- balancing

def fake(i, family, atype, ref, n_opts=4):
    t = {"task_id": f"t_{i:06d}_{family}_01", "family": family, "answer_type": atype, "reference": ref,
         "question": "Q?"}
    if atype == "choice":
        letters = "ABCD"[:n_opts]
        t["question"] = "Q? Options: " + " ".join(f"{k}) opt{k}" for k in letters)
    return t


def bool_tasks(n_true, n_false):
    return [fake(i, "F9", "bool", {"bool": i < n_true}) for i in range(n_true + n_false)]


def test_bool_answers_are_evened_out():
    kept, drops = balance(bool_tasks(300, 80), seed=0)
    assert Counter(t["reference"]["bool"] for t in kept) == {True: 50, False: 50}  # cap 100, half each
    assert drops["balance F9: evened out answers"] == 220
    assert baselines(kept)["F9"]["near_chance"] is True


def test_family_cap_keeps_balance():
    tasks = [fake(i, "F4", "choice", {"choice": "A", "options": {"A": "in front of" if i % 3 else "behind",
                                                                  "B": "behind" if i % 3 else "in front of"}}, 2)
             for i in range(600)]
    kept, drops = balance(tasks, seed=1)
    assert len(kept) == MAX_PER_FAMILY
    meanings = Counter(answer_key(t) for t in kept)
    assert meanings["in front of"] == meanings["behind"] == 50
    letters = Counter(t["reference"]["choice"] for t in kept)
    assert letters["A"] == letters["B"] == 50
    assert baselines(kept)["F4"]["near_chance"] is True


def test_count_majority_capped_to_runner_up():
    tasks = [fake(i, "F3", "count", {"count": 1 if i < 70 else (2 if i < 90 else 3)}) for i in range(100)]
    kept, _ = balance(tasks, seed=0)
    counts = Counter(t["reference"]["count"] for t in kept)
    assert counts[1] == counts[2] == 20 and counts[3] == 10
    assert baselines(kept)["F3"]["near_chance"] is True


def test_spread_letters_moves_only_the_position():
    t = fake(0, "F5", "choice", {"choice": "A", "options": {"A": "o1", "B": "table", "C": "o2", "D": "o3"}})
    t["question"] = "What is it on? Options: A) red box B) table C) blue sphere D) green box left of the red box"
    tasks = [copy.deepcopy(t) for _ in range(8)]
    for i, x in enumerate(tasks):
        x["task_id"] = f"t_{i:06d}_F5_01"
    out = spread_letters(tasks, seed=0)
    assert Counter(x["reference"]["choice"] for x in out) == {"A": 2, "B": 2, "C": 2, "D": 2}
    for x in out:
        ref = x["reference"]
        assert ref["options"][ref["choice"]] == "o1"                       # same answer
        assert f'{ref["choice"]}) red box' in x["question"]                 # label moved with it
        assert "green box left of the red box" in x["question"]
        assert sorted(ref["options"].values()) == ["o1", "o2", "o3", "table"]


def test_balance_is_deterministic():
    a, _ = balance(bool_tasks(200, 120), seed=5)
    b, _ = balance(bool_tasks(200, 120), seed=5)
    assert [t["task_id"] for t in a] == [t["task_id"] for t in b]


def test_open_answer_families_have_no_majority_baseline():
    t = fake(0, "F1", "point", {"seg_ids": [2]})
    assert baselines([t])["F1"]["majority_baseline"] is None


# ---------------------------------------------------------------- report via the CLI

def test_cli_writes_report_and_is_deterministic(tmp_path):
    out_a, out_b = tmp_path / "a" / "tasks.jsonl", tmp_path / "b" / "tasks.jsonl"
    assert main(["--data", str(FIXTURES), "--out", str(out_a)]) == 0
    assert main(["--data", str(FIXTURES), "--out", str(out_b)]) == 0
    rep_a = (out_a.parent / "validation_report.json").read_text()
    assert rep_a == (out_b.parent / "validation_report.json").read_text()
    assert out_a.read_text() == out_b.read_text()
    report = json.loads(rep_a)
    assert set(report) >= {"counts", "tasks_per_family", "rejections_by_reason", "majority_baseline",
                           "independent_check", "human_check"}
    assert report["counts"]["final"] == len(out_a.read_text().splitlines())
    assert report["independent_check"]["agreement_pct"] is None  # phase 7
    assert report["human_check"]["agreement_pct"] is None        # phase 8
