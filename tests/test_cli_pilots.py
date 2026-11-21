"""`roboglot run --team NAME`: a stored team of pilots, one LLM per body.

The counterpart to `tests/test_pilots.py`, which drives the runner directly. This drives the
command a person types.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from roboglot.cli import app
from roboglot.registry import Registry, RobotEntry, StoredTeam

runner = CliRunner()


def _reg(tmp_path: Path) -> list[str]:
    return ["--registry-dir", str(tmp_path)]


def _run_args(tmp_path: Path) -> list[str]:
    return [
        "--llm",
        "fake",
        "--no-log",
        "--runs-dir",
        str(tmp_path / "runs"),
        "--memory-dir",
        str(tmp_path / "mem"),
        *_reg(tmp_path),
    ]


def _seed(tmp_path: Path, robots: dict[str, str], teams: dict[str, list[str]]) -> Registry:
    registry = Registry(tmp_path)
    for name, spec in robots.items():
        registry.add_robot(RobotEntry(name=name, spec=spec))
    for name, members in teams.items():
        registry.add_team(StoredTeam(name=name, members=members))
    return registry


def _newest(tmp_path: Path) -> Path:
    return sorted((tmp_path / "runs").iterdir())[-1]


def _summary(tmp_path: Path) -> dict[str, Any]:
    return json.loads((_newest(tmp_path) / "summary.json").read_text(encoding="utf-8"))


# ── a name is a team of pilots ─────────────────────────────────────────────────────────


def test_the_demo_task_file_runs_with_no_registry_and_no_key(tmp_path: Path) -> None:
    """`roboglot run team-hello --llm fake` has to work in a fresh checkout."""
    result = runner.invoke(app, ["run", "team-hello", *_run_args(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "members 2/2 succeeded" in result.output
    assert "talk 2" in result.output
    summary = _summary(tmp_path)
    assert summary["team"]["method"] == "pilots"
    assert summary["robots"] == {"duck": "microduck:mock", "arm": "lerobot:mock"}
    for name in ("duck", "arm"):
        assert (_newest(tmp_path) / "robots" / name / "transcript.jsonl").exists()


def test_three_different_bodies_run_as_one_stored_team(tmp_path: Path) -> None:
    _seed(
        tmp_path,
        {"duck": "microduck:mock", "arm": "lerobot:mock", "cart": "rosbridge:mock"},
        {"trio": ["duck", "arm", "cart"]},
    )
    result = runner.invoke(app, ["run", "team-hello", "--team", "trio", *_run_args(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "members 3/3 succeeded" in result.output
    assert "stored as" in result.output and "trio" in result.output
    assert _summary(tmp_path)["team"]["name"] == "trio"


def test_two_bodies_of_one_kind_run_and_keep_separate_notes(tmp_path: Path) -> None:
    _seed(
        tmp_path,
        {"duck-a": "microduck:mock", "duck-b": "microduck:mock"},
        {"twins": ["duck-a", "duck-b"]},
    )
    result = runner.invoke(app, ["run", "team-hello", "--team", "twins", *_run_args(tmp_path)])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "mem" / "duck-a.jsonl").exists()
    assert (tmp_path / "mem" / "duck-b.jsonl").exists()
    assert not (tmp_path / "mem" / "microduck-mock.jsonl").exists()


def test_a_goal_can_be_given_to_a_stored_team(tmp_path: Path) -> None:
    _seed(
        tmp_path,
        {"duck": "microduck:mock", "arm": "lerobot:mock"},
        {"pair": ["duck", "arm"]},
    )
    result = runner.invoke(
        app,
        [
            "run",
            "--goal",
            "say hello to your team and stop",
            "--team",
            "pair",
            *_run_args(tmp_path),
        ],
    )
    assert result.exit_code in (0, 1), result.output
    assert "members" in result.output, "it ran rather than refusing"


def test_a_member_that_fails_fails_the_team(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from roboglot.agent.providers.base import ToolCall
    from roboglot.agent.providers.fake import FakeProvider

    def give_up(obs: Any, step: int, history: list[Any]) -> ToolCall:
        return ToolCall(name="declare_failure", arguments={"reason": "I do not want to"})

    monkeypatch.setattr(
        "roboglot.agent.providers.factory.make_provider",
        lambda *a, **k: FakeProvider(strategy=give_up),
    )
    result = runner.invoke(app, ["run", "team-hello", *_run_args(tmp_path)])
    assert result.exit_code == 1, result.output
    assert "members 0/2 succeeded" in result.output
    assert "I do not want to" in result.output


def test_dry_run_reaches_a_pilot_team(tmp_path: Path) -> None:
    result = runner.invoke(app, ["run", "team-hello", "--dry-run", *_run_args(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "DRY RUN" in result.output
    assert _summary(tmp_path)["dry_run"] is True


def test_no_memory_leaves_nothing_behind(tmp_path: Path) -> None:
    result = runner.invoke(app, ["run", "team-hello", "--no-memory", *_run_args(tmp_path)])
    assert result.exit_code == 0, result.output
    assert not (tmp_path / "mem").exists()


# ── what the summary wrote down ─────────────────────────────────────────────────────────


def test_a_team_writes_no_key_into_its_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A team's summary carries the command, so it carries the thing a solo run redacts out of
    `run_start`. A run directory is pasted into issues, and a key that reaches one has to be
    rotated.

    `sys.argv` is pytest's own here, because `CliRunner` calls the command in-process while
    the record is built from the arguments the process was given.
    """
    glotfile = "team-hello"
    argv = [glotfile, "--api-key", "sk-never-in-a-run-directory"]
    monkeypatch.setattr(sys, "argv", ["roboglot", "run", *argv, *_run_args(tmp_path)])
    result = runner.invoke(app, ["run", *argv, *_run_args(tmp_path)])
    assert result.exit_code == 0, result.output
    summary = _summary(tmp_path)
    assert summary["command"][:3] == ["roboglot", "run", glotfile]
    # the flag itself stays: that a key was given is often the thing a reader is checking
    assert summary["command"][3:5] == ["--api-key", "***"]
    assert "sk-never-in-a-run-directory" not in json.dumps(summary)


