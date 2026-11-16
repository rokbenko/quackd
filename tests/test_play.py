"""Play: a task can give the pilot a part to play, and nothing else changes.

`play: true` in a `glot: 4` task file, or `--play` on the command line, adds one section to the
system prompt, `## Playing a part`, directly before the task. The tools, every gate and every
other byte of the prompt are what they were, and a run that does not ask for it reads exactly
the prompt it read before there was such a thing. These tests hold each half of that, on every
body Roboglot ships, and the places a played run is said and recorded.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from roboglot import cli
from roboglot.adapters.factory import ADAPTER_NAMES, BACKENDS, RobotSpec, describe, registry_for
from roboglot.agent.loop import RunConfig, resolve_play, run_glot
from roboglot.agent.prompts import PLAY_HEADING, build_system_prompt, play_paragraphs, play_section
from roboglot.agent.providers.base import Exchange, ProviderTurn, ToolCall
from roboglot.agent.transcript import Transcript
from roboglot.glotfile.parser import GlotParseError, glot_from_goal, load_glot, parse_glot_text
from roboglot.glotfile.schema import Budgets, GlotFile, GlotFrontmatter
from roboglot.log import play_row
from roboglot.mcp_server import build_server
from roboglot.team.pilots import trim_contract
from roboglot.transport.mock import MockTransport
from roboglot.verdict import MOVES_THE_BODY
from roboglot_lerobot import LeRobotAdapter
from roboglot_lerobot.mock import REST, LeRobotMock

from .conftest import REPO, TASKS, help_text

SPECS = [RobotSpec(a, b) for a in ADAPTER_NAMES for b in BACKENDS[a]]
runner = CliRunner()

PLAYED = """---
glot: 4
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


def _played_team() -> str:
    """team-hello, at `glot: 4`, playing."""
    text = (TASKS / "team-hello.glot").read_text(encoding="utf-8")
    return text.replace("glot: 1", "glot: 4", 1).replace("persona:", "play: true\npersona:", 1)


def _played(glot: GlotFile) -> GlotFile:
    """The same task file, saying `play: true` at `glot: 4`."""
    fm = glot.frontmatter.model_copy(update={"glot": 4, "play": True})
    return glot.model_copy(update={"frontmatter": fm})


def _section(prompt: str) -> str:
    start = prompt.index(f"\n{PLAY_HEADING}\n")
    return prompt[start : prompt.index("\n## Task file:", start)]


def _render(spec: RobotSpec, glot: GlotFile, **kw: Any) -> str:
    manifest = describe(spec)
    registry = registry_for(spec, manifest)
    allow = [n for n in glot.frontmatter.verbs.allow if n in registry]
    return build_system_prompt(
        glot, [registry.view(n) for n in allow], manifest=manifest, sees=True, **kw
    )


def _goal(spec: RobotSpec, goal: str = "You are a T-rex. Show me.") -> GlotFile:
    """A `--goal` task as `roboglot run` builds one for this body: every safe verb it has."""
    registry = registry_for(spec, describe(spec))
    return glot_from_goal(
        goal, sorted(v.name for v in registry.verbs() if v.safety_class == "safe")
    )


def _flat(text: str) -> str:
    return " ".join(re.sub(r"\x1b\[[0-9;]*m", "", text).split())


def _start(path: Path) -> dict[str, Any]:
    """The `run_start` of one transcript, wherever it sits in the file."""
    return next(e for e in Transcript.read(path) if e["kind"] == "run_start")


# ── the task file ───────────────────────────────────────────────────────────────────────


def test_a_glot_4_file_can_give_its_pilot_a_part() -> None:
    assert parse_glot_text(PLAYED, path="t.glot").frontmatter.play is True


