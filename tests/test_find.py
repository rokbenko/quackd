"""`find`: the duck explores with a camera frame at every look until what it seeks is faced and seen
again, and marks where it then stands.

Every run is the duck's simulator in a room, on the simulator's own clock, with the colour
detector the simulator's camera is drawn for.
"""

from __future__ import annotations

import dataclasses
import math
from typing import Any

import pytest
from PIL import Image

from quackd.perception.base import Detection, labels_of
from quackd.perception.color_blob import ColorBlobDetector
from quackd.perception.host import HostDetector
from quackd.perception.yolo import YoloDetector
from quackd.sim2d.world import World
from quackd.space.places import MAX_PLACES, Place
from quackd.space.pose import wrap_angle
from quackd.space.room import load_room
from quackd.space.simmap import SimulatedMap
from quackd.transport.base import Intent, spatial_of
from quackd.verbs import explore as explore_module
from quackd.verbs.explore import MINUTES_UP, START, FindParams
from quackd.verbs.registry import STOPPED, VerbContext, VerbResult
from quackd_microduck import MICRODUCK_VERBS, NO_CAMERA_WHY, microduck_manifest
from quackd_microduck.verbs import MICRODUCK_EXPLORE
from tests.test_explore import _duck
from tests.test_go_to_place import Recording, _go

EDGE = "the edge of what its map has seen"


async def _seeker(
    room: str = "two-rooms", seed: int = 3, detector: Any = None
) -> tuple[Any, Recording, VerbContext]:
    adapter, body, ctx = await _duck(room, seed)
    return adapter, body, dataclasses.replace(ctx, detector=detector or ColorBlobDetector())


async def _find(ctx: VerbContext, target: str = "ball", minutes: float = 4.0) -> VerbResult:
    return await MICRODUCK_VERBS["find"].execute(ctx, FindParams(target=target, minutes=minutes))


def _walks_to_edges(monkeypatch: pytest.MonkeyPatch) -> list[tuple[float, float]]:
    """The goal of every walk to the edge of what the map has seen, from now on."""
    goals: list[tuple[float, float]] = []
    navigate = explore_module.navigate

    async def noted(*args: Any, **kw: Any) -> Any:
        if kw["label"] == EDGE:
            goals.append(args[2])
        return await navigate(*args, **kw)

    monkeypatch.setattr(explore_module, "navigate", noted)
    return goals


class _Blind:
    """A detector that knows the ball and never sees one."""

    name = "blind"
    labels = frozenset({"ball"})

    def detect(self, image: Image.Image) -> list[Detection]:
        return []


def _misreads_once(monkeypatch: pytest.MonkeyPatch) -> ColorBlobDetector:
    """The simulator's detector, which takes something for a ball 30° left in its first frame and
    never again."""
    detector = ColorBlobDetector()
    real = detector.detect
    frames: list[int] = []

    def misread_once(image: Image.Image) -> list[Detection]:
        frames.append(1)
        if len(frames) == 1:
            return [Detection(label="ball", cx=0.2, cy=0.6, area=0.01, bearing_deg=30.0)]
        return real(image)

    monkeypatch.setattr(detector, "detect", misread_once)
    return detector


# ── finding ─────────────────────────────────────────────────────────────────────────────


async def test_it_finds_the_ball_in_the_other_room_and_marks_a_place_that_sees_it() -> None:
    """two-rooms hides the ball from where the duck starts: it walks through the doorway, sees it,
    faces it, sees it again, and marks where it then stands. That place faces the ball, the way the
    head saw it rather than the way a turn left the body, with nothing solid between."""
    adapter, body, ctx = await _seeker("two-rooms")
    try:
        result = await _find(ctx)
        assert result.ok and result.data["exit"] == "found", result.summary
        assert result.data["place"] == "ball" and result.data["passed_by"] == 0
        assert ctx.places is not None
        place = ctx.places.store.get("ball")
        assert place is not None
        world = adapter.transport.world
        room = load_room("two-rooms")
        assert place.x > 0 > room.start[0], "found from the other room"  # type: ignore[index]
        ball = (world.ball.x, world.ball.y)
        heading = math.atan2(ball[1] - place.y, ball[0] - place.x)
        lens = ColorBlobDetector().fov_deg
        # facing it: well inside the middle of the view it was seen in again
        assert abs(math.degrees(wrap_angle(heading - place.yaw))) <= lens / 8
        far = math.dist((place.x, place.y), ball)
        wall = room.raycast(place.x, place.y, heading, far)
        assert wall is None, f"a wall {wall:.2f} m away stands before a ball {far:.2f} m away"
        assert result.data["where"]["x"] == pytest.approx(place.x, abs=1e-3)
        assert "marked 'ball' where it stands, facing it" in result.summary
        assert body.sent[-1][1] == "stop"
    finally:
        await adapter.close()


