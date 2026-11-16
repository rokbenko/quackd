"""Composite verbs close their own loops on the camera — the steering tier, tested alone."""

from __future__ import annotations

import asyncio
import math
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from annotated_types import Ge, Le
from PIL import Image, ImageDraw

from roboglot.adapters.manifest import RobotManifest, VerbSpec
from roboglot.glotfile.parser import load_glot, parse_glot_text
from roboglot.host import HostBox, HostDetections
from roboglot.perception.base import Detection
from roboglot.perception.color_blob import ColorBlobDetector
from roboglot.perception.host import HostDetector
from roboglot.perception.palette import BALL
from roboglot.perception.yolo import GUESSED_FOV_DEG, HEIGHT_M, YoloDetector
from roboglot.safety import Executor
from roboglot.transport.base import Ack, Intent
from roboglot.transport.mock import MockTransport
from roboglot.verbs import core
from roboglot.verbs.core import (
    LOST_TICKS,
    LOST_WZ,
    MAX_SCAN_STEPS,
    STEER_TURN_S,
    TICK_S,
    ApproachAndParams,
    GoToParams,
    SearchScanParams,
    scan_steps,
    views_round,
)
from roboglot.verbs.registry import STOP_ENDED, STOPPED, VerbContext, VerbRegistry, VerbResult
from roboglot_open_duck.mock import OpenDuckMock

GLOT = parse_glot_text(
    """---
glot: 0
name: t
description: d
verbs:
  allow: [search_scan, walk_to, kick, approach_and, grab]
success: [x]
---
# Task
x
"""
)


def _executor(registry: VerbRegistry, facing: float) -> tuple[Executor, OpenDuckMock]:
    """A body that walks and sees: the Open Duck's mock integrates every twist it is given and
    draws its orange ball where it lies in the world, from wherever the duck has got to, facing
    `facing` radians from the ball's side of the room to begin with."""
    body = OpenDuckMock()
    body.theta = facing
    ex = Executor(registry, body, contract=GLOT.frontmatter, detector=ColorBlobDetector())
    return ex, body


def _ball_from(body: OpenDuckMock) -> tuple[float, float]:
    """How far the ball truly is from the duck, and its bearing off where the camera points."""
    assert body.ball_xy is not None
    dx, dy = body.ball_xy[0] - body.x, body.ball_xy[1] - body.y
    bearing = math.atan2(dy, dx) - body.theta - math.radians(body.head_yaw_deg)
    return math.hypot(dx, dy), math.atan2(math.sin(bearing), math.cos(bearing))


async def test_search_scan_finds_the_ball(registry: VerbRegistry) -> None:
    ex, body = _executor(registry, facing=math.pi)
    assert abs(_ball_from(body)[1]) > math.radians(90), "the ball starts behind the duck"
    result = await ex.run_verb("search_scan", {"target": "ball"})
    assert result.ok, result.summary
    _dist, bearing = _ball_from(body)
    assert abs(bearing) < 0.9  # the ball is now in the 90° field of view


async def test_walk_to_reaches_the_ball(registry: VerbRegistry) -> None:
    ex, body = _executor(registry, facing=2.0)
    assert (await ex.run_verb("search_scan", {"target": "ball"})).ok
    result = await ex.run_verb("walk_to", {"target": "ball", "stop_distance": 0.25})
    assert result.ok, result.summary
    true_dist, true_bearing = _ball_from(body)
    assert true_dist <= 0.36
    assert abs(true_bearing) < 0.6


async def test_walk_to_without_detector_explains(registry: VerbRegistry) -> None:
    transport = MockTransport()
    ex = Executor(registry, transport, contract=GLOT.frontmatter, detector=None)
    result = await ex.run_verb("walk_to")
    assert not result.ok and "detector" in result.summary


class _BallUnderfoot:
    """A detector that sees the ball dead ahead and close enough that `go_to` has arrived and
    `search_scan` has found it on the first frame either looks at."""

    name = "ball_underfoot"

    def detect(self, image: Image.Image) -> list[Detection]:
        return [
            Detection(label="ball", cx=0.5, cy=0.5, area=0.01, bearing_deg=0.0, est_distance_m=0.1)
        ]


class _Glancing(MockTransport):
    """A body whose robot turns the head on its own while it stands still, as a Microduck on
    daemon-v0.16.1 does, and says how long that takes to wear off once a look arrives. It notes
    the body's clock at every frame it hands out, and what had been sent by the first."""

    def __init__(self, head_fade_s: float) -> None:
        super().__init__()
        self.head_fade_s = head_fade_s
        self.handed_out_at: list[float] = []
        self.sent_by_the_first_frame: list[str] | None = None

    async def get_frame(self) -> Image.Image | None:
        if self.sent_by_the_first_frame is None:
            self.sent_by_the_first_frame = [intent.kind for intent in self.intents]
        self.handed_out_at.append(self.now())
        return await super().get_frame()


@pytest.mark.parametrize("verb", ["go_to", "search_scan"])
@pytest.mark.parametrize("fade_s", [1.5, 0.0], ids=["glances", "still"])
async def test_a_steering_verb_lets_the_head_settle_before_its_first_frame(
    registry: VerbRegistry, verb: str, fade_s: float
) -> None:
    """Both verbs centre the head and then act on the next frame. On a body whose head was
    glancing when the look arrived, that frame came from a head still most of a glance off
    centre. They wait `head_fade_s` first, with nothing sent that moves the body, and a body
    whose head moves only when asked waits for nothing, as before."""
    body = _Glancing(head_fade_s=fade_s)
    ex = Executor(registry, body, contract=GLOT.frontmatter, detector=_BallUnderfoot())
    result = await ex.run_verb(verb, {"target": "ball"})
    assert result.ok, result.summary
    assert body.sent_by_the_first_frame == ["look"]
    assert body.handed_out_at[0] == pytest.approx(fade_s)


# ── a body that hands out frames it took a while before ─────────────────────────────────

#: The slice of its own clock `_Kept` moves its heading and its camera on by.
KEPT_STEP_S = 0.005
#: How long `_Kept` goes on turning at the last twist it was sent: robotd's deadman window.
KEPT_DEADMAN_S = 0.3


class _Kept(MockTransport):
    """A body that hands out frames it took a while before, and says when, as a Microduck on
    mediad's `/frame` does. Its camera asks for a picture every `period` of the body's clock, or
    as soon as the last one came when that took longer, takes it as it asks, and has it `fetch`
    later, and `get_frame` hands out the newest that has come. The body walks and turns at the
    last twist it was sent until a stop, or until twists stop coming for a deadman window, and
    each frame carries the time it was taken and the heading it was taken at, in degrees."""

    def __init__(self, *, period: float, fetch: float, head_fade_s: float = 0.0) -> None:
        super().__init__()
        self.head_fade_s = head_fade_s
        self.fetch = fetch
        self.every = max(period, fetch)
        self.x = self.y = self.yaw_deg = 0.0
        self.vx = self.wz = 0.0
        self.twisted_at = 0.0
        self.handed_out: list[Image.Image] = []
        # one frame has come already, as one has once a real camera connects, and the camera
        # asks for the next one now
        self.newest: Image.Image = self._picture(-self.every)
        self.coming: list[tuple[float, Image.Image]] = []
        self.next_ask = 0.0

    def _picture(self, taken_at: float) -> Image.Image:
        picture = Image.new("RGB", (8, 8))
        picture.info.update(taken_at=taken_at, yaw_deg=self.yaw_deg)
        return picture

    def _run(self, seconds: float) -> None:
        slices = max(1, math.ceil(seconds / KEPT_STEP_S))
        for _ in range(slices):
            self._t += seconds / slices
            if self._t - self.twisted_at > KEPT_DEADMAN_S:
                self.vx = self.wz = 0.0
            self.yaw_deg += math.degrees(self.wz * seconds / slices)
            self.x += self.vx * math.cos(math.radians(self.yaw_deg)) * seconds / slices
            self.y += self.vx * math.sin(math.radians(self.yaw_deg)) * seconds / slices
            while self.next_ask <= self._t:
                self.coming.append((self.next_ask + self.fetch, self._picture(self.next_ask)))
                self.next_ask += self.every
            while self.coming and self.coming[0][0] <= self._t:
                self.newest = self.coming.pop(0)[1]

    def frame_taken_at(self) -> float:
        return float(self.newest.info["taken_at"])

    async def get_frame(self) -> Image.Image | None:
        self.handed_out.append(self.newest)
        return self.newest

    async def send_intent(self, intent: Intent) -> Ack:
        if intent.kind == "move":
            self.vx, self.wz = float(intent.params["vx"]), float(intent.params["wz"])
            self.twisted_at = self._t
        return await super().send_intent(intent)

    async def stop(self) -> None:
        self.vx = self.wz = 0.0
        await super().stop()

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self._run(seconds)
        await asyncio.sleep(0)


@pytest.mark.parametrize("verb", ["go_to", "search_scan"])
async def test_the_first_frame_acted_on_was_taken_after_the_head_settled(
    registry: VerbRegistry, verb: str
) -> None:
    """The wait for a glance to fade did not make the frame after it a frame taken after it.
    A body that hands out the newest frame it holds, which on mediad's `/frame` was asked for up
    to a frame period and a fetch before, handed both verbs a frame from mid-glance, or from
    before the look. Here frames come every half second and take as long to fetch, so the
    newest frame when the wait ends was taken half a second before it. The first frame acted on
    is one taken once the wait had ended."""
    fade_s = 1.5
    body = _Kept(period=0.5, fetch=0.5, head_fade_s=fade_s)
    ex = Executor(registry, body, contract=GLOT.frontmatter, detector=_BallUnderfoot())
    result = await ex.run_verb(verb, {"target": "ball"})
    assert result.ok, result.summary
    first = body.handed_out[0].info["taken_at"]
    assert first >= fade_s - 1e-9, f"the first frame acted on was taken at {first:g} s"
    assert [i.kind for i in body.intents if i.kind != "stop"] == ["look"], "nothing moved first"


