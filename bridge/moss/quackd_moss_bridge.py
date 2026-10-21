#!/usr/bin/env python3
"""quackd's bridge for a MOSS rover: what MOSS's cockpit cannot do, over HTTP, on the rover.

MOSS's cockpit (`tools/moss_cockpit.py`) gives a goal to the planner, starts and stops the
explorer and sends a soft stop. It has no velocity command, no arm command, no frame that says
when it was taken, and no way to stop the litter mission, which listens to no stop at all. This
is those four things, and nothing else:

- `POST /twist`: a twist published on the stack's pad channel, re-sent at the mission's own
  rate while it is fresh, and after the firmware's deadman with no new one, one zero and then
  silence, because every message on that channel cancels navigation. An arc is kept tight
  enough that the inner track never reverses, and a turn on the spot is sent as the maker's own
  pulses, timed here on the rover where the timing is real.
- `GET /frame`: the first image of a camera captured after the request arrived.
- `POST /arm/cycle`: the maker's own `tools/moss_arm_play.py`, run as a subprocess exactly as
  MOSS's mission runs it, and a cancel that stops the script, cancels the trajectory through an
  RPC client of the bridge's own and reads the joints twice to see that they hold.
  `POST /arm/cancel` is that cancel for one cycle of quackd's, the one its id names, and for
  nothing else.
- `POST /stop`: on moss-litter the mission's own stop first, then at once the arm's cancel and a
  zero twist, and only then the wait that confirms the mission's stop. And
  `POST /session/keepalive`, one per quackd session, each named by its own id: three missed
  periods and the bridge halts what that session started.

Rules this file lives by, the same as every daemon under `bridge/`:

- **It never imports quackd.** It runs in MOSS's own virtual environment, with MOSS's own
  interpreter, as the cockpit is run.
- **lcm, dimos_lcm, dimos and moss_dimos are imported where they are used**, so `--fake` runs
  the whole protocol with none of them, and a rover missing one says so in `/hello`.
- **It reads the stack the way the cockpit does**, from dimOS's run registry and the stack's
  own environment, and it publishes on LCM only.
- **Loopback by default**, a token compared in constant time, a warning when it is bound
  elsewhere with no token, and a refusal to start on a token file that is missing or empty.

    python quackd_moss_bridge.py --fake                       # any machine, no rover
    .venv/bin/python quackd_moss_bridge.py --token-file ~/.config/quackd/moss-bridge.token

Nothing here has been run on a rover by this project.
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import glob
import hmac
import json
import logging
import math
import os
import secrets
import shlex
import signal
import subprocess
import sys
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import parse_qs

BRIDGE_VERSION = "0.1.0"
PROTOCOL = "quackd-moss-bridge"
PROTOCOL_VERSION = 1
"""Still 1 through every change so far, since nothing of the protocol has been released and no
bridge or client of another shape is out there to refuse. The first release fixes it, and a
change after that raises it."""
DEFAULT_PORT = 9876
"""The cockpit is 8904. 9875 is quackd's LeRobot policy server and 9874 its Jetson daemon."""
TOKEN_ENV = "QUACKD_MOSS_TOKEN"
TOKEN_HEADER = "X-Quackd-Token"
"""In a header and never in a URL: URLs are logged and land in transcripts."""

HELLO_PATH = "/hello"
TWIST_PATH = "/twist"
STOP_PATH = "/stop"
FRAME_PATH = "/frame"
ARM_PATH = "/arm"
ARM_CYCLE_PATH = "/arm/cycle"
ARM_CANCEL_PATH = "/arm/cancel"
MISSION_PATH = "/mission"
KEEPALIVE_PATH = "/session/keepalive"
NAV_PATH = "/nav"
GET_PATHS = frozenset({HELLO_PATH, FRAME_PATH, ARM_PATH, MISSION_PATH, NAV_PATH})
POST_PATHS = frozenset({TWIST_PATH, STOP_PATH, ARM_CYCLE_PATH, ARM_CANCEL_PATH, KEEPALIVE_PATH})
FRAME_STAMP_HEADER = "X-Frame-Stamp"
FRAME_AFTER_HEADER = "X-Frame-After-Request"
SESSION_MISSES = 3
MAX_KEEPALIVE_S = 1.0
"""The longest keepalive period the bridge takes from a client. A session ends three of its
declared periods after its last keepalive, so even a client that declares a long one is
halted within three seconds of its last word."""
SESSION_ID_MAX = 64
STARTED = ("goal", "explore", "litter")
JOB_RUNNING = ("starting", "running")
JOB_ENDINGS = ("done", "lag", "refused", "cancelled", "failed")

NOSE, WRIST = "nose", "wrist"
CAMERA_STREAMS = {NOSE: "color_image", WRIST: "wrist_image"}
"""The cockpit's own cameras and the bus topics they come from (moss_cockpit.py CAMERAS)."""
TWIST_MSG = "geometry_msgs.Twist"
BOOL_MSG = "std_msgs.Bool"
IMAGE_MSG = "sensor_msgs.Image"
CAMERA_INFO_MSG = "sensor_msgs.CameraInfo"
PAD_STREAM_EXPLORE = "tele_cmd_vel"
"""moss-explore and moss-litter: the pad's channel, which the MovementManager lets win."""
PAD_STREAM_NAV = "cmd_vel"
"""moss-nav: the pad is remapped onto cmd_vel itself and there is no MovementManager."""
STOP_EXPLORE_STREAM = "stop_explore_cmd"
STOP_MOVEMENT_STREAM = "stop_movement"
EXPLORE_STREAM = "explore_cmd"
EXPLORE_DONE_STREAM = "explore_done"
GOAL_REQUEST_STREAM = "goal_request"
GOAL_REACHED_STREAM = "goal_reached"
POSE_MSG = "geometry_msgs.PoseStamped"
NAV_BOOL_STREAMS = (
    EXPLORE_STREAM,
    STOP_EXPLORE_STREAM,
    EXPLORE_DONE_STREAM,
    STOP_MOVEMENT_STREAM,
    GOAL_REACHED_STREAM,
)
"""The base stack's own navigation, on the channels the cockpit's `_nav` reads (moss_cockpit.py
CHANNELS). The cockpit shows only the newest dimOS run, and a moss-arm started after the base
stack hides it, so the bridge reads them itself, for the stack it tells apart from the arm."""
CAMERA_INFO_STREAM = "camera_info"
BASE_STACKS = ("moss-nav", "moss-explore")
LITTER_STACK = "moss-litter"
ARM_STACK = "moss-arm"
MISSION_RPC = "LitterMission"
"""moss-litter deploys LitterMission with no instance name, so its RPCs are served under its
class name (dimOS serve_module_rpc)."""
PICK_SCRIPT = "tools/moss_arm_play.py"
"""The maker's cycle, which the litter mission runs as a subprocess of its own for every pick
(mission.py _start_pick) and which the mission's stop leaves running."""
PICK_CMD_ENV = "MOSS_PICK_CMD"
"""The stack's own override of that command (mission.py PICK_CMD_ENV), `none` for no arm."""

RPC_TIMEOUT_S = 2.0
"""How long any one RPC from here may take. dimOS's own default is two minutes, which a stop
must never wait out for a module that has gone."""
MISSION_JOIN_S = 2.0
"""LitterMission.stop() joins its loop for up to this long before it closes its RPC service
(moss_dimos/mission.py, stop)."""
MISSION_CONFIRM_S = MISSION_JOIN_S + RPC_TIMEOUT_S
"""How long a stop waits for the mission's RPC service to fall silent, its own sign that it
has stopped."""
MISSION_PROBE_S = 0.5
MISSION_POLL_S = 0.1
"""Between two probes of a mission still answering after its stop."""
ARM_HOLD_GAP_S = 0.2
"""Between the two joint reads that check an arm holds after a cancel."""
SCRIPT_GRACE_S = 2.0
"""How long the arm script has to end after a terminate before it is killed."""
PROC_POLL_S = 0.05
"""Between two looks at whether a signalled process has ended."""
MISSION_RESEND_FIRST_S = 0.2
"""When the mission's stop first goes out again, while it still answers, the gap doubling each
time after, so a few go in the confirmation's window rather than one every probe."""
STARTING_RECANCEL_S = 0.5
"""quackd's own allowance for an execute_trajectory already in flight when a cycle still
starting is cancelled: its script is ended first, but a request it sent just before can land
after the cancel, so a second cancel follows this long after the first."""
ARM_PLAY_STARTUP_S = 10.0
"""quackd's own bound on how long the maker's script takes from its start to its one
execute_trajectory: importing dimOS, reaching the arm's coordinator and reading the joints
(moss_arm_play.py lines 86 to 111). Nothing at the pin bounds it and no run has timed it
(ARM_PLAY_STARTUP in quackd's upstream_api, UNVERIFIED), so the hardware checklist reads it
off a cycle."""
ARM_GUARD_S = ARM_PLAY_STARTUP_S + RPC_TIMEOUT_S
"""How long the arm is watched after a stop whose cancel found a trajectory executing, or
that ended a cycle of quackd's or a pick of the mission's, counted from that stop's last
cancel. A pick the stop's looks did not find, one started as the stop went out, sends its
trajectory at most ARM_PLAY_STARTUP_S after it started, and a trajectory once sent lands
within RPC_TIMEOUT_S, quackd's bound on any one RPC to the coordinator: so a trajectory that
lands after the stop at all lands inside this window. Joints that move inside it are
cancelled again, and the arm is called held only once it has passed."""
ARM_GUARD_POLL_S = 0.25
"""Between two reads of the joints while the arm is watched."""
_CANCEL_AND_READ_WORST_S = 2 * (RPC_TIMEOUT_S + 2 * RPC_TIMEOUT_S + ARM_HOLD_GAP_S + RPC_TIMEOUT_S)
"""cancel_and_read with every RPC answering at its timeout: a cancel, two reads with their gap
and a confirming cancel, and all of that once more when the arm still moved."""
_PICK_END_WORST_S = 2 * (SCRIPT_GRACE_S + PROC_POLL_S)
"""The mission's pick ended: SIGTERM and its grace, SIGKILL and its grace, each to the poll."""
_CYCLE_CANCEL_WORST_S = (
    2 * SCRIPT_GRACE_S + _CANCEL_AND_READ_WORST_S + STARTING_RECANCEL_S + _CANCEL_AND_READ_WORST_S
)
"""A cycle of quackd's cancelled while still starting: its script terminated and killed, the
cancel and its reads, and the late cancel and its reads."""
STOP_WORST_S = (
    max(
        _PICK_END_WORST_S + _CYCLE_CANCEL_WORST_S,
        MISSION_CONFIRM_S + MISSION_POLL_S,  # the confirmation's own window, to its last poll
    )
    + MISSION_PROBE_S  # the mission is asked at least once after the cancel
    + RPC_TIMEOUT_S  # and the arm's coordinator, to tell its silence from a dead bus
    + _PICK_END_WORST_S  # after the mission's stop, its picks looked for and ended again
    + STARTING_RECANCEL_S
    + _CANCEL_AND_READ_WORST_S  # and the arm cancelled and read once more
)
"""The longest a stop can take on moss-litter with every RPC on the rover answering at its
timeout, the longest of the stacks. The confirmation's window starts when the mission's stop
goes out, before the pick and the arm, so the two overlap. The client's own wait for a stop
is held to this by the contract tests."""
TRACK_MIN_M = 0.05
TRACK_MAX_M = 1.0
"""No rover's tracks sit closer than 5 cm or further than a metre apart. A track width outside
that is a misread profile, and the arc clip it would give is wrong, so the twist is refused
rather than shaped by it."""
MIN_STALE_S = 2.0
STALE_PERIODS = 4
"""A camera reading older than four of its own periods, at least two seconds, is not a picture
of now: quackd's rule for every camera it reads."""
STAMP_SLACK_S = 0.01
"""How far a camera's stamp may sit after the moment its frame reached the bridge, both read
off this machine's clock, and still be trusted: quackd's own allowance for the two reading
the clock apart. A frame is said to be taken after a request only when it reached the bridge
after the request and its stamp is this much after the request too, so its capture is never
before the request."""
PUMP_IDLE_S = 0.1
"""The longest the twist scheduler sleeps, between edges or with nothing due, so a pump built
late is found and a closing bridge is never kept waiting."""
HALTS_KEPT = 64
"""The expired sessions' halts kept to report to their clients, the oldest dropped first."""
MAX_BODY_BYTES = 4096
"""quackd's bodies are a few dozen bytes of JSON."""
DRAIN_LIMIT_BYTES = 16 * MAX_BODY_BYTES
"""A refused body up to this long is read and dropped before the refusal is sent."""
REQUEST_TIMEOUT_S = 5.0
MAX_CONNECTIONS = 16
LOOPBACK = ("127.0.0.1", "localhost", "::1")

log = logging.getLogger("quackd-moss-bridge")


def channel(stream: str, msg_name: str) -> str:
    """The LCM channel of a stream, as dimOS and the cockpit name it: /<stream>#<msg_name>."""
    return f"/{stream}#{msg_name}"


def stale_after_s(period_s: float) -> float:
    return max(MIN_STALE_S, STALE_PERIODS * max(0.0, period_s))


# ── what the bridge reads from MOSS ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class Upstream:
    """Every number the bridge drives by. Read from moss_dimos in the stack's own environment
    (`read_upstream`), or given whole by a test or by `--fake`, whose geometry is deliberately
    not MOSS's and whose timing is (`FAKE_UPSTREAM`)."""

    track_m: float
    max_wz: float
    pulse_s: float
    rest_s: float
    deadman_s: float
    pump_hz: float
    max_pct: float
    ramp_ms: tuple[float, float] | None
    still_ticks_per_s: float
    ticks_per_rad: float
    start_tolerance_rad: float
    arm_joints: tuple[str, ...]
    gripper_joint: str
    drive_pose: tuple[float, ...]
    arm_coordinator: str = "ArmCoordinator"
    mission_name: str = MISSION_RPC

    @property
    def stop_ramp_s(self) -> float | None:
        """How long a stop from full power takes: the ramp's fall for every percent."""
        return None if self.ramp_ms is None else self.ramp_ms[1] * self.max_pct / 1000.0


FAKE_UPSTREAM = Upstream(
    track_m=0.3,
    max_wz=0.5,
    pulse_s=0.1,
    rest_s=0.3,
    deadman_s=0.3,
    pump_hz=10.0,
    max_pct=100.0,
    ramp_ms=(4.0, 4.0),
    still_ticks_per_s=20.0,
    ticks_per_rad=4096 / (2 * math.pi),
    start_tolerance_rad=0.05,
    arm_joints=("arm/a", "arm/b", "arm/c", "arm/d", "arm/e"),
    gripper_joint="arm/gripper",
    drive_pose=(0.0, 0.0, 0.0, 0.0, 0.0),
)
"""Synthetic, so `--fake` is never mistaken for a reading: the track width, the turn ceiling,
the rest, the ramp and the arm's joints are none of MOSS's. The deadman, the pulse, the pump
rate, the still rule and the start tolerance keep MOSS's own values, since the timing of the
protocol is what the fake is there to exercise."""


