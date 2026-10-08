"""`--controller vla`: a scripted pilot that hands the arm's learned policy each instruction and
leaves the task to the person who watched it.

The loop's half runs on the LeRobot mock, whose `manipulate` is a scripted segment on a clock of
its own, so every segment runs exactly the seconds it was told and a budget ends a list where the
arithmetic says. The command's half is `quackd run --controller vla` on `lerobot:mujoco` over the
simulator's stand-in model, against the real policy server serving `scripted:hold`, with the
terminal's answers scripted through `_ask`.

The person is always an asker marked `asks_a_person`, as the CLI marks its own, and every
refusal is checked to stop before a run directory exists.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import click
import pytest
from typer.testing import CliRunner

from quackd import cli
from quackd.agent.loop import RunConfig, RunResult, run_duck
from quackd.agent.providers import pricing
from quackd.agent.providers import vla as V
from quackd.agent.providers.base import (
    JUDGE_FEATURE,
    Exchange,
    JudgedPilot,
    Observation,
    ProviderTurn,
    ToolCall,
    Usage,
)
from quackd.agent.providers.vla import VlaProvider
from quackd.agent.transcript import Transcript
from quackd.duckfile.parser import parse_duck_text
from quackd.duckfile.schema import INSTRUCTION_MAX_CHARS, POLICY_VERB
from quackd.log import a_person_was_asked
from quackd.safety import allow_all
from quackd_lerobot import LeRobotAdapter
from quackd_lerobot.mock import LeRobotMock
from quackd_lerobot.policy import server as S
from tests.gl import REQUIRE_ENV
from tests.test_duck_policy import INSTRUCTIONS, SEGMENT_S, _v3
from tests.test_policy_contract import TOKEN, Serving, _serving

runner = CliRunner()

DEAD = "http://127.0.0.1:1"
"""A loopback address nothing listens on. Every refusal here fires before it would be asked."""


def _person(answer: bool, heard: list[str] | None = None) -> Callable[[str], bool]:
    """Somebody at a terminal, who answers `answer` to whatever they are asked."""

    def ask(question: str) -> bool:
        if heard is not None:
            heard.append(question)
        return answer

    ask.asks_a_person = True  # type: ignore[attr-defined]
    return ask


def _events(result: RunResult) -> list[dict[str, Any]]:
    return Transcript.read(result.run_dir / "transcript.jsonl")


def _prompts(result: RunResult) -> list[tuple[str, str, bool]]:
    return [
        (e["what"], e["question"], e["answer"]) for e in _events(result) if e["kind"] == "prompt"
    ]


def _segments(result: RunResult) -> list[str]:
    return [
        e["params"]["instruction"]
        for e in _events(result)
        if e["kind"] == "verb" and e["name"] == POLICY_VERB
    ]


async def _run(
    tmp_path: Path,
    *,
    judge: Callable[[str], bool] | None,
    decide: Callable[[str], bool] | None = None,
    total_s: float = len(INSTRUCTIONS) * SEGMENT_S,
    confirm: Any = allow_all,
    provider: Any = None,
    mock: LeRobotMock | None = None,
) -> RunResult:
    duck = parse_duck_text(_v3(total_s=total_s, name="stack"))
    return await run_duck(
        RunConfig(
            duck=duck,
            provider=provider
            or VlaProvider(
                duck.frontmatter.effective_policy.instructions,
                success=duck.frontmatter.success,
                label=duck.name,
            ),
            transport=LeRobotAdapter(mock or LeRobotMock()),
            runs_dir=tmp_path,
            confirm=confirm,
            decide=decide if decide is not None else _person(True),
            judge=judge,
        )
    )


# ── the loop ────────────────────────────────────────────────────────────────────────────


async def test_a_person_who_says_yes_is_the_only_success(tmp_path: Path) -> None:
    """The verdict is a scripted `uncertain` a person clears, one `manipulate` per listed
    instruction in order, then one question about the arm, and the declare quotes the person."""
    heard: list[str] = []
    result = await _run(tmp_path, judge=_person(True, heard))
    assert result.outcome == "success", result.reason
    assert "a person watched the arm and said it did the task" in result.reason
    assert _segments(result) == list(INSTRUCTIONS)
    (assessed,) = [e for e in _events(result) if e["kind"] == "assess"]
    assert assessed["verdict"] == "uncertain" and assessed["human"] == "go"
    assert assessed["reason"] == V.VERDICT_REASON
    rows = _prompts(result)
    assert [(what, answer) for what, _q, answer in rows] == [("decide", True), ("judge", True)]
    (question,) = heard
    assert rows[1][1] == question, "the record quotes the question in the words it was asked"
    assert all(repr(line) in question for line in INSTRUCTIONS), question
    assert "The task is done when: x." in question, "the task's own words for done"
    # the answer rides on the observation the pilot declares from, and on no other
    judged = [e for e in _events(result) if JUDGE_FEATURE in e.get("features", {})]
    assert len(judged) == 1
    assert judged[0]["features"][JUDGE_FEATURE] == {
        "question": question,
        "answer": True,
        "asked": True,
    }


async def test_it_costs_nothing_and_says_so(tmp_path: Path) -> None:
    """No model is asked, so no token is counted, and the run is priced at the scripted
    pilot's rate rather than left unpriced."""
    result = await _run(tmp_path, judge=_person(True))
    assert result.usage == Usage()
    summary = json.loads((result.run_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["provider"] == V.NAME and summary["cost_usd"] == 0.0
    assert summary["price"]["source"] == pricing.FAKE.source


async def test_a_person_who_says_no_fails_the_run(tmp_path: Path) -> None:
    result = await _run(tmp_path, judge=_person(False))
    assert result.outcome == "failure", result.reason
    assert "a person watched the arm and said it did not do the task" in result.reason
    assert _segments(result) == list(INSTRUCTIONS), "every segment ran before the question"
    assert [what for what, _q, _a in _prompts(result)] == ["decide", "judge"]


async def test_a_yes_nobody_gave_is_not_a_success(tmp_path: Path) -> None:
    """`yes | quackd run` answers yes without anybody reading the question. The record writes
    no `prompt` row for it, and the run cannot succeed on it: `a_person_was_asked` is the
    test for both."""
    piped: list[str] = []

    def pipe(question: str) -> bool:
        piped.append(question)
        return True

    pipe.asks_a_person = lambda: False  # type: ignore[attr-defined]
    result = await _run(tmp_path, judge=pipe)
    assert piped, "the question was still put"
    assert result.outcome == "failure", result.reason
    assert "no person was asked whether the arm did the task" in result.reason
    assert "judge" not in [what for what, _q, _a in _prompts(result)]


async def test_nobody_to_judge_is_a_failure_after_the_segments(tmp_path: Path) -> None:
    result = await _run(tmp_path, judge=None)
    assert result.outcome == "failure", result.reason
    assert "nobody was there to say whether the arm did the task" in result.reason
    assert _segments(result) == list(INSTRUCTIONS)


async def test_nobody_to_decide_ends_it_before_any_segment(tmp_path: Path) -> None:
    """A scripted pilot never answers its own doubt with `feasible`: with nobody to clear it,
    the arm never moves."""
    duck = parse_duck_text(_v3(total_s=len(INSTRUCTIONS) * SEGMENT_S))
    result = await run_duck(
        RunConfig(
            duck=duck,
            provider=VlaProvider(INSTRUCTIONS),
            transport=LeRobotAdapter(LeRobotMock()),
            runs_dir=tmp_path,
            confirm=allow_all,
            decide=None,
            judge=_person(True),
        )
    )
    assert result.outcome == "failure", result.reason
    assert "nobody was there to decide whether this body should try it" in result.reason
    assert _segments(result) == [] and _prompts(result) == []


async def test_a_segment_that_did_not_end_ok_ends_the_list_without_asking(
    tmp_path: Path,
) -> None:
    """The person at the confirm gate declines the second segment: the third is never
    started, and nobody is asked whether a task half refused was done."""
    answers = iter([True, False])

    def gate(_name: str, _params: dict[str, Any]) -> bool:
        return next(answers)

    heard: list[str] = []
    result = await _run(tmp_path, judge=_person(True, heard), confirm=gate)
    assert result.outcome == "failure", result.reason
    assert f"segment 2 of 3, {INSTRUCTIONS[1]!r}, did not end ok" in result.reason
    assert _segments(result) == list(INSTRUCTIONS[:2])
    assert heard == [], "no question about a list that stopped on a refusal"


async def test_a_budget_that_ends_the_list_still_asks_about_what_ran(tmp_path: Path) -> None:
    """`total_s` holds two segments of three. The third is refused as a budget, which ends the
    run on the budget's outcome, and the person is still asked about the two that ran, in a
    question that names them and not the third."""
    heard: list[str] = []
    result = await _run(tmp_path, judge=_person(True, heard), total_s=2 * SEGMENT_S)
    assert result.outcome == "budget", result.reason
    assert "policy total_s" in result.reason
    assert result.reason.endswith("a person watched what ran and said the arm did it")
    assert _segments(result) == list(INSTRUCTIONS[:2])
    (question,) = heard
    assert "The run stopped after 2 segments" in question and "of the 3 this task lists" in question
    assert all(repr(line) in question for line in INSTRUCTIONS[:2])
    assert repr(INSTRUCTIONS[2]) not in question, "the refused one never ran"
    assert ("judge", question, True) in _prompts(result)


async def test_a_budget_after_the_question_does_not_ask_it_twice(tmp_path: Path) -> None:
    """The question is put before the pilot's last call, and a budget of calls that runs out
    right there ends the run without its declare. The reason carries the answer already given,
    and nobody is asked again."""
    heard: list[str] = []
    duck = parse_duck_text(
        _v3(total_s=len(INSTRUCTIONS) * SEGMENT_S).replace(
            "success: [x]", "success: [x]\nbudgets:\n  max_llm_calls: 4"
        )
    )
    result = await run_duck(
        RunConfig(
            duck=duck,
            provider=VlaProvider(INSTRUCTIONS),
            transport=LeRobotAdapter(LeRobotMock()),
            runs_dir=tmp_path,
            confirm=allow_all,
            decide=_person(True),
            judge=_person(False, heard),
        )
    )
    assert result.outcome == "budget", result.reason
    assert "max_llm_calls" in result.reason and len(heard) == 1
    assert result.reason.endswith("a person watched what ran and said it did not")


async def test_the_time_a_person_takes_to_answer_is_not_the_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The person looks at the arm for longer than `max_minutes` has left, on the arm's own
    clock, which is the one the budget reads, and then says yes. That yes is what the run
    ends on, and not a spent budget."""
    cap_s = parse_duck_text(_v3(total_s=SEGMENT_S)).frontmatter.budgets.max_minutes * 60
    mock = LeRobotMock()
    arm_clock = mock.now
    looked_s = [0.0]
    monkeypatch.setattr(mock, "now", lambda: arm_clock() + looked_s[0])
    person = _person(True)

    def slow(question: str) -> bool:
        looked_s[0] += cap_s
        return person(question)

    slow.asks_a_person = True  # type: ignore[attr-defined]
    result = await _run(tmp_path, judge=slow, mock=mock)
    assert result.outcome == "success", result.reason
    assert looked_s[0] == cap_s, "the question was put once"


@pytest.mark.parametrize("raised", [EOFError(), click.exceptions.Abort()], ids=["eof", "abort"])
async def test_a_prompt_that_raised_is_nobody_answering_and_not_a_no(
    tmp_path: Path, raised: Exception
) -> None:
    """typer's y/N raises click's `Abort` on EOF or on Ctrl-C. The run cannot succeed on
    that, and the record does not say a person answered no: there is no `judge` prompt row,
    and the observation and the declare say what the prompt raised. The same holds for the
    question a budget puts about the segments that ran."""
    name = type(raised).__name__

    def ask(question: str) -> bool:
        raise raised

    ask.asks_a_person = True  # type: ignore[attr-defined]
    result = await _run(tmp_path / "list", judge=ask)
    assert result.outcome == "failure", result.reason
    assert f"the prompt raised {name}" in result.reason, result.reason
    assert "said it did not" not in result.reason, "nobody said no"
    assert "judge" not in [what for what, _q, _a in _prompts(result)]
    (judged,) = [
        e["features"][JUDGE_FEATURE]
        for e in _events(result)
        if JUDGE_FEATURE in e.get("features", {})
    ]
    assert judged["answer"] is None and judged["raised"] == name
    cut = await _run(tmp_path / "budget", judge=ask, total_s=2 * SEGMENT_S)
    assert cut.outcome == "budget", cut.reason
    assert cut.reason.endswith(f"the prompt raised {name} before they answered"), cut.reason
    assert "judge" not in [what for what, _q, _a in _prompts(cut)]


def _only_record(runs: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """The summary and the transcript of the one run under `runs`, for a run that raised
    rather than returning a result that names its directory."""
    (written,) = runs.rglob("summary.json")
    summary = json.loads(written.read_text(encoding="utf-8"))
    return summary, Transcript.read(written.parent / "transcript.jsonl")


async def test_a_ctrl_c_at_the_question_a_budget_puts_is_on_the_record(tmp_path: Path) -> None:
    """A `KeyboardInterrupt` raised inside the budget's handler escapes the handler for an
    interrupt beside it. The run still ends on its budget, and its reason and a note say the
    question about what ran was interrupted."""

    def ask(question: str) -> bool:
        raise KeyboardInterrupt

    ask.asks_a_person = True  # type: ignore[attr-defined]
    with pytest.raises(KeyboardInterrupt):
        await _run(tmp_path, judge=ask, total_s=2 * SEGMENT_S)
    summary, events = _only_record(tmp_path)
    notes = [e["text"] for e in events if e["kind"] == "note"]
    assert "run interrupted: KeyboardInterrupt" in notes, notes
    assert summary["outcome"] == "budget", summary["reason"]
    assert summary["reason"].startswith("policy total_s"), summary["reason"]
    assert summary["reason"].endswith(
        "the question about what ran was interrupted (KeyboardInterrupt)"
    ), summary["reason"]


class _Claims:
    """A pilot that says it cannot judge and declares success anyway."""

    name = "claims"
    model = "scripted"
    supports_vision = False

    def judge_question(self, history: Any, *, cut_short: str | None = None) -> str | None:
        return None

    async def step(self, system: str, history: list[Exchange], tools: Any) -> ProviderTurn:
        return ProviderTurn(
            tool_calls=[ToolCall(name="declare_success", arguments={"reason": "done"})]
        )


async def test_the_loop_holds_any_judged_pilot_to_a_persons_yes(tmp_path: Path) -> None:
    """`providers.vla` never declares success on its own. The loop does not take that on
    trust: a pilot that says it cannot judge, and declares success with nobody asked, ends in
    failure. No new outcome: a run is still a success or it is not."""
    assert isinstance(_Claims(), JudgedPilot)
    duck = parse_duck_text(_v3(total_s=len(INSTRUCTIONS) * SEGMENT_S))
    result = await run_duck(
        RunConfig(
            duck=duck,
            provider=_Claims(),  # type: ignore[arg-type]
            transport=LeRobotAdapter(LeRobotMock()),
            runs_dir=tmp_path,
            judge=_person(True),
        )
    )
    assert result.outcome == "failure", result.reason
    assert "no person asked said the arm did it" in result.reason


def test_the_pilot_reads_the_last_result_and_the_answer_and_nothing_else() -> None:
    """Whatever else an observation carries, the arm's state, a detection, the simulator's
    own words about the table, the pilot's calls are the same: it is told what its segment
    returned and what the person said, and it decides on those alone."""
    pilot = VlaProvider(INSTRUCTIONS)
    tools = [{"name": V.ASSESS}]
    seen = Observation(
        text="step 3",
        features={
            "state": {"extras": {"joints": {"shoulder_pan": 1.0}, "block_on_table": True}},
            "detections": [{"label": "block", "bearing_deg": 0.0}],
            "sim_truth": {"lifted": True},
            "last_result": {"verb": POLICY_VERB, "ok": True, "summary": "ran"},
        },
    )
    bare = Observation(text="step 3", features={"last_result": {"verb": POLICY_VERB, "ok": True}})
    decided = [
        Exchange(
            observation=Observation(text="0"),
            decision={"tool_call": ToolCall(name=V.ASSESS, arguments={})},  # type: ignore[arg-type]
        ),
        Exchange(
            observation=Observation(text="1", features={"last_result": {"verb": V.ASSESS}}),
            decision={  # type: ignore[arg-type]
                "tool_call": ToolCall(name=POLICY_VERB, arguments={"instruction": INSTRUCTIONS[0]})
            },
        ),
    ]
    for obs in (seen, bare):
        call = pilot._next(obs, [*decided, Exchange(observation=obs)], tools)
        assert call == ToolCall(name=POLICY_VERB, arguments={"instruction": INSTRUCTIONS[1]})


def test_a_pilot_built_from_python_is_held_to_the_rule_for_an_instruction() -> None:
    with pytest.raises(ValueError, match="at least one instruction"):
        VlaProvider([])
    with pytest.raises(ValueError, match="characters"):
        VlaProvider(["x" * (INSTRUCTION_MAX_CHARS + 1)])
    assert VlaProvider(["  hold still  "]).instructions == ("hold still",)


# ── the command ─────────────────────────────────────────────────────────────────────────


def _flat(text: str) -> str:
    """Output on one line, with no colour, as `tests/test_policy_plumbing.py` reads it."""
    return " ".join(re.sub(r"\x1b\[[0-9;]*m", "", text).split())


def _no_run(tmp_path: Path) -> bool:
    runs = tmp_path / "runs"
    return not runs.exists() or not any(runs.iterdir())


def _cli(tmp_path: Path, *argv: str) -> Any:
    return runner.invoke(
        cli.app, ["run", *argv, "--no-memory", "--runs-dir", str(tmp_path / "runs")]
    )


GOAL = ("--goal", "hold still")
ARM = ("--robot", "lerobot:mujoco", "--policy-url", DEAD, "--policy-token", TOKEN)


@pytest.mark.parametrize(
    ("argv", "needle"),
    [
        pytest.param((*GOAL, *ARM, "--yes"), "--yes answers every question", id="yes"),
        pytest.param((*GOAL, *ARM, "--dry-run"), "--dry-run moves nothing", id="dry-run"),
        pytest.param(
            (*GOAL, *ARM, "--llm", "fake", "--image", "x.png"),
            "so --llm and --image would do nothing",
            id="a-model",
        ),
        # no pilot reads a part, so either way of asking about one would do nothing
        pytest.param((*GOAL, *ARM, "--play"), "so --play would do nothing", id="play"),
        pytest.param((*GOAL, *ARM, "--no-play"), "so --no-play would do nothing", id="no-play"),
        pytest.param((*GOAL, "--robot", "lerobot:mujoco"), "names no policy server", id="policy"),
        pytest.param(
            ("--goal", "x" * (INSTRUCTION_MAX_CHARS + 1), *ARM), "characters", id="long-goal"
        ),
        pytest.param(("--goal", "one\ntwo", *ARM), "one line of plain text", id="two-lines"),
        pytest.param(
            (*GOAL, *ARM, "--decision-llm", "jev"),
            "no model for a decision LLM to step in front of, and --decision-llm names one",
            id="decision-llm",
        ),
    ],
)
def test_a_vla_run_is_refused_before_anything_connects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, argv: tuple[str, ...], needle: str
) -> None:
    monkeypatch.setattr(cli, "_can_prompt", lambda: True)
    result = _cli(tmp_path, *argv, "--controller", "vla")
    out = _flat(result.output)
    assert result.exit_code == 1 and needle in out, result.output
    assert out.count("--controller vla") >= 1, "the refusal names the flag it refuses"
    assert _no_run(tmp_path)


def test_a_vla_run_with_nobody_at_a_terminal_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A `CliRunner` has no terminal under it, which is the pipe and the script alike."""
    monkeypatch.setattr(cli, "_can_prompt", lambda: False)
    result = _cli(tmp_path, *GOAL, *ARM, "--controller", "vla")
    assert result.exit_code == 1, result.output
    assert "there is no terminal to ask on" in _flat(result.output)
    assert _no_run(tmp_path)


def test_a_decision_llm_in_the_environment_is_refused_by_its_own_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "_can_prompt", lambda: True)
    monkeypatch.setenv("QUACKD_DECISION_LLM", "jev")
    result = _cli(tmp_path, *GOAL, *ARM, "--controller", "vla")
    assert result.exit_code == 1 and "QUACKD_DECISION_LLM names one" in _flat(result.output)
    off = _cli(tmp_path, *GOAL, *ARM, "--controller", "vla", "--decision-llm", "off")
    assert "decision LLM" not in _flat(off.output), "--decision-llm off opts this run out"


def test_a_task_file_that_lists_no_instruction_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "_can_prompt", lambda: True)
    duck = tmp_path / "hand-off.duck"
    duck.write_text(
        "---\nduck: 1\nname: hand-off\ndescription: Hand the arm to its policy.\nverbs:\n"
        "  allow: [manipulate, report_state, stop]\n  confirm: [manipulate]\n"
        "success: [the block is in the bowl]\n---\nPut the block in the bowl.\n",
        encoding="utf-8",
    )
    result = _cli(tmp_path, str(duck), *ARM, "--controller", "vla")
    out = _flat(result.output)
    assert result.exit_code == 1 and "and hand-off lists none" in out, result.output
    assert "policy.instructions" in out and _no_run(tmp_path)


