"""Play: a task can give the pilot a part to play, and nothing else changes.

`play: true` in a `duck: 4` task file, or `--play` on the command line, adds one section to the
system prompt, `## Playing a part`, directly before the task. The tools, every gate and every
other byte of the prompt are what they were, and a run that does not ask for it reads exactly
the prompt it read before there was such a thing. These tests hold each half of that, on every
body quackd ships, and the places a played run is said and recorded.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from quackd import cli
from quackd.adapters.factory import ADAPTER_NAMES, BACKENDS, RobotSpec, describe, registry_for
from quackd.agent.loop import RunConfig, resolve_play, run_duck
from quackd.agent.prompts import (
    PLAY_HEADING,
    build_system_prompt,
    play_paragraphs,
    play_section,
)
from quackd.agent.providers.base import Exchange, ProviderTurn, ToolCall
from quackd.agent.transcript import Transcript
from quackd.duckfile.parser import DuckParseError, duck_from_goal, load_duck, parse_duck_text
from quackd.duckfile.schema import DuckFile
from quackd.flock.pilots import trim_contract
from quackd.log import play_row
from quackd.mcp_server import build_server
from quackd.transport.mock import MockTransport
from quackd_lerobot import LeRobotAdapter
from quackd_lerobot.mock import REST, LeRobotMock

SPECS = [RobotSpec(a, b) for a in ADAPTER_NAMES for b in BACKENDS[a]]
runner = CliRunner()

PLAYED = """---
duck: 4
name: t-rex
description: Be a T-rex.
verbs:
  allow: [report_state, move_joints, gripper, stop]
success:
  - You have shown it with the arm.
