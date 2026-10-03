"""A behaviour twin of upstream's `alohamini_host.py`, on loopback sockets, with no robot.

Written from the host's source at the pin (`quackd/adapters/alohamini/upstream_api.py`). It
reproduces the semantics that decide whether a stop is real, bugs included, because the bugs
are the point:

- `send_action` indexes `x.vel`, `y.vel` and `theta.vel` with no default, so a payload missing
  one raises **before** the lift is touched and before any bus write: the whole action is
  discarded and `last_cmd_time` is never refreshed.
- `LiftAxis.apply_action` is two independent `if key in action` blocks with no else. Neither
  key means no write at all, so the velocity register latches and the lift keeps travelling.
  Both keys means the velocity branch runs last and wins.
- `home()` leaves full-speed descent in that register, because upstream's zeroing write is
  commented out.
- the watchdog calls `stop_motion()`, which is the base and the lift and never the arms.

What it cannot prove: serial timing, calibration, real camera behaviour, or whether the arms
physically move. It proves quackd's client is correct against our reading of the host, which
is what `docs/adapters/status.md` claims and no more.
"""

from __future__ import annotations

import contextlib
import json
import threading
import time
from io import BytesIO
from typing import Any

import zmq
from PIL import Image

ARM_JOINTS_6DOF = (
    "shoulder_pan",
    "shoulder_lift",
    "elbow_flex",
    "wrist_flex",
    "wrist_yaw",
    "wrist_roll",
    "gripper",
)
ARM_JOINTS_5DOF = tuple(j for j in ARM_JOINTS_6DOF if j != "wrist_yaw")
VEL_KEYS = ("x.vel", "y.vel", "theta.vel")
LIFT_HEIGHT_KEY = "lift_axis.height_mm"
LIFT_VEL_KEY = "lift_axis.vel"
WATCHDOG_S = 1.0
LOOP_HZ = 30.0
HOME_DESCENT_VELOCITY = -1300
KP_VEL = 300.0
V_MAX = 1300.0
ON_TARGET_MM = 1.0


def _tiny_jpeg(colour: tuple[int, int, int] = (200, 90, 30), size: int = 32) -> bytes:
    """One JPEG at quality 70, which is what the host encodes at."""
    buf = BytesIO()
    Image.new("RGB", (size, size), colour).save(buf, format="JPEG", quality=70)
    return buf.getvalue()


def state_keys(model: str) -> tuple[str, ...]:
    joints = ARM_JOINTS_5DOF if model == "alohamini1" else ARM_JOINTS_6DOF
    return (
        *(f"arm_{side}_{j}.pos" for side in ("left", "right") for j in joints),
        *VEL_KEYS,
        LIFT_HEIGHT_KEY,
    )


