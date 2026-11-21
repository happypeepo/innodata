# AGENTS.md — Team, ownership and working rules

> Read this first, whether you're a person or a coding agent (Claude Code, Codex, Gemini CLI, Cursor).
> **Placeholder version.** Names, the idea and the specifics get filled in once the idea is finalised (see `context/handoff.md`). The **contracts and rules below hold whichever idea we pick.**

## Team & ownership

| Role | Owner | GitHub | Owns (only you edit these) | Delivers |
|---|---|---|---|---|
| **A — Generator** | `<NAME_A>` | `@<handle_a>` | `gen/` | Images + `scene.json` per sample (ground-truth scene data) |
| **B — Tasks & Validation** | `<NAME_B>` | `@<handle_b>` | `tasks/`, `validate/` | Questions + reference answers + independent validator |
| **C — Eval Harness** | `<NAME_C>` | `@<handle_c>` | `eval/` | Model runners, response parsing, scorer |
| **D — Analysis & Story** | `<NAME_D>` | `@<handle_d>` | `report/`, `docs/`, `README.md` | Findings, charts, limitations, demo, pitch |

Shared (edit by PR, ping the owner): `fixtures/`, `schema/`, `Makefile`, `context/`.

## Why nobody gets blocked

Everyone codes against **the contracts below plus the fixtures**, never against someone else's unfinished code.

- **Day 0 (first ~1 hr):** someone (whoever's free, default B) commits `schema/` and `fixtures/` with **5 hand-made samples** that follow the contracts. After that, all four streams run in parallel.
- A works on real generation and writes into `data/`. Until A is done, B, C and D read from `fixtures/`.
- B writes templates and validators against `fixtures/scene.json`.
- C builds runners and the scorer against `fixtures/tasks.jsonl`, using a **mock model** (`--model mock`) so no API key is needed to develop.
- D builds the report against `fixtures/results.jsonl` (fake results are fine) so charts exist before real numbers do.
- Swap fixtures → real data with one flag: `--data fixtures/` → `--data data/`.

**Changing a contract:** open a PR that edits `schema/` + `fixtures/` together, tag all owners, and keep it backward compatible (new fields are optional). Don't rename or remove fields without agreement in the handoff log.

## Contracts (v0)

### 1. `scene.json` (A → B), one per image
```json
{
  "scene_id": "s_000123",
  "seed": 123,
  "image_path": "data/images/s_000123.png",
  "width": 1024, "height": 768,
  "camera": {"intrinsics": [[...]], "extrinsics": [[...]]},
  "objects": [
    {"id": "o1", "category": "<cat>", "attributes": {"color": "red"},
     "bbox_2d": [x0, y0, x1, y1], "mask_path": "data/masks/s_000123_o1.png",
     "pose_3d": {"xyz": [0,0,0], "rpy": [0,0,0]}, "visible_frac": 0.82}
  ],
  "relations": [{"subj": "o1", "rel": "left_of", "obj": "o2"}],
  "difficulty": {"occlusion": 0.3, "clutter": 7, "lighting": "low"},
  "generator_version": "gen@<git-sha>"
}
```

### 2. `tasks.jsonl` (B → C), one task per line
```json
{
  "task_id": "t_000123_03",
  "scene_id": "s_000123",
  "image_path": "data/images/s_000123.png",
  "family": "<task_family>",
  "question": "Point to the object that is directly behind the blue mug.",
  "answer_type": "point | bbox | count | choice | number | bool | sequence",
  "reference": {"value": "...", "tolerance": null},
  "checker": "point_in_mask",
  "evidence": ["o4"],
  "validated": {"programmatic": true, "human_spot_check": null},
  "difficulty": {"occlusion": 0.3, "distractors": 2}
}
```

### 3. `results.jsonl` (C → D), one row per (task, model, run)
```json
{
  "task_id": "t_000123_03", "model": "gemini-robotics-er-2-preview",
  "run": 0, "raw_response": "...", "parsed": {...},
  "correct": true, "score": 1.0, "latency_ms": 1820,
  "tokens_in": 1200, "tokens_out": 40, "cost_usd": 0.0012,
  "error": null
}
```

## Working rules

1. **Branches:** `<role>/<short-desc>` (e.g. `gen/occlusion-knob`). Small PRs into `main`. Merge your own PR into your own folder; anything shared needs one approval.
2. **No breaking `main`.** `make smoke` (fixtures → mock eval → report) must pass before you merge.
3. **Determinism:** everything takes `--seed`. Same seed, same dataset.
4. **Ground truth comes from generator state, never from a model.** No model output may ever be written into `reference`.
5. **Secrets:** API keys live in `.env` (gitignored). Never commit keys, never paste them in issues/PRs.
6. **Cost:** log every API call's tokens and cost to `results.jsonl`. Check the budget in `context/handoff.md` before big runs.
7. **Generated data** (`data/`) is gitignored. Commit the code and seed, not the images. A small `fixtures/` set is committed.
8. **Async updates:** at the end of each work block, add 2–3 lines to the log in `context/handoff.md` (done / next / blocked-on). Read it before you start.

## For coding agents

- Stay inside your owner's folders unless the task says otherwise.
- Read `context/handoff.md` before acting. It holds the current decisions.
- Run `make smoke` before proposing a commit.
- Don't invent ground truth or "fix" references by asking a model.
