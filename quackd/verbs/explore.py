"""Explore: a robot that walks to what its map has not seen, and stops so the map can look.

A map that looks only at stops (the Microduck's `mapd`, and the simulators' stand-in for it) sees a
room a stop at a time, and `explore` gives it the stops. It finds the floor the map has seen beside
floor it has not (`quackd.space.frontier`), walks to the nearest stretch of it by the way the map
knows (`quackd.verbs.navigate`, called as a function, as `search_scan` turns by `_turn` rather than
calling `move`), stops, and looks each way a look could see something it has not, with the head on
a map that takes its keyframes from the head and with the body on one that does not. A stretch it
looked at without seeing past is given up for that map: under a sofa, glass, a gap seen at a
grazing angle. A spot it could not get to is tried once more, after the rest, and the floor within
a body's width of it is then left until the next call: a person in a doorway, a pane the map sees
through and the body walks into, a map not read once. It marks the reserved place `start` where it
begins, so `go_to_place start` is the way back.

`find` is the same walk with a camera frame at every look and every view at every stop (`glance`).
What the detector takes for the target is faced and seen again in the middle half of the view
before it is believed (`confirm`), and the place named after it is then marked where the robot
stands, facing it.

What it walks, turns and waits by is a sensor's, a clamp's or upstream's word (`ExploreSpec`,
`SpatialSource`):

- a look is the map's own view, its width and its reach (`SpatialSource.view`), the views of a
  stop step round a circle by the rule `search_scan` turns a camera by (`views_round`), the first
  stop takes them all, as every stop of a search does, and every later stop of an exploration only
  those that would see as many cells nobody has seen as the body is wide (`views_worth_taking`);
- a look holds until the map has taken its keyframe there, for the map's own settle at most
  (`settled_fix`), and one the head turned for first waits for a keyframe more than the map had,
  for the head's settle and the map's at most;
- a stretch is walked to only when the opening it looks into is as wide as the body, and the walk
  stops where the map follows it, which is never further than a look reaches;
- a lost map is found again as the map itself says it is: stand for a keyframe, then turn a view
  at a time with a keyframe each, one whole turn at most.

It ends when no stretch is left that it can reach (`done`) or its minutes are up (`minutes`), and
fails on a stop (`stopped`), the run's budget (`budget`), a map it cannot find itself in again
(`lost`), a twist the body refused or a body it may move no more (`held`, `fallen`, `sitting`,
`blind`), each in words of its own. A search ends well only by finding (`found`), and fails
besides on a camera with no frame or a detector that cannot read one (`camera`). The run's own
rules, the battery's among them, are read at every stop. Whatever ends it, the body is stopped.
"""

from __future__ import annotations

import dataclasses
import math
import weakref
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from quackd.perception.base import Detection, labels_of
from quackd.safety import SafetyStop
from quackd.space.frontier import (
    Cluster,
    GivenUp,
    WorldCell,
    body_cells,
    frontier_groups,
    nearest_first,
    nearest_view,
    still_unseen_beside,
    views_round,
    views_worth_taking,
    world_cell,
)
from quackd.space.grid import FREE, OccupancyGrid
from quackd.space.places import PlaceBook
from quackd.space.pose import Fix, Pose2D, where_record, wrap_angle
from quackd.space.source import SpatialError, SpatialSource
from quackd.transport.base import DuckState, Intent, TransportError, frame_head_yaw_of
from quackd.verbs.core import (
    TICK_S,
    _clamped,
    _Interrupted,
    _lens_deg,
    _Refused,
    _see,
    _twist,
)
from quackd.verbs.navigate import Gait, Walked, navigate
from quackd.verbs.places import NO_POSE, settled_fix
from quackd.verbs.registry import STOP_ENDED, STOPPED, Verb, VerbContext, VerbResult
from quackd.verbs.watch import body_exit

EXPLORE_MAX_MIN = 8.0
"""quackd's own cap on one exploration, the one MOSS's own `explore` keeps, which a test holds
equal: with its stop after it, it fits inside the 600 s a manifest may give one verb."""

START = "start"
"""The reserved place an exploration marks where it begins (`quackd.space.places`)."""

MINUTES_UP = "its minutes ran out"
"""What an exploration's own clock says when it ends a walk or a look, through the context the
walk is given (`VerbContext.interrupted`), as a stop from beside it would."""

Exit = Literal[
    "done",
    "minutes",
    "found",
    "stopped",
    "budget",
    "lost",
    "held",
    "fallen",
    "sitting",
    "blind",
    "camera",
]
ENDS_WELL: frozenset[Exit] = frozenset({"done", "minutes"})
"""How an exploration may end well. A search ends well only by finding (`FOUND`)."""
FOUND: frozenset[Exit] = frozenset({"found"})
_BODY_EXITS: dict[str, Exit] = {"held": "held", "fallen": "fallen", "blind": "blind"}

