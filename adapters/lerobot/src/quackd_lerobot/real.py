"""A real arm through LeRobot. Every name verified at a pin, and driven on one arm.

One SO-101 ran this backend on 2026-09-15 (lerobot 0.6.1, Windows 11, Python 3.12.12):
connect, `get_observation`, `send_action`, the two register reads and `disconnect` all
behaved as the rows in `upstream_api.py` say. The arm fell at the end of every one of those
runs, which is why `close()` now returns it to a recorded rest pose first and keeps torque
on when it could not get there.

Every LeRobot name comes from `upstream_api.py` (ADR-0022). LeRobot is synchronous, so
every call runs in a worker thread under one lock with a deadline, and a call that blows its
deadline wedges the transport rather than letting a second thread onto a half-duplex bus.
`stop` re-sends the present position as the goal (hold). quackd disables torque in exactly
one place, `let_go()`, which a person asks for with `--by-hand` and which refuses anywhere but
the recorded rest pose; `take_hold()` is how the arm is picked back up. LeRobot's own
`disconnect()` disables it too, where its config asks, which is upstream's default. quackd
builds the follower asking it not to, and `close()` asks for the release only over an arm at
its recorded rest pose or with none recorded, so an exit that never reaches `close()`, where
LeRobot disconnects the follower as it is collected (`up.ROBOT_DEL`), leaves the arm holding.

What this backend refuses to take on faith, because upstream cannot tell it:

- **the arm is still there.** `is_connected` is the serial port's open flag, so the
  heartbeat reads the arm instead (`up.BUS_IS_CONNECTED`).
- **torque is on.** `get_observation()` reads positions only, so torque state and joint
  temperature come off the bus by register (`up.STS3215_REGISTERS`).
- **the goal is reachable.** A degrees goal outside the calibrated range is written as-is
  (`up.DEGREES_NO_CLAMP`) and the servo clamps it to the limits calibration wrote into it
  (`up.POSITION_LIMITS_CLAMP_GOALS`), so it would be a goal the arm quietly stops short of.
  quackd computes each joint's travel from the calibration file and refuses the goal.
- **the reading is inside the travel.** The clamp is on goals and not on readings: an arm
  folded or placed with torque off can read past its travel, and its recorded rest pose can
  lie there. So the rest move drives to the pose clipped into the travel and judges a joint
  past it as folded, and no hold ever writes a goal for a joint reading past it, because the
  only goal the servo would take there hauls the joint up to the limit.
- **the arm can be told to jump.** `max_relative_target` is `None` upstream; quackd sets it,
  so one `send_action` moves a joint at most `max_step_deg`.
- **one lost packet is not a dead arm.** LeRobot's connect writes torque off and on again to
  every motor, one try per write (`up.CONFIGURE_TORQUE_WRITES_ONCE`), and a Feetech bus loses
  the odd status packet. So `connect()` closes the port without writing anything and tries
  again, a few times, and says each time which joint the bus stopped answering for.

`pick` and `manipulate` run an injected policy, a `PolicyRunner` or a `PolicyLike` object, one
segment at a time in the policy loop (`policy/loop.py`). A checkpoint runs in a policy server of
its own and reaches the arm through `policy/client.py`, whose policy is checked against this arm
before it is energised (`_fit_policy`). `load_policy` builds one in this process instead, and
nothing calls it (LOAD_POLICY, in `policy/upstream_api.py`). LeRobot is imported inside
`connect()` and `load_policy()` only: `quackd[lerobot]` is an extra.
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import logging
import math
import os
import re
import time
from collections import deque
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any, NoReturn, Protocol, cast
from urllib.parse import parse_qs, urlsplit

import numpy as np
from PIL import Image

from quackd.adapters.base import AdapterError, AdapterNotInstalled, HandResult, RestResult
from quackd.transport.base import (
    Ack,
    CameraFrame,
    DuckState,
    HeartbeatError,
    Intent,
    TransportError,
)
from quackd_lerobot import upstream_api as up
from quackd_lerobot.policy.loop import CLIP_SUSTAIN_S as CLIP_SUSTAIN_S
from quackd_lerobot.policy.loop import FAILED_SENDS as FAILED_SENDS
from quackd_lerobot.policy.loop import REGISTER_PERIOD_S as REGISTER_PERIOD_S
from quackd_lerobot.policy.loop import PolicyLoop, Segment
from quackd_lerobot.policy.runner import PolicyLike, PolicyRunner, is_runner
from quackd_lerobot.policy.scripted import ScriptedRunner
from quackd_lerobot.verbs import (
    GRIPPER_CLOSED,
    GRIPPER_OPEN,
    HOLD_NOT_CONFIRMED,
    IN_HAND_NOT_MOVED,
    JOINTS,
    LET_GO_AT_REST,
    LET_GO_TO_PLACE,
    LET_GO_WHERE_IT_STOOD,
    LIMP_AT_REST,
    LIMP_AT_REST_AS_ASKED,
    LIMP_IN_HAND,
    MANIPULATE_S,
    NO_INSTRUCTION,
    NO_REST_POSE_RECORDED,
    STALL_DEG,
    STALL_TICKS,
    TICK_S,
    TOL_DEG,
    TORQUE_COULD_NOT_BE_KEPT,
    TORQUE_KEPT_AFTER_REFUSAL,
    TORQUE_KEPT_AFTER_REFUSAL_WITHOUT_A_POSE,
    TORQUE_LEFT_ON_WITHOUT_A_POSE,
    TORQUE_UNKNOWN_AT_CLOSE,
    UNCONFIRMED_IN_HAND,
    UNNAMED,
    UNREAD_AT_REST,
    UNREAD_IN_HAND,
    Clip,
    SegmentEnd,
    SegmentHow,
    at_rest,
    checked_segment_s,
    held_in_part,
    past_reach,
    placed_past_travel,
    policy_past_travel,
    range_refusal,
    reachable_rest_goal,
    released_by_the_close,
    rest_budget_s,
    rest_clip_note,
    rest_goal,
    shortfall,
    still_holding_at_rest,
    still_holding_in_hand,
    torque_left_on,
    torque_reads_off,
    unlifted_from_rest,
    worth_saying,
)

STATUS = "LeRobot names verified at a pinned commit; one SO-101 driven on 2026-09-15"
POLICY_HZ = 10.0
"""The rate a `PolicyLike` object runs at, since it declares none: the verbs' own tick
(`verbs.TICK_S`), so its step cap per send is the verbs' own too (`policy.loop.speed_cap`)."""
POLICY_RATE_SOURCE = "quackd's rate for a policy object that declares none"
"""Where `POLICY_HZ` comes from, as a refusal quotes a rate's source."""
POLICY_TASK = "quackd-lerobot-policy"
"""The name a policy segment runs under as an asyncio task."""
SEGMENT_VERBS = ("pick", "manipulate")
"""The verbs that hand the arm to a policy segment, as a `do` names them: `policy:<verb>:<text>`."""
STATS_WINDOW = 256
"""How many of the latest timings the median and the 99th percentile are taken over
(`Timing`): some twenty-five seconds of a policy's ticks, long enough for a 99th percentile to
be more than the one worst tick, short enough to follow a bus that slows down."""
FINISH_S = 0.001
"""The last stretch of every wall-clock sleep, finished by watching `perf_counter` rather than
left to the event loop's timer (`WallClock.sleep`)."""

logger = logging.getLogger("quackd.lerobot")
"""Under `quackd`, not under this module's own name (`quackd_lerobot.real`): `quackd`'s logger is
the one the CLI prints at WARNING (`ui.install_logging`) and the MCP server logs, so a line
written here reaches the person at the arm while the connect is still happening."""

MAX_STEP_DEG = 5.0
"""How far one `send_action` may move a joint, in degrees. At the 10 Hz a verb re-sends a
goal that is also the top joint speed: 5 degrees a step is 50 degrees a second. The figure
is quackd's own choice for a first run and nothing upstream recommends one for this arm;
upstream's default is no cap at all."""
STEP_ENV = "QUACKD_LEROBOT_MAX_STEP_DEG"

CONNECT_ATTEMPTS = 3
"""How many times `connect()` asks LeRobot to connect before it gives up.

LeRobot's connect switches torque off on every motor to configure it and back on afterwards,
one write per register per motor and no retry (`up.CONFIGURE_TORQUE_WRITES_ONCE`), so one
status packet lost anywhere on a Feetech bus fails the whole connect. A lost packet is the bus
being a bus rather than the arm being broken, and the next attempt normally goes through. An
arm that fails every attempt has something a further try will not fix, a loose cable or a
servo that stopped answering, and the person is told which joint to look at instead."""
CONNECT_PAUSE_S = 0.5
"""How long `connect()` waits between two attempts, with the port closed. Long enough for a
reply still in flight to finish before the port is opened again, short enough that nobody at
the arm notices. quackd's own figure: nothing upstream recommends one."""
CONNECT_DEADLINE_S = 30.0
"""How long one attempt may take before quackd stops waiting for it. A connect that has not
come back by then is a worker thread still sitting on the serial bus, which `_call` files as a
wedged transport, and that is never tried again: a second talker on a half-duplex bus is how
packets get lost in the first place."""
PORT_CLOSE_DEADLINE_S = 5.0
"""How long closing the port between two connect attempts may take, the deadline every other
disconnect here has. A close that has not come back by then wedges the transport as any call
does, and the next attempt is refused rather than put a second talker on the bus."""
SPLIT_TORQUE = (
    "Connecting switches torque off on every motor and back on one motor at a time, so some "
    "motors may be left with torque on and others off: keep a hand under the arm, because the "
    "ones that are off hold nothing up."
)
"""What a refused connect tells the person at the arm when an attempt can have written torque:
one that failed on a write, inside `configure()` or somewhere nothing can place
(`may_have_written_torque`), or one still on the wire when quackd stopped waiting for it.
`configure()` writes torque off on every motor and back on one motor after another
(`up.CONFIGURE_TORQUE_WRITES_ONCE`), so where it stopped, the motors before that write can be
holding and the ones after it limp."""
KEPT_OVER_A_FAILED_CONNECT = (
    "Nothing has read where the arm is, so quackd kept whatever torque connecting switched on "
    "rather than let it go where it stands: hold the arm, and cut its power."
)
"""What a connect says when it fails after LeRobot's own connect went through, on something
other than one of quackd's refusals: a first read the arm did not answer, a calibration check
that raised, a calibration quackd could not read the travel out of. `configure()` has switched
torque on by then, and no read has said where the arm stands, so whether letting go would drop
it is not something quackd knows. It keeps the torque, as a close does over an arm that did not
answer (`TORQUE_UNKNOWN_AT_CLOSE`), and cutting the power is the way out, because
`quackd robot release` connects the same way and would fail in the same place."""
TORQUE_RETRIES = 5
"""Extra tries LeRobot gives each `Torque_Enable` and `Lock` write when quackd lets go of the
arm or takes hold of it again: the count upstream's own `disconnect()` gives its torque-off
(`up.BUS_DISCONNECT`). LeRobot's default is none, and one lost packet there is a hand-off that
happened to some motors and not to others."""
MOTOR_ID = re.compile(r"\bid_=(\d+)")
"""The motor a LeRobot bus error is about, wherever it sits in the sentence and whatever
register and transaction result surround it (`up.BUS_WRITE_ERROR_NAMES_THE_ID`)."""
HANDSHAKE_MOTOR_ID = re.compile(
    r"(?:Missing motor IDs|Motors with incorrect model numbers):\s*-\s*(\d+)\b"
)
"""The first motor LeRobot's handshake could not find, or found answering as another model
(`up.HANDSHAKE_NAMES_THE_ID`). Both lists put one motor on a line, `- <N> (...)`, under their
heading; `\\s` spans the line breaks of the raw message and the spaces of a one-lined one, and a
search finds whichever list comes first, which is the missing one when there are both."""
MISSING_LIST = re.compile(r"Missing motor IDs:((?:\s*-\s*\d+\s*\([^)]*\))+)")
"""Every line of the handshake's list of motors that did not answer their ping, one `- <N>
(expected model: <M>)` each (`up.HANDSHAKE_NAMES_THE_ID`), raw or on one line."""
LISTED_ID = re.compile(r"-\s*(\d+)")
"""One id out of `MISSING_LIST`'s lines."""
EXPECTED_LIST = re.compile(r"Full expected motor list \(id: model_number\):\s*(\{[^}]*\})")
"""The handshake's `{<id>: <model>, ...}` of every motor the bus expected, which upstream prints
from the bus's own motor table (`up.HANDSHAKE_NAMES_THE_ID`)."""
EXPECTED_ID = re.compile(r"(\d+)\s*:")
"""One id out of `EXPECTED_LIST`'s dict."""
WRITE_FAILED = re.compile(r"\bFailed to (?:sync )?write\b")
"""A LeRobot bus error about a write, to one motor or to several
(`up.BUS_WRITE_ERROR_NAMES_THE_ID`). Every write a connect makes is inside `configure()`, after
its torque-off has started, so a connect that failed on one may have left the motors in two
torque states."""

ENCODER_TICKS = 4096
"""An sts3215 turn in encoder counts (`up.STS3215_RESOLUTION`); degrees use it less one."""

HOT_C = 60.0
"""Refuse to move a joint at or above this. The servo's own cut-off is 70 (`up.TEMPERATURE_C`)."""

HOLD_MIN = 8.0
HOLD_MAX = 90.0
"""A gripper told to close and settled strictly inside this band is holding something."""
GRIPPER_SETTLE = 1.0
"""Two readings this close together mean the gripper has stopped moving."""
SETTLE_GAP_S = 0.08
"""How far apart in time those two readings have to be to say anything."""

OUT_OF_RANGE_DEG = 2.0
"""How far past its calibrated travel a joint must read before the state says so."""

PORT_SHAPE = re.compile(r"^(COM\d+|/dev/[\w./-]+)$", re.IGNORECASE)
"""A serial port looks like COM5 or /dev/ttyACM0. Which one is the arm is not our business
(`up.SERIAL_PORT`); a goal pasted into --address by mistake is."""

CAMERA_SCHEME = "opencv"
CAMERA_NAME = "front"
CAMERA_BACKENDS = ("any", "v4l2", "dshow", "avfoundation", "msmf")
CAMERA_ROTATIONS = (0, 90, 180, 270)
CAMERA_KEYS = ("name", "width", "height", "fps", "fourcc", "backend", "rotation", "fov")
CAMERA_HINT = (
    "A camera is one USB webcam named by its OpenCV index, opencv://0, or by a device path, "
    f"opencv:///dev/video2, with any of {', '.join(CAMERA_KEYS)} after a ?. Run "
    "`lerobot-find-cameras opencv` to see which index is which: it saves a frame per camera"
)
"""What a refused `--camera-url` is told a camera is, after why it was refused. The simulator
parses the same urls and passes a hint of its own (`sim/camera.py`), because a camera there is
a mount in a scene and not a webcam on a USB port."""
CAMERA_CONNECT_S = 15.0
"""Long enough for the open plus upstream's warmup, which reads frames before returning."""
CAMERA_CLOSE_S = 10.0
"""A release stops a read thread and joins it; a few seconds is routine on Windows."""


@dataclass(frozen=True)
class CameraSpec:
    """One USB webcam, as `--camera-url` described it.

    quackd owns this camera rather than handing it to the follower. A follower's cameras are
    part of its connected state (`up.SO_CAMERAS_ARE_THE_FOLLOWERS`): `send_action` and
    `disconnect()` are gated on `is_connected`, which is the bus AND every camera, so a
    webcam that came unplugged mid-session would make every move and every hold raise while
    the arm itself was fine. Beside the follower, a dead camera costs you `observe`."""

    url: str
    name: str
    index_or_path: int | str
    width: int | None
    height: int | None
    fps: int | None
    fourcc: str | None
    backend: str
    rotation: int
    fov_deg: float | None
    name_given: bool = False
    """The url said `?name=`. With several cameras it must, because the name is the only
    thing telling two views apart; with one it may, and `front` is the default."""


def _camera_int(key: str, raw: str, url: str, label: str, hint: str) -> int:
    try:
        value = int(raw)
    except ValueError:
        raise _camera_refusal(f"{key}={raw!r} is not a whole number", url, label, hint) from None
    if value <= 0:
        raise _camera_refusal(f"{key}={raw!r} must be above 0", url, label, hint)
    return value


def _camera_refusal(
    why: str, url: str, label: str = "real", hint: str = CAMERA_HINT
) -> AdapterError:
    """A refused `--camera-url`, from the backend `label` names (`LeRobotReal.label`), with
    `hint` saying what a camera is there."""
    return AdapterError(f"lerobot {label}: --camera-url {url!r}: {why}. {hint}")


def parse_camera_url(url: str, *, label: str = "real", hint: str = CAMERA_HINT) -> CameraSpec:
    """`opencv://0?width=640&height=480&fps=30&backend=msmf` into a `CameraSpec`.

    Strict, in the shape of the rosbridge adapter's address parser: an unknown scheme, key
    or value is refused with the shape rather than quietly ignored, because the alternative
    is an owner who believes they configured a camera and did not. Nothing here imports
    lerobot, so a bad url is refused before the extra is even looked for.

    Size and rate are left unset by default, which keeps whatever mode the camera already
    has (`up.OPENCV_MODE_DEFAULTS_TO_THE_CAMERA`). Asking for one it cannot do is a refusal
    at connect (`up.OPENCV_MODE_IS_A_DEMAND`), and the webcam in a lab drawer is unknown.

    `label` and `hint` are whose refusal it is and what it says a camera is: the simulator
    parses the same urls, so that a task's camera flags rehearse unchanged, and refuses them
    in its own name."""

    def refused(why: str) -> AdapterError:
        return _camera_refusal(why, url, label, hint)

    def whole(key: str, raw: str) -> int:
        return _camera_int(key, raw, url, label, hint)

    parts = urlsplit(url)
    if parts.scheme != CAMERA_SCHEME:
        seen = f"{parts.scheme!r} is not a scheme quackd knows" if parts.scheme else "no scheme"
        raise refused(seen)
    target = (parts.netloc + parts.path).rstrip("/")
    if not target:
        raise refused("no camera index or device path")
    index_or_path: int | str = int(target) if target.isdigit() else target
    query = parse_qs(parts.query, keep_blank_values=True)
    if unknown := sorted(set(query) - set(CAMERA_KEYS)):
        raise refused(f"unknown {'keys' if len(unknown) > 1 else 'key'} {unknown}")

    def one(key: str) -> str | None:
        values = query.get(key)
        return values[-1].strip() if values else None

    given = one("name")
    name = given or CAMERA_NAME
    if not name.replace("_", "").replace("-", "").isalnum():
        raise refused(f"name={name!r} is not a plain name")
    fourcc = one("fourcc")
    if fourcc is not None and len(fourcc) != 4:
        raise refused(f"fourcc={fourcc!r} must be four characters")
    backend = (one("backend") or "any").lower()
    if backend not in CAMERA_BACKENDS:
        raise refused(f"backend={backend!r} is not one of {CAMERA_BACKENDS}")
    raw_rotation = one("rotation")
    rotation = 0
    if raw_rotation:
        try:
            rotation = int(raw_rotation)
        except ValueError:
            raise refused(f"rotation={raw_rotation!r} is not a whole number") from None
    if rotation not in CAMERA_ROTATIONS:
        raise refused(f"rotation={rotation} is not one of {CAMERA_ROTATIONS}")
    fov_deg: float | None = None
    if raw_fov := one("fov"):
        try:
            fov_deg = float(raw_fov)
        except ValueError:
            raise refused(f"fov={raw_fov!r} is not a number of degrees") from None
        if not 0.0 < fov_deg < 180.0:
            raise refused(f"fov={raw_fov!r} must be between 0 and 180")
    width = whole("width", w) if (w := one("width")) else None
    height = whole("height", h) if (h := one("height")) else None
    if (width is None) != (height is None):
        # upstream keeps the camera's own mode unless BOTH are set, so one alone would be
        # accepted here and quietly dropped there
        raise refused("width and height come together or not at all")
    return CameraSpec(
        url=url,
        name=name,
        index_or_path=index_or_path,
        width=width,
        height=height,
        fps=whole("fps", f) if (f := one("fps")) else None,
        fourcc=fourcc,
        backend=backend,
        rotation=rotation,
        fov_deg=fov_deg,
        name_given=given is not None,
    )


