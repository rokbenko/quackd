"""quackd's ToddlerBot daemon: the seven things upstream does not do.

This is the file that earns the `bridge` backend's 🧪. Upstream protects nothing on this
robot: no clamp, no rate limit, no watchdog, no reset, no e-stop, and a shutdown that
disables torque and drops a standing humanoid. Every test here is about one of the
mechanisms quackd added because of that, and the protocol is driven end to end against the
real daemon over a real loopback socket, exactly as `test_open_duck_daemon.py` does.

What it cannot prove: that any of it is right on 3 kg of servos. Nothing here has run on a
robot, and `docs/adapters/status.md` says so.
"""

from __future__ import annotations

import asyncio
import importlib.util
import socket
import sys
import threading
import time
from types import ModuleType

import numpy as np
import pytest

from quackd.transport.base import HeartbeatError, Intent, TransportError
from quackd_toddlerbot import ToddlerBotAdapter
from quackd_toddlerbot.bridge import (
    DEFAULT_PORT,
    PROTOCOL,
    PROTOCOL_VERSION,
    ToddlerBotBridge,
)
from tests.conftest import REPO

DAEMON = REPO / "bridge" / "toddlerbot" / "quackd_toddlerbot_bridge.py"


def load_daemon() -> ModuleType:
    """It lives outside the package on purpose: quackd's core must never import it."""
    spec = importlib.util.spec_from_file_location("quackd_toddlerbot_bridge", DAEMON)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


D = load_daemon()


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _daemon(**kwargs: object) -> object:
    robot = D.FakeRobot()
    return D.Daemon(robot, D.FakeSim(robot), fake=True, **kwargs)  # type: ignore[arg-type]


def _ticks(d: object, n: int) -> None:
    """Run `n` ticks with the client kept alive.

    `tick()` reads the real clock and trips the deadman after `DEADMAN_S`, and there is no
    clock injection here, so a few hundred ticks on a loaded machine could cross half a
    second of wall time and silently start slewing to the safe pose. Every test that is not
    about the deadman itself drives the loop through this.
    """
    for _ in range(n):
        d.touch()  # type: ignore[attr-defined]
        d.tick()  # type: ignore[attr-defined]


