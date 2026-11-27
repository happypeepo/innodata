# Handoff & Context

> Living doc. **Read before you start, append to the log when you stop.** Decisions go in the Decisions table; don't bury them in chat.

## Current state

- **Phase:** build. **Idea 1 (Tabletop manipulation spatial QA) is locked**, with F9/F10 borrowed from Idea 3.
- **Repo:** scaffolding + full work split in `AGENTS.md`
- **Next:** everyone claims a role in `AGENTS.md` → B commits `schema/` + `fixtures/` (hour 0) → all four streams run in parallel

---

## Background: Gemini Robotics-ER 2

Google DeepMind's embodied-reasoning VLM, launched **2026-07-30**, replacing ER 1.6. It's the "robot brain" that plans and reasons over what the robot sees; low-level control is a separate model.

- **Model IDs:** `gemini-robotics-er-2-preview` (standard) plus a streaming variant for the Live API. Public preview on the Gemini API / AI Studio.
- **Inputs → output:** text, image, video, audio → text. ~131k input / ~65k output tokens.
- **Strengths it claims:** spatial reasoning (pointing, boxes, trajectories), **video moment-finding and task-progress classification**, tool/function calling, multi-robot coordination, safety instruction following / human-proximity awareness.
- **Reported numbers (third-party, unverified):** ~91% moment finding, ~57% progress classification. Progress/success detection is a **visibly weak spot**, which makes it a good target for us.
- **Pricing:** sources disagree. **Check Google's pricing page before any big run.**

