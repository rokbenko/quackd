"""The two halves of quackd's MOSS bridge protocol, talking to each other.

The bridge's own tests (`tests/test_moss_bridge.py`) drive it directly, and the client's
(`tests/test_moss_cockpit.py`) run against a fake cockpit with no bridge at all. Two sides can
each agree with a stand-in and still disagree with each other, so this file runs the REAL
bridge, loaded by path the way the rover runs it, in `--fake`, served on port 0, against the
REAL client in `quackd_moss.cockpit`, with the fake cockpit beside it. A change to either side
that breaks this is a change to both. What it cannot catch is anything about a rover.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import socket
import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass
from types import ModuleType
from typing import Any

import pytest

from quackd.perception.color_blob import ColorBlobDetector
from quackd.safety import Executor, allow_all
from quackd.transport.base import HeartbeatError, Intent, TransportError
from quackd.verbs.registry import registry_from_manifest
from quackd_moss import MossAdapter
from quackd_moss import cockpit as client
from quackd_moss import mock as mock_module
from quackd_moss import verbs as moss_verbs
from tests.conftest import REPO
from tests.fake_moss_cockpit import FakeCockpit

BRIDGE = REPO / "bridge" / "moss" / "quackd_moss_bridge.py"
TOKEN = "63d92f8974c051832d52dd04c78f314b01ef7436cc00e4a3805b9711e5318421"


def _load() -> ModuleType:
    """Its own module name, so nothing patched here leaks into the bridge's own tests."""
    spec = importlib.util.spec_from_file_location("quackd_moss_bridge_contract", BRIDGE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


B = _load()


@dataclass
class Rover:
    cockpit: FakeCockpit
    bridge: Any
    bridge_port: int

    def transport(self, token: str | None = TOKEN) -> client.MossCockpit:
        return client.MossCockpit(
            host="127.0.0.1",
            cockpit_port=self.cockpit.port,
            bridge_port=self.bridge_port,
            token=token,
        )


@pytest.fixture
def rover(request: pytest.FixtureRequest) -> Iterator[Rover]:
    blueprint = getattr(request, "param", "moss-explore")
    with FakeCockpit(blueprint=blueprint) as cockpit:
        bridge = B.fake_bridge(blueprint=blueprint, token=TOKEN, cycle_s=0.3, fps=10.0)
        server = B.serve(bridge, "127.0.0.1", 0)
        bridge.start()
        try:
            _wait(lambda: bridge.bus.frames.heard("nose")["heard"])
            yield Rover(cockpit, bridge, int(server.server_address[1]))
        finally:
            server.shutdown()
            server.server_close()
            bridge.close()


def _wait(predicate: Any, limit_s: float = 10.0) -> None:
    deadline = time.monotonic() + limit_s
    while not predicate():
        assert time.monotonic() < deadline, "timed out waiting"
        time.sleep(0.02)


@contextlib.contextmanager
def _rover_with(blueprint: str = "moss-explore", **bridge_kw: Any) -> Iterator[Rover]:
    """The `rover` fixture's rig, with the bridge built as a test asks."""
    with FakeCockpit(blueprint=blueprint) as cockpit:
        bridge = B.fake_bridge(blueprint=blueprint, token=TOKEN, fps=10.0, **bridge_kw)
        server = B.serve(bridge, "127.0.0.1", 0)
        bridge.start()
        try:
            _wait(lambda: bridge.bus.frames.heard("nose")["heard"])
            yield Rover(cockpit, bridge, int(server.server_address[1]))
        finally:
            server.shutdown()
            server.server_close()
            bridge.close()


# ── the constants ───────────────────────────────────────────────────────────────────────


def test_both_sides_spell_the_protocol_the_same() -> None:
    """Compared across the two files rather than each against a number written here, so moving
    one side alone is caught."""
    for name in (
        "PROTOCOL",
        "PROTOCOL_VERSION",
        "TOKEN_ENV",
        "TOKEN_HEADER",
        "HELLO_PATH",
        "TWIST_PATH",
        "STOP_PATH",
        "FRAME_PATH",
        "ARM_PATH",
        "ARM_CYCLE_PATH",
        "ARM_CANCEL_PATH",
        "MISSION_PATH",
        "KEEPALIVE_PATH",
        "FRAME_STAMP_HEADER",
        "FRAME_AFTER_HEADER",
        "SESSION_MISSES",
        "STARTED",
    ):
        assert getattr(client, name) == getattr(B, name), name
    assert client.BRIDGE_PORT == B.DEFAULT_PORT
    assert (client.NOSE, client.WRIST) == (B.NOSE, B.WRIST)
    assert moss_verbs.JOB_RUNNING == B.JOB_RUNNING and moss_verbs.JOB_ENDINGS == B.JOB_ENDINGS
    assert mock_module.SESSION_MISSES == B.SESSION_MISSES


@pytest.mark.parametrize("track", [mock_module.TRACK_M, 0.3])
@pytest.mark.parametrize(("vx", "wz"), [(0.05, 0.4), (0.0, 0.3), (0.2, -0.1), (-0.1, 1.2)])
def test_the_mock_and_the_bridge_shape_a_twist_the_same(track: float, vx: float, wz: float) -> None:
    """The mock plays the bridge's shaping on the rover side, so it must be the same rule."""
    from dataclasses import replace

    up = replace(B.FAKE_UPSTREAM, track_m=track)
    bvx, bwz, _, clip = B.shape(vx, wz, up)
    mvx, mwz, clipped = mock_module.shape_twist(vx, wz, track)
    assert (bvx, bwz) == pytest.approx((mvx, mwz)) or vx == 0.0
    assert (clip is not None) is clipped or vx == 0.0


# ── connect, over both servers ──────────────────────────────────────────────────────────


async def test_connect_over_the_cockpit_and_the_bridge_gives_the_whole_body(rover: Rover) -> None:
    adapter = MossAdapter(rover.transport())
    manifest = await adapter.connect()
    assert set(manifest.verb_names()) == {
        "observe",
        "report_state",
        "stop",
        "move",
        "go_to",
        "search_scan",
        "approach_and",
        "drive_to",
        "explore",
        "pick_and_drop",
        "stow_arm",
    }
    assert manifest.safety_authority.deadman is True
    assert manifest.extras["bridge"]["version"] == B.BRIDGE_VERSION
    assert manifest.extras["track_m"] == B.FAKE_UPSTREAM.track_m
    assert adapter.camera_keys == ["nose", "wrist"]
    await asyncio.sleep(0.6)
    assert await adapter.get_frame() is not None
    taken = adapter.frame_taken_at()
    assert taken is not None and taken <= time.monotonic()
    assert adapter.frame_period_s is not None and adapter.frame_period_s >= 0.2
    frames = await adapter.get_frames()
    assert [f.name for f in frames] == ["nose", "wrist"] and frames[0].primary
    await adapter.close()


async def test_a_bridge_that_cannot_publish_is_no_bridge_and_no_deadman() -> None:
    """A bridge that answers without a bus would take the twist verbs and the deadman's claim
    and reach nothing on the rover, so the client counts it as absent and says why."""
    why = "moss_dimos is not importable here: ModuleNotFoundError: No module named 'moss_dimos'"
    with FakeCockpit(blueprint="moss-explore") as cockpit:
        bridge = B.Bridge(
            stack=lambda: B.StackInfo(running=True, blueprint="moss-explore", run_id="r"),
            up=None,
            bus=None,
            up_error=why,
            token=TOKEN,
        )
        server = B.serve(bridge, "127.0.0.1", 0)
        try:
            transport = Rover(cockpit, bridge, int(server.server_address[1])).transport()
            adapter = MossAdapter(transport)
            manifest = await adapter.connect()
            assert not {"move", "go_to", "search_scan", "pick_and_drop"} & set(
                manifest.verb_names()
            )
            assert manifest.safety_authority.deadman is False
            assert transport.bridge_up is False
            assert transport.bridge_error is not None and why in transport.bridge_error
            assert "cannot publish" in transport.bridge_error
            await adapter.close()
        finally:
            server.shutdown()
            server.server_close()
            bridge.close()


async def test_a_bridge_that_refuses_the_token_is_refused_by_name(rover: Rover) -> None:
    with pytest.raises(TransportError, match="refused the token"):
        await rover.transport(token="not-the-token").connect()


async def test_a_bridge_of_another_protocol_version_is_refused_by_name(
    rover: Rover, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(B, "PROTOCOL_VERSION", B.PROTOCOL_VERSION + 1)
    with pytest.raises(TransportError, match=r"copy bridge/moss/quackd_moss_bridge\.py"):
        await rover.transport().connect()


# ── the stop, across both ───────────────────────────────────────────────────────────────


async def test_a_twist_reaches_the_pad_channel_and_a_stop_is_the_bridges_then_the_cockpits(
    rover: Rover,
) -> None:
    transport = rover.transport()
    await transport.connect()
    assert (await transport.send_intent(Intent.move(0.1, 0.0, 0.0))).accepted
    bus = rover.bridge.bus
    _wait(lambda: any(v == (0.1, 0.0) for _, _, v in bus.published))
    assert any(c == "/tele_cmd_vel#geometry_msgs.Twist" for _, c, _ in bus.published)
    await transport.stop()
    assert transport.stop_error is None
    zeros = [t for t, _, v in bus.published if v == (0.0, 0.0)]
    soft = rover.cockpit.posted("stop")[-1].t
    assert zeros and min(z for z in zeros if z <= soft) <= soft, "the bridge's zero comes first"
    await transport.close()


async def test_a_stop_reports_a_bridge_that_went_away_and_still_sends_the_soft_stop(
    rover: Rover,
) -> None:
    transport = rover.transport()
    await transport.connect()
    transport.bridge = client.BridgeClient("127.0.0.1", _closed_port(), TOKEN)
    await transport.stop()
    assert transport.stop_error is not None and "the bridge's stop" in transport.stop_error
    assert rover.cockpit.posted("stop"), "neither half skips the other"
    await transport.close()


def _closed_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


# ── the session ─────────────────────────────────────────────────────────────────────────


async def test_the_heartbeat_feeds_the_bridge_and_its_silence_halts_what_quackd_started(
    rover: Rover,
) -> None:
    transport = rover.transport()
    await transport.connect()
    await transport.send_intent(Intent(kind="do", params={"skill": "explore", "action": "start"}))
    assert rover.cockpit.exploring and transport.started == "explore"
    await transport.heartbeat()
    session = rover.bridge.hello()["session"]
    assert session["active"] and session["started"] == "explore"
    assert session["keepalive_s"] >= client.HEARTBEAT_S
    bus = rover.bridge.bus
    _wait(lambda: "true /stop_explore_cmd#std_msgs.Bool" in bus.events, B.SESSION_MISSES * 2.0)
    assert "true /stop_movement#std_msgs.Bool" in bus.events
    assert rover.bridge.session.halts == 1
    await transport.close()


async def test_close_ends_the_session_so_nothing_is_halted_after_quackd_has_gone(
    rover: Rover,
) -> None:
    transport = rover.transport()
    await transport.connect()
    await transport.heartbeat()
    await transport.close()
    before = list(rover.bridge.bus.events)
    await asyncio.sleep(B.SESSION_MISSES * client.HEARTBEAT_S + 0.5)
    assert rover.bridge.session.halts == 0
    assert [e for e in rover.bridge.bus.events if e not in before] == []


@pytest.mark.parametrize("cycled", [True, False], ids=["it moved the arm", "it moved nothing"])
async def test_close_says_the_arm_holds_out_of_its_drive_pose_after_moving_it(
    rover: Rover, cycled: bool
) -> None:
    """The note is for an arm this session moved. One that was out of its drive pose before a
    session that moved nothing is left as it was and not reported, so a probe stays healthy."""
    transport = rover.transport()
    await transport.connect()
    if cycled:
        cycle = Intent(kind="do", params={"skill": "arm_cycle", "pick_pose": "pick"})
        assert (await transport.send_intent(cycle)).accepted
        _wait(lambda: (rover.bridge.jobs.doc() or {}).get("state") == "done")
    rover.bridge.bus.joints["arm/a"] = 1.0
    await transport.close()
    if cycled:
        assert transport.close_note and "out of its drive pose" in transport.close_note
    else:
        assert transport.close_note is None
        assert "arm cancel" not in rover.bridge.bus.events


async def test_close_zeroes_its_own_twist_and_sends_nothing_it_did_not_start() -> None:
    """A session that started nothing sends nothing when it closes, so a goal the owner set on
    the cockpit's page runs on. One whose twist the bridge still repeats sends its zero, and no
    soft stop, since it started no goal. This bridge repeats a twist far longer than the test
    runs, so the zero can only be the close's."""
    from dataclasses import replace

    with _rover_with(up=replace(B.FAKE_UPSTREAM, deadman_s=60.0)) as rover:
        idle = rover.transport()
        await idle.connect()
        await idle.close()
        assert idle.close_note is None
        assert rover.bridge.bus.published == [] and rover.cockpit.posted("stop") == []
        moving = rover.transport()
        await moving.connect()
        assert moving.twist_deadman_s == 60.0, "read from the bridge's hello"
        assert (await moving.send_intent(Intent.move(0.1, 0.0, 0.0))).accepted
        await moving.close()
        sent = [v for _, _, v in rover.bridge.bus.published]
        assert sent[0] == (0.1, 0.0) and sent[-1] == (0.0, 0.0), sent
        assert rover.cockpit.posted("stop") == [], "no soft stop: it started no goal"
        assert moving.close_note is None


async def test_close_cancels_a_cycle_of_its_own_that_no_stop_ended() -> None:
    """By the id the bridge gave it, through `/arm/cancel`, and the arm holds."""
    with _rover_with(cycle_s=30.0) as rover:
        transport = rover.transport()
        await transport.connect()
        cycle = Intent(kind="do", params={"skill": "arm_cycle", "pick_pose": "pick"})
        assert (await transport.send_intent(cycle)).accepted
        _wait(lambda: rover.bridge.bus.executing)
        await transport.close()
        assert "arm cancel" in rover.bridge.bus.events and not rover.bridge.bus.executing
        _wait(lambda: (rover.bridge.jobs.doc() or {}).get("state") == "cancelled")
        assert rover.bridge.jobs.doc()["held"] is True
        assert rover.cockpit.posted("stop") == [], "the cancel, and not a stop"


async def test_a_stack_that_restarted_fails_the_heartbeat(rover: Rover) -> None:
    transport = rover.transport()
    await transport.connect()
    transport.run_id = "a-run-from-before"
    with pytest.raises(HeartbeatError, match="not running any more"):
        await transport.heartbeat()
    await transport.close()


# ── the arm, end to end ─────────────────────────────────────────────────────────────────


async def test_pick_and_drop_runs_the_cycle_on_the_bridge(rover: Rover) -> None:
    adapter = MossAdapter(rover.transport())
    manifest = await adapter.connect()
    ex = Executor(
        registry_from_manifest(manifest, adapter),
        adapter,
        contract=None,
        detector=ColorBlobDetector(),
        confirm=allow_all,
        manifest=manifest,
    )
    result = await ex.run_verb("pick_and_drop", {"pick_pose": "pick_low"})
    assert result.ok, result.summary
    assert result.data["job"]["exit"] == 0
    await adapter.close()


# ── moss-litter, end to end ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("rover", ["moss-litter"], indirect=True)
async def test_on_moss_litter_the_stop_ends_the_mission_and_it_stays_ended(rover: Rover) -> None:
    adapter = MossAdapter(rover.transport())
    manifest = await adapter.connect()
    assert set(manifest.verb_names()) == {"observe", "report_state", "stop", "collect_litter"}
    state = await adapter.get_state()
    assert state.extras["mission"]["answering"] is True
    await adapter.stop()
    assert adapter.stop_error is None, adapter.stop_error
    state = await adapter.get_state()
    assert state.extras["mission"]["stopped"] is True
    assert moss_verbs.moss_conditions()["mission_up"](state) == moss_verbs.MISSION_STOPPED
    assert rover.bridge.bus.events[0] == "mission stop"
    explores = len(rover.cockpit.posted("explore"))
    start = Intent(kind="do", params={"skill": "collect_litter", "action": "start"})
    ack = await adapter.send_intent(start)
    assert not ack.accepted and ack.reason == moss_verbs.MISSION_STOPPED
    assert len(rover.cockpit.posted("explore")) == explores, "no explorer for no mission"
    await adapter.close()
    assert "litter mission is stopped" in (adapter.close_note or "")


@pytest.mark.parametrize("rover", ["moss-litter"], indirect=True)
async def test_on_moss_litter_a_probe_that_started_nothing_leaves_the_mission_running(
    rover: Rover,
) -> None:
    """What `quackd doctor` does: connect, ask the rover's health and close. It started nothing,
    so it stops nothing and says nothing, and it keeps no session the bridge could halt however
    long it takes to close."""
    adapter = MossAdapter(rover.transport())
    await adapter.connect()
    health = await adapter.health()
    assert health.ok, health.reason
    await asyncio.sleep(B.SESSION_MISSES * client.HEARTBEAT_S + 0.5)
    await adapter.close()
    assert adapter.close_note is None
    assert "mission stop" not in rover.bridge.bus.events
    assert rover.cockpit.posted("stop") == []
    assert rover.bridge.session.halts == 0
    assert rover.bridge.mission_doc()["stopped"] is False


@pytest.mark.parametrize("rover", ["moss-litter"], indirect=True)
async def test_on_moss_litter_close_stops_a_mission_this_session_started(rover: Rover) -> None:
    """Only the start went out here, with no verb's own stop after it, so the close sends the
    whole stop, the mission's first."""
    adapter = MossAdapter(rover.transport())
    await adapter.connect()
    start = Intent(kind="do", params={"skill": "collect_litter", "action": "start"})
    assert (await adapter.send_intent(start)).accepted
    await adapter.close()
    assert rover.bridge.bus.events[0] == "mission stop"
    assert rover.bridge.mission_doc()["stopped"] is True
    assert "litter mission is stopped" in (adapter.close_note or "")


async def test_on_moss_litter_a_stop_left_unconfirmed_is_said_at_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A mission still answering after the stop a run ends with: the bridge says so, and so
    does the run's last word, which must not claim the mission ended."""
    monkeypatch.setattr(B, "MISSION_CONFIRM_S", 0.4)
    with FakeCockpit(blueprint="moss-litter") as cockpit:
        bridge = B.fake_bridge(
            blueprint="moss-litter", token=TOKEN, fps=10.0, mission_ignores_stop=True
        )
        server = B.serve(bridge, "127.0.0.1", 0)
        bridge.start()
        try:
            _wait(lambda: bridge.bus.frames.heard("nose")["heard"])
            transport = Rover(cockpit, bridge, int(server.server_address[1])).transport()
            adapter = MossAdapter(transport)
            await adapter.connect()
            await adapter.stop()
            await adapter.close()
            assert transport.mission_stop == "unconfirmed"
            assert transport.stop_error is not None and "still answers" in transport.stop_error
            note = adapter.close_note or ""
            assert "not confirmed" in note and "is stopped" not in note
        finally:
            server.shutdown()
            server.server_close()
            bridge.close()
