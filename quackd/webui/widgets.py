"""Streamlit pieces every screen of the page uses: widgets bound to the shared setup, a job's live
view, its questions, and the floating Stop button.

Imported by the page alone, which `quackd ui` starts with Streamlit installed.
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

import streamlit as st

from quackd.webui import jobs as jobs_mod
from quackd.webui.options import Option
from quackd.webui.state import setup

STOP_KEY = "quackd-stop"

CSS = f"""
<style>
.st-key-{STOP_KEY} {{
  position: fixed; right: 1rem; bottom: 7rem; z-index: 999990;
  width: auto !important; max-width: 16rem; padding: 0.4rem;
  background: var(--background-color, transparent); border-radius: 0.6rem;
}}
.st-key-{STOP_KEY} button {{ min-height: 3.2rem; font-size: 1.1rem; font-weight: 600; }}
div[class*="st-key-quackd-ask"] button {{ min-height: 3rem; font-size: 1.05rem; }}
</style>
"""

EITHER = {"default": None, "on": True, "off": False}
"""How a three-way option reads in the page: left to the task file or the environment, on, off."""


def style() -> None:
    st.html(CSS)


# ── the setup, bound ──


def write(name: str, value: Any) -> None:
    """Change the shared setup from this tab, and note that this tab did, so `sync` never pushes
    the value back over something typed since."""
    setup().set(name, value)
    st.session_state.setdefault("_setup_seen", {})[name] = value


def forget(name: str) -> None:
    """Take `name` out of the shared setup from this tab, as `write` puts one in."""
    setup().drop(name)
    st.session_state.setdefault("_setup_seen", {}).pop(name, None)


def sync() -> None:
    """Bring this tab's widgets up to the shared setup, before any widget is drawn: another tab,
    a phone say, may have changed it since this one last ran."""
    # Against what this tab saw when it last ran, not against a version number: this tab's own
    # writes change the setup too, and a value it wrote a moment ago must not be pushed back over
    # what the person has typed since, which is the widget's newer value arriving with this run.
    now = setup().snapshot()
    seen = st.session_state.get("_setup_seen")
    changed = now if seen is None else {k: v for k, v in now.items() if seen.get(k) != v}
    if changed:
        _know_the_kinds()
        for name, value in changed.items():
            st.session_state[f"opt:{name}"] = _shown(name, value)
    st.session_state["_setup_seen"] = now


def _shown(name: str, value: Any) -> Any:
    """A stored value as its widget holds it: a three-way option as a word, a list as lines."""
    if name in _EITHERS:
        return next((word for word, v in EITHER.items() if v == value), "default")
    if name in _LISTS:
        return "\n".join(value or [])
    if name in _NUMBERS:
        return "" if value is None else str(value)
    return value


_EITHERS: set[str] = set()
_LISTS: set[str] = set()
_NUMBERS: dict[str, str] = {}


def _know_the_kinds() -> None:
    """Which options are three-way, lists or numbers, from both commands, before any is drawn."""
    if _EITHERS or _LISTS or _NUMBERS:
        return
    from quackd.webui.options import COMMANDS, options

    for command in COMMANDS:
        for option in options(command):
            if option.kind == "either":
                _EITHERS.add(option.name)
            elif option.kind == "list":
                _LISTS.add(option.name)
            elif option.kind in ("integer", "number"):
                _NUMBERS[option.name] = option.kind


def _stored(option: Option, shown: Any) -> Any:
    if option.kind == "either":
        return EITHER.get(str(shown))
    if option.kind == "list":
        return [line.strip() for line in str(shown or "").splitlines() if line.strip()]
    if option.kind in ("integer", "number"):
        text = str(shown or "").strip()
        if not text:
            return None
        try:
            return int(text) if option.kind == "integer" else float(text)
        except ValueError:
            return text  # kept as typed: quackd refuses it in its own words when the run starts
    if option.kind == "switch":
        return bool(shown)
    return shown if shown not in ("",) else None


def option_widget(option: Option, *, note: str = "") -> None:
    """One option as the widget its kind needs, bound to the shared setup."""
    if option.kind == "either":
        _EITHERS.add(option.name)
    elif option.kind == "list":
        _LISTS.add(option.name)
    elif option.kind in ("integer", "number"):
        _NUMBERS[option.name] = option.kind
    key = f"opt:{option.name}"
    if key not in st.session_state:
        st.session_state[key] = _shown(option.name, setup().get(option.name, option.default))
    label = f"{option.flag or option.name}{f'  ({note})' if note else ''}"

    def changed() -> None:
        write(option.name, _stored(option, st.session_state.get(key)))

    kw: dict[str, Any] = {"key": key, "help": option.help or None, "on_change": changed}
    if option.kind == "switch":
        st.toggle(label, **kw)
    elif option.kind == "either":
        if st.session_state.get(key) not in EITHER:
            st.session_state[key] = "default"
        st.segmented_control(label, list(EITHER), required=True, **kw)
    elif option.choices:
        choices = ["", *option.choices]
        if st.session_state.get(key) not in choices:
            st.session_state[key] = ""
        st.selectbox(label, choices, format_func=lambda v: v or "default", **kw)
    elif option.kind == "list":
        st.text_area(f"{label}, one per line", **kw)
    else:
        if st.session_state.get(key) is None:
            st.session_state[key] = ""
        st.text_input(label, **kw)


def pick(
    name: str,
    label: str,
    choices: list[Any],
    *,
    default: Any = None,
    shown: Any = None,
    help: str | None = None,
    segmented: bool = False,
) -> Any:
    """A choice from `choices`, kept in the shared setup under `name`, and returned."""
    key = f"opt:{name}"
    shared = setup()
    current = st.session_state.get(key, shared.get(name, default))
    if current not in choices:
        current = default if default in choices else (choices[0] if choices else None)
    st.session_state[key] = current
    if shared.get(name) != current:
        write(name, current)

    def changed() -> None:
        write(name, st.session_state.get(key))

    kw: dict[str, Any] = {"key": key, "help": help, "on_change": changed}
    if shown is not None:
        kw["format_func"] = shown
    if segmented:
        st.segmented_control(label, choices, required=True, **kw)
    else:
        st.selectbox(label, choices, **kw)
    return st.session_state.get(key)


def text(name: str, label: str, *, help: str | None = None, placeholder: str = "") -> str:
    """A line of text kept in the shared setup under `name`, and returned."""
    key = f"opt:{name}"
    shared = setup()
    if key not in st.session_state:
        st.session_state[key] = shared.get(name) or ""

    def changed() -> None:
        write(name, (st.session_state.get(key) or "").strip() or None)

    st.text_input(label, key=key, help=help, placeholder=placeholder, on_change=changed)
    return str(st.session_state.get(key) or "").strip()


def key_field(env: str, label: str) -> None:
    """A key typed into the page for this session: kept in the page's memory, never shown again,
    never written down, and handed to a run through its environment under `env`."""
    typed = st.text_input(
        label,
        type="password",
        key=f"key:{env}",
        help=f"Kept in this page's memory until it stops, and given to each run as {env}. "
        "Never written to a file or put on a command line.",
    )
    if typed and typed.strip() != setup().keys.get(env):
        setup().set_key(env, typed)


# ── a job, live ──


def run_folder(view: dict[str, Any]) -> Path | None:
    """The job's run folder, from its `run_dir` line, under the page's working folder where the
    child named it relative to that."""
    from quackd.webui import launch

    given = view.get("run_dir")
    if not given:
        return None
    path = Path(str(given))
    if not path.is_absolute() and launch.CONFIG is not None:
        path = launch.CONFIG.folder / path
    return path


def last_saved_frame(folder: Path) -> Path | None:
    """The newest picture a run saved, `frames/NNNN.png` or `frames/NNNN-<camera>.png`."""
    saved = sorted((folder / "frames").glob("*.png")) if (folder / "frames").is_dir() else []
    return saved[-1] if saved else None


def frame(view: dict[str, Any]) -> None:
    """The newest frame: as it streams in while the run goes, and once the run has ended, the
    last one it saved, which the stream may have dropped to keep its pace."""
    folder = run_folder(view) if view.get("end") else None
    saved = last_saved_frame(folder) if folder is not None else None
    if saved is not None:
        st.image(str(saved), caption=saved.stem)
        return
    shot = view.get("frame")
    if shot and shot.get("jpeg"):
        st.image(base64.b64decode(shot["jpeg"]), caption=shot.get("caption") or None)


def status(view: dict[str, Any]) -> None:
    if view.get("end"):
        return
    said = view.get("status") or {}
    text = said.get("text")
    if text:
        step = said.get("step")
        st.caption(f"{step} · {text}" if step else text)
    elif view.get("running"):
        st.caption("starting")


def questions(job: Any, view: dict[str, Any]) -> None:
    """The run's questions, each with its own two buttons: an answer names the question it
    answers, so a second tap answers nothing."""
    for asked in view.get("asks") or ():
        with st.container(border=True, key=f"quackd-ask-{job.id}-{asked['id']}"):
            if asked.get("detail"):
                st.warning(asked["detail"])
            st.markdown(f"**{asked['question']}**")
            yes, no = st.columns(2)
            if yes.button(
                "Yes", key=f"yes-{job.id}-{asked['id']}", type="primary", width="stretch"
            ):
                job.answer(asked["id"], True)
                st.rerun()
            if no.button("No", key=f"no-{job.id}-{asked['id']}", width="stretch"):
                job.answer(asked["id"], False)
                st.rerun()


_RENDERED: dict[str, tuple[Any, list[str], int]] = {}
"""Per job and view: the terminal's own renderer, the lines it has made, the next record to read."""


