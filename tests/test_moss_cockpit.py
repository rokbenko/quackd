"""The real cockpit client against a fake of MOSS's cockpit, over loopback.

`tests/fake_moss_cockpit.py` is written from `tools/moss_cockpit.py` at the pin, and every test
here drives quackd's own client and transport against it: the state, the nose stream, goals and
a goal_reached that belongs to someone else, the explorer, the soft stop, and the idle keepalive
whose cadence comes from the cockpit's own idle limit. No bridge runs in this file: its port
points at nothing, which is a rover with no bridge, so the manifest is the cockpit's alone.
"""

from __future__ import annotations

import asyncio
import contextlib
import socket
import time
from collections.abc import Iterator
from typing import Any

import pytest
from PIL import Image

from quackd.transport.base import HeartbeatError, Intent, TransportError
from quackd.verbs.registry import VerbContext
from quackd_moss import MossAdapter
from quackd_moss import cockpit as moss_client
from quackd_moss.cockpit import (
    CMD_HEADER_NAME,
    CMD_HEADER_VALUE,
    CockpitClient,
    LinkDown,
    MjpegReader,
    MossCockpit,
)
from quackd_moss.verbs import DriveToParams, MinutesParams, drive_to, explore
from tests.fake_moss_cockpit import FakeCockpit


def _closed_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture
def cockpit() -> Iterator[FakeCockpit]:
    with FakeCockpit() as fake:
        yield fake


def _transport(fake: FakeCockpit) -> MossCockpit:
    return MossCockpit(host="127.0.0.1", cockpit_port=fake.port, bridge_port=_closed_port())


# ── the client ──────────────────────────────────────────────────────────────────────────


def test_the_state_comes_back_as_the_cockpit_sends_it(cockpit: FakeCockpit) -> None:
    state = CockpitClient("127.0.0.1", cockpit.port).state()
    assert state["stack"]["blueprint"] == "moss-explore" and state["bus"]["transport"] == "lcm"
    assert {"pose", "base", "nav", "idle", "topics"} <= set(state)


def test_every_command_carries_the_cockpits_header_and_one_without_it_is_refused(
    cockpit: FakeCockpit,
) -> None:
    client = CockpitClient("127.0.0.1", cockpit.port)
    ok, _ = client.stop()
    assert ok
    assert cockpit.posted("stop")[-1].headers.get(CMD_HEADER_NAME) == CMD_HEADER_VALUE
    bare = http_post(cockpit.port, "/cmd/stop")
    assert bare == 403, "the fake refuses a command with no header, as the cockpit does"


def http_post(port: int, path: str) -> int:
    import http.client

    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("POST", path, body=b"{}")
    status = conn.getresponse().status
    conn.close()
    return status


def test_a_refused_command_is_data_and_only_a_silent_cockpit_raises(cockpit: FakeCockpit) -> None:
    client = CockpitClient("127.0.0.1", cockpit.port)
    cockpit.blueprint = "unitree-go2"
    ok, doc = client.goal(1.0, 0.0)
    assert not ok and "not a MOSS stack" in doc["error"]
    cockpit.transport = "zenoh"
    ok, doc = client.explore("start")
    assert not ok and "cannot publish" in doc["error"]
    with pytest.raises(LinkDown):
        CockpitClient("127.0.0.1", _closed_port()).state()


def test_the_client_cannot_post_the_stack_command(cockpit: FakeCockpit) -> None:
    with pytest.raises(ValueError, match="never posts /cmd/stack"):
        CockpitClient("127.0.0.1", cockpit.port).command("stack", {"action": "stop"})
    assert cockpit.posted("stack") == []


# ── the nose stream ─────────────────────────────────────────────────────────────────────