# ── refusals ────────────────────────────────────────────────────────────────────────────


def test_a_number_for_a_team_is_refused_in_one_line(tmp_path: Path) -> None:
    """`--team 3` was the coordinator's count of robots, and a team is a stored one now."""
    result = runner.invoke(app, ["run", "team-hello", "--team", "3", *_run_args(tmp_path)])
    assert result.exit_code == 1
    said = " ".join(result.output.split())
    assert "--team 3 was a coordinator team of 3 robots, which Roboglot no longer has" in said
    assert "roboglot team list" in said
    assert not (tmp_path / "runs").exists(), "nothing was written"


def test_a_pilot_team_of_one_is_refused(tmp_path: Path) -> None:
    _seed(tmp_path, {"duck": "microduck:mock"}, {"solo": ["duck"]})
    result = runner.invoke(app, ["run", "team-hello", "--team", "solo", *_run_args(tmp_path)])
    assert result.exit_code == 1, result.output
    assert "2 to 8 members" in result.output


def test_an_unknown_team_says_where_to_find_the_stored_ones(tmp_path: Path) -> None:
    result = runner.invoke(app, ["run", "team-hello", "--team", "ghost", *_run_args(tmp_path)])
    assert result.exit_code == 1
    assert "no team called 'ghost'" in result.output
    assert "roboglot team list" in result.output


