# AGENTS.md — Team, ownership and working rules

> Read this first, whether you're a person or a coding agent (Claude Code, Codex, Gemini CLI, Cursor).
> **Project (locked):** *RoboSpatial-Synth*, a benchmark of synthetic tabletop manipulation scenes with spatial questions, used to evaluate Gemini Robotics-ER 2 against general frontier VLMs. Background and rationale: `context/handoff.md`.

## The one-paragraph pitch

A robot arm looking at a cluttered table has to answer "which thing, where, and can I grab it?" before it moves. We render cluttered tabletop scenes procedurally in PyBullet, so we know every object's mask, depth, pose and support/occlusion relationships. From that scene data we generate questions with **reference answers derived from simulator state, never from a model**, and score model answers with deterministic geometric checks.

## Team & ownership

Roster: `@happypeepo`, `@Dhruvk2004`, `@hritz23`, `@Raj-Shah19`. **Claim a role by putting your name in the table and pushing.**

| Role | Owner | Owns (only you edit these) | Delivers |
|---|---|---|---|
| **A — Scene Generator** | `@Raj-Shah19` | `gen/` | Rendered images + masks + depth + `scene.json` per scene |
| **B — Tasks & Validation** | `@Dhruvk2004` | `tasks/`, `validate/`, `schema/`, `fixtures/` | Question templates, reference answers, checkers, validation evidence |
| **C — Eval Harness** | `@happypeepo` | `eval/` | Model runners, prompt formats, response parsing, scoring, cost log |
| **D — Analysis & Story** | `<NAME_D>` | `report/`, `docs/`, `README.md` | Breakdowns, charts, failure gallery, demo, write-up, pitch |

Shared (edit by PR, tag the owner): `Makefile`, `requirements.txt`, `context/`.

## Locked technical decisions

| Topic | Decision | Why |
|---|---|---|
| Renderer | **PyBullet** (`pip install pybullet`), headless `getCameraImage` | Gives RGB, depth and per-object segmentation masks for free. Physics settles objects realistically and powers the counterfactual tasks. No GPU needed. |
| Objects | Primitives (box, cylinder, sphere) + meshes shipped in `pybullet_data`. YCB optional later. | Zero asset wrangling on day 1 |
| Image size | 1024×768 PNG | Large enough for small objects, cheap enough per call |
| Coordinates (storage) | **Pixels**, origin top-left, `[x, y]` | One source of truth |
| Coordinates (model I/O) | Ask models for **`[y, x]` normalised to 0–1000** (the ER-native format). C converts back to pixels before scoring. | Fair to ER 2, easy to ask of the others |
| Language | Python 3.11 | |
| Primary model | `gemini-robotics-er-2-preview` | |
| Comparison models | 2–3 general VLMs we have keys for (decide in handoff) + text-only baseline | |

## Task families (B owns these; A must supply the data each one needs)

| # | Family | Example question | `answer_type` | Checker | Needs from A |
|---|---|---|---|---|---|
| F1 | Referring pointing | "Point to the red cylinder left of the blue box." | `point` | `point_in_mask` | masks, colours, 2D positions |
| F2 | Referring detection | "Draw a box around the smallest green object." | `bbox` | `iou>=0.5` | bboxes |
| F3 | Counting under occlusion | "How many spheres are on the table?" | `count` | exact | `visible_frac` per object |
| F4 | 3D spatial relations | "Is the mug in front of or behind the bowl?" | `choice` | exact | depth / 3D poses |
| F5 | Support & stacking | "What is the yellow box resting on?" | `choice` | exact | contact graph from physics |
| F6 | Graspability / free-to-pick | "Point to an object you can pick up without moving anything else." | `point` (any valid) | `point_in_any_mask` | contact + occlusion graph |
| F7 | Clearing order | "List the objects in an order that clears the stack safely." | `sequence` | valid topological order | support graph |
| F8 | Counterfactual | "If the blue box is removed, which objects move?" | `set` | set-F1 ≥ threshold, exact for headline | **physics rerun** with the object deleted |
| F9 | Placement success (from Idea 3) | "Goal: red cube on the plate. Did it succeed?" | `bool` | exact | goal spec + final pose |
| F10 | Before/after change (from Idea 3) | Two images: "Which object moved?" | `choice` | exact | paired renders, same camera |