class UpstreamMissing(RuntimeError):
    """moss_dimos is not importable by this interpreter, or lacks what the bridge reads."""


def _ramp(raw: str | None) -> tuple[float, float] | None:
    parts = (raw or "").split()
    if len(parts) != 2:
        return None
    try:
        rise, fall = float(parts[0]), float(parts[1])
    except ValueError:
        return None
    return (rise, fall) if rise > 0 and fall > 0 else None


def _env_float(env: dict[str, str], name: str, default: float) -> float:
    try:
        return float(str(env.get(name, "")).strip() or default)
    except ValueError:
        return default


def read_upstream(env: dict[str, str], arm_env: dict[str, str] | None = None) -> Upstream:
    """The numbers from MOSS's own code, with the stack's own overrides applied, and the drive
    pose from the calibration of the run that holds the arm (`arm_env`, the stack's own when
    there is none), which upstream reads from the environment it is given and otherwise from
    the process's own, the bridge's."""
    try:
        from moss_dimos import arm, arm_feetech, mission, moss_adapter, robot_profile
    except Exception as e:  # any failure is a reason for /hello, not a crash
        raise UpstreamMissing(f"moss_dimos is not importable here: {type(e).__name__}: {e}") from e
    profile = robot_profile.current(env)
    return Upstream(
        track_m=float(profile.track_m),
        max_wz=float(mission.SERVO_W_MAX),
        pulse_s=_env_float(env, mission.PULSE_S_ENV, mission.PULSE_S),
        rest_s=_env_float(env, mission.REST_S_ENV, mission.REST_S),
        deadman_s=float(moss_adapter.FIRMWARE_COMMAND_TIMEOUT_S),
        pump_hz=float(mission.TICK_HZ),
        max_pct=float(moss_adapter.FIRMWARE_MAX_PCT),
        ramp_ms=_ramp(env.get("MOSS_RAMP_MS")),
        still_ticks_per_s=float(arm_feetech.DEFAULT_STILL_TICKS_PER_S),
        ticks_per_rad=float(arm.TICKS_PER_RAD),
        start_tolerance_rad=float(arm.START_TOLERANCE_RAD),
        arm_joints=tuple(f"{arm.HW_ID}/{j}" for j in arm.ARM_JOINTS),
        gripper_joint=f"{arm.HW_ID}/{arm.GRIPPER}",
        drive_pose=tuple(
            float(v)
            for v in arm.drive_pose_rad(
                arm.calibration_path(None, env if arm_env is None else arm_env)
            )
        ),
        arm_coordinator=str(arm.ARM_COORDINATOR_NAME),
    )


# ── the stack, read the way the cockpit reads it ────────────────────────────────────────


@dataclass(frozen=True)
class Run:
    pid: int
    blueprint: str
    run_id: str | None
    started: float
    cmdline: tuple[str, ...] = ()


@dataclass(frozen=True)
class StackInfo:
    running: bool
    blueprint: str | None = None
    pid: int | None = None
    run_id: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    arm_run: Run | None = None
    error: str | None = None
    arm_env: dict[str, str] | None = None
    """The moss-arm run's own environment, from its /proc entry, None when unreadable."""
    runs: tuple[Run, ...] = ()
    """Every live dimOS run read with it, the oldest first, so a run that still runs beside a
    newer one can be found by its id."""

    def arm_environ(self) -> dict[str, str] | None:
        """The environment of the run that holds the arm, the one the maker's script and the
        drive pose must use, its calibration among it: the moss-arm run's own, or with none
        beside the stack, the stack's own, since then its blueprint is the one that carries
        the arm. None when that cannot be read."""
        if self.arm_run is not None:
            return None if self.arm_env is None else dict(self.arm_env)
        return dict(self.env) if self.running and self.env else None

    @property
    def transport(self) -> str:
        """The stack's bus in dimOS's own order: DIMOS_TRANSPORT, then TRANSPORT, else lcm."""
        for var in ("DIMOS_TRANSPORT", "TRANSPORT"):
            value = (self.env.get(var) or "").strip().lower()
            if value:
                return value
        return "lcm"

    def doc(self) -> dict[str, Any]:
        return {
            "running": self.running,
            "blueprint": self.blueprint,
            "pid": self.pid,
            "run_id": self.run_id,
            "transport": self.transport if self.running else None,
            "error": self.error,
        }


def runs_dir() -> str:
    base = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    return os.path.join(base, "dimos", "runs")


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except (OSError, ValueError):
        return False


def _started(text: Any) -> float:
    import datetime as dt

    try:
        t = dt.datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return 0.0
    return (t if t.tzinfo is not None else t.replace(tzinfo=dt.UTC)).timestamp()


def proc_environ(pid: int) -> dict[str, str] | None:
    try:
        with open(f"/proc/{int(pid)}/environ", "rb") as fh:
            raw = fh.read()
    except (OSError, ValueError):
        return None
    env: dict[str, str] = {}
    for item in raw.split(b"\0"):
        key, sep, value = item.partition(b"=")
        if sep:
            env[key.decode("utf-8", "replace")] = value.decode("utf-8", "replace")
    return env


def proc_cmdline(pid: int) -> tuple[str, ...]:
    try:
        with open(f"/proc/{int(pid)}/cmdline", "rb") as fh:
            return tuple(a.decode("utf-8", "replace") for a in fh.read().split(b"\0") if a)
    except (OSError, ValueError):
        return ()


def live_runs(directory: str | None = None) -> list[Run]:
    found: list[Run] = []
    for path in glob.glob(os.path.join(directory or runs_dir(), "*.json")):
        try:
            with open(path, encoding="utf-8") as fh:
                entry = json.load(fh)
            pid = int(entry.get("pid"))
        except Exception:  # a half written or foreign file
            continue
        if not _pid_alive(pid):
            continue
        blueprint = str(entry.get("blueprint", "")).rsplit(".", 1)[-1]
        run_id = entry.get("run_id")
        found.append(
            Run(
                pid=pid,
                blueprint=blueprint,
                run_id=None if run_id is None else str(run_id),
                started=_started(entry.get("started_at")),
                cmdline=proc_cmdline(pid),
            )
        )
    return sorted(found, key=lambda r: r.started)


def read_stack(directory: str | None = None) -> StackInfo:
    """The base stack and the arm's run, kept apart: the cockpit shows only the newest run of
    any kind, and a moss-arm started after the base would hide the base from it."""
    runs = live_runs(directory)
    arm_run = next((r for r in reversed(runs) if r.blueprint == ARM_STACK), None)
    base = next((r for r in reversed(runs) if r.blueprint != ARM_STACK), None)
    arm_env = proc_environ(arm_run.pid) if arm_run is not None else None
    if base is None:
        return StackInfo(running=False, arm_run=arm_run, arm_env=arm_env, runs=tuple(runs))
    env = proc_environ(base.pid)
    return StackInfo(
        running=True,
        blueprint=base.blueprint,
        pid=base.pid,
        run_id=base.run_id,
        env=env or {},
        arm_run=arm_run,
        arm_env=arm_env,
        runs=tuple(runs),
        error=None
        if env is not None
        else f"the stack's environment (pid {base.pid}) is unreadable",
    )


def run_alive(stack: StackInfo, run_id: str) -> StackInfo | None:
    """The run `run_id` as a stack of its own while it is still alive, whether or not it is the
    newest: a second dimOS run started beside it by hand leaves it the run a stop of its
    session is for. None once it has gone."""
    if stack.running and stack.run_id == run_id:
        return stack
    for run in stack.runs:
        if run.run_id == run_id and run.blueprint != ARM_STACK:
            env = proc_environ(run.pid)
            return StackInfo(
                running=True,
                blueprint=run.blueprint,
                pid=run.pid,
                run_id=run.run_id,
                env=env or {},
                arm_run=stack.arm_run,
                arm_env=stack.arm_env,
                runs=stack.runs,
                error=None
                if env is not None
                else f"the stack's environment (pid {run.pid}) is unreadable",
            )
    return None


def _runs_of(stack: StackInfo) -> tuple[str | None, str | None]:
    """The base run and the arm's run: the numbers are read again when either changes."""
    return stack.run_id, (stack.arm_run.run_id if stack.arm_run is not None else None)


class Procs(Protocol):
    """What a stop needs of the rover's processes: the mission's own picks, and a way to end
    them. `ProcTable` reads /proc, and `--fake`'s bus answers for its own fake mission."""

    def picks(self, stack: StackInfo) -> list[int]: ...

    def descendants(self, pid: int) -> list[int]: ...

    def alive(self, pid: int) -> bool: ...

    def signal(self, pid: int, sig: int) -> None: ...


def pick_command(env: dict[str, str]) -> tuple[tuple[str, ...], str | None]:
    """The stack's own MOSS_PICK_CMD as the argv a pick of it starts with, and the sentence
    that refuses it when the bridge will not look for picks by it. One of a single argument
    names a program alone, an interpreter or a shell, which the stack's own workers may run
    too, and one that does not split as a command line names nothing: picks are then looked
    for by the maker's script alone."""
    override = (env.get(PICK_CMD_ENV) or "").strip()
    if override.lower() in ("", "none"):
        return (), None
    try:
        argv = tuple(shlex.split(override))
    except ValueError as e:
        why = f"does not split as a command line ({e})"
    else:
        if len(argv) > 1:
            return argv, None
        why = (
            f"is the one argument {override!r}, a program alone, which the stack's own "
            "processes may run as well"
        )
    return (), (
        f"the stack's {PICK_CMD_ENV} {why}, so the bridge does not look for the mission's "
        f"picks by it, only for {PICK_SCRIPT}, and a stop may leave a pick the mission runs "
        "with it running: give it as the program and the script it runs"
    )


def is_pick(argv: tuple[str, ...], custom: tuple[str, ...] = ()) -> bool:
    """A command line that runs the maker's cycle: an argument that is tools/moss_arm_play.py,
    or the stack's own MOSS_PICK_CMD, with anything the mission appends after it."""
    if any(arg == PICK_SCRIPT or arg.endswith("/" + PICK_SCRIPT) for arg in argv):
        return True
    return bool(custom) and argv[: len(custom)] == custom


class ProcTable:
    """The rover's processes as /proc has them, read the way the cockpit reads a run."""

    def __init__(self, root: str = "/proc") -> None:
        self.root = root

    def _stat(self, pid: int | str) -> tuple[str, int] | None:
        """(the state letter, the parent's pid). The command name sits in parentheses and may
        hold spaces or parentheses itself, so the fields are read after its last one."""
        try:
            with open(os.path.join(self.root, str(pid), "stat"), encoding="utf-8") as fh:
                text = fh.read()
        except (OSError, UnicodeDecodeError):
            return None
        fields = text.rpartition(")")[2].split()
        try:
            return fields[0], int(fields[1])
        except (IndexError, ValueError):
            return None

    def descendants(self, pid: int) -> list[int]:
        try:
            names = [n for n in os.listdir(self.root) if n.isdigit()]
        except OSError:
            return []
        children: dict[int, list[int]] = {}
        for name in names:
            stat = self._stat(name)
            if stat is not None:
                children.setdefault(stat[1], []).append(int(name))
        found: list[int] = []
        todo = [pid]
        while todo:
            for child in children.get(todo.pop(), []):
                if child != pid and child not in found:
                    found.append(child)
                    todo.append(child)
        return found

    def cmdline(self, pid: int) -> tuple[str, ...]:
        try:
            with open(os.path.join(self.root, str(pid), "cmdline"), "rb") as fh:
                raw = fh.read()
        except OSError:
            return ()
        return tuple(a.decode("utf-8", "replace") for a in raw.split(b"\0") if a)

    def picks(self, stack: StackInfo) -> list[int]:
        """The maker's cycle among the stack's own descendants, never the stack itself."""
        if not stack.running or stack.pid is None:
            return []
        custom = pick_command(stack.env)[0]
        return [p for p in self.descendants(stack.pid) if is_pick(self.cmdline(p), custom)]

    def alive(self, pid: int) -> bool:
        stat = self._stat(pid)
        return stat is not None and stat[0] not in ("Z", "X")

    def signal(self, pid: int, sig: int) -> None:
        os.kill(pid, sig)


SIGKILL = getattr(signal, "SIGKILL", signal.SIGTERM)


def _pids(pids: list[int]) -> str:
    return ", ".join(str(p) for p in pids)


def end_processes(
    procs: Procs,
    pids: list[int],
    *,
    grace_s: float = SCRIPT_GRACE_S,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, list[int]]:
    """SIGTERM, a grace to end in, SIGKILL for whatever outlived it, and a wait for that too.
    Says which ended on the first signal, which needed the second, and which are still there."""

    def wait(left: list[int]) -> list[int]:
        deadline = clock() + grace_s
        while True:
            left = [p for p in left if procs.alive(p)]
            if not left or clock() >= deadline:
                return left
            sleep(PROC_POLL_S)

    for pid in pids:
        with contextlib.suppress(OSError):
            procs.signal(pid, signal.SIGTERM)
    stubborn = wait(list(pids))
    for pid in stubborn:
        with contextlib.suppress(OSError):
            procs.signal(pid, SIGKILL)
    left = wait(stubborn)
    return {
        "terminated": [p for p in pids if p not in stubborn],
        "killed": [p for p in stubborn if p not in left],
        "left": left,
    }


# ── frames ──────────────────────────────────────────────────────────────────────────────


class FrameStore:
    """The newest frames of each camera, with their own stamps and when they arrived here.

    A stamp is trusted when it sits on this machine's wall clock: not after the frame arrived
    here by more than STAMP_SLACK_S, and not older than the camera's stale window. A camera on
    its own clock fails that by years, and then no frame of it is said to be taken after a
    request."""

    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._cond = threading.Condition()
        self._frames: dict[str, deque[tuple[float, bytes, float, bool]]] = {
            cam: deque(maxlen=8) for cam in CAMERA_STREAMS
        }
        self._period: dict[str, float] = {}
        self._last_rx: dict[str, float] = {}
        self.fov_deg: float | None = None
        self.errors: dict[str, str] = {}

    def period(self, cam: str) -> float | None:
        return self._period.get(cam)

    def put(self, cam: str, stamp: float, jpeg: bytes, received: float | None = None) -> None:
        received = self._clock() if received is None else received
        with self._cond:
            last = self._last_rx.get(cam)
            if last is not None and received > last:
                gap = received - last
                old = self._period.get(cam)
                self._period[cam] = gap if old is None else 0.8 * old + 0.2 * gap
            self._last_rx[cam] = received
            period = self._period.get(cam) or 0.0
            trusted = (stamp <= received + STAMP_SLACK_S) and (
                received - stamp <= stale_after_s(period)
            )
            self._frames[cam].append((stamp, jpeg, received, trusted))
            self._cond.notify_all()

    def heard(self, cam: str) -> dict[str, Any]:
        with self._cond:
            frames = self._frames[cam]
            if not frames:
                return {"heard": False, "error": self.errors.get(cam)}
            _, _, received, trusted = frames[-1]
            age = self._clock() - received
            return {
                "heard": age <= stale_after_s(self._period.get(cam) or 0.0),
                "age_s": round(age, 2),
                "period_s": round(self._period.get(cam) or 0.0, 3),
                "stamp_trusted": trusted,
                "error": self.errors.get(cam),
            }

    def first_after(
        self, cam: str, arrived: float, timeout_s: float
    ) -> tuple[float, bytes, bool] | None:
        """The first frame that reached this bridge after `arrived` with a stamp of its own after
        it by STAMP_SLACK_S, with True. From a camera whose stamps cannot be trusted, the first
        frame received after it, with False. Else None. A frame received before the request is
        never handed out for it, whatever its stamp says."""
        deadline = time.monotonic() + timeout_s
        with self._cond:
            while True:
                for stamp, jpeg, received, trusted in self._frames[cam]:
                    if received < arrived:
                        continue
                    if not trusted:
                        return stamp, jpeg, False
                    if stamp >= arrived + STAMP_SLACK_S:
                        return stamp, jpeg, True
                left = deadline - time.monotonic()
                if left <= 0:
                    return None
                self._cond.wait(left)


