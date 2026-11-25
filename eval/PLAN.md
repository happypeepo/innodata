# Role C (Eval Harness): implementation plan

> Owner: `@happypeepo`. Status: **approved 2026-11-25**, Phase 0 + Phase 1 done. Branch: `eval/harness`.
> Contracts this builds on: `AGENTS.md` (v1) + B's `schema/result.schema.json` and `validate/checkers.py` (PR #1, #2).

## 1. Where the project stands (2026-11-25)

| Role | Owner | State | Gaps that affect C |
|---|---|---|---|
| A, Generator | @Raj-Shah19 | **Strong.** `gen/scene-generator`: 300 scenes in ~8–13 min, deterministic, valid against B's schema, stacking knob, `manifest.json`, rebuild API. **No PR yet**, waiting on `make smoke`. | Real scenes have `counterfactuals: {}` and `pair: null`, so **F8 and F10 make zero tasks on real data** until someone fills them (agreed between A and B, but no owner or date). `data/` isn't shared, so C regenerates it locally (needs `pybullet`). |
| B, Tasks & Validation | @Dhruvk2004 | **Strong.** 8 stacked PRs (#1–#5, #7, #8). All 10 families, rules, balancing, independent re-derivation, spot-check tool. **146 tests pass**, schema check OK, 38 tasks from fixtures, 100% independent agreement. | **Nothing is merged**, and every PR is waiting on review of #1. C needs #1 (schema/fixtures) and #2 (`score()`). Majority baseline is above chance for F3/F5/F9/F10 on fixtures (tiny sample, so recheck on real data). |
| C, Eval | @happypeepo | Not started (this plan) | n/a |
| D, Analysis | **unclaimed** | Not started. @hritz23 hasn't accepted the repo invite yet. | Nobody consumes `results.jsonl` yet. No demo, no write-up. **Biggest team risk.** |
| Shared | n/a | **No `Makefile`, so `make smoke` doesn't exist.** A and B both list it as their blocker. | C proposes to own it (§4, D4). |

## 2. What C delivers

```
eval/
  __main__.py      # CLI: python -m eval --model <name> --tasks <file> [--data DIR] [--condition image|text_only] [--runs N] [--limit N] [--subset FILE]
  models/
    __init__.py    # registry: name -> adapter
    base.py        # Adapter interface: ask(images: list[Path], prompt: str) -> Reply(text, tokens_in, tokens_out, latency_ms)
    mock.py        # seeded random *valid* answers per answer_type (doubles as the random baseline)
    gemini.py      # google-genai SDK: ER 2 + general Gemini
    anthropic.py   # anthropic SDK
    openai.py      # openai SDK
  prompt.py        # one prompt builder, identical for every model
  parse.py         # raw text -> parsed dict in storage format (pixels), or None
  cache.py         # append-only JSONL cache keyed by (model, task_id, condition, run, prompt_hash)
  pricing.py       # $/token table + cost estimate for a planned run (--dry-run)
  tests/           # parser, prompt, cache/resume, end-to-end on fixtures with mock
Makefile           # (shared) smoke / test / gen / tasks / eval / report
```

Output: `data/results/<model>.jsonl` (one row per task × condition × run, valid against `schema/result.schema.json`), plus `data/results/<model>.meta.json` with the run config (model id, SDK version, temperature, thinking budget, prompt version, git sha, date).

## 3. Design

**Prompt (same for all models).** A short system line: *"You are looking at a photo of a tabletop. Answer with JSON only."* Then the task's `question`, then its `answer_format` string verbatim (B already writes the ER-native `[y, x]` 0–1000 format there). F10 sends both images in order with "Image 1 (before)" and "Image 2 (after)" labels. Images are sent as the original 1024×768 PNG, no resizing. `prompt_hash` = sha256 of the system line + text + image file hashes, so any template change misses the cache.

**Parsing (lenient on wrapping, strict on meaning).**
1. Pull out the JSON: strip code fences, take the last `{...}` or `[...]` that parses. Accept the ER habit of `[{"point": [y, x], "label": ...}]` and take the first item.
2. The key must match `answer_type`. Types are coerced only where it's unambiguous: `"3"` → 3, `"a"` → `"A"`, `"true"` → true. No guessing from prose ("yes it worked" is a parse failure).
3. Coordinates: `[y, x]` 0–1000 → `[x, y]` px with `x = xn/1000·W`, `y = yn/1000·H`. Boxes `[ymin, xmin, ymax, xmax]` → `[x1, y1, x2, y2]` px. Values aren't clamped: off-image points stay off-image and `score()` gives them 0.
4. Parse failure → `parsed: null, parse_ok: false, score: 0` (B's schema enforces this). D reports the parse-failure rate separately from accuracy.

**Scoring.** `validate.checkers.score(task, parsed, load_scene(data, scene_id))`. No stub: C's branch sits on top of PR #2 until it merges (D2). F8 keeps the raw F1 in `score`; headline accuracy counts only `score == 1.0` (decided 24 Nov).

**Running.**
- Thread pool with a per-model concurrency limit (default 4) and retry with exponential backoff and jitter on 429/5xx/timeouts (max 5 tries). After that the row gets `error: "<type>"`, `parse_ok: false`, `score: 0`, and isn't cached, so a rerun retries it.
- **Resume = cache.** Every finished call is appended to `data/cache/<model>.jsonl` straight away. A rerun only calls what's missing, so stopping halfway costs nothing.
- `--dry-run` prints the call count and estimated cost before any money is spent. A hard `--budget-usd` stops the run when spend passes it. A warning at 70% goes in the handoff (AGENTS rule).
- Seeds: `--seed` fixes mock answers and subset sampling. Temperature 0 where the API allows it, otherwise the provider default, written to `meta.json`.

**Conditions and baselines (what proves the questions need vision).**
| Run | What | Tasks |
|---|---|---|
| `image` | main eval | all (~1,000) |
| `text_only` | same prompt, no image | all, or a 300-task stratified subset if budget is tight (D5) |
| `random` | `mock` model, seeded | all (free) |
| majority | already in B's `validation_report.json` | n/a |
| repeats | `--runs 3` | 100-task stratified subset (`data/subset_100.txt`, seeded, balanced per family) |

## 4. Decisions for you

| # | Decision | My recommendation | Why |
|---|---|---|---|
| **D1** | **Which models?** | `gemini-robotics-er-2-preview` (primary) + **one Gemini generalist** (same vendor, isolates "specialist vs generalist") + **one Claude** + **one GPT**. Optional: an open model (Qwen-VL via OpenRouter) as a cheap baseline. | 4 models is enough for the story and keeps cost and time down. **I need the exact model IDs you have keys for.** |
| **D2** | How C uses B's `score()` before PR #2 merges | **Merge #1 and #2 now** (you're the lead; both are reviewed by tests: 146 green). Fallback: stack `eval/harness` on `validate/checkers`. | Skips a throwaway stub. Unblocks A as well (A needs #1's schema on main). |
| **D3** | SDKs | Official SDKs per provider (`google-genai`, `anthropic`, `openai`), ~40 lines each. Not LiteLLM. | Fewer moving parts. ER 2's thinking-budget and point-format options are Gemini-specific anyway. |
| **D4** | Who writes the `Makefile` / `make smoke` | **C.** `make smoke` = `schema.check fixtures` → `tasks --data fixtures` → `eval --model mock` → `report` (skipped until D's exists) → `pytest`. | It's the cross-role pipe and C sits in the middle. A and B are both blocked on it. It's the first thing I'd push. |
| **D5** | `text_only` coverage | Full set if budget ≥ ~$60, else 300-task subset | Text-only calls are cheap (no image tokens), but they're still ~25% of calls |
| **D6** | ER 2 thinking budget | Main run at the **API default**. One ablation on the 100-subset at budget 0 vs. high. | Thinking trades latency for accuracy. An ablation is a cheap, interesting finding; doing it on everything doubles cost. |
| **D7** | Parse strictness | Lenient extraction, strict meaning (§3). Report parse failures separately. | Fair to models that wrap JSON in prose, without the scorer guessing intent |
| **D8** | Data for real runs | C regenerates locally: `python -m gen --seed 0 --n 300` then `python -m tasks --data data/ --seed 0`. **Freeze** that `tasks.jsonl` (sha in `meta.json`) before any paid run. | Every model is scored on byte-identical tasks. Same seed means the same data for anyone. |
| **D9** | API budget | **Need a number.** Rough estimate below. | Gates D1/D5/D6 |
| **D10** | F8 / F10 on real data | Ask A and B to name an owner and date for counterfactuals + paired renders. If they're not ready by CP2, run F1–F7 + F9 and add F8/F10 later (cache makes it incremental). | Don't block the eval on it |
| **D11** | Role D | Ask @hritz23 to accept the invite and claim D today. If not, C also builds a minimal `report/analyze.py` (accuracy tables + 2 charts) so the pipeline is end to end. | Judges score "runs end to end". D is currently empty. |

**Rough cost** (verify prices first; handoff notes sources disagree): ~1,000 tasks × 4 models × (image + text-only) + 3 × 100 repeats ≈ **9–10k calls**, ~1.3k input tokens per image call. At ~$1–3 per M input tokens that's **~$15–45**, plus output/thinking tokens, which can match or beat that on thinking models. Budget **$50–100** for headroom. `--dry-run` gives the real figure before anything is spent.

## 5. Checklist

### Phase 0: unblock (first, ~1 h)
- [x] Claim role C in `AGENTS.md` (in this branch)
- [x] D2: merged B's #1 → #2. **D1 (model IDs) and D9 (budget) still open.**
- [x] `Makefile` with `test` and `smoke` (fixtures → tasks → mock eval → pytest). PR, needs one approval. Tell A and B they're unblocked.
- [x] `requirements.txt`: add `google-genai`, `anthropic`, `openai`, `python-dotenv`. `.env.example` with the key names (no values).

### Phase 1: harness on fixtures, no API keys (~3 h)
- [x] `models/base.py` + `models/mock.py` (seeded, valid answers in model format: `[y, x]` 0–1000, letters, etc.)
- [x] `prompt.py` + `prompt_hash`. Test: every fixture task gets a prompt, F10 gets 2 images.
- [x] `parse.py` + tests: fences, prose around JSON, ER list-of-points form, swapped/missing keys, out-of-range, `[y,x]`→`[x,y]` maths on a known pixel, bbox order
- [x] `cache.py` + resume test: kill halfway, rerun, no repeated calls
- [x] `__main__.py`: writes `results/<model>.jsonl` + `meta.json`. **Every row passes `python -m schema.check`.**
- [x] End-to-end test: mock on fixtures → rescoring rows with `score()` matches the stored scores
- [x] `make smoke` green. Open PR `eval/harness` → main.

Phase 1 notes:
- Rows are validated against `schema/result.schema.json` inside `python -m eval` itself, because `schema.check` only looks in `<data>/results/`.
- `text_only` adds one line, *"(The image is not available. Give your best guess in the required format.)"*, so models guess instead of refusing. Otherwise the no-image baseline would measure refusals, not guessing.
- The results file is **rebuilt from the cache** on every run (all conditions and runs so far, current prompt only). A parser fix just needs a rerun, with no API calls.
- `cost_usd` is 0 until `pricing.py` lands in Phase 2.

### Phase 2: real models (~3 h)
- [ ] `gemini.py` (ER 2 + generalist; thinking-budget option), `anthropic.py`, `openai.py`, each with token counts from the response
- [ ] Retry/backoff, concurrency limit, `error` field, `--budget-usd`, `--dry-run` with `pricing.py`
- [ ] One live call per adapter on 3 fixture tasks (pennies). Eyeball raw responses and fix prompt/parse issues **before** freezing the prompt (bump `prompt_version`).

### Phase 3: real run (CP2 → CP3)
- [ ] Generate 300 scenes + tasks locally (D8), freeze `tasks.jsonl`, stratified `subset_100.txt`
- [ ] Dry run → post the cost estimate in handoff → go
- [ ] ER 2 on all tasks first (CP2 = "≥1 model, ≥100 tasks") → hand to D early
- [ ] Remaining models, `text_only`, `random`, 3× repeats on the subset, ER thinking ablation (D6)
- [ ] Sanity: image accuracy ≫ text-only ≈ random on spatial families. Parse-failure rate < 5% per model, otherwise fix the prompt and rerun that model.
- [ ] Post spend + headline numbers in the handoff log. Hand `data/results/` to D (zip on the team drive; `data/` isn't in git).

### Done when
`python -m eval --model <any> --tasks data/tasks.jsonl` resumes safely, rows are schema-valid, `make smoke` is green in a fresh clone, and `data/results/` holds all models × conditions with spend logged.

## 6. Risks
- **Rate limits on preview models** (ER 2 is in preview): low concurrency, backoff, run ER first, cache everything.
- **Prompt changes after paid runs** cost a full rerun. Freeze after the Phase 2 pilot.
- **A model refuses or answers in the wrong coordinate order:** counted as a parse failure or a wrong answer, *reported* not hidden. Swapped-axis errors are a finding for D, not a reason to change scoring.
- **Bbox convention:** B's reference boxes use inclusive integer max, the parser produces continuous pixels. That's ≤1 px off, irrelevant at IoU ≥ 0.5.