class FakeAlohaMiniHost:
    def __init__(
        self,
        *,
        model: str = "alohamini2",
        cameras: tuple[str, ...] = ("forward", "wrist_right"),
        arm_torque: bool = True,
        calibrated: bool = True,
        watchdog_s: float = WATCHDOG_S,
    ) -> None:
        self.ctx = zmq.Context()
        # LINGER 0 at creation so a fake left unclosed by a failing test cannot hold the
        # interpreter's exit in term(), which is forever with the default linger
        self.cmd = self.ctx.socket(zmq.PULL)
        self.cmd.setsockopt(zmq.LINGER, 0)
        self.cmd.setsockopt(zmq.CONFLATE, 1)
        self.cmd_port = self.cmd.bind_to_random_port("tcp://127.0.0.1")
        self.obs = self.ctx.socket(zmq.ROUTER)
        self.obs.setsockopt(zmq.LINGER, 0)
        self.obs.setsockopt(zmq.SNDHWM, 3)
        self.obs.setsockopt(zmq.RCVHWM, 3)
        self.obs_port = self.obs.bind_to_random_port("tcp://127.0.0.1")

        self.model = model
        self.cameras = cameras
        self.arm_torque = arm_torque
        self.calibrated = calibrated
        self.watchdog_s = watchdog_s
        self.frame = _tiny_jpeg()
        self.state: dict[str, float] = dict.fromkeys(state_keys(model), 0.0)
        self.state[LIFT_HEIGHT_KEY] = 120.0
        # home() ran and left full-speed descent in the register with no zero after it
        self.lift_goal_velocity: int = HOME_DESCENT_VELOCITY if calibrated else 0
        self.actions: list[dict[str, Any]] = []
        self.rejected: list[dict[str, Any]] = []
        """Payloads that raised, exactly as the host's `except Exception` swallows them."""
        self.watchdog_trips = 0
        self._last_cmd = time.monotonic()
        self._running = False
        self._thread: threading.Thread | None = None

    @property
    def address(self) -> str:
        return f"tcp://127.0.0.1:{self.cmd_port}?obs={self.obs_port}"

    # ── the host's own logic ────────────────────────────────────────────────────────

    def apply(self, action: dict[str, Any]) -> None:
        """`AlohaMini.send_action`, faithfully, including the order it does things in."""
        # base_goal_vel["x.vel"] and friends: no .get, no default. This raises before the
        # lift is touched and before any bus write, so the whole action is lost.
        vel = {k: float(action[k]) for k in VEL_KEYS}

        # self.lift.apply_action(action) comes next, and its two ifs are independent
        if LIFT_HEIGHT_KEY in action:
            err = float(action[LIFT_HEIGHT_KEY]) - self.state[LIFT_HEIGHT_KEY]
            v = 0.0 if abs(err) <= ON_TARGET_MM else max(-V_MAX, min(V_MAX, KP_VEL * err))
            self.lift_goal_velocity = int(v)
        if LIFT_VEL_KEY in action:
            # runs last, so a payload carrying both keys ends here and the lift freezes
            self.lift_goal_velocity = int(max(-V_MAX, min(V_MAX, int(action[LIFT_VEL_KEY]))))

        for key, value in action.items():
            if key.endswith(".pos") and key in self.state and self.arm_torque:
                self.state[key] = float(value)
        for key in VEL_KEYS:
            self.state[key] = vel[key]
        self.actions.append(dict(action))

    def observation(self) -> dict[str, Any]:
        obs: dict[str, Any] = dict(self.state)
        obs["_image_encoding"] = "jpeg"
        obs["_images"] = list(self.cameras)
        # quackd's own host wrapper adds these; a stock host has neither
        obs["quackd_arm_torque"] = self.arm_torque
        obs["quackd_calibrated"] = self.calibrated
        return obs

    def _multipart(self) -> list[bytes]:
        parts = [json.dumps(self.observation()).encode("utf-8")]
        for cam in self.cameras:
            parts.extend([cam.encode("utf-8"), self.frame])
        return parts

    def pump(self) -> None:
        """One host cycle: command, watchdog, lift integration, then at most one reply."""
        try:
            action = dict(json.loads(self.cmd.recv_string(zmq.NOBLOCK)))
            try:
                self.apply(action)
                self._last_cmd = time.monotonic()
            except Exception:
                # the host logs and carries on, and crucially does NOT refresh last_cmd_time
                self.rejected.append(action)
        except zmq.Again:
            pass

        if time.monotonic() - self._last_cmd > self.watchdog_s:
            # robot.stop_motion(): the base and the lift, and never the arms
            if any(self.state[k] for k in VEL_KEYS) or self.lift_goal_velocity:
                self.watchdog_trips += 1
            for key in VEL_KEYS:
                self.state[key] = 0.0
            self.lift_goal_velocity = 0

        self.state[LIFT_HEIGHT_KEY] += self.lift_goal_velocity * (60.0 / V_MAX)

        with contextlib.suppress(zmq.Again):
            request = self.obs.recv_multipart(flags=zmq.NOBLOCK)
            identity, token = request[0], request[-1]
            self.obs.send_multipart([identity, token, *self._multipart()], flags=zmq.NOBLOCK)

    # ── running it like the robot would ─────────────────────────────────────────────

    def start(self) -> None:
        self._running = True

        def loop() -> None:
            while self._running:
                self.pump()
                time.sleep(1.0 / LOOP_HZ)

        self._thread = threading.Thread(target=loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    def close(self) -> None:
        self.stop()
        self.cmd.close(linger=0)
        self.obs.close(linger=0)
        self.ctx.term()

    def __enter__(self) -> FakeAlohaMiniHost:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
