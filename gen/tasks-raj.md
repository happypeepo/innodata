# Role A — Scene Generator: Raj's task list

Work top to bottom. Tick each box only when its **Done when** line is true.

Tags:
- **[AGENTS]** stated in `AGENTS.md` or `context/handoff.md`
- **[decide]** not settled anywhere yet; your call (write the answer down)
- Everything else is a how-to step, not a team decision.

Branch: `gen/scene-generator` (created from `main` at `f142509`), following the `<role>/<short-desc>` convention in `AGENTS.md`.

---

## Phase 0 — Setup

- [x] **0.1 Read the two docs.** Read `AGENTS.md` and `context/handoff.md` fully. [AGENTS: "read before you start"]
  - Done when: you know the `scene.json` contract and the validity rules.

- [ ] **0.2 Claim Role A.** In `AGENTS.md`, replace `<NAME_A>` in the roles table with your name. Commit and push. [AGENTS: "Claim a role by putting your name in the table and pushing"]
  - Done when: the roles table on GitHub shows you as A.

- [x] **0.3 Install Python 3.11.** [AGENTS: Python 3.11]
  This Mac has Python 3.13 (Homebrew) and `uv`, but no 3.11.
  ```bash
  uv python install 3.11
  uv venv --python 3.11 .venv
  source .venv/bin/activate
  ```
  `.venv/` is already in `.gitignore`.
  - Done when: `python --version` prints 3.11.x inside the venv.

- [x] **0.4 Install packages.**
  ```bash
  uv pip install pybullet numpy pillow
  ```
  `requirements.txt` doesn't exist yet. It's a shared file, so add these through a PR and tag the other owners. [AGENTS: shared files are edited by PR]
  - Done when: `python -c "import pybullet, numpy, PIL"` runs without errors.
  - Note: pybullet 3.2.7 has no macOS arm64 wheel for 3.11, so it builds from source, and on the macOS 27 SDK its bundled zlib fails (`fdopen` macro). It must also be built against numpy: otherwise `p.isNumpyEnabled()` is 0 and every `getCameraImage` returns ~800k-element Python tuples. Install numpy first, then:
    ```bash
    uv pip install numpy pillow setuptools wheel
    CFLAGS="-Dfdopen=fdopen" CXXFLAGS="-Dfdopen=fdopen" uv pip install --no-build-isolation pybullet
    python -c "import pybullet as p; print(p.isNumpyEnabled())"   # must print 1
    ```

- [x] **0.5 PyBullet smoke test.**
  ```bash
  python -c "import pybullet as p; print(p.connect(p.DIRECT))"
  ```
  - Done when: it prints `0`, with no error.

- [x] **0.6 Create the package skeleton.**
  - `gen/__init__.py` (empty)
  - `gen/__main__.py`, so that `python -m gen` works [AGENTS]
  - `gen/scene.py` [AGENTS]
  - Any other module names are your choice.
  - Done when: `python -m gen` runs (it can just print "todo").

---

## Phase 1 — Decide before coding

Write each answer in the **Decisions** table in `context/handoff.md`. [AGENTS: "Decisions go in the Decisions table"]
Tell Dhruv the first three: he reads your files and will be affected.

- [x] **1.1 Seg PNG background value.** [decide] PyBullet uses `-1` for "nothing", but a PNG can't store -1. Also choose the bit depth (8-bit only works while ids stay under 256).
- [x] **1.2 How the table appears.** [decide] Does it get an entry in `objects[]`, and what is its seg value? In `contacts` it is the string `"table"`. [AGENTS]
- [x] **1.3 Colour list.** [decide] Names must be distinct enough that a phrase like "the red cylinder" matches exactly one object. [AGENTS: uniqueness rule]
- [x] **1.4 Shapes and size ranges.** [decide] Start from box, cylinder and sphere. [AGENTS] Meshes from `pybullet_data` are optional.
- [x] **1.5 Camera.** [decide] Distance, target point, field of view, and near/far planes. The format only stores elevation and azimuth plus the two matrices. [AGENTS]
- [x] **1.6 Settling.** [decide] The velocity threshold that counts as "still", and the maximum number of steps.
- [x] **1.7 Per-scene seed rule.** [decide] How each scene's seed comes from `--seed` and the scene index. The AGENTS example pairs `s_000123` with `"seed": 123`.
- [x] **1.8 Fully hidden or fallen objects.** [decide] Drop them, re-spawn, or keep them with `visible_frac: 0`.
  - Done when (whole phase): every item above has a row in the handoff Decisions table.
  - Result (2026-11-24): all eight are recorded, plus data-relative paths. Dhruv (B) chose option A for 1.1–1.4 and 1.8; Raj chose A for 1.5–1.7.

