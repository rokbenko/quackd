"""The rosbridge adapter: a wheeled base that takes a Twist, and the messages it speaks."""

from __future__ import annotations

import asyncio
import base64
import importlib.util
import inspect
import io
import json
import math
import threading
import time
from collections.abc import Callable
from typing import Any

import pytest
from PIL import Image

import roboglot_rosbridge.ws as ws_module
from roboglot.adapters.base import AdapterNotInstalled
from roboglot.adapters.factory import describe, make_adapter, parse_robot_spec
from roboglot.perception.color_blob import ColorBlobDetector
from roboglot.safety import Aborted, Executor, Heartbeat
from roboglot.space.places import Place, PlaceBook, PlaceStore
from roboglot.transport.base import HeartbeatError, Intent, TransportError
from roboglot.verbs.core import MOVE_RESEND_S, TICK_S, speed_limits
from roboglot.verbs.navigate import Gait
from roboglot.verbs.places import PLACE_VERBS, GoToPlaceParams
from roboglot.verbs.registry import VerbContext, VerbResult, registry_from_manifest
from roboglot_microduck import microduck_manifest
from roboglot_rosbridge import DATASHEET, RosbridgeAdapter, rosbridge_manifest
from roboglot_rosbridge.introspection import datasheet_from_introspection, parse_urdf
from roboglot_rosbridge.mock import MOCK_URDF, RosbridgeMock, mock_introspection
from roboglot_rosbridge.ws import (
    ANSWER_WAIT_S,
    FRAME_STALE_FLOOR_S,
    LISTEN_FLOOR_S,
    LISTEN_PERIODS,
    SILENT_BEATS,
    SLOWEST_PACE_S,
    STALE_FLOOR_S,
    STALE_PERIODS,
    RosbridgeWs,
    decode_compressed_image,
    parse_address,
    twist_message,
    yaw_from_quaternion,
)

PLACES = {"map", "mark_place", "forget_place", "go_to_place"}
MOCK_VERBS = {
    "observe",
    "report_state",
    "stop",
    "move",
    "go_to",
    "search_scan",
    "approach_and",
    "introspect",
    *PLACES,
}
NO_ROSLIBPY = importlib.util.find_spec("roslibpy") is None


def test_the_address_carries_the_topics() -> None:
    default = parse_address(None)
    assert (default.host, default.port, default.secure) == ("localhost", 9090, False)
    assert (default.cmd_vel, default.odom, default.image) == ("/cmd_vel", "/odom", None)
    full = parse_address("wss://robot.local:9443?cmd_vel=/base/cmd_vel&image=/cam/compressed")
    assert full.secure and full.url == "wss://robot.local:9443"
    assert full.cmd_vel == "/base/cmd_vel" and full.image == "/cam/compressed"
    assert full.odom == "/odom"
    with pytest.raises(TransportError, match="ws://"):
        parse_address("http://robot.local:9090")
    with pytest.raises(TransportError, match="must start with /"):
        parse_address("ws://robot.local:9090?cmd_vel=cmd_vel")


def test_manifest_is_a_wheeled_base_with_twist_only() -> None:
    m = rosbridge_manifest("mock", camera=True)
    assert m.embodiment == "wheeled" and m.mobility == "wheeled" and m.intents == ["twist"]
    assert set(m.verb_names()) == MOCK_VERBS
    assert not any(m.provides(v) for v in ("say", "gaze", "kick", "sit", "express"))
    assert m.provides("walk_to") and m.provides("walk")  # the aliases of go_to and move
    assert m.safety_authority.native == "none" and not m.safety_authority.deadman
    assert m.limits == {"max_vx": 0.3, "max_vy": 0.0, "max_wz": 1.0}
    # the mock serves a canned description, so its static row already knows the body
    described = describe(parse_robot_spec("rosbridge:mock"))
    assert described == rosbridge_manifest("mock", camera=True, datasheet=described.datasheet)
    assert described.datasheet is not None and described.datasheet.mass_kg is not None
    assert described.datasheet.mass_kg.value == 11.0

    blind = describe(parse_robot_spec("rosbridge:ws"))
    assert set(blind.verb_names()) == {"report_state", "stop", "move", "introspect", *PLACES}
    assert blind.sensors == ["odometry"] and blind.extras["image"] is None
    # a name says nothing about a body: the ws backend knows nothing until it has asked
    assert blind.datasheet is not None and blind.datasheet.mass_kg is None
    assert blind.datasheet.dof is None


async def test_mock_base_drives_coasts_on_silence_and_reaches_the_ball() -> None:
    adapter = RosbridgeAdapter(RosbridgeMock())
    manifest = await adapter.connect()
    ex = Executor(
        registry_from_manifest(manifest, adapter),
        adapter,
        detector=ColorBlobDetector(),
        manifest=manifest,
    )
    mock = adapter.transport
    assert isinstance(mock, RosbridgeMock)
    assert (await ex.run_verb("move", {"vx": 0.2, "duration_s": 1.0})).ok
    assert abs(mock.x - 0.2) < 0.03 and mock.twists[-1] == {"vx": 0.0, "vy": 0.0, "wz": 0.0}
    # a raw Twist with nobody re-sending it: the deadman coasts the base to zero
    x0 = mock.x
    assert (await adapter.send_intent(Intent.move(0.2, 0.0, 0.0))).accepted
    await adapter.sleep(2.0)
    assert 0.09 < mock.x - x0 < 0.13
    assert not (await adapter.send_intent(Intent.look(1.0, 0.0, 0.0))).accepted
    found = await ex.run_verb("search_scan", {"target": "ball"})
    assert found.ok and found.data["steps"] == 0  # 18 degrees off the nose: in view already
    reached = await ex.run_verb("go_to", {"target": "ball"})
    assert reached.ok, reached.summary
    rel = mock.ball_relative()
    assert rel is not None and rel[0] < 0.4
    state = await ex.run_verb("report_state")
    assert state.ok and state.data["state"]["extras"]["odom"]["x"] > 0.8
    assert (await adapter.health()).ok


async def test_speed_limits_come_from_the_manifest() -> None:
    assert speed_limits(None) == (0.3, 0.2, 1.5)
    # the Microduck narrows the schema's turn to its own, 1.0 rad/s from 0.21.0
    assert speed_limits(microduck_manifest("mock")) == (0.3, 0.2, 1.0)
    adapter = RosbridgeAdapter(RosbridgeMock())
    await adapter.connect()
    slow = rosbridge_manifest("mock", camera=True, max_vx=0.1, max_wz=0.5)
    ex = Executor(registry_from_manifest(slow, adapter), adapter, manifest=slow)
    res = await ex.run_verb("move", {"vx": 0.3, "wz": 1.0, "duration_s": 0.5})
    assert res.ok and "clamped" in res.summary
    mock = adapter.transport
    assert isinstance(mock, RosbridgeMock)
    assert mock.twists[0] == {"vx": 0.1, "vy": 0.0, "wz": 0.5}


class FakeTopic:
    def __init__(self, ros: Any, name: str, message_type: str) -> None:
        self.name = name
        self.message_type = message_type
        self.published: list[dict[str, Any]] = []
        self.callback: Any = None
        self.unsubscribed = False
        self.unadvertised = False

    def publish(self, message: Any) -> None:
        self.published.append(dict(message))

    def subscribe(self, callback: Any) -> None:
        self.callback = callback

    def unsubscribe(self) -> None:
        self.unsubscribed = True

    def unadvertise(self) -> None:
        self.unadvertised = True


class FakeRos:
    def __init__(self) -> None:
        self.is_connected = False
        self.runs = 0
        self.closed = 0
        self.terminated = False

    def run(self, timeout: float = 5.0) -> None:
        self.runs += 1
        self.is_connected = True

    def close(self, timeout: float = 5.0) -> None:
        self.closed += 1
        self.is_connected = False

    def terminate(self) -> None:
        self.terminated = True
        self.is_connected = False


def _png(color: tuple[int, int, int], size: tuple[int, int] = (8, 6), fmt: str = "PNG") -> str:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format=fmt, quality=95)
    return base64.b64encode(buf.getvalue()).decode("ascii")


async def test_ws_backend_speaks_verified_topics_and_messages() -> None:
    ros = FakeRos()
    topics: dict[str, FakeTopic] = {}

    def factory(client: Any, name: str, message_type: str) -> FakeTopic:
        return topics.setdefault(name, FakeTopic(client, name, message_type))

    ws = RosbridgeWs(
        "ws://robot.local:9090?image=/cam/compressed&cmd_vel=/base/cmd_vel",
        ros=ros,
        topic_factory=factory,
    )
    adapter = RosbridgeAdapter(ws)
    manifest = await adapter.connect()
    assert ros.runs == 1 and set(topics) == {"/base/cmd_vel", "/odom", "/cam/compressed"}
    assert topics["/base/cmd_vel"].message_type == "geometry_msgs/msg/Twist"
    assert topics["/odom"].message_type == "nav_msgs/msg/Odometry"
    assert topics["/cam/compressed"].message_type == "sensor_msgs/msg/CompressedImage"
    assert manifest.provides("observe") and manifest.extras["image"] == "/cam/compressed"
    assert manifest.extras["cmd_vel"] == "/base/cmd_vel"

    assert (await adapter.send_intent(Intent.move(0.2, 0.0, 0.5))).accepted
    assert topics["/base/cmd_vel"].published == [twist_message(0.2, 0.0, 0.5)]
    assert twist_message(0.2, 0.0, 0.5) == {
        "linear": {"x": 0.2, "y": 0.0, "z": 0.0},
        "angular": {"x": 0.0, "y": 0.0, "z": 0.5},
    }
    before = await adapter.get_state()
    assert before.x is None and before.extras["odom_seen"] is False
    topics["/odom"].callback(
        {
            "pose": {
                "pose": {
                    "position": {"x": 1.0, "y": 2.0, "z": 0.0},
                    "orientation": {
                        "x": 0.0,
                        "y": 0.0,
                        "z": math.sin(math.pi / 4),
                        "w": math.cos(math.pi / 4),
                    },
                }
            }
        }
    )
    after = await adapter.get_state()
    assert after.x == 1.0 and after.y == 2.0 and after.theta is not None
    assert abs(after.theta - math.pi / 2) < 1e-6
    assert await adapter.get_frame() is None  # nothing published on the image topic yet
    topics["/cam/compressed"].callback(
        {"format": "rgb8; png compressed rgb8", "data": _png((255, 0, 0))}
    )
    frame = await adapter.get_frame()
    assert frame is not None and frame.size == (8, 6) and frame.getpixel((1, 1)) == (255, 0, 0)
    topics["/cam/compressed"].callback(
        {"format": "bgr8; jpeg compressed bgr8", "data": _png((255, 0, 0), fmt="JPEG")}
    )
    swapped = await adapter.get_frame()
    assert swapped is not None
    r, _g, b = swapped.getpixel((1, 1))
    assert b > 200 and r < 50  # the format said bgr8, so the channels were swapped
    await adapter.stop()
    assert topics["/base/cmd_vel"].published[-1] == twist_message(0.0, 0.0, 0.0)
    await adapter.heartbeat()
    ros.is_connected = False
    with pytest.raises(HeartbeatError):
        await adapter.heartbeat()
    ros.is_connected = True
    await adapter.close()
    assert topics["/odom"].unsubscribed and topics["/cam/compressed"].unsubscribed
    # its own socket closed, and roslibpy's reactor, which every client in the process shares,
    # left running for the others
    assert topics["/base/cmd_vel"].unadvertised and ros.closed == 1 and not ros.terminated
    assert topics["/base/cmd_vel"].published[-1] == twist_message(0.0, 0.0, 0.0)


