"""The arm's model inside quackd's scene, and the maps between LeRobot's units and the model's.

`load()` takes either the maker's model, fetched by `assets.py`, or the stand-in arm that CI
uses in its place, and sets it in a scene of quackd's own: the physics options, a table at the
height of the arm's base, lights, three camera mounts and a few objects laid out from a seed.
The arm itself is drawn in greys, however its model colours it (`_grey`), so that the colour
detector finds nothing on the arm and a blob it finds is something on the table.
Upstream's model is the arm and nothing around it (`upstream_api.MODEL_HAS_NO_SCENE`), so every
one of those is quackd's, and each is laid out from the arm's own geometry and the datasheet's
reach rather than from numbers typed in here.

The model's gripper needs one repair to hold anything. Its fixed finger is one printed part
with the wrist housing, and MuJoCo collides a mesh as its convex hull, so the hull fills the
opening the other finger closes into and throws a pen out of the grasp
(`upstream_api.FINGER_MESHES`). The two finger meshes stop colliding, and boxes cut from their
own vertices when the model loads collide in their place: one for each finger and one for the
palm between them. The stand-in's fingers are already boxes under the same names, so the
contact settings that follow reach both models alike.

What a reading means on the model is here too. LeRobot's five body joints read in degrees
and its gripper from 0 to 100; the model's joints are hinges in radians. Each body joint maps
as `q = s * deg2rad(deg - z)`, with s = +1 and z = 0 on the SO-101 (`upstream_api.JOINT_SIGN`,
`upstream_api.JOINT_ZERO`), and the gripper maps linearly over the model's hinge with its
closed end found from the model rather than assumed (`upstream_api.GRIPPER_MAP`).

And so is the calibration a real arm would read. `calibration_path()` walks LeRobot's own
search for a file without importing LeRobot, `read_calibration()` reads one as LeRobot does,
and `generic_calibration()` builds one from the model's own ranges for a run that names no arm,
so the real backend's code finds travel to read either way.

Nothing here imports `mujoco` when the module is imported: `load()` does, when it is called.
"""

from __future__ import annotations

import json
import math
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal

import numpy as np

from quackd.adapters.base import AdapterError
from quackd.transport.base import TransportError
from quackd_lerobot import REACH
from quackd_lerobot import upstream_api as lr
from quackd_lerobot.arms import SO_ENCODER_TICKS
from quackd_lerobot.real import PORT_SHAPE
from quackd_lerobot.sim import upstream_api as up
from quackd_lerobot.verbs import GRIPPER_CLOSED, GRIPPER_OPEN, JOINTS

LABEL = "lerobot mujoco:"
"""How every refusal from the simulator starts, as `lerobot real:` starts the real backend's."""

# ── the physics ─────────────────────────────────────────────────────────────────────────

TIMESTEP_S = 0.002
"""MuJoCo's own default timestep. Both models step steadily at it, and every contact setting
below keeps its time constant above the two timesteps MuJoCo's refsafe flag enforces."""
IMPRATIO = 10.0
"""MuJoCo's documentation says an impratio above 1 makes friction harder than the normal force,
which keeps a held object from slipping without raising any friction coefficient. It describes
that for elliptic cones, which is why the scene uses them rather than the default pyramid.

It and NOSLIP_ITERATIONS are what the grasp sweep (`tests/test_lerobot_sim_model.py`) guards.
With both at MuJoCo's defaults the stiff pads below let the cube slide out of the fingers on
every seed, and with either one alone the sweep still lifts it on every seed."""
NOSLIP_ITERATIONS = 3
"""Passes of MuJoCo's noslip solver, which the documentation describes as a post-processing
step that suppresses the slip the soft contact model otherwise allows. The default of 0 turns it
off. A few passes are what quackd starts from, and IMPRATIO says what the grasp sweep showed of
the two. Of the pair this is the one a longer hold needs: without it the sweep still lifts the
cube, but held a few seconds past the lift the cube creeps down the pads, where with it the
cube stays put."""

MUJOCO_FRICTION = (1.0, 0.005, 0.0001)
"""MuJoCo's default geom friction: sliding, torsional and rolling. Nobody has measured a printed
finger against a pen, so the scene starts from the documented defaults and names them, and the
nightly grasp sweep is what would move them."""
PAD_CONDIM = 4
"""A finger pad's contact dimensions: sliding plus torsional friction, so an object held
between two pads resists spinning about the line between them, not only sliding along it.
At 3 the grasp sweep lifts its cube just as well, so the sweep does not guard it."""
PAD_SOLREF = (0.01, 1.0)
"""A pad's contact time constant and damping ratio: half MuJoCo's default time constant, so the
pad is stiffer and a squeezed object sinks less far into it, and still five timesteps, above
the two that MuJoCo's refsafe flag takes as the floor. A damping ratio of 1 is critical
damping, the default.

The grasp sweep lifts the cube at MuJoCo's default time constant too, so it does not guard
this. What the stiffness changes is how far the gripper closes into what it holds: at the
default it settles about at `real.HOLD_MIN`, the reading below which quackd takes a close to
be on nothing, and with every contact setting here at MuJoCo's default it settles below it on
every seed. The sweep prints quackd's verdict beside the world's and never asserts it, since
whether a real gripper settles where this one does is a question for the bench."""
OBJECT_CONDIM = 6
"""An object's contact dimensions. Six adds rolling friction, the third coefficient, which does
nothing below it: without it a capsule touched on the table rolls until something stops it."""
PAD_PRIORITY = 1
"""A pad's contact priority, above every other geom's, which MuJoCo leaves at 0. Where two geoms
of equal priority touch, MuJoCo mixes their settings: the larger condim and friction, and
solref averaged between the two. A pad would then meet an object at the object's condim, and
at a time constant halfway between the object's and its own. The higher priority makes a pad's
own settings the ones anything touching it meets, which is what PAD_CONDIM and PAD_SOLREF are
for. At 0 the grasp sweep still lifts the cube, and the gripper settles a little further shut
on it, as PAD_SOLREF says a softer pad does."""

# ── the scene ───────────────────────────────────────────────────────────────────────────

FIXED_PAD = "quackd_fixed_finger"
MOVING_PAD = "quackd_moving_finger"
PALM_PAD = "quackd_palm"
"""The three boxes the gripper collides through, on either model."""
FINGER_PAIR = "quackd_fingers"
TABLE = "quackd_table"
MOUNTS = ("front", "top", "wrist")
"""The cameras the scene carries. `front` and `top` are quackd's documented views of the
table, not the extrinsics of anybody's real cameras; `wrist` hangs from the model's wrist
camera body (`upstream_api.WRIST_CAMERA_POSE`)."""