@pytest.mark.parametrize("fade_s", [1.5, 0.0], ids=["glances", "still"])
async def test_search_scan_looks_at_each_heading_from_that_heading(
    registry: VerbRegistry, fade_s: float
) -> None:
    """`search_scan` turned, let the view settle for a tick and looked at the newest frame, which
    on a camera slower than a tick was taken during the turn or before it: a target found there
    was reported a step late, at a heading the duck had already turned away from. Each view is
    a frame taken after its turn had settled now, so the frame looked at after each turn shows
    the heading the turns add up to."""
    step = 45.0
    body = _Kept(period=0.5, fetch=0.5, head_fade_s=fade_s)
    ex = Executor(registry, body, contract=GLOT.frontmatter, detector=_Empty())
    result = await ex.run_verb("search_scan", {"target": "ball", "step_deg": step, "max_steps": 4})
    assert not result.ok and "not found" in result.summary
    seen = [round(frame.info["yaw_deg"], 1) for frame in body.handed_out]
    assert seen == [0.0, 45.0, 90.0, 135.0, 180.0], seen


@pytest.mark.parametrize(("fade_s", "after_each_turn"), [(1.5, ["look"]), (0.0, [])])
async def test_search_scan_claims_the_head_again_after_each_turn_on_a_body_that_glances(
    registry: VerbRegistry, fade_s: float, after_each_turn: list[str]
) -> None:
    """Each turn ends with a stop, and on a Microduck a stop ends the gaze hold the look before
    the scan started, so the head was nobody's for the rest of the scan, and robotd glances once
    the duck has stood still long enough while it waits for a frame. On a body whose head
    glances on its own the scan centres it again after every turn, before anything else goes
    out. A body whose head moves only when asked is sent what it always was."""
    body = _Kept(period=0.5, fetch=0.5, head_fade_s=fade_s)
    ex = Executor(registry, body, contract=GLOT.frontmatter, detector=_Empty())
    await ex.run_verb("search_scan", {"target": "ball", "step_deg": 45.0, "max_steps": 3})
    kinds = [intent.kind for intent in body.intents]
    assert kinds[0] == "look"
    stops = [at for at, kind in enumerate(kinds) if kind == "stop"]  # each turn ends with one
    assert len(stops) == 3, kinds
    for at in stops:
        assert kinds[at + 1 : at + 1 + len(after_each_turn)] == after_each_turn, kinds
    assert kinds.count("look") == 1 + len(after_each_turn) * len(stops), kinds


# ── go_to on a camera too slow for its steering law ─────────────────────────────────────

#: A narrow lens, of the width a Microduck's upright frame has, for `_SeesTheBall`.
NARROW_LENS_DEG = 40.0


class _KeptBall(_Kept):
    """`_Kept` on a floor with a ball on it, `ball_m` away and `ball_deg` to the left of where it
    starts out facing. It walks forward at the speed of the last twist as well as turning, and
    each frame carries the ball's bearing and distance from where the body stood and pointed when
    the frame was taken. Its camera says how often a frame comes, `frame_period_s`, unless told
    not to, and then `go_to` steers on it by the law as it always did."""

    def __init__(
        self,
        *,
        period: float,
        fetch: float,
        ball_deg: float,
        ball_m: float,
        says_period: bool = True,
    ) -> None:
        self.ball = (
            ball_m * math.cos(math.radians(ball_deg)),
            ball_m * math.sin(math.radians(ball_deg)),
        )
        self.says_period = says_period
        super().__init__(period=period, fetch=fetch)

    @property
    def frame_period_s(self) -> float | None:
        return self.every if self.says_period else None

    def _picture(self, taken_at: float) -> Image.Image:
        picture = super()._picture(taken_at)
        dx, dy = self.ball[0] - self.x, self.ball[1] - self.y
        bearing = math.degrees(math.atan2(dy, dx)) - self.yaw_deg
        picture.info.update(bearing=(bearing + 180.0) % 360.0 - 180.0, dist=math.hypot(dx, dy))
        return picture


class _SeesTheBall:
    """A detector that finds the ball where the frame says it was, when that is inside a lens
    `lens_deg` wide, and nothing otherwise, and says that lens is the one it measures through."""

    name = "sees_the_ball"

    def __init__(self, lens_deg: float) -> None:
        self.lens_deg = self.fov_deg = lens_deg

    def detect(self, image: Image.Image) -> list[Detection]:
        bearing, dist = float(image.info["bearing"]), float(image.info["dist"])
        if abs(bearing) > self.lens_deg / 2:
            return []
        return [
            Detection(
                label="ball", cx=0.5, cy=0.5, area=0.01, bearing_deg=bearing, est_distance_m=dist
            )
        ]


async def _go_to_twists(
    registry: VerbRegistry, *, period: float, fetch: float, says_period: bool
) -> list[dict[str, float]]:
    """The twists `go_to` sends toward a ball a little off to the left, on `_KeptBall`."""
    body = _KeptBall(period=period, fetch=fetch, ball_deg=12.0, ball_m=1.5, says_period=says_period)
    ex = Executor(registry, body, contract=GLOT.frontmatter, detector=_SeesTheBall(NARROW_LENS_DEG))
    result = await ex.run_verb("walk_to", {"target": "ball", "timeout_s": 60})
    assert result.ok, result.summary
    return [dict(intent.params) for intent in body.intents if intent.kind == "move"]


async def test_go_to_on_a_camera_too_slow_for_its_law_turns_no_further_than_it_measured(
    registry: VerbRegistry,
) -> None:
    """`go_to` turns STEER_GAIN radians a second for every degree of bearing, and measured again
    every tick on the newest frame, which through mediad's `/frame` was the same frame for half a
    second or more. Held that long, one measurement turned the duck past the ball, the next,
    taken before that turn, said to go on, and on a narrow lens the ball left the picture and
    the verb gave up on it. Here frames come every half second and take 0.6 s to come. On a
    camera this slow `go_to` turns no further than each frame measured and looks again only at a
    frame taken once the turn had settled, so the ball never leaves the picture and is reached
    dead ahead."""
    body = _KeptBall(period=0.5, fetch=0.6, ball_deg=12.0, ball_m=1.5)
    ex = Executor(registry, body, contract=GLOT.frontmatter, detector=_SeesTheBall(NARROW_LENS_DEG))
    result = await ex.run_verb("walk_to", {"target": "ball", "timeout_s": 60})
    assert result.ok, result.summary
    seen = [abs(frame.info["bearing"]) for frame in body.handed_out]
    assert max(seen) <= NARROW_LENS_DEG / 2, f"the ball got {max(seen):.1f}° off the axis"
    assert abs(result.data["bearing_deg"]) < 1.0, result.summary


@pytest.mark.parametrize(
    ("period", "measures_first"),
    [(STEER_TURN_S / 2 * 0.9, False), (STEER_TURN_S / 2 * 1.1, True)],
    ids=["keeps-up", "too-slow"],
)
async def test_go_to_measures_first_only_where_two_frame_periods_outlast_its_turn(
    registry: VerbRegistry, period: float, measures_first: bool
) -> None:
    """The law covers a whole bearing in STEER_TURN_S of holding one measurement's turn, and a
    frame can steer for two periods, its own and the fetch of the next, so where two periods are
    longer than that one measurement can carry the duck past what it measured. On a camera
    whose two periods fit inside it, `go_to` sends exactly the twists it sends a body that says
    no period at all, and on one whose do not, it sends others."""
    said = await _go_to_twists(registry, period=period, fetch=period, says_period=True)
    unsaid = await _go_to_twists(registry, period=period, fetch=period, says_period=False)
    assert (said != unsaid) is measures_first


@pytest.mark.parametrize("says_period", [False, True], ids=["keeps-up", "too-slow"])
async def test_go_to_looks_for_a_lost_target_as_far_whether_or_not_it_measures_first(
    registry: VerbRegistry, says_period: bool
) -> None:
    """A target never seen, or lost, is looked for by turning toward the side it was last seen on
    for LOST_TICKS ticks of the lost turn before `go_to` gives up. Measuring first, each look
    turns for as long as the law takes to cover a bearing rather than for a tick, and counts for
    that many ticks, so the duck turns as far before it gives up, give or take one look's turn,
    where counting looks would have spun it round more than once."""
    body = _KeptBall(period=0.5, fetch=0.5, ball_deg=-90.0, ball_m=1.5, says_period=says_period)
    ex = Executor(registry, body, contract=GLOT.frontmatter, detector=_SeesTheBall(NARROW_LENS_DEG))
    result = await ex.run_verb("walk_to", {"target": "ball", "timeout_s": 60})
    assert not result.ok and "lost the ball" in result.summary, result.summary
    as_far = math.degrees(LOST_TICKS * TICK_S * LOST_WZ)
    one_look = math.degrees(STEER_TURN_S * LOST_WZ)
    assert as_far - one_look <= body.yaw_deg <= as_far + 1e-6, body.yaw_deg


# ── search_scan's step, from the lens ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    "lens_deg", [10.0, 22.5, 30.0, 37.3, 40.0, 44.9, 45.0, 46.0, 62.0, 90.0, 120.0]
)
def test_a_scan_left_to_its_step_overlaps_each_view_with_the_next(lens_deg: float) -> None:
    """A step the caller leaves out is the widest that turns a whole circle in whole steps with
    each view overlapping the next, and never wider than the 45° it always was. So a lens wider
    than 45° scans as before, one exactly 45° wide overlaps its views where 45° steps would only
    have met them, a narrower one leaves no wedge between two views unseen, and a count of steps
    left out makes the circle, as far as the bound on steps allows."""
    step, steps = scan_steps(SearchScanParams(), lens_deg)
    assert step < lens_deg, "each view overlaps the next"
    assert step <= 45.0
    if lens_deg > 45.0:
        assert (step, steps) == (45.0, 8), "a wide lens scans as it always did"
    if lens_deg == 45.0:
        assert (step, steps) == (40.0, 9), "views that would meet edge to edge overlap"
    if steps < MAX_SCAN_STEPS:
        assert steps * step == pytest.approx(360.0), "a whole circle"


def test_a_scan_keeps_the_step_and_the_count_it_was_given() -> None:
    """A step or a count the caller gave is the caller's, and a detector that names no lens
    leaves the scan as it always was."""
    assert scan_steps(SearchScanParams(step_deg=45.0), 37.3) == (45.0, 8)
    step, steps = scan_steps(SearchScanParams(max_steps=3), 37.3)
    assert steps == 3 and step < 37.3
    assert scan_steps(SearchScanParams(), None) == (45.0, 8)


