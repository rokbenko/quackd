"""A rigid body arena that keeps the cartoon's rules.

What MuJoCo brings is contact: the ball rolls, a body pushes it, a kick is a velocity on a
sphere the floor slows down. What is kept from `sim2d`, on purpose, is everything a `.duck`
or a test might have come to rely on: the seeded spawn order (duck, then ball, from the same
distributions), the 0.3 s deadman that zeroes a velocity nobody re-sends, the kick that only
connects inside 0.30 m and a ±35° cone, and the scoop that succeeds 60 % of the time because
upstream's is open-loop.

What is not kept is the cartoon's person marker: nobody stands in this arena. The cartoon
draws its person from the RNG *after* the duck and the ball, so dropping it leaves every
seeded duck and ball exactly where it was in both worlds — a seed still lays out everything
the two arenas share. `follow-me`, which is a task about a person, is 2D-only for it.

The duck's base is a `Body`. Two exist: `Puppet`, a kinematic block that moves exactly as
the cartoon does and needs nothing downloaded, and `MicroduckBody`, upstream's real model
walking on upstream's real policy. The world does not know which; it hands the body a twist,
a head pose and the clock's dt, and asks where it ended up.
"""

from __future__ import annotations

import math
from typing import Any, Literal, Protocol

import mujoco
import numpy as np

from quackd.space.room import Room, nearest_free, spawn
from quackd.transport.base import TransportError
from quackd_microduck.sim3d.scene import (
    ARENA_HALF,
    BALL_PARK,
    BALL_R,
    DUCK_R,
    OFFSCREEN_PX,
    PUPPET_BODY_Z,
    PUPPET_HEAD_AHEAD,
    PUPPET_HEAD_Z,
    PUPPET_SIT_Z,
    PUPPET_XML,
    ROOM_GEOM_PREFIX,
    arena_xml,
)

CONTROL_DT = 0.02  # 50 Hz: the cadence of every policy the Microduck ships
PHYSICS_DT = 0.005  # upstream's timestep; four substeps per control tick
MAX_VX = 0.3
MAX_VY = 0.2
MAX_WZ = 1.5
DEADMAN_S = 0.3
KICK_RANGE_M = 0.30
KICK_CONE_DEG = 35.0
KICK_SPEED = 1.2
GRAB_RANGE_M = 0.18
GRAB_CONE_DEG = 30.0
GRAB_SUCCESS_P = 0.6  # open-loop scoop: deliberately unreliable, as in sim2d
HEAD_YAW_LIMIT = math.radians(60)
HEAD_PITCH_LIMIT = math.radians(35)

BODIES = ("puppet", "microduck")

Posture = Literal["standing", "sitting", "fallen"]


def _wrap(radians_: float) -> float:
    """An angle folded back into (-pi, pi]."""
    return math.atan2(math.sin(radians_), math.cos(radians_))


def _finite(what: str, *values: float) -> None:
    """A non-finite command must not reach the body.

    `np.clip` passes NaN straight through, and so does every comparison against it, so an
    unguarded NaN twist would arrive at the servos as a NaN target. The verb layer's pydantic
    bounds already reject one, but this is the public API a flock runner or a notebook calls
    directly, and the refusal belongs where the world is rather than in one caller's habits.
    """
    if not all(math.isfinite(v) for v in values):
        raise ValueError(f"{what} must be finite, got {values}")


class RenderError(TransportError):
    """No offscreen context, or a frame size the model's buffer cannot hold."""


class NotSupported(TransportError):
    """This body cannot do that, and quackd will not pretend otherwise."""