play: true
---
# Task
You are a T-rex. Show me.
"""

HELLO_FLOCK = Path("ducks/flock-hello.duck").read_text(encoding="utf-8")


def _played_flock(method: str) -> str:
    return (
        HELLO_FLOCK.replace("duck: 1", "duck: 4", 1)
        .replace("method: pilots", f"method: {method}", 1)
        .replace("persona:", "play: true\npersona:", 1)
    )


def _played(duck: DuckFile) -> DuckFile:
    """The same task file, saying `play: true` at `duck: 4`."""
    fm = duck.frontmatter.model_copy(update={"duck": 4, "play": True})
    return duck.model_copy(update={"frontmatter": fm})


def _section(prompt: str) -> str:
    start = prompt.index(f"\n{PLAY_HEADING}\n")
    return prompt[start : prompt.index("\n## Task file:", start)]


def _render(spec: RobotSpec, duck: DuckFile, **kw: Any) -> str:
    manifest = describe(spec)
    registry = registry_for(spec, manifest)
    allow = [n for n in duck.frontmatter.verbs.allow if n in registry]
    return build_system_prompt(
        duck,
        [registry.view(n) for n in allow],
        spec.backend,
        manifest=manifest,
        adapter=spec.adapter,
        sees=True,
        **kw,
    )


def _goal(spec: RobotSpec, goal: str = "You are a T-rex. Show me.") -> DuckFile:
    registry = registry_for(spec, describe(spec))
    return duck_from_goal(
        goal, sorted(v.name for v in registry.verbs() if v.safety_class == "safe")
    )


# ── the task file ───────────────────────────────────────────────────────────────────────


def test_a_duck_4_file_can_give_its_pilot_a_part() -> None:
    assert parse_duck_text(PLAYED, path="t.duck").frontmatter.play is True


def test_play_needs_duck_4() -> None:
    with pytest.raises(DuckParseError, match="play needs duck: 4"):
        parse_duck_text(PLAYED.replace("duck: 4", "duck: 3"), path="t.duck")


@pytest.mark.parametrize("version", [0, 1, 2, 3, 4])
def test_play_false_parses_at_every_version(version: int) -> None:
    """Read off the value: a pilot flock dumps every member's contract and validates it again,
    so a dumped `play: false` must pass at the version the file was written at."""
    text = PLAYED.replace("duck: 4", f"duck: {version}").replace("play: true", "play: false")
    assert parse_duck_text(text, path="t.duck").frontmatter.play is False


def test_an_auction_flock_cannot_play_and_a_pilot_flock_can() -> None:
    with pytest.raises(DuckParseError, match="an auction flock has none"):
        parse_duck_text(_played_flock("auction"), path="flock-hello.duck")
    assert parse_duck_text(_played_flock("pilots"), path="flock-hello.duck").frontmatter.play


def test_a_trimmed_pilot_flock_contract_keeps_its_play() -> None:
    arm = describe(RobotSpec("lerobot", "mock"))
    plain = load_duck("ducks/flock-hello.duck").frontmatter
    assert trim_contract(plain, arm).play is False, "a dumped play: false at duck: 1 still passes"
    played = parse_duck_text(_played_flock("pilots"), path="flock-hello.duck").frontmatter
    assert trim_contract(played, arm).play is True


# ── the prompt ──────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("spec", SPECS, ids=lambda s: f"{s.adapter}:{s.backend}")
def test_a_run_that_does_not_play_reads_the_prompt_it_always_did(spec: RobotSpec) -> None:
    """`play=False` is the default, and it adds nothing: the call without the keyword, which is
    every caller written before there was one, renders the same bytes."""
    duck = _goal(spec)
    manifest = describe(spec)
    registry = registry_for(spec, manifest)
    verbs = [registry.view(n) for n in duck.frontmatter.verbs.allow if n in registry]
    without = build_system_prompt(
        duck, verbs, spec.backend, manifest=manifest, adapter=spec.adapter, sees=True
    )
    assert _render(spec, duck, play=False) == without
    assert PLAY_HEADING not in without


@pytest.mark.parametrize("spec", SPECS, ids=lambda s: f"{s.adapter}:{s.backend}")
def test_a_played_prompt_is_the_plain_one_with_one_section_before_the_task(
    spec: RobotSpec,
) -> None:
    duck = _goal(spec)
    plain, played = _render(spec, duck), _render(spec, duck, play=True)
    assert played.count(f"\n{PLAY_HEADING}\n") == 1
    section = _section(played)
    assert played.replace(section, "", 1) == plain, "the section is the only difference"
    assert played.index(section) + len(section) == played.index("\n## Task file:")
    assert "\n## " not in section[len(PLAY_HEADING) + 2 :], "one section, with no heading inside"


@pytest.mark.parametrize("spec", SPECS, ids=lambda s: f"{s.adapter}:{s.backend}")
def test_the_offered_verbs_are_the_same_played_or_not(spec: RobotSpec) -> None:
    """Four adapter tests read the offered verbs as the lines that start with a dash and a
    backtick, so no line of the section may start that way."""
    duck = _goal(spec)

    def offered(prompt: str) -> list[str]:
        return [line for line in prompt.splitlines() if line.startswith("- `")]

    assert offered(_render(spec, duck, play=True)) == offered(_render(spec, duck))


@pytest.mark.parametrize("still", [True, False])
@pytest.mark.parametrize("speaks", [True, False])
def test_the_section_says_remember_only_when_the_run_keeps_notes(still: bool, speaks: bool) -> None:
    assert "remember" not in play_section(still=still, speaks=speaks, memory=False).lower()
    assert "`remember`" in play_section(still=still, speaks=speaks, memory=True)
    mcp = "\n\n".join(play_paragraphs(still=still, speaks=speaks, memory=False, mcp=True))
    assert "remember" not in mcp.lower()


def test_a_played_hello_world_with_memory_off_never_says_remember() -> None:
    """The whole prompt, as `tests/test_memory.py` reads it for the same task unplayed."""
    played = _render(
        RobotSpec("microduck", "sim2d"), load_duck("ducks/hello-world.duck"), play=True
    )
    assert PLAY_HEADING in played
    assert "remember" not in played.lower()


def test_the_open_duck_prompt_still_names_no_verb_it_does_not_offer() -> None:
    """`tests/test_open_duck_adapter.py` bans these words from the whole prompt, played or not."""
    spec = RobotSpec("open_duck", "mock")
    played = _render(spec, load_duck("ducks/open-duck-lookout.duck"), play=True)
    assert PLAY_HEADING in played
    for word in ("kick", "grab", "stand_up"):
        assert word not in played, word


def test_a_body_that_cannot_travel_is_told_how_to_play_going_somewhere() -> None:
    arm = _section(
        _render(RobotSpec("lerobot", "mock"), _goal(RobotSpec("lerobot", "mock")), play=True)
    )
    duck = _section(
        _render(RobotSpec("microduck", "sim2d"), _goal(RobotSpec("microduck", "sim2d")), play=True)
    )
    assert "This body cannot travel" in arm and "mobility none" in arm
    assert "This body cannot travel" not in duck, "a body that walks plays going somewhere itself"


def test_the_lines_sentence_follows_what_the_body_can_say() -> None:
    duck = _section(
        _render(RobotSpec("microduck", "sim2d"), _goal(RobotSpec("microduck", "sim2d")), play=True)
    )
    arm = _section(
        _render(RobotSpec("lerobot", "mock"), _goal(RobotSpec("lerobot", "mock")), play=True)
    )
    assert "`say` gives them this body's own sound" in duck, "tones, not speech"
    assert "No verb here speaks" in arm


def test_the_section_reads_no_word_of_the_task() -> None:
    """The rule from #25: nothing quackd says to a pilot turns on what the task's words are."""
    spec = RobotSpec("lerobot", "mock")
    cat = _section(_render(spec, _goal(spec, "You are a cat."), play=True))
    switch = _section(_render(spec, _goal(spec, "Flip the switch off."), play=True))
    assert cat == switch


