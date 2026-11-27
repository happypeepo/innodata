"""Demo logic (no Streamlit needed) on the fixtures."""
from pathlib import Path

from report import demo_core as core
from report.analyze import read_jsonl

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"


def test_browse_lookups():
    tasks = read_jsonl(FIXTURES / "tasks.jsonl")
    assert core.scene_ids(tasks)[0] == "s_000001"
    assert {t["task_id"] for t in core.tasks_for(tasks, "s_000003")} == {"t_000003_F9_01", "t_000003_F2_01",
                                                                         "t_000003_F3_01"}
    results = core.load_results(FIXTURES / "results")
    image = core.answers(results["t_000001_F1_01"], "image")
    text = core.answers(results["t_000001_F1_01"], "text_only")
    assert [r["model"] for r in image] == ["mock"] and image[0]["condition"] == "image"
    assert text[0]["condition"] == "text_only"
    assert core.answers(results["t_000002_F5_01"], "text_only") == []


def test_answers_take_first_run():
    rows = [{"model": "m", "condition": "image", "run": 1, "score": 0.0},
            {"model": "m", "condition": "image", "run": 0, "score": 1.0}]
    assert core.answers(rows)[0]["run"] == 0


def test_verdict():
    assert [core.verdict({"score": s}) for s in (1.0, 0.67, 0.0, None)] == ["✓", "◐", "✗", "✗"]


def test_missing_results_folder_is_empty():
    assert core.load_results(FIXTURES / "nope") == {}


def test_mock_needs_no_key():
    assert core.has_key("mock")