TABLE_REACHES = 1.25
"""The table's half width, in reaches, around the arm's pan axis: past anywhere it can touch."""
TABLE_THICKNESS_M = 0.02
TABLE_RGBA = (0.55, 0.56, 0.58, 1.0)
"""A grey table: no colour a detector looks for."""
AMBIENT = 0.6
"""The light every face meets whichever way it faces, as a share of full light: most of it, so
that a side turned from every lamp still shows its colour bright enough for the colour
detector's floor (`perception.color_blob.HSVRange.v_lo`)."""
HEADLIGHT = 0.15
"""What the camera's own lamp adds to a face turned full on to it: enough to tell one face of
a box from the next."""
LAMP_DIRECTIONS = ((-0.3, 0.2, -1.0), (0.3, -0.2, -1.0))
"""Two lamps shining down from above, one from either side, so no face is left in the ambient
alone. How bright they are is what the ambient and the headlight leave (`_lamp_diffuse`)."""
LUMA = (0.299, 0.587, 0.114)
"""How much red, green and blue each count for in the grey an arm's colour becomes (`_grey`):
the weights OpenCV's own conversion to grey uses."""

VIEW_HFOV_DEG = 90.0
"""The horizontal field of view the default views are laid out for: what quackd's colour
detector assumes when nothing says otherwise (`perception.color_blob.DEFAULT_FOV_DEG`)."""
VIEW_ASPECT = 4 / 3
"""The frame shape the default views are laid out for: a webcam's 640 by 480."""
FRONT_ELEVATION_DEG = 30.0
"""How far above the table the front view looks down on it from."""

PLACE_NEAR = 0.35
PLACE_FAR = 0.7
"""The ring objects are laid out in, as shares of the gripper's reach across the table top
from the pan axis: out of the base's way and short of the arm fully stretched."""
PLACE_SPREAD_DEG = 45.0
"""How far either side of straight ahead an object may be laid out."""
PLACE_GAP_M = 0.03
"""The least room between two objects, so no two start touching."""
PLACE_TRIES = 1000

TIP_SHARE = 0.25
"""The distal share of a finger, from its hinge to its tip, whose vertices say where its inner
face is: the fingertip, where a thin object between the fingers is held."""
PAD_EPS_M = 0.001
"""How much further into the opening than the fingertip a vertex must reach to be the palm
rather than the finger's own surface."""


class ModelError(TransportError):
    """The arm's model could not be loaded or set in the scene."""


STANDIN_LABEL = "the stand-in arm"
"""What a model given as MJCF text is called in a refusal. The stand-in is the only one."""


def _remedy(label: str) -> str:
    """What to do about a model the simulator cannot use: a checkout's is its owner's to swap
    for the pinned one, and the pinned model and the stand-in are quackd's to fix."""
    if label == STANDIN_LABEL:
        return "The stand-in is quackd's own, so report it."
    return (
        "If QUACKD_LEROBOT_SIM_ASSETS points at a checkout of your own, unset it to use the "
        "pinned model, and otherwise report it."
    )


class CalibrationError(TransportError):
    """A calibration file that LeRobot would refuse or that holds a null, or none where one was
    named."""


# ── objects ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SceneObject:
    """Something on the table: a box or a capsule lying down, free to be pushed or picked up."""

    name: str
    shape: Literal["box", "capsule"]
    size: tuple[float, ...]
    """MuJoCo's size for the shape: a box's three half sizes, a capsule's radius and half
    length."""
    mass_kg: float | None
    """None for the mass MuJoCo gives a solid of that size at its own default density, which is
    what a scene that names no mass gets."""
    rgba: tuple[float, float, float, float]

    @property
    def rest_height(self) -> float:
        """How high its centre sits above the table when it lies on it."""
        return self.size[2] if self.shape == "box" else self.size[0]

    @property
    def footprint(self) -> float:
        """The radius of the circle it covers on the table, whichever way it lies."""
        if self.shape == "box":
            return math.hypot(self.size[0], self.size[1])
        return self.size[0] + self.size[1]


CUBE = SceneObject("cube", "box", (0.0125, 0.0125, 0.0125), 0.02, (0.8, 0.15, 0.12, 1.0))
"""A 25 mm cube, about the size of a toy block, which either model's gripper opens past."""
PEN = SceneObject("pen", "capsule", (0.0045, 0.065), 0.01, (0.1, 0.1, 0.12, 1.0))
"""A 9 mm by 14 cm capsule, the size of a ballpoint pen, and black, as most are. Neither it
nor the cube is a colour the detector looks for (`perception.color_blob.DEFAULT_TARGETS`): a
blue pen read as a person on every run."""
DEFAULT_OBJECTS = (CUBE, PEN)

ON_TABLE = "table"
BETWEEN_JAWS = "jaws"
PLACES = (ON_TABLE, BETWEEN_JAWS)
"""Where a scene can put an object: on the table at a spot its seed picks, as the default
objects are laid, or on the table between the gripper's fingers as the arm starts
(`world.ArmWorld.place_between_jaws`)."""
SIZES = MappingProxyType({"box": 3, "capsule": 2})
"""How many numbers MuJoCo's size takes for each shape an object can have."""
SHAPE_RGBA = MappingProxyType({"box": CUBE.rgba, "capsule": PEN.rgba})
"""The colour of an object a scene gives none: the default object of the same shape's, which
no target of the colour detector matches. A scene object that should be seen names a colour
the detector looks for (`perception.color_blob.DEFAULT_TARGETS`)."""


@dataclass(frozen=True)
class Scene:
    """The objects a scene lays on the table, in place of the default ones."""

    objects: tuple[SceneObject, ...]
    jaws: str | None = None
    """The one object laid between the jaws as the arm starts, or None."""


def parse_scene(scene: Sequence[Mapping[str, Any]]) -> Scene:
    """A scene as `quackd preflight` hands one over from a task's sidecar: each object's `name`,
    `kind` (box or capsule), `size` as MuJoCo gives it, `place` (table or jaws), and optionally
    its `mass_kg` and `rgba`. Refused whole, saying what is wrong, rather than laid out in part:
    a rehearsal on a table other than the one its file describes would be judged against the
    wrong world."""
    objects: list[SceneObject] = []
    jaws: list[str] = []
    for raw in scene:
        name = str(raw.get("name") or "")
        given = raw.get("kind")
        if not name or given not in SIZES:
            raise AdapterError(
                f"{LABEL} a scene object needs a name and a kind, box or capsule, and "
                f"{dict(raw)!r} gives {'no name' if not name else f'the kind {given!r}'}."
            )
        kind: Literal["box", "capsule"] = "box" if given == "box" else "capsule"
        size = _numbers(raw.get("size"))
        if size is None or len(size) != SIZES[kind] or not all(v > 0 for v in size):
            raise AdapterError(
                f"{LABEL} the scene's {name} is a {kind}, whose size is {SIZES[kind]} positive "
                f"numbers in metres, and it gives {raw.get('size')!r}."
            )
        place = raw.get("place") or ON_TABLE
        if place not in PLACES:
            raise AdapterError(
                f"{LABEL} the scene puts {name} at {place!r}, and an object goes on the "
                f"{ON_TABLE} or between the {BETWEEN_JAWS}."
            )
        if place == BETWEEN_JAWS:
            jaws.append(name)
        mass = None if raw.get("mass_kg") is None else _numbers([raw.get("mass_kg")])
        if raw.get("mass_kg") is not None and (mass is None or not mass[0] > 0):
            raise AdapterError(
                f"{LABEL} the scene's {name} weighs {raw.get('mass_kg')!r}, and a mass is a "
                "positive number of kilograms."
            )
        rgba = None if raw.get("rgba") is None else _numbers(raw.get("rgba"))
        if raw.get("rgba") is not None and (
            rgba is None or len(rgba) != 4 or not all(0.0 <= v <= 1.0 for v in rgba)
        ):
            raise AdapterError(
                f"{LABEL} the scene's {name} has the rgba {raw.get('rgba')!r}, and a colour is "
                "four numbers from 0 to 1."
            )
        objects.append(
            SceneObject(
                name,
                kind,
                size,
                None if mass is None else mass[0],
                SHAPE_RGBA[kind] if rgba is None else (rgba[0], rgba[1], rgba[2], rgba[3]),
            )
        )
    names = [obj.name for obj in objects]
    if not objects or len(set(names)) != len(names):
        raise AdapterError(
            f"{LABEL} a scene lays out one or more objects, each under a name of its own, and "
            f"this one names {', '.join(names) or 'none'}."
        )
    if len(jaws) > 1:
        raise AdapterError(
            f"{LABEL} the scene puts {' and '.join(jaws)} between the jaws, and there is room "
            "there for one."
        )
    return Scene(tuple(objects), jaws[0] if jaws else None)