def test_message_helpers() -> None:
    assert abs(yaw_from_quaternion({"x": 0, "y": 0, "z": 0, "w": 1})) < 1e-9
    with pytest.raises(ValueError, match="jpeg or png"):
        decode_compressed_image({"format": "rgb8; tiff compressed", "data": _png((1, 2, 3))})
    plain = decode_compressed_image({"format": "", "data": _png((0, 255, 0))})
    assert plain.getpixel((0, 0)) == (0, 255, 0)


@pytest.mark.skipif(not NO_ROSLIBPY, reason="roslibpy is installed here")
async def test_ws_backend_without_the_extra_names_it() -> None:
    adapter = make_adapter("rosbridge:ws", address="ws://robot.local:9090")
    with pytest.raises(AdapterNotInstalled, match=r"roboglot\[rosbridge\]"):
        await adapter.connect()


def test_the_builder_both_transports_call_names_whichever_extra_asked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The roslibpy builders were lifted out of `RosbridgeWs._build` into one function the
    industrial arm family calls too, and a machine without roslibpy is still told to install the
    extra of the adapter that needed it."""
    import sys

    from roboglot_rosbridge.ws import RosbridgeWs, build_roslibpy

    monkeypatch.setitem(sys.modules, "roslibpy", None)
    with pytest.raises(AdapterNotInstalled, match=r"roboglot\[rosbridge\]"):
        RosbridgeWs(address="ws://robot.local:9090")._build()
    with pytest.raises(AdapterNotInstalled, match=r"roboglot\[rosarm\]"):
        build_roslibpy("robot.local", 9090, False, adapter="rosarm", extra="roboglot[rosarm]")


async def test_the_base_describes_itself_as_it_did_before_the_builder_moved() -> None:
    """`tests/golden/manifest_digests.json` holds the Microduck's and Huebot's descriptions only,
    so the base's are pinned here: each digest was computed on origin/main before the builders
    left `RosbridgeWs._build`, and is what it computes now."""
    from roboglot_rosbridge import describe, rosbridge_manifest

    # the mock's and the camera's offer go_to, and moved with its stop_distance's bound and words
    assert describe("mock").digest() == "1c5be7305e64cd93"
    assert describe("ws").digest() == "a66b6376ea69717f"
    adapter = make_adapter("rosbridge:mock")
    assert (await adapter.connect()).digest() == "1c5be7305e64cd93"
    camera = rosbridge_manifest("ws", camera=True, image="/camera/compressed")
    assert camera.digest() == "d2f3b58b710d3029"


# ── what the bridge says about the body ─────────────────────────────────────────────────


def test_parse_urdf_reads_the_mass_the_joints_and_their_limits() -> None:
    summary = parse_urdf(MOCK_URDF)
    assert summary.parsed and summary.name == "mock-base"
    assert (summary.links, summary.links_with_mass) == (4, 3)
    assert summary.mass_kg == 11.0  # the caster has no inertial and is not guessed at
    assert summary.dof == 2 and len(summary.joints) == 3
    wheel = summary.joints[0]
    assert (wheel.name, wheel.type) == ("left_wheel_joint", "continuous")
    assert (wheel.parent, wheel.child) == ("base_link", "left_wheel")
    assert (wheel.effort, wheel.velocity) == (5.0, 10.0)
    assert wheel.lower is None and wheel.upper is None  # a continuous joint has no stops
    caster = summary.joints[-1]
    assert caster.type == "fixed" and not caster.moves
    assert summary.errors == ()


@pytest.mark.parametrize(
    ("text", "needle"),
    [
        ("", "empty"),
        ("<<not xml", "not XML"),
        ("<html/>", "not <robot>"),
        ('<robot name="x">${arm_mass}</robot>', "unexpanded xacro"),
        ('<robot><link name="a"><inertial><mass/></inertial></link></robot>', "no readable mass"),
        (
            '<robot><link name="a"><inertial><mass value="heavy"/></inertial></link></robot>',
            "no readable mass",
        ),
        ('<robot><joint name="j" type="wobbly"/></robot>', "unknown type"),
    ],
)
def test_parse_urdf_never_raises_on_a_description_it_cannot_use(text: str, needle: str) -> None:
    """A robot that answered with nonsense still answered, and the pilot is told which."""
    summary = parse_urdf(text)
    assert any(needle in e for e in summary.errors), summary.errors
    assert summary.mass_kg is None


def test_an_oversize_description_is_refused_rather_than_parsed() -> None:
    huge = "<robot>" + ("<link name='x'/>" * 600_000) + "</robot>"
    summary = parse_urdf(huge)
    assert not summary.parsed and "over" in summary.errors[0]


def test_the_datasheet_says_only_what_the_urdf_says() -> None:
    sheet = datasheet_from_introspection(mock_introspection(), base=DATASHEET)
    assert sheet.mass_kg is not None
    assert sheet.mass_kg.value == 11.0 and sheet.mass_kg.confidence == "official"
    assert "canned URDF" in sheet.mass_kg.source
    assert "3 link inertials" in sheet.mass_kg.note
    assert sheet.dof is not None and sheet.dof.value == 2.0
    # a description says nothing about what a gripper holds, so nothing is claimed
    assert sheet.payload_kg is None and sheet.reach_m is None and sheet.terrain is None
    assert sheet.manipulator == "none"
    assert any("4 links (3 with inertials)" in n for n in sheet.notes)
    assert any("left_wheel_joint (continuous" in n for n in sheet.notes)


def test_a_bridge_that_says_nothing_leaves_the_body_unknown() -> None:
    sheet = datasheet_from_introspection(mock_introspection(urdf=None), base=DATASHEET)
    assert sheet.mass_kg is None and sheet.dof is None
    sheet = datasheet_from_introspection(None, base=DATASHEET)
    assert any("nothing discovered on the bridge" in n for n in sheet.notes)
    assert sheet.mass_kg is None


def test_the_address_names_both_places_a_description_hides() -> None:
    default = parse_address(None)
    assert default.urdf_param == "/robot_state_publisher:robot_description"
    assert default.urdf_topic == "/robot_description"
    off = parse_address("ws://robot.local:9090?urdf_param=off&urdf_topic=off")
    assert off.urdf_param is None and off.urdf_topic is None
    named = parse_address("ws://h:9090?urdf_param=/my_node:urdf&urdf_topic=/desc")
    assert named.urdf_param == "/my_node:urdf" and named.urdf_topic == "/desc"
    with pytest.raises(TransportError, match="must start with /"):
        parse_address("ws://h:9090?urdf_topic=robot_description")
    with pytest.raises(TransportError, match="name a node and a parameter"):
        parse_address("ws://h:9090?urdf_param=robot_description")


async def test_the_mock_serves_a_description_and_the_verb_reads_it_again() -> None:
    adapter = RosbridgeAdapter(RosbridgeMock())
    manifest = await adapter.connect()
    assert manifest.provides("introspect")
    assert manifest.datasheet is not None and manifest.datasheet.mass_kg is not None
    assert manifest.datasheet.mass_kg.value == 11.0

    ex = Executor(registry_from_manifest(manifest, adapter), adapter, manifest=manifest)
    result = await ex.run_verb("introspect")
    assert result.ok
    assert result.data["urdf"]["name"] == "mock-base"
    assert result.data["urdf"]["dof"] == 2
    assert result.data["topics"]["/cmd_vel"] == "geometry_msgs/msg/Twist"
    assert result.data["topics"]["/robot_description"] == "std_msgs/msg/String"
    assert result.data["errors"] == []
    mock = adapter.transport
    assert isinstance(mock, RosbridgeMock)
    assert mock.introspections == 1
    assert mock.twists == [], "asking about the body sends nothing"


async def test_reading_the_description_runs_even_under_dry_run() -> None:
    adapter = RosbridgeAdapter(RosbridgeMock())
    manifest = await adapter.connect()
    ex = Executor(
        registry_from_manifest(manifest, adapter), adapter, manifest=manifest, dry_run=True
    )
    assert (await ex.run_verb("introspect")).ok, "it reads, it does not act"


async def test_a_mock_without_a_description_still_connects() -> None:
    adapter = RosbridgeAdapter(RosbridgeMock(urdf=None))
    manifest = await adapter.connect()
    assert manifest.datasheet is not None and manifest.datasheet.mass_kg is None
    assert any(
        "nothing discovered" in n or "without a description" in n for n in manifest.datasheet.notes
    )


class FakeService:
    """The slice of `roslibpy.Service` the backend uses, with the failures a bridge has."""

    def __init__(
        self,
        answer: dict[str, Any] | None = None,
        *,
        raise_with: Exception | None = None,
        hang_s: float = 0.0,
    ) -> None:
        self.answer = answer or {}
        self.raise_with = raise_with
        self.hang_s = hang_s
        self.calls: list[dict[str, Any]] = []
        self._held: list[threading.Event] = []
        self._holding = False
        self._lock = threading.Lock()

    def hold(self) -> None:
        """From now on each call waits until the test lets it go (`release`): when an answer
        comes, and whether it comes before the transport's own deadline, is the test's to say,
        and not the machine's."""
        with self._lock:
            self._holding = True

    @property
    def held(self) -> int:
        """How many calls have come since `hold`."""
        with self._lock:
            return len(self._held)

    def release(self, k: int | None = None) -> None:
        """Let the `k`th call held (from 1) answer, or every one, and stop holding."""
        with self._lock:
            if k is None:
                self._holding = False
                gates = list(self._held)
            else:
                gates = [self._held[k - 1]]
        for gate in gates:
            gate.set()

    def call(self, args: dict[str, Any], *, timeout: float) -> dict[str, Any]:
        self.calls.append(dict(args))
        with self._lock:
            gate = threading.Event() if self._holding else None
            if gate is not None:
                self._held.append(gate)
        if gate is not None:
            gate.wait(GUARD_S)  # the test lets it go; the guard only ends a test that forgot
        if self.hang_s:
            time.sleep(self.hang_s)
        if self.raise_with is not None:
            raise self.raise_with
        return dict(self.answer)


