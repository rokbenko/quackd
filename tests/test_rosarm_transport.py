"""`RosArmWs`, the one class in the industrial arm family that sends anything, run on the fake.

Each refusal at connect is one a stock bridge or a stock driver can earn, and says what it found:
a bridge that would hold a stop behind the move, an action it does not list, readings that name
other joints, a description with no travel. The goal is the one shape all four arms take, the
results are read as rosbridge and roslibpy hand them over, the stop is a cancel and a bounded wait
for its answer, and the heartbeat ends a run whose readings stop, whose socket drops or whose
driver stopped the arm itself. Every operation the transport sends is one of five, and none of
them powers, enables, un-brakes or un-stops an arm.
"""

from __future__ import annotations

import math
import re
import sys
from typing import Any

import pytest

from quackd.adapters.base import AdapterNotInstalled
from quackd.transport.base import HeartbeatError, Intent, TransportError
from quackd_rosarm.bodies import BODIES, arm_body
from quackd_rosarm.fake import FakeBridge, description_urdf
from quackd_rosarm.transport import (
    CANCEL_WAIT_S,
    RosArmWs,
    parse_address,
    trajectory_goal,
)
from quackd_rosbridge.ws import build_roslibpy

UR = arm_body("ur5e")
MOTO = arm_body("yaskawa-motomini")


def _over(bridge: FakeBridge, address: str | None = None) -> RosArmWs:
    return RosArmWs(
        address, body=bridge.body, roslibpy=bridge.roslibpy(), clock=bridge.clock, name="mock"
    )


async def _connected(body: Any = UR, address: str | None = None, **bridge: Any) -> tuple[Any, Any]:
    fake = FakeBridge(body, **bridge)
    transport = _over(fake, address)
    await transport.connect()
    return fake, transport


def _goal_of(fake: FakeBridge) -> dict[str, Any]:
    return next(op for op in reversed(fake.sent) if op["op"] == "send_action_goal")


# ── the address ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("robot_type", list(BODIES))
def test_every_key_defaults_from_the_body_record(robot_type: str) -> None:
    body = arm_body(robot_type)
    endpoint = parse_address("ws://cell.local:9090", body)
    assert (endpoint.host, endpoint.port, endpoint.secure) == ("cell.local", 9090, False)
    assert endpoint.action == body.action and endpoint.joint_states == body.joint_states
    assert endpoint.urdf_param == body.description_param
    assert endpoint.urdf_topic == body.description_topic
    assert endpoint.bridge_node == "/rosbridge_websocket" and endpoint.prefix == ""
    assert endpoint.status is (body.status is not None)


def test_the_iiwas_names_are_under_its_namespace() -> None:
    iiwa = parse_address(None, arm_body("kuka-lbr-iiwa-7-r800"))
    assert iiwa.action == "/lbr/joint_trajectory_controller/follow_joint_trajectory"
    assert iiwa.joint_states == "/lbr/joint_states"
    assert iiwa.urdf_param == "/lbr/robot_state_publisher:robot_description"


def test_the_address_names_what_a_bringup_changed() -> None:
    endpoint = parse_address(
        "wss://cell:9443?action=/arm/scaled_joint_trajectory_controller/follow_joint_trajectory"
        "&joint_states=/arm/joint_states&prefix=ur_&urdf_param=off&bridge_node=/arm/rosbridge"
        "&status=off",
        UR,
    )
    assert endpoint.secure and endpoint.port == 9443 and endpoint.prefix == "ur_"
    assert endpoint.action.startswith("/arm/") and endpoint.urdf_param is None
    assert endpoint.bridge_node == "/arm/rosbridge" and endpoint.status is False


@pytest.mark.parametrize(
    ("address", "said"),
    [
        ("http://cell:9090", "must start with ws:// or wss://"),
        ("ws://cell:9090?cmd_vel=/cmd_vel", "does not read cmd_vel"),
        ("ws://cell:9090?action=scaled/follow", "action='scaled/follow' must start with /"),
        ("ws://cell:9090?urdf_param=/robot_state_publisher", "must name a node and a parameter"),
        ("ws://cell:9090?prefix=ur/", "no / or space"),
        ("ws://cell:9090?status=maybe", "is on or off"),
    ],
)
def test_an_address_quackd_cannot_read_is_refused(address: str, said: str) -> None:
    with pytest.raises(TransportError, match=said):
        parse_address(address, UR)