def _numbers(value: Any) -> tuple[float, ...] | None:
    """A list of finite numbers as floats, or None for anything else: a string, a bool, a
    number standing alone, a NaN."""
    if not isinstance(value, (list, tuple)):
        return None
    out: list[float] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            return None
        if not math.isfinite(float(item)):
            return None
        out.append(float(item))
    return tuple(out)


@dataclass(frozen=True)
class Workspace:
    """Where on the table the arm works, from its own geometry and the datasheet's reach."""

    table_top: float
    """The height of the table's surface: the lowest point of the arm's base."""
    center: tuple[float, float]
    """The pan axis, where it meets the table."""
    reach: float
    """How far across the table top the gripper gets from the pan axis."""


def object_poses(
    workspace: Workspace, objects: Sequence[SceneObject], seed: int
) -> tuple[tuple[float, float, float, float], ...]:
    """Where each object starts, as `(x, y, z, yaw)`, the same for the same seed.

    Each lies on the table inside the ring `PLACE_NEAR` to `PLACE_FAR` of the reach, within
    `PLACE_SPREAD_DEG` of straight ahead (+x, the way both models face at zero), and no two
    closer than `PLACE_GAP_M`."""
    rng = np.random.default_rng(seed)
    placed: list[tuple[float, float, float, float]] = []
    for obj in objects:
        for _ in range(PLACE_TRIES):
            x, y = table_spot(workspace, rng)
            if all(
                math.hypot(x - px, y - py) >= obj.footprint + other.footprint + PLACE_GAP_M
                for (px, py, _, _), other in zip(placed, objects, strict=False)
            ):
                break
        else:
            raise ModelError(
                f"{LABEL} there is no room in reach for {obj.name} beside the other objects. "
                "Ask for fewer or smaller objects."
            )
        yaw = float(rng.uniform(0.0, math.pi))
        placed.append((x, y, workspace.table_top + obj.rest_height, yaw))
    return tuple(placed)


def table_spot(workspace: Workspace, rng: np.random.Generator) -> tuple[float, float]:
    """One spot on the table in the ring objects are laid out in, drawn from `rng`: its
    distance from the pan axis, then its bearing, in that order, so a seed lays a table out the
    same way whoever draws from it."""
    near, far = PLACE_NEAR * workspace.reach, PLACE_FAR * workspace.reach
    spread = math.radians(PLACE_SPREAD_DEG)
    cx, cy = workspace.center
    r = float(rng.uniform(near, far))
    bearing = float(rng.uniform(-spread, spread))
    return cx + r * math.cos(bearing), cy + r * math.sin(bearing)


# ── the maps ────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class JointMap:
    """One body joint: where it lives in the model, its hard stops, and LeRobot's degrees."""

    name: str
    qpos: int
    dof: int
    actuator: int
    lo: float
    hi: float
    """The MJCF range in radians: the hard stops, which nothing in the simulator moves past."""
    sign: float = 1.0
    """`upstream_api.JOINT_SIGN`: +1, as LeRobot's own kinematics helper assumes."""
    zero_deg: float = 0.0
    """`upstream_api.JOINT_ZERO`: 0, for the same reason."""

    def to_model(self, deg: float) -> float:
        """`q = s * deg2rad(deg - z)`: a LeRobot reading in degrees, as the model's radians."""
        return self.sign * math.radians(deg - self.zero_deg)

    def to_lerobot(self, q: float) -> float:
        """The inverse: radians on the model, as the degrees LeRobot would read."""
        return math.degrees(q) * self.sign + self.zero_deg

    @property
    def stops(self) -> tuple[float, float]:
        """The hard stops in LeRobot's degrees, low end first."""
        a, b = self.to_lerobot(self.lo), self.to_lerobot(self.hi)
        return (min(a, b), max(a, b))


@dataclass(frozen=True)
class GripperMap:
    """The gripper: LeRobot's 0..100 laid linearly over the model's hinge.

    Which end of the hinge is closed was found from the model (`closed`), and LeRobot's 0 is
    closed and 100 open, as quackd assumes of a real arm (`GRIPPER_OPEN_VALUE`). Both ways the
    value is bounded to 0..100, as LeRobot bounds its RANGE_0_100 motors
    (`upstream_api.GRIPPER_MAP`)."""

    name: str
    qpos: int
    dof: int
    actuator: int
    lo: float
    hi: float
    closed: float
    """The end of the hinge's range where the fingers are nearest each other."""
    open: float

    def to_model(self, value: float) -> float:
        share = (_bounded(value) - GRIPPER_CLOSED) / (GRIPPER_OPEN - GRIPPER_CLOSED)
        return self.closed + (self.open - self.closed) * share

    def to_lerobot(self, q: float) -> float:
        share = (q - self.closed) / (self.open - self.closed)
        return _bounded(GRIPPER_CLOSED + (GRIPPER_OPEN - GRIPPER_CLOSED) * share)

    @property
    def stops(self) -> tuple[float, float]:
        return (GRIPPER_CLOSED, GRIPPER_OPEN)


def _bounded(value: float) -> float:
    return min(max(float(value), GRIPPER_CLOSED), GRIPPER_OPEN)


JointOrGripper = JointMap | GripperMap


@dataclass(frozen=True)
class ArmModel:
    """A compiled model in quackd's scene, with everything the world needs to find in it.

    A world built on it steps a copy, because switching a joint's torque off edits that
    actuator's gains in place, so one loaded model can start any number of worlds as it
    loaded."""

    model: Any
    label: str
    """Where the model came from: a file's path, or the stand-in."""
    joints: Mapping[str, JointOrGripper]
    """All six, in LeRobot's order, the gripper last."""
    workspace: Workspace
    objects: tuple[SceneObject, ...]
    object_bodies: tuple[int, ...]
    object_qpos: tuple[int, ...]
    object_dofs: tuple[int, ...]
    object_geoms: tuple[frozenset[int], ...]
    table: int
    fixed_pad: int
    moving_pad: int
    palm_pad: int
    gripper_geoms: frozenset[int]
    """Every geom on the gripper that collides: the pads and anything else on the hand."""

    @property
    def gripper(self) -> GripperMap:
        g = self.joints[JOINTS[-1]]
        assert isinstance(g, GripperMap)
        return g