async def test_search_scan_finds_a_target_where_its_old_step_left_a_wedge_unseen(
    registry: VerbRegistry,
) -> None:
    """Through a 40° lens, 45° steps see from 20° right of the start to 20° left of it, then from
    25° to 65° left, and a ball 22° to the left stands in the wedge between the two, which a
    whole turn of the old step never looked into. The step comes from the lens the detector
    measures through now, so one view overlaps the next and the scan finds the ball."""
    body = _KeptBall(period=0.5, fetch=0.5, ball_deg=22.0, ball_m=1.5)
    ex = Executor(registry, body, contract=GLOT.frontmatter, detector=_SeesTheBall(NARROW_LENS_DEG))
    result = await ex.run_verb("search_scan", {"target": "ball"})
    assert result.ok, result.summary


# ── a twist the body refuses ends the verb that steers ──────────────────────────────────

#: What `_Refusing` says of every twist it refuses, which a Microduck's transport says in words
#: of its own once the duck has stopped standing mid-verb.
REFUSED = "the duck stopped standing"


class _Refusing(MockTransport):
    """A body that refuses every twist once `refuses` says so, giving `REFUSED` as the reason,
    as a Microduck's transport does once the duck stops standing mid-verb and as any body does
    once its link is gone."""

    def __init__(self, refuses: Callable[[_Refusing], bool]) -> None:
        super().__init__()
        self.refuses = refuses
        self.taken = 0
        self.refused = 0

    async def send_intent(self, intent: Intent) -> Ack:
        if intent.kind == "move" and self.refuses(self):
            self.intents.append(intent)
            self.refused += 1
            return Ack(accepted=False, reason=REFUSED)
        if intent.kind == "move":
            self.taken += 1
        return await super().send_intent(intent)


class _BallAway:
    """A detector that always sees the ball a metre ahead, so `go_to` never arrives."""

    name = "ball_away"

    def detect(self, image: Image.Image) -> list[Detection]:
        return [
            Detection(label="ball", cx=0.5, cy=0.5, area=0.01, bearing_deg=0.0, est_distance_m=1.0)
        ]


class _Empty:
    """A detector that sees nothing, so `search_scan` turns on to its last step."""

    name = "empty"

    def detect(self, image: Image.Image) -> list[Detection]:
        return []


async def test_go_to_stops_at_the_first_twist_the_body_refuses(registry: VerbRegistry) -> None:
    """`go_to` threw away the body's answer to every twist, so a body that refused them was
    steered on regardless until the verb's own timeout, which said only that it had timed out.
    It stops at the first twist refused now, and says why."""
    body = _Refusing(lambda b: b.taken >= 2)
    ex = Executor(registry, body, contract=GLOT.frontmatter, detector=_BallAway())
    result = await ex.run_verb("walk_to", {"target": "ball", "timeout_s": 60})
    assert not result.ok
    assert result.summary == f"go_to: a twist was refused, so the body stopped: {REFUSED}"
    assert result.data["move_refused"] == REFUSED
    assert body.refused == 1, "a twist went out after the body refused one"
    assert body.intents[-1].kind == "stop"


async def test_search_scan_says_how_far_it_turned_when_a_turn_is_refused(
    registry: VerbRegistry,
) -> None:
    """`search_scan` threw the answers away as well, and reported the whole sweep, the target
    not in it, of a body that never turned, which a pilot reads as the room being empty. It
    stops at the turn that was refused, and counts only the turns before it."""
    body = _Refusing(lambda b: b.stops >= 1)  # the first turn ends on a stop; the second is refused
    ex = Executor(registry, body, contract=GLOT.frontmatter, detector=_Empty())
    result = await ex.run_verb("search_scan", {"target": "ball", "step_deg": 90, "max_steps": 4})
    assert not result.ok
    said = "search_scan: a twist was refused, so the scan stopped after 1 of 4 turns (90°): "
    assert result.summary == said + REFUSED
    assert result.data["steps"] == 1
    assert body.refused == 1 and body.intents[-1].kind == "stop"


class _Braked(MockTransport):
    """A body on which `stop` is asked for from beside the verb running on it, as an MCP client
    or a person driving it would, once `when` says so, and which counts every twist it was sent
    after the executor had counted that stop."""

    def __init__(self, when: Callable[[_Braked], bool]) -> None:
        super().__init__()
        self.when = when
        self.executor: Executor | None = None
        self.brake: asyncio.Task[VerbResult] | None = None
        self.twists = 0
        self.twists_after = 0

    def _brake_if_due(self) -> None:
        if self.brake is None and self.executor is not None and self.when(self):
            self.brake = asyncio.get_running_loop().create_task(self.executor.run_verb("stop", {}))

    async def send_intent(self, intent: Intent) -> Ack:
        if intent.kind == "move":
            self.twists += 1
            if self.executor is not None and self.executor.stops:
                self.twists_after += 1
        ack = await super().send_intent(intent)
        if intent.kind == "move":
            self._brake_if_due()
        return ack

    async def stop(self) -> None:
        await super().stop()
        self._brake_if_due()


class _BallHere:
    """A detector that sees the ball right in front, so `go_to` arrives on its first look."""

    name = "ball_here"

    def detect(self, image: Image.Image) -> list[Detection]:
        return [
            Detection(label="ball", cx=0.5, cy=0.5, area=0.2, bearing_deg=0.0, est_distance_m=0.05)
        ]


async def _braked(
    registry: VerbRegistry, body: _Braked, detector: object, verb: str, params: dict[str, object]
) -> tuple[VerbResult, Executor]:
    ex = Executor(registry, body, contract=GLOT.frontmatter, detector=detector)  # type: ignore[arg-type]
    body.executor = ex
    result = await ex.run_verb(verb, params)
    assert body.brake is not None, "the stop was never asked for"
    assert (await body.brake).ok
    return result, ex


async def test_a_stop_from_beside_go_to_ends_it_before_its_next_twist(
    registry: VerbRegistry,
) -> None:
    """A stop asked for while `go_to` steered sent its stop, and the next tick's twist walked
    the body on. No twist goes out once the stop is counted, and the verb says why it ended, in
    the words `watch` uses for the same brake."""
    body = _Braked(lambda b: b.twists >= 3)
    params = {"target": "ball", "timeout_s": 60}
    result, _ex = await _braked(registry, body, _BallAway(), "walk_to", params)
    assert not result.ok
    assert result.summary == f"go_to: {STOPPED}, so the body stopped"
    assert result.data["ended_by"] == STOP_ENDED and result.data["interrupted"] == STOPPED
    assert body.twists_after == 0, "a twist went out after the stop"
    assert body.intents[-1].kind == "stop"


async def test_a_stop_from_beside_search_scan_ends_it_where_it_had_turned_to(
    registry: VerbRegistry,
) -> None:
    """Asked for as the first turn ends, the stop ends the scan before its next look, counting
    the turn it made and no other."""
    body = _Braked(lambda b: b.stops >= 1)
    params = {"target": "ball", "step_deg": 90, "max_steps": 4}
    result, _ex = await _braked(registry, body, _Empty(), "search_scan", params)
    assert not result.ok
    said = f"search_scan: {STOPPED}, so the scan stopped after 1 of 4 turns (90°)"
    assert result.summary == said
    assert result.data["steps"] == 1 and result.data["ended_by"] == STOP_ENDED
    assert body.twists_after == 0, "a twist went out after the stop"


async def test_a_stop_after_the_walk_of_approach_and_ends_it_before_its_then_verb(
    registry: VerbRegistry,
) -> None:
    """The walk arrived and stopped, a stop was asked for in the tick after, and `approach_and`
    used to kick anyway. It ends there now, saying where the walk got to."""
    body = _Braked(lambda b: b.stops >= 1)
    params = {"target": "ball", "then": "kick"}
    result, ex = await _braked(registry, body, _BallHere(), "approach_and", params)
    assert not result.ok
    assert result.summary.startswith(f"approach_and: {STOPPED}, so it stopped before kick: ")
    assert "reached the ball" in result.summary
    assert result.data["ended_by"] == STOP_ENDED
    assert "kick" not in [name for name, _params, _result in ex.history], "it kicked anyway"


class _Confirming(_Refusing):
    """A body whose twists go out unanswered and whose own state says afterwards which it took,
    as a Microduck's robotd does (`twist_shown_since`): it takes the twists of its first `takes`
    turns, each turn ended by a stop, and none after, and refuses as `_Refusing` does."""

    def __init__(self, refuses: Callable[[_Refusing], bool], takes: int) -> None:
        super().__init__(refuses)
        self.takes = takes
        self.shown: list[float] = []
        """When each twist the body took was sent, on its own clock."""

    async def send_intent(self, intent: Intent) -> Ack:
        ack = await super().send_intent(intent)
        if intent.kind == "move" and ack.accepted and self.stops < self.takes:
            self.shown.append(self.now())
        return ack

    def twist_shown_since(self, since: float) -> bool:
        return any(at >= since for at in self.shown)


@pytest.mark.parametrize(
    ("takes", "said", "steps"),
    [
        (2, "the scan stopped after 2 of 4 turns (180°)", 2),
        (
            1,
            "the scan stopped after 1 of 4 turns (90°) the robot's own state confirmed, of the 2 "
            "sent to it",
            1,
        ),
        (
            0,
            "the scan stopped, and the robot's own state confirmed none of the 2 turns sent to "
            "it, so it may not have turned at all",
            0,
        ),
    ],
    ids=["took-both", "took-one", "took-none"],
)
async def test_search_scan_counts_the_turns_the_body_says_it_took_when_a_turn_is_refused(
    registry: VerbRegistry, takes: int, said: str, steps: int
) -> None:
    """A Microduck's robotd answers no twist, so a scan of one that took none of them, stopped at
    last by a refused twist, said it had turned as far as it had sent. On a body whose own state
    says which twists it took, the turns counted are the ones it confirmed, and a body that
    confirmed none is said to have confirmed none, where the turns used to be counted as sent."""
    body = _Confirming(lambda b: b.stops >= 2, takes)  # two turns go out; the third is refused
    ex = Executor(registry, body, contract=GLOT.frontmatter, detector=_Empty())
    result = await ex.run_verb("search_scan", {"target": "ball", "step_deg": 90, "max_steps": 4})
    assert not result.ok
    assert result.summary == f"search_scan: a twist was refused, so {said}: {REFUSED}"
    assert result.data["steps"] == steps


