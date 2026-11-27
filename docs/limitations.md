# Limitations

What this benchmark does not show, and why a reader should be careful with the numbers.

## Sim-to-real gap

- **Rendering.** Scenes come from PyBullet's CPU renderer: simple shading with one light direction
  and three brightness presets, hard shadows, and no textures, reflections, motion blur or sensor noise. A model that does well here may still fail on a real
  camera image, and a model tuned on real photos may be slightly handicapped by the synthetic look.
- **Physics.** PyBullet contact and friction are approximations. F8 (what moves when an object is
  removed) and F9 (did the placement succeed) are labelled by re-simulating. They are right *for the
  simulator*, which can differ from a real stack on edge cases (objects that barely balance, rolling
  spheres). We keep a label only when the rerun settles, or when everything still moving has already
  moved more than 2 cm, but the threshold itself is a choice.
- **One fixed camera per scene.** Camera elevation and azimuth vary between scenes, but each
  question is asked from one view. A real robot can move its camera or use a second one.

## Primitive objects

- Objects are boxes, cylinders and spheres in six flat colours, not real household items (YCB
  meshes were left for later). Referring expressions are therefore colour + shape + size + position;
  there is no "the mug with the chipped handle".
- Real objects have handles, transparency, deformable parts and brand text. None of that is tested,
  so the results say little about grasp-point choice on real items.
- F9 places only flat-topped objects (boxes, cylinders) on a target wider than the object, so it
  tests on/off-the-edge outcomes, not careful placement on awkward shapes.

## Template language

- Questions come from templates (at least 3 phrasings per family). Real instructions are messier:
  vague ("the thing next to the cup"), wrong ("the red one" when there are two), or multi-step.
- Every referring expression matches exactly one object by construction. Ambiguity handling,
  asking for clarification, or refusing is not tested.
- All models get the same prompt and answer format (normalised `[y, x]` points, the ER-native
  format). That is fair to ER 2 by design. General models were not prompt-tuned, so a model-specific
  prompt might raise their scores.

## Small N

- About 1,000 tasks over 10 families is roughly 100 per family, fewer once split by difficulty
  knob. Confidence intervals are wide for single cells (for example, F10 with high occlusion).
  The report always shows 95% bootstrap intervals; differences inside them are not claimed.
- 300 base scenes are generated from one seed range. Tasks from the same scene are not independent,
  but the bootstrap resamples tasks, not scenes, so intervals are somewhat too narrow.
- Repeat runs (variance) cover only a 100-task subset, not the full set.
- F8 and F10 need extra physics reruns and paired renders, so they have fewer tasks than the
  single-image families.

## Budget limits

- The total API budget is $100. That allows one main run per model, a text-only run, 3 repeats on
  a 100-task subset, and one ER 2 thinking-budget ablation. It does not allow a sweep over prompts,
  image sizes or sampling settings.
- Sampling uses each provider's default (several current models reject temperature 0), so answers
  are not deterministic. Variance from the repeat runs is reported instead.
- Four models are compared: ER 2, one Gemini generalist, one Claude, one GPT. No open-weight
  model is included, and model versions are previews that may change after our runs.

## Reference answers

- References come from simulator state, never from a model, but the generator and task code can
  still have bugs. Two checks bound this: `validate/independent.py` re-derives answers from the raw
  seg/depth arrays (F8 and F9 can't be re-derived from one image and are reported as not checkable),
  and a 50-task human spot-check estimates the reference error rate. A 50-task sample can only bound
  that rate roughly.
- The visibility thresholds (30% to be a target, 10% to be counted) and the F4 3 cm depth margin are
  design choices. Changing them changes which tasks exist, not how they are scored.
