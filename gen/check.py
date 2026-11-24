"""Self-check for generated scenes: `python -m gen.check --data data/`.

For every scenes/*.json: the image, depth and seg files exist with the right size; the seg PNG
holds only 0 (nothing/floor), 1 (table) and the scene's object seg_ids; and each object's pixel
count in the seg PNG equals its pixel_area (so visible objects appear and hidden ones don't).
Exits 1 if any scene fails.
"""

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

from gen.scene import SEG_BACKGROUND, SEG_TABLE


def check_scene(data: Path, scene_file: Path) -> list[str]:
    s = json.loads(scene_file.read_text())
    errors = []
    size = (s["width"], s["height"])
    for key in ("image_path", "depth_path", "seg_path"):
        if not (data / s[key]).is_file():
            errors.append(f"missing {s[key]}")
    if errors:
        return errors
    if Image.open(data / s["image_path"]).size != size:
        errors.append("image size")
    if np.load(data / s["depth_path"]).shape != (s["height"], s["width"]):
        errors.append("depth shape")
    seg_img = Image.open(data / s["seg_path"])
    if seg_img.mode != "L" or seg_img.size != size:
        errors.append(f"seg mode/size {seg_img.mode} {seg_img.size}")
    seg = np.asarray(seg_img)
    values, counts = np.unique(seg, return_counts=True)
    in_png = dict(zip(values.tolist(), counts.tolist()))
    seg_ids = [o["seg_id"] for o in s["objects"]]
    if len(set(seg_ids)) != len(seg_ids):
        errors.append("duplicate seg_id in objects[]")
    unknown = set(in_png) - {SEG_BACKGROUND, SEG_TABLE} - set(seg_ids)
    if unknown:
        errors.append(f"seg values not in objects[]: {sorted(unknown)}")
    for o in s["objects"]:
        if in_png.get(o["seg_id"], 0) != o["pixel_area"]:
            errors.append(f"{o['id']}: {in_png.get(o['seg_id'], 0)} px in seg PNG, pixel_area {o['pixel_area']}")
    return errors


def main() -> None:
    ap = argparse.ArgumentParser(prog="python -m gen.check", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", default="data/", help="data folder (default data/)")
    data = Path(ap.parse_args().data)
    scene_files = sorted((data / "scenes").glob("*.json"))
    failed = 0
    for f in scene_files:
        errors = check_scene(data, f)
        if errors:
            failed += 1
            print(f"FAIL {f.name}: {'; '.join(errors)}")
    print(f"{len(scene_files) - failed}/{len(scene_files)} scenes pass")
    sys.exit(1 if failed or not scene_files else 0)


if __name__ == "__main__":
    main()
