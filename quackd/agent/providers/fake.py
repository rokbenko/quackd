"""A scripted LLM, so the whole system can be tested — and demoed — with no API key.

Two modes: a fixed script of tool calls, or a *strategy* (a function of the structured
observation) that plays the starter ducks well enough to prove the loop closes. The
strategies are intentionally dumb rules; the point is that the same verbs, executor and
transcript run whether the pilot is a rule or a frontier model.
"""

from __future__ import annotations

import asyncio
import math
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


def xlerobot_lookout_strategy(obs: Observation, step: int, history: list[Exchange]) -> ToolCall:
    """One frame, then the answer — because this robot has neither a voice nor a head.

    Every other lookout task ends by saying what it saw. There is no speaker in an XLeRobot's
    bill of materials, so `say` does not exist for it, and there is no documented head axis
    either, so it cannot look anywhere its owner did not point it. That leaves one frame and
    one report, and the only place the report can go is the reason it succeeds with.
    """
    if _count_calls(history, "observe") == 0:
        return ToolCall(name="observe", arguments={})
    if balls := _detections(obs, "ball"):
        return ToolCall(name="declare_success", arguments={"reason": _where(balls[0])})
    return ToolCall(
        name="declare_success",
        arguments={"reason": "nothing in view, and this robot cannot turn to look further"},
    )


def alohamini_lookout_strategy(obs: Observation, step: int, history: list[Exchange]) -> ToolCall:
    """One frame, then the answer. Like the XLeRobot, this body has no voice and no head, so
    the report has nowhere to go but the reason it succeeds with."""
    if _count_calls(history, "observe") == 0:
        return ToolCall(name="observe", arguments={})
    if balls := _detections(obs, "ball"):
        return ToolCall(name="declare_success", arguments={"reason": _where(balls[0])})
    return ToolCall(
        name="declare_success",
        arguments={"reason": "nothing in view, and this robot cannot turn to look further"},
    )


def toddlerbot_lookout_strategy(obs: Observation, step: int, history: list[Exchange]) -> ToolCall:
    """Head only, no legs, and no voice to report with.

    This robot cannot get up if it falls, so the strategy never walks, and there is no text
    to speech on it at all, so the answer goes in the reason it succeeds with."""
    if balls := _detections(obs, "ball"):
        return ToolCall(name="declare_success", arguments={"reason": _where(balls[0])})
    looks = _count_calls(history, "look")
    if looks >= len(_TODDLER_SWEEP):
        return ToolCall(
            name="declare_success",
            arguments={"reason": "nothing in view after looking left, right and centre"},
        )
    if _count_calls(history, "observe") <= looks:
        return ToolCall(name="observe", arguments={})
    return ToolCall(name="look", arguments={"yaw_deg": _TODDLER_SWEEP[looks]})


#: Gentle on purpose: the neck is two small servos and this body has no fall recovery.
_TODDLER_SWEEP = (30.0, -30.0, 0.0)


#: What the shape walkers ask for. Every body clamps a twist to its own limits, and the
#: Microduck's gait has a floor besides, so these are requests rather than promises: the
#: strategies close the loop on the pose the robot reports, which is what a model does too.
SHAPE_VX = 0.25
SHAPE_WZ = 0.9
SHAPE_SIDE_M = 0.5
_TURN_TOLERANCE = math.radians(10)
#: How near the start a square has to finish to count as closed.
#:
#: One full side, which is loose on purpose. Measured on seed 0: the trained gait ends 0.30 m
#: from where it started and the kinematic stand-in 0.44 m, because each corner is accepted
#: within ten degrees and four of those compound. What this bound separates is a loop from a
#: drift: four legs walked in a straight line finish two metres out and fail. The distance
#: itself goes in the reason either way, so a run says how square it was rather than only
#: that it declared success, and `docs/assets/hero3d.py` prints it.
SHAPE_CLOSURE_M = SHAPE_SIDE_M
#: One `move` is one sample of the heading: the strategy reads the pose before and after and
#: nothing in between, so a turn past +/-pi is indistinguishable from its complement and a
#: whole revolution from standing still. Bounded by the fastest twist a body here accepts
#: rather than the rate asked for, because the gait floor scales a small twist up.
SHAPE_MAX_TURN_S = 2.0


