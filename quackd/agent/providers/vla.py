"""`--controller vla`: a scripted pilot that hands the arm's learned policy one subtask at a time
and leaves the task itself to the person who watched it.

A vision-language-action policy is good at contact and says nothing about the task it was given:
it has no judgement of the body, no report of the table between segments, and no notion of done.
So this pilot claims none of those either:

- **Its verdict is `uncertain`, always.** A person decides whether this body should try the task
  at all (`RunConfig.decide`), and a run with nobody to decide it ends there.
- **It calls `manipulate` once per instruction, in order.** The task file's
  `policy.instructions`, or the `--goal` text as the only one. Each goes through the same
  narrowed verb, executor and budget as any pilot's call, so the list, the confirm gate and
  `total_s` hold for it too.
- **It declares from a person's answer.** After the last segment the loop asks whether the arm
  did it (`RunConfig.judge`) and puts the answer on the next observation (`JUDGE_FEATURE`). A
  yes from a person really asked is success. A no, an answer no person gave, a prompt that
  raised before anybody answered, nobody to ask, or a segment that did not end ok is failure.

What it reads is the observation's `last_result` and that answer, and nothing else: never the
simulator's own truth about the table, which `quackd preflight` judges on its own. It calls no
model, so it spends no tokens and is priced as the scripted pilot is, at nothing
(`providers.pricing`).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from quackd.agent.prompts import ASSESS_TASK_NAME
from quackd.agent.providers.base import (
    JUDGE_FEATURE,
    Exchange,
    Observation,
    ProviderTurn,
    ToolCall,
    Usage,
)
from quackd.duckfile.schema import POLICY_VERB, instruction_line

NAME = "vla"
"""The pilot's name in the record, the header and the price: `--controller vla`."""

ASSESS = str(ASSESS_TASK_NAME)
"""The verdict tool's name, which the prompts module holds as a value of its schema."""

VERDICT_REASON = (
    "it is a scripted pilot handing the arm's learned policy one subtask at a time, and neither "
    "of them can judge whether this body can do the task, so the person watching it decides"
)
"""What the pilot says with its `uncertain`. The person reads it after "The pilot is not sure
this robot can do the task:", as the case they decide on."""


def _last(obs: Observation) -> dict[str, Any]:
    return obs.features.get("last_result") or {}


def _quoted(instructions: Sequence[str]) -> str:
    return ", ".join(repr(line) for line in instructions)


def _segments(n: int) -> str:
    return f"{n} segment" + ("" if n == 1 else "s")