# ── loading ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class _Pad:
    """A pad box in its body's frame, before the model it lives in is compiled."""

    body: int
    center: np.ndarray
    half: np.ndarray


def load(
    source: str | Path,
    *,
    seed: int,
    objects: Sequence[SceneObject] = DEFAULT_OBJECTS,
) -> ArmModel:
    """The arm in quackd's scene, from a model file or from MJCF text such as the stand-in's.

    A file's meshes resolve through the file's own meshdir, beside it, as upstream lays them
    out. The model is compiled once to measure it, the scene is laid over it from what was
    measured, and it is compiled again."""
    import mujoco

    if isinstance(source, Path):
        label = str(source)
        try:
            spec = mujoco.MjSpec.from_file(str(source))
        except Exception as e:  # mujoco raises ValueError, but a bad path is anything
            raise ModelError(
                f"{LABEL} could not read the arm's model at {source} ({e}). Delete the cache "
                "directory it is in to have it fetched again, or point "
                "QUACKD_LEROBOT_SIM_ASSETS at a checkout that loads."
            ) from e
    else:
        label = STANDIN_LABEL
        try:
            spec = mujoco.MjSpec.from_string(source)
        except Exception as e:
            raise ModelError(f"{LABEL} could not read {label} ({e}). {_remedy(label)}") from e
    _grey(spec)
    first = _compile(spec, label)
    data = mujoco.MjData(first)
    _require(first, label)
    mujoco.mj_kinematics(first, data)
    jaw = int(first.jnt_bodyid[first.joint(JOINTS[-1]).id])
    hand = int(first.body_parentid[jaw])
    pads, pad_geoms = _pads(spec, first, data, hand, jaw, label)
    workspace = _workspace(first, data, label)
    _options(spec)
    _table_and_lights(spec, workspace)
    names = {first.body(i).name for i in range(first.nbody)}
    for obj in objects:
        if obj.name in names:
            raise ModelError(
                f"{LABEL} an object may not be called {obj.name!r}: the arm has a body by that "
                "name. Call it something else."
            )
    _objects(spec, objects, object_poses(workspace, objects, seed))
    _contacts(spec, pad_geoms)
    _gripper_force(spec, label)
    _views(spec, workspace)
    _wrist_view(spec, first, data, pads, hand, jaw)
    model = _compile(spec, label)
    return _arm(model, label, workspace, tuple(objects), hand)


def _grey(spec: Any) -> None:
    """Take the hue out of every material and geom the arm's model comes with, keeping how light
    each one is. The colour detector's every target is a saturated hue
    (`perception.color_blob.DEFAULT_TARGETS`), and the SO-101's printed parts are drawn in a
    yellow it reads as a duck, so a view with the arm in it would never be empty. A grey has no
    hue for any target to match. Run before the scene is laid over the model, so the table and
    the objects keep their own colours."""
    red, green, blue = LUMA
    for item in (*spec.materials, *spec.geoms):
        r, g, b, a = (float(x) for x in item.rgba)
        light = red * r + green * g + blue * b
        item.rgba = [light, light, light, a]


def _compile(spec: Any, label: str) -> Any:
    try:
        return spec.compile()
    except Exception as e:
        raise ModelError(f"{LABEL} MuJoCo could not compile {label} ({e}). {_remedy(label)}") from e


def _require(model: Any, label: str) -> None:
    """Every joint and actuator LeRobot names, each joint a hinge between stops and driven by
    its own actuator, and a wrist camera body, or a refusal naming what is missing."""
    import mujoco

    missing = []
    for name in JOINTS:
        if mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, name) < 0:
            missing.append(f"joint {name}")
        if mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_ACTUATOR, name) < 0:
            missing.append(f"actuator {name}")
    if mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, up.WRIST_CAMERA_BODY) < 0:
        missing.append(f"body {up.WRIST_CAMERA_BODY}")
    if missing:
        raise ModelError(
            f"{LABEL} {label} has no {', '.join(missing)}, and the simulator drives an arm whose "
            f"joints and actuators carry LeRobot's motor names. {_remedy(label)}"
        )
    for name in JOINTS:
        j = model.joint(name).id
        if model.jnt_type[j] != mujoco.mjtJoint.mjJNT_HINGE or not model.jnt_limited[j]:
            raise ModelError(
                f"{LABEL} {label}'s {name} is not a hinge with a range, and the simulator maps "
                f"a reading onto a hinge between its stops. {_remedy(label)}"
            )
        if model.actuator_trnid[model.actuator(name).id, 0] != j:
            raise ModelError(
                f"{LABEL} {label}'s actuator {name} does not drive the joint {name}. "
                f"{_remedy(label)}"
            )


def _mat(quat: Any) -> np.ndarray:
    import mujoco

    out = np.zeros(9)
    mujoco.mju_quat2Mat(out, np.asarray(quat, dtype=float))
    return out.reshape(3, 3)


def _quat(rot: np.ndarray) -> np.ndarray:
    import mujoco

    out = np.zeros(4)
    mujoco.mju_mat2Quat(out, np.ascontiguousarray(rot, dtype=float).reshape(9))
    return out


def _geom_points(model: Any, g: int) -> np.ndarray:
    """A geom's vertices, or its bounding box's corners for a primitive, in its body's frame."""
    import mujoco

    if model.geom_type[g] == mujoco.mjtGeom.mjGEOM_MESH:
        mesh = model.geom_dataid[g]
        start, count = model.mesh_vertadr[mesh], model.mesh_vertnum[mesh]
        local = np.asarray(model.mesh_vert[start : start + count], dtype=float)
    else:
        center, half = model.geom_aabb[g, :3], model.geom_aabb[g, 3:]
        signs = np.array([[x, y, z] for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)])
        local = center + signs * half
    return local @ _mat(model.geom_quat[g]).T + model.geom_pos[g]


def _world(data: Any, body: int, points: np.ndarray) -> np.ndarray:
    return points @ data.xmat[body].reshape(3, 3).T + data.xpos[body]


def _local(data: Any, body: int, points: np.ndarray) -> np.ndarray:
    return (points - data.xpos[body]) @ data.xmat[body].reshape(3, 3)


def _at_gripper(model: Any, data: Any, q: float) -> None:
    """Forward kinematics with every joint at the model's zero and the gripper at `q`."""
    import mujoco

    data.qpos[:] = model.qpos0
    data.qpos[model.jnt_qposadr[model.joint(JOINTS[-1]).id]] = q
    mujoco.mj_kinematics(model, data)


