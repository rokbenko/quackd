"""A task file's `policy:` section (`duck: 3`), the `manipulate` it narrows, the seconds it
budgets, and the stepper that is shown the verb and never let take it.

Everything here runs on the LeRobot mock, whose `manipulate` is a scripted segment on a clock of
its own, so a segment's seconds are exactly the ones it was told. The simulator's half, the
thinking its clock waits for, is in `tests/test_lerobot_sim.py`, beside the other lockstep tests.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import math
import re
import sys
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict

from quackd.agent.decision.factory import resolve_decision_price
from quackd.agent.decision.stepper import (
    MAX_CALLS_PER_VERB,
    SHADOW_GATE,
    Stepper,
    discrete_calls,
)
from quackd.agent.providers.base import ToolCall
from quackd.duckfile.narrow import (
    FROZEN_INFERENCE_MAX_S,
    LISTED,
    frozen_inference_s,
    narrow_policy_verb,
    policy_problem,
)
from quackd.duckfile.parser import DuckParseError, duck_from_goal, parse_duck_text
from quackd.duckfile.schema import (
    DEFAULT_POLICY_TOTAL_S,
    DEFAULT_SEGMENT_S,
    INSTRUCTION_MAX_CHARS,
    MAX_INSTRUCTIONS,
    POLICY_TOTAL_MAX_S,
    POLICY_VERB,
    SEGMENT_HEADROOM_S,
    SEGMENT_MAX_S,
    DuckFrontmatter,
    PolicySection,
)
from quackd.safety import (
    Aborted,
    Budget,
    BudgetExceeded,
    Executor,
    VerbNotAllowed,
    _segment_seconds,
    allow_all,
)
from quackd.verbs.registry import VerbRegistry, VerbResult, registry_from_manifest
from quackd_lerobot import LeRobotAdapter
from quackd_lerobot.mock import LeRobotMock
from quackd_lerobot.policy.loop import MAX_RATE_HZ
from quackd_lerobot.policy.scripted import ScriptedRunner
from quackd_lerobot.real import LeRobotReal
from quackd_lerobot.verbs import MANIPULATE_S, MANIPULATE_TIMEOUT_S, TICK_S, lerobot_verbs
from tests import fake_systemone
from tests.test_decision import BUDGET, JEV, _fake, _observation, _records
from tests.test_lerobot_adapter import FakeArm, SteppedClock

INSTRUCTIONS = ("pick up the red block", "put it in the cup", "open the gripper")
OTHERS = ("wave", "point at the cup")
SEGMENT_S = DEFAULT_SEGMENT_S / 2
"""A segment the task file asks for, other than the default, so a test can tell them apart."""
ALLOW = ("report_state", POLICY_VERB)
BOTH = (*ALLOW, "pick")
"""A task that hands the arm to its policy through `pick` as well as through `manipulate`."""


def _v3(
    instructions: tuple[str, ...] = INSTRUCTIONS,
    *,
    segment_s: float = SEGMENT_S,
    total_s: float = 2 * SEGMENT_S,
    name: str = "t",
    allow: tuple[str, ...] = ALLOW,
) -> str:
    listed = "".join(f"\n    - {line}" for line in instructions) or " []"
    moving = ", ".join(verb for verb in allow if verb != "report_state")
    return f"""\
---
duck: 3
name: {name}
description: d
verbs:
  allow: [{", ".join(allow)}]
  confirm: [{moving}]
success: [x]
policy:
  instructions:{listed}
  segment_s: {segment_s:g}
  total_s: {total_s:g}