def _services(**answers: FakeService) -> tuple[Any, dict[str, FakeService], dict[str, str]]:
    """A service factory over a fixed table, plus what it was asked for."""
    by_name = {
        "/rosapi/topics": answers.get("topics", FakeService()),
        "/rosapi/get_param": answers.get("get_param", FakeService()),
    }
    types: dict[str, str] = {}

    def factory(client: Any, name: str, service_type: str) -> FakeService:
        types[name] = service_type
        return by_name[name]

    return factory, by_name, types


async def test_the_ws_backend_asks_the_bridge_what_the_body_is() -> None:
    topics = FakeService({"topics": ["/cmd_vel", "/odom"], "types": ["T", "O"]})
    param = FakeService({"value": json.dumps(MOCK_URDF), "successful": True, "reason": ""})
    factory, _by_name, types = _services(topics=topics, get_param=param)
    ws = RosbridgeWs(
        "ws://robot.local:9090",
        ros=FakeRos(),
        topic_factory=lambda c, n, t: FakeTopic(c, n, t),
        service_factory=factory,
    )
    adapter = RosbridgeAdapter(ws)
    manifest = await adapter.connect()

    assert types["/rosapi/topics"] == "rosapi_msgs/srv/Topics"
    assert types["/rosapi/get_param"] == "rosapi_msgs/srv/GetParam"
    assert param.calls == [
        {"name": "/robot_state_publisher:robot_description", "default_value": ""}
    ]
    intro = ws.introspection
    assert intro is not None and intro.source == "param"
    assert intro.topics == {"/cmd_vel": "T", "/odom": "O"}
    assert manifest.datasheet is not None and manifest.datasheet.mass_kg is not None
    assert manifest.datasheet.mass_kg.value == 11.0
    assert "robot_description" in manifest.datasheet.mass_kg.source

    state = await adapter.get_state()
    assert "URDF_MASS_IS_THE_BODY" in state.extras["assumptions"]
    assert "DOF_IS_NON_FIXED_JOINTS" in state.extras["assumptions"]
    assert "DESCRIPTION_NAMES" in state.extras["assumptions"]
    assert state.extras["introspection"]["source"] == "param"


async def test_the_ws_backend_falls_back_to_the_latched_topic() -> None:
    topics = FakeService({"topics": ["/cmd_vel"], "types": ["T"]})
    param = FakeService({"value": "", "successful": False, "reason": "node not found"})
    factory, _by_name, _types = _services(topics=topics, get_param=param)

    class LatchedTopic(FakeTopic):
        def subscribe(self, callback: Any) -> None:
            super().subscribe(callback)
            if self.name == "/robot_description":
                callback({"data": MOCK_URDF})

    made: dict[str, LatchedTopic] = {}

    def topic_factory(client: Any, name: str, message_type: str) -> LatchedTopic:
        return made.setdefault(name, LatchedTopic(client, name, message_type))

    ws = RosbridgeWs(
        "ws://robot.local:9090", ros=FakeRos(), topic_factory=topic_factory, service_factory=factory
    )
    await RosbridgeAdapter(ws).connect()
    intro = ws.introspection
    assert intro is not None and intro.source == "topic"
    assert intro.where == "/robot_description"
    assert intro.mass_kg == 11.0
    assert made["/robot_description"].message_type == "std_msgs/msg/String"
    assert made["/robot_description"].unsubscribed, "a one-shot read leaves no subscription"
    assert any("node not found" in e for e in intro.errors)


async def test_a_bridge_that_will_not_answer_still_drives() -> None:
    """Introspection is bounded and never fails a connect: the datasheet says it knows
    nothing, and the pilot declines whatever hinges on that."""
    factory, _by_name, _types = _services(
        topics=FakeService(raise_with=RuntimeError("no rosapi here")),
        get_param=FakeService(hang_s=1.0),
    )
    ws = RosbridgeWs(
        "ws://robot.local:9090?urdf_topic=off",
        ros=FakeRos(),
        topic_factory=lambda c, n, t: FakeTopic(c, n, t),
        service_factory=factory,
        timeout_s=0.3,
    )
    adapter = RosbridgeAdapter(ws)
    started = time.monotonic()
    manifest = await adapter.connect()
    assert time.monotonic() - started < 3.0, "one deadline covers the whole look"

    intro = ws.introspection
    assert intro is not None and not intro.discovered
    assert any("no rosapi here" in e for e in intro.errors)
    assert manifest.datasheet is not None and manifest.datasheet.mass_kg is None
    assert any("nothing discovered on the bridge" in n for n in manifest.datasheet.notes)
    # and it still drives
    assert (await adapter.send_intent(Intent.move(0.2, 0.0, 0.0))).accepted


async def test_a_parameter_reads_whether_or_not_it_arrives_as_json() -> None:
    for value in (MOCK_URDF, json.dumps(MOCK_URDF)):
        factory, _by_name, _types = _services(
            topics=FakeService({"topics": [], "types": []}),
            get_param=FakeService({"value": value, "successful": True, "reason": ""}),
        )
        ws = RosbridgeWs(
            "ws://h:9090",
            ros=FakeRos(),
            topic_factory=lambda c, n, t: FakeTopic(c, n, t),
            service_factory=factory,
        )
        await RosbridgeAdapter(ws).connect()
        assert ws.introspection is not None and ws.introspection.mass_kg == 11.0


async def test_the_verb_refreshes_a_datasheet_that_was_empty_at_connect() -> None:
    param = FakeService({"value": "", "successful": False, "reason": "not up yet"})
    factory, _by_name, _types = _services(
        topics=FakeService({"topics": [], "types": []}), get_param=param
    )
    ws = RosbridgeWs(
        "ws://h:9090?urdf_topic=off",
        ros=FakeRos(),
        topic_factory=lambda c, n, t: FakeTopic(c, n, t),
        service_factory=factory,
    )
    adapter = RosbridgeAdapter(ws)
    manifest = await adapter.connect()
    assert manifest.datasheet is not None and manifest.datasheet.mass_kg is None

    # the robot came up; now the parameter is there
    param.answer = {"value": json.dumps(MOCK_URDF), "successful": True, "reason": ""}
    ex = Executor(registry_from_manifest(manifest, adapter), adapter, manifest=manifest)
    assert (await ex.run_verb("introspect")).ok
    assert manifest.datasheet.mass_kg is not None
    assert manifest.datasheet.mass_kg.value == 11.0


# ── where the base is, by its /odom ─────────────────────────────────────────────────────


def _odom(x: float, y: float = 0.0, *, stamp: float | None = None) -> dict[str, Any]:
    message: dict[str, Any] = {
        "pose": {
            "pose": {
                "position": {"x": x, "y": y, "z": 0.0},
                "orientation": {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0},
            }
        }
    }
    if stamp is not None:
        sec = int(stamp)
        message["header"] = {"stamp": {"sec": sec, "nanosec": int((stamp - sec) * 1e9)}}
    return message


class _TimedTopic(FakeTopic):
    """A topic that writes down when each message went out, on this machine's clock."""

    def __init__(self, ros: Any, name: str, message_type: str) -> None:
        super().__init__(ros, name, message_type)
        self.times: list[float] = []

    def publish(self, message: Any) -> None:
        self.times.append(time.monotonic())
        super().publish(message)


async def _ws() -> tuple[RosbridgeWs, RosbridgeAdapter, dict[str, _TimedTopic]]:
    topics: dict[str, _TimedTopic] = {}

    def factory(client: Any, name: str, message_type: str) -> _TimedTopic:
        return topics.setdefault(name, _TimedTopic(client, name, message_type))

    ws = RosbridgeWs("ws://robot.local:9090", ros=FakeRos(), topic_factory=factory)
    adapter = RosbridgeAdapter(ws)
    await adapter.connect()
    return ws, adapter, topics


async def test_odom_is_where_the_base_is_dated_by_its_stamp_and_the_place_verbs_are_offered() -> (
    None
):
    ws, adapter, topics = await _ws()
    assert adapter.manifest is not None and set(adapter.manifest.verb_names()) >= PLACES
    assert ws.spatial is not None and ws.spatial.fix().lost == "no odometry reading yet"
    topics["/odom"].callback(_odom(1.5, -0.5, stamp=1234.25))
    fix = ws.spatial.fix()
    assert fix.pose is not None and (fix.pose.x, fix.pose.y) == (1.5, -0.5)
    assert fix.t == pytest.approx(1234.25) and fix.kind == "odometry"
    where = (await adapter.get_state()).extras["where"]
    assert where["kind"] == "odometry" and where["frame"].startswith("odom:")


async def test_an_odom_the_stack_does_not_stamp_is_dated_by_its_arrival() -> None:
    ws, _adapter, topics = await _ws()
    before = time.monotonic()
    topics["/odom"].callback(_odom(0.2))
    fix = ws.spatial.fix() if ws.spatial is not None else None
    assert fix is not None and fix.t is not None and before <= fix.t <= time.monotonic()


async def _go(adapter: RosbridgeAdapter, book: PlaceBook, timeout_s: float) -> VerbResult:
    assert adapter.manifest is not None
    ctx = VerbContext(transport=adapter, manifest=adapter.manifest, places=book)
    return await PLACE_VERBS["go_to_place"].execute(
        ctx, GoToPlaceParams(name="dock", face=False, timeout_s=timeout_s)
    )


async def test_odom_gone_quiet_holds_the_base_still_and_then_ends_the_walk() -> None:
    """rosbridge has no deadman, so a twist left standing drives the base on: a walk on a reading
    that stopped moving on holds the base still within a reading's freshness, and ends."""
    ws, adapter, topics = await _ws()
    assert ws.spatial is not None and adapter.manifest is not None
    t0 = 100.0
    topics["/odom"].callback(_odom(0.0, stamp=t0))
    book = PlaceBook(PlaceStore("base", persist=False), ws.spatial)
    frame = book.frame()
    assert frame is not None
    book.store.mark(Place("dock", 2.0, 0.0, 0.0, frame.id, "odometry", 0.0))
    ctx = VerbContext(transport=adapter, manifest=adapter.manifest, places=book)
    stopped: list[float] = []

    async def odometry_then_quiet() -> None:
        x, t = 0.0, t0
        for _ in range(15):  # a second and a half of a base that drives as told, then nothing
            await asyncio.sleep(0.1)
            x, t = x + 0.015, t + 0.1
            topics["/odom"].callback(_odom(x, stamp=t))
        stopped.append(time.monotonic())

    feed = asyncio.create_task(odometry_then_quiet())
    result = await PLACE_VERBS["go_to_place"].execute(
        ctx, GoToPlaceParams(name="dock", face=False, timeout_s=30.0)
    )
    await feed
    assert not result.ok and result.data["outcome"] in ("stale", "lost"), result.summary
    gait = Gait.of(ctx, ws.spatial, {})
    cmd = topics["/cmd_vel"]
    sent = list(zip(cmd.times, cmd.published, strict=True))
    moving = [m for _, m in sent if m["linear"]["x"] or m["angular"]["z"]]
    assert moving, "it never drove"
    held = [m for at, m in sent if at > stopped[0] + gait.fresh_s + 0.2]
    assert held, "nothing went out after the reading stopped"
    assert all(not m["linear"]["x"] and not m["angular"]["z"] for m in held), held
    assert sent[-1][1] == twist_message(0.0, 0.0, 0.0)


