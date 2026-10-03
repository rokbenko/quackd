"""quackd's AlohaMini client against a fake host, over real loopback sockets.

This is what the `zmq` backend's 🧪 in `docs/adapters/status.md` rests on. The tests that matter
most are the ones about stopping, because this robot's own stop is incomplete: its watchdog
covers two subsystems of three, its lift latches when you say nothing, its arms are limp, and
its fail-safe is a Python process that may have exited.
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Callable

import pytest

from quackd.transport.base import HeartbeatError, Intent, TransportError
from quackd_alohamini import AlohaMiniAdapter
from quackd_alohamini.zmq_host import (
    LIFT_HEIGHT_KEY,
    LIFT_VEL_KEY,
    VEL_KEYS,
    AlohaMiniZmq,
    split_address,
)
from tests.fake_alohamini_host import FakeAlohaMiniHost

pytest.importorskip("zmq", reason="the alohamini zmq backend needs quackd[alohamini]")


async def _until(predicate: Callable[[], bool], limit_s: float = 3.0) -> None:
    deadline = asyncio.get_running_loop().time() + limit_s
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("the fake host never reached the expected state")


async def _lands(link: AlohaMiniZmq, host: FakeAlohaMiniHost, intent: Intent) -> None:
    """Send one intent and wait for the host to take it.

    The command socket is CONFLATE, so back-to-back sends collapse into the newest. That is
    correct behaviour and quackd's merged payload survives it, but a test that wants to look
    at each payload has to let each one land."""
    seen = len(host.actions)
    ack = await link.send_intent(intent)
    assert ack.accepted, ack.reason
    await _until(lambda: len(host.actions) > seen)


async def _connected(host: FakeAlohaMiniHost, **kwargs: object) -> AlohaMiniZmq:
    link = AlohaMiniZmq(address=host.address, **kwargs)  # type: ignore[arg-type]
    host.start()
    await link.connect()
    return link


# ── the payload, which is the whole safety story ───────────────────────────────────────


async def test_every_payload_carries_all_three_velocity_keys() -> None:
    """`send_action` indexes them with no default, so a payload missing one raises before the
    lift is touched and before any bus write: the entire action is discarded, arms included,
    and the watchdog is never refreshed. Nothing quackd sends may be missing one."""
    with FakeAlohaMiniHost(watchdog_s=10.0) as host:
        link = await _connected(host)
        await _lands(link, host, Intent.move(vx=0.1))
        await _lands(link, host, Intent.joint({"arm_left_elbow_flex": 20.0}, 0.2))
        await _lands(link, host, Intent(kind="pose", params={"lift_height_mm": 200.0}))
        assert host.rejected == [], "the host discarded a payload quackd sent"
        for action in host.actions:
            assert set(VEL_KEYS) <= set(action), f"payload without all three .vel keys: {action}"
        await link.close()


async def test_every_payload_carries_exactly_one_lift_key() -> None:
    """Neither key leaves the velocity register latched and the lift travelling. Both keys let
    the velocity branch run last and win, freezing a lift that was asked to move."""
    with FakeAlohaMiniHost(watchdog_s=10.0) as host:
        link = await _connected(host)
        await _lands(link, host, Intent.move(vx=0.1))
        await _lands(link, host, Intent(kind="pose", params={"lift_height_mm": 200.0}))
        for action in host.actions:
            keys = {LIFT_HEIGHT_KEY, LIFT_VEL_KEY} & set(action)
            assert len(keys) == 1, f"payload with {len(keys)} lift keys: {action}"
        await link.close()


async def test_a_payload_missing_a_velocity_key_is_dropped_entirely() -> None:
    """The fake reproduces the failure so the guarantee above is worth something."""
    with FakeAlohaMiniHost() as host:
        host.apply({"x.vel": 0.0, "y.vel": 0.0, "theta.vel": 0.0, LIFT_VEL_KEY: 0})
        before = dict(host.state)
        with pytest.raises(KeyError):
            host.apply({"x.vel": 0.1, "arm_left_elbow_flex.pos": 30.0, LIFT_VEL_KEY: 0})
        assert host.state == before, "a rejected action must change nothing at all"


# ── stopping ───────────────────────────────────────────────────────────────────────────


async def test_stop_zeroes_the_lift() -> None:
    """The test the plan for this adapter was built around. A stop that sends only the three
    base velocities leaves the lift's register latched, and upstream's own replay example does
    exactly that. quackd's stop carries the lift velocity zero as well."""
    with FakeAlohaMiniHost(watchdog_s=10.0) as host:
        link = await _connected(host)
        await _lands(link, host, Intent(kind="pose", params={"lift_height_mm": 400.0}))
        assert host.lift_goal_velocity != 0, "the lift was asked to move"

        await link.stop()
        await _until(lambda: host.lift_goal_velocity == 0)
        assert all(host.state[k] == 0.0 for k in VEL_KEYS)
        assert host.actions[-1][LIFT_VEL_KEY] == 0
        await link.close()