Sources: [Google blog](https://blog.google/innovation-and-ai/models-and-research/google-deepmind/gemini-robotics-er-2/) · [API model page](https://ai.google.dev/gemini-api/docs/models/gemini-robotics-er-2-preview) · [Robotics ER overview](https://ai.google.dev/gemini-api/docs/robotics-overview)

**Why build around it:** it's new, it was built for exactly this kind of spatial question, and its output formats (normalised points/boxes) are **machine-checkable**. Comparing it against general-purpose frontier models (Gemini 3.x, Claude, GPT) gives a clean "does a specialist beat generalists, and where?" story.

---

## Shortlisted ideas

All three use **procedural generation**, so ground truth comes free from the generator's own scene data and never from a model.

### Idea 1 — Tabletop manipulation spatial QA ("RoboSpatial-Synth") ✅ CHOSEN

**Application:** a robot arm deciding *what* to grab and *where* on a cluttered table (warehouse picking, kitchen, lab automation).
**Why errors matter:** point at the wrong object and the robot grabs the wrong item. Misjudge "behind/under" and it knocks things over. Say "graspable" when an object is occluded and the grasp fails or damages the item.

- **Images:** cluttered tabletop scenes rendered with Blender / PyBullet / MuJoCo (pick one; PyBullet or BlenderProc is fastest to set up). Free assets: YCB objects or primitives.
- **Task families (the "meaningful variation" axis):**
  1. *Referring pointing:* "point to the red mug left of the bowl" → checked with `point_in_mask`
  2. *Counting under occlusion:* "how many screwdrivers?" → exact match
  3. *3D relations:* behind / on top of / inside / closer to the camera → choice
  4. *Affordance / reachability:* "which object can be picked up without moving another?" → derived from the support/occlusion graph
  5. *Action planning:* "order to clear the table safely" → any valid topological order of the stacking graph
  6. *Counterfactual:* "if the box is removed, what falls?" → physics-sim check
- **Difficulty knobs:** occlusion %, clutter count, distractors (same colour, different shape), lighting, camera angle, object scale.
- **Validation:** geometric checkers built on masks and depth; each task has a `visible_frac` threshold so the evidence really is in the image; human spot-check of ~50 samples to estimate the error rate of the references themselves.
- **Risk:** setting up a 3D renderer can eat a day. **Mitigation:** start with simple primitives and fixed lighting, and add realism later.

### Idea 2 — Industrial gauge & instrument reading (inspection robots)

**Application:** quadruped or wheeled robots doing plant inspection rounds and reading analog gauges, valves and indicator lights (oil & gas, utilities, data centres).
**Why errors matter:** misread a pressure gauge and you miss an overpressure alarm, a safety incident, or an unnecessary shutdown. Numeric errors have a direct cost.

- **Images:** procedurally drawn gauges (PIL/SVG → render) composited onto plant-like backgrounds with perspective warp, glare, blur and dirt.
- **Task families:** single-needle reading (number with tolerance), multi-needle / dual-scale, **non-linear or log scales**, "is the reading in the red zone?" (bool), valve open/closed, comparing two gauges ("which is higher?"), unit conversion.
- **Difficulty knobs:** viewing angle, glare, motion blur, tick density, partially hidden needle, mirror-image scales.
- **Validation:** the needle angle comes from the generator, so the value is analytic. The tolerance comes from tick spacing. Very clean.
- **Pros:** fastest to build and cleanest to verify (safe fallback). **Cons:** less variety in visual *relationships*, which matters for the 20% criterion. Add the multi-gauge/comparison families to cover it.

### Idea 3 — Task-progress & success detection from multi-frame episodes

**Application:** a robot (or a supervising monitor) deciding "is this step done? did it fail? is a human hand too close?", the core of closed-loop autonomy and safety.
**Why errors matter:** a false "success" makes the robot move on with a failed grasp, and errors compound. A missed hand near the gripper is a safety incident.

- **Images:** short simulated episodes (4–8 frames or a short clip) of stacking, sorting or pouring in PyBullet/MuJoCo, with scripted failures injected (drop, misalignment, wrong bin) plus a synthetic hand/arm intruder.
- **Task families:** success / failure (bool), "which frame did the failure happen in?" (index, ±1 tolerance), progress % bucket, "which subgoal is next?", human-proximity violation (distance threshold from sim state), multi-view consistency.
- **Validation:** labels come straight from the simulator state, e.g. object pose vs. goal pose under a threshold.
- **Pros:** targets ER 2's headline feature *and* its reported weak spot (~57% progress classification). Strong findings story. **Cons:** video and multi-frame calls cost more, and the sim plus failure scripting is the most engineering work.

### Recommendation

**Go with Idea 1 as the core, and borrow 1–2 families from Idea 3** (single-frame "is this placement successful?" and a two-frame before/after). That gives the widest reasoning variety (20%) and the strongest geometric validation (25%), and it plays to ER 2's pointing strength. **Keep Idea 2 as the fallback** if 3D rendering isn't working by checkpoint 1.

---

## Evaluation plan (applies to any idea)

- **Models:** `gemini-robotics-er-2-preview` (primary) vs. 2–3 general frontier VLMs. Optionally one open model (e.g. Qwen-VL) as a cheap baseline.
- **Scoring:** deterministic checker per `answer_type` (point-in-mask, IoU ≥ 0.5, exact count, numeric tolerance, set/order validity). No LLM-as-judge for correctness.
- **Breakdowns:** accuracy by task family × difficulty knob, so we can say *where* models break (e.g. "accuracy drops from 90% to 40% once occlusion > 50%").
- **Sanity baselines:** a text-only (no image) run to prove the questions need vision, and a random/majority baseline.
- **Repeatability:** fixed seeds, temperature 0 where supported, 3 runs on a subset to measure variance.

## Async workstreams (no one waits on anyone)

| Stream | Can start immediately with | Hands off |
|---|---|---|
| A — Generator | idea choice | `data/` with `scene.json` + images |
| B — Tasks/Validation | `fixtures/scene.json` | `tasks.jsonl` + validators |
| C — Eval harness | `fixtures/tasks.jsonl` + mock model | `results.jsonl` |
| D — Analysis/Story | `fixtures/results.jsonl` (fake) | report, charts, demo, pitch |

Contracts live in `AGENTS.md`. Integration is a data swap (`--data data/`), not a code merge.

## Checkpoints (fill in times)

- [ ] **CP0** `<time>`: idea locked, AGENTS.md names filled in, schema + fixtures committed
- [ ] **CP1** `<time>`: each stream runs on fixtures; `make smoke` green
- [ ] **CP2** `<time>`: real data → real eval on ≥1 model, ≥100 tasks
- [ ] **CP3** `<time>`: all models, breakdown charts, limitations written
- [ ] **CP4** `<time>`: demo rehearsed, README final

## Budget

- API budget: `<$X>` total. Owner C tracks spend in `results.jsonl`; stop and post here at 70%.

## Decisions

| Date | Decision | By | Why |
|---|---|---|---|
| 2026-11-22 | Repo scaffolded; 3 ideas shortlisted | `<name>` | Kickoff |
| 2026-11-22 | **Idea 1 chosen** + F9/F10 from Idea 3. Idea 2 stays the fallback. | team | Best variation + geometric validation; plays to ER 2 pointing |
| 2026-11-22 | Renderer = PyBullet. Model I/O in ER-native `[y,x]` 0–1000, stored in pixels. | team | Free masks/depth/physics, no GPU; fair to ER 2 |
| 2026-11-24 | `parsed` is in storage format: points `[x, y]` pixels, boxes `[x1, y1, x2, y2]` pixels. C converts from the model's `[y, x]` 0–1000. Fixes the `[350, 441]` results example. | lead (proposed by B) | Checker reads `parsed`; one coordinate order everywhere |
| 2026-11-24 | Answer shapes per `answer_type`: `point`, `bbox`, `count`, `choice`, `bool`, `sequence`, `set` as same-named keys in `parsed` and `reference`. | lead (proposed by B) | C's parser and B's checker agree |
| 2026-11-24 | Choice/sequence/set questions list lettered options; models answer with letters; `reference.options` maps letter → object id. | lead (proposed by B) | Exact grading without an AI judge |
| 2026-11-24 | Paths in `scene.json` / `tasks.jsonl` are relative to the data folder (`images/s_000123.png`). | lead (proposed by B) | `--data fixtures/` and `--data data/` both work |
| 2026-11-24 | F3 counts `visible_frac ≥ 0.10` and drops tasks with a match in (0, 0.10). F6 free-to-pick = nothing rests on it and `visible_frac ≥ 0.30`. | lead (proposed by B) | Fair counting; graspable means visible |
| 2026-11-24 | F8: `score()` returns raw F1 (partial credit); headline accuracy counts F1 = 1.0 only. | lead (proposed by B) | Docs gave no threshold |
| 2026-11-24 | Human spot-check results committed as `validate/evidence/human_check_seed<N>.jsonl`. | lead (proposed by B) | `data/` is gitignored; judges need the evidence |
| 2026-11-24 | Unit tests in `validate/tests/` with pytest and a `make test` target. | lead (proposed by B) | |

## Open questions

- Which comparison models do we have API access to? (list keys/accounts here)
- Who owns which role? (claim in `AGENTS.md`)
- API budget amount?

## Log (append newest at the bottom)

```
[YYYY-MM-DD HH:MM] <name> (<role>): done: ... | next: ... | blocked on: ...
[2026-11-24 12:19] Dhruvk2004 (B): done: claimed Role B in AGENTS.md; sent contract questions (parsed coord order, answer shapes, option naming, paths) to lead | next: schema/ + fixtures/ on branch schema/v1-contracts-fixtures | blocked on: none
[2026-11-24 15:20] Dhruvk2004 (B): done: schema/ (3 JSON Schemas + python -m schema.check --data <dir>), fixtures/ (5 scene files, 16 tasks F1–F10, mock results), validate/tests, AGENTS.md contract updates + Decisions rows for the 8 lead-approved defaults | next: validate/checkers.py | blocked on: team confirming Raj's Role A option-A picks (seg 0/1, 6 colours, 3 shapes, hidden-object nulls) that the schema uses
[2026-11-24 16:13] Dhruvk2004 (B): done: validate/checkers.py (point_in_mask, point_in_any_mask, bbox_iou, exact, topo_order_valid, set_f1 + score() and load_scene()), 65 new unit tests, mock results re-score to the stored values | next: tasks/ pipeline + F1, F2, F4, F9 templates | blocked on: Phase 1 PR review (this branch is stacked on it); C to swap eval/checkers_stub.py for validate.checkers once merged
[2026-11-27 18:00] hritz23 (D): done: claimed Role D; report/ (python -m report --results <dir> [--tasks] [--out report/out]): accuracy by model x family and x each difficulty knob, bootstrap 95% CIs over tasks (runs averaged per task), image vs text-only gap, run spread, cost/latency -> summary.json/.md, CSVs, index.html with inline SVG charts (no new deps); make smoke green with C's Makefile + mock eval | next: failure gallery, docs/application.md + limitations.md | blocked on: push access for hritz23 (gen/counterfactuals with F8/F9/F10 data is ready locally)
```
