"""The discrete stepper, against a real decision LLM, over the network.

Jev, because it is the one preset with a hosted API and a published rate: there is an address
to reach without standing a server up first, and a figure of its own to check the measurement
against. Nothing here is about TypeSafe otherwise. Naming another preset in `PRESET` below --
`kev` on your own GPU, `von` on your own CPU -- is the whole of what it takes to point these
at it, and that is the point of running them at all.

Everything in `tests/test_decision.py` fakes the answer: a stub returns whatever the test
scripted, which proves the classification, the router and the loop hook and proves nothing at
all about the model. These tests are the other half, and there is one number they exist to
produce.

`docs/guides/decision-llms/README.md` estimates what a decision LLM saves from TypeSafe's published
0.114 s a call. Nobody has checked that against the shape of request quackd actually builds, from an
ordinary developer's network, so every speed figure in this repository is arithmetic rather
than a measurement. The first test here is what turns it into one, and it prints what it
measured so the number can be read off the terminal and put in an issue.

They cost money and need that preset's key, so they are opt-in twice over: the `live_decision`
marker and `QUACKD_LIVE_DECISION=1`. CI sets neither. Run them with

    QUACKD_LIVE_DECISION=1 uv run pytest tests/test_live_decision.py -m live_decision -s

and keep them cheap: each one is a single fan-out against the mock arm, not a run. `-s`
matters, because what these are for is the number they print.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from quackd.agent.decision.catalogue import PRESETS
from quackd.agent.decision.factory import make_decision_llm, resolve_decision_price
from quackd.agent.decision.stepper import FLOORS, Stepper
from quackd.agent.providers.base import Observation
from quackd.verbs.registry import registry_from_manifest

ALLOW = ["report_state", "stop", "gripper", "place", "move_joints"]

PRESET = PRESETS["jev"]
"""Which decision LLM these run against. One name, and every test here moves with it."""


def _live_or_skip() -> None:
    if os.environ.get("QUACKD_LIVE_DECISION") != "1":
        pytest.skip("live stepper tests are opt-in: set QUACKD_LIVE_DECISION=1")
    pytest.importorskip("typesafe_sdk", reason="needs the optional extra quackd[decision]")
    # The CLI loads `.env` at startup and a developer's key usually lives there rather than in
    # the shell, so look there too before deciding there is no key, in the CLI's own order.
    try:
        from dotenv import load_dotenv

        load_dotenv(Path.cwd() / ".env")
        load_dotenv()
    except ImportError:  # pragma: no cover - dotenv ships with the CLI
        pass
    if PRESET.key_env and not os.environ.get(PRESET.key_env):
        pytest.skip(f"no {PRESET.key_env}, in the environment or a .env")


async def _arm_turn(goal: str, *, cleared: bool = True) -> tuple[Any, Any]:
    """One real fan-out against the mock arm's state. Returns (advice, stepper)."""
    from quackd_lerobot import LeRobotAdapter
    from quackd_lerobot.mock import LeRobotMock

    adapter = LeRobotAdapter(LeRobotMock())
    manifest = await adapter.connect()
    stepper = Stepper.build(
        mode="on",
        # Built here the way the CLI builds it, from the row and nothing else: the address,
        # the key variable, the model id and the rate all come off `PRESET`, so pointing
        # these tests at another decision LLM is one name at the top of this file.
        llm=make_decision_llm(PRESET),
        price=resolve_decision_price(PRESET),
        registry=registry_from_manifest(manifest, adapter),
        allow=ALLOW,
        goal=goal,
        success=["You have said whether anything is held."],
        body=manifest.summary(),
    )
    obs = Observation(
        text="x",
        features={
            "state": (await adapter.get_state()).model_dump(),
            "detections": [],
            "last_result": None,
            "allowed": ALLOW,
        },
    )
    try:
        advice = await stepper.advise(
            obs, cleared=cleared, budget="step 0/12, llm calls 0/12, 0.0/3 min"
        )
    finally:
        await adapter.disconnect()
    return advice, stepper


