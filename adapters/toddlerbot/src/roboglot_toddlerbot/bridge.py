"""EXPERIMENTAL: a ToddlerBot, through the daemon Roboglot ships for it.

This robot has no network API at all, so there is nothing to speak to until Roboglot puts
something there. `bridge/toddlerbot/` is that something, and this is its client: line
delimited JSON-RPC over TCP, standard library only, the same shape the Open Duck Mini's
bridge uses (ADR-0024, extended by ADR-0028).

The protocol is Roboglot's own at both ends, so it is deliberately **not** in `upstream_api.py`:
there is no upstream to verify it against and citing one would be a false citation. What
`upstream_api.py` carries instead is every assumption the daemon makes about the robot.

Two things are different here from every other adapter Roboglot has, and both come from the
same fact: **on this body, silence means hold forever, not stop.**

- The daemon owns the fifty hertz loop and its own deadman, because a verb is episodic and a
  humanoid frozen mid-stride while an LLM thinks is a humanoid on the floor. Roboglot sends
  intents, not motor targets.
- `stop` cannot mean zero velocity. There is no velocity at the hardware boundary at all: the
  command is an absolute pose. So `stop` means *hold the last verified-good measured pose*,
  and the daemon is the one that knows which pose that was.

Never run against a robot by us.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import contextlib
import ipaddress
import json
import logging
import math
import os
import re
import time
from collections.abc import AsyncIterator
from io import BytesIO
from typing import Any

from PIL import Image, UnidentifiedImageError

from roboglot.transport.base import Ack, HeartbeatError, Intent, RobotState, TransportError
from roboglot_toddlerbot.verbs import MOTIONS, look_degrees

log = logging.getLogger(__name__)

STATUS = "EXPERIMENTAL - Roboglot's own protocol, never run against a robot"

PROTOCOL = "roboglot-toddlerbot-bridge"
PROTOCOL_VERSION = 1
JSONRPC_VERSION = "2.0"
DEFAULT_PORT = 9873
"""The Open Duck Mini takes 9871 for its bridge and 9872 for its camera daemon, and
SECURITY.md tells people to tunnel that pair, so this robot starts after both."""

TOKEN_ENV = "ROBOGLOT_TODDLERBOT_TOKEN"

HELLO = "bot.hello"
COMMAND = "bot.command"  # notification, re-sent while a verb runs; feeds the daemon's deadman
STOP = "bot.stop"
STATE = "bot.state"
HEALTH = "bot.health"
LOOK = "bot.look"
STAND = "bot.stand"
PERFORM = "bot.perform"
GRIP = "bot.grip"
FRAME = "bot.frame"
KEEPALIVE = "bot.keepalive"

KEEPALIVE_S = 0.15
"""How often the client says it is still there.

The daemon's deadman fires after half a second of silence, and on this body that is
not a stop but a slew to the safe pose, so a verb that took longer than half a second
without sending anything would be cancelled underneath itself. `stand` takes three.

Roboglot's own `Heartbeat` cannot be the signal: its period is a run setting rather than
the manifest's, and it defaults to the same half second the deadman uses. So the
transport keeps its own timer, the way the Microduck's re-sent `robot.move` does, and
reading state or a frame deliberately does not count as being alive."""

MIN_LOOP_HZ = 40.0
"""The daemon runs at fifty. Much below that and the gait phase drifts, so Roboglot says so
rather than letting a slow robot look like a healthy one."""
STALE_LIMIT_MS = 1000.0

READER_LIMIT = 2**22
"""The longest line the reader takes from the daemon, 4 MiB, where asyncio's default is 64 KiB.

A frame comes as one line of base64 (`bot.frame`), so the default stopped at a JPEG of about
48 KB, and the camera's 640x480 can pass that at the quality the daemon encodes at. Over it,
`readline` raised out of the pump, which died without a word: every request after it waited
out its timeout on a socket nobody read, while the keepalive went on telling the daemon all was
well. 4 MiB holds a 1080p frame of pure noise at that quality, and `_read_loop` skips a line
longer still instead of ending the link on it."""


def is_loopback(host: str) -> bool:
    """Whether `host` names this machine alone: `localhost`, or an address in 127.0.0.0/8 or
    ::1. Any other name can resolve to anything, so it counts as the network."""
    name = host.strip().lower()
    if name == "localhost":
        return True
    try:
        return ipaddress.ip_address(name).is_loopback
    except ValueError:
        return False


NAME = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")
"""A build name as a manifest writes its ids (the slug rule in `roboglot.adapters.manifest`),
which every build upstream ships fits: `toddlerbot_2xm_gripper` is twenty-two characters."""
VERSION = re.compile(r"[0-9A-Za-z.+!_-]{1,64}")
"""A version as PEP 440 writes one, no longer than a name."""
COMMIT = re.compile(r"[0-9a-f]{7,40}")
"""A git commit: its forty hex digits, or git's shortest abbreviation of seven."""
ENVELOPE_KEYS = ("max_vx", "max_vy", "max_wz")


