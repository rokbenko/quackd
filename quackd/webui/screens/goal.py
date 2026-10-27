"""Goal: one goal in your own words, or a task file, run once with the setup's robot and pilot.

It is `quackd run`, started as a child of the job table and watched as it goes: what the run is
waiting on, the camera, the steps as a terminal narrates them, the questions it puts, and how it
ended. A task file written for a flock is not offered: the page drives one robot at a time.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import streamlit as st

from quackd.webui import catalog, jobs, launch, widgets
from quackd.webui.options import argv_for
from quackd.webui.state import setup


@st.cache_data(show_spinner=False)
def bundled() -> list[str]:
    """The task files that ship with quackd and drive one robot, by name."""
    from quackd.duckfile.parser import list_bundled_ducks, load_duck

    names = []
    for path in list_bundled_ducks():
        try:
            if load_duck(str(path)).frontmatter.flock is None:
                names.append(path.stem)
        except Exception:  # a file this quackd cannot read is not offered
            continue
    return sorted(names)


def _folder() -> Path:
    return launch.CONFIG.folder if launch.CONFIG is not None else Path.cwd()


def _current() -> Any:
    """The goal this tab started, else the newest one the page has, so a refreshed page or a
    phone finds the run that is going."""
    table = jobs.table()
    job = table.get(st.session_state.get("goal_job", ""))
    if job is None:
        goals = [j for j in table.all() if j.kind == "goal"]
        job = goals[-1] if goals else None
    return job


def screen() -> None:
    st.title("Goal")
    job = _current()
    if job is None or not job.running:
        _form()
    if job is not None:
        _watch(job)


def _form() -> None:
    shared = setup()
    kind = widgets.pick(
        "_task",
        "Task",
        ["goal", "file"],
        default="goal",
        shown=lambda v: {"goal": "A goal in your own words", "file": "A task file"}[v],
        segmented=True,
    )
    task: dict[str, Any] = {}
    if kind == "goal":
        key = "opt:_goal_text"
        if key not in st.session_state:
            st.session_state[key] = shared.get("_goal_text") or ""
        goal = st.text_area("Goal", key=key, placeholder="find the ball and kick it")
        widgets.write("_goal_text", goal)
        if goal.strip():
            task = {"goal": goal.strip()}
    else:
        source = widgets.pick(
            "_file_from",
            "From",
            ["bundled", "upload"],
            shown=lambda v: {"bundled": "One that ships with quackd", "upload": "My own"}[v],
            segmented=True,
        )
        if source == "bundled":
            names = bundled()
            if names:
                task = {
                    "duckfile": widgets.pick("_duck", "Task file", names, default="hello-world")
                }
        else:
            uploaded = st.file_uploader("A .duck file", type=["duck"])
            if uploaded is not None:
                folder = _folder() / ".quackd-ui" / "uploads"
                folder.mkdir(parents=True, exist_ok=True)
                path = folder / Path(uploaded.name).name
                path.write_bytes(uploaded.getvalue())
                checked = jobs.run_command(["validate", str(path)], cwd=_folder())
                if checked.returncode == 0:
                    st.success(f"{path.name} is a task quackd can run")
                    task = {"duckfile": str(path)}
                else:
                    st.error(
                        (checked.stdout + checked.stderr).strip() or "quackd could not read it"
                    )
    if st.button("Run", type="primary", disabled=not task, width="stretch"):
        _start(task)


def _start(task: dict[str, Any]) -> None:
    shared = setup()
    values = {**shared.options(), **task}
    argv = argv_for("run", values)
    try:
        job = jobs.table().start(
            "goal",
            argv,
            robot=str(values.get("robot") or "") or None,
            holds=catalog.robot_identity(values.get("robot"), values.get("address")),
            env=catalog.session_environment(shared.keys),
        )
    except RuntimeError as e:
        st.error(str(e))
        return
    st.session_state["goal_job"] = job.id
    st.rerun()


def _watch(job: Any) -> None:
    @st.fragment(run_every=0.5 if job.running else None)
    def live() -> None:
        view = job.snapshot()
        widgets.status(view)
        widgets.questions(job, view)
        left, right = st.columns([2, 3])
        with left:
            widgets.frame(view)
        with right, st.container(height=480, autoscroll=True):
            widgets.narration(job)
        widgets.outcome(view)
        run_dir = widgets.run_folder(view) if view.get("end") else None
        if run_dir is not None:
            st.caption(f"Run folder: {run_dir}")
            gif = run_dir / "run.gif"
            if gif.is_file():
                st.image(str(gif), caption="run.gif")
        widgets.terminal(view)
        # once the run ends, the whole screen is drawn again, so the form comes back above it
        if view["running"]:
            st.session_state["_goal_live"] = job.id
        elif st.session_state.pop("_goal_live", None) == job.id:
            st.rerun()

    live()