EXPLORE_MARGIN_S = 60.0
"""What the executor's clock keeps beyond an exploration's own minutes, which its own clock ends
within a tick or a request of: the requests a slow link may still be waiting on then, the stop
after them and the map read its words count from, so its own stop runs and reports rather than
the executor's. A margin, as MOSS's own is (`VERB_MARGIN_S`), not a measurement."""

EXPLORE_TIMEOUT_S = EXPLORE_MAX_MIN * 60.0 + EXPLORE_MARGIN_S
"""The executor's clock for one exploration, inside the 600 s a manifest may give one verb."""

TRIES = 2
"""How many walks at one stretch end without getting there before it is left for the rest of the
call: the second after the map has looked again, which a passing hold-up has cleared by then and
something the body walks into every time has not."""


_GIVEN_UP: weakref.WeakKeyDictionary[SpatialSource, GivenUp] = weakref.WeakKeyDictionary()
"""What each connection's explorations gave up, kept by the reading the connection carries
(`spatial_of`): a stretch given up in one call is not walked to again in the next one, in the
same map, for as long as the robot stays connected."""


def given_up_of(source: SpatialSource) -> GivenUp:
    """The connection's own record of what it gave up, begun empty."""
    try:
        return _GIVEN_UP.setdefault(source, GivenUp())
    except TypeError:
        return GivenUp()  # a reading that cannot be kept by: what it gives up lasts the call


@dataclass(frozen=True)
class ExploreSpec:
    """One body's numbers for exploring, beside what its map says of itself (`SpatialSource`)."""

    head_yaw_deg: float
    """How far either way a look turns the head, on a map that takes its keyframes from the head
    (`SpatialSource.keyframes_follow_head`). Past that the body turns."""
    head_settle_s: float = 0.0
    """How long the head takes to get where a look sends it. A head that says where it got to
    rather than where it was sent has not moved when it is first read after the look, so a look
    waits this long, and the map's own settle, for the keyframe it turned the head for."""
    source: str = ""
    """Where the numbers come from, for the record."""


class ExploreParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    minutes: float = Field(
        default=1.0,
        gt=0,
        le=EXPLORE_MAX_MIN,
        description="How long to explore, in minutes, at most 8. It ends sooner once nothing is "
        "left that it can reach, and always with a stop.",
    )


class FindParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: str = Field(
        default="ball",
        min_length=1,
        description="What to look for, as the detector names it (e.g. ball, person).",
    )
    minutes: float = Field(
        default=1.0,
        gt=0,
        le=EXPLORE_MAX_MIN,
        description="How long to look, in minutes, at most 8. It ends sooner once it has found "
        "it, or nothing is left that it can reach, and always with a stop.",
    )


class _Ended(Exception):
    """An exploration that ends here, for the reason it carries."""

    def __init__(self, exit: Exit, said: str, **data: Any) -> None:
        super().__init__(said)
        self.exit, self.said, self.data = exit, said, data


@dataclass(frozen=True)
class _Sighting:
    """Something the detector took for what is sought: which way it was across the map's frame,
    from where the robot stood, and what the detector made of it."""

    heading: float
    at: tuple[float, float]
    detection: Detection

    def record(self) -> dict[str, Any]:
        return {
            "from": [round(self.at[0], 2), round(self.at[1], 2)],
            "heading": round(self.heading, 3),
            "confidence": round(self.detection.confidence, 2),
        }


