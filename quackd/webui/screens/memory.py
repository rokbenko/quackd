"""Memory: what a robot remembers between runs (`quackd memory`): read, added to, cleared."""

from __future__ import annotations

import json

import streamlit as st

from quackd.webui import catalog, jobs, widgets
from quackd.webui.state import setup


def screen() -> None:
    st.title("Memory")
    st.caption(
        "What a robot keeps from one run to the next: the notes its pilot reads at the start."
    )
    choices = sorted(
        {str(setup().get("robot") or "microduck:sim2d"), *(r["name"] for r in catalog.registered())}
    )
    robot = widgets.pick("_memory_robot", "Robot", choices, default=setup().get("robot"))
    shown = jobs.run_command(["memory", "show", "--robot", robot, "--raw"])
    notes = []
    for line in shown.stdout.splitlines():
        try:
            notes.append(json.loads(line))
        except ValueError:
            continue
    if notes:
        st.dataframe(notes, hide_index=True, width="stretch")
    elif shown.returncode != 0:
        st.error((shown.stdout + shown.stderr).strip())
    else:
        st.caption(f"{robot} remembers nothing yet.")
    with st.form("memory-add", clear_on_submit=True):
        text = st.text_input("A note to keep", placeholder="the charger is by the door")
        if st.form_submit_button("Keep it") and text.strip():
            done = jobs.run_command(["memory", "add", text.strip(), "--robot", robot])
            (st.success if done.returncode == 0 else st.error)(
                (done.stdout + done.stderr).strip() or "kept"
            )
    with st.popover("Clear it"):
        st.write(f"Forget everything {robot} remembers?")
        if st.button(f"Yes, clear {robot}", type="primary"):
            done = jobs.run_command(["memory", "clear", "--robot", robot, "--yes"])
            (st.success if done.returncode == 0 else st.error)(
                (done.stdout + done.stderr).strip() or "cleared"
            )