async def _until(predicate: object, limit_s: float = 5.0) -> None:
    """The daemon's loop is a real thread on a real clock, so these waits are real waits."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + limit_s
    while loop.time() < deadline:
        if predicate():  # type: ignore[operator]
            return
        await asyncio.sleep(0.01)
    raise AssertionError("the daemon never reached the expected state")


# ── 1. the clamp, because set_motor_target has none ────────────────────────────────────


def test_a_command_past_the_joint_limits_is_clamped() -> None:
    """Upstream never reads motor_limits and the motors are in multi-turn mode, so the
    firmware limits are off too. This clamp is the only thing there is."""
    lo, hi = np.array([-2.0, -2.0], np.float32), np.array([2.0, 2.0], np.float32)
    assert list(D.clamp_to_limits(np.array([9.0, -9.0], np.float32), lo, hi)) == [2.0, -2.0]


def test_no_joint_may_move_far_in_one_tick() -> None:
    """A position command on this body is a full-torque snap, so the difference between a
    slew and a jump is the difference between a move and a bang."""
    current = np.zeros(3, np.float32)
    stepped = D.rate_limit(np.ones(3, np.float32) * 5.0, current)
    assert float(np.max(stepped)) == pytest.approx(D.MAX_STEP_RAD)
    # and it is symmetric
    stepped = D.rate_limit(np.ones(3, np.float32) * -5.0, current)
    assert float(np.min(stepped)) == pytest.approx(-D.MAX_STEP_RAD)


def test_the_daemon_clamps_and_rate_limits_every_tick() -> None:
    d = _daemon()
    d.tick()  # the first reading seeds the target; the step is measured from there
    d.command.hold(np.ones(d.robot.nu, np.float32) * 50.0)
    was = float(np.max(d.target))
    d.tick()
    # the STEP is what is limited, not the absolute value: the daemon seeds its target from
    # the first reading, so it starts wherever the robot was rather than at zero
    assert float(np.max(d.target)) - was == pytest.approx(D.MAX_STEP_RAD)
    _ticks(d, 500)
    assert float(np.max(d.target)) <= 2.0, "the joint limit holds however long it is pushed"


# ── 2. the all-zeros detector, because a dropped read looks like a valid one ────────────


def test_an_all_zeros_reading_is_refused_rather_than_believed() -> None:
    """With zero retries a comm failure returns the pre-zeroed buffer, indistinguishable
    from every joint genuinely at zero. Believing it commands a full-scale move to zero."""
    assert D.looks_like_a_dropped_read(np.zeros(4), np.zeros(4))
    assert not D.looks_like_a_dropped_read(np.array([0.0, 0.1, 0.0, 0.0]), np.zeros(4))


def test_a_simulated_body_at_its_home_pose_is_believed_rather_than_refused() -> None:
    """The guard above is about a serial bus, and a simulator has none.

    Upstream's MuJoCo body starts with every motor at zero and every velocity at zero, which
    is exactly the buffer a failed bulk read hands back. Applying the guard there refused the
    first reading, and then every reading, because nothing moved to make one look different:
    `observe` returned None on every tick, `plan` was never reached, and `stand` reported
    itself moving for as long as anybody waited without turning a joint. The nightly contract
    job had never once passed because of it.

    So the caller says whether these numbers came off a bus. Hardware does, and keeps the
    refusal. A simulator does not, and is believed."""
    on_a_bus = _daemon()
    on_a_bus.command.hold(np.ones(on_a_bus.robot.nu, np.float32) * 0.5)  # type: ignore[attr-defined]
    _ticks(on_a_bus, 200)
    settled = on_a_bus.safe.pose.copy()  # type: ignore[attr-defined]
    before = on_a_bus.safe.rejected  # type: ignore[attr-defined]
    on_a_bus.sim.drop_next = True  # type: ignore[attr-defined]
    on_a_bus.tick()  # type: ignore[attr-defined]
    assert on_a_bus.safe.rejected == before + 1, "hardware still refuses the zeroed buffer"  # type: ignore[attr-defined]
    assert np.allclose(on_a_bus.safe.pose, settled)  # type: ignore[attr-defined]

    no_bus = _daemon(bus=False)
    no_bus.command.hold(np.ones(no_bus.robot.nu, np.float32) * 0.5)  # type: ignore[attr-defined]
    _ticks(no_bus, 200)
    assert float(np.max(no_bus.safe.pose)) > 0.1, "the fake body moved"  # type: ignore[attr-defined]
    no_bus.sim.drop_next = True  # type: ignore[attr-defined]
    no_bus.tick()  # type: ignore[attr-defined]
    assert no_bus.safe.rejected == 0, "a simulator has no dropped read to guard against"  # type: ignore[attr-defined]
    assert np.allclose(no_bus.safe.pose, 0.0), "and an all-zeros pose is just a pose"  # type: ignore[attr-defined]


def test_the_last_good_pose_survives_a_dropped_read() -> None:
    d = _daemon()
    d.command.hold(np.ones(d.robot.nu, np.float32) * 0.5)
    _ticks(d, 200)
    settled = d.safe.pose.copy()
    assert float(np.max(settled)) > 0.1, "the fake body moved"

    # a body genuinely at all zeros is indistinguishable from a dropped packet, so the very
    # first reading of one is refused too. That is the honest side of this trade.
    before = d.safe.rejected
    d.sim.drop_next = True
    d.tick()
    assert d.safe.rejected == before + 1
    assert np.allclose(d.safe.pose, settled), "a dropped read never becomes the held pose"


def test_a_controller_fault_is_survived_rather_than_raised() -> None:
    """A per-controller fault arrives as a bare KeyError because the C++ swallows it and
    inserts an empty map. It is a hardware fault, not a transient."""
    d = _daemon()
    d.tick()  # one good reading first, so there IS a last good pose to keep commanding
    before = d.sim.writes

    def explode() -> None:
        raise KeyError("pos")

    d.sim.get_observation = explode  # type: ignore[method-assign]
    d.tick()  # must not raise
    assert d.sim.writes > before, "the loop kept commanding the last good pose"


def test_a_daemon_that_has_never_read_the_robot_commands_nothing() -> None:
    """The other half of the same rule. Before the first reading arrives the target is a
    guess, and writing a guess to a servo bus is a full-scale jump from wherever the robot
    actually is."""
    d = _daemon()

    def explode() -> None:
        raise KeyError("pos")

    d.sim.get_observation = explode  # type: ignore[method-assign]
    for _ in range(10):
        d.tick()
    assert not d.seeded
    assert d.sim.writes == 0, "nothing was commanded before the robot was ever read"


# ── 3. the safe pose, because no reset exists anywhere upstream ────────────────────────


def test_the_waist_untwists_before_anything_else_moves() -> None:
    """Upstream's own reset rule: a twisted waist is straightened first, on its own."""
    order_len = 6
    waist = np.array([0, 1])
    current = np.zeros(order_len, np.float32)
    current[0] = 1.2  # well past the half radian threshold
    current[3] = 0.9
    goal = np.ones(order_len, np.float32)
    staged = D.waist_first(goal, current, waist)
    assert staged[0] == 0.0 and staged[1] == 0.0, "the waist is driven to zero"
    assert staged[3] == pytest.approx(0.9), "everything else holds where it is"


def test_a_straight_waist_goes_straight_to_the_goal() -> None:
    current = np.zeros(4, np.float32)
    goal = np.ones(4, np.float32)
    assert np.allclose(D.waist_first(goal, current, np.array([0, 1])), goal)


def test_settling_reaches_the_safe_pose_before_shutdown() -> None:
    d = _daemon()
    d.command.hold(np.ones(d.robot.nu, np.float32) * 1.5)
    _ticks(d, 300)
    assert float(np.max(d.target)) > 0.5
    # No timeout here on purpose: a shutdown gets whatever `settle()` gives itself, and the
    # bug this covers was that default being shorter than the slew it had to wait for.
    assert d.settle() is True
    assert float(np.max(np.abs(d.target))) < 0.05, "it ended at the default pose"


def test_the_settle_deadline_outlasts_the_slew_it_waits_for() -> None:
    """The cheap half of the test above, which the slow one cannot check for every pose.

    A deadline shorter than the slew means `shutdown()` gives up partway and torques off a
    robot that is still moving. The wall time is at least `travel / RESET_VEL`, because the
    slew is handed out one `CONTROL_DT` at a time, and a real bus makes every one of those
    ticks cost more than its sleep."""
    for travel in (0.0, 0.1, 0.5, 1.5, 3.0):
        assert D.settle_budget(travel) > travel / D.RESET_VEL
    assert D.settle_budget(0.0) == D.SETTLE_FLOOR_S, "a body already there still gets a floor"
    assert D.settle_budget(1e6) == D.SETTLE_MAX_S, "and a stuck one is not waited on forever"


def test_shutdown_settles_and_then_closes() -> None:
    d = _daemon()
    d.command.hold(np.ones(d.robot.nu, np.float32) * 0.4)
    _ticks(d, 100)
    d.shutdown()
    assert d.sim.closed, "the bus was closed"
    assert float(np.max(np.abs(d.target))) < 0.05, "and only after reaching the safe pose"


