"""Play: a task can give the pilot a part to play, and nothing else changes.

`play: true` in a `duck: 4` task file, or `--play` on the command line, adds one section to the
system prompt, `## Playing a part`, directly before the task. The tools, every gate and every
other byte of the prompt are what they were, and a run that does not ask for it reads exactly
the prompt it read before there was such a thing. These tests hold each half of that, on every
body quackd ships, and the places a played run is said and recorded.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from quackd import cli
from quackd.adapters.factory import ADAPTER_NAMES, BACKENDS, RobotSpec, describe, registry_for
from quackd.agent.loop import RunConfig, resolve_play, run_duck
from quackd.agent.prompts import PLAY_HEADING, build_system_prompt, play_paragraphs, play_section
from quackd.agent.providers.base import Exchange, ProviderTurn, ToolCall
from quackd.agent.transcript import Transcript
from quackd.duckfile.parser import DuckParseError, duck_from_goal, load_duck, parse_duck_text
from quackd.duckfile.schema import Budgets, DuckFile, DuckFrontmatter
from quackd.flock.pilots import trim_contract
from quackd.log import play_row
from quackd.mcp_server import build_server
from quackd.preflight import Rehearsal
from quackd.transport.mock import MockTransport
from quackd.verdict import MOVES_THE_BODY
from quackd_lerobot import LeRobotAdapter
from quackd_lerobot.mock import REST, LeRobotMock

from .conftest import DUCKS, REPO, help_text

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

WORDS = (
    "no",
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
    "ten",
    "eleven",
    "twelve",
    "thirteen",
    "fourteen",
    "fifteen",
)
"""A count as the examples README spells it in a sentence."""


def _played_flock(method: str) -> str:
    """flock-hello, at `duck: 4`, playing, with the allocation method given."""
    text = (DUCKS / "flock-hello.duck").read_text(encoding="utf-8")
    return (
        text.replace("duck: 1", "duck: 4", 1)
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
    """A `--goal` task as `quackd run` builds one for this body: every safe verb it has."""
    registry = registry_for(spec, describe(spec))
    return duck_from_goal(
        goal, sorted(v.name for v in registry.verbs() if v.safety_class == "safe")
    )


def _flat(text: str) -> str:
    return " ".join(re.sub(r"\x1b\[[0-9;]*m", "", text).split())


def _start(path: Path) -> dict[str, Any]:
    """The `run_start` of one transcript, wherever it sits in the file."""
    return next(e for e in Transcript.read(path) if e["kind"] == "run_start")


# ── the task file ───────────────────────────────────────────────────────────────────────


def test_a_duck_4_file_can_give_its_pilot_a_part() -> None:
    assert parse_duck_text(PLAYED, path="t.duck").frontmatter.play is True


@pytest.mark.parametrize("version", [0, 1, 2, 3])
def test_play_needs_duck_4(version: int) -> None:
    """Every version before the one that adds the key refuses it, with the fix in the words."""
    with pytest.raises(DuckParseError, match="play needs duck: 4"):
        parse_duck_text(PLAYED.replace("duck: 4", f"duck: {version}"), path="t.duck")


@pytest.mark.parametrize("version", [0, 1, 2, 3, 4])
def test_play_false_parses_at_every_version(version: int) -> None:
    """Read off the value: a pilot flock dumps every member's contract and validates it again,
    so a dumped `play: false` must pass at the version the file was written at."""
    text = PLAYED.replace("duck: 4", f"duck: {version}").replace("play: true", "play: false")
    assert parse_duck_text(text, path="t.duck").frontmatter.play is False


def test_an_auction_flock_cannot_play_and_a_pilot_flock_can() -> None:
    """An auction flock's members follow a plan and none of them is a pilot a part could
    reach, so a task file cannot ask for one there."""
    with pytest.raises(DuckParseError, match="an auction flock has none"):
        parse_duck_text(_played_flock("auction"), path="flock-hello.duck")
    assert parse_duck_text(_played_flock("pilots"), path="flock-hello.duck").frontmatter.play


def test_a_trimmed_pilot_flock_contract_keeps_its_play() -> None:
    arm = describe(RobotSpec("lerobot", "mock"))
    plain = load_duck(str(DUCKS / "flock-hello.duck")).frontmatter
    assert trim_contract(plain, arm).play is False, "a dumped play: false at duck: 1 still passes"
    played = parse_duck_text(_played_flock("pilots"), path="flock-hello.duck").frontmatter
    assert trim_contract(played, arm).play is True


# ── the prompt ──────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("spec", SPECS, ids=lambda s: f"{s.adapter}:{s.backend}")
def test_play_false_adds_nothing_to_the_prompt(spec: RobotSpec) -> None:
    """`play=False` adds nothing to the call without it, which is every caller written before
    there was a keyword. That this call renders what it did before play existed was checked
    once, by rendering every body before and after the change (ADR-0055): no golden pins it,
    because the prompt is meant to keep improving."""
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
    """The lasting form of the promise: whatever the prompt says, playing adds one section and
    changes nothing else, last before the task file and after every simulator note."""
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

    plain = offered(_render(spec, duck))
    assert plain, "the verb lines moved, so this comparison would hold with nothing in it"
    assert offered(_render(spec, duck, play=True)) == plain


@pytest.mark.parametrize("still", [True, False])
@pytest.mark.parametrize("voice", ["say", "quack", None])
@pytest.mark.parametrize("joints", [True, False])
def test_the_section_says_remember_only_when_the_run_keeps_notes(
    still: bool, voice: str | None, joints: bool
) -> None:
    facts: dict[str, Any] = {"still": still, "voice": voice, "joints": joints}
    assert "remember" not in play_section(**facts, memory=False).lower()
    assert "`remember`" in play_section(**facts, memory=True)
    mcp = "\n\n".join(play_paragraphs(**facts, memory=False, mcp=True))
    assert "remember" not in mcp.lower()
    kept = "\n\n".join(play_paragraphs(**facts, memory=True, mcp=True))
    assert "robot_remember" in kept


def test_a_played_hello_world_reads_what_its_run_keeps_and_says() -> None:
    """The whole prompt, as `tests/test_memory.py` reads it for the same task unplayed: no
    "remember" with memory off, and the note sentence with memory on, in a prompt that is still
    the plain one plus the section. hello-world allows `quack` and no `say`, so its part gives
    its lines with `quack`, which maps them to a tone as the Microduck's `say` does: the section
    follows the task's verbs, not the body's."""
    spec = RobotSpec("microduck", "sim2d")
    duck = load_duck(str(DUCKS / "hello-world.duck"))
    played = _render(spec, duck, play=True)
    assert PLAY_HEADING in played and "remember" not in played.lower()
    assert "`quack` gives them this body's own sound" in played
    assert "No verb here speaks" not in played and "`say` gives" not in played
    kept = _render(spec, duck, play=True, memory_text="")
    assert "A note you keep with `remember`" in _section(kept)
    assert kept.replace(_section(kept), "", 1) == _render(spec, duck, memory_text="")


def test_the_open_duck_prompt_still_names_no_verb_it_does_not_offer() -> None:
    """`tests/test_open_duck_adapter.py` bans these words from the whole prompt, played or not."""
    spec = RobotSpec("open_duck", "mock")
    played = _render(spec, load_duck(str(DUCKS / "open-duck-lookout.duck")), play=True)
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


@pytest.mark.parametrize(
    ("spec", "joints", "travel", "table"),
    [
        (RobotSpec("lerobot", "mock"), True, True, True),
        # the same arm on a task that leaves `move_joints` out sets no pose joint by joint
        (RobotSpec("lerobot", "mock"), False, True, False),
        (RobotSpec("microduck", "sim2d"), True, False, False),
        # an arm on a wheeled base sets its poses joint by joint too, and goes where it likes
        (RobotSpec("xlerobot", "mock"), True, False, True),
        # unless the task leaves its `move_joints` out
        (RobotSpec("xlerobot", "mock"), False, False, False),
        # a standing humanoid with no walk cannot travel, and its floor motions are confirm-gated
        # verbs of their own, offered here, not poses the pilot sets
        (RobotSpec("toddlerbot", "bridge"), True, True, False),
    ],
    ids=lambda v: f"{v.adapter}:{v.backend}" if isinstance(v, RobotSpec) else str(v),
)
def test_the_table_sentence_follows_move_joints_and_the_travel_one_follows_travel(
    spec: RobotSpec, joints: bool, travel: bool, table: bool
) -> None:
    """Each sentence reads one fact: whether the body can take itself anywhere, and whether
    the task allows `move_joints`, the verb that sets a pose joint by joint near the surface the
    body stands on. A walking body stands on its floor with every step. Every confirm-gated verb
    is allowed behind the gate, `move_joints` on a mobile manipulator among them, so no other
    verb the task allows turns the sentence on. A mimed blow stops before it reaches anything
    whatever verbs strike it."""
    registry = registry_for(spec, describe(spec))
    safe = {v.name for v in registry.verbs() if v.safety_class == "safe"}
    gated = [v.name for v in registry.verbs() if v.safety_class == "confirm"]
    if not joints:
        safe, gated = safe - {"move_joints"}, [g for g in gated if g != "move_joints"]
    duck = duck_from_goal("You are a T-rex. Show me.", sorted(safe | set(gated)), confirm=gated)
    section = _section(_render(spec, duck, play=True))
    assert ("This body cannot travel" in section) is travel
    assert ("stops short of the surface this body stands on" in section) is table
    assert "A blow, a stomp or a touch at something that is not here stops before it" in section


def test_the_lines_sentence_follows_the_verbs_the_task_allows() -> None:
    """Every safe verb of each body is offered here, so the Microduck's goal offers `say`, which
    the section names over `quack`, and the arm, which has neither, cannot speak at all."""
    duck = _section(
        _render(RobotSpec("microduck", "sim2d"), _goal(RobotSpec("microduck", "sim2d")), play=True)
    )
    arm = _section(
        _render(RobotSpec("lerobot", "mock"), _goal(RobotSpec("lerobot", "mock")), play=True)
    )
    assert "`say` gives them this body's own sound" in duck, "tones, not speech"
    assert "`quack` gives" not in duck, "one voice is named, and `say` first"
    assert "No verb here speaks" in arm


def test_the_section_reads_no_word_of_the_task() -> None:
    """The rule from #25: nothing quackd says to a pilot turns on what the task's words are."""
    spec = RobotSpec("lerobot", "mock")
    cat = _section(_render(spec, _goal(spec, "You are a cat."), play=True))
    switch = _section(_render(spec, _goal(spec, "Flip the switch off."), play=True))
    assert cat == switch