async def test_connect_stops_before_it_does_anything_else() -> None:
    """Homing leaves full-speed descent in the lift's register and upstream's zeroing write is
    commented out, so the lift is already travelling when quackd arrives."""
    with FakeAlohaMiniHost() as host:
        assert host.lift_goal_velocity < 0, "the fake starts where home() leaves the robot"
        link = await _connected(host)
        await _until(lambda: host.lift_goal_velocity == 0)
        assert host.actions[0][LIFT_VEL_KEY] == 0, "the first thing quackd says is stop"
        await link.close()


async def test_stop_holds_the_arms_where_they_were_asked_to_go() -> None:
    """The base has to be moving when the stop arrives, or the velocity half of this proves
    nothing: every payload carries all three velocity keys, so a joint command on its own
    already leaves them at zero and the assertion would be true before `stop` was sent."""
    with FakeAlohaMiniHost(watchdog_s=10.0) as host:
        link = await _connected(host)
        await _lands(link, host, Intent.joint({"arm_left_shoulder_pan": 25.0}, 0.2))
        await _lands(link, host, Intent.move(vx=0.2))
        assert host.state["x.vel"] == pytest.approx(0.2), "the base really was moving"

        seen = len(host.actions)
        await link.stop()
        await _until(lambda: len(host.actions) > seen)
        assert all(host.state[k] == 0.0 for k in VEL_KEYS), "the stop zeroed the base"
        assert host.state["arm_left_shoulder_pan.pos"] == pytest.approx(25.0)
        assert host.actions[-1]["arm_left_shoulder_pan.pos"] == pytest.approx(25.0)
        await link.close()


async def test_stop_is_safe_when_the_host_has_already_gone() -> None:
    """The host kills its own process on an over-current trip, so this is a real state."""
    with FakeAlohaMiniHost() as host:
        link = await _connected(host)
        host.close()
        await link.stop()  # must not raise
        await link.close()


# ── the deadman, from both ends ────────────────────────────────────────────────────────


async def test_silence_stops_the_base_and_the_lift_but_not_the_arms() -> None:
    with FakeAlohaMiniHost(watchdog_s=0.05) as host:
        link = await _connected(host)
        await link.send_intent(Intent.joint({"arm_right_wrist_roll": 15.0}, 0.2))
        await link.send_intent(Intent.move(vx=0.2))
        # On the applied action rather than on `state`: the watchdog zeroes the base again
        # within a couple of host cycles, and catching that window is a race under load.
        await _until(lambda: any(a.get("x.vel") == pytest.approx(0.2) for a in host.actions))
        # The counter is cumulative, and home()'s stray descent velocity already tripped it
        # once before the client even connected. Waiting for `>= 1` therefore returns
        # immediately, under load before the move above has been zeroed. Wait for a trip that
        # happens after this move landed, which is the one that zeroes these keys.
        tripped = host.watchdog_trips
        await _until(lambda: host.watchdog_trips > tripped)
        assert all(host.state[k] == 0.0 for k in VEL_KEYS)
        assert host.lift_goal_velocity == 0
        assert host.state["arm_right_wrist_roll.pos"] == pytest.approx(15.0), "arms are untouched"
        await link.close()


async def test_a_stale_reading_is_a_heartbeat_failure() -> None:
    with FakeAlohaMiniHost() as host:
        link = await _connected(host, stale_limit_ms=50.0)
        await link.heartbeat()
        host.stop()
        await asyncio.sleep(0.2)
        with pytest.raises(HeartbeatError, match="no fresh observation"):
            await link.heartbeat()
        await link.close()


# ── what the wire tells quackd about the robot ─────────────────────────────────────────