def test_the_section_keeps_what_the_gate_and_the_record_rest_on() -> None:
    """The sentences the part may never outweigh: a real result stays real and is judged as
    it would be without the part, nothing moves toward a person, and the record is true."""
    text = play_section(still=True, speaks=False, memory=False)
    assert "it has to happen for real" in text
    assert "A mime of it does not do it and is never reported as done." in text
    assert "into contact with a person, or toward one" in text and "leave it out" in text
    assert "A task that also asks for a real result this body cannot make is infeasible" in text
    assert "every need of anything the task asks to happen for real, in full" in text
    assert "The act may pretend, and the record never does." in text


# ── the run ─────────────────────────────────────────────────────────────────────────────


class Script:
    """Plays an exact list of calls, records what it was shown, then declares failure. Unlike
    the scripted pilot it answers no verdict of its own, so the gate is met as it stands."""

    name = "script"
    model = "test"
    supports_vision = False

    def __init__(self, calls: list[ToolCall] | None = None) -> None:
        self.calls = list(calls or [ToolCall(name="declare_success", arguments={"reason": "x"})])
        self.systems: list[str] = []
        self.tools: list[list[dict[str, Any]]] = []

    async def step(
        self, system: str, history: list[Exchange], tools: list[dict[str, Any]]
    ) -> ProviderTurn:
        self.systems.append(system)
        self.tools.append(tools)
        call = (
            self.calls.pop(0)
            if self.calls
            else ToolCall(name="declare_failure", arguments={"reason": "the script ended"})
        )
        return ProviderTurn(tool_calls=[call.model_copy(update={"id": f"s-{len(self.systems)}"})])


async def _run(duck: DuckFile, tmp_path: Path, **cfg: Any) -> tuple[Script, list[dict[str, Any]]]:
    provider = cfg.pop("provider", None) or Script()
    transport = cfg.pop("transport", None) or MockTransport()
    result = await run_duck(
        RunConfig(duck=duck, provider=provider, transport=transport, runs_dir=tmp_path, **cfg)
    )
    return provider, Transcript.read(result.run_dir / "transcript.jsonl")


