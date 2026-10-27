"""Setup: the robot, its pilot, and every other option of the two commands, chosen once for both.

A goal is `quackd run` and a chat is `quackd drive`, and both are made from what is chosen here,
which the "same as" lines show as the commands to type at a terminal for the same run.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

import streamlit as st

from quackd import dotenv_names
from quackd.command import command_text
from quackd.webui import catalog, jobs, launch, widgets
from quackd.webui.options import DECISION_OPTIONS, MODEL_OPTIONS, SECRET_ENV, argv_for, by_panel
from quackd.webui.state import setup

CURATED = frozenset(
    {
        "duckfile",
        "goal",
        "robot",
        "address",
        "camera_url",
        "token",
        "llm",
        "base_url",
        "api_key",
        "vision",
        "extra_body",
        "effort",
        "price",
        "controller",
        "policy_url",
        "policy_token",
        "accept_other_frame",
        "async_chunks",
        "decision_llm",
        "decision_url",
        "decision_mode",
        "decision_frames",
    }
)
"""The options the robot and pilot sections choose; the rest are offered under their panels."""

NO_DECISION = "none"


def screen() -> None:
    st.title("Setup")
    st.caption(
        "Choose a robot and its pilot once. Goal and Chat both run with what is chosen here."
    )
    _robot()
    controller = _pilot()
    _decision(controller)
    _everything_else()
    _same_as()


def _option(command_or_both: str, name: str) -> Any:
    """The option `name` of either command, as the page offers it."""
    for command in ("run", "drive") if command_or_both == "both" else (command_or_both,):
        for options in by_panel(command).values():
            for option in options:
                if option.name == name:
                    return option
    return None


def _robot() -> None:
    st.subheader("Robot")
    registered = catalog.registered()
    sources = ["adapter"] + (["registered"] if registered else [])
    source = widgets.pick(
        "_robot_from",
        "Robot",
        sources,
        default="registered" if registered else "adapter",
        shown=lambda v: {"adapter": "An adapter and backend", "registered": "A registered robot"}[
            v
        ],
        segmented=True,
    )
    if source == "registered":
        names = [r["name"] for r in registered]
        name = widgets.pick("_registered", "Registered robot", names)
        entry = next(r for r in registered if r["name"] == name)
        st.caption(
            " · ".join(f"{k} {entry[k]}" for k in ("spec", "address", "note") if entry.get(k))
        )
        widgets.write("robot", name)
        backend = str(entry.get("spec", "")).split(":")[-1]
        adapter = str(entry.get("spec", "")).split(":")[0]
    else:
        rows = catalog.adapters()
        names = [row["name"] for row in rows]
        installed = {row["name"]: row.get("installed") for row in rows}
        adapter = widgets.pick(
            "_adapter",
            "Adapter",
            names,
            default="microduck",
            shown=lambda n: n if installed.get(n) else f"{n} (not installed)",
        )
        row = next(r for r in rows if r["name"] == adapter)
        backend = widgets.pick("_backend", "Backend", list(row.get("backends") or ["mock"]))
        widgets.write("robot", f"{adapter}:{backend}")
        if not row.get("installed"):
            st.warning(
                f'This adapter is not installed: uv pip install "quackd[{row.get("extra")}]"'
            )
    if not catalog.is_simulated(backend):
        _connect(adapter, backend, source == "registered")


def _connect(adapter: str, backend: str, registered: bool) -> None:
    """A real body: where it is, its cameras, its token, and a check that moves nothing."""
    shared = setup()
    with st.container(border=True):
        st.markdown("**Connecting it**")
        if registered:
            st.caption(
                "A registered robot brings its own address, cameras and token. What is set "
                "here overrides them for these runs."
            )
        for name in ("address", "camera_url"):
            option = _option("run", name)
            if option is not None:
                widgets.option_widget(option)
        block = catalog.connect(adapter, backend)
        if block is not None:
            _how_to_connect(block)
        token_env = getattr(block, "token_env", None)
        if token_env:
            widgets.key_field(token_env, f"Token ({token_env})")
        if block is not None and block.caution:
            st.warning(block.caution)
        if st.button(
            "Check the connection",
            help="Connects, asks how it is, and closes, driving it nowhere.",
        ):
            request: dict[str, Any] = (
                {"registered": shared.get("_registered")}
                if registered
                else {
                    "spec": shared.get("robot"),
                    "address": shared.get("address"),
                    "camera_url": shared.get("camera_url") or None,
                    "token_env": token_env,
                }
            )
            _probe(request, shared.get("robot"))
        _probe_answer()


def _how_to_connect(block: Any) -> None:
    """The adapter's own word on connecting this backend (`AdapterInfo.connect`)."""
    said = [f"**Address:** {block.address}"]
    if block.default:
        said.append(f"Without one, quackd uses `{block.default}`.")
    else:
        said.append("There is no default: give one.")
    if block.example:
        said.append(f"For example `{block.example}`.")
    if block.camera:
        said.append(f"**Camera:** {block.camera}")
    if block.on_robot:
        said.append(f"**On the robot:** {block.on_robot}")
    if block.first:
        said.append(f"**First:** {block.first}")
    st.markdown("  \n".join(said))
    if block.docs:
        st.caption(f"More in {block.docs}")