@pytest.mark.parametrize(("model", "expected"), [("alohamini1", 6), ("alohamini2", 7)])
async def test_the_sku_comes_from_the_observed_keys_not_from_config(
    model: str, expected: int
) -> None:
    """The host defaults to alohamini2 and upstream's own client defaults to alohamini1, with
    nothing cross-checking them, so a config-driven client silently zero-fills two joints."""
    with FakeAlohaMiniHost(model=model) as host:
        link = await _connected(host)
        assert link.robot_model == model
        state = await link.get_state()
        assert len(state.extras["joints"]) == expected * 2
        await link.close()


async def test_the_camera_list_comes_off_the_wire() -> None:
    """The host names what it actually sent in `_images`; upstream's own client throws that
    away and trusts its own config instead."""
    with FakeAlohaMiniHost(cameras=("forward",)) as host:
        link = await _connected(host)
        adapter = AlohaMiniAdapter(link)
        manifest = await adapter.connect()
        assert link.cameras == ("forward",)
        assert manifest.extras["cameras"] == ["forward"]
        frame = await link.get_frame()
        assert frame is not None and frame.size == (32, 32)
        await link.close()


async def test_a_host_with_no_cameras_loses_the_camera_verbs() -> None:
    with FakeAlohaMiniHost(cameras=()) as host:
        link = await _connected(host)
        adapter = AlohaMiniAdapter(link)
        manifest = await adapter.connect()
        assert "camera" not in manifest.sensors
        for verb in ("observe", "go_to", "search_scan", "approach_and"):
            assert not manifest.provides(verb)
        assert await link.get_frame() is None
        await link.close()


async def test_a_stock_host_refuses_the_arm_verbs_instead_of_pretending() -> None:
    """As shipped, upstream disables arm torque and never re-enables it, so a joint command
    would move nothing. quackd's own host wrapper is what turns it back on."""
    with FakeAlohaMiniHost(arm_torque=False) as host:
        link = await _connected(host)
        assert link.arm_torque is False
        ack = await link.send_intent(Intent.joint({"arm_left_elbow_flex": 10.0}, 0.2))
        assert not ack.accepted and "torque" in str(ack.reason)
        state = await link.get_state()
        assert state.extras["arm_torque"] is False
        await link.close()


async def test_turn_rate_goes_out_in_degrees_per_second() -> None:
    # a long watchdog: this test is about the unit on the wire, not about the deadman, and a
    # one second watchdog would zero the value between the wait and the assertion
    with FakeAlohaMiniHost(watchdog_s=10.0) as host:
        link = await _connected(host)
        await _lands(link, host, Intent.move(wz=1.0))
        assert host.state["theta.vel"] == pytest.approx(math.degrees(1.0), rel=1e-6)
        state = await link.get_state()
        assert state.extras["twist"]["wz"] == pytest.approx(1.0, rel=1e-6)
        await link.close()


async def test_the_state_never_claims_a_pose_or_a_battery() -> None:
    with FakeAlohaMiniHost() as host:
        link = await _connected(host)
        state = await link.get_state()
        assert (state.x, state.y, state.theta) == (None, None, None)
        assert state.battery_percent is None
        await link.close()


async def test_no_host_says_so_and_names_what_to_check() -> None:
    link = AlohaMiniZmq(address="tcp://127.0.0.1:59991?obs=59992", connect_timeout_s=0.3)
    with pytest.raises(TransportError, match="no observation"):
        await link.connect()
    await link.close()


def test_the_observation_port_defaults_to_one_past_the_command_port() -> None:
    assert split_address(None) == ("127.0.0.1", 5555, 5556)
    assert split_address("tcp://10.0.0.9:5555") == ("10.0.0.9", 5555, 5556)
    assert split_address("tcp://10.0.0.9:7000?obs=9001") == ("10.0.0.9", 7000, 9001)


async def test_a_host_that_dies_mid_verb_is_a_refusal_and_not_a_crash() -> None:
    """The host exits by itself after about 100 minutes and also on a sustained over-current,
    so losing it mid-verb is a scheduled event. Refusal is data: the pilot is told the link
    is gone rather than handed a ZMQ error through the executor's catch-all."""
    with FakeAlohaMiniHost() as host:
        link = await _connected(host)

        def gone(*args: object, **kwargs: object) -> None:
            raise RuntimeError("Again: Resource temporarily unavailable")

        link._link.send = gone  # type: ignore[union-attr, method-assign]
        ack = await link.send_intent(Intent.move(vx=0.1))
        assert not ack.accepted
        assert "stopped answering" in str(ack.reason)
        await link.close()
