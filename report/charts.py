"""Self-contained HTML report: inline SVG charts (no plotting dependency) + the summary tables.

Colours: the reference categorical palette, light and dark steps, one fixed slot per model.
"""
from __future__ import annotations

import html
import re

LIGHT = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
DARK = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767"]

CSS = """
:root { color-scheme: light;
  --page: #f9f9f7; --surface: #fcfcfb; --ink: #0b0b0b; --ink-2: #52514e; --muted: #898781;
  --grid: #e1e0d9; --axis: #c3c2b7; --ring: rgba(11,11,11,0.10); %(light)s }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) { color-scheme: dark;
  --page: #0d0d0d; --surface: #1a1a19; --ink: #f0efec; --ink-2: #c3c2b7; --muted: #898781;
  --grid: #2c2c2a; --axis: #383835; --ring: rgba(255,255,255,0.10); %(dark)s } }
:root[data-theme="dark"] { color-scheme: dark;
  --page: #0d0d0d; --surface: #1a1a19; --ink: #f0efec; --ink-2: #c3c2b7; --muted: #898781;
  --grid: #2c2c2a; --axis: #383835; --ring: rgba(255,255,255,0.10); %(dark)s }
* { box-sizing: border-box; }
body { margin: 0; background: var(--page); color: var(--ink);
  font: 14px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; }
main { max-width: 1040px; margin: 0 auto; padding: 24px 16px 64px; }
h1 { font-size: 22px; margin: 0 0 4px; } h2 { font-size: 16px; margin: 32px 0 8px; }
p { color: var(--ink-2); margin: 4px 0 12px; }
.card { background: var(--surface); border: 1px solid var(--ring); border-radius: 8px; padding: 12px 16px; margin: 12px 0; }
.card h3 { font-size: 14px; margin: 0 0 4px; } .card .sub { color: var(--muted); font-size: 12px; margin: 0 0 8px; }
.multiples { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 12px; }
.multiples .card { margin: 0; }
svg { width: 100%%; height: auto; display: block; overflow: visible; }
svg text { fill: var(--muted); font-size: 11px; font-variant-numeric: tabular-nums; }
.grid { stroke: var(--grid); stroke-width: 1; } .axis { stroke: var(--axis); stroke-width: 1; }
.ci { stroke: var(--ink-2); stroke-width: 1.5; }
.legend { display: flex; flex-wrap: wrap; gap: 4px 16px; margin: 4px 0 8px; color: var(--ink-2); font-size: 12px; }
.legend span::before { content: ""; display: inline-block; width: 10px; height: 10px; border-radius: 3px;
  margin-right: 6px; vertical-align: -1px; background: var(--c); }
[data-tip] { cursor: default; } [data-tip]:hover { opacity: 0.85; }
#tip { position: fixed; pointer-events: none; background: var(--surface); color: var(--ink); border: 1px solid var(--ring);
  border-radius: 6px; padding: 6px 8px; font-size: 12px; box-shadow: 0 2px 8px rgba(0,0,0,0.12); display: none; white-space: pre; }
.tables { overflow-x: auto; }
table { border-collapse: collapse; font-size: 12px; margin: 8px 0 16px; font-variant-numeric: tabular-nums; }
th, td { border-bottom: 1px solid var(--grid); padding: 4px 10px; text-align: left; white-space: nowrap; }
th { color: var(--ink-2); font-weight: 600; }
code { font-size: 12px; }
"""

TIP_JS = """
const tip = document.getElementById('tip');
document.addEventListener('mousemove', e => {
  const t = e.target.closest('[data-tip]');
  if (!t) { tip.style.display = 'none'; return; }
  tip.textContent = t.dataset.tip; tip.style.display = 'block';
  const x = Math.min(e.clientX + 12, innerWidth - tip.offsetWidth - 8);
  tip.style.left = x + 'px'; tip.style.top = (e.clientY + 12) + 'px';
});
"""


def esc(s) -> str:
    return html.escape(str(s), quote=True)


def model_slots(models: list[str], order: tuple[str, ...]) -> dict[str, int]:
    """Known models keep their slot whatever else is in the run; unknown ones take the next free slots."""
    slots, free = {}, [i for i in range(len(LIGHT)) if i >= len(order)]
    for m in models:
        if m in order and order.index(m) < len(LIGHT):
            slots[m] = order.index(m)
        else:
            slots[m] = free.pop(0) if free else len(LIGHT) - 1
    return slots


def legend(models, slots) -> str:
    if len(models) < 2:
        return ""
    return '<div class="legend">' + "".join(
        f'<span style="--c: var(--s{slots[m] + 1})">{esc(m)}</span>' for m in models) + "</div>"