@pytest.mark.parametrize(
    ("takes", "said"),
    [
        (4, "ball not found after 4 steps (360°)"),
        (
            1,
            "ball not found, but the robot's own state confirmed only 1 of the 4 turns sent to "
            "it (90°), so the scan may not have seen all the 360° it was meant to",
        ),
        (
            0,
            "ball not found, but the robot's own state confirmed none of the 4 turns sent to it, "
            "so it may not have turned at all and every look may have been at the same view",
        ),
    ],
    ids=["took-all", "took-one", "took-none"],
)
async def test_search_scan_says_not_found_only_of_the_turns_the_body_says_it_took(
    registry: VerbRegistry, takes: int, said: str
) -> None:
    """A scan too short for the body to refuse any of its twists ends looking, and said it had
    not found the target after the whole sweep it had sent, which a pilot reads as the target not
    being in the room. On a body whose own state says which twists it took, a sweep it did not
    confirm is not reported as one."""
    body = _Confirming(lambda b: False, takes)
    ex = Executor(registry, body, contract=GLOT.frontmatter, detector=_Empty())
    result = await ex.run_verb("search_scan", {"target": "ball", "step_deg": 90, "max_steps": 4})
    assert not result.ok and result.summary == said, result.summary


async def test_a_scan_names_the_one_turn_its_body_did_not_confirm(
    registry: VerbRegistry,
) -> None:
    """One turn is what a slow link's verdict usually lets out before it stops a scan, so the
    sentence for a body that confirmed none of a single turn is the one a pilot meets most, and
    it read "confirmed none of the 1 turn sent to it". It names the turn now, whether a refused
    twist stopped the scan or the scan ran out of turns."""
    stopped = _Confirming(lambda b: b.stops >= 1, takes=0)  # one turn goes out; the next is refused
    ex = Executor(registry, stopped, contract=GLOT.frontmatter, detector=_Empty())
    result = await ex.run_verb("search_scan", {"target": "ball", "step_deg": 90, "max_steps": 4})
    assert result.summary == (
        "search_scan: a twist was refused, so the scan stopped, and the robot's own state did not "
        f"confirm the turn sent to it, so it may not have turned at all: {REFUSED}"
    ), result.summary
    assert result.data["steps"] == 0
    looked = _Confirming(lambda b: False, takes=0)
    ex = Executor(registry, looked, contract=GLOT.frontmatter, detector=_Empty())
    result = await ex.run_verb("search_scan", {"target": "ball", "step_deg": 90, "max_steps": 1})
    assert result.summary == (
        "ball not found, but the robot's own state did not confirm the turn sent to it, so it may "
        "not have turned at all and every look may have been at the same view"
    ), result.summary


async def test_search_scan_says_which_turns_before_a_find_the_body_took(
    registry: VerbRegistry,
) -> None:
    """A target found after turns the body's own state did not confirm was found from a heading
    nobody can vouch for, and the result says so, with the turns it confirmed as its steps."""

    class _ThirdLook:
        name = "third_look"

        def __init__(self) -> None:
            self.looks = 0

        def detect(self, image: Image.Image) -> list[Detection]:
            self.looks += 1
            if self.looks < 3:
                return []
            return [Detection(label="ball", cx=0.5, cy=0.5, area=0.01, bearing_deg=0.0)]

    body = _Confirming(lambda b: False, takes=1)
    ex = Executor(registry, body, contract=GLOT.frontmatter, detector=_ThirdLook())
    result = await ex.run_verb("search_scan", {"target": "ball", "step_deg": 90, "max_steps": 4})
    assert result.ok and result.summary.endswith(
        "(after 2 turn steps, of which the robot's own state confirmed 1)"
    ), result.summary
    assert result.data["steps"] == 1


async def test_a_twist_refused_while_go_to_waits_for_a_frame_ends_it(
    registry: VerbRegistry,
) -> None:
    """While a slow frame is looked at, `go_to` re-sends the twist the last one justified, and
    it threw the answer away there too: a body that refused the re-send was waited on for as
    long as the frame took. The refusal ends the wait, and the verb, at once."""
    body = _Refusing(lambda b: b.taken >= 1)
    never = asyncio.Event()  # the second frame never comes
    handed_out = 0

    async def get_frame() -> Image.Image:
        nonlocal handed_out
        handed_out += 1
        if handed_out > 1:
            await never.wait()
        return Image.new("RGB", (64, 48))

    body.get_frame = get_frame  # type: ignore[method-assign]
    ex = Executor(registry, body, contract=GLOT.frontmatter, detector=_BallAway())
    async with asyncio.timeout(30):  # only ends a test whose wait never ends
        result = await ex.run_verb("walk_to", {"target": "ball"})
    assert not result.ok
    assert result.summary == f"go_to: a twist was refused, so the body stopped: {REFUSED}"
    assert body.refused == 1 and body.intents[-1].kind == "stop"


class _GlancingBraked(MockTransport):
    """A head that glances, so the scan centres it again after each turn, and a stop asked for
    from beside the scan while the first turn's own stop is on the wire."""

    head_fade_s = 0.2

    def __init__(self) -> None:
        super().__init__()
        self.executor: Executor | None = None
        self.brake: asyncio.Task[object] | None = None
        self.after: list[str] = []

    async def send_intent(self, intent: Intent) -> Ack:
        if self.executor is not None and self.executor.stops:
            self.after.append(intent.kind)
        return await super().send_intent(intent)

    async def stop(self) -> None:
        await super().stop()
        if self.brake is None and self.executor is not None and self.intents_of("move"):
            self.brake = asyncio.get_running_loop().create_task(self.executor.run_verb("stop", {}))
            for _ in range(5):  # a round trip, which the stop beside the scan lands in
                await asyncio.sleep(0)


async def test_search_scan_sends_no_look_after_a_stop_beside_it(registry: VerbRegistry) -> None:
    """The look that centres the head after each turn went out unchecked, after the stop asked
    for while that turn ended had been counted."""
    body = _GlancingBraked()
    ex = Executor(registry, body, contract=GLOT.frontmatter, detector=_Empty())  # type: ignore[arg-type]
    body.executor = ex
    result = await ex.run_verb("search_scan", {"target": "ball", "step_deg": 90, "max_steps": 4})
    assert body.brake is not None
    await body.brake
    assert not result.ok and result.data.get("ended_by") == STOP_ENDED, result.summary
    assert "look" not in body.after, f"sent after the stop was counted: {body.after}"


def test_a_sensor_45_degrees_wide_looks_40_degrees_apart_as_a_camera_does() -> None:
    assert views_round(45.0) == (40.0, 9)
    assert scan_steps(SearchScanParams(), 45.0) == (40.0, min(MAX_SCAN_STEPS, 9))
    assert views_round(90.0) == (72.0, 5)


# ── a target the camera cannot range near enough to stop ────────────────────────────────
#
# Every number below comes from the code's own constants: the box detectors' guessed lens
# (`GUESSED_FOV_DEG`), the label heights (`HEIGHT_M`) and `GoToParams`' bounds. The frame is
# 640x480, as the reviewer's repro names it; a box detector's nearest reading grows with the
# frame's width over its height.

FRAME = (640, 480)
TASKS = Path(__file__).resolve().parents[1] / "tasks"


def _bound(kind: type) -> float:
    for m in GoToParams.model_fields["stop_distance"].metadata:
        if isinstance(m, kind):
            return float(m.ge if kind is Ge else m.le)  # type: ignore[attr-defined]
    raise AssertionError("GoToParams.stop_distance lost its bound")


STOP_MIN, STOP_MAX = _bound(Ge), _bound(Le)
STOP_DEFAULT = float(GoToParams.model_fields["stop_distance"].default)


def _frame_tall_m(label: str, size: tuple[int, int] = FRAME, share: float = 1.0) -> float:
    """What a box `share` of the frame's height reads at the guessed lens, by
    `detections_from_boxes`' own arithmetic: as tall as the frame, the nearest a box detector
    can say."""
    w, h = size
    f = (w / 2) / math.tan(math.radians(GUESSED_FOV_DEG) / 2)
    return round(f * HEIGHT_M[label] / (h * share), 3)


def _askable(nearest_m: float) -> float:
    """A nearest reading rounded up to the centimetre, as a refusal names it."""
    return math.ceil(round(nearest_m * 100, 6)) / 100


@dataclass
class _Box:
    xyxy: list[list[float]]
    cls: int
    conf: float


@dataclass
class _Result:
    names: dict[int, str]
    boxes: list[_Box]


#: A box as tall as the frame and a third of its width, centred, as fractions of the frame.
FRAME_TALL = (1 / 3, 0.0, 2 / 3, 1.0)


class _BoxModel:
    """Stands in for YOLO itself: frame after frame, one box of `coco` at the next of
    `boxes` (fractions of the frame), the last kept once they run out. `YoloDetector.detect` reads
    `res.names`, `box.xyxy[0]`, `box.cls` and `box.conf`, and this has exactly those."""

    def __init__(self, coco: str, *boxes: tuple[float, float, float, float]) -> None:
        self.coco = coco
        self.boxes = list(boxes) or [FRAME_TALL]
        self.calls = 0

    def predict(self, image: Image.Image, conf: float, verbose: bool) -> list[_Result]:
        w, h = image.size
        x1, y1, x2, y2 = self.boxes[min(self.calls, len(self.boxes) - 1)]
        self.calls += 1
        return [_Result({0: self.coco}, [_Box([[x1 * w, y1 * h, x2 * w, y2 * h]], 0, 0.9)])]


def _local_yolo(coco: str, *boxes: tuple[float, float, float, float]) -> YoloDetector:
    """The real `YoloDetector`, at the lens it guesses, with only YOLO itself stood in for."""
    det = YoloDetector.__new__(YoloDetector)  # no ultralytics needed
    det._model = _BoxModel(coco, *boxes)
    det.conf = 0.4
    det.fov_deg = GUESSED_FOV_DEG
    det.calibrated = False
    det._told_lens = None
    return det


class _BoardClient:
    """Stands in for `HostClient`: `/detect` answers with one frame-tall box in the pixels of the
    image it was sent, as the board's daemon does."""

    address = "127.0.0.1:0"

    def __init__(self, coco: str) -> None:
        self.coco = coco

    def detect(self, jpeg: bytes, *, conf: float | None = None) -> HostDetections:
        w, h = FRAME
        box = HostBox(self.coco, 0.9, w / 3, 0.0, 2 * w / 3, float(h))
        return HostDetections(w, h, (box,), "yolov8n", "cuda", 10.0)


def _board_yolo(coco: str) -> HostDetector:
    return HostDetector(_BoardClient(coco), fov_deg=GUESSED_FOV_DEG, calibrated=False)  # type: ignore[arg-type]


