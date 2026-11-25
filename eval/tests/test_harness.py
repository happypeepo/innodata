import json
from pathlib import Path

import jsonschema
import pytest

from eval import pricing, prompt
from eval.__main__ import RESULT_SCHEMA, load_tasks, main
from eval.models.base import Adapter, Reply
from eval.models import REGISTRY
from validate.checkers import load_scene, score

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
TASKS = FIXTURES / "tasks.jsonl"


def test_prompt_for_every_fixture_task():
    for t in load_tasks(TASKS):
        system, parts = prompt.build(t, FIXTURES, "image")
        images = [v for k, v in parts if k == "image"]
        assert len(images) == len(t["images"]) and all(p.is_file() for p in images)
        assert t["question"] in parts[-1][1] and t["answer_format"] in parts[-1][1]
        _, no_img = prompt.build(t, FIXTURES, "text_only")
        assert not [1 for k, _ in no_img if k == "image"]
    f10 = next(t for t in load_tasks(TASKS) if t["family"] == "F10")
    texts = [v for k, v in prompt.build(f10, FIXTURES, "image")[1] if k == "text"]
    assert texts[0].startswith("Image 1 (before)") and texts[1].startswith("Image 2 (after)")


def test_prompt_hash_tracks_condition_and_wording():
    t = load_tasks(TASKS)[0]
    h = prompt.prompt_hash(*prompt.build(t, FIXTURES, "image"))
    assert h == prompt.prompt_hash(*prompt.build(t, FIXTURES, "image"))
    assert h != prompt.prompt_hash(*prompt.build(t, FIXTURES, "text_only"))
    assert h != prompt.prompt_hash(*prompt.build({**t, "question": t["question"] + "?"}, FIXTURES, "image"))


def run(tmp_path, *extra, model="mock"):
    return main(["--model", model, "--data", str(FIXTURES), "--tasks", str(TASKS), "--out", str(tmp_path), *extra])


def rows(tmp_path, model="mock"):
    return [json.loads(l) for l in (tmp_path / f"{model}.jsonl").read_text().splitlines()]


def test_mock_end_to_end_rows_are_valid_and_rescore(tmp_path):
    assert run(tmp_path, "--condition", "image", "--condition", "text_only") == 0
    out = rows(tmp_path)
    assert len(out) == 2 * len(load_tasks(TASKS))
    tasks = {t["task_id"]: t for t in load_tasks(TASKS)}
    for r in out:
        jsonschema.validate(r, RESULT_SCHEMA)
        assert r["parse_ok"]
        t = tasks[r["task_id"]]
        assert score(t, r["parsed"], load_scene(FIXTURES, t["scene_id"])) == r["score"]
    meta = json.loads((tmp_path / "mock.meta.json").read_text())
    assert meta["rows"] == len(out) and meta["parse_failures"] == 0


def test_same_seed_same_results(tmp_path):
    run(tmp_path / "a"); run(tmp_path / "b")
    strip = lambda rs: [{k: v for k, v in r.items() if k != "latency_ms"} for r in rs]
    assert strip(rows(tmp_path / "a")) == strip(rows(tmp_path / "b"))


class Counting(Adapter):
    name = "counting"
    model_id = "counting"
    calls = 0
    fail_on = set()

    def ask(self, system, parts, *, task, condition, run):
        type(self).calls += 1
        if task["task_id"] in self.fail_on:
            raise ValueError("simulated")  # permanent: no back-off in tests
        return Reply(text=json.dumps({task["answer_type"]: 1 if task["answer_type"] == "count" else None}),
                     tokens_in=10, tokens_out=2)


@pytest.fixture
def counting(monkeypatch):
    Counting.calls, Counting.fail_on = 0, set()
    monkeypatch.setitem(REGISTRY, "counting", Counting)
    monkeypatch.setitem(pricing.PRICES, "counting", (1.0, 1.0))  # $1 per 1M tokens
    return Counting


def test_resume_never_repeats_a_call(tmp_path, counting):
    n = len(load_tasks(TASKS))
    run(tmp_path, "--limit", "5", model="counting")
    assert counting.calls == 5
    run(tmp_path, model="counting")                    # picks up where it stopped
    assert counting.calls == n
    run(tmp_path, model="counting")                    # nothing left to ask
    assert counting.calls == n
    run(tmp_path, "--runs", "2", model="counting")    # only the new run index is asked
    assert counting.calls == 2 * n
    assert len(rows(tmp_path, "counting")) == 2 * n


def test_failed_calls_are_reported_then_retried(tmp_path, counting):
    first = load_tasks(TASKS)[0]["task_id"]
    counting.fail_on = {first}
    assert run(tmp_path, model="counting") == 1                       # non-zero exit on errors
    bad = [r for r in rows(tmp_path, "counting") if r["task_id"] == first]
    assert bad[0]["error"].startswith("ValueError") and bad[0]["score"] == 0 and not bad[0]["parse_ok"]
    counting.fail_on = set()
    calls = counting.calls
    assert run(tmp_path, model="counting") == 0
    assert counting.calls == calls + 1                                # only the failed one is re-asked
    assert all(r["error"] is None for r in rows(tmp_path, "counting"))


def test_unparseable_answers_score_zero_but_are_kept(tmp_path, counting):
    run(tmp_path, model="counting")  # answers {"point": null} etc. -> parse failures
    out = rows(tmp_path, "counting")
    assert any(not r["parse_ok"] and r["parsed"] is None and r["score"] == 0 for r in out)
    assert any(r["parse_ok"] for r in out)  # count tasks answered 1 parse fine


def test_cost_comes_from_tokens_and_price_table(tmp_path, counting):
    run(tmp_path, model="counting")
    assert all(r["cost_usd"] == 12 / 1e6 for r in rows(tmp_path, "counting"))  # (10 in + 2 out) x $1/M


def test_budget_stops_asking_and_leaves_the_rest_for_later(tmp_path, counting, monkeypatch):
    monkeypatch.setitem(pricing.PRICES, "counting", (100_000.0, 0.0))  # $1 per call
    assert run(tmp_path, "--budget-usd", "2.5", "--workers", "1", model="counting") == 1
    assert counting.calls == 3                                        # stops once $2.5 is passed
    out = rows(tmp_path, "counting")
    assert sum(r["error"] is None for r in out) == 3
    assert all(r["error"].startswith("BudgetExceeded") for r in out if r["error"])


def test_dry_run_calls_nothing(tmp_path, counting, capsys):
    assert main(["--model", "er2", "--data", str(FIXTURES), "--tasks", str(TASKS), "--out", str(tmp_path),
                 "--dry-run"]) == 0
    assert "calls to make" in capsys.readouterr().out and not (tmp_path / "gemini-robotics-er-2-preview.jsonl").exists()