# ── the twist ───────────────────────────────────────────────────────────────────────────


def shape(vx: float, wz: float, up: Upstream) -> tuple[float, float, str, dict[str, float] | None]:
    """(vx, wz, mode, clip) for one asked twist. An arc keeps |wz| <= 2|vx|/track, so its inner
    track never reverses into a full power pivot. A spin keeps its turn rate within the maker's
    ceiling and is sent as pulses (`TwistPump`)."""
    if vx == 0.0 and wz == 0.0:
        return 0.0, 0.0, "zero", None
    if vx == 0.0:
        if abs(wz) > up.max_wz:
            sent = math.copysign(up.max_wz, wz)
            return 0.0, sent, "spin", {"wz_asked": wz, "wz_sent": sent}
        return 0.0, wz, "spin", None
    # a track width that is not a rover's never divides: the twist route refuses it first
    limit = 2.0 * abs(vx) / up.track_m if up.track_m > 0 else 0.0
    if abs(wz) > limit:
        sent = math.copysign(limit, wz)
        return vx, sent, "arc", {"wz_asked": wz, "wz_sent": sent}
    return vx, wz, ("straight" if wz == 0.0 else "arc"), None


_EDGE_S = 1e-6
"""A scheduler that wakes on an edge computes it a hair short: this far is the edge itself."""


def rest_s(wz: float, up: Upstream) -> float:
    """The zeros after a spin pulse: the maker's own rest at the ceiling, longer below it."""
    return up.rest_s * up.max_wz / max(1e-9, min(abs(wz), up.max_wz))


class Publisher(Protocol):
    def publish_twist(self, chan: str, vx: float, wz: float) -> None: ...

    def publish_true(self, chan: str) -> None: ...


class TwistPump:
    """The twist quackd asked for, published at the mission's own rate while it is fresh.

    After the firmware's deadman with no new request, one zero and then silence: every message
    on the pad's channel cancels navigation, a zero as much as any. A spin is the maker's
    pulses: one pulse of twist, then a rest of zeros, timed here by this clock."""

    def __init__(
        self,
        bus: Publisher,
        up: Upstream,
        chan: str,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.bus, self.up, self.chan, self.clock = bus, up, chan, clock
        self._lock = threading.Lock()
        self._want: tuple[float, float, str] | None = None
        self._at = -1e18
        self._pulse_t0 = 0.0
        self._zeroed = True
        self.published = 0
        self.pulses = 0
        self._was_on = False
        self.wake = threading.Event()
        """Set by a new twist, so the scheduler plans its next edge from it at once."""

    def fresh(self) -> bool:
        with self._lock:
            return self._want is not None and self.clock() - self._at <= self.up.deadman_s

    def post(self, vx: float, wz: float) -> dict[str, Any]:
        sent_vx, sent_wz, mode, clip = shape(vx, wz, self.up)
        with self._lock:
            now = self.clock()
            was_spin = self._want is not None and self._want[2] == "spin"
            if mode == "spin" and not (was_spin and now - self._at <= self.up.deadman_s):
                self._pulse_t0 = now
            self._want, self._at, self._zeroed = (sent_vx, sent_wz, mode), now, False
        self.tick()
        self.wake.set()
        return {
            "ok": True,
            "sent": {"vx": sent_vx, "wz": sent_wz, "mode": mode},
            "clipped": clip,
            "channel": self.chan,
        }

    def tick(self) -> float | None:
        """What is due now, published, and when the next edge falls on this clock: a pulse's
        end exactly pulse_s after its start, the next pulse a whole period after it, the next
        resend at the mission's rate, or the deadman's one zero. None with nothing due."""
        with self._lock:
            now = self.clock()
            expires = self._at + self.up.deadman_s
            resend = now + 1.0 / self.up.pump_hz
            if self._want is not None and now <= expires:
                vx, wz, mode = self._want
                if mode == "spin":
                    period = self.up.pulse_s + rest_s(wz, self.up)
                    phase = (now - self._pulse_t0) % period
                    # an edge reached by its own wake reads a hair short of it in floating point:
                    # that close to the next pulse is the next pulse
                    if period - phase <= _EDGE_S:
                        phase = 0.0
                    on = phase < self.up.pulse_s - _EDGE_S
                    if on and not self._was_on:
                        self.pulses += 1
                    self._was_on = on
                    out = (0.0, wz) if on else (0.0, 0.0)
                    edge = now - phase + (self.up.pulse_s if on else period)
                    nxt = min(edge, resend)
                else:
                    out = (vx, wz)
                    nxt = resend
                # the deadman's zero is due the moment the twist is no longer fresh
                due: float | None = min(nxt, expires + _EDGE_S)
            elif not self._zeroed:
                self._zeroed, self._want, out = True, None, (0.0, 0.0)
                due = None
            else:
                return None
            self.published += 1
        self.bus.publish_twist(self.chan, *out)
        return due

    def zero(self) -> None:
        """One zero now, and nothing after it: the stop's own twist."""
        with self._lock:
            self._want, self._zeroed = None, True
            self.published += 1
        self.bus.publish_twist(self.chan, 0.0, 0.0)


# ── the arm ─────────────────────────────────────────────────────────────────────────────


class ArmBus(Protocol):
    def arm_joints(self) -> dict[str, float]: ...

    def arm_cancel(self) -> str: ...


@dataclass
class Job:
    id: str
    state: str
    started: float
    reason: str = ""
    exit: int | None = None
    held: bool | None = None
    lines: deque[str] = field(default_factory=lambda: deque(maxlen=12))
    cancelled: bool = False
    session: str | None = None
    """The session that started it, whose halt cancels it."""

    def doc(self, now: float) -> dict[str, Any]:
        return {
            "id": self.id,
            "state": self.state,
            "reason": self.reason,
            "exit": self.exit,
            "held": self.held,
            "elapsed_s": round(now - self.started, 1),
            "lines": list(self.lines),
        }


NOTHING_EXECUTING = ("ALREADY_STOPPED", "NO_TRAJECTORY_TASK")
"""What cancel_trajectory answers when no trajectory was executing."""


def arm_holds(bus: ArmBus, up: Upstream, sleep: Callable[[float], None]) -> bool | None:
    """Two reads of the joints: True when every joint moved slower than the maker's own still
    rule between them, False when one moved faster, None when the arm did not answer."""
    try:
        first = bus.arm_joints()
        sleep(ARM_HOLD_GAP_S)
        second = bus.arm_joints()
    except Exception:
        return None
    for name in up.arm_joints:
        if name not in first or name not in second:
            return None
        speed = abs(second[name] - first[name]) * up.ticks_per_rad / ARM_HOLD_GAP_S
        if speed >= up.still_ticks_per_s:
            return False
    return True


def _moved(up: Upstream, first: dict[str, float], second: dict[str, float], gap_s: float) -> bool:
    """Whether a joint moved faster than the maker's own still rule between two reads `gap_s`
    apart. A joint missing from either read is no motion seen."""
    for name in up.arm_joints:
        if name in first and name in second:
            speed = abs(second[name] - first[name]) * up.ticks_per_rad / gap_s
            if speed >= up.still_ticks_per_s:
                return True
    return False


def cancel_and_read(
    bus: ArmBus, up: Upstream, sleep: Callable[[float], None], *, always_read: bool = True
) -> dict[str, Any]:
    """cancel_trajectory, the joints read twice, and a second cancel that must find nothing
    executing: a trajectory in one of its pauses reads as still too, so two still reads alone
    never say the arm holds. A cancel that still finds a trajectory, or reads that moved, are a
    cancel and two reads more.

    With always_read off, a first cancel that finds nothing executing is the end of it, with
    no reads: its caller turns it off only when nothing of quackd's, a cycle or a pick of the
    mission's, was starting or running, so nothing of quackd's can be about to land."""
    out: dict[str, Any] = {"cancel": None, "held": None}
    try:
        out["cancel"] = bus.arm_cancel()
    except Exception as e:
        out["error"] = f"cancel_trajectory did not answer: {type(e).__name__}: {e}"
        return out
    if not always_read and out["cancel"] in NOTHING_EXECUTING:
        out["executing"] = False
        return out
    held = _held_after(bus, up, sleep, out)
    if held is False:
        with contextlib.suppress(Exception):
            out["cancel_again"] = bus.arm_cancel()
        held = _held_after(bus, up, sleep, out)
    out["held"] = held
    return out


def _held_after(
    bus: ArmBus, up: Upstream, sleep: Callable[[float], None], out: dict[str, Any]
) -> bool | None:
    """Two reads, and if they read still, a cancel that finds nothing to cancel."""
    held = arm_holds(bus, up, sleep)
    if held is not True:
        return held
    try:
        confirm = bus.arm_cancel()
    except Exception:
        return None
    out["confirm"] = confirm
    if confirm == "CANCELLED":
        return False  # still executing: a pause read as still
    return True if confirm in NOTHING_EXECUTING else None


def ending_of(code: int | None, cancelled: bool) -> str:
    """How a run of tools/moss_arm_play.py ended, from its own exit code."""
    if cancelled:
        return "cancelled"
    return {0: "done", 2: "refused", 3: "refused", 4: "lag"}.get(
        code if code is not None else -1, "failed"
    )


class ArmJobs:
    """The maker's cycle as a subprocess, its progress read from its own lines, its cancel the
    three steps that make the arm hold: the script stopped, the trajectory cancelled, the joints
    read twice."""

    def __init__(
        self,
        bus: ArmBus,
        up: Upstream,
        argv_for: Callable[[str, str | None], list[str]],
        cwd: str | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        on_running: Callable[[Job], None] | None = None,
        on_end: Callable[[Job], None] | None = None,
    ) -> None:
        self.bus, self.up, self.argv_for, self.cwd = bus, up, argv_for, cwd
        self.clock, self.sleep = clock, sleep
        self.on_running, self.on_end = on_running, on_end
        self._lock = threading.Lock()
        self.job: Job | None = None
        self._proc: subprocess.Popen[str] | None = None
        self._count = 0
        self._prefix = secrets.token_hex(4)
        """Drawn per process, so a bridge restarted since never hands out an id that a session
        from before it still holds: a close cancels its cycle by id."""

    def running(self) -> bool:
        with self._lock:
            return self.job is not None and self.job.state in JOB_RUNNING

    def running_id(self) -> str | None:
        """The id of the cycle running now, or None."""
        with self._lock:
            return self.job.id if self.job is not None and self.job.state in JOB_RUNNING else None

    def running_session(self) -> str | None:
        """The session that started the cycle running now, or None."""
        with self._lock:
            running = self.job is not None and self.job.state in JOB_RUNNING
            return self.job.session if running and self.job is not None else None

    def start(
        self,
        pick_pose: str,
        from_phase: str | None,
        session: str | None = None,
        env: dict[str, str] | None = None,
    ) -> tuple[bool, str]:
        """The maker's script, in `env`, the environment of the run that holds the arm, as the
        mission runs it in its own. None inherits this process's."""
        with self._lock:
            if self.job is not None and self.job.state in JOB_RUNNING:
                return False, "another arm job is running: wait for it, or stop"
            self._count += 1
            job = Job(
                f"job-{self._prefix}-{self._count}", "starting", self.clock(), session=session
            )
            try:
                proc = subprocess.Popen(
                    self.argv_for(pick_pose, from_phase),
                    cwd=self.cwd,
                    env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    # a session of its own: a Ctrl-C on the bridge's terminal, or a signal
                    # to its group, never kills the script behind the bridge's back
                    start_new_session=True,
                )
            except OSError as e:
                return False, f"the arm script did not start: {e}"
            self.job, self._proc = job, proc
        threading.Thread(
            target=self._read, args=(job, proc), name="moss-arm-job", daemon=True
        ).start()
        return True, job.id

    def _read(self, job: Job, proc: subprocess.Popen[str]) -> None:
        assert proc.stdout is not None
        for raw in proc.stdout:
            line = raw.rstrip()
            began = False
            with self._lock:
                job.lines.append(line)
                if line.startswith("running:") and job.state == "starting":
                    job.state, began = "running", True
                elif "ARM LAG" in line or line.startswith(("refused", "done:")):
                    job.reason = line
            if began and self.on_running is not None:
                self.on_running(job)
        code = proc.wait()
        with self._lock:
            ending, cancelled = ending_of(code, job.cancelled), job.cancelled
        after: dict[str, Any] | None = None
        if ending not in ("done", "refused") and not cancelled:
            # the script is gone and its trajectory may be playing on, with nothing watching
            # it for lag: an ARM LAG cancelled it itself, but an exception, an exit of its own
            # or a signal did not. So it is cancelled here and the hold read, before the job
            # ends and a twist or a new cycle may start
            after = cancel_and_read(self.bus, self.up, self.sleep)
        with self._lock:
            job.exit = code
            job.state = ending
            if not job.reason:
                job.reason = f"the script exited {code}"
            if after is not None:
                job.held = after["held"]
                if after.get("error"):
                    job.reason += f". The cancel after it: {after['error']}"
            if self._proc is proc:
                self._proc = None
        if self.on_end is not None:
            self.on_end(job)

    def _stop_script(self) -> bool:
        with self._lock:
            proc, job = self._proc, self.job
            if job is not None and job.state in JOB_RUNNING:
                job.cancelled = True
        if proc is None or proc.poll() is not None:
            return False
        proc.terminate()
        try:
            proc.wait(timeout=SCRIPT_GRACE_S)
        except subprocess.TimeoutExpired:
            proc.kill()
            with contextlib.suppress(subprocess.TimeoutExpired):
                proc.wait(timeout=SCRIPT_GRACE_S)
        return True

    def holds(self) -> bool | None:
        return arm_holds(self.bus, self.up, self.sleep)

    def cancel(self) -> dict[str, Any]:
        """The script first, so it cannot send the trajectory after the cancel, then the cancel,
        then the two reads. A second cancel and read when the first did not hold.

        The hold is always read, whatever the cancel answered: ALREADY_STOPPED says nothing was
        executing at that instant, not that nothing is about to, and an arm whose joints were
        not read twice is reported unread (None), never held."""
        with self._lock:
            starting = self.job is not None and self.job.state == "starting"
        out: dict[str, Any] = {"script": self._stop_script()}
        out.update(cancel_and_read(self.bus, self.up, self.sleep))
        if starting and not out.get("error"):
            # an execute_trajectory the script sent just before it was ended can land after
            # the cancel, and nothing would cancel it: once more, a little later
            self.sleep(STARTING_RECANCEL_S)
            late = cancel_and_read(self.bus, self.up, self.sleep)
            out["late"] = late
            if late.get("cancel") == "CANCELLED" or late.get("held") is not True:
                out["held"] = late.get("held")
        if out["held"] is False:
            # an arm read moving is said at once, and a hold only once the stop's window has
            # passed with the arm still (held_after_guard)
            self._mark(False)
        return out

    def held_after_guard(self, held: bool | None) -> None:
        """The hold of the cycle a stop cancelled, once the stop's window has passed: the
        window's word, which replaces what the stop read."""
        with self._lock:
            if self.job is not None and self.job.cancelled:
                self.job.held = held

    def _mark(self, held: bool | None) -> None:
        with self._lock:
            if self.job is not None and self.job.cancelled and self.job.held is None:
                self.job.held = held

    def doc(self) -> dict[str, Any] | None:
        with self._lock:
            return None if self.job is None else self.job.doc(self.clock())


# ── the bus ─────────────────────────────────────────────────────────────────────────────


class Bus(Publisher, ArmBus, Protocol):
    frames: FrameStore

    def nav_seen(self) -> dict[str, tuple[Any, float]]: ...

    def mission_status(self, timeout_s: float) -> dict[str, Any] | None: ...

    def mission_stop(self) -> None: ...

    def close(self) -> None: ...


class LcmBus:
    """The rover's own bus: LCM, dimos_lcm's messages, and dimOS RPC, all imported here."""

    def __init__(self, up: Upstream, frames: FrameStore, *, arm: bool, mission: bool) -> None:
        import lcm
        from dimos_lcm.geometry_msgs import PoseStamped, Twist, Vector3
        from dimos_lcm.sensor_msgs import CameraInfo, Image
        from dimos_lcm.std_msgs import Bool

        self.up, self.frames = up, frames
        self._twist, self._vector, self._bool = Twist, Vector3, Bool
        self._pose = PoseStamped
        self._image, self._camera_info = Image, CameraInfo
        self._lc = lcm.LCM()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._nav: dict[str, tuple[Any, float]] = {}
        for cam, stream in CAMERA_STREAMS.items():
            self._lc.subscribe(channel(stream, IMAGE_MSG), self._on_image(cam))
        self._lc.subscribe(channel(CAMERA_INFO_STREAM, CAMERA_INFO_MSG), self._on_info)
        for stream in NAV_BOOL_STREAMS:
            self._lc.subscribe(channel(stream, BOOL_MSG), self._on_nav(stream))
        self._lc.subscribe(channel(GOAL_REQUEST_STREAM, POSE_MSG), self._on_goal)
        threading.Thread(target=self._loop, name="moss-lcm", daemon=True).start()
        self._rpc: Any = None
        self._arm: Any = None
        if arm or mission:
            from dimos.protocol.rpc.pubsubrpc import LCMRPC

            self._rpc = LCMRPC(default_rpc_timeout=RPC_TIMEOUT_S)
            self._rpc.start()
        if arm:
            from dimos.control.coordinator import ControlCoordinator
            from dimos.core.rpc_client import RPCClient

            # the client moss_arm_play.py builds, on an RPC of the bridge's own
            self._arm = RPCClient(
                None, ControlCoordinator, remote_name=up.arm_coordinator, rpc=self._rpc
            )

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._lc.handle_timeout(250)
            except Exception as e:
                log.warning("the LCM handler failed: %s", e)
                time.sleep(1.0)

    def _on_image(self, cam: str) -> Callable[[str, bytes], None]:
        def handler(_chan: str, raw: bytes) -> None:
            received = time.time()
            try:
                msg = self._image.lcm_decode(raw)
                stamp = float(msg.header.stamp.sec) + float(msg.header.stamp.nsec) * 1e-9
                if str(msg.encoding).lower() != "jpeg":
                    self.frames.errors[cam] = (
                        f"the {cam} camera is {msg.encoding} on the bus, not JPEG"
                    )
                    return
                data = msg.data if isinstance(msg.data, bytes | bytearray) else bytes(msg.data)
                self.frames.put(cam, stamp, bytes(data), received)
            except Exception as e:
                self.frames.errors[cam] = f"a {cam} frame did not decode: {e}"

        return handler

    def _on_nav(self, stream: str) -> Callable[[str, bytes], None]:
        def handler(_chan: str, raw: bytes) -> None:
            received = time.monotonic()
            with contextlib.suppress(Exception):
                self._nav[stream] = (bool(self._bool.lcm_decode(raw).data), received)

        return handler

    def _on_goal(self, _chan: str, raw: bytes) -> None:
        received = time.monotonic()
        with contextlib.suppress(Exception):
            p = self._pose.lcm_decode(raw).pose.position
            self._nav[GOAL_REQUEST_STREAM] = ({"x": float(p.x), "y": float(p.y)}, received)

    def nav_seen(self) -> dict[str, tuple[Any, float]]:
        return dict(self._nav)

    def _on_info(self, _chan: str, raw: bytes) -> None:
        try:
            msg = self._camera_info.lcm_decode(raw)
            fx, width = float(msg.K[0]), float(msg.width)
            if fx > 0 and width > 0:
                self.frames.fov_deg = round(math.degrees(2 * math.atan(width / (2 * fx))), 1)
        except Exception as e:
            self.frames.errors["camera_info"] = f"camera_info did not decode: {e}"

    def publish_twist(self, chan: str, vx: float, wz: float) -> None:
        v = self._vector
        msg = self._twist(linear=v(x=float(vx), y=0.0, z=0.0), angular=v(x=0.0, y=0.0, z=float(wz)))
        with self._lock:
            self._lc.publish(chan, msg.lcm_encode())

    def publish_true(self, chan: str) -> None:
        with self._lock:
            self._lc.publish(chan, self._bool(data=True).lcm_encode())

    def arm_joints(self) -> dict[str, float]:
        if self._arm is None:
            raise RuntimeError("the arm's coordinator is not loaded")
        try:
            got = self._arm.get_joint_positions() or {}
        finally:
            self._release()
        return {str(k): float(v) for k, v in dict(got).items()}

    def arm_cancel(self) -> str:
        if self._arm is None:
            raise RuntimeError("the arm's coordinator is not loaded")
        try:
            result = self._arm.cancel_trajectory()
        finally:
            self._release()
        status = getattr(result, "status", None)
        return str(getattr(status, "name", status))

    def _release(self) -> None:
        """The client keeps one callback per call until told to let go, and this one is asked
        before every verb for as long as the bridge runs. stop_rpc_client() lets go of them and,
        on an RPC the client does not own, leaves it running (dimOS rpc_client.py)."""
        with contextlib.suppress(Exception):
            self._arm.stop_rpc_client()

    def mission_status(self, timeout_s: float) -> dict[str, Any] | None:
        if self._rpc is None:
            return None
        try:
            result, unsub = self._rpc.call_sync(
                f"{self.up.mission_name}/mission_status", ((), {}), rpc_timeout=timeout_s
            )
        except Exception:
            return None
        with contextlib.suppress(Exception):
            unsub()
        return dict(result) if isinstance(result, dict) else {"raw": repr(result)}

    def mission_stop(self) -> None:
        # what an RPCClient sends for stop: no answer waited for, the module closes its RPC
        # service before it could give one
        if self._rpc is not None:
            self._rpc.call_nowait(f"{self.up.mission_name}/stop", ((), {}))

    def close(self) -> None:
        self._stop.set()
        if self._rpc is not None:
            with contextlib.suppress(Exception):
                self._rpc.stop()


_TINY_JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAQEAYABgAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0a"
    "HBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/wAALCAACAAIBAREA/8QAHwAAAQUBAQEB"
    "AQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1Fh"
    "ByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZ"
    "WmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXG"
    "x8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/9oACAEBAAA/APn+iiiv/9k="
)
"""A 2 by 2 grey JPEG, so `--fake` serves frames where no image library is installed."""


