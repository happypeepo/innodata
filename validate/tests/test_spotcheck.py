"""Tests for validate/spotcheck.py (phase 8). The interactive loop is driven with scripted answers.

Run from the repo root:  python -m pytest tasks/tests validate/tests
"""

import json
from collections import Counter
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from tasks.__main__ import generate, main
from tasks.io import load_scenes
from validate.spotcheck import (answer_text, apply_to_tasks, draw_overlay, load_results, review, sample,
                                summarise)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"


@pytest.fixture(scope="module")
def scenes():
    return {s["scene_id"]: s for s in load_scenes(FIXTURES)}


@pytest.fixture(scope="module")
def tasks():
    return generate(FIXTURES, seed=0)[0]


def test_sample_spreads_across_families_and_is_seeded(tasks):
    picked = sample(tasks, 20, seed=0)
    fams = Counter(t["family"] for t in picked)
    assert len(picked) == 20 and len(fams) == 10          # every family represented
    assert max(fams.values()) <= 3                        # round-robin: no family dominates
    assert [t["task_id"] for t in sample(tasks, 20, 0)] == [t["task_id"] for t in picked]
    assert [t["task_id"] for t in sample(tasks, 20, 1)] != [t["task_id"] for t in picked]
    assert len(sample(tasks, 500, 0)) == len(tasks)


def test_answer_text_for_every_answer_type(tasks, scenes):
    seen = set()
    for t in tasks:
        text = answer_text(t, scenes[t["scene_id"]])
        assert text and "{" not in text, t["task_id"]
        seen.add(t["answer_type"])
    assert seen == {"point", "bbox", "count", "choice", "bool", "sequence", "set"}


def test_overlay_keeps_true_object_colours(tasks, scenes):
    t = next(t for t in tasks if t["family"] == "F1")
    scene = scenes[t["scene_id"]]
    img = draw_overlay(t, scene, FIXTURES, scenes)
    raw = np.array(Image.open(FIXTURES / t["images"][0]).convert("RGB"))
    seg = np.array(Image.open(FIXTURES / scene["seg_path"]))
    target = seg == t["reference"]["seg_ids"][0]
    body = np.array(img)[img.height - raw.shape[0]:]
    assert (body[target] == raw[target]).all()       # object pixels untouched (outline sits outside)
    assert (body != raw).any()                         # but something was drawn


def test_f10_overlay_shows_both_images(tasks, scenes):
    t = next(t for t in tasks if t["family"] == "F10")
    img = draw_overlay(t, scenes[t["scene_id"]], FIXTURES, scenes)
    assert img.width > 2 * 1024 - 1


def scripted(answers):
    it = iter(answers)
    return lambda prompt="": next(it)


def test_review_records_resumes_and_summarises(tasks, scenes, tmp_path):
    out = tmp_path / "human.jsonl"
    # y, n (+ note), s, then q
    s1 = review(tasks, scenes, FIXTURES, 0, "tester", n=10, out_path=out,
                ask=scripted(["y", "n", "wrong support", "s", "q"]), overlay_dir=tmp_path / "png")
    rows = load_results(out)
    assert len(rows) == 2 and s1["checked"] == 2 and s1["agreement_pct"] == 50.0
    assert {r["verdict"] for r in rows.values()} == {"agree", "disagree"}
    assert any(r["note"] == "wrong support" for r in rows.values())
    assert all(r["reviewer"] == "tester" for r in rows.values())
    # resume: already-checked tasks are not asked again; an invalid key is re-asked
    s2 = review(tasks, scenes, FIXTURES, 0, "tester", n=10, out_path=out,
                ask=scripted(["maybe", "y"] + ["y"] * 20), overlay_dir=tmp_path / "png")
    # 10 sampled, 2 saved before; the skipped one wasn't saved, so it is asked again -> 10 in total
    assert s2["checked"] == 10 and len(load_results(out)) == 10


def test_stale_verdicts_are_not_counted(tasks):
    t = tasks[0]
    results = {t["task_id"]: {"task_id": t["task_id"], "question": "an older question", "verdict": "agree"}}
    s = summarise(results, tasks)
    assert s["checked"] == 0 and s["stale"] == 1


def test_apply_and_report_via_cli(tasks, tmp_path):
    out = tmp_path / "tasks.jsonl"
    assert main(["--data", str(FIXTURES), "--out", str(out)]) == 0
    final = [json.loads(l) for l in out.read_text().splitlines()]
    human = tmp_path / "human.jsonl"
    rows = [{"task_id": final[0]["task_id"], "question": final[0]["question"], "verdict": "agree"},
            {"task_id": final[1]["task_id"], "question": final[1]["question"], "verdict": "disagree"}]
    human.write_text("".join(json.dumps(r) + "\n" for r in rows))
    assert main(["--data", str(FIXTURES), "--out", str(out), "--human-check", str(human)]) == 0
    final = [json.loads(l) for l in out.read_text().splitlines()]
    assert final[0]["validated"]["human"] is True and final[1]["validated"]["human"] is False
    assert all(t["validated"]["human"] is None for t in final[2:])
    report = json.loads((tmp_path / "validation_report.json").read_text())["human_check"]
    assert report["checked"] == 2 and report["agreement_pct"] == 50.0 and report["reference_error_rate_pct"] == 50.0