def _number(value: Any) -> bool:
    """A real number off the wire: a bool is an int to Python, and is not one here."""
    return isinstance(value, int | float) and not isinstance(value, bool)


def _unfit_hello(hello: dict[str, Any]) -> str | None:
    """What makes a daemon's hello unfit to repeat to the pilot, or None. Each field is checked
    only for being the kind of thing it is: absent stays absent, as it always was."""
    remote = hello.get("protocol_version")
    if remote is not None and not (_number(remote) and float(remote).is_integer()):
        return "reported a protocol version that is not a number"
    robot = hello.get("robot")
    if robot and not (isinstance(robot, str) and NAME.fullmatch(robot)):
        return "reported a robot that is not a build name"
    motors = hello.get("motors")
    if motors and not (_number(motors) and float(motors).is_integer() and motors > 0):
        return "reported a motor count that is not a whole number"
    caps = hello.get("capabilities")
    if caps is not None and not isinstance(caps, dict):
        return "reported its capabilities as something other than a table"
    envelope = (caps or {}).get("walk_envelope")
    if envelope is not None and not (
        isinstance(envelope, dict)
        and all(
            _number(envelope.get(k)) and math.isfinite(envelope[k]) and envelope[k] >= 0
            for k in ENVELOPE_KEYS
            if k in envelope
        )
    ):
        return "reported a walk envelope that is not numbers of zero or more"
    return None


def _offered_motions(reported: Any) -> tuple[str, ...]:
    """The motions a daemon reported that Roboglot offers, in the daemon's order, once each.

    The names become the `perform` tool the pilot reads, so only Roboglot's own may: a name it
    does not know, or anything that is not a name at all, is left out and counted in the log."""
    if not isinstance(reported, list | tuple):
        return ()
    offered = tuple(dict.fromkeys(m for m in reported if isinstance(m, str) and m in MOTIONS))
    if len(offered) != len(reported):
        log.warning(
            "the bridge reported %d motion(s) Roboglot does not offer; leaving them out",
            len(reported) - len(offered),
        )
    return offered


def parse_address(address: str) -> tuple[str, int]:
    text = address[len("tcp://") :] if address.startswith("tcp://") else address
    if "://" in text:
        raise TransportError(f"unknown address {address!r}; use tcp://host:port")
    host, _, port = text.rpartition(":")
    if not host:
        return text, DEFAULT_PORT
    if not port.isdigit():
        raise TransportError(f"bad address {address!r}; expected tcp://host:port")
    return host, int(port)


def _yaw_deg(p: dict[str, Any]) -> float:
    return look_degrees(float(p.get("x", 1.0)), float(p.get("y", 0.0)), float(p.get("z", 0.0)))[0]


def _pitch_deg(p: dict[str, Any]) -> float:
    return look_degrees(float(p.get("x", 1.0)), float(p.get("y", 0.0)), float(p.get("z", 0.0)))[1]