def _probe(request: dict[str, Any], robot: str | None) -> None:
    table = jobs.table()
    try:
        job = table.start(
            "probe",
            [json.dumps(request)],
            robot=robot,
            holds=catalog.robot_identity(robot, setup().get("address")),
            env=catalog.session_environment(setup().keys),
            front=False,
            module="quackd.webui.probe",
        )
    except RuntimeError as e:
        st.error(str(e))
        return
    st.session_state["probe_job"] = job.id


PROBE_WAIT_S = 60.0
"""How long a check may take before the page lets it go."""


def _probe_answer() -> None:
    """What the check found, watched by a fragment of its own while it runs, so the screen, and
    the Stop over it, never wait on a robot that takes its time to answer."""
    job = jobs.table().get(st.session_state.get("probe_job", ""))
    if job is None:
        return

    @st.fragment(run_every=0.5 if job.running else None)
    def answer() -> None:
        if job.running and time.time() - job.started_at > PROBE_WAIT_S:
            job.shutdown(stop_grace_s=0, end_grace_s=2)
        if job.running:
            st.caption("Checking: connecting, asking how it is, and closing")
            return
        said = "\n".join(job.stderr)
        try:
            found = json.loads(said.strip().splitlines()[-1])
        except (ValueError, IndexError):
            st.error(said or f"the check said nothing within {PROBE_WAIT_S:g} seconds")
            return
        if found.get("reachable") is True:
            st.success(f"It answered: {found.get('detail')}")
        elif found.get("reachable") is None:
            st.info(str(found.get("detail")))
        else:
            st.error(f"It did not answer: {found.get('detail')}")

    answer()


def _pilot() -> str:
    st.subheader("Pilot")
    # kept under a name of its own, and `--controller` set only for a VLA: the LLM is run's
    # default, and a command line saying so would be one nobody types
    controller = widgets.pick(
        "_controller",
        "Who flies it",
        ["llm", "vla"],
        default="llm",
        shown=lambda v: {"llm": "An LLM", "vla": "A VLA policy (LeRobot arm)"}[v],
        segmented=True,
    )
    if controller != "vla":
        widgets.forget("controller")
    else:
        widgets.write("controller", "vla")
        for name in MODEL_OPTIONS:
            widgets.forget(name)
        for name in ("policy_url", "async_chunks", "accept_other_frame"):
            option = _option("run", name)
            if option is not None:
                widgets.option_widget(
                    option, note="goal only" if _option("drive", name) is None else ""
                )
        widgets.key_field(SECRET_ENV["policy_token"], "Policy server token (QUACKD_POLICY_TOKEN)")
        st.caption("The Policies screen starts a local policy server and gives its address.")
        return str(controller)
    vendors = list(catalog.vendors())
    vendor = widgets.pick("_vendor", "Vendor", vendors, default="fake")
    models = catalog.models(vendor)
    if models:
        ids = [m.id for m in models]
        labels = {m.id: f"{m.label} ({m.id})" for m in models}
        model = widgets.pick("_model", "Model", ids, default=ids[0], shown=lambda i: labels[i])
    elif vendor == "fake":
        model = ""
    else:
        model = widgets.text(
            "_model_text",
            "Model",
            help="The model id the server serves, as it spells it.",
            placeholder="as the server names it",
        )
    widgets.write("llm", f"{vendor}:{model}" if model else vendor)
    _key_status(vendor)
    for name in ("effort", "vision", "base_url", "extra_body", "price"):
        option = _option("run", name)
        if option is not None:
            widgets.option_widget(
                option, note="goal only" if _option("drive", name) is None else ""
            )
    return str(controller)