**Difficulty knobs** (A exposes them, B records them on every task, D slices by them): `n_objects` (3–20), `occlusion` (max hidden fraction), `distractors` (same colour or same shape as the target), `camera` (elevation/azimuth), `lighting`, `scale_range`.

**Validity rules** (B enforces them, the validator rejects tasks that break them):
- The target must be ≥ 30% visible (`visible_frac`) unless the question is explicitly about occlusion (F3).
- F3 counts objects with `visible_frac ≥ 0.10`; drop the task if any matching object is between 0 and 0.10. Fully hidden objects (0) are not counted.
- F6 free-to-pick = nothing rests on it (from `contacts`) and `visible_frac ≥ 0.30`.
- Referring expressions must have **exactly one** match in the scene (uniqueness check).
- Relation answers (F4) need a margin: depth difference ≥ 3 cm, otherwise drop the task as ambiguous.
- F8/F9 labels come from re-simulating, not from heuristics.

## Contracts (v1). Code against these, not against each other

### 1. `scene.json` (A → B), one per scene, in `data/scenes/`
```json
{
  "scene_id": "s_000123", "seed": 123, "generator_version": "gen@<git-sha>",
  "image_path": "images/s_000123.png",
  "depth_path": "depth/s_000123.npy",
  "seg_path": "seg/s_000123.png",
  "width": 1024, "height": 768,
  "camera": {"view_matrix": [16 floats], "proj_matrix": [16 floats], "elevation_deg": 45, "azimuth_deg": 30},
  "objects": [
    {"id": "o1", "seg_id": 3, "shape": "cylinder", "color": "red", "size_cm": [6, 6, 10],
     "pose": {"xyz_m": [0.1, -0.05, 0.05], "quat": [0, 0, 0, 1]},
     "bbox_2d": [412, 300, 470, 395], "centroid_2d": [441, 350],
     "pixel_area": 4210, "visible_frac": 0.82, "depth_mean_m": 0.71}
  ],
  "contacts": [{"top": "o1", "bottom": "table"}, {"top": "o4", "bottom": "o1"}],
  "occludes": [{"front": "o2", "back": "o1", "frac": 0.18}],
  "counterfactuals": {"o1": {"moved": ["o4"]}},
  "pair": null,
  "difficulty": {"n_objects": 9, "occlusion": 0.35, "distractors": 2, "lighting": "dim"}
}
```
- All paths are **relative to the data folder** passed as `--data`, so `--data fixtures/` and `--data data/` both work.
- `seg_path` is a PNG where pixel value = `seg_id`. Masks derive from it, so there's no per-object mask file.
- `counterfactuals` is filled only for objects that support something (cost control).
- `pair` is set for F10 scenes: `{"before": "s_000123a", "after": "s_000123b", "moved": ["o3"]}`.
- F9 scenes add `"goal": {"object": "o2", "relation": "on", "target": "o7", "success": true}`.

### 2. `tasks.jsonl` (B → C), one task per line, in `data/tasks.jsonl`
```json
{
  "task_id": "t_000123_F1_02", "scene_id": "s_000123", "family": "F1",
  "images": ["images/s_000123.png"],
  "question": "Point to the red cylinder left of the blue box.",
  "answer_type": "point",
  "answer_format": "Return JSON: {\"point\": [y, x]} with y, x normalised 0-1000.",
  "reference": {"seg_ids": [3]},
  "checker": "point_in_mask",
  "evidence": ["o1", "o5"],
  "validated": {"unique_referent": true, "min_visible": 0.82, "margin_ok": true, "human": null},
  "difficulty": {"n_objects": 9, "occlusion": 0.35, "distractors": 2}
}
```
`answer_type` ∈ `point | bbox | count | choice | bool | sequence | set`.

**Answer shapes.** C's parser outputs `parsed` in exactly these shapes, already converted to pixels. `reference` uses the same keys, so the checker compares like with like.