async def test_the_period_is_the_streams_own_however_seldom_roboglot_reads_it() -> None:
    """Roboglot reads where the base is at each verb, seconds apart while a pilot thinks, and
    /odom streams all the while. Taken only when read, the track measured how often Roboglot
    looked, and a walk sized its tolerance from that: a base its own joystick had driven three
    metres off in half a minute was said to be at its place already."""
    ws, adapter, topics = await _ws()
    assert ws.spatial is not None
    odom, period, t0 = topics["/odom"], 0.02, 100.0
    odom.callback(_odom(0.0, stamp=t0))
    book = PlaceBook(PlaceStore("base", persist=False), ws.spatial)
    assert book.mark("dock")[0].x == 0.0
    steps = 1500  # half a minute of /odom, none of it read
    for i in range(1, steps + 1):
        odom.callback(_odom(3.0 * i / steps, stamp=t0 + period * i))
    assert ws.spatial.period_s == pytest.approx(period)
    result = await _go(adapter, book, timeout_s=5.0)
    assert not result.ok and "already at" not in result.summary, result.summary


async def test_a_pause_in_odom_before_a_walk_does_not_stretch_how_long_it_drives_blind() -> None:
    """rosbridge has no deadman. The walk holds a twist for a reading's freshness and then
    zeroes it, and that freshness is sized from the period: a pause before the walk began read
    as the stream's period, and the base drove on long after /odom stopped."""
    ws, adapter, topics = await _ws()
    assert ws.spatial is not None and adapter.manifest is not None
    odom = topics["/odom"]
    odom.callback(_odom(0.0, stamp=100.0))
    book = PlaceBook(PlaceStore("base", persist=False), ws.spatial)
    book.mark("dock")
    odom.callback(_odom(-3.0, stamp=106.0))  # pushed off by hand while /odom was quiet
    last: list[float] = []

    async def drives_then_dies() -> None:
        x, t = -3.0, 106.0
        for _ in range(20):  # a second of a base that drives as told, then nothing
            await asyncio.sleep(0.05)
            x, t = x + 0.0075, t + 0.05
            odom.callback(_odom(x, stamp=t))
        last.append(time.monotonic())

    feed = asyncio.create_task(drives_then_dies())
    result = await _go(adapter, book, timeout_s=30.0)
    await feed
    assert not result.ok, result.summary
    gait = Gait.of(
        VerbContext(transport=adapter, manifest=adapter.manifest, places=book), ws.spatial, {}
    )
    cmd = topics["/cmd_vel"]
    moving = [at for at, m in zip(cmd.times, cmd.published, strict=True) if m["linear"]["x"]]
    assert moving, "it never drove"
    assert moving[-1] - last[0] <= gait.fresh_s + 2 * TICK_S, (moving[-1] - last[0], gait)


async def test_an_unstamped_odom_on_a_stamped_stream_is_no_reading() -> None:
    """A stamp of nothing on a stream the stack stamps (sim time before /clock, a second
    publisher) is another clock's: dated by its arrival in the same track, it sent the frame
    back and forth between two clocks and orphaned every place."""
    ws, _adapter, topics = await _ws()
    assert ws.spatial is not None
    odom, t0 = topics["/odom"], 1_761_000_000.0  # ROS time, in epoch seconds
    odom.callback(_odom(0.0, stamp=t0))
    book = PlaceBook(PlaceStore("base", persist=False), ws.spatial)
    place, _ = book.mark("dock")
    odom.callback(_odom(0.01))
    odom.callback(_odom(0.02, stamp=t0 + 0.1))
    odom.callback(_odom(0.03))
    fix = ws.spatial.fix()
    assert fix.pose is not None and fix.pose.x == 0.02 and fix.t == pytest.approx(t0 + 0.1)
    assert book.view(place).status == "here"


async def test_odom_stamped_after_some_that_were_not_goes_on_in_the_same_frame() -> None:
    """A stack whose first messages go unstamped, sim time before /clock: from its first stamp
    on the readings are dated by the stack's clock, far behind this machine's, in one frame."""
    ws, _adapter, topics = await _ws()
    assert ws.spatial is not None
    odom = topics["/odom"]
    odom.callback(_odom(0.0))
    book = PlaceBook(PlaceStore("base", persist=False), ws.spatial)
    place, _ = book.mark("dock")
    odom.callback(_odom(0.01))
    for i in range(1, 4):
        odom.callback(_odom(0.01 + 0.01 * i, stamp=3.0 + 0.1 * i))
    assert book.view(place).status == "here"
    assert ws.spatial.fix().t == pytest.approx(3.3)
    assert ws.spatial.period_s == pytest.approx(0.1)


async def test_the_same_stamp_again_is_no_new_reading_and_the_stream_goes_stale() -> None:
    """A stack that has frozen sends its last /odom again, stamped as before. That says nothing
    of where the base is now, so the reading goes stale as if nothing came at all."""
    ws, _adapter, topics = await _ws()
    assert ws.spatial is not None
    odom = topics["/odom"]
    odom.callback(_odom(0.0, stamp=100.0))
    assert ws.spatial.fix().localized
    deadline = time.monotonic() + 3.0
    while ws.spatial.fix().localized and time.monotonic() < deadline:
        await asyncio.sleep(TICK_S)
        odom.callback(_odom(0.0, stamp=100.0))
    fix = ws.spatial.fix()
    assert not fix.localized and "no new /odom" in (fix.lost or ""), fix.lost


async def test_the_mocks_places_last_one_connection_as_the_ws_backends_do(tmp_path: Any) -> None:
    first = RosbridgeMock()
    await first.connect()
    assert first.spatial is not None
    PlaceBook(PlaceStore("base", base_dir=tmp_path), first.spatial).mark("dock")
    await first.close()
    second = RosbridgeMock()
    await second.connect()
    assert second.spatial is not None
    book = PlaceBook(PlaceStore("base", base_dir=tmp_path), second.spatial)
    assert {v.place.name: v.status for v in book.views()} == {"dock": "orphaned"}


# ── the topic it drives ─────────────────────────────────────────────────────────────────

TWIST, STAMPED = "geometry_msgs/msg/Twist", "geometry_msgs/msg/TwistStamped"


async def _listing(
    listed: dict[str, str],
) -> tuple[RosbridgeAdapter, Executor, dict[str, FakeTopic]]:
    """A ws base on a bridge whose rosapi lists `listed`, topic by type, and an executor over it."""
    answer = FakeService({"topics": list(listed), "types": list(listed.values())})
    factory, _by_name, _types = _services(topics=answer)
    made: dict[str, FakeTopic] = {}
    adapter = RosbridgeAdapter(
        RosbridgeWs(
            "ws://robot.local:9090?urdf_param=off&urdf_topic=off",
            ros=FakeRos(),
            topic_factory=lambda c, n, t: made.setdefault(n, FakeTopic(c, n, t)),
            service_factory=factory,
        )
    )
    manifest = await adapter.connect()
    return (
        adapter,
        Executor(registry_from_manifest(manifest, adapter), adapter, manifest=manifest),
        made,
    )


@pytest.mark.parametrize(
    ("listed", "found"),
    [
        ({"/cmd_vel": STAMPED, "/odom": "nav_msgs/msg/Odometry"}, f"lists /cmd_vel as {STAMPED}"),
        ({"/cmd_vel_nav": TWIST, "/odom": "nav_msgs/msg/Odometry"}, "lists no /cmd_vel"),
    ],
    ids=["stamped", "absent"],
)
async def test_a_command_topic_the_bridge_lists_otherwise_is_refused_naming_what_it_found(
    listed: dict[str, str], found: str
) -> None:
    """The command topic was never held against the topic list read at the same connect, so a
    base listening on another topic, or taking TwistStamped, got nothing while every Twist was
    accepted and `move` said it walked. The check is made before anything is published, since
    roslibpy advertises the topic as a Twist on the first publish and a list read after that
    would show Roboglot's own; it refuses with what the bridge listed, and sends nothing."""
    adapter, ex, made = await _listing(listed)
    result = await ex.run_verb("move", {"vx": 0.2, "duration_s": 2 * MOVE_RESEND_S})
    assert not result.ok and "move refused" in result.summary and found in result.summary
    twists = [name for name, kind in listed.items() if kind == TWIST]
    assert f"the Twist topics it lists: {', '.join(twists) or 'none'}" in result.summary
    assert made["/cmd_vel"].published == [], "a Twist went to a topic the bridge listed otherwise"
    assert adapter.manifest is not None and adapter.manifest.datasheet is not None
    assert any(found in note for note in adapter.manifest.datasheet.notes)


async def test_with_more_than_one_twist_topic_it_says_which_it_drives() -> None:
    """Introspection chose no topic and compared none, so where a bridge lists two a base could
    take a Twist on, nobody was told which one Roboglot drives. It drives the one the address
    names, as before, and the datasheet says so, naming the others."""
    listed = {"/cmd_vel": TWIST, "/cmd_vel_nav": TWIST, "/odom": "nav_msgs/msg/Odometry"}
    adapter, ex, made = await _listing(listed)
    assert (await ex.run_verb("move", {"vx": 0.2, "duration_s": 2 * MOVE_RESEND_S})).ok
    assert made["/cmd_vel"].published[0] == twist_message(0.2, 0.0, 0.0)
    assert adapter.manifest is not None and adapter.manifest.datasheet is not None
    said = [note for note in adapter.manifest.datasheet.notes if "Roboglot drives" in note]
    assert said and "/cmd_vel_nav" in said[0], adapter.manifest.datasheet.notes