---
# Task
Do it.
"""


def _fm(*instructions: str, segment_s: float = SEGMENT_S, total_s: float | None = None) -> Any:
    total = 2 * segment_s if total_s is None else total_s
    return parse_duck_text(_v3(instructions, segment_s=segment_s, total_s=total)).frontmatter


async def _mock_arm() -> tuple[LeRobotAdapter, LeRobotMock, VerbRegistry]:
    mock = LeRobotMock()
    adapter = LeRobotAdapter(mock)
    manifest = await adapter.connect()
    return adapter, mock, registry_from_manifest(manifest, adapter)


def _labels(registry: VerbRegistry) -> list[str] | None:
    calls = discrete_calls(registry.view(POLICY_VERB).tool_schema())
    return None if calls is None else [c.label for c in calls]


def _said(*instructions: str) -> list[str]:
    return [f"{POLICY_VERB}(instruction={line})" for line in instructions]


# ── the section ─────────────────────────────────────────────────────────────────────────


def test_a_v3_section_parses_and_a_file_without_one_gets_the_named_defaults() -> None:
    policy = parse_duck_text(_v3()).frontmatter.policy
    assert policy is not None
    assert policy.instructions == list(INSTRUCTIONS)
    assert policy.segment_s == SEGMENT_S and policy.total_s == 2 * SEGMENT_S
    goal = duck_from_goal("stack the blocks", ["report_state"], confirm=[POLICY_VERB])
    for fm in (goal.frontmatter, DuckFrontmatter.model_validate(_v2_mapping())):
        assert fm.policy is None
        assert fm.effective_policy == PolicySection()
        assert fm.effective_policy.segment_s == DEFAULT_SEGMENT_S
        assert fm.effective_policy.total_s == DEFAULT_POLICY_TOTAL_S
        assert fm.effective_policy.instructions == []


def _v2_mapping() -> dict[str, Any]:
    return {
        "duck": 2,
        "name": "t",
        "description": "d",
        "verbs": {"allow": ["report_state", POLICY_VERB]},
        "success": ["x"],
    }


@pytest.mark.parametrize(
    ("text", "needle"),
    [
        pytest.param(_v3().replace("duck: 3", "duck: 2"), "policy needs duck: 3", id="v2"),
        pytest.param(
            _v3()
            .replace("allow: [report_state, manipulate]", "allow: [report_state]")
            .replace("  confirm: [manipulate]\n", ""),
            "verbs.allow does not list",
            id="not-allowed",
        ),
        pytest.param(
            _v3().replace("success: [x]", "success: [x]\nflock:\n  members: 2"),
            "a flock duck cannot carry one",
            id="flock",
        ),
        pytest.param(
            _v3(segment_s=SEGMENT_MAX_S * 2, total_s=SEGMENT_MAX_S * 2), "segment_s", id="long"
        ),
        pytest.param(_v3(total_s=POLICY_TOTAL_MAX_S * 2), "total_s", id="total"),
        pytest.param(_v3(total_s=SEGMENT_S / 2), "shorter than one segment", id="short"),
        pytest.param(_v3(segment_s=0), "segment_s", id="zero"),
        pytest.param(
            _v3().replace(f"segment_s: {SEGMENT_S:g}", "segment_s: .nan"), "finite", id="nan"
        ),
        pytest.param(
            _v3(tuple(f"subtask {i}" for i in range(MAX_INSTRUCTIONS + 1))),
            "instructions",
            id="too-many",
        ),
        pytest.param(_v3(("x" * (INSTRUCTION_MAX_CHARS + 1),)), "characters", id="wordy"),
        pytest.param(_v3((INSTRUCTIONS[0], INSTRUCTIONS[0])), "duplicate", id="duplicate"),
        pytest.param(_v3((INSTRUCTIONS[0], "' '")), "must say something", id="blank"),
        pytest.param(_v3(('"one\\ntwo"',)), "one line", id="two-lines"),
        pytest.param(_v3().replace("  total_s:", "  seed: 1\n  total_s:"), "seed", id="unknown"),
        pytest.param(_v3(allow=BOTH), "tells it a target of the pilot's own", id="pick-and-list"),
    ],
)
def test_an_untrusted_section_is_refused_with_a_reason(text: str, needle: str) -> None:
    """A `.duck` is untrusted input: every number bounded, every instruction a short line, the
    verb it governs allowed, never next to a flock, and never next to a `pick` that would tell
    the policy words the list does not hold, all refused before anything connects."""
    with pytest.raises(DuckParseError) as refused:
        parse_duck_text(text, path="x.duck")
    assert needle.lower() in str(refused.value).lower(), str(refused.value)


def test_the_bounds_are_held_together_with_the_stepper_and_the_arms_own_timeout() -> None:
    """Every instruction a task may list is one choice the stepper can offer, the longest
    segment a task may ask for ends inside the timeout the arm registers `manipulate` with, and
    the arm's own default segment is the task file's default."""
    assert MAX_INSTRUCTIONS == MAX_CALLS_PER_VERB
    assert SEGMENT_MAX_S + SEGMENT_HEADROOM_S <= MANIPULATE_TIMEOUT_S
    assert lerobot_verbs(policy=True)[POLICY_VERB].timeout_s == MANIPULATE_TIMEOUT_S
    assert MANIPULATE_S == DEFAULT_SEGMENT_S <= SEGMENT_MAX_S
    assert DEFAULT_SEGMENT_S <= DEFAULT_POLICY_TOTAL_S <= POLICY_TOTAL_MAX_S


def test_the_example_in_the_spec_validates_plainly_and_against_the_mock_arm(
    tmp_path: Path,
) -> None:
    """docs/reference/duck-spec.md shows a whole v3 file and the `quackd validate` it passes, so the
    file it shows has to pass it. The page quotes the plain command, with no robot named, which
    checks the file against every body installed here with what each offers a policy server: that is
    where an arm's `manipulate` is, and the Microduck's list, which the command used to check
    against, refused it. Named, the mock arm keeps it as it is registered.

    A plain validate would pass a verb that some other body provides, and the page's own run
    of the file is on `lerobot:mujoco` with a policy server, which checks it against the arm as
    it describes itself before it connects. So every verb the file allows is one that arm
    provides: the file allowed `observe`, which it does not, and every run of it was refused.
    Named as it is registered, the arm holds no server, and the refusal says where to give it
    one, as the page says."""
    from typer.testing import CliRunner

    from quackd.adapters import factory
    from quackd.adapters.base import PolicyChoice
    from quackd.cli import app

    page = (Path(__file__).parents[1] / "docs" / "reference" / "duck-spec.md").read_text(
        encoding="utf-8"
    )
    # to the end of the page: the file it shows has a `## Strategy` heading of its own
    section = page.split("\n### `policy` (v3)", 1)[1]
    found = re.search(r"```yaml\n(---\n.*?)```", section, flags=re.DOTALL)
    assert found, "the policy section no longer shows a whole file"
    table = section.split("```", 1)[0]
    for said in (
        f"at most {MAX_INSTRUCTIONS}, each at most {INSTRUCTION_MAX_CHARS} characters",
        f"number > 0, at most {SEGMENT_MAX_S:g} | {DEFAULT_SEGMENT_S:g} |",
        f"this plus {SEGMENT_HEADROOM_S:g} s",
        f"number > 0, at most {POLICY_TOTAL_MAX_S:g}, and at least `segment_s` | "
        f"{DEFAULT_POLICY_TOTAL_S:g} |",
    ):
        assert said in table, f"docs/reference/duck-spec.md's policy table no longer says {said!r}"
    duck = tmp_path / "stack-blocks.duck"
    duck.write_text(found.group(1), encoding="utf-8")
    fm = parse_duck_text(found.group(1)).frontmatter
    assert fm.duck == 3 and fm.policy is not None and fm.policy.instructions
    assert "```console\n$ quackd validate stack-blocks.duck\n" in section, "the plain form"
    plain = CliRunner().invoke(app, ["validate", str(duck)])
    assert plain.exit_code == 0, plain.output
    assert "1 file valid" in plain.output and "1 file valid" in section
    result = CliRunner().invoke(app, ["validate", str(duck), "--robot", "lerobot:mock"])
    assert result.exit_code == 0, result.output
    arm = factory.parse_robot_spec("lerobot:mujoco")
    served = factory.describe(arm, policy=PolicyChoice("http://127.0.0.1"))
    provided = set(factory.registry_for(arm, served).names())
    missing = sorted(set(fm.verbs.allow) - provided)
    assert not missing, f"lerobot:mujoco with a policy does not provide {missing}"
    unserved = CliRunner().invoke(app, ["validate", str(duck), "--robot", "lerobot:mujoco"])
    assert unserved.exit_code == 1, unserved.output
    said = " ".join(unserved.output.split())
    assert f"requires {POLICY_VERB}" in said and "quackd policy serve" in said, said
    assert "--policy-url" in said, said


def test_a_plain_validate_knows_what_a_body_offers_a_policy_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The vocabulary a file that names no robot is checked against holds `pick` and
    `manipulate` because the arm offers them to a policy server, asked through the factory's
    own `policy` parameter, and not because the mock happens to be the backend described
    first: with the mock taken out of the arm's list they are still there. A description
    with a policy is static, so it reaches no server and imports no torch."""
    from quackd.adapters import factory

    arm = factory.info("lerobot")
    real = factory.parse_robot_spec("lerobot:real")
    assert not factory.describe(real).provides(POLICY_VERB), "the arm alone offers neither"
    listed = factory.info
    unmocked = dataclasses.replace(arm, backends=tuple(b for b in arm.backends if b != "mock"))
    monkeypatch.setattr(
        factory, "info", lambda name: unmocked if name == "lerobot" else listed(name)
    )
    before = set(sys.modules)
    vocabulary = factory.installed_vocabulary()
    assert {"pick", POLICY_VERB} <= set(vocabulary.names())
    assert "torch" not in set(sys.modules) - before