def test_a_task_file_that_plays_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Refused after the file is read, which is after the flags are: the policy flies the arm
    with no pilot to read the part, and a run recorded as played would be one nobody played."""
    monkeypatch.setattr(cli, "_can_prompt", lambda: True)
    duck = tmp_path / "crane.duck"
    duck.write_text(
        "---\nduck: 4\nname: crane\ndescription: Play a crane.\nverbs:\n"
        "  allow: [manipulate, report_state, stop]\n  confirm: [manipulate]\n"
        "success: [the block is in the bowl]\n"
        "policy:\n  instructions: [put the block in the bowl]\n"
        "play: true\n---\nYou are a crane. Put the block in the bowl.\n",
        encoding="utf-8",
    )
    result = _cli(tmp_path, str(duck), *ARM, "--controller", "vla")
    out = _flat(result.output)
    assert result.exit_code == 1, result.output
    assert "crane gives its pilot a part to play, and --controller vla flies the arm" in out
    assert _no_run(tmp_path)


def test_a_controller_nobody_defined_is_refused(tmp_path: Path) -> None:
    result = _cli(tmp_path, *GOAL, "--controller", "gpt")
    assert result.exit_code == 1 and "--controller is llm or vla, not 'gpt'" in _flat(result.output)
    assert _no_run(tmp_path)


@pytest.mark.parametrize("blank", ["", " "], ids=["empty", "spaces"])
def test_a_blank_controller_is_the_default_as_a_blank_detector_is(
    tmp_path: Path, blank: str
) -> None:
    """Either spelling of nothing is `--controller llm`, so both go on to the same refusal
    the unnamed robot gets with no `--controller` at all."""
    nowhere = ("--robot", "no-such-robot")
    unset = _cli(tmp_path / "unset", *GOAL, *nowhere)
    given = _cli(tmp_path / "given", *GOAL, *nowhere, "--controller", blank)
    assert "no-such-robot" in _flat(unset.output), unset.output
    assert (given.exit_code, _flat(given.output)) == (unset.exit_code, _flat(unset.output))


def test_serve_mcp_refuses_a_controller_in_words() -> None:
    """Over MCP the client is the pilot: there is nothing to choose, and nobody at a terminal
    for a vla run to ask."""
    result = runner.invoke(
        cli.app, ["serve-mcp", "--robot", "lerobot:mujoco", "--controller", "vla"]
    )
    out = _flat(result.output)
    assert result.exit_code == 1 and "over MCP the client flies" in out, result.output
    assert "quackd run --controller vla" in out


def test_the_judge_prompt_is_marked_as_reaching_a_person_only_at_a_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli, "_can_prompt", lambda: True)
    assert a_person_was_asked(cli._judge_prompt)
    monkeypatch.setattr(cli, "_can_prompt", lambda: False)
    assert not a_person_was_asked(cli._judge_prompt)


@pytest.fixture
def held() -> Iterator[Serving]:
    """`quackd policy serve --policy scripted:hold`: a `manipulate` of it ends on a stall,
    which is a segment that ran and is ok."""
    serving = _serving(S.ServeOptions(policy="scripted:hold"))
    try:
        yield serving
    finally:
        serving.http.shutdown()
        serving.http.server_close()
        serving.app.close()


@pytest.mark.parametrize("said", [True, False], ids=["yes", "no"])
def test_a_vla_run_on_the_simulator_ends_on_what_the_person_said(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, held: Serving, said: bool
) -> None:
    """`quackd run --goal ... --controller vla` on the simulator's stand-in, against the real
    server: the person clears the pilot's doubt and the segment's confirm, the goal is the one
    instruction, and then they are asked whether the arm did it. Their answer is the exit code,
    and all three questions are in the record in the words they were asked in."""
    pytest.importorskip("mujoco")
    from quackd_lerobot.sim import standin

    monkeypatch.setattr("quackd_lerobot.sim.transport.default_model", standin.mjcf)
    monkeypatch.setattr(cli, "_can_prompt", lambda: True)
    asked: list[str] = []

    def terminal(question: str) -> bool:
        asked.append(question)
        return said if question == "Did the arm do it?" else True

    monkeypatch.setattr(cli, "_ask", terminal)
    result = _cli(
        tmp_path,
        *GOAL,
        "--robot",
        "lerobot:mujoco",
        "--policy-url",
        held.url,
        "--policy-token",
        TOKEN,
        "--controller",
        "vla",
    )
    if "no OpenGL context" in result.output and os.environ.get(REQUIRE_ENV) != "1":
        pytest.skip("no OpenGL context for offscreen rendering")
    assert result.exit_code == (0 if said else 1), result.output
    assert asked == [
        "Go ahead anyway?",
        "run manipulate(instruction='hold still')?",
        "Did the arm do it?",
    ]
    out = _flat(result.output)
    assert "provider vla (scripted:goal)" in out, "the header names the pilot"
    assert ("SUCCESS" if said else "FAILURE") in out
    (run_dir,) = (tmp_path / "runs").iterdir()
    events = Transcript.read(run_dir / "transcript.jsonl")
    rows = [(e["what"], e["answer"]) for e in events if e["kind"] == "prompt"]
    assert rows == [("decide", True), ("confirm", True), ("judge", said)]
    (judged,) = [e for e in events if e["kind"] == "prompt" and e["what"] == "judge"]
    assert "'hold still'" in judged["question"]
    assert "The task is done when" not in judged["question"], "a goal's success lines are a model's"
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["provider"] == V.NAME and summary["cost_usd"] == 0.0
    assert summary["policy"]["segments"] == 1
