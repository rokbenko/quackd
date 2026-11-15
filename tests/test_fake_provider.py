"""What the scripted pilot puts on the log's `think` line.

Every runnable example in the README uses `--llm fake`, so this line is the only
thinking most readers ever see. It has to be honest about being a rule, and it has to be
inert: the transcript is grepped elsewhere for quoted verb names.
"""

from __future__ import annotations

from typing import Any

import pytest

from roboglot.agent.providers.base import Decision, Exchange, Observation, ToolCall
from roboglot.agent.providers.fake import FakeProvider

SYSTEM = "You are the brain of a small duck robot."
ALLOWED = ["search_scan", "walk_to", "kick", "quack", "declare_success", "declare_failure"]


def _observation(
    *,
    detections: list[dict[str, Any]] | None = None,
    last_result: dict[str, Any] | None = None,
) -> Observation:
    return Observation(
        text="[step 0/12] state: posture=stand policy=walk",
        features={
            "state": {"posture": "stand", "policy": "walk"},
            "detections": detections or [],
            "last_result": last_result,
            "allowed": ALLOWED,
        },
    )


def _ball(distance: float = 0.58, bearing: float = 18.0) -> dict[str, Any]:
    return {
        "label": "ball",
        "cx": 0.4,
        "cy": 0.6,
        "area": 0.02,
        "bearing_deg": bearing,
        "est_distance_m": distance,
    }


def _scanned(verb: str = "search_scan") -> list[Exchange]:
    """Two turns already spent on `verb`, so the observation under test is really step 2."""
    return [
        Exchange(
            observation=_observation(),
            decision=Decision(tool_call=ToolCall(id=f"fake-{i}", name=verb, arguments={})),
        )
        for i in (1, 2)
    ]


async def test_the_scripted_pilot_says_what_it_saw_and_what_it_picked() -> None:
    """The line names the two inputs the rule branched on — the camera and the last verb —
    and the verb that fell out, which is the whole point of showing it."""
    history = [
        *_scanned(),
        Exchange(
            observation=_observation(
                detections=[_ball()],
                last_result={"verb": "search_scan", "ok": True, "summary": "found 1", "data": {}},
            )
        ),
    ]
    turn = await FakeProvider.for_glot("find-and-kick").step(SYSTEM, history, [])

    assert turn.thinking == (
        "[scripted] step 2: sees 1 ball (nearest 0.58 m, 18 deg left) after search_scan ok, "
        "so the rule picks walk_to"
    )
    assert turn.thinking.endswith(f"so the rule picks {turn.tool_calls[0].name}")


async def test_the_scripted_pilot_says_when_it_sees_nothing() -> None:
    """An empty camera and no verb behind it still produce a whole sentence: a blank think
    line would be indistinguishable from the bug this feature fixes."""
    history = [Exchange(observation=_observation())]
    turn = await FakeProvider.for_glot("find-and-kick").step(SYSTEM, history, [])

    assert (
        turn.thinking == "[scripted] step 0: sees nothing it knows, so the rule picks search_scan"
    )
    assert turn.tool_calls[0].name == "search_scan"


HOSTILE_LABEL = 'ball" ' + "with an implausibly long detector label " * 5


@pytest.mark.parametrize(
    ("provider", "observation"),
    [
        (FakeProvider.for_glot("hello-world"), _observation()),
        (
            FakeProvider.for_glot("find-and-kick"),
            _observation(
                detections=[_ball(0.22, 0.0)],
                last_result={"verb": 'walk_to "ball"', "ok": False, "summary": "", "data": {}},
            ),
        ),
        (FakeProvider(script=[ToolCall(name="kick")]), _observation(detections=[_ball()])),
        (
            FakeProvider.for_glot("find-and-kick"),
            _observation(
                detections=[
                    {"label": HOSTILE_LABEL, "cx": 0.5, "cy": 0.5, "area": 0.1, "bearing_deg": None}
                ]
            ),
        ),
    ],
    ids=["hello-world", "find-and-kick", "a fixed script", "a hostile label"],
)
async def test_scripted_thinking_never_contains_a_double_quote_or_stands_alone(
    provider: FakeProvider, observation: Observation
) -> None:
    """Three invariants the rest of the suite leans on. The quote matters most:
    `tests/test_cli.py` greps the raw transcript for `"kick"` and `"name": "kick"` to prove
    the duck kicked, and a quoted verb name in the thinking would make that pass vacuously.
    The prefix keeps the line from ever reading as an LLM's own words, and the log prints
    one `think` row per turn, so it stays a single line."""
    turn = await provider.step(SYSTEM, [Exchange(observation=observation)], [])

    assert turn.thinking is not None
    assert '"' not in turn.thinking
    assert turn.thinking.startswith("[scripted] step ")
    assert "\n" not in turn.thinking
    assert turn.thinking.endswith(f"so the rule picks {turn.tool_calls[0].name}")


