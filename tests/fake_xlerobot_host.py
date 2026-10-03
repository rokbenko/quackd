"""A behaviour twin of upstream's `xlerobot_host.py`, on loopback sockets, with no robot.

Upstream ships no test, no CI and no simulator that runs: its ManiSkill host imports
`xlerobot_single`, which is defined nowhere in the repository. So the only way to exercise
quackd's client against the real wire is to write the other end of it, from the host's source
at the pin (`quackd/adapters/xlerobot/upstream_api.py`).

This reproduces the host's semantics, including the ones that are easy to get wrong:

- both sockets are `CONFLATE`, so only the newest message survives;
- `send_action` filters by prefix and suffix and writes the wheels on *every* action, so an
  action with no velocity keys commands zero base velocity;
- the watchdog zeroes the base after 500 ms of silence and touches nothing else;
- cameras are base64 JPEG strings inside the same JSON object as the seventeen float keys.

What it cannot prove: anything about serial timing, calibration, or a real camera. It proves
quackd's client is correct against *our reading* of the host, which is why the adapter's row
in `docs/adapters/status.md` says exactly that and no more.
"""

from __future__ import annotations

import base64
import contextlib
import json
import threading
import time
from io import BytesIO
from typing import Any

import zmq
from PIL import Image

ARM_JOINTS = (
    "shoulder_pan",
    "shoulder_lift",
    "elbow_flex",
    "wrist_flex",
    "wrist_roll",
    "gripper",
)
STATE_KEYS: tuple[str, ...] = (
    *(f"{side}_arm_{j}.pos" for side in ("left", "right") for j in ARM_JOINTS),
    "head_motor_1.pos",
    "head_motor_2.pos",
    "x.vel",
    "y.vel",
    "theta.vel",
)
VEL_KEYS = ("x.vel", "y.vel", "theta.vel")
WATCHDOG_S = 0.5
LOOP_HZ = 30.0


def _tiny_jpeg(colour: tuple[int, int, int] = (200, 90, 30), size: int = 32) -> str:
    """One base64 JPEG, encoded the way the host encodes: quality 90."""
    buf = BytesIO()
    Image.new("RGB", (size, size), colour).save(buf, format="JPEG", quality=90)
    return base64.b64encode(buf.getvalue()).decode("utf-8")


class FakeXLerobotHost:
    """Binds real loopback sockets and answers like the host does.

    Drive it either by running `start()` (a background thread, as the robot would) or by
    calling `pump()` by hand, which is what most tests want because it needs no clock."""

    def __init__(
        self,
        *,
        cameras: tuple[str, ...] = ("head",),
        watchdog_s: float = WATCHDOG_S,
        broken_camera: bool = False,
    ) -> None:
        self.ctx = zmq.Context()
        # LINGER 0 at creation so a fake left unclosed by a failing test cannot hold the
        # interpreter's exit in term(), which is forever with the default linger
        self.cmd = self.ctx.socket(zmq.PULL)
        self.cmd.setsockopt(zmq.LINGER, 0)
        self.cmd.setsockopt(zmq.CONFLATE, 1)
        self.cmd_port = self.cmd.bind_to_random_port("tcp://127.0.0.1")
        self.obs = self.ctx.socket(zmq.PUSH)
        self.obs.setsockopt(zmq.LINGER, 0)
        self.obs.setsockopt(zmq.CONFLATE, 1)
        self.obs_port = self.obs.bind_to_random_port("tcp://127.0.0.1")

        self.cameras = cameras
        self.watchdog_s = watchdog_s
        # upstream sets the key to "" when cv2.imencode fails, rather than dropping it
        self.frame = "" if broken_camera else _tiny_jpeg()
        self.state: dict[str, float] = dict.fromkeys(STATE_KEYS, 0.0)
        self.actions: list[dict[str, Any]] = []
        self.watchdog_trips = 0
        self._last_cmd = time.monotonic()
        self._running = False
        self._thread: threading.Thread | None = None

    @property
    def address(self) -> str:
        """What `--address` would be. Carries the observation port, because these were
        assigned rather than configured."""
        return f"tcp://127.0.0.1:{self.cmd_port}?obs={self.obs_port}"

    # ── the host's own logic ────────────────────────────────────────────────────────

    def apply(self, action: dict[str, Any]) -> None:
        """`XLerobot.send_action`, faithfully: prefix and suffix filters, and a base write on
        every action whether or not it carried velocity keys."""
        self.actions.append(dict(action))
        for key, value in action.items():
            if key.endswith(".pos") and key.split(".")[0] + ".pos" in STATE_KEYS:
                self.state[key] = float(value)
        # _body_to_wheel_raw is called unconditionally with .get(key, 0.0) defaults, and always
        # returns three wheels, so `if base_wheel_goal_vel:` is always true
        for key in VEL_KEYS:
            self.state[key] = float(action.get(key, 0.0))

    def observation(self) -> dict[str, Any]:
        obs: dict[str, Any] = dict(self.state)
        for cam in self.cameras:
            obs[cam] = self.frame
        return obs

    def pump(self, publish: bool = True) -> bool:
        """One host cycle: take a command if one is waiting, run the watchdog, publish."""
        got = False
        try:
            action = dict(json.loads(self.cmd.recv_string(zmq.NOBLOCK)))
            self.apply(action)
            self._last_cmd = time.monotonic()
            got = True
        except zmq.Again:
            pass
        if time.monotonic() - self._last_cmd > self.watchdog_s:
            # robot.stop_base(): the three wheels, and nothing else. The arms keep holding.
            if any(self.state[k] for k in VEL_KEYS):
                self.watchdog_trips += 1
            for key in VEL_KEYS:
                self.state[key] = 0.0
        if publish:
            # no client attached: the host drops the observation rather than queueing it
            with contextlib.suppress(zmq.Again):
                self.obs.send_string(json.dumps(self.observation()), flags=zmq.NOBLOCK)
        return got

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

    def __enter__(self) -> FakeXLerobotHost:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