# ── the narrowing ───────────────────────────────────────────────────────────────────────


async def test_the_narrowed_verb_offers_the_stepper_exactly_the_listed_instructions() -> None:
    """An inline enum, so the stepper can read it where a `$ref` would hide it, one call per
    instruction and no other, and the segment and its timeout from the same number."""
    adapter, mock, registry = await _mock_arm()
    assert _labels(registry) is None, "the arm's own verb takes any words"
    narrowed = narrow_policy_verb(registry, _fm(*INSTRUCTIONS), adapter)
    assert narrowed is registry.get(POLICY_VERB)
    schema = registry.view(POLICY_VERB).tool_schema()
    assert "$ref" not in json.dumps(schema) and "$defs" not in schema["input_schema"]
    instruction = schema["input_schema"]["properties"]["instruction"]
    assert instruction["enum"] == list(INSTRUCTIONS)
    assert instruction["description"] == LISTED
    calls = discrete_calls(schema)
    assert calls is not None and len(calls) == len(INSTRUCTIONS)
    assert [c.label for c in calls] == _said(*INSTRUCTIONS)
    assert narrowed.timeout_s == SEGMENT_S + SEGMENT_HEADROOM_S
    assert mock.segment_s == SEGMENT_S and adapter.segment_s == SEGMENT_S
    assert f"up to {SEGMENT_S:g} s" in narrowed.description
    assert f"{2 * SEGMENT_S:g} s in all" in narrowed.description
    await adapter.disconnect()


async def test_every_narrowing_starts_again_from_the_verb_the_robot_registered() -> None:
    """Never cumulative: a second task file is narrowed from the body's own verb and not
    through the first one's list, a task with one instruction is one call, and no task at all
    is the arm's own verb again with the default segment."""
    adapter, mock, registry = await _mock_arm()
    own = registry.get(POLICY_VERB)
    narrow_policy_verb(registry, _fm(*INSTRUCTIONS), adapter)
    narrow_policy_verb(registry, _fm(*OTHERS, segment_s=SEGMENT_S / 2), adapter)
    assert _labels(registry) == _said(*OTHERS)
    assert registry.get(POLICY_VERB).template is own
    assert mock.segment_s == SEGMENT_S / 2
    narrow_policy_verb(registry, _fm(OTHERS[0]), adapter)
    assert _labels(registry) == _said(OTHERS[0])
    for contract in (None, duck_from_goal("stack", ["report_state"]).frontmatter):
        narrowed = narrow_policy_verb(registry, contract, adapter)
        assert narrowed is not None and narrowed.template is own
        assert _labels(registry) is None, "no list, so any words, as the arm registered it"
        assert narrowed.params is own.params
        assert narrowed.timeout_s == DEFAULT_SEGMENT_S + SEGMENT_HEADROOM_S
        assert mock.segment_s == DEFAULT_SEGMENT_S
    await adapter.disconnect()


async def test_a_list_of_one_is_an_enum_of_one_which_gemini_takes() -> None:
    """pydantic writes a `Literal` of one value as `const`, which Gemini's schema refuses, and
    with it every tool the run offers. The narrowed verb says `enum` whatever the count, and
    the stepper still reads it as the one call it is."""
    from quackd.agent.providers.gemini import render_tools

    adapter, _mock, registry = await _mock_arm()
    narrow_policy_verb(registry, _fm(OTHERS[0]), adapter)
    schema = registry.view(POLICY_VERB).tool_schema()
    assert schema["input_schema"]["properties"]["instruction"]["enum"] == [OTHERS[0]]
    (declaration,) = render_tools([schema])[0]["function_declarations"]
    rendered = declaration["parameters"]["properties"]["instruction"]
    assert "const" not in rendered and rendered["enum"] == [OTHERS[0]], rendered
    calls = discrete_calls(schema)
    assert calls is not None and [c.label for c in calls] == _said(OTHERS[0])
    await adapter.disconnect()