class ToddlerBotBridge:
    name = "bridge"

    def __init__(
        self,
        address: str | None = None,
        *,
        token: str | None = None,
        request_timeout_s: float = 3.0,
    ) -> None:
        self.address = address or f"tcp://toddlerbot.local:{DEFAULT_PORT}"
        # Whatever token the daemon was started with. It travels in the handshake and never in
        # the address, because addresses are printed and land in transcripts.
        self.token = token or os.environ.get(TOKEN_ENV) or None
        self.request_timeout_s = request_timeout_s
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._pump: asyncio.Task[None] | None = None
        self._alive: asyncio.Task[None] | None = None
        self._pending: dict[int, asyncio.Future[Any]] = {}
        self._notifications: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=256)
        self._next_id = 1
        self._last_state: dict[str, Any] | None = None
        self._t0 = time.monotonic()
        self.hello: dict[str, Any] | None = None
        #: What the daemon said is actually on this robot. The adapter narrows its manifest
        #: from these, so a build with no walk checkpoint loses locomotion entirely.
        self.camera_available = False
        self.neck_available = True
        self.gripper_available = False
        self.walk_available = False
        #: The motions the daemon actually loaded, which is at most the five Roboglot
        #: offers and fewer if a keyframe file was missing or unreadable.
        self.motions: tuple[str, ...] = ()
        #: The velocity envelope the loaded walk checkpoint was trained on, read
        #: from its own config rather than assumed.
        self.walk_envelope: dict[str, float] | None = None
        self.deadman = False
        self.robot_name = "toddlerbot_2xc"
        self.motors = 30
        self.daemon_version: str | None = None
        self.upstream_commit: str | None = None
        #: Why the last `stop` did not reach the daemon, or None if it did. The stop is what the
        #: pilot is told to reach for when something is wrong, and it is asked for most often
        #: when the link is what is wrong, so a stop that never landed must not be recorded as
        #: one that did. The core `stop` verb and the executor's brake both read this.
        self.stop_error: str | None = None
        #: Set when a daemon reached over an address that is not loopback says it checks no
        #: token, which the connect cannot otherwise tell from one that checked it.
        #: `roboglot doctor` prints it.
        self.auth_warning: str | None = None
        #: Why the keepalive is not being sent, or None while it is. A stop that did not land
        #: leaves the robot doing whatever the stop was meant to end, and the keepalive would
        #: then be the only thing keeping it at it. So it goes quiet: the daemon's own deadman
        #: takes the robot to its safe pose within its window, the heartbeat says why, and the
        #: next command the daemon accepts starts the keepalive again.
        self.keepalive_withheld: str | None = None

    # ── wire ────────────────────────────────────────────────────────────────────────

    async def connect(self) -> None:
        host, port = parse_address(self.address)
        try:
            self._reader, self._writer = await asyncio.open_connection(
                host, port, limit=READER_LIMIT
            )
        except OSError as e:
            raise TransportError(
                f"cannot connect to the bridge at {self.address}: {e}. Is "
                "roboglot-toddlerbot-bridge running on the robot?"
            ) from e
        self._pump = asyncio.create_task(self._read_loop(), name="roboglot-toddlerbot-pump")
        hello: dict[str, Any] = {"protocol": PROTOCOL, "protocol_version": PROTOCOL_VERSION}
        if self.token:
            hello["token"] = self.token
        try:
            result = await self.request(HELLO, hello)
        except TransportError as e:
            await self.close()
            if "2:" in str(e) and not self.token:
                raise TransportError(
                    f"the bridge at {self.address} wants a token and none was given. Pass the "
                    f"one its daemon was started with, with --token or {TOKEN_ENV}. Original "
                    f"answer: {e}"
                ) from e
            raise
        self.hello = result if isinstance(result, dict) else {}
        # What the daemon says about itself reaches the pilot in Roboglot's own voice: the robot
        # name opens the body summary, and the motions become the `perform` tool. So nothing is
        # repeated as it came. A value of the wrong kind refuses the connect, without quoting
        # it, and a motion Roboglot does not offer is left out (`_offered_motions`).
        unfit = _unfit_hello(self.hello)
        if unfit is not None:
            await self.close()
            raise TransportError(
                f"the bridge at {self.address} {unfit}; refusing rather than repeating it"
            )
        remote = self.hello.get("protocol_version")
        if remote is not None and int(remote) != PROTOCOL_VERSION:
            await self.close()
            raise TransportError(
                f"the bridge speaks {PROTOCOL} v{remote}, Roboglot speaks v{PROTOCOL_VERSION}; "
                "refusing rather than guessing. Update whichever is older"
            )
        caps = self.hello.get("capabilities") or {}
        self.camera_available = bool(caps.get("camera", False))
        self.neck_available = bool(caps.get("neck", True))
        self.gripper_available = bool(caps.get("gripper", False))
        # No walk checkpoint means no locomotion at all: it is an ONNX artifact upstream does
        # not publish, so the adapter drops move, go_to and approach_and rather than gate them.
        self.walk_available = bool(caps.get("walk", False))
        self.motions = _offered_motions(caps.get("motions"))
        envelope = caps.get("walk_envelope")
        self.walk_envelope = (
            {k: float(v) for k, v in envelope.items()} if isinstance(envelope, dict) else None
        )
        self.deadman = bool(caps.get("deadman", False))
        self.robot_name = str(self.hello.get("robot") or self.robot_name)
        self.motors = int(self.hello.get("motors") or self.motors)
        version = self.hello.get("daemon_version")
        self.daemon_version = (
            version if isinstance(version, str) and VERSION.fullmatch(version) else None
        )
        self._alive = asyncio.create_task(self._keepalive(), name="roboglot-toddlerbot-keepalive")
        upstream = self.hello.get("upstream")
        commit = upstream.get("commit") if isinstance(upstream, dict) else None
        self.upstream_commit = (
            commit if isinstance(commit, str) and COMMIT.fullmatch(commit) else None
        )
        # Loopback is where the ssh tunnel the docs recommend ends; anything else is a network,
        # and a daemon on one that checks no token can be driven by anything that reaches it.
        if self.hello.get("auth") == "none" and not is_loopback(host):
            self.auth_warning = (
                f"the robot's daemon at {self.address} asks for no token, so anything that "
                "can reach that address can drive the robot. Start it with --token-file, or "
                "keep it on 127.0.0.1 and reach it through an ssh tunnel."
            )
            log.warning("%s", self.auth_warning)

    async def _keepalive(self) -> None:
        """Say we are here, often enough that the deadman does not fire on a verb, except while
        a stop is unconfirmed (`keepalive_withheld`)."""
        while self._writer is not None and not self._writer.is_closing():
            if self.keepalive_withheld is None:
                with contextlib.suppress(Exception):
                    await self.notify(KEEPALIVE, {})
            await asyncio.sleep(KEEPALIVE_S)

    async def close(self) -> None:
        if self._alive is not None:
            self._alive.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._alive
            self._alive = None
        # The final stop goes FIRST, while the reader is still running. `request()` waits on a
        # future that only the read loop can resolve, so cancelling the pump before asking
        # meant every close sat out the full request timeout and never saw the answer.
        if self._writer is not None:
            # never a torque-off from here: Roboglot asks the daemon to hold, and the daemon
            # decides what leaving looks like. Dropping the socket is not a shutdown.
            with contextlib.suppress(Exception):
                await self.stop()
        if self._pump is not None:
            self._pump.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._pump
            self._pump = None
        if self._writer is not None:
            self._writer.close()
            with contextlib.suppress(Exception):
                await self._writer.wait_closed()
            self._writer = None
        self._reader = None

    async def _read_loop(self) -> None:
        """Every answer comes through here, so nothing one line does may end it. A pump that died
        left every request after it to wait out its timeout on a socket nobody read, while the
        keepalive went on feeding the daemon's deadman."""
        assert self._reader is not None
        while True:
            try:
                line = await self._reader.readline()
            except ValueError as e:
                # A line over READER_LIMIT. `readline` has already let go of it, so the next read
                # starts past it, or at a tail of it that `_take_line` throws away.
                log.warning("skipped a line from the bridge over %d bytes: %s", READER_LIMIT, e)
                continue
            except OSError as e:
                self._fail_pending(f"the link to the bridge failed: {e}")
                return
            if not line:
                self._fail_pending("the bridge closed the connection")
                return
            try:
                self._take_line(line)
            except Exception as e:  # one line Roboglot mishandles must not cut the robot off
                log.warning("skipped a line from the bridge that Roboglot could not read: %r", e)

    def _fail_pending(self, why: str) -> None:
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(TransportError(why))

    def _take_line(self, line: bytes) -> None:
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            return
        if "id" in msg and msg["id"] is not None and ("result" in msg or "error" in msg):
            pending = self._pending.pop(int(msg["id"]), None)
            if pending is not None and not pending.done():
                if "error" in msg:
                    err = msg["error"]
                    pending.set_exception(
                        TransportError(f"{err.get('code')}: {err.get('message')}")
                    )
                else:
                    pending.set_result(msg.get("result"))
        elif "method" in msg:
            if msg["method"] == STATE:
                self._last_state = msg.get("params") or {}
            with contextlib.suppress(asyncio.QueueFull):
                self._notifications.put_nowait(msg)

    def _write(self, obj: dict[str, Any]) -> None:
        if self._writer is None:
            raise TransportError("not connected to the bridge")
        self._writer.write((json.dumps(obj, separators=(",", ":")) + "\n").encode())

    async def request(self, method: str, params: dict[str, Any] | None = None) -> Any:
        req_id = self._next_id
        self._next_id += 1
        fut: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._pending[req_id] = fut
        msg: dict[str, Any] = {"jsonrpc": JSONRPC_VERSION, "id": req_id, "method": method}
        if params is not None:
            msg["params"] = params
        # The write, the drain and the answer under one deadline. A drain awaited before the
        # timeout began waited with no deadline at all on a send buffer a silent peer had let
        # fill, and the heartbeat's question and the stop waited in it with everything else.
        try:
            async with asyncio.timeout(self.request_timeout_s):
                self._write(msg)
                assert self._writer is not None
                await self._writer.drain()
                return await fut
        except TimeoutError as e:
            raise TransportError(f"{method}: no answer within {self.request_timeout_s:g}s") from e
        finally:
            self._pending.pop(req_id, None)

    async def notify(self, method: str, params: dict[str, Any]) -> None:
        """A message with no answer, its drain under the same deadline a request has."""
        try:
            async with asyncio.timeout(self.request_timeout_s):
                self._write({"jsonrpc": JSONRPC_VERSION, "method": method, "params": params})
                assert self._writer is not None
                await self._writer.drain()
        except TimeoutError as e:
            raise TransportError(
                f"{method}: the link took nothing within {self.request_timeout_s:g}s"
            ) from e

    # ── protocol ────────────────────────────────────────────────────────────────────

    async def get_frame(self) -> Image.Image | None:
        """The camera lives on the daemon's own thread, because upstream's observation has no
        image field at all and reading a camera on the control thread would cost ticks."""
        if not self.camera_available:
            return None
        try:
            result = await self.request(FRAME)
        except TransportError:
            return None
        payload = (result or {}).get("jpeg") if isinstance(result, dict) else None
        if not payload:
            return None
        try:
            img = Image.open(BytesIO(base64.b64decode(payload, validate=True)), formats=["JPEG"])
            img.load()
        except (
            binascii.Error,
            ValueError,
            OSError,
            UnidentifiedImageError,
            Image.DecompressionBombError,
        ):
            return None
        return img.convert("RGB")

    def _stale_ms(self) -> float:
        state = self._last_state or {}
        stamped = state.get("t")
        if stamped is None:
            return float("inf")
        return max(0.0, (time.monotonic() - self._t0 - float(stamped)) * 1000.0)

    async def get_state(self) -> RobotState:
        result = await self.request(STATE)
        state = result if isinstance(result, dict) else {}
        self._last_state = state
        extras = {
            "joints": state.get("joints") or {},
            "neck": state.get("neck") or {},
            "head_yaw_deg": float((state.get("neck") or {}).get("yaw_deg") or 0.0),
            "holding": state.get("holding") or {},
            "robot": self.robot_name,
            "calibrated": bool(state.get("calibrated", False)),
            "moving": bool(state.get("moving", False)),
            "loop_hz": state.get("loop_hz"),
            "deadman_tripped": bool(state.get("deadman_tripped", False)),
            "walk_policy": self.walk_available,
            "stale_ms": round(float(state.get("age_ms") or 0.0), 1),
            "stale_limit_ms": STALE_LIMIT_MS,
            "tilt_deg": state.get("tilt_deg"),
        }
        return RobotState(
            t=float(state.get("t") or 0.0),
            policy=str(state.get("policy") or "idle"),
            posture=state.get("posture") or "unknown",
            fallen=bool(state.get("fallen", False)),
            # nothing on this robot reports a battery to Python
            battery_percent=None,
            # no odometry anywhere: motor positions and an orientation, and nothing else
            x=None,
            y=None,
            theta=None,
            holding=any((state.get("holding") or {}).values()),
            extras=extras,
        )

    async def send_intent(self, intent: Intent) -> Ack:
        p = intent.params
        try:
            match intent.kind:
                case "move":
                    if not self.walk_available:
                        return Ack(
                            accepted=False,
                            reason="this robot has no walk policy staged, so it cannot walk",
                        )
                    # all three keys or none: the walk policy indexes them unconditionally
                    return self._answered(
                        await self.request(
                            COMMAND,
                            {
                                "walk_x": float(p.get("vx", 0.0)),
                                "walk_y": float(p.get("vy", 0.0)),
                                "walk_turn": float(p.get("wz", 0.0)),
                            },
                        )
                    )
                case "stop":
                    ack = self._answered(await self.request(STOP))
                    if not ack.accepted:
                        self.keepalive_withheld = ack.reason or "the daemon refused the stop"
                    return ack
                case "look":
                    if not self.neck_available:
                        return Ack(accepted=False, reason="this build has no neck")
                    return self._answered(
                        await self.request(
                            LOOK,
                            {
                                # the intent carries a direction; the wire carries
                                # the two servo angles the daemon actually commands
                                "yaw_deg": _yaw_deg(p),
                                "pitch_deg": _pitch_deg(p),
                            },
                        )
                    )
                case "gripper":
                    if not self.gripper_available:
                        return Ack(accepted=False, reason="this build has no grippers")
                    return self._answered(
                        await self.request(
                            GRIP,
                            {"side": str(p.get("side", "right")), "close": not p.get("open", True)},
                        )
                    )
                case "do":
                    skill = str(p.get("skill", ""))
                    if skill == "stand":
                        return self._answered(await self.request(STAND))
                    kind, _, name = skill.partition(":")
                    if kind == "motion" and name:
                        return self._answered(await self.request(PERFORM, {"motion": name}))
                    return Ack(accepted=False, reason=f"unknown skill {skill!r}")
                case "enable":
                    if not p.get("on", True):
                        # there is no way back: enable_motors is not bound to Python at this
                        # pin, so a torque-off would be one way and Roboglot never sends one
                        return Ack(accepted=False, reason="Roboglot never limps a robot")
                    return Ack()
                case _:
                    return Ack(accepted=False, reason=f"a ToddlerBot cannot {intent.kind}")
        except TransportError as e:
            if intent.kind == "stop":
                self.keepalive_withheld = str(e)
            return Ack(accepted=False, reason=str(e))

    def _answered(self, result: Any) -> Ack:
        """The daemon's answer to a command, as an Ack. A command it accepted is one it is
        carrying out for this connection, so the keepalive starts again if an unconfirmed stop
        had held it back."""
        ack = _ack(result)
        if ack.accepted:
            self.keepalive_withheld = None
        return ack

    async def subscribe(self, topic: str) -> AsyncIterator[dict[str, Any]]:  # type: ignore[override]
        while self._writer is not None:
            msg = await self._notifications.get()
            yield {"topic": topic, **(msg.get("params") or {})}

    async def heartbeat(self) -> None:
        if self.keepalive_withheld is not None:
            raise HeartbeatError(
                f"the last stop did not land ({self.keepalive_withheld}), so Roboglot has "
                "stopped keeping the robot alive and the daemon's deadman takes it to its safe "
                "pose"
            )
        try:
            result = await self.request(HEALTH)
        except TransportError as e:
            raise HeartbeatError(f"toddlerbot bridge: {e}") from e
        health = result if isinstance(result, dict) else {}
        loop_hz = float(health.get("loop_hz") or 0.0)
        if loop_hz and loop_hz < MIN_LOOP_HZ:
            raise HeartbeatError(
                f"the robot's control loop has fallen to {loop_hz:.0f} Hz (needs "
                f"{MIN_LOOP_HZ:.0f}). Its gait is timed against fifty, so it is no longer "
                "walking the way it was trained to"
            )
        if not health.get("ok", True):
            raise HeartbeatError(str(health.get("reason") or "the robot reported not ok"))

    async def stop(self) -> None:
        """Hold, never limp.

        There is no zero velocity on this body and torque-off is a fall, so the daemon holds
        the last verified-good measured pose. Safe to call when the link has already gone: the
        daemon's own deadman is what is holding the robot at that point.

        A stop that got no answer, or that the daemon refused, is recorded in `stop_error`
        rather than raised, because a stop must never itself take a run down. It also holds the
        keepalive back (`keepalive_withheld`), so the daemon's deadman, not this client, decides
        what the robot does next; a stop that landed lets it go on."""
        try:
            result = await self.request(STOP)
        except Exception as e:
            self.stop_error = str(e) or type(e).__name__
        else:
            if isinstance(result, dict) and result.get("accepted") is False:
                reason = result.get("reason") or "no reason given"
                self.stop_error = f"the daemon refused it: {reason}"
            else:
                self.stop_error = None
        self.keepalive_withheld = self.stop_error

    def now(self) -> float:
        return time.monotonic() - self._t0

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)


def _ack(result: Any) -> Ack:
    if isinstance(result, dict) and not result.get("accepted", True):
        return Ack(accepted=False, reason=str(result.get("reason") or "refused"))
    return Ack()


__all__ = [
    "COMMAND",
    "DEFAULT_PORT",
    "HELLO",
    "MIN_LOOP_HZ",
    "PROTOCOL",
    "PROTOCOL_VERSION",
    "STATUS",
    "STOP",
    "TOKEN_ENV",
    "ToddlerBotBridge",
    "parse_address",
]