def test_the_section_keeps_what_the_gate_and_the_record_rest_on() -> None:
    """The sentences the part may never outweigh: a real result stays real and is judged as
    it would be without the part, nothing comes within a person's reach, and the record is true.

    The exception for a touch a task really asks for, a high five, is closed to an idiom: "twist
    my arm" taken at its word is a part played on the body itself, never a touch to make. And
    only such a touch is let toward a person within reach. A pose set with `move_joints` stops
    short of the table and of anything else here unless the task asks to touch it, since a part
    asks for bigger poses than a plain task and a bow aims at nothing, and a touch of the body on
    itself stops short whatever the task says, though a grip may still close on nothing."""
    text = play_section(still=True, voice=None, memory=False, joints=True)
    assert "it has to happen for real" in text
    assert "A mime of it does not do it and is never reported as done." in text
    assert "into contact with a person, or within reach of one" in text and "leave it out" in text
    assert "but an idiom taken at its word is never by itself such a request" in text
    assert "every move but a touch the task really asks for away from anyone" in text
    assert "or no camera picture shows this body and where a person near it would stand" in text
    assert "A blow, a stomp or a touch at something that is not here stops before" in text
    assert "lands only where the task asks for that touch for real" in text
    assert "a bow, a slump or a fold among them, stops short of the surface this body" in text
    assert "and of anything else here, unless the task asks it to touch that for real" in text
    assert "comes near either only in small, slow steps" in text
    assert "A touch of one part of this body on another, whatever the task asks, stops well" in text
    assert "a grip closing on nothing is no such touch" in text
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
    """`--play` over a file that does not play: the pilot reads the section, the record says
    the flag asked for it, and the contract stays the file's own, which cannot say so."""
    provider, events = await _run(hello_duck, tmp_path, play=True)
    assert PLAY_HEADING in provider.systems[0]
    start = events[0]
    assert start["play"] == {"on": True, "from": "--play"}
    assert start["contract"]["play"] is False, "the contract is the file's own, which does not play"


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