@dataclass
class _Explore:
    ctx: VerbContext
    walk_ctx: VerbContext
    """The run's context, with the minutes in what ends a walk (`interrupted`)."""
    source: SpatialSource
    book: PlaceBook
    spec: ExploreSpec
    minutes: float
    t0: float
    deadline: float
    gait: Gait
    footprint: float
    given_up: GivenUp = field(default_factory=GivenUp)
    """What the connection gave up: stretches it reached, looked at and still could not see."""
    missed: GivenUp = field(default_factory=GivenUp)
    """What this call walked at `TRIES` times without getting there, left for the rest of it."""
    tries: dict[WorldCell, int] = field(default_factory=dict)
    """How many walks to within a body's width of each cell ended without getting there, in
    `missed`'s frame."""
    stops: list[tuple[float, float]] = field(default_factory=list)
    """Where it stood to look, in order."""
    looks: list[int] = field(default_factory=list)
    """The map's count of keyframes after each look: one more each time, or the look saw nothing."""
    walked_m: float = 0.0
    head: float = 0.0
    """Where the head was last sent, across the body: straight until a look turns it."""
    marked: bool = False
    """Whether this call marked `start` where it began."""
    unmarked: str | None = None
    """Why it could not, when it tried."""
    stop_sent: bool = False
    """Whether a `SafetyStop` leaving now comes with the body stopped already: the run's rule read
    at a stop stops it before it raises, and a walk stops it before it lets one go. A stop awaited
    after that would let the run's abort cancel the verb before its words are read."""
    seek: str | None = None
    """The detector's label a search looks for in a camera frame at every look, or None."""
    sightings: list[_Sighting] = field(default_factory=list)
    """What this stop's looks took for it, to be faced and seen again once they are done."""
    passed_by: int = 0
    """How many sightings it faced and did not see again."""

    # ── reading ─────────────────────────────────────────────────────────────────────

    def now(self) -> float:
        return self.ctx.transport.now()

    def told(self, why: str) -> _Ended:
        """The end a stop, the run's budget or its own minutes made."""
        if why == MINUTES_UP:
            return _Ended("minutes", MINUTES_UP)
        if why == STOPPED:
            return _Ended("stopped", why, ended_by=STOP_ENDED)
        return _Ended("budget", why)

    def check_told(self) -> None:
        if (why := self.walk_ctx.interrupted()) is not None:
            raise self.told(why)

    def check_body(self, state: DuckState) -> None:
        """Before every command: a body somebody holds, one that fell or sits, and one nothing
        says is upright are not walked (`watch.body_exit`)."""
        exited = body_exit(state)
        if exited is not None:
            kind, why = exited
            raise _Ended(_BODY_EXITS.get(kind, "held"), why)
        if state.posture == "sitting":
            raise _Ended("sitting", "the robot is sitting: stand it up first")

    async def frame(self) -> str:
        """The frame the robot is in now, as its places are kept in (`PlaceBook.frame`), which a
        map names once its stops are read: waited for, for the map's own settle at most."""
        frame = self.book.frame()
        deadline = self.now() + (self.source.settle_timeout_s or 0.0)
        while frame is None and self.now() < deadline:
            self.check_told()
            await self.ctx.transport.sleep(TICK_S)
            frame = self.book.frame()
        if frame is None:
            raise _Ended("lost", "its map is not known yet, so nothing it finds can be kept in it")
        return frame.id

    async def grid(self) -> OccupancyGrid:
        try:
            grid = await self.source.grid()
        except (TransportError, SpatialError) as e:
            raise _Ended("lost", f"its map could not be read: {e}") from None
        if grid is None:
            raise _Ended("lost", "its map sent no grid")
        return grid

    # ── standing ────────────────────────────────────────────────────────────────────

    async def settle(self) -> Fix:
        """Stand at a stop until the map has looked from it, then read the run's rules there."""
        fix = await settled_fix(self.walk_ctx, self.source, tick=TICK_S)
        self.check_told()
        state = await self.ctx.transport.get_state()
        self.check_body(state)
        self.stop_sent = True
        await self.ctx.abort_when(state)
        self.stop_sent = False
        return fix

    async def turn_blind(self, angle: float) -> None:
        """Turn by `angle` in place with no reading to steer by, at the gait's turn for as long as
        that covers it, a stop or a refused twist ending it as it ends any walk."""
        seconds = abs(angle) / self.gait.w_exp
        end = self.now() + seconds
        try:
            while self.now() < end:
                await _twist(
                    self.walk_ctx,
                    *_clamped(self.walk_ctx, 0.0, 0.0, math.copysign(self.gait.w, angle)),
                )
                await self.ctx.transport.sleep(TICK_S)
        except _Interrupted as told:
            raise self.told(told.why) from None
        except _Refused as refused:
            raise _Ended("held", f"a twist was refused: {refused}") from None
        finally:
            await self.ctx.transport.stop()

    async def found(self, fix: Fix) -> Fix:
        """The map's reading once it knows where the robot is again: stand for a keyframe, then
        turn a view at a time with a keyframe each, a whole turn at most."""
        if fix.localized:
            return fix
        why = fix.lost or "no reading"
        fix = await self.settle()
        assert self.source.view is not None
        step_deg, count = views_round(math.degrees(self.source.view[0]))
        for _ in range(count):
            if fix.localized:
                return fix
            await self.turn_blind(math.radians(step_deg))
            fix = await self.settle()
        if fix.localized:
            return fix
        raise _Ended(
            "lost",
            f"it does not know where it is ({why}), and a whole turn of looks did not find it",
        )

    # ── looking ─────────────────────────────────────────────────────────────────────

    async def look(self, *, every_way: bool) -> Fix:
        """Look from here each way worth it, the head first, the body where the head does not
        reach, and the head straight again after. Returns the reading after the last look.

        A view may land off its heading by no more than the overlap its neighbours leave, half of
        what a view is wider than the step between them, or the views stop meeting: so a view the
        stop's own keyframe already took is not taken again, and a body turn that stops further
        off than that, as a turn read four times a second does, is finished with the head."""
        fix = self.source.fix()
        pose = self.pose(fix)
        assert self.source.view is not None
        fov, reach = self.source.view
        width = math.degrees(fov)
        if self.seek is not None and (lens := _lens_deg(self.ctx)) is not None:
            # a camera narrower than the map's view needs its views at least as close
            width = min(width, lens)
        step_deg, count = views_round(width)
        overlap = (math.radians(width) - math.radians(step_deg)) / 2
        if every_way:
            headings = [wrap_angle(pose.yaw + math.radians(step_deg) * k) for k in range(count)]
        else:
            grid = await self.grid()
            headings = views_worth_taking(
                grid, pose.x, pose.y, pose.yaw, fov_rad=fov, range_m=reach, radius_m=self.footprint
            )
        self.stops.append((round(pose.x, 3), round(pose.y, 3)))
        head = self.source.keyframes_follow_head
        reach_rad = math.radians(self.spec.head_yaw_deg)
        # the stop's own keyframe looked where the head points, once the map has taken it
        looked = [] if fix.collecting else [wrap_angle(pose.yaw + self.head)]
        if looked:
            await self.glance()  # and a search looks at that view as at every other

        def covered(heading: float) -> bool:
            return any(abs(wrap_angle(heading - view)) <= overlap for view in looked)

        left = [h for h in headings if not covered(h)]
        while left:
            pose = self.pose(await self.found(self.source.fix()))
            by_head = [h for h in left if head and abs(wrap_angle(h - pose.yaw)) <= reach_rad]
            for heading in by_head:
                fix = await self.look_with_head(wrap_angle(heading - pose.yaw))
                looked.append(heading)
            left = [h for h in left if h not in by_head]
            if left:
                heading = nearest_view(left, pose.yaw)
                fix, facing = await self.face(heading)
                await self.glance()
                looked.append(facing)
                miss = wrap_angle(heading - facing)
                if not covered(heading) and head and abs(miss) <= reach_rad:
                    fix = await self.look_with_head(miss)
                    looked.append(heading)
                left = [h for h in left if h != heading and not covered(h)]
        if head and self.head != 0.0:
            await self.send_head(0.0)
        return fix

    async def send_head(self, yaw: float) -> None:
        self.check_told()
        state = await self.ctx.transport.get_state()
        self.check_body(state)
        await self.ctx.transport.send_intent(Intent.look(math.cos(yaw), math.sin(yaw), 0.0))
        self.head = yaw

    async def head_to(self, yaw: float) -> Fix:
        """Send the head to `yaw` across the body and stand until the map has looked from there.
        A head that says where it got to rather than where it was sent has not moved when it is
        first read, and the map reads a stop it has looked from as looked from, so the map is
        waited on for a keyframe more than it had, for the head's settle and the map's at most,
        before the stop is settled as any other."""
        before = self.source.fix().keyframes
        await self.send_head(yaw)
        if before is not None:
            limit = self.spec.head_settle_s + (self.source.settle_timeout_s or 0.0)
            deadline = self.now() + limit
            while (self.source.fix().keyframes or 0) <= before and self.now() < deadline:
                self.check_told()
                await self.ctx.transport.sleep(TICK_S)
        return await self.settle()

    async def look_with_head(self, yaw: float) -> Fix:
        fix = await self.head_to(yaw)
        self.looks.append(fix.keyframes or 0)
        await self.glance()
        return fix

    # ── seeking ─────────────────────────────────────────────────────────────────────

    async def glance(self) -> None:
        """Where it seeks something, a camera frame of the view it looks at now, the head settled:
        what the detector takes for it there is a sighting, faced and seen again once the stop's
        looks are done (`confirm`). A view it cannot say the way of is no sighting."""
        if self.seek is None:
            return
        fix = self.source.fix()
        if not fix.localized or fix.pose is None:
            return
        head, hits = await self.frame_hits(f"find {self.seek}")
        camera = fix.pose.yaw + head
        lens = _lens_deg(self.ctx)
        same = math.radians(lens) / 4 if lens is not None else 0.0
        for hit in hits:
            bearing = math.radians(hit.bearing_deg) if hit.bearing_deg is not None else 0.0
            seen = _Sighting(wrap_angle(camera + bearing), (fix.pose.x, fix.pose.y), hit)
            # one thing in two frames that overlap is one sighting, the surer kept
            twin = next(
                (s for s in self.sightings if abs(wrap_angle(s.heading - seen.heading)) <= same),
                None,
            )
            if twin is None:
                self.sightings.append(seen)
            elif hit.confidence > twin.detection.confidence:
                self.sightings[self.sightings.index(twin)] = seen

    async def frame_hits(self, caption: str) -> tuple[float, list[Detection]]:
        """What the detector finds of the thing sought in a frame taken from now on, and the yaw
        of the head the frame was taken through, or the end a camera with no frame, or a detector
        that could not read it, makes of the search. The yaw is the one the body says it reached,
        on the frame (`frame_head_yaw_of`) or in its state, since a head can lag the look it was
        sent, and the one last sent only from a body that says neither."""
        assert self.seek is not None
        self.check_told()
        img, hits = await _see(self.ctx, self.seek, caption, since=self.now())
        self.check_told()  # a stop while the frame was fetched and read ends it there
        if img is None:
            why = getattr(self.ctx.transport, "camera_error", None)
            raise _Ended("camera", str(why) if why else "its camera gave no frame")
        why = getattr(self.ctx.detector, "error", None)
        if isinstance(why, str) and why:
            name = getattr(self.ctx.detector, "name", "the detector")
            raise _Ended("camera", f"{name} could not read a frame: {why}")
        head = frame_head_yaw_of(img)
        if head is None:
            state = await self.ctx.transport.get_state()
            said = state.extras.get("head_yaw_deg") if isinstance(state.extras, dict) else None
            if isinstance(said, int | float) and not isinstance(said, bool):
                head = float(said) if math.isfinite(said) else None
        return (math.radians(head) if head is not None else self.head), hits

    async def confirm(self) -> None:
        """Face what this stop's looks took for the thing sought, the surest first, and look
        again: one seen again in the middle half of the view is found, and ends the search there;
        one not seen again is passed by, as a frame the detector misread is. What a search that
        ends here had not faced yet stays in `sightings`, to be said."""
        if self.seek is None or not self.sightings:
            return
        self.sightings.sort(key=lambda s: -s.detection.confidence)
        lens = _lens_deg(self.ctx)
        reach = math.radians(self.spec.head_yaw_deg)
        while self.sightings:
            sighting = self.sightings[0]
            fix, facing = await self.face(sighting.heading)
            # the turn stops within its own tolerance of the way, which can be more than the
            # middle of a narrow lens: the head finishes it, as it finishes a look's
            miss = wrap_angle(sighting.heading - facing)
            if abs(miss) <= reach:
                await self.send_head(miss)
                await self.hold(self.spec.head_settle_s)
            head, hits = await self.frame_hits(f"find {self.seek}, facing it")
            ahead = [
                hit
                for hit in hits
                if lens is None or hit.bearing_deg is None or abs(hit.bearing_deg) <= lens / 4
            ]
            if ahead:
                raise self.found_it(fix, ahead[0], toward=head)
            self.sightings.pop(0)
            self.passed_by += 1
        if self.head != 0.0:
            await self.send_head(0.0)  # straight again, as after a stop's looks

    async def hold(self, seconds: float) -> None:
        """Stand for `seconds`, a tick at a time, ended as every wait here is."""
        end = self.now() + seconds
        while self.now() < end:
            self.check_told()
            await self.ctx.transport.sleep(TICK_S)

    def found_it(self, fix: Fix, hit: Detection, *, toward: float) -> _Ended:
        """The search's end: the place named after what it sought, marked where the robot stands
        and facing it, the head's yaw `toward` it was seen through and the detection's bearing
        added to the body's, floor it stood on, where the thing's own spot is not."""
        assert self.seek is not None
        bearing = math.radians(hit.bearing_deg) if hit.bearing_deg is not None else 0.0
        far = f", about {hit.est_distance_m:.1f} m ahead" if hit.est_distance_m is not None else ""
        data: dict[str, Any] = {"where": where_record(fix), "detection": hit.model_dump()}
        try:
            place, old = self.book.mark(
                self.seek,
                f"where it saw the {self.seek}{far}, facing it",
                turned=wrap_angle(toward + bearing),
            )
            data["place"] = place.name
            if old is not None:
                data["was_at"] = [round(old.x, 2), round(old.y, 2)]
        except ValueError as e:
            data["place_not_marked"] = str(e)
        return _Ended("found", f"found the {self.seek}: {hit.summary()}", **data)

    async def face(self, heading: float) -> tuple[Fix, float]:
        """Turn the body in place to `heading` and look there: a walk that goes nowhere and ends
        facing the way, which turns, stops and looks as every walk does, the head straight. Returns
        the reading and the heading it came to, within the walk's own tolerance of the one asked."""
        fix = self.source.fix()
        pose = self.pose(fix)
        walked = await self.walk((pose.x, pose.y), heading, "the way it looks next")
        self.head = 0.0  # a walk turns the head to the way it walks
        if walked.outcome != "arrived":
            self.ended_by_walk(walked)
        # a turn its map lost it on is a map to find itself in again before it looks on
        fix = await self.found(self.source.fix())
        self.looks.append(fix.keyframes or 0)
        return fix, self.pose(fix).yaw

    # ── walking ─────────────────────────────────────────────────────────────────────

    async def walk(self, goal: tuple[float, float], yaw: float | None, label: str) -> Walked:
        self.check_told()
        state = await self.ctx.transport.get_state()
        self.check_body(state)
        frame = await self.frame()
        try:
            walked = await navigate(
                self.walk_ctx,
                self.source,
                goal,
                goal_yaw=yaw,
                frame_id=frame,
                timeout_s=max(0.0, self.deadline - self.now()),
                label=label,
                verb="explore",
            )
        except SafetyStop:
            # the walk lets one go only with the body stopped, by the rule's reader at one of its
            # stops or by the walk itself
            self.stop_sent = True
            raise
        self.walked_m += walked.metres
        return walked

    def ended_by_walk(self, walked: Walked) -> None:
        """Raise the end a walk's own end makes of the exploration, or return for one it walks on
        from: a stretch it could not get to, or a map it is lost in."""
        match walked.outcome:
            case "interrupted":
                raise self.told(str(walked.data.get("interrupted") or STOPPED))
            case "timeout":
                raise _Ended("minutes", MINUTES_UP)
            case "body":
                refused = walked.data.get("move_refused") or walked.said
                raise _Ended("held", f"a twist was refused: {refused}")
            case _:
                return

    def pose(self, fix: Fix) -> Pose2D:
        if not fix.localized or fix.pose is None:
            raise _Ended("lost", f"it does not know where it is ({fix.lost or 'no reading'})")
        return fix.pose

    # ── the round ───────────────────────────────────────────────────────────────────

    def missed_in(self, frame: str) -> set[WorldCell]:
        """What this call left in `frame`, emptied with its count of tries in a new one."""
        if frame != self.missed.frame:
            self.tries.clear()
        return self.missed.in_frame(frame)

    def missed_once(self, frame: str, grid: OccupancyGrid, target: Cluster) -> None:
        """A walk at `target` ended without getting there. A try counts against the cells of the
        stretch within a body's width of where it walked to, since another way may still reach
        the rest of it, and a cell tried `TRIES` times is left for the rest of the call."""
        left = self.missed_in(frame)
        for cell in target.cells:
            if math.dist(grid.center_of(cell), target.goal) > 2 * self.footprint:
                continue
            key = world_cell(grid, cell)
            self.tries[key] = self.tries.get(key, 0) + 1
            if self.tries[key] >= TRIES:
                left.add(key)

    def tried(self, grid: OccupancyGrid, target: Cluster) -> int:
        """How many walks at the cell `target` would be walked to ended without getting there."""
        return self.tries.get(world_cell(grid, grid.cell_of(*target.goal)), 0)

    async def explore(self) -> _Ended:
        # where a head another verb turned is, nothing here says: straight before the first look,
        # and looked from where the map takes its keyframes from the head
        if self.source.keyframes_follow_head:
            fix = await self.found(await self.head_to(0.0))
        else:
            await self.send_head(0.0)
            await self.hold(self.spec.head_settle_s)
            fix = await self.found(await self.settle())
        try:
            self.book.mark(START, reserved_ok=True)
            self.marked = True
        except ValueError as e:
            # it knows where it is by now, and a place it could not keep is no reason to stop
            self.unmarked = str(e)
        fix = await self.look(every_way=True)
        await self.confirm()
        while True:
            fix = await self.found(self.source.fix())
            frame = await self.frame()
            pose = self.pose(fix)
            grid = await self.grid()
            gone = self.given_up.in_frame(frame) | self.missed_in(frame)
            groups = frontier_groups(grid, gone, min_cells=body_cells(grid.res_m, self.footprint))
            ranked = nearest_first(
                grid, (pose.x, pose.y), groups, radius_m=self.footprint + self.gait.track_m
            )
            if not ranked:
                if still_unseen_beside(grid, self.missed_in(frame)):
                    return _Ended(
                        "done",
                        "no gap in its map is left that it can reach, but for floor it could not "
                        f"get to in {TRIES} tries, which the next call tries again",
                    )
                return _Ended("done", "no gap in its map is left that it can reach")
            # the nearest stretch not tried yet, and one tried once only when none is left: a
            # hold-up there has the others' walks to clear
            target = min(ranked, key=lambda k: self.tried(grid, k))
            cells = {world_cell(grid, cell) for cell in target.cells}
            walked = await self.walk(target.goal, None, "the edge of what its map has seen")
            if walked.outcome == "refused" and "way_m" in walked.data:
                # refused before a step for want of time: every other stretch is as far, or was
                # tried already
                raise _Ended(
                    "minutes",
                    f"the next floor its map has not seen is {walked.data['way_m']:.1f} m away, "
                    f"about {walked.data['needs_s']:.0f} s of walking, and its minutes leave "
                    f"{walked.data['given_s']:g} s",
                )
            self.ended_by_walk(walked)
            if walked.outcome == "frame":
                continue  # a map of its own now: nothing is given up or left in it yet
            if walked.outcome != "arrived":
                # lost on the way, stalled, blocked or refused by its map: tried again once the
                # map has looked from where the walk ended, and the rest has been tried, then
                # left for the rest of the call. Only floor it reached and looked at is given up
                # for good
                self.missed_once(frame, grid, target)
                await self.found(await self.settle())
                continue
            # a search looks all the way round at every stop, since what it seeks may stand in
            # floor the map has seen from elsewhere, where no look of the map's would turn to
            await self.look(every_way=self.seek is not None)
            await self.confirm()
            after = await self.grid()
            self.given_up.in_frame(frame).update(still_unseen_beside(after, cells))

    async def result(self) -> tuple[_Ended, dict[str, Any]]:
        try:
            ended = await self.explore()
        except _Ended as end:
            ended = end
        seen, missed = 0.0, len(self.missed.cells)
        try:
            grid = await self.source.grid()
            if grid is not None:
                seen = float((grid.cells == FREE).sum()) * grid.res_m**2
                # what it could not get to and the map has not seen from elsewhere since
                missed = len(still_unseen_beside(grid, self.missed.cells))
        except (TransportError, SpatialError):
            pass
        data: dict[str, Any] = {
            "exit": ended.exit,
            "seconds": round(self.now() - self.t0, 1),
            "stops": [list(stop) for stop in self.stops],
            "looks": len(self.looks),
            "walked_m": round(self.walked_m, 2),
            "seen_m2": round(seen, 2),
            "given_up": len(self.given_up.cells),
            "missed": missed,
            "marked_start": self.marked,
        }
        if self.seek is not None:
            data.update(target=self.seek, passed_by=self.passed_by)
            if ended.exit != "found" and self.sightings:
                # taken for it and not faced yet when the search ended: not "not seen"
                data["unchecked"] = [sighting.record() for sighting in self.sightings]
        data.update(ended.data)
        return ended, data