async def test_the_pilots_own_words_are_held_to_what_a_listed_one_would_be() -> None:
    """With no list the pilot words each subtask itself, and those words are held to the list's
    own rule, one line of plain text of at most `INSTRUCTION_MAX_CHARS`, refused as the verb's
    params before the policy is told anything. So is the target a `pick` tells the same policy,
    whose default and whose blank the backend takes as it always has."""
    adapter, mock, registry = await _mock_arm()
    narrow_policy_verb(registry, parse_duck_text(_v3(())).frontmatter, adapter)
    ex = Executor(registry, adapter, confirm=allow_all)
    longest = "x" * INSTRUCTION_MAX_CHARS
    for verb, key in ((POLICY_VERB, "instruction"), ("pick", "target")):
        schema = registry.get(verb).params.model_json_schema()["properties"][key]
        assert schema["maxLength"] == INSTRUCTION_MAX_CHARS, verb
        for words in (longest + "x", "stack it\nthen throw it", "stack it \x1b[2J"):
            refused = await ex.run_verb(verb, {key: words})
            assert not refused.ok and "invalid params" in refused.summary, refused.summary
    assert mock.policy_runs == []
    assert (await ex.run_verb(POLICY_VERB, {"instruction": longest})).ok
    for params in ({"target": longest}, {}, {"target": ""}):
        await ex.run_verb("pick", params)
    assert mock.policy_runs == [longest, longest, "object", ""]
    await adapter.disconnect()


async def test_the_executor_refuses_words_the_task_did_not_list_and_runs_its_segment() -> None:
    adapter, _mock, registry = await _mock_arm()
    fm = _fm(*INSTRUCTIONS)
    narrow_policy_verb(registry, fm, adapter)
    ex = Executor(registry, adapter, contract=fm, confirm=allow_all)
    refused = await ex.run_verb(POLICY_VERB, {"instruction": OTHERS[0]})
    assert not refused.ok and "invalid params" in refused.summary, refused.summary
    ran = await ex.run_verb(POLICY_VERB, {"instruction": INSTRUCTIONS[0]})
    assert ran.ok and ran.data["seconds"] == pytest.approx(SEGMENT_S), ran.summary
    assert f"its {SEGMENT_S:g} s ran out" in ran.summary
    await adapter.disconnect()


async def test_no_verb_changes_nothing_and_a_body_that_cannot_be_told_keeps_its_timeout() -> None:
    """No `manipulate`: nothing changes. A transport with no setter: the words are still
    narrowed, and the verb keeps its own timeout, since the segment it runs is its own."""
    empty = VerbRegistry()
    assert narrow_policy_verb(empty, _fm(*INSTRUCTIONS), object()) is None
    assert empty.names() == []
    registry = VerbRegistry()
    own = lerobot_verbs(policy=True)[POLICY_VERB]
    registry.register(own)
    narrowed = narrow_policy_verb(registry, _fm(*INSTRUCTIONS), object())
    assert narrowed is not None and narrowed.timeout_s == own.timeout_s
    assert _labels(registry) == _said(*INSTRUCTIONS)
    assert "each segment lasts" not in narrowed.description


class _Frozen:
    """A transport that can be told a segment's length, and says `said` for the seconds its
    clock stands still over one, or raises it."""

    def __init__(self, said: Any) -> None:
        self.said = said
        self.segment_s: float | None = None

    def set_segment_s(self, seconds: float) -> None:
        self.segment_s = seconds

    def frozen_inference_s(self, _segment_s: float) -> Any:
        if isinstance(self.said, BaseException):
            raise self.said
        return self.said


def test_a_simulators_frozen_seconds_are_held_to_a_named_maximum_and_never_raise() -> None:
    """The executor's timeout is the only thing on the wall's clock that ends a runner hanging on
    a lockstep simulator, so what a transport says its clock stands still for is held to
    `FROZEN_INFERENCE_MAX_S`, and what it cannot say, or raises saying, leaves the tightest
    timeout rather than refusing the run: a narrowing that raised did so after an MCP session had
    adopted the file. The maximum still covers a policy asked every tick at the fastest rate the
    loop paces, thinking for the whole of every tick of the longest segment."""
    most = FROZEN_INFERENCE_MAX_S
    for said, frozen in (
        (most / 2, most / 2),
        (math.inf, most),
        (sys.float_info.max, most),
        (10 ** (sys.float_info.max_10_exp + 1), most),
        (math.nan, 0.0),
        (-most, 0.0),
        (True, 0.0),
        (str(most), 0.0),
        (OverflowError("cannot convert float infinity to integer"), 0.0),
    ):
        transport = _Frozen(said)
        assert frozen_inference_s(transport, SEGMENT_MAX_S) == frozen, said
        registry = VerbRegistry()
        registry.register(lerobot_verbs(policy=True)[POLICY_VERB])
        narrowed = narrow_policy_verb(registry, _fm(segment_s=SEGMENT_MAX_S), transport)
        assert narrowed is not None and transport.segment_s == SEGMENT_MAX_S
        assert narrowed.timeout_s == SEGMENT_MAX_S + SEGMENT_HEADROOM_S + frozen, said
    thinking = (math.ceil(SEGMENT_MAX_S * MAX_RATE_HZ) + 1) / MAX_RATE_HZ
    assert thinking < most


