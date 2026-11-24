# fixtures/

Hand-made data that A, C and D can code against before real scenes exist. Same layout as `data/`, so every tool works with `--data fixtures/`.

| Path | What |
|---|---|
| `scenes/<scene_id>.json` | `scene.json` contract (see `schema/scene.schema.json`) |
| `images/`, `seg/`, `depth/` | RGB PNG, 8-bit seg PNG (0 nothing/floor, 1 table, objects from 2), float32 depth in metres along the camera axis |
| `tasks.jsonl` | 16 tasks covering F1–F10 and all 7 answer types |
| `results/mock.jsonl` | Fake results for a `mock` model: right, wrong, partial credit (F8), one parse failure, and `text_only` rows |

Scenes:

- `s_000001`: clutter with two red cylinders (one left of the blue box, one right) and an orange sphere fully hidden behind the purple box (`visible_frac: 0`, null bbox/centroid/depth).
- `s_000002`: a yellow box → blue box → red cylinder stack, with contacts and counterfactuals, plus free objects.
- `s_000003`: F9 goal scene; the red box was meant to go on the green box but landed beside it (`success: false`).
- `s_000004a` / `s_000004b`: F10 before/after pair; only the red sphere (`o3`) moves.

Regenerate (deterministic, byte-identical): `python fixtures/make_fixtures.py`
Check against the contracts: `python -m schema.check --data fixtures/`

Not real physics: objects are hand-placed and drawn with a small numpy renderer through the same camera matrices stored in `scene.json`. Contacts and counterfactuals are written by hand. Real labels come from A's PyBullet runs.