def _pose(obs: Observation) -> tuple[float, float, float] | None:
    state = obs.features.get("state") or {}
    x, y, theta = state.get("x"), state.get("y"), state.get("theta")
    if x is None or y is None or theta is None:
        return None
    return float(x), float(y), float(theta)


def _wrap(radians_: float) -> float:
    return math.atan2(math.sin(radians_), math.cos(radians_))


class _Gait:
    """How much of a commanded twist this body actually delivers, learned as it goes.

    A cartoon duck walks at the speed it is asked for. A real one on an RL gait delivers
    roughly forty percent of it, and a shape walker that assumes otherwise spends its whole
    budget creeping up on the first corner. So each move records what it asked for and from
    where, the next turn measures what happened, and the estimate is the ratio — which is
    the same correction a model makes when it reads the pose and tries again.
    """

    def __init__(self) -> None:
        self.walk = 1.0
        self.turn = 1.0
        self._pending: tuple[str, float, float] | None = None  # kind, asked, from

    def expect(self, kind: str, asked: float, mark: float) -> None:
        self._pending = (kind, asked, mark)

    def observe(self, walked: float, turned: float) -> None:
        if self._pending is None:
            return
        kind, asked, mark = self._pending
        self._pending = None
        got = (walked if kind == "walk" else turned) - mark
        if asked <= 1e-6:
            return
        ratio = min(1.5, max(0.15, abs(got) / asked))
        if kind == "walk":
            self.walk = 0.5 * self.walk + 0.5 * ratio
        else:
            self.turn = 0.5 * self.turn + 0.5 * ratio

    def seconds(self, kind: str, remaining: float, rate: float) -> float:
        gain = self.walk if kind == "walk" else self.turn
        # A turn is capped so one move can never carry the heading past half a circle. The
        # strategy samples the pose once per move, so a longer turn is indistinguishable from
        # its complement, and the accumulated total quietly loses whole revolutions.
        cap = SHAPE_MAX_TURN_S if kind == "turn" else 10.0
        return min(cap, max(0.4, abs(remaining) / (rate * max(gain, 0.15))))