@pytest.mark.live_decision
async def test_a_real_decision_llm_answers_an_arm_state_and_says_how_long_it_took() -> None:
    """The measurement this repository does not have.

    The assertions are about the machinery rather than the answer: that the request quackd
    builds is one a real server accepts, that what comes back has the shape the router reads,
    and that it arrived inside the timeout the loop gives it. Which verb it picks on a folded
    mock arm is the model's business and not a thing to pin.

    What matters is printed. `docs/guides/decision-llms/README.md` quotes TypeSafe's 0.114 s, and
    this is the same number measured on the request quackd actually sends, from wherever you are.
    """
    _live_or_skip()
    advice, stepper = await _arm_turn("Say whether you are holding anything, then let it go")

    assert advice.record.get("error") is None, advice.record["error"]
    assert advice.gate in {"taken", "below_floor", "escalate", "done", "need_human"}
    latency = advice.record["latency_s"]
    chars = advice.record["state_chars"]
    print(
        f"\n{stepper.name} {stepper.model}: {latency:.3f} s, {chars} chars of state, "
        f"gate={advice.gate}, choice={advice.record.get('choice')!r}, "
        f"confidence={advice.record.get('confidence')}"
    )
    print(f"  probabilities: {advice.record.get('probabilities')}")
    print(f"  done={advice.record.get('done')} need_human={advice.record.get('need_human')}")
    print(f"  feasible: {advice.record.get('feasible')}")
    print(
        "  TypeSafe publish 0.114 s a call. docs/guides/decision-llms/README.md estimates from "
        "that figure for every preset, so if this one differs, that section is what needs "
        "correcting."
    )

    from quackd.agent.decision.systemone import TIMEOUT_S

    assert 0.0 < latency < TIMEOUT_S * 2, (
        f"{latency:.3f} s is outside what the router is built for: the retry policy gives it "
        f"{TIMEOUT_S} s and one retry, and docs/guides/decision-llms/README.md assumes far less"
    )


@pytest.mark.live_decision
async def test_a_real_answer_carries_the_fields_the_router_reads() -> None:
    """The SDK is early access and its answer container has been spelled two ways in its own
    docs, which is why `_answer` and `_field` are tolerant. Tolerant code hides a rename, so
    this is the test that would notice one: every field the router branches on, present and
    the right type, from a real response rather than from the stub that was written to match
    the documentation."""
    _live_or_skip()
    advice, _stepper = await _arm_turn("Read the arm back and say what it reports")

    record = advice.record
    assert isinstance(record["confidence"], float), "a Choice came back with no confidence"
    assert 0.0 <= record["confidence"] <= 1.0
    assert isinstance(record["probabilities"], dict) and record["probabilities"], (
        "no distribution came back, so confidence routing has nothing under it"
    )
    assert record["choice"] in record["labels"], (
        f"answered {record['choice']!r}, which was not one of the options it was given"
    )
    for noul in ("done", "need_human"):
        assert isinstance(record[noul], float) and 0.0 <= record[noul] <= 1.0, noul
    assert record["feasible"]["choice"] in {"feasible", "infeasible", "uncertain", None}


@pytest.mark.live_decision
async def test_a_real_answer_before_a_verdict_cannot_reach_a_moving_verb() -> None:
    """The safety claim, against the real model rather than a stub told to behave.

    Before a feasibility verdict the stepper is offered the reads and the brake and nothing
    else, so whatever the decision LLM thinks the right action is, the call that comes back
    cannot be one that moves the arm."""
    _live_or_skip()
    advice, _stepper = await _arm_turn(
        "Close the gripper on whatever is in front of you", cleared=False
    )

    assert set(advice.record["labels"]) <= {"report_state", "stop", "escalate"}
    if advice.call is not None:
        assert advice.call.name in {"report_state", "stop"}
        assert advice.record["floor"] == FLOORS[advice.record["class"]]