# ── 4. the deadman, which is a trajectory rather than a message ────────────────────────


def test_silence_slews_to_the_safe_pose_and_never_goes_limp() -> None:
    """On this body silence means hold forever, and torque off means fall. Neither is safe,
    so the deadman manufactures a third option."""
    d = _daemon()
    d.command.hold(np.ones(d.robot.nu, np.float32) * 1.0)
    _ticks(d, 200)
    assert float(np.max(d.target)) > 0.3

    d.command.last_client -= D.DEADMAN_S * 4  # quackd went quiet
    d.tick()
    assert d.deadman_tripped
    assert d.command.mode == D.Command.DEADMAN
    for _ in range(500):
        d.tick()  # silence, deliberately: this is the one loop that must not touch
    assert d.deadman_tripped, "and it stayed tripped throughout"
    assert float(np.max(np.abs(d.target))) < 0.05, "it settled rather than collapsing"
    assert not d.sim.closed, "and never disabled torque"


def test_a_command_clears_the_deadman() -> None:
    d = _daemon()
    d.command.last_client -= D.DEADMAN_S * 4
    d.tick()
    assert d.deadman_tripped
    d.touch()
    d.tick()
    assert not d.deadman_tripped


# ── 5. the protocol, end to end over a real socket ─────────────────────────────────────


class _Serving:
    """The real daemon, its real loop and its real socket, on loopback."""

    def __init__(self, **caps: bool) -> None:
        self.port = _free_port()
        robot = D.FakeRobot()
        self.daemon = D.Daemon(robot, D.FakeSim(robot), fake=True)
        D.Handler.daemon_ref = self.daemon
        D.Handler.token = caps.pop("token", None)  # type: ignore[assignment]
        D.Handler.capabilities = {
            "camera": caps.get("camera", False),
            "neck": True,
            "gripper": caps.get("gripper", False),
            "walk": caps.get("walk", False),
            "deadman": True,
            "motions": list(caps.get("motions", D.MOTIONS)),  # type: ignore[call-overload]
        }
        if caps.get("envelope"):
            D.Handler.capabilities["walk_envelope"] = caps["envelope"]
        D.Handler.robot_name = "toddlerbot_2xc"
        D.Handler.motors = robot.nu
        self.server = D.Server(("127.0.0.1", self.port), D.Handler)
        self._loop = threading.Thread(target=self.daemon.run, daemon=True)
        self._serve = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self) -> _Serving:
        self._loop.start()
        self._serve.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.daemon.running = False
        self.server.shutdown()
        self.server.server_close()

    @property
    def address(self) -> str:
        return f"tcp://127.0.0.1:{self.port}"


async def test_the_handshake_reports_what_the_robot_actually_has() -> None:
    with _Serving(camera=True, walk=True) as s:
        link = ToddlerBotBridge(address=s.address)
        await link.connect()
        assert link.hello is not None
        assert link.hello["protocol"] == PROTOCOL
        assert link.hello["protocol_version"] == PROTOCOL_VERSION
        assert link.camera_available and link.walk_available and link.neck_available
        assert not link.gripper_available
        assert link.motors == 30
        await link.close()


async def test_a_robot_with_no_walk_checkpoint_has_no_locomotion_at_all() -> None:
    """The walk policy is an ONNX artifact upstream does not publish. Without one the verbs
    that need the twist intent do not exist, rather than being gated off."""
    with _Serving(camera=True, walk=False) as s:
        adapter = ToddlerBotAdapter(ToddlerBotBridge(address=s.address))
        manifest = await adapter.connect()
        link = adapter.transport
        assert not link.walk_available
        assert manifest.mobility == "none"
        assert "twist" not in manifest.intents
        for verb in ("move", "go_to", "approach_and"):
            assert not manifest.provides(verb)
        assert manifest.provides("stand") and manifest.provides("perform")
        ack = await link.send_intent(Intent.move(vx=0.1))
        assert not ack.accepted and "walk" in str(ack.reason)
        await link.close()


async def test_a_staged_checkpoint_brings_locomotion_into_the_manifest() -> None:
    with _Serving(camera=True, walk=True) as s:
        adapter = ToddlerBotAdapter(ToddlerBotBridge(address=s.address))
        manifest = await adapter.connect()
        link = adapter.transport
        assert manifest.mobility == "legged"
        assert "twist" in manifest.intents
        for verb in ("move", "go_to", "approach_and", "search_scan"):
            assert manifest.provides(verb)
        await link.close()


async def test_stop_holds_the_last_good_pose_and_never_limps() -> None:
    with _Serving() as s:
        link = ToddlerBotBridge(address=s.address)
        await link.connect()
        assert (await link.send_intent(Intent.do("stand"))).accepted
        await asyncio.sleep(0.2)
        await link.stop()
        state = await link.get_state()
        assert state.extras["moving"] is False
        assert not s.daemon.sim.closed, "stopping never disables torque"
        await link.close()


async def test_state_never_claims_a_pose_or_a_battery() -> None:
    with _Serving() as s:
        link = ToddlerBotBridge(address=s.address)
        await link.connect()
        state = await link.get_state()
        assert (state.x, state.y, state.theta) == (None, None, None)
        assert state.battery_percent is None
        assert state.extras["loop_hz"] > 0
        await link.close()


async def test_a_wrong_token_is_refused_and_says_so() -> None:
    with _Serving(token="hunter2") as s:
        link = ToddlerBotBridge(address=s.address, token=None)
        with pytest.raises(TransportError, match="token"):
            await link.connect()
        good = ToddlerBotBridge(address=s.address, token="hunter2")
        await good.connect()
        assert good.hello is not None
        await good.close()