async def test_play_from_the_run_reaches_the_prompt_and_the_record(
    hello_duck: DuckFile, tmp_path: Path
) -> None:
    provider, events = await _run(hello_duck, tmp_path, play=True)
    assert PLAY_HEADING in provider.systems[0]
    start = events[0]
    assert start["play"] == {"on": True, "from": "--play"}
    assert start["contract"]["play"] is False, "the contract is the file's own, at duck: 1"


async def test_no_play_turns_a_files_play_off(hello_duck: DuckFile, tmp_path: Path) -> None:
    provider, events = await _run(_played(hello_duck), tmp_path, play=False)
    assert PLAY_HEADING not in provider.systems[0]
    assert events[0]["play"] == {"on": False, "from": "--no-play"}


async def test_without_a_flag_the_task_file_decides(hello_duck: DuckFile, tmp_path: Path) -> None:
    played, events = await _run(_played(hello_duck), tmp_path / "a")
    assert PLAY_HEADING in played.systems[0]
    assert events[0]["play"] == {"on": True, "from": "task file"}
    plain, events = await _run(hello_duck, tmp_path / "b")
    assert PLAY_HEADING not in plain.systems[0]
    assert events[0]["play"] == {"on": False, "from": "task file"}


async def test_play_and_max_steps_both_hold(hello_duck: DuckFile, tmp_path: Path) -> None:
    provider, events = await _run(hello_duck, tmp_path, play=True, max_steps=7)
    assert PLAY_HEADING in provider.systems[0] and "Budgets: 7 steps" in provider.systems[0]
    assert events[0]["contract"]["budgets"]["max_steps"] == 7


async def test_the_tools_are_the_same_played_or_not(hello_duck: DuckFile, tmp_path: Path) -> None:
    played, _ = await _run(hello_duck, tmp_path / "a", play=True)
    plain, _ = await _run(hello_duck, tmp_path / "b")
    assert played.tools[0] == plain.tools[0]


ARM_TASK = """---
duck: {version}
name: arm-part
description: Play a part with the arm.
verbs:
  allow: [report_state, move_joints, stop]
success:
  - The arm has shown it.
{play}---
# Task
You are a crane. Show me.
"""


async def test_the_gate_says_the_same_words_played_or_not(tmp_path: Path) -> None:
    """A part changes what the pilot is told, never what the executor answers: a move before
    the verdict is refused in the same words, and so is a feasible verdict whose needs the
    arm's own datasheet does not meet."""
    calls = [
        ToolCall(name="move_joints", arguments={"positions": {"elbow_flex": 10.0}}),
        ToolCall(
            name="assess_task",
            arguments={"verdict": "feasible", "reason": "x", "needs": {"mobility": "legged"}},
        ),
        ToolCall(name="declare_failure", arguments={"reason": "done"}),
    ]
    answers = []
    for version, play in ((1, ""), (4, "play: true\n")):
        duck = parse_duck_text(ARM_TASK.format(version=version, play=play), path="arm.duck")
        _provider, events = await _run(
            duck,
            tmp_path / f"v{version}",
            provider=Script(list(calls)),
            transport=LeRobotAdapter(LeRobotMock(rest_pose=dict(REST))),
        )
        assert events[0]["play"]["on"] is bool(play)
        refused = next(e for e in events if e["kind"] == "verb" and e.get("name") == "move_joints")
        objected = next(e for e in events if e["kind"] == "assess")
        answers.append((refused.get("summary"), objected.get("summary")))
    assert answers[0] == answers[1]
    assert "no feasibility verdict has been recorded" in str(answers[0][0])
    assert "mobility = legged (has none)" in str(answers[0][1])


