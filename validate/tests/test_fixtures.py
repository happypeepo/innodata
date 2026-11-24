"""Fixtures must always pass the v1 contract checks, and the schemas must reject obvious mistakes.

Run from the repo root:  python -m pytest validate/tests
"""

import copy
import json
from pathlib import Path

import pytest

from schema.check import check, load_validator

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "fixtures"


def first_line(name):
    return json.loads((FIXTURES / name).read_text().splitlines()[0])


def test_fixtures_pass_all_checks():
    assert check(FIXTURES) == []


def test_fixtures_cover_every_family_and_answer_type():
    tasks = [json.loads(l) for l in (FIXTURES / "tasks.jsonl").read_text().splitlines()]
    assert {t["family"] for t in tasks} == {f"F{i}" for i in range(1, 11)}
    assert {t["answer_type"] for t in tasks} == {"point", "bbox", "count", "choice", "bool", "sequence", "set"}


def test_fixture_results_cover_every_case():
    rows = [json.loads(l) for l in (FIXTURES / "results" / "mock.jsonl").read_text().splitlines()]
    scores = {r["score"] for r in rows}
    assert 1.0 in scores and 0.0 in scores and any(0 < s < 1 for s in scores)
    assert any(not r["parse_ok"] for r in rows)
    assert {r["condition"] for r in rows} == {"image", "text_only"}


@pytest.mark.parametrize("mutate", [
    lambda s: s.update(image_path="data/images/s_000001.png"),       # paths are relative to the data folder
    lambda s: s["objects"][0].update(color="crimson"),               # only the 6 agreed colours
    lambda s: s["objects"][0].update(seg_id=1),                      # 1 is the table
    lambda s: s["contacts"].append({"top": "o1", "bottom": "floor"}),
])
def test_scene_schema_rejects(mutate):
    scene = json.loads((FIXTURES / "scenes" / "s_000001.json").read_text())
    bad = copy.deepcopy(scene)
    mutate(bad)
    assert list(load_validator("scene").iter_errors(bad))


@pytest.mark.parametrize("mutate", [
    lambda t: t.update(checker="exact"),                             # point tasks need a mask checker
    lambda t: t["reference"].update(point=[1, 2]),                   # point reference is seg_ids only
    lambda t: t.update(images=["data/images/s_000001.png"]),
])
def test_task_schema_rejects(mutate):
    bad = copy.deepcopy(first_line("tasks.jsonl"))
    mutate(bad)
    assert list(load_validator("task").iter_errors(bad))


def test_result_schema_rejects_score_on_parse_failure():
    row = first_line("results/mock.jsonl")
    row.update(parse_ok=False, parsed=None, score=1.0)
    assert list(load_validator("result").iter_errors(row))