def lines(job: Any, *, goal: int | None = None) -> list[str]:
    """The job's records as a terminal narrates them (`log.LineLog`), each read once. A burst of
    intents is folded into one line as at a terminal, and flushed when the job has ended. The
    system prompt is left out, as `--no-log-prompt` leaves it out at a terminal: it would push
    the steps out of view, and it is in the run's transcript and in its terminal below."""
    from quackd.log import LineLog, LogEvent

    cache_key = f"{job.id}:{goal}"
    renderer, made, index = _RENDERED.get(cache_key, (None, [], 0))
    if renderer is None:
        made = []
        renderer = LineLog(lambda text, _style: made.append(text), prompt=False)
    for line in job.since(index):
        index = line["i"] + 1
        if line.get("ev") != "event" or (goal is not None and line.get("goal") != goal):
            continue
        event = LogEvent(
            kind=str(line.get("kind")), t=float(line.get("t") or 0), data=line.get("data") or {}
        )
        renderer(event)
    if not job.running:
        renderer.flush()
    _RENDERED[cache_key] = (renderer, made, index)
    return made


def narration(job: Any, *, goal: int | None = None, tail: int = 300) -> None:
    text = "\n".join(lines(job, goal=goal)[-tail:])
    if text:
        st.code(text, language=None, wrap_lines=True)


