"""The page's options: read off `quackd run` and `quackd drive`, and turned back into the command
a set of choices makes (`quackd.webui.options`). Plain Python, no Streamlit."""

from __future__ import annotations

from typing import Any

import pytest
import typer

from quackd.agent.decision.factory import MODES
from quackd.agent.providers.factory import EFFORTS
from quackd.cli import CONTROLLERS, app
from quackd.command import SECRET_FLAGS
from quackd.perception import DETECTOR_CHOICES
from quackd.webui.options import LEFT_OUT, PANELS, argv_for, by_panel, options, parsed


def _visible(command: str) -> set[str]:
    params = typer.main.get_command(app).commands[command].params  # type: ignore[attr-defined]
    return {p.name for p in params if not getattr(p, "hidden", False)}


@pytest.mark.parametrize("command", ["run", "drive"])
def test_every_option_a_command_has_is_offered_but_the_ones_the_page_leaves_out(
    command: str,
) -> None:
    """Derived, never listed: a flag quackd gains is on the page the day it lands."""
    offered = {o.name for o in options(command)}
    assert offered == _visible(command) - LEFT_OUT
    assert "front" not in offered, "the page's own flag is not one of its choices"


@pytest.mark.parametrize("command", ["run", "drive"])
def test_nothing_chosen_makes_the_bare_command(command: str) -> None:
    """But for a chat's memory, which the page shows on, as a run keeps it."""
    bare = [command] if command == "run" else [command, "--memory"]
    assert argv_for(command, {}) == bare
    defaults = {o.name: o.default for o in options(command)}
    assert argv_for(command, defaults) == [command], "the command's own defaults, said outright"


def test_every_kind_of_option_comes_back_through_quackds_own_parser() -> None:
    chosen: dict[str, Any] = {
        "duckfile": "hello-world",
        "image": ["front.png", "side.png"],
        "seed": 7,
        "dry_run": True,
        "play": False,
        "async_chunks": False,
        "gif_size": 512,
        "effort": "high",
        "robot": "microduck:sim2d",
        "decision_frames": True,
    }
    argv = argv_for("run", chosen)
    assert argv[:2] == ["run", "hello-world"]
    assert "--no-play" in argv and "--no-async-chunks" in argv and "--decision-frames" in argv
    got = parsed(argv)
    back = {name: list(v) if isinstance(v, tuple) else v for name, v in got.items()}
    assert {name: back[name] for name in chosen} == chosen
    kinds = {o.name: o.kind for o in options("run")}
    assert (kinds["seed"], kinds["gif_size"], kinds["fov_deg"]) == ("integer", "integer", "number")


def test_a_secret_never_reaches_the_command_line() -> None:
    secrets = {"api_key": "sk-live", "token": "t0k", "host_token": "h0st", "policy_token": "p0l"}
    argv = argv_for("run", {**secrets, "goal": "wave"})
    assert argv == ["run", "--goal", "wave"]
    assert not set(argv) & set(SECRET_FLAGS)
    assert {o.name for o in options("run") if o.secret} == set(secrets)


def test_one_set_of_choices_makes_both_commands() -> None:
    """A setup made once serves both modes: what drive does not take is simply not on its line."""
    chosen = {"robot": "microduck:sim2d", "llm": "fake", "play": True, "gif": False, "seed": 3}
    run = argv_for("run", chosen)
    drive = argv_for("drive", chosen)
    assert "--play" in run and "--no-gif" in run
    assert drive == [
        "drive",
        *("--robot", "microduck:sim2d", "--seed", "3", "--llm", "fake", "--play", "--memory"),
    ], "drive takes --play and a memory for its goals, and no GIF"
    parsed(drive)


def test_the_words_an_option_takes_come_from_where_quackd_defines_them() -> None:
    by_name = {o.name: o for o in options("run")}
    assert by_name["effort"].choices == tuple(EFFORTS)
    assert by_name["decision_mode"].choices == tuple(MODES)
    assert by_name["detector"].choices == tuple(DETECTOR_CHOICES)
    assert by_name["controller"].choices == tuple(CONTROLLERS)


def test_the_panels_come_in_the_order_help_shows_them() -> None:
    panels = list(by_panel("run"))
    assert panels[: len(PANELS)] == [p for p in PANELS if p in panels]
    assert by_panel("run")["Task"][0].kind == "task"


def test_a_vla_pilot_takes_no_model_and_no_decision_llm_however_it_was_chosen() -> None:
    """`quackd run` refuses a model's flag, a picture and --play beside --controller vla, and
    the Policies screen chooses a VLA without passing through Setup's pilot section."""
    values = {
        "robot": "lerobot:mujoco",
        "controller": "vla",
        "policy_url": "http://127.0.0.1:9875",
        "llm": "fake",
        "vision": True,
        "decision_llm": "jev",
        "play": True,
    }
    argv = argv_for("run", {**values, "goal": "pick up the cube"})
    assert "--controller" in argv and "--policy-url" in argv
    for flag in ("--llm", "--vision", "--decision-llm", "--play"):
        assert flag not in argv, flag
    llm_run = argv_for("run", {**values, "controller": "llm", "goal": "x"})
    assert "--llm" in llm_run and "--decision-llm" in llm_run
    chat = argv_for("drive", values)
    assert "--controller" in chat and "--policy-url" in chat, "drive takes a VLA pilot too"
    for flag in ("--llm", "--vision", "--decision-llm", "--play"):
        assert flag not in chat, flag


def test_one_memory_switch_for_a_goal_and_a_chat() -> None:
    """A run keeps a memory unless told not to and a chat keeps none unless told to, and the page
    shows one switch, on. A chat nobody changed it for keeps a memory, as the switch says."""
    assert "--memory" in argv_for("drive", {})
    assert not {"--memory", "--no-memory"} & set(argv_for("run", {"goal": "x"}))
    assert not {"--memory", "--no-memory"} & set(argv_for("drive", {"memory": False}))
    assert "--no-memory" in argv_for("run", {"goal": "x", "memory": False})