# ── connect: each check a refusal that says what it found ────────────────────────────────────


async def test_a_bridge_that_would_hold_a_stop_behind_the_move_is_refused() -> None:
    with pytest.raises(TransportError) as refused:
        await _connected(new_thread=False)
    said = str(refused.value)
    assert "a stop would wait until the move had ended" in said and "#909" in said
    assert "send_action_goals_in_new_thread:=true" in said


async def test_a_bridge_whose_answer_cannot_be_read_is_refused_the_same_way() -> None:
    with pytest.raises(TransportError) as refused:
        await _connected(new_thread=None)
    said = str(refused.value)
    assert "cannot tell whether a stop would reach this arm" in said and "?bridge_node=" in said
    with pytest.raises(TransportError, match="cannot tell whether a stop"):
        await _connected(rosapi=False)


async def test_an_action_the_bridge_does_not_list_is_refused_with_the_ones_it_does() -> None:
    with pytest.raises(TransportError, match=r"lists no action server .* it lists none"):
        await _connected(server=False)
    with pytest.raises(TransportError, match="it lists /scaled_joint_trajectory_controller"):
        await _connected(address="ws://localhost:9090?action=/joint_trajectory_controller/x")


async def test_readings_that_name_other_joints_are_refused_and_named() -> None:
    with pytest.raises(TransportError) as refused:
        await _connected(prefix="left_")
    said = str(refused.value)
    assert "names left_wrist_3_joint" in said and "?prefix=" in said
    # the same bridge, with the prefix said, connects
    _fake, transport = await _connected(address="ws://localhost:9090?prefix=left_", prefix="left_")
    assert transport.wire_joints[0] == "left_shoulder_pan_joint"


async def test_no_description_and_a_joint_with_no_limit_are_refused() -> None:
    with pytest.raises(TransportError, match="no description of the arm could be read"):
        await _connected(description=None)
    urdf = description_urdf(UR).replace('velocity="3.141592653589793"', "", 1)
    with pytest.raises(TransportError, match="shoulder_pan_joint has no lower, upper and velocity"):
        await _connected(urdf=urdf)


@pytest.mark.parametrize(
    ("limit", "value"),
    [
        ("velocity", "nan"),
        ("velocity", "inf"),
        ("velocity", "0"),
        ("velocity", "-3.14"),
        ("lower", "-inf"),
        ("upper", "nan"),
        ("lower", "7.0"),
    ],
)
async def test_a_limit_that_is_no_number_or_no_speed_is_refused(limit: str, value: str) -> None:
    """`float()` reads nan and inf, so a description could hand the ceiling a speed that lifts
    it: a nan first made the longest joint's need nan and the move went out at the time asked,
    and an inf made every need zero. A speed of zero or less, and a lower above the upper, are no
    limits either. The joint is named, and nothing is sent."""
    urdf = description_urdf(UR)
    head, _, tail = urdf.partition('<joint name="shoulder_pan_joint"')
    start = tail.index(f'{limit}="') + len(limit) + 2
    tail = tail[:start] + value + tail[tail.index('"', start) :]
    fake = FakeBridge(UR, urdf=f'{head}<joint name="shoulder_pan_joint"{tail}')
    with pytest.raises(TransportError, match="shoulder_pan_joint has lower"):
        await _over(fake).connect()
    assert "send_action_goal" not in fake.operations()