def _bar_path(x, w, y0, y1, r=4.0) -> str:
    """Bar from baseline y0 to y1 with the data end (away from the baseline) rounded."""
    h = abs(y1 - y0)
    r = min(r, w / 2, h)
    if y1 <= y0:   # grows up
        return (f"M{x:.1f},{y0:.1f}V{y1 + r:.1f}Q{x:.1f},{y1:.1f} {x + r:.1f},{y1:.1f}"
                f"H{x + w - r:.1f}Q{x + w:.1f},{y1:.1f} {x + w:.1f},{y1 + r:.1f}V{y0:.1f}Z")
    return (f"M{x:.1f},{y0:.1f}V{y1 - r:.1f}Q{x:.1f},{y1:.1f} {x + r:.1f},{y1:.1f}"
            f"H{x + w - r:.1f}Q{x + w:.1f},{y1:.1f} {x + w:.1f},{y1 - r:.1f}V{y0:.1f}Z")


def bar_chart(cats, series, slots, lo=0.0, hi=1.0, ticks=(0, 0.25, 0.5, 0.75, 1.0), signed=False) -> str:
    """Grouped bars. series: [(model, {cat: (value, ci_low, ci_high, n)})]."""
    W, H, L, R, T, B = 720, 240, 40, 8, 10, 26
    pw, ph = W - L - R, H - T - B
    y = lambda v: T + ph * (hi - v) / (hi - lo)
    out = [f'<svg viewBox="0 0 {W} {H}" role="img">']
    zero = 0 if signed else lo
    for t in ticks:
        label = f"{t * 100:+.0f}" if signed and t else f"{t * 100:.0f}"
        out.append(f'<line class="{"axis" if t == zero else "grid"}" x1="{L}" x2="{W - R}" y1="{y(t):.1f}" y2="{y(t):.1f}"/>'
                   f'<text x="{L - 6}" y="{y(t) + 4:.1f}" text-anchor="end">{label}</text>')
    gw = pw / max(len(cats), 1)
    k = max(len(series), 1)
    inner = gw * 0.72
    bw = max((inner - 2 * (k - 1)) / k, 2)
    base = y(0 if signed else lo)
    for i, c in enumerate(cats):
        gx = L + i * gw + (gw - inner) / 2
        out.append(f'<text x="{L + i * gw + gw / 2:.1f}" y="{H - 8}" text-anchor="middle">{esc(c)}</text>')
        for j, (m, vals) in enumerate(series):
            if c not in vals:
                continue
            v, clo, chi, n = vals[c]
            x = gx + j * (bw + 2)
            tip = f"{m} · {c}\n{v * 100:+.1f} pts" if signed else f"{m} · {c}\n{v * 100:.1f}%"
            tip += f"  [{clo * 100:.1f}, {chi * 100:.1f}]  n={n}"
            out.append(f'<path d="{_bar_path(x, bw, base, y(v))}" fill="var(--s{slots[m] + 1})" data-tip="{esc(tip)}"/>')
            if chi > clo:
                cx = x + bw / 2
                cap = min(bw / 2, 4)
                out.append(f'<path class="ci" fill="none" d="M{cx:.1f},{y(clo):.1f}V{y(chi):.1f}'
                           f'M{cx - cap:.1f},{y(clo):.1f}H{cx + cap:.1f}M{cx - cap:.1f},{y(chi):.1f}H{cx + cap:.1f}"/>')
    out.append("</svg>")
    return "".join(out)


def line_chart(bins, series, slots) -> str:
    """Accuracy per difficulty bin. series: [(model, {bin: (value, n)})]."""
    W, H, L, R, T, B = 340, 190, 34, 12, 10, 26
    pw, ph = W - L - R, H - T - B
    y = lambda v: T + ph * (1 - v)
    x = lambda i: L + (pw * (i + 0.5) / len(bins))
    out = [f'<svg viewBox="0 0 {W} {H}" role="img">']
    for t in (0, 0.5, 1.0):
        out.append(f'<line class="{"axis" if t == 0 else "grid"}" x1="{L}" x2="{W - R}" y1="{y(t):.1f}" y2="{y(t):.1f}"/>'
                   f'<text x="{L - 6}" y="{y(t) + 4:.1f}" text-anchor="end">{t * 100:.0f}</text>')
    for i, b in enumerate(bins):
        out.append(f'<text x="{x(i):.1f}" y="{H - 8}" text-anchor="middle">{esc(b)}</text>')
    for m, vals in series:
        pts = [(x(i), y(vals[b][0]), b) for i, b in enumerate(bins) if b in vals]
        if len(pts) > 1:
            out.append(f'<polyline fill="none" stroke="var(--s{slots[m] + 1})" stroke-width="2" stroke-linejoin="round" '
                       f'points="{" ".join(f"{px:.1f},{py:.1f}" for px, py, _ in pts)}"/>')
        for px, py, b in pts:
            v, n = vals[b]
            out.append(f'<circle cx="{px:.1f}" cy="{py:.1f}" r="4" fill="var(--s{slots[m] + 1})" stroke="var(--surface)" '
                       f'stroke-width="2" data-tip="{esc(f"{m} · {b}" + chr(10) + f"{v * 100:.1f}%  n={n}")}"/>')
    out.append("</svg>")
    return "".join(out)


