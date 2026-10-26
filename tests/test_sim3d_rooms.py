"""A room in the physics simulator (`--room` on `microduck:mujoco`): its walls and furniture are
geoms, the duck is held by them and counts its bumps, its camera sees them and nothing behind
them, and its transport keeps the same simulated map the cartoon's does.

Skipped wholesale without `quackd[mujoco]`, and the camera tests without an offscreen context."""

from __future__ import annotations

import math

import numpy as np
import pytest

mujoco = pytest.importorskip("mujoco")

from quackd.adapters.factory import make_adapter  # noqa: E402
from quackd.perception.color_blob import ColorBlobDetector  # noqa: E402
from quackd.sim2d.world import World  # noqa: E402
from quackd.space.room import Room, Wall, load_room  # noqa: E402
from quackd.space.simmap import STAND_IN, SimulatedMap  # noqa: E402
from quackd.transport.base import spatial_of  # noqa: E402
from quackd_microduck.sim3d.render import render_headcam  # noqa: E402
from quackd_microduck.sim3d.scene import DUCK_R, ROOM_GEOM_PREFIX, arena_xml  # noqa: E402
from quackd_microduck.sim3d.world import CONTROL_DT, MujocoWorld  # noqa: E402
from quackd_microduck.transports.mapd import sim_map_params  # noqa: E402
from quackd_microduck.transports.mujoco import MujocoTransport  # noqa: E402
from tests.gl import require_render  # noqa: E402

TWO_ROOMS = load_room("two-rooms")


def _walk(world: MujocoWorld, vx: float, seconds: float) -> None:
    for k in range(round(seconds / CONTROL_DT)):
        if k % 10 == 0:  # inside the deadman, as the walk verb re-sends
            world.set_velocity(vx, 0.0, 0.0)
        world.step()
    world.stop()


def test_a_room_lays_the_duck_and_the_ball_out_where_the_cartoon_does() -> None:
    for seed in range(5):
        cartoon = World(seed=seed, room=TWO_ROOMS)
        physics = MujocoWorld(seed=seed, room=TWO_ROOMS)
        try:
            assert (physics.x, physics.y, physics.theta) == pytest.approx(
                (cartoon.duck.x, cartoon.duck.y, cartoon.duck.theta)
            )
            assert (physics.ball_x, physics.ball_y) == pytest.approx(
                (cartoon.ball.x, cartoon.ball.y), abs=1e-3
            )
            assert (
                physics.rng.bit_generator.state == np.random.default_rng(seed).bit_generator.state
            )
            assert physics.snapshot()["room"] == "two-rooms"
        finally:
            physics.close()


def test_without_a_room_the_scene_is_the_arena_it_always_was() -> None:
    arena = arena_xml("", ball=(0.5, 0.5))
    assert ROOM_GEOM_PREFIX not in arena
    for side in ("east", "west", "north", "south"):
        assert f'name="quackd_wall_{side}"' in arena
    assert 'pos="1.02 0.0 0.08"' in arena, "the arena's own walls, where they always stood"
    world = MujocoWorld(seed=0)
    try:
        assert world.room is None and "room" not in world.snapshot() and world.bumps == 0
    finally:
        world.close()


def test_every_wall_and_box_of_a_room_is_a_geom_where_the_room_says() -> None:
    world = MujocoWorld(seed=0, room=TWO_ROOMS)
    try:
        names = [
            mujoco.mj_id2name(world.model, mujoco.mjtObj.mjOBJ_GEOM, g) or ""
            for g in range(world.model.ngeom)
        ]
        ours = [n for n in names if n.startswith(ROOM_GEOM_PREFIX)]
        # four outer walls, each wall a box and its two ends, each box a box
        assert len(ours) == 4 + 3 * len(TWO_ROOMS.walls) + len(TWO_ROOMS.boxes)
        box = mujoco.mj_name2id(world.model, mujoco.mjtObj.mjOBJ_GEOM, f"{ROOM_GEOM_PREFIX}box_0")
        assert tuple(world.model.geom_pos[box][:2]) == pytest.approx(
            (TWO_ROOMS.boxes[0].cx, TWO_ROOMS.boxes[0].cy)
        )
    finally:
        world.close()


