"""quackd's XLeRobot client against a fake host, over real loopback sockets.

This is what the `zmq` backend's 🧪 in `docs/adapters/status.md` rests on. It proves the client
is correct against our reading of `xlerobot_host.py` at the pin: the ports, the JSON, the
conflation, the watchdog, the base-write-on-every-action rule, and the deg/s wire unit. It
proves nothing about serial timing, calibration, or a real camera, and the status row says so.

The fake is `tests/fake_xlerobot_host.py`. Where a test needs to know exactly what the host
saw, it stops the fake's thread and pumps by hand, which is also how the conflation test can
be deterministic rather than a race.
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Callable

import pytest

from quackd.transport.base import HeartbeatError, Intent, TransportError
from quackd_xlerobot import XLerobotAdapter
from quackd_xlerobot.verbs import GRIPPER_CLOSED, GRIPPER_OPEN
from quackd_xlerobot.zmq_host import (
    CMD_PORT,
    OBS_PORT,
    XLerobotZmq,
    pick_camera,
    split_address,
)
from tests.fake_xlerobot_host import FakeXLerobotHost

pytest.importorskip("zmq", reason="the xlerobot zmq backend needs quackd[xlerobot]")


async def _until(predicate: Callable[[], bool], limit_s: float = 3.0) -> None:
    """Wait for the fake's own loop to catch up, without sleeping a fixed amount."""
    deadline = asyncio.get_running_loop().time() + limit_s
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("the fake host never reached the expected state")


async def _connected(host: FakeXLerobotHost, **kwargs: object) -> XLerobotZmq:
    link = XLerobotZmq(address=host.address, **kwargs)  # type: ignore[arg-type]
    host.start()
    await link.connect()
    return link


# ── the address ────────────────────────────────────────────────────────────────────────


def test_the_observation_port_defaults_to_one_past_the_command_port() -> None:
    assert split_address(None) == ("127.0.0.1", CMD_PORT, OBS_PORT)
    assert split_address("tcp://10.0.0.4:5555") == ("10.0.0.4", 5555, 5556)
    assert split_address("10.0.0.4:7000") == ("10.0.0.4", 7000, 7001)
    # a host whose ports were assigned rather than configured says so
    assert split_address("tcp://10.0.0.4:7000?obs=9001") == ("10.0.0.4", 7000, 9001)


def test_a_camera_is_the_key_whose_value_is_a_string() -> None:
    """The seventeen state keys are floats; the host writes each camera as base64. A head
    camera wins over a wrist one, whose bearing means nothing for navigation."""
    assert pick_camera({"x.vel": 0.0, "left_arm_gripper.pos": 1.0}) is None
    assert pick_camera({"x.vel": 0.0, "left_wrist": "aGk="}) == "left_wrist"
    assert pick_camera({"left_wrist": "aGk=", "head": "aGk=", "right_wrist": "aGk="}) == "head"


# ── the handshake ──────────────────────────────────────────────────────────────────────


async def test_no_host_says_so_and_names_what_to_check() -> None:
    """There is no hello on this wire, so the handshake is "did an observation come back".
    The failure has to name the two things that are actually usually wrong."""
    link = XLerobotZmq(address="tcp://127.0.0.1:59999?obs=59998", connect_timeout_s=0.3)
    with pytest.raises(TransportError, match="no observation"):
        await link.connect()
    await link.close()


async def test_connecting_finds_the_camera_on_the_wire() -> None:
    with FakeXLerobotHost(cameras=("head",)) as host:
        link = await _connected(host)
        assert link.camera_available and link.camera_key == "head"
        frame = await link.get_frame()
        assert frame is not None and frame.size == (32, 32)
        await link.close()


async def test_a_blind_host_loses_exactly_the_camera_verbs() -> None:
    """A stock cart ships with every camera commented out, so this is the common case."""
    with FakeXLerobotHost(cameras=()) as host:
        link = await _connected(host)
        adapter = XLerobotAdapter(link)
        manifest = await adapter.connect()
        assert not link.camera_available
        assert "camera" not in manifest.sensors
        for verb in ("observe", "go_to", "search_scan", "approach_and"):
            assert not manifest.provides(verb)
        assert await link.get_frame() is None
        await link.close()


async def test_a_camera_that_failed_to_encode_is_advertised_but_yields_no_frame() -> None:
    """Upstream sets the key to "" when cv2.imencode fails rather than dropping it, so the
    camera is real and the frame is not."""
    with FakeXLerobotHost(cameras=("head",), broken_camera=True) as host:
        link = await _connected(host)
        assert link.camera_available and link.camera_key == "head"
        assert await link.get_frame() is None
        await link.close()


# ── units on the wire ──────────────────────────────────────────────────────────────────