def test_the_mjpeg_reader_keeps_the_newest_nose_frame(cockpit: FakeCockpit) -> None:
    cockpit.frame_period_s = 0.05
    reader = MjpegReader("127.0.0.1", cockpit.port)
    reader.start()
    try:
        assert reader.first.wait(5.0)
        jpeg, first = reader.latest() or (b"", 0.0)
        assert jpeg[:2] == b"\xff\xd8"
        time.sleep(0.3)
        later = reader.latest()
        assert later is not None and later[1] > first, "it keeps reading, the newest wins"
        assert cockpit.requests[-1].query == "cam=nose"
    finally:
        reader.stop()


def test_a_fifth_viewer_is_refused_and_the_reader_says_why(cockpit: FakeCockpit) -> None:
    cockpit.max_viewers = 1
    first = MjpegReader("127.0.0.1", cockpit.port)
    first.start()
    try:
        assert first.first.wait(5.0)
        second = MjpegReader("127.0.0.1", cockpit.port)
        second.start()
        try:
            deadline = time.monotonic() + 5
            while second.error is None and time.monotonic() < deadline:
                time.sleep(0.05)
            assert second.error and "503" in second.error and "viewers" in second.error
            assert second.latest() is None
        finally:
            second.stop()
    finally:
        first.stop()


def test_a_stopped_camera_has_its_last_frame_expire_on_the_cameras_own_rate() -> None:
    """Every fetch from a camera that stopped waits out the bridge and fails, and a frame's
    life once stretched with how long the last fetch took, to four times the bridge's wait.
    It expires on the camera's configured rate, as the Microduck's frames do."""
    t = MossCockpit(host="127.0.0.1")
    t.bridge_up = True
    t._frame, t._frame_after = Image.new("RGB", (8, 8)), True
    t._fetch_s = moss_client.FRAME_WAIT_S + moss_client.CALL_TIMEOUT_S
    t._frame_asked = time.monotonic() - moss_client.stale_after_s(moss_client.CAMERA_PERIOD_S) - 1.0
    assert t._frame_age_s() is None and t.frame_taken_at() is None
    assert t.frame_period_s == t._fetch_s, "the cadence still says how slow a frame comes"


async def test_a_failed_fetch_never_says_how_long_a_frame_takes() -> None:
    t = MossCockpit(host="127.0.0.1")
    t.bridge_up = True

    def waited_and_failed(cam: str) -> Any:
        _block_the_loop(0.3)
        raise LinkDown("the bridge waited out a camera that stopped")

    t.bridge.frame = waited_and_failed  # type: ignore[method-assign]
    t._frames_on = True  # as connect starts the loop
    task = asyncio.create_task(t._frame_loop())
    await asyncio.sleep(0.8)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    assert t._fetch_s == 0.0 and t.camera_error is not None


# ── the transport, with no bridge ───────────────────────────────────────────────────────


async def test_with_no_bridge_the_manifest_is_the_cockpits_alone(cockpit: FakeCockpit) -> None:
    adapter = MossAdapter(_transport(cockpit))
    manifest = await adapter.connect()
    assert set(manifest.verb_names()) == {
        "observe",
        "report_state",
        "stop",
        "drive_to",
        "explore",
    }
    assert manifest.safety_authority.deadman is False
    assert manifest.extras["bridge"] is None and "did not answer" in manifest.extras["bridge_error"]
    assert adapter.frame_taken_at() is None and adapter.frame_period_s is None
    assert await adapter.get_frame() is not None
    await adapter.close()


async def test_moss_nav_with_no_bridge_has_nothing_that_moves(cockpit: FakeCockpit) -> None:
    cockpit.blueprint = "moss-nav"
    manifest = await MossAdapter(_transport(cockpit)).connect()
    assert set(manifest.verb_names()) == {"observe", "report_state", "stop"}


@pytest.mark.parametrize(
    ("change", "said"),
    [
        ({"mode": "demo"}, "--demo mode"),
        ({"running": False}, "no stack is running"),
        ({"blueprint": "moss-gamepad"}, "the pad and the base alone"),
        ({"blueprint": "moss-arm"}, "reads only the newest dimOS run, and that is moss-arm"),
        ({"transport": "zenoh"}, "speak LCM only"),
        ({"blueprint": "moss-litter"}, "only quackd's bridge"),
    ],
)
async def test_a_rover_quackd_cannot_drive_is_refused_at_connect(
    cockpit: FakeCockpit, change: dict[str, Any], said: str
) -> None:
    for key, value in change.items():
        setattr(cockpit, key, value)
    with pytest.raises(TransportError, match=said):
        await _transport(cockpit).connect()


