"""A scripted LLM, so the whole system can be tested — and demoed — with no API key.

Two modes: a fixed script of tool calls, or a *strategy* (a function of the structured
observation) that plays the starter ducks well enough to prove the loop closes. The
strategies are intentionally dumb rules; the point is that the same verbs, executor and
transcript run whether the pilot is a rule or a frontier model.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from quackd.agent.providers.base import Exchange, Observation, ProviderTurn, ToolCall, Usage

Strategy = Callable[[Observation, int, list[Exchange]], ToolCall]


def _detections(obs: Observation, label: str) -> list[dict[str, Any]]:
    return [d for d in obs.features.get("detections", []) if d.get("label") == label]


def _last(obs: Observation) -> dict[str, Any]:
    return obs.features.get("last_result") or {}


def _count_calls(history: list[Exchange], name: str) -> int:
    return sum(1 for ex in history if ex.decision and ex.decision.tool_call.name == name)


def hello_world_strategy(obs: Observation, step: int, history: list[Exchange]) -> ToolCall:
    script = [
        ToolCall(name="quack", arguments={"text": "hello!"}),
        ToolCall(name="walk", arguments={"vx": 0.1, "duration_s": 1.0}),
        ToolCall(name="quack", arguments={"text": "done"}),
        ToolCall(name="declare_success", arguments={"reason": "quacked and walked one step"}),
    ]
    return script[min(step, len(script) - 1)]


def find_and_kick_strategy(obs: Observation, step: int, history: list[Exchange]) -> ToolCall:
    last = _last(obs)
    last_name = last.get("verb")
    if last_name == "kick" and last.get("ok"):
        moved = (last.get("data") or {}).get("ball_moved_m")
        if moved is not None and moved >= 0.3:
            if _count_calls(history, "quack") == 0:
                return ToolCall(name="quack", arguments={"text": "yay, got it!"})
        elif moved is None:
            return ToolCall(
                name="declare_success", arguments={"reason": "kicked; no displacement telemetry"}
            )
    if last_name == "quack" and _count_calls(history, "kick") > 0:
        return ToolCall(name="declare_success", arguments={"reason": "ball displaced by the kick"})
    balls = _detections(obs, "ball")
    if not balls:
        if (
            _count_calls(history, "search_scan") >= 3
            and last_name == "search_scan"
            and not last.get("ok")
        ):
            return ToolCall(
                name="declare_failure", arguments={"reason": "no ball found after repeated scans"}
            )
        return ToolCall(name="search_scan", arguments={"target": "ball"})
    dist = balls[0].get("est_distance_m")
    bearing = abs(balls[0].get("bearing_deg") or 0.0)
    if dist is not None and dist <= 0.3 and bearing < 30:
        return ToolCall(name="kick", arguments={"leg": "right"})
    return ToolCall(name="walk_to", arguments={"target": "ball", "stop_distance": 0.22})


def patrol_strategy(obs: Observation, step: int, history: list[Exchange]) -> ToolCall:
    people = _detections(obs, "person") + _detections(obs, "pet")
    last = _last(obs)
    if people and last.get("verb") != "quack":
        return ToolCall(name="quack", arguments={"text": "quack quack! someone is here"})
    legs = _count_calls(history, "walk")
    if legs >= 3:
        return ToolCall(name="declare_success", arguments={"reason": "patrol lap complete"})
    if step % 2 == 0:
        return ToolCall(name="walk", arguments={"vx": 0.12, "duration_s": 2.0})
    return ToolCall(name="search_scan", arguments={"target": "person", "max_steps": 4})


def reachy_spotter_strategy(obs: Observation, step: int, history: list[Exchange]) -> ToolCall:
    """A head that cannot walk: sweep, then say where the ball is, then stop."""
    if _count_calls(history, "say") > 0:
        return ToolCall(name="declare_success", arguments={"reason": "said where the ball is"})
    balls = _detections(obs, "ball")
    if balls:
        bearing = balls[0].get("bearing_deg") or 0.0
        dist = balls[0].get("est_distance_m")
        where = f"ball at {abs(bearing):.0f} degrees {'left' if bearing >= 0 else 'right'}"
        if dist is not None:
            where += f", about {dist:.1f} m"
        return ToolCall(name="say", arguments={"text": where})
    last = _last(obs)
    swept_twice = _count_calls(history, "search_scan") >= 2
    if swept_twice and last.get("verb") == "search_scan" and not last.get("ok"):
        return ToolCall(
            name="declare_failure", arguments={"reason": "no ball found after two sweeps"}
        )
    return ToolCall(name="search_scan", arguments={"target": "ball"})


def _where(ball: dict[str, Any]) -> str:
    bearing = ball.get("bearing_deg") or 0.0
    dist = ball.get("est_distance_m")
    where = f"ball at {abs(bearing):.0f} degrees {'left' if bearing >= 0 else 'right'}"
    return where + (f", about {dist:.1f} m" if dist is not None else "")


def open_duck_scout_strategy(obs: Observation, step: int, history: list[Exchange]) -> ToolCall:
    """A duck that walks but cannot kick: find the ball, walk up to it, report once."""
    if _count_calls(history, "say") > 0:
        return ToolCall(name="declare_success", arguments={"reason": "walked up and reported"})
    last = _last(obs)
    balls = _detections(obs, "ball")
    if balls:
        dist = balls[0].get("est_distance_m")
        if dist is None or dist <= 0.45 or _count_calls(history, "go_to") > 0:
            return ToolCall(name="say", arguments={"text": _where(balls[0])})
        return ToolCall(name="go_to", arguments={"target": "ball", "stop_distance": 0.3})
    if (
        _count_calls(history, "search_scan") >= 2
        and last.get("verb") == "search_scan"
        and not last.get("ok")
    ):
        return ToolCall(
            name="declare_failure", arguments={"reason": "no ball found after two sweeps"}
        )
    return ToolCall(name="search_scan", arguments={"target": "ball"})


def open_duck_lookout_strategy(obs: Observation, step: int, history: list[Exchange]) -> ToolCall:
    """Head only, no legs: look left, right, centre, then report whatever is true."""
    if _count_calls(history, "say") > 0:
        return ToolCall(name="declare_success", arguments={"reason": "reported what is in view"})
    balls = _detections(obs, "ball")
    if balls:
        return ToolCall(name="say", arguments={"text": _where(balls[0])})
    # head control is off by default on a real duck, so gaze may not exist at all
    if "gaze" not in obs.features.get("allowed", []):
        return ToolCall(name="say", arguments={"text": "no ball in view, and no head to look"})
    looks = _count_calls(history, "gaze")
    if looks >= len(_LOOKOUT_SWEEP):
        return ToolCall(name="say", arguments={"text": "no ball in view"})
    return ToolCall(name="gaze", arguments={"bearing_deg": _LOOKOUT_SWEEP[looks]})


#: Inside the Open Duck Mini v2's neck travel, which is about 23 degrees either way.
_LOOKOUT_SWEEP = (20.0, -20.0, 0.0)

#: The Microduck runs its own gaze IK and clamps rather than forcing, so this can be wider.
_MICRODUCK_SWEEP = (45.0, -45.0, 0.0)


def microduck_lookout_strategy(obs: Observation, step: int, history: list[Exchange]) -> ToolCall:
    """Head only, no legs — and it reports an unreadable posture rather than ignoring it.

    On real hardware `posture` is `unknown` when no `robot.state` frames are arriving, which
    means nothing can tell whether the duck is upright and every verb that moves it will
    refuse. Saying so is the useful answer, and it is the one thing this task exists to find
    out before anybody lets the duck walk.
    """
    if _count_calls(history, "say") > 0:
        return ToolCall(name="declare_success", arguments={"reason": "reported what is in view"})
    posture = (obs.features.get("state") or {}).get("posture")
    if posture == "unknown":
        return ToolCall(
            name="say",
            arguments={"text": "posture is unknown: nothing is reporting whether I am upright"},
        )
    allowed = obs.features.get("allowed", [])
    if balls := _detections(obs, "ball"):
        return ToolCall(name="say", arguments={"text": _where(balls[0])})
    if "gaze" not in allowed:
        return ToolCall(name="say", arguments={"text": "no ball in view, and no head to look"})
    looks = _count_calls(history, "gaze")
    if looks >= len(_MICRODUCK_SWEEP):
        return ToolCall(name="say", arguments={"text": "no ball in view"})
    return ToolCall(name="gaze", arguments={"bearing_deg": _MICRODUCK_SWEEP[looks]})


def generic_strategy(obs: Observation, step: int, history: list[Exchange]) -> ToolCall:
    allowed = obs.features.get("allowed", [])
    if step == 0 and "quack" in allowed:
        return ToolCall(name="quack", arguments={"text": "hello"})
    if step < 2 and "search_scan" in allowed:
        return ToolCall(name="search_scan", arguments={})
    return ToolCall(
        name="declare_success", arguments={"reason": "scripted pilot: nothing more to do"}
    )


STRATEGIES: dict[str, Strategy] = {
    "hello-world": hello_world_strategy,
    "find-and-kick": find_and_kick_strategy,
    "patrol-and-quack": patrol_strategy,
    "reachy-spotter": reachy_spotter_strategy,
    "open-duck-scout": open_duck_scout_strategy,
    "open-duck-lookout": open_duck_lookout_strategy,
    "microduck-lookout": microduck_lookout_strategy,
}


class FakeProvider:
    name = "fake"
    supports_vision = False

    def __init__(
        self,
        strategy: Strategy | None = None,
        script: list[ToolCall] | None = None,
        model: str = "scripted",
    ) -> None:
        self.model = model
        self._strategy = strategy
        self._script = script
        self.calls = 0

    @classmethod
    def for_duck(cls, duck_name: str, goal: str | None = None) -> FakeProvider:
        """Pick a scripted strategy by duck name, or by keywords in a plain-language goal."""
        strategy = STRATEGIES.get(duck_name)
        label = duck_name
        if strategy is None and goal:
            text = goal.lower()
            if "kick" in text or "ball" in text:
                strategy, label = find_and_kick_strategy, "goal:find-and-kick"
            elif "patrol" in text or "person" in text or "someone" in text:
                strategy, label = patrol_strategy, "goal:patrol"
        return cls(strategy=strategy or generic_strategy, model=f"scripted:{label}")

    async def step(
        self, system: str, history: list[Exchange], tools: list[dict[str, Any]]
    ) -> ProviderTurn:
        obs = history[-1].observation
        decisions = sum(1 for ex in history if ex.decision is not None)
        if self._script is not None:
            call = self._script[min(decisions, len(self._script) - 1)]
        elif self._strategy is not None:
            call = self._strategy(obs, decisions, history)
        else:
            call = ToolCall(
                name="declare_failure", arguments={"reason": "fake provider has no strategy"}
            )
        self.calls += 1
        call = call.model_copy(update={"id": f"fake-{self.calls}"})
        usage = Usage(input_tokens=len(system) // 4 + len(obs.text) // 4, output_tokens=16)
        return ProviderTurn(tool_calls=[call], text=None, usage=usage, stop_reason="tool_use")
