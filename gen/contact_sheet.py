"""Contact sheet of N scenes for eyeballing, plus a "no object touches the border" check.

The sheet draws every object's bbox in its own colour with its id, so a wrong colour
name, a mis-sized box or a low-contrast object (yellow/orange on the beige table) is
easy to spot. The border check runs over every scene, not just the sampled ones.

    python -m gen.contact_sheet --data data/ --n 20 --seed 0
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from random import Random

from PIL import Image, ImageDraw

TILE = (320, 240)
COLS = 5
BOX_RGB = {"red": (230, 30, 30), "green": (30, 170, 30), "blue": (30, 80, 230),
           "yellow": (240, 210, 0), "orange": (250, 130, 0), "purple": (150, 50, 200)}


def load_scenes(data_dir: Path) -> list[dict]:
    return [json.loads(f.read_text()) for f in sorted((Path(data_dir) / "scenes").glob("s_*.json"))]


def border_touches(scene: dict, margin: int = 0) -> list[str]:
    """Ids whose bbox reaches the image edge (bbox max is inclusive)."""
    w, h = scene["width"], scene["height"]
    out = []
    for o in scene["objects"]:
        b = o["bbox_2d"]
        if b and (b[0] <= margin or b[1] <= margin or b[2] >= w - 1 - margin or b[3] >= h - 1 - margin):
            out.append(o["id"])
    return out


def tile(scene: dict, data_dir: Path) -> Image.Image:
    img = Image.open(Path(data_dir) / scene["image_path"]).convert("RGB")
    sx, sy = TILE[0] / img.width, TILE[1] / img.height
    img = img.resize(TILE)
    d = ImageDraw.Draw(img)
    for o in scene["objects"]:
        if not o["bbox_2d"]:
            continue
        x1, y1, x2, y2 = o["bbox_2d"]
        rgb = BOX_RGB.get(o["color"], (255, 255, 255))
        d.rectangle([x1 * sx, y1 * sy, x2 * sx, y2 * sy], outline=rgb, width=2)
        d.text((x1 * sx + 2, y1 * sy + 1), o["id"], fill=rgb)
    diff = scene.get("difficulty", {})
    label = f"{scene['scene_id']}  n={diff.get('n_objects', '?')} {diff.get('lighting', '')}"
    if scene.get("goal"):
        g = scene["goal"]
        label += f"  goal {g['object']} on {g['target']}: {'OK' if g['success'] else 'FAIL'}"
    d.rectangle([0, 0, TILE[0], 14], fill=(0, 0, 0))
    d.text((3, 1), label, fill=(255, 255, 255))
    return img


def build_sheet(scenes: list[dict], data_dir: Path) -> Image.Image:
    rows = max(1, -(-len(scenes) // COLS))
    sheet = Image.new("RGB", (TILE[0] * COLS, TILE[1] * rows), (40, 40, 40))
    for i, scene in enumerate(scenes):
        sheet.paste(tile(scene, data_dir), ((i % COLS) * TILE[0], (i // COLS) * TILE[1]))
    return sheet


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data", type=Path, required=True)
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", type=Path, help="default: <data>/contact_sheet.png")
    args = ap.parse_args(argv)

    scenes = load_scenes(args.data)
    if not scenes:
        print(f"no scenes in {args.data / 'scenes'}", file=sys.stderr)
        return 1
    sample = sorted(Random(args.seed).sample(scenes, min(args.n, len(scenes))), key=lambda s: s["scene_id"])
    out = args.out or args.data / "contact_sheet.png"
    build_sheet(sample, args.data).save(out)

    touching = {s["scene_id"]: ids for s in scenes if (ids := border_touches(s))}
    report = {"scenes_checked": len(scenes), "sampled": [s["scene_id"] for s in sample],
              "border_touches": touching}
    out.with_suffix(".json").write_text(json.dumps(report, indent=2) + "\n")
    print(f"wrote {out} ({len(sample)} scenes); {len(touching)} of {len(scenes)} scenes have an object on the border")
    return 1 if touching else 0


if __name__ == "__main__":
    sys.exit(main())
