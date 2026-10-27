"""Robots: the robots registered here, a check of each that moves nothing, adding and removing one,
and the robots announcing themselves on this network.

Recording an arm's rest pose and releasing an arm are not here: both need somebody at the arm,
pressing Enter at the terminal at the right moment, so the page shows the command to run there.
"""

from __future__ import annotations

import json
from typing import Any

import streamlit as st

from quackd.webui import catalog, jobs, widgets
from quackd.webui.screens.setup import _probe
from quackd.webui.state import setup


def screen() -> None:
    st.title("Robots")
    _registered()
    _add()
    _network()
    st.info(
        "Recording an arm's rest pose and releasing an arm need somebody at the arm: run "
        "`quackd robot rest-pose NAME` or `quackd robot release NAME` in a terminal beside it."
    )


def _registered() -> None:
    robots = catalog.registered()
    if not robots:
        st.caption("No robot is registered here yet.")
        return
    shown = [
        {k: r.get(k) for k in ("name", "spec", "address", "camera_url", "host", "llm", "note")}
        for r in robots
    ]
    st.dataframe(shown, hide_index=True, width="stretch")
    names = [r["name"] for r in robots]
    name = widgets.pick("_robots_pick", "A robot", names)
    check, use, remove = st.columns(3)
    if check.button(
        "Check it", help="Connects, asks how it is, and closes. Moves nothing.", width="stretch"
    ):
        _probe({"registered": name}, name)
    if use.button("Use it on Setup", width="stretch"):
        widgets.write("_robot_from", "registered")
        widgets.write("_registered", name)
        widgets.write("robot", name)
        st.success(f"Goal and Chat now run {name}.")
    with remove.popover("Remove it", width="stretch"):
        st.write(f"Forget {name}? Its memory stays.")
        if st.button(f"Yes, forget {name}", type="primary"):
            done = jobs.run_command(["robot", "remove", name, "--yes"])
            (st.success if done.returncode == 0 else st.error)(
                (done.stdout + done.stderr).strip() or "done"
            )


def _add() -> None:
    with st.expander("Register a robot"):
        rows = catalog.adapters()
        name = st.text_input("Name", placeholder="arm-01", key="add:name")
        adapter = st.selectbox("Adapter", [r["name"] for r in rows], key="add:adapter")
        row = next(r for r in rows if r["name"] == adapter)
        backend = st.selectbox("Backend", list(row.get("backends") or []), key="add:backend")
        address = st.text_input("Address", key="add:address")
        cameras = st.text_area("Camera URLs, one per line", key="add:cameras")
        note = st.text_input("A note for people", key="add:note")
        block = catalog.connect(adapter, backend)
        token_env = getattr(block, "token_env", None)
        if token_env:
            widgets.key_field(token_env, f"Token ({token_env})")
        if st.button("Register it", type="primary", disabled=not name.strip()):
            args = ["robot", "add", name.strip(), f"{adapter}:{backend}"]
            if address.strip():
                args += ["--address", address.strip()]
            for url in (u.strip() for u in cameras.splitlines()):
                if url:
                    args += ["--camera-url", url]
            if note.strip():
                args += ["--note", note.strip()]
            env: dict[str, Any] = {}
            if token_env and setup().keys.get(token_env):
                # the token travels in the environment, named on the line, never written on it
                args += ["--token-env", token_env]
                env = {token_env: setup().keys[token_env]}
            done = jobs.run_command(args, env=env)
            (st.success if done.returncode == 0 else st.error)(
                (done.stdout + done.stderr).strip() or "done"
            )


def _network() -> None:
    with st.expander("Robots on this network"):
        if not catalog.installed("zeroconf"):
            st.info(
                'Finding robots on the network needs the lan extra: uv pip install "quackd[lan]"'
            )
            return
        st.caption("Robots something on this network announces with `quackd announce`.")
        if st.button("Look"):
            done = jobs.run_command(["discover", "--json", "--timeout", "3"], timeout_s=30)
            found = []
            for line in done.stdout.splitlines():
                try:
                    found.append(json.loads(line))
                except ValueError:
                    continue
            if found:
                st.dataframe(found, hide_index=True, width="stretch")
            else:
                st.caption((done.stderr or "").strip() or "Nothing answered.")