async def test_turn_rate_goes_out_in_degrees_per_second() -> None:
    """quackd speaks rad/s and this wire is deg/s. Sending rad/s straight through would be a
    57x error on a 12 kg cart, which is the worst bug this adapter could have."""
    with FakeXLerobotHost() as host:
        link = await _connected(host)
        assert (await link.send_intent(Intent.move(vx=0.2, wz=1.0))).accepted
        await _until(lambda: host.state["theta.vel"] != 0.0)
        assert host.state["theta.vel"] == pytest.approx(math.degrees(1.0), rel=1e-6)
        assert host.state["x.vel"] == pytest.approx(0.2)
        # and it comes back the other way
        state = await link.get_state()
        assert state.extras["twist"]["wz"] == pytest.approx(1.0, rel=1e-6)
        await link.close()


async def test_joint_positions_read_back_off_the_wire() -> None:
    with FakeXLerobotHost() as host:
        link = await _connected(host)
        assert (await link.send_intent(Intent.joint({"left_arm_elbow_flex": 42.0}, 0.2))).accepted
        await _until(lambda: host.state["left_arm_elbow_flex.pos"] == 42.0)

        async def read_back() -> float | None:
            state = await link.get_state()
            return state.extras["joints"].get("left_arm_elbow_flex")

        deadline = asyncio.get_running_loop().time() + 3.0
        while asyncio.get_running_loop().time() < deadline:
            if await read_back() == pytest.approx(42.0):
                break
            await asyncio.sleep(0.01)
        assert await read_back() == pytest.approx(42.0)
        await link.close()


async def test_a_bare_gripper_intent_still_commands_a_goal() -> None:
    """`Intent.gripper(open=...)` names no position, and the verb's own goals are what usually
    fill that in. A gripper that reported `holding` while moving nothing would be a lie, so the
    backend derives the goal rather than trusting the caller to have supplied one."""
    with FakeXLerobotHost() as host:
        link = await _connected(host)
        # Open first. The fake starts every key at 0.0, which is also the closed goal, so
        # closing first would wait on a value that was already there.
        assert host.state["right_arm_gripper.pos"] == 0.0, "the starting value"
        assert (await link.send_intent(Intent.gripper(open=True))).accepted
        await _until(lambda: host.state["right_arm_gripper.pos"] == GRIPPER_OPEN)
        assert link.holding["right"] is False

        assert (await link.send_intent(Intent.gripper(open=False))).accepted
        await _until(lambda: host.state["right_arm_gripper.pos"] == GRIPPER_CLOSED)
        assert link.holding == {"left": False, "right": True}
        await link.close()


async def test_an_unknown_joint_is_refused_as_data_not_raised() -> None:
    with FakeXLerobotHost() as host:
        link = await _connected(host)
        ack = await link.send_intent(Intent.joint({"head_motor_1": 10.0}, 0.2))
        assert not ack.accepted and "unknown joints" in str(ack.reason)
        await link.close()


# ── the two behaviours that are easy to get wrong ──────────────────────────────────────


async def test_two_intents_in_one_host_cycle_both_land_despite_conflate() -> None:
    """CONFLATE keeps only the newest message, so a client that sent one message per intent
    would silently lose the first. This client re-sends the whole desired action instead."""
    with FakeXLerobotHost() as host:
        link = await _connected(host)
        host.stop()  # nothing is reading now, so the next two sends conflate into one
        close_left = Intent(
            kind="gripper",
            params={"side": "left", "open": False, "positions": {"left_arm_gripper": 0.0}},
        )
        assert (await link.send_intent(close_left)).accepted
        assert (await link.send_intent(Intent.move(vx=0.25))).accepted
        await asyncio.sleep(0.05)
        host.pump(publish=False)
        assert len(host.actions) == 1, "conflation should have collapsed the two sends into one"
        assert host.state["left_arm_gripper.pos"] == pytest.approx(0.0), "the gripper was not lost"
        assert host.state["x.vel"] == pytest.approx(0.25), "the drive command arrived too"
        await link.close()


async def test_an_arm_command_commands_zero_base_velocity() -> None:
    """Upstream writes the wheels on every action, so an arms-only command halts the base.
    quackd reproduces that deliberately rather than leaving it to emerge."""
    with FakeXLerobotHost() as host:
        link = await _connected(host)
        assert (await link.send_intent(Intent.move(vx=0.25))).accepted
        await _until(lambda: host.state["x.vel"] == pytest.approx(0.25))
        assert (await link.send_intent(Intent.joint({"right_arm_wrist_roll": 12.0}, 0.2))).accepted
        await _until(lambda: host.state["right_arm_wrist_roll.pos"] == 12.0)
        assert host.state["x.vel"] == 0.0, "moving an arm stops the cart"
        await link.close()


# ── stopping ───────────────────────────────────────────────────────────────────────────