def parse_camera_urls(
    urls: Sequence[str], *, label: str = "real", hint: str = CAMERA_HINT
) -> tuple[CameraSpec, ...]:
    """Every `--camera-url` this arm was given, in order. The first is the primary.

    With one camera this is `parse_camera_url` and nothing more. With several, each url has
    to name its own camera and the names have to differ, because the name is what the model
    reading two pictures, a policy's observation dict and `frames/NNNN-<name>.png` all tell
    them apart by. An index may only appear once: two handles on one webcam is not two
    views, it is a camera that will not open twice. `label` and `hint` are
    `parse_camera_url`'s."""
    specs = tuple(parse_camera_url(url, label=label, hint=hint) for url in urls)
    if len(specs) < 2:
        return specs
    by_name: dict[str, CameraSpec] = {}
    by_index: dict[int | str, CameraSpec] = {}
    for spec in specs:
        if not spec.name_given:
            raise _camera_refusal(
                f"it has no ?name= and {len(specs)} cameras were given. With several, every "
                "url names its own camera, opencv://1?name=top --camera-url "
                "opencv://2?name=side, because the name is what the model, a pick policy "
                "and frames/NNNN-<name>.png tell them apart by",
                spec.url,
                label,
                hint,
            )
        if (clash := by_name.get(spec.name)) is not None:
            raise _camera_refusal(
                f"name={spec.name!r} is already the name of {clash.url!r}. With several "
                "cameras every name is its own",
                spec.url,
                label,
                hint,
            )
        if (same := by_index.get(spec.index_or_path)) is not None:
            raise _camera_refusal(
                f"{spec.index_or_path} is already {same.url!r}. One url per camera",
                spec.url,
                label,
                hint,
            )
        by_name[spec.name] = spec
        by_index[spec.index_or_path] = spec
    return specs


def step_from_env(default: float = MAX_STEP_DEG) -> float:
    """`QUACKD_LEROBOT_MAX_STEP_DEG`, or the default. A bad value is an error, not a shrug:
    silently falling back would give somebody a faster arm than they asked for."""
    raw = os.environ.get(STEP_ENV)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError:
        raise TransportError(f"{STEP_ENV}={raw!r} is not a number of degrees") from None
    if not 0.0 < value <= 180.0:
        raise TransportError(f"{STEP_ENV}={raw!r} must be above 0 and at most 180")
    return value


def check_port(port: str, *, label: str = "real") -> None:
    """A serial port, or a clear refusal. The shape is all quackd checks: which port is the
    arm is the owner's business (`up.SERIAL_PORT`), but an empty --address, or a robot name
    that never resolved, is worth catching before LeRobot opens something."""
    if not port:
        raise TransportError(f"lerobot {label}: --address must be the arm's serial port")
    if not PORT_SHAPE.match(port):
        raise TransportError(
            f"lerobot {label}: --address {port!r} is not a serial port; it looks like COM5 on "
            "Windows or /dev/ttyACM0 elsewhere"
        )


def joint_ranges(calibration: dict[str, Any]) -> dict[str, tuple[float, float]]:
    """Each joint's travel in degrees, from the arm's own calibration file.

    LeRobot records raw encoder ticks and converts with `up.DEGREES_FORMULA`, which centres
    a joint on the middle of its recorded travel. So the reachable range is half the
    recorded span either side of zero. The gripper is a 0..100 range whatever else is."""
    ranges: dict[str, tuple[float, float]] = {}
    for joint in JOINTS:
        cal = calibration.get(joint)
        if cal is None:
            continue
        if joint == "gripper":
            ranges[joint] = (GRIPPER_CLOSED, GRIPPER_OPEN)
            continue
        span = abs(float(cal.range_max) - float(cal.range_min))
        half = span / 2.0 * 360.0 / (ENCODER_TICKS - 1)
        ranges[joint] = (-half, half)
    return ranges


def motor_in_error(message: str, motors: Mapping[str, Any] | None) -> tuple[str, str] | None:
    """The motor a LeRobot bus error is about, as `(label, name)`, or None when it names none.

    LeRobot says which servo a failed write or read was for as `id_=<N>`, in a sentence that
    changes with the register, the value and the transaction result around it
    (`up.BUS_WRITE_ERROR_NAMES_THE_ID`). The id is the servo's address on the bus, which nobody
    at the arm can see; the joint is what they can put a hand on. So the id is looked up in the
    bus's own motor table (`up.BUS_MOTORS`), which is what gave each servo its address, rather
    than read off the order the follower happens to list its motors in today.

    The handshake that opens every connect names its motors differently: a servo that did not
    answer its ping, or answered as another model, is a line `- <N> (...)` in a list
    (`up.HANDSHAKE_NAMES_THE_ID`). That is the servo that stopped answering, or the cable to it
    that came out, which is the failure a further attempt will not fix and the one the person
    most needs sent to the right joint for, so the first id listed is read the same way.

    `name` is the joint, and `label` is the joint with its id, `<joint> (id <N>)`, for the
    sentence that also quotes LeRobot. An id the table does not know is still worth saying, and
    both are then `motor <N>`. A message with no id names nothing: a sync read or write is about
    several servos at once and a port that will not open is about none, and a guess would send
    somebody to the wrong cable.

    Nor does a handshake that found no motor of the arm at all (`_every_motor_missing`). That
    is what a servo supply switched off looks like, the state an arm is in after the power cut
    a session ends on, and a cable out between the board and the first servo looks the same.
    Naming the first id listed would send the person to one joint's cable for what is the whole
    arm's cables or its power, which is the guess this refuses to make."""
    if _every_motor_missing(message, motors):
        return None
    found = MOTOR_ID.search(message) or HANDSHAKE_MOTOR_ID.search(message)
    if found is None:
        return None
    motor_id = int(found.group(1))
    for joint, motor in dict(motors or {}).items():
        if getattr(motor, "id", None) == motor_id:
            return f"{joint} (id {motor_id})", str(joint)
    return f"motor {motor_id}", f"motor {motor_id}"


def _every_motor_missing(message: str, motors: Mapping[str, Any] | None) -> bool:
    """Whether LeRobot's handshake listed every motor of the arm as missing
    (`up.HANDSHAKE_NAMES_THE_ID`): each id in the bus's own motor table under "Missing motor
    IDs", and so no servo on the bus answered its ping.

    Where the table is not known, the message's own "Full expected motor list" stands in for
    it, which upstream prints from the same table. A message that is not a handshake's, or
    lists no motor as missing, or leaves one out, is not this."""
    listed = MISSING_LIST.search(message)
    if listed is None:
        return False
    missing = {int(n) for n in LISTED_ID.findall(listed.group(1))}
    ids = {getattr(motor, "id", None) for motor in dict(motors or {}).values()}
    expected = {n for n in ids if isinstance(n, int)}
    if not expected and (shown := EXPECTED_LIST.search(message)) is not None:
        expected = {int(n) for n in EXPECTED_ID.findall(shown.group(1))}
    return bool(missing) and bool(expected) and expected <= missing


CALIBRATION_CHECK = ("is_calibrated", "read_calibration")
"""The frames of the calibration check LeRobot's connect makes between the handshake and
`configure()` (`up.BUS_IS_CALIBRATED`, reached at so_follower.py line 99 whatever `calibrate`
says): reads of each motor's limits and offset, and no write."""
TORQUE_WRITERS = ("configure", "torque_disabled", "enable_torque", "disable_torque")
"""The frames every torque write of a connect is made under: `configure()`, the
`torque_disabled()` it runs in, and the two calls that switch torque off and on again
(`up.CONFIGURE_TORQUE_WRITES_ONCE`)."""


def may_have_written_torque(error: BaseException) -> bool:
    """Whether a connect attempt that failed with an open port can have written torque.

    One connect opens the port, runs the bus's handshake (a ping and a model check per motor,
    then the firmware reads), checks the calibration by reading every motor's limits and
    offset, and only once all of that has returned runs `configure()`, where every torque write
    of a connect is (`up.BUS_HANDSHAKE`, `up.BUS_IS_CALIBRATED`). So a failure placed in the
    handshake or the calibration check left every motor's torque as it found it, and is not one
    to warn a person about. The calibration check used to be missed: it runs after the
    handshake, so a read of it that lost its status packet was taken for a failure in
    `configure()`, and the person told to keep a hand under an arm nothing had written to.

    Placed off the traceback rather than the words, because each stretch fails in several of
    them (a motor check, a firmware check, any one read) and a frame is the one thing they
    share. LeRobot re-raises a serial error and a failed read from in there as its own port
    error, `from` the one that happened, so the cause's traceback is read too. Only the cause:
    an exception's context is whatever was being handled when it was raised, which says nothing
    about where.

    Three things count as a possible torque write, in this order. A write that failed
    (`failed_on_a_write`), wherever it was raised: the handshake at 0.6.1 writes nothing, and a
    LeRobot whose did would be one to warn about. A failure placed under `configure()` or its
    torque calls (`TORQUE_WRITERS`). And a failure that cannot be placed at all, which costs at
    most a warning that was not needed; the other mistake costs a person's hand under the arm."""
    if failed_on_a_write(error):
        return True
    frames = _frames_of(error)
    if frames.intersection(TORQUE_WRITERS):
        return True
    return not frames.intersection((up.BUS_HANDSHAKE.name, *CALIBRATION_CHECK))


def _frames_of(error: BaseException) -> set[str]:
    """The name of every function in the traceback of `error` and of each error it was raised
    `from` (`_cause_chain`)."""
    names: set[str] = set()
    for at in _cause_chain(error):
        frame = at.__traceback__
        while frame is not None:
            names.add(frame.tb_frame.f_code.co_name)
            frame = frame.tb_next
    return names


def failed_on_a_write(error: BaseException) -> bool:
    """Whether a connect failure is a write that failed (`WRITE_FAILED`), in its own words or
    in those of the error it was raised from: LeRobot re-raises a `ConnectionError` from inside
    its connect as its port error, and a write whose status packet was lost is one."""
    return any(WRITE_FAILED.search(str(at)) is not None for at in _cause_chain(error))


def _cause_chain(error: BaseException) -> list[BaseException]:
    """`error`, then each error it was raised `from`, in order, each once."""
    chain: list[BaseException] = []
    at: BaseException | None = error
    while at is not None and all(at is not seen for seen in chain):
        chain.append(at)
        at = at.__cause__
    return chain


def _sentence(text: str) -> str:
    """`text` ending as a sentence ends, so that what quackd says after it is the next one."""
    return text if text.endswith((".", "!", "?")) else f"{text}."


def _one_line(error: BaseException) -> str:
    """LeRobot's own words, on one line and ending as a sentence ends. Its port error starts
    and ends with a line break and has no full stop, and what quackd says after it is the next
    sentence in a transcript line or a log line."""
    return _sentence(" ".join(str(error).split()) or type(error).__name__)


def _name_of(fn: Callable[..., Any]) -> str:
    """A LeRobot call's name for a sentence, looking through the `functools.partial` that
    carries a keyword argument, because `_call` forwards positional arguments only."""
    inner = getattr(fn, "func", fn)
    return str(getattr(inner, "__name__", inner))


def _late(fn: Callable[..., Any], budget_s: float, pending: asyncio.Future[Any] | None) -> str:
    """What a LeRobot call that ran out of time did, naming the call and its budget.

    Three things look alike from the caller's side: a call that never got the bus because the
    call ahead of it held it, one still out on the wire, which wedges the transport, and one
    that came back after its budget was spent. A call the worker pool dropped unrun, which a
    pool shut down under it does, never went out either."""
    name = _name_of(fn)
    if pending is None or pending.cancelled():
        return f"a LeRobot call ({name}) waited {budget_s:g} s for the bus and never went out"
    if not pending.done():
        return (
            f"a LeRobot call ({name}) has not come back within {budget_s:g} s; the serial bus "
            "has one owner, so quackd refuses every call until it does"
        )
    return f"a LeRobot call ({name}) came back after its {budget_s:g} s were spent"


@dataclass
class _Errors:
    """Why each of the two status registers did not answer, if it did not.

    One field each, because the two are read in their own transaction and a failure on one
    says nothing about the other. `summary()` is what the observation and `doctor` show, which
    has always been "something went wrong reading the registers" and stays that."""

    torque: str | None = None
    temperature: str | None = None

    def summary(self) -> str | None:
        both = [e for e in (self.torque, self.temperature) if e]
        return "; ".join(dict.fromkeys(both)) or None


class Timing:
    """How long one kind of thing took on the wall's clock, `perf_counter`: how many there were,
    the median and the 99th percentile of the latest `STATS_WINDOW`, and the longest ever.

    Kept by the transport for every bus call (`_call`, the wait for the lock included) and
    every tick of a policy segment, and shown in the state's extras under `timing`, where a
    `pick` reports it. It changes nothing the arm does. It is what a bench session needs to
    say how fast this arm's bus and a policy's loop really are, which nothing measured before."""

    def __init__(self, window: int = STATS_WINDOW) -> None:
        self.count = 0
        self.longest_s = 0.0
        self._recent: deque[float] = deque(maxlen=window)

    def add(self, seconds: float) -> None:
        self.count += 1
        self.longest_s = max(self.longest_s, seconds)
        self._recent.append(seconds)

    def summary(self) -> dict[str, float | int]:
        """`count`, then `p50_ms`, `p99_ms` and `max_ms` once there is anything to take them
        of, each rounded to a tenth of a millisecond. Nearest rank, so a percentile is always
        a time that was measured."""
        if not self._recent:
            return {"count": self.count}
        ordered = sorted(self._recent)

        def rank(share: float) -> float:
            return ordered[max(0, math.ceil(share * len(ordered)) - 1)]

        return {
            "count": self.count,
            "p50_ms": round(rank(0.5) * 1000.0, 1),
            "p99_ms": round(rank(0.99) * 1000.0, 1),
            "max_ms": round(self.longest_s * 1000.0, 1),
        }


class Clock(Protocol):
    """The time this backend paces and watches the arm in: `now()` and `sleep()`, the pair a
    transport already exposes to every verb.

    A real arm has one time, the wall's (`WallClock`), and that is the default. The seam exists
    so that a test can run a ramp of many seconds without waiting for them: every wait this
    backend measures against `now()` goes through the same clock, the verbs' ticks, the rest
    move's, the settle before a hold is read back and the policy's own rate, so that a clock
    that only advances when it is slept keeps them all in step. The calls to LeRobot keep their
    own deadlines on the wall's time, because a thread sitting on the serial bus does not care
    what a test's clock says.

    A clock may also say it is `lockstep`: time that runs only while everyone waiting on it is
    asleep, as the simulator's does. A policy segment reads it, and on such a clock awaits the
    runner's inference with time standing still (`policy/loop.py`). It is looked up rather than
    declared here, and a clock without it is the wall's kind."""

    def now(self) -> float: ...

    async def sleep(self, seconds: float) -> None: ...


class WallClock:
    """`time.perf_counter`, and `asyncio.sleep` finished on it: the only time a real arm moves in.

    Not `time.monotonic`, which on Windows before Python 3.13 ticks every 15.6 ms (gh-88494),
    and neither is the event loop's own timer, which runs on it. That loop takes a timer due
    within one tick of its clock to be due already, so `asyncio.sleep` there wakes up to 15.6
    ms early, which is a sixth of a policy's tick. So a sleep here sleeps on the loop until
    the last `FINISH_S` and then yields to it until `perf_counter` says the time is up. Where
    the loop woke early, as it does there, it goes round again rather than end the sleep: it
    never wakes early, and on Windows its last tick of the loop's clock costs a little CPU.

    Everything that reads `now()` compares it with another `now()` and never with
    `time.monotonic`: the verbs' ticks, the rest move's budget, the gripper's trace and the
    age of a camera's frame."""

    lockstep = False
    """The wall runs whether anybody sleeps or not (`Clock`)."""

    def now(self) -> float:
        return time.perf_counter()

    async def sleep(self, seconds: float) -> None:
        due = time.perf_counter() + seconds
        while True:
            # on the loop's timer down to the last FINISH_S and then a yield at a time, and a
            # timer that woke early leaves what is left to the next pass
            left = due - time.perf_counter()
            await asyncio.sleep(left - FINISH_S if left > FINISH_S else 0)
            if time.perf_counter() >= due:
                return