def _how(data: dict[str, Any]) -> str:
    """What a call did, in the words its result ends with."""
    stops, looks = len(data["stops"]), data["looks"]
    return (
        f"{stops} stop{'s' if stops != 1 else ''}, "
        f"{looks} look{'s' if looks != 1 else ''}, "
        f"walked {data['walked_m']:.1f} m, "
        f"{data['seen_m2']:.1f} m² of floor seen"
    )


def _back(run: _Explore) -> str:
    """Where it began, as the words of a call that ends well say it."""
    if run.marked:
        return f"; '{START}' is where it began"
    if run.unmarked is not None:
        return f"; '{START}' was not marked where it began: {run.unmarked}"
    return ""  # its minutes, or a stop, came before it knew where it began


def _ran_out(ended: _Ended, minutes: float) -> str:
    if ended.said == MINUTES_UP:
        return f"its {minutes:g} minute{'s' if minutes != 1 else ''} ran out"
    return ended.said


def _words(ended: _Ended, data: dict[str, Any], minutes: float, run: _Explore) -> str:
    how, back = _how(data), _back(run)
    if ended.exit == "done":
        return f"explore: done, {ended.said}: {how}{back}"
    if ended.exit == "minutes":
        return f"explore: {_ran_out(ended, minutes)}: {how}{back}"
    return f"explore: {ended.said}, so the robot stopped: {how}"