async def test_nothing_answering_names_the_cockpit_and_the_tunnel() -> None:
    transport = MossCockpit(host="127.0.0.1", cockpit_port=_closed_port())
    with pytest.raises(TransportError, match=r"tools/moss-fly\.sh cockpit") as e:
        await transport.connect()
    assert "ssh -L 8904:127.0.0.1:8904 -L 9876:127.0.0.1:9876" in str(e.value)


@pytest.mark.parametrize("error", ["ValueError: no pose yet", "KeyError: 'base'"])
async def test_a_cockpit_that_answers_an_error_is_not_told_to_start(
    cockpit: FakeCockpit, error: str
) -> None:
    cockpit.state_error = error
    with pytest.raises(TransportError) as e:
        await _transport(cockpit).connect()
    said = str(e.value)
    assert "answered, but not with the rover's state" in said and error in said
    assert "nothing answers" not in said and "ssh -L" not in said


async def test_the_state_reads_the_pose_the_battery_and_the_freshness(cockpit: FakeCockpit) -> None:
    cockpit.pose = {"x": 1.25, "y": -0.5, "yaw": 0.75, "age_s": 0.1}
    transport = _transport(cockpit)
    await transport.connect()
    state = await transport.get_state()
    assert (state.x, state.y, state.theta) == (1.25, -0.5, 0.75)
    assert state.battery_percent == 58 and state.extras["battery_v"] == 11.6
    assert state.extras["telemetry_age_s"] == 0.2 and state.extras["stack"] == "moss-explore"
    assert state.extras["pose_limit_s"] == pytest.approx(max(2.0, 4 / cockpit.odom_hz))
    await transport.close()


async def test_a_cockpit_that_stops_answering_is_a_state_that_says_so(cockpit: FakeCockpit) -> None:
    transport = _transport(cockpit)
    await transport.connect()
    cockpit.__exit__()
    state = await transport.get_state()
    assert state.extras["state_stale"] is True and "did not answer" in state.extras["cockpit_error"]
    await transport.stop()
    assert transport.stop_error and "the cockpit's soft stop" in transport.stop_error
    with pytest.raises(HeartbeatError, match="stopped answering"):
        await transport.heartbeat()


# ── goals, the explorer and the soft stop ───────────────────────────────────────────────


async def test_drive_to_waits_for_its_own_goal_and_ignores_someone_elses_arrival(
    cockpit: FakeCockpit,
) -> None:
    """While exploring, the cockpit stops the explorer and holds the goal back. Meanwhile the
    explorer's own stay here goal shows, reached. That arrival is not drive_to's."""
    cockpit.exploring, cockpit.goal_defer_s = True, 0.8
    adapter = MossAdapter(_transport(cockpit))
    await adapter.connect()
    ctx = VerbContext(transport=adapter)
    task = asyncio.ensure_future(drive_to(ctx, DriveToParams(x_m=0.5, y_m=0.0)))
    await asyncio.sleep(0.3)
    cockpit.goal, cockpit.goal_reached = {"x": 9.0, "y": 9.0}, True  # the explorer's, reached
    await asyncio.sleep(0.4)
    assert not task.done(), "a goal_reached for somebody else's goal is not believed"
    await asyncio.sleep(1.2)
    assert cockpit.goal == {"x": 0.5, "y": 0.0} and cockpit.goal_reached is None
    cockpit.reach_goal(True)
    result = await asyncio.wait_for(task, 5.0)
    assert result.ok, result.summary
    assert cockpit.posted("stop"), "it ends with a stop"
    await adapter.close()