async def test_a_version_mismatch_is_refused_rather_than_guessed() -> None:
    with _Serving() as s:
        link = ToddlerBotBridge(address=s.address)
        original = D.PROTOCOL_VERSION
        D.PROTOCOL_VERSION = original + 1
        try:
            with pytest.raises(TransportError, match="refusing rather than guessing"):
                await link.connect()
        finally:
            D.PROTOCOL_VERSION = original
        await link.close()


async def test_no_daemon_says_what_to_start() -> None:
    link = ToddlerBotBridge(address=f"tcp://127.0.0.1:{_free_port()}")
    with pytest.raises(TransportError, match="quackd-toddlerbot-bridge"):
        await link.connect()
    await link.close()


# ── 6. the rules this file lives by ────────────────────────────────────────────────────


def test_the_daemon_never_imports_quackd() -> None:
    """quackd's dependencies do not belong on a robot, and this file is the proof."""
    for line in DAEMON.read_text(encoding="utf-8").splitlines():
        # the statement only: a late import's comment may well say the word
        stripped = line.split("#", 1)[0].strip()
        if stripped.startswith(("import ", "from ")):
            assert "quackd" not in stripped, f"the daemon imports quackd: {stripped}"


def test_the_daemon_runs_with_upstream_absent() -> None:
    """It is the only quackd code that runs on the robot, and it must start before upstream
    is proven good, so nothing at import time may need it."""
    import subprocess

    script = (
        "import sys\n"
        "for name in ('toddlerbot', 'torch', 'mujoco', 'jax', 'quackd'):\n"
        "    sys.modules[name] = None\n"
        "import importlib.util\n"
        f"spec = importlib.util.spec_from_file_location('d', r'{DAEMON}')\n"
        "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)\n"
        "raise SystemExit(m.main(['--fake', '--once']))\n"
    )
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_it_ships_in_the_sdist_and_never_in_the_wheel() -> None:
    import tomllib

    build = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["hatch"][
        "build"
    ]["targets"]
    assert build["wheel"]["packages"] == ["quackd"]
    assert "bridge" in build["sdist"]["include"]


# ── 7. the loop that must not die quietly ──────────────────────────────────────────────