def test_a_dangling_team_refuses_before_it_runs(tmp_path: Path) -> None:
    _seed(
        tmp_path,
        {"duck": "microduck:mock", "arm": "lerobot:mock"},
        {"pair": ["duck", "arm"]},
    )
    # by hand, which is the only way to make a team dangle: `robot remove` refuses
    (tmp_path / "robots.json").write_text(
        json.dumps({"version": 1, "robots": {"duck": {"spec": "microduck:mock"}}}),
        encoding="utf-8",
    )
    result = runner.invoke(app, ["run", "team-hello", "--team", "pair", *_run_args(tmp_path)])
    assert result.exit_code == 1, result.output
    assert "no longer registered" in result.output
    assert not (tmp_path / "runs").exists(), "nothing was written"


def test_a_stored_team_brings_its_own_robots(tmp_path: Path) -> None:
    _seed(
        tmp_path,
        {"duck": "microduck:mock", "arm": "lerobot:mock"},
        {"pair": ["duck", "arm"]},
    )
    for extra in (["--robot", "duck"], ["--robots", "a=microduck:mock,b=lerobot:mock"]):
        result = runner.invoke(
            app, ["run", "team-hello", "--team", "pair", *extra, *_run_args(tmp_path)]
        )
        assert result.exit_code == 1, result.output
        assert "brings its own robots" in result.output


@pytest.mark.parametrize("where", ["--robots", "robots:", "--robot NAME", "--robot SPEC"])
def test_a_robot_given_for_no_member_is_refused_before_anything_connects(
    tmp_path: Path, where: str
) -> None:
    """`ghost` is no member of `team-hello`, and the robot given for it was passed over: the run
    went on as if it had never been typed, and the member it was meant for took whatever this
    machine had. So was a registered robot named with `--robot` that is no member, and a bare
    `--robot lerobot:mock`, which names a body and no member at all. Each is refused now, naming
    what was given and the members, with nothing written; the bare one also says how to give a
    body to a member by name."""
    named = "ghost=lerobot:mock"
    if where == "--robots":
        args = ["team-hello", "--robots", "duck=microduck:mock,arm=lerobot:mock,ghost=lerobot:mock"]
    elif where == "--robot NAME":
        _seed(tmp_path, {"ghost": "lerobot:mock"}, {})
        args = ["team-hello", "--robot", "ghost"]
    elif where == "--robot SPEC":
        args, named = ["team-hello", "--robot", "lerobot:mock"], "--robot lerobot:mock"
    else:
        bundled = Path(__file__).resolve().parents[1] / "tasks" / "team-hello.glot"
        text = bundled.read_text(encoding="utf-8")
        task = tmp_path / "team-ghost.glot"
        task.write_text(
            text.replace("  arm: lerobot:mock\n", "  arm: lerobot:mock\n  ghost: lerobot:mock\n"),
            encoding="utf-8",
        )
        args = [str(task)]
    result = runner.invoke(app, ["run", *args, *_run_args(tmp_path)])
    assert result.exit_code == 1, result.output
    said = " ".join(result.output.split())
    assert f"{named} names no member of this team, whose members are duck, arm" in said, said
    if where == "--robot SPEC":
        assert "--robots duck=lerobot:mock" in said, said
    assert "Traceback" not in result.output
    assert not (tmp_path / "runs").exists(), "nothing was written"


def test_a_gated_verb_needs_yes(tmp_path: Path) -> None:
    glot = tmp_path / "gated.glot"
    glot.write_text(
        "---\nglot: 1\nname: gated\ndescription: d\nverbs:\n  allow: [report_state, stop, pick]\n"
        "  confirm: [pick]\nsuccess: [x]\nrequires: [report_state]\n"
        "team:\n  members: [duck, arm]\n  allocation:\n    method: pilots\n"
        "---\n# Task\nx\n",
        encoding="utf-8",
    )
    _seed(
        tmp_path,
        {"duck": "microduck:mock", "arm": "lerobot:mock"},
        {"pair": ["duck", "arm"]},
    )
    refused = runner.invoke(app, ["run", str(glot), "--team", "pair", *_run_args(tmp_path)])
    assert refused.exit_code == 1, refused.output
    assert "cannot prompt y/N per member" in refused.output


