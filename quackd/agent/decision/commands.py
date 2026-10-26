"""The fast lane's question: which of the body's own calls does a person mean by what they said?

`quackd drive` matches a line that names a call outright itself (`quackd.drive.lanes`). A line
that does not, "have a seat" or "look to your left", is asked of the run's decision LLM as one
closed question, bounded by the stepper's second, whose options are every call the body has
that is a choice rather than a value, the presets, and two ways out: `pilot`, for a line that
is more than one call, and `unclear`. The answer is read exactly as the stepper reads its own
(`stepper._answer`, `_probability`, `_refused`), and held to the stepper's floors, so nothing
moves on a guess: below the floor a person is asked first, and a question that could not be
answered hands the line to the pilot.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from quackd.agent.decision.base import TIMEOUT_S
from quackd.agent.decision.stepper import (
    FLOORS,
    _answer,
    _distribution,
    _failure,
    _field,
    _one_line,
    _probability,
    _refused,
    discrete_calls,
    verb_class,
)
from quackd.verbs.registry import Verb

if TYPE_CHECKING:
    from quackd.agent.decision.base import DecisionLLM
    from quackd.drive.presets import Preset

COMMAND = "command"
"""The one question's name."""

PILOT = "pilot"
"""The way out for a line that is more than one call: the pilot plans it."""

UNCLEAR = "unclear"
"""The way out for a line that names none of the calls, or could mean more than one."""

GATES = (
    "taken",
    "ask",
    "pilot",
    "pilot_ask",
    "unclear",
    "refused",
    "unreadable",
    "error",
    "timeout",
)
"""Every way a question ends. `taken` runs the call, `ask` asks the person whether it was the one
they meant, `unclear` asks them to say it another way, `pilot` hands the line to the pilot and
`pilot_ask` asks first, and the last four hand it to the pilot because nobody could read an
answer: declined, unreadable, failed, or out of its second."""


@dataclass(frozen=True)
class Offer:
    """One call the person may have meant, and the words it is offered in."""

    label: str
    """What the decision LLM chooses between: the stepper's own label, `kick(leg=left)`, or a
    preset's name, `forward`."""
    verb: str
    arguments: dict[str, Any]
    kind: str
    """Which of the stepper's floors it answers to (`verb_class`)."""
    what: str
    """The criterion the question gives it: the verb's own description, or the preset's walk."""
    preset: str | None = None


@dataclass(frozen=True)
class Choice:
    """How one question ended, and what the record says about it."""

    gate: str
    offer: Offer | None = None
    confidence: float | None = None
    floor: float | None = None
    record: dict[str, Any] = field(default_factory=dict)


def offers_for(
    verbs: Mapping[str, Verb],
    presets: Mapping[str, Preset],
    gated: frozenset[str] = frozenset(),
) -> list[Offer]:
    """Every call a line may mean on this body: each closed-set call of an allowed verb, a
    dangerous verb never among them, since its floor is a refusal, and every preset, which
    walks and answers to the motion floor."""
    offers: list[Offer] = []
    for name, verb in verbs.items():
        kind = verb_class(verb, name, gated)
        if kind == "never":
            continue
        for call in discrete_calls({**verb.tool_schema(), "name": name}) or []:
            what = _one_line(verb.description, 200)
            offers.append(Offer(call.label, name, dict(call.arguments), kind, what))
    taken = {offer.label for offer in offers}
    for preset in presets.values():
        if preset.name in taken:  # a verb's own call beats a preset spelled the same way
            continue
        what = f"Walk the body: {preset.says()}."
        offers.append(Offer(preset.name, "move", dict(preset.call), "motion", what, preset.name))
    return offers


class CommandChooser:
    """The fast lane: one question to the run's decision LLM about one line."""

    def __init__(self, llm: DecisionLLM, offers: Sequence[Offer], body: str) -> None:
        self.llm = llm
        self.offers = {offer.label: offer for offer in offers}
        self.body = body

    def questions(self) -> dict[str, dict[str, Any]]:
        criteria = {label: offer.what for label, offer in self.offers.items()}
        criteria[PILOT] = (
            "Several of these actions, a search, going to something named, or judging what the "
            "camera shows: more than one action, which the robot's pilot plans."
        )
        criteria[UNCLEAR] = "`said` names none of these actions, or could mean more than one."
        return {
            COMMAND: {
                "type": "choice",
                "instructions": (
                    "Which one of the robot's own actions does the person mean by `said`? "
                    "`doing` is what the robot is doing now and `body` what it is. Choose an "
                    "action only where `said` asks for that action itself."
                ),
                "criteria": criteria,
            }
        }

    async def choose(self, said: str, doing: str) -> Choice:
        """The answer to one line, read as the stepper reads its own, and never raised: a failure
        is a gate, and only an interrupt goes on up."""
        started = time.perf_counter()
        record: dict[str, Any] = {"said": said, "offered": len(self.offers)}
        bound: asyncio.Timeout | None = None
        try:
            async with asyncio.timeout(TIMEOUT_S) as bound:
                result = await self.llm.decide(
                    {"said": said, "doing": doing, "body": self.body}, self.questions()
                )
        except Exception as e:
            ran_out = bound is not None and bound.expired()
            gate = "timeout" if ran_out else "error"
            record.update(latency_ms=_ms(started), gate=gate, error=_failure(e, ran_out=ran_out))
            return Choice(gate, record=record)
        record["latency_ms"] = _ms(started)
        if declined := _refused(result):
            return Choice("refused", record={**record, "gate": "refused", "refused": declined})
        answer = _answer(result, COMMAND)
        picked = _field(answer, "choice")
        confidence, finite = _probability(_field(answer, "confidence"))
        record.update(
            choice=picked if isinstance(picked, str) else None,
            confidence=confidence if finite else None,
            probabilities=_distribution(_field(answer, "probabilities")),
        )
        # NaN loses every comparison it is put through, and a choice that is no label is no
        # answer to the question: either hands the line on rather than moving the body
        if not (isinstance(picked, str) and finite):
            return Choice("unreadable", record={**record, "gate": "unreadable"})
        if picked == UNCLEAR:
            return Choice("unclear", confidence=confidence, record={**record, "gate": "unclear"})
        if picked == PILOT:
            floor = FLOORS["read"]
            gate = "pilot" if confidence >= floor else "pilot_ask"
            return Choice(gate, None, confidence, floor, {**record, "gate": gate, "floor": floor})
        offer = self.offers.get(picked)
        if offer is None:
            return Choice("unreadable", record={**record, "gate": "unreadable"})
        floor = FLOORS[offer.kind]
        gate = "taken" if confidence >= floor else "ask"
        record.update(gate=gate, floor=floor, kind=offer.kind)
        return Choice(gate, offer, confidence, floor, record)


def _ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 1)
