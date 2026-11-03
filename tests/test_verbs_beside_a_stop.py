"""What the arms and the humanoid that are not the SO-101 do with a `stop` from beside a verb, and
with a call that does not say which way a gripper goes.

A verb that waits on a move has to hear a stop sent beside it, over MCP from a second call, or it
reports the move as done, and one with another goal to send after the wait undoes the stop. The
SO-101's own verbs have their tests in `test_lerobot_adapter.py`, and the core's in `test_verbs.py`.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from quackd.perception.color_blob import ColorBlobDetector
from quackd.safety import Executor, allow_all
from quackd.transport.base import DuckState, Intent
from quackd.verbs.core import TICK_S, _waited_out
from quackd.verbs.registry import (
    STOP_ENDED,
    STOPPED,
    NoParams,
    VerbContext,
    VerbResult,
    registry_from_manifest,
)
from quackd_alohamini import AlohaMiniAdapter
from quackd_alohamini.mock import AlohaMiniMock
from quackd_huebot import verbs as huebot_verbs
from quackd_lerobot import LeRobotAdapter
from quackd_lerobot.mock import LeRobotMock
from quackd_microduck import verbs as microduck_verbs
from quackd_toddlerbot import ToddlerBotAdapter
from quackd_toddlerbot.mock import ToddlerBotMock
from quackd_xlerobot import XLerobotAdapter
from quackd_xlerobot.mock import XLerobotMock


def _aloha() -> Any:
    return AlohaMiniAdapter(AlohaMiniMock())


def _xlerobot() -> Any:
    return XLerobotAdapter(XLerobotMock())


def _toddlerbot() -> Any:
    return ToddlerBotAdapter(ToddlerBotMock(gripper=True))


async def _executor(adapter: Any) -> Executor:
    manifest = await adapter.connect()
    return Executor(
        registry_from_manifest(manifest, adapter),
        adapter,
        contract=None,
        detector=ColorBlobDetector(),
        confirm=allow_all,
    )


async def _braked(
    adapter: Any, verb: str, params: dict[str, Any], *, at_sleep: int = 2
) -> tuple[VerbResult, list[str]]:
    """`verb` with a `stop` asked for through the same executor at its `at_sleep`th wait, as a
    second MCP call lands, and the kinds of what the robot was sent from the stop on. A mock's clock
    runs as fast as it is asked to, so the moment is a wait of the verb's, not a time."""
    ex = await _executor(adapter)
    mock = adapter.transport
    sleep = mock.sleep
    waits = 0
    sent_at_the_stop: int | None = None

    async def sleeping(seconds: float) -> None:
        nonlocal waits, sent_at_the_stop
        waits += 1
        if waits == at_sleep:
            braked = await asyncio.create_task(ex.run_verb("stop"))
            assert braked.ok, braked.summary
            sent_at_the_stop = len(mock.intents)
        await sleep(seconds)

    mock.sleep = sleeping
    result = await ex.run_verb(verb, params)
    assert sent_at_the_stop is not None, f"{verb} ended before it could be braked: {result.summary}"
    return result, [intent.kind for intent in mock.intents[sent_at_the_stop:]]


CASES = [
    pytest.param(
        _xlerobot, "move_joints", {"positions": {"right_arm_elbow_flex": 30}}, id="xl-move"
    ),
    pytest.param(_xlerobot, "gripper", {"open": False}, id="xl-gripper"),
    pytest.param(
        _aloha,
        "move_joints",
        {"arm": "left", "positions": {"shoulder_pan": 10}},
        id="aloha-move",
    ),
    pytest.param(_aloha, "gripper", {"open": False}, id="aloha-gripper"),
    pytest.param(_aloha, "lift", {"height_mm": 400}, id="aloha-lift"),
    pytest.param(_aloha, "home_arms", {}, id="aloha-home"),
    pytest.param(_toddlerbot, "look", {"yaw_deg": 30}, id="toddler-look"),
    pytest.param(_toddlerbot, "perform", {"motion": "kneel"}, id="toddler-perform"),
    pytest.param(_toddlerbot, "grip", {"close": True}, id="toddler-grip"),
]


@pytest.mark.parametrize(("body", "verb", "params"), CASES)
async def test_a_stop_from_beside_a_verb_ends_it_and_nothing_is_sent_after_it(
    body: Any, verb: str, params: dict[str, Any]
) -> None:
    """Each of these reported the move as done with a stop sent beside it. `lift` and `home_arms`
    also carried on sending, which undid the stop: the lift's next tick set it going again."""
    result, after = await _braked(body(), verb, params)
    assert not result.ok, result.summary
    assert result.summary.startswith(f"{verb}: {STOPPED}"), result.summary
    assert result.data["interrupted"] == STOPPED
    assert result.data["ended_by"] == STOP_ENDED, "not a failure for `abort_when` to count"
    assert set(after) <= {"stop"}, f"{verb} sent {after} after the stop"


async def test_a_stop_ends_the_wait_for_a_stand() -> None:
    """It reads the robot until it stands or the time is up, with the robot held by the stop."""
    result, after = await _braked(_toddlerbot(), "stand", {}, at_sleep=1)
    assert result.summary.startswith(f"stand: {STOPPED}"), result.summary
    assert result.data["ended_by"] == STOP_ENDED
    assert set(after) <= {"stop"}


async def test_a_stop_ends_a_sweep_of_the_head_before_its_next_look() -> None:
    """ToddlerBot's own `search_scan`, which waits for the neck where the shared one does not."""
    mock = ToddlerBotMock(camera=True)
    adapter = ToddlerBotAdapter(mock)
    result, after = await _braked(
        adapter, "search_scan", {"target": "ball", "step_deg": 30, "max_steps": 6}, at_sleep=4
    )
    assert not result.ok, result.summary
    assert result.summary.startswith(f"search_scan: {STOPPED}"), result.summary
    assert result.data["ended_by"] == STOP_ENDED
    assert "look" not in after, f"the sweep looked again after the stop: {after}"