class LeRobotReal:
    name = "real"
    label = "real"
    """What every refusal this backend makes says it is, after `lerobot`. The simulator runs
    this same code under its own (`sim/transport.py`), and a refusal from a rehearsal must not
    read as one from the arm on the desk."""
    mobility = "none"

    def __init__(
        self,
        address: str | None = None,
        *,
        robot: Any = None,
        policy: PolicyLike | PolicyRunner | None = None,
        robot_type: str = up.ROBOT_TYPE_SO101.name,
        robot_id: str = "arm-01",
        timeout_s: float = 1.0,
        max_step_deg: float = MAX_STEP_DEG,
        camera: CameraSpec | None = None,
        camera_object: Any = None,
        cameras: Sequence[CameraSpec] = (),
        camera_objects: Mapping[str, Any] | None = None,
        rest_pose: dict[str, float] | None = None,
        clock: Clock | None = None,
        registered_name: str | None = None,
    ) -> None:
        self.port = address or ""
        self.clock: Clock = clock if clock is not None else WallClock()
        """Whose time `now()`, `sleep()` and every paced wait in here run on (`Clock`)."""
        self.robot_type = robot_type
        self.robot_id = robot_id
        self.registered_name = registered_name
        """The name a person registered this arm under, when whoever built it said so, for the
        commands the close note gives them (`verbs.torque_left_on`). Kept apart from
        `robot_id`, which always has a value because LeRobot needs one to find a calibration,
        and whose default is not a name anybody necessarily registered."""
        self.timeout_s = timeout_s
        self.max_step_deg = max_step_deg
        self._robot: Any = robot  # injected in tests; built in connect() otherwise
        self.camera_specs: tuple[CameraSpec, ...] = (
            tuple(cameras) if cameras else ((camera,) if camera is not None else ())
        )
        self.camera_connect_s = CAMERA_CONNECT_S
        self.camera_close_s = CAMERA_CLOSE_S
        self.connect_pause_s = CONNECT_PAUSE_S
        self.connect_deadline_s = CONNECT_DEADLINE_S
        self.port_close_deadline_s = PORT_CLOSE_DEADLINE_S
        self._stop_check: Callable[[], bool] | None = None
        """What `connect()` asks, between attempts, whether a stop was asked for
        (`set_stop_check`). None is a connect nobody can stop but by cancelling it."""
        self.connect_notes: list[str] = []
        """One sentence per connect attempt that failed and was tried again, from the last
        `connect()`. Logged as it happens, and kept here for whoever narrates the session (the
        run's transcript, `doctor`'s advisories), because a retry that worked is still a bus
        that lost a packet, and a joint that does it every session is a cable to look at."""
        # name -> camera object, in the order the urls were given. Injected in tests; built
        # in connect() otherwise.
        self._cameras: dict[str, Any] = dict(camera_objects or {})
        if camera_object is not None:
            self._cameras.setdefault(self._primary_name(), camera_object)
        self.rest_pose = dict(rest_pose) if rest_pose else None
        """Where this arm rests, recorded off the arm by `quackd robot rest-pose`. The five
        body joints are what is ever driven; the gripper is kept for the record only."""
        self._rest_result: RestResult | None = None
        self.close_note: str | None = None
        """Set by `close()` when it left torque on. Said by whichever caller closed the arm,
        because a library that prints has picked one terminal and quackd has four callers."""
        runner: PolicyRunner | None = None
        if policy is not None and is_runner(policy):
            runner = cast(PolicyRunner, policy)
        elif policy is not None:
            runner = ScriptedRunner.wrapping(
                cast(PolicyLike, policy), rate_hz=POLICY_HZ, rate_source=POLICY_RATE_SOURCE
            )
        self._policy_loop: PolicyLoop | None = PolicyLoop(runner) if runner is not None else None
        """The loop every segment of this backend's policy runs in, with its own worker for the
        runner (`policy/loop.py`), or None without a policy. A `PolicyLike` is wrapped in a
        `ScriptedRunner` and runs one `act` a tick at `POLICY_HZ`, as `pick` always ran it."""
        self._segment_verb = "pick"
        """The verb the last segment was started for, which a refused verb names."""
        self.segment_s = MANIPULATE_S
        """How long `manipulate` runs a segment on this arm, in its own time: what the run
        told it from the task file (`set_segment_s`), and the default until then. The verb reads
        it and asks for it in its `do`, so the arm runs no segment the run did not size."""
        self._policy_cap_on = False
        """A policy segment's step cap may be on the follower's config: set as a segment writes
        its own, cleared once the verbs' cap reads back (`_verb_cap`). Only a cap quackd wrote
        for a segment is ever undone, so a follower handed in with a cap of its own keeps it
        until a segment has run on it."""
        self._segment_caps = 0
        """How many segments have written a cap of their own, so a restore left waiting on a
        call still on the wire never undoes a later segment's (`_cap_back`)."""
        self._lock = asyncio.Lock()
        self._closed = False
        self._wedged: asyncio.Future[Any] | None = None
        self._bus: tuple[float, float | None] = (0.0, None)
        """The bus's own clock (`_busy`): how long LeRobot calls have held it, in all, and
        when the one holding it now was handed to the worker pool, on the event loop's clock,
        or None while none is. `_call` stamps a call under the lock before its worker can
        start and the worker closes the stamp, one call at a time, and a tuple is swapped
        whole, so neither thread reads it half written."""
        self._wedged_by: asyncio.Task[Any] | None = None
        """The task a cancellation took off `_wedged`'s call, or None when the call ran out its
        time, so a stop can tell a policy segment's own call still on the wire from a bus that
        stopped answering (`_segment_call`)."""
        self._wedged_spent = 0.0
        """The bus's reading (`_busy`) at which `_wedged`'s call would have spent its budget had
        nothing cancelled it, which is how `_call` judges every call's. While that call is out
        the bus is its own and the reading climbs with the loop's clock, so the stamps say
        whether it is still inside its time however late the loop looks, and once its worker is
        back its closed stamp says it is on no wire (`_segment_call`). The longest anything
        waits for a segment's call still on the wire."""
        self._policy_task: asyncio.Task[SegmentEnd] | None = None
        self._policy_name = "idle"
        self._policy_error: str | None = None
        self._policy_stopped_by: str | None = None
        """What cancelled the last segment that something cancelled: a stop, a rest move, a
        release, a take-hold, another pick or the close (`_cancel_policy`)."""
        self._stop_count = 0
        """How many stops have come through `_cancel_policy`, a segment running or not, and
        `_last_stop` is what the latest said. A `do` compares the count across its own start,
        because a stop that lands before its segment's task exists has nothing to cancel."""
        self._last_stop = "a stop was sent to the arm"
        self._stops_in_flight = 0
        """How many stops (`_hold`) have begun and not yet returned. Each is counted in
        `_stop_count` as it begins, so a `do` that comes while one is under way takes its count
        from before it, and does not start a segment under a stop that has yet to hold."""
        self._bus_timing = Timing()
        self._tick_timing = Timing()
        self._joints: dict[str, float] = {}
        self._torque = True
        self._temperature_c: dict[str, float] = {}
        self._register_error: str | None = None
        self._torque_error: str | None = None
        """Why the torque register itself could not be read, or None when it answered.

        Kept apart from `_register_error`, which is either register, because `take_hold()` has
        to know whether *torque* is unknown rather than whether anything is."""
        self._torque_on: tuple[str, ...] = ()
        """The motors whose `Torque_Enable` read 1 in the last torque read that answered, in
        the bus's order. `_torque` is whether all of them did, which is the question a hold
        asks; a release asks the other one, whether any still does, because a motor that kept
        its torque is a joint still holding in the hands of somebody told it is limp."""
        self._torque_writes = 0
        """How many torque writes quackd has put on the bus, the releases' and the take-holds',
        counted in the worker thread that sends each one, as it starts (`_call` with
        `writes_torque`). The bus has one owner at a time (`_lock`), so this and the count a
        read notes as it goes out (`_read_all`) are in the order the bus saw them, which is the
        one order `_torque_read_back` can be judged by."""
        self._torque_read_at = -1
        """The torque-write count the last torque read that answered was taken at, or -1 before
        any has answered."""
        self._hold_written = False
        """A take-hold's torque write has been asked for since the last release went out, so the
        arm in a person's hands may be energised by it, all of it or part of it, until a read
        since that write says otherwise. Set as the write is asked for rather than once it is on
        the wire, which only ever errs toward saying quackd cannot confirm. Cleared by the next
        release. The close reads it to choose between the release's words and the take-hold's
        for an arm in a hand, and a take-hold refused before its own write reads it to say
        whether an earlier one, an interrupt landed on, may have left the arm energised."""
        self._answered = True
        """The last read of the arm came back. Cleared as each read starts and set again when it
        returns, so after a failure it says whether the arm itself stopped answering or
        something else did: a lost write after a good read is an arm that answered."""
        self._release_refused = False
        """The last release a person asked for was refused: through the second door, or through
        the first with `asked` (`quackd robot release` at the rest pose). Every motor still read
        torque on, or the arm did not answer before it. The close then says so rather than
        sending them back to the command that just failed (`verbs.TORQUE_KEPT_AFTER_REFUSAL`,
        `verbs.released_by_the_close`)."""
        self._gripper_goal: float | None = None
        self._gripper_trace: deque[tuple[float, float]] = deque(maxlen=16)
        self._policy_lock = asyncio.Lock()
        self._range_clips = 0
        self.joint_range_deg: dict[str, tuple[float, float]] = {}
        self.calibration_file: str | None = None
        self.camera_keys: tuple[str, ...] = ()
        self.camera_errors: dict[str, str | None] = {}
        self._frame_sizes: dict[str, tuple[int, int]] = {}
        self._frame_ats: dict[str, float] = {}
        self.lerobot_version: str | None = None
        self.stop_error: str | None = None
        self.stop_skipped: tuple[str, ...] = ()
        """The body joints the last hold wrote no goal for, because each read past its travel
        (`_hold`), in the order the arm reported them. Cleared at the start of every hold. The
        core `stop` verb reads it and says which, so a stop that left a joint alone is not
        reported in the same words as one that held all five."""
        self.post_sleep: Callable[[], None] | None = None
        self._in_hand = False
        """This arm is limp in somebody's hands, because `let_go()` put it there.

        Set the moment a release call goes out (`let_go`), cleared only by a release that every
        motor refused and by a `take_hold()` that confirmed torque came on. Every teardown
        begins with `stop`, which is what picks the arm back up, so the window this is true in
        is the wait itself, and past it only where the take-hold was refused
        (`_refused_hold`): a joint placed past its travel, which it leaves torque off under, a
        servo that never took torque back, or a torque write nothing read back. Then it holds
        to the close, because nothing after a refused take-hold tries again: the stop sends the
        arm nothing, the rest move refuses to move it, and no other call takes hold."""
        self._refused_hold: HandResult | None = None
        """The take-hold refused since the last release that left the arm in a hand, or None.

        Set by a `take_hold()` that refused with `_in_hand` still set, cleared by one that took
        hold and by the next release that goes out. `_hold()` reads it to leave the arm alone:
        it took hold of an arm in a hand only while nothing had refused, which is a Ctrl-C in
        the placement wait (ADR-0039), and never again after a refusal. A retry is what put
        torque on silently under a person who had just been told quackd never took hold and to
        keep hold of the arm, the moment they moved a joint back inside its travel, and a goal
        written to a limp servo instead stops nothing and stays in its register for the next
        torque write to drive to. The close reads its `energised` to say which kind of arm is
        in the person's hands: one the take-hold switched nothing on under, or one its torque
        write may have energised."""
        self._let_go_why: str | None = None
        """What the close says the arm was let go of for, when the last release said: set by
        the second door (`verbs.LET_GO_WHERE_IT_STOOD`) and by the first opened with `asked`
        (`verbs.LET_GO_AT_REST`), cleared by the first otherwise, which is `--by-hand`'s and
        leaves the close to say where the arm is against its pose."""

    def _primary_name(self) -> str:
        """The first `--camera-url`'s camera: the one the detections describe, the one
        `--fov-deg` measures, and the only one a verb that steers by sight reads."""
        return self.camera_specs[0].name if self.camera_specs else CAMERA_NAME

    @property
    def camera_spec(self) -> CameraSpec | None:
        """The primary camera, for everything written when an arm had at most one."""
        return self.camera_specs[0] if self.camera_specs else None

    @property
    def camera_error(self) -> str | None:
        """Why the primary camera gave no frame. `camera_health()` has all of them."""
        return self.camera_errors.get(self._primary_name())

    @property
    def camera_available(self) -> bool:
        return bool(self.camera_keys)

    @property
    def policy_available(self) -> bool:
        return self._policy_loop is not None

    @property
    def policy_loop(self) -> PolicyLoop | None:
        """The loop this backend's policy segments run in, or None without a policy. Read for
        what the run's record says about the policy (`PolicyLoop.record`) and for what the run's
        header names before the connect (`LeRobotAdapter.ask_policy`), and never driven from
        outside: a segment starts through a `do` and nothing else."""
        return self._policy_loop

    @property
    def policy_running(self) -> bool:
        return self._policy_task is not None and not self._policy_task.done()

    @property
    def policy_segment(self) -> asyncio.Task[SegmentEnd] | None:
        """The policy segment the last accepted `do` started, running or ended, for the verb
        that sent the `do` to wait on (`verbs.pick`, `verbs.manipulate`). Read straight after
        the acknowledgement, with nothing awaited in between, so it is that verb's own segment:
        a later `do` or a stop replaces it or clears it here, and never the task the verb
        already holds.

        The task returns how the segment ended (`verbs.SegmentEnd`), and ends cancelled when
        something else stopped it, which `policy_stopped_by` names."""
        return self._policy_task

    def set_segment_s(self, seconds: float) -> None:
        """How long each `manipulate` segment runs from now on, in this arm's time: the task
        file's `policy.segment_s`, as the run narrows the verb to it
        (`quackd.duckfile.narrow`). Anything but a finite number of seconds above 0 is a
        ValueError, and the length stays what it was."""
        self.segment_s = checked_segment_s(seconds)

    def frozen_inference_s(self, segment_s: float) -> float:
        """The wall seconds this arm's clock stands still over a segment of `segment_s` while
        its policy thinks, which the executor's timeout has to cover too. Nothing on the
        wall's clock, where the thinking happens inside the segment's own seconds; the
        simulator, whose clock waits for it, says otherwise."""
        return 0.0

    @property
    def policy_stopped_by(self) -> str | None:
        """What cancelled the last segment something else stopped, in a clause a verb can say
        after `stopped:`, or None before anything has.

        Kept until the next one, and not cleared by a `do`: a verb whose segment another pick
        cancelled can read it after that pick has started. Only a cancelled segment is ever
        asked about, and only `_cancel_policy` cancels one for anybody but the verb waiting on
        it, which is gone by then."""
        return self._policy_stopped_by

    @property
    def _torque_read_back(self) -> bool:
        """A torque read has answered since the last torque write went out, the release's or a
        take-hold's, so `_torque_on` says something about the arm as it is now rather than as
        it was before that write. The close of an arm in a hand reads `_torque_on` only when
        this is so: from a read that predates the release, every motor would be named, over an
        arm that may be limp, and from one that predates a take-hold's torque write, none
        would, over an arm that may be energised. When it is not so, the close says nothing
        read the write back (`verbs.UNREAD_IN_HAND` after a release,
        `verbs.UNCONFIRMED_IN_HAND` after a take-hold), rather than either of the two things a
        read could have said.

        Judged by the bus's order, never by where the coroutines are. It used to be a flag
        cleared in coroutine code just before each write's call and set by every read that
        answered. The run's heartbeat reads the arm on its own clock, and a heartbeat read
        queued on the bus behind a take-hold's first goal write got the bus before the torque
        write did, since the lock hands itself to whoever queued first. It set the flag after
        the take-hold had cleared it, from a read taken before the torque write, and when the
        torque register then went quiet the close told somebody holding an energised arm that
        it was limp and nothing held it up. Now a write counts itself on the bus as it goes out
        (`_torque_writes`), each read notes the count it went out at (`_torque_read_at`), and
        only a read at the current count speaks for the arm: the write makes every read before
        it stale the moment it starts, however its call ends."""
        return self._torque_read_at == self._torque_writes

    @property
    def refused_hold(self) -> HandResult | None:
        """The take-hold refused since the last release that left the arm in a hand, or None
        (`_refused_hold`), for a caller whose own record of it may be missing.

        The run reads it after the stop that opens its teardown. A take-hold that stop made and
        was refused, a Ctrl-C in the placement wait over a joint placed past its travel, is
        otherwise said to nobody: the stop swallows it into `stop_error`, which only the pilot's
        `stop` verb reads. And a run whose own take-hold an interrupt landed on has no refusal
        of its own to go by, where this one says which arm the person is holding rather than
        leave the run to assume the worst."""
        return self._refused_hold

    @property
    def in_hand(self) -> bool:
        """Whether this backend takes the arm to be in somebody's hands, because a release went
        out (`let_go`) and no hold has confirmed torque since (`take_hold`).

        Read by whoever asked for a release that an interrupt landed on, to know which side of
        the send it landed: the flag is set the moment the release call is issued, so False
        after an interrupted `let_go` is a release that never went out, and the arm's torque is
        as it was."""
        return self._in_hand

    @property
    def rest_reachable(self) -> dict[str, float]:
        """The rest goal this arm's servos can actually be driven to.

        The recorded pose's body joints, each clipped into the travel `connect()` read off this
        arm's calibration (`verbs.reachable_rest_goal`). `rest_pose` stays the pose as it was
        recorded, because the half-line rule needs to know which side of its limit a clipped
        joint was folded to. Before `connect()` no calibration has been read, so this is the
        recorded pose unchanged; nothing drives the arm before then."""
        return reachable_rest_goal(self.rest_pose or {}, self.joint_range_deg)[0]

    @property
    def rest_clipped(self) -> tuple[Clip, ...]:
        """Every joint the recorded pose puts past its travel, as `(joint, recorded,
        reachable)`. The adapter hands these to the manifest, which is how a run's record
        learns the pose it will park in is not the pose it was recorded in."""
        return reachable_rest_goal(self.rest_pose or {}, self.joint_range_deg)[1]

    def _rest_target(self) -> tuple[dict[str, float], dict[str, float]]:
        """`(goal, recorded)`: what every judgement of the rest pose drives to and judges by,
        the reachable goal (`rest_reachable`) and the pose as recorded (`verbs.rest_goal`),
        which the half-line rule reads the side of a clipped joint from. One place, so the
        rest move, the release, the take-hold and the close all judge the same pose, and the
        simulator can put a joint its start settled out of the table where the arm really
        rests (`sim.world.ArmWorld.settled`)."""
        return self.rest_reachable, rest_goal(self.rest_pose or {})

    def _outside_travel(self, joint: str, reading: float) -> bool:
        """The joint reads strictly outside the travel its calibration recorded.

        Only a goal is clamped to the travel, never a reading, so this is an arm folded or
        placed there with torque off, or one the servo parked at its limit that then sagged a
        little past it under its own weight. Either way the one goal the servo would accept
        for this joint is its limit, and writing that moves the joint away from where it is.
        A joint with no known range is never outside it."""
        span = self.joint_range_deg.get(joint)
        return span is not None and not span[0] <= reading <= span[1]

    # ── plumbing ────────────────────────────────────────────────────────────────────

    async def _call(
        self,
        fn: Callable[..., Any],
        *args: Any,
        deadline_s: float | None = None,
        writes_torque: bool = False,
    ) -> Any:
        """One LeRobot call at a time (thread safety is UNVERIFIED), each with a deadline.

        A call that blows its deadline is not over: the worker thread is still sitting on a
        half-duplex serial bus waiting for a reply. Releasing the lock and starting another
        would put two talkers on that bus, so the transport stays wedged until the thread
        comes back, and says so instead. The arm holds its last goal meanwhile, which is the
        one thing that needs no rescuing (`up.NO_CLIENT_DEADMAN`).

        `writes_torque` marks a release's or a take-hold's torque write, which is counted in
        the worker thread as it starts (`_torque_writes`), under the lock, so that no read the
        bus answered before it can speak for the arm afterwards. Counted there and not before
        the call, because the lock is fair: a read queued behind the call ahead of this one is
        on the wire before this write is, and a count taken in coroutine code would put it
        after.

        A budget is spent by the bus's time, not the event loop's. The loop's thread can be
        busy past a deadline: a pilot's SDK parsing its first response, or a frame being
        encoded. A call out on the bus then answers in a millisecond, or the call ahead of a
        queued one comes back in time and the loop does not hand the queued one the bus, and
        the deadline fired first when the loop resumed: a heartbeat the arm answered in time
        was reported as an arm that did not answer, which ends the run. So what spends a
        budget is time the bus was busy after the call was asked, with the calls ahead of it
        while it waited and then with its own, each from when it was handed to the worker pool
        until its worker came back, stamped on the loop's clock (`_bus`). From the hand-off
        and not from when a thread picks it up, because with every pool thread busy (a camera
        read, a frame being saved) the call waits for one holding the bus, and goes out when
        one frees: it spends its own budget meanwhile, and the calls queued behind it spend
        theirs. When the deadline fires it reads those stamps, and a call with budget left
        keeps its place in the queue while its deadline moves on by what is left. A call still
        out when its budget is spent is wedged and refused exactly as before, one that spent it
        waiting behind calls on the bus never goes out, and every timeout says which call it
        was and its budget.

        Every call is timed on `perf_counter`, the wait for the lock included, however it ends
        (`Timing`, the state's `timing.bus_call`).

        A call a policy segment was cancelled in the middle of is waited for before the check,
        here and once the lock is this call's, inside this call's own budget, as a call ahead
        of it on the lock would be (`_outwait_segment_call`), rather than refused on: it is the
        segment's own read or send, and the stop that cancelled it is no bus that stopped
        answering. That call holds the bus while it is out, so waiting for it spends this
        call's budget as waiting behind any call on the bus does, and a call whose budget it
        spent never goes out."""
        loop = asyncio.get_running_loop()
        started = time.perf_counter()
        budget = deadline_s or self.timeout_s
        asked = loop.time()
        bus_asked = self._busy(asked)

        def budget_end() -> float:
            # when this call's budget runs out on the loop's clock, should the bus stay busy
            # from now on, which it is while a call is out on it
            now = loop.time()
            return now + budget - (self._busy(now) - bus_asked)

        await self._outwait_segment_call(budget_end())
        self._refuse_if_wedged()
        if budget_end() <= loop.time():
            # the segment's call it waited for spent its budget on the bus, and the loop's
            # thread was busy past it until that call came back: it does not go out, as a call
            # that spent its budget queued on the lock does not, and nothing below would judge
            # it before the free lock handed it the bus
            raise TimeoutError(_late(fn, budget, None))
        pending: asyncio.Future[Any] | None = None
        spent = False
        timer: asyncio.TimerHandle | None = None
        call = functools.partial(fn, *args)
        if writes_torque:
            call = functools.partial(self._counted, call)

        def stamped(handed: float) -> Any:
            try:
                return call()
            finally:
                # `loop.time()` reads the monotonic clock, which is safe from any thread
                self._bus = (self._bus[0] + loop.time() - handed, None)

        def judge(deadline: asyncio.Timeout) -> None:
            nonlocal timer, spent
            now = loop.time()
            left = budget - (self._busy(now) - bus_asked)
            if left > 0:
                timer = loop.call_at(now + left, judge, deadline)
                return
            spent = True
            deadline.reschedule(now)  # which cancels this task on the loop's next turn

        try:
            async with asyncio.timeout(None) as deadline:
                timer = loop.call_at(asked + budget, judge, deadline)
                async with self._lock:
                    # a caller parked on the lock passed the check above before the call
                    # ahead of it wedged; the lock's release is what woke it, so ask again,
                    # and wait out a segment's call as above
                    await self._outwait_segment_call(budget_end())
                    self._refuse_if_wedged()
                    if spent:
                        # its budget ran out while it waited, and the lock reached it before
                        # the cancel did: it does not go out
                        raise TimeoutError
                    # the bus is this call's from here; stamped before the pool can start the
                    # worker, which closes the stamp, and any stamp a call the pool dropped
                    # unrun left open is closed into the total first
                    now = loop.time()
                    self._bus = (self._busy(now), now)
                    pending = loop.run_in_executor(None, stamped, now)
                    answer = await asyncio.shield(pending)
                    if spent:
                        # it came back after its budget, and reached this task before the
                        # cancel did
                        raise TimeoutError
                    return answer
        except (TimeoutError, asyncio.CancelledError) as e:
            # a cancelled verb (Ctrl-C mid-move) leaves its thread on the wire exactly as a
            # timed-out one does, and the stop that follows must not join it there
            if pending is not None and not pending.done():
                self._wedged = pending
                self._wedged_by = (
                    asyncio.current_task() if isinstance(e, asyncio.CancelledError) else None
                )
                # where on the bus's clock it would have run out had nothing cancelled it,
                # judged from its stamps as the deadline above judges it
                self._wedged_spent = bus_asked + budget
                self.stop_error = (
                    f"a LeRobot call ({_name_of(fn)}) has not come back; the "
                    "serial bus has one owner, so quackd refuses every call until it does"
                )
            if spent and isinstance(e, TimeoutError):
                raise TimeoutError(_late(fn, budget, pending)) from None
            # a call that raised in time, a TimeoutError of its own among them, raises what
            # it raised
            raise
        finally:
            if timer is not None:
                timer.cancel()
            self._bus_timing.add(time.perf_counter() - started)

    def _busy(self, now: float) -> float:
        """How long LeRobot calls have held the bus up to `now`, the one holding it now
        included, on the event loop's clock (`_bus`): the time that spends a call's budget
        (`_call`)."""
        total, since = self._bus
        return total if since is None else total + max(0.0, now - since)

    def _counted(self, write: Callable[[], Any]) -> Any:
        """A torque write, in `_call`'s worker thread: counted before it goes out, so a write
        that raises part way, or one an interrupt lands on while its thread goes on writing, is
        counted all the same, since some of it may have reached a motor."""
        self._torque_writes += 1
        return write()

    def _refuse_if_wedged(self) -> None:
        if self._wedged is None:
            return
        if not self._wedged.done():
            raise TransportError(self.stop_error or "a LeRobot call has not come back")
        self._wedged = None
        self.stop_error = None

    def _segment_call(self) -> asyncio.Future[Any] | None:
        """The call a policy segment was cancelled in the middle of, while its future is not
        yet done and the bus's stamps say it is inside its own time, or None.

        Cancelling a segment, by a stop, the verb waiting on it or the next `do`, leaves the
        call it was in on the wire, which `_call` files as a wedge. That thread is the
        segment's own (`_wedged_by`), and on an arm that answers it comes back at once, so
        whatever lands behind it, the stop's own hold, the executor's read before a verb or a
        heartbeat, waits for it as it would have waited for it on the lock
        (`_outwait_segment_call`), rather than be refused. Past its own budget it is what it
        would have been had nothing cancelled it, a bus that stopped answering, and so is a
        call that ran out its time or one anything else left: none of those is waited on.

        Judged by the stamps `_call` judges every call by (`_bus`), never by when the loop
        looks or by the future. The worker closes its call's stamp as it comes back, on the
        loop's clock, but the future is only marked done on a later turn of the loop, and a
        loop busy past the call's budget, a pilot's SDK or a frame being encoded, used to find
        a call that had come back in time neither done nor inside its time, and refuse a
        heartbeat on an answer the bus gave. A call whose worker is back is on no wire, and
        nothing else goes out while it is filed here, so the closed stamp is its own. One
        still out is inside its time until the bus has been busy for its budget
        (`_wedged_spent`), which is when the loop's clock says it is, since the bus is its own
        while it is out."""
        left, by = self._wedged, self._wedged_by
        if left is None or left.done() or by is None or by.get_name() != POLICY_TASK:
            return None
        if self._bus[1] is None:
            return left
        if self._busy(asyncio.get_running_loop().time()) >= self._wedged_spent:
            return None
        return left

    async def _outwait_segment_call(self, until: float | None = None) -> None:
        """Wait for `_segment_call` to come back, no longer than its own budget, which is when
        everything behind it would have been refused had nothing cancelled it, and no longer
        than `until`, the waiting call's own, on the event loop's clock. So an arm that stopped
        answering under it is found no later than it was.

        Every wake is judged again by the stamps (`_segment_call`), not by what ended the wait:
        a wait the loop ran late, or woke early (gh-88494), finds a call that is back and waits
        the turn its future needs, and finds one still out past its budget refused. A call
        whose worker is back is waited for with no deadline, since its future is done on the
        loop's next turn however late that turn comes."""
        loop = asyncio.get_running_loop()
        while (left := self._segment_call()) is not None:
            if self._bus[1] is None:
                await asyncio.wait({left})
                return
            now = loop.time()
            end = now + self._wedged_spent - self._busy(now)
            if until is not None:
                end = min(end, until)
            if end <= now:
                return
            await asyncio.wait({left}, timeout=end - now)

    def _config_kwargs(self) -> dict[str, Any]:
        """Every safety-shaped field of `up.SO_CONFIG`, spelled out.

        Inheriting a default is fine until upstream changes one. `max_relative_target` is the
        field upstream leaves at None, and it has to be a float, not an int
        (`up.SO_ACTION_CLAMP_IS_FLOAT`).

        `disable_torque_on_disconnect` is False, the opposite of upstream's default, because
        quackd's own disconnects are not the only ones. LeRobot disconnects a follower that is
        still connected when it is collected (`up.ROBOT_DEL`), so under True any exit that
        skipped `close()`, a second Ctrl-C during the rest move or a crash, could let the arm
        fall wherever it stood. `close()` and `_give_up` write the flag just before their own
        disconnect (`up.SO_DISCONNECT_READS_ITS_CONFIG_LATE`), and no other disconnect lets
        go: a connect that fails any other way once the arm is energised closes the port and
        keeps the torque (`_keep_over_a_failure`)."""
        return {
            "port": self.port,
            "id": self.robot_id,
            "use_degrees": True,
            "disable_torque_on_disconnect": False,
            # quackd owns its camera instead: up.SO_CAMERAS_ARE_THE_FOLLOWERS
            "cameras": {},
            "max_relative_target": float(self.max_step_deg),
        }

    def _build_robot(self) -> Any:
        try:
            import lerobot
            from lerobot.robots import make_robot_from_config
            from lerobot.robots.so_follower import SO101FollowerConfig
        except ImportError as e:
            raise AdapterNotInstalled("lerobot", "quackd[lerobot]") from e
        self.lerobot_version = getattr(lerobot, "__version__", None)
        check_port(self.port, label=self.label)
        if self.robot_type != up.ROBOT_TYPE_SO101.name:
            raise TransportError(f"lerobot {self.label}: only {up.ROBOT_TYPE_SO101.name} is wired")
        config = SO101FollowerConfig(**self._config_kwargs())
        return make_robot_from_config(config)

    def _build_camera(self, spec: CameraSpec) -> Any:
        """One webcam, built by quackd and not by the follower (`up.OPENCV_CAMERA`)."""
        try:
            from lerobot.cameras import ColorMode, Cv2Backends, Cv2Rotation
            from lerobot.cameras.opencv import OpenCVCamera, OpenCVCameraConfig
        except ImportError as e:  # up.CAMERA_EXPORTS: the config is not in lerobot.cameras
            raise AdapterNotInstalled("lerobot", "quackd[lerobot]") from e
        return OpenCVCamera(
            OpenCVCameraConfig(
                index_or_path=spec.index_or_path,
                fps=spec.fps,
                width=spec.width,
                height=spec.height,
                color_mode=ColorMode.RGB,
                rotation=Cv2Rotation(spec.rotation if spec.rotation != 270 else -90),
                fourcc=spec.fourcc,
                backend=Cv2Backends[spec.backend.upper()],
            )
        )

    # ── protocol ────────────────────────────────────────────────────────────────────

    async def connect(self) -> None:
        self._closed = False
        self.connect_notes = []
        if self._robot is None:
            self._robot = await asyncio.to_thread(self._build_robot)
        with contextlib.suppress(Exception):
            # Asked for again on every connect, and not only at the build: a close at rest and
            # a refused connect both leave the flag asking for the release, and a transport
            # connected a second time would carry that into a session whose exit may skip the
            # close. A robot handed in, which is built by whoever handed it, is asked too.
            self._robot.config.disable_torque_on_disconnect = False
        # the camera first, before the arm is touched: a bad index then refuses with the
        # arm never energised, never de-torqued on the way back out, and nothing to undo
        await self._connect_cameras()
        # then whether a policy served elsewhere fits this arm, still before any torque
        await self._fit_policy()
        await self._connect_arm()
        # From here the arm is energised. quackd's own refusals let it go (`_give_up`), and
        # anything else that raises before the connect is done keeps it (`_keep_over_a_failure`)
        # rather than leaving the choice to whatever disconnects the follower later.
        try:
            refusal = self._refusal()
            if refusal is None:
                calibration = dict(getattr(self._robot, "calibration", None) or {})
                self.joint_range_deg = joint_ranges(calibration)
                path = getattr(self._robot, "calibration_fpath", None)
                self.calibration_file = str(path) if path else None
                # the follower is built with cameras={}, so its observation_features never
                # name one; the only camera here is the one quackd opened
                # (up.SO_CAMERAS_ARE_THE_FOLLOWERS)
                await self._probe()
        except Exception as e:
            await self._keep_over_a_failure(e)
        if refusal is not None:
            await self._give_up(refusal)

    def _refusal(self) -> str | None:
        """Why quackd will not drive the arm LeRobot has just connected, or None. A refusal
        here is quackd's own decision about an arm that answered, and lets go of it
        (`_give_up`). A check that raises instead is a failure, and keeps the arm's torque
        (`_keep_over_a_failure`)."""
        if not bool(self._robot.is_calibrated):
            return (
                f"lerobot {self.label}: the arm is not calibrated; run LeRobot's calibration "
                "first (it is interactive, quackd never triggers it)"
            )
        if not dict(getattr(self._robot, "calibration", None) or {}):
            return (
                f"lerobot {self.label}: the arm reports no calibration file, so nothing knows "
                "how far each joint travels; run LeRobot's calibration first"
            )
        if getattr(self._robot, "bus", None) is None:
            return (
                f"lerobot {self.label}: this robot has no motors bus, so torque and "
                "temperature cannot be read; quackd drives an SO-101 follower and nothing else"
            )
        return None

    async def _keep_over_a_failure(self, error: Exception) -> NoReturn:
        """Refuse a connect that failed once the arm was energised, keeping its torque.

        The follower is built asking its disconnect to keep torque (`_config_kwargs`), so an
        arm left connected here would be held by whatever disconnected it later, LeRobot's
        own as the follower is collected included (`up.ROBOT_DEL`), and nothing would have
        said so. So the port is closed now with every motor still holding its goal
        (`_close_port`, which writes nothing to a motor), the cameras are let go of, and the
        refusal says the arm is still energised and what to do about it
        (`KEPT_OVER_A_FAILED_CONNECT`). A read that blew its deadline has wedged the
        transport, and then the port is not closed, but the torque is kept all the same."""
        said = _one_line(error) if str(error).strip() else self.stop_error or type(error).__name__
        await self._close_port()
        await self._close_cameras()
        raise TransportError(
            f"lerobot {self.label}: connect failed once the arm was energised: "
            f"{_sentence(said)} {KEPT_OVER_A_FAILED_CONNECT}"
        ) from error

    async def _connect_arm(self) -> None:
        """LeRobot's connect, tried again when the bus loses a packet. Raises TransportError.

        One attempt is `connect(calibrate=False)`: open the port, ping the motors, then
        `configure()`, which switches torque off on every motor, writes its settings, and
        switches it back on, a `Torque_Enable` and a `Lock` write per motor and each tried once
        (`up.CONFIGURE_TORQUE_WRITES_ONCE`). One status packet lost in any of those fails the
        attempt with the port still open, and every later `connect()` is then refused as
        already connected (`up.SO_CONNECT_REFUSES_WHILE_OPEN`). So between attempts the port is
        closed through the bus with `disable_torque` False (`_close_port`), which writes nothing
        to any motor. The follower's own `disconnect()` would first switch torque off on every
        motor again: two more writes per motor on the bus that has just lost one, each able to
        fail the same way, and dropping the motors the failed attempt had just re-energised. The
        next attempt's `configure()` leaves every motor's torque where any connect leaves it.

        Never tried again: a call that blew its deadline, because its thread is still on the
        wire and `_call` has wedged the transport (a retry would only be refused, and a second
        talker on a half-duplex bus is how packets get lost), and quackd's own refusal of a
        wedged transport. The cameras opened before this and stay open across attempts: they
        are not on the serial bus, and reopening one costs its warmup for nothing.

        Each retry is said twice over, as a WARNING while it happens and in `connect_notes`
        for whoever narrates the session afterwards. When every attempt fails, the port is
        closed the same way, the cameras are let go of, and the refusal carries LeRobot's own
        words, the joint they name, whether the arm may be left half energised, and what to
        check.

        "May be left half energised" is kept across the attempts (`split`), because a later
        attempt that fails before it writes anything leaves the motors as the earlier one left
        them. An attempt can have written torque when it failed on a write, inside
        `configure()`, or somewhere nothing can place; one whose port never opened, or that
        failed in the handshake or the calibration check before `configure()`, wrote nothing
        (`may_have_written_torque`). A connect that ends on a timeout says it too, whatever came
        before: an attempt still on the wire is somewhere in the handshake, the calibration
        check or `configure()`, and nothing says which, and one LeRobot timed out itself could
        have been in any of them.

        A stop asked for while the attempts fail ends them (`set_stop_check`). It is looked
        for once an attempt has failed and its port is closed, and throughout the pause before
        the next, and a stop found there refuses the connect on the spot with the cameras let go
        of, rather than making another attempt, each of which is a `configure()` that switches
        torque off every motor and on again, for a person who has just asked for things to
        stop. A stop asked for during an attempt that then connects is not this method's: the
        connect is done, and the caller's own teardown is what answers it.
        """
        split = False  # whether any attempt can have left the motors in two torque states
        for attempt in range(1, CONNECT_ATTEMPTS + 1):
            try:
                # never calibrate: calibration is interactive (up.ROBOT_CALIBRATE)
                await self._call(self._robot.connect, False, deadline_s=self.connect_deadline_s)
                return
            except (TimeoutError, TransportError) as e:
                # Not tried again. The port is still closed on the way out where that can be
                # done: `_call` refuses it on a wedged transport, which is the case where a
                # thread still owns the port, and does it for a timeout LeRobot raised itself.
                # The reason is read first: a wedge that clears in the meantime clears it too.
                why = str(e) or self.stop_error or type(e).__name__
                await self._close_port()
                await self._close_cameras()
                said = f"lerobot {self.label}: connect failed: {why}"
                if split or isinstance(e, TimeoutError):
                    said = f"{_sentence(said)} {SPLIT_TORQUE}"
                raise TransportError(said) from e
            except Exception as e:
                port_was_open = self._port_open()
                wrote = port_was_open and may_have_written_torque(e)
                split = split or wrote
                where = motor_in_error(
                    str(e), getattr(getattr(self._robot, "bus", None), "motors", None)
                )
                await self._close_port()
                if attempt == CONNECT_ATTEMPTS:
                    await self._close_cameras()
                    raise TransportError(self._connect_refusal(e, where, split=split)) from e
                stopped = self._connect_stopped(
                    e, where, attempt, port_was_open=port_was_open, split=split
                )
                if self._stop_asked():
                    # asked for while this attempt was failing: nothing more goes on the bus
                    await self._close_cameras()
                    raise TransportError(stopped) from e
                then = (
                    "The port was closed without a write to any motor"
                    if port_was_open
                    else "The port never opened, so nothing reached a motor"
                )
                note = (
                    f"connect attempt {attempt} of {CONNECT_ATTEMPTS} failed"
                    + (f" on {where[0]}" if where else "")
                    + f": {_one_line(e)} {then}, and connect runs again"
                )
                self.connect_notes.append(note)
                logger.warning("%s", note)
                if await self._paused_until_stopped():
                    await self._close_cameras()
                    raise TransportError(stopped) from e

    def _connect_refusal(
        self, error: Exception, where: tuple[str, str] | None, *, split: bool
    ) -> str:
        """What the person at the arm reads when every connect attempt failed.

        LeRobot's words first, because they are the evidence. Then the state the arm may be in:
        an attempt that failed on a write, or inside `configure()`, may have stopped anywhere in
        its torque writes, which go off on every motor and back on one motor at a time, so the
        motors before the failed write can be holding and the ones after it limp. Nothing quackd
        can write fixes that on a bus that will not answer, so it is said instead
        (`SPLIT_TORQUE`), and only when an attempt can have written torque (`split`): one that
        never opened the port wrote nothing, and neither did one refused before `configure()`
        began, in the handshake (a servo that did not answer its ping among them) or in the
        calibration check after it, which ping and read and write nothing. Then what to look
        at: the cable of the joint LeRobot named, where it named one, with the servo supply,
        since a servo with no power fails the same way as a cable that came out, and whatever
        else might be holding the port, because the bus has one owner at a time. Where LeRobot
        named no joint, every motor it had missing included, the arm's cables and power."""
        head = f"lerobot {self.label}: connect failed {CONNECT_ATTEMPTS} times"
        said = [f"{head}, the last on {where[0]}" if where else head]
        said[0] += f": {_one_line(error)}"
        if split:
            said.append(SPLIT_TORQUE)
        look = (
            f"{where[1]}'s cable and connectors, that the servo supply is on,"
            if where
            else "the arm's cables and power,"
        )
        said.append(
            f"Check {look} and that nothing else has {self.port or 'the port'} open (a "
            "teleoperation, a recording or a serial monitor), then connect again."
        )
        return " ".join(said)

    def _connect_stopped(
        self,
        error: Exception,
        where: tuple[str, str] | None,
        attempt: int,
        *,
        port_was_open: bool,
        split: bool,
    ) -> str:
        """What the person reads when a stop was asked for while the connect was failing.

        Said as a stop and not as a fault: the connect was not tried again because a person
        asked for things to stop, and the attempts it did make are no verdict on the cable. The
        last attempt's failure is still quoted, LeRobot's words and the joint they name, because
        it is what happened to the arm, and the half-energised warning still follows it where
        an attempt can have written torque (`split`), for `_connect_refusal`'s reason. And what
        quackd did last: it closed the port, with no write to any motor, and sent nothing
        more."""
        then = (
            "The port was closed without a write to any motor"
            if port_was_open
            else "The port never opened, so nothing reached a motor"
        )
        said = [
            f"lerobot {self.label}: connect stopped after attempt {attempt} of "
            f"{CONNECT_ATTEMPTS}, because a stop was asked for.",
            f"Attempt {attempt} failed" + (f" on {where[0]}" if where else "") + ":",
            _one_line(error),
        ]
        if split:
            said.append(SPLIT_TORQUE)
        said.append(f"{then}, and connect was not tried again.")
        return " ".join(said)

    def set_stop_check(self, check: Callable[[], bool] | None) -> None:
        """Give `connect()` a way to hear that a stop was asked for, or take it away.

        A connect that is failing tries again (`CONNECT_ATTEMPTS`), and each attempt is a
        `configure()` that switches torque off on every motor and back on. Nothing else can
        reach the retries: the kill switch's first press sets the run's abort flag and cancels
        nothing, so a person who pressed Ctrl-C while the first attempt failed used to get the
        second and the third anyway, and on a later one that connected, the rest of the run's
        start. The agent loop hands its abort flag's `is_set` over before it connects, read
        with `getattr` so that no other body has to carry this. `check` is called with no
        arguments and anything true is a stop; one that raises is taken as no stop, because a
        broken check must not make a connect nobody stopped refuse."""
        self._stop_check = check

    def _stop_asked(self) -> bool:
        """Whether the check `set_stop_check` gave says a stop was asked for."""
        check = self._stop_check
        if check is None:
            return False
        try:
            return bool(check())
        except Exception:
            return False

    async def _paused_until_stopped(self) -> bool:
        """The pause between two attempts (`connect_pause_s`), watching for a stop throughout
        it, a tick at a time (`TICK_S`). True when one was asked for, before the pause or in
        it, which ends the pause there."""
        left = self.connect_pause_s
        while left > 0:
            if self._stop_asked():
                return True
            step = min(TICK_S, left)
            await asyncio.sleep(step)
            left -= step
        return self._stop_asked()

    def _port_open(self) -> bool:
        """Whether the serial port is open, off the port's own flag (`up.BUS_IS_CONNECTED`).

        Read after an attempt failed, to know whether it can have written to a motor. LeRobot
        opens the port before it writes anything, and nothing in its connect closes it again
        on the way out of a failure, so a closed port is an attempt that never reached the
        servos. A flag and not a transaction, so not under `_call`. Unknown reads as open,
        which costs at most a warning that was not needed."""
        try:
            return bool(self._robot.bus.is_connected)
        except Exception:
            return True

    async def _close_port(self) -> None:
        """Close the serial port and write nothing to any motor. Never raises.

        `MotorsBus.disconnect(disable_torque=False)` (`up.BUS_DISCONNECT`): its torque-off is
        inside `if disable_torque`, so this is the port's close and nothing else. It refuses a
        port that is already shut (`check_if_not_connected`), which is an attempt that never
        opened one, and a shut port is what this was going to leave anyway. Through `_call`,
        because a close is still a call on the serial handle, and inside a function of no
        arguments, because `_call` forwards positional arguments only.

        Then the port handler's busy flag is cleared, which upstream's own disconnect does too,
        and inside the same `if disable_torque` that this close skips (`up.BUS_DISCONNECT`). The
        servo SDK raises that flag before every packet it sends and lowers it once the reply is
        in, and a serial error in between (a USB glitch in a write or a read) leaves it raised.
        Reopening the port does not lower it, so every packet of every later attempt would be
        answered "port in use" without reaching the wire, the handshake would find no motor at
        all, and the refusal would name every motor as missing when not one had been reached,
        in the one passing fault the retry is there to absorb. A flag on the handler and not a
        transaction, so it writes nothing to a motor.

        Cleared in the same call as the close, straight after it in the same worker thread, so
        only while this close holds the bus's lock and only once the port has actually shut.
        Not while another call's thread is on the wire: the flag is that thread's. Clearing it
        after the call instead, whenever the transport was not wedged, cleared it too when the
        close had run out of time still waiting for the lock, which leaves nothing wedged, in
        the middle of the packet of whichever call was holding the lock."""
        bus = getattr(self._robot, "bus", None)
        if bus is None:
            return

        def disconnect() -> None:
            # named for the LeRobot call in it, which is what a wedge names (`_name_of`): a
            # close that has not come back is stuck in the port's close
            bus.disconnect(disable_torque=False)
            with contextlib.suppress(Exception):
                bus.port_handler.is_using = False

        with contextlib.suppress(Exception):
            await self._call(disconnect, deadline_s=self.port_close_deadline_s)

    async def _camera_call(self, fn: Callable[..., Any], *args: Any, timeout_s: float) -> Any:
        """A camera call: its own thread and its own deadline, and never the serial lock.

        The camera touches no bus, so it has no business under `_lock`. Worse than needless:
        `_call` files a call that blows its deadline as a wedged serial thread and refuses
        every later call until it returns, so a webcam whose open or release takes a few
        seconds (routine on Windows) would have refused the arm's own disconnect and left
        torque on. A camera that hangs here is simply abandoned, and says so."""
        try:
            return await asyncio.wait_for(asyncio.to_thread(fn, *args), timeout_s)
        except TimeoutError:
            raise TimeoutError(
                f"{getattr(fn, '__name__', 'the call')}() did not return within {timeout_s:g} s"
            ) from None

    async def _connect_cameras(self) -> None:
        """A camera the owner asked for and did not get is a refusal, not a warning.

        They named an index on the command line and `doctor` gates its verdict on a frame,
        so failing quietly would leave somebody believing they had eyes. It runs before the
        arm is touched, so a refusal here has energised nothing and let nothing go slack; the
        arm connects without --camera-url. With several, they open in the order the urls were
        given and the second one failing lets go of the first: half a set of eyes nobody
        asked for is worse than the refusal, because the frames would still arrive."""
        if not self.camera_specs:
            return
        self.camera_keys = ()
        self.camera_errors = {}
        self._frame_sizes = {}
        self._frame_ats = {}
        opened: list[str] = []
        for spec in self.camera_specs:
            try:
                if spec.name not in self._cameras:
                    self._cameras[spec.name] = await asyncio.to_thread(self._build_camera, spec)
                # warmup reads frames before it returns (up.CAMERA_CONNECT); an index that
                # will not open raises here, with upstream's own instructions in it
                # (up.OPENCV_OPEN_FAILS)
                await self._camera_call(
                    self._cameras[spec.name].connect, timeout_s=self.camera_connect_s
                )
            except AdapterNotInstalled:
                raise
            except Exception as e:
                await self._close_cameras()
                raise TransportError(
                    f"lerobot {self.label}: --camera-url {spec.url!r} did not open: {e}. The "
                    "arm was not touched, and it connects without --camera-url"
                ) from e
            opened.append(spec.name)
        self.camera_keys = tuple(opened)

    async def _fit_policy(self) -> None:
        """Ask a policy served by another process whether it fits this arm, and refuse the
        connect when it does not, before a motor is energised.

        Only a runner that can be asked (`RemoteRunner.fit_arm`) is: a policy object handed in
        is the caller's own. It is told the arm as the files and the hardware say it is, never
        as anybody typed it: the bus's motors in the bus's order, each camera's size from a
        frame it gave just now, and each joint's travel from the calibration LeRobot loaded
        when the follower was built (`up.ROBOT_CALIBRATION_ATTR`), with `OUT_OF_RANGE_DEG` of
        reading past it forgiven, as everywhere else. What the policy is and what the server
        loaded go into `connect_notes`, which the run's record keeps. A misfit, a server that
        cannot be asked, or a camera with no frame refuses with the cameras let go of and the
        arm untouched. A robot with no bus is left to `_refusal`, which says so after."""
        loop = self._policy_loop
        fit_arm = getattr(loop.runner, "fit_arm", None) if loop is not None else None
        bus = getattr(self._robot, "bus", None)
        if loop is None or not callable(fit_arm) or bus is None:
            return
        from quackd_lerobot.policy.protocol import CameraInfo

        rotations = {spec.name: spec.rotation for spec in self.camera_specs}
        try:
            cameras = []
            for name in self.camera_keys:
                frame = np.asarray(
                    await self._camera_call(
                        self._cameras[name].read_latest, timeout_s=self.camera_connect_s
                    )
                )
                size = {"height": int(frame.shape[0]), "width": int(frame.shape[1])}
                rotation = rotations.get(name, 0)
                cameras.append(
                    CameraInfo.model_validate({"name": name, **size, "rotation": rotation})
                )
            travel = joint_ranges(dict(getattr(self._robot, "calibration", None) or {}))
            motors = tuple(str(m) for m in bus.motors)
            notes = await loop.call(
                fit_arm,
                motors,
                cameras,
                travel,
                OUT_OF_RANGE_DEG,
                within=getattr(loop.runner, "answer_within_s", None),
            )
        except Exception as e:
            await self._close_cameras()
            raise TransportError(
                f"lerobot {self.label}: {_one_line(e)} The arm was not touched"
            ) from e
        self.connect_notes.extend(str(note) for note in notes)

    async def _close_cameras(self) -> None:
        for camera in list(self._cameras.values()):
            with contextlib.suppress(Exception):
                # up.CAMERA_DISCONNECT
                await self._camera_call(camera.disconnect, timeout_s=self.camera_close_s)
        self.camera_keys = ()

    async def _give_up(self, why: str) -> None:
        """Let go of everything opened so far, then say why. The arm's disconnect is the one
        LeRobot ships, asked to drop torque (`up.SO_DISCONNECT_TORQUE`), which is what a
        refusal of a freshly built follower has always done. The follower is built asking it
        to keep torque (`_config_kwargs`), so the flag is written here, just before the call
        that reads it (`up.SO_DISCONNECT_READS_ITS_CONFIG_LATE`). One refusal changed with
        that: a transport connected again after a close that kept torque used to carry that
        close's flag into this disconnect, and keep the arm energised with nothing said, and
        now lets go like any other."""
        await self._close_cameras()
        with contextlib.suppress(Exception):
            self._robot.config.disable_torque_on_disconnect = True
        with contextlib.suppress(Exception):
            await self._call(self._robot.disconnect, deadline_s=5.0)
        raise TransportError(why)

    async def close(self) -> None:
        """Let go of the arm, and let go of its torque only where it can be let go of.

        LeRobot's `disconnect()` disables torque where its config asks, which is upstream's
        default, and this close asks for it over an arm at rest: an arm at rest should be limp,
        because that is what "at rest" means. An arm that is not at rest is an arm that would
        fall, so this reads the joints one last time and, where they are not the recorded pose,
        asks for torque to be kept instead and says so. Without a rest pose recorded there is
        nothing to check against, and nowhere the arm is known to rest, so the close keeps its
        torque as well and says so in words of its own (`KEEPS_TORQUE_WITHOUT_A_POSE`,
        `TORQUE_LEFT_ON_WITHOUT_A_POSE`), where it used to ask for the release as a session always
        had and let the arm fall where it stood (ADR-0058). The follower is built asking to keep
        torque (`_config_kwargs`), so an exit that never gets here leaves the arm holding rather
        than dropping it.

        "The recorded pose" is judged the way the rest move judges it (`verbs.at_rest`): a
        joint recorded past its travel is at rest parked at the edge of it or anywhere beyond,
        and is let go of there to settle the rest of the way. That release says nothing here:
        the settle sentence travels on the rest move's result, which is said once by whoever
        narrates it, and a `close_note` is read everywhere as torque left on. A joint stopped
        short *inside* its travel, against a hand or the desk, is still a miss and still keeps
        torque.

        An arm still limp in somebody's hands is the one case where neither of those notes is
        true, and it says so in its own words, from what a read of the torque register since the
        release found: every motor off is an arm with no torque to keep and nothing to keep it
        from (`LIMP_IN_HAND`), and motors still on are named, with the switch as what lets go of
        them (`still_holding_in_hand`). And where no read has answered since the release went
        out, a release that raised part way, one a Ctrl-C landed on, or one whose read-back
        failed, and the close's own read made none or was refused, it says exactly that
        (`UNREAD_IN_HAND`): the release went out, nothing read it back, so hold the arm as
        though nothing holds it and cut its power to be sure. "Nothing is holding it up" there
        would be the unread claim, over motors that may still hold. After a take-hold that was
        refused once its torque write went out, the same silence is an arm that may be
        energised, and it is said that way (`UNCONFIRMED_IN_HAND`). And an arm the placing
        release let go of at its rest pose that this close reads still there, every motor off,
        is said to be limp at its rest pose (`LIMP_AT_REST`), not in somebody's hands, as is one
        `quackd robot release` folded and let go of there, in its own words
        (`LIMP_AT_REST_AS_ASKED`), since nobody asked it to be placed.

        Three more cases say something of their own, because the usual line would tell the
        person something quackd did not do or does not know. An arm that did not answer the
        read the close decides by is one quackd cannot say is holding itself up
        (`TORQUE_UNKNOWN_AT_CLOSE`). An arm whose release a person just asked for and was
        refused is not sent back to that same release (`TORQUE_KEPT_AFTER_REFUSAL`). And such
        an arm, closed at its rest pose or with no pose recorded, is let go of by the
        disconnect as every such close is, which after a refusal is said
        (`released_by_the_close`)."""
        self._closed = True
        await self._cancel_policy("the arm's transport was closed")
        if self._policy_loop is not None:
            # the runner is closed on its own worker, behind anything it is still doing, and the
            # close does not wait for it: the arm's disconnect below is what matters now
            self._policy_loop.close()
        # the cameras on their own deadline and never the serial lock, so however long a
        # release takes, the arm's disconnect below still runs
        await self._close_cameras()
        if self._robot is None:
            return
        self.close_note = None
        if self.rest_pose is not None:
            why, answered = await self._not_resting()
        elif self.KEEPS_TORQUE_WITHOUT_A_POSE and not self._in_hand:
            # an arm in somebody's hands is the branch below's, which keeps its torque and says
            # what the release's own reads found, with no read of this close's beside them
            why, answered = await self._unposed()
        else:
            why, answered = None, True
        if self._in_hand:
            # Whoever is reading this has the arm in their hand. The torque note below would
            # tell them it is holding itself up, which is the one thing it may not be.
            #
            # Why it is limp, as the mock says it: why it was let go of. It used to fall back
            # on the rest shortfall (`why`), which `_not_resting` builds by joining the shortfall
            # to the rest move's reason, and that reason begins with the same shortfall, so the
            # person holding the arm read it twice and never read that it was let go of for
            # them to place and never taken hold of again.
            limp = self._let_go_why or LET_GO_TO_PLACE
            # A take-hold's torque write went out since the release, and the arm is still in a
            # hand, so it may be energised, and only a torque read since that write says
            # whether it is (`_torque_read_back`, judged by the bus's order). Read off the
            # writes themselves rather than off the refusal kept last: that refusal speaks for
            # its own take-hold, and one an interrupt landed on after its write kept none. No
            # branch below says the arm is limp unless a read after that write said so.
            wrote = self._hold_written
            # The release `quackd robot release` asked for at the rest pose, over an arm this
            # close does not read as moved off it: nobody is holding it, since the person was
            # told to take their hands away before it folded, so no line tells them to put it
            # down from their hands.
            lying = self._let_go_why == LET_GO_AT_REST and (why is None or not answered)
            if not self._torque_read_back:
                self.close_note = (
                    UNCONFIRMED_IN_HAND
                    if wrote
                    else UNREAD_AT_REST
                    if lying
                    else UNREAD_IN_HAND.format(why=limp)
                )
            elif self._torque_on:
                self.close_note = (
                    still_holding_at_rest(self._torque_on)
                    if lying and not wrote
                    else still_holding_in_hand(
                        self._torque_on, HOLD_NOT_CONFIRMED if wrote else limp
                    )
                )
            elif lying or (
                why is None
                and answered
                and self.rest_pose is not None
                and self._let_go_why in (None, LET_GO_AT_REST)
            ):
                # this close's own read found it at its rest pose, and a torque read found every
                # motor off: a first-door release, which lets go only at that pose, left it
                # there, and telling the person to put down an arm lying in its fold is telling
                # them it was lifted, which no read said. Which release it was is the line's
                # one difference: `--by-hand` let it go to be placed, `robot release` because
                # the person asked for torque off
                self.close_note = (
                    LIMP_AT_REST if self._let_go_why is None else LIMP_AT_REST_AS_ASKED
                )
            else:
                self.close_note = LIMP_IN_HAND.format(why=limp)
            with contextlib.suppress(Exception):
                # Keep torque, and keep it without knowing whether there is any to keep. This
                # branch is reached in two states: an arm that is genuinely limp, where the
                # flag changes nothing at all, and an arm whose `take_hold` could not read the
                # torque register back, where it may well be energised and away from its fold.
                # Dropping it there would drop the arm, and the no-op costs nothing.
                self._robot.config.disable_torque_on_disconnect = False
            with contextlib.suppress(Exception):
                await self._call(self._robot.disconnect, deadline_s=5.0)
            return
        if why is not None and not answered:
            self.close_note = TORQUE_UNKNOWN_AT_CLOSE.format(why=why)
        elif why is not None and self.rest_pose is not None and (off := self._limp_joints()):
            # its own read finds torque off, so it is not holding itself up, whatever kept it
            # off its pose: the line says what the read found, not that torque was left on
            self.close_note = torque_reads_off(why, off)
        elif why is not None and self._release_refused:
            self.close_note = (
                TORQUE_KEPT_AFTER_REFUSAL_WITHOUT_A_POSE
                if self.rest_pose is None
                else TORQUE_KEPT_AFTER_REFUSAL.format(why=why)
            )
        elif why is not None:
            self.close_note = self._torque_left_on(why)
        with contextlib.suppress(Exception):
            # up.SO_DISCONNECT_READS_ITS_CONFIG_LATE: the flag is read off the config instance
            # inside disconnect() rather than copied at construction, so this is the seam.
            # _config_kwargs() asks for False, so this write is the one thing that lets an arm
            # go at a close.
            #
            # Written every time, both ways. The flag lives on the robot, not on this call, so
            # writing it one way only would let an earlier session of this transport decide
            # this one: a close that reached its pose once and missed it the next time would
            # drop the arm, and the other way round would keep an arm energised with nothing
            # said about it.
            self._robot.config.disable_torque_on_disconnect = why is None
        # What the disconnect below will do, read back rather than assumed: a write that did
        # not take leaves whatever the config already held, which for a follower quackd built
        # is the hold and for one handed in may be upstream's release. None is a config that
        # cannot be read either, and is treated as the release.
        releases: bool | None = None
        with contextlib.suppress(Exception):
            releases = bool(self._robot.config.disable_torque_on_disconnect)
        if why is not None and releases is not False:
            # the seam did not take, so the disconnect below releases torque after all. Saying
            # the arm is being held when it is about to be let go is worse than saying nothing.
            self.close_note = TORQUE_COULD_NOT_BE_KEPT.format(why=why)
        disconnected = False
        with contextlib.suppress(Exception):
            await self._call(self._robot.disconnect, deadline_s=5.0)  # up.SO_DISCONNECT_TORQUE
            disconnected = True
        if why is None and self._release_refused and disconnected and releases is not False:
            # only once the disconnect came back, because its `Torque_Enable` 0 writes raise on
            # a bus that lost them, and "the close took torque off" is a thing to say only of a
            # close that sent it
            self.close_note = released_by_the_close(at_rest=self.rest_pose is not None)

    def _torque_left_on(self, why: str) -> str:
        """The close's line for an arm it kept torque on away from its rest pose
        (`verbs.torque_left_on`), which the simulator words for a simulated arm, or with no rest
        pose recorded at all (`TORQUE_LEFT_ON_WITHOUT_A_POSE`)."""
        if self.rest_pose is None:
            return TORQUE_LEFT_ON_WITHOUT_A_POSE.format(name=self.registered_name or UNNAMED)
        return torque_left_on(why, self.registered_name)

    KEEPS_TORQUE_WITHOUT_A_POSE = True
    """Whether the close keeps the torque of an arm with no rest pose recorded. True for an arm on
    a desk: with no pose there is nowhere it is known to rest, so nowhere it may be let go of, and
    an arm a probe found holding itself up would fall from where it stood (ADR-0058). The
    simulator's arm drops on nothing, and its runs need no pose, so its close lets go as it did."""

    async def _unposed(self) -> tuple[str | None, bool]:
        """Why an arm with no rest pose keeps its torque, and whether it answered the read that
        says so. Read once, as the close of an arm with a pose reads, so an arm that does not
        answer is said to be unknown rather than held up (`TORQUE_UNKNOWN_AT_CLOSE`)."""
        try:
            await self._probe()
        except Exception as e:
            return f"the arm did not answer: {type(e).__name__}: {e}", False
        return NO_REST_POSE_RECORDED, True

    def rests_where_it_reads(self) -> None:
        """Take where the arm reads now as where it rests, for this session alone.

        `quackd robot rest-pose` reads an arm a person has just folded by hand, and its close
        would otherwise keep torque on, the arm having no pose recorded yet. The pose being read
        is the one being recorded, and the arm is lying in it, so the close lets go of it there as
        it lets go of any arm at its pose. Nothing is written: recording is the command's."""
        if self._joints:
            self.rest_pose = {joint: float(angle) for joint, angle in self._joints.items()}

    async def _not_resting(self) -> tuple[str | None, bool]:
        """Why this arm must keep its torque, or None if it may let go, and whether the arm
        answered the read that decided it. Reads, never moves.

        The second value is False only where the read itself failed, which is the one reason
        to keep torque that says nothing about whether the arm is holding itself up."""
        goal, recorded = self._rest_target()
        if not goal:
            # a pose was recorded and none of it can be driven: the arm is somewhere nobody
            # chose, so it keeps holding rather than being let go there
            return "the recorded pose names no joint this arm drives", True
        try:
            await self._probe()
        except Exception as e:
            return f"the arm did not answer: {type(e).__name__}: {e}", False
        joints = dict(self._joints)
        if at_rest(goal, joints, recorded):
            return None, True
        why = shortfall(goal, joints, recorded)
        missed = self._rest_result
        if missed is not None and not missed.reached:
            # A move that stalled or ran out of time gives as its reason its own shortfall and
            # how it ended (`_drive_to_rest`), and it held the arm where it stopped, so that
            # reason usually begins with this read's shortfall word for word. Joined to it, the
            # close said the shortfall twice, as the in-hand line above once did.
            if missed.reason.startswith(why):
                return missed.reason, True
            return f"{why}; {missed.reason}", True
        return f"{why}; nothing moved it there", True

    # ── reading ─────────────────────────────────────────────────────────────────────

    def _read_all(self) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], _Errors, int]:
        """Three bus transactions in one worker thread, so nothing interleaves on the wire, and
        the count of torque writes the bus had seen when they went out (`_torque_writes`), so
        the torque reading can be told apart from one taken before the last write.

        The positions are the liveness check and are allowed to raise. The two registers are
        not: a corrupt status packet should cost a reading, not the run, so a failure there
        comes back as a note and the last known values stand.

        The two registers are read in their own `try` each, and that matters rather than being
        tidiness. They used to share one, so a corrupt temperature packet reported a failure
        over a torque reading that had arrived perfectly well, and `take_hold()` read that
        report as "torque is unknowable", stopped checking, and told a person holding a limp
        arm that it was holding itself. One bad register must not be able to speak for the
        other."""
        obs: dict[str, Any] = self._robot.get_observation()
        torque: dict[str, Any] = {}
        temperature: dict[str, Any] = {}
        errors = _Errors()
        # in this thread and under the bus's lock, so it is the count this read went out after
        written = self._torque_writes
        try:
            torque = self._robot.bus.sync_read("Torque_Enable", normalize=False, num_retry=2)
        except Exception as e:
            errors.torque = f"{type(e).__name__}: {e}"
        try:
            temperature = self._robot.bus.sync_read(
                "Present_Temperature", normalize=False, num_retry=2
            )
        except Exception as e:
            errors.temperature = f"{type(e).__name__}: {e}"
        return obs, torque, temperature, errors, written

    def _heartbeat_reads(self) -> Callable[[], Any]:
        """What the heartbeat's probe runs in the worker thread: the same three transactions
        as every other probe (`_read_all`). The simulator marks them as the heartbeat's, in
        that thread, so that no seeded fault lands on a read whose timing is the wall's
        (`sim/faults.py`). An arm on a desk is told nothing: whose read it answers makes no
        difference to it."""
        return self._read_all

    async def _probe(self, trace: bool = True) -> dict[str, Any]:
        """Read the arm: its joints and both status registers, in one worker thread.

        `trace` is whether the gripper's reading joins `_gripper_trace`, which `_holding` judges
        a grasp by. Every probe feeds it but the heartbeat's (`heartbeat` passes False), which
        reads on the wall's clock: where its samples land among a verb's polls is chance, and a
        grasp judged on them is judged differently every time the same run is played. On an
        arm that can cost `pick` one poll before it sees a grasp settle; the verb's own polls
        still feed it."""
        self._answered = False
        reads = self._read_all if trace else self._heartbeat_reads()
        obs, torque, temperature, errors, written = await self._call(reads)
        self._answered = True
        self._joints = self._joints_of(obs)
        gripper = self._joints.get("gripper")
        if gripper is not None and trace:
            self._gripper_trace.append((self.now(), gripper))
        self._register_error = errors.summary()
        self._torque_error = errors.torque
        if torque:
            self._torque = all(int(v) == 1 for v in torque.values())
            self._torque_on = tuple(str(k) for k, v in torque.items() if int(v) == 1)
            # what this reading speaks for is the arm after the writes the bus had seen when
            # it went out, and only while no torque write has gone out since
            # (`_torque_read_back`)
            self._torque_read_at = written
        if temperature:
            self._temperature_c = {str(k): float(v) for k, v in temperature.items()}
        return obs

    async def _loop_read(self, *, registers: bool) -> dict[str, Any]:
        """One tick's reading of the arm for a policy segment, which is the policy's
        observation and everything the tick's guards judge. Raises what the read raised.

        The joints every tick, and with `registers` the torque and temperature too, which is
        `_probe` whole: a policy ticking at 10 Hz does not need them each time
        (`REGISTER_PERIOD_S`). Either way the reading is the arm's latest, as a verb's is: the
        joints land in `_joints`, and the gripper's in `_gripper_trace`, stamped as the read
        comes back, so `holding` is judged mid segment on the loop's own reads, which are paced
        on the transport's clock."""
        if registers:
            return dict(await self._probe())
        self._answered = False
        obs = dict(await self._call(self._robot.get_observation))
        self._answered = True
        self._joints = self._joints_of(obs)
        gripper = self._joints.get("gripper")
        if gripper is not None:
            self._gripper_trace.append((self.now(), gripper))
        return obs

    async def _loop_frames(self, obs: dict[str, Any]) -> str | None:
        """Add every camera's newest frame to a policy's observation, or say which camera gave
        none, in a clause that ends a segment.

        Each is added under its own name, which is the dict the follower would have built had
        it owned the camera (`up.SO_CAMERA_KEYS`), so a policy trained against that key sees
        what it expects. A camera that fails here ends the segment and says why, rather than
        being swallowed the way `observe`'s is: a policy that cannot see is guessing. The
        failure is kept in `camera_errors`, so the state says `CAMERA DOWN` afterwards too."""
        for name in self.camera_keys:
            camera = self._cameras.get(name)
            if camera is None:
                continue
            try:
                obs[name] = await asyncio.to_thread(camera.read_latest)
            except Exception as e:
                self.camera_errors[name] = f"{type(e).__name__}: {e}"
                return f"the {name} camera gave no frame ({self.camera_errors[name]})"
        return None

    @staticmethod
    def _joints_of(obs: dict[str, Any]) -> dict[str, float]:
        return {k.removesuffix(".pos"): float(v) for k, v in obs.items() if k.endswith(".pos")}

    async def _read_frame(self, name: str) -> Image.Image | None:
        """One camera's newest frame, or None and a reason. Never raises.

        `observe` moves nothing, so a camera that has stopped delivering should cost the
        picture and not the run: the agent loop asks for a frame every step and `doctor`
        polls for one, and neither expects an exception. The read does not go through
        `_call`, because it touches no serial bus and a wedge must never be filed as a
        camera fault (`up.CAMERA_READ_LATEST`)."""
        camera = self._cameras.get(name)
        if camera is None:
            return None
        try:
            frame = await asyncio.to_thread(camera.read_latest)
            image = Image.fromarray(np.asarray(frame))  # up.CAMERA_COLOR_MODE_DEFAULT: RGB
        except Exception as e:
            self.camera_errors[name] = f"{type(e).__name__}: {e}"
            return None
        self.camera_errors[name] = None
        self._frame_sizes[name] = image.size
        self._frame_ats[name] = self.now()
        return image

    async def get_frame(self) -> Image.Image | None:
        """The primary camera's newest frame. What steers, and what the detector reads."""
        return await self._read_frame(self._primary_name())

    async def get_frames(self) -> list[CameraFrame]:
        """Every camera's newest frame, primary first, each under its own name.

        A camera that gave nothing this time is simply absent from the list rather than an
        empty slot: the model is shown the views that exist, and `camera_health()` is where
        the one that stopped says so."""
        primary = self._primary_name()
        frames = []
        for name in self.camera_keys or (primary,):
            image = await self._read_frame(name)
            if image is not None:
                frames.append(CameraFrame(name, image, primary=name == primary))
        return frames

    def _camera_row(self, spec: CameraSpec) -> dict[str, Any]:
        size = self._frame_sizes.get(spec.name)
        at = self._frame_ats.get(spec.name)
        return {
            "name": spec.name,
            "url": spec.url,
            "ok": self.camera_errors.get(spec.name) is None and at is not None,
            "age_s": None if at is None else round(self.now() - at, 2),
            "size": f"{size[0]}x{size[1]}" if size else None,
            "error": self.camera_errors.get(spec.name),
        }

    def camera_health(self) -> dict[str, Any]:
        """What `doctor` prints and gates its verdict on, shaped like every other backend's.

        One camera answers exactly what it always answered. Several answer that plus a
        `cameras` list, one row each, so a reader written for one camera still reads the
        primary and a reader that knows about several gets all of them."""
        spec = self.camera_spec
        at = self._frame_ats.get(self._primary_name())
        size = self._frame_sizes.get(self._primary_name())
        health: dict[str, Any] = {
            "configured": spec is not None,
            "url": spec.url if spec else None,
            "ok": spec is not None and self.camera_error is None and at is not None,
            "age_s": None if at is None else round(self.now() - at, 2),
            "size": f"{size[0]}x{size[1]}" if size else None,
            "error": self.camera_error,
        }
        if len(self.camera_specs) > 1:
            health["cameras"] = [self._camera_row(s) for s in self.camera_specs]
        return health

    @property
    def hot_joints(self) -> list[str]:
        """The body joints at or above `HOT_C`. The gripper is left out on purpose: it has
        LeRobot's own torque and current caps, and its temperature is still reported."""
        return sorted(k for k, v in self._temperature_c.items() if v >= HOT_C and k != "gripper")

    def _out_of_range(self) -> list[str]:
        """Joints reading outside the travel their own calibration recorded. Not a refusal:
        it means the file and the arm disagree, which is worth seeing before a goal is."""
        out = []
        for joint, value in self._joints.items():
            span = self.joint_range_deg.get(joint)
            if span and not (span[0] - OUT_OF_RANGE_DEG <= value <= span[1] + OUT_OF_RANGE_DEG):
                out.append(joint)
        return sorted(out)

    def _holding(self) -> bool:
        """Inferred, never sensed (`up.HOLDING_INFERRED`): the gripper was told to close, it
        has stopped moving, and it stopped short of shut."""
        if self._gripper_goal is None or self._gripper_goal > HOLD_MIN:
            return False
        if not self._gripper_trace:
            return False
        at, latest = self._gripper_trace[-1]
        # settled means the reading has not moved over a real interval. The heartbeat and a
        # verb's poll can land a millisecond apart, and two samples that close agree whatever
        # the gripper is doing, so the comparison reaches back at least one tick
        earlier = [pos for t, pos in self._gripper_trace if at - t >= SETTLE_GAP_S]
        if not earlier or abs(latest - earlier[-1]) > GRIPPER_SETTLE:
            return False
        return HOLD_MIN < latest < HOLD_MAX

    async def get_state(self) -> DuckState:
        await self._probe()
        extras: dict[str, Any] = {
            "joints": {k: round(v, 1) for k, v in self._joints.items()},
            "torque": self._torque,  # measured, not assumed: up.STS3215_REGISTERS
            "temperature_c": {k: round(v) for k, v in self._temperature_c.items()},
            "hot": self.hot_joints,
            "out_of_range": self._out_of_range(),
            "step_deg": self.max_step_deg,
            "range_clips": self._range_clips,
            "assumptions": [
                up.GRIPPER_OPEN_VALUE.name,
                up.HOLDING_INFERRED.name,
                up.TEMPERATURE_C.name,
            ],
        }
        # measured on the wall's clock and changing nothing: how long the bus calls take, and a
        # policy segment's ticks once one has run (`Timing`)
        timing = {"bus_call": self._bus_timing.summary()}
        if self._tick_timing.count:
            timing["policy_tick"] = self._tick_timing.summary()
        extras["timing"] = timing
        if self._policy_error is not None:
            extras["policy_error"] = self._policy_error
        if self._register_error is not None:
            extras["register_error"] = self._register_error
        if self.camera_spec is not None:
            # A camera that dies mid-run is otherwise invisible on a `quackd run`: the only
            # reader of camera_error is the `observe` verb, and `observe` cannot be in a
            # .duck's allowlist on this backend because the static manifest has no camera.
            # So the frames stop, the observation loses a line, and nothing says why. This
            # is pure field reads, no bus and no camera call, so the heartbeat pays nothing.
            extras["camera"] = self.camera_health()
        return DuckState(
            t=self.now(),
            policy=self._policy_name,
            posture="unknown",
            fallen=False,
            battery_percent=None,
            holding=self._holding(),
            extras=extras,
        )

    # ── writing ─────────────────────────────────────────────────────────────────────

    def _clip(self, joint: str, goal: float) -> float:
        span = self.joint_range_deg.get(joint)
        if span is None:
            return goal
        clipped = min(span[1], max(span[0], goal))
        if abs(clipped - goal) > 1e-6:
            self._range_clips += 1
        return clipped

    async def _send(self, goals: dict[str, float], *, clip: bool = True) -> dict[str, float]:
        """One `send_action`, and what it says it actually sent (`up.SO_SEND_ACTION_RETURN`).

        `clip` is off for a hold, where the goal is the position the arm is already in and a
        clip would be a goal somewhere it is not. Every caller that passes it has already
        left out each joint reading outside its travel (`_outside_travel`) rather than send it
        clipped, because a clipped goal for that joint is exactly what the servo's own clamp
        would make of an unclipped one: a goal at the limit, which hauls the joint to it."""
        action = {
            f"{joint}.pos": (self._clip(joint, float(goal)) if clip else float(goal))
            for joint, goal in goals.items()
            if joint in JOINTS
        }
        if not action:
            return {}
        if "gripper.pos" in action:
            self._gripper_goal = action["gripper.pos"]
        sent = await self._call(self._robot.send_action, action)
        written = dict(sent) if sent else action
        return {str(k).removesuffix(".pos"): float(v) for k, v in written.items()}

    def _refuse_out_of_range(self, goals: dict[str, float]) -> str | None:
        """A goal outside the travel this arm's calibration gives, refused in the one sentence
        `move_joints` also refuses with (`range_refusal`), against the exact travel read at
        connect rather than the published one rounded inward."""
        return range_refusal(goals, self.joint_range_deg)

    async def send_intent(self, intent: Intent) -> Ack:
        p = intent.params
        if self._closed and intent.kind != "stop":
            return Ack(accepted=False, reason="the arm's transport is closed")
        if intent.kind in ("joint", "gripper") and self.policy_running:
            return Ack(accepted=False, reason=f"{self._segment_verb} is running: stop first")
        try:
            match intent.kind:
                case "joint":
                    goals = {str(k): float(v) for k, v in dict(p.get("positions", {})).items()}
                    if (refusal := self._refuse_out_of_range(goals)) is not None:
                        return Ack(accepted=False, reason=refusal)
                    await self._verb_cap()
                    await self._send(goals)
                case "gripper":
                    open_ = bool(p.get("open", True))
                    await self._verb_cap()
                    await self._send({"gripper": GRIPPER_OPEN if open_ else GRIPPER_CLOSED})
                case "do":
                    return await self._do(str(p.get("skill")), p.get("max_s"), p.get("max_chunks"))
                case "stop":
                    await self._hold()
                case "enable":
                    if not p.get("on", True):
                        return Ack(accepted=False, reason="quackd never limps a robot")
                case _:
                    return Ack(accepted=False, reason=f"an arm cannot {intent.kind}")
        except TimeoutError:
            return Ack(accepted=False, reason=f"{intent.kind} timed out on LeRobot")
        except Exception as e:  # LeRobot's own errors are feedback, not a crash
            return Ack(accepted=False, reason=f"{intent.kind} failed: {type(e).__name__}: {e}")
        return Ack()

    async def _do(self, skill: str, max_s: Any = None, max_chunks: Any = None) -> Ack:
        """Start a policy segment: `policy:pick:<task>` or `policy:manipulate:<instruction>`, for
        at most `max_s` seconds of the transport's time when it is given (both verbs give it) and
        at most `max_chunks` chunks when that is, and until the policy is done or something
        stops it when neither is. The segment's body is the policy loop (`policy/loop.py`).

        The segment's first reading is taken inside its task, registers and all, and judged
        before it sends anything, and the `do` is acknowledged only once that reading has
        passed and the policy has been reset with a rate quackd paces. A refusal sends nothing
        and asks the policy for nothing: a body joint further outside its travel than
        `OUT_OF_RANGE_DEG` (`verbs.policy_past_travel`), a hot joint, torque off, or a read the
        arm did not answer. Nor does one of the policy's own: still busy with the last
        segment's inference, a reset that raised, a rate or a latency that is not a number
        quackd can pace (`PolicyLoop.start`). Two segments at once must not each start a loop,
        so the lock covers the cancel of the one before and the start.

        The task exists from before that first read, so everything that stops the arm finds it
        and cancels it, as it would a running segment. Were the reading taken before the task,
        a stop that landed while it was on the bus would find no task, hold the arm and return,
        and the policy would start after it. The read's own call is the segment's, so a stop
        that cancels it there waits for it as it waits for any segment's (`_cancel_policy`). A
        stop that lands before the task exists, while this `do` waits for the lock or for the
        segment before it to end, is counted (`_stop_count`), and that count keeps the segment
        from starting too. So does a stop still under way when this `do` comes, such as one
        whose hold is queued behind the read the executor makes before `pick`: it began before
        the `do`, found no segment to cancel, and would otherwise hold the arm and return with
        the policy started behind it. Either way the `do` is refused, naming the stop. A `do`
        its own caller abandons while the segment starts, an executor's timeout or an abort,
        takes the segment with it."""
        # a stop under way was counted as it began, so the count is taken from before it
        stops = self._stop_count - self._stops_in_flight
        kind, _, rest = skill.partition(":")
        name, _, task = rest.partition(":")
        if kind != "policy" or name not in SEGMENT_VERBS:
            return Ack(accepted=False, reason=f"unknown skill {skill!r}")
        if self._policy_loop is None:
            return Ack(accepted=False, reason="no policy was given to this backend")
        if name == "manipulate" and not task.strip():
            return Ack(accepted=False, reason=NO_INSTRUCTION)
        try:
            limit = None if max_s is None else float(max_s)
        except (TypeError, ValueError):
            limit = math.nan
        if limit is not None and not (math.isfinite(limit) and limit > 0):
            return Ack(
                accepted=False, reason=f"max_s={max_s!r} must be a number of seconds above 0"
            )
        if max_chunks is not None and not (
            isinstance(max_chunks, int) and not isinstance(max_chunks, bool) and max_chunks > 0
        ):
            return Ack(
                accepted=False, reason=f"max_chunks={max_chunks!r} must be a whole number above 0"
            )
        async with self._policy_lock:
            await self._cancel_policy(f"another {name} started", stop=False)
            if self._stop_count == stops:
                self._policy_error = None
                self._policy_name = f"policy:{name}:{task}"
                self._segment_verb = name
                ready: asyncio.Future[None] = asyncio.get_running_loop().create_future()
                # named, so a task left running at a close says whose it is wherever it is
                # listed
                segment = asyncio.create_task(
                    self._run_policy(Segment(name, task, limit, max_chunks), ready),
                    name=POLICY_TASK,
                )
                self._policy_task = segment
                started: set[asyncio.Future[Any]] = {ready, segment}
                try:
                    await asyncio.wait(started, return_when=asyncio.FIRST_COMPLETED)
                except BaseException:
                    # this `do`'s caller went, an executor's timeout or an abort, and the
                    # segment goes with it; the stop that follows waits for its read
                    segment.cancel()
                    raise
                if segment.done() and not segment.cancelled():
                    ended = segment.result()
                    if ended.how == "refused":
                        return Ack(accepted=False, reason=ended.reason)
                # still this segment's, with nothing awaited from here to the verb that reads
                # it (`policy_segment`)
                if ready.done() and self._policy_task is segment:
                    return Ack()
        return Ack(
            accepted=False,
            reason=f"the policy was not started: {self._last_stop} while it was starting",
        )

    def _start_refusal(self) -> str | None:
        """Why a policy segment must not start on the reading just taken, or None."""
        outside = {
            joint: self._joints[joint]
            for joint in self._out_of_range()
            if joint in JOINTS and joint != "gripper"
        }
        if outside:
            return policy_past_travel(outside, self.joint_range_deg)
        if (why := self._unsafe_to_drive()) is not None:
            return f"the policy was not started: {why}"
        return None

    def _unsafe_to_drive(self) -> str | None:
        """What in the last reading of the registers rules out another goal, or None: a body
        joint at or above `HOT_C`, or torque off on any motor. What becomes of the policy is the
        caller's to say."""
        if hot := self.hot_joints:
            worst = max(hot, key=lambda joint: self._temperature_c.get(joint, 0.0))
            return (
                f"{worst} reads {self._temperature_c[worst]:.0f}°C, and quackd moves no joint at "
                f"or above {HOT_C:g}°C: let the arm cool"
            )
        if not self._torque:
            off = [j for j in JOINTS if j in self._joints and j not in self._torque_on]
            where = f" on {', '.join(off)}" if off else ""
            return (
                f"torque reads off{where}, so a goal would reach a limp servo, and a servo that "
                "trips its own overload protection reads this way"
            )
        return None

    async def _run_policy(self, segment: Segment, ready: asyncio.Future[None]) -> SegmentEnd:
        """One policy segment, `pick`'s or `manipulate`'s, in the policy task: its start here,
        and its ticks in the policy loop (`PolicyLoop.run`), until something is held, the
        policy is done, its time or its chunks run out, the arm stops moving under
        `manipulate`, the policy starves, a guard ends it or something else cancels it. quackd
        only says which verb, and holds the arm to its rules.

        It starts with a reading of its own, registers and all, which the `do` waits on: one
        that rules the segment out ends it `refused` before the policy is reset or asked for
        anything (`_start_refusal`). Then the runner is reset, behind whatever inference an
        earlier segment left on its worker, and its rate is read and judged (`PolicyLoop.start`),
        and a runner that is still busy, that raised, or whose rate or latency is not one quackd
        paces ends it `refused` too, as does anything the start itself raises, with its cause.
        Only then is `ready` resolved.

        The step cap is the policy's own for the length of the segment: the verbs' speed at the
        policy's rate (`policy.loop.speed_cap`), written on the follower's config here, in this
        task, and put back to `max_step_deg` in its `finally` however the segment ends, a
        guard, an error, a stop, the verb's own timeout or the close. Put back to the setting and
        never to a value saved at the start, which a segment cut short in its own start could
        have saved as the policy's. `_hold`, `go_to_rest` and every verb's send write the verbs'
        cap again before they send, so no path round this `finally` leaves a policy's cap on a
        verb.

        Each tick's guards are the loop's, on that tick's reading and before its send:

        - **holding** ends a `pick`, judged on the loop's own reads, so a grasp that settles mid
          segment stops the policy the moment it does, rather than leaving it driving the arm
          through the pilot's thinking.
        - **a hot joint, torque off or a dead camera** ends it, with the arm held.
        - **an action that is not a finite number for a motor of this arm** ends it. `_clip`
          would make a NaN the joint's floor and nothing would count it, and a key that names
          no motor would be dropped by the send, so the motor the policy meant is missing.
        - **a joint reading strictly outside its travel** is left out of the action, as `_hold`
          leaves it out, because a clipped goal there is the end of the travel and the servo
          would haul the joint to it.
        - **a goal outside the travel** is clipped and counted (`_range_clips`), unlike a
          verb's goal, which is refused (ADR-0036), and a joint whose goal stays clipped for
          `CLIP_SUSTAIN_S` ends the segment.
        - **a send that fails** is let go `FAILED_SENDS - 1` times in a row, and the next one
          ends the segment, as does a read that fails.

        A segment a guard ends holds the arm where it is before it returns, whoever waits on
        it. When a `pick`'s policy says it is done, the loop keeps reading, and sends nothing,
        for `PICK_SETTLE_S`, because a grasp still closing reads as held only once it has
        settled.

        The loop sleeps on the transport's clock, a tick at a time at the runner's rate
        (`POLICY_HZ` for a `PolicyLike`), and is the only thing that does while it runs: the verb
        that started it waits on the task. Each tick is timed on the wall's clock (`Timing`)."""
        loop = self._policy_loop
        assert loop is not None
        try:
            try:
                await self._probe()
            except Exception as e:
                said = self.stop_error or f"{type(e).__name__}: {e}"
                return SegmentEnd(
                    "refused",
                    f"the policy was not started: the arm did not answer a read ({said})",
                )
            if (refusal := self._start_refusal()) is not None:
                return SegmentEnd("refused", refusal)
            # the start's own reading read the registers
            registers_at = self.now()
            plan = await loop.start(segment.instruction, self.max_step_deg)
            if isinstance(plan, str):
                return SegmentEnd("refused", plan)
            # owed back from here, whether or not the write below takes
            self._policy_cap_on = True
            self._segment_caps += 1
            if not self._set_cap(plan.cap_deg):
                return SegmentEnd(
                    "refused",
                    f"the policy was not started: the follower did not take a step cap of "
                    f"{plan.cap_deg:g} degrees, and a policy at {plan.features.rate_hz:g} Hz "
                    "without it could move the arm faster than any verb may",
                )
            if not ready.done():
                ready.set_result(None)
            return await loop.run(self, plan, segment, self.now(), registers_at)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self._policy_error = f"{type(e).__name__}: {e}"
            if not ready.done():
                # nothing was sent and the `do` is still waiting: a refusal, with its cause,
                # rather than an error the `do` could only guess the reason for
                return SegmentEnd(
                    "refused", f"the policy was not started: its start raised {self._policy_error}"
                )
            return SegmentEnd("error", f"the policy raised {self._policy_error}")
        finally:
            self._cap_back()
            self._policy_name = "idle"

    def _set_cap(self, deg: float) -> bool:
        """Write `deg` as the step cap LeRobot applies to every send, which `send_action` reads
        off the follower's config each time it is called (`up.SO_ACTION_CLAMP`), as a float
        (`up.SO_ACTION_CLAMP_IS_FLOAT`), and say whether the config reads it back. Never raises:
        a cap that did not take is the caller's to act on."""
        config = getattr(self._robot, "config", None)
        if config is None:
            return False
        try:
            config.max_relative_target = float(deg)
            return bool(config.max_relative_target == float(deg))
        except Exception:
            return False

    async def _verb_cap(self) -> None:
        """The verbs' own step cap, `max_step_deg`, written back wherever a segment's cap may
        still be on the follower (`_policy_cap_on`): in the segment's own `finally`, and again
        before every hold, rest move and verb's send, so that a policy's cap is never what a
        verb moves the arm under, even where that `finally` did not get it back.

        A call a cancelled segment left on the wire is waited for first, no longer than its own
        deadline (`_outwait_segment_call`), and the write itself waits for any call still out
        after that (`_cap_back`): LeRobot reads the cap as a send goes out
        (`up.SO_ACTION_CLAMP`), and the policy's last goal must not go out under the verbs' cap,
        which on a policy faster than the verbs is the larger step."""
        if self._policy_cap_on:
            await self._outwait_segment_call()
            self._cap_back()

    def _cap_back(self) -> None:
        """Put the verbs' cap back: at once, or, while a call is still on the wire, the moment
        that call is back. It may be one of a segment's sends, left there by a cancel or run
        out of its own time, and a send reads the cap as it goes out, so the policy's last goal
        must not go out under the verbs' cap. Nothing else reaches the bus while that call is
        out (`_refuse_if_wedged`), a hold included, so nothing waits on the write. A segment
        that has written its own cap by the time the call is back keeps it (`_segment_caps`).

        A segment's `finally` calls this, and ends at once either way, as it always did."""
        left = self._wedged
        if left is None or left.done():
            self._restore_cap()
            return
        caps = self._segment_caps
        left.add_done_callback(
            lambda _call: self._restore_cap() if self._segment_caps == caps else None
        )

    def _restore_cap(self) -> None:
        """`_verb_cap`'s write, at once: the setting, never a value saved from before, and a write
        that does not read back leaves it owed for the next caller to try again."""
        if self._policy_cap_on and self._set_cap(self.max_step_deg):
            self._policy_cap_on = False

    def _policy_goals(self, action: Any) -> tuple[dict[str, float], str | None]:
        """The goals one policy action asks for, less every joint reading strictly outside its
        travel, or why the segment ends on it instead."""
        if not isinstance(action, Mapping) or not action:
            return {}, f"the policy's action named no motor ({action!r})"
        unknown = sorted(str(key) for key in action if str(key) not in JOINTS)
        if unknown:
            return {}, (
                f"the policy's action names {', '.join(unknown)}, and this arm's motors are "
                f"{', '.join(JOINTS)}, so the goal meant for a motor would never have been sent"
            )
        goals: dict[str, float] = {}
        for key, value in action.items():
            try:
                goal = float(value)
            except (TypeError, ValueError):
                goal = math.nan
            if not math.isfinite(goal):
                return {}, (
                    f"the policy asked for {key}={value!r}, which is not a finite number, and a "
                    "clip would have sent the joint to the end of its travel"
                )
            goals[str(key)] = goal
        return {
            joint: goal
            for joint, goal in goals.items()
            if joint not in self._joints or not self._outside_travel(joint, self._joints[joint])
        }, None

    async def _held(self, why: str, *, how: SegmentHow = "guard") -> SegmentEnd:
        """End a segment on a guard, or on a policy that starved (`how`): hold the arm where it
        is, then say why. Never raises; a hold that did not reach the arm leaves `stop_error`
        saying so, and the verb's own stop after it tries again."""
        with contextlib.suppress(Exception):
            await self._hold()
        return SegmentEnd(how, why)

    async def _cancel_policy(self, why: str, *, stop: bool = True) -> None:
        """Stop the running segment, if there is one, and keep `why` for the verb waiting on it
        (`policy_stopped_by`). Every stop path starts here. A `do` starting the next segment
        comes here too, with `stop` False: it is no stop, and only a stop is counted
        (`_stop_count`), segment or none, so a `do` still starting can tell one landed.

        So does a guard's hold, from inside the segment, which cancels nothing and leaves the
        segment where it is. It is still running, in that hold, and a close, a rest move or a
        stop that lands meanwhile has to find it, cancel it and wait for it, or the close would
        disconnect under the hold.

        The wait is `asyncio.wait`, which neither passes a cancellation of the caller on to
        the segment nor hides it: a `do` its own caller abandons while it waits here for the
        segment before it raises, rather than go on to start the next one.

        A segment cancelled in the middle of a bus call, here, by the verb waiting on it or by
        the `do` that was starting it, leaves that call's thread on the wire, and whatever
        cancelled it waits for that call before it writes to the arm (`_segment_call`), rather
        than have its own hold refused on it. It is waited on
        whichever segment left it, because the stop that comes here may find the segment
        already gone, cancelled by its own verb or by the next `do`."""
        if stop:
            self._stop_count += 1
            self._last_stop = why
        task = self._policy_task
        if task is not None and task is not asyncio.current_task():
            self._policy_task = None
            if not task.done():
                self._policy_stopped_by = why
                task.cancel()
                await asyncio.wait({task})
        await self._outwait_segment_call()
        self._policy_name = "idle"

    async def _hold(self) -> None:
        """Stop is 'stay where you are': the present position becomes the goal.

        The gripper is left out of that goal on purpose. LeRobot writes only the keys it is
        given, so omitting it keeps whatever squeeze is already commanded: a stop that
        re-sent the gripper's measured position would open a hand that is holding something
        against its own goal, and every failed verb ends in a stop.

        An arm somebody is holding is taken hold of first. Every teardown begins with a stop,
        so this is what a Ctrl-C during the hand-off wait reaches: the arm is energised where
        the person's hand has it, and the rest move that follows can then put it down. Sending
        a goal to a limp servo instead would be a stop that stopped nothing.

        That is the one take-hold a stop makes, and only while nothing has refused one since
        the release (`_refused_hold`). Where `take_hold` refuses, here or at the end of the
        placement wait, the arm stays in the person's hands and this stop sends it nothing at
        all: no second take-hold and no goal. It reports that it held nothing (`stop_error`).
        A second take-hold is what switched torque on without a word under a person who had
        just been told quackd never took hold, once they did as the refusal asked and moved a
        joint back inside its travel. A goal is what a limp servo keeps in its register and
        drives to the next time torque comes on, which is the stale goal the refusal was about,
        and over an arm the refused take-hold may have energised it is a command to an arm in
        somebody's hands. The close that follows says which arm they are holding.

        A joint reading outside its calibrated travel is left out of the goal too. The servo
        clamps every goal to its travel, so "stay where you are" written to a joint folded past
        it arrives as "go to the limit", and the servo does that at full speed: on the bench a
        stop at the end of a run hauled a folded shoulder up out of its fold this way, with
        nothing in the record saying the stop had moved it.

        Leaving it out avoids starting a rise, and that is all it can do: it does not stop one
        already under way. For a joint reading past its travel, any goal quackd writes while it
        reads there is the limit to the servo. LeRobot caps each send to within a step of the
        reading (`max_relative_target`), and a step from a reading past the travel is still past
        it, so the servo clamps it to the limit and drives there at its own speed. A joint that a
        move had started lifting out of its fold is therefore still rising when a stop lands,
        whatever the stop writes to it or leaves out, until it reaches the limit, and quackd
        has nothing that halts that stretch: the power switch is the only stop for it
        (`verbs.ramp_start` says the same of the move). What a stop owes the pilot is to say
        which joints it left alone, which is `stop_skipped`. If that leaves nothing to send,
        nothing is sent and the stop is not reported as undelivered, because it started
        nothing and was never going to halt what it skipped.

        A stop is under way (`_stops_in_flight`) from here until it returns, so a `do` that
        comes meanwhile does not start a segment under a hold that has yet to go out.

        The verbs' step cap is written again (`_verb_cap`) the moment the segment is cancelled
        and gone, before this reads or sends anything, so a hold never goes out under a
        policy's cap. Not before the cancel: a segment still running would send its next goal
        under the verbs' cap."""
        self._stops_in_flight += 1
        try:
            await self._hold_still()
        finally:
            self._stops_in_flight -= 1

    async def _hold_still(self) -> None:
        """`_hold` itself, counted as under way by its caller."""
        self.stop_skipped = ()
        await self._cancel_policy("a stop was sent to the arm")
        await self._verb_cap()
        if self._in_hand and self._refused_hold is None:
            await self.take_hold()
        if self._in_hand and self._refused_hold is not None:
            # Still in a hand, because a take-hold was refused, this one or the one before it:
            # whatever this sent would go to a limp servo, or to one the refused take-hold may
            # have energised with a person holding it, so it sends nothing, and it is not a
            # stop. A take-hold that refused because the arm slipped did energise it, cleared
            # `_in_hand`, and that arm is held below like any other.
            self.stop_error = (
                "the arm is in somebody's hands, so the stop sent it nothing: "
                f"{self._refused_hold.reason}"
            )
            return
        try:
            await self._probe()
            body = {
                k: v
                for k, v in self._joints.items()
                if k in JOINTS and k != "gripper" and not self._outside_travel(k, v)
            }
            self.stop_skipped = tuple(
                k
                for k, v in self._joints.items()
                if k in JOINTS and k != "gripper" and self._outside_travel(k, v)
            )
            if body:
                await self._send(body, clip=False)
        except Exception as e:
            # the core `stop` verb reads this and refuses to say "stopped" over a hold that
            # never reached the arm; a wedged call has already set its own, better, reason
            if self.stop_error is None:
                self.stop_error = f"the hold did not reach the arm: {type(e).__name__}: {e}"
            raise
        self.stop_error = None

    async def subscribe(self, topic: str) -> AsyncIterator[dict[str, Any]]:  # type: ignore[override]
        while not self._closed:
            await self.sleep(0.5)
            yield {"topic": topic, **(await self.get_state()).model_dump()}

    async def heartbeat(self) -> None:
        """A round trip to the arm, not a flag. `is_connected` is the serial port's own open
        flag (`up.BUS_IS_CONNECTED`): pull the cable and it stays True until a read fails.

        The closed, connected and wedged checks come first, and then a probe of its own, a
        policy segment running or not. A segment's reads of the arm would do for a round trip,
        but every one of them went out before the beat asked: the probe queues behind the read
        on the bus and asks after it, and an arm that died as that read came back would pass a
        beat that took its answer, and be found a whole period later. So a beat during a pick
        costs its loop one read of the arm.

        A call a segment was cancelled in the middle of, by a stop, its own verb or the next
        pick, is no wedge to this check while it is inside its own time (`_segment_call`): the
        probe waits for it inside the probe's own deadline, as it would wait for it on the lock
        had nothing cancelled it. On an arm that answers it comes back at once, and failing on
        it would abort the run over an arm that is fine. An arm that died under it fails the
        beat no later than the beat would have failed had nothing cancelled the segment."""
        if self._closed:
            raise HeartbeatError(f"lerobot {self.label} transport is closed")
        if self._robot is None or not bool(self._robot.is_connected):
            raise HeartbeatError("the arm is not connected")
        wedged = self._wedged is not None and not self._wedged.done()
        if wedged and self._segment_call() is None:
            raise HeartbeatError(self.stop_error or "a LeRobot call has not come back")
        try:
            await self._probe(trace=False)
        except HeartbeatError:
            raise
        except Exception as e:
            raise HeartbeatError(f"the arm did not answer: {type(e).__name__}: {e}") from e

    async def stop(self) -> None:
        with contextlib.suppress(Exception):
            await self._hold()

    # ── handing the arm to a person ─────────────────────────────────────────────────

    async def let_go(self, *, anywhere: bool = False, asked: bool = False) -> HandResult:
        """Take torque off, so somebody can pick the arm up and place it. Never raises an
        `Exception`; a Ctrl-C or a cancellation that lands while the release is on the wire
        goes on up, with the arm already taken to be in somebody's hands (`in_hand`), and one
        that lands before it, on the read, goes on up with nothing sent, the arm in nobody's
        hands and nothing refused, so the caller can tell the two apart.

        This is the only call in quackd that de-energises a robot, and it is deliberately the
        narrowest one that could do the job. By default it refuses anywhere but the recorded
        rest pose, which is the same condition `close()` uses to decide whether letting go is
        safe: a pose the arm demonstrably holds with no torque on it. Releasing an arm held up
        by torque alone would drop it, and the person asking for this with `--by-hand` has
        their hands nowhere near it yet.

        `anywhere` is the second door, and a person opens it, never a verb or a model: the
        question `quackd robot release` and the end of a run put only after the arm's fold
        missed twice, or before connecting to an arm with no rest pose to fold to
        (`quackd.release`), both at a terminal, both after telling the person the arm will drop
        and to hold it, which is the whole difference. It skips the two refusals about the
        pose, the arm being away from it and there being none recorded, and releases wherever
        the arm stands. Everything else is the same call: the joints are read first, the
        release is read back, and the arm is in somebody's hands afterwards. It exists because
        the power switch was the only other way to take torque off an arm left holding itself
        up, and on the bench of 2026-09-23 that was how every run that got to its end finished.

        `asked` is the first door, for a person who asked for torque off rather than to place
        the arm: `quackd robot release`, once its fold has arrived. The rule is the default's
        and refuses anywhere but the rest pose, so it can never drop the arm; what it changes is
        what the close says afterwards (`verbs.LET_GO_AT_REST`), and that a refusal of it is a
        release a person asked for, as a refusal of the second door is. With `anywhere` it
        changes nothing.

        Nothing is said to be released that was not sent. A read that fails *before* the
        release refuses, because nothing has changed on the arm and a person told it is theirs
        would be holding an arm that is still holding itself. From the moment the release is
        sent the answer is `released`, whatever comes after, and the arm is taken to be in
        somebody's hands: a release call that never came back, a read that failed, a torque
        register that said nothing. The alternative reading, that torque is still on, ends
        with `close()` printing that the arm is holding itself up over an arm hanging limp in
        somebody's hand, and of the two wrong answers that is the one that gets an arm
        dropped. `torque_on` on the result says which of those it was, so the command that
        asked can tell the person "torque reads off" only when every motor said so."""

        def refused(reason: str) -> HandResult:
            # A refusal of the second door is what the close is told, so it does not send the
            # person back to the door that just refused them, and so is one of the first door
            # opened with `asked`, which is the same person asking at the rest pose. The first
            # door's other refusals are `--by-hand`'s, not a release anybody asked for to take
            # torque off, and change nothing about the close. Set here, where a refusal is
            # returned, and nowhere else: an interrupt that lands on the read before the release
            # goes on up with nothing sent and nothing refused, and a close told "the release
            # did not take" of a release that never went out would say something that did not
            # happen.
            self._release_refused = anywhere or asked
            return HandResult("refused", reason)

        if self._closed:
            return refused("the arm's transport is closed")
        if not anywhere and self.rest_pose is None:
            return refused(
                "no rest pose is recorded for this arm, so there is nowhere it is known to be "
                "safe to let go of it: quackd robot rest-pose NAME",
            )
        # the reachable pose and the half-line rule, as `close()` judges it: an arm folded past
        # its travel is at its rest pose, and refusing it here would refuse `--by-hand` the one
        # arm whose fold is the most certainly safe place to let go of it
        goal, recorded = self._rest_target()
        if not anywhere and not goal:
            return refused("the recorded pose names no joint this arm drives")
        try:
            await self._cancel_policy("the arm was let go of")
            await self._probe()
        except Exception as e:
            return refused(
                "the arm did not answer before the release, so nothing was released "
                f"({self.stop_error or f'{type(e).__name__}: {e}'})",
            )
        resting = bool(goal) and at_rest(goal, self._joints, recorded)
        if not anywhere and not resting:
            return refused(
                "the arm is not at its rest pose "
                f"({shortfall(goal, self._joints, recorded)}), and "
                "an arm held up by torque alone falls when torque goes",
            )
        where = "at the rest pose" if resting else "where the arm stands"
        before = dict(self._joints)
        self._let_go_why = LET_GO_WHERE_IT_STOOD if anywhere else LET_GO_AT_REST if asked else None
        # from here the release is going out, so no take-hold refused before it, nor the torque
        # write of one, speaks for the hand it is going into. No read from before it speaks for
        # the arm either, which the write itself sees to as it goes out (`_torque_read_back`)
        self._release_refused = False
        self._refused_hold = None
        self._hold_written = False
        try:
            # up.BUS_DISABLE_TORQUE, with the retries upstream's own disconnect gives the same
            # writes: without them one lost packet releases the motors before it and not the
            # ones after, and the person is told the arm is theirs while part of it still holds
            await self._call(
                functools.partial(self._robot.bus.disable_torque, num_retry=TORQUE_RETRIES),
                writes_torque=True,
            )
        except BaseException as e:
            if not isinstance(e, Exception):
                # A Ctrl-C or a cancellation landing while the release is on the wire. The
                # call has been issued and its thread goes on writing whatever comes of the
                # interrupt, so the arm is limp in somebody's hands from here, for the reason
                # below: the other reading ends with `close()` telling them it holds itself up.
                # The interrupt still goes on up, since it is not this method's to swallow.
                self._in_hand = True
                raise
            # The writes go out one motor at a time, so a call that raised may have released
            # the motors before the one it failed on and none after. Some joints are limp and
            # some hold, and which is unknown: the person is told to treat it as limp, the next
            # `stop` treats it the same way, and `close()`, unless a read of its own answers
            # first, says that nothing read the release back and to cut the power to be sure.
            self._in_hand = True
            return HandResult(
                "released",
                f"torque was being taken off {where} and the call did not come back "
                f"({self.stop_error or f'{type(e).__name__}: {e}'}), so some joints may be "
                "limp and some may still hold: hold the arm as though nothing holds it",
                joints=before,
            )
        # Believed the moment the call returns, and before anything is read back. The
        # confirming probe can fail on its own, and treating that as "no release happened" is
        # the reading that ends with `close()` telling somebody holding a limp arm that it is
        # holding itself up. Everything after this point may only downgrade the reason, never
        # the fact.
        self._in_hand = True
        try:
            await self._probe()
        except Exception as e:
            return HandResult(
                "released",
                f"torque is off {where}, and the arm then stopped answering "
                f"({self.stop_error or f'{type(e).__name__}: {e}'})",
                joints=before,
            )
        if self._torque_error is not None:
            return HandResult(
                "released",
                f"torque is off {where}, and the torque register did not answer to confirm "
                f"it ({self._torque_error})",
                joints=dict(self._joints),
            )
        if self._torque:
            self._in_hand = False  # nothing was released, so nothing is in anybody's hands
            self._release_refused = anywhere or asked
            return HandResult(
                "refused",
                "the arm still reports torque on, so it was not released",
                joints=dict(self._joints),
                torque_on=self._torque_on,
            )
        if self._torque_on:
            # some motors took the release and some did not: limp in part, so still in a hand
            return HandResult(
                "released",
                f"torque is off {where} except on {', '.join(self._torque_on)}, which still "
                "read on",
                joints=dict(self._joints),
                torque_on=self._torque_on,
            )
        return HandResult(
            "released", f"torque is off {where}", joints=dict(self._joints), torque_on=()
        )

    async def take_hold(self) -> HandResult:
        """Hold the pose the arm is in right now, so the person can let go of it.

        The order matters and is the whole of this method. The present position is written as
        the goal *before* torque comes on, because nothing upstream documents what a servo
        does with the goal it was last told when it is re-energised
        (`up.TORQUE_ENABLE_HOLDS_PRESENT`), and the goal it was last told here is a rest pose
        the arm has since been lifted out of by hand. Enabling torque first could therefore
        snap the arm back to the fold with a person's hand in it.

        It is written again afterwards, and read back, so the answer says whether the arm
        actually stayed where it was put rather than assuming it. A refusal after torque came on
        leaves torque on: the arm is holding *something*, and the caller is told what moved.

        It refuses before any of that, with nothing written, torque still off and the arm still
        in the person's hands, when a body joint reads outside its calibrated travel, because
        nothing quackd can do then keeps that joint where it was put. Writing where it is writes
        a goal past the travel, which the servo clamps to the limit
        (`up.POSITION_LIMITS_CLAMP_GOALS`), so torque would haul the joint to that end with a
        hand on it. Writing nothing leaves the servo the last goal it was given, and after a
        hand-off that is the rest move's, written before the person lifted the arm and possibly
        the far end of the travel from where they placed it: if the servo drives to its stored
        goal when torque comes on, which is the unverified row above, the joint swings across
        its whole travel. This method used to take that second way, a joint past its travel
        left out of both writes, as the smaller motion, and it can be by far the larger. So the
        person is told which joint, where it reads and where its travel is
        (`verbs.placed_past_travel`). `_in_hand` is left set, so everything after this goes on
        treating the arm as in a hand: `_hold()` sends it nothing, the rest move does not move
        it, and `close()` ends on the note for an arm in somebody's hands.

        Every refusal says which of two kinds it is (`HandResult.energised`), because the
        person holding the arm acts on it. One made before the torque write went out, over a
        joint past its travel, a closed transport, an arm that reported nothing, or a failure
        on the way there, switched nothing on: the arm is as the release left it
        (`energised=False`). One made after it may have left torque on, all of it or part of
        it: a register that did not answer (`None`, and no read from before the write speaks
        for the arm once the write has gone out on the bus, `_torque_read_back`), a call that
        raised with the write on the wire (`None`), motors that read on while others read off
        (`True`, with those motors in `torque_on`), or an arm that slipped as torque came on
        (`True`). Only a read that found every motor off after it is `False` again. Every
        refusal that leaves the arm in a hand is kept (`_refused_hold`), and nothing takes hold
        again until the next release.

        "Switched nothing on" is said of the arm, not of this call. A take-hold an interrupt
        landed on after its torque write went out kept no refusal, and a refusal before this
        one's own write says what the reads say of that earlier write (`_before_writing`):
        `False` only where none went out since the release or a read since found every motor
        off. And where this call's own read finds a joint outside its travel with the whole arm
        at its rest pose and every motor off, the person pressed Enter without lifting it out
        of a fold recorded past its travel. It is refused in those words
        (`verbs.unlifted_from_rest`, `HandResult.resting`): the arm is lying in its fold, and
        "in your hands, keep hold of it" would tell them it is up and needs holding.

        The gripper is not in that check. LeRobot bounds a gripper reading into its 0..100 range
        before quackd sees it (`_normalize`, the function `up.DEGREES_FORMULA` cites, bounds
        every mode but degrees), so it cannot read outside its travel, and a goal for it is
        bounded the same way (`up.DEGREES_NO_CLAMP`)."""
        held = await self._take_hold()
        # the one place the rule `_hold()` and `close()` read is kept: a refusal that left the
        # arm in a hand, or nothing, since a hold that took and a slip both energised the arm
        self._refused_hold = held if not held.ok and self._in_hand else None
        return held

    def _before_writing(self, reason: str, **kw: Any) -> HandResult:
        """A take-hold refused before its own torque write went out, saying what the arm may
        have been left with all the same (`HandResult.energised`).

        This call switched nothing on, and that is not the question the person holding the arm
        is asking. A take-hold before this one, in the same hand-off, may have: one an
        interrupt landed on after its torque write went out, which kept no refusal of its own,
        and then the teardown's stop makes this one. So it is `False` only where no take-hold's
        torque write has gone out since the release (`_hold_written`), or a torque read since
        the last one found every motor off. A read since that found motors on is `True`, and
        no read since is `None`. The motors that read on always go with a `True` (`torque_on`),
        every one of them where the whole arm read on: the line said over it names the joints
        that hold, and a read that found every motor on confirmed the torque as surely as one
        that found some, so it must not be told as a torque nobody could confirm."""
        if not self._hold_written:
            return HandResult("refused", reason, energised=False, **kw)
        if not self._torque_read_back:
            return HandResult("refused", reason, energised=None, **kw)
        on = self._torque_on
        return HandResult("refused", reason, energised=bool(on), torque_on=on or None, **kw)

    async def _take_hold(self) -> HandResult:
        """`take_hold` itself; the caller keeps what a refusal left behind."""
        if self._closed:
            return self._before_writing("the arm's transport is closed")
        enabling = False
        try:
            await self._cancel_policy("the arm was taken hold of")
            await self._probe()
            placed = {j: v for j, v in self._joints.items() if j in JOINTS}
            if not placed:
                return self._before_writing("the arm reported no joint to hold")
            outside = {
                j: v for j, v in placed.items() if j != "gripper" and self._outside_travel(j, v)
            }
            if outside:
                # Before a single write: a goal for such a joint and no goal for it both move
                # it once torque comes on (the docstring says how), so torque stays off. Where
                # this read finds the whole arm at its rest pose and every motor off, nobody
                # lifted it out of a fold recorded past its travel, and it is said that way.
                goal, recorded = self._rest_target()
                resting = (
                    bool(goal)
                    and at_rest(goal, placed, recorded)
                    and self._torque_read_back
                    and not self._torque_on
                )
                refusal = unlifted_from_rest if resting else placed_past_travel
                return self._before_writing(
                    refusal(outside, self.joint_range_deg),
                    joints=placed,
                    outside=tuple(outside),
                    resting=resting,
                )
            # The gripper IS in that goal, which is the opposite of what `_hold()` and the rest
            # move do, for the reason they leave it out. They omit it because the squeeze the
            # gripper is holding is a goal somebody meant, and re-sending its measured position
            # would relax it. Here there is no such goal: the jaws are wherever a person's
            # fingers left them with no torque behind them, and the last goal this arm was
            # written may be from another session. Writing where they are is what pins the
            # pencil; omitting it hands the servo whatever stale goal it still had.
            # Unclipped, because this is where the arm physically is, and every body joint of
            # it is inside the travel by now.
            await self._send(placed, clip=False)
            # From here the torque write may reach a motor, so a refusal from here on may leave
            # the arm energised, and so may this take-hold after an interrupt lands on it
            # (`_hold_written`). No torque read from before the write says anything about the
            # arm once it is on the bus, which the write itself sees to (`writes_torque`): a
            # read the run's heartbeat queued behind the goal write above still gets the bus
            # before it, and it is counted as the read before the write that it is.
            enabling = True
            self._hold_written = True
            # up.BUS_ENABLE_TORQUE, retried like the release: one lost packet here refuses the
            # hold with the joints before it energised and the rest still limp in a hand
            await self._call(
                functools.partial(self._robot.bus.enable_torque, num_retry=TORQUE_RETRIES),
                writes_torque=True,
            )
            await self._send(placed, clip=False)
            await self.clock.sleep(TICK_S)
            await self._probe()
        except Exception as e:
            reason = self.stop_error or f"{type(e).__name__}: {e}"
            if not enabling:
                return self._before_writing(reason)
            return HandResult("refused", reason, energised=None)
        # Nothing but a torque register that came back and said "on" gets past here. This is
        # the one place in the adapter where an unread register must refuse rather than be
        # assumed past: `let_go()` takes the opposite reading of the same silence on purpose,
        # because there a release that did not happen costs a refusal and here a hold that did
        # not happen costs the arm, told to a person who is about to take their hands off it.
        if self._torque_error is not None:
            return HandResult(
                "refused",
                f"the arm did not say whether torque came back on ({self._torque_error}), and "
                "a hold nothing confirmed is not a hold",
                energised=None,
            )
        if not self._torque:
            # still limp, all of it or the motors that read off, so still in somebody's hands,
            # and `close()` should still say so. Motors that did read on are named: "nothing
            # holds it" said over them would be the one thing the read did not say
            if self._torque_on:
                return HandResult(
                    "refused",
                    held_in_part(self._torque_on),
                    energised=True,
                    torque_on=self._torque_on,
                )
            return HandResult(
                "refused", "the arm still reports torque off, so nothing holds it", energised=False
            )
        # Torque is on, and read back rather than assumed, so the arm is holding itself up and
        # is no longer hanging off a hand. The refusal below is about the *pose* and not about
        # that: an arm reported limp in somebody's hands while it is energised sends them to
        # cut the power on a robot that is holding perfectly well.
        self._in_hand = False
        held = dict(self._joints)
        moved = {j: abs(held[j] - v) for j, v in placed.items() if j in held}
        slipped = sorted(j for j, gap in moved.items() if gap > TOL_DEG)
        if slipped:
            worst = max(slipped, key=lambda j: moved[j])
            why = (
                f"the arm moved as torque came on ({worst} by {moved[worst]:.0f} degrees), so "
                "it is not holding the pose you set; it is holding where it is now"
            )
            return HandResult("refused", why, joints=held, energised=True)
        return HandResult("held", "holding the pose you set", joints=held, energised=True)

    async def go_to_rest(self) -> RestResult:
        """Drive the arm to the pose it was recorded resting in. Never raises.

        Every caller is a teardown or the first moment of a run, so a wedged bus, an arm
        that stopped answering or a send that never landed are answers here rather than
        exceptions: the caller still has to disconnect, and `close()` reads the joints
        itself before deciding whether torque may drop.

        The goal is the reachable one (`rest_reachable`) and "there" is the half-line rule
        (`verbs.at_rest` with the recorded pose), so an arm folded past its travel is
        `already` at rest, and one driven down to the edge of it has `arrived`. Either way the
        result names the joints recorded further past their travel than a reached pose may
        miss by, and carries the one sentence a person should hear about them.

        An arm in somebody's hands (`in_hand`) is read and sent nothing: it is `already` at
        rest where the read finds it there, and refused otherwise (`verbs.IN_HAND_NOT_MOVED`).
        It is only still in a hand this late because a take-hold was refused, and a rest move
        over it wrote goals into limp servos, which stay in their registers for the next torque
        write to drive to, over an arm whose take-hold may have left it energised, which is a
        fold under a person's hands. Its stall, taken as a stop, took hold of the arm the
        moment a joint came back inside its travel, with nothing said. So none of that runs."""
        if self.rest_pose is None:
            return RestResult.none("no rest pose is recorded for this arm")
        if self._closed:
            return RestResult("refused", "the arm's transport is closed", answered=False)
        goal, recorded = self._rest_target()
        if not goal:
            # "refused", never "none": `none` means there is nothing to go to, and the run
            # would start anyway and the arm be released at the end. There is a pose here,
            # it names nothing this arm drives, and that is a reason to keep holding.
            return RestResult("refused", "the recorded pose names no joint this arm drives")
        clipped = worth_saying(self.rest_clipped)
        try:
            await self._cancel_policy("the arm was sent to its rest pose")
            # the verbs' cap, whatever a policy segment left, before the move sends anything
            await self._verb_cap()
            await self._probe()
            if at_rest(goal, self._joints, recorded):
                result = RestResult("already", "already at the rest pose")
            elif self._in_hand:
                result = RestResult("refused", IN_HAND_NOT_MOVED)
            else:
                result = await self._drive_to_rest(goal, recorded)
        except asyncio.CancelledError:
            # Stopped where it was by whoever cancelled the move, a press during a fold
            # (`quackd.release.fold_unless_stopped`), with goals already sent: the close's line
            # says the fold was stopped partway, where it said nothing had moved the arm there.
            self._rest_result = RestResult(
                "refused", "a fold to it was stopped partway", answered=self._answered
            )
            raise
        except Exception as e:
            # Whether the arm answered, which is whether anything can be said about it holding
            # itself up. A write the arm refused after a read that came back is an arm that
            # answered (`_answered`), and a read that never came back is one nothing can be said
            # about. So is a call that never came back at all, read or write: a timeout counts
            # whether or not its thread is still out, since the call it cut off said nothing
            # either way, and the wedge it leaves is read just below.
            result = RestResult(
                "refused",
                self.stop_error or f"{type(e).__name__}: {e}",
                answered=self._answered and not isinstance(e, TimeoutError),
            )
        if self._wedged is not None and result.answered:
            # A call of this move's own that has not come back, a goal write or the hold a
            # stalled move ends with, which goes on quietly under `suppress`: the bus is wedged
            # behind it, the arm has not answered it, and every release offered over it is
            # refused at its first read for as long as the thread is out. Any wedge from before
            # the move refused the move's first read, so one still here is this move's.
            result = RestResult(result.how, result.reason, answered=False)
        if clipped:
            # a fact about the pose, whatever the move did; the sentence only where it is true,
            # which is an arm that reached the reachable pose and is about to be let go there
            note = rest_clip_note(clipped, self.registered_name) if result.reached else None
            # every other field as the move left it, `limp` above all: a fold that missed over
            # servos with no torque is that whatever the pose, and a pose past the travel is the
            # bench arm's own
            result = replace(result, clipped=clipped, note=note)
        self._rest_result = result
        return result

    async def _drive_to_rest(
        self, goal: dict[str, float], recorded: dict[str, float]
    ) -> RestResult:
        """Re-send the rest goal until the arm is there, stops moving, or the time is up.

        This looks like `verbs._drive` and cannot be it. That one goes through the executor,
        whose abort is already set by the time a person's Ctrl-C reaches a teardown, and this
        move has to run on exactly that path. It also skips the range refusal a pilot's goal
        gets, and has no need of it: `goal` is the recorded pose already clipped into the travel
        from the same calibration, so there is nothing for it to refuse.

        Each tick sends the goal less any clipped joint that already reads past it on the side
        it was recorded (`verbs.past_reach`). That goal is the servo's limit, and a folded joint
        past its limit that is sent it is hauled up to it and held there against its own
        weight, which is the opposite of resting. Such a joint is already at rest by the
        half-line rule, so leaving it out never stops the move from arriving."""
        joints = dict(self._joints)
        todo = [
            abs(joints[j] - v)
            for j, v in goal.items()
            if j in joints and not past_reach(v, joints[j], recorded.get(j))
        ]
        budget_s = rest_budget_s(max(todo, default=0.0), self.max_step_deg)
        stall = min(STALL_DEG, self.max_step_deg / 2) if self.max_step_deg > 0 else STALL_DEG
        started = self.now()
        previous: dict[str, float] = {}
        still = 0
        while self.now() - started < budget_s:
            send = {
                j: v
                for j, v in goal.items()
                if j not in joints or not past_reach(v, joints[j], recorded.get(j))
            }
            await self._send(send)
            await self.clock.sleep(TICK_S)
            await self._probe()
            joints = dict(self._joints)
            if at_rest(goal, joints, recorded):
                return RestResult("arrived", "moved to the rest pose")
            moved = [abs(joints[j] - previous[j]) for j in previous if j in joints]
            still = still + 1 if moved and max(moved) <= stall else 0
            previous = {j: joints[j] for j in goal if j in joints}
            if still >= STALL_TICKS:
                # hold, so the servo stops pushing at a goal it has been told it cannot reach
                with contextlib.suppress(Exception):
                    await self._hold()
                return RestResult(
                    "stalled",
                    f"{shortfall(goal, joints, recorded)}, and it has stopped moving",
                    # whether the hold's own read came back: an arm that went silent as it
                    # stalled is nothing a person can be told is holding itself up
                    answered=self._answered,
                    # and which joints its torque read found off: a servo that browned out
                    # under the stall stops moving because it has no torque, not against a hand
                    limp=self._limp_joints(),
                )
        with contextlib.suppress(Exception):
            await self._hold()
        return RestResult(
            "timeout",
            f"{shortfall(goal, joints, recorded)} when the time ran out ({budget_s:.0f} s)",
            answered=self._answered,
            limp=self._limp_joints(),
        )

    def _limp_joints(self) -> tuple[str, ...]:
        """The body joints the last torque read found off, in the bus's order, and nothing where
        that read failed or none has answered since the connect: a torque register nobody could
        read is not one that says the arm is limp. Never the gripper, which holds none of the arm
        up and is never folded, and whose servo is the one that trips its overload protection
        while gripping: a gripper alone reading off is an arm still holding itself up."""
        if self._torque_read_at < 0 or self._torque_error is not None:
            return ()
        return tuple(
            j for j in JOINTS if j != "gripper" and j in self._joints and j not in self._torque_on
        )

    def now(self) -> float:
        return self.clock.now()

    async def sleep(self, seconds: float) -> None:
        await self.clock.sleep(seconds)
        if self.post_sleep is not None:
            self.post_sleep()