def square_strategy(side: float = SHAPE_SIDE_M) -> Strategy:
    """Walk a square by watching the pose, the way a model would.

    Four legs and four corners, each ended by what the robot reports rather than by a
    stopwatch, so a body whose gait delivers half of what it was asked still walks a square.
    Returns a fresh closure per run, so one seed cannot leak into the next.
    """
    state: dict[str, Any] = {
        "leg": 0,
        "turning": False,
        "anchor": None,
        "start": None,  # where leg 0 began, which is what "a square" has to come back to
        "target": None,
        "gait": _Gait(),
        "walked": 0.0,
        "turned": 0.0,
        "last": None,
    }

    def strategy(obs: Observation, step: int, history: list[Exchange]) -> ToolCall:
        pose = _pose(obs)
        if pose is None:
            return _no_pose("a shape")
        x, y, theta = pose
        gait: _Gait = state["gait"]
        if state["last"] is not None:
            px, py, pt = state["last"]
            state["walked"] += math.dist((x, y), (px, py))
            state["turned"] += _wrap(theta - pt)  # signed: a stride's wag must cancel
            gait.observe(state["walked"], state["turned"])
        state["last"] = (x, y, theta)
        if state["anchor"] is None:
            state["anchor"] = (x, y)
            state["start"] = (x, y)
        if state["leg"] >= 4:
            # Four legs is a count, not a shape. Each leg ends when the pose says it has gone
            # far enough, so an overshoot or a corner short of ninety degrees gives four legs
            # that finish somewhere else entirely. Say how far off, first, because
            # `docs/assets/hero3d.py` prints the first sixty characters of this and then
            # decides whether to publish the GIF.
            closed = math.dist((x, y), state["start"])
            if closed <= SHAPE_CLOSURE_M:
                return ToolCall(
                    name="declare_success",
                    arguments={
                        "reason": f"closed the square {closed:.2f} m from where it started, "
                        f"after four {side:.2f} m legs with a 90 degree turn between each"
                    },
                )
            return ToolCall(
                name="declare_failure",
                arguments={
                    "reason": f"finished {closed:.2f} m from where it started, after four "
                    f"{side:.2f} m legs: that is not a square"
                },
            )
        if state["turning"]:
            error = _wrap(float(state["target"]) - theta)
            if abs(error) < _TURN_TOLERANCE:
                state["turning"] = False
                state["leg"] += 1
                state["anchor"] = (x, y)
            else:
                seconds = gait.seconds("turn", error, SHAPE_WZ)
                gait.expect("turn", SHAPE_WZ * seconds, state["turned"])
                return ToolCall(
                    name="move",
                    arguments={
                        "vx": 0.0,
                        "wz": math.copysign(SHAPE_WZ, error),
                        "duration_s": round(seconds, 2),
                    },
                )
        walked = math.dist((x, y), state["anchor"])
        if walked >= side:
            state["turning"] = True
            state["target"] = _wrap(theta + math.pi / 2)
            seconds = gait.seconds("turn", math.pi / 2, SHAPE_WZ)
            gait.expect("turn", SHAPE_WZ * seconds, state["turned"])
            return ToolCall(
                name="move",
                arguments={"vx": 0.0, "wz": SHAPE_WZ, "duration_s": round(seconds, 2)},
            )
        seconds = gait.seconds("walk", side - walked, SHAPE_VX)
        gait.expect("walk", SHAPE_VX * seconds, state["walked"])
        return ToolCall(
            name="move", arguments={"vx": SHAPE_VX, "wz": 0.0, "duration_s": round(seconds, 2)}
        )

    return strategy


def circle_strategy(turns: float = 1.0) -> Strategy:
    """Walk a circle: one twist held until the heading has come all the way round."""
    state: dict[str, Any] = {"turned": 0.0, "last": None, "gait": _Gait()}

    def strategy(obs: Observation, step: int, history: list[Exchange]) -> ToolCall:
        pose = _pose(obs)
        if pose is None:
            return _no_pose("a circle")
        theta = pose[2]
        gait: _Gait = state["gait"]
        if state["last"] is not None:
            # Signed. A real gait wags the trunk every stride, which an absolute total counts
            # as turning: `tests/test_sim3d.py` says exactly that about the same measurement.
            state["turned"] += _wrap(theta - float(state["last"]))
            gait.observe(0.0, state["turned"])
        state["last"] = theta
        target = turns * 2 * math.pi
        if abs(state["turned"]) >= target:
            return ToolCall(
                name="declare_success",
                arguments={
                    "reason": f"walked a circle: the heading came round "
                    f"{abs(math.degrees(state['turned'])):.0f} degrees while walking forward"
                },
            )
        seconds = gait.seconds("turn", target - abs(state["turned"]), SHAPE_WZ)
        gait.expect("turn", SHAPE_WZ * seconds, state["turned"])
        return ToolCall(
            name="move",
            arguments={"vx": SHAPE_VX, "wz": SHAPE_WZ, "duration_s": round(seconds, 2)},
        )

    return strategy


def _no_pose(shape: str) -> ToolCall:
    return ToolCall(
        name="declare_failure",
        arguments={
            "reason": f"this robot reports no pose, so it cannot walk {shape} and know it did"
        },
    )


