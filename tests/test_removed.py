"""What the changelog says is gone stays gone, and is refused in words that say what to do.

`### Removed` in `[Unreleased]` takes out every simulator and what ran only on one: the 2D
world's and the physics backends, three commands, five run flags on six commands, the
Microduck's `explore` and `find`, three bundled tasks and the ToddlerBot daemon's `--sim`, with
the `von` decision LLM beside them. Each row here is one refusal, and every expected word was
pasted from one real run of the refused command against a mock, never typed from the changelog,
so a test fails on the day the refusal changes what it says.

Guarded elsewhere, and not repeated: `--team N` (`tests/test_cli_pilots.py`), the coordinator's
task-file settings (`tests/test_glotfile.py`) and `list-llms` (`tests/test_llm_wording.py`).

Not guarded, on purpose: the seven removed variables, `ROBOGLOT_MUJOCO_BODY`,
`ROBOGLOT_MUJOCO_SHADOWS`, `ROBOGLOT_MICRODUCK_ASSETS`, `ROBOGLOT_LEROBOT_SIM_ASSETS`,
`ROBOGLOT_CACHE_DIR`, `ROBOGLOT_STRICT_SEEDS` and `ROBOGLOT_REQUIRE_GL`. The CLI ignores each
without a word today. Whether it should warn when one is set, as it does for a renamed name, is
the owner's open question, so nothing here asserts that it warns or that it stays silent. When
the owner answers, the answer gets its row here.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from roboglot.cli import app
from tests.conftest import plain_text
from tests.support import load_script, verb_column

REPO = Path(__file__).resolve().parents[1]
DAEMON = REPO / "bridge" / "toddlerbot" / "roboglot_toddlerbot_bridge.py"

runner = CliRunner()


def _refused(argv: list[str], code: int) -> str:
    """The output of `roboglot ARGV` as one plain line, once it exited with `code`."""
    result = runner.invoke(app, argv, env={"COLUMNS": "200"})
    assert result.exit_code == code, (argv, result.exit_code, result.output)
    return plain_text(result.output)


def _names(listed: str) -> list[str]:
    """The names of a refusal's `choose one of a, b, c`."""
    return listed.replace(",", " ").split()


# ── the simulator backends: refused at --robot, naming the ones the adapter has ─────────────


@pytest.mark.parametrize(
    "robot",
    [
        "microduck:sim2d",
        "open_duck:sim2d",
        "toddlerbot:sim2d",
        "alohamini:sim2d",
        "microduck:mujoco",
        "lerobot:mujoco",
    ],
)
def test_a_removed_simulator_backend_is_refused_naming_the_mock(robot: str) -> None:
    """A run on one stops before anything is built, saying which backends the adapter has; the
    mock is among them for every body that had a simulator."""
    adapter, backend = robot.split(":")
    argv = ["run", "hello-world", "--robot", robot, "--llm", "fake", "--no-memory"]
    said = _refused(argv, 1)
    opening = f"unknown backend '{backend}' for {adapter}; choose one of "
    assert opening in said, said
    assert "mock" in _names(said.split(opening, 1)[1]), said


# ── the commands that ran only on a simulator ───────────────────────────────────────────────


@pytest.mark.parametrize(
    "argv",
    [["robot", "twin"], ["preflight"], ["record"]],
    ids=["robot-twin", "preflight", "record"],
)
def test_a_removed_command_is_no_command(argv: list[str]) -> None:
    said = _refused(argv, 2)
    assert f"No such command '{argv[-1]}'." in said, said


# ── the run flags, on every command that took them ──────────────────────────────────────────

COMMANDS = [["run"], ["drive"], ["serve-mcp"], ["map"], ["places", "add"], ["validate"]]
FLAGS = [
    ["--seed", "1"],
    ["--live"],
    ["--gif"],
    ["--no-gif"],
    ["--gif-size", "64"],
    ["--room", "kitchen"],
]


@pytest.mark.parametrize("command", COMMANDS, ids=[" ".join(c) for c in COMMANDS])
@pytest.mark.parametrize("flag", FLAGS, ids=[f[0] for f in FLAGS])
def test_a_removed_flag_is_no_option(command: list[str], flag: list[str]) -> None:
    """`--help` comes last, so a flag that came back would print the help and exit 0 here
    rather than start a run, a drive or a server."""
    said = _refused([*command, *flag, "--help"], 2)
    assert f"No such option: {flag[0]}" in said, said


@pytest.mark.parametrize(
    "argv", [["onboard", "serve", "--seed", "1"], ["onboard", "preflight", "--live"]]
)
def test_the_onboard_flags_of_the_same_names_stay(argv: list[str]) -> None:
    """`onboard serve --seed` seeds the stand-in radio and `onboard preflight --live` is the
    preflight's own: both still parse, which the rows above must not be read as denying."""
    result = runner.invoke(app, [*argv, "--help"], env={"COLUMNS": "200"})
    assert result.exit_code == 0, result.output
    assert "No such option" not in plain_text(result.output)


# ── what ran only on a simulator's room ─────────────────────────────────────────────────────


def test_the_microduck_has_no_explore_or_find_and_moss_keeps_its_own_explore() -> None:
    duck = runner.invoke(app, ["list-verbs", "--robot", "microduck:mock"], env={"COLUMNS": "200"})
    assert duck.exit_code == 0, duck.output
    verbs = verb_column(duck.output)
    assert "go_to_place" in verbs and not {"explore", "find"} & verbs, verbs
    moss = runner.invoke(app, ["list-verbs", "--robot", "moss:mock"], env={"COLUMNS": "200"})
    assert moss.exit_code == 0, moss.output
    assert "explore" in verb_column(moss.output)


@pytest.mark.parametrize("task", ["team-kick", "find-the-ball", "favourite-spot"])
def test_a_removed_bundled_task_is_not_found(task: str) -> None:
    said = _refused(["validate", task], 1)
    assert f"{task}: file not found (also not a bundled starter task)" in said, said


# ── the decision LLM that is gone ───────────────────────────────────────────────────────────


def test_the_von_decision_llm_is_an_unknown_name_and_local_is_offered() -> None:
    """A Von server someone still runs is reached as `--decision-llm local`, which the refusal
    lists among the names it takes."""
    argv = ["run", "hello-world", "--robot", "microduck:mock", "--llm", "fake"]
    said = _refused([*argv, "--decision-llm", "von", "--no-memory"], 1)
    opening = "unknown decision LLM 'von' from --decision-llm; choose one of "
    assert opening in said, said
    assert "local" in _names(said.split(opening, 1)[1]), said


# ── the ToddlerBot daemon's --sim ───────────────────────────────────────────────────────────


def test_the_toddlerbot_daemon_refuses_sim_even_for_the_real_robot(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`--sim real` was the default and drove the robot; the daemon drives the robot always and
    takes no `--sim` at all."""
    daemon = load_script(DAEMON, "toddlerbot_bridge_in_test_removed")
    with pytest.raises(SystemExit) as stopped:
        daemon.main(["--sim", "real"])
    assert stopped.value.code == 2
    assert "error: unrecognized arguments: --sim real" in capsys.readouterr().err