# ── the budget ──────────────────────────────────────────────────────────────────────────


async def test_the_segments_seconds_are_charged_and_the_next_is_refused_once_spent() -> None:
    """Checked before each `manipulate`, charged from what each said it ran, and a segment
    refused for time charges no step."""
    adapter, _mock, registry = await _mock_arm()
    fm = _fm(*INSTRUCTIONS)
    narrow_policy_verb(registry, fm, adapter)
    budget = Budget(fm.budgets, now=adapter.now, policy_total_s=fm.effective_policy.total_s)
    ex = Executor(registry, adapter, contract=fm, budget=budget, confirm=allow_all)
    for instruction in INSTRUCTIONS[:2]:
        assert (await ex.run_verb(POLICY_VERB, {"instruction": instruction})).ok
    assert budget.policy_s == pytest.approx(fm.effective_policy.total_s)
    assert f"policy {budget.policy_s:.1f}/{budget.policy_total_s:g} s" in budget.status()
    with pytest.raises(BudgetExceeded, match="total_s"):
        await ex.run_verb(POLICY_VERB, {"instruction": INSTRUCTIONS[2]})
    assert budget.steps == 2, "the refused segment charged a step"
    assert (await ex.run_verb("report_state")).ok, "only the policy's seconds are spent"
    await adapter.disconnect()


async def test_a_pick_spends_the_same_seconds_and_is_refused_once_they_are_spent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`pick` hands the arm to the same learned policy `manipulate` does, so its seconds come
    out of the same `total_s`: what it said it ran, or the clock it ran on when it says nothing,
    as a pick that ends with nothing held does. Once they are spent the next of either verb is
    refused, and charges no step."""
    adapter, mock, registry = await _mock_arm()
    fm = parse_duck_text(_v3((), allow=BOTH)).frontmatter
    narrow_policy_verb(registry, fm, adapter)
    budget = Budget(fm.budgets, now=adapter.now, policy_total_s=fm.effective_policy.total_s)
    ex = Executor(registry, adapter, contract=fm, budget=budget, confirm=allow_all)
    picked = await ex.run_verb("pick", {"target": "the red block"})
    assert picked.ok and budget.policy_s == picked.data["seconds"] > 0, picked.summary
    assert (await ex.run_verb(POLICY_VERB, {"instruction": OTHERS[0]})).ok
    assert budget.policy_s == pytest.approx(picked.data["seconds"] + SEGMENT_S)

    monkeypatch.setattr(mock, "_near_object", lambda: False)
    spent, before = budget.policy_s, adapter.now()
    missed = await ex.run_verb("pick", {"target": "the red block"})
    assert not missed.ok and "seconds" not in missed.data, missed.summary
    charged = budget.policy_s - spent
    assert charged > 0 and charged == pytest.approx(adapter.now() - before)

    assert (await ex.run_verb(POLICY_VERB, {"instruction": OTHERS[0]})).ok
    assert budget.policy_s >= budget.policy_total_s
    steps = budget.steps
    for verb, params in (
        ("pick", {"target": "the red block"}),
        (POLICY_VERB, {"instruction": "x"}),
    ):
        with pytest.raises(BudgetExceeded, match="total_s"):
            await ex.run_verb(verb, params)
    assert budget.steps == steps, "a refused segment charged a step"
    assert mock.policy_runs == ["the red block", OTHERS[0], "the red block", OTHERS[0]]
    await adapter.disconnect()


def test_a_segment_is_charged_what_it_said_or_else_the_robots_clock_across_the_call() -> None:
    said = VerbResult.success("ran", seconds=SEGMENT_S)
    assert _segment_seconds(said, 0.0, 99.0) == SEGMENT_S
    for data in ({}, {"seconds": True}, {"seconds": float("nan")}, {"seconds": "4"}):
        silent = VerbResult.fail("timed out", **data)
        assert _segment_seconds(silent, 1.0, 1.0 + SEGMENT_S) == SEGMENT_S, data
        assert _segment_seconds(silent, 1.0, None) == 0.0, "nobody could time it"
        assert _segment_seconds(silent, None, None) == 0.0
    assert _segment_seconds(VerbResult.success("ran", seconds=-1.0), 0.0, 1.0) == 0.0
    # a call that ended with no result at all, cancelled or aborted, is timed the same way
    assert _segment_seconds(None, 1.0, 1.0 + SEGMENT_S) == SEGMENT_S
    assert _segment_seconds(None, None, 1.0) == 0.0
    assert Budget(DuckFrontmatter.model_validate(_v2_mapping()).budgets).policy_total_s == (
        DEFAULT_POLICY_TOTAL_S
    )


async def _until(adapter: LeRobotAdapter, t: float) -> None:
    """Let the mock's segments run until its clock reads `t`: they are the only sleepers on it."""
    for _ in range(10_000):
        if adapter.now() >= t:
            return
        await asyncio.sleep(0)
    raise AssertionError(f"nothing ran the mock's clock to {t:g} s")