---

## Phase 2 — Job 1: build and render one scene

[AGENTS: "`gen/scene.py`: PyBullet table + random objects, drop and settle physics, render RGB/depth/seg at the chosen camera."]

- [x] **2.1 Empty world.** Connect in `DIRECT` mode, add the `pybullet_data` search path, set gravity, load `plane.urdf` and `table/table.urdf`.
  - Done when: `p.getAABB(table_id)` prints the table bounds. Note the table-top height; you need it to place objects.
  - Result: table top is 1.5 × 1.0 m, centred at the origin, top surface at **z = 0.625 m**. `getAABB` reports 0.626 because it pads by the ~1 mm collision margin, so `make_world()` reads the top off the collision box instead.

- [x] **2.2 Seeded randomness.** Create `rng = np.random.default_rng(seed)` once. Pass it everywhere and never use `random` or `np.random.*` directly.
  - Done when: grepping `gen/` shows no other random source.

- [x] **2.3 One object.** Spawn a single box above the table (`createCollisionShape` + `createVisualShape` + `createMultiBody`), step the simulation, print its final pose.
  - Done when: the box ends up resting on the table top (its z matches the table height plus half its size).

- [x] **2.4 Many random objects.** Spawn `n` objects with shape, colour, size and position drawn from your Phase 1 lists. Keep a record per object: your id (`o1`, `o2`, …), body id, shape, colour, `size_cm`.
  - Done when: the same seed always gives the same list of objects.
  - Result: same list for seeds 0–3 at n = 8 and 20. Each object is dropped just above the table, or just above the highest earlier object whose footprint it overlaps.

- [x] **2.5 Settle loop.** Step until every object's velocity (`getBaseVelocity`) is below your threshold, or the step cap is reached. Then read each pose with `getBasePositionAndOrientation`.
  - Done when: printed poses don't change if you step 100 more times.
  - Result: over 40 scenes (n = 3–20), the worst movement in 100 extra steps was 0.14 mm (the 1 mm/s threshold allows up to 0.4 mm). Settling takes a median of ~200 steps. "Still" must hold for 24 steps (0.1 s) in a row, so the spawn instant or a bounce apex doesn't count. Rolling and spinning friction are 0.002 (at 0.001, small spheres rolled off). About 1 scene in 40 still hits the 2,400-step cap from solver jitter in stacks (<0.4 mm/s); it is rendered with a warning, as decided.

- [x] **2.6 Camera and RGB.** Build the view matrix (`computeViewMatrixFromYawPitchRoll`) and projection matrix (`computeProjectionMatrixFOV`). Call `getCameraImage(1024, 768, view, proj)` and save RGB as a PNG. [AGENTS: 1024×768 PNG]
  - Done when: you open the PNG and see the table with every object in frame.
  - Result: checked s_000000 (8 objects) and s_000002 (20) by eye, and every object centre projects inside the image. Renderer is `ER_TINY_RENDERER` (CPU; the only one available in DIRECT mode).

- [x] **2.7 Depth.** Convert the depth buffer to metres with `far * near / (far - (far - near) * d)` and save it as `.npy`.
  - Done when: depth values on objects lie between `near` and `far`, and nearer objects have smaller values.
  - Result: object depths 0.68–1.14 m; rank correlation with true camera distance 0.98–1.0. Table pixels back-project to z = 0.6253 ± 0.0015 m, so depth is in true metres. Saved as float32.

- [x] **2.8 Segmentation.** Reshape the seg buffer to (768, 1024), apply your background decision (1.1), save as PNG. [AGENTS: pixel value = `seg_id`]
  - Done when: the unique values in the PNG are exactly your object seg ids plus the table and background values. [AGENTS self-check]
  - Result: exact match on both test scenes ({0, 1} plus seg ids 2…n+1).