def _pads(
    spec: Any, model: Any, data: Any, hand: int, jaw: int, label: str
) -> tuple[dict[str, _Pad], dict[str, Any]]:
    """The three pads: the stand-in's own boxes, or boxes cut from the real model's fingers.

    Returned with the spec's element for each, because a spec finds by name only what it held
    when it was last compiled, and the pads cut here are new."""
    import mujoco

    found = {
        name: g
        for name in (FIXED_PAD, MOVING_PAD, PALM_PAD)
        if (g := mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_GEOM, name)) >= 0
    }
    if len(found) == 3:
        return {
            name: _Pad(
                int(model.geom_bodyid[g]),
                np.array(model.geom_pos[g], dtype=float),
                np.array(model.geom_size[g], dtype=float),
            )
            for name, g in found.items()
        }, {name: spec.geom(name) for name in found}
    if found:
        raise ModelError(
            f"{LABEL} {label} names {', '.join(sorted(found))} but not all three of quackd's "
            "finger pads. Name all three or none."
        )
    pads = _cut_pads(model, data, hand, jaw, label)
    group = int(model.geom_group[_mesh_geom(model, hand, up.FIXED_FINGER_MESH, label)])
    for geom in spec.geoms:
        if geom.meshname in (up.FIXED_FINGER_MESH, up.MOVING_JAW_MESH) and (
            geom.contype or geom.conaffinity
        ):
            geom.contype = 0
            geom.conaffinity = 0
    added = {
        name: spec.body(model.body(pad.body).name).add_geom(
            name=name,
            type=mujoco.mjtGeom.mjGEOM_BOX,
            pos=pad.center,
            size=pad.half,
            group=group,
            mass=0.0,
        )
        for name, pad in pads.items()
    }
    return pads, added


def _mesh_geom(model: Any, body: int, mesh: str, label: str) -> int:
    import mujoco

    for g in range(model.ngeom):
        if (
            model.geom_bodyid[g] == body
            and model.geom_type[g] == mujoco.mjtGeom.mjGEOM_MESH
            and model.mesh(model.geom_dataid[g]).name == mesh
            and (model.geom_contype[g] or model.geom_conaffinity[g])
        ):
            return g
    raise ModelError(
        f"{LABEL} {label} has neither quackd's finger pads nor a colliding {mesh} on the "
        f"{model.body(body).name} body, so the simulator cannot tell where its fingers are. "
        f"{_remedy(label)}"
    )


@dataclass(frozen=True)
class _Finger:
    """One finger's vertices in its own body's frame, read along axes snapped to that frame's.

    The hinge's axis runs across the finger's width, the finger runs from the hinge to its tip
    along the axis it reaches furthest along, and the third axis crosses the opening. CAD
    exports a part's frame square to the part, so snapping to it loses nothing."""

    points: np.ndarray
    hinge: np.ndarray
    width: int
    along: int
    out: float
    """Which way along `along` the tip is."""
    across: int
    inward: float
    """Which way along `across` the other finger is."""

    @property
    def reach(self) -> np.ndarray:
        """How far each vertex is from the hinge, toward the tip."""
        return self.out * (self.points[:, self.along] - self.hinge[self.along])

    @property
    def depth(self) -> np.ndarray:
        """How far each vertex reaches into the opening."""
        return self.inward * self.points[:, self.across]

    @property
    def tip(self) -> np.ndarray:
        """The vertices in the fingertip: the last `TIP_SHARE` of the way to the tip."""
        reach = self.reach
        return reach >= float(reach.max()) * (1 - TIP_SHARE)

    @property
    def face(self) -> float:
        """The inner face: the furthest the fingertip reaches into the opening."""
        return float(self.depth[self.tip].max())

    @property
    def thickness(self) -> float:
        return self.face - float(self.depth[self.tip].min())

    def box(
        self, reach: tuple[float, float], depth: tuple[float, float]
    ) -> tuple[np.ndarray, np.ndarray]:
        """A box's centre and half sizes, between two reaches and two depths and as wide as the
        fingertip."""
        lo, hi = np.zeros(3), np.zeros(3)
        lo[self.width] = float(self.points[self.tip, self.width].min())
        hi[self.width] = float(self.points[self.tip, self.width].max())
        lo[self.along], hi[self.along] = sorted(
            self.hinge[self.along] + self.out * r for r in reach
        )
        lo[self.across], hi[self.across] = sorted(self.inward * d for d in depth)
        return (lo + hi) / 2, (hi - lo) / 2


def _finger(points: np.ndarray, hinge: np.ndarray, axis: np.ndarray, other: np.ndarray) -> _Finger:
    """A finger read along its own axes, `other` being the other finger's vertices in the same
    frame, to say which side of it the opening is."""
    width = int(np.argmax(np.abs(axis)))
    rest = [i for i in range(3) if i != width]
    offset = points - hinge
    along = max(rest, key=lambda i: float(np.abs(offset[:, i]).max()))
    furthest = int(np.argmax(np.abs(offset[:, along])))
    (across,) = (i for i in rest if i != along)
    out = 1.0 if offset[furthest, along] > 0 else -1.0
    finger = _Finger(points, hinge, width, along, out, across, inward=1.0)
    inward = 1.0 if other[:, across].mean() > points[finger.tip, across].mean() else -1.0
    return replace(finger, inward=inward)


def _cut_pads(model: Any, data: Any, hand: int, jaw: int, label: str) -> dict[str, _Pad]:
    """Boxes for the fixed finger, the palm and the moving finger, from the meshes' vertices.

    Each pad is as thick and as wide as its fingertip, with its face where the fingertip's
    inner face is. The palm is the level furthest from the hinge at which the fixed part
    reaches further into the opening than its own fingertip, anywhere across its width, and
    both fingers run from there to their tips. A box cannot follow a finger's taper, so a pad's
    face stands where the fingertip's does along the whole finger: where a thin object between
    the tips meets it."""
    fixed_points = _geom_points(model, _mesh_geom(model, hand, up.FIXED_FINGER_MESH, label))
    moving_points = _geom_points(model, _mesh_geom(model, jaw, up.MOVING_JAW_MESH, label))
    joint = model.joint(JOINTS[-1]).id
    lo, hi = model.jnt_range[joint]
    # half open: the fingers apart, so which side of each the other stands is unambiguous
    _at_gripper(model, data, (lo + hi) / 2)
    hand_rot, jaw_rot = data.xmat[hand].reshape(3, 3), data.xmat[jaw].reshape(3, 3)
    fixed = _finger(
        fixed_points,
        _local(data, hand, _world(data, jaw, model.jnt_pos[joint][None, :]))[0],
        model.jnt_axis[joint] @ jaw_rot.T @ hand_rot,
        _local(data, hand, _world(data, jaw, moving_points)),
    )
    reach, depth, face, thick = fixed.reach, fixed.depth, fixed.face, fixed.thickness
    palm = (depth > face + PAD_EPS_M) & (reach < float(reach.max()))
    if not palm.any():
        raise ModelError(
            f"{LABEL} {label}'s fixed finger never meets a palm, so the simulator cannot cut its "
            f"pads. {_remedy(label)}"
        )
    tip, level = float(reach.max()), float(reach[palm].max())
    hinge_depth = fixed.inward * fixed.hinge[fixed.across]
    moving = _finger(
        moving_points,
        model.jnt_pos[joint],
        model.jnt_axis[joint],
        _local(data, jaw, _world(data, hand, fixed_points[fixed.tip])),
    )
    moving_tip = float(moving.reach.max())
    return {
        FIXED_PAD: _Pad(hand, *fixed.box((level, tip), (face - thick, face))),
        PALM_PAD: _Pad(hand, *fixed.box((level - thick, level), (face - thick, hinge_depth))),
        MOVING_PAD: _Pad(
            jaw,
            *moving.box(
                (moving_tip - (tip - level), moving_tip),
                (moving.face - moving.thickness, moving.face),
            ),
        ),
    }