async def test_a_segment_cancelled_or_aborted_mid_way_is_charged_the_clock_it_ran_on() -> None:
    """A call cancelled from outside, as an MCP client's is when it gives up on it, and one an
    abort ends mid-segment leave with no result, and each is charged the robot's clock across
    the call: a client that cancelled every call just short of its end would otherwise run the
    policy as long as it liked with nothing charged. The next segment is let in once the
    cancelled one has gone."""
    adapter, mock, registry = await _mock_arm()
    fm = _fm(*INSTRUCTIONS, total_s=4 * SEGMENT_S)
    narrow_policy_verb(registry, fm, adapter)
    budget = Budget(fm.budgets, now=adapter.now, policy_total_s=fm.effective_policy.total_s)
    ex = Executor(registry, adapter, contract=fm, budget=budget, confirm=allow_all)

    before = adapter.now()
    call = asyncio.create_task(ex.run_verb(POLICY_VERB, {"instruction": INSTRUCTIONS[0]}))
    await _until(adapter, before + SEGMENT_S / 2)
    call.cancel()
    with pytest.raises(asyncio.CancelledError):
        await call
    assert 0 < budget.policy_s < SEGMENT_S
    assert budget.policy_s == pytest.approx(adapter.now() - before)

    spent = budget.policy_s
    ran = await ex.run_verb(POLICY_VERB, {"instruction": INSTRUCTIONS[1]})
    assert ran.ok and budget.policy_s == pytest.approx(spent + SEGMENT_S), ran.summary

    spent, before = budget.policy_s, adapter.now()
    call = asyncio.create_task(ex.run_verb(POLICY_VERB, {"instruction": INSTRUCTIONS[2]}))
    await _until(adapter, before + SEGMENT_S / 2)
    ex.abort.set()
    with pytest.raises(Aborted):
        await call
    charged = budget.policy_s - spent
    assert 0 < charged < SEGMENT_S and charged == pytest.approx(adapter.now() - before)
    assert mock.policy_runs == list(INSTRUCTIONS)
    await adapter.disconnect()


async def test_a_second_segment_is_refused_while_one_runs_so_one_at_most_overruns() -> None:
    """Over MCP every call is a task of its own. A second segment let in while the first ran was
    checked against seconds nobody had charged yet, and then ran its own, so the arm could run
    past `total_s` by as many segments as calls came in. It is refused while one runs, a `pick`
    as well as a `manipulate`, and charges no step; `stop` is not a segment and still runs, and
    once the first has been charged the budget alone decides."""
    adapter, mock, registry = await _mock_arm()
    fm = parse_duck_text(_v3((), allow=BOTH)).frontmatter
    narrow_policy_verb(registry, fm, adapter)
    budget = Budget(fm.budgets, now=adapter.now, policy_total_s=fm.effective_policy.total_s)
    budget.policy_s = budget.policy_total_s - SEGMENT_S / 4
    ex = Executor(registry, adapter, contract=fm, budget=budget, confirm=allow_all)

    before = adapter.now()
    first = asyncio.create_task(ex.run_verb(POLICY_VERB, {"instruction": OTHERS[0]}))
    await _until(adapter, before + SEGMENT_S / 2)
    steps = budget.steps
    for verb, params in (
        (POLICY_VERB, {"instruction": OTHERS[1]}),
        ("pick", {"target": "the red block"}),
    ):
        with pytest.raises(VerbNotAllowed, match=f"{POLICY_VERB} is still running a segment"):
            await ex.run_verb(verb, params)
    assert budget.steps == steps, "a refused segment charged a step"
    assert (await ex.run_verb("stop")).ok
    stopped = await first
    assert not stopped.ok and "stopped" in stopped.summary, stopped.summary
    assert budget.policy_total_s < budget.policy_s <= budget.policy_total_s + SEGMENT_S
    with pytest.raises(BudgetExceeded, match="total_s"):
        await ex.run_verb(POLICY_VERB, {"instruction": OTHERS[1]})
    assert mock.policy_runs == [OTHERS[0]]
    await adapter.disconnect()


async def test_a_segment_whose_do_is_refused_before_it_began_is_charged_nothing() -> None:
    """A policy whose reset raises, however long it took to on the robot's clock, is refused
    before the arm is handed to it. Each verb says it ran 0 s, so the executor charges nothing,
    where it would otherwise have charged the clock the refusal took, as it does for a call it
    ends itself."""
    clock = SteppedClock()
    runner = ScriptedRunner(lambda _o, _s: None, rate_hz=1 / TICK_S)
    took = SEGMENT_S / 2

    def fails() -> None:
        # on the runner's worker, while the start waits on it and nothing else moves the clock
        clock.t += took
        raise RuntimeError("the checkpoint would not load")

    runner._on_reset = fails
    arm = FakeArm(camera=False)
    adapter = LeRobotAdapter(LeRobotReal("COM5", robot=arm, policy=runner, clock=clock))
    registry = registry_from_manifest(await adapter.connect(), adapter)
    fm = parse_duck_text(_v3((), allow=BOTH)).frontmatter
    narrow_policy_verb(registry, fm, adapter)
    budget = Budget(fm.budgets, now=adapter.now, policy_total_s=fm.effective_policy.total_s)
    ex = Executor(registry, adapter, contract=fm, budget=budget, confirm=allow_all)
    try:
        for verb, params in ((POLICY_VERB, {"instruction": "wave"}), ("pick", {"target": "cup"})):
            before = adapter.now()
            refused = await ex.run_verb(verb, params)
            assert not refused.ok and "its reset raised" in refused.summary, refused.summary
            assert refused.data["seconds"] == 0.0
            assert adapter.now() - before >= took, (
                "the refusal took no time, so this proves nothing"
            )
        assert budget.policy_s == 0.0
    finally:
        await adapter.close()