def _find_words(ended: _Ended, data: dict[str, Any], p: FindParams, run: _Explore) -> str:
    how, back = _how(data), _back(run)
    passed = run.passed_by
    faced = (
        f"; {passed} time{'s' if passed != 1 else ''} it took something for one, faced it and "
        "did not see it again"
        if passed
        else ""
    )
    unchecked = len(data.get("unchecked") or [])
    pending = (
        f"; {unchecked} time{'s' if unchecked != 1 else ''} it took something for one and had "
        "not faced it yet"
        if unchecked
        else ""
    )
    if ended.exit == "found":
        if "place" in data:
            marked = f"; marked '{data['place']}' where it stands, facing it"
            if "was_at" in data:
                x, y = data["was_at"]
                marked += f", where the '{data['place']}' it had marked was at ({x:.2f}, {y:.2f})"
        else:
            marked = f"; it could not mark where it stands: {data['place_not_marked']}"
        return f"find: {ended.said}{marked}: {how}{back}"
    if ended.exit == "done":
        return f"find: no {p.target} seen, and {ended.said}{faced}: {how}{back}"
    if ended.exit == "minutes":
        # sightings it had no time to face are not a target that is not there
        seen = f"no {p.target} seen for sure" if unchecked else f"no {p.target} seen"
        ran_out = _ran_out(ended, p.minutes)
        return f"find: {seen}, and {ran_out}{faced}{pending}: {how}{back}"
    return f"find: {ended.said}, so the robot stopped{pending}: {how}"