class _NoDistance:
    """A detector that sees the target dead ahead and gives no distance (`est_distance_m` is
    optional on `Detection`). No shipped detector does; a third party's may."""

    name = "no_distance"

    def detect(self, image: Image.Image) -> list[Detection]:
        return [Detection(label="person", cx=0.5, cy=0.5, area=0.3, bearing_deg=0.0)]


class _Seen:
    """Wraps a detector and keeps every detection it handed `go_to`. It passes on `name`,
    `blocking` and `error` and nothing else, so `go_to` sees it as a third party's detector that
    names no nearest reading of its own."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.name = inner.name
        self.blocking = getattr(inner, "blocking", False)
        self.seen: list[Detection] = []

    @property
    def error(self) -> str | None:
        return getattr(self.inner, "error", None)

    def detect(self, image: Image.Image) -> list[Detection]:
        found = self.inner.detect(image)
        self.seen.extend(found)
        return found


def _alpha_lens() -> tuple[tuple[int, int], float]:
    """A Microduck alpha's upright `/frame` (9:16) and the lens its transport assumes for it,
    by the transport's own formula (`JsonRpcUnixTransport._assumed_lens`)."""
    from roboglot_microduck import upstream_api as up

    w, h = 270, 480
    half = math.radians(up.IMX219_FOV_DEG) / 2
    return (w, h), math.degrees(2 * math.atan(math.tan(half) * w / h))


def _ball_across_the_bottom(size: tuple[int, int]) -> Image.Image:
    """An orange disc as wide as the frame, touching its bottom edge: a ball under the nose."""
    w, h = size
    img = Image.new("RGB", size, (40, 40, 40))
    ImageDraw.Draw(img).ellipse([0, h - w, w - 1, h - 1], fill=BALL)
    return img


def _colour_on_alpha() -> ColorBlobDetector:
    _, fov = _alpha_lens()
    return ColorBlobDetector(fov_deg=fov, calibrated=False)


def _body_for(detector: Any) -> MockTransport:
    """A 640x480 body for the box detectors, and an alpha's upright frame with a ball under the
    nose for the colour detector."""
    if isinstance(detector, ColorBlobDetector):
        size, _ = _alpha_lens()
        return MockTransport(frame_size=size, frames=[_ball_across_the_bottom(size)])
    return MockTransport(frame_size=FRAME)