# ── MCP ─────────────────────────────────────────────────────────────────────────────────


async def test_a_file_that_does_not_play_loads_over_mcp_as_it_always_did(tmp_path: Path) -> None:
    _server, session = build_server(
        LeRobotAdapter(LeRobotMock(rest_pose=dict(REST))), heartbeat_period_s=0.05
    )
    await session.connect()
    try:
        result = session.load("ducks/lerobot-lookout.duck")
    finally:
        await session.close()
    assert result["ok"], result
    assert "play" not in result and "play" not in result["contract"]
    assert "part to play" not in result["note"]


async def test_a_played_file_hands_the_mcp_pilot_its_part(tmp_path: Path) -> None:
    path = tmp_path / "t-rex.duck"
    path.write_text(PLAYED, encoding="utf-8")
    _server, session = build_server(
        LeRobotAdapter(LeRobotMock(rest_pose=dict(REST))), heartbeat_period_s=0.05
    )
    await session.connect()
    try:
        result = session.load(str(path))
    finally:
        await session.close()
    assert result["ok"], result
    assert result["contract"]["play"] is True
    assert "robot_assess_task" in result["play"]
    assert "not whether it is the thing it plays" in result["play"]
    assert "This body cannot travel" in result["play"], "the arm's own facts"
    assert "assess_task`" not in result["play"], "the MCP tool's name, not the run's"
    assert "read the play field before you assess it" in result["note"]


async def test_a_session_with_no_manifest_loads_a_played_file(tmp_path: Path) -> None:
    path = tmp_path / "t-rex.duck"
    path.write_text(
        PLAYED.replace("[report_state, move_joints, gripper, stop]", "[quack, stop]"),
        encoding="utf-8",
    )
    _server, session = build_server(MockTransport(), heartbeat_period_s=0.05)
    await session.connect()
    try:
        result = session.load(str(path))
    finally:
        await session.close()
    assert result["ok"], result
    assert "This body cannot travel" not in result["play"], "no manifest reads as a body that moves"


# ── the command line ────────────────────────────────────────────────────────────────────


def _flat(text: str) -> str:
    return " ".join(re.sub(r"\x1b\[[0-9;]*m", "", text).split())


def test_play_is_a_flag_of_run_record_and_preflight_and_not_serve_mcp() -> None:
    """Over MCP the client's own prompt frames the pilot and a played file says so itself; a
    session-wide flag would make every file loaded in it play."""
    for command in (cli.run, cli.record, cli.preflight):
        assert "play" in inspect.signature(command).parameters, command.__name__
    assert "play" not in inspect.signature(cli.serve_mcp).parameters