@dataclass
class FakePick:
    """The mission's own run of the maker's script, as a fake process. It spends `startup_s`
    importing dimOS, building its client and reading the joints, as the real one does before
    its one execute_trajectory, then sends the trajectory unless it was ended first, and the
    coordinator plays that trajectory to its end whatever becomes of the script."""

    pid: int
    started: float
    startup_s: float
    ignores_term: bool = False
    alive: bool = True
    sent: bool = False
    signals: int = 0


class FakeBus:
    """`--fake`: an LCM bus, an arm coordinator and a litter mission in memory, never a rover.

    Every publish is recorded with its time. The arm drifts while a trajectory runs and holds
    after a cancel, unless told to ignore one, and an arm that does not answer makes its caller
    wait out the RPC's timeout, as a coordinator that is not there does. The mission answers
    until its stop has run, then falls silent after `mission_stop_s`, as the real one closes its
    RPC service, and its stop leaves its own pick running, as the real one's does: the bus is
    also the process table for that pick (`Procs`)."""

    def __init__(
        self,
        up: Upstream,
        frames: FrameStore,
        *,
        stamps: str = "rover",
        fps: float = 5.0,
        mission_stop_s: float = 0.2,
        arm_ignores_cancel: bool = False,
        arm_still_while_executing: bool = False,
        mission_ignores_stop: bool = False,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        rpc_timeout_s: float = RPC_TIMEOUT_S,
    ) -> None:
        self.up, self.frames, self.clock = up, frames, clock
        self.sleep, self.rpc_timeout_s = sleep, rpc_timeout_s
        self.arm_asks = 0
        """Every call to the arm's coordinator, answered or not."""
        self.published: list[tuple[float, str, tuple[float, ...]]] = []
        self.joints: dict[str, float] = {name: 0.0 for name in (*up.arm_joints, up.gripper_joint)}
        self.executing = False
        self.arm_ignores_cancel = arm_ignores_cancel
        self.arm_still_while_executing = arm_still_while_executing
        """A trajectory in one of its pauses: executing, and the joints read as still."""
        self.arm_answers = True
        self.cancels = 0
        self.mission_answers = True
        self.mission_ignores_stop = mission_ignores_stop
        self.mission_stop_s = mission_stop_s
        self._mission_stop_at: float | None = None
        self.mission_stops = 0
        self.mission = {"state": "EXPLORING", "target": None, "picked": 0, "skipped": 0}
        self.events: list[str] = []
        self.pick: FakePick | None = None
        self._lands_at: float | None = None
        """When an execute_trajectory already on the bus lands, whatever ended its sender."""
        self._pids = 40000
        self.nav: dict[str, tuple[Any, float]] = {}
        """The base stack's navigation as the bus last carried it, with when it arrived."""
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._stamps = stamps
        self._fps = fps
        if fps > 0:
            threading.Thread(target=self._camera, name="moss-fake-camera", daemon=True).start()

    # ── the mission's own pick, and the process table a stop reads it from ──────────────

    def start_mission_pick(self, startup_s: float = 0.5, *, ignores_term: bool = False) -> int:
        """The mission reaches its pick point and runs the maker's script, which sends its
        trajectory `startup_s` from now unless something ends it first."""
        with self._lock:
            self._pids += 1
            self.pick = FakePick(self._pids, self.clock(), startup_s, ignores_term=ignores_term)
            self.mission["state"] = "PICKING"
            self.events.append("the mission's pick starts")
            return self.pick.pid

    def in_flight(self, lands_in_s: float) -> None:
        """An execute_trajectory already sent, which lands `lands_in_s` from now whatever
        becomes of the script that sent it: a cancel the coordinator handles before it finds
        nothing to cancel, and the trajectory then plays."""
        with self._lock:
            self._lands_at = self.clock() + lands_in_s

    def _advance(self) -> None:
        """With the lock held: the script's trajectory goes out once its startup is over, and
        one already in flight lands when it is due."""
        if self._lands_at is not None and self.clock() >= self._lands_at:
            self._lands_at = None
            self.executing = True
            self.events.append("a trajectory in flight lands")
        p = self.pick
        if p is not None and p.alive and not p.sent and self.clock() >= p.started + p.startup_s:
            p.sent = True
            self.executing = True
            self.events.append("the mission's script: execute_trajectory")

    def picks(self, stack: StackInfo) -> list[int]:
        with self._lock:
            self._advance()
            return [self.pick.pid] if self.pick is not None and self.pick.alive else []

    def descendants(self, pid: int) -> list[int]:
        return []  # the fake mission's pick runs the script itself, with nothing under it

    def alive(self, pid: int) -> bool:
        with self._lock:
            self._advance()
            return self.pick is not None and self.pick.pid == pid and self.pick.alive

    def signal(self, pid: int, sig: int) -> None:
        with self._lock:
            self._advance()
            p = self.pick
            if p is None or p.pid != pid or not p.alive:
                raise ProcessLookupError(pid)
            p.signals += 1
            # the first signal asks, and any later one is the kill, which no script outlives
            kill = p.signals > 1 or sig != signal.SIGTERM
            self.events.append("pick script " + ("SIGKILL" if kill else "SIGTERM"))
            if kill or not p.ignores_term:
                p.alive = False

    def _camera(self) -> None:
        boot = time.time()
        while not self._stop.wait(1.0 / self._fps):
            now = time.time()
            # a camera on its own boot clock, the case the bridge must not trust
            stamp = now if self._stamps == "rover" else now - boot
            for cam in CAMERA_STREAMS:
                self.frames.put(cam, stamp, _TINY_JPEG, now)

    def publish_twist(self, chan: str, vx: float, wz: float) -> None:
        with self._lock:
            self.published.append((self.clock(), chan, (vx, wz)))
            self.events.append(f"twist {chan} {vx:g} {wz:g}")

    def publish_true(self, chan: str) -> None:
        with self._lock:
            self.published.append((self.clock(), chan, (1.0,)))
            self.events.append(f"true {chan}")
            # what the bridge publishes is on the bus, and its own reading of the nav sees it
            for stream in NAV_BOOL_STREAMS:
                if chan == channel(stream, BOOL_MSG):
                    self.nav[stream] = (True, self.clock())

    def nav_publish(self, stream: str, value: Any) -> None:
        """A message on one of the base stack's nav channels, from the planner, the explorer or
        the cockpit: a Bool's data, or a goal's {x, y}."""
        with self._lock:
            self.nav[stream] = (value, self.clock())

    def nav_seen(self) -> dict[str, tuple[Any, float]]:
        with self._lock:
            return dict(self.nav)

    def _arm_silent(self) -> None:
        self.sleep(self.rpc_timeout_s)
        raise TimeoutError("the fake arm's coordinator does not answer")

    def arm_joints(self) -> dict[str, float]:
        self.arm_asks += 1
        if not self.arm_answers:
            self._arm_silent()
        with self._lock:
            self._advance()
            if self.executing and not self.arm_still_while_executing:
                for name in self.up.arm_joints:
                    # well past the still rule, so a moving arm reads as moving
                    self.joints[name] += 10 * self.up.still_ticks_per_s / self.up.ticks_per_rad
            return dict(self.joints)

    def arm_cancel(self) -> str:
        self.arm_asks += 1
        if not self.arm_answers:
            self._arm_silent()
        with self._lock:
            self._advance()
            self.cancels += 1
            self.events.append("arm cancel")
            if not self.executing:
                return "ALREADY_STOPPED"
            if not self.arm_ignores_cancel:
                self.executing = False
            return "CANCELLED"

    def mission_status(self, timeout_s: float) -> dict[str, Any] | None:
        with self._lock:
            silent = self._mission_stop_at is not None and self.clock() >= self._mission_stop_at
            answer = None if not self.mission_answers or silent else dict(self.mission)
        if answer is None:
            time.sleep(min(timeout_s, 0.05))
        return answer

    def mission_stop(self) -> None:
        with self._lock:
            self.mission_stops += 1
            self.events.append("mission stop")
            if not self.mission_ignores_stop and self._mission_stop_at is None:
                self._mission_stop_at = self.clock() + self.mission_stop_s

    def job_running(self, job: Job) -> None:
        """The script sent its trajectory: the fake coordinator runs it."""
        with self._lock:
            self.executing = True

    def job_ended(self, job: Job) -> None:
        """A trajectory that ran out is over. One the script cancelled for lag is over too, and
        one quackd cancelled was ended by the cancel (or, told to, ignored it)."""
        with self._lock:
            if job.state in ("done", "lag"):
                self.executing = False

    def close(self) -> None:
        self._stop.set()


