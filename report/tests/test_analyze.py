"""report.analyze on the fixture results (fixtures/results/mock.jsonl + fixtures/tasks.jsonl)."""
import json
from pathlib import Path

import numpy as np
import pytest

from report import analyze, charts

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"


@pytest.fixture(scope="module")
def summary():
    rows = analyze.join(analyze.load_results(FIXTURES / "results"), analyze.read_jsonl(FIXTURES / "tasks.jsonl"))
    return analyze.analyse(rows, seed=0, n_boot=500)


def by(rows, **kw):
    return [r for r in rows if all(r[k] == v for k, v in kw.items())]


def test_headline_counts_only_exact(summary):
    image = by(summary["overall"], condition="image")[0]
    # 9 of 16 tasks score 1.0; F8's 0.6667 is partial credit, not a correct answer.
    assert image["n_tasks"] == 16 and image["accuracy"] == pytest.approx(9 / 16)
    assert image["mean_score"] == pytest.approx((9 + 0.6667) / 16)
    assert image["parse_fail_rate"] == pytest.approx(1 / 16)
    assert image["ci_low"] <= image["accuracy"] <= image["ci_high"]


def test_by_family(summary):
    f8 = by(summary["by_family"], condition="image", family="F8")[0]
    assert (f8["n_tasks"], f8["accuracy"]) == (2, 0.5)
    assert summary["families"][-1] == "F10"   # numeric order, not F1, F10, F2


def test_gap_uses_tasks_in_both_conditions(summary):
    gaps = {g["family"]: g for g in summary["gap"]}
    assert set(gaps) == {"F1", "F2", "F3", "F4"}
    assert gaps["F2"]["gap"] == 1.0 and gaps["F3"]["gap"] == -1.0


def test_knob_bins(summary):
    n_obj = {r["bin"]: r["n_tasks"] for r in by(summary["by_knob"], condition="image", knob="n_objects")}
    assert n_obj == {"3–6": 11, "7–10": 5}
    assert analyze.knob_bin("distractors", 5) == (3, "3+")
    assert analyze.knob_bin("lighting", "dim") == (0, "dim")


def test_runs_are_averaged_per_task():
    rows = [{"task_id": "t_1_F1_01", "model": "m", "condition": "image", "run": run, "score": s, "parse_ok": True,
             "family": "F1", "difficulty": {}} for run, s in ((0, 1.0), (1, 0.0), (2, 1.0))]
    s = analyze.analyse(rows, n_boot=100)
    assert s["overall"][0]["n_tasks"] == 1 and s["overall"][0]["accuracy"] == pytest.approx(2 / 3)
    assert s["runs"][0]["n_runs"] == 3


def test_bootstrap_is_seeded():
    v = [1, 0, 1, 1, 0, 0, 1]
    assert analyze.bootstrap_ci(v, np.random.default_rng(3)) == analyze.bootstrap_ci(v, np.random.default_rng(3))


def test_family_from_task_id_without_tasks_file():
    rows = analyze.join([{"task_id": "t_000123_F10_02"}], [])
    assert rows[0]["family"] == "F10" and rows[0]["difficulty"] == {}


def test_model_colours_follow_the_model():
    a = charts.model_slots(["claude-opus-5-5", "mock"], analyze.MODEL_ORDER)
    b = charts.model_slots(["gemini-robotics-er-2-preview", "claude-opus-5-5", "new-model"], analyze.MODEL_ORDER)
    assert a["claude-opus-5-5"] == b["claude-opus-5-5"]
    assert b["new-model"] not in a.values()


def test_cli_writes_report(tmp_path):
    out = tmp_path / "out"
    assert analyze.main(["--results", str(FIXTURES / "results"), "--out", str(out), "--n-boot", "200"]) == 0
    for name in ("summary.json", "summary.md", "index.html", "by_family.csv", "by_knob.csv", "gap.csv"):
        assert (out / name).exists(), name
    s = json.loads((out / "summary.json").read_text())
    assert s["inputs"]["tasks_file"].endswith("tasks.jsonl")   # found next to the results folder
    assert "<svg" in (out / "index.html").read_text()


def test_cli_refuses_empty_folder(tmp_path):
    assert analyze.main(["--results", str(tmp_path)]) == 1