BLIND_TEAM = (
    "---\nglot: 1\nname: blind-team\ndescription: d\nverbs:\n"
    "  allow: [move, report_state, stop]\nsuccess: [x]\nrequires: [report_state]\n"
    "team:\n  members: [duck, buddy]\n  allocation:\n    method: pilots\n"
    "robots:\n  duck: open_duck:mock\n  buddy: microduck:mock\n---\n# Task\nx\n"
)
"""A team task that can make its members walk, which no shipped team task does, on an Open Duck
and a Microduck."""


def _fall_blind(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every Open Duck mock the run builds reports itself as its bridge backend does:
    `fall_detection` a constant False, beside a manifest with no `stand_up`."""
    from roboglot_open_duck.mock import OpenDuckMock

    real = OpenDuckMock.get_state

    async def blind(self: OpenDuckMock) -> Any:
        state = await real(self)
        return state.model_copy(update={"extras": {**state.extras, "fall_detection": False}})

    monkeypatch.setattr(OpenDuckMock, "get_state", blind)


def _rows(run_dir: Path, member: str) -> list[dict[str, Any]]:
    from roboglot.agent.transcript import Transcript

    return list(Transcript.read(run_dir / "robots" / member / "transcript.jsonl"))


def _prompts(rows: list[dict[str, Any]]) -> list[tuple[str, bool]]:
    return [(row["what"], row["answer"]) for row in rows if row["kind"] == "prompt"]


@pytest.mark.parametrize(("typed", "watching"), [("y", True), ("n", False)], ids=["yes", "no"])
def test_a_team_asks_the_terminal_about_each_member_that_cannot_see_a_fall(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, typed: str, watching: bool
) -> None:
    """A team put that question to nobody, and a duck that cannot see a fall walked with a note in
    its transcript. The person at the terminal is asked now, as a solo run asks, with the member's
    name on the warning and in the question, through the team's kill switch as a solo run's is
    through its own. The answer is in that member's transcript, and a no ends the team before any
    body moved. `_can_prompt` is forced, since a `CliRunner` has no terminal under it."""
    from roboglot import cli as cli_mod

    _fall_blind(monkeypatch)
    monkeypatch.setattr(cli_mod, "_can_prompt", lambda: True)
    through_the_switch: list[bool] = []
    ask = cli_mod._ask

    def spied(question: str) -> Any:
        through_the_switch.append(cli_mod._SWITCH is not None)
        return ask(question)

    monkeypatch.setattr(cli_mod, "_ask", spied)
    task = tmp_path / "blind-team.glot"
    task.write_text(BLIND_TEAM, encoding="utf-8")
    result = runner.invoke(
        app, ["run", str(task), *_run_args(tmp_path)], input=f"{typed}\n", env={"COLUMNS": "200"}
    )
    assert "Traceback" not in result.output, result.output
    assert "duck: nothing on this robot detects a fall" in " ".join(result.output.split())
    terminal = (_newest(tmp_path) / "terminal.txt").read_text(encoding="utf-8")
    asked = [line for line in terminal.splitlines() if "[y/N]" in line]
    assert asked == [f"Are you watching duck right now? [y/N]: {typed}"], asked
    assert through_the_switch == [True]
    duck, buddy = (_rows(_newest(tmp_path), name) for name in ("duck", "buddy"))
    assert _prompts(duck) == [("acknowledge", watching)]
    assert _prompts(buddy) == []
    if watching:
        assert result.exit_code == 0, result.output
        return
    assert result.exit_code == 1, result.output
    assert not [row for row in duck + buddy if row["kind"] == "verb_start"], "nothing moved"
    said = " ".join(result.output.split())
    assert "duck aborted: nobody confirmed they were watching" in said, said
    assert {row["outcome"] for row in _summary(tmp_path)["per_member"].values()} == {"aborted"}


def test_the_yes_you_typed_answers_every_member_that_cannot_see_a_fall_as_on_a_solo_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--yes` on a solo run says you are watching: the warning is noted, nobody is asked and no
    `prompt` row is written. The team's own `yes`, its standing answer to its confirm gates,
    answers nothing about a fall, so the `--yes` you typed is handed in as the answer to every
    member's question at once, and the team runs as a solo run would."""
    from roboglot import cli as cli_mod

    _fall_blind(monkeypatch)
    monkeypatch.setattr(cli_mod, "_can_prompt", lambda: True)
    task = tmp_path / "blind-team.glot"
    task.write_text(BLIND_TEAM, encoding="utf-8")
    result = runner.invoke(app, ["run", str(task), "--yes", *_run_args(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "[y/N]" not in (_newest(tmp_path) / "terminal.txt").read_text(encoding="utf-8")
    duck = _rows(_newest(tmp_path), "duck")
    assert _prompts(duck) == []
    assert any(row["kind"] == "note" and "no way to get up" in row["text"] for row in duck)
    assert _summary(tmp_path)["outcome"] == "success"


class _Console:
    """A console read a key at a time, as `roboglot.keys` hands the key thread one, fed by the
    test: one key a read, a look that finds nothing between keys, and "" once it is closed."""

    def __init__(self) -> None:
        import queue

        self.keys: queue.Queue[str] = queue.Queue()

    def read(self, nothing_waiting: Any = lambda _at: None) -> str:
        import queue
        import time

        while True:
            try:
                return self.keys.get(timeout=0.02)
            except queue.Empty:
                nothing_waiting(time.monotonic())

    def erase(self) -> None:
        pass

    def newline(self) -> None:
        pass

    def close(self) -> None:
        self.keys.put("")


async def test_q_pressed_while_a_member_is_asked_finds_every_body_still_and_ends_the_team(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The team's header says `Ctrl-C or q stops every robot`, and while a question is up the key
    thread hands every key to its answer, `q` among them (`KillSwitch._watch_keys`). Asked while
    the other members moved, the duck's question took the `q` and the buddy went on walking until
    Enter (the refuter's test, `stops_the_members_that_move`, fitted to the team that waits). Now
    nothing moves while a question is up, and the `q` with the Enter after it is the duck's no,
    which ends the team before any body moved. The switch is the real `KillSwitch` with its real
    key thread over a console stand-in, the question the CLI's own, as `roboglot run` wires them.
    At a console the question holds the event loop, as a solo run's does, so the person here is a
    thread."""
    import asyncio
    import threading

    from roboglot import cli as cli_mod
    from roboglot.adapters.factory import RobotSpec
    from roboglot.agent.providers.base import ToolCall
    from roboglot.agent.providers.fake import FakeProvider
    from roboglot.glotfile.parser import parse_glot_text
    from roboglot.safety import ASK_POLL_S, KillSwitch
    from roboglot.team.pilots import roster_from_specs, run_pilot_team
    from tests.support import until

    _fall_blind(monkeypatch)
    master = asyncio.Event()
    ks = KillSwitch(master)
    ks._loop = asyncio.get_running_loop()
    console = _Console()
    ks._keys = console  # type: ignore[assignment]
    ks._thread = threading.Thread(target=ks._watch_keys, name="test-keys", daemon=True)
    ks._thread.start()
    monkeypatch.setattr(cli_mod, "_SWITCH", ks)
    monkeypatch.setattr(cli_mod, "_can_prompt", lambda: True)
    monkeypatch.setattr("roboglot.safety.in_the_background", lambda _stream: False)
    events: list[tuple[str, str]] = []
    seen: dict[str, Any] = {}

    def person() -> None:
        until(ks._line_wanted.is_set, 60, what="the duck's question")
        seen["before"] = [name for name, kind in events if kind == "verb_start"]
        console.keys.put("q")
        # forty of the question's own looks: far longer than the key thread takes to fire
        threading.Event().wait(40 * ASK_POLL_S)
        seen["presses"] = ks.presses
        seen["after"] = [name for name, kind in events if kind == "verb_start"]
        console.keys.put("\r")

    walk = ToolCall(name="move", arguments={"vx": 0.1, "wz": 0.0, "duration_s": 0.5})
    verdict = ToolCall(name="assess_task", arguments={"verdict": "feasible", "reason": "flat"})
    done = ToolCall(name="declare_success", arguments={"reason": "walked"})
    providers = {
        name: FakeProvider(script=[verdict, *[walk] * 20, done]) for name in ("duck", "buddy")
    }
    asking = threading.Thread(target=person, name="test-person", daemon=True)
    asking.start()
    try:
        result = await asyncio.wait_for(
            run_pilot_team(
                parse_glot_text(BLIND_TEAM),
                roster_from_specs(
                    {
                        "duck": RobotSpec("open_duck", "mock", "duck"),
                        "buddy": RobotSpec("microduck", "mock", "buddy"),
                    }
                ),
                providers=providers,
                runs_dir=tmp_path,
                abort=master,
                view=lambda name: lambda event: events.append((name, str(event.kind))),
                acknowledge=cli_mod._member_acknowledge_prompt,
            ),
            timeout=120,
        )
    finally:
        console.close()
        asking.join(timeout=60)
    assert seen.get("before") == [] and seen.get("after") == [], f"what moved meanwhile: {seen}"
    assert seen["presses"] == 0, "the q went into the answer, as at any question"
    assert not [name for name, kind in events if kind == "verb_start"], "and nothing moved after"
    assert result.outcome == "aborted", result.reason
    assert result.reason.startswith("duck aborted: nobody confirmed they were watching"), (
        result.reason
    )
    assert {row["outcome"] for row in result.per_member.values()} == {"aborted"}


ASKED_THEN_STOPPED = """
import asyncio, sys
from roboglot_open_duck.mock import OpenDuckMock
_state = OpenDuckMock.get_state
async def _blind(self):
    state = await _state(self)
    return state.model_copy(update={"extras": {**state.extras, "fall_detection": False}})
OpenDuckMock.get_state = _blind
from roboglot import cli
asked = asyncio.Event()
_prompt = cli._member_acknowledge_prompt
def _spied(member, why):
    asked.set()
    return _prompt(member, why)
_spied.asks_a_person = _prompt.asks_a_person
cli._member_acknowledge_prompt = _spied
from roboglot.team import pilots
_make = pilots.make_adapter
class _Late:
    name = "microduck"
    backend = "mock"
    async def connect(self):
        await asked.wait()
        raise RuntimeError("no socket")
    async def close(self):
        return None
    def now(self):
        return 0.0
pilots.make_adapter = lambda spec, **kw: _Late() if spec.name == "buddy" else _make(spec, **kw)
cli.app(args=["run", sys.argv[1], "--llm", "fake", "--no-memory", "--no-log", "--runs-dir",
              sys.argv[2]], prog_name="roboglot")
"""
"""The real CLI on `BLIND_TEAM`, its duck made to read as its bridge does and its buddy given a
transport whose connect fails once the duck's question has been put, which stops the team."""

CHILD_EXIT_S = 120.0
"""How long the child has to end: a bound on a run that never ends, not a figure any test checks."""


def _asked_then_stopped(tmp_path: Path) -> tuple[int, str]:
    """`ASKED_THEN_STOPPED` in a child Python whose stdin is a pipe held open with nothing
    written, as a supervisor or a CI step holds one: its exit code, and its stdout and stderr
    together, as they were written."""
    import os
    import subprocess

    task = tmp_path / "blind-team.glot"
    task.write_text(BLIND_TEAM, encoding="utf-8")
    out_file = tmp_path / "out.txt"
    root = Path(__file__).resolve().parents[1]
    env = {**os.environ, "PYTHONPATH": str(root), "PYTHONDONTWRITEBYTECODE": "1"}
    # files rather than `communicate`, which would close stdin first and so answer with its end
    with out_file.open("wb") as sink:
        child = subprocess.Popen(
            [sys.executable, "-X", "utf8", "-c", ASKED_THEN_STOPPED, str(task), str(tmp_path)],
            stdin=subprocess.PIPE,
            stdout=sink,
            stderr=subprocess.STDOUT,
            cwd=tmp_path,
            env=env,
        )
        try:
            child.wait(timeout=CHILD_EXIT_S)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()
            raise AssertionError(
                "the process never ended with the question's pipe open:\n"
                + out_file.read_text("utf-8", "replace")[-3000:]
            ) from None
        finally:
            assert child.stdin is not None
            child.stdin.close()
    return child.returncode, out_file.read_text("utf-8", "replace")


def test_a_question_left_up_on_an_open_pipe_lets_the_team_end_and_the_process_exit(
    tmp_path: Path,
) -> None:
    """A question read with `input()` on a thread of its own was still blocked in it, holding
    stdin's lock, when another member's failed connect ended the team, and Python 3.11 aborted at
    shutdown with `Fatal Python error: _enter_buffered_busy`, exit code 0xC0000005 in place of 1
    (the refuter's test, fitted to a buddy that fails once the question is up rather than at a
    time). The question is read by the kill switch's one reader of a stream now, which the stop
    lets go of, and the process ends with its own exit code on every Python."""
    code, text = _asked_then_stopped(tmp_path)
    assert "Are you watching duck right now?" in text, "the question never came up\n" + text
    assert "went unanswered" not in text, "stdin ended: the pipe was not held open\n" + text
    assert "Fatal Python error" not in text, f"exit code {code}\n{text[-1200:]}"
    assert "Traceback" not in text, text[-3000:]
    assert code == 1, (code, text[-3000:])


def test_a_question_the_team_takes_back_is_taken_back_on_the_screen(tmp_path: Path) -> None:
    """The team's stop took the duck's question back and said nothing of it, so the question was
    left open on the screen and the verdict was printed on the end of its line, `[y/N]: ┌─ ✗
    ERROR`. Its line is ended now, and a line says it was taken back, before the verdict."""
    from roboglot.cli import TAKEN_BACK

    code, text = _asked_then_stopped(tmp_path)
    asked = [line for line in text.splitlines() if "Are you watching duck right now?" in line]
    assert len(asked) == 1 and asked[0].rstrip().endswith("[y/N]:"), (asked, text)
    flat = " ".join(text.split())  # as the terminal folded it, unfolded
    said = flat.find(TAKEN_BACK.format(member="duck"))
    assert -1 < flat.find("Are you watching duck") < said < flat.find("buddy error"), text
    assert code == 1, (code, text[-3000:])


def test_a_stored_team_refuses_the_endpoint_flags_it_would_have_dropped(
    tmp_path: Path,
) -> None:
    _seed(
        tmp_path,
        {"duck": "microduck:mock", "arm": "lerobot:mock"},
        {"pair": ["duck", "arm"]},
    )
    for extra in (["--address", "tcp://x:1"], ["--token", "t"], ["--camera-url", "http://x"]):
        result = runner.invoke(
            app, ["run", "team-hello", "--team", "pair", *extra, *_run_args(tmp_path)]
        )
        assert result.exit_code == 1, result.output
        assert "takes every member's address" in result.output


def test_validate_answers_in_one_line_when_the_registry_is_broken(tmp_path: Path) -> None:
    """`validate --robot <name>` reads robots.json, and a broken one must not be a traceback."""
    (tmp_path / "robots.json").write_text("{ not json", encoding="utf-8")
    result = runner.invoke(app, ["validate", "hello-world", "--robot", "duck-a", *_reg(tmp_path)])
    assert result.exit_code == 1, result.output
    assert "not valid JSON" in result.output
    assert "Traceback" not in result.output
