"""quackd's AlohaMini client against a fake host, over real loopback sockets.

This is what the `zmq` backend's 🧪 in `docs/adapters/status.md` rests on. The tests that matter
most are the ones about stopping, because this robot's own stop is incomplete: its watchdog
covers two subsystems of three, its lift latches when you say nothing, its arms are limp, and
its fail-safe is a Python process that may have exited.
"""

from __future__ import annotations

import asyncio
import json
import math
import time
from collections.abc import Callable

import pytest

from quackd.transport.base import HeartbeatError, Intent, TransportError
from quackd_alohamini import AlohaMiniAdapter, zmq_host
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


async def test_stop_holds_the_arms_where_they_were_asked_to_go(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The base has to be moving when the stop arrives, or the velocity half of this proves
    nothing: every payload carries all three velocity keys, so a joint command on its own
    already leaves them at zero and the assertion would be true before `stop` was sent. And an
    arm that has arrived stays there."""
    monkeypatch.setattr(zmq_host, "HOLD_READ_S", 30.0)  # a slow test is no silent host
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


@pytest.fixture
def _a_slow_machine_is_no_silent_host(monkeypatch: pytest.MonkeyPatch) -> None:
    """How long a stop looks for a reading is what these leave to the fake's own 30 Hz."""
    monkeypatch.setattr(zmq_host, "HOLD_READ_S", 30.0)


@pytest.mark.usefixtures("_a_slow_machine_is_no_silent_host")
async def test_a_stop_holds_an_arm_on_its_way_where_it_reads() -> None:
    """A stop that left the goal alone was a stop for the base: the arm finished its move, with a
    person's hand in the way or not. It is held once for each goal, since a servo under load reads
    below its goal and every failed verb ends in a stop, and never a gripper, which would open on
    what it holds. A new goal is a new move for a stop to end."""
    with FakeAlohaMiniHost(watchdog_s=10.0) as host:
        link = await _connected(host)
        key = "arm_left_elbow_flex.pos"
        grip = "arm_right_gripper.pos"
        await _lands(link, host, Intent.joint({"arm_left_elbow_flex": 80.0}, 2.0))
        await _lands(link, host, Intent(kind="gripper", params={"open": False, "side": "right"}))
        closed = host.state[grip]
        await _lands(link, host, Intent.joint({"arm_left_gripper": 70.0}, 0.2))
        host.slow = {key, grip, "arm_left_gripper.pos"}
        host.state[key] = 12.0
        host.state[grip] = closed + 40.0
        host.state["arm_left_gripper.pos"] = 41.0

        await link.stop()
        assert link.stop_left_going == ()
        await _until(lambda: host.actions[-1].get(key) == pytest.approx(12.0))
        assert host.actions[-1][grip] == pytest.approx(closed), "the jaws were sent what they read"
        assert host.actions[-1]["arm_left_gripper.pos"] == pytest.approx(70.0)

        host.state[key] = 11.0  # it sinks
        await link.stop()
        assert link._arm_goals[key] == pytest.approx(12.0), "a second stop walked it down"

        await _lands(link, host, Intent.joint({"arm_left_elbow_flex": 90.0}, 1.0))
        host.state[key] = 33.0
        await link.stop()
        assert link._arm_goals[key] == pytest.approx(33.0)
        await link.close()


@pytest.mark.usefixtures("_a_slow_machine_is_no_silent_host")
async def test_a_stop_holds_at_its_own_speed_and_leaves_no_replies_behind() -> None:
    """It asked again every 20 ms a host that answers one request a cycle, so the answer came back
    under an older token, most stops held nothing, and every reply it left behind made each read
    after it an older reading than the last, marked fresh. On the fake's own 30 Hz, the host's."""
    with FakeAlohaMiniHost(watchdog_s=10.0) as host:
        link = await _connected(host)
        key = "arm_left_elbow_flex.pos"
        for n in range(6):
            await _lands(link, host, Intent.joint({"arm_left_elbow_flex": 80.0 + n}, 2.0))
            host.slow = {key}
            host.state[key] = float(n)
            await link.stop()
            assert link.stop_left_going == (), "the stop had no reading to hold from"
            assert link._arm_goals[key] == pytest.approx(float(n))
            host.slow = set()

        host.slow = {key}
        host.state[key] = 55.0
        assert await link._pump(5000)
        assert link._state[key] == pytest.approx(55.0), "a reply left behind answered this request"
        await link.close()


class _Late:
    """A host with replies already waiting, behind its time. No sockets, so what is read when is
    what the test says. It answers the request it is sent only once those have been read."""

    def __init__(self, *waiting: tuple[bytes, float]) -> None:
        self.waiting = list(waiting)
        self.asked: list[bytes] = []
        self.said: list[dict[str, float]] = []
        self.next_reading = 12.0

    def open(self, host: str, cmd_port: int, obs_port: int) -> None: ...

    def send(self, text: str) -> None:
        self.said.append(json.loads(text))

    def _reply(self, token: bytes, reading: float) -> list[bytes]:
        return [token, json.dumps({"arm_left_elbow_flex.pos": reading}).encode()]

    def request(self, token: bytes, timeout_ms: int) -> list[bytes] | None:
        self.asked.append(token)
        self.waiting.append((token, self.next_reading))
        self.next_reading += 10.0
        return self.answer(timeout_ms)

    def answer(self, timeout_ms: int) -> list[bytes] | None:
        return self._reply(*self.waiting.pop(0)) if self.waiting else None

    def close(self) -> None: ...


async def test_a_stop_reads_down_to_the_answer_to_its_own_request() -> None:
    """A DEALER keeps a reply that came in behind its time, so a stop's first read can be the answer
    to an earlier request, however old: no goal is built from it, and nothing more is asked until it
    has its answer. That answer can be as old, where the host stuck between reading its motors and
    answering, with the servo gone on, so it is the answer after that."""
    key = "arm_left_elbow_flex.pos"
    host = _Late((b"1", 3.0), (b"2", 4.0))
    link = AlohaMiniZmq(client=host)
    link._token = 2
    link._arm_goals[key] = 80.0
    link._unheld.add(key)
    await link.stop()
    assert host.asked == [b"3", b"4"], "asking again leaves replies behind"
    assert link._arm_goals[key] == pytest.approx(22.0)
    assert link.stop_left_going == ()


async def test_a_read_reads_past_replies_behind_their_time() -> None:
    """Each read asks once and takes one, so a reply left behind by a host that stalled made every
    read for the rest of the session that much older than it said."""
    host = _Late((b"1", 3.0), (b"2", 4.0))
    link = AlohaMiniZmq(client=host)
    link._token = 2
    assert await link._pump(5)
    assert link._state["arm_left_elbow_flex.pos"] == pytest.approx(12.0)


async def test_a_stop_with_no_answer_stops_the_base_and_leaves_the_goal_and_says_so() -> None:
    """A goal built from an old reading is a move of its own (ADR-0026), and a reading takes a
    round trip here. So the wheels and the lift go first, the wait is short, that joint goes on,
    and the pilot is told which."""

    class Silent:
        """A host that takes commands and has stopped answering."""

        def __init__(self) -> None:
            self.told: list[str] = []

        def open(self, host: str, cmd_port: int, obs_port: int) -> None: ...

        def send(self, text: str) -> None:
            self.told.append(f"send {text}")

        def request(self, token: bytes, timeout_ms: int) -> None:
            self.told.append("ask")

        def answer(self, timeout_ms: int) -> None:
            self.told.append("listen")

        def close(self) -> None: ...

    silent = Silent()
    link = AlohaMiniZmq(client=silent)
    link._arm_goals["arm_left_elbow_flex.pos"] = 80.0
    link._unheld.add("arm_left_elbow_flex.pos")
    link._vel["x.vel"] = 0.2
    link._state["arm_left_elbow_flex.pos"] = 12.0  # known, and from before the silence
    started = time.monotonic()
    await link.stop()
    assert time.monotonic() - started < 2.0
    assert silent.told[0].startswith("send "), "the wheels waited on a reading"
    first = json.loads(silent.told[0].removeprefix("send "))
    assert first["x.vel"] == 0.0 and first[LIFT_VEL_KEY] == 0
    assert silent.told.count("ask") == 1
    assert link.stop_left_going == ("arm_left_elbow_flex",)
    assert link._arm_goals["arm_left_elbow_flex.pos"] == pytest.approx(80.0)


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
