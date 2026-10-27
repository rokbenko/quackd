"""Chat: give a goal, the robot does it, give the next, with the robot kept connected between them.

It is `quackd drive`, started as a child of the job table: each line goes to the session the
moment it is sent, as a keyboard's does, so a command such as `forward` runs at once, a goal goes
to the pilot, `stop` brakes, and a newer line ends the one moving. Questions are answered with the
buttons, never by typing: a line is always a command.
"""

from __future__ import annotations

from typing import Any

import streamlit as st

from quackd.webui import catalog, jobs, widgets
from quackd.webui.options import argv_for
from quackd.webui.state import setup


def _current() -> Any:
    table = jobs.table()
    job = table.get(st.session_state.get("chat_job", ""))
    if job is None:
        chats = [j for j in table.all() if j.kind == "chat"]
        job = chats[-1] if chats else None
    return job


def screen() -> None:
    st.title("Chat")
    job = _current()
    if job is None or not job.running:
        if job is not None:
            _conversation(job)
            widgets.outcome(job.snapshot())
        vla = setup().get("controller") == "vla"
        if vla:
            # drive takes no VLA pilot yet, and a chat started now would be flown by whatever
            # LLM the robot or the environment names, without a word
            st.info(
                "A chat is flown by an LLM: quackd drive takes no VLA pilot yet. Choose An LLM "
                "on Setup to chat, or give the arm its instruction on Goal."
            )
        if st.button("Start a chat", type="primary", width="stretch", disabled=vla):
            _start()
        st.caption("The chat runs with the robot and pilot chosen on Setup.")
        return
    _watch(job)
    said = st.chat_input(
        "A goal, or a command such as forward or stop. A newer line ends the one moving."
    )
    if said and said.strip():
        job.line(said.strip())
        st.rerun()
    if st.button(
        "End the session", help="The robot is stopped, folded where it folds, and let go of."
    ):
        job.quit()
        st.rerun()


def _start() -> None:
    shared = setup()
    values = shared.options()
    try:
        job = jobs.table().start(
            "chat",
            argv_for("drive", values),
            robot=str(values.get("robot") or "") or None,
            holds=catalog.robot_identity(values.get("robot"), values.get("address")),
            env=catalog.session_environment(shared.keys),
        )
    except RuntimeError as e:
        st.error(str(e))
        return
    st.session_state["chat_job"] = job.id
    st.rerun()


def _records(job: Any) -> dict[int, dict[str, Any]]:
    """What drive's own records say of each line: its lane once routed, its end once over."""
    lines: dict[int, dict[str, Any]] = {}
    for line in job.since(0):
        if line.get("ev") != "event" or "goal" in line:
            continue
        kind, data = line.get("kind"), line.get("data") or {}
        if kind in ("routed", "ended") and isinstance(data.get("n"), int):
            lines.setdefault(data["n"], {})[kind] = data
    return lines


def _conversation(job: Any) -> None:
    records = _records(job)
    for n, text in enumerate(list(job.sent), start=1):
        with st.chat_message("user"):
            st.write(text)
        said = records.get(n, {})
        routed, ended = said.get("routed"), said.get("ended")
        with st.chat_message("assistant"):
            if routed is None:
                st.caption("working it out")
                continue
            label = routed.get("label") or routed.get("note") or ""
            st.caption(f"{routed.get('lane')} · {label}".rstrip(" ·"))
            goal = _goal_number(routed)
            if goal is not None:
                with st.expander(f"goal {goal}, step by step", expanded=ended is None):
                    widgets.narration(job, goal=goal, tail=120)
            if ended is not None:
                outcome = f"{ended.get('outcome')}: {ended.get('summary') or ''}".rstrip(": ")
                (st.success if ended.get("outcome") in ("ok", "success") else st.info)(outcome)


def _goal_number(routed: dict[str, Any]) -> int | None:
    label = str(routed.get("label") or "")
    if routed.get("lane") == "pilot" and label.startswith("goal "):
        try:
            return int(label.split()[1])
        except (IndexError, ValueError):
            return None
    return None


def _watch(job: Any) -> None:
    @st.fragment(run_every=0.5)
    def live() -> None:
        view = job.snapshot()
        widgets.status(view)
        widgets.questions(job, view)
        widgets.frame(view)
        with st.container(height=520, autoscroll=True):
            _conversation(job)
        if not view["running"]:
            st.rerun()

    live()