@pytest.mark.parametrize(
    "robot_type", ["ur5e", "kuka-lbr-iisy-3-r760", "kuka-lbr-iiwa-7-r800"], ids=str
)
async def test_a_goal_an_earlier_run_left_running_refuses_the_connect_until_it_ends(
    robot_type: str,
) -> None:
    """A run that went quiet in the middle of a move leaves the arm finishing it
    (`up.ONE_MOVE_WHEN_QUIET`), and the next run's connect is the first to meet it. The action's
    own status, which the controller latches, says a goal is still executing, so the connect is
    refused and nothing is sent over it; once that move has ended, the same arm connects."""
    body = arm_body(robot_type)
    fake = FakeBridge(body)
    earlier = _over(fake)
    await earlier.connect()
    goal = dict((await earlier.get_state()).extras["joints"])
    goal[body.joints[0]] += 20.0
    assert (await earlier.send_intent(Intent.joint(goal, 6.0))).accepted
    await earlier.sleep(1.0)
    fake.client_gone()  # no cancel, no close: the earlier run is simply gone
    with pytest.raises(TransportError) as refused:
        await _over(fake).connect()
    said = str(refused.value)
    assert f"{body.action} is still running a goal sent before quackd connected (executing)" in (
        said
    )
    assert "stop it on the pendant, then connect again" in said
    asked = [op for op in fake.sent if op["op"] == "subscribe" and "_action" in op["topic"]]
    assert asked[-1] == {
        "op": "subscribe",
        "topic": f"{body.action}/_action/status",
        "type": "action_msgs/msg/GoalStatusArray",
    }
    assert fake.arm.goal is not None, "nothing ended it: the arm goes on with its move"
    ended = [op for op in fake.sent if op["op"] == "unsubscribe" and "_action" in op["topic"]]
    assert ended and ended[-1]["topic"] == f"{body.action}/_action/status", "it let go of it"
    fake.clock.advance(6.0)
    assert fake.arm.goal is None
    later = _over(fake)
    await later.connect()
    assert (await later.get_state()).extras["move"] is None


@pytest.mark.parametrize(
    ("statuses", "said"),
    [
        ([1], "a goal sent before quackd connected (accepted)"),
        ([2], "a goal sent before quackd connected (executing)"),
        ([3], "a goal sent before quackd connected (canceling)"),
        ([4, 2, 3], "2 goals sent before quackd connected (executing, canceling)"),
        ([4, 5, 6], None),
        ([], None),
    ],
)
async def test_only_a_goal_that_has_not_ended_refuses_the_connect(
    statuses: list[int], said: str | None
) -> None:
    """`up.MSG_GOAL_STATUS`: ACCEPTED, EXECUTING and CANCELING are a goal that has not ended, and
    each refuses the connect, named; SUCCEEDED, CANCELED and ABORTED are goals the server keeps
    after they ended, and an empty array is a server whose goals have all expired."""
    fake = FakeBridge(UR)
    fake._goal_statuses = {
        "status_list": [
            {"goal_info": {"goal_id": {"uuid": ""}, "stamp": {"sec": 0, "nanosec": 0}}, "status": s}
            for s in statuses
        ]
    }
    transport = _over(fake)
    if said is None:
        await transport.connect()
        return
    with pytest.raises(TransportError, match=f"is still running {re.escape(said)}"):
        await transport.connect()


async def test_a_goal_left_running_on_a_motomini_is_not_seen_at_connect() -> None:
    """`up.MOTO_NO_ACTION_STATUS`: MotoROS2 publishes no goal status, so at connect nothing is
    heard of a goal still running and nothing is refused, as the page and Known limitations say."""
    fake = FakeBridge(MOTO)
    earlier = _over(fake)
    await earlier.connect()
    goal = dict((await earlier.get_state()).extras["joints"])
    goal["joint_1"] += 20.0
    assert (await earlier.send_intent(Intent.joint(goal, 6.0))).accepted
    await earlier.sleep(1.0)
    fake.client_gone()
    later = _over(fake)
    await later.connect()
    assert fake.arm.goal is not None, "the earlier goal still runs"
    await later.close()


async def test_the_description_is_read_off_the_latched_topic_when_no_parameter_has_it() -> None:
    _fake, transport = await _connected(description="topic")
    assert transport.travel_deg["elbow_joint"] == (-180.0, 180.0)
    assert transport.speed_deg_s["elbow_joint"] == 180.0


async def test_an_unreachable_bridge_says_where_it_looked() -> None:
    with pytest.raises(TransportError, match="cannot reach ws://localhost:9090"):
        await _connected(reachable=False)