async def _run(
    ctx: VerbContext, verb: str, minutes: float, spec: ExploreSpec, *, seek: str | None = None
) -> tuple[_Explore, _Ended, dict[str, Any]] | VerbResult:
    """One exploration, or a search for `seek`: refused before anything moves where the robot's
    reading keeps no map or nothing says how big the body is, and stopped whatever ends it."""
    book = ctx.places
    if book is None:
        return VerbResult.fail(f"{verb}: {NO_POSE}")
    source = book.source
    if not source.has_grid or source.view is None:
        return VerbResult.fail(
            f"{verb}: {source.describe()} keeps no map to explore by, only a position"
        )
    footprint = source.footprint_m
    if footprint is None or footprint <= 0:
        return VerbResult.fail(
            f"{verb}: nothing says how big the body is, so no way through a room can be planned"
        )
    t0 = ctx.transport.now()
    deadline = t0 + minutes * 60.0

    def interrupted() -> str | None:
        if (why := ctx.interrupted()) is not None:
            return why
        return MINUTES_UP if ctx.transport.now() >= deadline else None

    state = await ctx.transport.get_state()
    extras = state.extras if isinstance(state.extras, dict) else {}
    run = _Explore(
        ctx=ctx,
        walk_ctx=dataclasses.replace(ctx, interrupted=interrupted),
        source=source,
        book=book,
        spec=spec,
        minutes=minutes,
        t0=t0,
        deadline=deadline,
        gait=Gait.of(ctx, source, extras),
        footprint=footprint,
        given_up=given_up_of(source),
        seek=seek,
    )
    try:
        ended, data = await run.result()
    except SafetyStop:
        # the run's rule, read at a stop, stopped the body before it raised, and so did a walk
        # that let one go; anything else that raised one came with a twist standing
        if not run.stop_sent:
            await ctx.transport.stop()
        raise
    except BaseException:
        await ctx.transport.stop()
        raise
    await ctx.transport.stop()
    return run, ended, data