async def test_drive_to_fails_when_its_goal_is_replaced(cockpit: FakeCockpit) -> None:
    adapter = MossAdapter(_transport(cockpit))
    await adapter.connect()
    task = asyncio.ensure_future(
        drive_to(VerbContext(transport=adapter), DriveToParams(x_m=1.0, y_m=0.0))
    )
    await asyncio.sleep(0.8)
    assert cockpit.goal == {"x": 1.0, "y": 0.0}
    cockpit.goal = {"x": -3.0, "y": 2.0}
    result = await asyncio.wait_for(task, 5.0)
    assert not result.ok and "replaced drive_to's" in result.summary
    await adapter.close()


async def test_explore_starts_the_explorer_and_always_ends_with_its_stop(
    cockpit: FakeCockpit,
) -> None:
    adapter = MossAdapter(_transport(cockpit))
    await adapter.connect()
    task = asyncio.ensure_future(
        explore(VerbContext(transport=adapter), MinutesParams(minutes=0.05))
    )
    await asyncio.sleep(1.5)
    assert cockpit.exploring
    result = await asyncio.wait_for(task, 10.0)
    assert result.ok and not cockpit.exploring
    actions = [r.body.get("action") for r in cockpit.posted("explore") if r.body]
    assert actions == ["start", "stop"] and cockpit.posted("stop")
    await adapter.close()


async def test_a_goal_through_the_transport_says_it_was_started(cockpit: FakeCockpit) -> None:
    transport = _transport(cockpit)
    await transport.connect()
    ack = await transport.send_intent(
        Intent(kind="pose", params={"x": 0.3, "y": 0.1, "frame": "world"})
    )
    assert ack.accepted and transport.started == "goal"
    await transport.stop()
    assert transport.started is None and transport.stop_error is None
    refused = await transport.send_intent(
        Intent(kind="pose", params={"x": 0, "y": 0, "frame": "body"})
    )
    assert not refused.accepted and "world frame" in (refused.reason or "")
    await transport.close()


async def test_close_ends_a_goal_it_started_and_posts_nothing_when_it_started_nothing(
    cockpit: FakeCockpit,
) -> None:
    """A close is a soft stop only for a goal or an exploration this session started. One that
    started nothing posts nothing, so the owner's goal from the cockpit's page runs on."""
    idle = _transport(cockpit)
    await idle.connect()
    cockpit.goal = {"x": 1.0, "y": 0.0}  # the owner's
    await idle.close()
    assert cockpit.posted("stop") == [] and cockpit.goal == {"x": 1.0, "y": 0.0}
    assert idle.close_note is None
    transport = _transport(cockpit)
    await transport.connect()
    goal = Intent(kind="pose", params={"x": 0.3, "y": 0.1, "frame": "world"})
    assert (await transport.send_intent(goal)).accepted
    await transport.close()
    assert len(cockpit.posted("stop")) == 1 and transport.started is None


async def test_with_no_bridge_a_stop_after_the_stack_restarted_sends_no_soft_stop(
    cockpit: FakeCockpit,
) -> None:
    """The owner restarted the stack since quackd connected, and the cockpit names another
    run, whose explorer quackd's soft stop would end: none is sent, and the stop says why.
    A moss-arm run the cockpit shows over the base tells nothing of the base's run, so that
    stop goes out."""
    transport = _transport(cockpit)
    await transport.connect()
    connected = transport.run_id
    assert connected == cockpit.run_id
    cockpit.run_id = "20261021-restarted"
    await transport.stop()
    assert cockpit.posted("stop") == [], "nothing went to the new run"
    said = transport.stop_error or ""
    assert "20261021-restarted" in said and connected in said, said
    cockpit.run_id, cockpit.newest = connected, "moss-arm"
    await transport.stop()
    assert len(cockpit.posted("stop")) == 1, "the base may be the one it connected to"
    await transport.close()