def test_the_missing_extra_names_this_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "roslibpy", None)
    with pytest.raises(AdapterNotInstalled, match=r"quackd\[rosarm\]") as missing:
        build_roslibpy("localhost", 9090, False, adapter="rosarm", extra="quackd[rosarm]")
    assert missing.value.adapter == "rosarm"


# ── the goal ─────────────────────────────────────────────────────────────────────────────────


async def test_the_goal_is_every_joint_two_points_at_rest_in_radians_starting_now() -> None:
    fake, transport = await _connected()
    goal = {joint: 0.0 for joint in UR.joints} | {"elbow_joint": 30.0}
    ack = await transport.send_intent(Intent.joint(goal, 6.5))
    assert ack.accepted, ack.reason
    sent = _goal_of(fake)
    assert sent["action"] == UR.action
    assert sent["action_type"] == "control_msgs/action/FollowJointTrajectory"
    trajectory = sent["args"]["trajectory"]
    assert trajectory["header"]["stamp"] == {"sec": 0, "nanosec": 0}
    assert trajectory["joint_names"] == list(UR.joints), "the controller's order, every joint"
    start, end = trajectory["points"]
    assert start["time_from_start"] == {"sec": 0, "nanosec": 0}
    assert end["time_from_start"] == {"sec": 6, "nanosec": 500_000_000}
    for point in (start, end):
        assert point["velocities"] == [0.0] * 6 and point["accelerations"] == [0.0] * 6
        assert point["effort"] == []
    assert end["positions"][2] == pytest.approx(math.radians(30.0))
    assert start["positions"] == [0.0] * 6, "where the arm reads, in radians"


def test_a_duration_is_written_in_seconds_and_nanoseconds() -> None:
    goal = trajectory_goal(["a"], [0.0], [1.0], 0.9999999999)
    assert goal["trajectory"]["points"][1]["time_from_start"] == {"sec": 1, "nanosec": 0}


async def test_a_partial_goal_and_a_second_goal_are_never_sent() -> None:
    fake, transport = await _connected()
    partial = await transport.send_intent(Intent.joint({"elbow_joint": 10.0}, 2.0))
    assert not partial.accepted and "leaves out shoulder_pan_joint" in str(partial.reason)
    goal = {joint: 5.0 for joint in UR.joints}
    assert (await transport.send_intent(Intent.joint(goal, 4.0))).accepted
    second = await transport.send_intent(Intent.joint(goal, 4.0))
    assert not second.accepted and "never sends a goal over one" in str(second.reason)
    assert fake.operations().count("send_action_goal") == 1


async def test_readings_are_kept_by_name_whatever_order_they_come_in() -> None:
    _fake, transport = await _connected(body=arm_body("kuka-lbr-iisy-3-r760"))
    state = await transport.get_state()
    assert list(state.extras["joints"]) == [f"joint_{n}" for n in range(1, 7)]
    assert state.extras["joints"]["joint_2"] == pytest.approx(-90.0, abs=0.1)
    assert state.extras["stale"] is None, "NaN velocities are no stale reading: positions are read"


# ── the results ──────────────────────────────────────────────────────────────────────────────


async def _run(transport: RosArmWs, seconds: float) -> dict[str, Any]:
    deadline = transport.now() + seconds
    while transport.now() < deadline:
        move = (await transport.get_state()).extras["move"]
        if move and move["status"] != "running":
            return dict(move)
        await transport.sleep(0.1)
    return dict((await transport.get_state()).extras["move"])


async def test_a_goal_that_ran_to_its_end_reads_succeeded() -> None:
    _fake, transport = await _connected()
    await transport.send_intent(Intent.joint({joint: 3.0 for joint in UR.joints}, 1.0))
    move = await _run(transport, 3.0)
    assert move["status"] == "succeeded" and move["error_code"] == 0
    assert move["started"], "the feedback said it began"


async def test_an_aborted_goal_carries_the_controllers_own_words() -> None:
    fake, transport = await _connected()
    await transport.send_intent(Intent.joint({joint: 30.0 for joint in UR.joints}, 4.0))
    await transport.sleep(1.0)
    fake.arm.protective_stop()
    move = await _run(transport, 1.0)
    assert move["status"] == "aborted" and move["error_code"] == -1
    assert move["error_string"] == "Current goal cancelled during deactivate transition."


