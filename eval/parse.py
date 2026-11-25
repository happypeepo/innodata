"""Raw model text -> `parsed` in storage format, or None (eval/PLAN.md §3, decision D7).

Lenient about wrapping (code fences, prose, ER's list-of-points form), strict about meaning: the key must
match answer_type and values are only coerced when unambiguous. Model coords are [y, x] / boxes
[ymin, xmin, ymax, xmax], normalised 0-1000; output is [x, y] / [x1, y1, x2, y2] in pixels. Not clamped:
off-image answers stay off-image and score() gives them 0.
"""

from __future__ import annotations

import json
import math
import re

_DEC = json.JSONDecoder()
_LETTER = re.compile(r"^\s*([A-Za-z])(?![A-Za-z])")


def extract_json(text: str):
    """Last top-level JSON object or array in the text that decodes, or None."""
    found, i = None, 0
    while i < len(text):
        if text[i] in "{[":
            try:
                found, end = _DEC.raw_decode(text, i)
                i = end  # skip its insides, so an inner [y, x] never wins over the outer object
                continue
            except ValueError:
                pass
        i += 1
    return found


def _num(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, str):
        try:
            v = float(v.strip())
        except ValueError:
            return None
    return float(v) if isinstance(v, (int, float)) and math.isfinite(v) else None


def _nums(v, n):
    if not isinstance(v, list) or len(v) != n:
        return None
    out = [_num(x) for x in v]
    return None if None in out else out


def _letter(v):
    if not isinstance(v, str):
        return None
    m = _LETTER.match(v)
    return m.group(1).upper() if m else None


def _coerce(atype, v, w, h):
    if atype == "point":
        p = _nums(v, 2)
        return None if p is None else [round(p[1] * w / 1000, 1), round(p[0] * h / 1000, 1)]
    if atype == "bbox":
        b = _nums(v, 4)
        if b is None:
            return None
        ymin, xmin, ymax, xmax = b
        return [round(xmin * w / 1000, 1), round(ymin * h / 1000, 1),
                round(xmax * w / 1000, 1), round(ymax * h / 1000, 1)]
    if atype == "count":
        n = _num(v)
        return int(n) if n is not None and n >= 0 and n == int(n) else None
    if atype == "bool":
        if isinstance(v, bool):
            return v
        s = v.strip().lower() if isinstance(v, str) else None
        return {"true": True, "yes": True, "false": False, "no": False}.get(s)
    if atype == "choice":
        return _letter(v)
    if atype in ("sequence", "set"):
        if not isinstance(v, list):
            return None
        out = [_letter(x) for x in v]
        return None if None in out else out
    raise ValueError(f"unknown answer_type {atype!r}")


def parse(text: str, answer_type: str, width: int, height: int):
    obj = extract_json(text or "")
    if isinstance(obj, list):  # ER style: [{"point": [y, x], "label": ...}, ...] -> first item
        obj = next((o for o in obj if isinstance(o, dict)), None)
    if not isinstance(obj, dict):
        return None
    key = next((k for k in obj if isinstance(k, str) and k.strip().lower() == answer_type), None)
    if key is None:
        return None
    val = _coerce(answer_type, obj[key], width, height)
    return None if val is None else {answer_type: val}