@pytest.mark.parametrize(
    ("file_play", "flag", "said"),
    [
        (False, True, {"on": True, "from": "--play"}),
        (True, False, {"on": False, "from": "--no-play"}),
        (True, None, {"on": True, "from": "task file"}),
    ],
    ids=["play", "no-play", "file"],
)
async def test_a_rehearsal_plays_as_a_run_of_the_same_file_would(
    tmp_path: Path, file_play: bool, flag: bool | None, said: dict[str, Any]
) -> None:
    """`preflight` builds its runs itself, so its flag has to reach each one's `RunConfig` the
    way `run`'s does. On the arm's mock rather than its simulator, which a machine without
    MuJoCo cannot build: what is checked is the record, not the physics."""
    lookout = (DUCKS / "lerobot-lookout.duck").read_text(encoding="utf-8")
    if file_play:
        lookout = re.sub(r"^duck: \d+$", "duck: 4\nplay: true", lookout, count=1, flags=re.M)
    path = tmp_path / "lerobot-lookout.duck"
    path.write_text(lookout, encoding="utf-8")
    spec = RobotSpec("lerobot", "mujoco")
    rehearsal = Rehearsal(
        spec=spec,
        manifest=describe(spec),
        pilot=lambda _duck: Script(),
        seeds=1,
        connect_cycles=0,
        runs_dir=tmp_path / "runs",
        play=flag,
    )
    rehearsal._build = lambda *_a: LeRobotAdapter(  # type: ignore[method-assign]
        LeRobotMock(rest_pose=dict(REST))
    )
    report = await rehearsal.file(str(path))
    assert not report.problems and len(report.runs) == 1, report.public()
    (transcript,) = (tmp_path / "runs").rglob("transcript.jsonl")
    start = _start(transcript)
    assert start["play"] == said
    assert (PLAY_HEADING in start["system_prompt"]) is said["on"]
    assert start["contract"]["play"] is file_play, "the file's own word, whatever the flag"


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