class Body(Protocol):
    """Whatever carries the duck's base: a kinematic puppet, or a robot and its policy."""

    name: str
    posture: Posture

    @property
    def include_xml(self) -> str:
        """Top-level MJCF spliced into the scene (a robot's own model), or ''."""
        ...

    @property
    def xml(self) -> str:
        """Worldbody MJCF, or '' when the body arrives through `include_xml`."""
        ...

    def mujoco_assets(self) -> dict[str, bytes]:
        """Files `from_xml_string` resolves includes and meshes from. May be empty."""
        ...

    def attach(self, model: Any, data: Any) -> None:
        """The scene is built; find your ids in it."""
        ...

    def reset(self, x: float, y: float, theta: float) -> None: ...

    def set_head(self, head: tuple[float, float]) -> None:
        """Aim the camera: `(yaw, pitch)` in radians, already clamped. Takes effect at once
        on a kinematic body and becomes a joint target on a body with a real neck."""
        ...

    walking: bool
    """Whether the last control tick actually produced a gait.

    A body may be sent a twist and legitimately not walk: `MicroduckBody` drops one below the
    gait floor, and a duck that is down is sent nothing at all. The world reports `policy`
    from this rather than from the twist it commanded, so the state does not say `walk` while
    the duck stands still.
    """

    def control(self, cmd: tuple[float, float, float], dt: float, rng: np.random.Generator) -> None:
        """One control step: apply a body-frame twist before the physics substeps."""
        ...

    def pose(self) -> tuple[float, float, float]:
        """`(x, y, theta)` of the base in the world."""
        ...

    def head_pose(self) -> tuple[float, float, float, float, float]:
        """`(x, y, z, yaw, pitch)` of the camera in the world, head pose included."""
        ...

    def sit_toggle(self) -> Posture:
        """Raise `NotSupported` if this body has no sit."""
        ...

    def enable(self) -> None:
        """Recover from a fall, however this body can."""
        ...

    def close(self) -> None:
        """Release whatever the body holds: inference sessions, handles, buffers."""

    def extras(self) -> dict[str, Any]:
        """Body-specific telemetry, merged into the state the pilot reads."""
        ...