def load_policy(path: str, *, device: str = "cpu") -> PolicyLike:
    """A `PolicyLike` from a LeRobot checkpoint, from verified names, in the arm's own process.
    UNTESTED end to end, and not the path a checkpoint takes (`policy/upstream_api.py`'s
    LOAD_POLICY): serve it with `quackd policy serve` and hand the arm a `RemoteRunner`."""
    try:
        import torch
        from lerobot.configs.policies import PreTrainedConfig
        from lerobot.policies.factory import get_policy_class, make_pre_post_processors
    except ImportError as e:
        raise AdapterNotInstalled("lerobot", "quackd[lerobot]") from e

    config = PreTrainedConfig.from_pretrained(path)
    config.device = device
    policy = get_policy_class(config.type).from_pretrained(path, config=config)
    preprocess, postprocess = make_pre_post_processors(config, pretrained_path=path)

    class _HubPolicy:
        def act(self, observation: dict[str, Any], *, task: str) -> dict[str, float] | None:
            batch = preprocess({**observation, "task": task})
            with torch.no_grad():
                action = policy.select_action(batch)
            out = postprocess(action)
            return {str(k): float(v) for k, v in dict(out).items()}

        def reset(self) -> None:
            # the queue of actions the last chunk predicted (POLICY_SELECT_ACTION), which a
            # new segment must not play out from wherever the arm now is
            policy.reset()  # POLICY_RESET, in policy/upstream_api.py

    return _HubPolicy()
