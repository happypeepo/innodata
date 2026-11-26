"""Counterfactuals, F9/F10 scenes and the contact sheet, run against the fake_scene stand-in."""
import json
import sys
from random import Random

import pytest

from gen import _rebuild as rb
from gen import contact_sheet, counterfactuals, pairs
from gen.tests import fake_scene

STACK = {"layout": [
    {"shape": "box", "half": [0.03, 0.03, 0.03], "xy": [0.0, 0.0], "z": 0.0, "color": "blue"},
    {"shape": "box", "half": [0.03, 0.03, 0.03], "xy": [0.0, 0.0], "z": 0.06, "color": "red"},
    {"shape": "box", "half": [0.025, 0.025, 0.025], "xy": [0.0, 0.0], "z": 0.12, "color": "green"},
    {"shape": "cylinder", "half": [0.03, 0.03, 0.04], "xy": [0.2, 0.1], "z": 0.0, "color": "yellow"},
]}
SPREAD = {"layout": [
    {"shape": "box", "half": [0.06, 0.06, 0.02], "xy": [-0.15, 0.0], "z": 0.0, "color": "blue"},
    {"shape": "box", "half": [0.02, 0.02, 0.02], "xy": [0.1, 0.1], "z": 0.0, "color": "red"},
    {"shape": "cylinder", "half": [0.025, 0.025, 0.04], "xy": [0.15, -0.1], "z": 0.0, "color": "orange"},
]}


@pytest.fixture
def data(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "gen.scene", fake_scene)

    def make(ranges, seed=7):
        (tmp_path / "manifest.json").write_text(json.dumps({"ranges": ranges}))
        sim = fake_scene.build_scene(seed, ranges)
        scene = fake_scene.write_scene(sim, tmp_path)
        sim.close()
        return tmp_path, scene
    return make


def test_counterfactuals_follow_physics(data):
    _, scene = data(STACK)
    cf, unsettled = counterfactuals.compute(scene, STACK, fake_scene)
    assert unsettled == []
    assert cf == {"o1": {"moved": ["o2", "o3"]}, "o2": {"moved": ["o3"]}}


def test_counterfactuals_cli_writes_scene(data):
    d, _ = data(STACK)
    assert counterfactuals.main(["--data", str(d), "--workers", "1"]) == 0
    written = json.loads((d / "scenes/s_000007.json").read_text())
    assert written["counterfactuals"]["o1"]["moved"] == ["o2", "o3"]


def test_rebuild_mismatch_is_refused(data):
    _, scene = data(STACK)
    scene["objects"][0]["pose"]["xyz_m"][0] += 0.05
    with pytest.raises(rb.RebuildMismatch):
        counterfactuals.compute(scene, STACK, fake_scene)


def test_f10_moves_exactly_one_object(data):
    d, scene = data(SPREAD)
    pair = pairs.make_f10(scene, SPREAD, Random(1), d, fake_scene)
    assert pair and pair["before"] == "s_000007" and pair["after"] == "s_000007b"
    after = json.loads((d / "scenes/s_000007b.json").read_text())
    assert after["pair"] == pair
    pos = lambda s: {o["id"]: o["pose"]["xyz_m"] for o in s["objects"]}
    assert rb.displaced(pos(scene), pos(after)) == pair["moved"]
    assert rb.displaced(pos(scene), pos(after), pairs.MIN_SHIFT_M - 0.005) == pair["moved"]


@pytest.mark.parametrize("aim", [True, False])
def test_f9_label_comes_from_physics(data, aim):
    _, scene = data(SPREAD)
    sim = fake_scene.build_scene(scene["seed"], SPREAD)
    try:
        assert pairs.place_on(sim, "o2", "o1", aim, Random(3))
        assert fake_scene.settle(sim)
        assert pairs.rests_on(sim, "o2", "o1") is aim
    finally:
        rb.close(sim)


def test_f9_goal_scene_written(data):
    d, scene = data(SPREAD)
    goal = pairs.make_f9(scene, SPREAD, Random(0), d, fake_scene)
    assert goal and goal["relation"] == "on" and isinstance(goal["success"], bool)
    written = json.loads((d / "scenes/s_000007g.json").read_text())
    assert written["goal"] == goal
    # The goal object is no longer at its base-scene spot.
    pos = lambda s: {o["id"]: o["pose"]["xyz_m"] for o in s["objects"]}
    assert goal["object"] in rb.displaced(pos(scene), pos(written))


def test_contact_sheet_and_border_check(data, tmp_path):
    d, scene = data(SPREAD)
    out = tmp_path / "sheet.png"
    assert contact_sheet.main(["--data", str(d), "--out", str(out)]) == 0
    assert out.exists() and json.loads(out.with_suffix(".json").read_text())["border_touches"] == {}
    scene["objects"][0]["bbox_2d"] = [0, 10, 30, 40]
    assert contact_sheet.border_touches(scene) == ["o1"]
