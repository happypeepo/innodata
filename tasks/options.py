"""Lettered options for choice / sequence / set questions (lead-approved, 24 Nov 2026).

The question lists "Options: A) red cylinder B) table C) blue box", the model answers with letters,
and reference["options"] maps each letter to an object id (or "table"). This is the one place that
builds options, so if the naming rule changes, only this file changes.
"""

from __future__ import annotations

from random import Random

from .refexpr import describe

LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
DEFAULT_OPTIONS = 4   # my take: 4 options keeps chance at 25% for single-answer questions


def option_label(oid: str, scene: dict) -> str | None:
    """Text shown for an option: 'table', or the object's unique description without 'the '."""
    if oid == "table":
        return "table"
    obj = next(o for o in scene["objects"] if o["id"] == oid)
    ref = describe(obj, scene)
    if ref is None:
        return None
    return ref.text[4:] if ref.text.startswith("the ") else ref.text


def build_options(rng: Random, scene: dict, correct, pool, n_total: int = DEFAULT_OPTIONS):
    """Shuffle `correct` ids plus distractors drawn from `pool` into lettered options.

    Returns (options, labels): options maps letter -> id (goes in reference), labels maps
    letter -> text (goes in the question). Returns None if a correct answer can't be described.
    Distractors that can't be described are skipped.
    """
    correct = list(dict.fromkeys(correct))
    labels = {}
    for oid in correct:
        label = option_label(oid, scene)
        if label is None:
            return None
        labels[oid] = label
    extras = [oid for oid in dict.fromkeys(pool) if oid not in labels]
    rng.shuffle(extras)
    for oid in extras:
        if len(labels) >= max(n_total, len(correct)):
            break
        label = option_label(oid, scene)
        if label is not None and label not in labels.values():
            labels[oid] = label
    ids = list(labels)
    rng.shuffle(ids)
    options = {LETTERS[i]: oid for i, oid in enumerate(ids)}
    return options, {k: labels[v] for k, v in options.items()}


def options_suffix(labels: dict) -> str:
    return " Options: " + " ".join(f"{k}) {v}" for k, v in labels.items())


def letters_for(options: dict, ids) -> list[str]:
    ids = set(ids)
    return sorted(k for k, v in options.items() if v in ids)