| `answer_type` | `parsed` | `reference` |
|---|---|---|
| `point` | `{"point": [x, y]}` pixels | `{"seg_ids": [...]}` (any listed mask counts) |
| `bbox` | `{"bbox": [x1, y1, x2, y2]}` pixels | `{"bbox": [x1, y1, x2, y2], "seg_ids": [...]}` |
| `count` | `{"count": 4}` | `{"count": 4}` |
| `choice` | `{"choice": "B"}` | `{"choice": "B", "options": {"A": "o3", "B": "table"}}` |
| `bool` | `{"bool": true}` | `{"bool": true}` |
| `sequence` | `{"sequence": ["C", "A", "B"]}` | `{"sequence": [...], "options": {...}, "precedes": [["C", "A"]]}` (any order respecting `precedes` is correct) |
| `set` | `{"set": ["A", "C"]}` | `{"set": ["A", "C"], "options": {...}}` |

**Objects are named by letter.** Choice, sequence and set questions list lettered options in the question text (`Options: A) blue box B) red cylinder C) table`). The model answers with letters, and `reference.options` maps each letter to an object id. Models are asked for points as `[y, x]` and boxes as `[ymin, xmin, ymax, xmax]`, normalised 0–1000 (see `answer_format`).

### 3. Checker API (B implements in `validate/checkers.py`, C imports it)
```python
def score(task: dict, parsed: dict, scene: dict) -> float:  # 0.0..1.0, coords already in pixels
```
F8 (`set_f1`) returns the raw F1 as partial credit. Headline accuracy counts only exact matches (F1 = 1.0).
Shipped in PR #2 (on `main`). C imports it directly; there is no stub.

### 4. `results.jsonl` (C → D), one row per (task, model, run), in `data/results/<model>.jsonl`
```json
{"task_id": "t_000123_F1_02", "model": "gemini-robotics-er-2-preview", "run": 0,
 "condition": "image | text_only", "raw_response": "...", "parsed": {"point": [441, 350]},
 "parse_ok": true, "score": 1.0, "latency_ms": 1820,
 "tokens_in": 1200, "tokens_out": 40, "cost_usd": 0.0012, "error": null}
```

## Work split

Every role can start at hour 0. Only one sync point: **B commits `schema/` + `fixtures/` (3 hand-made scenes + ~15 tasks + fake results) within the first hour.** Until then A, C and D read the contract above.

Check any data folder against the contracts: `python -m schema.check --data fixtures/` (or `--data data/`). `fixtures/` uses the same layout as `data/`: `scenes/`, `images/`, `seg/`, `depth/`, `tasks.jsonl`, `results/<model>.jsonl`.

### A — Scene Generator (`gen/`)
- [ ] `gen/scene.py`: PyBullet table + random objects, drop and settle physics, render RGB/depth/seg at the chosen camera.
- [ ] Compute per object: bbox, centroid, pixel area, `visible_frac` (render each object alone vs. in the full scene), mean depth.
- [ ] Contact graph from `getContactPoints` after settling. Occlusion pairs from the seg comparison.
- [ ] Counterfactuals: for each supporting object, reload, delete it, step the sim, record what moved > 2 cm.
- [ ] F9 goal scenes (scripted placement that succeeds or fails) and F10 paired renders (move 1 object, re-render with the same camera).
- [ ] Difficulty knobs as CLI flags. `python -m gen --seed 0 --n 300 --out data/`.
- [ ] Self-check: the seg ids in the PNG match `objects[].seg_id`. Visual contact-sheet of 20 scenes for eyeballing.
- **Done when:** 300+ scenes are generated deterministically from a seed in under ~15 min on a laptop.