def _workspace(model: Any, data: Any, label: str) -> Workspace:
    """The table top is the lowest point of the arm's base, at the model's zero, and the reach
    across it is the datasheet's reach from the shoulder, less the shoulder's height."""
    import mujoco

    data.qpos[:] = model.qpos0
    mujoco.mj_kinematics(model, data)
    pan = model.joint(JOINTS[0]).id
    base = int(model.jnt_bodyid[pan])
    while model.body_parentid[base] != 0:  # the body welded to the world that carries the arm
        base = int(model.body_parentid[base])
    bottoms = [
        float(_world(data, base, _geom_points(model, g))[:, 2].min())
        for g in range(model.ngeom)
        if model.geom_bodyid[g] == base
    ]
    if not bottoms:
        raise ModelError(
            f"{LABEL} {label}'s base has no geometry to stand on a table. {_remedy(label)}"
        )
    table_top = min(bottoms)
    anchor = data.xanchor[pan]
    shoulder = float(data.xanchor[model.joint(JOINTS[1]).id][2]) - table_top
    reach = float(REACH.value)
    if shoulder >= reach:
        raise ModelError(
            f"{LABEL} {label}'s shoulder stands higher above its base than the arm reaches, so "
            f"it could never touch the table. {_remedy(label)}"
        )
    return Workspace(
        table_top=table_top,
        center=(float(anchor[0]), float(anchor[1])),
        reach=math.sqrt(reach**2 - shoulder**2),
    )


def _options(spec: Any) -> None:
    import mujoco

    spec.option.timestep = TIMESTEP_S
    spec.option.cone = mujoco.mjtCone.mjCONE_ELLIPTIC
    spec.option.impratio = IMPRATIO
    spec.option.noslip_iterations = NOSLIP_ITERATIONS


def _table_and_lights(spec: Any, workspace: Workspace) -> None:
    import mujoco

    half = TABLE_REACHES * float(REACH.value)
    cx, cy = workspace.center
    spec.worldbody.add_geom(
        name=TABLE,
        type=mujoco.mjtGeom.mjGEOM_BOX,
        pos=[cx, cy, workspace.table_top - TABLE_THICKNESS_M / 2],
        size=[half, half, TABLE_THICKNESS_M / 2],
        rgba=TABLE_RGBA,
        friction=MUJOCO_FRICTION,
    )
    # No face may meet more than full light, or its colour's brightest channel clips and the
    # colour changes hue: under MuJoCo's own headlight and two brighter lamps, the ball's
    # orange read as the duck's cream from above, and a blue and a green washed out below the
    # detector's saturation. So the camera's lamp is turned down, nothing shines (a highlight
    # is a white patch on a colour), and no lamp casts a shadow, a dark patch a detector could
    # take for an object and one that costs every render time.
    headlight = spec.visual.headlight
    headlight.ambient = [AMBIENT] * 3
    headlight.diffuse = [HEADLIGHT] * 3
    headlight.specular = [0.0] * 3
    height = 2 * float(REACH.value)
    lamp = _lamp_diffuse()
    for name, direction in zip(("quackd_key", "quackd_fill"), LAMP_DIRECTIONS, strict=True):
        spec.worldbody.add_light(
            name=name,
            type=mujoco.mjtLightType.mjLIGHT_DIRECTIONAL,
            pos=[cx, cy, workspace.table_top + height],
            dir=list(direction),
            castshadow=False,
            ambient=[0.0] * 3,
            diffuse=[lamp] * 3,
            specular=[0.0] * 3,
        )


def _lamp_diffuse() -> float:
    """How bright each of the two lamps is: what is left of full light once the ambient and the
    headlight are counted, shared so that the face that meets the most, one lying flat and
    seen from straight above, meets exactly full light. The lamps shine from either side of
    straight down, so no other face meets more from them than a flat one does."""
    down = sum(-d[2] / math.sqrt(sum(c * c for c in d)) for d in LAMP_DIRECTIONS)
    return (1.0 - AMBIENT - HEADLIGHT) / down


def _objects(
    spec: Any, objects: Sequence[SceneObject], poses: Sequence[tuple[float, float, float, float]]
) -> None:
    import mujoco

    # a capsule's axis is its geom's z, turned onto the body's x so that it lies down
    lying = _quat(np.array([[0.0, 0.0, 1.0], [0.0, 1.0, 0.0], [-1.0, 0.0, 0.0]]))
    for obj, (x, y, z, yaw) in zip(objects, poses, strict=True):
        body = spec.worldbody.add_body(
            name=obj.name, pos=[x, y, z], quat=[math.cos(yaw / 2), 0.0, 0.0, math.sin(yaw / 2)]
        )
        body.add_freejoint()
        size = list(obj.size) + [0.0] * (3 - len(obj.size))
        body.add_geom(
            name=obj.name,
            type=mujoco.mjtGeom.mjGEOM_BOX if obj.shape == "box" else mujoco.mjtGeom.mjGEOM_CAPSULE,
            size=size,
            quat=[1.0, 0.0, 0.0, 0.0] if obj.shape == "box" else lying,
            rgba=obj.rgba,
            condim=OBJECT_CONDIM,
            friction=MUJOCO_FRICTION,
            # no mass is MuJoCo's own default density over the shape's volume
            **({} if obj.mass_kg is None else {"mass": obj.mass_kg}),
        )


def _contacts(spec: Any, pads: dict[str, Any]) -> None:
    for pad in pads.values():
        pad.contype = 1
        pad.conaffinity = 1
        pad.condim = PAD_CONDIM
        pad.friction = MUJOCO_FRICTION
        pad.solref = PAD_SOLREF
        pad.priority = PAD_PRIORITY
    # The two fingers are a body and its parent, which MuJoCo does not let collide by default,
    # so a gripper closed on nothing would pass one finger through the other without this pair.
    sliding, torsional, rolling = MUJOCO_FRICTION
    spec.add_pair(
        name=FINGER_PAIR,
        geomname1=FIXED_PAD,
        geomname2=MOVING_PAD,
        condim=PAD_CONDIM,
        friction=[sliding, sliding, torsional, rolling, rolling],
        solref=PAD_SOLREF,
    )


def _gripper_force(spec: Any, label: str) -> None:
    """LeRobot caps the gripper's torque when it connects (`SO_GRIPPER_TORQUE_LIMIT`), so the
    model's gripper gets the same share of its own force range."""
    actuator = spec.actuator(JOINTS[-1])
    lo, hi = (float(x) for x in actuator.forcerange)
    if not lo < hi:
        raise ModelError(
            f"{LABEL} {label}'s gripper actuator has no force range, so the simulator cannot give "
            f"it the torque limit LeRobot writes. {_remedy(label)}"
        )
    share = lr.GRIPPER_MAX_TORQUE_LIMIT / lr.MAX_TORQUE_LIMIT_FULL
    actuator.forcerange = [lo * share, hi * share]