async def _load(transport: Any, path: str, **kwargs: Any) -> dict[str, Any]:
    _server, session = build_server(transport, heartbeat_period_s=0.05, **kwargs)
    await session.connect()
    try:
        return session.load(path)
    finally:
        await session.close()


async def test_a_file_that_does_not_play_loads_over_mcp_as_it_always_did() -> None:
    """The load result's keys, and the contract's, are exactly what they were before `play`
    existed: the key is left out of the dump for a file that does not play."""
    arm = LeRobotAdapter(LeRobotMock(rest_pose=dict(REST)))
    result = await _load(arm, str(DUCKS / "lerobot-lookout.duck"))
    assert result["ok"], result
    assert set(result) == {
        "ok",
        "robot",
        "name",
        "contract",
        "instructions",
        "datasheet_text",
        "note",
    }
    assert set(result["contract"]) == set(DuckFrontmatter.model_fields) - {"play"}
    assert "part to play" not in result["note"]


async def test_a_played_file_hands_the_mcp_pilot_its_part(tmp_path: Path) -> None:
    path = tmp_path / "t-rex.duck"
    path.write_text(PLAYED, encoding="utf-8")
    result = await _load(LeRobotAdapter(LeRobotMock(rest_pose=dict(REST))), str(path))
    assert result["ok"], result
    assert result["contract"]["play"] is True
    assert "robot_assess_task" in result["play"]
    assert "not whether it is the thing it plays" in result["play"]
    assert "This body cannot travel" in result["play"], "the arm's own facts"
    assert "stops short of the surface this body stands on" in result["play"], "move_joints"
    assert "assess_task`" not in result["play"], "the MCP tool's name, not the run's"
    assert "robot_remember" in result["play"], "this session keeps notes"
    assert "No verb this task allows speaks" in result["play"], "the arm offers neither voice"
    assert "This task may give the robot a part to play" in result["note"]
    assert "read the play field before you assess it" in result["note"]


def test_an_mcp_part_speaks_through_the_tool_that_reaches_its_voice() -> None:
    """Over MCP `say` has a tool of its own and `quack` is reached as any verb is, and either
    way the lines come after what the body did, as they do in a run's declare reason."""
    facts: dict[str, Any] = {"still": False, "memory": False, "joints": False, "mcp": True}
    said = "\n\n".join(play_paragraphs(voice="say", **facts))
    assert "robot_say gives them this body's own sound" in said
    quacked = "\n\n".join(play_paragraphs(voice="quack", **facts))
    assert "robot_run_verb with `quack` gives them this body's own sound" in quacked
    for text in (said, quacked):
        assert "chatting with after what the body did, as the part's lines" in text