### B — Tasks & Validation (`tasks/`, `validate/`, `schema/`, `fixtures/`)
- [ ] **Hour 0:** `schema/*.json` (JSON Schema for the 3 contracts) + `fixtures/`.
- [ ] `tasks/templates.py`: a generator per family F1–F10 that reads `scene.json` and emits tasks. Varied phrasings (≥3 templates per family).
- [ ] Referring-expression builder (colour / shape / size / relative position) with a uniqueness check.
- [ ] `validate/checkers.py`: `point_in_mask`, `point_in_any_mask`, `bbox_iou`, `exact`, `topo_order_valid`, `set_f1`.
- [ ] `validate/independent.py`: **re-derives answers from the raw seg/depth arrays without reading A's `contacts`/`relations`** and flags any disagreement. This is our "verified independently" evidence.
- [ ] Human spot-check: a tiny script that shows 50 random tasks with the image and reference overlaid and records agree/disagree → reference error rate. Results are committed as `validate/evidence/human_check_seed<N>.jsonl` (`data/` is gitignored).
- [ ] Unit tests in `validate/tests/` (pytest), run with `make test`.
- [ ] Balance: cap tasks per family, balance yes/no and choice answers so a majority baseline gets ≈ chance.
- **Done when:** `python -m tasks --data data/` gives ~1,000 validated tasks plus `data/validation_report.json` (rejection counts by reason, independent-check agreement %, human-check agreement %).

### C — Eval Harness (`eval/`)
- [ ] `eval/models/`: one adapter per model with the same interface `ask(images, prompt) -> raw_text`. Add `mock` first (returns random valid answers) so nothing waits on keys.
- [ ] Prompt builder per `answer_type`, using the `answer_format` string. Same prompt for every model.
- [ ] Robust parser (JSON extraction, `[y,x]`/0–1000 → pixels, tolerate prose). Log `parse_ok`; a parse failure scores 0 but is reported separately.
- [ ] Response cache keyed on (model, task_id, prompt hash), so reruns cost nothing.
- [ ] Conditions: `image` (main) and `text_only` (no image, proves the questions need vision). Random/majority baselines computed offline.
- [ ] 3 repeat runs on a 100-task subset for variance. Temperature 0 where supported.
- [ ] Cost and latency tracking. Stop and post in handoff at 70% of budget.
- **Done when:** `python -m eval --model <name> --tasks data/tasks.jsonl` resumes safely and writes `results.jsonl`.

### D — Analysis & Story (`report/`, `docs/`, `README.md`)
- [ ] `report/analyze.py`: accuracy by model × family, and by model × each difficulty knob (curves). Bootstrap 95% CIs. Image vs. text-only gap.
- [ ] Failure gallery: images with the model's point/box drawn next to the reference, grouped by failure type (wrong referent, off-object, relation flip, hallucinated count).
- [ ] `docs/application.md`: robot picking use case, why each error type matters physically, how scoring separates right from wrong.
- [ ] `docs/limitations.md`: sim-to-real gap, primitive objects, template language, small N, budget limits.
- [ ] Demo: Streamlit/Gradio page. Pick a seed → render a scene → ask → show each model's answer overlaid with ✓/✗.
- [ ] Pitch slides / talk track (5 criteria → 5 sections). Keep README current.
- **Done when:** `python -m report --results data/results/` builds `report/out/` (charts + HTML/MD summary) from fixtures *and* from real data.

## Working rules

1. **Branches:** `<role>/<short-desc>` (e.g. `gen/counterfactuals`). Small PRs into `main`. You may merge your own PR inside your own folders. Shared files need one approval.
2. **`make smoke` must pass before merging:** fixtures → tasks → mock eval → report.
3. **Determinism:** everything takes `--seed`. Same seed, same dataset.
4. **Ground truth comes from simulator state, never from a model.** No model output may ever be written into `reference`.
5. **Secrets:** API keys live in `.env` (gitignored). Never commit keys, never paste them in issues/PRs.
6. **Generated data** (`data/`) is gitignored. Commit code + seeds + `fixtures/` only.
7. **Contract changes:** a PR that edits `schema/` + `fixtures/` + this file together, tag all owners, additive only (new optional fields). Log it in the handoff Decisions table.
8. **Async updates:** at the end of each work block, append one line to the log in `context/handoff.md` (done / next / blocked-on). Read it before you start.

## For coding agents

- Stay inside your role's folders unless the task says otherwise.
- Read `context/handoff.md` before acting. It holds the current decisions and log.
- Run `make smoke` before proposing a commit.
- Never create or "fix" reference answers by asking a model.
