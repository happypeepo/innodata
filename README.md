# innodata — Synthetic Visual-Understanding Benchmark

> **Status:** idea not finalised. See [`context/handoff.md`](context/handoff.md) for the shortlisted ideas and [`AGENTS.md`](AGENTS.md) for who owns what.

## What we're building

An automated pipeline that:

1. **Generates** synthetic images from a chosen image family (procedurally, so we know the ground truth).
2. **Generates questions** about each image that need real visual understanding.
3. **Produces reference answers** straight from the generator's own scene data, not from any model, and checks them with an independent validator.
4. **Evaluates** advanced multimodal models (e.g. Gemini Robotics-ER 2, Gemini 3.x, Claude, GPT) on those tasks and scores them automatically.
5. **Reports** where models do well and where they fail, broken down by type of visual reasoning.

## Problem statement (summary)

Pick an image family and a visual-understanding problem with real practical impact. Explain the application, why errors matter, and how the synthetic examples capture its visual challenges. Every task must need visual understanding, give enough image evidence to answer, and have a reliable answer check that doesn't depend on the model being evaluated.

## Judging criteria → where we address it

| Weight | Criterion | Where we address it |
|---|---|---|
| 25% | Working implementation & repeatability | `make all` runs generate → validate → eval → report end to end with a fixed seed |
| 25% | Validity & validation evidence | `validate/`: reference answers come from scene data, plus independent geometric/programmatic checks and a human spot-check sample |
| 20% | Meaningful variation | Task taxonomy with controlled difficulty knobs (see handoff) |
| 15% | Original approach & engineering judgement | `docs/limitations.md`, cost log, scoped design |
| 15% | Practical impact & communication | Application write-up + demo notebook/app |

## Repo layout (planned)

```
gen/        # Owner A — scene + image generation
tasks/      # Owner B — question templates + reference answers
validate/   # Owner B — independent answer checks
eval/       # Owner C — model runners + scoring
report/     # Owner D — analysis, charts, demo
fixtures/   # shared sample data so everyone can work without waiting
context/    # handoff notes and decisions
```

## Quickstart

_TBD once the idea is locked._ Target:

```bash
make all SEED=42 N=200
```

## Team

See [`AGENTS.md`](AGENTS.md).