def generic_strategy(obs: Observation, step: int, history: list[Exchange]) -> ToolCall:
    allowed = obs.features.get("allowed", [])
    if step == 0 and "quack" in allowed:
        return ToolCall(name="quack", arguments={"text": "hello"})
    if step < 2 and "search_scan" in allowed:
        return ToolCall(name="search_scan", arguments={})
    return ToolCall(
        name="declare_success", arguments={"reason": "scripted pilot: nothing more to do"}
    )


WAIT_STEPS = 3
"""How many times `flock-hello`'s rule checks for a peer before giving up. A rule cannot tell a
slow peer from an absent one, so it is bounded: a demo that hangs is not a demo."""

WAIT_PAUSES_S = (0.25, 1.0, 4.0)
"""How long the rule waits on the wall clock before each of those checks. A check on a mock body
takes a few milliseconds, so without a wait all three passed before a peer that connected or
stepped more slowly had said anything, and CI's macOS runners failed two flock tests that way.
Short first, so a flock whose peers have already spoken pays almost nothing, and a few seconds
in all, so a run whose peer never comes still ends."""


def flock_hello_pause(call: ToolCall, history: list[Exchange]) -> float:
    """How long to wait before `call`: a check waits its turn in `WAIT_PAUSES_S`, and nothing
    else waits at all."""
    if call.name != "report_state":
        return 0.0
    done = _count_calls(history, "report_state")
    return WAIT_PAUSES_S[min(done, len(WAIT_PAUSES_S) - 1)]


def flock_hello_strategy(obs: Observation, step: int, history: list[Exchange]) -> ToolCall:
    """The pilot flock demo: say you are here, wait to be answered, then stop.

    It is a rule, so it does not reason about anyone's datasheet, but it does exercise the
    whole path the real thing uses: the `tell` tool, the bus, and the inbox that arrives in the
    next observation. `features["flock"]` is how it knows its own name; `features["inbox"]` is
    how it knows it was answered. Deterministic and finite even if nobody ever answers."""
    me = str((obs.features.get("flock") or {}).get("me") or "this robot")
    heard = {
        str(message.get("from"))
        for exchange in history
        for message in (exchange.observation.features.get("inbox") or [])
        if message.get("from") not in (None, "flock", me)
    }
    if _count_calls(history, "tell") == 0:
        return ToolCall(
            name="tell",
            arguments={"to": "all", "text": f"{me} here and ready; say hello back"},
        )
    if heard:
        return ToolCall(
            name="declare_success",
            arguments={"reason": f"said hello and heard back from {', '.join(sorted(heard))}"},
        )
    if _count_calls(history, "report_state") >= WAIT_STEPS:
        return ToolCall(
            name="declare_failure",
            arguments={"reason": f"nobody answered after {WAIT_STEPS} checks"},
        )
    return ToolCall(name="report_state", arguments={})


def lerobot_lookout_strategy(obs: Observation, step: int, history: list[Exchange]) -> ToolCall:
    """Read the arm back and report it. No camera, no voice, and nothing that moves a joint.

    Every other lookout looks; this body cannot. What it can do is say whether it answered,
    whether torque is on and whether a servo is hot, which is what the bring-up checklist
    needs before it lets the arm sweep a volume."""
    state = obs.features.get("state") or {}
    extras = state.get("extras") or {}
    if _count_calls(history, "report_state") == 0:
        return ToolCall(name="report_state", arguments={})
    if not extras:
        return ToolCall(
            name="declare_failure",
            arguments={"reason": "the arm did not report a state; a human should check it"},
        )
    joints = extras.get("joints") or {}
    where = ", ".join(f"{name} {value:.0f}" for name, value in sorted(joints.items()))
    torque = "torque on" if extras.get("torque", True) else "TORQUE OFF"
    hot = extras.get("hot") or []
    heat = f"hot: {', '.join(hot)}" if hot else "nothing hot"
    return ToolCall(
        name="declare_success",
        arguments={"reason": f"{where or 'no joints reported'}; {torque}; {heat}"},
    )