async def test_a_session_with_no_manifest_loads_a_played_file(tmp_path: Path) -> None:
    """A bare transport has no manifest, so `effective_manifest()` is None. Loading must not
    read `mobility` off it, and a body nobody described is taken as one that moves, as the
    run's prompt takes it. With memory off, the part says nothing about notes."""
    path = tmp_path / "t-rex.duck"
    path.write_text(
        PLAYED.replace("[report_state, move_joints, gripper, stop]", "[quack, move_joints, stop]"),
        encoding="utf-8",
    )
    result = await _load(MockTransport(), str(path), memory=False)
    assert result["ok"], result
    assert "This body cannot travel" not in result["play"], "no manifest reads as a body that moves"
    # the task allows `move_joints`, but this body has no such verb to set a pose with
    assert "stops short of the surface this body stands on" not in result["play"]
    assert "remember" not in result["play"].lower()
    assert "robot_run_verb with `quack` gives them this body's own sound" in result["play"]


async def test_an_mcp_part_reads_its_poses_off_the_verbs_the_task_allows(tmp_path: Path) -> None:
    """The arm offers `move_joints`, but a task that does not allow it sets no pose with it, so
    its part reads no sentence about where those poses stop, as a run of the same file would."""
    path = tmp_path / "t-rex.duck"
    path.write_text(PLAYED.replace(", move_joints", ""), encoding="utf-8")
    result = await _load(LeRobotAdapter(LeRobotMock(rest_pose=dict(REST))), str(path))
    assert result["ok"], result
    assert "stops short of the surface this body stands on" not in result["play"]
    assert "This body cannot travel" in result["play"], "the arm's own facts"


# ── the command line ────────────────────────────────────────────────────────────────────


def test_play_is_a_flag_of_run_record_and_preflight_and_not_serve_mcp() -> None:
    """Over MCP the client's own prompt frames the pilot and a played file says so itself; a
    session-wide flag would make every file loaded in it play."""
    for name in ("run", "record", "preflight"):
        said = help_text([name, "--help"])
        assert "--play" in said and "--no-play" in said, name
    assert "--play" not in help_text(["serve-mcp", "--help"])
    # preflight has no --goal and rehearses many files, so its help says what it does instead
    rehearsed = help_text(["preflight", "--help"])
    assert "--play rehearses every file with a part for its pilot to play" in rehearsed
    assert "a --goal run plays" not in rehearsed and "off for this run" not in rehearsed


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
    start = _start(next(runs.glob("*/transcript.jsonl")))
    assert start["play"] == {"on": True, "from": "--play"}
    assert PLAY_HEADING in start["system_prompt"]


@pytest.mark.parametrize(
    "argv",
    [
        ["record", "hello-world", "--gif-size", "64"],
        ["run", "flock-hello", "--no-gif", "--no-memory"],
    ],
    ids=["record", "pilot-flock"],
)
def test_play_reaches_every_pilot_a_command_builds(argv: list[str], tmp_path: Path) -> None:
    """`record` and a pilot flock are call sites of their own, so a `--play` that reached only a
    solo `run` would do nothing on them and say nothing. Every pilot they build reads the
    section and records `--play`, a flock's `flock_start` says what its members say, and the
    header says so before anything moves. Whether the flock's scripted members hear each other
    in time is another test's business, so its outcome is not asserted."""
    runs = tmp_path / "runs"
    result = runner.invoke(cli.app, [*argv, "--llm", "fake", "--play", "--runs-dir", str(runs)])
    assert result.exit_code in (0, 1), result.output
    assert "play on, from --play" in _flat(result.output), "the header row"
    starts = [_start(p) for p in sorted(runs.rglob("transcript.jsonl"))]
    assert starts, f"no pilot ran: {result.output}"
    for start in starts:
        assert start["play"] == {"on": True, "from": "--play"}
        assert PLAY_HEADING in start["system_prompt"]
    for flock in runs.rglob("flock.jsonl"):
        lines = flock.read_text(encoding="utf-8").splitlines()
        first = next(json.loads(line) for line in lines if '"flock_start"' in line)
        assert first["play"] == starts[0]["play"], "the flock's record agrees with its members"