async def test_an_arm_this_body_stop_leaves_alone_says_it_goes_on() -> None:
    """These bodies' `stop` leaves the arm's goals where they are, and the reply must not read as an
    arm that stopped: the goal it was sent is still where it is going."""
    result, _ = await _braked(
        _xlerobot(), "move_joints", {"positions": {"right_arm_elbow_flex": 30}}
    )
    assert "goes on to the goal it was sent" in result.summary
    assert result.data["base_stopped"] is True
    result, _ = await _braked(
        _aloha(), "move_joints", {"arm": "left", "positions": {"shoulder_pan": 10}}
    )
    assert "goes on to the goal it was sent" in result.summary


async def test_home_arms_sends_the_second_arm_nowhere_after_a_stop() -> None:
    mock = AlohaMiniMock()
    adapter = AlohaMiniAdapter(mock)
    result, after = await _braked(adapter, "home_arms", {})
    assert result.data["arms_sent"] == 1
    assert "joint" not in after
    assert [intent.kind for intent in mock.intents].count("joint") == 1


async def test_a_lift_a_stop_ended_is_not_set_going_again() -> None:
    """One proportional step per command, sent again every tick, so the tick after the stop was a
    command to go on. The mock's stop forgets the target as the robot's does."""
    mock = AlohaMiniMock()
    adapter = AlohaMiniAdapter(mock)
    result, after = await _braked(adapter, "lift", {"height_mm": 400}, at_sleep=3)
    assert result.data["goal_mm"] == 400
    assert "pose" not in after


async def test_a_run_whose_minutes_are_spent_ends_a_wait_as_a_failure() -> None:
    """The other thing `interrupted` says, which is no stop: `abort_when` still counts it."""
    adapter = _xlerobot()
    await adapter.connect()

    async def noop(_seconds: float) -> None:
        return None

    ctx = VerbContext(
        transport=adapter,
        interrupted=lambda: "max_minutes (1) reached",
    )
    adapter.transport.sleep = noop
    assert await _waited_out(ctx, 1.0) == "max_minutes (1) reached"


async def test_a_wait_nothing_ends_takes_the_time_it_was_asked_for() -> None:
    slept: list[float] = []
    adapter = _xlerobot()
    await adapter.connect()

    async def counting(seconds: float) -> None:
        slept.append(seconds)

    adapter.transport.sleep = counting
    ctx = VerbContext(transport=adapter)
    assert await _waited_out(ctx, 1.5) is None
    assert sum(slept) == pytest.approx(1.5)
    assert len(slept) == round(1.5 / TICK_S)
    slept.clear()
    assert await _waited_out(ctx, 0.01) is None
    assert slept == [0.01], "a wait shorter than a tick still waits"


@pytest.mark.parametrize(
    "stand_up", [huebot_verbs.stand_up, microduck_verbs.stand_up], ids=["huebot", "microduck"]
)
async def test_a_stop_ends_the_wait_for_a_robot_to_get_up(stand_up: Any) -> None:
    """It sends one request and waits for a standing reading, and a stop beside it was answered with
    `did not get up`, half a minute later."""

    class Down:
        """A robot on the floor nothing is holding, which stays there."""

        def __init__(self) -> None:
            self.sent: list[str] = []
            self.stops = 0
            self.slept = 0.0

        def now(self) -> float:
            return self.slept

        async def sleep(self, seconds: float) -> None:
            self.slept += seconds

        async def stop(self) -> None:
            self.stops += 1

        async def get_state(self) -> DuckState:
            return DuckState(posture="fallen", fallen=True)

        async def send_intent(self, intent: Intent) -> Any:
            self.sent.append(intent.kind)
            return SimpleNamespace(accepted=True, reason=None)

    body = Down()
    told: list[str] = []

    def interrupted() -> str | None:
        return STOPPED if body.slept > 0 else None

    ctx = VerbContext(transport=body, interrupted=interrupted, log=told.append)  # type: ignore[arg-type]
    result = await stand_up(ctx, NoParams())
    assert not result.ok
    assert result.summary.startswith(f"stand_up: {STOPPED}"), result.summary
    assert result.data["ended_by"] == STOP_ENDED
    assert body.stops == 1 and body.slept < 2, "it waited on after the stop"


# ── a gripper says which way ──


@pytest.mark.parametrize(
    ("body", "verb", "field"),
    [
        pytest.param(lambda: LeRobotAdapter(LeRobotMock()), "gripper", "open", id="so101"),
        pytest.param(_xlerobot, "gripper", "open", id="xlerobot"),
        pytest.param(_aloha, "gripper", "open", id="alohamini"),
        pytest.param(_toddlerbot, "grip", "close", id="toddlerbot"),
    ],
)
async def test_a_gripper_call_that_does_not_say_which_way_moves_nothing(
    body: Any, verb: str, field: str
) -> None:
    """`{}` opened the arm's gripper by its schema's default, on whatever was in it, from a model
    that sent no arguments or a vendor's `null`. It goes back to the pilot as a refusal now."""
    adapter = body()
    ex = await _executor(adapter)
    mock = adapter.transport
    sent = list(getattr(mock, "intents", ()))
    joints = dict(getattr(mock, "joints", {}))
    refused = await ex.run_verb(verb, {})
    assert not refused.ok
    assert field in refused.summary, refused.summary
    assert list(getattr(mock, "intents", ())) == sent, "something was sent"
    assert dict(getattr(mock, "joints", {})) == joints, "something moved"