async def test_the_scripted_pilot_has_no_text_and_no_reasoning_tokens() -> None:
    """A rule says nothing to the human and spends nothing thinking. Inventing either would
    make the log's token line lie about what a keyless run costs."""
    history = [Exchange(observation=_observation(detections=[_ball()]))]
    turn = await FakeProvider.for_glot("find-and-kick").step(SYSTEM, history, [])

    assert turn.text is None
    assert turn.usage.reasoning_tokens == 0
    assert turn.thinking  # but it does explain itself


def _pose_obs(x: float, y: float, theta: float) -> Observation:
    """An observation carrying a pose, which the shape strategies steer on."""
    return Observation(
        text=f"[step] state: pose=({x:.2f}, {y:.2f}, {theta:.2f} rad)",
        features={
            "state": {"posture": "stand", "policy": "walk", "x": x, "y": y, "theta": theta},
            "detections": [],
            "last_result": None,
            "allowed": ["move", "stop", "declare_success", "declare_failure"],
        },
    )


def _drive(strategy: Any, poses: list[tuple[float, float, float]]) -> list[ToolCall]:
    return [strategy(_pose_obs(*p), i, []) for i, p in enumerate(poses)]


def test_the_square_walker_says_how_square_it_was_and_fails_when_it_is_not() -> None:
    """Four legs is a count, not a shape. Each leg ends when the pose says it has gone far
    enough, so an overshoot or a corner short of ninety degrees gives four legs that finish
    somewhere else. `docs/assets/hero3d.py` publishes the GIF on this outcome."""
    from roboglot.agent.providers.fake import SHAPE_CLOSURE_M, square_strategy

    # a walk that wanders off: four legs, ending far from where it started
    wandering = square_strategy()
    calls = _drive(wandering, [(0.0, 0.0, 0.0)] + [(i * 1.0, 0.0, i * 1.6) for i in range(1, 14)])
    declared = [c for c in calls if c.name.startswith("declare_")]
    if declared:
        assert declared[0].name == "declare_failure"
        assert "from where it started" in declared[0].arguments["reason"]

    # and the check has teeth: one full side is loose, two metres out is not
    assert SHAPE_CLOSURE_M < 2.0, "four legs walked in a line must not pass as a square"


def test_a_true_square_is_judged_where_its_fourth_corner_ends() -> None:
    """A body that does exactly what it is told walks four legs and turns four corners, and the
    walker judges the shape there. It used to look one call late: a corner that ended went
    straight on to begin the next leg, so a square was judged from the end of a fifth leg, a
    whole side from where it started. This body passed that by a fraction of a millimetre,
    which is why the legs are counted: a leg is the moves between two corners, however many
    a leg takes."""
    import math

    from roboglot.agent.providers.fake import square_strategy

    strategy = square_strategy()
    x = y = theta = 0.0
    legs, walking = 0, False
    for step in range(60):
        call = strategy(_pose_obs(x, y, theta), step, [])
        if call.name != "move":
            break
        vx, wz, seconds = (call.arguments[k] for k in ("vx", "wz", "duration_s"))
        legs += vx > 0 and not walking
        walking = vx > 0
        x += vx * seconds * math.cos(theta)
        y += vx * seconds * math.sin(theta)
        theta = math.atan2(math.sin(theta + wz * seconds), math.cos(theta + wz * seconds))
    assert legs == 4, f"{legs} legs walked for one square"
    assert call.name == "declare_success", call.arguments


def test_a_shape_walker_never_asks_for_a_turn_it_cannot_measure() -> None:
    """The strategy reads the heading once per move and not in between, so a turn past pi is
    indistinguishable from its complement and a whole revolution from standing still."""
    import math

    from roboglot.agent.providers.fake import circle_strategy, square_strategy

    for make in (square_strategy, circle_strategy):
        strategy = make()
        # poses that barely change, so the gait learner floors and asks for its longest move
        calls = _drive(strategy, [(0.0, 0.0, 0.0)] * 12)
        for call in calls:
            if call.name != "move":
                continue
            wz = abs(call.arguments.get("wz", 0.0))
            duration = call.arguments["duration_s"]
            assert wz * duration < math.pi, (
                f"a {duration}s move at {wz} rad/s turns {wz * duration:.1f} rad, and the "
                f"strategy cannot tell that from {wz * duration - 2 * math.pi:.1f}"
            )


def test_the_scripted_pilot_takes_vision_without_teaching_it_to_every_other_one() -> None:
    """`--vision` on the scripted pilot is set on the instance, not on the class, because the
    class attribute is the default every other `FakeProvider` in the suite reads. A `vision=`
    that wrote through to the class would quietly turn pictures on for every test that built
    one afterwards, including the goldens."""
    seeing = FakeProvider(vision=True)
    assert seeing.supports_vision is True
    assert FakeProvider.supports_vision is False, "the class default is untouched"
    assert FakeProvider().supports_vision is False, "and so is the next one built"