def test_the_colour_detector_marks_a_ball_as_wide_as_the_frame_and_names_its_reading_nearest() -> (
    None
):
    """The colour detector reads a ball the frame cuts off by its width, so a ball as wide as the
    frame reads `0.05 / tan(fov / 2)` however near it comes: 0.148 m on a Microduck alpha's
    upright frame, where a stop distance of 0.1 was never reached and the body crept into the
    ball. Such a ball is marked `clipped`, and the nearest the detector names is what the widest
    ball it does not mark reads, a little farther; one the frame cuts at one side only, which
    turning toward it brings back into view, is not marked, and nor is one inside the frame."""
    size, _ = _alpha_lens()
    w, h = size
    detector = _colour_on_alpha()
    (across,) = detector.detect(_ball_across_the_bottom(size))
    nearest = detector.nearest_m("ball", size)
    assert across.clipped and nearest is not None and across.est_distance_m < nearest
    one_side = Image.new("RGB", size, (40, 40, 40))
    ImageDraw.Draw(one_side).ellipse([-w // 8, h // 3, w // 4, h // 3 + 3 * w // 8], fill=BALL)
    inside = Image.new("RGB", size, (40, 40, 40))
    ImageDraw.Draw(inside).ellipse([w // 4, h // 3, w // 2, h // 3 + w // 4], fill=BALL)
    assert [d.clipped for img in (one_side, inside) for d in detector.detect(img)] == [False] * 2
    assert detector.nearest_m("person", size) is None


#: A target in `_World`: (COCO name, how far along x it stands in m, its height, its width,
#: the confidence a network gives it).
Target = tuple[str, float, float, float, float]


class _World(OpenDuckMock):
    """The Open Duck's walking mock, which integrates every twist it is sent, with no ball of its
    own and blank frames `size` big: the targets standing on the x axis ahead of it are drawn as
    the boxes an ideal network would give them (`boxes`), or, with `colour`, as an orange disc
    of a ball's size centred in the frame. The camera is at each target's mid-height, the case
    most favourable to measuring, looking through a lens `fov_deg` wide."""

    def __init__(
        self,
        size: tuple[int, int],
        targets: list[Target],
        *,
        fov_deg: float = GUESSED_FOV_DEG,
        colour: bool = False,
        inset: float = 0.0,
    ) -> None:
        super().__init__(ball_xy=None)
        self.size, self.targets, self.fov_deg = size, targets, fov_deg
        self.colour, self.inset = colour, inset

    def _focal(self) -> float:
        return (self.size[0] / 2) / math.tan(math.radians(self.fov_deg) / 2)

    def boxes(self) -> list[tuple[str, float, float, float, float, float]]:
        """Each target's box as a network clips it to the frame, a cut edge reported `inset`
        pixels inside the frame's, as a network that regressed it on a coarser input might."""
        w, h = self.size
        f = self._focal()
        out = []
        for name, x, height, width, conf in self.targets:
            d = x - self.x
            if d <= 0:
                continue
            half_h, half_w = f * height / 2 / d, f * width / 2 / d
            x1, x2 = max(0.0, w / 2 - half_w), min(float(w), w / 2 + half_w)
            y1, y2 = max(0.0, h / 2 - half_h), min(float(h), h / 2 + half_h)
            y1, y2 = max(y1, self.inset), min(y2, h - self.inset)
            out.append((name, conf, x1, y1, x2, y2))
        return out

    async def get_frame(self) -> Image.Image | None:
        img = Image.new("RGB", self.size, (40, 40, 40))
        if self.colour:
            w, h = self.size
            for _name, x, _height, width, _conf in self.targets:
                r = self._focal() * width / 2 / (x - self.x)
                ImageDraw.Draw(img).ellipse([w / 2 - r, h / 2 - r, w / 2 + r, h / 2 + r], fill=BALL)
        return img

    def gap(self) -> float:
        """How far the nearest target truly is."""
        return min(x for _name, x, *_ in self.targets) - self.x


class _WorldBoard:
    """The board's HTTP client, answering `/detect` with `_World`'s boxes."""

    address = "stand-in:0"

    def __init__(self, world: _World) -> None:
        self.world = world

    def detect(self, jpeg: bytes, *, conf: float | None = None) -> HostDetections:
        w, h = self.world.size
        found = tuple(HostBox(*box) for box in self.world.boxes())
        return HostDetections(w, h, found, "yolov8n", "cuda", 10.0)


class _WorldNet:
    """Stands in for YOLO itself, giving `_World`'s boxes for every frame."""

    def __init__(self, world: _World) -> None:
        self.world = world

    def predict(self, image: Image.Image, conf: float, verbose: bool) -> list[_Result]:
        boxes = self.world.boxes()
        names = {i: name for i, (name, *_rest) in enumerate(boxes)}
        found = [_Box([[x1, y1, x2, y2]], i, c) for i, (_n, c, x1, y1, x2, y2) in enumerate(boxes)]
        return [_Result(names, found)]


def _board_on(world: _World) -> HostDetector:
    return HostDetector(_WorldBoard(world), fov_deg=world.fov_deg, calibrated=False)  # type: ignore[arg-type]


def _local_on(world: _World) -> YoloDetector:
    det = _local_yolo("person")
    det._model = _WorldNet(world)
    det.fov_deg = world.fov_deg
    return det


def _colour_on(world: _World) -> ColorBlobDetector:
    return ColorBlobDetector(fov_deg=world.fov_deg, calibrated=False)


def _forward(body: MockTransport) -> list[float]:
    """Every forward speed the body was sent."""
    return [float(i.params["vx"]) for i in body.intents_of("move") if i.params["vx"] > 0]


RANGE_CASES = [
    # (id, detector factory, label, stop_distance)
    ("ball, colour, alpha microduck lens, stop at the least", _colour_on_alpha, "ball", STOP_MIN),
    ("follow-me: person, local yolo, stop 0.5", lambda: _local_yolo("person"), "person", 0.5),
    ("person, local yolo, stop at the most", lambda: _local_yolo("person"), "person", STOP_MAX),
    ("person, board yolo, stop at the most", lambda: _board_yolo("person"), "person", STOP_MAX),
    ("pet, local yolo, default stop", lambda: _local_yolo("dog"), "pet", STOP_DEFAULT),
    ("ball, board yolo, stop at the least", lambda: _board_yolo("sports ball"), "ball", STOP_MIN),
    ("no distance at all, default stop", lambda: _NoDistance(), "person", STOP_DEFAULT),
]


@pytest.mark.parametrize(
    ("make", "label", "stop"), [c[1:] for c in RANGE_CASES], ids=[c[0] for c in RANGE_CASES]
)
async def test_go_to_never_walks_on_toward_a_target_that_fills_the_frame_or_has_no_distance(
    registry: VerbRegistry, make: Any, label: str, stop: float
) -> None:
    """`go_to` arrived only when `est_distance_m <= stop_distance`, and a box detector's distance
    (`f * HEIGHT_M[label] / box height`) cannot fall below what a box as tall as the frame reads:
    1.775 m for a person on a 4:3 frame at the guessed lens, above every stop distance the verb
    took. Below that nothing ever arrived, and the verb walked on at up to 0.2 m/s toward a target
    already filling the frame until its timeout. The colour detector's ball as wide as the frame
    floors the same way, and a detector that gives no distance never arrived either. A target that
    fills the frame, or has no distance, is not walked toward now: no forward twist goes out.

    Driven through the executor with the shipped `tasks/follow-me.glot` as the contract and
    `walk_to` as its pilot calls it. The detectors are the real `YoloDetector.detect`,
    `HostDetector.detect` and `ColorBlobDetector.detect`, with only YOLO itself or the board's
    HTTP client stood in for, and wrapped so that `go_to` cannot ask them for their nearest reading:
    what stops it here is the detection itself."""
    task = load_glot(str(TASKS / "follow-me.glot"))
    inner = make()
    detector = _Seen(inner)
    body = _body_for(inner)
    ex = Executor(registry, body, contract=task.frontmatter, detector=detector)
    timeout = 2.0
    result = await ex.run_verb(
        "walk_to", {"target": label, "stop_distance": stop, "timeout_s": timeout}
    )

    dists = sorted({d.est_distance_m for d in detector.seen}, key=lambda d: d or 0.0)
    if isinstance(inner, ColorBlobDetector):
        # a ball as wide as the frame is read at half the frame's width: 0.05 / tan(fov / 2)
        assert dists and min(dists) > stop, dists  # type: ignore[operator]
    elif not isinstance(inner, _NoDistance):
        assert dists == [_frame_tall_m(label)], dists
    forward = [i.params["vx"] for i in body.intents_of("move") if i.params["vx"] > 0]
    assert not forward, (
        f"walk_to sent {len(forward)} forward twists (vx {sorted(set(forward))} m/s) over "
        f"{body.now():.1f} s of the body's clock toward a {label} filling the frame, ending "
        f"{result.summary!r}; every detection read {dists} m against stop_distance {stop}"
    )


REFUSAL_CASES = [
    # (id, detector factory, label, stop_distance, the nearest the camera can measure)
    (
        "follow-me: person, local yolo",
        lambda: _local_yolo("person"),
        "person",
        0.5,
        _frame_tall_m("person"),
    ),
    ("person, board yolo", lambda: _board_yolo("person"), "person", 0.5, _frame_tall_m("person")),
    ("pet, local yolo", lambda: _local_yolo("dog"), "pet", STOP_DEFAULT, _frame_tall_m("pet")),
    (
        "ball, board yolo",
        lambda: _board_yolo("sports ball"),
        "ball",
        STOP_MIN,
        _frame_tall_m("ball"),
    ),
    (
        "ball, colour, alpha microduck lens",
        _colour_on_alpha,
        "ball",
        STOP_MIN,
        round(0.05 / math.tan(math.radians(_alpha_lens()[1]) / 2), 3),
    ),
]


@pytest.mark.parametrize(
    ("make", "label", "stop", "nearest"),
    [c[1:] for c in REFUSAL_CASES],
    ids=[c[0] for c in REFUSAL_CASES],
)
async def test_go_to_refuses_a_stop_distance_nearer_than_the_camera_can_measure_and_names_it(
    registry: VerbRegistry, make: Any, label: str, stop: float, nearest: float
) -> None:
    """A stop distance under the nearest this detector can read for this label, lens and frame
    could never be reached, and the body walked on into the target. It is refused now on the
    first frame, with nothing sent but the look that centres the head, which the refusal says,
    naming a stop distance the camera can measure, and that distance can be asked for: here,
    where every frame shows a box as tall as the frame, the walk it gives stops at once and says
    it is as near as the camera can tell."""
    body = _body_for(make())
    ex = Executor(registry, body, contract=GLOT.frontmatter, detector=make())
    refused = await ex.run_verb("walk_to", {"target": label, "stop_distance": stop})
    assert not refused.ok and "nearest_m" in refused.data, refused
    need = refused.data["nearest_m"]
    assert _askable(nearest) <= need, f"named {need} m, nearer than the frame lets it read"
    assert f"Ask for {need:.2f} m or more." in refused.summary, refused.summary
    assert "nothing was sent but the look that centres the head" in refused.summary
    assert [i.kind for i in body.intents] == ["look"], "the refusal came after the body moved"
    assert need <= STOP_MAX, f"the nearest the camera can measure, {need} m, cannot be asked for"

    asked = await ex.run_verb("walk_to", {"target": label, "stop_distance": need})
    assert asked.ok, asked.summary
    assert asked.summary.startswith(f"as near to the {label} as the camera can tell"), asked
    assert "distance_m" not in asked.data and asked.data["at_most_m"] >= nearest, asked.data
    assert not [i for i in body.intents_of("move") if i.params["vx"] > 0]
    assert body.intents[-1].kind == "stop"


#: A pet a long way off, its box a quarter of the frame's height and inside it, then near
#: enough that the frame cuts the top of its box off at half the frame's height.
PET_FAR = (1 / 3, 3 / 8, 2 / 3, 5 / 8)
PET_CUT = (1 / 3, 0.0, 2 / 3, 1 / 2)


async def test_go_to_stops_once_the_target_runs_off_the_frame_and_says_it_is_as_near_as_it_can_tell(
    registry: VerbRegistry,
) -> None:
    """A box the frame cuts at its top or bottom reads farther than the target is, since its
    distance comes from its height, and it only gets worse nearer in: the body kept walking on a
    reading that fell more slowly than the gap did. The approach ends at the first such frame now,
    with the body stopped, and the result says the target fills the frame, that it is at most the
    last reading away and that the camera cannot measure nearer. It is not a failure: follow-me
    ends every leg that way, so three of them in a row do not trip its `abort_when`. Nor does it
    say "reached" with a distance nobody measured: the data has `at_most_m`, not `distance_m`."""
    task = load_glot(str(TASKS / "follow-me.glot"))
    legs = task.frontmatter.repeat_failure_abort
    assert legs is not None
    far, cut = (_frame_tall_m("pet", share=b[3] - b[1]) for b in (PET_FAR, PET_CUT))
    stop = STOP_DEFAULT * 2
    assert _frame_tall_m("pet") < stop < cut < far
    body = MockTransport(frame_size=FRAME)
    detector = _local_yolo("dog", *[PET_FAR, PET_CUT] * legs)
    ex = Executor(registry, body, contract=task.frontmatter, detector=detector)
    for _ in range(legs):
        sent = len(body.intents)
        result = await ex.run_verb("walk_to", {"target": "pet", "stop_distance": stop})
        assert result.ok, result.summary
        assert result.summary.startswith(
            "as near to the pet as the camera can tell: it fills the frame and reads "
            f"~{cut:.2f} m, the farthest it can be if it is the size the detector takes it for"
        ), result.summary
        assert result.data["at_most_m"] == cut and "distance_m" not in result.data
        assert result.data["cannot_range"] == "fills the frame"
        leg = body.intents[sent:]
        moves = [i for i in leg if i.kind == "move"]
        assert [m.params["vx"] > 0 for m in moves] == [True], f"one tick's walk, then: {leg}"
        assert [i.kind for i in leg[leg.index(moves[-1]) + 1 :]] == ["stop"], leg
    assert ex.consecutive_failures.get("go_to", 0) == 0 and not ex.abort.is_set()


class _DistanceThenNone:
    """A third party's detector: the ball dead ahead three metres off, then no distance."""

    name = "then_none"

    def __init__(self) -> None:
        self.calls = 0

    def detect(self, image: Image.Image) -> list[Detection]:
        self.calls += 1
        far = 3.0 if self.calls == 1 else None
        return [
            Detection(label="ball", cx=0.5, cy=0.5, area=0.1, bearing_deg=0.0, est_distance_m=far)
        ]


async def test_approach_and_runs_its_then_verb_only_where_go_to_measured_the_stop_distance(
    registry: VerbRegistry,
) -> None:
    """`then` is promised at the stop distance. Where `go_to` ends as near as the camera can
    tell, it knows only that the target is at most so far away, which can be farther than the stop
    distance: a ball whose box the frame cuts at the top while it is still out of reach. The kick
    went out all the same, since that ending is a success. `approach_and` now leaves the body
    stopped there, runs no `then`, and fails naming the verb it did not run and carrying
    `at_most_m`, so the pilot can run it itself. Where that bound is within the stop distance,
    the target certainly is too, and `then` runs as it did at the base commit: a ball that fills
    the frame was kicked before the camera-limit ending existed, and is kicked now. An arrival by
    measurement kicks as before."""
    body = MockTransport(frame_size=FRAME)
    ex = Executor(registry, body, contract=GLOT.frontmatter, detector=_local_yolo("sports ball"))
    refused = await ex.run_verb("walk_to", {"target": "ball", "stop_distance": STOP_MIN})
    stop = refused.data["nearest_m"]
    cut = _frame_tall_m("ball", share=PET_CUT[3] - PET_CUT[1])
    assert cut > stop, "the ball has to be cut while it may still be out of reach"
    body = MockTransport(frame_size=FRAME)
    detector = _local_yolo("sports ball", PET_CUT)
    ex = Executor(registry, body, contract=GLOT.frontmatter, detector=detector)
    result = await ex.run_verb(
        "approach_and", {"target": "ball", "stop_distance": stop, "then": "kick"}
    )
    assert not result.ok
    assert result.summary.startswith(
        "approach_and did not run kick: go_to stopped as near to the ball as the camera can tell"
    ), result.summary
    assert f"may be farther than the stop_distance of {stop:g} m" in result.summary
    assert result.data["at_most_m"] == cut and result.data["not_run"] == "kick", result.data
    assert "kick" not in [name for name, _params, _result in ex.history], "it kicked anyway"
    assert body.intents[-1].kind == "stop"

    # a ball as wide as the frame, read by the colour detector at an upper bound under the stop
    within = Executor(
        registry,
        MockTransport(frames=[Image.new("RGB", (64, 64), BALL)]),
        contract=GLOT.frontmatter,
        detector=ColorBlobDetector(),
    )
    kicked = await within.run_verb(
        "approach_and", {"target": "ball", "stop_distance": STOP_DEFAULT, "then": "kick"}
    )
    assert kicked.ok and "kick" in [name for name, _params, _result in within.history], kicked
    assert kicked.summary.startswith("as near to the ball as the camera can tell"), kicked
    assert "may be farther" not in kicked.summary
    assert kicked.data["go_to"]["at_most_m"] <= STOP_DEFAULT

    measured = Executor(
        registry, MockTransport(), contract=GLOT.frontmatter, detector=_BallUnderfoot()
    )
    arrived = await measured.run_verb("approach_and", {"target": "ball", "then": "kick"})
    assert arrived.summary.startswith("reached the ball: ~0.10 m away, bearing +0°; then kick: ")
    assert "kick" in [name for name, _params, _result in measured.history]


async def test_a_detector_that_stops_giving_a_distance_ends_the_approach_at_its_last_reading(
    registry: VerbRegistry,
) -> None:
    """`dist is None` never arrived, so a detection that stopped giving a distance was walked
    toward at full speed until the timeout. A detector that never gave one is refused before
    anything moves (the case above); one that stops giving one mid-approach ends it with the body
    stopped, as near as the camera can tell, the last reading quoted as at most that far."""
    body = MockTransport()
    ex = Executor(registry, body, contract=GLOT.frontmatter, detector=_DistanceThenNone())
    result = await ex.run_verb("walk_to", {"target": "ball", "timeout_s": 60})
    assert result.ok, result.summary
    assert result.summary.startswith("as near to the ball as the camera can tell"), result
    assert result.data["at_most_m"] == 3.0 and result.data["cannot_range"] == "no distance"
    assert len(body.intents_of("move")) == 1 and body.intents[-1].kind == "stop"


@pytest.mark.parametrize("wide", [True, False], ids=["16:9", "4:3"])
async def test_go_to_takes_a_stop_distance_as_far_as_a_person_reads_at_the_nearest_on_a_wide_frame(
    registry: VerbRegistry, wide: bool
) -> None:
    """`stop_distance` was capped at 1.5 m, under the 1.775 m a person reads at the nearest on a
    4:3 frame through the guessed lens, so the refusal that names that distance would have made a
    person impossible to approach. The cap is the stop distance a refusal names for a person on
    the widest frame Roboglot expects, a Microduck beta's 16:9, through that lens, so it can be
    asked for, and every narrower frame names less. `approach_and`, which hands its stop
    distance to `go_to`, takes the same."""
    from roboglot_microduck import upstream_api as up

    size = (up.BETA_SOLVE["width"], up.BETA_SOLVE["height"]) if wide else FRAME
    world = _World(size, [("person", 4.0, HEIGHT_M["person"], 0.5, 0.9)])
    ex = Executor(registry, world, contract=GLOT.frontmatter, detector=_board_on(world))
    refused = await ex.run_verb("walk_to", {"target": "person", "stop_distance": STOP_MIN})
    named = refused.data["nearest_m"]
    assert named == STOP_MAX if wide else named < STOP_MAX, (named, STOP_MAX)
    approach = ApproachAndParams.model_fields["stop_distance"].metadata
    assert [m.le for m in approach if isinstance(m, Le)] == [STOP_MAX]


#: Where each label stands to begin with in the world tests, far enough that every detector reads
#: it whole on the first frame and near enough that the walk to it fits go_to's default timeout.
START_M = {"person": 4.0, "pet": 1.5, "ball": 1.0}
#: Its width, for the box's sides and the colour detector's disc (a ball's is its radius twice).
WIDTH_M = {"person": 0.5, "pet": 0.3, "ball": 0.1}

NAMED_CASES = [
    (kind, make, coco, label, size)
    for kind, make in (("local yolo", _local_on), ("board yolo", _board_on))
    for coco, label in (("person", "person"), ("dog", "pet"), ("sports ball", "ball"))
    for size in ((640, 480), (1280, 720))
] + [("colour", _colour_on, "sports ball", "ball", size) for size in ((270, 480), (128, 128))]


@pytest.mark.parametrize(
    ("make", "coco", "label", "size"),
    [c[1:] for c in NAMED_CASES],
    ids=[f"{c[0]}, {c[3]}, {c[4][0]}x{c[4][1]}" for c in NAMED_CASES],
)
async def test_the_stop_distance_a_refusal_names_is_one_go_to_arrives_at_by_measurement(
    registry: VerbRegistry,
    make: Callable[[_World], Any],
    coco: str,
    label: str,
    size: tuple[int, int],
) -> None:
    """A refusal named the nearest a box as big as the frame reads, rounded up, and a box that
    big touches the frame's edge rows, which the detector marks as cut: the tallest box it still
    measures reads farther than the distance named. "Ask for 1.78 m or more", asked, could only
    end as near as the camera can tell, and `approach_and` at that distance never ran its `then`.
    The distance named now is the nearest reading of a box the detector does not mark, rounded
    up past one creeping step, and asking for it walks straight at the target standing whole in
    view and arrives by measurement. The colour detector's case is a ball through a Microduck
    alpha's lens, the narrowest the code assumes, where the least stop distance is refused."""
    fov = _alpha_lens()[1] if make is _colour_on else GUESSED_FOV_DEG
    height = WIDTH_M[label] if make is _colour_on else HEIGHT_M[label]
    world = _World(
        size,
        [(coco, START_M[label], height, WIDTH_M[label], 0.9)],
        fov_deg=fov,
        colour=make is _colour_on,
    )
    ex = Executor(registry, world, contract=GLOT.frontmatter, detector=make(world))
    refused = await ex.run_verb("walk_to", {"target": label, "stop_distance": STOP_MIN})
    assert not refused.ok and "nearest_m" in refused.data, refused
    named = refused.data["nearest_m"]
    assert not _forward(world), "nothing moves before the refusal"
    asked = await ex.run_verb("walk_to", {"target": label, "stop_distance": named})
    assert asked.ok and "distance_m" in asked.data, (
        f"walk_to(stop_distance={named}) ended {asked.summary!r} {asked.data} with the {label} "
        f"truly {world.gap():.3f} m away, so the distance named is not one it arrives at"
    )
    assert asked.data["distance_m"] <= named and world.gap() > named - WIDTH_M[label]


async def test_a_near_target_the_frame_cuts_ends_the_approach_while_it_steers_by_a_far_one(
    registry: VerbRegistry,
) -> None:
    """Two people dead ahead: one a metre away, whose box the frame cuts at top and bottom, and
    one five metres off, seen whole and named first, as a network names its most confident box
    first. `go_to` judged only the first detection of the label, so the near person's mark was
    never read, and the body walked through where the near person stands toward the far one,
    saying it had reached the person. Any detection of the label the frame cuts ends the approach
    now, whichever one it steers by, and the result says it is another one."""
    near = ("person", 1.0, HEIGHT_M["person"], 0.5, 0.6)
    far = ("person", 5.0, HEIGHT_M["person"], 0.5, 0.9)
    world = _World(FRAME, [far, near])
    ex = Executor(registry, world, contract=GLOT.frontmatter, detector=_board_on(world))
    result = await ex.run_verb("walk_to", {"target": "person", "stop_distance": 2.0})
    assert not _forward(world), (
        f"walk_to sent forward twists {sorted(set(_forward(world)))} m/s with a person 1.0 m ahead "
        f"whose box the frame cut; the duck ended {world.gap():+.2f} m from them; it said "
        f"{result.summary!r}"
    )
    assert result.ok and result.summary.startswith(
        "as near to the person as the camera can tell: another person fills the frame and reads ~"
    ), result.summary
    assert result.data["another"] is True and result.data["cannot_range"] == "fills the frame"


async def test_a_ball_that_runs_off_the_bottom_of_the_frame_ends_the_approach(
    registry: VerbRegistry,
) -> None:
    """The Open Duck's mock draws its ball on the floor, so as the duck nears it the ball sinks
    and leaves the frame at the bottom, as a real ball does under a forward camera. Once its
    centre is below the frame the colour detector measures the chord the frame leaves, so the
    reading rose while the ball came nearer; the detector marked only a ball as wide as the
    frame, so `go_to` walked on, the ball left the frame, and the verb said it had lost the ball
    with the duck nearer than the stop distance asked for. The detector marks that ball now, and
    the approach ends as near as the camera can tell, or arrives by measurement first."""
    stop = 0.2
    body = OpenDuckMock(ball_xy=(0.6, 0.0))
    ex = Executor(registry, body, contract=GLOT.frontmatter, detector=ColorBlobDetector())
    result = await ex.run_verb("walk_to", {"target": "ball", "stop_distance": stop})
    assert body.ball_xy is not None
    end = math.hypot(body.ball_xy[0] - body.x, body.ball_xy[1] - body.y)
    assert result.ok and ({"distance_m", "cannot_range"} & set(result.data)), (
        f"walk_to(stop_distance={stop}) ended {result.summary!r} with the duck {end:.3f} m from "
        "the ball"
    )
    assert end >= stop - TICK_S * 0.1, f"the duck ended {end:.3f} m from the ball"


def test_the_colour_detector_marks_a_ball_whose_width_the_frame_may_have_cut() -> None:
    """The colour detector reads a ball's size off its area, or off its width once the frame
    cuts it at the bottom or a side. A ball cut at the bottom whose centre is still in the frame
    is as wide as it ever is, and is measured; one whose centre has left the frame shows only a
    chord, narrower than the ball, and one cut at the top is measured off an area the frame has
    cut too. Both read farther than the ball is, and are marked `clipped`."""
    size = (128, 128)
    w, h = size
    detector = ColorBlobDetector()

    def ball(cx: float, cy: float, r: float) -> Detection:
        img = Image.new("RGB", size, (40, 40, 40))
        ImageDraw.Draw(img).ellipse([cx - r, cy - r, cx + r, cy + r], fill=BALL)
        (found,) = detector.detect(img)
        return found

    r = w / 4
    assert not ball(w / 2, h - r / 2, r).clipped, "centre in the frame, cut at the bottom"
    assert ball(w / 2, h + r / 2, r).clipped, "centre below the frame"
    assert ball(w / 2, r / 2, r).clipped, "cut at the top"
    assert not ball(w / 2, h / 2, r).clipped, "inside the frame"


@pytest.mark.parametrize("reading", [math.nan, math.inf, 0.0, -0.5])
async def test_a_distance_that_is_not_finite_and_above_nought_is_no_distance(
    registry: VerbRegistry, reading: float
) -> None:
    """A third party's detector may give NaN, infinity, nought or less, and none of them is a
    distance a body can be at. NaN and infinity never compared at or under the stop distance, so
    `go_to` walked at full speed for its whole timeout; nought or less was announced as reached.
    Each is no distance now: refused on the first sighting, as a missing one is."""

    class _Third:
        name = "third"

        def detect(self, image: Image.Image) -> list[Detection]:
            return [
                Detection(
                    label="ball", cx=0.5, cy=0.5, area=0.1, bearing_deg=0.0, est_distance_m=reading
                )
            ]

    body = MockTransport()
    ex = Executor(registry, body, contract=GLOT.frontmatter, detector=_Third())
    result = await ex.run_verb("walk_to", {"target": "ball", "timeout_s": 2})
    assert not _forward(body) and "reached" not in result.summary, result
    assert result.summary == (
        "go_to: third gives no distance for the ball, so it cannot tell when to stop, and did not "
        "approach it"
    ), result.summary


async def test_a_cut_box_a_network_reports_two_rows_inside_the_edge_still_ends_the_approach(
    registry: VerbRegistry,
) -> None:
    """A network regresses a box on its own input, ultralytics' 640 pixels on the long side, and
    scales it back, so the edge of a box the frame cut need not land on the frame's outer row.
    With a one-row edge band, a box reported two rows in went unmarked, its reading floored just
    above the distance a refusal names, and `go_to` crept on toward a person filling the frame
    until its timeout. Asked for the distance named, the body stops short of the person."""
    world = _World(FRAME, [("person", 4.0, HEIGHT_M["person"], 0.5, 0.9)], inset=2.0)
    ex = Executor(registry, world, contract=GLOT.frontmatter, detector=_board_on(world))
    refused = await ex.run_verb("walk_to", {"target": "person", "stop_distance": STOP_MIN})
    named = refused.data["nearest_m"]
    result = await ex.run_verb("walk_to", {"target": "person", "stop_distance": named})
    assert result.ok and "timed out" not in result.summary, result.summary
    assert world.gap() > _frame_tall_m("person"), (
        f"walk_to(person, {named}) ended {result.summary!r} with the person {world.gap():.2f} m "
        "away, nearer than a person tall enough to fill the frame"
    )


# ── what go_to's arrival says, the clamp, and a refused look ────────────────────────────


class _UncalibratedBallClose:
    """A detector whose lens was a guess: the ball dead ahead, inside the default stop."""

    name = "uncalibrated"

    def detect(self, image: Image.Image) -> list[Detection]:
        return [
            Detection(
                label="ball",
                cx=0.5,
                cy=0.5,
                area=0.1,
                bearing_deg=0.0,
                est_distance_m=STOP_DEFAULT - 0.05,
                calibrated=False,
            )
        ]


async def test_go_to_s_arrival_says_when_its_distance_was_a_guess() -> None:
    """`go_to`'s success read 'reached the ball: ~D m away' and carried no `calibrated` flag, so
    an arrival judged through a guessed lens read as measured, where `observe` would have said
    '(uncalibrated: distance is a rough guess)'. The summary says so now, in `observe`'s words,
    and the data carries `calibrated: False`. An arrival through a lens somebody measured reads
    as it always did."""
    result = await core.go_to(
        VerbContext(transport=MockTransport(), detector=_UncalibratedBallClose()), GoToParams()
    )
    assert result.ok, result.summary
    assert result.summary.endswith("(uncalibrated: distance is a rough guess)"), result.summary
    assert result.data["calibrated"] is False
    measured = await core.go_to(
        VerbContext(transport=MockTransport(), detector=_BallUnderfoot()), GoToParams()
    )
    assert measured.summary == "reached the ball: ~0.10 m away, bearing +0°"
    assert set(measured.data) == {"distance_m", "bearing_deg", "ticks"}


def _limited(limits: dict[str, float], *, head: bool = False) -> RobotManifest:
    """A body that walks and sees, with a head to look with when `head`, its manifest validated
    and then given `limits` as they stand, past the validator, as code holding a manifest can:
    the clamp has to be safe alone."""
    manifest = RobotManifest(
        id="bot-01",
        vendor="acme",
        model="bot",
        embodiment="biped",
        mobility="legged",
        intents=["twist", "gaze"] if head else ["twist"],
        sensors=["camera"],
        verbs=[VerbSpec(name="move", core=True), VerbSpec(name="search_scan", core=True)],
    )
    manifest.limits.update(limits)
    return manifest


LIMIT_CASES = [
    # (id, limits, what to run, the field read back)
    (
        "max_vx -0.1, stand still",
        {"max_vx": -0.1, "max_vy": 0.2, "max_wz": 1.0},
        ("move", {"vx": 0.0, "duration_s": 0.2}),
        "vx",
    ),
    (
        "max_vx -0.2, turn in place",
        {"max_vx": -0.2, "max_vy": 0.2, "max_wz": 1.5},
        ("move", {"vx": 0.0, "wz": 0.5, "duration_s": 0.2}),
        "vx",
    ),
    ("max_vx NaN", {"max_vx": float("nan")}, ("move", {"vx": 0.1, "duration_s": 0.2}), "vx"),
    ("max_wz -1.0, turn left", {"max_wz": -1.0}, ("turn", {"radians": math.radians(45)}), "wz"),
    (
        "max_wz inf, turn left",
        {"max_wz": float("inf")},
        ("turn", {"radians": math.radians(45)}),
        "wz",
    ),
]


@pytest.mark.parametrize(
    ("limits", "run", "field"), [c[1:] for c in LIMIT_CASES], ids=[c[0] for c in LIMIT_CASES]
)
async def test_a_manifest_s_speed_limit_never_turns_a_request_into_another_motion(
    limits: dict[str, float], run: tuple[str, dict[str, Any]], field: str
) -> None:
    """`_clamped`, `speed_limits` and `_turn` read a manifest's limits as they stand: a negative
    `max_vx` clamped every request to its magnitude, zero included, NaN went out as the twist,
    and a negative `max_wz` turned `_turn` the other way, for a negative time. The manifest
    refuses such limits now (tests/test_manifest.py), and the clamp reads one that got past it
    as no limit at all, the schema's bound, so nothing a manifest says changes the sign of a
    request or sends a twist that is not finite."""
    body = MockTransport()
    ctx = VerbContext(transport=body, manifest=_limited(limits))
    verb, args = run
    summary = ""
    if verb == "move":
        p = core.MoveParams(**args)
        summary = (await core.move(ctx, p)).summary
        asked = getattr(p, field)
    else:
        await core._turn(ctx, args["radians"])
        asked = args["radians"]
    sent = [i.params[field] for i in body.intents_of("move")]
    ok = all(
        math.isfinite(v)
        and (v == 0.0 if asked == 0.0 else math.copysign(1, v) == math.copysign(1, asked))
        for v in sent
    )
    assert sent and ok, (
        f"limits {limits}; asked {field}={asked:g}, the body was sent "
        f"{field}={sorted(set(map(str, sent)))}; the verb said {summary!r}"
    )
    assert all(math.isfinite(v) and v >= 0 for v in core.speed_limits(ctx.manifest))


def test_a_limit_held_as_any_kind_of_real_number_still_holds_the_clamp() -> None:
    """A limit set after validation was read only as an `int` or a `float`, so one held as a
    numpy number, which is what a limit computed from an array is, or as a Decimal, read as no
    limit at all: a base that cannot strafe (`max_vy` 0) was allowed the schema's 0.2 m/s
    sideways and a 0.1 m/s cap became 0.3. Every real number is read now, and NaN, infinity and a
    negative are still not."""
    for vx, vy, wz in [
        (np.float32(0.1), np.int64(0), Decimal("0.5")),
        (Fraction(1, 10), 0, np.float64(0.5)),
    ]:
        manifest = _limited({})
        manifest.limits.update({"max_vx": vx, "max_vy": vy, "max_wz": wz})  # type: ignore[dict-item]
        got = core.speed_limits(manifest)
        assert got == pytest.approx((0.1, 0.0, 0.5)), (vx, vy, wz, got)
        assert all(type(v) is float for v in got)
    manifest = _limited({"max_vx": np.float64("nan"), "max_vy": Decimal("-0.1")})  # type: ignore[dict-item]
    assert core.speed_limits(manifest)[:2] == (core.MAX_VX, core.MAX_VY)


async def test_a_body_s_head_fade_that_is_not_finite_is_not_waited_for() -> None:
    """`_head_fade_s` took any positive number, infinity included, and `_look_ahead` sleeps it
    before `go_to` looks: on a real transport a sleep with no end until the executor's timeout,
    on the mock a clock at infinity, which ended `go_to` as timed out without one frame looked
    at. A fade that is not finite is not waited for."""

    class _InfiniteFade(MockTransport):
        head_fade_s = float("inf")

    ctx = VerbContext(transport=_InfiniteFade(), detector=_BallUnderfoot())
    assert core._head_fade_s(ctx) == 0.0
    result = await core.go_to(ctx, GoToParams())
    assert result.ok, result.summary


def _looks_only() -> RobotManifest:
    """A body that cannot move and looks around by sweeping its head."""
    return RobotManifest(
        id="bot-01",
        vendor="acme",
        model="bot",
        embodiment="humanoid",
        mobility="none",
        intents=["gaze"],
        sensors=["camera"],
        verbs=[VerbSpec(name="search_scan", core=True)],
    )


async def test_a_head_sweep_the_body_refused_does_not_read_as_a_sweep_that_found_nothing() -> None:
    """`_gaze_sweep` sent each look and threw its answer away, so a body that refused every look
    was reported as having looked across the whole sweep and not found the target, which a pilot
    reads as the target not being there. The sweep ends at the first refused look and says so."""
    manifest = _looks_only()
    assert core.scan_mode(manifest) == "gaze"
    body = MockTransport(refuse_kinds={"look"})
    ctx = VerbContext(transport=body, detector=ColorBlobDetector(), manifest=manifest)
    result = await core.search_scan(ctx, SearchScanParams(target="ball"))
    assert not result.ok
    assert result.summary.startswith(
        "search_scan: a look was refused, so the sweep stopped after 0 of "
    ), result.summary
    assert result.summary.endswith(" looks: mock refuses look"), result.summary
    assert result.data == {"look_refused": "mock refuses look", "steps": 0}
    assert len(body.intents_of("look")) == 1


@pytest.mark.parametrize("verb", ["go_to", "search_scan"])
async def test_a_refused_look_that_would_have_centred_the_head_ends_the_verb_before_it_moves(
    verb: str,
) -> None:
    """`go_to` and a turning `search_scan` centre the head first (`_look_ahead`) and threw that
    look's answer away too, so on a body with a head that refused it they steered on bearings
    taken through a head pointing wherever it was. On such a body the verb ends there, saying
    why, before anything moves. A body with no head to centre refuses a look as a matter of
    course (a wheeled base over rosbridge says it cannot look), and goes on as it always has."""
    body = MockTransport(refuse_kinds={"look"})
    ctx = VerbContext(transport=body, detector=_BallUnderfoot(), manifest=_limited({}, head=True))
    if verb == "go_to":
        result = await core.go_to(ctx, GoToParams())
    else:
        result = await core.search_scan(ctx, SearchScanParams())
    assert not result.ok
    assert result.summary == (
        f"{verb}: the look that centres the head was refused, so nothing moved: mock refuses look"
    ), result.summary
    assert result.data == {"look_refused": "mock refuses look"}
    assert body.intents_of("move") == []
    headless = VerbContext(
        transport=MockTransport(refuse_kinds={"look"}),
        detector=_BallUnderfoot(),
        manifest=_limited({}),
    )
    assert (await core.go_to(headless, GoToParams())).ok