class VlaProvider:
    """The pilot `--controller vla` flies: see the module docstring. A `JudgedPilot`, so the
    loop asks a person the question it names and holds its declare to their answer."""

    name = NAME
    supports_vision = False

    def __init__(
        self, instructions: Sequence[str], *, success: Sequence[str] = (), label: str = "task"
    ) -> None:
        if not instructions:
            raise ValueError(
                "--controller vla needs at least one instruction to hand the arm's policy: list "
                "them under policy.instructions in a task file at duck: 3 or later, or give "
                "--goal"
            )
        # held to the one rule every instruction is, so a pilot built from Python is refused
        # the same words a task file or a goal would be
        self.instructions = tuple(instruction_line(line) for line in instructions)
        self.success = tuple(success)
        self.model = f"scripted:{label}"
        self.calls = 0

    # ── what the loop asks ──────────────────────────────────────────────────────────

    def ran(self, history: Sequence[Exchange]) -> list[str]:
        """The instructions whose segment came back with a result, in order: each `manipulate`
        this pilot decided, answered by the observation after it. One the budget refused before
        it began has no observation after it, and is not counted."""
        done: list[str] = []
        for i, exchange in enumerate(history[:-1]):
            decision = exchange.decision
            if decision is None or decision.tool_call.name != POLICY_VERB:
                continue
            if _last(history[i + 1].observation).get("verb") == POLICY_VERB:
                done.append(str(decision.tool_call.arguments.get("instruction", "")))
        return done

    def judge_question(
        self, history: Sequence[Exchange], *, cut_short: str | None = None
    ) -> str | None:
        """What a person is asked about the arm, or None when it is not yet time.

        Time is once the last instruction's segment has run and ended ok, or, with `cut_short`,
        once a budget has ended the run with at least one segment behind it. A segment that did
        not end ok is not asked about at all: the pilot declares failure in its own words."""
        if not history:
            return None
        ran = self.ran(history)
        task = self._task()
        if cut_short is not None:
            if not ran:
                return None
            return (
                f"The run stopped after {_segments(len(ran))} of the arm's learned policy, of the "
                f"{len(self.instructions)} this task lists ({cut_short}). It was told "
                f"{_quoted(ran)}. {task}Nothing on the arm can tell whether that much was done: "
                "look at it."
            )
        last = _last(history[-1].observation)
        if last.get("verb") != POLICY_VERB or not last.get("ok"):
            return None
        if len(ran) < len(self.instructions):
            return None
        return (
            f"The arm's learned policy has run every subtask this task lists, "
            f"{_segments(len(ran))}: {_quoted(ran)}. {task}Nothing on the arm can tell "
            "whether the task is done: look at it."
        )

    def _task(self) -> str:
        """The task file's own words for done, where it has any, for the person to judge by,
        as one sentence however each of them ends."""
        said = [line.strip().rstrip(".") for line in self.success if line.strip().rstrip(".")]
        if not said:
            return ""
        return "The task is done when: " + "; ".join(said) + ". "

    # ── the pilot ───────────────────────────────────────────────────────────────────

    def _next(
        self, obs: Observation, history: Sequence[Exchange], tools: list[dict[str, Any]]
    ) -> ToolCall:
        decided = [ex.decision.tool_call for ex in history if ex.decision is not None]
        offered = any(tool.get("name") == ASSESS for tool in tools)
        if offered and not any(call.name == ASSESS for call in decided):
            return ToolCall(
                name=ASSESS,
                arguments={
                    "verdict": "uncertain",
                    "reason": VERDICT_REASON,
                    "limits_consulted": [],
                    "estimates": [],
                    "needs": {},
                },
            )
        last = _last(obs)
        judged = obs.features.get(JUDGE_FEATURE)
        if isinstance(judged, dict):
            return self._declare(judged, history)
        if last.get("verb") == ASSESS and not last.get("ok"):
            return ToolCall(
                name="declare_failure",
                arguments={
                    "reason": "a scripted pilot never clears a task itself, and nobody was there "
                    f"to decide whether this body should try it: {last.get('summary', '')}"
                },
            )
        handed = sum(1 for call in decided if call.name == POLICY_VERB)
        if last.get("verb") == POLICY_VERB and not last.get("ok"):
            told = self.instructions[handed - 1] if 0 < handed <= len(self.instructions) else ""
            return ToolCall(
                name="declare_failure",
                arguments={
                    "reason": f"segment {handed} of {len(self.instructions)}, {told!r}, did not "
                    f"end ok, so no other was started: {last.get('summary', '')}"
                },
            )
        if handed < len(self.instructions):
            return ToolCall(name=POLICY_VERB, arguments={"instruction": self.instructions[handed]})
        # every segment ran and nobody was asked about them: a loop that never asks a
        # `JudgedPilot` anything, which cannot make this run a success
        return ToolCall(
            name="declare_failure",
            arguments={
                "reason": f"every segment ran ({_quoted(self.instructions)}), and no person was "
                "asked whether the arm did the task, which only a person can say"
            },
        )

    def _declare(self, judged: dict[str, Any], history: Sequence[Exchange]) -> ToolCall:
        """Success on a yes from a person really asked, and failure on everything else."""
        ran = _quoted(self.ran(history) or self.instructions)
        if judged.get("asked") is True and judged.get("answer") is True:
            return ToolCall(
                name="declare_success",
                arguments={
                    "reason": f"a person watched the arm and said it did the task, after "
                    f"its learned policy was told {ran}"
                },
            )
        if judged.get("raised"):
            # EOF or click's `Abort` at the prompt: nobody said no, and nobody said yes either
            why = (
                "the question whether the arm did the task went unanswered: the prompt raised "
                f"{judged['raised']}"
            )
        elif judged.get("asked") is True:
            why = "a person watched the arm and said it did not do the task"
        elif judged.get("answer") is None:
            why = "nobody was there to say whether the arm did the task"
        else:
            # a pipe on stdin, or a standing answer: it said something, and nobody watched
            why = (
                "no person was asked whether the arm did the task, and an answer from "
                "anything else counts for nothing"
            )
        return ToolCall(
            name="declare_failure", arguments={"reason": f"{why}, after its policy was told {ran}"}
        )

    async def step(
        self, system: str, history: list[Exchange], tools: list[dict[str, Any]]
    ) -> ProviderTurn:
        obs = history[-1].observation
        call = self._next(obs, history, tools)
        self.calls += 1
        # No tokens: nothing reads the prompt or writes an answer, and a made-up count would
        # price a run that called nothing. The thinking row is bracketed as the scripted
        # pilot's is, so the rule is never mistaken for reasoning.
        return ProviderTurn(
            tool_calls=[call.model_copy(update={"id": f"vla-{self.calls}"})],
            text=None,
            usage=Usage(),
            stop_reason="tool_use",
            thinking=f"[scripted] step {self.calls}: the rule picks {call.name}",
        )