class Puppet:
    """The stand-in: a mocap block placed where the cartoon's kinematics say it is.

    It integrates the command exactly as `sim2d` does, 2 % noise and wall clamp included,
    then writes the pose to the mocap body. That makes it a physics-free double of the
    duck: the ball feels it, the plumbing sees a real MuJoCo body, and nothing about it can
    fail for reasons a walking policy would. It needs no download and no GPU, which is why
    it is what the tests use.
    """

    name = "puppet"

    def __init__(self) -> None:
        self.posture: Posture = "standing"
        self.walking = False
        self.x = self.y = self.theta = 0.0
        self.head = (0.0, 0.0)
        self._data: Any = None
        self._mocap = -1
        self.room: Room | None = None
        """The room it walks in, whose walls hold it as the cartoon's hold its duck; a mocap body
        goes through any geom, so the walls are kept here rather than by contact."""
        self.bumps = 0

    @property
    def include_xml(self) -> str:
        return ""

    @property
    def xml(self) -> str:
        return PUPPET_XML

    def mujoco_assets(self) -> dict[str, bytes]:
        return {}

    def attach(self, model: Any, data: Any) -> None:
        body = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, "duck")
        self._mocap = int(model.body_mocapid[body])
        self._data = data

    def reset(self, x: float, y: float, theta: float) -> None:
        self.x, self.y, self.theta = x, y, theta
        self.posture = "standing"
        self.walking = False
        self._place()

    def set_head(self, head: tuple[float, float]) -> None:
        self.head = head

    def control(self, cmd: tuple[float, float, float], dt: float, rng: np.random.Generator) -> None:
        self.walking = self.posture == "standing" and any(cmd)
        if self.posture != "standing":
            return
        vx, vy, wz = cmd
        if not (vx or vy or wz):
            return
        noise = rng.normal(0.0, 0.02, size=3)
        vx *= 1 + noise[0]
        vy *= 1 + noise[1]
        wz *= 1 + noise[2]
        self.theta = _wrap(self.theta + wz * dt)
        x = self.x + (vx * math.cos(self.theta) - vy * math.sin(self.theta)) * dt
        y = self.y + (vx * math.sin(self.theta) + vy * math.cos(self.theta)) * dt
        if self.room is None:
            lim = ARENA_HALF - DUCK_R
            self.x = float(np.clip(x, -lim, lim))
            self.y = float(np.clip(y, -lim, lim))
        elif self.room.blocked(x, y, DUCK_R):
            self.bumps += 1  # held where it is, turned as it turned, as the cartoon's duck is
        else:
            self.x, self.y = float(x), float(y)
        self._place()

    def pose(self) -> tuple[float, float, float]:
        return self.x, self.y, self.theta

    def head_pose(self) -> tuple[float, float, float, float, float]:
        drop = 0.0 if self.posture == "standing" else PUPPET_BODY_Z - PUPPET_SIT_Z
        if self.posture == "fallen":
            drop = PUPPET_HEAD_Z - 0.05  # on its side: the camera is nearly on the floor
        return (
            self.x + PUPPET_HEAD_AHEAD * math.cos(self.theta),
            self.y + PUPPET_HEAD_AHEAD * math.sin(self.theta),
            PUPPET_HEAD_Z - drop,
            self.theta + self.head[0],
            self.head[1],
        )

    def sit_toggle(self) -> Posture:
        if self.posture != "fallen":
            self.posture = "sitting" if self.posture == "standing" else "standing"
            self._place()
        return self.posture

    def enable(self) -> None:
        if self.posture == "fallen":
            self.posture = "standing"
            self._place()

    def fall(self) -> None:
        """Knock it over (tests): the world reports `fallen` and refuses to walk."""
        self.posture = "fallen"
        self.walking = False
        self._place()

    def close(self) -> None:
        """Nothing to release: it is a mocap body in the world's own model."""

    def extras(self) -> dict[str, Any]:
        return {
            "assumptions": [
                "the puppet is kinematic: it moves exactly as the cartoon does, has no gait, "
                "and goes down only when a test knocks it over"
            ]
        }

    def _place(self) -> None:
        if self._data is None:
            return
        z = {"standing": PUPPET_BODY_Z, "sitting": PUPPET_SIT_Z, "fallen": 0.05}[self.posture]
        self._data.mocap_pos[self._mocap] = (self.x, self.y, z)
        if self.posture == "fallen":
            # rolled onto its side: yaw about z, then 90° about the body's x axis
            half = self.theta / 2
            yaw = (math.cos(half), 0.0, 0.0, math.sin(half))
            roll = (math.cos(math.pi / 4), math.sin(math.pi / 4), 0.0, 0.0)
            quat = np.zeros(4)
            mujoco.mju_mulQuat(quat, np.array(yaw), np.array(roll))
            self._data.mocap_quat[self._mocap] = quat
        else:
            self._data.mocap_quat[self._mocap] = (
                math.cos(self.theta / 2),
                0.0,
                0.0,
                math.sin(self.theta / 2),
            )


def make_body(name: str) -> Body:
    """`puppet` needs nothing; `microduck` fetches upstream's model and policies."""
    if name == "puppet":
        return Puppet()
    if name == "microduck":
        from quackd_microduck.sim3d.microduck import MicroduckBody

        return MicroduckBody()
    raise TransportError(f"unknown mujoco body {name!r}; choose one of {', '.join(BODIES)}")


