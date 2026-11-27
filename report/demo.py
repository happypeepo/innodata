"""RoboSpatial-Synth demo: pick a scene (or render a new seed), pick a question, see each model's answer
drawn on the image with ✓/✗.

    pip install -r report/requirements-demo.txt
    streamlit run report/demo.py -- --data data/ --results data/results/
    streamlit run report/demo.py -- --data fixtures/ --results fixtures/results/   # no generated data needed
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # `streamlit run` doesn't add the repo root

from report import demo_core as core  # noqa: E402
from report import gallery  # noqa: E402
from report.analyze import read_jsonl  # noqa: E402
from validate.checkers import load_scene  # noqa: E402


def args():
    ap = argparse.ArgumentParser()
    default = Path("data") if Path("data/tasks.jsonl").exists() else Path("fixtures")
    ap.add_argument("--data", type=Path, default=default)
    ap.add_argument("--results", type=Path)
    ap.add_argument("--tasks", type=Path)
    ap.add_argument("--live-dir", type=Path, default=Path("data/demo"))
    a = ap.parse_args(sys.argv[1:])
    a.results = a.results or a.data / "results"
    a.tasks = a.tasks or a.data / "tasks.jsonl"
    return a


@st.cache_data
def cached_tasks(path: str) -> list[dict]:
    return read_jsonl(Path(path)) if Path(path).exists() else []


@st.cache_data
def cached_results(path: str) -> dict:
    return core.load_results(Path(path))


def task_label(t: dict) -> str:
    return f"{t['family']} · {t['question'][:90]}"


def show_answers(task: dict, rows: list[dict], data: Path) -> None:
    scene = load_scene(data, task["scene_id"])
    ref = gallery.describe(task, {"parsed": {}, "parse_ok": True}, scene)[1]
    st.markdown(f"**Reference** (from the simulator): {ref}")
    if not rows:
        st.info("No stored answers for this task yet.")
        return
    cols = st.columns(max(min(len(rows), 3), 2))
    for i, r in enumerate(rows):
        with cols[i % len(cols)]:
            st.image(gallery.draw(task, r, scene, data), width="stretch")
            ans = gallery.describe(task, r, scene)[0]
            st.markdown(f"### {core.verdict(r)} {r['model']}")
            st.caption(f"Answer: {ans} · score {r['score']:.2f}"
                       + ("" if r["score"] >= 1 else f" · {gallery.classify(task, r, scene)}"))
            with st.expander("Raw response"):
                st.code(core.dump(r), language="json")


def browse(a) -> None:
    tasks = cached_tasks(str(a.tasks))
    if not tasks:
        st.warning(f"No tasks at {a.tasks}. Run `python -m tasks --data {a.data}/`, or start with `--data fixtures/`.")
        return
    results = cached_results(str(a.results))
    left, right = st.columns([1, 2])
    with left:
        scene_id = st.selectbox("Scene", core.scene_ids(tasks))
        mine = core.tasks_for(tasks, scene_id)
        task = mine[st.selectbox("Question", range(len(mine)), format_func=lambda i: task_label(mine[i]))]
        condition = st.radio("Condition", ["image", "text_only"], horizontal=True,
                             help="text_only: the same question without the image")
        st.markdown(f"> {task['question']}")
        st.caption(f"`{task['task_id']}` · checker `{task['checker']}` · difficulty {task.get('difficulty')}")
    with right:
        show_answers(task, core.answers(results.get(task["task_id"], []), condition), a.data)


def live(a) -> None:
    missing = core.missing_packages()
    if missing:
        st.info(f"Live mode needs {', '.join(missing)} on this checkout (still on open PRs). Browse mode works now.")
        return
    left, right = st.columns([1, 2])
    with left:
        seed = st.number_input("Seed", min_value=0, value=1000, step=1)
        cf = st.checkbox("Compute counterfactuals (F8), ~5 s", value=True)
        if st.button("Render scene", type="primary"):
            with st.spinner("Generating and settling physics…"):
                st.session_state.live = core.render(int(seed), a.live_dir, cf)
        scene_id = st.session_state.get("live")
        if not scene_id:
            st.caption("Render a scene to start. It is written to " + str(a.live_dir))
            return
        tasks = core.live_tasks(a.live_dir, scene_id, int(seed))
        if not tasks:
            st.warning("No valid questions for this scene; try another seed.")
            return
        task = tasks[st.selectbox("Question", range(len(tasks)), format_func=lambda i: task_label(tasks[i]))]
        st.markdown(f"> {task['question']}")
        picks = st.multiselect("Models", core.LIVE_MODELS, default=["mock"],
                               format_func=lambda m: m if core.has_key(m) else f"{m} (no key)")
        st.caption("Real models cost money per call (a few cents at most). mock is free.")
        go = st.button("Ask", disabled=not picks)
    with right:
        scene = load_scene(a.live_dir, scene_id)
        key = ("asked", task["task_id"], tuple(picks))
        if go:
            rows = []
            for m in picks:
                if not core.has_key(m):
                    st.error(f"{m}: no {core.KEY_FOR[m]} in .env")
                    continue
                with st.spinner(f"Asking {m}…"):
                    try:
                        rows.append(core.ask(m, task, a.live_dir))
                    except Exception as e:   # show the provider's error instead of crashing the page
                        st.error(f"{m}: {type(e).__name__}: {e}")
            st.session_state[key] = rows
        if key in st.session_state:
            show_answers(task, st.session_state[key], a.live_dir)
        else:
            st.image(str(a.live_dir / scene["image_path"]), width="stretch")


def main() -> None:
    a = args()
    st.set_page_config(page_title="RoboSpatial-Synth demo", layout="wide")
    st.title("RoboSpatial-Synth")
    st.caption("Can a model tell a robot arm which thing, where, and whether it can grab it? "
               "Reference answers come from the simulator; aqua = reference, orange = the model's point or box.")
    tab_browse, tab_live = st.tabs(["Benchmark answers", "New scene (live)"])
    with tab_browse:
        browse(a)
    with tab_live:
        live(a)


main()