- [x] **2.9 Write `scene.json` (job 1 fields).** `scene_id`, `seed`, `generator_version` (`gen@<git-sha>`), `image_path`, `depth_path`, `seg_path`, `width`, `height`, `camera` (both matrices plus elevation and azimuth), and per object `id`, `seg_id`, `shape`, `color`, `size_cm`, `pose {xyz_m, quat}`. Use the paths `data/images/`, `data/depth/`, `data/seg/` and `data/scenes/`. [AGENTS]
  - Done when: the file matches the AGENTS example's shape. Once Dhruv commits `schema/`, it must also validate against it.
  - Result: the job-1 keys match the example's shape and order. Paths are data-relative (`images/…`), as decided. `size_cm` uses 0.1 cm steps. **Still to do: validate against `schema/` once B pushes it.**

- [x] **2.10 Determinism check.** Generate the same seed into two folders and compare the files.
  - Done when: all files are byte-identical.
  - Result: seeds 0 and 2, generated in two separate processes, gave 8 byte-identical files, which also match a same-process run.

- [x] **2.11 Commit.** Small commit on `gen/scene-generator`. Add a log line to `context/handoff.md`: done / next / blocked on. [AGENTS]

---

## Phase 3 — Job 2: per-object facts

[AGENTS: "bbox, centroid, pixel area, `visible_frac` (render each object alone vs. in the full scene), mean depth."]

- [x] **3.1 Mask-based facts.** For each object, `mask = seg == seg_id`, then:
  - `pixel_area = mask.sum()`
  - `bbox_2d = [x_min, y_min, x_max, y_max]`. This order matches the AGENTS example `[412, 300, 470, 395]` with centroid `[441, 350]`.
  - `centroid_2d` = mean `[x, y]` of the mask pixels
  - `depth_mean_m` = mean of `depth[mask]`
  - Done when: drawing each bbox and centroid on the RGB image puts them on the right object.
  - Result: checked by eye on s_000002 (20 objects). Every box hugs its object and every centroid sits on it. bbox max is inclusive, centroid is rounded to int (Decisions table).

- [x] **3.2 Zero-pixel objects.** Apply your decision from 1.8 when `pixel_area == 0`.
  - Done when: no crash on a scene with a fully hidden object.
  - Result: hand-built scene with a 4 cm sphere behind a 10 cm box gives `pixel_area: 0`, `visible_frac: 0.0` and `null` bbox/centroid/depth.

- [x] **3.3 "Render alone" helper.** Move every other object out of view with `resetBasePositionAndOrientation`, render seg with the same camera, then put them back. Do not call `stepSimulation` in between.
  - Done when: poses after restoring are exactly the poses before.
  - Result: pose and velocity are bit-identical. Restoring uses `saveState`/`restoreState`, because putting a pose back through `resetBasePositionAndOrientation` shifts the quaternion by ~1e-17.

- [x] **3.4 `visible_frac`.** `visible_frac = full-scene pixels ÷ alone pixels`. Keep each object's alone-mask; Phase 4 reuses it.
  - Done when: an object nothing covers gets about 1.0, a partly hidden one is below 1.0, and no value exceeds 1.0.
  - Result: over 63 objects in seeds 0–5, 33 are exactly 1.0, 30 fall between 0.50 and 0.99, and none exceed 1.0. A hand-built sliver reads 0.016. The alone-masks are kept in memory for Phase 4.
  - Cost: one extra render per object. Renders took ~2 s each while the machine was under heavy load (load average 22 on 8 cores), so re-time this in 6.1/6.4.

- [x] **3.5 Add the fields** `bbox_2d`, `centroid_2d`, `pixel_area`, `visible_frac`, `depth_mean_m` to `objects[]`. [AGENTS]

- [ ] **3.6 Hand off.** Commit, add a log line, tell Dhruv. This unblocks F1, F2, F3 and F4 for him. [AGENTS "Needs from A"]
  Context: he applies the 30% visibility rule and the 3 cm depth-margin rule using these values. [AGENTS]
  - Status: committed and log line added. **Still to do:** push, then tell Dhruv.

---

## Phase 4 — Job 3: contact and occlusion graphs

