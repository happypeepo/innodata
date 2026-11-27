# innodata — RoboSpatial-Synth

> **RoboSpatial-Synth:** procedurally rendered tabletop manipulation scenes with spatial questions (pointing, counting under occlusion, 3D relations, support, graspability, clearing order, counterfactuals, placement success, before/after change) whose answers come from simulator state. Used to evaluate Gemini Robotics-ER 2 against general frontier VLMs.
>
> Work split and contracts: [`AGENTS.md`](AGENTS.md). Decisions and log: [`context/handoff.md`](context/handoff.md). Why it matters: [`docs/application.md`](docs/application.md). What it doesn't show: [`docs/limitations.md`](docs/limitations.md).

## What it does

A robot arm looking at a cluttered table has to answer "which thing, where, and can I grab it?" before it moves. The pipeline:

1. **Generates** cluttered tabletop scenes in PyBullet (`gen/`). Physics settles every object, so we know each one's mask, depth, 3D pose, what it rests on and what hides it.
2. **Asks questions** in 10 task families (F1–F10) built from that scene data (`tasks/`), and rejects any question that is ambiguous, hidden or guessable (`validate/rules.py`).
3. **Takes reference answers from the simulator, never from a model.** Counterfactual (F8) and placement (F9) answers come from re-running the physics. Answers are re-derived independently from the raw seg/depth arrays (`validate/independent.py`), and a human spot-checks 50 of them (`validate/spotcheck.py`).
4. **Evaluates** ER 2, a Gemini generalist, Claude and GPT on the same prompts, with and without the image (`eval/`), and scores every answer with a deterministic geometric check: point in mask, box IoU ≥ 0.5, exact match, valid order, set F1 (`validate/checkers.py`).
5. **Reports** accuracy by model × family and × difficulty knob with 95% bootstrap CIs, the image-vs-text-only gap, run-to-run spread, cost, and a failure gallery (`report/`).

| # | Family | Example | Checked by |
|---|---|---|---|
| F1 | Referring pointing | "Point to the red cylinder left of the blue box." | point in the target's mask |
| F2 | Referring detection | "Draw a box around the smallest green object." | IoU ≥ 0.5 |
| F3 | Counting under occlusion | "How many spheres are on the table?" | exact |
| F4 | 3D relations | "Is the purple box in front of or behind the green sphere?" | exact (≥ 3 cm depth margin) |
| F5 | Support | "What is the yellow box resting on?" | exact |
| F6 | Free to pick | "Point to an object you can pick up without moving anything else." | point in any free object's mask |
| F7 | Clearing order | "List the objects in an order that clears the stack safely." | any valid top-down order |
| F8 | Counterfactual | "If the blue box is removed, which objects move?" | exact set (F1 reported too) |
| F9 | Placement success | "Goal: red box on the green box. Did it succeed?" | exact |
| F10 | Before/after change | Two images: "Which object moved?" | exact |

## Setup

Python 3.11.

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pip install pybullet          # only needed to generate scenes (gen/)
cp .env.example .env          # API keys for real model runs; never commit .env
```

If `pip install pybullet` fails to compile on macOS (recent Xcode clang), build it with:

```bash
CFLAGS="-Dfdopen=fdopen" CXXFLAGS="-Dfdopen=fdopen" pip install --no-build-isolation pybullet
```

## Quickstart: the whole pipeline on fixtures, no keys

```bash
make smoke
```

This checks the fixtures against the contracts, makes tasks, runs the `mock` model with and without images, builds the report into `.smoke/report/`, and runs every test. Each stage switches on once its folder is on `main`. `make smoke` must pass before any merge.

## Full run

Same seed, same dataset (AGENTS.md rule 3). Generated data goes to `data/`, which is gitignored.

```bash
# 1. Scenes: 300 scenes, ~10 min on a laptop
python -m gen --seed 0 --n 300 --out data/
python -m gen.counterfactuals --data data/          # F8 labels: re-simulate with each supporter removed
python -m gen.pairs --data data/ --seed 0           # F10 after-scenes (<id>b) and F9 goal scenes (<id>g)
python -m gen.contact_sheet --data data/            # 20-scene contact sheet for eyeballing