async def test_an_mcp_session_narrows_at_connect_and_on_load_and_carries_the_seconds(
    tmp_path: Path,
) -> None:
    """With no task the verb takes any words and runs the default segment under the default
    total. A task loaded narrows it to its own, and a second one narrows from the arm's verb.
    The seconds a policy has driven the arm are the session's from the start: the first load
    keeps what was spent with no task, and the second what the first spent, as steps and model
    calls are kept."""
    from quackd.mcp_server import build_server

    adapter = LeRobotAdapter(LeRobotMock())
    _server, session = build_server(adapter, yes=True, heartbeat_period_s=0.05, memory=False)
    await session.connect()
    try:
        verb = session.registry.get(POLICY_VERB)
        assert verb.timeout_s == DEFAULT_SEGMENT_S + SEGMENT_HEADROOM_S
        assert adapter.segment_s == DEFAULT_SEGMENT_S
        assert session.executor.budget is not None
        assert session.executor.budget.policy_total_s == DEFAULT_POLICY_TOTAL_S
        assert _labels(session.registry) is None
        await session.assess({"verdict": "feasible", "reason": "a test"}, {})
        ran = await session.run(POLICY_VERB, {"instruction": OTHERS[0]})
        assert ran["ok"], ran
        taskless = session.executor.budget.policy_s
        assert taskless == pytest.approx(DEFAULT_SEGMENT_S)

        first, second = tmp_path / "first.duck", tmp_path / "second.duck"
        first.write_text(_v3(name="first", total_s=taskless + 2 * SEGMENT_S), encoding="utf-8")
        wider = 4 * SEGMENT_S
        second.write_text(
            _v3(OTHERS, segment_s=SEGMENT_S / 2, total_s=wider, name="second"), encoding="utf-8"
        )
        assert session.load(str(first))["ok"]
        assert session.executor.budget.policy_s == taskless, "the first load refunded the seconds"
        assert _labels(session.registry) == _said(*INSTRUCTIONS)
        await session.assess({"verdict": "feasible", "reason": "a test"}, {})
        ran = await session.run(POLICY_VERB, {"instruction": INSTRUCTIONS[0]})
        assert ran["ok"], ran
        spent = session.executor.budget.policy_s
        assert spent == pytest.approx(taskless + SEGMENT_S)

        assert session.load(str(second))["ok"]
        budget = session.executor.budget
        assert budget is not None and budget.policy_s == spent, "a load refunded the seconds"
        assert budget.policy_total_s == wider
        assert _labels(session.registry) == _said(*OTHERS)
        assert session.registry.get(POLICY_VERB).timeout_s == SEGMENT_S / 2 + SEGMENT_HEADROOM_S
        assert adapter.segment_s == SEGMENT_S / 2
    finally:
        await session.close()


class _TargetOnly(BaseModel):
    """A `manipulate` of some other body's, told a target rather than an instruction."""

    model_config = ConfigDict(extra="forbid")

    target: str = "object"