async def test_with_no_bridge_a_newer_run_of_another_blueprint_is_stopped_all_the_same(
    cockpit: FakeCockpit,
) -> None:
    """The cockpit shows only the newest run: one of another blueprint, started by hand, says
    nothing of whether the run quackd connected to still runs behind it, so the soft stop goes
    out all the same."""
    transport = _transport(cockpit)
    await transport.connect()
    cockpit.newest, cockpit.newest_run_id = "unitree-go2-sim", "20261021-other"
    await transport.stop()
    assert len(cockpit.posted("stop")) == 1, transport.stop_error
    await transport.close()


async def test_with_no_bridge_close_after_a_restart_leaves_the_new_runs_goal_alone(
    cockpit: FakeCockpit,
) -> None:
    """A goal quackd started went with the run it was sent to. Its close sends the new run
    no soft stop, which would end that run's own goal, and its note says why."""
    transport = _transport(cockpit)
    await transport.connect()
    connected = transport.run_id
    ack = await transport.send_intent(
        Intent(kind="pose", params={"x": 0.3, "y": 0.1, "frame": "world"})
    )
    assert ack.accepted and transport.started == "goal"
    cockpit.run_id = "20261021-restarted"
    before = len(cockpit.posted("stop"))
    await transport.close()
    assert len(cockpit.posted("stop")) == before, "the new run's explorer and goal untouched"
    note = transport.close_note or ""
    assert "restarted as run 20261021-restarted" in note and connected in note, note


async def test_with_no_bridge_a_twist_and_an_arm_cycle_are_refused_as_data(
    cockpit: FakeCockpit,
) -> None:
    transport = _transport(cockpit)
    await transport.connect()
    assert not (await transport.send_intent(Intent.move(0.1, 0.0, 0.0))).accepted
    cycle = Intent(kind="do", params={"skill": "arm_cycle", "pick_pose": "pick"})
    assert "bridge" in ((await transport.send_intent(cycle)).reason or "")
    assert not (await transport.send_intent(Intent.look(1, 0, 0))).accepted
    assert not (await transport.send_intent(Intent.enable(False))).accepted
    await transport.close()


# ── the keepalives ──────────────────────────────────────────────────────────────────────


def _wait(predicate: Any, limit_s: float = 5.0) -> None:
    deadline = time.monotonic() + limit_s
    while not predicate():
        assert time.monotonic() < deadline, "timed out waiting"
        time.sleep(0.02)


def _block_the_loop(seconds: float) -> None:
    """What a prompt on the terminal does to the event loop: nothing on it runs meanwhile."""
    time.sleep(seconds)


@pytest.mark.parametrize("limit", [30.0, 120.0])
async def test_the_idle_keepalive_follows_the_cockpits_own_limit(
    cockpit: FakeCockpit, limit: float
) -> None:
    """Three times inside the cockpit's own limit, from the loop's own heartbeat: a loop
    blocked on a prompt sends none, which is the cost of a deadman that also covers a wedged
    loop."""
    cockpit.idle_limit_s = limit
    transport = _transport(cockpit)
    await transport.connect()
    await transport.heartbeat()
    assert len(cockpit.posted("keepalive")) == 1
    await transport.heartbeat()
    assert len(cockpit.posted("keepalive")) == 1, "not again until a third of the limit has gone"
    _block_the_loop(1.2)
    assert len(cockpit.posted("keepalive")) == 1, "and none while the loop is held"
    transport._last_cockpit_keepalive = time.monotonic() - limit / 3 - 0.1
    await transport.heartbeat()
    assert len(cockpit.posted("keepalive")) == 2
    await transport.close()


async def test_no_idle_keepalive_when_the_idle_stop_is_off(cockpit: FakeCockpit) -> None:
    cockpit.idle_enabled = False
    transport = _transport(cockpit)
    await transport.connect()
    await asyncio.sleep(1.2)
    assert cockpit.posted("keepalive") == []
    await transport.close()


async def test_a_stack_the_idle_stop_ended_fails_the_heartbeat_by_name(
    cockpit: FakeCockpit,
) -> None:
    transport = _transport(cockpit)
    await transport.connect()
    cockpit.running = False
    with pytest.raises(HeartbeatError, match="idle stop"):
        await transport.heartbeat()
    await transport.close()
