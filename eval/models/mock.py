"""Seeded random *valid* answers in the model's own format. Needs no API key; doubles as the random
baseline. Blind to the reference: option letters come from the question text, like a real model."""

from __future__ import annotations

import json
import re
from random import Random

from .base import Adapter, Reply

OPTION_LETTER = re.compile(r"(?:^|\s)([A-Z])\) ")


class Mock(Adapter):
    name = "mock"
    model_id = "mock"

    def __init__(self, seed: int = 0):
        self.seed = seed

    def ask(self, system, parts, *, task, condition, run):
        rng = Random(f"{self.seed}:{task['task_id']}:{condition}:{run}")  # order/thread independent
        letters = OPTION_LETTER.findall(task["question"]) or ["A", "B"]
        atype = task["answer_type"]
        if atype == "point":
            ans = [rng.randint(0, 1000), rng.randint(0, 1000)]
        elif atype == "bbox":
            y0, y1 = sorted(rng.sample(range(1001), 2))
            x0, x1 = sorted(rng.sample(range(1001), 2))
            ans = [y0, x0, y1, x1]
        elif atype == "count":
            ans = rng.randint(0, 6)
        elif atype == "bool":
            ans = rng.random() < 0.5
        elif atype == "choice":
            ans = rng.choice(letters)
        elif atype == "sequence":
            ans = rng.sample(letters, len(letters))
        elif atype == "set":
            ans = sorted(rng.sample(letters, rng.randint(0, len(letters))))
        else:
            raise ValueError(f"unknown answer_type {atype!r}")
        return Reply(text=json.dumps({atype: ans}))
