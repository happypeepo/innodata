# Application: what a robot arm has to get right before it moves

## The use case

A robot arm stands over a cluttered table: a warehouse tote, a kitchen counter, a lab bench.
It is told *"pick up the red cylinder left of the blue box"* or *"clear the table"*. A
vision-language model is its "robot brain": it looks at one camera image and has to answer
three questions before any motor turns.

1. **Which thing?** Find the one object the instruction refers to, among look-alikes.
2. **Where?** Give a pixel the gripper can aim at, or a box around the object.
3. **Can I grab it, and what happens if I do?** Is it free, what is it resting on, what will fall?

Low-level control (joint angles, grasp force) is a separate model. RoboSpatial-Synth tests only
this perception-and-reasoning step, because every wrong answer there turns into a wrong physical
action, and the control model cannot fix it.

Gemini Robotics-ER 2 is built for exactly this step and answers in machine-checkable formats
(normalised points and boxes). We compare it with general frontier VLMs on the same questions to
answer: **does the specialist beat the generalists, and where does each one break?**

## Why each error type matters physically

| Family | Question the robot asks | If the model is wrong, the robot… |
|---|---|---|
| F1 Referring pointing | "Point to the red cylinder left of the blue box." | grasps the **wrong item**: the right shape and colour, the wrong one of two. In picking, that's a mis-shipped order. |
| F2 Referring detection | "Draw a box around the smallest green object." | plans the grasp around the wrong extent: the gripper opens too narrow, or closes on a neighbour. |
| F3 Counting under occlusion | "How many spheres are on the table?" | ends a "pick all" task early and leaves items behind, or keeps searching for an item that isn't there. |
| F4 3D relations | "Is the mug in front of or behind the bowl?" | reaches **through** the nearer object. A front/behind flip is a collision. |
| F5 Support | "What is the yellow box resting on?" | lifts the bottom of a stack and drops everything on it. |
| F6 Free to pick | "Point to an object you can pick up without moving anything else." | starts a grasp on an object something else is resting on, or on one it can barely see. |
| F7 Clearing order | "List the objects in an order that clears the stack safely." | removes a supporting object first and topples the stack. |
| F8 Counterfactual | "If the blue box is removed, which objects move?" | misses a knock-on fall, the main way a single pick damages several items. |
| F9 Placement success | "Goal: red box on the green box. Did it succeed?" | reports success on a failed place and moves on, so the error compounds through the rest of the task. |
| F10 Before/after change | Two images: "Which object moved?" | can't tell what its own last action changed, or that a person moved something. |

F1, F2 and F6 are about **where**; F3 and F4 about **what is there**; F5, F7, F8 about **what
holds what up**; F9 and F10 about **checking its own work**. The report shows accuracy for each
family separately, so a model that points well but misjudges support is visible as such.

## How scoring separates right from wrong

Every reference answer comes from the simulator's own state, never from a model. Every model answer
is scored by a deterministic geometric check, with no LLM judge.

**Where the reference comes from.** Each scene is rendered with PyBullet, which gives the exact
per-object segmentation mask, depth, 3D pose and contact graph after physics settles. F8 and F9
labels come from **re-running the physics**: delete the object (or drop it on its target), settle
again, and record what actually moved more than 2 cm (or whether it actually rests on the target).

**How answers are checked** (`validate/checkers.py`):

| Answer | Check | Right means |
|---|---|---|
| point (F1) | `point_in_mask` | the pixel lands on the target's own mask; a pixel on a neighbour or the table is wrong |
| point (F6) | `point_in_any_mask` | the pixel lands on *any* object that is free to pick |
| bbox (F2) | `bbox_iou` | intersection-over-union with the reference box ≥ 0.5 |
| count, choice, bool (F3, F4, F5, F9, F10) | `exact` | the same number, option letter or yes/no |
| sequence (F7) | `topo_order_valid` | any order where nothing is removed before what rests on it; every valid order counts |
| set (F8) | `set_f1` | headline accuracy needs the exact set (F1 = 1.0); the partial F1 is reported as mean score |

**Why a right answer can't be a lucky guess.** The task set is built so that guessing doesn't pay:

- **Only one object matches.** Every referring expression matches exactly one object in the scene.
- **The target can be seen.** Targets are at least 30% visible. For counting, objects under 10%
  visible are ruled out by construction, so the count isn't a judgement call.
- **Relations have a margin.** For F4, front/behind needs at least 3 cm of depth difference, or the
  task is dropped as ambiguous.
- **Answers are balanced.** Yes/no and multiple-choice answers are balanced so that always giving
  the most common answer scores near chance. The majority baseline per family is reported in
  `validation_report.json`.
- **Text-only baseline.** Every model also answers the same questions **without the image**. The
  report's image-vs-text gap shows how much of each family really needs vision.
- **References are checked independently.** `validate/independent.py` re-derives answers from the
  raw seg and depth arrays without reading the generator's contact graph. A human spot-check of
  50 tasks estimates how often the references themselves are wrong.

**What the scores do not hide.** A reply that can't be parsed scores 0 but is counted separately
(parse-failure rate). Headline accuracy is reported with a 95% bootstrap confidence interval over
tasks, and repeat runs on a 100-task subset show run-to-run spread. So "model A beats model B" is
only claimed when the intervals say so.