def outcome(view: dict[str, Any]) -> None:
    end = view.get("end")
    if end:
        shown = f"{end.get('outcome')}: {end.get('reason') or ''}".rstrip(": ")
        if end.get("outcome") == "success":
            st.success(shown)
        elif end.get("outcome") in ("refused", "error"):
            st.error(shown + (f"\n\n{end['hint']}" if end.get("hint") else ""))
        else:
            st.warning(shown)
    for notice in view.get("notices") or ():
        st.info(notice)


def terminal(view: dict[str, Any]) -> None:
    said = view.get("stderr") or []
    if said:
        with st.expander("What a terminal would show"):
            st.code("\n".join(said), language=None)


# ── the way here from a phone ──


def phone() -> None:
    """The address `quackd ui` printed, with its token, and its QR code, so a session begun at a
    desk can be picked up on a phone. On loopback no phone can reach the page, and it says how."""
    from quackd.webui import launch

    config = launch.CONFIG
    if config is None:
        return
    with st.expander("Open on your phone"):
        if launch.is_loopback(config.bind) or not config.token or not config.urls:
            st.caption(
                "This page listens on this computer alone. To reach it from a phone on the same "
                "Wi-Fi, start it with quackd ui --bind 0.0.0.0."
            )
            return
        st.image(launch.qr_image(config.urls[0]), width=220)
        st.code(config.urls[0], language=None, wrap_lines=True)
        st.caption("Anyone who has this address can drive your robots.")


# ── the Stop button ──


def floating_stop() -> None:
    """Stop, over every screen and at every width, whenever a run or a chat is going: on a phone
    the sidebar folds away, and the button must not go with it.

    Drawn by a fragment of its own, again every second, so a tab left idle on another screen
    shows it the moment a run starts from somewhere else, and a press acts in its callback, before
    anything else is drawn again. In a run the first Stop is the kill switch's first press and
    Quit at once its second; in a chat every Stop brakes, and the session goes on."""

    @st.fragment(run_every=1.0)
    def stop_button() -> None:
        moving = [job for job in jobs_mod.table().running() if job.kind in ("goal", "chat")]
        if not moving:
            return
        stopping = [job for job in moving if job.kind == "goal" and job.stop_sent]
        with st.container(key=STOP_KEY):
            if len(stopping) < len(moving):  # a run not yet stopped, or a chat to brake
                st.button(
                    "Stop",
                    type="primary",
                    width="stretch",
                    icon=":material/stop_circle:",
                    key="quackd-stop-press",
                    on_click=_stop_everything,
                )
            if stopping:
                st.caption("Stopping: the robot is being stopped and folded")
                st.button(
                    "Quit at once",
                    width="stretch",
                    key="quackd-quit-press",
                    help="Ends the run without waiting for its fold, as a second Ctrl-C does.",
                    on_click=_quit_at_once,
                )
            st.caption("Not an emergency stop")

    stop_button()


def _stop_everything() -> None:
    """Every run's Stop and every chat's brake, on every robot the page drives."""
    for job in jobs_mod.table().running():
        if job.kind in ("goal", "chat"):
            job.stop()


def _quit_at_once() -> None:
    for job in jobs_mod.table().running():
        if job.kind == "goal" and job.stop_sent:
            job.stop(now=True)