async def test_the_command_topic_is_judged_again_when_needed_and_when_the_bridge_reads_anew() -> (
    None
):
    """The topic was judged once, against the list read at connect, and a refusal was never
    lifted: a base driver that came up a few seconds after Roboglot connected was refused for the
    whole session, even once `introspect` listed it as a Twist. It is judged again whenever the
    bridge is read, and a refused move reads it first: missing then and listed now is driven,
    and listed then and gone now, before Roboglot has published on it, is refused."""
    later = {"topics": ["/odom", "/cmd_vel"], "types": ["nav_msgs/msg/Odometry", TWIST]}
    for re_read in ("introspect", "the move itself"):
        listing = FakeService({"topics": ["/odom"], "types": ["nav_msgs/msg/Odometry"]})
        adapter, ex, made = await _listed_by(listing)
        assert not (await ex.run_verb("move", {"vx": 0.2, "duration_s": 0.2})).ok
        listing.answer = later  # the base driver is up
        if re_read == "introspect":
            assert (await adapter.introspect()).topics.get("/cmd_vel") == TWIST
        moved = await ex.run_verb("move", {"vx": 0.2, "duration_s": 0.2})
        assert moved.ok, f"{re_read}: {moved.summary}"
        assert made["/cmd_vel"].published[0] == twist_message(0.2, 0.0, 0.0)

    listing = FakeService(dict(later))
    adapter, ex, made = await _listed_by(listing)
    listing.answer = {"topics": ["/odom"], "types": ["nav_msgs/msg/Odometry"]}  # gone
    await adapter.introspect()
    gone = await ex.run_verb("move", {"vx": 0.2, "duration_s": 0.2})
    assert not gone.ok and "lists no /cmd_vel" in gone.summary, gone.summary
    assert made["/cmd_vel"].published == []


async def _listed_by(
    listing: FakeService,
) -> tuple[RosbridgeAdapter, Executor, dict[str, FakeTopic]]:
    """A ws base on a bridge whose rosapi answers as `listing` does, and an executor over it."""
    factory, _by_name, _types = _services(topics=listing)
    made: dict[str, FakeTopic] = {}
    adapter = RosbridgeAdapter(
        RosbridgeWs(
            "ws://robot.local:9090?urdf_param=off&urdf_topic=off",
            ros=FakeRos(),
            topic_factory=lambda c, n, t: made.setdefault(n, FakeTopic(c, n, t)),
            service_factory=factory,
        )
    )
    manifest = await adapter.connect()
    return (
        adapter,
        Executor(registry_from_manifest(manifest, adapter), adapter, manifest=manifest),
        made,
    )


# ── a link that drops ───────────────────────────────────────────────────────────────────


async def _driving(
    address: str = "ws://robot.local:9090",
) -> tuple[FakeRos, dict[str, FakeTopic], RosbridgeAdapter, Executor]:
    """A ws base connected over the stand-in link, and an executor over it. A publish handed to
    a `FakeTopic` while `FakeRos.is_connected` is False is one roslibpy would hold until the
    socket came back and send then, unwatched, or drop with a client asked to close
    (`upstream_api.QUEUED_UNTIL_READY`): nothing should be handed over then."""
    ros = FakeRos()
    topics: dict[str, FakeTopic] = {}

    def factory(client: Any, name: str, message_type: str) -> FakeTopic:
        return topics.setdefault(name, FakeTopic(client, name, message_type))

    adapter = RosbridgeAdapter(RosbridgeWs(address, ros=ros, topic_factory=factory))
    manifest = await adapter.connect()
    ex = Executor(registry_from_manifest(manifest, adapter), adapter, manifest=manifest)
    return ros, topics, adapter, ex


async def test_a_stop_into_a_dropped_link_says_its_zero_twist_was_not_sent() -> None:
    """The stop published its zero Twist under a blanket suppress and kept no `stop_error`, and
    roslibpy raises nothing for a publish with the socket down. So with the link gone the stop
    verb said "stopped (velocity zeroed)" while the base, which has no deadman, went on at the
    last Twist that reached it. The stop verb reads `stop_error` off the adapter, so this fails
    again if the transport stops saying so or the adapter stops passing it on."""
    ros, topics, adapter, ex = await _driving()
    assert (await adapter.send_intent(Intent.move(0.2, 0.0, 0.0))).accepted
    cmd = topics["/cmd_vel"]
    handed = len(cmd.published)
    ros.is_connected = False  # the link drops
    result = await ex.run_verb("stop")
    assert not result.ok and "could not be delivered" in result.summary, result.summary
    assert "is not connected" in result.summary and "no deadman" in result.summary
    assert "switch" not in result.summary, "the core advised a physical act it cannot vouch for"
    assert len(cmd.published) == handed, "a zero handed to a dropped link is sent later, or never"
    ros.is_connected = True  # back: the next stop goes out, and says nothing went wrong
    assert (await ex.run_verb("stop")).summary == "stopped (velocity zeroed)"
    assert cmd.published[-1] == twist_message(0.0, 0.0, 0.0) and adapter.stop_error is None


async def test_a_move_into_a_dropped_link_is_refused_and_hands_nothing_over() -> None:
    """`send_intent` published with no look at the link, so every slice of a `move` waited in
    roslibpy's queue for a socket that might come back, and the verb said the base walked."""
    ros, topics, _adapter, ex = await _driving()
    ros.is_connected = False
    result = await ex.run_verb("move", {"vx": 0.2, "duration_s": 3 * MOVE_RESEND_S})
    assert not result.ok and "move refused" in result.summary, result.summary
    assert "is not connected" in result.summary
    assert topics["/cmd_vel"].published == []


async def test_the_abort_after_a_failed_heartbeat_says_its_stop_did_not_go_out() -> None:
    """The heartbeat fails on the dropped link, the abort cancels the `move`, and the executor's
    own stop read no `stop_error`, so the abort said "a stop was sent" over a zero Twist that
    only waited in a queue. The heartbeat beats ten times a slice, so it, not the move's next
    slice, is what finds the drop."""
    ros, _topics, adapter, ex = await _driving()
    ws = adapter.transport
    assert isinstance(ws, RosbridgeWs)
    beat = Heartbeat(adapter, ex.abort, period_s=MOVE_RESEND_S / 10)
    real_sleep = ws.sleep

    async def drop_then_sleep(seconds: float) -> None:
        ros.is_connected = False  # gone just after the first slice went out
        await real_sleep(seconds)

    ws.sleep = drop_then_sleep  # type: ignore[method-assign]
    beat.start()
    try:
        with pytest.raises(Aborted) as aborted:
            await ex.run_verb("move", {"vx": 0.2, "duration_s": 10 * MOVE_RESEND_S})
    finally:
        await beat.stop()
    said = str(aborted.value)
    assert "the stop sent after it failed" in said and "is not connected" in said, said


async def test_close_on_a_dropped_link_records_that_its_zero_twist_never_left() -> None:
    """`close()` published its zero under a blanket suppress and then closed the client, which
    drops whatever waited for the socket, so nothing recorded that the last word the base heard
    was a moving Twist."""
    ros, topics, adapter, _ex = await _driving()
    assert (await adapter.send_intent(Intent.move(0.2, 0.0, 0.0))).accepted
    ros.is_connected = False
    await adapter.close()
    assert topics["/cmd_vel"].published == [twist_message(0.2, 0.0, 0.0)]
    assert adapter.stop_error is not None and "is not connected" in adapter.stop_error
    # and where a run reads after a close, so a person hears it (`close_note`, `agent/loop.py`)
    assert adapter.close_note is not None and "zero Twist was not sent" in adapter.close_note


# ── a camera that has gone quiet ────────────────────────────────────────────────────────


async def test_an_image_older_than_the_window_it_is_steered_by_is_not_handed_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`get_frame` decoded the newest image however old it was, so a camera that stopped with
    the ball in shot showed it in shot for as long as `go_to` steered at it, up to its timeout.
    An image is stamped on arrival, and one older than the window the Microduck keeps for the
    same hazard, four of the stream's periods and two seconds at least, is not handed out; the
    verb that asked is told the camera went quiet (`camera_error`). The clock is moved on for
    the one call that reads it, so no window is waited out."""
    _ros, topics, adapter, _ex = await _driving("ws://robot.local:9090?image=/cam/compressed")
    period = 0.1
    for _ in range(3):
        topics["/cam/compressed"].callback({"format": "png", "data": _png((255, 0, 0))})
        await asyncio.sleep(period)
    assert await adapter.get_frame() is not None
    assert getattr(adapter, "camera_error", None) is None
    later = time.monotonic() + FRAME_STALE_FLOOR_S + 1.0
    with monkeypatch.context() as clock:
        clock.setattr(time, "monotonic", lambda: later)
        assert await adapter.get_frame() is None, "an image from a camera gone quiet"
        why = getattr(adapter, "camera_error", None)
    assert why is not None and why.startswith("the newest image on /cam/compressed is "), why
    topics["/cam/compressed"].callback({"format": "png", "data": _png((0, 255, 0))})
    assert await adapter.get_frame() is not None
    assert getattr(adapter, "camera_error", None) is None


# ── a link that reads up and has gone quiet ─────────────────────────────────────────────


async def test_the_heartbeat_fails_once_odom_goes_quiet_on_a_link_that_reads_connected() -> None:
    """A link that dies without a close leaves `is_connected` True until TCP gives up, and the
    heartbeat read nothing else, although the transport kept how stale /odom was: every beat
    passed, `move` said it walked, and the base, which has no deadman, drove on. `FakeRos` reads
    connected throughout, and /odom streams at 20 Hz for a second and then stops, which is what
    a dead link looks like from this end. The heartbeat passes while it streams and fails once
    it has been quiet for its stale window, naming the topic, and a stop after that cannot say
    its zero reached the base."""
    ros, topics, adapter, _ex = await _driving()
    period, t0 = 0.05, 1000.0
    quiet_from = time.monotonic()
    for i in range(20):
        quiet_from = time.monotonic()  # no later than the transport dates this message
        topics["/odom"].callback(_odom(0.01 * i, stamp=t0 + i * period))
        await adapter.heartbeat()
        await asyncio.sleep(period)
    deadline = quiet_from + 10 * STALE_FLOOR_S
    with pytest.raises(HeartbeatError, match="/odom") as failed:
        while time.monotonic() < deadline:
            await adapter.heartbeat()
            await asyncio.sleep(period)
    assert time.monotonic() - quiet_from >= STALE_FLOOR_S, f"failed while fresh: {failed.value}"
    assert ros.is_connected
    await adapter.stop()
    assert topics["/cmd_vel"].published[-1] == twist_message(0.0, 0.0, 0.0)
    assert adapter.stop_error is not None and "/odom" in adapter.stop_error
    assert adapter.stop_unconfirmed, "a zero that went out is not one that was not sent"

    ws = adapter.transport
    assert isinstance(ws, RosbridgeWs)
    stopping = asyncio.ensure_future(adapter.stop())
    await _until(lambda: bool(ws._listeners), "the stop listens after its zero")
    topics["/odom"].callback(_odom(0.5, stamp=t0 + 100.0))  # a message while it listens
    await stopping
    assert adapter.stop_error is None and not adapter.stop_unconfirmed


class _Clock:
    """`time` as the transport's module reads it, moved on by hand, so a stream's pace is the
    test's and not the machine's. The event loop keeps its own clock, which a round trip's
    deadline runs on."""

    def __init__(self) -> None:
        self.t = time.monotonic()

    def monotonic(self) -> float:
        return self.t


GUARD_S = 60.0
"""How long a test waits for something it has made sure will happen before it calls the wait
broken: a guard against a hang, never what decides a test, however slow the machine."""
REQUEST_S: float = inspect.signature(RosbridgeWs.__init__).parameters["timeout_s"].default
"""The transport's own request deadline, as a run has it: what an answer that must count is
given, since no machine is so slow that an answer made at once misses it."""
LOST_AFTER_S = 2.0
"""A request deadline short enough that a test can wait out the ones it never answers, and long
enough that an answer made at once never misses it."""
QUICK_S = 0.3
"""A transport's own request deadline in these tests (`RosbridgeWs(timeout_s=...)`), short so
that a round trip left unanswered is waited out in a fraction of a second."""


def _asked(*, timeout_s: float = 5.0) -> tuple[RosbridgeWs, FakeService, dict[str, FakeTopic]]:
    """A ws base on a bridge whose rosapi lists only /cmd_vel, a Twist, as `FakeService`."""
    listing = FakeService({"topics": ["/cmd_vel"], "types": ["geometry_msgs/msg/Twist"]})
    factory, _by_name, _types = _services(topics=listing)
    topics: dict[str, FakeTopic] = {}
    ws = RosbridgeWs(
        "ws://robot.local:9090?urdf_param=off&urdf_topic=off",
        ros=FakeRos(),
        topic_factory=lambda c, n, t: topics.setdefault(n, FakeTopic(c, n, t)),
        service_factory=factory,
        timeout_s=timeout_s,
    )
    return ws, listing, topics


@pytest.mark.parametrize("period_s", [2.0, 1.25], ids=["half-a-hertz", "four-fifths-of-a-hertz"])
async def test_a_base_whose_odom_comes_seldom_but_steadily_keeps_its_heartbeat(
    monkeypatch: pytest.MonkeyPatch, period_s: float
) -> None:
    """The stale window sat on its one-second floor until two intervals of /odom had come, so a
    healthy base sending it less than once a second failed its heartbeat between its first and
    third message, and one failed beat ends a run, or an MCP session for good. A stream is not
    judged before its pace is known. Beats come every `Heartbeat`'s default period."""
    _ros, topics, _adapter, _ex = await _driving()
    ws = _adapter.transport
    assert isinstance(ws, RosbridgeWs)
    clock = _Clock()
    monkeypatch.setattr(ws_module, "time", clock)
    start, beat_s = clock.t, inspect.signature(Heartbeat.__init__).parameters["period_s"].default
    arrivals = [start + k * period_s for k in range(4)]
    fed, beat = 0, start + beat_s / 5
    while beat < arrivals[-1] + period_s:
        while fed < len(arrivals) and arrivals[fed] <= beat:
            topics["/odom"].callback(_odom(0.0, stamp=100.0 + fed * period_s))
            fed += 1
        clock.t = beat
        await ws.heartbeat()  # raises, and fails the test, on a healthy base
        beat += beat_s