def _finite(value: Any) -> float | None:
    if isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value):
        return float(value)
    return None


def nav_state(seen: dict[str, tuple[Any, float]], blueprint: str | None) -> dict[str, Any]:
    """exploring, explore_done, goal and goal_reached, derived from the same channels and by the
    same rules as the cockpit's own `_nav`, for the base stack: an explore_cmd newer than any
    stop, done or stop_movement is an exploration, only an explore stack explores, and a
    goal_reached counts only when it came after the goal it answers."""

    def got(stream: str) -> tuple[Any, float | None]:
        return seen.get(stream, (None, None))

    start, t_start = got(EXPLORE_STREAM)
    _, t_stop = got(STOP_EXPLORE_STREAM)
    done, t_done = got(EXPLORE_DONE_STREAM)
    halt, t_halt = got(STOP_MOVEMENT_STREAM)
    began = t_start if start else None
    ended = max(
        t_stop or -1.0, (t_done if done else None) or -1.0, (t_halt if halt else None) or -1.0
    )
    exploring = began is not None and began > ended
    if blueprint is not None and "explore" not in blueprint:
        exploring = False
    explore_done = bool(done) and (began is None or (t_done or 0.0) > began)
    pose, t_goal = got(GOAL_REQUEST_STREAM)
    goal = None
    if isinstance(pose, dict):
        x, y = _finite(pose.get("x")), _finite(pose.get("y"))
        if x is not None and y is not None:
            goal = {"x": round(x, 3), "y": round(y, 3)}
    reached, t_reached = got(GOAL_REACHED_STREAM)
    after = t_reached is not None and (t_goal is None or t_reached > t_goal)
    return {
        "exploring": exploring,
        "explore_done": explore_done,
        "goal": goal,
        "goal_reached": bool(reached) if after else None,
    }


# ── the bridge ──────────────────────────────────────────────────────────────────────────


@dataclass
class Session:
    """One client's session, named by an id the client drew at random. It keeps its own
    period, its own word of what it started and its own expiry, and its halt touches nothing
    another session started."""

    id: str
    keepalive_s: float = 0.5
    started: str | None = None
    last: float = 0.0
    run: str | None = None
    """The base run its client connected to, which its halt is scoped to."""


