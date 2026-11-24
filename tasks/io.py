"""Reading scenes and writing tasks. Paths in scene.json are relative to the data folder."""

from __future__ import annotations

import json
from pathlib import Path


def load_scenes(data_dir) -> list[dict]:
    """All <data_dir>/scenes/*.json, sorted by scene_id. Each gets `_data_dir` so checkers can find files."""
    data_dir = Path(data_dir)
    files = sorted((data_dir / "scenes").glob("*.json"))
    if not files:
        raise FileNotFoundError(f"no scenes/*.json under {data_dir}")
    scenes = []
    for f in files:
        scene = json.loads(f.read_text())
        scene["_data_dir"] = str(data_dir)
        scenes.append(scene)
    return sorted(scenes, key=lambda s: s["scene_id"])


def resolve(scene: dict, key: str) -> Path:
    """Absolute path of image_path / depth_path / seg_path for a loaded scene."""
    return Path(scene["_data_dir"]) / scene[key]


def write_jsonl(path, rows) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
