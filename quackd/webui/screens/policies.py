"""Policies: the LeRobot arm's policy server, started and checked from here, and Hugging Face
endpoints, which serve the same policy on a rented GPU.

A server started here is a job like a run, but it holds no robot: stopping it ends it outright.
Starting an endpoint is paid, by the hour, so the page asks before it does, and starts it as a job,
watched from here: it can take half an hour, and neither the screen nor the Stop over it waits on
it.
"""

from __future__ import annotations

import json

import streamlit as st

from quackd.webui import catalog, jobs, launch, widgets
from quackd.webui.options import DECISION_OPTIONS, MODEL_OPTIONS

DEFAULT_PORT = 9875


def screen() -> None:
    st.title("Policies")
    st.caption("A VLA policy flies the LeRobot arm. It runs in a policy server, here or on a GPU.")
    if not catalog.installed("quackd_lerobot"):
        st.info(
            'The policy server comes with the arm\'s adapter: uv pip install "quackd[lerobot-vla]"'
        )
    _local()
    _endpoints()
    with st.expander("The policy types the server serves"):
        st.dataframe(
            [
                {
                    "type": f.type,
                    "policy": f.label,
                    "by": f.maker,
                    "told the task in words": f.takes_words,
                    "GPU": f.gpu,
                    "with quackd": f.ran,
                }
                for f in catalog.policy_families()
            ],
            hide_index=True,
            width="stretch",
        )


def _local() -> None:
    st.subheader("A server on this computer")
    table = jobs.table()
    serving = [j for j in table.running() if j.kind == "policy"]
    for job in serving:
        port = job.argv[job.argv.index("--port") + 1] if "--port" in job.argv else DEFAULT_PORT
        url = f"http://127.0.0.1:{port}"
        with st.container(border=True):
            st.markdown(f"Serving at `{url}`")
            use, check, stop = st.columns(3)
            if use.button("Fly the arm with it", key=f"use-{job.id}", width="stretch"):
                widgets.write("_controller", "vla")  # the picker on Setup
                widgets.write("controller", "vla")
                widgets.write("policy_url", url)
                for name in (*MODEL_OPTIONS, *DECISION_OPTIONS):  # as Setup's own VLA choice does
                    widgets.forget(name)
                st.success("Setup now flies the arm with this policy.")
            if check.button("Check it", key=f"check-{job.id}", width="stretch"):
                done = jobs.run_command(["policy", "check", "--policy-url", url], timeout_s=60)
                st.code((done.stdout + done.stderr).strip(), language=None)
            if stop.button("Stop it", key=f"stop-{job.id}", width="stretch"):
                job.shutdown(stop_grace_s=0, end_grace_s=5)
                st.rerun()
            st.code("\n".join(list(job.stderr)[-20:]), language=None)
    with st.form("policy-serve"):
        policy = st.text_input(
            "Policy",
            value="scripted:sweep",
            help="REPO@REVISION of a LeRobot checkpoint, or scripted:hold and scripted:sweep, "
            "which need no GPU.",
        )
        port = st.number_input("Port", value=DEFAULT_PORT, step=1)
        if st.form_submit_button("Start it") and policy.strip():
            table.start(
                "policy",
                ["policy", "serve", "--policy", policy.strip(), "--port", str(int(port))],
                front=False,
            )
            st.rerun()


@st.cache_data(ttl=30, show_spinner=False)
def _listed(folder: str) -> tuple[str, str]:
    """`policy endpoint list --json`, asked at most every half minute, since it may ask Hugging
    Face, and never again on every redraw of the screen. `folder` is only what it is kept by."""
    done = jobs.run_command(["policy", "endpoint", "list", "--json"], timeout_s=60)
    return done.stdout, done.stderr


def _endpoints() -> None:
    st.subheader("Hugging Face endpoints")
    stdout, stderr = _listed(str(launch.CONFIG.folder) if launch.CONFIG else "")
    rows = []
    for line in stdout.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):  # one object per endpoint, and nothing when there is none
            rows.append(row)
    if rows:
        st.dataframe(rows, hide_index=True, width="stretch")
        names = [str(r.get("name")) for r in rows]
        name = st.selectbox("An endpoint", names)
        pause, delete = st.columns(2)
        if pause.button("Pause it", width="stretch"):
            done = jobs.run_command(["policy", "endpoint", "pause", name], timeout_s=120)
            _listed.clear()
            st.code((done.stdout + done.stderr).strip(), language=None)
        with delete.popover("Delete it", width="stretch"):
            st.write(f"Delete {name} on Hugging Face?")
            if st.button(f"Yes, delete {name}", type="primary"):
                done = jobs.run_command(
                    ["policy", "endpoint", "delete", name, "--yes"], timeout_s=120
                )
                _listed.clear()
                st.code((done.stdout + done.stderr).strip(), language=None)
    else:
        st.caption((stdout + stderr).strip() or "No endpoint is recorded here.")
    with st.expander("Start one (paid)"):
        name = st.text_input("Name", key="ep:name")
        policy = st.text_input("Policy, REPO@REVISION", key="ep:policy")
        instance = st.text_input("GPU", value="nvidia-l4", key="ep:instance")
        region = st.text_input("Region", value="us-east-1", key="ep:region")
        sure = st.checkbox(
            f"Start {instance or 'it'} in {region or 'its region'}: Hugging Face bills it by the "
            "hour until it scales to zero",
            key="ep:sure",
        )
        if st.button("Start it", disabled=not (sure and name.strip() and policy.strip())):
            where = ["--instance", instance.strip(), "--region", region.strip()]
            # --yes, because the box above has asked, and with no terminal the command asks
            # nothing and refuses without it
            args = ["policy", "endpoint", "up", name.strip(), "--policy", policy.strip(), *where]
            try:
                jobs.table().start("command", [*args, "--yes"], front=False)
            except RuntimeError as e:
                st.error(str(e))
            _listed.clear()
    _started()


def _started() -> None:
    """Each endpoint started from here, its output as it comes, watched by a fragment of its own."""
    started = [job for job in jobs.table().all() if job.kind == "command"][-3:]
    if not started:
        return

    @st.fragment(run_every=1.0 if any(job.running for job in started) else None)
    def watch() -> None:
        for job in started:
            said = "starting" if job.running else f"ended, exit code {job.proc.poll()}"
            st.caption(f"{' '.join(job.argv[4:8])}: {said}")
            st.code("\n".join(list(job.stderr)[-20:]) or "nothing yet", language=None)

    watch()