def md_to_html(md: str) -> str:
    """Just enough Markdown for summary.md: headings, paragraphs, tables, inline code."""
    inline = lambda s: re.sub(r"`([^`]+)`", r"<code>\1</code>", esc(s))
    out, table = [], []

    def flush():
        if table:
            head, *body = [[c.strip() for c in r.strip().strip("|").split("|")] for r in table if not set(r) <= set("|-")]
            out.append("<table><thead><tr>" + "".join(f"<th>{inline(c)}</th>" for c in head) + "</tr></thead><tbody>"
                       + "".join("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in r) + "</tr>" for r in body)
                       + "</tbody></table>")
            table.clear()

    for line in md.splitlines():
        if line.startswith("|"):
            table.append(line)
            continue
        flush()
        if line.startswith("# "):
            continue   # the page has its own title
        if line.startswith("## "):
            out.append(f"<h2>{inline(line[3:])}</h2>")
        elif line.strip():
            out.append(f"<p>{inline(line)}</p>")
    flush()
    return "\n".join(out)


def page(s: dict, md: str) -> str:
    from report.analyze import MODEL_ORDER
    models = s["models"]
    slots = model_slots(models, MODEL_ORDER)
    css = CSS % {"light": " ".join(f"--s{i + 1}: {c};" for i, c in enumerate(LIGHT)),
                 "dark": " ".join(f"--s{i + 1}: {c};" for i, c in enumerate(DARK))}
    key = legend(models, slots)
    parts = ["<!doctype html><html lang='en'><head><meta charset='utf-8'>",
             "<meta name='viewport' content='width=device-width, initial-scale=1'>",
             f"<title>RoboSpatial-Synth report</title><style>{css}</style></head><body><main>",
             "<h1>RoboSpatial-Synth report</h1>",
             f"<p>{len(models)} model(s) · headline accuracy counts score = 1.0 · whiskers are 95% bootstrap CIs over tasks.</p>"]

    fam = [r for r in s["by_family"] if r["condition"] == "image"]
    if fam:
        series = [(m, {r["family"]: (r["accuracy"], r["ci_low"], r["ci_high"], r["n_tasks"]) for r in fam if r["model"] == m})
                  for m in models]
        parts += ["<div class='card'><h3>Accuracy by task family (image)</h3>",
                  "<p class='sub'>Percent of tasks answered exactly right</p>", key,
                  bar_chart(s["families"], series, slots), "</div>"]

    if s["gap"]:
        fams = [f for f in s["families"] if any(g["family"] == f for g in s["gap"])]
        series = [(m, {g["family"]: (g["gap"], g["ci_low"], g["ci_high"], g["n_tasks"]) for g in s["gap"] if g["model"] == m})
                  for m in models]
        parts += ["<div class='card'><h3>Image vs text-only gap</h3>",
                  "<p class='sub'>Accuracy with the image minus without it, in points. Near zero means the question can be guessed from text.</p>",
                  key, bar_chart(fams, series, slots, -1.0, 1.0, (-1.0, -0.5, 0, 0.5, 1.0), signed=True), "</div>"]

    knob_cards = []
    for knob in s["knobs"]:
        rows = [r for r in s["by_knob"] if r["knob"] == knob and r["condition"] == "image"]
        bins = [b for _, b in sorted({(r["bin_order"], r["bin"]) for r in rows})]
        if not rows:
            continue
        series = [(m, {r["bin"]: (r["accuracy"], r["n_tasks"]) for r in rows if r["model"] == m}) for m in models]
        knob_cards.append(f"<div class='card'><h3>By {esc(knob)}</h3><p class='sub'>Accuracy (image), percent</p>"
                          + line_chart(bins, series, slots) + "</div>")
    if knob_cards:
        parts += ["<h2>Difficulty</h2>", key, "<div class='multiples'>", *knob_cards, "</div>"]

    parts += ["<h2>Tables</h2><div class='tables'>", md_to_html(md), "</div>",
              f"</main><div id='tip'></div><script>{TIP_JS}</script></body></html>"]
    return "\n".join(parts)