BEAT_S: float = inspect.signature(Heartbeat.__init__).parameters["period_s"].default
"""How often a run's heartbeat beats (`Heartbeat`'s default), which every bound is counted in."""


def _paced(clock: _Clock, odom: FakeTopic, gap: float, count: int, stamp: float) -> float:
    """`count` messages on /odom, `gap` apart on the transport's clock; the last stamp."""
    for _ in range(count):
        clock.t += gap
        stamp += gap
        odom.callback(_odom(0.0, stamp=stamp))
    return stamp


async def _asks_settled(ws: RosbridgeWs) -> None:
    """Let the round trips a beat set going finish, as they do between two beats of a run: each
    ends with its answer or at the transport's own request deadline, whichever the stand-in
    makes it, however long the machine takes to get there."""
    if ws._asks:
        await asyncio.wait(set(ws._asks), timeout=GUARD_S)


async def _until(ready: Callable[[], bool], what: str) -> None:
    """Let the event loop run until `ready()`, however long the machine takes (`GUARD_S`)."""
    end = time.monotonic() + GUARD_S
    while not ready():
        assert time.monotonic() < end, f"never came: {what}"
        await asyncio.sleep(0.01)


async def _noticed_within(
    ws: RosbridgeWs, clock: _Clock, limit_s: float, *, waits_for_nothing: bool = False
) -> float:
    """Beat every `BEAT_S` on the transport's clock until the heartbeat fails, and say how long
    that took; a link still unnoticed past `limit_s` fails the test. With `waits_for_nothing`,
    a beat must come back within half the transport's own request deadline, which a beat that
    waits for an ask held unanswered cannot."""
    start = clock.t
    while clock.t - start <= limit_s:
        clock.t += BEAT_S
        try:
            if waits_for_nothing:
                await asyncio.wait_for(ws.heartbeat(), ws.timeout_s / 2)
            else:
                await ws.heartbeat()
        except HeartbeatError:
            return clock.t - start
    pytest.fail(f"a dead link went unnoticed for {limit_s:.1f} s")


def _listens(monkeypatch: pytest.MonkeyPatch, ws: RosbridgeWs) -> list[tuple[float, bool]]:
    """Every listen a stop of `ws` makes from now on, how long and whether for /odom alone, as
    it begins: its length read off the transport, not off the machine's clock."""
    made: list[tuple[float, bool]] = []
    listen = ws._hears_within

    async def recorded(seconds: float, *, base_only: bool = False) -> bool:
        made.append((seconds, base_only))
        return await listen(seconds, base_only=base_only)

    monkeypatch.setattr(ws, "_hears_within", recorded)
    return made


async def _stopped_while_listening(
    ex: Executor, ws: RosbridgeWs, then: Callable[[], Any]
) -> VerbResult:
    """`ex` stops the body, and once the stop listens after its zero Twist, `then` happens, a
    message the test delivers: when it comes is the test's to say, and the machine's speed
    decides nothing, since a listen already begun hears it however late its thread starts."""
    stopping = asyncio.ensure_future(ex.run_verb("stop"))
    await _until(lambda: bool(ws._listeners) or stopping.done(), "the stop listens")
    then()
    return await stopping


async def _stopped_once_answered(
    monkeypatch: pytest.MonkeyPatch,
    ex: Executor,
    ws: RosbridgeWs,
    answer: Callable[[], Any] | None = None,
) -> VerbResult:
    """`ex` stops the body, and `answer`, the test letting the bridge answer, runs after the zero
    Twist went out and before the stop listens, which waits for it: an answer the stop judges
    by has come by the time it judges, however slow the machine."""
    listen = ws._hears_within
    begun, answered = asyncio.Event(), asyncio.Event()

    async def after_the_answer(seconds: float, *, base_only: bool = False) -> bool:
        begun.set()
        await answered.wait()
        return bool(await listen(seconds, base_only=base_only))

    monkeypatch.setattr(ws, "_hears_within", after_the_answer)
    try:
        stopping = asyncio.ensure_future(ex.run_verb("stop"))
        await _until(lambda: begun.is_set() or stopping.done(), "the stop's zero goes out")
        if answer is not None:
            await answer()
        answered.set()
        return await stopping
    finally:
        monkeypatch.setattr(ws, "_hears_within", listen)


@pytest.mark.parametrize("answer", ["late", "a-failure"])
async def test_a_bridge_whose_round_trip_answers_late_or_says_no_is_alive(
    monkeypatch: pytest.MonkeyPatch, answer: str
) -> None:
    """On a base with no /odom the heartbeat asked for the topic list within the stream's stale
    floor, a second, and read anything else as silence: an answer half as late again, as one
    queued behind camera frames over Wi-Fi, ended the run, and so did a failed answer, which the
    bridge gives at once while its rosapi node restarts. A beat waits for no answer; one of any
    kind is a sign of life, and one that comes after a beat gave up on it still is when it
    comes, and the count of silent beats begins again. The stand-in answers when the test lets
    it, after the beat that gave up on it, and the one asked again never does."""
    ws, listing, _topics = _asked()
    await ws.connect()
    clock = _Clock()
    monkeypatch.setattr(ws_module, "time", clock)
    if answer == "a-failure":
        listing.raise_with = RuntimeError("Service /rosapi/topics does not exist")
    listing.hold()
    try:
        clock.t += STALE_FLOOR_S + BEAT_S
        await ws.heartbeat()  # asks, and waits for nothing
        (first,) = ws._asks
        await _until(lambda: listing.held >= 1, "the first ask reaches the bridge")
        clock.t += ANSWER_WAIT_S + BEAT_S
        await ws.heartbeat()  # the ask has had its time: one silent beat; it asks again
        await _until(lambda: listing.held >= 2, "the second ask reaches the bridge")
        listing.release(1)  # the first answer comes, late, after a beat gave up on it
        await asyncio.wait({first}, timeout=GUARD_S)
        for _ in range(int((ANSWER_WAIT_S + BEAT_S) / BEAT_S) + 1):  # past the second ask's time
            clock.t += BEAT_S
            await ws.heartbeat()
    finally:
        listing.release()


