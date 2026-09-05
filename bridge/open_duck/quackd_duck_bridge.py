"""quackd's bridge for the Open Duck Mini v2: the walk loop, with the network as its pad.

This is the only part of quackd that runs on a robot. It exists because the Open Duck Mini
v2's runtime has no network control API: its command source is a local pygame gamepad and
its only socket checks the IMU. Rather than reimplement a 50 Hz control loop we are not
licensed to copy, this process *is* upstream's loop, with one substitution: the class it
constructs to read a gamepad is replaced, before its module executes, by a controller that
reads a socket instead.

That has three consequences worth stating plainly.

- The Feetech serial bus keeps exactly one owner, because there is still exactly one
  process. Do not run this and `v2_rl_walk_mujoco.py` at the same time.
- Nothing upstream is copied. quackd imports what you installed on your own Pi.
- Going limp is unreachable. The only channel from the network to the body is seven floats
  and a few buttons, so no message, malicious or buggy, can reach a torque register.

The deadman is evaluated inside `get_last_command()`, by the control thread, not by a timer.
A server thread that is starved, wedged or dead therefore still leaves a duck that stops.

Requires: the standard library, numpy, and `mini_bdx_runtime` installed by you from
https://github.com/apirrone/Open_Duck_Mini_Runtime (which carries no licence file, so it is
yours to install and not ours to ship). Run `--fake` to exercise everything with no robot.

Nothing here has been run on a physical duck.
"""

from __future__ import annotations

import _thread
import argparse
import contextlib
import hmac
import json
import logging
import math
import os
import selectors
import signal
import socket
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

PROTOCOL = "quackd-open-duck-bridge"
PROTOCOL_VERSION = 1
BRIDGE_VERSION = "0.1.0"
JSONRPC = "2.0"
DEFAULT_PORT = 9871
MAX_LINE = 64 * 1024

# The runtime's own XBoxController clamps, read from upstream source on 2026-09-03. The
# bridge re-applies them no matter what arrives, because a client is not to be trusted.
VX = (-0.15, 0.15)
VY = (-0.2, 0.2)
VYAW = (-1.0, 1.0)
NECK_PITCH = (-0.34, 1.1)
HEAD_PITCH = (-0.78, 0.3)
HEAD_YAW = (-0.5, 0.5)
HEAD_ROLL = (-0.5, 0.5)
HEAD_ORDER = ("neck_pitch", "head_pitch", "head_yaw", "head_roll")
HEAD_BOUNDS = {
    "neck_pitch": NECK_PITCH,
    "head_pitch": HEAD_PITCH,
    "head_yaw": HEAD_YAW,
    "head_roll": HEAD_ROLL,
}

#: No command for this long and the three velocities go to zero. quackd re-sends at 10 Hz,
#: so this is three missed packets: a link failure, not jitter. It fires before quackd's own
#: 0.5 s heartbeat, so the duck stops before the laptop has noticed anything.
DEADMAN_S = 0.3
#: The head holds instead of zeroing. A velocity step to zero is what releasing a stick
#: does and the policy has seen it; a neck snapping to centre is not.
HEAD_HOLDS_ON_DEADMAN = True
#: Head targets move no faster than this, which is what protects the neck from a step
#: command arriving over a network at 10 Hz.
HEAD_SLEW_RAD_S = 1.0
#: Fraction of the runtime's head range quackd will use when head control is enabled.
HEAD_SAFETY = 0.8
#: How long one antenna gesture plays before the antennas return to rest, and how fast a
#: wiggle wiggles. Upstream drives the antennas from the pad's triggers, so a gesture is a
#: shape in trigger space over time, and this is the only channel the bridge has to them.
GESTURE_S = 1.0
GESTURE_WIGGLE_HZ = 3.0
#: If upstream never constructs our controller within this long, the loop is reading a real
#: pad (or the class was renamed) while our socket does nothing. That is a duck moving for
#: reasons its owner cannot see, so we exit instead.
PATCH_WATCHDOG_S = 20.0
#: How long a shutdown waits after zeroing before it interrupts the loop. At 50 Hz that is
#: ~25 ticks of zero velocity, which is enough for the walk policy to come to a stand rather
#: than be killed mid-stride with torque on. Well inside the unit's TimeoutStopSec=8.
SETTLE_S = 0.5

log = logging.getLogger("quackd-duck-bridge")


def clamp(value: float, bounds: tuple[float, float]) -> float:
    return max(bounds[0], min(bounds[1], value))


def head_bounds(name: str, safety: float) -> tuple[float, float]:
    lo, hi = HEAD_BOUNDS[name]
    return lo * safety, hi * safety