async def test_stop_zeroes_the_wheels_and_holds_the_arms_where_they_are() -> None:
    """Stop is a hold, never a collapse.

    It zeroes the three wheels and leaves every arm goal exactly where it already was. It
    deliberately does not re-command the arms from the latest observation: that reading can be
    several cycles behind, nothing on the wire is timestamped, and sending a stale position to
    a servo does not hold an arm, it moves one (ADR-0026). The hold is the servos keeping their
    last goal under torque, and what makes that safe is that quackd never sends the disconnect
    that would disable it."""
    with FakeXLerobotHost() as host:
        link = await _connected(host)
        await link.send_intent(Intent.joint({"left_arm_shoulder_pan": 33.0}, 0.2))
        await _until(lambda: host.state["left_arm_shoulder_pan.pos"] == 33.0)
        await link.send_intent(Intent.move(vx=0.25))
        await _until(lambda: host.state["x.vel"] == pytest.approx(0.25))

        await link.stop()
        await _until(lambda: host.state["x.vel"] == 0.0)
        # the arm stays where it was asked to go: stop must never move it, and a hold rebuilt
        # from a reading that is a few cycles behind would do exactly that
        assert host.state["left_arm_shoulder_pan.pos"] == pytest.approx(33.0)
        held = host.actions[-1]
        assert held["left_arm_shoulder_pan.pos"] == pytest.approx(33.0), "the goal still stands"
        assert all(v == 0.0 for k, v in held.items() if k.endswith(".vel")), "the wheels zeroed"
        assert "head_motor_1.pos" not in held, "quackd never commands the head, even to hold it"
        await link.close()


async def test_stop_is_safe_when_the_host_has_already_gone() -> None:
    """The executor calls stop exactly when things have gone wrong. Raising there would turn a
    stop into a failed verb and hide that the host's own watchdog is what stopped the base."""
    with FakeXLerobotHost() as host:
        link = await _connected(host)
        host.close()
        await link.stop()  # must not raise
        await link.close()


# ── the deadman, from both ends ────────────────────────────────────────────────────────


async def test_silence_trips_the_hosts_watchdog_and_stops_the_base() -> None:
    """The window matters. At a 50 ms watchdog and a 30 Hz fake there are barely two pumps
    between the move landing and the watchdog zeroing it, so the observation of a moving base
    was a coin flip on a loaded machine. Half a second is still fast and is not a race."""
    with FakeXLerobotHost(watchdog_s=0.5) as host:
        link = await _connected(host)
        await link.send_intent(Intent.move(vx=0.25))
        # On the applied action, which is append-only, rather than on `state`, which the
        # watchdog zeroes again a moment later: catching a transient value on a real clock
        # against a real host thread is a race that a busy machine loses.
        await _until(lambda: any(a.get("x.vel") == pytest.approx(0.25) for a in host.actions))

        # A trip AFTER the move landed. `watchdog_trips` is cumulative, so waiting for `>= 1`
        # can be satisfied by a trip that happened before it.
        tripped = host.watchdog_trips
        await _until(lambda: host.watchdog_trips > tripped, limit_s=5.0)
        assert host.state["x.vel"] == 0.0
        await link.close()


async def test_a_stale_reading_is_a_heartbeat_failure_not_a_reading() -> None:
    """Nothing on the wire is timestamped and upstream's own client serves its cache on a
    timeout. A stopped robot must not look like a moving one.

    The limit is the host's own window rather than a tighter one, because a loaded runner
    can starve the fake's thread for longer than 50 ms and fail the live heartbeat this test
    needs to pass first. And the socket is drained once after the host stops, because its
    last cycle may have published an observation nobody has read, and a heartbeat that finds
    it is right to call it fresh. The silence this test is about starts after that."""
    with FakeXLerobotHost() as host:
        link = await _connected(host)
        await link.heartbeat()
        host.stop()
        await link.get_state()  # take whatever the host's last cycle left in the socket
        await asyncio.sleep(link.stale_limit_ms / 1000.0 + 0.2)
        with pytest.raises(HeartbeatError, match="no observation"):
            await link.heartbeat()
        state = await link.get_state()
        assert state.extras["stale_ms"] > link.stale_limit_ms
        await link.close()


async def test_the_state_never_claims_a_pose_or_a_battery() -> None:
    """There is no odometry anywhere in the observation and no data link to the power
    station, so both are unknowable rather than merely unknown."""
    with FakeXLerobotHost() as host:
        link = await _connected(host)
        state = await link.get_state()
        assert (state.x, state.y, state.theta) == (None, None, None)
        assert state.battery_percent is None
        adapter = XLerobotAdapter(link)
        await adapter.connect()
        assert (await adapter.health()).battery_percent is None
        await link.close()


async def test_a_host_that_dies_mid_verb_is_a_refusal_and_not_a_crash() -> None:
    """The host exits by itself after an hour, and there is no supervisor anywhere upstream
    to restart it, so this is a scheduled event rather than a rare one. Refusal is data: the
    pilot must be told the link is gone, not handed a ZMQ error through the executor's
    catch-all, which reads like a crash in quackd."""
    with FakeXLerobotHost() as host:
        link = await _connected(host)

        def gone(*args: object, **kwargs: object) -> None:
            raise RuntimeError("Again: Resource temporarily unavailable")

        link._link.send = gone  # type: ignore[union-attr, method-assign]
        ack = await link.send_intent(Intent.move(vx=0.1))
        assert not ack.accepted
        assert "stopped answering" in str(ack.reason)
        assert "restart it on the robot" in str(ack.reason)
        await link.close()