async def test_a_base_with_no_stream_is_noticed_within_its_bound_and_judges_no_stop_by_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no stream to go by the heartbeat asks the bridge, gives each ask `ANSWER_WAIT_S`,
    and fails on the second silent beat in a row, naming the round trip: within the floor, a
    beat's phase, and two asks' time and beats, a bound of the constants. A stop says only what
    it can see, so one after the bridge answers again is not judged by the failed beats. A
    bridge that gave no answer at connect has no round trip, and the socket is all there is.
    Half open is a stand-in that answers nothing until the test lets it, and only the asks it
    holds are given a short deadline, which they cannot meet however fast the machine."""
    ws, listing, topics = _asked()  # the connect's answer counts, however slow the machine
    adapter = RosbridgeAdapter(ws)
    await adapter.connect()
    ws.timeout_s = QUICK_S  # the asks held from here are waited out quickly
    clock = _Clock()
    monkeypatch.setattr(ws_module, "time", clock)
    listing.hold()  # half open: nothing is answered
    try:
        bound = STALE_FLOOR_S + BEAT_S + SILENT_BEATS * (ANSWER_WAIT_S + BEAT_S)
        took = await _noticed_within(ws, clock, bound)
        assert STALE_FLOOR_S + ANSWER_WAIT_S < took <= bound, took
        with pytest.raises(HeartbeatError, match="/rosapi/topics") as failed:
            await ws.heartbeat()
        assert "beats in a row" in str(failed.value)
        await _asks_settled(ws)  # the asks the beats gave up on end, at their deadline
    finally:
        listing.release()  # it answers again
    ws.timeout_s = REQUEST_S  # and an answer is given the run's own deadline to come back in
    await adapter.stop()
    assert topics["/cmd_vel"].published[-1] == twist_message(0.0, 0.0, 0.0)
    assert adapter.stop_error is None, adapter.stop_error
    clock.t += BEAT_S
    with pytest.raises(HeartbeatError):
        await ws.heartbeat()  # nothing has answered yet, and it asks again
    await _asks_settled(ws)  # and is answered
    for _ in range(SILENT_BEATS + 1):
        clock.t += BEAT_S
        await ws.heartbeat()  # the count began again with the answer

    bare, silent, _topics = _asked(timeout_s=QUICK_S)
    silent.hold()  # no answer at connect: no round trip to make
    try:
        await bare.connect()
        for _ in range(3):
            await bare.heartbeat()  # the socket reads up, and nothing else can be asked
        await bare.stop()
        assert bare.stop_error is None
    finally:
        silent.release()


@pytest.mark.parametrize("rosapi", [True, False], ids=["with-rosapi", "without-rosapi"])
async def test_a_half_open_link_under_a_paced_stream_is_noticed_as_soon_with_rosapi_as_without(
    monkeypatch: pytest.MonkeyPatch, rosapi: bool
) -> None:
    """Once a paced stream went quiet, a bridge that answered at connect was asked for its topic
    list on each silent beat with the five-second request deadline, so a half-open link ran
    unseen for about twelve seconds with rosapi, where it was two without. A paced stream's
    silence counts on every beat and no beat waits on an ask: within the window and two beats on
    the transport's clock, the test's, and each beat back within half the request deadline while
    every ask is held unanswered, which a beat that waited for one could not be."""
    ws, listing, topics = _asked()  # the transport's own request deadline, as a run has it
    if not rosapi:
        ws._service_factory = None  # a bridge with no rosapi: nothing can be asked
    await ws.connect()
    clock = _Clock()
    monkeypatch.setattr(ws_module, "time", clock)
    _paced(clock, topics["/odom"], 0.1, 12, 100.0)  # a little over a second at 10 Hz
    listing.hold()  # half open: nothing comes, nothing is answered
    try:
        window = ws._window()
        assert window is not None
        bound = window + SILENT_BEATS * BEAT_S
        took = await _noticed_within(ws, clock, bound, waits_for_nothing=True)
        assert took <= bound, took
    finally:
        listing.release()


@pytest.mark.parametrize("pause_s", [3.0, 30.0], ids=["a-hiccup-it-survived", "a-stall"])
async def test_a_pause_in_the_past_widens_no_window_once_the_stream_is_back(
    monkeypatch: pytest.MonkeyPatch, pause_s: float
) -> None:
    """The window was five of the slowest gap the streams had ever left, so one pause the link
    came back from, a Wi-Fi hiccup or a stall, widened it for the rest of the connection: after
    a 3 s hiccup a 10 Hz base's dead link ran unseen for fifteen seconds, and a stop into it
    said `stopped (velocity zeroed)`. The pace is the stream's last few gaps, capped, so once it
    is back the window is its own again, the heartbeat notices within it, and a stop that hears
    nothing after its zero says so. Between beats the answers that keep the link alive through
    the pause come, however long they take, and the dead link answers nothing at all."""
    ws, listing, topics = _asked()
    adapter = RosbridgeAdapter(ws)
    manifest = await adapter.connect()
    ex = Executor(registry_from_manifest(manifest, adapter), adapter, manifest=manifest)
    clock = _Clock()
    monkeypatch.setattr(ws_module, "time", clock)
    odom = topics["/odom"]
    stamp = _paced(clock, odom, 0.1, 20, 100.0)
    for _ in range(int(pause_s / BEAT_S)):  # the pause: quiet, and rosapi answering
        clock.t += BEAT_S
        await ws.heartbeat()
        await _asks_settled(ws)
    _paced(clock, odom, 0.0, 1, stamp + pause_s)  # it comes back, a pause's gap behind
    _paced(clock, odom, 0.1, STALE_PERIODS + 15, stamp + pause_s)
    assert ws._heard_gap is not None and ws._heard_gap <= 0.1 + 1e-9
    clock.t += 10 * STALE_FLOOR_S  # the link dies, half open: nothing comes, nothing answers
    listing.hold()
    try:
        sent = len(topics["/cmd_vel"].published)
        stopped = await ex.run_verb("stop")
        assert topics["/cmd_vel"].published[sent:] == [twist_message(0.0, 0.0, 0.0)]
        assert not stopped.ok and "not known to have reached" in stopped.summary, stopped.summary
        clock.t -= 10 * STALE_FLOOR_S - 0.1
        took = await _noticed_within(ws, clock, STALE_FLOOR_S + (SILENT_BEATS + 1) * BEAT_S)
        assert took <= STALE_FLOOR_S + (SILENT_BEATS + 1) * BEAT_S
    finally:
        listing.release()


async def test_no_pace_stretches_the_window_past_the_slowest_one_taken(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stream slower than the slowest pace taken, or one whose last gaps were long, is judged
    as if it went at that pace, so a dead link is noticed within `STALE_PERIODS` of it and two
    beats, whatever the past held, and a stop into it, which listens `LISTEN_PERIODS` of that
    pace and no longer, the longest a stop's answer takes, says it is not known to have
    arrived. A bridge with no rosapi: nothing but a message could answer the stop."""
    _ros, topics, adapter, ex = await _driving()
    ws = adapter.transport
    assert isinstance(ws, RosbridgeWs)
    clock = _Clock()
    monkeypatch.setattr(ws_module, "time", clock)
    _paced(clock, topics["/odom"], 3 * SLOWEST_PACE_S, STALE_PERIODS + 1, 100.0)
    made = _listens(monkeypatch, ws)
    stopped = await ex.run_verb("stop")
    assert not stopped.ok and "not known to have reached" in stopped.summary, stopped.summary
    assert f"in the {LISTEN_PERIODS * SLOWEST_PACE_S:.1f} s after" in stopped.summary
    assert made == [(LISTEN_PERIODS * SLOWEST_PACE_S, False)], made
    bound = STALE_PERIODS * SLOWEST_PACE_S + (SILENT_BEATS + 1) * BEAT_S
    took = await _noticed_within(ws, clock, bound)
    assert STALE_PERIODS * SLOWEST_PACE_S < took <= bound, took