def _fovy() -> float:
    """The vertical field of view that gives `VIEW_HFOV_DEG` across a `VIEW_ASPECT` frame."""
    return math.degrees(2 * math.atan(math.tan(math.radians(VIEW_HFOV_DEG) / 2) / VIEW_ASPECT))


def _look(forward: np.ndarray, right: np.ndarray) -> np.ndarray:
    """A camera's orientation, looking along `forward` with `right` as near to the frame's
    right-hand side as it can be. MuJoCo's cameras look down their own -z, with x to the right
    of the frame and y up it."""
    z = -forward / np.linalg.norm(forward)
    x = right - (right @ z) * z
    x = x / np.linalg.norm(x)
    y = np.cross(z, x)
    return _quat(np.column_stack([x, y, z]))


def _views(spec: Any, workspace: Workspace) -> None:
    """`front` and `top`, aimed at the middle of the square of table the arm reaches over and
    far enough back that a sphere around that square fills the frame's narrower angle."""
    reach = float(REACH.value)
    cx, cy = workspace.center
    target = np.array([cx + reach / 2, cy, workspace.table_top])
    radius = math.hypot(reach / 2, reach / 2)
    distance = radius / math.sin(math.radians(_fovy()) / 2)
    elevation = math.radians(FRONT_ELEVATION_DEG)
    for name, eye, up_ in (
        # from above, with the arm at the bottom of the frame and ahead up it
        ("top", target + distance * np.array([0.0, 0.0, 1.0]), np.array([1.0, 0.0, 0.0])),
        # from in front, looking back at the arm with the table level across the frame
        (
            "front",
            target + distance * np.array([math.cos(elevation), 0.0, math.sin(elevation)]),
            np.array([0.0, 0.0, 1.0]),
        ),
    ):
        forward = target - eye
        spec.worldbody.add_camera(
            name=name, pos=eye, quat=_look(forward, np.cross(forward, up_)), fovy=_fovy()
        )


def _wrist_view(
    spec: Any, model: Any, data: Any, pads: dict[str, _Pad], hand: int, jaw: int
) -> None:
    """`wrist`, on the wrist camera body, looking from it at the middle of the two fingers with
    the gripper half open, with the fingers either side of the frame. It sits just in front of
    the body's own shell along that line, so the shell does not fill the view."""
    joint = model.joint(JOINTS[-1]).id
    lo, hi = model.jnt_range[joint]
    _at_gripper(model, data, (lo + hi) / 2)
    fixed = _world(data, pads[FIXED_PAD].body, pads[FIXED_PAD].center[None, :])[0]
    moving = _world(data, pads[MOVING_PAD].body, pads[MOVING_PAD].center[None, :])[0]
    body = model.body(up.WRIST_CAMERA_BODY).id
    origin = np.array(data.xpos[body])
    forward = (fixed + moving) / 2 - origin
    forward /= np.linalg.norm(forward)
    shell = [
        _world(data, body, _geom_points(model, g))
        for g in range(model.ngeom)
        if model.geom_bodyid[g] == body
    ]
    ahead = max((float(((p - origin) @ forward).max()) for p in shell), default=0.0)
    eye = origin + max(ahead, 0.0) * forward
    rot = data.xmat[body].reshape(3, 3)
    spec.body(up.WRIST_CAMERA_BODY).add_camera(
        name="wrist",
        pos=(eye - origin) @ rot,
        quat=_look(forward @ rot, (moving - fixed) @ rot),
        fovy=_fovy(),
    )


def _subtree(model: Any, root: int) -> set[int]:
    bodies = {root}
    for b in range(root + 1, model.nbody):
        if int(model.body_parentid[b]) in bodies:
            bodies.add(b)
    return bodies


def _arm(
    model: Any, label: str, workspace: Workspace, objects: tuple[SceneObject, ...], hand: int
) -> ArmModel:
    import mujoco

    data = mujoco.MjData(model)
    joints: dict[str, JointOrGripper] = {}
    for name in JOINTS[:-1]:
        j = model.joint(name).id
        joints[name] = JointMap(
            name=name,
            qpos=int(model.jnt_qposadr[j]),
            dof=int(model.jnt_dofadr[j]),
            actuator=int(model.actuator(name).id),
            lo=float(model.jnt_range[j][0]),
            hi=float(model.jnt_range[j][1]),
        )
    j = model.joint(JOINTS[-1]).id
    lo, hi = (float(x) for x in model.jnt_range[j])
    fixed = model.geom(FIXED_PAD).id
    moving = model.geom(MOVING_PAD).id
    apart = {}
    for end in (lo, hi):
        _at_gripper(model, data, end)
        apart[end] = float(np.linalg.norm(data.geom_xpos[fixed] - data.geom_xpos[moving]))
    if math.isclose(apart[lo], apart[hi]):
        raise ModelError(
            f"{LABEL} {label}'s fingers are as far apart at one end of the gripper's range as at "
            f"the other, so the simulator cannot tell which end is closed. {_remedy(label)}"
        )
    closed = lo if apart[lo] < apart[hi] else hi
    joints[JOINTS[-1]] = GripperMap(
        name=JOINTS[-1],
        qpos=int(model.jnt_qposadr[j]),
        dof=int(model.jnt_dofadr[j]),
        actuator=int(model.actuator(JOINTS[-1]).id),
        lo=lo,
        hi=hi,
        closed=closed,
        open=hi if closed == lo else lo,
    )
    bodies = [model.body(obj.name).id for obj in objects]
    hand_bodies = _subtree(model, hand)
    colliding = [
        g for g in range(model.ngeom) if model.geom_contype[g] or model.geom_conaffinity[g]
    ]
    return ArmModel(
        model=model,
        label=label,
        joints=MappingProxyType(joints),
        workspace=workspace,
        objects=objects,
        object_bodies=tuple(bodies),
        object_qpos=tuple(int(model.jnt_qposadr[model.body_jntadr[b]]) for b in bodies),
        object_dofs=tuple(int(model.jnt_dofadr[model.body_jntadr[b]]) for b in bodies),
        object_geoms=tuple(
            frozenset(g for g in range(model.ngeom) if model.geom_bodyid[g] == b) for b in bodies
        ),
        table=int(model.geom(TABLE).id),
        fixed_pad=int(fixed),
        moving_pad=int(moving),
        palm_pad=int(model.geom(PALM_PAD).id),
        gripper_geoms=frozenset(g for g in colliding if int(model.geom_bodyid[g]) in hand_bodies),
    )


# ── calibration ─────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class MotorCalibration:
    """One motor's entry in a calibration file, as LeRobot's own dataclass holds it
    (`upstream_api.MOTOR_CALIBRATION`): raw encoder ticks, not degrees."""

    id: int
    drive_mode: int
    homing_offset: int
    range_min: int
    range_max: int


FIELDS = ("id", "drive_mode", "homing_offset", "range_min", "range_max")