# ── the command the control loop reads ──────────────────────────────────────────────────


@dataclass(frozen=True)
class Snapshot:
    """Published by the server thread, read by the control thread. Never mutated.

    One writer and one reader, and a single attribute store is atomic under the GIL, so the
    control loop can never see half of a seven float vector."""

    seq: int = 0
    at: float = 0.0
    vx: float = 0.0
    vy: float = 0.0
    vyaw: float = 0.0
    head: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    triggers: tuple[float, float] = (0.0, 0.0)


@dataclass
class Limits:
    vx: tuple[float, float] = VX
    vy: tuple[float, float] = VY
    vyaw: tuple[float, float] = VYAW
    head_enabled: bool = False
    head_safety: float = HEAD_SAFETY

    def as_dict(self) -> dict[str, list[float]]:
        out = {"vx": list(self.vx), "vy": list(self.vy), "vyaw": list(self.vyaw)}
        for name in HEAD_ORDER:
            lo, hi = head_bounds(name, self.head_safety) if self.head_enabled else (0.0, 0.0)
            out[name] = [lo, hi]
        return out


class BridgeCore:
    """Everything the bridge decides, with no sockets and no robot in sight.

    Kept free of I/O on purpose: the deadman, the clamps and the protocol are exactly the
    parts that must be tested without hardware, and this is the object the tests drive."""

    def __init__(
        self,
        *,
        limits: Limits | None = None,
        capabilities: dict[str, bool] | None = None,
        deadman_s: float = DEADMAN_S,
        token: str | None = None,
        camera_url: str | None = None,
        runtime: dict[str, Any] | None = None,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self.limits = limits or Limits()
        self.capabilities = capabilities or {}
        self.deadman_s = deadman_s
        self.token = token
        self.camera_url = camera_url
        self.runtime = runtime or {}
        self.now = now
        self.snapshot = Snapshot(at=now())
        self.controller_built_at: float | None = None
        #: None means nobody is watching. Nothing upstream reports a fall, and this
        #: bridge does not read the IMU (that bus has one owner too), so on real
        #: hardware this stays None and quackd reports posture unknown rather than
        #: standing. A duck on its side must never read as upright.
        self.fallen: bool | None = None
        self.paused = False
        self.loop_hz = 0.0
        self.ticks = 0
        self.deadman_tripped = False
        self.stopped_upto = 0
        #: A stop holds the duck down for this long, rather than lasting one tick. See
        #: `_zero()`. Sized at one deadman window: long enough to outlive the commands
        #: already in flight when the stop was sent, short enough that a human who
        #: deliberately drives again a moment later is not fighting it.
        self.stop_latch_s = self.deadman_s
        self.stopped_until = 0.0
        self.sounds: list[str] = []
        self.gestures: list[str] = []
        self.greeted = False
        self._seq = 0
        self._last_tick: float | None = None
        self._gesture: tuple[str, float] | None = None

    # ── what the control thread calls, once per tick ────────────────────────────────

    def triggers_for(self, now: float) -> tuple[float, float]:
        """An antenna gesture, as the trigger values upstream turns into servo positions.

        A gesture is a transient: it plays for `GESTURE_S` and the antennas then rest. This
        is what makes `express` a real movement rather than an accepted no-op."""
        if self._gesture is None:
            return (0.0, 0.0)
        name, started = self._gesture
        elapsed = now - started
        if elapsed > GESTURE_S:
            self._gesture = None
            return (0.0, 0.0)
        if name == "perk":
            return (1.0, 1.0)
        if name == "droop":
            return (0.0, 0.0)
        phase = math.sin(2 * math.pi * GESTURE_WIGGLE_HZ * elapsed)
        return (0.5 + 0.35 * phase, 0.5 - 0.35 * phase)

    def command_for_tick(self) -> Snapshot:
        """The deadman lives here, in the consumer, so that a dead server thread, a wedged
        one, or a crashed one all still leave a duck that stops."""
        now = self.now()
        if self._last_tick is not None:
            dt = now - self._last_tick
            if dt > 0:
                self.loop_hz = 0.9 * self.loop_hz + 0.1 * (1.0 / dt) if self.loop_hz else 1.0 / dt
        self._last_tick = now
        self.ticks += 1
        snap = self.snapshot
        stale = (now - snap.at) > self.deadman_s
        self.deadman_tripped = stale
        if not stale and self.fallen is not True:
            return Snapshot(
                seq=snap.seq,
                at=snap.at,
                vx=snap.vx,
                vy=snap.vy,
                vyaw=snap.vyaw,
                head=snap.head,
                triggers=self.triggers_for(now),
            )
        # zero the velocities and rest the antennas; hold the head, because a neck that
        # snaps is the failure upstream warns about and a velocity dropping to zero is not
        self._gesture = None
        head = snap.head if HEAD_HOLDS_ON_DEADMAN else (0.0, 0.0, 0.0, 0.0)
        return Snapshot(seq=snap.seq, at=snap.at, head=head)

    # ── the protocol ────────────────────────────────────────────────────────────────

    def handle(self, msg: dict[str, Any], *, authed: bool) -> tuple[dict[str, Any] | None, bool]:
        """Return (reply or None, is_now_authed). A notification replies with None."""
        method = msg.get("method")
        params = msg.get("params") or {}
        msg_id = msg.get("id")

        if method == "duck.hello":
            if params.get("protocol") != PROTOCOL:
                return self._err(
                    msg_id, 3, f"this is {PROTOCOL}, not {params.get('protocol')!r}"
                ), authed
            remote = params.get("protocol_version")
            if remote is not None and int(remote) != PROTOCOL_VERSION:
                return (
                    self._err(
                        msg_id,
                        3,
                        f"the bridge speaks {PROTOCOL} v{PROTOCOL_VERSION}, the client speaks "
                        f"v{remote}; refusing rather than guessing",
                    ),
                    authed,
                )
            if self.token is not None and not hmac.compare_digest(
                str(params.get("token") or ""), self.token
            ):
                return self._err(
                    msg_id, 2, "bad or missing token; see the bridge's token file"
                ), False
            self.greeted = True
            return self._ok(msg_id, self.hello()), True

        if not self.greeted or (self.token is not None and not authed):
            return self._err(msg_id, 2, "say duck.hello first"), authed

        if method == "duck.command":
            self._apply(params)
            return None, authed
        if method == "duck.stop":
            self._zero()
            return self._ok(
                msg_id,
                {
                    "stopped": True,
                    "limp": False,
                    "ignore_seq_upto": self._seq,
                    # how long commands will be dropped for, so a client can say honestly
                    # that the stop holds rather than that zeros were published once
                    "latched_ms": int(self.stop_latch_s * 1000),
                },
            ), authed
        if method == "duck.state":
            return self._ok(msg_id, self.state()), authed
        if method == "duck.health":
            return self._ok(msg_id, self.health()), authed
        if method == "duck.sound":
            if not self.capabilities.get("speaker"):
                return self._err(
                    msg_id, 4, "this duck has no speaker in its duck_config.json"
                ), authed
            mood = str(params.get("mood", "chirp"))
            self.sounds.append(mood)
            # All the bridge can reach through the pad is upstream's random-sound button, so
            # the mood is logged and the reply says honestly how it was played.
            return self._ok(
                msg_id, {"accepted": True, "mood": mood, "how": "the pad's sound button"}
            ), authed
        if method == "duck.antennas":
            if not self.capabilities.get("antennas"):
                return self._err(
                    msg_id, 4, "this duck has no antennas in its duck_config.json"
                ), authed
            gesture = str(params.get("gesture", "wiggle"))
            if gesture not in ("perk", "droop", "wiggle"):
                return self._err(msg_id, -32602, f"unknown antenna gesture {gesture!r}"), authed
            self.gestures.append(gesture)
            self._gesture = (gesture, self.now())
            return self._ok(
                msg_id, {"accepted": True, "gesture": gesture, "seconds": GESTURE_S}
            ), authed
        return self._err(msg_id, -32601, f"unknown method {method!r}"), authed

    def _apply(self, params: dict[str, Any]) -> None:
        # A stop that lasts one tick is not a stop. `duck.stop` used to publish zeros and
        # nothing more, so the very next `duck.command` — one already in flight when the
        # operator hit the brake, or the tail of a verb that had not noticed yet — put the
        # velocity straight back 100 ms later. Commands arriving inside the latch are
        # dropped entirely, which also leaves `snap.at` stale, so the duck stays stopped
        # until something deliberately drives it again.
        if self.now() < self.stopped_until:
            return
        snap = self.snapshot
        head = list(snap.head)
        if self.limits.head_enabled:
            asked = params.get("head") or {}
            for i, name in enumerate(HEAD_ORDER):
                if name in asked:
                    target = clamp(float(asked[name]), head_bounds(name, self.limits.head_safety))
                    step = HEAD_SLEW_RAD_S * self.deadman_s
                    head[i] = max(head[i] - step, min(head[i] + step, target))
        self._seq += 1
        self.snapshot = Snapshot(
            seq=self._seq,
            at=self.now(),
            vx=clamp(float(params.get("vx", 0.0)), self.limits.vx),
            vy=clamp(float(params.get("vy", 0.0)), self.limits.vy),
            vyaw=clamp(float(params.get("vyaw", 0.0)), self.limits.vyaw),
            head=(head[0], head[1], head[2], head[3]),
            triggers=snap.triggers,
        )

    def _zero(self) -> None:
        self._seq += 1
        self.stopped_upto = self._seq
        self.stopped_until = self.now() + self.stop_latch_s
        self._gesture = None
        self.snapshot = Snapshot(seq=self._seq, at=self.now(), head=self.snapshot.head)

    def hello(self) -> dict[str, Any]:
        return {
            "protocol": PROTOCOL,
            "protocol_version": PROTOCOL_VERSION,
            "bridge_version": BRIDGE_VERSION,
            "robot": {"vendor": "apirrone", "model": "open-duck-mini-v2"},
            "runtime": self.runtime,
            "capabilities": {**self.capabilities, "head": self.limits.head_enabled},
            "camera": {"url": self.camera_url},
            "limits": self.limits.as_dict(),
            "safety": {
                "deadman_ms": int(self.deadman_s * 1000),
                "deadman_owner": "bridge",
                "head_on_deadman": "hold",
                "stop_is_limp": False,
                "getup_policy": False,
                "estop": "the power switch, and nothing else",
            },
        }

    def state(self) -> dict[str, Any]:
        snap = self.snapshot
        return {
            "t": self.now(),
            "seq": snap.seq,
            "policy_running": not self.paused,
            "fallen": self.fallen,
            "fall_detection": self.fallen is not None,
            "moving": bool(snap.vx or snap.vy or snap.vyaw),
            "loop_hz": round(self.loop_hz, 1),
            "ticks": self.ticks,
            "command_age_ms": int((self.now() - snap.at) * 1000),
            "deadman_tripped": self.deadman_tripped,
            "stop_latched": self.now() < self.stopped_until,
            "pad_override": False,
            "unknowns": ["fall detection", "battery", "whether the pause took"],
        }

    def health(self) -> dict[str, Any]:
        healthy = self.controller_built_at is not None
        reason = None if healthy else "the walk loop never asked for a controller"
        if self.fallen is True:
            healthy, reason = False, "the duck is down and this robot has no get-up policy"
        return {
            "healthy": healthy,
            "reason": reason,
            "loop_hz": round(self.loop_hz, 1),
            "ticks": self.ticks,
        }

    @staticmethod
    def _ok(msg_id: Any, result: dict[str, Any]) -> dict[str, Any]:
        return {"jsonrpc": JSONRPC, "id": msg_id, "result": result}

    @staticmethod
    def _err(msg_id: Any, code: int, message: str) -> dict[str, Any]:
        return {"jsonrpc": JSONRPC, "id": msg_id, "error": {"code": code, "message": message}}


# ── the controller upstream thinks it constructed ───────────────────────────────────────


class _Button:
    """Enough of upstream's Button that reading one never raises inside the control loop."""

    def __init__(self) -> None:
        self.last_pressed_time = 0.0
        self.timeout = 0.2
        self.is_pressed = False
        self.triggered = False
        self.released = True


class _Buttons:
    def __init__(self) -> None:
        for name in ("A", "B", "X", "Y", "LB", "RB", "dpad_up", "dpad_down"):
            setattr(self, name, _Button())

    def __getattr__(self, name: str) -> Any:
        # an attribute we did not anticipate must not raise mid-stride
        log.warning("the walk loop read an unknown button %r; answering not pressed", name)
        button = _Button()
        setattr(self, name, button)
        return button


def make_buttons() -> Any:
    """Prefer upstream's own Buttons so every attribute it reads exists, without calling a
    constructor whose side effects we have not read."""
    try:
        from mini_bdx_runtime.buttons import Buttons  # type: ignore[import-not-found]

        return object.__new__(Buttons)
    except Exception:
        return _Buttons()


class NetworkController:
    """A drop-in for upstream's `XBoxController`, fed by a socket instead of a stick."""

    def __init__(self, core: BridgeCore, command_freq: float = 20, only_head_control: bool = False):
        self.core = core
        self.command_freq = command_freq
        self.only_head_control = only_head_control
        self.buttons = make_buttons()
        if isinstance(self.buttons, _Buttons) is False and not hasattr(self.buttons, "A"):
            for name in ("A", "B", "X", "Y", "LB", "RB", "dpad_up", "dpad_down"):
                setattr(self.buttons, name, _Button())
        self.last_commands = _zeros7()
        core.controller_built_at = core.now()

    def get_last_command(self) -> tuple[Any, Any, float, float]:
        snap = self.core.command_for_tick()
        self.last_commands = _vector(snap)
        self._pulse_buttons()
        return self.last_commands, self.buttons, snap.triggers[0], snap.triggers[1]

    def _pulse_buttons(self) -> None:
        """One queued sound becomes one press of the pad's sound button. That is the only
        channel the bridge has to the speaker, and the reply to duck.sound says so."""
        sound_button = getattr(self.buttons, "B", None)
        if sound_button is None:
            return
        wants = bool(self.core.sounds)
        if wants:
            self.core.sounds.pop(0)
        sound_button.triggered = wants
        sound_button.is_pressed = wants
        sound_button.released = not wants


def _zeros7() -> Any:
    try:
        import numpy as np

        return np.zeros(7)
    except Exception:
        return [0.0] * 7


def _vector(snap: Snapshot) -> Any:
    values = [snap.vx, snap.vy, snap.vyaw, *snap.head]
    try:
        import numpy as np

        return np.array(values, dtype=float)
    except Exception:
        return values


# ── the server ──────────────────────────────────────────────────────────────────────────


@dataclass
class _Client:
    conn: socket.socket
    buf: bytes = b""
    authed: bool = False
    out: list[bytes] = field(default_factory=list)
    #: Whether this connection has ever driven the duck. Only a client that was actually
    #: commanding gets to zero it on the way out — see `Server._drop`.
    commanded: bool = False


class Server(threading.Thread):
    """A `selectors` loop in a background thread: no event loop, no allocation per poll, and
    a strict work budget, because the control loop's whole period is 20 ms."""

    daemon = True

    def __init__(self, core: BridgeCore, host: str, port: int) -> None:
        super().__init__(name="quackd-duck-bridge-server")
        self.core = core
        self.host = host
        self.port = port
        self._sel = selectors.DefaultSelector()
        self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._listener.bind((host, port))
        self._listener.listen(4)
        self.port = self._listener.getsockname()[1]
        self._listener.setblocking(False)
        self._sel.register(self._listener, selectors.EVENT_READ, None)
        self._shutdown = threading.Event()

    def stop(self) -> None:
        self._shutdown.set()

    def run(self) -> None:
        while not self._shutdown.is_set():
            for key, _ in self._sel.select(timeout=0.05):
                if key.data is None:
                    self._accept()
                else:
                    self._serve(key)
        self._sel.close()
        self._listener.close()

    def _accept(self) -> None:
        try:
            conn, _ = self._listener.accept()
        except OSError:
            return
        conn.setblocking(False)
        conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self._sel.register(conn, selectors.EVENT_READ, _Client(conn))

    def _serve(self, key: selectors.SelectorKey) -> None:
        client: _Client = key.data
        try:
            data = client.conn.recv(8192)
        except (BlockingIOError, InterruptedError):
            # a spurious readability wakeup, not a closed socket. Reading it as EOF dropped a
            # healthy control client and, before `_drop` learned who was driving, stopped the
            # duck for it.
            return
        except OSError:
            data = b""
        if not data:
            self._drop(client)
            return
        client.buf += data
        if len(client.buf) > MAX_LINE:
            log.warning("dropping an oversized line from a client")
            client.buf = b""
            return
        while b"\n" in client.buf:
            line, _, client.buf = client.buf.partition(b"\n")
            if not line.strip():
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if msg.get("method") == "duck.command":
                client.commanded = True
            reply, client.authed = self.core.handle(msg, authed=client.authed)
            if reply is not None:
                self._send(client, reply)

    def _send(self, client: _Client, obj: dict[str, Any]) -> None:
        payload = (json.dumps(obj, separators=(",", ":")) + "\n").encode()
        try:
            client.conn.sendall(payload)
        except OSError:
            self._drop(client)

    def _drop(self, client: _Client) -> None:
        # A control client that vanished must not leave the duck walking — but only a client
        # that was actually driving. This used to zero on *any* disconnect, and the hardware
        # checklist tells the operator to run `quackd doctor` from a second terminal while the
        # duck walks, so following the instructions cut the velocity mid-stride and looked
        # exactly like the Wi-Fi latency step 5 had just warned about.
        #
        # Safe to be choosy: the immediate zero is a latency optimisation, and the deadman is
        # what actually guarantees a vanished pilot stops the duck 300 ms later.
        if client.commanded:
            log.info("the commanding client disconnected; zeroing")
            self.core._zero()
        with contextlib.suppress(KeyError, ValueError):
            self._sel.unregister(client.conn)
        client.conn.close()


# ── running it ──────────────────────────────────────────────────────────────────────────


def read_duck_config(path: str) -> dict[str, Any]:
    try:
        with open(os.path.expanduser(path), encoding="utf-8") as fh:
            return dict(json.load(fh))
    except (OSError, ValueError):
        return {}


def capabilities_from(config: dict[str, Any]) -> dict[str, bool]:
    """A real duck is whatever its owner soldered, and duck_config.json is where it says so."""
    features = config.get("expression_features") or {}
    return {
        "camera": bool(features.get("camera")),
        "speaker": bool(features.get("speaker")),
        "antennas": bool(features.get("antennas")),
        "microphone": bool(features.get("microphone")),
    }


def install_shim(core: BridgeCore) -> None:
    """Rebind the class upstream imports, before the module that imports it is executed."""
    import mini_bdx_runtime.xbox_controller as xc  # type: ignore[import-not-found]

    def factory(command_freq: float = 20, only_head_control: bool = False) -> NetworkController:
        return NetworkController(core, command_freq, only_head_control)

    xc.XBoxController = factory


def install_settle(
    core: BridgeCore,
    seconds: float = SETTLE_S,
    interrupt: Callable[[], None] = _thread.interrupt_main,
) -> threading.Event:
    """Zero the command on SIGTERM/SIGINT, then let the loop run long enough to act on it.

    The process had no signal handling at all, so `systemctl stop`, a restart, a reboot or a
    Ctrl-C killed the interpreter between two 20 ms ticks. The Feetech servos hold their last
    goal position with torque on, so a duck stopped mid-stride topples with rigid legs — the
    load case the unit's own comment says strips printed gears.

    The `finally` in `main()` looked like it covered this and did not: it runs *after*
    `runpy.run_path` has returned, so the zeros it published had no reader left. The settle
    has to happen while the loop is still ticking, which means from a signal handler.

    The handler itself does two cheap things and returns. A helper thread then waits out the
    settle window — during which the still-running loop consumes ~25 ticks of zero velocity
    and comes to a stand — before raising KeyboardInterrupt in the main thread, so upstream's
    own cleanup and ours both run inside the unit's TimeoutStopSec. A second signal gives up
    waiting and exits now.

    Torque stays on throughout: `stop_is_limp` is False by design, and de-energising a
    standing duck drops it.

    `interrupt` is injectable only so a test can watch the settle happen without a
    KeyboardInterrupt landing in the test runner."""
    asked = threading.Event()

    def handler(signum: int, _frame: Any) -> None:
        if asked.is_set():  # the human is not convinced; stop asking nicely
            os._exit(1)
        asked.set()
        core._zero()
        log.info("signal %d: zeroed the command, settling for %.1fs", signum, seconds)

    def settle() -> None:
        asked.wait()
        time.sleep(seconds)
        interrupt()

    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(ValueError, OSError, AttributeError):
            signal.signal(sig, handler)
    threading.Thread(target=settle, name="quackd-duck-bridge-settle", daemon=True).start()
    return asked


def watchdog(core: BridgeCore, seconds: float = PATCH_WATCHDOG_S) -> None:
    def check() -> None:
        time.sleep(seconds)
        if core.controller_built_at is None:
            log.error(
                "the walk loop never constructed our controller after %.0fs. It is reading a "
                "real gamepad, or upstream renamed XBoxController. Refusing to keep a socket "
                "open that controls nothing, and exiting.",
                seconds,
            )
            os._exit(3)

    threading.Thread(target=check, name="quackd-duck-bridge-watchdog", daemon=True).start()


def run_fake_loop(
    core: BridgeCore, controller: NetworkController, hz: float, seconds: float
) -> None:
    """A synthetic control loop, so every part of this file can be exercised on a laptop
    with no robot, no runtime and no servos."""
    period = 1.0 / hz
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        controller.get_last_command()
        time.sleep(period)


def build_core(args: argparse.Namespace) -> BridgeCore:
    config = read_duck_config(args.duck_config)
    caps = capabilities_from(config)
    # `expression_features.camera` says who owns the *device*, not whether quackd can see.
    # When it is true the robot's own runtime constructs a Cam and owns it, so
    # quackd_duck_camd.py refuses to start rather than fight for it — which is why an owner
    # who wants frames sets it false. Reading the capability from that same flag therefore
    # meant a correctly configured duck reported no camera and lost `observe`, `go_to`,
    # `search_scan` and `approach_and` at connect, with no configuration that produced both
    # frames and the verbs that use them.
    runtime_owns_camera = bool(caps.get("camera"))
    if args.fake:
        caps = {"camera": False, "speaker": True, "antennas": True, "microphone": False}
    # quackd reads frames from an HTTP snapshot, never through this socket: encoding a
    # 512 by 512 JPEG inside a 20 ms control tick is not affordable on a Pi Zero 2 W. So the
    # snapshot URL is what decides this, on the real path exactly as under --fake: a camera
    # with nowhere to fetch it from is not a camera, and saying otherwise would promise four
    # verbs that then fail at runtime.
    caps["camera"] = bool(args.camera_url)
    if runtime_owns_camera and not args.camera_url:
        log.warning(
            "duck_config.json says this duck has a camera, but no --camera-url was given, "
            "so quackd has nowhere to fetch a frame from. Advertising no camera: the verbs "
            "that need one will not exist rather than fail. See docs/adapters/open_duck.md."
        )
    elif runtime_owns_camera and args.camera_url:
        log.warning(
            "duck_config.json says expression_features.camera is true, so the robot's own "
            "runtime owns the camera and quackd_duck_camd.py will refuse to start — nothing "
            "will be serving %s. Set that flag false and let camd have the device. See "
            "docs/adapters/open_duck.md.",
            args.camera_url,
        )
    limits = Limits(
        vx=(-abs(args.max_vx), abs(args.max_vx)),
        vy=(-abs(args.max_vy), abs(args.max_vy)),
        vyaw=(-abs(args.max_vyaw), abs(args.max_vyaw)),
        head_enabled=bool(args.enable_head),
        head_safety=args.head_safety,
    )
    token = None
    if args.token_file and os.path.exists(args.token_file):
        with open(args.token_file, encoding="utf-8") as fh:
            token = fh.read().strip()
    core = BridgeCore(
        limits=limits,
        capabilities=caps,
        deadman_s=args.deadman_ms / 1000.0,
        token=token,
        camera_url=args.camera_url,
        runtime={"script": args.script, "start_paused": bool(config.get("start_paused"))},
    )
    core.paused = bool(config.get("start_paused"))
    return core


#: What upstream's walk loop opens by relative path, read at the pin on 2026-09-03:
#: `PolyReferenceMotion("./polynomial_coefficients.pkl")` and
#: `Sounds(sound_directory="../mini_bdx_runtime/assets/")`. Both resolve only from the
#: script's own `scripts/` directory, and the first is opened inside `RLWalk.__init__`
#: *after* the servo bus has been powered — so a wrong working directory is a traceback over
#: fourteen energised joints. The bridge checks before it binds a socket instead.
SCRIPT_RELATIVE_FILES = ("polynomial_coefficients.pkl",)
SCRIPT_RELATIVE_DIRS = (os.path.join("..", "mini_bdx_runtime", "assets"),)


def script_workdir(args: argparse.Namespace) -> str:
    """The directory upstream's loop must run from: its own, unless told otherwise."""
    if args.workdir:
        return os.path.abspath(os.path.expanduser(args.workdir))
    return os.path.dirname(os.path.abspath(os.path.expanduser(args.script)))


def preflight(args: argparse.Namespace) -> str | None:
    """Everything that must hold before a socket is bound or a servo is energised.

    Returns a message to fail with, or None. This runs early on purpose: upstream powers the
    Feetech bus in its constructor and only then reads its motion data, so checking after the
    fact means the failure lands on a duck that is already stiff and listening."""
    script = os.path.abspath(os.path.expanduser(args.script))
    if not os.path.isfile(script):
        return f"--script {script} does not exist"
    workdir = script_workdir(args)
    if not os.path.isdir(workdir):
        return f"the working directory {workdir} does not exist; set --workdir"
    for name in SCRIPT_RELATIVE_FILES:
        if not os.path.isfile(os.path.join(workdir, name)):
            return (
                f"{name} is not in {workdir}. Upstream's walk loop opens it by a path "
                "relative to its working directory, while the servo bus is already powered. "
                "Point --script at the copy inside your Open_Duck_Mini_Runtime/scripts, or "
                "set --workdir to the directory that holds it."
            )
    for name in SCRIPT_RELATIVE_DIRS:
        if not os.path.isdir(os.path.join(workdir, name)):
            log.warning(
                "%s is not under %s, so upstream may not find its sounds. This is a warning, "
                "not a refusal: a duck with no speaker does not need them.",
                name,
                workdir,
            )
    return None


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="quackd-duck-bridge", description=__doc__)
    p.add_argument("command", choices=["serve", "check"], nargs="?", default="serve")
    p.add_argument("--script", default="", help="upstream's v2_rl_walk_mujoco.py")
    p.add_argument(
        "--script-arg",
        action="append",
        default=[],
        help="passed through verbatim. Use the = form for a value that starts with a dash "
        "(--script-arg=--onnx_model_path), and absolute paths: the loop runs from the "
        "script's own directory, not from yours",
    )
    p.add_argument(
        "--workdir",
        default=None,
        help="run upstream's loop from here instead of the script's own directory. It opens "
        "its motion data by relative path, so this has to be the directory holding "
        "polynomial_coefficients.pkl",
    )
    p.add_argument(
        "--bind",
        default="127.0.0.1",
        help="loopback by default: there is no auth "
        "unless you set a token, and this port walks a robot",
    )
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    p.add_argument("--duck-config", default="~/duck_config.json")
    p.add_argument("--token-file", default="/etc/quackd/duck-bridge.token")
    p.add_argument("--camera-url", default=None, help="an HTTP snapshot the camera process serves")
    p.add_argument("--deadman-ms", type=int, default=int(DEADMAN_S * 1000))
    p.add_argument("--max-vx", type=float, default=VX[1])
    p.add_argument("--max-vy", type=float, default=VY[1])
    p.add_argument("--max-vyaw", type=float, default=VYAW[1])
    p.add_argument(
        "--enable-head",
        action="store_true",
        help="EXPERIMENTAL: upstream warns "
        "head control can break the head, so it is off unless you ask",
    )
    p.add_argument("--head-safety", type=float, default=HEAD_SAFETY)
    p.add_argument(
        "--settle-s",
        type=float,
        default=SETTLE_S,
        help="on SIGTERM or Ctrl-C, hold the loop at zero velocity this long before letting "
        "it exit, so the duck comes to a stand instead of being killed mid-stride. Must stay "
        "under the unit's TimeoutStopSec",
    )
    p.add_argument("--fake", action="store_true", help="run a synthetic loop, no robot needed")
    p.add_argument("--seconds", type=float, default=0.0, help="--fake: stop after this long")
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    logging.basicConfig(
        stream=sys.stderr, level=logging.INFO, format="quackd-duck-bridge %(levelname)s %(message)s"
    )
    core = build_core(args)
    if args.command == "check":
        sys.stdout.write(json.dumps(core.hello(), indent=2) + "\n")
        return 0
    # Before the socket, before the servos: a serve that cannot possibly work must fail while
    # the duck is still inert and nothing can connect to it.
    if not args.fake:
        if not args.script:
            log.error("serve needs --script pointing at upstream's v2_rl_walk_mujoco.py")
            return 2
        if (problem := preflight(args)) is not None:
            log.error("%s", problem)
            return 2
    if args.bind not in ("127.0.0.1", "localhost") and core.token is None:
        log.warning(
            "binding %s with no token: anything on this network can walk your duck. Write a "
            "token to %s, or bind 127.0.0.1 and use ssh -L.",
            args.bind,
            args.token_file,
        )
    server = Server(core, args.bind, args.port)
    server.start()
    # Armed before anything can walk, and armed under --fake too, so the dry run rehearses
    # the shutdown as well as the protocol.
    install_settle(core, args.settle_s)
    log.info(
        "listening on %s:%d, deadman %d ms, settle %.1f s, head %s",
        args.bind,
        server.port,
        args.deadman_ms,
        args.settle_s,
        "on" if args.enable_head else "off",
    )
    try:
        if args.fake:
            controller = NetworkController(core)
            run_fake_loop(core, controller, 50.0, args.seconds or 3600.0)
            return 0
        import runpy

        script = os.path.abspath(os.path.expanduser(args.script))
        workdir = script_workdir(args)
        install_shim(core)
        watchdog(core)
        # Upstream opens its motion data and its sounds by relative path, so the loop has to
        # run from the script's own directory. Doing it here as well as in the unit means a
        # hand-edited or hand-run invocation cannot get it wrong.
        os.chdir(workdir)
        log.info("running %s from %s", script, workdir)
        sys.argv = [script, *args.script_arg]
        runpy.run_path(script, run_name="__main__")
        return 0
    except KeyboardInterrupt:
        return 0
    finally:
        # No zero-and-sleep here. It used to sit in this block looking like the shutdown
        # settle, but it runs after the loop has already exited, so the zeros it published
        # had no reader and the sleep settled nothing. The settle that works is
        # `install_settle`, which acts while the loop is still ticking.
        server.stop()


if __name__ == "__main__":
    raise SystemExit(main())