@pytest.mark.parametrize("version", [0, 1, 2, 3])
def test_play_needs_glot_4(version: int) -> None:
    """Every version before the one that adds the key refuses it, with the fix in the words."""
    with pytest.raises(GlotParseError, match="play needs glot: 4"):
        parse_glot_text(PLAYED.replace("glot: 4", f"glot: {version}"), path="t.glot")


@pytest.mark.parametrize("version", [0, 1, 2, 3, 4])
def test_play_false_parses_at_every_version(version: int) -> None:
    """Read off the value: a pilot team dumps every member's contract and validates it again,
    so a dumped `play: false` must pass at the version the file was written at."""
    text = PLAYED.replace("glot: 4", f"glot: {version}").replace("play: true", "play: false")
    assert parse_glot_text(text, path="t.glot").frontmatter.play is False


def test_a_trimmed_pilot_team_contract_keeps_its_play() -> None:
    arm = describe(RobotSpec("lerobot", "mock"))
    plain = load_glot(str(TASKS / "team-hello.glot")).frontmatter
    assert trim_contract(plain, arm).play is False, "a dumped play: false at glot: 1 still passes"
    played = parse_glot_text(_played_team(), path="team-hello.glot").frontmatter
    assert trim_contract(played, arm).play is True


# ── the prompt ──────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("spec", SPECS, ids=lambda s: f"{s.adapter}:{s.backend}")
def test_play_false_adds_nothing_to_the_prompt(spec: RobotSpec) -> None:
    """`play=False` adds nothing to the call without it, which is every caller written before
    there was a keyword. That this call renders what it did before play existed was checked
    once, by rendering every body before and after the change (ADR-0055): no golden pins it,
    because the prompt is meant to keep improving."""
    glot = _goal(spec)
    manifest = describe(spec)
    registry = registry_for(spec, manifest)
    verbs = [registry.view(n) for n in glot.frontmatter.verbs.allow if n in registry]
    without = build_system_prompt(glot, verbs, manifest=manifest, sees=True)
    assert _render(spec, glot, play=False) == without
    assert PLAY_HEADING not in without


@pytest.mark.parametrize("spec", SPECS, ids=lambda s: f"{s.adapter}:{s.backend}")
def test_a_played_prompt_is_the_plain_one_with_one_section_before_the_task(
    spec: RobotSpec,
) -> None:
    """The lasting form of the promise: whatever the prompt says, playing adds one section and
    changes nothing else, last before the task file."""
    glot = _goal(spec)
    plain, played = _render(spec, glot), _render(spec, glot, play=True)
    assert played.count(f"\n{PLAY_HEADING}\n") == 1
    section = _section(played)
    assert played.replace(section, "", 1) == plain, "the section is the only difference"
    assert played.index(section) + len(section) == played.index("\n## Task file:")
    assert "\n## " not in section[len(PLAY_HEADING) + 2 :], "one section, with no heading inside"


@pytest.mark.parametrize("spec", SPECS, ids=lambda s: f"{s.adapter}:{s.backend}")
def test_the_offered_verbs_are_the_same_played_or_not(spec: RobotSpec) -> None:
    """Four adapter tests read the offered verbs as the lines that start with a dash and a
    backtick, so no line of the section may start that way."""
    glot = _goal(spec)

    def offered(prompt: str) -> list[str]:
        return [line for line in prompt.splitlines() if line.startswith("- `")]

    plain = offered(_render(spec, glot))
    assert plain, "the verb lines moved, so this comparison would hold with nothing in it"
    assert offered(_render(spec, glot, play=True)) == plain


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
    spec = RobotSpec("microduck", "mock")
    glot = load_glot(str(TASKS / "hello-world.glot"))
    played = _render(spec, glot, play=True)
    assert PLAY_HEADING in played and "remember" not in played.lower()
    assert "`quack` gives them this body's own sound" in played
    assert "No verb here speaks" not in played and "`say` gives" not in played
    kept = _render(spec, glot, play=True, memory_text="")
    assert "A note you keep with `remember`" in _section(kept)
    assert kept.replace(_section(kept), "", 1) == _render(spec, glot, memory_text="")


