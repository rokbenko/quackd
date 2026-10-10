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
from quackd.verbs.registry import VerbRegistry

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
    later, and `get_frame` hands out the newest that has come. The body turns at the rate of the
    last twist it was sent until a stop, or until twists stop coming for a deadman window, and
    each frame carries the time it was taken and the heading it was taken at, in degrees."""

    def __init__(self, *, period: float, fetch: float, head_fade_s: float = 0.0) -> None:
        super().__init__()
        self.head_fade_s = head_fade_s
        self.fetch = fetch
        self.every = max(period, fetch)
        self.yaw_deg = 0.0
        self.wz = 0.0
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
                self.wz = 0.0
            self.yaw_deg += math.degrees(self.wz * seconds / slices)
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
            self.wz, self.twisted_at = float(intent.params.get("wz", 0.0)), self._t
        return await super().send_intent(intent)

    async def stop(self) -> None:
        self.wz = 0.0
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