def test_a_run_that_does_not_play_has_no_play_row(tmp_path: Path) -> None:
    """The header of every run that neither plays nor turned a file's play off is what it was.
    The run has to have started, or a header that was never printed would pass."""
    runs = tmp_path / "runs"
    # its own five steps, which the scripted pilot finishes in: a run that started and succeeded
    argv = ["run", "hello-world", "--llm", "fake", "--no-memory", "--no-gif"]
    result = runner.invoke(cli.app, [*argv, "--runs-dir", str(runs)])
    out = _flat(result.output)
    assert result.exit_code == 0, result.output
    assert "provider fake" in out, "the header was printed"
    assert " play " not in f" {out} ", result.output


def test_a_coordinator_flock_refuses_play_before_it_warns_of_a_decision_llm(
    tmp_path: Path,
) -> None:
    """The warning that a decision LLM does not apply to a flock would otherwise speak of a run
    the play refusal then stops before it starts."""
    runs = tmp_path / "runs"
    argv = ["run", "find-and-kick", "--flock", "2", "--llm", "fake", "--play", "--no-memory"]
    result = runner.invoke(cli.app, [*argv, "--decision-llm", "jev", "--runs-dir", str(runs)])
    out = _flat(result.output)
    assert result.exit_code == 1, result.output
    assert "a coordinator flock has no pilot for --play or --no-play to reach" in out
    assert "does not apply to a flock" not in out
    assert not runs.exists() or not any(runs.iterdir())


@pytest.mark.parametrize("flag", ["--play", "--no-play"])
def test_a_coordinator_flock_refuses_either_flag(flag: str, tmp_path: Path) -> None:
    """A coordinator flock has no pilot a part could reach, so either way of asking about one
    is refused rather than dropped without a word."""
    runs = tmp_path / "runs"
    argv = ["run", "find-and-kick", "--flock", "2", "--llm", "fake", flag, "--no-memory"]
    result = runner.invoke(cli.app, [*argv, "--runs-dir", str(runs)])
    assert result.exit_code == 1
    said = "a coordinator flock has no pilot for --play or --no-play to reach"
    assert said in _flat(result.output)
    assert not runs.exists() or not any(runs.iterdir())


@pytest.mark.parametrize("flags", [[], ["--play"], ["--no-play"]], ids=["file", "play", "no-play"])
def test_a_coordinator_flock_refuses_a_file_that_plays(flags: list[str], tmp_path: Path) -> None:
    """`--flock N` with a task file that plays: the file asked for a pilot nobody would be, and
    with no pilot there is none for `--no-play` to turn the part off for either, so the file's
    refusal comes first and no flag lifts it."""
    runs = tmp_path / "runs"
    path = tmp_path / "hello.duck"
    hello = (DUCKS / "hello-world.duck").read_text(encoding="utf-8")
    played = re.sub(r"^duck: \d+$", "duck: 4", hello, count=1, flags=re.M)
    path.write_text(played.replace("persona:", "play: true\npersona:", 1), encoding="utf-8")
    argv = ["run", str(path), "--flock", "2", "--llm", "fake", *flags, "--no-memory"]
    result = runner.invoke(cli.app, [*argv, "--runs-dir", str(runs)])
    assert result.exit_code == 1
    out = _flat(result.output)
    assert "gives its pilot a part to play, and a coordinator flock has no pilot to play it" in out
    assert not runs.exists() or not any(runs.iterdir())


# ── what the header says ────────────────────────────────────────────────────────────────


def test_the_play_row_says_something_only_when_there_is_something_to_say() -> None:
    """A run that plays says whose word that was, a run that turned a file's play off says so,
    and every other run's header has no row at all, as before there was play."""
    assert resolve_play(False, None) == (False, "task file")
    assert resolve_play(True, None) == (True, "task file")
    assert resolve_play(False, True) == (True, "--play")
    assert resolve_play(True, False) == (False, "--no-play")
    assert play_row(*resolve_play(True, None), file_play=True) == "on, from the task file"
    assert play_row(*resolve_play(False, True), file_play=False) == "on, from --play"
    assert play_row(*resolve_play(True, False), file_play=True) == "off, from --no-play"
    assert play_row(*resolve_play(False, None), file_play=False) is None
    assert play_row(*resolve_play(False, False), file_play=False) is None, "nothing turned off"


# ── what the documentation shows ────────────────────────────────────────────────────────