def test_a_wall_written_at_an_angle_stands_at_that_angle() -> None:
    """Turned by quaternion: a robot's own MJCF may set the compiler's angles to radians, and a
    turn written in degrees would then turn the wall nowhere near where the room says."""
    room = Room("slant", 1.5, 1.5, walls=(Wall(-0.5, -0.5, 0.5, 0.5),))
    world = MujocoWorld(seed=0, room=room)
    try:
        geom = mujoco.mj_name2id(world.model, mujoco.mjtObj.mjOBJ_GEOM, f"{ROOM_GEOM_PREFIX}wall_0")
        along = world.data.geom_xmat[geom].reshape(3, 3)[:, 0]
        assert math.degrees(math.atan2(along[1], along[0])) == pytest.approx(45.0, abs=1e-6)
    finally:
        world.close()


def test_a_room_holds_the_puppet_at_its_wall_and_counts_the_bump() -> None:
    world = MujocoWorld(seed=0, room=TWO_ROOMS)
    try:
        world.body.reset(-1.0, 0.0, math.pi)  # the outer wall half a metre behind the start
        _walk(world, 0.2, 5.0)
        assert world.bumps > 0
        assert world.x >= -TWO_ROOMS.half_w + DUCK_R - 1e-9
        assert not TWO_ROOMS.blocked(world.x, world.y, DUCK_R)
    finally:
        world.close()


def test_a_duck_stood_up_against_a_wall_stands_clear_of_it() -> None:
    world = MujocoWorld(seed=0, room=TWO_ROOMS)
    try:
        world.body.reset(-0.03, -0.6, 0.0)  # partly inside the wall below the doorway
        world.body.fall()  # type: ignore[attr-defined]
        world.enable()
        assert world.posture == "standing"
        assert not TWO_ROOMS.blocked(world.x, world.y, DUCK_R)
        assert math.hypot(world.x + 0.03, world.y + 0.6) < 2 * DUCK_R, "set down near where it fell"
    finally:
        world.close()


def test_the_head_camera_sees_walls_as_nothing_and_nothing_through_them() -> None:
    world = MujocoWorld(seed=3, room=TWO_ROOMS)
    try:
        require_render(world)
        detector = ColorBlobDetector()
        world.body.reset(-1.0, 0.0, 0.0)

        def labels() -> list[str]:
            return [d.label for d in detector.detect(render_headcam(world, 256))]

        adr = world._ball_qpos

        def ball_at(x: float, y: float) -> None:
            world.data.qpos[adr : adr + 3] = (x, y, 0.05)
            mujoco.mj_forward(world.model, world.data)

        ball_at(0.9, -0.15)
        assert "ball" in labels(), "through the doorway"
        ball_at(0.5, -0.8)
        assert "ball" not in labels(), "behind the wall below the doorway"
        ball_at(6.0, 6.0)  # out of the room altogether
        for heading in np.linspace(-math.pi, math.pi, 8, endpoint=False):
            world.body.reset(-1.0, 0.0, float(heading))
            mujoco.mj_forward(world.model, world.data)
            assert labels() == [], f"a wall seen as something, facing {heading:.2f}"
    finally:
        world.close()


async def test_a_duck_in_a_room_on_the_physics_backend_says_where_its_map_says_it_is() -> None:
    transport = MujocoTransport(
        seed=3, body="puppet", room=TWO_ROOMS, map_params=sim_map_params(DUCK_R)
    )
    assert transport.spatial is None, "the map is the connected world's"
    await transport.connect()
    try:
        assert isinstance(transport.spatial, SimulatedMap)
        state = await transport.get_state()
        assert STAND_IN in state.extras["assumptions"]
        assert any("puppet" in a for a in state.extras["assumptions"]), "the body's kept too"
        assert "where=lost(" in state.summary()
        await transport.sleep(1.0)
        state = await transport.get_state()
        assert "where=map(-1.00, 0.00, 0.00 rad)" in state.summary()
        assert state.extras["where"]["frame"].startswith("sim:mujoco:")
    finally:
        await transport.close()