def test_a_played_goal_run_says_so_and_records_it(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    result = runner.invoke(
        cli.app,
        [
            "run",
            "--goal",
            "You are a T-rex. Show me.",
            "--robot",
            "lerobot:mock",
            "--llm",
            "fake",
            "--play",
            "--no-memory",
            "--max-steps",
            "4",
            "--runs-dir",
            str(runs),
        ],
    )
    out = _flat(result.output)
    assert "play on, from --play" in out, result.output
    transcript = next(runs.glob("*/transcript.jsonl"))
    start = Transcript.read(transcript)[0]
    assert start["play"] == {"on": True, "from": "--play"}
    assert PLAY_HEADING in start["system_prompt"]


def test_a_run_that_does_not_play_has_no_play_row(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    result = runner.invoke(
        cli.app,
        [
            "run",
            "hello-world",
            "--llm",
            "fake",
            "--no-memory",
            "--max-steps",
            "3",
            "--runs-dir",
            str(runs),
        ],
    )
    assert " play " not in f" {_flat(result.output)} ", result.output


def test_a_coordinator_flock_refuses_play(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    flag = runner.invoke(
        cli.app,
        [
            "run",
            "find-and-kick",
            "--flock",
            "2",
            "--llm",
            "fake",
            "--play",
            "--no-memory",
            "--runs-dir",
            str(runs),
        ],
    )
    assert flag.exit_code == 1
    assert "a coordinator flock has no pilot for --play or --no-play to reach" in _flat(flag.output)
    path = tmp_path / "hello.duck"
    hello = Path("ducks/hello-world.duck").read_text(encoding="utf-8")
    played = re.sub(r"^duck: \d+$", "duck: 4", hello, count=1, flags=re.M)
    path.write_text(played.replace("persona:", "play: true\npersona:", 1), encoding="utf-8")
    filed = runner.invoke(
        cli.app,
        ["run", str(path), "--flock", "2", "--llm", "fake", "--no-memory", "--runs-dir", str(runs)],
    )
    assert filed.exit_code == 1
    out = _flat(filed.output)
    assert "gives its pilot a part to play, and a coordinator flock has no pilot to play it" in out
    assert not runs.exists() or not any(runs.iterdir())


# ── what the header says ────────────────────────────────────────────────────────────────


def test_the_play_row_says_something_only_when_there_is_something_to_say() -> None:
    assert resolve_play(False, None) == (False, "task file")
    assert resolve_play(True, None) == (True, "task file")
    assert resolve_play(False, True) == (True, "--play")
    assert resolve_play(True, False) == (False, "--no-play")
    assert play_row(*resolve_play(True, None), file_play=True) == "on, from the task file"
    assert play_row(*resolve_play(False, True), file_play=False) == "on, from --play"
    assert play_row(*resolve_play(True, False), file_play=True) == "off, from --no-play"
    assert play_row(*resolve_play(False, None), file_play=False) is None
    assert play_row(*resolve_play(False, False), file_play=False) is None, "nothing turned off"


def test_the_spec_shows_a_whole_v4_file_and_the_validate_it_passes(tmp_path: Path) -> None:
    """docs/reference/duck-spec.md shows a played file and the `quackd validate` it passes, so
    the file it shows has to be one and pass it."""
    page = (Path(__file__).parents[1] / "docs" / "reference" / "duck-spec.md").read_text(
        encoding="utf-8"
    )
    section = page.split("\n### `play` (v4)", 1)[1]
    found = re.search(r"```yaml\n(---\n.*?)```", section, flags=re.DOTALL)
    assert found, "the play section no longer shows a whole file"
    fm = parse_duck_text(found.group(1)).frontmatter
    assert fm.duck == 4 and fm.play is True
    duck = tmp_path / "t-rex.duck"
    duck.write_text(found.group(1), encoding="utf-8")
    assert "```console\n$ quackd validate t-rex.duck\n" in section, "the command it quotes"
    result = runner.invoke(cli.app, ["validate", str(duck)])
    assert result.exit_code == 0, result.output
    assert "1 file valid" in result.output and "1 file valid" in section


def test_the_example_files_parse_and_play_only_where_the_readme_says() -> None:
    """CI validates only `ducks/*.duck`, and nothing else reads the shoot's task files, so a hand
    edit could leave one unparseable, or playing at a version that cannot say so, without
    anything noticing. Every file parses, `play: true` comes only with `duck: 4`, and the
    examples README's counts are the numbers of files that play and that do not."""
    folder = Path(__file__).parents[1] / "docs" / "examples" / "lerobot"
    files = sorted(folder.glob("*/*.duck"))
    assert files, "no example files"
    played = []
    for path in files:
        fm = load_duck(str(path)).frontmatter
        if fm.play:
            assert fm.duck == 4, path
            played.append(path)
    readme = (folder / "README.md").read_text(encoding="utf-8")
    said = re.search(r"^(\d+) of these files give the pilot a part to play", readme, flags=re.M)
    assert said, "the examples README no longer says how many files play"
    assert int(said.group(1)) == len(played), f"{len(played)} files play"
    assert f"The other {len(files) - len(played)} keep every line" in readme