STRATEGIES: dict[str, Strategy] = {
    "flock-hello": flock_hello_strategy,
    "hello-world": hello_world_strategy,
    "find-and-kick": find_and_kick_strategy,
    "patrol-and-quack": patrol_strategy,
    "open-duck-scout": open_duck_scout_strategy,
    "open-duck-lookout": open_duck_lookout_strategy,
    "microduck-lookout": microduck_lookout_strategy,
    "xlerobot-lookout": xlerobot_lookout_strategy,
    "alohamini-lookout": alohamini_lookout_strategy,
    "toddlerbot-lookout": toddlerbot_lookout_strategy,
    "lerobot-lookout": lerobot_lookout_strategy,
}

PAUSES: dict[Strategy, Callable[[ToolCall, list[Exchange]], float]] = {
    flock_hello_strategy: flock_hello_pause,
}
"""Strategies that wait on the wall clock before some of their calls, and how long. A rule
decides in no time, and a peer it is waiting on needs some."""


def _seen_label(detection: dict[str, Any]) -> str:
    """One label, whitespace-collapsed and clipped, so a garbage label cannot run away with
    the line: perception labels come from whatever model is loaded, not from a fixed list."""
    return " ".join(str(detection.get("label") or "thing").split())[:24] or "thing"


def _seen(detections: list[dict[str, Any]]) -> str:
    """What the rule saw, as counts per label plus where the nearest thing is."""
    if not detections:
        return "nothing it knows"
    counts: dict[str, int] = {}
    for d in detections:
        label = _seen_label(d)
        counts[label] = counts.get(label, 0) + 1
    groups = ", ".join(
        f"{n} {label}{'' if n == 1 or label.endswith('s') else 's'}" for label, n in counts.items()
    )
    # Unknown distances sort last, so "nearest" is the nearest thing actually ranged.
    nearest = min(
        detections,
        key=lambda d: (d.get("est_distance_m") is None, d.get("est_distance_m") or 0.0),
    )
    dist, bearing = nearest.get("est_distance_m"), nearest.get("bearing_deg")
    where = f"{dist:.2f} m" if dist is not None else "distance unknown"
    if bearing is None:
        where += ", bearing unknown"
    else:
        where += f", {abs(bearing):.0f} deg {'left' if bearing >= 0 else 'right'}"
    return f"{groups} (nearest {where})"


def _scripted_thinking(obs: Observation, step: int, call: ToolCall) -> str:
    """The one line the scripted pilot puts on the log's `think` row.

    It is not reasoning and must never be mistaken for it, hence the bracketed `[scripted]`
    prefix: it is the rule reporting the two inputs it actually branched on — what the camera
    saw and how the last verb ended — and the verb that fell out. Without it every keyless
    run (which is every runnable example in the README, and every recorded asset) shows a
    log whose thinking line is permanently blank, so the headline feature cannot be
    demonstrated at all without an API key.

    Double quotes are stripped on the way out. `tests/test_cli.py` greps the raw transcript
    for `"kick"` and `"name": "kick"` to prove the duck really kicked; a quoted verb name in
    the thinking would satisfy those assertions whether or not it did.
    """
    last = _last(obs)
    verb = last.get("verb")
    after = f" after {verb} {'ok' if last.get('ok') else 'failed'}" if verb else ""
    seen = _seen(obs.features.get("detections") or [])
    return f"[scripted] step {step}: sees {seen}{after}, so the rule picks {call.name}".replace(
        '"', "'"
    )


AUTO_VERDICT = ToolCall(
    name="assess_task",
    arguments={
        "verdict": "feasible",
        "reason": "scripted pilot: a rule has no judgement of the body, so it assumes the "
        "task fits",
        "limits_consulted": [],
        "estimates": [],
        "needs": {},
    },
)
"""What the rule answers when a run offers it `assess_task`.

A strategy is a rule, not a judgement: it cannot look at a pile of clothes and weigh it. So it
says so in the reason, and the record shows a scripted verdict rather than a considered one.
Emitted only when the tool is actually offered, which is why `tests/test_fake_provider.py`,
which calls `step` with no tools at all, reads exactly as it did."""