def _key_status(vendor: str) -> None:
    named = catalog.vendor_key(vendor)
    extra = catalog.vendor_extra(vendor)
    if extra is not None and not extra[1]:
        st.warning(f'Its SDK is not installed: uv pip install "quackd[{extra[0]}]"')
    if named is None:
        return
    env, fallback = named
    folder = launch.CONFIG.folder if launch.CONFIG else os.getcwd()
    key = catalog.key_for(
        env,
        # the shell's alone: a value a `.env` gave the page is that file's, and is reported so
        environment=dotenv_names.shell_environment(),
        dotenv_values=catalog.dotenv(folder),
        session=setup().keys,
        fallback=fallback,
    )
    if key.source:
        where = {"environment": "the environment", ".env": ".env", "session": "this page"}[
            key.source
        ]
        st.caption(f"{key.fallback or env}: set in {where} ({key.shown})")
    else:
        st.caption(f"{env}: not set")
    widgets.key_field(env, f"{env} for this session")


def _decision(controller: str) -> None:
    st.subheader("Decision LLM")
    if controller == "vla":
        for name in DECISION_OPTIONS:
            widgets.forget(name)
        st.caption("A VLA pilot asks no decision LLM: each instruction goes to the arm's policy.")
        return
    presets = catalog.decision_llms()
    names = [NO_DECISION, *[p.name for p in presets]]
    name = widgets.pick(
        "_decision",
        "Decision LLM",
        names,
        default=NO_DECISION,
        help="A stepper beside the pilot in a goal, and the fast lane for a chat line. A chat's "
        "goals have the stepper too only with a mode chosen below.",
    )
    if name == NO_DECISION:
        widgets.forget("decision_llm")
        return
    model = widgets.text(
        "_decision_model", "Its model, where it serves several", placeholder="its default"
    )
    widgets.write("decision_llm", f"{name}:{model}" if model else name)
    preset = next(p for p in presets if p.name == name)
    if preset.key_env:
        widgets.key_field(preset.key_env, f"{preset.key_env} for this session")
    for option_name in ("decision_mode", "decision_frames", "decision_url"):
        option = _option("both", option_name)
        if option is not None:
            note = "goal only" if _option("drive", option_name) is None else ""
            widgets.option_widget(option, note=note)


def _everything_else() -> None:
    st.subheader("Everything else")
    st.caption(
        "Every other option of quackd run and quackd drive, under the panels --help shows them in."
    )
    run_panels, drive_panels = by_panel("run"), by_panel("drive")
    drive_names = {o.name for opts in drive_panels.values() for o in opts}
    run_names = {o.name for opts in run_panels.values() for o in opts}
    panels: dict[str, list[Any]] = {}
    for grouped in (run_panels, drive_panels):
        for panel, options in grouped.items():
            for option in options:
                if option.name in CURATED or any(
                    o.name == option.name for o in panels.get(panel, [])
                ):
                    continue
                panels.setdefault(panel, []).append(option)
    for panel, options in panels.items():
        with st.expander(panel):
            for option in options:
                note = (
                    "goal only"
                    if option.name not in drive_names
                    else "chat only"
                    if option.name not in run_names
                    else ""
                )
                if option.secret:
                    env = SECRET_ENV.get(option.name)
                    if env:
                        widgets.key_field(env, f"{option.flag} ({env})")
                    continue
                widgets.option_widget(option, note=note)


def _same_as() -> None:
    st.subheader("Same as")
    values = setup().options()
    goal = {**values, "goal": values.get("goal") or "..."}
    st.caption("A goal")
    st.code(command_text(argv_for("run", goal)), language="bash", wrap_lines=True)
    st.caption("A chat")
    st.code(command_text(argv_for("drive", values)), language="bash", wrap_lines=True)