async def test_a_rejected_goal_reads_refused_with_the_bridges_words() -> None:
    fake, transport = await _connected()
    fake.arm.active = False  # the UR's program stopped: the controller is inactive
    await transport.send_intent(Intent.joint({joint: 3.0 for joint in UR.joints}, 1.0))
    move = await _run(transport, 1.0)
    assert move["status"] == "refused" and move["error_string"] == "Action goal was rejected"


async def test_motoros2s_aborted_goal_carries_its_code() -> None:
    fake, transport = await _connected(body=MOTO)
    fake.arm.servo_on = False
    here = (await transport.get_state()).extras["joints"]
    await transport.send_intent(Intent.joint({j: here[j] + 3.0 for j in MOTO.joints}, 1.0))
    move = await _run(transport, 1.0)
    assert move["status"] == "aborted" and move["error_code"] == -100106
    assert move["error_string"].startswith("Servo power is OFF")


# ── the stop ─────────────────────────────────────────────────────────────────────────────────


async def test_a_stop_cancels_the_running_goal_and_hears_it_end() -> None:
    fake, transport = await _connected()
    await transport.send_intent(Intent.joint({joint: 40.0 for joint in UR.joints}, 8.0))
    await transport.sleep(2.0)
    await transport.stop()
    assert transport.stop_error is None
    assert (await transport.get_state()).extras["move"]["status"] == "canceled"
    assert fake.operations().count("cancel_action_goal") == 1
    held = dict(fake.arm.positions)
    await transport.sleep(2.0)
    assert fake.arm.positions == held, "the controller holds where the cancel found it"


async def test_a_stop_with_nothing_running_sends_nothing() -> None:
    fake, transport = await _connected()
    await transport.stop()
    assert transport.stop_error is None and "cancel_action_goal" not in fake.operations()


async def test_a_stop_that_cannot_reach_the_arm_says_so() -> None:
    fake, transport = await _connected()
    await transport.send_intent(Intent.joint({joint: 40.0 for joint in UR.joints}, 8.0))
    fake.drop()
    await transport.stop()
    assert "no cancel could be sent" in str(transport.stop_error)
    assert "cancel_action_goal" not in fake.operations()


async def test_a_cancel_nobody_answers_is_a_stop_not_delivered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake, transport = await _connected()
    await transport.send_intent(Intent.joint({joint: 40.0 for joint in UR.joints}, 8.0))
    monkeypatch.setattr(fake.arm, "cancel", lambda goal_id: None)  # a bridge that loses it
    started = transport.now()
    await transport.stop()
    assert f"no answer to the cancel within {CANCEL_WAIT_S:g} s" in str(transport.stop_error)
    assert transport.now() - started == pytest.approx(CANCEL_WAIT_S, abs=0.1)


async def test_close_cancels_a_goal_still_running() -> None:
    fake, transport = await _connected()
    await transport.send_intent(Intent.joint({joint: 40.0 for joint in UR.joints}, 8.0))
    await transport.close()
    ops = fake.operations()
    assert ops.index("cancel_action_goal") > ops.index("send_action_goal")
    assert (await transport.get_state()).extras["move"]["status"] == "canceled", "it waited"
    assert not fake.terminated, "its own socket and nobody else's"


async def test_no_goal_goes_out_on_a_dropped_socket_and_the_heartbeat_lets_go_for_good() -> None:
    """roslibpy would hold a goal handed over while the socket is down and send it when its
    factory reconnected by itself (`up.QUEUED_UNTIL_READY`), with nobody watching: the transport
    hands none over, and the heartbeat that finds the socket down lets go of the client, so it
    hands over nothing more, the bridge's return included. Never by `terminate()`, which would
    end every roslibpy client's socket in the process (`up.ONE_REACTOR`)."""
    fake, transport = await _connected()
    fake.drop()
    goal = {joint: 10.0 for joint in UR.joints}
    sent = await transport.send_intent(Intent.joint(goal, 4.0))
    assert not sent.accepted and "is not connected, so no goal is sent" in str(sent.reason)
    with pytest.raises(HeartbeatError, match="quackd let go of the client"):
        await transport.heartbeat()
    assert not fake.terminated
    fake.reconnect()
    assert "send_action_goal" not in fake.operations()
    refused = await transport.send_intent(Intent.joint(goal, 4.0))
    assert not refused.accepted, "a client let go of drives nothing more"