def _result(ended: _Ended, data: dict[str, Any], said: str, well: frozenset[Exit]) -> VerbResult:
    if ended.exit in well:
        return VerbResult.success(said, **data)
    if ended.exit in ("stopped", "budget"):
        # told to end from beside it, as `_ended_by` marks a verb a stop or the budget ended
        data["interrupted"] = ended.said
    return VerbResult.fail(said, **data)


async def explore(ctx: VerbContext, p: ExploreParams, spec: ExploreSpec) -> VerbResult:
    ran = await _run(ctx, "explore", p.minutes, spec)
    if isinstance(ran, VerbResult):
        return ran
    run, ended, data = ran
    return _result(ended, data, _words(ended, data, p.minutes, run), ENDS_WELL)


async def find(ctx: VerbContext, p: FindParams, spec: ExploreSpec) -> VerbResult:
    """Explore with a camera frame at every look, until what the detector takes for `p.target`
    is faced and seen again (`_Explore.confirm`). A target the detector never gives is refused
    before anything moves."""
    if ctx.detector is None:
        return VerbResult.fail("find needs a detector (none configured)")
    known = labels_of(ctx.detector)
    if known is not None and p.target not in known:
        name = getattr(ctx.detector, "name", "the detector")
        return VerbResult.fail(
            f"find: {name} finds {', '.join(sorted(known))}, and never a {p.target!r}: ask for "
            "one of those"
        )
    ran = await _run(ctx, "find", p.minutes, spec, seek=p.target)
    if isinstance(ran, VerbResult):
        return ran
    run, ended, data = ran
    return _result(ended, data, _find_words(ended, data, p, run), FOUND)


