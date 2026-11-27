# Pitch talk track (5 minutes)

Five sections, one per judging criterion, in the order that tells the story best: the problem
first, then how we know our answers are right, then what we found. Each section has what is on
screen, what to say, and the proof to point at if a judge asks.

**Numbers in `‹…›` come from the real run.** Fill them in from `report/out/summary.md` and
`data/validation_report.json` after the full run. Don't quote a number that isn't in those files,
and don't claim a difference between two models when their 95% intervals overlap.

| # | Section | Criterion (weight) | Time | Speaker (suggested) |
|---|---|---|---|---|
| 1 | The robot's three questions | Practical impact & communication (15%) | 0:00–0:45 | D |
| 2 | Answers from the simulator, not a model | Validity & validation evidence (25%) | 0:45–1:45 | B |
| 3 | Ten ways to be wrong | Meaningful variation (20%) | 1:45–2:45 | A |
| 4 | What we found | Original approach & engineering judgement (15%) | 2:45–4:00 | C + D |
| 5 | Run it yourself | Working implementation & repeatability (25%) | 4:00–5:00 | C |

---

## 1. The robot's three questions (0:00–0:45)

**On screen:** one rendered cluttered scene, full screen.

> A robot arm is looking at this table. Before it moves, it has to answer three questions:
> *which thing*, *where exactly*, and *can I grab it without knocking something over*. Every
> wrong answer here becomes a wrong physical action: the wrong item picked, a reach through
> another object, a stack toppled. The motor controller can't fix a wrong answer from the vision
> model.
>
> Gemini Robotics-ER 2 came out in July and is built for exactly this step. We asked: **does the
> specialist actually beat general frontier models at it, and where does each one break?**

**Proof if asked:** `docs/application.md`, the table of what each error costs a robot.

## 2. Answers from the simulator, not a model (0:45–1:45)

**On screen:** the same scene with the segmentation mask and depth map beside it, then one
question with its reference answer drawn in aqua.

> Most VLM benchmarks have a person or another model write the answer key. We don't. We render
> every scene in a physics simulator, so we know each object's exact mask, depth, pose, and what
> is resting on what. Every reference answer is computed from that state. For "if you remove the
> blue box, what moves?", we actually delete it and re-run the physics.
>
> Then we make sure a right answer can't be a lucky guess. Every description matches exactly one
> object. Targets are at least 30% visible. Front/behind questions need 3 cm of depth
> difference. Yes/no and multiple-choice answers are balanced, so always saying "yes" scores
> about chance.
>
> And we checked the answer key itself, two ways. A second script re-derives the answers from the
> raw pixels without reading the simulator's contact graph: it agrees on **‹independent %›** of
> tasks. A person checked 50 tasks by eye: **‹human %›** agreement. Models are scored by a
> geometric check, never an AI judge: did the point land inside the mask, does the box overlap
> by at least half.

**Proof if asked:** `data/validation_report.json` (rejection counts by reason, both agreement
rates, majority baselines), `validate/evidence/human_check_seed<N>.jsonl`, `validate/checkers.py`.

## 3. Ten ways to be wrong (1:45–2:45)

**On screen:** a 2×5 grid, one example image and question per family.

> We ask ten kinds of question, from easy to hard for a robot. *Where*: point to an object, draw
> a box around it, point to something you can pick up freely. *What's there*: count objects that
> are half hidden, say what's in front. *What holds what up*: what is this resting on, in which
> order do I clear the stack, what falls if I remove this. And *checking its own work*: did the
> placement succeed, and which object moved between two photos.
>
> Every scene also turns difficulty knobs: 3 to 20 objects, how much is hidden, look-alike
> distractors, camera angle, lighting, object size, how much is stacked. That's how we can say
> not just *whether* a model fails but *when*.

**Proof if asked:** the F1–F10 table in `README.md`; `gen/` CLI flags for every knob; the knob
values are recorded on every task.

## 4. What we found (2:45–4:00)

**On screen:** the report's "accuracy by family" chart, then the difficulty curves, then 2–3
failure-gallery cards.

