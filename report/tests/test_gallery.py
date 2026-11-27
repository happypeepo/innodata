"""Failure gallery on the fixture results."""
import json
from pathlib import Path

import pytest

from report import analyze, gallery
from validate.checkers import load_scene

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"


@pytest.fixture(scope="module")
def tasks():
    return {t["task_id"]: t for t in analyze.read_jsonl(FIXTURES / "tasks.jsonl")}


def row(task_id, parsed, ok=True):
    return {"task_id": task_id, "model": "m", "condition": "image", "run": 0, "score": 0.0,
            "parse_ok": ok, "parsed": parsed, "raw_response": "?"}


def kind(tasks, task_id, parsed, ok=True):
    t = tasks[task_id]
    return gallery.classify(t, row(task_id, parsed, ok), load_scene(FIXTURES, t["scene_id"]))


def test_point_failures(tasks):
    scene = load_scene(FIXTURES, "s_000001")
    other = next(o for o in scene["objects"] if o["seg_id"] not in tasks["t_000001_F1_01"]["reference"]["seg_ids"]
                 and o["centroid_2d"])
    assert kind(tasks, "t_000001_F1_01", {"point": other["centroid_2d"]}) == "wrong referent"
    assert kind(tasks, "t_000001_F1_01", {"point": [2, 2]}) == "off-object"
    assert kind(tasks, "t_000001_F1_01", {"point": [5000, 10]}) == "off-object"


def test_bbox_failures(tasks):
    scene = load_scene(FIXTURES, "s_000003")
    other = next(o for o in scene["objects"] if o["seg_id"] != 5 and o["bbox_2d"])
    assert kind(tasks, "t_000003_F2_01", {"bbox": other["bbox_2d"]}) == "wrong referent"
    assert kind(tasks, "t_000003_F2_01", {"bbox": [0, 0, 10, 10]}) == "off-object"


def test_answer_failures(tasks):
    assert kind(tasks, "t_000001_F3_01", {"count": 3}) == "over-count (hallucinated)"
    assert kind(tasks, "t_000001_F3_01", {"count": 1}) == "under-count (missed under occlusion)"
    assert kind(tasks, "t_000001_F4_02", {"choice": "A"}) == "relation flip"
    assert kind(tasks, "t_000003_F9_01", {"bool": True}) == "false success"
    assert kind(tasks, "t_000002_F6_01", None, ok=False) == "unparseable"


def test_report_writes_gallery(tmp_path):
    out = tmp_path / "out"
    assert analyze.main(["--results", str(FIXTURES / "results"), "--out", str(out), "--n-boot", "100"]) == 0
    s = json.loads((out / "summary.json").read_text())
    assert s["inputs"]["data"] == str(FIXTURES)   # found next to the results folder
    wrong_image = sum(1 for r in analyze.read_jsonl(FIXTURES / "results/mock.jsonl")
                      if r["condition"] == "image" and r["score"] < 1.0)
    assert sum(f["n"] for f in s["failure_types"]) == wrong_image
    for g in s["gallery"]:
        assert (out / g["image"]).exists()
    assert "Failure gallery" in (out / "index.html").read_text()


def test_gallery_skipped_without_data(tmp_path):
    res = tmp_path / "results"
    res.mkdir()
    (res / "mock.jsonl").write_text((FIXTURES / "results/mock.jsonl").read_text())
    assert analyze.main(["--results", str(res), "--out", str(tmp_path / "out"), "--n-boot", "50"]) == 0
    assert json.loads((tmp_path / "out/summary.json").read_text())["gallery"] == []