async def test_a_stop_into_a_quiet_stream_says_what_answered_and_listens_no_longer_than_it_must(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stream can stop while its link lives, a driver gone or a topic remapped; the bridge
    answers still. A stop into it asks, and the answer to that request, sent after its zero
    Twist, says the zero reached the bridge, and the stop says so, and that the base, whose
    /odom is quiet, is not known to have taken it; on the same base whose bridge answers nothing
    either it went out and nothing came. Each listens `LISTEN_FLOOR_S`, the floor of two of a
    10 Hz pace, for /odom alone, and no longer."""
    ws, listing, topics = _asked()
    adapter = RosbridgeAdapter(ws)
    manifest = await adapter.connect()
    ex = Executor(registry_from_manifest(manifest, adapter), adapter, manifest=manifest)
    clock = _Clock()
    monkeypatch.setattr(ws_module, "time", clock)
    _paced(clock, topics["/odom"], 0.1, STALE_PERIODS + 1, 100.0)
    clock.t += 10 * STALE_FLOOR_S  # the stream stops; the bridge goes on answering
    made = _listens(monkeypatch, ws)

    async def answered() -> None:
        await _until(lambda: not ws._asks, "the stop's own ask is answered")

    stopped = await _stopped_once_answered(monkeypatch, ex, ws, answered)
    assert not stopped.ok and "so the zero reached the bridge" in stopped.summary, stopped.summary
    listing.hold()  # and now it answers nothing either
    try:
        clock.t += BEAT_S
        stopped = await ex.run_verb("stop")
        assert not stopped.ok and "nothing has come over it" in stopped.summary, stopped.summary
    finally:
        listing.release()
    assert made == [(LISTEN_FLOOR_S, True)] * 2, made
    assert topics["/cmd_vel"].published[-1] == twist_message(0.0, 0.0, 0.0)


async def test_a_bridge_that_loses_every_other_answer_keeps_a_base_with_no_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rosapi that answers once in a while, one request in two lost past its deadline, is a
    live link: a beat whose ask went unanswered asks again, so one lost answer is one silent
    beat, and the next answer begins the count again. Between beats the asks a beat set going
    end, as in a run: a lost one is held until the transport has given up on it, and the others
    answer, given a deadline no machine is slow enough to miss."""
    ws, listing, _topics = _asked(timeout_s=LOST_AFTER_S)
    await ws.connect()
    clock = _Clock()
    monkeypatch.setattr(ws_module, "time", clock)
    answer = listing.call
    asked = [0]
    lost: list[threading.Event] = []

    def every_other(args: dict[str, Any], *, timeout: float) -> dict[str, Any]:
        asked[0] += 1
        if asked[0] % 2:
            gone = threading.Event()
            lost.append(gone)
            gone.wait(GUARD_S)  # lost: no answer before the transport gives up on it
        return answer(args, timeout=timeout)

    monkeypatch.setattr(listing, "call", every_other)
    try:
        for _ in range(int(4 * (STALE_FLOOR_S + ANSWER_WAIT_S) / BEAT_S)):
            clock.t += BEAT_S
            await ws.heartbeat()
            await _asks_settled(ws)
            for gone in lost:
                gone.set()  # given up on: its thread may go
    finally:
        for gone in lost:
            gone.set()
    assert asked[0] >= 4, asked


async def _a_stop_hearing(
    ex: Executor, ws: RosbridgeWs, topic: FakeTopic, message: dict[str, Any]
) -> VerbResult:
    """`ex` stops the body, and `message` comes on `topic` once the stop listens, as the next
    message of a live stream would; the stop's result."""
    return await _stopped_while_listening(ex, ws, lambda: topic.callback(message))


async def test_a_stop_right_after_held_messages_come_together_listens_for_the_next_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 10 Hz base over Wi-Fi: a hiccup of a little over half a second holds six messages in the
    bridge's socket, and they come back to back. The pace was the slowest of the last five gaps,
    so it became a millisecond, a stop listened two of it, and said its zero was not known to
    have reached a base whose next message came 50 ms later: the verb that sent it failed on a
    healthy link, and `abort_when` counted it. Messages a long gap held, come together, are not
    the stream's pace, and no stop listens less than `LISTEN_FLOOR_S`."""
    _ros, topics, adapter, ex = await _driving()
    ws = adapter.transport
    assert isinstance(ws, RosbridgeWs)
    clock = _Clock()
    monkeypatch.setattr(ws_module, "time", clock)
    stamp = _paced(clock, topics["/odom"], 0.1, 10, 100.0)
    clock.t += 0.6  # the hiccup
    stamp = _paced(clock, topics["/odom"], 0.001, STALE_PERIODS + 1, stamp + 0.6)
    assert ws._heard_gap is not None and ws._heard_gap >= 0.1, ws._heard_gap
    made = _listens(monkeypatch, ws)
    stopped = await _a_stop_hearing(ex, ws, topics["/odom"], _odom(0.0, stamp=stamp + 0.1))
    assert stopped.ok and stopped.summary.startswith("stopped"), stopped.summary
    assert made == [(LISTEN_PERIODS * ws._heard_gap, False)], made  # the hiccup's gap, not 1 ms


async def test_no_stop_listens_less_than_the_floor_whatever_its_pace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A driver that flushes what it queued sends a few messages back to back with no long gap
    before them to say they were held, and the pace measured over them is a millisecond. A stop
    listens `LISTEN_FLOOR_S` at least, so the next message of a live stream is heard."""
    _ros, topics, adapter, ex = await _driving()
    ws = adapter.transport
    assert isinstance(ws, RosbridgeWs)
    clock = _Clock()
    monkeypatch.setattr(ws_module, "time", clock)
    stamp = _paced(clock, topics["/odom"], 0.1, 10, 100.0)
    stamp = _paced(clock, topics["/odom"], 0.001, STALE_PERIODS + 1, stamp)  # the flush
    assert ws._heard_gap is not None and ws._heard_gap < 0.01, ws._heard_gap
    made = _listens(monkeypatch, ws)
    stopped = await _a_stop_hearing(ex, ws, topics["/odom"], _odom(0.0, stamp=stamp + 0.1))
    assert stopped.ok and stopped.summary.startswith("stopped"), stopped.summary
    assert made == [(LISTEN_FLOOR_S, False)], made


async def test_held_messages_set_neither_the_window_nor_the_listen_of_a_slow_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stream at 0.8 Hz whose link held it eight seconds and then let seven messages through
    together: taken as its pace, they set its window to the floor, a second, and its stops'
    listen to the same, shorter than the 1.25 s its next message takes. The gap that held them
    is the pace until the stream's own gaps come again."""
    _ros, topics, adapter, ex = await _driving()
    ws = adapter.transport
    assert isinstance(ws, RosbridgeWs)
    clock = _Clock()
    monkeypatch.setattr(ws_module, "time", clock)
    stamp = _paced(clock, topics["/odom"], 1.25, STALE_PERIODS + 1, 100.0)
    clock.t += 8.0
    stamp = _paced(clock, topics["/odom"], 0.001, STALE_PERIODS + 2, stamp + 8.0)
    window = ws._window()
    assert window is not None and window > 1.25 + SILENT_BEATS * BEAT_S, window
    made = _listens(monkeypatch, ws)
    stopped = await _a_stop_hearing(ex, ws, topics["/odom"], _odom(0.0, stamp=stamp + 1.25))
    assert stopped.ok and stopped.summary.startswith("stopped"), stopped.summary
    assert made == [(LISTEN_PERIODS * SLOWEST_PACE_S, False)], made  # longer than its 1.25 s


@pytest.mark.parametrize("death", ["the-base-side-only", "the-whole-link"])
async def test_a_stop_after_the_base_went_silent_is_not_said_to_have_stopped_it(
    monkeypatch: pytest.MonkeyPatch, death: str
) -> None:
    """A base's driver can die while rosbridge and rosapi run on: /odom stops, and the bridge
    answers. The stop asked rosapi, heard the answer, and said `stopped (velocity zeroed)`; the
    answer, asked after the zero over the same socket, says the zero reached the bridge, and
    nothing of the base. Where /odom had a pace and is quiet past its window, only /odom
    vouches for the base: the stop listens for it, and with only the bridge heard says the zero
    reached the bridge and the base is not known to have taken it. With the whole link gone it
    says nothing came at all. The stop's own ask is answered before it judges, or never."""
    ws, listing, topics = _asked()
    adapter = RosbridgeAdapter(ws)
    manifest = await adapter.connect()
    ex = Executor(registry_from_manifest(manifest, adapter), adapter, manifest=manifest)
    clock = _Clock()
    monkeypatch.setattr(ws_module, "time", clock)
    _paced(clock, topics["/odom"], 0.1, 20, 100.0)
    clock.t += 3.0
    listing.hold()
    sent = len(topics["/cmd_vel"].published)
    try:

        async def answered() -> None:
            await _until(lambda: listing.held >= 1, "the stop's own ask reaches the bridge")
            listing.release(1)
            await _until(lambda: not ws._asks, "the answer comes")

        stopped = await _stopped_once_answered(
            monkeypatch, ex, ws, answered if death == "the-base-side-only" else None
        )
    finally:
        listing.release()
    assert topics["/cmd_vel"].published[sent:] == [twist_message(0.0, 0.0, 0.0)]
    assert not stopped.ok and "not known to have reached" in stopped.summary, stopped.summary
    assert stopped.data.get("stop_unconfirmed") is True, stopped.data
    if death == "the-base-side-only":
        assert "answered a request sent after the zero Twist" in stopped.summary, stopped.summary
        assert "so the zero reached the bridge" in stopped.summary, stopped.summary
        assert "/odom has brought nothing for 3.0 s" in stopped.summary, stopped.summary
    else:
        assert "nothing has come over it" in stopped.summary, stopped.summary


async def test_odometry_that_comes_back_during_the_stop_vouches_for_the_base(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A base whose /odom was quiet, standing still, and comes on as the stop listens, is heard:
    the stop says `stopped`. A camera image in the same while vouches for the link and not for
    the base, nor for the zero, since it travels the other way: the stop says what came, when,
    and that the base is not known to have taken the zero."""
    _ros, topics, adapter, ex = await _driving("ws://robot.local:9090?image=/cam/compressed")
    ws = adapter.transport
    assert isinstance(ws, RosbridgeWs)
    clock = _Clock()
    monkeypatch.setattr(ws_module, "time", clock)
    stamp = _paced(clock, topics["/odom"], 0.1, 20, 100.0)
    clock.t += 3.0
    frame = {"format": "jpeg", "data": ""}
    made = _listens(monkeypatch, ws)
    imaged = await _a_stop_hearing(ex, ws, topics["/cam/compressed"], frame)
    assert not imaged.ok and "sent an image on /cam/compressed 0.0 s after" in imaged.summary
    assert "not that the zero reached the bridge" in imaged.summary, imaged.summary
    assert "/odom has brought nothing" in imaged.summary, imaged.summary
    back = await _a_stop_hearing(ex, ws, topics["/odom"], _odom(0.0, stamp=stamp + 3.0))
    assert back.ok and back.summary.startswith("stopped"), back.summary
    assert made == [(LISTEN_FLOOR_S, True)] * 2, made


async def test_a_base_that_never_had_odometry_is_vouched_for_by_its_link(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A base with a camera and no /odom it ever published has nothing but the link to go by,
    as before: an image after the zero Twist, and the stop says `stopped`."""
    _ros, topics, adapter, ex = await _driving("ws://robot.local:9090?image=/cam/compressed")
    ws = adapter.transport
    assert isinstance(ws, RosbridgeWs)
    clock = _Clock()
    monkeypatch.setattr(ws_module, "time", clock)
    frame = {"format": "jpeg", "data": ""}
    for _ in range(STALE_PERIODS + 1):
        clock.t += 0.1
        topics["/cam/compressed"].callback(frame)
    clock.t += 3.0
    stopped = await _a_stop_hearing(ex, ws, topics["/cam/compressed"], frame)
    assert stopped.ok and stopped.summary.startswith("stopped"), stopped.summary


async def test_a_stream_that_starts_again_at_its_own_rate_sets_its_pace_at_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only messages that come back to back after a long gap are taken as ones it held
    (`BACK_TO_BACK_S`): a stream that ran slow and comes back at 10 Hz is at 10 Hz, and its
    window is the floor again once `STALE_PERIODS` of its own gaps have come, not later."""
    _ros, topics, adapter, _ex = await _driving()
    ws = adapter.transport
    assert isinstance(ws, RosbridgeWs)
    clock = _Clock()
    monkeypatch.setattr(ws_module, "time", clock)
    stamp = _paced(clock, topics["/odom"], 0.1, 1, 100.0)
    stamp = _paced(clock, topics["/odom"], 3 * SLOWEST_PACE_S, STALE_PERIODS, stamp)
    assert ws._window() == STALE_PERIODS * SLOWEST_PACE_S
    for k in range(1, STALE_PERIODS + 1):
        stamp = _paced(clock, topics["/odom"], 0.1, 1, stamp)
        floor = k == STALE_PERIODS
        assert ws._window() == pytest.approx(
            STALE_FLOOR_S if floor else STALE_PERIODS * SLOWEST_PACE_S
        ), k


async def test_with_odometry_quiet_the_stop_says_only_what_the_bridge_showed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With /odom quiet, anything that came after the zero Twist was read as `the bridge took
    it`: an answer to a request a beat had sent before the zero among it, since the stop sent no
    ask of its own while one was out. Over one socket only an answer to a request sent after the
    zero shows the zero reached the bridge; an earlier one shows only that the link is up. The
    stop sends its own whatever is out, and says which it heard, and when. Which answer comes,
    and when, is the test's to say."""
    ws, listing, topics = _asked()  # the transport's own request deadline, as a run has it
    adapter = RosbridgeAdapter(ws)
    manifest = await adapter.connect()
    ex = Executor(registry_from_manifest(manifest, adapter), adapter, manifest=manifest)
    clock = _Clock()
    monkeypatch.setattr(ws_module, "time", clock)
    _paced(clock, topics["/odom"], 0.1, 20, 100.0)
    clock.t += 3.0
    listing.hold()
    try:
        clock.t += BEAT_S
        await ws.heartbeat()  # past the window: a beat asks, before any zero
        (earlier,) = ws._asks
        await _until(lambda: listing.held >= 1, "the beat's ask reaches the bridge")

        async def the_earlier_answer() -> None:
            await _until(lambda: listing.held >= 2, "the stop's own ask reaches the bridge")
            listing.release(1)
            await _until(earlier.done, "the earlier answer comes")

        heard_before = await _stopped_once_answered(monkeypatch, ex, ws, the_earlier_answer)
        assert "answered a request sent before the zero Twist" in heard_before.summary
        assert "which shows the link is up" in heard_before.summary, heard_before.summary
        assert "so the zero reached the bridge" not in heard_before.summary

        clock.t += BEAT_S
        out = set(ws._asks)

        async def its_own_answer() -> None:
            await _until(lambda: len(set(ws._asks) - out) == 1, "the stop asks")
            (mine,) = set(ws._asks) - out
            await _until(lambda: listing.held >= 3, "the stop's own ask reaches the bridge")
            listing.release(3)
            await _until(mine.done, "its answer comes")

        heard_after = await _stopped_once_answered(monkeypatch, ex, ws, its_own_answer)
        assert "answered a request sent after the zero Twist" in heard_after.summary
        assert "so the zero reached the bridge" in heard_after.summary, heard_after.summary
        for said in (heard_before, heard_after):
            assert not said.ok and said.data.get("stop_unconfirmed") is True, said
    finally:
        listing.release()