def test_a_loop_that_faults_keeps_running_and_recovers() -> None:
    """`tick()` never raises for a bad reading, but `set_motor_target` can raise for a bad
    bus. If that killed the loop thread the socket would go on answering healthy while
    nothing at all drove the robot."""
    d = _daemon()
    seen = {"n": 0}
    good = d.sim.set_motor_target

    def flaky(target: object) -> None:
        seen["n"] += 1
        if seen["n"] <= 3:
            raise RuntimeError("bus fault")
        good(target)

    d.sim.set_motor_target = flaky  # type: ignore[method-assign]
    thread = threading.Thread(target=d.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline and seen["n"] < 8:
        time.sleep(0.01)
    d.running = False
    thread.join(timeout=2.0)
    assert seen["n"] >= 8, "the loop went on ticking through the fault"
    assert d.fault is None, "and stopped reporting one once the bus came back"


def test_a_loop_that_never_recovers_gives_up_rather_than_spinning() -> None:
    d = _daemon()

    def gone(target: object) -> None:
        raise RuntimeError("the servo bus is gone")

    d.sim.set_motor_target = gone  # type: ignore[method-assign]
    thread = threading.Thread(target=d.run, daemon=True)
    thread.start()
    thread.join(timeout=10.0)
    assert not thread.is_alive(), "it stopped instead of failing fifty times a second forever"
    assert d.faults >= D.FAULT_LIMIT
    assert d.fault is not None and "servo bus is gone" in d.fault


def test_shutdown_is_safe_to_call_twice() -> None:
    """The excepthook and main's `finally` can both reach it, and closing a real bus twice
    is not a thing to discover on hardware."""
    d = _daemon()
    closes = {"n": 0}
    once = d.sim.close

    def counted() -> None:
        closes["n"] += 1
        once()

    d.sim.close = counted  # type: ignore[method-assign]
    d.shutdown()
    d.shutdown()
    assert closes["n"] == 1, "the bus is closed once, however many things ask"


async def test_a_faulted_loop_fails_the_heartbeat_rather_than_looking_healthy() -> None:
    with _Serving() as s:

        def gone(target: object) -> None:
            raise RuntimeError("the servo bus is gone")

        s.daemon.sim.set_motor_target = gone
        link = ToddlerBotBridge(address=s.address)
        await link.connect()
        await _until(lambda: s.daemon.fault is not None)

        # `bot.health` names the fault. The heartbeat may trip on either that or the loop
        # rate, because a loop that is faulting is also a loop that is not ticking, and
        # loop_hz is a rate over the last second rather than over the run.
        health = await link.request("bot.health")
        assert isinstance(health, dict)
        assert health["ok"] is False
        assert "faulted" in str(health["reason"]), health
        with pytest.raises(HeartbeatError):
            await link.heartbeat()
        await link.close()


async def test_a_client_that_dies_mid_move_trips_the_deadman() -> None:
    """The failure the deadman exists for is not a polite stop. It is a client that stops
    existing while the robot is walking, because silence on this body means hold the last
    gait target forever, and that is a humanoid mid-stride with nobody driving it."""
    with _Serving(walk=True) as s:
        link = ToddlerBotBridge(address=s.address)
        await link.connect()
        assert (await link.send_intent(Intent.move(vx=0.1))).accepted
        await _until(lambda: s.daemon.command.mode == D.Command.WALK)
        assert link._writer is not None
        link._writer.close()  # not a close(): the client stops existing, mid-move
        await _until(lambda: s.daemon.deadman_tripped)
        assert s.daemon.command.mode == D.Command.DEADMAN
        await _until(lambda: float(np.max(np.abs(s.daemon.target))) < 0.05)
        assert not s.daemon.sim.closed, "it slewed to the safe pose and never went limp"


async def test_the_token_is_compared_in_constant_time() -> None:
    """A plain `!=` returns as soon as two bytes differ, which hands the token to anyone who
    can time the reply. The Open Duck Mini's daemon has always used `compare_digest`.

    Driven rather than grepped: a source check passes on the string appearing in a comment,
    and rules out exactly one spelling of the thing it is trying to forbid.
    """
    calls: list[tuple[str, str]] = []

    class _Watched:
        """Stands in for the daemon's `hmac`, and only the daemon's.

        Assigning to `D.hmac.compare_digest` would replace it on the stdlib module object for
        the whole interpreter, including for anything else running concurrently. Replacing the
        daemon module's own reference is the narrower thing, and it is what the daemon calls.
        """

        @staticmethod
        def compare_digest(a: object, b: object) -> bool:
            calls.append((str(a), str(b)))
            return str(a) == str(b)

    real_module = D.hmac
    D.hmac = _Watched  # type: ignore[assignment]
    try:
        with _Serving(token="hunter2") as s:
            link = ToddlerBotBridge(address=s.address, token="hunter2")
            await link.connect()
            await link.close()
    finally:
        D.hmac = real_module

    assert calls, "the handshake never compared the token in constant time"
    assert ("hunter2", "hunter2") in calls


def test_this_robot_does_not_take_the_open_ducks_ports() -> None:
    """The Open Duck Mini already has 9871 for its bridge and 9872 for its camera daemon,
    and SECURITY.md tells people to tunnel that pair."""
    taken = set()
    for name in ("quackd_duck_bridge.py", "quackd_duck_camd.py"):
        text = (REPO / "bridge" / "open_duck" / name).read_text(encoding="utf-8")
        for line in text.splitlines():
            if line.startswith("DEFAULT_PORT"):
                taken.add(int(line.split("=")[1].strip()))
    assert taken == {9871, 9872}, f"the Open Duck's ports moved: {taken}"
    assert D.DEFAULT_PORT not in taken, f"{D.DEFAULT_PORT} is already an Open Duck port"
    assert DEFAULT_PORT == D.DEFAULT_PORT, "the client and the daemon must agree on the port"


# ── 8. capabilities are what loaded, never what was asked for ──────────────────────────


def test_a_fake_body_still_has_motions_to_play() -> None:
    """`--fake` has to exercise `perform` end to end, so the fake gets a synthetic library.
    A real one gets its frames from upstream's keyframe files and nothing is synthesised."""
    d = _daemon()
    assert set(d.motion_library) == set(D.MOTIONS)
    assert all(len(frames) > 0 for frames in d.motion_library.values())


def test_a_real_body_offers_no_motion_it_could_not_load(tmp_path: object) -> None:
    """A missing or unreadable keyframe file must shorten the list rather than produce a verb
    that refuses on a robot. There is nothing at this path, so nothing loads."""
    library = D.load_motions(str(tmp_path), "toddlerbot_2xc")
    assert library == {}


def test_the_motion_files_are_looked_for_per_variant(
    tmp_path: object, caplog: pytest.LogCaptureFixture
) -> None:
    """There is no bare `cuddle.lz4` upstream: every motion is written once per variant, and
    the daemon picks the suffix from the robot name it was started with. A 2xc robot handed
    only 2xm files finds nothing, which is the correct answer rather than a wrong motion."""
    import pathlib

    root = pathlib.Path(str(tmp_path)) / "motion"
    root.mkdir(parents=True)
    for name in D.MOTIONS:
        (root / f"{name}_2xm.lz4").write_bytes(b"not a real keyframe file")

    with caplog.at_level("WARNING"):
        assert D.load_motions(str(tmp_path), "toddlerbot_2xc") == {}, "it looked for _2xc"
    # It has to have looked for the right filenames, not merely failed to find anything: with
    # no `_2xc` file present it returns before it ever needs joblib, so an empty answer alone
    # would also be what a completely broken suffix rule produced.
    looked_for = " ".join(r.getMessage() for r in caplog.records)
    assert "hold_2xc.lz4" in looked_for, looked_for
    assert "_2xm" not in looked_for, "it must not look for the other variant's files"


async def test_the_manifest_offers_only_the_motions_the_daemon_reported() -> None:
    with _Serving(motions=["hold", "kneel"]) as s:
        adapter = ToddlerBotAdapter(ToddlerBotBridge(address=s.address))
        manifest = await adapter.connect()
        assert manifest.extras["motions"] == ["hold", "kneel"]
        assert adapter.transport.motions == ("hold", "kneel")
        await adapter.transport.close()


async def test_the_envelope_comes_from_the_checkpoint_rather_than_a_gin_file() -> None:
    """`command_range` is read off the checkpoint that is actually loaded, so a policy trained
    tighter than quackd's own caps narrows the manifest to what it can really do."""
    with _Serving(walk=True, envelope={"max_vx": 0.12, "max_vy": 0.05, "max_wz": 0.4}) as s:
        adapter = ToddlerBotAdapter(ToddlerBotBridge(address=s.address))
        manifest = await adapter.connect()
        assert manifest.limits["max_vx"] == pytest.approx(0.12)
        assert manifest.limits["max_vy"] == pytest.approx(0.05)
        assert manifest.limits["max_wz"] == pytest.approx(0.4)
        await adapter.transport.close()


async def test_an_envelope_wider_than_the_schema_is_narrowed_not_believed() -> None:
    """`limits` may only ever narrow. A checkpoint trained wider than quackd's caps does not
    get to widen them."""
    with _Serving(walk=True, envelope={"max_vx": 9.0, "max_vy": 9.0, "max_wz": 9.0}) as s:
        adapter = ToddlerBotAdapter(ToddlerBotBridge(address=s.address))
        manifest = await adapter.connect()
        assert manifest.limits["max_vx"] == pytest.approx(0.2)
        assert manifest.limits["max_vy"] == pytest.approx(0.1)
        assert manifest.limits["max_wz"] == pytest.approx(1.0)
        await adapter.transport.close()


def test_the_daemon_reports_the_camera_it_opened_rather_than_the_flag() -> None:
    """A `--camera` that could not open must not become an `observe` the robot cannot do."""
    src = DAEMON.read_text(encoding="utf-8")
    assert '"camera": daemon.camera is not None' in src
    assert '"walk": daemon.walk_policy is not None' in src
    assert '"camera": bool(args.camera)' not in src


def test_the_walk_policy_is_driven_through_upstreams_own_interface() -> None:
    """`step_target` does not exist upstream. The real method takes the whole observation and
    the sim and answers with a pair, and inventing an upstream name is what ADR-0022 exists
    to stop."""
    src = DAEMON.read_text(encoding="utf-8")
    assert "step_target" not in src
    assert "self.walk_policy.step(self.last_obs, self.sim)" in src


def test_the_safe_pose_is_the_robots_home_pose_and_never_zeros() -> None:
    """The bug this guards is the worst one in this file's history.

    The daemon read `default_motor_pos`, which upstream really does have -- on `BasePolicy`,
    not on `Robot` -- behind a `getattr` fallback that quietly turned the wrong name into an
    all-zeros pose. Zeros are not neutral on this body: home carries plus or minus 1.57 rad of
    shoulder and elbow yaw and 1.22 of wrist. So the pose the deadman slews to, `stand`
    targets and the excepthook settles to would have been a large wrong motion on every limb,
    at the exact moment nobody was driving the robot, and every test passed because the fake
    body happened to define the name the daemon was reading.
    """
    robot = D.FakeRobot()
    robot.default_motor_angles = dict.fromkeys(robot.motor_ordering, 0.3)
    d = D.Daemon(robot, D.FakeSim(robot), fake=True)
    assert np.allclose(d.command.default_pose, 0.3), "the safe pose is the robot's home pose"
    assert not np.allclose(d.command.default_pose, 0.0), "and is not the all-zeros fallback"


def test_a_robot_with_no_home_pose_refuses_to_start() -> None:
    """No fallback on this path. A rename upstream has to be a crash at startup rather than a
    plausible-looking pose discovered at slew time."""
    robot = D.FakeRobot()
    del robot.default_motor_angles
    with pytest.raises(AttributeError):
        D.Daemon(robot, D.FakeSim(robot), fake=True)


def test_a_home_pose_that_does_not_cover_every_motor_refuses_to_start() -> None:
    """Built by name, like the joint limits beside it, so a motor with no home angle is named
    rather than silently taking whatever the dict's insertion order happened to line up."""
    robot = D.FakeRobot()
    robot.default_motor_angles = {"only_one": 0.0}
    with pytest.raises(SystemExit, match="no home angle for"):
        D.Daemon(robot, D.FakeSim(robot), fake=True)

    # and one missing motor out of thirty is still a refusal, not a shrug
    robot = D.FakeRobot()
    robot.default_motor_angles = dict.fromkeys(robot.motor_ordering[:-1], 0.0)
    with pytest.raises(SystemExit, match=robot.motor_ordering[-1]):
        D.Daemon(robot, D.FakeSim(robot), fake=True)


async def test_a_daemon_that_loaded_no_motions_offers_no_perform() -> None:
    """Absent, not gated. An empty list means the keyframe files were missing or unreadable,
    which is a different thing from a backend that does not report motions at all, and the
    client used to treat the two the same and re-advertise all five."""
    with _Serving(motions=[]) as s:
        adapter = ToddlerBotAdapter(ToddlerBotBridge(address=s.address))
        manifest = await adapter.connect()
        assert adapter.transport.motions == ()
        assert not manifest.provides("perform")
        assert "perform" not in manifest.preconditions
        assert manifest.extras["motions"] == []
        assert manifest.provides("stand"), "and the verbs that need no keyframes survive"
        await adapter.transport.close()


def test_reading_state_is_not_a_signal_of_life() -> None:
    """Only the methods that drive the robot feed the deadman, plus the keepalive the
    transport sends to say it is still there. A question is not a command, and neither is a
    method this daemon has never heard of: a client built against a newer protocol would
    otherwise hold the deadman off with calls this one is refusing."""
    for question in ("bot.state", "bot.health", "bot.frame", "bot.hello"):
        assert question not in D.COMMAND_METHODS, question
    assert "bot.keepalive" in D.COMMAND_METHODS
    assert "bot.some.method.from.the.future" not in D.COMMAND_METHODS


async def test_a_connected_client_is_not_treated_as_gone_part_way_through_a_verb() -> None:
    """The deadman fires after half a second of silence and `stand` takes three, so without a
    keepalive every long verb would be cancelled underneath itself by the safe-pose slew.

    quackd's own `Heartbeat` cannot be that signal: its period is a run setting rather than
    the manifest's, and it defaults to the same half second the deadman uses. The transport
    keeps its own timer instead."""
    with _Serving() as s:
        link = ToddlerBotBridge(address=s.address)
        await link.connect()
        assert (await link.send_intent(Intent.do("stand"))).accepted
        await asyncio.sleep(D.DEADMAN_S * 3)  # well past the window, sending no commands
        assert not s.daemon.deadman_tripped, "the keepalive said the client was still there"
        await link.close()


# ── 9. the numbers that must never reach a servo ───────────────────────────────────────


def test_a_non_finite_target_is_refused_rather_than_latched() -> None:
    """`json.loads` accepts a bare NaN, `np.clip` propagates it, and `rate_limit` would then
    carry it into `target`, where every later tick rate-limits from NaN. One bad number off
    the wire would freeze the robot's commanded pose for the life of the process."""
    d = _daemon()
    d.tick()  # seed from the robot first, so there is a last good pose to hold
    good = d.target.copy()
    d.command.hold(np.full(d.robot.nu, np.nan, np.float32))
    d.tick()
    assert np.all(np.isfinite(d.target)), "NaN never reached the target"
    assert np.allclose(d.target, good), "and it held the last good pose"
    d.command.hold(np.full(d.robot.nu, 0.2, np.float32))
    _ticks(d, 200)
    assert float(np.max(d.target)) > 0.1, "and it recovered once the numbers were numbers"


async def test_a_nonsense_parameter_is_refused_and_the_link_survives() -> None:
    """A TypeError inside the handler used to escape and drop the connection, so a bad
    parameter cost the session rather than the call."""
    with _Serving() as s:
        link = ToddlerBotBridge(address=s.address)
        await link.connect()
        for bad in (float("nan"), float("inf"), None, "left"):
            with pytest.raises(TransportError, match="yaw_deg"):
                await link.request("bot.look", {"yaw_deg": bad, "pitch_deg": 0.0})
        # and the link is still usable: the refusal cost the call, not the session
        assert (await link.send_intent(Intent.look(x=1.0, y=0.0, z=0.0))).accepted
        assert (await link.get_state()).extras["loop_hz"] > 0
        await link.close()


# ── 10. the head is not the body ───────────────────────────────────────────────────────


def test_looking_around_does_not_cancel_what_the_body_is_doing() -> None:
    """`bot.look` used to `hold()`, which cancelled a walk or a motion mid-stride and still
    answered accepted. On a real body the neck is independent of the gait."""
    d = _daemon()
    d.command.play("kneel", [np.full(d.robot.nu, 0.2, np.float32)] * 600)
    assert d.command.mode == D.Command.MOTION
    d.aim_neck(0.4, -0.2)
    _ticks(d, 400)
    assert d.command.mode == D.Command.MOTION, "the motion is still running"
    assert d.target[d.neck_yaw] == pytest.approx(0.4, abs=0.05)
    assert d.target[d.neck_pitch] == pytest.approx(-0.2, abs=0.05)


def test_which_neck_motor_is_yaw_comes_from_its_name() -> None:
    """Taking `neck[0]` as yaw assumes an ordering upstream never promises: an alphabetical
    motors block lists neck_pitch first, and every look would go to the wrong joint."""
    robot = D.FakeRobot()
    robot.motor_ordering = ["neck_pitch", "neck_yaw", *robot.motor_ordering[2:]]
    robot.default_motor_angles = dict.fromkeys(robot.motor_ordering, 0.0)
    d = D.Daemon(robot, D.FakeSim(robot), fake=True)
    assert robot.motor_ordering[d.neck_yaw] == "neck_yaw"
    assert robot.motor_ordering[d.neck_pitch] == "neck_pitch"


def test_one_dropped_read_does_not_cancel_a_walk() -> None:
    """A dropped packet is routine on this bus. Cancelling a gait mid-stride because of one
    is worse than feeding the policy a reading a tick old."""
    d = _daemon()
    # Move it off zero first: the all-zeros detector refuses a fresh fake's opening reading,
    # correctly, because that is exactly what a dropped packet looks like on this bus.
    d.command.hold(np.full(d.robot.nu, 0.2, np.float32))
    _ticks(d, 200)
    assert d.last_obs is not None
    d.sim.drop_next = True
    d.tick()
    assert d.last_obs is not None, "the last good reading survived one drop"
    for _ in range(D.DROP_LIMIT + 2):
        d.sim.drop_next = True
        d.tick()
    assert d.last_obs is None, "but not an unbroken run of them"


# ── 11. the gripper, which used to say yes and do nothing ──────────────────────────────


def test_a_gripper_build_has_gripper_motors_and_a_plain_one_does_not() -> None:
    plain = D.Daemon(D.FakeRobot(), D.FakeSim(D.FakeRobot()), fake=True)
    assert not any(plain.grippers.values())
    robot = D.FakeRobot("toddlerbot_2xc_gripper")
    assert robot.nu == 32, "the gripper builds carry two more motors"
    gripped = D.Daemon(robot, D.FakeSim(robot), fake=True)
    assert gripped.grippers["left"] and gripped.grippers["right"]


def test_closing_a_gripper_actually_commands_the_motor() -> None:
    """`bot.grip` used to answer accepted and command nothing at all, on a capability taken
    from a command line flag rather than from the body."""
    robot = D.FakeRobot("toddlerbot_2xc_gripper")
    robot.default_motor_angles = dict.fromkeys(robot.motor_ordering, 0.0)
    d = D.Daemon(robot, D.FakeSim(robot), fake=True)
    index = d.grippers["right"][0]

    moved = d.set_grip("right", close=True)
    assert moved == ["right_gripper"]
    _ticks(d, 400)
    assert d.target[index] == pytest.approx(float(d.lo[index]), abs=0.05)

    d.set_grip("right", close=False)
    _ticks(d, 400)
    assert d.target[index] == pytest.approx(float(d.hi[index]), abs=0.05)

    assert d.set_grip("left", close=True) == ["left_gripper"]
    assert d.set_grip("both", close=True) == ["left_gripper", "right_gripper"]


async def test_a_client_told_there_is_no_gripper_refuses_to_grip() -> None:
    with _Serving(gripper=False) as s:
        link = ToddlerBotBridge(address=s.address)
        await link.connect()
        assert link.gripper_available is False
        reply = await link.request("bot.grip", {"side": "right", "close": True})
        assert isinstance(reply, dict) and reply["accepted"] is False
        await link.close()


def test_a_camera_that_stopped_answering_stops_being_a_camera() -> None:
    """A wedged USB camera would otherwise serve its last good picture forever, and a pilot
    would navigate by a photograph of somewhere the robot used to be."""
    feed = D.CameraFeed.__new__(D.CameraFeed)
    feed.lock = threading.Lock()
    feed.jpeg = b"not really a jpeg"
    feed.taken = time.monotonic()
    assert feed.latest() is not None
    feed.taken = time.monotonic() - D.CAMERA_STALE_S - 1.0
    assert feed.latest() is None, "a stale frame is no frame"


async def test_closing_the_link_does_not_sit_out_the_request_timeout() -> None:
    """`close()` sends a final stop, and `request()` waits on a future that only the read loop
    can resolve. Cancelling the pump first meant every close waited the whole request timeout
    and never saw the answer, which is three seconds of nothing on every disconnect."""
    with _Serving() as s:
        link = ToddlerBotBridge(address=s.address, request_timeout_s=3.0)
        await link.connect()
        loop = asyncio.get_running_loop()
        started = loop.time()
        await link.close()
        elapsed = loop.time() - started
        assert elapsed < 1.0, f"close() took {elapsed:.1f}s waiting for an answer nobody could give"
        assert s.daemon.command.mode == D.Command.HOLD, "and the daemon really got the stop"


def test_a_motion_is_played_rather_than_smoothed_past() -> None:
    """The keyframe files are fifty hertz recordings of a body moving faster than this loop's
    per-tick rate limit allows. Advancing a frame every tick regardless meant the robot traced
    a smoothed shortcut through them and still reported the motion complete."""
    d = _daemon()
    d.tick()
    far = np.full(d.robot.nu, 1.0, np.float32)  # far further than one tick can travel
    d.command.play("kneel", [far, far * -1.0])
    d.touch()
    d.tick()
    assert d.command.frame_index == 0, "it did not advance past a frame it has not reached"

    _ticks(d, 300)
    assert d.command.frame_index >= 1, "and it did advance once the body caught up"
    assert float(np.max(np.abs(d.target))) > 0.5, "having actually gone most of the way there"


def test_a_keyframe_that_can_never_be_reached_does_not_stall_the_motion_forever() -> None:
    """A frame outside the joint limits is unreachable by construction, and a motion that
    waits for it forever is worse than one that skips it and says so."""
    d = _daemon()
    d.tick()
    impossible = np.full(d.robot.nu, 99.0, np.float32)  # the clamp will never allow this
    d.command.play("kneel", [impossible])
    _ticks(d, D.FRAME_DWELL_LIMIT + 5)
    assert d.command.frame_index >= 1, "it gave up on the frame rather than dwelling forever"


async def test_a_checkpoint_that_cannot_move_the_robot_is_not_locomotion() -> None:
    """`limits` may only narrow, so an envelope of zero clamps every velocity to nothing.
    Offering `move` on top of that is a verb that accepts every command and moves nothing,
    which is the same empty claim a missing checkpoint would make."""
    with _Serving(walk=True, envelope={"max_vx": 0.0, "max_vy": 0.0, "max_wz": 0.0}) as s:
        adapter = ToddlerBotAdapter(ToddlerBotBridge(address=s.address))
        manifest = await adapter.connect()
        assert manifest.mobility == "none"
        for verb in ("move", "go_to", "approach_and"):
            assert not manifest.provides(verb), verb
        await adapter.transport.close()


async def test_an_envelope_with_one_usable_axis_is_still_locomotion() -> None:
    """Forward only is a real robot. Only a checkpoint with nothing left on any axis is not."""
    with _Serving(walk=True, envelope={"max_vx": 0.12, "max_vy": 0.0, "max_wz": 0.0}) as s:
        adapter = ToddlerBotAdapter(ToddlerBotBridge(address=s.address))
        manifest = await adapter.connect()
        assert manifest.mobility == "legged"
        assert manifest.provides("move")
        assert manifest.limits["max_vx"] == pytest.approx(0.12)
        assert manifest.limits["max_vy"] == pytest.approx(0.0)
        await adapter.transport.close()


async def test_a_line_that_is_not_a_request_does_not_cost_the_session() -> None:
    """`null`, `7`, `[]` and `"hi"` are all valid JSON and none of them is a JSON-RPC object.
    `msg.get` on any of them used to raise outside the dispatch guard, dropping the connection
    and losing every later verb on that session over one bad line."""
    with _Serving() as s:
        link = ToddlerBotBridge(address=s.address)
        await link.connect()
        assert link._writer is not None
        for junk in ("null", "7", "[]", '"hi"', '{"params": 3, "method": "bot.state", "id": 9}'):
            link._writer.write((junk + "\n").encode())
            await link._writer.drain()
        # the link still works, which is the whole point
        assert (await link.get_state()).extras["loop_hz"] > 0
        assert (await link.send_intent(Intent.do("stand"))).accepted
        await link.close()