def test_the_spec_shows_a_whole_v4_file_and_the_validate_it_passes(tmp_path: Path) -> None:
    """docs/reference/duck-spec.md shows a played file and the `quackd validate` it passes, so
    the file it shows has to be one and pass it, plainly and against the arm it is written
    for, and the table it quotes has to count the verbs the file allows."""
    page = (REPO / "docs" / "reference" / "duck-spec.md").read_text(encoding="utf-8")
    section = page.split("\n### `play` (v4)", 1)[1].split("\n### ", 1)[0]
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
    quoted = re.search(r"\|\s*t-rex\.duck\s*\|\s*t-rex\s*\|\s*(\d+)\s*\|", section)
    assert quoted and int(quoted.group(1)) == len(fm.verbs.allow), "the verbs column it quotes"
    arm = runner.invoke(cli.app, ["validate", str(duck), "--robot", "lerobot:mock"])
    assert arm.exit_code == 0, arm.output


def test_the_example_files_parse_and_play_only_where_the_readme_says() -> None:
    """CI validates only `ducks/*.duck`, and nothing else reads the shoot's task files, so a hand
    edit could leave one unparseable, half edited or miscounted without anything noticing.
    Every file parses, a file plays exactly when it is `duck: 4`, no played file still counts
    saying why the body cannot as a success, every played file carries the lines they share, and
    the arm's README's counts and the files it names are the files' own, the ones it says are
    meant to fall short among them."""
    folder = REPO / "docs" / "examples"
    for path in sorted(folder.rglob("*.duck")):
        fm = load_duck(str(path)).frontmatter
        assert (fm.duck == 4) == fm.play, f"{path}: duck {fm.duck}, play {fm.play}"
    arm = folder / "lerobot"
    files = sorted(arm.rglob("*.duck"))
    assert files, "no example files"
    played = [p for p in files if load_duck(str(p)).frontmatter.play]
    shared = "Game for anything: commit to the bit"
    success = "You have shown what the task asks with this arm's joints and gripper"
    declare = (
        "3. Declare success with a reason that says what the arm did and what it was meant to show"
    )
    # Their runs end without a declare, on the cable or on the budget, so their success line and
    # their last step say so instead.
    held = {arm / "e067" / "last-words.duck", arm / "e076" / "strike.duck"}
    for path in played:
        text = path.read_text(encoding="utf-8")
        assert "said plainly why this body cannot" not in text, path
        own = ('- "The run ends', "3. Do not declare") if path in held else (success, declare)
        assert all(line in text for line in (shared, *own)), f"{path} is half edited"
    readme = " ".join((arm / "README.md").read_text(encoding="utf-8").split())
    for path in held:
        assert path in played and f"`{path.parent.name}/{path.stem}`" in readme, path
    said = re.search(r"(\d+) of these files give the pilot a part to play", readme)
    assert said, "the examples README no longer says how many files play"
    assert int(said.group(1)) == len(played), f"{len(played)} files play"
    assert f"The other {len(files) - len(played)} keep every line" in readme
    step = "2. Move with `move_joints` in small, deliberate steps"
    kept = [p for p in played if step in p.read_text(encoding="utf-8")]
    count = WORDS[len(kept)] if len(kept) < len(WORDS) else str(len(kept))
    assert f"{count.capitalize()} of them keep the older second step" in readme, len(kept)
    assert arm / "e089" / "less.duck" in kept, "the README names e089/less among them"
    for ran in ("e004/royalty", "e004/mornings", "e011/yoga", "e022/pat-on-the-back"):
        assert arm / f"{ran}.duck" in played, ran
    # Where the run sheet expects the arm not to do what a file asks, by what stops it. An entry
    # that hedges, names failure as one outcome of several, or expects some of a set to fall
    # short without saying which, counts as running, so none of those is here.
    short = {
        "meet a limit of the arm or of how quackd drives it": (
            "e001/circle",
            "e001/duck-picture",
            "e001/initials",
            "e001/smiley",
            "e014/typist",
            "e019/draw-your-ceo",
            "e020/draw-your-competitor",
            "e039/claw-machine",
            "e100/jenga",
            "e109/headphones",
            "e130/where-is-your-hand",
        ),
        "meet the seconds each model call takes": (
            "e037/no-thinking",
            "e045/metronome",
            "e096/red-light-green-light",
            "e113/swat-the-fly",
        ),
        "ask for two things at the same time": ("e062/pat-and-rub", "e070/multitasking"),
        "end on one of quackd's own limits": ("e084/give-110", "e120/out-of-steps"),
        "are cut short by a person, as the experiment plans": (
            "e081/too-slow",
            "e116/slow-raise",
            "e117/slow-raise",
            "e121/the-human-says-no",
        ),
        "ask for a guess the sheet expects the pilot to refuse": (
            "e085/dumbbell",
            "e085/me",
            "e085/teddy",
        ),
        "end on an answer the sheet expects to be wrong": ("e073/is-this-a-ball", "e131/my-left"),
    }
    names = [name for group in short.values() for name in group]
    run = len(files) - len(names)
    assert f"{run} of the {len(files)}, {round(100 * run / len(files))} percent" in readme
    assert f"the other {len(names)} are meant to fall short" in readme
    assert f"Whether the other {run} run as asked" in readme
    # Each named in the sentence that says why, by its folder where every file in it falls short
    # and by its own name where a file beside it does not, and no other file named there.
    span = readme[readme.index("are meant to fall short") : readme.index("Whether the other")]
    heads = []
    for why, group in short.items():
        head = f"{WORDS[len(group)].capitalize()} {why}"
        assert head in span, head
        heads.append((span.index(head), why))
    heads.sort()
    named = set()
    for (start, why), (end, _) in zip(heads, [*heads[1:], (len(span), "")], strict=True):
        for name in short[why]:
            assert arm / f"{name}.duck" in files, name
            folder = name.split("/")[0]
            whole = {f"{folder}/{p.stem}" for p in (arm / folder).glob("*.duck")} <= set(names)
            said = folder if whole else name
            assert f"`{said}`" in span[start:end], f"the README does not say why {name} falls short"
            named.add(said)
    listed = set(re.findall(r"`(e\d{3}(?:/[\w-]+)?)`", span[heads[0][0] :]))
    assert listed == named, f"the README also names {sorted(listed - named)} as falling short"
    drawn = [n for n in names if n.split("/")[0] in ("e001", "e019", "e020")]
    assert f"the {WORDS[len(drawn)]} drawings of" in span


