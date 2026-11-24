"""Balancing (AGENTS.md: "cap tasks per family, balance yes/no and choice answers so a majority
baseline gets about chance") and the majority-answer baseline that proves it.

All sampling uses a seeded RNG, so the same seed always keeps the same tasks.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from random import Random

MAX_PER_FAMILY = 100      # my take: 10 families x 100 = ~1,000 tasks, the AGENTS.md target
NEAR_CHANCE_TOL = 0.10    # my take: a majority baseline within 10 points of chance counts as balanced
COUNT_MAX_SHARE = 0.50    # my take: no single F3 count may be the answer for more than half the tasks

# Families whose answers form a small closed set: each answer gets equal share.
EQUALISE = {"F4", "F5", "F9"}
# Families where the most common answer is capped at the runner-up's size.
CAP_TO_RUNNER_UP = {"F3", "F10"}


def answer_key(task):
    """What 'the same answer' means for balancing. None = not balanced (open answers)."""
    ref, fam = task["reference"], task["family"]
    if task["answer_type"] == "bool":
        return str(ref["bool"]).lower()
    if task["answer_type"] == "count":
        return str(ref["count"])
    if task["answer_type"] == "choice":
        meaning = ref["options"][ref["choice"]]
        if fam == "F4":
            return meaning                      # "in front of" / "behind"
        if fam == "F5":
            return "table" if meaning == "table" else "object"
        return ref["choice"]                    # F10: the letter
    return None


def letter_key(task):
    ref = task["reference"]
    return ref.get("choice")


def _groups(tasks, key):
    g = defaultdict(list)
    for t in tasks:
        g[key(t)].append(t)
    return g


def balance(tasks, seed=0, max_per_family=MAX_PER_FAMILY):
    """Return (kept_tasks, drops) where drops counts removed tasks by reason. Order of kept tasks is stable."""
    rng = Random(f"balance:{seed}")
    by_family = _groups(tasks, lambda t: t["family"])
    keep, drops = set(), Counter()
    for fam in sorted(by_family):
        fam_tasks = by_family[fam]
        groups = _groups(fam_tasks, answer_key)
        for g in groups.values():
            rng.shuffle(g)
        if fam in EQUALISE and len(groups) >= 2:
            size = min(len(g) for g in groups.values())
            for k, g in groups.items():
                drops[f"balance {fam}: evened out answers"] += len(g) - size
                groups[k] = g[:size]
        elif fam in CAP_TO_RUNNER_UP and len(groups) >= 2:
            sizes = sorted((len(g) for g in groups.values()), reverse=True)
            for k, g in groups.items():
                if len(g) > sizes[1]:
                    drops[f"balance {fam}: capped most common answer"] += len(g) - sizes[1]
                    groups[k] = g[:sizes[1]]
        # family cap, round-robin across answer groups so the cap keeps the balance
        order = [groups[k] for k in sorted(groups, key=str)]
        picked, i = [], 0
        while len(picked) < max_per_family and any(i < len(g) for g in order):
            for g in order:
                if i < len(g) and len(picked) < max_per_family:
                    picked.append(g[i])
            i += 1
        total = sum(len(g) for g in order)
        if total > len(picked):
            drops[f"cap {fam}: over {max_per_family} tasks per family"] += total - len(picked)
        keep.update(id(t) for t in picked)
    return spread_letters([t for t in tasks if id(t) in keep], seed), drops


OPTION_RE = re.compile(r"([A-Z])\) (.*?)(?= [A-Z]\) |$)")


def spread_letters(tasks, seed=0):
    """Rotate where the correct option sits so each letter is right about equally often (choice tasks).

    Only the order the options are shown in changes: the correct option swaps places with the option at
    the target letter, in both reference.options and the question's "Options:" list. The answer itself
    never changes.
    """
    rng = Random(f"letters:{seed}")
    out = []
    by_family = _groups([t for t in tasks if t["answer_type"] == "choice"], lambda t: t["family"])
    slot = {}
    for fam, fam_tasks in by_family.items():
        order = list(range(len(fam_tasks)))
        rng.shuffle(order)
        for rank, i in enumerate(order):
            slot[id(fam_tasks[i])] = rank
    for t in tasks:
        if id(t) not in slot:
            out.append(t)
            continue
        ref = t["reference"]
        letters = list(ref["options"])
        want = letters[slot[id(t)] % len(letters)]
        have = ref["choice"]
        if want == have:
            out.append(t)
            continue
        stem, _, opts_text = t["question"].rpartition(" Options: ")
        labels = dict(OPTION_RE.findall(opts_text))
        options = dict(ref["options"])
        options[want], options[have] = options[have], options[want]
        labels[want], labels[have] = labels[have], labels[want]
        q = stem + " Options: " + " ".join(f"{k}) {labels[k]}" for k in letters)
        out.append({**t, "question": q, "reference": {**ref, "choice": want, "options": options}})
    return out


def baselines(tasks):
    """Majority-answer baseline per family: always answering the most common reference answer."""
    out = {}
    for fam, fam_tasks in sorted(_groups(tasks, lambda t: t["family"]).items(), key=lambda kv: int(kv[0][1:])):
        n = len(fam_tasks)
        atype = fam_tasks[0]["answer_type"]
        entry = {"n": n, "answer_type": atype}
        if atype in ("point", "bbox", "sequence", "set"):
            entry.update(majority_baseline=None, chance=None, near_chance=None,
                         note="open answer; no single majority answer exists")
            out[fam] = entry
            continue
        counts = Counter(answer_key(t) for t in fam_tasks)
        top, top_n = counts.most_common(1)[0]
        entry["answer_counts"] = dict(sorted(counts.items()))
        entry["majority_answer"] = top
        entry["majority_baseline"] = round(top_n / n, 3)
        if atype == "choice":
            letters = Counter(letter_key(t) for t in fam_tasks)
            entry["letter_counts"] = dict(sorted(letters.items()))
            entry["majority_letter_baseline"] = round(letters.most_common(1)[0][1] / n, 3)
            chance = sum(1 / len(t["reference"]["options"]) for t in fam_tasks) / n
            worst = max(entry["majority_baseline"], entry["majority_letter_baseline"])
        elif atype == "bool":
            chance, worst = 0.5, entry["majority_baseline"]
        else:  # count: no fixed chance level; check the cap instead
            chance, worst = None, entry["majority_baseline"]
        entry["chance"] = round(chance, 3) if chance is not None else None
        if chance is not None:
            entry["near_chance"] = worst <= chance + NEAR_CHANCE_TOL
        else:
            entry["near_chance"] = worst <= COUNT_MAX_SHARE
            entry["note"] = f"count answers: most common count must be at most {int(COUNT_MAX_SHARE * 100)}% of tasks"
        if len(counts) < 2:
            entry["note"] = "only one answer value present; needs more scenes to balance"
        out[fam] = entry
    return out
