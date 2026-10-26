"""Composite verbs close their own loops on the camera — the steering tier, tested alone."""

from __future__ import annotations

import asyncio
import math
from collections.abc import Callable

import pytest
from PIL import Image

from quackd.duckfile.parser import parse_duck_text
from quackd.perception.base import Detection
from quackd.perception.color_blob import ColorBlobDetector
from quackd.safety import Executor
from quackd.transport.base import Ack, Intent
from quackd.transport.mock import MockTransport
from quackd.transport.sim2d import Sim2DTransport
from quackd.verbs.core import (
    LOST_TICKS,
    LOST_WZ,
    MAX_SCAN_STEPS,
    STEER_TURN_S,
    TICK_S,
    SearchScanParams,
    scan_steps,
)
from quackd.verbs.registry import STOP_ENDED, STOPPED, VerbRegistry, VerbResult

DUCK = parse_duck_text(
    """---
duck: 0
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


def _executor(registry: VerbRegistry, seed: int) -> tuple[Executor, Sim2DTransport]:
    transport = Sim2DTransport(seed=seed)
    ex = Executor(registry, transport, contract=DUCK.frontmatter, detector=ColorBlobDetector())
    return ex, transport


async def test_search_scan_finds_the_ball(registry: VerbRegistry) -> None:
    ex, transport = _executor(registry, seed=4)
    result = await ex.run_verb("search_scan", {"target": "ball"})
    assert result.ok, result.summary
    _dist, bearing = transport.world.relative(transport.world.ball.x, transport.world.ball.y)
    assert abs(bearing) < 0.9  # the ball is now in the 90° field of view


async def test_walk_to_reaches_the_ball(registry: VerbRegistry) -> None:
    ex, transport = _executor(registry, seed=2)
    assert (await ex.run_verb("search_scan", {"target": "ball"})).ok
    result = await ex.run_verb("walk_to", {"target": "ball", "stop_distance": 0.25})
    assert result.ok, result.summary
    true_dist, true_bearing = transport.world.relative(
        transport.world.ball.x, transport.world.ball.y
    )
    assert true_dist <= 0.36
    assert abs(true_bearing) < 0.6


async def test_approach_and_kick_moves_the_ball(registry: VerbRegistry) -> None:
    ex, transport = _executor(registry, seed=6)
    assert (await ex.run_verb("search_scan", {"target": "ball"})).ok
    result = await ex.run_verb(
        "approach_and", {"target": "ball", "stop_distance": 0.22, "then": "kick"}
    )
    assert result.ok, result.summary
    # the kick connected; how far the ball travels also depends on walls (seed 6 bounces)
    assert transport.world.kicks_connected == 1
    assert transport.world.last_kick_ball_moved_m is not None
    assert transport.world.last_kick_ball_moved_m > 0.1


async def test_walk_to_without_detector_explains(registry: VerbRegistry) -> None:
    transport = Sim2DTransport(seed=0)
    ex = Executor(registry, transport, contract=DUCK.frontmatter, detector=None)
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
    ex = Executor(registry, body, contract=DUCK.frontmatter, detector=_BallUnderfoot())
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
    ex = Executor(registry, body, contract=DUCK.frontmatter, detector=_BallUnderfoot())
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
    ex = Executor(registry, body, contract=DUCK.frontmatter, detector=_Empty())
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
    ex = Executor(registry, body, contract=DUCK.frontmatter, detector=_Empty())
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
    ex = Executor(registry, body, contract=DUCK.frontmatter, detector=_SeesTheBall(NARROW_LENS_DEG))
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
    ex = Executor(registry, body, contract=DUCK.frontmatter, detector=_SeesTheBall(NARROW_LENS_DEG))
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
    ex = Executor(registry, body, contract=DUCK.frontmatter, detector=_SeesTheBall(NARROW_LENS_DEG))
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
    ex = Executor(registry, body, contract=DUCK.frontmatter, detector=_SeesTheBall(NARROW_LENS_DEG))
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
    ex = Executor(registry, body, contract=DUCK.frontmatter, detector=_BallAway())
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
    ex = Executor(registry, body, contract=DUCK.frontmatter, detector=_Empty())
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
    ex = Executor(registry, body, contract=DUCK.frontmatter, detector=detector)  # type: ignore[arg-type]
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
    ex = Executor(registry, body, contract=DUCK.frontmatter, detector=_Empty())
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
    ex = Executor(registry, body, contract=DUCK.frontmatter, detector=_Empty())
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
    ex = Executor(registry, stopped, contract=DUCK.frontmatter, detector=_Empty())
    result = await ex.run_verb("search_scan", {"target": "ball", "step_deg": 90, "max_steps": 4})
    assert result.summary == (
        "search_scan: a twist was refused, so the scan stopped, and the robot's own state did not "
        f"confirm the turn sent to it, so it may not have turned at all: {REFUSED}"
    ), result.summary
    assert result.data["steps"] == 0
    looked = _Confirming(lambda b: False, takes=0)
    ex = Executor(registry, looked, contract=DUCK.frontmatter, detector=_Empty())
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
    ex = Executor(registry, body, contract=DUCK.frontmatter, detector=_ThirdLook())
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
    ex = Executor(registry, body, contract=DUCK.frontmatter, detector=_BallAway())
    async with asyncio.timeout(30):  # only ends a test whose wait never ends
        result = await ex.run_verb("walk_to", {"target": "ball"})
    assert not result.ok
    assert result.summary == f"go_to: a twist was refused, so the body stopped: {REFUSED}"
    assert body.refused == 1 and body.intents[-1].kind == "stop"