def test_the_open_duck_prompt_still_names_no_verb_it_does_not_offer() -> None:
    """`tests/test_open_duck_adapter.py` bans these words from the whole prompt, played or not."""
    spec = RobotSpec("open_duck", "mock")
    played = _render(spec, load_glot(str(TASKS / "open-duck-lookout.glot")), play=True)
    assert PLAY_HEADING in played
    for word in ("kick", "grab", "stand_up"):
        assert word not in played, word


def test_a_body_that_cannot_travel_is_told_how_to_play_going_somewhere() -> None:
    arm = _section(
        _render(RobotSpec("lerobot", "mock"), _goal(RobotSpec("lerobot", "mock")), play=True)
    )
    duck = _section(
        _render(RobotSpec("microduck", "mock"), _goal(RobotSpec("microduck", "mock")), play=True)
    )
    assert "This body cannot travel" in arm and "mobility none" in arm
    assert "This body cannot travel" not in duck, "a body that walks plays going somewhere itself"


@pytest.mark.parametrize(
    ("spec", "joints", "travel", "table"),
    [
        (RobotSpec("lerobot", "mock"), True, True, True),
        # the same arm on a task that leaves `move_joints` out sets no pose joint by joint
        (RobotSpec("lerobot", "mock"), False, True, False),
        (RobotSpec("microduck", "mock"), True, False, False),
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
    glot = glot_from_goal("You are a T-rex. Show me.", sorted(safe | set(gated)), confirm=gated)
    section = _section(_render(spec, glot, play=True))
    assert ("This body cannot travel" in section) is travel
    assert ("stops short of the surface this body stands on" in section) is table
    assert "A blow, a stomp or a touch at something that is not here stops before it" in section


def test_the_lines_sentence_follows_the_verbs_the_task_allows() -> None:
    """Every safe verb of each body is offered here, so the Microduck's goal offers `say`, which
    the section names over `quack`, and the arm, which has neither, cannot speak at all."""
    duck = _section(
        _render(RobotSpec("microduck", "mock"), _goal(RobotSpec("microduck", "mock")), play=True)
    )
    arm = _section(
        _render(RobotSpec("lerobot", "mock"), _goal(RobotSpec("lerobot", "mock")), play=True)
    )
    assert "`say` gives them this body's own sound" in duck, "tones, not speech"
    assert "`quack` gives" not in duck, "one voice is named, and `say` first"
    assert "No verb here speaks" in arm


def test_the_section_reads_no_word_of_the_task() -> None:
    """The rule from #25: nothing Roboglot says to a pilot turns on what the task's words are."""
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
    llm_id = "test"
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


async def _run(glot: GlotFile, tmp_path: Path, **cfg: Any) -> tuple[Script, list[dict[str, Any]]]:
    provider = cfg.pop("provider", None) or Script()
    transport = cfg.pop("transport", None) or MockTransport()
    result = await run_glot(
        RunConfig(glot=glot, provider=provider, transport=transport, runs_dir=tmp_path, **cfg)
    )
    return provider, Transcript.read(result.run_dir / "transcript.jsonl")


async def test_play_from_the_run_reaches_the_prompt_and_the_record(
    hello_glot: GlotFile, tmp_path: Path
) -> None:
    """`--play` over a file that does not play: the pilot reads the section, the record says
    the flag asked for it, and the contract stays the file's own, which cannot say so."""
    provider, events = await _run(hello_glot, tmp_path, play=True)
    assert PLAY_HEADING in provider.systems[0]
    start = events[0]
    assert start["play"] == {"on": True, "from": "--play"}
    assert start["contract"]["play"] is False, "the contract is the file's own, which does not play"


async def test_no_play_turns_a_files_play_off(hello_glot: GlotFile, tmp_path: Path) -> None:
    provider, events = await _run(_played(hello_glot), tmp_path, play=False)
    assert PLAY_HEADING not in provider.systems[0]
    assert events[0]["play"] == {"on": False, "from": "--no-play"}


async def test_without_a_flag_the_task_file_decides(hello_glot: GlotFile, tmp_path: Path) -> None:
    played, events = await _run(_played(hello_glot), tmp_path / "a")
    assert PLAY_HEADING in played.systems[0]
    assert events[0]["play"] == {"on": True, "from": "task file"}
    plain, events = await _run(hello_glot, tmp_path / "b")
    assert PLAY_HEADING not in plain.systems[0]
    assert events[0]["play"] == {"on": False, "from": "task file"}


async def test_play_and_max_steps_both_hold(hello_glot: GlotFile, tmp_path: Path) -> None:
    provider, events = await _run(hello_glot, tmp_path, play=True, max_steps=7)
    assert PLAY_HEADING in provider.systems[0] and "Budgets: 7 steps" in provider.systems[0]
    assert events[0]["contract"]["budgets"]["max_steps"] == 7


async def test_the_tools_are_the_same_played_or_not(hello_glot: GlotFile, tmp_path: Path) -> None:
    played, _ = await _run(hello_glot, tmp_path / "a", play=True)
    plain, _ = await _run(hello_glot, tmp_path / "b")
    assert played.tools[0] == plain.tools[0]


ARM_TASK = """---
glot: {version}
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
        glot = parse_glot_text(ARM_TASK.format(version=version, play=play), path="arm.glot")
        _provider, events = await _run(
            glot,
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
    result = await _load(arm, str(TASKS / "lerobot-lookout.glot"))
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
    assert set(result["contract"]) == set(GlotFrontmatter.model_fields) - {"play"}
    assert "part to play" not in result["note"]


async def test_a_played_file_hands_the_mcp_pilot_its_part(tmp_path: Path) -> None:
    path = tmp_path / "t-rex.glot"
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
    path = tmp_path / "t-rex.glot"
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
    path = tmp_path / "t-rex.glot"
    path.write_text(PLAYED.replace(", move_joints", ""), encoding="utf-8")
    result = await _load(LeRobotAdapter(LeRobotMock(rest_pose=dict(REST))), str(path))
    assert result["ok"], result
    assert "stops short of the surface this body stands on" not in result["play"]
    assert "This body cannot travel" in result["play"], "the arm's own facts"


# ── the command line ────────────────────────────────────────────────────────────────────


def test_play_is_a_flag_of_run_and_not_serve_mcp() -> None:
    """Over MCP the client's own prompt frames the pilot and a played file says so itself; a
    session-wide flag would make every file loaded in it play."""
    said = help_text(["run", "--help"])
    assert "--play" in said and "--no-play" in said
    assert "--play" not in help_text(["serve-mcp", "--help"])


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


def test_play_reaches_every_pilot_a_team_builds(tmp_path: Path) -> None:
    """A pilot team is a call site of its own, so a `--play` that reached only a solo `run`
    would do nothing on it and say nothing. Every pilot it builds reads the section and
    records `--play`, the team's `team_start` says what its members say, and the header says
    so before anything moves. Whether the team's scripted members hear each other in time is
    another test's business, so its outcome is not asserted."""
    runs = tmp_path / "runs"
    argv = ["run", "team-hello", "--no-memory"]
    result = runner.invoke(cli.app, [*argv, "--llm", "fake", "--play", "--runs-dir", str(runs)])
    assert result.exit_code in (0, 1), result.output
    assert "play on, from --play" in _flat(result.output), "the header row"
    starts = [_start(p) for p in sorted(runs.rglob("transcript.jsonl"))]
    assert starts, f"no pilot ran: {result.output}"
    for start in starts:
        assert start["play"] == {"on": True, "from": "--play"}
        assert PLAY_HEADING in start["system_prompt"]
    for team in runs.rglob("team.jsonl"):
        lines = team.read_text(encoding="utf-8").splitlines()
        first = next(json.loads(line) for line in lines if '"team_start"' in line)
        assert first["play"] == starts[0]["play"], "the team's record agrees with its members"


def test_a_run_that_does_not_play_has_no_play_row(tmp_path: Path) -> None:
    """The header of every run that neither plays nor turned a file's play off is what it was.
    The run has to have started, or a header that was never printed would pass."""
    runs = tmp_path / "runs"
    # its own five steps, which the scripted pilot finishes in: a run that started and succeeded
    argv = ["run", "hello-world", "--robot", "microduck:mock", "--llm", "fake", "--no-memory"]
    result = runner.invoke(cli.app, [*argv, "--runs-dir", str(runs)])
    out = _flat(result.output)
    assert result.exit_code == 0, result.output
    assert "provider fake" in out, "the header was printed"
    assert " play " not in f" {out} ", result.output


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
    """docs/reference/glot-spec.md shows a played file and the `roboglot validate` it passes, so
    the file it shows has to be one and pass it, plainly and against the arm it is written
    for, and the table it quotes has to count the verbs the file allows."""
    page = (REPO / "docs" / "reference" / "glot-spec.md").read_text(encoding="utf-8")
    section = page.split("\n### `play` (v4)", 1)[1].split("\n### ", 1)[0]
    found = re.search(r"```yaml\n(---\n.*?)```", section, flags=re.DOTALL)
    assert found, "the play section no longer shows a whole file"
    fm = parse_glot_text(found.group(1)).frontmatter
    assert fm.glot == 4 and fm.play is True
    glot = tmp_path / "t-rex.glot"
    glot.write_text(found.group(1), encoding="utf-8")
    assert "```console\n$ roboglot validate t-rex.glot\n" in section, "the command it quotes"
    result = runner.invoke(cli.app, ["validate", str(glot)])
    assert result.exit_code == 0, result.output
    assert "1 file valid" in result.output and "1 file valid" in section
    quoted = re.search(r"\|\s*t-rex\.glot\s*\|\s*t-rex\s*\|\s*(\d+)\s*\|", section)
    assert quoted and int(quoted.group(1)) == len(fm.verbs.allow), "the verbs column it quotes"
    arm = runner.invoke(cli.app, ["validate", str(glot), "--robot", "lerobot:mock"])
    assert arm.exit_code == 0, arm.output


def test_the_example_files_parse_and_play_only_where_the_readme_says() -> None:
    """CI validates only `tasks/*.glot`, and nothing else reads the shoot's task files, so a hand
    edit could leave one unparseable, half edited or miscounted without anything noticing.
    Every file parses, a file plays exactly when it is `glot: 4`, no played file still counts
    saying why the body cannot as a success, every played file carries the lines they share, and
    the arm's README's counts and the files it names are the files' own, the ones it says are
    meant to fall short among them."""
    folder = REPO / "docs" / "examples"
    for path in sorted(folder.rglob("*.glot")):
        fm = load_glot(str(path)).frontmatter
        assert (fm.glot == 4) == fm.play, f"{path}: duck {fm.glot}, play {fm.play}"
    arm = folder / "lerobot"
    files = sorted(arm.rglob("*.glot"))
    assert files, "no example files"
    played = [p for p in files if load_glot(str(p)).frontmatter.play]
    shared = "Game for anything: commit to the bit"
    success = "You have shown what the task asks with this arm's joints and gripper"
    declare = (
        "3. Declare success with a reason that says what the arm did and what it was meant to show"
    )
    # Their runs end without a declare, on the cable or on the budget, so their success line and
    # their last step say so instead.
    held = {arm / "e067" / "last-words.glot", arm / "e076" / "strike.glot"}
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
    assert f"The other {len(files) - len(played)} do not play" in readme
    step = "2. Move with `move_joints` in small, deliberate steps"
    kept = [p for p in played if step in p.read_text(encoding="utf-8")]
    count = WORDS[len(kept)] if len(kept) < len(WORDS) else str(len(kept))
    assert f"{count.capitalize()} of them keep the older second step" in readme, len(kept)
    assert arm / "e089" / "less.glot" in kept, "the README names e089/less among them"
    for ran in ("e004/royalty", "e004/mornings", "e011/yoga", "e022/pat-on-the-back"):
        assert arm / f"{ran}.glot" in played, ran
    # Where the run sheet expects the arm not to do what a file asks, by what stops it. An entry
    # that hedges, names failure as one outcome of several, or expects some of a set to fall
    # short without saying which, counts as running, so none of those is here.
    short = {
        "meet a limit of the arm or of how Roboglot drives it": (
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
        "meet the seconds each LLM call takes": (
            "e037/no-thinking",
            "e045/metronome",
            "e096/red-light-green-light",
            "e113/swat-the-fly",
        ),
        "ask for two things at the same time": ("e062/pat-and-rub", "e070/multitasking"),
        "end on one of Roboglot's own limits": (
            "e084/give-110",
            "e120/out-of-steps",
            "e126/monkey",
            "e170/out-of-range",
            "e187/out-of-calls",
            "e187/out-of-time",
            "e193/two-steps",
            "e194/as-far-as-your-notes-say",
            "e215/hold-on",
        ),
        "are cut short by a person, as the experiment plans": (
            "e081/too-slow",
            "e116/slow-raise",
            "e117/slow-raise",
            "e121/the-human-says-no",
            "e166/raise-for-the-switch",
            "e180/slow-raise",
            "e182/think-hard",
            "e183/push-back",
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
            assert arm / f"{name}.glot" in files, name
            folder = name.split("/")[0]
            whole = {f"{folder}/{p.stem}" for p in (arm / folder).glob("*.glot")} <= set(names)
            said = folder if whole else name
            assert f"`{said}`" in span[start:end], f"the README does not say why {name} falls short"
            named.add(said)
    listed = set(re.findall(r"`(e\d{3}(?:/[\w-]+)?)`", span[heads[0][0] :]))
    assert listed == named, f"the README also names {sorted(listed - named)} as falling short"
    drawn = [n for n in names if n.split("/")[0] in ("e001", "e019", "e020")]
    assert f"the {WORDS[len(drawn)]} drawings of" in span


# ── a real LLM, opt-in ────────────────────────────────────────────────────────────────


def _live_or_skip() -> None:
    """Opt-in twice over: the `live_llm` marker and `ROBOGLOT_LIVE_LLM=1`, then a key, which
    the CLI would find in `.env`."""
    if os.environ.get("ROBOGLOT_LIVE_LLM") != "1":
        pytest.skip("live LLM tests are opt-in: set ROBOGLOT_LIVE_LLM=1")
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
async def test_a_real_llm_plays_a_part_with_the_arm(tmp_path: Path) -> None:
    """The arm's variant of the section, with the travel sentence and no voice, read by a real
    LLM: "You are a T-rex. Show me." played on the arm's mock gets past the verdict and
    moves. `ROBOGLOT_LIVE_LLM_MODEL` points it at another LLM. One paid run."""
    _live_or_skip()
    from roboglot.agent.providers.factory import make_provider

    goal = "You are a T-rex. Show me."
    glot = _goal(RobotSpec("lerobot", "mock"), goal)
    budgets = Budgets(max_steps=3, max_minutes=3, max_llm_calls=6)
    glot = glot.model_copy(
        update={"frontmatter": glot.frontmatter.model_copy(update={"budgets": budgets})}
    )
    result = await run_glot(
        RunConfig(
            glot=glot,
            provider=make_provider(
                "openai", llm_id=os.environ.get("ROBOGLOT_LIVE_LLM_MODEL"), goal=goal
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