def explore_verb(spec: ExploreSpec) -> Verb:
    """`explore` for a body with these numbers."""

    async def run(ctx: VerbContext, p: ExploreParams) -> VerbResult:
        return await explore(ctx, p, spec)

    return Verb(
        "explore",
        "Walk to what the robot's own map has not seen yet and stop to look, again and again, "
        "until nothing is left it can reach or its minutes run out, at most 8. It marks where it "
        "began as the place 'start'. It walks on floor its map has seen, looking before it steps "
        "past the edge of it, but the map's sensor cannot see a step down, so keep it to one flat "
        "floor.",
        run,
        ExploreParams,
        timeout_s=EXPLORE_TIMEOUT_S,
        kind="composite",
        safety_class="confirm",
        done_condition="no gap left in its map that it can reach, or its minutes ran out, or "
        "stopped saying why: a stop, lost, or a body it may move no more.",
    )


def find_verb(spec: ExploreSpec) -> Verb:
    """`find` for a body with these numbers, on the same walks and looks as `explore`."""

    async def run(ctx: VerbContext, p: FindParams) -> VerbResult:
        return await find(ctx, p, spec)

    return Verb(
        "find",
        "Look for something the detector knows, a ball or a person, by exploring: walk to what "
        "the robot's own map has not seen yet and look there, a camera frame at every look, "
        "until it sees it, faces it and sees it again, for at most 8 minutes. It marks where it "
        "then stands, facing it, as a place named after it, and where it began as 'start'. It "
        "does not walk up to it: go_to does. It walks on floor its map has seen, looking before "
        "it steps past the edge of it, but the map's sensor cannot see a step down, so keep it to "
        "one flat floor.",
        run,
        FindParams,
        timeout_s=EXPLORE_TIMEOUT_S,
        kind="composite",
        safety_class="confirm",
        done_condition="it saw the target, faced it and saw it again, and marked where it "
        "stands when it could; or it did not, saying why: nothing left it can reach, its "
        "minutes, its camera, a stop, lost, or a body it may move no more.",
    )