async def test_a_stop_after_a_lost_cancel_sends_it_again(monkeypatch: pytest.MonkeyPatch) -> None:
    """A cancel the bridge lost is answered by nobody, and the next stop is the one that has to
    reach the arm: it is sent again, and so is the one a close sends."""
    fake, transport = await _connected()
    await transport.send_intent(Intent.joint({joint: 40.0 for joint in UR.joints}, 8.0))
    with monkeypatch.context() as patched:
        patched.setattr(fake.arm, "cancel", lambda goal_id: None)  # a bridge that loses it
        await transport.stop()
    assert "no answer to the cancel" in str(transport.stop_error)
    await transport.stop()
    assert transport.stop_error is None
    assert fake.operations().count("cancel_action_goal") == 2
    assert (await transport.get_state()).extras["move"]["status"] == "canceled"


async def test_close_cancels_a_goal_whose_first_cancel_was_lost(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake, transport = await _connected()
    await transport.send_intent(Intent.joint({joint: 40.0 for joint in UR.joints}, 8.0))
    with monkeypatch.context() as patched:
        patched.setattr(fake.arm, "cancel", lambda goal_id: None)
        await transport.stop()
    await transport.close()
    assert fake.operations().count("cancel_action_goal") == 2


async def test_a_goal_at_the_published_edge_is_held_to_the_descriptions_own_radians() -> None:
    """The travel is read to a millionth of a degree and published to a tenth, inward, so its
    edge can lie a hair past the description's radians: 169.9999996 degrees reads as 170.0 and
    is published so, and the goal sent at 170.0 is the description's radian. A goal further out
    is refused."""
    upper = math.radians(169.9999996)
    urdf = description_urdf(UR)
    head, _, tail = urdf.partition('<joint name="shoulder_pan_joint"')
    for limit, value in (("lower", repr(-upper)), ("upper", repr(upper))):
        start = tail.index(f'{limit}="') + len(limit) + 2
        tail = tail[:start] + value + tail[tail.index('"', start) :]
    fake, transport = await _connected(urdf=f'{head}<joint name="shoulder_pan_joint"{tail}')
    assert transport.travel_deg["shoulder_pan_joint"] == (-170.0, 170.0)
    fake.arm.positions["shoulder_pan_joint"] = math.radians(169.0)
    fake.arm.hold()  # jogged a degree inside the edge
    await transport.sleep(0.1)
    goal = {joint: 0.0 for joint in UR.joints}
    goal["shoulder_pan_joint"] = 170.0
    assert (await transport.send_intent(Intent.joint(goal, 2.0))).accepted
    sent = _goal_of(fake)["args"]["trajectory"]["points"][-1]["positions"]
    assert sent[0] == upper
    await transport.stop()
    goal["shoulder_pan_joint"] = 170.1
    refused = await transport.send_intent(Intent.joint(goal, 2.0))
    assert not refused.accepted and "lies outside the travel the description gives" in str(
        refused.reason
    )


@pytest.mark.parametrize("bad", [math.nan, math.inf])
async def test_a_goal_that_is_no_number_is_refused_at_the_transport(bad: float) -> None:
    fake, transport = await _connected()
    goal = {joint: 0.0 for joint in UR.joints}
    goal["elbow_joint"] = bad
    assert not (await transport.send_intent(Intent.joint(goal, 4.0))).accepted
    assert not (
        await transport.send_intent(Intent.joint({j: 0.0 for j in UR.joints}, bad))
    ).accepted
    assert "send_action_goal" not in fake.operations()


async def test_a_rated_speed_above_the_stock_description_is_held_to_the_stock_one() -> None:
    """A figure no arm has, 1e300 radians a second here, would lift the ceiling with it: the
    rated speed is never taken above the stock description's own."""
    urdf = description_urdf(UR)
    head, _, tail = urdf.partition('<joint name="shoulder_pan_joint"')
    start = tail.index('velocity="') + len("velocity=") + 1
    tail = tail[:start] + "1e300" + tail[tail.index('"', start) :]
    _fake, transport = await _connected(urdf=f'{head}<joint name="shoulder_pan_joint"{tail}')
    assert transport.speed_deg_s["shoulder_pan_joint"] == 180.0


async def test_the_transport_holds_the_ceiling_whoever_sends_the_intent() -> None:
    """`move_joints` keeps the ceiling, and the transport keeps it again: a goal that would peak
    past a tenth of a joint's rated speed, or one longer than a move may take, is not sent."""
    fake, transport = await _connected()
    here = (await transport.get_state()).extras["joints"]
    fast = {j: here[j] for j in UR.joints}
    fast["shoulder_pan_joint"] += 30.0  # 30 degrees in a second peaks at 56 degrees a second
    refused = await transport.send_intent(Intent.joint(fast, 1.0))
    assert not refused.accepted and "past a tenth of its rated 180" in str(refused.reason)
    long = await transport.send_intent(Intent.joint(dict(here), 13.0))
    assert not long.accepted and "longer than the 12 s a move may take" in str(long.reason)
    assert "send_action_goal" not in fake.operations()


# ── the heartbeat ────────────────────────────────────────────────────────────────────────────


async def test_the_heartbeat_ends_a_run_whose_readings_stopped() -> None:
    fake, transport = await _connected()
    await transport.heartbeat()
    fake.quiet()
    await transport.sleep(0.6)
    with pytest.raises(HeartbeatError, match=r"no new /joint_states for 0\.6 s"):
        await transport.heartbeat()


async def test_the_heartbeat_ends_a_run_whose_socket_dropped() -> None:
    fake, transport = await _connected()
    fake.drop()
    with pytest.raises(HeartbeatError, match="not connected"):
        await transport.heartbeat()


async def test_the_heartbeat_ends_a_run_whose_driver_stopped_the_arm() -> None:
    fake, transport = await _connected()
    fake.arm.protective_stop()
    await transport.sleep(0.1)
    with pytest.raises(HeartbeatError, match="safety mode PROTECTIVE_STOP"):
        await transport.heartbeat()
    state = await transport.get_state()
    assert state.extras["driver"] == {
        "read": True,
        "can_move": False,
        "says": "program not running, safety mode PROTECTIVE_STOP",
    }


async def test_an_arm_already_stopped_at_connect_refuses_moves_and_lets_a_lookout_run() -> None:
    """A driver that says from the start that it cannot move is a precondition, not a fault: the
    heartbeat is for an arm that stopped itself while quackd watched."""
    fake = FakeBridge(MOTO)
    fake.arm.e_stopped = True
    transport = _over(fake)
    await transport.connect()
    await transport.heartbeat()
    state = await transport.get_state()
    assert state.extras["driver"]["can_move"] is False
    assert "e-stopped" in state.extras["driver"]["says"]


# ── what it sends ────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("robot_type", list(BODIES))
async def test_it_sends_a_goal_a_cancel_and_questions_and_nothing_that_powers_an_arm(
    robot_type: str,
) -> None:
    body = arm_body(robot_type)
    fake, transport = await _connected(body=body)
    here = (await transport.get_state()).extras["joints"]
    sent = await transport.send_intent(Intent.joint({j: here[j] + 1.0 for j in body.joints}, 2.0))
    assert sent.accepted, sent.reason
    await transport.sleep(0.5)
    await transport.stop()
    await transport.close()
    assert set(fake.operations()) <= {
        "subscribe",
        "unsubscribe",
        "call_service",
        "send_action_goal",
        "cancel_action_goal",
    }
    asked = {op["service"] for op in fake.sent if op["op"] == "call_service"}
    assert asked == {"/rosapi/get_param", "/rosapi/action_servers"}
    names = {op["args"]["name"] for op in fake.sent if op.get("service") == "/rosapi/get_param"}
    assert names == {
        "/rosbridge_websocket:send_action_goals_in_new_thread",
        body.description_param,
    }
    actions = {op["action"] for op in fake.sent if "action" in op}
    assert actions == {body.action}
