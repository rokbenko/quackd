"""The fast lane's question, and how its answer is read (`quackd.agent.decision.commands`)."""

from __future__ import annotations

import asyncio
import math
from typing import Any

import pytest

import quackd_microduck
from quackd.adapters.factory import RobotSpec, describe
from quackd.agent.decision import commands
from quackd.agent.decision.catalogue import PRESETS
from quackd.agent.decision.commands import PILOT, UNCLEAR, CommandChooser, offers_for
from quackd.agent.decision.decisions_api import to_questions
from quackd.agent.decision.factory import make_decision_llm
from quackd.agent.decision.stepper import FLOORS
from quackd.drive.presets import presets_for
from quackd.verbs.registry import NoParams, Verb, VerbResult, registry_from_manifest
from tests import fake_laya


def _verbs(extra: list[Verb] | None = None) -> tuple[dict[str, Verb], dict[str, Any]]:
    manifest = describe(RobotSpec("microduck", "mock"))
    adapter = quackd_microduck.MicroduckAdapter.__new__(quackd_microduck.MicroduckAdapter)
    registry = registry_from_manifest(manifest, adapter)
    for verb in extra or ():
        registry.register(verb, replace=True)
    verbs = {name: registry.get(name) for name in registry.names()}
    return verbs, presets_for(manifest, verbs)


class _Answers:
    """A decision LLM that answers the one question as told, and keeps what it was asked."""

    name = "scripted"
    model = "test"
    url = None

    def __init__(self, answer: Any = None, *, raises: Exception | None = None, hangs: bool = False):
        self.answer = answer
        self.raises = raises
        self.hangs = hangs
        self.asked: list[tuple[dict[str, Any], dict[str, Any]]] = []

    async def decide(self, state: Any, questions: Any) -> Any:
        self.asked.append((dict(state), dict(questions)))
        if self.hangs:
            await asyncio.Event().wait()
        if self.raises is not None:
            raise self.raises
        return self.answer


def _chooser(llm: Any) -> CommandChooser:
    verbs, presets = _verbs()
    return CommandChooser(llm, offers_for(verbs, presets), "a small biped duck robot")


def _said(choice: Any, confidence: Any) -> dict[str, Any]:
    return {"answers": {"command": {"choice": choice, "confidence": confidence}}}


def test_every_choice_and_every_preset_is_offered_and_no_dangerous_verb() -> None:
    async def flips(_ctx: Any, _p: Any) -> VerbResult:
        return VerbResult.success("flipped")

    risky = Verb("cartwheel", "flips the body", flips, NoParams, safety_class="dangerous")
    verbs, presets = _verbs([risky])
    labels = {offer.label for offer in offers_for(verbs, presets)}
    assert {"sit", "kick(leg=left)", "gaze(direction=up)", "stop"} <= labels
    assert set(presets) <= labels
    assert "cartwheel" not in labels, "a dangerous verb was offered"
    assert "say" not in labels and "move" not in labels, "a value was offered as a choice"


async def test_one_question_with_both_ways_out_goes_out_and_reads_through_openais_shape() -> None:
    llm = _Answers(_said("sit", 0.95))
    chooser = _chooser(llm)
    await chooser.choose("have a seat", "standing still")
    ((state, questions),) = llm.asked
    assert state == {"said": "have a seat", "doing": "standing still", "body": chooser.body}
    criteria = questions["command"]["criteria"]
    assert {PILOT, UNCLEAR} <= set(criteria) and set(chooser.offers) <= set(criteria)
    (translated,) = to_questions(questions)
    assert translated["type"] == "choice" and translated["name"] == "command"


@pytest.mark.parametrize(
    ("answer", "gate"),
    [
        (_said("sit", 0.95), "taken"),
        (_said("sit", 0.70), "ask"),
        (_said("stop", 0.55), "taken"),
        (_said("report_state", 0.65), "taken"),
        (_said("forward", 0.90), "taken"),
        (_said(UNCLEAR, 0.99), "unclear"),
        (_said(PILOT, 0.90), "pilot"),
        (_said(PILOT, 0.40), "pilot_ask"),
        (_said("sit", math.nan), "unreadable"),
        (_said(None, 0.99), "unreadable"),
        (_said("cartwheel", 0.99), "unreadable"),
        ({"answers": {}}, "unreadable"),
        ({"answers": {}, "refused": ["command"]}, "refused"),
    ],
)
async def test_an_answer_is_held_to_the_steppers_floors(answer: Any, gate: str) -> None:
    """Nothing moves on a guess: below its floor a call is asked about, NaN and a label not on
    offer are no answer at all, and `pilot` starts a goal only at the read floor."""
    choice = await _chooser(_Answers(answer)).choose("whatever was said", "nothing")
    assert choice.gate == gate, choice.record
    if gate in ("taken", "ask"):
        assert choice.offer is not None and choice.floor == FLOORS[choice.offer.kind]
    assert choice.record["gate"] == gate and "latency_ms" in choice.record


async def test_a_failure_and_a_question_out_of_its_second_are_gates_and_not_raised(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failed = await _chooser(_Answers(raises=ConnectionError("refused"))).choose("sit", "")
    assert failed.gate == "error" and failed.record["error"].startswith("ConnectionError")
    monkeypatch.setattr(commands, "TIMEOUT_S", 0.01)
    late = await _chooser(_Answers(hangs=True)).choose("sit", "")
    assert late.gate == "timeout" and "no answer within" in late.record["error"]


async def test_laya_warms_its_weights_once_and_a_failed_load_waits_for_decide(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Loaded before the prompt opens, so the first line typed does not pay seven seconds, and
    never raised there: the first question reaches the pilot as any failed question does."""
    router = fake_laya.install(monkeypatch, fake_laya.FakeRouter(answers=_said("sit", 0.9)))
    llm = make_decision_llm(PRESETS["laya"])
    await llm.warm()  # type: ignore[attr-defined]
    await llm.warm()  # type: ignore[attr-defined]
    await llm.decide({"said": "sit"}, {})
    assert router.loads == 1

    broken = fake_laya.FakeRouter(rejects=("preload", "default", "max_loaded"))
    fake_laya.install(monkeypatch, broken)
    cold = make_decision_llm(PRESETS["laya"])
    await cold.warm()  # type: ignore[attr-defined]
    with pytest.raises(TypeError):
        await cold.decide({"said": "sit"}, {})