class MujocoWorld:
    """One duck, one ball, four walls, and MuJoCo between them, or a room's walls and furniture
    (`room`), laid out as the cartoon lays them out."""

    def __init__(
        self, *, seed: int = 0, body: Body | str = "puppet", room: Room | None = None
    ) -> None:
        self.seed = seed
        self.rng = np.random.default_rng(seed)
        self.body: Body = make_body(body) if isinstance(body, str) else body
        self.room = room
        if room is not None:
            # where the cartoon puts them in the same room, from the room's own stream, so the
            # world's stream is drawn from for nothing but what it always drove
            placed, _draw = spawn(room, seed, duck_r=DUCK_R, ball_r=BALL_R)
            x, y, theta = placed.duck
            bx, by = placed.ball
        else:
            # duck then ball, in EXACTLY sim2d's RNG order and from its distributions: a seed
            # puts both where the cartoon puts them. The cartoon draws a person after these two
            # and this world has none, and because that draw came last its absence moves neither.
            x = float(self.rng.uniform(-0.3, 0.3))
            y = float(self.rng.uniform(-0.3, 0.3))
            theta = float(self.rng.uniform(-math.pi, math.pi))
            for _ in range(1000):
                bx, by = self.rng.uniform(-0.75, 0.75, size=2)
                if math.hypot(bx - x, by - y) >= 0.5:
                    break
        if isinstance(self.body, Puppet):
            self.body.room = room  # a physical body meets the walls as geoms instead
        xml = arena_xml(
            self.body.xml,
            ball=(float(bx), float(by)),
            timestep=PHYSICS_DT,
            include=self.body.include_xml,
            room=room,
        )
        assets = self.body.mujoco_assets()
        self.model = mujoco.MjModel.from_xml_string(xml, assets or None)
        self.data = mujoco.MjData(self.model)
        joint = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_JOINT, "ball_free")
        self._ball_qpos = int(self.model.jnt_qposadr[joint])
        self._ball_dof = int(self.model.jnt_dofadr[joint])
        self.ball_start = (float(bx), float(by))
        self.ball_present = True
        self.kick_origin: tuple[float, float] | None = None
        self.kicks = 0
        self.kicks_connected = 0
        self.holding = False
        self.head: tuple[float, float] = (0.0, 0.0)
        self.cmd: tuple[float, float, float] = (0.0, 0.0, 0.0)
        self.cmd_age = 0.0
        self.quacks: list[tuple[float, str, str | None]] = []
        self.t = 0.0
        self.steps = 0
        self._renderers: dict[int, Any] = {}
        self.closed = False
        # A room's geoms, and the two bodies a touch on one of them is no bump from: the floor's
        # and the ball's. A body that walks into a wall touches one with a body of its own.
        self._room_geoms = frozenset(
            g
            for g in range(self.model.ngeom)
            if (mujoco.mj_id2name(self.model, mujoco.mjtObj.mjOBJ_GEOM, g) or "").startswith(
                ROOM_GEOM_PREFIX
            )
        )
        self._not_a_bump = frozenset(
            {0, mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_BODY, "ball")}
        )
        self._touching = False
        self._contact_bumps = 0
        self.body.attach(self.model, self.data)
        self.body.set_head(self.head)
        self.body.reset(x, y, theta)
        mujoco.mj_forward(self.model, self.data)

    # ── what the transport reads ────────────────────────────────────────────────────

    @property
    def x(self) -> float:
        return self.body.pose()[0]

    @property
    def y(self) -> float:
        return self.body.pose()[1]

    @property
    def theta(self) -> float:
        return self.body.pose()[2]

    @property
    def posture(self) -> Posture:
        return self.body.posture

    @property
    def bumps(self) -> int:
        """Times the duck walked into a room's wall or furniture: the puppet's own count, which
        no contact can see on a mocap body, and each touch a physical body began."""
        return self._contact_bumps + int(getattr(self.body, "bumps", 0))

    @property
    def moving(self) -> bool:
        return any(abs(c) > 1e-6 for c in self.cmd)

    @property
    def policy(self) -> str:
        """What the body is actually doing, not what it was asked to do.

        `moving` is the commanded twist, and a body is free to decline it: below the gait
        floor `MicroduckBody` sends nothing and stands. Reporting the command here told the
        pilot `policy=walk` while the duck stood still, which is the one thing the gait floor
        exists to stop, and it reached the model while the body's own honest `extras["policy"]`
        did not.
        """
        if self.posture == "sitting":
            return "sit"
        return "walk" if self.moving and self.body.walking else "stand"

    @property
    def head_yaw(self) -> float:
        """Where the head is pointing, not where it was asked to point.

        On the real body the neck is a servo the policy drives, so it lags a `look` and may
        never quite arrive. Bearings already come from the achieved pose through
        `relative(camera=True)`, so reporting the command here made the state disagree with
        the camera it describes. On the puppet the two are the same number.
        """
        return _wrap(self.body.head_pose()[3] - self.theta)

    @property
    def ball_x(self) -> float:
        return float(self.data.qpos[self._ball_qpos])

    @property
    def ball_y(self) -> float:
        return float(self.data.qpos[self._ball_qpos + 1])

    @property
    def ball_displacement_m(self) -> float:
        if not self.ball_present:
            return 0.0
        return math.hypot(self.ball_x - self.ball_start[0], self.ball_y - self.ball_start[1])

    @property
    def last_kick_ball_moved_m(self) -> float | None:
        if self.kick_origin is None or not self.ball_present:
            return None
        return math.hypot(self.ball_x - self.kick_origin[0], self.ball_y - self.kick_origin[1])

    def head_pose(self) -> tuple[float, float, float, float, float]:
        return self.body.head_pose()

    def relative(self, x: float, y: float, *, camera: bool = False) -> tuple[float, float]:
        """(distance, bearing_rad) of a point from the front of the duck, where the camera
        is and where a kick or a scoop connects.

        The cartoon measures from the duck's centre because its camera is there. Here the
        camera is where a head is, ahead of the trunk, and the detector's distance is what
        `go_to` stops on, so the kick cone is measured from the same point or a duck that
        stopped "0.22 m away" would be 0.31 m from its own centre and every kick would miss.
        """
        hx, hy, _z, cam_yaw, _pitch = self.body.head_pose()
        dx, dy = x - hx, y - hy
        heading = cam_yaw if camera else self.theta
        bearing = math.atan2(dy, dx) - heading
        return math.hypot(dx, dy), math.atan2(math.sin(bearing), math.cos(bearing))

    # ── intents ─────────────────────────────────────────────────────────────────────

    def set_velocity(self, vx: float, vy: float, wz: float) -> None:
        _finite("a twist", vx, vy, wz)
        self.cmd = (
            float(np.clip(vx, -MAX_VX, MAX_VX)),
            float(np.clip(vy, -MAX_VY, MAX_VY)),
            float(np.clip(wz, -MAX_WZ, MAX_WZ)),
        )
        self.cmd_age = 0.0

    def stop(self) -> None:
        self.cmd = (0.0, 0.0, 0.0)
        self.cmd_age = 0.0

    def look(self, x: float, y: float, z: float = 0.0) -> bool:
        """Point the camera at a trunk-frame point. Returns True if clamped."""
        _finite("a gaze target", x, y, z)
        yaw = math.atan2(y, x)
        pitch = math.atan2(z, math.hypot(x, y))
        clamped = abs(yaw) > HEAD_YAW_LIMIT or abs(pitch) > HEAD_PITCH_LIMIT
        self.head = (
            float(np.clip(yaw, -HEAD_YAW_LIMIT, HEAD_YAW_LIMIT)),
            float(np.clip(pitch, -HEAD_PITCH_LIMIT, HEAD_PITCH_LIMIT)),
        )
        self.body.set_head(self.head)
        return clamped

    def kick(self, leg: str = "right") -> bool:
        """Kick along the heading. Connects only if the ball is close and ahead."""
        self.kicks += 1
        if not self.ball_present or self.posture != "standing":
            return False
        dist, bearing = self.relative(self.ball_x, self.ball_y)
        self.kick_origin = (self.ball_x, self.ball_y)  # a miss moves nothing
        if dist > KICK_RANGE_M or abs(math.degrees(bearing)) > KICK_CONE_DEG:
            return False
        self.kicks_connected += 1
        skew = math.radians(self.rng.normal(0.0, 6.0)) + (0.05 if leg == "left" else -0.05)
        ang = self.theta + skew
        self._push_ball(KICK_SPEED * math.cos(ang), KICK_SPEED * math.sin(ang))
        return True

    def ground_pick(self) -> bool:
        if self.holding or not self.ball_present or self.posture != "standing":
            return False
        dist, bearing = self.relative(self.ball_x, self.ball_y)
        if dist > GRAB_RANGE_M or abs(math.degrees(bearing)) > GRAB_CONE_DEG:
            return False
        if self.rng.random() > GRAB_SUCCESS_P:
            # the scoop nudges the ball away — realistic and annoying
            self._push_ball(0.2 * math.cos(self.theta + 0.6), 0.2 * math.sin(self.theta + 0.6))
            return False
        self.holding = True
        self.ball_present = False
        self._park_ball()
        return True

    def sit_toggle(self) -> Posture:
        posture = self.body.sit_toggle()
        self.stop()
        return posture

    def enable(self) -> None:
        """`stand_up`: put it back on its feet, stopped, and not inside anything.

        The body knows how to stand itself up but not what it would be standing in. A duck
        goes down while walking, so it comes to rest wherever it slid to: against a wall or on
        top of the ball. And `stop()` first, because the twist that
        put it down is still on the books until the deadman notices.
        """
        if self.posture != "fallen":
            return
        self.stop()
        self.body.enable()
        x, y, theta = self.body.pose()
        sx, sy = self._standing_spot(x, y)
        if (sx, sy) != (x, y):
            self.body.reset(sx, sy, theta)

    def _standing_spot(self, x: float, y: float) -> tuple[float, float]:
        """`(x, y)` pushed out of anything solid and back inside the walls, a room's included."""
        lim = ARENA_HALF - DUCK_R
        room = self.room

        def inside(x: float, y: float) -> tuple[float, float]:
            if room is not None:
                return nearest_free(room, x, y, DUCK_R)
            return min(max(x, -lim), lim), min(max(y, -lim), lim)

        x, y = inside(x, y)
        obstacles = [(self.ball_x, self.ball_y, BALL_R)] if self.ball_present else []
        for ox, oy, r in obstacles:
            dx, dy = x - ox, y - oy
            dist = math.hypot(dx, dy)
            need = DUCK_R + r
            if dist >= need:
                continue
            if dist < 1e-6:  # exactly on top of it: any direction will do, pick a fixed one
                dx, dy, dist = 1.0, 0.0, 1.0
            x, y = inside(ox + dx / dist * need, oy + dy / dist * need)
        return x, y

    def sound(self, tag: str, text: str | None) -> None:
        self.quacks.append((self.t, tag, text))

    # ── physics ─────────────────────────────────────────────────────────────────────

    def step(self, dt: float = CONTROL_DT) -> None:
        self.cmd_age += dt
        if self.cmd_age > DEADMAN_S and self.moving:
            self.cmd = (0.0, 0.0, 0.0)  # upstream's deadman: stop is not limp
        self.body.control(self.cmd, dt, self.rng)
        for _ in range(max(1, round(dt / self.model.opt.timestep))):
            mujoco.mj_step(self.model, self.data)
        self._check_diverged()
        if self._room_geoms:
            touching = self._touching_room()
            if touching and not self._touching:
                self._contact_bumps += 1
            self._touching = touching
        if not self.ball_present:
            self._park_ball()
        self.t += dt
        self.steps += 1

    def _touching_room(self) -> bool:
        """Whether a body of the duck's touches a room's wall or furniture now."""
        for i in range(self.data.ncon):
            contact = self.data.contact[i]
            one, other = int(contact.geom1), int(contact.geom2)
            if (one in self._room_geoms) == (other in self._room_geoms):
                continue
            theirs = other if one in self._room_geoms else one
            if int(self.model.geom_bodyid[theirs]) not in self._not_a_bump:
                return True
        return False

    def _check_diverged(self) -> None:
        """Refuse to keep simulating a state MuJoCo has already given up on.

        MuJoCo does not raise when the physics goes non-finite. `mj_checkPos`, `mj_checkVel`
        and `mj_checkAcc` log a warning and call `mj_resetData`, which silently teleports
        every body to `qpos0` and sets the clock back to zero. Without this the run would
        carry on reporting poses and distances from a world that had quietly restarted, which
        is worse than a crash: the transcript would look ordinary and be fiction.
        """
        for flag in (
            mujoco.mjtWarning.mjWARN_BADQPOS,
            mujoco.mjtWarning.mjWARN_BADQVEL,
            mujoco.mjtWarning.mjWARN_BADQACC,
        ):
            if self.data.warning[flag].number:
                raise TransportError(
                    f"the physics diverged at t={self.t:.2f}s ({flag.name}) and MuJoCo reset "
                    "the world; nothing after this point would be true"
                )

    def _push_ball(self, vx: float, vy: float) -> None:
        self.data.qvel[self._ball_dof : self._ball_dof + 3] = (vx, vy, 0.0)

    def _park_ball(self) -> None:
        self.data.qpos[self._ball_qpos : self._ball_qpos + 3] = BALL_PARK
        self.data.qvel[self._ball_dof : self._ball_dof + 6] = 0.0
        # a frame taken before the next step must not still show the ball in the beak's reach
        mujoco.mj_forward(self.model, self.data)

    # ── telemetry ───────────────────────────────────────────────────────────────────

    def snapshot(self) -> dict[str, Any]:
        """The same keys `sim2d` reports, so verbs and tests read both worlds alike, plus
        whatever the body wants the pilot to know about itself."""
        snap: dict[str, Any] = {
            "sim_time": round(self.t, 3),
            "ball": (
                {"x": round(self.ball_x, 3), "y": round(self.ball_y, 3), "present": True}
                if self.ball_present
                else {"present": False}
            ),
            "ball_displacement_m": round(self.ball_displacement_m, 3),
            "last_kick_ball_moved_m": (
                None
                if self.last_kick_ball_moved_m is None
                else round(self.last_kick_ball_moved_m, 3)
            ),
            "kicks": self.kicks,
            "quacks": len(self.quacks),
            "head_yaw_deg": round(math.degrees(self.head[0]), 1),
            "physics": self.body.name,
        }
        if self.room is not None:
            snap["room"] = self.room.name
        snap.update(self.body.extras())
        return snap

    # ── rendering ───────────────────────────────────────────────────────────────────

    def renderer(self, size: int) -> Any:
        """One offscreen renderer per frame size, kept for the world's lifetime: creating
        one is a GL context, which costs hundreds of milliseconds; rendering costs a few."""
        if self.closed:
            raise RenderError("this world is closed")
        if not 0 < size <= OFFSCREEN_PX:
            raise RenderError(
                f"a {size} px frame does not fit the model's offscreen buffer "
                f"({OFFSCREEN_PX} px, `sim3d.scene.OFFSCREEN_PX`)"
            )
        if size not in self._renderers:
            try:
                self._renderers[size] = mujoco.Renderer(self.model, height=size, width=size)
            except Exception as e:
                # A bare OpenGL traceback is the least useful thing to hand someone on a
                # server. docs/faq.md promises this sentence; say it here so it is true.
                raise RenderError(
                    f"no OpenGL context for offscreen rendering ({type(e).__name__}: {e}). "
                    "On a headless Linux box install libosmesa6 and set MUJOCO_GL=osmesa, "
                    "or MUJOCO_GL=egl where there is a GPU"
                ) from e
        return self._renderers[size]

    def close(self) -> None:
        for renderer in self._renderers.values():
            renderer.close()
        self._renderers.clear()
        self.body.close()
        self.closed = True
