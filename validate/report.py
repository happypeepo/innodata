"""validation_report.json (AGENTS.md B "Done when": rejection counts by reason, independent-check
agreement %, human-check agreement %). D uses these numbers in the write-up.

No timestamps, so the same seed gives a byte-identical report.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from .balance import baselines


def build_report(*, seed, data, n_scenes, n_generated, n_invalid, rule_rejects, balance_drops, gen_rejects,
                 final, independent=None, human=None) -> dict:
    base = baselines(final)
    checked = [v["near_chance"] for v in base.values() if v["near_chance"] is not None]
    return {
        "seed": seed,
        "data": str(data),
        "n_scenes": n_scenes,
        "counts": {
            "generated": n_generated,
            "failed_validity_rules": n_invalid,
            "dropped_by_balancing": sum(balance_drops.values()),
            "final": len(final),
        },
        "tasks_per_family": dict(sorted(Counter(t["family"] for t in final).items(), key=lambda kv: int(kv[0][1:]))),
        "rejections_by_reason": {
            "generation": dict(sorted(gen_rejects.items())),
            "validity_rules": dict(sorted(rule_rejects.items())),
            "balancing": dict(sorted(balance_drops.items())),
        },
        "majority_baseline": base,
        "all_near_chance": all(checked) if checked else None,
        "independent_check": independent or {"status": "not run yet (phase 7)", "agreement_pct": None},
        "human_check": human if human and human.get("checked") else {"status": "not run yet (phase 8)", "agreement_pct": None},
    }


def write_report(path, report) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2) + "\n")