async def test_a_file_the_bodys_verb_cannot_be_held_to_leaves_the_session_as_it_was(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A list of instructions needs a `manipulate` that takes one. A body whose verb does not is
    refused the file before anything is adopted, so the task, its verdict, its budget and the
    verb all stay the last file's, and the narrowing itself refuses it the same way with
    nothing changed."""
    from quackd.mcp_server import build_server

    adapter = LeRobotAdapter(LeRobotMock())
    registry = registry_from_manifest(await adapter.connect(), adapter)
    await adapter.disconnect()
    own = dataclasses.replace(registry.get(POLICY_VERB), params=_TargetOnly)
    registry.register(own, replace=True)
    _server, session = build_server(
        adapter, yes=True, heartbeat_period_s=0.05, memory=False, registry=registry
    )
    await session.connect()
    try:
        first, listed = tmp_path / "first.duck", tmp_path / "listed.duck"
        first.write_text(_v3((), name="first"), encoding="utf-8")
        listed.write_text(_v3(name="listed"), encoding="utf-8")
        assert session.load(str(first))["ok"]
        await session.assess({"verdict": "feasible", "reason": "a test"}, {})
        verb, budget, verdict = (
            session.registry.get(POLICY_VERB),
            session.executor.budget,
            session.executor.verdict,
        )
        refused = session.load(str(listed))
        assert not refused["ok"] and "takes no instruction" in refused["error"], refused
        assert session.duck is not None and session.duck.name == "first"
        assert session.executor.contract is not None and session.executor.contract.name == "first"
        assert session.executor.verdict is verdict is not None
        assert session.executor.budget is budget
        assert session.registry.get(POLICY_VERB) is verb
        contract = parse_duck_text(_v3()).frontmatter
        assert policy_problem(session.registry, contract) == refused["error"]
        with pytest.raises(ValueError, match="takes no instruction"):
            narrow_policy_verb(session.registry, contract, adapter)
        assert session.registry.get(POLICY_VERB) is verb

        # a segment the backend itself refuses is refused the same way, before the file is
        # adopted, rather than raised out of the load with the file half taken
        longer = tmp_path / "longer.duck"
        longer.write_text(_v3((), segment_s=2 * SEGMENT_S, name="longer"), encoding="utf-8")
        said = "this arm runs no segment that long: ask for a shorter one"

        def refuses(_seconds: float) -> None:
            raise ValueError(said)

        monkeypatch.setattr(adapter, "set_segment_s", refuses)
        assert session.load(str(longer)) == {"ok": False, "error": said}
        assert session.duck is not None and session.duck.name == "first"
        assert session.executor.contract is not None and session.executor.contract.name == "first"
        assert session.executor.verdict is verdict and session.executor.budget is budget
        assert session.registry.get(POLICY_VERB) is verb
    finally:
        await session.close()


# ── the stepper ─────────────────────────────────────────────────────────────────────────


async def _stepper(llm: Any) -> tuple[Stepper, LeRobotAdapter]:
    adapter, _mock, registry = await _mock_arm()
    narrow_policy_verb(registry, _fm(*INSTRUCTIONS), adapter)
    stepper = Stepper.build(
        mode="on",
        llm=llm,
        price=resolve_decision_price(JEV),
        registry=registry,
        allow=["report_state", "stop", POLICY_VERB],
        goal="Stack the blocks",
        gated=[POLICY_VERB],
    )
    return stepper, adapter


async def test_the_stepper_is_offered_each_segment_and_takes_none_however_sure_it_is() -> None:
    """`manipulate` answers to the confirm floor, and under `--yes` nobody is asked there, so
    an answer that cleared it would start a segment with nobody involved. It clears every gate
    a taken answer clears, is recorded as having done so, and the turn goes to the model."""
    choice = _said(INSTRUCTIONS[1])[0]
    stepper, adapter = await _stepper(_fake(answers=fake_systemone.turn(choice, 0.99)))
    advice = await stepper.advise(await _observation(adapter), cleared=True, budget=BUDGET)
    assert set(_said(*INSTRUCTIONS)) <= set(advice.record["labels"])
    assert advice.call is None and advice.gate == SHADOW_GATE, advice.record
    assert advice.record["class"] == "confirm" and stepper.taken == 0
    assert stepper.compares(advice)
    event = stepper.shadow_event(
        advice, ToolCall(name=POLICY_VERB, arguments={"instruction": INSTRUCTIONS[1]})
    )
    assert event["agree"] is True and event["would_have_acted"] is False

    unsure = await _stepper(_fake(answers=fake_systemone.turn(choice, 0.5)))
    below = await unsure[0].advise(await _observation(unsure[1]), cleared=True, budget=BUDGET)
    assert below.gate == "below_floor", "a floor it missed is still said as the floor"
    await adapter.disconnect()
    await unsure[1].disconnect()


class _Seen:
    """A scripted pilot that keeps the tools it was handed each turn."""

    def __init__(self, script: list[ToolCall]) -> None:
        from quackd.agent.providers.fake import FakeProvider

        self.inner = FakeProvider(script=script)
        self.tools: list[list[dict[str, Any]]] = []
        self.texts: list[str] = []

    name = property(lambda self: self.inner.name)
    model = property(lambda self: self.inner.model)
    supports_vision = property(lambda self: self.inner.supports_vision)

    async def step(self, system: str, history: list[Any], tools: list[Any]) -> Any:
        self.tools.append(list(tools))
        self.texts.append(history[-1].observation.text)
        return await self.inner.step(system, history, tools)


async def _run(tmp_path: Path, mode: str) -> tuple[Any, _Seen]:
    from quackd.agent.loop import RunConfig, run_duck

    duck = parse_duck_text(_v3(name=f"stack-{mode}"))
    pilot = _Seen(
        [
            ToolCall(name=POLICY_VERB, arguments={"instruction": INSTRUCTIONS[0]}),
            ToolCall(name=POLICY_VERB, arguments={"instruction": INSTRUCTIONS[1]}),
            ToolCall(name="declare_success", arguments={"reason": "stacked"}),
        ]
    )
    stub = _fake(answers=fake_systemone.turn(_said(INSTRUCTIONS[0])[0], 0.99))
    result = await run_duck(
        RunConfig(
            duck=duck,
            provider=pilot,  # type: ignore[arg-type]
            transport=LeRobotAdapter(LeRobotMock()),
            runs_dir=tmp_path,
            confirm=allow_all,
            decision=mode,  # type: ignore[arg-type]
            decision_llm=stub,
            decision_price=resolve_decision_price(JEV),
        )
    )
    return result, pilot


@pytest.mark.parametrize("mode", ["shadow", "on"])
async def test_a_run_compares_every_segment_the_stepper_would_start_and_starts_none(
    tmp_path: Path, mode: str
) -> None:
    """With the stub sure of a segment every turn and every confirm answered yes: in shadow
    and in on alike, the segments are the model's, the stepper's answer on each of those turns
    is recorded beside the model's, and the model and the stepper were shown the same verb."""
    result, pilot = await _run(tmp_path, mode)
    assert result.outcome == "success", result.outcome
    segments = [r for r in _records(result, "verb_start") if r["name"] == POLICY_VERB]
    assert len(segments) == 2 and all(r["source"] == "agent" for r in segments), segments
    asked = _records(result, "decision")
    assert SHADOW_GATE in [r["gate"] for r in asked], [r["gate"] for r in asked]
    assert not any((r.get("call") or {}).get("name") == POLICY_VERB for r in asked)
    compared = _records(result, "decision_shadow")
    choice = _said(INSTRUCTIONS[0])[0]
    on_segments = [r for r in compared if r["model_verb"] == POLICY_VERB]
    assert [r["agree"] for r in on_segments] == [True, False], compared
    assert all(r["decision_choice"] == choice and not r["would_have_acted"] for r in compared)
    summary = json.loads((result.run_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["decision"]["taken"] == 0

    # the schema the model was handed is the one the stepper enumerated
    shown = [t for tools in pilot.tools for t in tools if t["name"] == POLICY_VERB]
    assert shown and all(t == shown[0] for t in shown)
    offered = {label for r in asked for label in r["labels"] if label.startswith(POLICY_VERB)}
    calls = discrete_calls(shown[0])
    assert calls is not None and {c.label for c in calls} == offered == set(_said(*INSTRUCTIONS))
    assert any(f"policy {SEGMENT_S:.1f}/" in text for text in pilot.texts), pilot.texts


def test_the_shadow_only_verbs_are_the_one_the_policy_section_governs() -> None:
    from quackd.agent.decision.stepper import SHADOW_ONLY

    assert frozenset({POLICY_VERB}) == SHADOW_ONLY