# ── a real model, opt-in ────────────────────────────────────────────────────────────────


def _live_or_skip() -> None:
    """Opt-in twice over, as `tests/test_llm_in_the_simulator.py` is: the `live_llm` marker and
    `QUACKD_LIVE_LLM=1`, then a key, which the CLI would find in `.env`."""
    if os.environ.get("QUACKD_LIVE_LLM") != "1":
        pytest.skip("live LLM tests are opt-in: set QUACKD_LIVE_LLM=1")
    try:
        from dotenv import load_dotenv

        load_dotenv(Path.cwd() / ".env")
        load_dotenv()
    except ImportError:  # pragma: no cover - dotenv ships with the CLI
        pass
    if not os.environ.get("OPENAI_API_KEY"):
        pytest.skip("no OPENAI_API_KEY in the environment or in .env")
    pytest.importorskip("openai")


@pytest.mark.live_llm
async def test_a_real_model_plays_a_part_with_the_arm(tmp_path: Path) -> None:
    """The arm's variant of the section, with the travel sentence and no voice, read by a real
    model: "You are a T-rex. Show me." played on the arm's mock gets past the verdict and
    moves. `QUACKD_LIVE_LLM_MODEL` points it at another model. One paid run."""
    _live_or_skip()
    from quackd.agent.providers.factory import make_provider

    goal = "You are a T-rex. Show me."
    duck = _goal(RobotSpec("lerobot", "mock"), goal)
    budgets = Budgets(max_steps=3, max_minutes=3, max_llm_calls=6)
    duck = duck.model_copy(
        update={"frontmatter": duck.frontmatter.model_copy(update={"budgets": budgets})}
    )
    result = await run_duck(
        RunConfig(
            duck=duck,
            provider=make_provider(
                "openai", model=os.environ.get("QUACKD_LIVE_LLM_MODEL"), goal=goal
            ),
            transport=LeRobotAdapter(LeRobotMock(rest_pose=dict(REST))),
            runs_dir=tmp_path,
            play=True,
        )
    )
    events = Transcript.read(result.run_dir / "transcript.jsonl")
    moved = [
        e
        for e in events
        if e["kind"] == "verb" and e.get("ok") and e.get("canonical") in MOVES_THE_BODY
    ]
    assert result.outcome not in ("infeasible", "aborted"), result.reason
    assert moved, f"the pilot never moved: {result.outcome} ({result.reason})"