> Here's accuracy per family with 95% intervals. *(Point at the clearest gap.)*
> **‹Model›** leads on **‹families›**; **‹model›** leads on **‹families›**. Overall, ER 2 scores
> **‹ER 2 overall %›** against **‹best generalist %›** for the best general model.
>
> The difficulty curves show *where* it breaks: **‹e.g. pointing drops from X% to Y% once more
> than half the target is hidden›**.
>
> Two checks keep us honest. We also ask every question *without the image*: the gap is
> **‹image vs text-only gap›** points, so these questions really need vision. And here is what
> the failures look like. *(Gallery card.)* This is a wrong-referent error: the right colour and
> shape, the wrong one of two. On a robot, that's the wrong item shipped.
>
> Judgement calls we made: deterministic checkers instead of an AI judge; the ER-native answer
> format for everyone, so it's fair to ER 2; a dry-run cost estimate and a hard budget cap
> before any paid call. Total spend: **‹$ spent› of $100**.

**Say only what the chart shows.** If the intervals overlap, say "we can't separate them at this
sample size", which is itself a finding. Where ER 2 loses, say so.

**Proof if asked:** `report/out/index.html`, `summary.md` (overall, by family, by knob, gap, runs,
cost), `docs/limitations.md`.

## 5. Run it yourself (4:00–5:00)

**On screen:** the Streamlit demo, live: type a seed → Render → pick a question → Ask → each
model's answer drawn on the image with ✓/✗. Keep the terminal with `make smoke` ready as backup.

> Everything you've seen is reproducible from a seed. Same seed, byte-identical scenes and
> questions. *(Render a new seed live.)* This scene didn't exist a minute ago. Here are its
> validated questions; ask one, and you get each model's answer scored on the spot.
>
> `make smoke` runs the whole pipeline (fixtures → tasks → mock model → report → **‹N› tests**)
> with no API keys. Every model answer is cached, so a rerun costs nothing, and each results file
> records the hash of the task file it was run on. 300 scenes take about 10 minutes on a laptop.
>
> What this doesn't show: it's simulation with simple shapes and template questions, so it tells
> you about spatial reasoning, not about real kitchens. That's in our limitations doc. Thank you.

**Proof if asked:** `README.md` Quickstart and Full run; `make smoke` output; `data/results/<model>.meta.json`.

---

## Demo checklist (before going on stage)

- [ ] `streamlit run report/demo.py -- --data data/` is open, the browse tab is on a scene with a clear win and a clear failure, and you know a seed that renders a good live scene in under 10 s.
- [ ] `.env` has the keys, or ask `mock` only in the live tab (say so: "mock answers randomly").
- [ ] `report/out/index.html` open in a second tab, scrolled to the family chart.
- [ ] Backup: screenshots of the demo and the charts, in case the Wi-Fi or an API is down.
- [ ] Every `‹…›` above is filled in from the files, and checked against the intervals.

## Likely questions

| Question | Short answer |
|---|---|
| Isn't synthetic data too easy / unrealistic? | Yes, the scenes are simpler than a real table, which makes failures more telling: if a model misjudges support on three plain boxes, it won't do better on a real shelf. Sim-to-real is the first item in `docs/limitations.md`. |
| How do you know your answer key is right? | It's computed from simulator state, re-derived independently from raw pixels (**‹independent %›** agreement), and spot-checked by a person (**‹human %›**). |
| Couldn't a model guess its way to a good score? | Answers are balanced so the majority baseline is about chance, and the text-only run shows how much each family needs the image. |
| Why not use an LLM to judge answers? | Every answer type has a geometric or exact check, so there's nothing to judge. An LLM judge would add its own errors. |
| Is the comparison fair to the general models? | They get the same prompt and the ER-native output format, without model-specific prompt tuning. That favours ER 2 slightly, and we say so. |
| Why are the intervals so wide? | About 100 tasks per family. We only claim a difference when the intervals separate. |
| What would you do next? | Real object meshes (YCB), multi-view cameras, messier language, and an open-weight model. |