[AGENTS: "Contact graph from `getContactPoints` after settling. Occlusion pairs from the seg comparison."]

- [ ] **4.1 Contacts.** For every pair of bodies (objects plus the table), call `p.getContactPoints(bodyA=a, bodyB=b)`. If they touch, decide which is on top and append `{"top": ..., "bottom": ...}`, with the table as `"table"`. [AGENTS]
  - Rule for top/bottom: [decide]. Comparing centre heights is simplest. The contact normal (index 7 of each contact tuple) is more robust for tall or tipped objects.
  - Done when: in a scene you build by hand, with box B dropped onto box A, you get `{"top": B, "bottom": A}` and `{"top": A, "bottom": "table"}`.

- [ ] **4.2 Occlusion.** For each object, look at the full-scene seg pixels under its alone-mask (from 3.4). Every other object id found there is in front of it. Use `frac = those pixels ÷ alone pixels` and append `{"front": ..., "back": ..., "frac": ...}`. [AGENTS]
  - Done when: in a scene you build by hand, with one object placed between the camera and another, you get one entry with a sensible `frac`.

- [ ] **4.3 Add `contacts` and `occludes`** to `scene.json`.

- [ ] **4.4 Hand off.** Commit, add a log line, tell Dhruv. This unblocks F5, F6 and F7. [AGENTS]

---

## Phase 5 — Job 4: difficulty knobs and command line

[AGENTS: "Difficulty knobs as CLI flags. `python -m gen --seed 0 --n 300 --out data/`."]

- [ ] **5.1 Basic CLI.** `gen/__main__.py` with `argparse` flags `--seed`, `--n` and `--out`, looping over scenes using your seed rule (1.7).
  - Done when: `python -m gen --seed 0 --n 5 --out data/` writes 5 complete scenes.

- [ ] **5.2 `n_objects` knob** (range 3–20). [AGENTS]
- [ ] **5.3 `camera` knob** (elevation and azimuth). [AGENTS]
- [ ] **5.4 `occlusion` knob** (max hidden fraction). [AGENTS] How it changes generation: [decide]
- [ ] **5.5 `distractors` knob** (same colour or same shape as the target). [AGENTS] How: [decide]
- [ ] **5.6 `lighting` knob.** [AGENTS] The example value is `"dim"`. Values and how they work: [decide]
- [ ] **5.7 `scale_range` knob.** [AGENTS] Meaning: [decide]
- [ ] **5.8 `difficulty` block.** Write the knob values used for each scene, e.g. `{"n_objects": 9, "occlusion": 0.35, "distractors": 2, "lighting": "dim"}`. [AGENTS]
  - Done when: every scene has the block, and changing a flag visibly changes the scenes.

- [ ] **5.9 Commit plus log line.**

---

## Phase 6 — Job 5: hit the "done" target

[AGENTS: "300+ scenes are generated deterministically from a seed in under ~15 min on a laptop."]

- [ ] **6.1 Full run, timed.**
  ```bash
  time python -m gen --seed 0 --n 300 --out data/
  ```
  - Done when: 300 complete scenes exist and the time is about 15 minutes or less.

- [ ] **6.2 Determinism at scale.** Run it again into a second folder and compare hashes of all files.
  - Done when: everything matches.

- [ ] **6.3 Seg check on all scenes.** A small script confirms that every seg PNG's ids match its `objects[]`. [AGENTS]

- [ ] **6.4 Speed up if needed.** Usual costs: the extra render per object in 3.3, and long settle loops. Scenes are independent, so you can run them in parallel processes.

- [ ] **6.5 Commit plus log line.**

---

## Phase 7 — Merge

- [ ] **7.1 `make smoke`** must pass before merging. [AGENTS] The `Makefile` doesn't exist yet; check whether someone has added it.
- [ ] **7.2 Make sure the branch is pushed.** `git push -u origin gen/scene-generator`
- [ ] **7.3 Open a PR into `main`.** You may merge your own PR inside `gen/`. Changes to shared files (`requirements.txt`, `Makefile`, `context/`) need one approval. [AGENTS]
- [ ] **7.4 Format changes**, if you needed any: one PR that edits `schema/`, `fixtures/` and `AGENTS.md` together. Tag all owners, add only new optional fields, and log it in the Decisions table. [AGENTS]