async def test_microduck_mujoco_takes_a_room(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QUACKD_MUJOCO_BODY", "puppet")
    adapter = make_adapter("microduck:mujoco", seed=3, room="two-rooms")
    try:
        manifest = await adapter.connect()
        assert {v.name for v in manifest.verbs} >= {"map", "mark_place", "forget_place"}
        assert isinstance(spatial_of(adapter), SimulatedMap)
    finally:
        await adapter.close()


class _Crate:
    """The least body there is that physics moves: a free box pushed along by its twist, so a
    wall it meets is met by contact, as the walking duck meets one, and not by a rule."""

    name = "crate"

    def __init__(self) -> None:
        self.posture = "standing"
        self.walking = False
        self._data: object = None
        self._qpos = self._qvel = 0

    @property
    def include_xml(self) -> str:
        return ""

    @property
    def xml(self) -> str:
        return (
            '<body name="crate" pos="0 0 0.06"><freejoint name="crate_free"/>'
            '<geom name="crate_geom" type="box" size="0.06 0.06 0.06" mass="1"/></body>'
        )

    def mujoco_assets(self) -> dict[str, bytes]:
        return {}

    def attach(self, model: object, data: object) -> None:
        joint = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, "crate_free")
        self._qpos = int(model.jnt_qposadr[joint])  # type: ignore[attr-defined]
        self._qvel = int(model.jnt_dofadr[joint])  # type: ignore[attr-defined]
        self._data = data

    def reset(self, x: float, y: float, theta: float) -> None:
        q = self._data.qpos  # type: ignore[attr-defined]
        q[self._qpos : self._qpos + 7] = (x, y, 0.06, 1.0, 0.0, 0.0, 0.0)

    def set_head(self, head: tuple[float, float]) -> None:
        pass

    def control(self, cmd: tuple[float, float, float], dt: float, rng: object) -> None:
        self.walking = any(cmd)
        v = self._data.qvel  # type: ignore[attr-defined]
        v[self._qvel : self._qvel + 2] = (cmd[0], cmd[1])

    def pose(self) -> tuple[float, float, float]:
        q = self._data.qpos  # type: ignore[attr-defined]
        return float(q[self._qpos]), float(q[self._qpos + 1]), 0.0

    def head_pose(self) -> tuple[float, float, float, float, float]:
        x, y, _ = self.pose()
        return x, y, 0.2, 0.0, 0.0

    def sit_toggle(self) -> str:
        return self.posture

    def enable(self) -> None:
        pass

    def close(self) -> None:
        pass

    def extras(self) -> dict[str, object]:
        return {}


def _ball_touches_a_wall(world: MujocoWorld) -> bool:
    ball = mujoco.mj_name2id(world.model, mujoco.mjtObj.mjOBJ_BODY, "ball")
    for i in range(world.data.ncon):
        c = world.data.contact[i]
        bodies = {int(world.model.geom_bodyid[c.geom1]), int(world.model.geom_bodyid[c.geom2])}
        geoms = {int(c.geom1), int(c.geom2)}
        if ball in bodies and geoms & world._room_geoms:
            return True
    return False


def test_a_physical_body_that_walks_into_a_wall_counts_one_bump_per_touch() -> None:
    world = MujocoWorld(seed=0, body=_Crate(), room=TWO_ROOMS)  # type: ignore[arg-type]
    try:
        world.body.reset(-0.6, -0.6, 0.0)  # facing the wall below the doorway, 0.6 m off
        mujoco.mj_forward(world.model, world.data)
        _walk(world, 0.3, 1.0)
        assert world.bumps == 0, "it touched nothing yet"
        _walk(world, 0.3, 2.0)
        assert world.bumps == 1, "pressed against it for a second: one touch, one bump"
        assert world.x < -0.02, "the wall held it"
        _walk(world, -0.3, 1.0)
        _walk(world, 0.3, 2.0)
        assert world.bumps == 2
        # the ball rolled into the far wall touches it, and a touch of the ball's is no bump
        world.data.qpos[world._ball_qpos : world._ball_qpos + 3] = (1.0, 0.0, 0.05)
        world.data.qvel[world._ball_dof : world._ball_dof + 3] = (1.0, 0.0, 0.0)
        mujoco.mj_forward(world.model, world.data)
        touched = False
        for _ in range(round(1.0 / CONTROL_DT)):
            world.step()
            touched = touched or _ball_touches_a_wall(world)
        assert touched, "the ball never reached the wall, so this proves nothing"
        assert world.bumps == 2, "the ball is no duck"
    finally:
        world.close()
