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
| 2026-11-24 | **Seg PNG:** 8-bit grayscale. `0` = nothing and the floor plane; table and objects get remapped ids (next row). | Raj (A), Dhruv (B) | ≤20 objects fit in 8 bits; B's point checker scores a `0` pixel as a miss |
| 2026-11-24 | **Table:** not in `objects[]`; fixed seg value `1`; objects get `seg_id` 2, 3, …; `contacts` use `"table"`. | Raj (A), Dhruv (B) | B's generators only loop over `objects[]`, so the table is never a target; F5 can still answer "table" |
| 2026-11-24 | **Colours:** exactly 6 names, each one fixed RGB: red, green, blue, yellow, orange, purple. | Raj (A), Dhruv (B) | Far-apart hues keep referring expressions unambiguous; B's schema allows only these. Check yellow/orange contrast on the wooden table in renders |
| 2026-11-24 | **Shapes/sizes:** box (sides 4–10 cm each), cylinder (Ø 4–8 cm, height 5–15 cm), sphere (Ø 4–8 cm); uniform draws from the seeded rng; no meshes. | Raj (A), Dhruv (B) | Fits the colour + shape naming scheme; B uses size words only when the gap is clearly visible |
| 2026-11-24 | **Fallen/hidden objects:** objects that fall off the table are removed before rendering. Fully hidden objects stay in `objects[]` with `visible_frac: 0`, `pixel_area: 0`, and `null` for `bbox_2d`, `centroid_2d`, `depth_mean_m`. | Raj (A), Dhruv (B) | Keeps the physical scene intact for contacts/occlusion; B's schema accepts the nulls and B skips these objects as targets |
| 2026-11-24 | **Camera:** looks at the table-top centre `(0, 0, 0.625)` from 0.9 m, vertical FOV 50°, near 0.05 m, far 3.0 m. Objects spawn in a 0.6 × 0.4 m area centred on the table. `elevation_deg` = downward pitch, `azimuth_deg` = yaw about +z (default 45°/30°, as in the AGENTS example). | Raj (A) | All spawn-area corners in frame at 1024×768; a 5 cm cube is ~55–90 px wide |
| 2026-11-24 | **Settling:** still = every object < 1 mm/s linear and < 0.01 rad/s angular, checked each step at 240 Hz and held for 24 steps (0.1 s) in a row, cap 2,400 steps (10 s simulated). If the cap is hit, the scene is rendered as-is and a warning is printed. | Raj (A) | Avoids rendering objects mid-roll; bounded runtime |
| 2026-11-24 | **Seed rule:** scene *i* (0-based) uses `seed = --seed + i`; `scene_id = s_{seed:06d}`. | Raj (A) | Matches the AGENTS example (`s_000123` ↔ 123); B's `task_id` builds on it |
| 2026-11-24 | **Paths in `scene.json` are relative to the data folder**, e.g. `"images/s_000123.png"`, `"depth/…npy"`, `"seg/…png"` (AGENTS example shows `data/images/…`). **`AGENTS.md` still needs updating in B's contract PR** (rule 7). | Raj (A), Dhruv (B) | `--data fixtures/` and `--data data/` both resolve the same paths |
| 2026-11-24 | **Per-object number formats:** `bbox_2d = [x_min, y_min, x_max, y_max]` with **inclusive** max (the last mask pixel; width = x_max − x_min + 1). `centroid_2d` = mean `[x, y]` of mask pixels **rounded to int**. `visible_frac` to 3 decimals, `depth_mean_m` to 4. | Raj (A) | Matches the AGENTS example's integer pixels; B's IoU checker must use the same inclusive convention |
| 2026-11-24 | **Contacts top/bottom:** average the pair's contact normals (from `getContactPoints` after settling). If the average is within ~72° of vertical (\|n_z\| ≥ 0.3), the upper body is `top`. Flatter (side-by-side) contacts are not recorded. (First set at 60° / 0.5; lowered the same day after a box leaning ~30° on a cube's edge, n_z = 0.497, was dropped although it falls without the cube. In 60 random scenes, pure side contacts sit below 0.1.) Pairs are object–object and object–table. | Raj (A) | Robust for tipped objects and a sphere wedged between two others; `contacts` only expresses support |
| 2026-11-24 | **Occlusion:** for each object, the full-scene seg pixels under its render-alone mask that belong to another object; `frac` = those pixels ÷ alone pixels, rounded to 3 decimals. A pair is recorded only when `frac` ≥ 0.01. Only objects occlude (the table never does from above). | Raj (A) | Drops few-pixel edge overlaps that would make "free to pick" checks noisy |
| 2026-11-24 | **Knobs are ranges sampled per scene** from that scene's seeded rng (a flag like `--n-objects 9 9` pins a value). Defaults: `--n-objects 3 20`, `--elevation 30 70`, `--azimuth 0 360` (spawn-area corners stay in frame over all of these), `--distractors 0 3`, `--lighting bright normal dim`, `--scale-range 1.0 1.0`. Drawn angles are rounded to 0.1° and the scale to 0.01 before use, so the stored value is the one used. | Raj (A) | One run covers the whole difficulty spread, so D can plot curves |
| 2026-11-24 | **`occlusion` is measured, not controlled:** `difficulty.occlusion` = max over objects of `1 − visible_frac` (a fully hidden object counts as 1.0). | Raj (A) | Spreads naturally with n_objects and elevation; no extra renders |
| 2026-11-24 | **`distractors` = exactly k:** per scene draw k; pick one anchor object; k others share its colour (different shape) or its shape (different colour), 50/50; every other object shares neither, so exactly k objects share an attribute with the anchor and the anchor's colour+shape is unique. Records k. | Raj (A) | Controlled confusers; B still counts per-task distractors itself |
| 2026-11-24 | **`lighting` presets** `bright`, `normal`, `dim` (fixed ambient/diffuse coefficients, same light direction), picked per scene; records the name. Setting light parameters turns on shadows in PyBullet's CPU renderer, so the main render costs ~2×; the render-alone masks don't need lighting and stay at the cheap default. | Raj (A) | Matches the example's `"dim"` |
| 2026-11-24 | **`scale_range` = per-scene size multiplier:** each scene draws f from `--scale-range LO HI` and multiplies every object's size draw by f; records `difficulty.scale = f`. Default 1.0–1.0 keeps the agreed sizes; widening it needs Dhruv's OK (it changes his size ranges). | Raj (A) | Object scale as a difficulty axis without breaking the agreed sizes by default |
| 2026-11-24 | **`counterfactuals: {}` and `pair: null`** are written in every generated scene (B's schema requires both). `{}` means "none computed"; whoever builds counterfactuals/F10 pairs fills them in. | Raj (A) | Scenes validate against `schema/scene.schema.json`; B's F8/F10 generators simply produce no tasks from them |
| 2026-11-24 | **If the distractor anchor falls off the table, the scene is rebuilt** from the same seed's rng stream (next draws; knob values kept), up to 10 attempts. `distractors` is therefore always an exact integer. | Raj (A) | Happened in 1 of 300 scenes; keeps the field schema-valid and exact |

## Open questions

- Which comparison models do we have API access to? (list keys/accounts here)
- Who owns which role? (claim in `AGENTS.md`)
- API budget amount?

## Log (append newest at the bottom)

```
[YYYY-MM-DD HH:MM] <name> (<role>): done: ... | next: ... | blocked on: ...
[2026-11-24 12:19] Dhruvk2004 (B): done: claimed Role B in AGENTS.md; sent contract questions (parsed coord order, answer shapes, option naming, paths) to lead | next: schema/ + fixtures/ on branch schema/v1-contracts-fixtures | blocked on: none
[2026-11-24 15:21] Raj-Shah19 (A): done: Phase 0 (py3.11 venv, pybullet), Phase 1 decisions logged above, Phase 2 one scene end to end (gen/scene.py: spawn, settle, RGB/depth/seg, scene.json job-1 fields, byte-identical per seed) | next: Phase 3 per-object facts (bbox, centroid, pixel_area, visible_frac, depth_mean_m) | blocked on: B's schema/ to validate scene.json against; AGENTS.md path example needs B's contract PR; Role A claim not pushed (no write access yet)
[2026-11-24 16:18] Raj-Shah19 (A): done: Phase 3 per-object facts in scene.json (bbox_2d inclusive, centroid_2d int, pixel_area, visible_frac via render-alone, depth_mean_m; fully hidden objects get nulls), byte-identical per seed | next: Phase 4 contacts + occludes | blocked on: B's schema/ to validate against; pushes (no write access yet)
[2026-11-24 16:34] Raj-Shah19 (A): done: Phase 4 contacts (contact-normal rule, |n_z| >= 0.3; side contacts skipped) + occludes (>= 1% of the back object's alone-pixels) in scene.json, byte-identical per seed | next: Phase 5 CLI + difficulty knobs | blocked on: B's schema/; pushes (no write access yet); render speed (~2 s/render) to fix in Phase 6
[2026-11-24 18:07] Raj-Shah19 (A): done: Phase 5 CLI `python -m gen --seed --n --out` + knob ranges (n_objects, elevation, azimuth, distractors exact-k, lighting presets, scale) and the difficulty block; byte-identical per seed | next: Phase 6 300-scene run + speed-up | blocked on: speed (~50-85 s/scene single process); pushes (no write access yet); B's schema/
[2026-11-24 18:46] Raj-Shah19 (A): done: Phase 6: `python -m gen --seed 0 --n 300 --out data/` makes 300 scenes in 11.3 min (8 workers), byte-identical on re-run; `python -m gen.check` passes 300/300 | next: Phase 7 (needs push access, Makefile/make smoke) | blocked on: pushes (no write access yet); B's schema/; note stacking is thin (221 object-on-object contacts in 300 scenes) for F5/F7
[2026-11-24 18:56] Raj-Shah19 (A): done: scene.json now validates against B's schema/scene.schema.json (counterfactuals {}, pair null; anchor-fell scenes rebuilt); 300 scenes regenerated in 7.7 min, schema.check + gen.check pass | next: Phase 7 | blocked on: pushes (no write access yet); Makefile/make smoke
```