async def test_a_ball_in_view_where_it_begins_is_found_without_a_step(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The hall's ball is in the duck's view from the start, three metres down the hall: the
    first stop's look finds it, and no walk to any edge of the map is taken."""
    goals = _walks_to_edges(monkeypatch)
    adapter, _body, ctx = await _seeker("hall", seed=0)
    try:
        result = await _find(ctx)
        assert result.ok and result.data["exit"] == "found", result.summary
        assert goals == [] and result.data["walked_m"] < 0.05, result.data
    finally:
        await adapter.close()


async def test_a_sighting_in_one_frame_only_is_faced_and_passed_by(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The detector takes something for a ball in the first frame and never again: the duck faces
    it, does not see it, passes it by, and finds the real ball in the other room."""
    adapter, _body, ctx = await _seeker("two-rooms", detector=_misreads_once(monkeypatch))
    try:
        result = await _find(ctx)
        assert result.ok and result.data["exit"] == "found", result.summary
        assert result.data["passed_by"] == 1
        assert "1 time it took something for one" not in result.summary  # said only when unfound
        assert ctx.places is not None
        place = ctx.places.store.get("ball")
        assert place is not None and place.x > 0, "marked where it misread, not where it found"
    finally:
        await adapter.close()


async def test_start_is_marked_and_go_to_place_brings_it_back() -> None:
    adapter, _body, ctx = await _seeker("two-rooms")
    try:
        result = await _find(ctx)
        assert result.ok, result.summary
        assert f"'{START}' is where it began" in result.summary
        assert ctx.places is not None
        start = ctx.places.store.get(START)
        assert start is not None
        back = await _go(ctx, START, face=False, timeout_s=120.0)
        assert back.ok, back.summary
        duck = adapter.transport.world.ducks[0]
        assert math.dist((duck.x, duck.y), (start.x, start.y)) < 0.2
    finally:
        await adapter.close()


async def _frames_round_every_stop(
    monkeypatch: pytest.MonkeyPatch, *, frame_rad: float | None
) -> None:
    """A search of two-rooms that sees nothing, and every stop's camera frames, where the camera
    truly pointed as each was taken, each taken as `frame_rad` wide, or as wide as a look of the
    map's, meeting all the way round."""
    glances: list[tuple[float, float, float]] = []
    adapter, _body, ctx = await _seeker("two-rooms", detector=_Blind())
    duck = adapter.transport.world.ducks[0]
    see = explore_module._see

    async def noted(*args: Any, **kw: Any) -> Any:
        glances.append((duck.x, duck.y, duck.theta + duck.head_yaw))
        return await see(*args, **kw)

    monkeypatch.setattr(explore_module, "_see", noted)
    try:
        result = await _find(ctx)
        assert result.data["exit"] == "done", result.summary
        view = spatial_of(adapter).view  # type: ignore[union-attr]
        assert view is not None
        half = (frame_rad if frame_rad is not None else view[0]) / 2
        stops: dict[tuple[int, int], list[float]] = {}
        for x, y, heading in glances:
            stops.setdefault((round(x / 0.05), round(y / 0.05)), []).append(heading)
        assert len(stops) == len(result.data["stops"]) > 1, (stops.keys(), result.data["stops"])
        for stop, headings in stops.items():
            unseen = [
                degree
                for degree in range(360)
                if not any(
                    abs(wrap_angle(math.radians(degree) - h)) <= half + 1e-9 for h in headings
                )
            ]
            assert unseen == [], (stop, unseen[:3])
    finally:
        await adapter.close()


async def test_its_camera_sees_all_the_way_round_at_every_stop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """What it seeks may stand on floor the map saw from elsewhere, where no look of the map's
    would turn: every look of a search has a camera frame, the stop's own view among them, and at
    every stop they meet all the way round. Each frame is taken here as no wider than a look of the
    map's, which a camera narrower than the simulators' would be."""
    await _frames_round_every_stop(monkeypatch, frame_rad=None)


async def test_with_only_the_body_turning_its_camera_still_sees_all_the_way_round(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On a map that takes no keyframe from where the head looks, as `mapd` may not, every view
    past the stop's own is a body turn, which lands within its own tolerance of the way: each has
    its camera frame, as wide as the camera, and they still meet."""
    monkeypatch.setattr(SimulatedMap, "keyframes_follow_head", property(lambda _self: False))
    await _frames_round_every_stop(monkeypatch, frame_rad=math.radians(ColorBlobDetector().fov_deg))


async def test_a_ball_never_seen_in_the_middle_of_the_view_is_not_believed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A detector that sees a ball at the edge of every frame, wherever the duck turns: facing it
    never brings it to the middle half of the view, so it is passed by each time and nothing is
    marked."""

    class _Edge:
        name = "edge"
        labels = frozenset({"ball"})
        fov_deg = ColorBlobDetector().fov_deg

        def detect(self, image: Image.Image) -> list[Detection]:
            return [Detection(label="ball", cx=0.1, cy=0.6, area=0.01, bearing_deg=40.0)]

    adapter, _body, ctx = await _seeker("hall", detector=_Edge())
    try:
        result = await _find(ctx, minutes=0.5)
        assert not result.ok and result.data["exit"] in ("minutes", "done"), result.summary
        assert result.data["passed_by"] > 0 and "place" not in result.data
        assert ctx.places is not None and ctx.places.store.get("ball") is None
        assert "faced it and did not see it again" in result.summary
    finally:
        await adapter.close()


# ── refused, and ending without it ──────────────────────────────────────────────────────


async def test_a_label_the_detector_never_gives_is_refused_before_anything_moves() -> None:
    adapter, body, ctx = await _seeker("two-rooms")
    try:
        result = await _find(ctx, target="unicorn")
        assert not result.ok and "never a 'unicorn'" in result.summary, result.summary
        for label in sorted(ColorBlobDetector().labels):
            assert label in result.summary
        assert body.sent == []
        nothing = dataclasses.replace(ctx, detector=None)
        result = await _find(nothing)
        assert not result.ok and "needs a detector" in result.summary
        assert body.sent == []
    finally:
        await adapter.close()


def test_the_detectors_say_which_labels_they_give() -> None:
    assert labels_of(ColorBlobDetector()) == {"ball", "person", "pet", "duck"}
    # a YOLO model's, wherever it runs, built here without the model
    assert labels_of(YoloDetector) == labels_of(HostDetector) == {"ball", "person", "pet"}
    assert labels_of(_Blind()) == {"ball"}
    assert labels_of(object()) is None  # one that does not say is refused nothing


def test_a_duck_that_explores_with_no_camera_is_not_offered_find_and_says_why() -> None:
    blind = microduck_manifest("sim2d", places=True, mapped=True, sized=True, camera=False)
    assert "explore" in blind.verb_names() and "find" not in blind.verb_names()
    assert blind.not_offered() == {"find": NO_CAMERA_WHY}
    seeing = microduck_manifest("sim2d", places=True, mapped=True, sized=True)
    assert {"explore", "find"} <= set(seeing.verb_names()) and seeing.not_offered() == {}
    assert seeing.preconditions["find"] == ["standing"]


async def test_nothing_seen_anywhere_it_can_reach_fails_saying_so() -> None:
    adapter, body, ctx = await _seeker("two-rooms", detector=_Blind())
    try:
        result = await _find(ctx)
        assert not result.ok and result.data["exit"] == "done", result.summary
        assert result.summary.startswith("find: no ball seen, and no gap in its map is left")
        assert body.sent[-1][1] == "stop"
    finally:
        await adapter.close()


async def test_its_minutes_running_out_unfound_fail() -> None:
    adapter, body, ctx = await _seeker("hall", detector=_Blind())
    try:
        result = await _find(ctx, minutes=0.25)
        assert not result.ok and result.data["exit"] == "minutes", result.summary
        # its minutes ran out, or left too little to walk to the next floor it has not seen
        assert result.summary.startswith("find: no ball seen, and "), result.summary
        assert body.sent[-1][1] == "stop"
    finally:
        await adapter.close()


async def test_a_stop_while_it_faces_a_sighting_ends_it_with_no_twist_after(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    told: list[float] = []
    confirm = explore_module._Explore.confirm

    async def braked(self: Any) -> None:
        if self.sightings and not told:
            told.append(self.now())
        await confirm(self)

    monkeypatch.setattr(explore_module._Explore, "confirm", braked)
    adapter, body, ctx = await _seeker("hall", seed=0)
    try:
        ctx.interrupted = lambda: STOPPED if told else None
        result = await _find(ctx)
        assert told, "it never saw the ball"
        assert not result.ok and result.data["exit"] == "stopped", result.summary
        assert body.twists(since=told[0]) == []
        assert body.sent[-1][1] == "stop"
    finally:
        await adapter.close()


async def test_a_camera_that_gives_no_frame_ends_it() -> None:
    adapter, body, ctx = await _seeker("two-rooms")
    try:

        async def no_frame() -> None:
            return None

        body.get_frame = no_frame  # type: ignore[method-assign]
        result = await _find(ctx)
        assert not result.ok and result.data["exit"] == "camera", result.summary
        assert body.sent[-1][1] == "stop"
    finally:
        await adapter.close()


# ── where the head points ───────────────────────────────────────────────────────────────


async def test_the_way_it_saw_something_is_the_way_its_camera_truly_pointed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A sighting's way is the body's heading and the head's yaw its frame was taken through, as
    the body reports it: after a sighting passed by has turned the head and the next walk has
    straightened it, what the search takes the head's yaw for is still where the head is."""
    adapter, _body, ctx = await _seeker("two-rooms", detector=_misreads_once(monkeypatch))
    duck = adapter.transport.world.ducks[0]
    truths: list[float] = []
    see = explore_module._see

    async def seen(*args: Any, **kw: Any) -> Any:
        truths.append(duck.theta + duck.head_yaw)
        return await see(*args, **kw)

    frame_hits = explore_module._Explore.frame_hits
    off: list[tuple[float, float]] = []

    async def checked(self: Any, caption: str) -> Any:
        head, hits = await frame_hits(self, caption)
        pose = self.source.fix().pose
        assert pose is not None
        off.append(
            (
                abs(wrap_angle(pose.yaw + head - truths[-1])),
                abs(wrap_angle(self.head - duck.head_yaw)),
            )
        )
        return head, hits

    monkeypatch.setattr(explore_module, "_see", seen)
    monkeypatch.setattr(explore_module._Explore, "frame_hits", checked)
    try:
        result = await _find(ctx)
        assert result.ok and result.data["passed_by"] == 1, result.summary
        tenth = math.radians(0.1)  # the state's head yaw is said to a tenth of a degree
        assert off and max(camera for camera, _ in off) <= tenth, off
        assert max(belief for _, belief in off) <= tenth, off
    finally:
        await adapter.close()


async def test_a_head_that_stops_short_of_its_look_is_read_where_it_stopped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A head that does not quite get where it is sent, as the physics duck's may not: each frame's
    way is the yaw the body says the head reached, not the one sent."""
    look = World.look

    def short(self: Any, x: float, y: float, z: float = 0.0, duck_index: int = 0) -> bool:
        clamped = look(self, x, y, z, duck_index)
        self.ducks[duck_index].head_yaw *= 0.8  # four fifths of the way there
        return clamped

    monkeypatch.setattr(World, "look", short)
    adapter, _body, ctx = await _seeker("two-rooms", detector=_Blind())
    duck = adapter.transport.world.ducks[0]
    truths: list[float] = []
    see = explore_module._see

    async def seen(*args: Any, **kw: Any) -> Any:
        truths.append(duck.theta + duck.head_yaw)
        return await see(*args, **kw)

    frame_hits = explore_module._Explore.frame_hits
    off: list[float] = []

    async def checked(self: Any, caption: str) -> Any:
        head, hits = await frame_hits(self, caption)
        pose = self.source.fix().pose
        assert pose is not None
        off.append(abs(wrap_angle(pose.yaw + head - truths[-1])))
        return head, hits

    monkeypatch.setattr(explore_module, "_see", seen)
    monkeypatch.setattr(explore_module._Explore, "frame_hits", checked)
    try:
        await _find(ctx, minutes=0.25)
        turned = [truth for truth in truths if abs(wrap_angle(truth)) > math.radians(1.0)]
        assert turned, "no frame was taken through a turned head"
        assert off and max(off) <= math.radians(0.1), max(off)  # said to a tenth of a degree
    finally:
        await adapter.close()


async def test_a_head_left_turned_is_straightened_before_the_first_look(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On a map that takes no keyframe from where the head looks, as `mapd` may not, a head an
    earlier gaze left turned is straightened before the first look, so the stop's own view, which
    the map and the camera both count as looked at, is the way the body faces."""
    monkeypatch.setattr(SimulatedMap, "keyframes_follow_head", property(lambda _self: False))
    goals = _walks_to_edges(monkeypatch)
    adapter, _body, ctx = await _seeker("hall", seed=0)
    duck = adapter.transport.world.ducks[0]
    heads: list[float] = []
    see = explore_module._see

    async def seen(*args: Any, **kw: Any) -> Any:
        heads.append(duck.head_yaw)
        return await see(*args, **kw)

    monkeypatch.setattr(explore_module, "_see", seen)
    try:
        turned = math.radians(40.0)
        await adapter.send_intent(Intent.look(math.cos(turned), math.sin(turned), 0.0))
        assert duck.head_yaw == pytest.approx(turned)
        result = await _find(ctx)
        assert heads and heads[0] == pytest.approx(0.0, abs=1e-9), heads[:3]
        assert result.ok and result.data["exit"] == "found", result.summary
        assert result.data["passed_by"] == 0 and goals == [], (result.data, goals)
    finally:
        await adapter.close()


# ── what it says ────────────────────────────────────────────────────────────────────────


async def test_sightings_it_had_no_time_to_face_are_not_said_to_be_unseen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Its minutes run out as it turns to face what it took for the ball: the words say it saw
    something it had not faced yet, and the result says from where and which way, rather than
    that no ball was seen."""
    confirm = explore_module._Explore.confirm

    async def out_of_time(self: Any) -> None:
        if self.sightings:
            self.walk_ctx = dataclasses.replace(self.walk_ctx, interrupted=lambda: MINUTES_UP)
        await confirm(self)

    monkeypatch.setattr(explore_module._Explore, "confirm", out_of_time)
    adapter, _body, ctx = await _seeker("hall", seed=0)
    try:
        result = await _find(ctx)
        assert not result.ok and result.data["exit"] == "minutes", result.summary
        unchecked = result.data["unchecked"]
        assert unchecked and {"from", "heading", "confidence"} <= set(unchecked[0])
        assert result.summary.startswith("find: no ball seen for sure, and its 4 minutes ran out")
        assert "it took something for one and had not faced it yet" in result.summary
    finally:
        await adapter.close()


async def test_one_ball_in_two_frames_that_overlap_is_one_sighting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The hall's ball is in more than one of a stop's frames, which overlap: it is one sighting
    there, faced once. It is never seen again here, so the search goes on to count each stop's."""
    frame_hits = explore_module._Explore.frame_hits

    async def never_again(self: Any, caption: str) -> Any:
        head, hits = await frame_hits(self, caption)
        return (head, []) if caption.endswith("facing it") else (head, hits)

    confirm = explore_module._Explore.confirm
    per_stop: list[int] = []

    async def counted(self: Any) -> None:
        per_stop.append(len(self.sightings))
        await confirm(self)

    monkeypatch.setattr(explore_module._Explore, "frame_hits", never_again)
    monkeypatch.setattr(explore_module._Explore, "confirm", counted)
    adapter, _body, ctx = await _seeker("hall", seed=0)
    try:
        result = await _find(ctx, minutes=1.0)
        assert not result.ok and "place" not in result.data, result.summary
        assert per_stop and per_stop[0] == 1 and max(per_stop) <= 1, per_stop
    finally:
        await adapter.close()


async def test_a_place_of_the_same_name_is_moved_and_its_words_say_from_where() -> None:
    adapter, _body, ctx = await _seeker("hall", seed=0)
    try:
        assert ctx.places is not None
        frame = spatial_of(adapter).frame  # type: ignore[union-attr]
        ctx.places.store.mark(Place("ball", 1.0, -0.3, 0.0, frame.id, "map", 0.0))
        result = await _find(ctx)
        assert result.ok and result.data["was_at"] == [1.0, -0.3], result.data
        assert "where the 'ball' it had marked was at (1.00, -0.30)" in result.summary
    finally:
        await adapter.close()


async def test_a_found_ball_with_no_room_left_for_its_place_is_found_all_the_same() -> None:
    adapter, _body, ctx = await _seeker("hall", seed=0)
    try:
        assert ctx.places is not None
        for k in range(MAX_PLACES):
            ctx.places.store.mark(Place(f"p{k}", 0.0, 0.0, 0.0, "elsewhere", "map", 0.0))
        result = await _find(ctx)
        assert result.ok and result.data["exit"] == "found", result.summary
        assert "place" not in result.data and "this robot keeps" in result.data["place_not_marked"]
        assert "it could not mark where it stands: this robot keeps" in result.summary
    finally:
        await adapter.close()


# ── ending while it looks ───────────────────────────────────────────────────────────────


async def test_a_stop_while_it_reads_the_frame_it_faced_ends_it_unmarked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    told: list[float] = []
    see = explore_module._see

    async def braked(*args: Any, **kw: Any) -> Any:
        got = await see(*args, **kw)
        if args[2].endswith("facing it"):
            told.append(1.0)  # the stop lands as the frame is read
        return got

    monkeypatch.setattr(explore_module, "_see", braked)
    adapter, _body, ctx = await _seeker("hall", seed=0)
    try:
        ctx.interrupted = lambda: STOPPED if told else None
        result = await _find(ctx)
        assert told and not result.ok and result.data["exit"] == "stopped", result.summary
        assert "place" not in result.data
        assert ctx.places is not None and ctx.places.store.get("ball") is None
    finally:
        await adapter.close()


async def test_a_stop_while_the_head_settles_on_a_sighting_sends_nothing_after(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The stop lands while the head, turned the rest of the way to what it took for the ball,
    settles: nothing goes to the body after it, not the head straightened, not a twist."""
    sent: list[str] = []
    told: list[int] = []  # how much had been sent when the stop landed
    at: list[float] = []
    hold = explore_module._Explore.hold

    async def braked(self: Any, seconds: float) -> None:
        if self.sightings and not told:
            told.append(len(sent))
            at.append(self.now())
        await hold(self, seconds)

    monkeypatch.setattr(explore_module._Explore, "hold", braked)
    adapter, body, ctx = await _seeker("hall", seed=0)
    send = body.send_intent

    async def noted(intent: Any) -> Any:
        sent.append(intent.kind)
        return await send(intent)

    body.send_intent = noted  # type: ignore[method-assign]
    try:
        ctx.interrupted = lambda: STOPPED if told else None
        result = await _find(ctx)
        assert told and not result.ok and result.data["exit"] == "stopped", result.summary
        assert sent[told[0] - 1] == "look", "the stop came after the head was turned"
        assert sent[told[0] :] == [], sent[told[0] :]
        assert body.sent[-1][1] == "stop"
        # heard within a tick, not once the head's settle is out
        assert body.sent[-1][0] - at[0] < MICRODUCK_EXPLORE.head_settle_s
    finally:
        await adapter.close()


async def test_a_detector_that_cannot_read_a_frame_ends_it() -> None:
    detector = _Blind()
    detector.error = "the board did not answer"  # type: ignore[attr-defined]
    adapter, body, ctx = await _seeker("two-rooms", detector=detector)
    try:
        result = await _find(ctx)
        assert not result.ok and result.data["exit"] == "camera", result.summary
        assert "blind could not read a frame: the board did not answer" in result.summary
        assert body.sent[-1][1] == "stop"
    finally:
        await adapter.close()


def test_the_prompt_names_find_among_the_verbs_that_ask_first() -> None:
    """The task's own confirm list is empty, and `find` asks first whatever it says: the pilot is
    told it will be asked, as the executor will ask."""
    from quackd.adapters.factory import describe, parse_robot_spec, registry_for
    from quackd.agent.prompts import build_system_prompt
    from quackd.duckfile.parser import load_duck

    robot = parse_robot_spec("microduck:sim2d")
    manifest = describe(robot, room="two-rooms")
    registry = registry_for(robot, manifest)
    duck = load_duck("find-the-ball")
    assert duck.frontmatter.verbs.confirm == []
    verbs = [registry.view(name) for name in duck.frontmatter.verbs.allow]
    prompt = build_system_prompt(duck, verbs, "sim2d", manifest=manifest)
    assert "Verbs marked confirm (find) ask a human before running." in prompt
