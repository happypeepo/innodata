# validate/evidence/

Committed evidence for the judges (`data/` is gitignored). Lead-approved location, 24 Nov 2026.

- `human_check_seed<N>.jsonl`: one line per human spot-check verdict, written by
  `python -m validate.spotcheck --data data/ --tasks data/tasks.jsonl --seed <N> --reviewer <handle>`.
  Fields: `task_id`, `family`, `question`, `reference`, `verdict` (`agree` / `disagree`), `note`, `reviewer`, `seed`.

`python -m tasks --data data/ --seed <N>` reads the matching file, sets `validated.human` on those tasks,
and puts the agreement % and reference error rate into `data/validation_report.json`. A verdict whose
question no longer matches the regenerated task is counted as stale and left out of the %.