class Bridge:
    """Everything the HTTP server answers, with no HTTP in it, so a test can drive it whole."""

    def __init__(
        self,
        *,
        stack: Callable[[], StackInfo],
        up: Upstream | None,
        bus: Bus | None,
        bus_error: str | None = None,
        up_error: str | None = None,
        arm_argv: Callable[[str, str | None], list[str]] | None = None,
        moss_dir: str | None = None,
        arm_error: str | None = None,
        token: str | None = None,
        fake: bool = False,
        reread: Callable[[StackInfo], tuple[Upstream | None, str | None]] | None = None,
        make_bus: Callable[[Upstream], Bus] | None = None,
        procs: Procs | None = None,
        clock: Callable[[], float] = time.monotonic,
        wall: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
        arm_guard_s: float = ARM_GUARD_S,
    ) -> None:
        self._stack = stack
        self.up, self.bus = up, bus
        # `--fake`'s bus keeps its own mission, so it is that mission's process table too
        fake_procs = bus if isinstance(bus, FakeBus) else None
        self.procs: Procs = procs or fake_procs or ProcTable()
        self.bus_error, self.up_error, self.arm_error = bus_error, up_error, arm_error
        self.token = token or None
        self.fake = fake
        self.clock, self.wall, self.sleep = clock, wall, sleep
        self.moss_dir = moss_dir
        self.started_stack = stack()
        self.pump: TwistPump | None = None
        self.jobs: ArmJobs | None = None
        self._arm_argv = arm_argv
        if bus is not None and up is not None:
            self._build(bus, up, self.started_stack)
        self.sessions: dict[str, Session] = {}
        self.expired = 0
        """How many sessions missed their keepalives, whether or not they had started anything."""
        self.halts = 0
        """How many of those halts halted something: a session that started nothing halts
        nothing, and is not counted here."""
        self.halted: dict[str, dict[str, Any]] = {}
        """Each expired session's halt, by its id, with what it halted, which every keepalive of
        that session reports from then on, so its client learns it was halted."""
        self.last_halt: str | None = None
        self.mission_stopped_run: str | None = None
        """The run whose mission this bridge stopped: stopped stays stopped until a new run."""
        self.mission_heard_run: str | None = None
        """The run whose mission has answered this bridge. Only a mission heard before can be
        heard to fall silent, so only its silence confirms a stop."""
        self._reread = reread
        self._up_run = _runs_of(self.started_stack) if self.started_stack.running else None
        """The base run and the arm's run whose environments the numbers in `up` were read
        from, None for none."""
        self.run_error: str | None = None
        """Why the running stack is one this bridge cannot publish to, or None."""
        self._settle_lock = threading.Lock()
        self._lock = threading.Lock()
        self._motion = threading.Lock()
        """Held across the check and the start of a twist and of a cycle, so the two never both
        pass their check and run together."""
        self._stops = 0
        """How many stops this bridge has run: a start that a stop overtook does not happen."""
        self._make_bus = make_bus
        self.arm_guard_s = arm_guard_s
        """How long the arm is watched after a stop cancelled it: ARM_GUARD_S, but in a test."""
        self._guard: dict[str, Any] | None = None
        """The arm a stop cancelled, while its window lasts: until when, the joints as last read
        and when, and how often they moved and were cancelled again."""
        self.guard_doc: dict[str, Any] | None = None
        """The last window's word on the arm, once it has passed, for /arm."""
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

    def _build(self, bus: Bus, up: Upstream, stack: StackInfo) -> None:
        """The twist pump and the arm's job runner, on a bus this bridge can publish to."""
        self.pump = TwistPump(bus, up, self._pad_channel(stack), self.clock)
        if self._arm_argv is not None:
            self.jobs = ArmJobs(
                bus,
                up,
                self._arm_argv,
                self.moss_dir,
                self.clock,
                self.sleep,
                on_running=getattr(bus, "job_running", None),
                on_end=getattr(bus, "job_ended", None),
            )

    # ── the stack ───────────────────────────────────────────────────────────────────

    def stack(self) -> StackInfo:
        return self._stack()

    def _settle(self, stack: StackInfo) -> None:
        """The numbers again, from a stack that started after the one they were read from.

        The arc clip is only sound with the track width MOSS's own base adapter uses, which is
        the running stack's, overrides and all. A bridge started at boot, before any stack,
        read none of them. A stack this bridge cannot publish to refuses the twist and the arm
        until one it can starts, and the stop is never held for any of this."""
        runs = _runs_of(stack)
        if self._reread is None or not stack.running or runs == self._up_run:
            return
        with self._settle_lock:
            if runs == self._up_run:
                return
            up, error = self._reread(stack)
            self._up_run, self.run_error = runs, error
            if up is None:
                log.warning("the running stack %s: %s", stack.run_id, error)
                return
            self.up = up
            if self.bus is None and self._make_bus is not None:
                # started beside a stack it could not publish to, it builds its bus when one
                # it can reach starts
                try:
                    self.bus = self._make_bus(up)
                except Exception as e:
                    self.bus_error = f"the LCM bus did not open: {type(e).__name__}: {e}"
                else:
                    self.bus_error = None
                    self._build(self.bus, up, stack)
            if self.pump is not None:
                self.pump.up = up
            if self.jobs is not None:
                self.jobs.up = up
            log.info("read the numbers of run %s: track %s m", stack.run_id, up.track_m)

    @staticmethod
    def _pad_channel(stack: StackInfo) -> str:
        stream = PAD_STREAM_NAV if stack.blueprint == "moss-nav" else PAD_STREAM_EXPLORE
        return channel(stream, TWIST_MSG)

    def _stop_twist_channels(self, stack: StackInfo) -> list[str]:
        """The cockpit's own rule for a zero: tele_cmd_vel, and cmd_vel itself on any blueprint
        but moss-explore, since moss-nav has no MovementManager to pass one on."""
        chans = [channel(PAD_STREAM_EXPLORE, TWIST_MSG)]
        if stack.blueprint != "moss-explore":
            chans.append(channel(PAD_STREAM_NAV, TWIST_MSG))
        return chans

    def _litter(self, stack: StackInfo) -> bool:
        return stack.blueprint == LITTER_STACK

    def _arm_answering(self) -> tuple[bool, str | None]:
        """Whether ArmCoordinator answers, by a real round trip, and why not. With no moss-arm
        run registered nothing is asked: an RPC to a coordinator that is not there waits out its
        whole timeout, and this is read before every verb."""
        if self.bus is None or self.up is None:
            return False, self.bus_error or self.up_error
        if self.stack().arm_run is None:
            return False, "no moss-arm run is registered: dimos run moss-dimos.moss-arm"
        try:
            joints = self.bus.arm_joints()
        except Exception as e:
            return False, f"the arm's coordinator did not answer ({type(e).__name__}: {e})"
        missing = [j for j in self.up.arm_joints if j not in joints]
        if missing:
            return False, f"the arm's coordinator does not report {missing}: is moss-arm running?"
        return True, None

    def _mission_doc(self, stack: StackInfo) -> dict[str, Any] | None:
        if not self._litter(stack):
            return None
        refused = pick_command(stack.env)[1]
        doc = self._mission_answer(stack)
        doc["pick_cmd_refused"] = refused
        return doc

    def _mission_answer(self, stack: StackInfo) -> dict[str, Any]:
        stopped = self.mission_stopped_run is not None and self.mission_stopped_run == stack.run_id
        if self.bus is None:
            return {"answering": False, "stopped": stopped, "status": None, "error": self.bus_error}
        if stopped:
            return {"answering": False, "stopped": True, "status": None, "error": None}
        status = self.bus.mission_status(RPC_TIMEOUT_S)
        if status is None:
            return {
                "answering": False,
                "stopped": False,
                "status": None,
                "error": f"{MISSION_RPC}/mission_status did not answer in {RPC_TIMEOUT_S:g} s",
            }
        self.mission_heard_run = stack.run_id or "unknown"
        return {"answering": True, "stopped": False, "status": status, "error": None}

    # ── the routes ──────────────────────────────────────────────────────────────────

    def hello(self) -> dict[str, Any]:
        stack = self.stack()
        self._settle(stack)
        arm_ok, arm_why = self._arm_answering()
        frames = self.bus.frames if self.bus is not None else None
        publisher = None
        if self.pump is not None and self.run_error:
            publisher = {"loaded": False, "error": self.run_error, "channel": None}
        elif self.pump is not None:
            publisher = {"loaded": True, "error": None, "channel": self._pad_channel(stack)}
        else:
            publisher = {"loaded": False, "error": self.bus_error or self.up_error, "channel": None}
        up = self.up
        return {
            "ok": True,
            "protocol": PROTOCOL,
            "protocol_version": PROTOCOL_VERSION,
            "bridge_version": BRIDGE_VERSION,
            "python": sys.version.split()[0],
            "fake": self.fake,
            "stack": stack.doc(),
            "arm_run": None
            if stack.arm_run is None
            else {
                "pid": stack.arm_run.pid,
                "run_id": stack.arm_run.run_id,
                "sim": "--simulation" in " ".join(stack.arm_run.cmdline),
            },
            "publisher": publisher,
            "arm": {
                "answering": arm_ok,
                "error": arm_why,
                "cycle": self.jobs is not None,
                "cycle_error": None if self.jobs is not None else self.arm_error,
            },
            "mission": self._mission_doc(stack),
            "track_m": None if up is None else up.track_m,
            "ramp_ms": None if up is None or up.ramp_ms is None else list(up.ramp_ms),
            "stop_ramp_s": None if up is None else up.stop_ramp_s,
            "shaping": None
            if up is None
            else {
                "max_wz": up.max_wz,
                "pulse_s": up.pulse_s,
                "rest_s": up.rest_s,
                "deadman_s": up.deadman_s,
                "pump_hz": up.pump_hz,
            },
            "cameras": {cam: frames.heard(cam) for cam in CAMERA_STREAMS} if frames else {},
            "fov_deg": None if frames is None else frames.fov_deg,
            "sessions": self._sessions_doc(),
        }

    def twist(self, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        try:
            vx, wz = float(body.get("vx", 0.0)), float(body.get("wz", 0.0))
        except (TypeError, ValueError):
            return 400, {"ok": False, "reason": "a twist needs numbers vx and wz"}
        if not (math.isfinite(vx) and math.isfinite(wz)):
            return 400, {"ok": False, "reason": "a twist must be finite"}
        stops = self._stops
        stack = self.stack()
        self._settle(stack)
        if self.pump is None:
            return 503, {
                "ok": False,
                "reason": f"no twist publisher: {self.bus_error or self.up_error}",
            }
        if self.run_error:
            return 503, {"ok": False, "reason": self.run_error}
        if self._litter(stack):
            return 409, {"ok": False, "reason": "on moss-litter the mission owns the base"}
        if stack.blueprint not in BASE_STACKS:
            return 409, {"ok": False, "reason": f"no twist on {stack.blueprint or 'no stack'}"}
        track = self.pump.up.track_m
        if not (math.isfinite(track) and TRACK_MIN_M <= track <= TRACK_MAX_M):
            return 503, {
                "ok": False,
                "reason": f"the stack's track width reads {track} m, which is no rover's: the "
                "arc clip would be wrong, so no twist goes out until MOSS's robot profile "
                f"gives one between {TRACK_MIN_M:g} and {TRACK_MAX_M:g} m",
            }
        with self._motion:
            if self.jobs is not None and self.jobs.running():
                return 409, {"ok": False, "reason": "the arm is playing a cycle: stop it first"}
            if self._stops != stops:
                return 409, {"ok": False, "reason": "a stop came in while this twist was asked"}
            self.pump.chan = self._pad_channel(stack)
            return 200, self.pump.post(vx, wz)

    def stop(self, run: str | None = None, session: str | None = None) -> dict[str, Any]:
        """Idempotent, never raises, and says what it did. On moss-litter the mission's stop
        goes out first, then at once the zero, so the base stops whatever the rest takes, then
        the end of the mission's own pick and the arm's cancel, and only then is the mission's
        stop confirmed. A cycle cancelled under a
        mission not yet told to stop makes its script exit on its lag watch, the mission marks
        the pick failed, and its next tick explores again. The mission's stop leaves its pick
        running, and a pick still starting sends its trajectory after any cancel, so its script
        is ended first. A cancel that waited for the confirmation would leave the arm moving for
        as long as the confirmation took. Off moss-litter the zero goes first, so a twist and a
        planner goal end at once whatever the arm's coordinator takes to answer.

        `run` is the base run the caller connected to, and `session` the caller's session. A
        stack restarted since is another run, and the stop touches nothing of it, cancels only
        a cycle that session started, and says why. A stop that names no run stops whatever
        runs, and one that names no session cancels any cycle of quackd's.

        Every stop does the same arm step (_cancel_arm), whoever sends it: a cancel that finds
        nothing executing, with nothing of quackd's in play, reads nothing and watches nothing."""
        stack = self.stack()
        did: list[str] = []
        errors: list[str] = []
        doc: dict[str, Any] = {
            "ok": True,
            "did": did,
            "errors": errors,
            "mission": None,
            "pick": None,
            "arm": None,
        }
        if self.bus is None:
            errors.append(f"no bus: {self.bus_error or self.up_error}")
            doc["ok"] = False
            return doc
        with self._lock:
            self._stops += 1
        if run is not None and stack.run_id is not None and run != stack.run_id:
            own = run_alive(stack, run)
            if own is not None:
                # the run quackd connected to still runs, beside a newer one started by hand: it is
                # the one this stop is for
                did.append(f"run {run} still runs beside the newer run {stack.run_id}: stopping it")
                stack = own
        if run is not None and stack.run_id is not None and run != stack.run_id:
            doc["run_changed"], doc["run"] = True, stack.run_id
            errors.append(
                f"the stack quackd connected to (run {run}) is not running any more, and run "
                f"{stack.run_id} runs now: this stop touches nothing of it"
            )
            mine = session is None or (
                self.jobs is not None and self.jobs.running_session() == session
            )
            if self.jobs is not None and self.jobs.running() and mine:
                doc["arm"] = self._cancel_arm(did, errors)
            doc["ok"] = False
            return doc
        litter = self._litter(stack)
        sent = self._send_mission_stop(stack, did, errors) if litter else None
        cancelled_at = self.clock()
        if litter:
            # the base first: ending the pick and the arm's cancel can take seconds
            self._zeros(stack, did, errors)
            doc["pick"] = self._end_mission_pick(stack, did, errors)
            doc["arm"] = self._cancel_arm(did, errors, picked=bool(doc["pick"]))
            cancelled_at = self.clock()
        else:
            self._zeros(stack, did, errors)
            doc["arm"] = self._cancel_arm(did, errors)
        if sent is not None:
            doc["mission"] = self._confirm_mission_stop(stack, sent, did, errors)
        if litter:
            self._after_the_mission(stack, doc, did, errors, cancelled_at)
        if self._litter(stack) and doc["mission"] == "stopped":
            # once more, after the mission's loop has ended, so the last word on the pad is ours
            with contextlib.suppress(Exception):
                self.bus.publish_twist(channel(PAD_STREAM_EXPLORE, TWIST_MSG), 0.0, 0.0)
        # a failure met by both looks is said once
        errors[:] = list(dict.fromkeys(errors))
        doc["ok"] = not errors
        return doc

    def _after_the_mission(
        self,
        stack: StackInfo,
        doc: dict[str, Any],
        did: list[str],
        errors: list[str],
        cancelled_at: float,
    ) -> None:
        """The arm once more, once the mission's stop has had its time. The mission's last tick
        can start a pick after the first look and before its stop takes effect, and a pick ended
        just after it sent its trajectory leaves that trajectory to land after the first cancel.
        So the stack's picks are looked for again and ended, and no sooner than
        STARTING_RECANCEL_S after the first cancel the arm is cancelled and read again, and that
        reading is the stop's word on the arm."""
        late_pick = self._end_mission_pick(stack, did, errors)
        wait = cancelled_at + STARTING_RECANCEL_S - self.clock()
        if wait > 0:
            self.sleep(wait)
        late = self._cancel_arm(did, errors, picked=bool(doc["pick"] or late_pick))
        doc["late"] = {"pick": late_pick, "arm": late}
        if late is None or late.get("error"):
            return
        if late.get("cancel") == "CANCELLED":
            did.append("a trajectory that landed after the first cancel was cancelled")
        first = doc.get("arm")
        word = {"held": late.get("held"), "read_still": late.get("read_still", False)}
        doc["arm"] = {**first, **word} if isinstance(first, dict) else late

    def _zeros(self, stack: StackInfo, did: list[str], errors: list[str]) -> None:
        """A zero on every channel the cockpit's own soft stop zeroes, and the pump's own."""
        assert self.bus is not None
        for chan in self._stop_twist_channels(stack):
            try:
                self.bus.publish_twist(chan, 0.0, 0.0)
                did.append(f"zero twist on {chan}")
            except Exception as e:
                errors.append(f"the zero twist on {chan}: {e}")
        if self.pump is not None:
            try:
                self.pump.zero()
            except Exception as e:  # said, and the mission's stop is still confirmed
                errors.append(f"the pump's zero: {e}")

    def _send_mission_stop(
        self, stack: StackInfo, did: list[str], errors: list[str]
    ) -> tuple[str, float]:
        """The mission's own stop, sent without waiting for an answer, as dimOS sends every
        stop, and when it went out. `stopped` is a run whose stop this bridge saw run already,
        `unconfirmed` a stop that did not go out, and `sent` one still to be confirmed."""
        assert self.bus is not None
        if self.mission_stopped_run is not None and self.mission_stopped_run == stack.run_id:
            did.append("the mission was already stopped")
            return "stopped", self.clock()
        try:
            self.bus.mission_stop()
            did.append(f"{MISSION_RPC}/stop sent")
        except Exception as e:
            errors.append(f"the mission's stop did not go out: {e}")
            return "unconfirmed", self.clock()
        return "sent", self.clock()

    def _confirm_mission_stop(
        self, stack: StackInfo, sent: tuple[str, float], did: list[str], errors: list[str]
    ) -> str:
        """A sent stop, confirmed by the mission's RPC falling silent, its own sign that its stop
        has run. The silence counts only from a mission this bridge heard answer on this run,
        as it does when quackd connects. The mission is asked at least once, however long the
        arm's cancel took, and for no longer than MISSION_CONFIRM_S after its stop went out."""
        assert self.bus is not None
        state, sent_at = sent
        if state != "sent":
            return state
        run = stack.run_id or "unknown"
        heard = self.mission_heard_run == run
        deadline = sent_at + MISSION_CONFIRM_S
        gap = MISSION_RESEND_FIRST_S
        resend_at = sent_at + gap
        while True:
            if self.bus.mission_status(MISSION_PROBE_S) is not None:
                heard = True
                self.mission_heard_run = run
                # still answering: the stop goes out again, since a stop sent without an answer
                # on a lossy bus may never have arrived, backing off as it goes
                if self.clock() >= resend_at:
                    with contextlib.suppress(Exception):
                        self.bus.mission_stop()
                    gap *= 2
                    resend_at = self.clock() + gap
            elif heard:
                silent = self._second_module_answers(stack)
                if silent is False:
                    errors.append(
                        "the mission fell silent, and so did the arm's coordinator in the same "
                        "breath, so the bus itself may be down and the silence confirms nothing: "
                        "stop the stack on the rover"
                    )
                    return "unconfirmed"
                self.mission_stopped_run = run
                did.append("the mission's RPC fell silent: its stop has run")
                if silent is None:
                    did.append(
                        "no second module answers here to tell a stopped mission from a dead bus"
                    )
                return "stopped"
            else:
                errors.append(
                    "the mission had not answered this bridge on this run, so its silence "
                    "cannot confirm its stop: stop the stack on the rover"
                )
                return "unconfirmed"
            if self.clock() >= deadline:
                break
            self.sleep(MISSION_POLL_S)
        errors.append(
            f"the mission still answers {MISSION_CONFIRM_S:g} s after its stop: stop the stack "
            "on the rover"
        )
        return "unconfirmed"

    def _second_module_answers(self, stack: StackInfo) -> bool | None:
        """Whether another module on the bus answers in the same breath as a silent mission:
        the arm's coordinator. True says the bus carries RPCs and the silence is the mission's
        own, False that the bus may be down, and None that there is no module to ask."""
        assert self.bus is not None
        if stack.arm_run is None:
            return None
        try:
            self.bus.arm_joints()
        except Exception:
            return False
        return True

    def _end_mission_pick(
        self, stack: StackInfo, did: list[str], errors: list[str]
    ) -> dict[str, list[int]] | None:
        """The mission's own pick, which its stop leaves running: the maker's script, found
        among the stack's own descendants, sent SIGTERM with every process under it, a shell's
        child among them, and SIGKILL for any that outlives its grace, so none can send a
        trajectory after the cancel that follows. The processes under a pick are read before
        any is signalled, since a child whose parent has ended is no longer under it. A
        MOSS_PICK_CMD the bridge refuses is said here, at every stop, as at hello. None for no
        pick."""
        refused = pick_command(stack.env)[1]
        if refused is not None:
            errors.append(refused)
        try:
            pids = self.procs.picks(stack)
            family = list(
                dict.fromkeys(p for pid in pids for p in (pid, *self.procs.descendants(pid)))
            )
        except Exception as e:
            errors.append(f"the mission's pick could not be looked for: {e}")
            return None
        if not pids:
            did.append("no pick of the mission's was running")
            return None
        if under := [p for p in family if p not in pids]:
            did.append(f"the processes under the mission's pick: pid {_pids(under)}")
        ended = end_processes(self.procs, family, clock=self.clock, sleep=self.sleep)
        for how in ("terminated", "killed"):
            if ended[how]:
                did.append(f"the mission's pick script {how}: pid {_pids(ended[how])}")
        if ended["left"]:
            errors.append(
                f"the mission's pick script outlived SIGKILL (pid {_pids(ended['left'])}): it "
                "may still send its trajectory, so support the arm and stop the stack"
            )
        return ended

    def _cancel_arm(
        self, did: list[str], errors: list[str], *, picked: bool = False
    ) -> dict[str, Any] | None:
        """The arm's step, the same at every stop, the stop verb's, a verb's own at its end, the
        abort's and a halt's alike: cancel_trajectory, and when it finds nothing executing and
        nothing of quackd's was starting or running, that is all, with no reads and no watch.
        A trajectory cancelled, a cycle of quackd's, or a pick of the mission's just ended has
        the joints read twice, a second cancel if they still moved, and the arm watched for
        arm_guard_s. Skipped only when nothing can be moving the arm: no cycle of quackd's, no
        pick of the mission's just ended, and no moss-arm run for a cancel to reach."""
        assert self.bus is not None and self.up is not None
        ours = self.jobs is not None and self.jobs.running()
        if not ours and not picked and self.stack().arm_run is None:
            # nothing can be moving the arm through a coordinator that is not running, and a
            # cancel sent to it would hold this stop for the RPC's whole timeout
            did.append("no moss-arm run, and no cycle of quackd's: nothing to cancel")
            return None
        if ours and self.jobs is not None:
            result = self.jobs.cancel()
        else:
            result = cancel_and_read(self.bus, self.up, self.sleep, always_read=picked)
        if result.get("error"):
            if ours or picked:
                errors.append(f"the arm: {result['error']}")
            else:
                did.append("the arm's coordinator did not answer, and no cycle of quackd's ran")
            return result
        did.append(f"cancel_trajectory: {result.get('cancel')}")
        if result.get("executing") is False:
            did.append("nothing was executing and nothing of quackd's was in play: no reads")
            return result
        if result.get("held") is False:
            errors.append(
                "the arm did not hold after its cancel, read moving or still executing: "
                "support it or cut its power"
            )
        return self._watch_arm(result, did)

    def _watch_arm(self, result: dict[str, Any], did: list[str]) -> dict[str, Any]:
        """The arm a stop has just cancelled, watched from now for arm_guard_s: a pick the
        stop's looks did not find, or a trajectory sent before the stop, can still land. Until
        the window has passed, a reading that found the arm still is said as read_still, never
        as held. An arm whose joints did not answer the stop's reads is not watched: it is
        said unread, and nothing is claimed of it."""
        if result.get("held") is None:
            return result
        with self._lock:
            until = self.clock() + self.arm_guard_s
            if self._guard is None:
                self._guard = {"until": until, "last": None, "cancels": 0, "moved": False}
            else:
                self._guard["until"] = max(self._guard["until"], until)
            self.guard_doc = None
        did.append(f"the arm is watched {self.arm_guard_s:g} s, and cancelled again if it moves")
        watched = {**result, "watched_s": self.arm_guard_s, "read_still": False}
        if watched.get("held") is True:
            watched["held"], watched["read_still"] = None, True
        return watched

    def guard_tick(self) -> dict[str, Any] | None:
        """One look at an arm a stop cancelled, while its window lasts: the joints read, and a
        cancel more when they moved since the look before. Once the window has passed, the
        arm's word: two reads and a cancel that finds nothing executing, and a cancel and two
        reads more when one did. That word becomes the hold of the cycle the stop cancelled
        and /arm's, and is returned, once. Every other look returns None."""
        with self._lock:
            guard = self._guard
        bus, up = self.bus, self.up
        if guard is None or bus is None or up is None:
            return None
        now = self.clock()
        if now < guard["until"]:
            try:
                joints = dict(bus.arm_joints())
            except Exception:
                guard["last"] = None
                return None
            last, guard["last"] = guard["last"], (now, joints)
            # reads at least a poll apart, whatever a test's clock says between them
            if last is not None and _moved(
                up, last[1], joints, max(now - last[0], ARM_GUARD_POLL_S)
            ):
                guard["moved"] = True
                guard["cancels"] += 1
                log.warning("the arm moved after a stop had cancelled it: cancelling it again")
                with contextlib.suppress(Exception):
                    bus.arm_cancel()
            return None
        out: dict[str, Any] = {"cancel": None, "held": None}
        held = _held_after(bus, up, self.sleep, out)
        if held is False:
            held = cancel_and_read(bus, up, self.sleep).get("held")
        with self._lock:
            if self._guard is not guard or self.clock() < guard["until"]:
                return None  # a stop watched it again meanwhile, and the window goes on
            self._guard = None
            doc = {
                "watching": False,
                "left_s": None,
                "held": held,
                "cancels": guard["cancels"],
                "moved": guard["moved"],
            }
            self.guard_doc = doc
        if self.jobs is not None:
            self.jobs.held_after_guard(held)
        return dict(doc)

    def _guard_view(self) -> dict[str, Any] | None:
        """/arm's word on the window after a stop: watching, with the seconds left, or the last
        window's word once it has passed, or None before any stop cancelled the arm."""
        with self._lock:
            guard, last = self._guard, self.guard_doc
            if guard is not None:
                return {
                    "watching": True,
                    "left_s": round(max(0.0, guard["until"] - self.clock()), 1),
                    "held": None,
                    "cancels": guard["cancels"],
                    "moved": guard["moved"],
                }
        return None if last is None else dict(last)

    def frame(self, cam: str) -> tuple[int, Any, dict[str, str]]:
        arrived = self.wall()
        if cam not in CAMERA_STREAMS:
            return 404, {"ok": False, "reason": f"no camera called {cam!r}: nose or wrist"}, {}
        if self.bus is None:
            return 503, {"ok": False, "reason": f"no bus: {self.bus_error or self.up_error}"}, {}
        period = self.bus.frames.period(cam) or 0.0
        got = self.bus.frames.first_after(cam, arrived, stale_after_s(period))
        if got is None:
            return (
                503,
                {"ok": False, "reason": f"no {cam} frame in {stale_after_s(period):g} s"},
                {},
            )
        stamp, jpeg, after = got
        return (
            200,
            jpeg,
            {FRAME_STAMP_HEADER: f"{stamp:.6f}", FRAME_AFTER_HEADER: "1" if after else "0"},
        )

    def arm_doc(self) -> dict[str, Any]:
        ok, why = self._arm_answering()
        doc: dict[str, Any] = {
            "ok": True,
            "answering": ok,
            "error": why,
            "job": None if self.jobs is None else self.jobs.doc(),
            "guard": self._guard_view(),
            "joints": None,
            "gap_m": None,
            "folded": None,
        }
        if ok and self.bus is not None and self.up is not None:
            with contextlib.suppress(Exception):
                joints = self.bus.arm_joints()
                doc["joints"] = {k: round(v, 4) for k, v in joints.items()}
                doc["gap_m"] = joints.get(self.up.gripper_joint)
                off = max(
                    abs(joints[j] - target)
                    for j, target in zip(self.up.arm_joints, self.up.drive_pose, strict=True)
                )
                doc["folded"] = off <= self.up.start_tolerance_rad
        return doc

    def arm_cycle(self, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        stops = self._stops
        stack = self.stack()
        self._settle(stack)
        if self.jobs is None:
            return 503, {
                "ok": False,
                "reason": f"no arm job runner: {self.arm_error or self.bus_error}",
            }
        if self.run_error:
            return 503, {"ok": False, "reason": self.run_error}
        if self._litter(stack):
            return 409, {"ok": False, "reason": "on moss-litter the mission owns the arm"}
        if (guard := self._guard_view()) is not None and guard["watching"]:
            return 409, {
                "ok": False,
                "reason": f"the arm is watched {guard['left_s']:g} s more after a stop, for a "
                "trajectory sent before it that may still land: start a cycle once that has "
                "passed",
            }
        if self.arm_error:
            return 503, {"ok": False, "reason": self.arm_error}
        pick_pose = str(body.get("pick_pose") or "pick")
        phase = body.get("from_phase")
        session = body.get("session")
        # the check that no twist runs and the start, under one lock: a twist asked during the
        # coordinator's round trip waits for it, and then sees the cycle
        with self._motion:
            if self.pump is not None and self.pump.fresh():
                return 409, {"ok": False, "reason": "a twist is running: stop first"}
            ok, why = self._arm_answering()
            if not ok:
                return 503, {"ok": False, "reason": why}
            env = stack.arm_environ()
            if env is None:
                pid = stack.arm_run.pid if stack.arm_run is not None else stack.pid
                return 503, {
                    "ok": False,
                    "reason": f"the environment of the run that holds the arm (pid {pid}) is "
                    "unreadable, and the maker's script runs in it, its calibration among it: "
                    "run the bridge as the user that runs MOSS",
                }
            if self._stops != stops:
                return 409, {"ok": False, "reason": "a stop came in while this cycle was asked"}
            started, said = self.jobs.start(
                pick_pose,
                None if phase is None else str(phase),
                session if isinstance(session, str) and session else None,
                env,
            )
        if not started:
            return 409, {"ok": False, "reason": said}
        return 200, {"ok": True, "id": said}

    def arm_cancel(self, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        """The cancel for one cycle of quackd's, the one `id` names, while it still runs: what
        quackd's close sends, which halts what its own session started and nothing else. A
        cycle that has ended or that another id names is left alone, and so is a trajectory
        this bridge did not start, which `/stop` would cancel."""
        wanted = body.get("id")
        if not isinstance(wanted, str) or not wanted:
            return 400, {"ok": False, "reason": "an arm cancel names the cycle it cancels: {id}"}
        if self.jobs is None or self.jobs.running_id() != wanted:
            return 200, {"ok": True, "cancelled": False, "arm": None, "errors": []}
        errors: list[str] = []
        result = self.jobs.cancel()
        if result.get("error"):
            errors.append(f"the arm: {result['error']}")
        else:
            if result.get("held") is False:
                errors.append(
                    "the arm did not hold after its cancel, read moving or still executing: "
                    "support it or cut its power"
                )
            result = self._watch_arm(result, [])
        return 200, {"ok": not errors, "cancelled": True, "arm": result, "errors": errors}

    def nav_doc(self) -> dict[str, Any]:
        """The base stack's own navigation, which the cockpit cannot show while a moss-arm run
        started after the base is the newest run it reads."""
        stack = self.stack()
        if self.bus is None:
            return {"ok": False, "reason": f"no bus: {self.bus_error or self.up_error}"}
        return {
            "ok": True,
            "blueprint": stack.blueprint,
            **nav_state(self.bus.nav_seen(), stack.blueprint),
        }

    def mission_doc(self) -> dict[str, Any]:
        stack = self.stack()
        doc = self._mission_doc(stack) or {
            "answering": False,
            "stopped": False,
            "status": None,
            "error": f"no mission on {stack.blueprint or 'no stack'}",
        }
        return {"ok": True, **doc}

    def keepalive(self, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        """One keepalive of one session, named by the id its client drew. Each session keeps
        its own period, capped at MAX_KEEPALIVE_S, its own word of what it started and its own
        end, so one client's keepalive or end never touches another's."""
        sid = body.get("session")
        if not isinstance(sid, str) or not sid or len(sid) > SESSION_ID_MAX:
            return 400, {
                "ok": False,
                "reason": "a keepalive names its session: {session, keepalive_s, started, end}",
            }
        try:
            period = float(body.get("keepalive_s") or 0.5)
        except (TypeError, ValueError):
            period = 0.5
        if not math.isfinite(period):
            period = MAX_KEEPALIVE_S
        started = body.get("started")
        mine: dict[str, Any] | None = None
        stack = self.stack()
        with self._lock:
            if body.get("end"):
                self.sessions.pop(sid, None)
                self.halted.pop(sid, None)
            else:
                s = self.sessions.setdefault(sid, Session(sid))
                s.keepalive_s = min(MAX_KEEPALIVE_S, max(0.05, period))
                s.started = str(started) if started in STARTED else None
                s.last = self.clock()
                given = body.get("run")
                s.run = str(given) if isinstance(given, str) and given else None
                mine = {
                    "id": s.id,
                    "keepalive_s": s.keepalive_s,
                    "started": s.started,
                    "halted": self.halted.get(sid),
                    "run_alive": s.run is None or run_alive(stack, s.run) is not None,
                }
        return 200, {
            "ok": True,
            "stack": stack.doc(),
            "session": mine,
            "sessions": self._sessions_doc(),
        }

    def _sessions_doc(self) -> dict[str, Any]:
        return {
            "live": len(self.sessions),
            "expired": self.expired,
            "halts": self.halts,
            "last_halt": self.last_halt,
        }

    # ── the watchdogs ───────────────────────────────────────────────────────────────

    def _expired_now(self) -> list[tuple[str, Session]]:
        """The sessions three of their own periods past their last keepalive, ended here, each
        with why."""
        now = self.clock()
        with self._lock:
            late = [
                s for s in self.sessions.values() if now - s.last > SESSION_MISSES * s.keepalive_s
            ]
            for s in late:
                del self.sessions[s.id]
                self.expired += 1
        return [(f"session {s.id} missed {SESSION_MISSES} keepalives", s) for s in late]

    def session_tick(self) -> list[dict[str, Any]]:
        """Each session three of its own periods past its last keepalive is ended, and what it
        said it started is halted, and nothing any other session started, one after the other
        as a test drives it."""
        return [self.halt(why, s) for why, s in self._expired_now()]

    def _session_watch(self) -> None:
        """The bridge's own watch: each expired session halted in a thread of its own, so a
        long halt, a litter mission's whole stop, never holds back another session's expiry."""
        for why, s in self._expired_now():
            threading.Thread(target=self.halt, args=(why, s), name="moss-halt", daemon=True).start()

    def halt(self, why: str, session: Session) -> dict[str, Any]:
        """What one session started, halted, and nothing it did not. On moss-litter the whole
        stop, the mission's own included, only for a session that started the mission: one
        that only watched the owner's mission leaves it running. Elsewhere, the cycle that
        session started is cancelled, and a goal or an exploration it started is ended with
        stop_explore_cmd, stop_movement and a zero.

        Its record is written as it starts, with what it acts on and done false, and finished
        when it ends: a halt on moss-litter is the whole stop, which can take a minute, and a
        session back meanwhile reads it at its first keepalive."""
        stack = self.stack()
        log.warning("halting what session %s started: %s", session.id, why)
        halted: list[str] = []
        if session.run is not None and stack.run_id not in (None, session.run):
            own = run_alive(stack, session.run)
            stack = own if own is not None else stack
        moved_on = session.run is not None and stack.run_id not in (None, session.run)
        cycling = self.jobs is not None and self.jobs.running_session() == session.id
        if self._litter(stack) and session.started == "litter" and not moved_on:
            acting = ["the litter mission it started"]
        else:
            acting = ["its arm cycle"] if cycling else []
            if session.started in ("goal", "explore") and not moved_on:
                acting.append("its goal" if session.started == "goal" else "its exploration")
        with self._lock:
            self.halted[session.id] = {
                "why": why,
                "started": session.started,
                "halted": acting,
                "ok": None,
                "done": False,
            }
        if moved_on:
            # the stack the session was kept on has gone: nothing of the new run is its
            did: list[str] = []
            errors: list[str] = []
            doc = {"ok": True, "did": did, "errors": errors, "arm": None}
            if self.jobs is not None and self.jobs.running_session() == session.id:
                doc["arm"] = self._cancel_arm(did, errors)
                halted.append("its arm cycle")
            did.append(f"run {session.run} is gone, and nothing of run {stack.run_id} is halted")
            doc["ok"] = not errors
        elif self._litter(stack) and session.started == "litter":
            doc = self.stop(session.run)
            halted.append("the litter mission it started")
        else:
            did = []
            errors = []
            doc = {"ok": True, "did": did, "errors": errors, "arm": None}
            if self.bus is not None and self.up is not None:
                if self.jobs is not None and self.jobs.running_session() == session.id:
                    doc["arm"] = self._cancel_arm(did, errors)
                    halted.append("its arm cycle")
                if session.started in ("goal", "explore"):
                    halted.append("its goal" if session.started == "goal" else "its exploration")
                    for stream in (STOP_EXPLORE_STREAM, STOP_MOVEMENT_STREAM):
                        with contextlib.suppress(Exception):
                            self.bus.publish_true(channel(stream, BOOL_MSG))
                            did.append(f"true on {stream}")
                    for chan in self._stop_twist_channels(stack):
                        with contextlib.suppress(Exception):
                            self.bus.publish_twist(chan, 0.0, 0.0)
                            did.append(f"zero twist on {chan}")
            doc["ok"] = not errors
        doc["halted"] = halted
        record = {
            "why": why,
            "started": session.started,
            "halted": halted,
            "ok": doc["ok"],
            "done": True,
        }
        with self._lock:
            self.last_halt = why
            if halted:
                self.halts += 1
            self.halted[session.id] = record
            while len(self.halted) > HALTS_KEPT:
                self.halted.pop(next(iter(self.halted)))
        return doc

    def start(self) -> None:
        pump = threading.Thread(target=self._pump_loop, name="moss-pump", daemon=True)
        pump.start()
        self._threads.append(pump)
        self._threads.append(self._every(0.1, self._session_watch, "moss-session"))
        self._threads.append(self._every(ARM_GUARD_POLL_S, self.guard_tick, "moss-arm-guard"))

    def _pump_loop(self) -> None:
        """The one scheduler of every twist this bridge sends. It ticks the pump when its next
        edge is due rather than on a beat of its own, so a pulse is on for exactly pulse_s
        whatever the resend rate, and it ticks a pump built after the bridge started, for a
        stack that started later, as much as one built with it."""
        while not self._stop.is_set():
            pump = self.pump
            if pump is None:
                self._stop.wait(PUMP_IDLE_S)
                continue
            try:
                due = pump.tick()
            except Exception as e:  # a pump that died would leave a twist unrepeated
                log.warning("the twist pump failed: %s", e)
                due = None
            wait = PUMP_IDLE_S if due is None else max(0.0, due - pump.clock())
            pump.wake.wait(min(wait, PUMP_IDLE_S))
            pump.wake.clear()

    def _every(self, period: float, fn: Callable[[], Any], name: str) -> threading.Thread:
        def run() -> None:
            while not self._stop.wait(period):
                try:
                    fn()
                except Exception as e:  # a watchdog that died would be worse than a noisy one
                    log.warning("%s failed: %s", name, e)

        thread = threading.Thread(target=run, name=name, daemon=True)
        thread.start()
        return thread

    def close(self) -> None:
        """Halt what every live session started, cancel a cycle of quackd's still running
        whichever session started it, then let go of the bus. Never a go-limp call."""
        with contextlib.suppress(Exception):
            with self._lock:
                live = list(self.sessions.values())
                self.sessions.clear()
            for session in live:
                self.halt("the bridge is shutting down", session)
            if self.jobs is not None and self.jobs.running() and self.bus is not None:
                self._cancel_arm([], [])
        self._stop.set()
        for thread in self._threads:
            thread.join(timeout=1.0)
        if self.bus is not None:
            with contextlib.suppress(Exception):
                self.bus.close()

    def authorised(self, given: str | None) -> bool:
        if self.token is None:
            return True
        if given is None:
            return False
        return hmac.compare_digest(given.encode("utf-8"), self.token.encode("utf-8"))


# ── building one ────────────────────────────────────────────────────────────────────────


def arm_play_argv(
    moss_dir: str, *, sim: Callable[[], bool]
) -> Callable[[str, str | None], list[str]]:
    """The command MOSS's own mission runs for its pick (moss_dimos/mission.py, _start_pick)."""
    script = str(Path(moss_dir) / "tools" / "moss_arm_play.py")

    def argv(pick_pose: str, from_phase: str | None) -> list[str]:
        out = [sys.executable, script, "--pick-pose", pick_pose]
        if from_phase:
            out += ["--from", from_phase]
        if sim():
            out.append("--sim")
        return out

    return argv


def find_moss_dir(given: str | None) -> str | None:
    if given:
        return given
    try:
        import moss_dimos
    except Exception:
        return None
    return str(Path(str(moss_dimos.__file__)).resolve().parent.parent)


def bus_refusal(stack: StackInfo, own_url: str) -> str | None:
    """Why this bridge cannot publish to a stack: another bus, or LCM at another address."""
    if stack.transport != "lcm":
        return f"the stack runs on {stack.transport}, and this bridge speaks LCM only"
    if (stack.env.get("LCM_DEFAULT_URL") or "") != own_url:
        return (
            "the stack's LCM_DEFAULT_URL is not this bridge's: restart the bridge with the "
            "stack's environment"
        )
    return None


def live_bridge(*, moss: str | None, token: str | None, runs: str | None = None) -> Bridge:
    """The rover's own bridge. Whatever does not load is a reason in /hello, never a crash."""
    stack = read_stack(runs)
    env = dict(stack.env)
    up: Upstream | None = None
    bus: LcmBus | None = None
    up_error = bus_error = arm_error = None
    moss_dir = find_moss_dir(moss)
    if moss_dir is not None and moss_dir not in sys.path:
        sys.path.append(moss_dir)
    try:
        up = read_upstream(env, stack.arm_environ())
    except UpstreamMissing as e:
        up_error = str(e)
    own_url = os.environ.get("LCM_DEFAULT_URL") or ""

    def reread(running: StackInfo) -> tuple[Upstream | None, str | None]:
        refusal = bus_refusal(running, own_url)
        if refusal is not None:
            return None, refusal
        try:
            return read_upstream(dict(running.env), running.arm_environ()), None
        except UpstreamMissing as e:
            return None, str(e)

    if up is not None:
        refusal = bus_refusal(stack, own_url) if stack.running else None
        if refusal is not None:
            bus_error = refusal
        else:
            try:
                bus = LcmBus(up, FrameStore(), arm=True, mission=True)
            except Exception as e:
                bus_error = f"the LCM bus did not open: {type(e).__name__}: {e}"
    argv = None
    if moss_dir is None:
        arm_error = "no MOSS checkout: pass --moss PATH"
    elif not (Path(moss_dir) / "tools" / "moss_arm_play.py").is_file():
        arm_error = f"{moss_dir}/tools/moss_arm_play.py is not there: pass --moss PATH"
    else:

        def sim() -> bool:
            run = read_stack(runs).arm_run
            return run is not None and "--simulation" in " ".join(run.cmdline)

        argv = arm_play_argv(moss_dir, sim=sim)
    return Bridge(
        stack=lambda: read_stack(runs),
        up=up,
        bus=bus,
        bus_error=bus_error,
        up_error=up_error,
        arm_argv=argv,
        moss_dir=moss_dir,
        arm_error=arm_error,
        token=token,
        reread=reread if up is not None else None,
        make_bus=lambda u: LcmBus(u, FrameStore(), arm=True, mission=True),
    )


FAKE_ARM_SCRIPT = (
    "import sys, time\n"
    "args = sys.argv[1:]\n"
    "cycle = float(args[args.index('--cycle-s') + 1]) if '--cycle-s' in args else 1.0\n"
    "print('cycle: fake', flush=True)\n"
    "if '--refuse' in args:\n"
    "    print('refused: the fake was told to refuse', flush=True)\n"
    "    raise SystemExit(2)\n"
    "print('running: fake trajectory', flush=True)\n"
    "if '--lag' in args:\n"
    "    print('ARM LAG: arm/a 200 ticks behind', flush=True)\n"
    "    raise SystemExit(4)\n"
    "time.sleep(cycle)\n"
    "print('done: fake', flush=True)\n"
)
"""What `--fake` runs in place of tools/moss_arm_play.py: the same lines and exit codes."""


def fake_bridge(
    *,
    blueprint: str = "moss-explore",
    token: str | None = None,
    up: Upstream = FAKE_UPSTREAM,
    stamps: str = "rover",
    cycle_s: float = 1.0,
    arm: bool = True,
    mission: bool = True,
    fps: float = 5.0,
    script_flags: tuple[str, ...] = (),
    arm_guard_s: float = ARM_GUARD_S,
    **bus_kw: Any,
) -> Bridge:
    """`--fake`, whole: a fake bus, a fake arm coordinator, a fake mission, and in place of the
    maker's cycle a script with its lines and exit codes, `--lag` or `--refuse` among
    `script_flags` making it end the way the maker's does on a lag or a refusal."""
    frames = FrameStore()
    bus = FakeBus(up, frames, stamps=stamps, fps=fps, **bus_kw)
    bus.arm_answers = arm
    bus.mission_answers = mission
    run = Run(pid=os.getpid(), blueprint=ARM_STACK, run_id="fake-arm", started=0.0) if arm else None
    stack = StackInfo(
        running=True,
        blueprint=blueprint,
        pid=os.getpid(),
        run_id="fake-run",
        env={"DIMOS_TRANSPORT": "lcm"},
        arm_run=run,
        # the fake arm run is this process, so its script runs in this process's environment
        arm_env=dict(os.environ) if arm else None,
    )

    def argv(pick_pose: str, from_phase: str | None) -> list[str]:
        out = [
            sys.executable,
            "-c",
            FAKE_ARM_SCRIPT,
            "--pick-pose",
            pick_pose,
            "--cycle-s",
            str(cycle_s),
            *script_flags,
        ]
        return out + (["--from", from_phase] if from_phase else [])

    return Bridge(
        stack=lambda: stack,
        up=up,
        bus=bus,
        arm_argv=argv,
        token=token,
        fake=True,
        procs=bus,
        arm_guard_s=arm_guard_s,
    )


# ── the server ──────────────────────────────────────────────────────────────────────────


def make_handler(bridge: Bridge) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = f"quackd-moss-bridge/{BRIDGE_VERSION}"
        protocol_version = "HTTP/1.1"
        timeout = REQUEST_TIMEOUT_S

        def do_GET(self) -> None:
            self._route("GET")

        def do_POST(self) -> None:
            self._route("POST")

        def send_error(
            self, code: int, message: str | None = None, explain: str | None = None
        ) -> None:
            self._json(
                code,
                {"ok": False, "reason": message or "the request could not be read"},
                close=True,
            )

        def _route(self, method: str) -> None:
            path, _, query = self.path.partition("?")
            if not bridge.authorised(self.headers.get(TOKEN_HEADER)):
                # its body read and dropped first, as one refused for its length is: a close
                # with a body unread, or still to come, resets the connection, and the
                # refusal is lost to a client that reads it a moment later
                self._drop_body()
                self._json(401, {"ok": False, "reason": "bad or missing token"}, close=True)
                return
            if path in GET_PATHS and method == "GET":
                self._get(path, query)
            elif path in POST_PATHS and method == "POST":
                body = self._body()
                if body is not None:
                    self._post(path, body)
            elif path in GET_PATHS | POST_PATHS:
                allowed = "GET" if path in GET_PATHS else "POST"
                self._json(405, {"ok": False, "reason": f"{path} answers {allowed}, not {method}"})
            else:
                self._json(404, {"ok": False, "reason": f"nothing at {path}"})

        def _drop_body(self) -> None:
            """A declared body of at most DRAIN_LIMIT_BYTES, read and dropped."""
            try:
                length = int(self.headers.get("Content-Length") or "0")
            except ValueError:
                return
            if 0 < length <= DRAIN_LIMIT_BYTES:
                with contextlib.suppress(OSError):
                    self.rfile.read(length)

        def _body(self) -> dict[str, Any] | None:
            declared = self.headers.get("Content-Length")
            try:
                length = int(declared or "0")
            except ValueError:
                length = -1
            if length < 0 or length > MAX_BODY_BYTES:
                # a body a little too long is read and dropped, so the refusal is not lost to a
                # reset, and one far too long, or of no length at all, closes the connection
                drained = 0 < length <= DRAIN_LIMIT_BYTES
                if drained:
                    with contextlib.suppress(OSError):
                        self.rfile.read(length)
                self._json(
                    413,
                    {"ok": False, "reason": f"a body of at most {MAX_BODY_BYTES} bytes"},
                    close=not drained,
                )
                return None
            raw = self.rfile.read(length) if length else b""
            try:
                doc = json.loads(raw.decode("utf-8")) if raw.strip() else {}
            except (UnicodeDecodeError, ValueError):
                self._json(400, {"ok": False, "reason": "the body is not JSON"})
                return None
            if not isinstance(doc, dict):
                self._json(400, {"ok": False, "reason": "the body must be a JSON object"})
                return None
            return doc

        def _get(self, path: str, query: str) -> None:
            if path == HELLO_PATH:
                self._json(200, bridge.hello())
            elif path == ARM_PATH:
                self._json(200, bridge.arm_doc())
            elif path == MISSION_PATH:
                self._json(200, bridge.mission_doc())
            elif path == NAV_PATH:
                self._json(200, bridge.nav_doc())
            else:
                cam = parse_qs(query).get("cam", [NOSE])[0]
                code, payload, headers = bridge.frame(cam)
                if code == 200 and isinstance(payload, bytes):
                    self._bytes(200, "image/jpeg", payload, headers)
                else:
                    self._json(code, payload)

        def _post(self, path: str, body: dict[str, Any]) -> None:
            if path == TWIST_PATH:
                code, doc = bridge.twist(body)
                self._json(code, doc)
            elif path == STOP_PATH:
                run, sid = body.get("run"), body.get("session")
                self._json(
                    200,
                    bridge.stop(
                        run if isinstance(run, str) and run else None,
                        sid if isinstance(sid, str) and sid else None,
                    ),
                )
            elif path == ARM_CYCLE_PATH:
                code, doc = bridge.arm_cycle(body)
                self._json(code, doc)
            elif path == ARM_CANCEL_PATH:
                code, doc = bridge.arm_cancel(body)
                self._json(code, doc)
            else:
                code, doc = bridge.keepalive(body)
                self._json(code, doc)

        def _bytes(
            self,
            code: int,
            kind: str,
            payload: bytes,
            headers: dict[str, str] | None = None,
            close: bool = False,
        ) -> None:
            if close:
                self.close_connection = True
            try:
                self.send_response(code)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("Cache-Control", "no-store")
                for name, value in (headers or {}).items():
                    self.send_header(name, value)
                if self.close_connection:
                    self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(payload)
            except OSError:
                self.close_connection = True

        def _json(self, code: int, body: dict[str, Any], close: bool = False) -> None:
            self._bytes(code, "application/json", json.dumps(body).encode("utf-8"), close=close)

        def log_message(self, fmt: str, *args: Any) -> None:
            log.debug("%s %s", self.address_string(), fmt % args)

    return Handler


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = sys.platform != "win32"

    def __init__(self, address: Any, handler: Any) -> None:
        self._connections = threading.BoundedSemaphore(MAX_CONNECTIONS)
        super().__init__(address, handler)

    def verify_request(self, request: Any, client_address: Any) -> bool:
        if self._connections.acquire(blocking=False):
            return True
        with contextlib.suppress(OSError):
            request.sendall(
                b"HTTP/1.1 503 Service Unavailable\r\n"
                b"Content-Length: 0\r\nConnection: close\r\n\r\n"
            )
        return False

    def process_request_thread(self, request: Any, client_address: Any) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._connections.release()


def serve(bridge: Bridge, host: str, port: int) -> ThreadingHTTPServer:
    server = _Server((host, port), make_handler(bridge))
    threading.Thread(target=server.serve_forever, name="quackd-moss-bridge", daemon=True).start()
    return server


# ── running it ──────────────────────────────────────────────────────────────────────────


class TokenFileError(RuntimeError):
    """A token file that was named and cannot be read, or holds no token."""


def read_token(path: str | None) -> str | None:
    """A named token file that cannot be read, or is empty, refuses to start: a bridge that
    started with authentication silently off is the one nobody can tell from a safe one."""
    if not path:
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            token = fh.read().strip()
    except (OSError, UnicodeDecodeError) as e:
        raise TokenFileError(
            f"cannot read the token file {path}: {e}. Refusing to start without it."
        ) from e
    if not token:
        raise TokenFileError(f"the token file {path} is empty. Refusing to start without a token.")
    return token


def resolve_token(args: argparse.Namespace) -> str | None:
    if args.token_file:
        return read_token(args.token_file)
    if args.token is not None:
        return str(args.token).strip() or None
    return os.environ.get(TOKEN_ENV, "").strip() or None


def wide_bind_warning(bind: str, token: str | None) -> str | None:
    if bind in LOOPBACK or token:
        return None
    return (
        f"binding {bind} with no token: anyone who can reach this port can drive the rover's "
        "tracks and its arm. Prefer --bind 127.0.0.1 and an ssh tunnel, or give it a --token-file."
    )


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="quackd-moss-bridge", description=(__doc__ or "").splitlines()[0]
    )
    p.add_argument(
        "--bind", default="127.0.0.1", help="loopback by default: reach it through an ssh tunnel"
    )
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    tokens = p.add_mutually_exclusive_group()
    tokens.add_argument(
        "--token", default=None, help=f"the token clients must send (default: ${TOKEN_ENV})"
    )
    tokens.add_argument(
        "--token-file",
        default=None,
        help="read the token from this file, and refuse to start if it is unreadable or empty",
    )
    p.add_argument(
        "--moss",
        default=None,
        help="the MOSS checkout (default: where moss_dimos is installed from)",
    )
    p.add_argument(
        "--fake", action="store_true", help="no rover: a fake bus, a fake arm and a fake mission"
    )
    p.add_argument("--fake-stack", default="moss-explore", help=argparse.SUPPRESS)
    p.add_argument(
        "--once", action="store_true", help="print what /hello would say, as JSON, and exit"
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    logging.basicConfig(
        stream=sys.stderr, level=logging.INFO, format="quackd-moss-bridge %(levelname)s %(message)s"
    )
    try:
        token = resolve_token(args)
    except TokenFileError as e:
        log.error("%s", e)
        return 2
    bridge = (
        fake_bridge(blueprint=args.fake_stack, token=token)
        if args.fake
        else live_bridge(moss=args.moss, token=token)
    )
    if args.once:
        try:
            sys.stdout.write(json.dumps(bridge.hello(), indent=2) + "\n")
            sys.stdout.flush()
        finally:
            bridge.close()
        return 0
    warning = wide_bind_warning(args.bind, token)
    if warning:
        log.warning("%s", warning)
    try:
        server = serve(bridge, args.bind, args.port)
    except OSError as e:
        log.error("cannot listen on %s:%s: %s", args.bind, args.port, e)
        bridge.close()
        return 2
    bridge.start()
    stop = threading.Event()
    previous: Any = None
    with contextlib.suppress(ValueError):  # not the main thread
        previous = signal.signal(signal.SIGTERM, lambda _s, _f: stop.set())
    log.info(
        "serving http://%s:%s (%s, stack: %s, token: %s)",
        args.bind,
        server.server_address[1],
        "fake" if args.fake else "live",
        bridge.stack().blueprint,
        "required" if token else "none",
    )
    try:
        while not stop.is_set():
            stop.wait(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        server.server_close()
        bridge.close()
        if previous is not None:
            with contextlib.suppress(ValueError):
                signal.signal(signal.SIGTERM, previous)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