def calibration_dir() -> Path:
    """Where LeRobot looks for calibration files, walked as LeRobot walks it
    (`upstream_api.CALIBRATION_DIR`), without importing it.

    Each variable counts once it is set, even to nothing, because LeRobot reads each with
    `os.getenv` and a default."""
    env = os.environ
    if (value := env.get(lr.CALIBRATION_ENV)) is not None:
        return Path(value).expanduser()
    if (home := env.get(lr.LEROBOT_HOME_ENV)) is None:
        hf = env.get(lr.HF_HOME_ENV)
        if hf is None:
            cache = env.get(lr.XDG_CACHE_ENV, os.path.join(os.path.expanduser("~"), ".cache"))
            hf = os.path.join(cache, lr.HF_SUBDIR)
        home = str(Path(os.path.expandvars(os.path.expanduser(hf))) / lr.LEROBOT_SUBDIR)
    return Path(home).expanduser() / lr.CALIBRATION_SUBDIR


def calibration_path(robot_id: str) -> Path:
    """The file LeRobot would read for an SO-101 follower with this id."""
    return calibration_dir() / lr.ROBOTS_SUBDIR / lr.SO_FOLLOWER_NAME / f"{robot_id}.json"


def _decode_int(value: Any) -> int | None:
    """One field as the draccus LeRobot reads the file with decodes an int
    (`upstream_api.CALIBRATION_INTS`): `int()` of anything but a float, so `true` is 1 and
    `"3"` is 3. None where that fails, and for a null, which draccus passes through and the
    simulator cannot build a travel from."""
    if value is None or isinstance(value, float):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


DEVICE_PREFIX = "//./"
"""How a Windows device path starts once its backslashes are turned round, which is how a port
past COM9 is written."""


def names_a_port(address: str | Path) -> bool:
    """Whether an address is a serial port, shaped as the real backend's `--address` is
    (`real.PORT_SHAPE`), or a Windows device path.

    The simulator's address is a calibration file and never a port, and one that names a port
    is refused on its shape alone, before the filesystem is asked anything about it: on Windows
    `COM5` is the device itself in whatever directory it is looked for, and reading it would
    hold the arm's own port open waiting for an end a serial port never sends."""
    text = str(address).strip()
    return any(
        bool(PORT_SHAPE.match(form)) or form.startswith(DEVICE_PREFIX)
        for form in (text, text.replace("\\", "/"))
    )


def port_refusal(address: str | Path) -> str:
    """Why an address that names a port is refused (`names_a_port`), and what to give instead."""
    return (
        f"{LABEL} --address {str(address)!r} is a serial port, and the address of a "
        "lerobot:mujoco robot is the calibration file the arm's runs read, never the arm's "
        "port, so nothing was opened there. Give --address the file lerobot-calibrate wrote "
        "for the arm, which quackd robot twin finds for a registered one, or leave it out to "
        "rehearse on the generic arm."
    )


def read_calibration(path: Path) -> dict[str, MotorCalibration]:
    """A calibration file, read as LeRobot reads one (`upstream_api.CALIBRATION_FILE`).

    It must name exactly the six motors, because LeRobot calls an arm whose file names any
    other set not calibrated (`upstream_api.BUS_IS_CALIBRATED`), and give each exactly the five
    fields, each decoded to a whole number as draccus decodes it for LeRobot. A range_min equal
    to its range_max is refused here, where LeRobot loads it and refuses the first reading
    through that motor (`upstream_api.CALIBRATION_EQUAL_RANGE`)."""
    fix = (
        "Point --address at the file lerobot-calibrate wrote for this arm, or leave it out to "
        "rehearse on the generic arm."
    )
    if names_a_port(path):
        raise CalibrationError(port_refusal(path))
    if not Path(path).is_file():
        raise CalibrationError(f"{LABEL} there is no calibration file at {path}. {fix}")
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise CalibrationError(f"{LABEL} there is no calibration file at {path}. {fix}") from None
    except (OSError, UnicodeDecodeError, ValueError) as e:
        raise CalibrationError(
            f"{LABEL} {path} is not a calibration file LeRobot could read ({e}). {fix}"
        ) from e
    if not isinstance(raw, dict) or set(raw) != set(JOINTS):
        named = ", ".join(sorted(raw)) if isinstance(raw, dict) and raw else "no motors"
        raise CalibrationError(
            f"{LABEL} {path} names {named}, not the SO-101's six motors "
            f"({', '.join(JOINTS)}), so LeRobot would call this arm not calibrated. {fix}"
        )
    out: dict[str, MotorCalibration] = {}
    for joint in JOINTS:
        entry = raw[joint]
        if not isinstance(entry, dict) or set(entry) != set(FIELDS):
            got = ", ".join(sorted(entry)) if isinstance(entry, dict) else type(entry).__name__
            raise CalibrationError(
                f"{LABEL} {path} gives {joint} {got}, not LeRobot's {', '.join(FIELDS)}. {fix}"
            )
        ticks = {k: v for k in FIELDS if (v := _decode_int(entry[k])) is not None}
        if bad := [k for k in FIELDS if k not in ticks]:
            raise CalibrationError(
                f"{LABEL} {path} gives {joint} a value for {', '.join(bad)} that is not a whole "
                f"number. {fix}"
            )
        cal = MotorCalibration(**ticks)
        if cal.range_min == cal.range_max:
            raise CalibrationError(
                f"{LABEL} {path} gives {joint} a range_min equal to its range_max, which LeRobot "
                f"refuses as an invalid calibration. {fix}"
            )
        out[joint] = cal
    return out


def generic_calibration(arm: ArmModel) -> dict[str, MotorCalibration]:
    """A calibration for an arm nobody named, built from the model's own ranges.

    Each body joint gets the widest travel either side of LeRobot's zero that stays inside the
    model's stops, in whole encoder ticks, rounded inward, so the real backend's joint_ranges()
    reads the model's travel to within one tick and never past a stop. The gripper's span is its
    hinge's. Ids are the SO follower's bus table's (`upstream_api.SO_MOTORS`), drive modes and
    homing offsets zero. None of it is any real arm's: the travel a calibration records is one
    arm's, and a run that names no arm has none to borrow (`JOINT_RANGES`)."""
    per_deg = (SO_ENCODER_TICKS - 1) / 360.0  # upstream_api.DEGREES_FORMULA
    middle = SO_ENCODER_TICKS // 2
    out: dict[str, MotorCalibration] = {}
    for name, joint in arm.joints.items():
        if isinstance(joint, GripperMap):
            half_deg = math.degrees(joint.hi - joint.lo) / 2
        else:
            lo, hi = joint.stops
            half_deg = min(-lo, hi)
            if half_deg <= 0:
                raise ModelError(
                    f"{LABEL} {arm.label}'s {name} cannot reach LeRobot's zero between its stops, "
                    f"so there is no travel to give it. {_remedy(arm.label)}"
                )
        half = min(math.floor(half_deg * per_deg), middle, SO_ENCODER_TICKS - 1 - middle)
        out[name] = MotorCalibration(
            id=lr.SO_MOTOR_IDS[name],
            drive_mode=0,
            homing_offset=0,
            range_min=middle - half,
            range_max=middle + half,
        )
    return out
