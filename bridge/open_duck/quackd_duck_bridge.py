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
#: command arriving over a network at 10 Hz. Applied per elapsed second, in the control
#: loop, because a limit expressed per received message is not a rate limit at all.
HEAD_SLEW_RAD_S = 1.0
#: The longest gap the slew will integrate over. Without it, a loop resuming after a stall
#: would take one catch-up leap of exactly the size the limit exists to prevent.
HEAD_SLEW_MAX_DT_S = 0.1
#: Fraction of the runtime's head range quackd will use when head control is enabled.
HEAD_SAFETY = 0.8
#: How long one antenna gesture plays before the antennas return to rest, and how fast a
#: wiggle wiggles. Upstream drives the antennas from the pad's triggers, so a gesture is a
#: shape in trigger space over time, and this is the only channel the bridge has to them.
GESTURE_S = 1.0
GESTURE_WIGGLE_HZ = 3.0
#: Where a drooped antenna sits. Upstream maps -1..1 onto the servo with 0 as rest, so rest
#: and droop were the same number until this existed. Short of -1 because nobody has watched
#: these two 9 g servos hit their stop.
DROOP_POSITION = -0.6
#: If upstream never constructs our controller within this long, the loop is reading a real
#: pad (or the class was renamed) while our socket does nothing. That is a duck moving for
#: reasons its owner cannot see, so we exit instead.
#:
#: The clock starts before `runpy`, so this window has to cover importing onnxruntime,
#: building an InferenceSession, opening the Feetech bus, `RLWalk.start()` (which contains a
#: hard two-second sleep) and constructing the IMU — on a 512 MB Pi Zero 2 W with a cold page
#: cache. It was 20 s, which is a plausible way to lose a bring-up to a false positive, and
#: `--patch-watchdog-s` exists because nobody here has measured the real number (up.LOOP_HEADROOM).
PATCH_WATCHDOG_S = 150.0
#: How long a shutdown waits after zeroing before it interrupts the loop. At 50 Hz that is
#: ~25 ticks of zero velocity, which is enough for the walk policy to come to a stand rather
#: than be killed mid-stride with torque on. Well inside the unit's TimeoutStopSec=8.
SETTLE_S = 0.5
#: A loop that has not ticked for this long is wedged, not slow. Above one 20 ms period plus
#: GC jitter, below quackd's own 0.5 s heartbeat, so the bridge notices first.
TICK_STALE_S = 0.25

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
        #: Bumped by every stop. A `duck.command` carries the epoch its sender last knew
        #: about, so one composed before the stop it had not yet heard of is distinguishable
        #: from one a pilot deliberately sent afterwards — which is the whole difference
        #: between discarding an in-flight packet and refusing to drive again. See `_apply`.
        self.stop_epoch = 0
        #: How long after a stop a stale command is still refused. This bounds the epoch
        #: check rather than replacing it: if the stop's reply were lost, the client would
        #: never learn the new epoch, and without a window every later command would be
        #: dropped forever. Long enough to outlive a link round trip, and irrelevant to a
        #: client that is up to date.
        self.stop_latch_s = self.deadman_s
        self.stopped_until = 0.0
        self.sounds: list[str] = []
        self.gestures: list[str] = []
        self._seq = 0
        self._last_tick: float | None = None
        self._gesture: tuple[str, float] | None = None
        #: Where the head is being asked to go, and where the slew has actually got it to.
        #: Two fields rather than one because the limit is a rate: the target arrives from
        #: the network whenever a client feels like it, and the position moves in the
        #: control loop, at a bounded speed, in the only place that knows how much time
        #: has passed.
        self.head_target: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
        self.head_now: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
        self._head_tick: float | None = None
        #: So a deadman trip is logged once when it happens, rather than 50 times a second.
        self._was_stale = False

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
            # Upstream's Antennas.set_position takes -1..1 with 0 as rest, so returning 0.0
            # commanded the exact resting position and `express(droop)` was a physical no-op
            # that reported success. A physical trigger axis cannot produce a negative value,
            # so this is a place quackd sends something a gamepad never could — recorded as
            # ANTENNA_GESTURES in upstream_api.py.
            return (DROOP_POSITION, DROOP_POSITION)
        phase = math.sin(2 * math.pi * GESTURE_WIGGLE_HZ * elapsed)
        return (0.5 + 0.35 * phase, 0.5 - 0.35 * phase)

    def command_for_tick(self) -> Snapshot:
        """The deadman lives here, in the consumer, so that a dead server thread, a wedged
        one, or a crashed one all still leave a duck that stops."""
        now = self.now()
        if self._last_tick is not None:
            dt = now - self._last_tick
            # Seeding the average from a single sample let one sub-millisecond first gap
            # plant a four-figure rate that then took hundreds of ticks to decay.
            if dt > 0:
                sample = 1.0 / dt
                self.loop_hz = 0.9 * self.loop_hz + 0.1 * sample if self.ticks > 1 else sample
        self._last_tick = now
        self.ticks += 1
        snap = self.snapshot
        # The head is a rate-limited servo of its own, stepped here rather than in `_apply`:
        # the slew has to be per unit of time, and this is the only place that knows how much
        # time has passed. See `_step_head`.
        head = self._step_head(now)
        # Evaluated every tick regardless of the deadman. An antenna gesture is not motion —
        # two 9 g servos on a GPIO pin cannot move the robot — and `duck.antennas` does not
        # refresh the command timestamp, so cancelling it on a stale command meant a gesture
        # that arrived between two LLM turns (which is every gesture) never played at all,
        # while `express` returned success.
        triggers = self.triggers_for(now)
        stale = (now - snap.at) > self.deadman_s
        self.deadman_tripped = stale
        if stale and not self._was_stale:
            log.warning(
                "deadman: no command for %.0f ms, velocities zeroed (tick %d)",
                (now - snap.at) * 1000,
                self.ticks,
            )
        elif self._was_stale and not stale:
            log.info("deadman: commands are arriving again (tick %d)", self.ticks)
        self._was_stale = stale
        if not stale and self.fallen is not True:
            return Snapshot(
                seq=snap.seq,
                at=snap.at,
                vx=snap.vx,
                vy=snap.vy,
                vyaw=snap.vyaw,
                head=head,
                triggers=triggers,
            )
        # Zero the velocities; hold the head, because a neck that snaps is the failure
        # upstream warns about and a velocity dropping to zero is not.
        #
        # "Hold" means do not re-centre it. It deliberately does NOT mean freeze it where it
        # happens to be: a head command is a *position*, already clamped, and letting it
        # finish travelling there at the rate limit is bounded and finite — unlike a
        # velocity, which would mean walking forever. Freezing it instead meant `gaze` could
        # never reach a target more than one deadman window away (0.3 rad at 1 rad/s), which
        # is most of this neck's travel, so the verb reported an angle the head never took.
        # `duck.stop` still pins the target, because a stop should stop everything.
        if not HEAD_HOLDS_ON_DEADMAN:
            self.head_target = (0.0, 0.0, 0.0, 0.0)
        return Snapshot(seq=snap.seq, at=snap.at, head=head, triggers=triggers)

    def _step_head(self, now: float) -> tuple[float, float, float, float]:
        """Move the head toward its target at no more than HEAD_SLEW_RAD_S.

        This used to live in `_apply`, where the step was `HEAD_SLEW_RAD_S * self.deadman_s`
        applied once per *received message*. Two things were wrong with that. A single `gaze`
        sends exactly one message, so the head moved 0.3 rad once and stopped — short of a
        target the verb then reported as reached. And a faster sender bypassed the limit
        entirely: at 10 Hz the same constant is 3 rad/s on the joint it exists to protect,
        and a burst of buffered commands is unbounded. Time is the only honest denominator."""
        if self._head_tick is None:
            self._head_tick = now
            return self.head_now
        dt = max(0.0, now - self._head_tick)
        self._head_tick = now
        # a resumed stall must not become one catch-up leap
        step = HEAD_SLEW_RAD_S * min(dt, HEAD_SLEW_MAX_DT_S)
        moved = tuple(
            max(current - step, min(current + step, target))
            for current, target in zip(self.head_now, self.head_target, strict=True)
        )
        self.head_now = (moved[0], moved[1], moved[2], moved[3])
        return self.head_now

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
            log.info("client authenticated (auth: %s)", "token" if self.token else "none")
            return self._ok(msg_id, self.hello()), True

        # `authed` is per connection. This used to also consult a `greeted` flag that lived
        # on the core, so once *any* client had said hello, a second connection could send
        # `duck.command` without a handshake at all — skipping the protocol and version
        # checks on a socket that walks a robot.
        if not authed:
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
                    # The client echoes this on every later command, which is how the
                    # bridge tells a deliberate one from a packet that was already in
                    # flight when the brake went on.
                    "stop_epoch": self.stop_epoch,
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
        # velocity straight back 100 ms later.
        #
        # What must be discarded is a command composed *before* its sender knew about the
        # stop, and only that. A plain time window cannot tell the two apart and would break
        # ordinary driving: `_turn` ends every scan step with a stop, so the next step's
        # opening commands would be swallowed and the duck would under-rotate all the way
        # through a `search_scan`. The epoch makes the distinction exact, and the window
        # only bounds it, so a stop whose reply was lost cannot wedge the duck for good.
        #
        # A client that sends no epoch at all is held to the blunt window instead: it cannot
        # prove it has heard, so it is not given the benefit of the doubt.
        if self.now() < self.stopped_until:
            epoch = params.get("epoch")
            if epoch is None or int(epoch) < self.stop_epoch:
                return
        snap = self.snapshot
        # Only the target moves here. The position is stepped toward it by the control loop
        # at a bounded rate (`_step_head`), which is the only place that knows how much time
        # has passed — a step applied per received message is not a rate limit.
        if self.limits.head_enabled:
            asked = params.get("head") or {}
            target = list(self.head_target)
            for i, name in enumerate(HEAD_ORDER):
                if name in asked:
                    target[i] = clamp(
                        float(asked[name]), head_bounds(name, self.limits.head_safety)
                    )
            self.head_target = (target[0], target[1], target[2], target[3])
        self._seq += 1
        self.snapshot = Snapshot(
            seq=self._seq,
            at=self.now(),
            vx=clamp(float(params.get("vx", 0.0)), self.limits.vx),
            vy=clamp(float(params.get("vy", 0.0)), self.limits.vy),
            vyaw=clamp(float(params.get("vyaw", 0.0)), self.limits.vyaw),
            head=self.head_now,
            triggers=snap.triggers,
        )

    def _zero(self) -> None:
        self._seq += 1
        self.stopped_upto = self._seq
        self.stop_epoch += 1
        self.stopped_until = self.now() + self.stop_latch_s
        self._gesture = None
        # A stop must not leave the neck still travelling toward a target nobody asked for
        # any more.
        self.head_target = self.head_now
        self.snapshot = Snapshot(seq=self._seq, at=self.now(), head=self.head_now)

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
                "fall_detection": self.fallen is not None,
                # The docs promise a token in four places. Saying which it actually is lets
                # `check`, `doctor` and the client all see when there is none, instead of a
                # missing token being indistinguishable from a working one.
                "auth": "token" if self.token else "none",
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
            "tick_age_ms": self._tick_age_ms(),
            "head": list(self.head_now),
            "head_target": list(self.head_target),
            "head_yaw_deg": math.degrees(self.head_now[HEAD_ORDER.index("head_yaw")]),
            "deadman_tripped": self.deadman_tripped,
            "stop_latched": self.now() < self.stopped_until,
            # so a client that missed a stop's reply can resynchronise from any state read
            # rather than having its commands dropped until the window expires
            "stop_epoch": self.stop_epoch,
            "pad_override": False,
            "unknowns": ["fall detection", "battery", "whether the pause took"],
        }

    def _tick_age_ms(self) -> int | None:
        return None if self._last_tick is None else int((self.now() - self._last_tick) * 1000)

    def health(self) -> dict[str, Any]:
        healthy = self.controller_built_at is not None
        reason = None if healthy else "the walk loop never asked for a controller"
        # A wedged loop is invisible from a rate: loop_hz, ticks and _last_tick are all
        # written by the control thread itself, so if it blocks inside a Feetech read the
        # server thread keeps answering "healthy, 50.0 Hz" forever while the duck stands
        # frozen with torque on — and the deadman, which lives in that same function, cannot
        # fire either. The clock is the one thing that keeps moving. This buys a diagnosis
        # and an abort, not a stop: nothing reaches a loop that is not reading.
        age = self._tick_age_ms()
        if healthy and age is not None and age > TICK_STALE_S * 1000:
            healthy = False
            reason = f"the walk loop has not ticked for {age / 1000:.2f}s"
        if self.paused:
            # Checked before the rate is reported, because a paused loop runs at ~10 Hz and
            # would otherwise be diagnosed as a starved Pi.
            healthy = False
            reason = (
                "the walk policy is paused (start_paused in duck_config.json). quackd cannot "
                "unpause it: upstream's only unpause is its gamepad's A button, and the "
                "bridge replaced that pad"
            )
        if self.fallen is True:
            healthy, reason = False, "the duck is down and this robot has no get-up policy"
        return {
            "healthy": healthy,
            "reason": reason,
            "paused": self.paused,
            "loop_hz": round(self.loop_hz, 1),
            "tick_age_ms": age,
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
    constructor whose side effects we have not read.

    Subclassed rather than instantiated bare: `object.__new__(Buttons)` gave an object with
    no `__getattr__`, so the net written so that "an attribute we did not anticipate must not
    raise mid-stride" lived only on `_Buttons` — which is reached when `mini_bdx_runtime` is
    absent, i.e. never on the robot. Nothing pins the owner's checkout to the commit we read,
    so upstream adding a button its loop then reads would raise inside the 50 Hz loop."""
    try:
        from mini_bdx_runtime.buttons import Buttons  # type: ignore[import-not-found]

        class _SafeButtons(Buttons):  # type: ignore[misc, valid-type]
            __getattr__ = _Buttons.__getattr__

        return object.__new__(_SafeButtons)
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
            conn, addr = self._listener.accept()
        except OSError:
            return
        log.info("client connected from %s:%s", *addr[:2])
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
        log.info("client disconnected (it %s driving)", "was" if client.commanded else "was not")
        if client.commanded:
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


def runtime_commit(script: str) -> str | None:
    """The commit of the Open_Duck_Mini_Runtime checkout this bridge is about to run.

    Every name the bridge borrows was read at one pinned commit, and nothing pins the
    *owner's* checkout to it. The hello has advertised a `runtime.commit` field since the
    protocol existed and nobody ever filled it in, so quackd could not tell an audited
    runtime from one six months ahead of it — which matters most for the class rebind the
    whole bridge stands on. Read here rather than asked for, because an operator should not
    have to know it.

    Best effort: no git, no checkout, or a tarball install all yield None, and None means
    unknown rather than matching."""
    import subprocess

    root = os.path.dirname(os.path.dirname(os.path.abspath(os.path.expanduser(script))))
    try:
        out = subprocess.run(
            ["git", "-C", root, "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    commit = out.stdout.strip()
    return commit or None


def read_token(path: str | None) -> str | None:
    """The bridge's token, or None if it genuinely has none.

    `os.path.exists()` was the wrong probe. The installer wrote the token 0600 root:root
    inside a 0700 root:root directory while the unit ran as another user, so traversing it
    raised EACCES, `exists()` swallowed that and returned False, and the bridge started with
    authentication silently off — indistinguishable, from the client's side, from a bridge
    that checked the token it was sent. Four places in the docs promise that token.

    So: no file is no token, and the daemon says so. An unreadable file is a configuration
    error and refuses to start. An empty one is not a token either — an empty string would
    otherwise authenticate every client that sent nothing."""
    if not path:
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            token = fh.read().strip()
    except FileNotFoundError:
        return None
    except OSError as e:
        raise SystemExit(
            f"cannot read the token file {path}: {e}. The service user has to be able to "
            "read it — check the owner and mode, or point --token-file somewhere it can. "
            "Refusing to start with authentication silently disabled."
        ) from e
    if not token:
        log.warning("%s is empty, so this bridge has no token at all", path)
        return None
    return token


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


def watchdog(
    core: BridgeCore, seconds: float = PATCH_WATCHDOG_S, server: Server | None = None
) -> None:
    """Exit if upstream never asks us for a controller.

    Polled rather than slept in one go, so a slow boot reads as a slow boot in the journal
    instead of as silence followed by a hard exit. `seconds <= 0` disables it."""
    if seconds <= 0:
        log.warning("the patch watchdog is disabled; a duck driven by a real pad will be silent")
        return

    def check() -> None:
        started = time.monotonic()
        warned = False
        while core.controller_built_at is None:
            waited = time.monotonic() - started
            if waited >= seconds:
                break
            if not warned and waited >= seconds / 2:
                warned = True
                log.warning(
                    "upstream has not asked for a controller after %.0fs of a %.0fs budget. "
                    "That is normal on a cold Pi (onnxruntime, the servo bus, a two second "
                    "settle); raise --patch-watchdog-s if your boot is slower than this.",
                    waited,
                    seconds,
                )
            time.sleep(1.0)
        if core.controller_built_at is not None:
            return
        # Close the socket first: for the whole of this window a client could connect, be
        # accepted, and believe it was driving something.
        if server is not None:
            with contextlib.suppress(Exception):
                server.stop()
        log.error(
            "the walk loop never constructed our controller after %.0fs. Most likely it is "
            "still starting up, in which case raise --patch-watchdog-s. Otherwise it is "
            "reading a real gamepad, or upstream renamed XBoxController. Either way this "
            "socket controls nothing, so exiting rather than pretending.",
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
    # Narrowing these is the obvious first-power-on precaution; widening them past what
    # upstream's own pad allows is not something quackd should quietly do on the owner's
    # behalf. Refused rather than silently clamped, so the number in the unit is the number
    # in force.
    for name, asked, bound in (
        ("--max-vx", args.max_vx, VX[1]),
        ("--max-vy", args.max_vy, VY[1]),
        ("--max-vyaw", args.max_vyaw, VYAW[1]),
    ):
        if abs(asked) > bound + 1e-9:
            raise SystemExit(
                f"{name}={asked} is above the runtime's own clamp of {bound}. quackd never "
                "asks this robot for more than its own gamepad could."
            )
    if not 0.0 < args.head_safety <= 1.0:
        raise SystemExit(
            f"--head-safety={args.head_safety} must be in (0, 1]: it is the fraction of "
            "upstream's head range quackd will use, not a multiplier on it."
        )
    limits = Limits(
        vx=(-abs(args.max_vx), abs(args.max_vx)),
        vy=(-abs(args.max_vy), abs(args.max_vy)),
        vyaw=(-abs(args.max_vyaw), abs(args.max_vyaw)),
        head_enabled=bool(args.enable_head),
        head_safety=args.head_safety,
    )
    token = read_token(args.token_file)
    core = BridgeCore(
        limits=limits,
        capabilities=caps,
        deadman_s=args.deadman_ms / 1000.0,
        token=token,
        camera_url=args.camera_url,
        runtime={
            "script": args.script,
            "start_paused": bool(config.get("start_paused")),
            "commit": runtime_commit(args.script) if args.script else None,
        },
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
        "--patch-watchdog-s",
        type=float,
        default=PATCH_WATCHDOG_S,
        help="how long upstream may take to ask for a controller before this gives up. It "
        "covers onnxruntime, the servo bus and a two second settle on a cold Pi. 0 disables",
    )
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
        if core.paused:
            # Refusing here is the whole fix. quackd cannot unpause: upstream toggles pause
            # on its gamepad's A button and this process replaced that pad, so a bridge
            # started paused can never walk. Worse, a paused loop sleeps 0.1 s a tick, so it
            # reports ~10 Hz and quackd's heartbeat kills the session in under a second
            # blaming a starved Pi. Better to not start than to be diagnosed wrong all
            # evening. Deliberately not a `duck.resume` that blind-pulses A: it is a toggle,
            # the bridge cannot read upstream's real pause bit, and a wrong guess starts a
            # walk policy on a biped that cannot get up.
            log.error(
                "duck_config.json has start_paused true, so upstream's loop will sit in its "
                "pause branch and quackd has no way to release it — the A button belongs to "
                "the gamepad this bridge replaced. Set start_paused false in %s and start "
                "again.",
                args.duck_config,
            )
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
        watchdog(core, args.patch_watchdog_s, server)
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