class FakeProvider:
    name = "fake"
    supports_vision = False

    def __init__(
        self,
        strategy: Strategy | None = None,
        script: list[ToolCall] | None = None,
        model: str = "scripted",
        *,
        vision: bool | None = None,
    ) -> None:
        self.model = model
        self._strategy = strategy
        self._script = script
        self.calls = 0
        if vision is not None:
            # A scripted pilot reads features and looks at nothing, so this changes no
            # decision it makes. What it changes is whether the loop builds the pictures at
            # all, which is the whole of `--image` and every frame path, and `--llm fake`
            # is the only way to exercise either without a key and a vendor.
            self.supports_vision = vision

    @classmethod
    def for_duck(
        cls, duck_name: str, goal: str | None = None, *, vision: bool | None = None
    ) -> FakeProvider:
        """Pick a scripted strategy by duck name, or by keywords in a plain-language goal."""
        strategy = STRATEGIES.get(duck_name)
        label = duck_name
        if strategy is None and goal:
            text = goal.lower()
            if "kick" in text or "ball" in text:
                strategy, label = find_and_kick_strategy, "goal:find-and-kick"
            elif "patrol" in text or "person" in text or "someone" in text:
                strategy, label = patrol_strategy, "goal:patrol"
            elif "square" in text:
                strategy, label = square_strategy(), "goal:square"
            elif "circle" in text or "circuit" in text:
                strategy, label = circle_strategy(), "goal:circle"
        return cls(strategy=strategy or generic_strategy, model=f"scripted:{label}", vision=vision)

    async def step(
        self, system: str, history: list[Exchange], tools: list[dict[str, Any]]
    ) -> ProviderTurn:
        obs = history[-1].observation
        assessed = [
            ex
            for ex in history
            if ex.decision is not None and ex.decision.tool_call.name == AUTO_VERDICT.name
        ]
        auto = [ex for ex in assessed if ex.decision.tool_call.id.startswith("fake-auto")]  # type: ignore[union-attr]
        # the script is indexed by the pilot's own decisions: a verdict the rule inserted is
        # not one of them, so a scripted run reads step for step as it did before the gate
        decisions = sum(1 for ex in history if ex.decision is not None) - len(auto)
        if self._script is not None:
            call = self._script[min(decisions, len(self._script) - 1)]
        elif self._strategy is not None:
            call = self._strategy(obs, decisions, history)
        else:
            call = ToolCall(
                name="declare_failure", arguments={"reason": "fake provider has no strategy"}
            )
        self.calls += 1
        offered = any(t.get("name") == AUTO_VERDICT.name for t in tools)
        if offered and not assessed and call.name != AUTO_VERDICT.name:
            call = AUTO_VERDICT.model_copy(update={"id": f"fake-auto-{self.calls}"})
            return ProviderTurn(
                tool_calls=[call],
                text=None,
                usage=Usage(input_tokens=len(system) // 4 + len(obs.text) // 4, output_tokens=16),
                stop_reason="tool_use",
                thinking=_scripted_thinking(obs, decisions, call),
            )
        pause = PAUSES.get(self._strategy) if self._strategy is not None else None
        if pause is not None and (seconds := pause(call, history)) > 0:
            await asyncio.sleep(seconds)
        call = call.model_copy(update={"id": f"fake-{self.calls}"})
        usage = Usage(input_tokens=len(system) // 4 + len(obs.text) // 4, output_tokens=16)
        # No `text` and no `reasoning_tokens`: a rule has nothing to say to the human and
        # spends nothing thinking, and a made-up count in the token line would be theatre.
        return ProviderTurn(
            tool_calls=[call],
            text=None,
            usage=usage,
            stop_reason="tool_use",
            thinking=_scripted_thinking(obs, decisions, call),
        )