# 2. Tasks + validation evidence -> data/tasks.jsonl, data/validation_report.json
python -m tasks --data data/ --seed 0
python -m validate.spotcheck --data data/ --tasks data/tasks.jsonl --seed 0 --reviewer <you>

# 3. Eval -> data/results/<model>.jsonl (cached, so a rerun only asks what's missing)
python -m eval --model er2 --data data/ --tasks data/tasks.jsonl --dry-run          # call count + cost estimate
python -m eval --model er2 --data data/ --tasks data/tasks.jsonl --condition image --condition text_only --budget-usd 25
python -m eval --model claude --data data/ --tasks data/tasks.jsonl --runs 3 --subset data/subset_100.txt   # repeat runs; the subset file is C's Phase 3 (not written yet)

# 4. Report -> report/out/index.html, summary.md, CSVs, gallery/
python -m report --results data/results/
```

Model aliases: `mock`, `er2` (`gemini-robotics-er-2-preview`), `gemini-pro`, `claude`, `gpt`. `--model-id` overrides the default ID. The API budget is $100; stop and post in the handoff at $70.

## Outputs

| Path | From | What |
|---|---|---|
| `data/scenes/<id>.json`, `images/`, `depth/`, `seg/` | `gen` | one scene per seed: RGB, float depth, 8-bit seg (pixel = `seg_id`), contract 1 |
| `data/tasks.jsonl`, `data/validation_report.json` | `tasks` | contract 2; rejection counts, independent and human agreement %, baselines |
| `data/results/<model>.jsonl`, `<model>.meta.json` | `eval` | contract 4, one row per task × condition × run; run config and tasks file hash |
| `report/out/` | `report` | `index.html` (charts + failure gallery), `summary.md`, `summary.json`, CSVs |

## Judging criteria → where we address it

| Weight | Criterion | Where we address it |
|---|---|---|
| 25% | Working implementation & repeatability | `make smoke` runs fixtures → tasks → mock eval → report → tests. Every stage takes `--seed`; same seed gives byte-identical scenes and tasks. Eval caches every answer and records the tasks file hash. |
| 25% | Validity & validation evidence | References from simulator state, never a model. F8/F9 from physics reruns. Validity rules (uniqueness, ≥ 30% visible, 3 cm margin), independent re-derivation from seg/depth, human spot-check, all in `validation_report.json`. |
| 20% | Meaningful variation | 10 families from pointing to counterfactuals. Difficulty knobs (objects, occlusion, distractors, camera, lighting, scale, stacking) recorded on every task and sliced in the report. |
| 15% | Original approach & engineering judgement | Deterministic checkers instead of an LLM judge, text-only baseline, budget cap and dry-run, [`docs/limitations.md`](docs/limitations.md). |
| 15% | Practical impact & communication | [`docs/application.md`](docs/application.md): what each error costs a robot. Report with failure gallery; demo app (in progress). |

## Repo layout

```
gen/        # A — PyBullet scenes; counterfactuals, F9/F10 scenes, contact sheet
tasks/      # B — question templates F1–F10 + reference answers
validate/   # B — checkers, validity rules, balancing, independent re-derivation, human spot-check
schema/     # B — JSON Schemas for the contracts (python -m schema.check --data <dir>)
fixtures/   # B — hand-made scenes, tasks and fake results so nobody waits
eval/       # C — model adapters, prompt, parser, cache, pricing
report/     # D — breakdowns, charts, failure gallery
docs/       # D — application, limitations
context/    # handoff notes and decisions
```

## Status

Every piece above exists, but not all of it is on `main` yet. Open PRs: B's tasks/validation stack, C's eval harness (which adds the `Makefile`), A's generator, and D's report and docs. Until they merge, the stages that aren't on `main` are skipped by `make smoke`. The live state is in the log in [`context/handoff.md`](context/handoff.md).

## Team

| Role | Owner |
|---|---|
| A — Scene Generator | `@Raj-Shah19` |
| B — Tasks & Validation | `@Dhruvk2004` |
| C — Eval Harness | `@happypeepo` |
| D — Analysis & Story | `@hritz23` |

Ownership rules and contracts: [`AGENTS.md`](AGENTS.md).
