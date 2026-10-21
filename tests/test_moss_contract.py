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

import quackd.cli as cli
from quackd.duckfile.parser import parse_duck_text
from quackd.perception.color_blob import ColorBlobDetector
from quackd.safety import Executor, Heartbeat, allow_all
from quackd.transport.base import HeartbeatError, Intent, TransportError
from quackd.verbs.registry import VerbContext, registry_from_manifest
from quackd_moss import MossAdapter, describe
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
        cockpit.bus = bridge.bus  # one LCM bus on the rover, which both read
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


def _block_the_loop(seconds: float) -> None:
    """What a prompt on the terminal does to the event loop: nothing on it runs meanwhile."""
    time.sleep(seconds)


@contextlib.contextmanager
def _rover_with(blueprint: str = "moss-explore", **bridge_kw: Any) -> Iterator[Rover]:
    """The `rover` fixture's rig, with the bridge built as a test asks."""
    with FakeCockpit(blueprint=blueprint) as cockpit:
        bridge = B.fake_bridge(blueprint=blueprint, token=TOKEN, fps=10.0, **bridge_kw)
        cockpit.bus = bridge.bus
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
        "MAX_KEEPALIVE_S",
        "STARTED",
    ):
        assert getattr(client, name) == getattr(B, name), name
    assert client.BRIDGE_PORT == B.DEFAULT_PORT
    assert (client.NOSE, client.WRIST) == (B.NOSE, B.WRIST)
    assert moss_verbs.JOB_RUNNING == B.JOB_RUNNING and moss_verbs.JOB_ENDINGS == B.JOB_ENDINGS
    assert mock_module.SESSION_MISSES == B.SESSION_MISSES
    assert mock_module.KEEPALIVE_S == client.HEARTBEAT_S
    assert mock_module.MAX_KEEPALIVE_S == B.MAX_KEEPALIVE_S


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


async def test_a_twist_reaches_the_pad_channel_and_a_stop_sends_both_halves(
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
    assert any(v == (0.0, 0.0) for _, _, v in bus.published), "the bridge's zero"
    assert rover.cockpit.posted("stop"), "and the cockpit's soft stop, beside it"
    await transport.close()


def test_the_clients_wait_for_a_stop_covers_the_bridges_worst_case() -> None:
    """The bridge bounds its stop, every RPC on the rover answering at its timeout, and the
    client waits that long and two seconds more for the link: a stop it gave up on early
    would be said not delivered while the bridge was still delivering it."""
    assert client.STOP_TIMEOUT_S >= B.STOP_WORST_S + 2.0


def test_every_verb_leaves_its_own_stop_room_before_the_executor_cuts_it() -> None:
    """A verb that gives up, or runs out its minutes, ends with its own stop, which is the
    transport's and waits up to the bridge's worst case for an answer. The executor's timeout
    keeps that wait beyond the verb's own give up, and the state read and the poll before it,
    so the verb's stop runs and reports rather than the executor cutting it short."""
    slowest_stop = client.STOP_TIMEOUT_S + 1.0  # the client's own wait for the bridge's answer
    before_it = 2 * client.CALL_TIMEOUT_S + max(
        moss_verbs.EXPLORE_POLL_S, moss_verbs.DRIVE_TO_POLL_S, moss_verbs.ARM_POLL_S
    )
    assert slowest_stop + before_it <= moss_verbs.VERB_MARGIN_S
    own = moss_verbs.moss_verbs()
    for name, verb in own.items():
        assert verb.timeout_s is not None and verb.timeout_s <= 600, name
    assert own["explore"].timeout_s == moss_verbs.EXPLORE_MAX_MIN * 60 + moss_verbs.VERB_MARGIN_S
    for spec in describe("cockpit").verbs:
        assert spec.timeout_s is None or spec.timeout_s <= 600, spec.name


async def test_the_soft_stop_goes_out_beside_a_bridge_stop_that_waits_on_the_arm(
    rover: Rover,
) -> None:
    """A moss-arm run whose coordinator hangs holds the bridge's cancel for the RPC's whole
    timeout. The bridge's zero and the cockpit's soft stop go out at once all the same: a
    goal or an exploration never drives on while the arm is asked."""
    transport = rover.transport()
    await transport.connect()
    bus = rover.bridge.bus
    bus.arm_answers = False
    began = time.monotonic()
    await transport.stop()
    took = time.monotonic() - began
    soft = rover.cockpit.posted("stop")[-1].t - began
    zero = min(t for t, _, v in bus.published if v == (0.0, 0.0) and t >= began) - began
    assert took >= B.RPC_TIMEOUT_S, "the bridge's stop waited on the arm"
    assert soft < B.RPC_TIMEOUT_S / 2 and zero < B.RPC_TIMEOUT_S / 2, (soft, zero, took)
    await transport.close()


async def test_a_stop_after_the_stack_restarted_touches_nothing_of_the_new_run(
    rover: Rover,
) -> None:
    """The owner restarted the base stack: the bridge reads another run, and says so in the
    keepalive answer. quackd's stop names the run it connected to, so neither the bridge's
    stop nor the cockpit's soft stop touches the new run, and both say why."""
    from dataclasses import replace

    transport = rover.transport()
    await transport.connect()
    assert transport.run_id == "fake-run"
    old = rover.bridge.stack()
    rover.bridge._stack = lambda: replace(old, run_id="restarted-run")
    _wait(lambda: (transport._session_doc.get("stack") or {}).get("run_id") == "restarted-run")
    bus = rover.bridge.bus
    published, events = len(bus.published), len(bus.events)
    await transport.stop()
    assert rover.cockpit.posted("stop") == [], "no soft stop to the new run"
    assert len(bus.published) == published and len(bus.events) == events, "no zero either"
    said = transport.stop_error or ""
    assert "restarted-run" in said and "touches nothing of it" in said, said
    await transport.close()
    assert "restarted as run restarted-run" in (transport.close_note or "")


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


def _dies(transport: client.MossCockpit) -> None:
    """quackd killed where it stands: its keepalive thread stops with nothing else sent."""
    transport._beats_stop.set()


async def test_the_keepalive_thread_feeds_the_bridge_and_its_death_halts_what_quackd_started(
    rover: Rover,
) -> None:
    transport = rover.transport()
    await transport.connect()
    await transport.send_intent(Intent(kind="do", params={"skill": "explore", "action": "start"}))
    assert rover.cockpit.exploring and transport.started == "explore"
    session = rover.bridge.sessions[transport.session_id]
    assert session.started == "explore" and session.keepalive_s == client.HEARTBEAT_S
    await transport.heartbeat()
    _dies(transport)
    bus = rover.bridge.bus
    _wait(lambda: "true /stop_explore_cmd#std_msgs.Bool" in bus.events, B.SESSION_MISSES * 2.0)
    assert "true /stop_movement#std_msgs.Bool" in bus.events
    assert rover.bridge.halts == 1
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
    assert rover.bridge.halts == 0 and rover.bridge.sessions == {}
    assert [e for e in rover.bridge.bus.events if e not in before] == []


async def test_a_loop_blocked_on_a_prompt_never_reads_as_a_dead_quackd(rover: Rover) -> None:
    """A confirm on the terminal blocks the event loop for as long as the person reads it. The
    keepalives come from the transport's own thread, so the bridge's deadman never fires on a
    quackd that is alive and waiting."""
    transport = rover.transport()
    await transport.connect()
    await transport.send_intent(Intent(kind="do", params={"skill": "explore", "action": "start"}))
    _block_the_loop(B.SESSION_MISSES * client.HEARTBEAT_S * 2)
    assert rover.bridge.halts == 0 and transport.session_id in rover.bridge.sessions
    assert "true /stop_explore_cmd#std_msgs.Bool" not in rover.bridge.bus.events
    await transport.heartbeat()
    await transport.close()


async def test_a_doctor_beside_a_run_never_strips_the_runs_deadman(rover: Rover) -> None:
    """Each transport draws its own session id, so a probe's keepalive and its end are its own,
    and the run's exploration is still halted when the run dies."""
    run = rover.transport()
    await run.connect()
    start = Intent(kind="do", params={"skill": "explore", "action": "start"})
    assert (await run.send_intent(start)).accepted
    doctor = MossAdapter(rover.transport())
    await doctor.connect()
    assert (await doctor.health()).ok
    await doctor.close()
    assert rover.bridge.sessions[run.session_id].started == "explore"
    _dies(run)
    bus = rover.bridge.bus
    _wait(lambda: "true /stop_explore_cmd#std_msgs.Bool" in bus.events, B.SESSION_MISSES * 2.0)
    assert rover.bridge.halts == 1
    await run.close()


async def test_a_start_is_said_to_the_bridge_before_it_goes_out(rover: Rover) -> None:
    """A quackd killed the instant after its explore start: the bridge already has its word,
    because the keepalive that carried it went out first."""
    transport = rover.transport()
    await transport.connect()
    original = rover.cockpit.command
    seen: list[str | None] = []

    def watching(name: str, body: dict[str, Any]) -> Any:
        if name == "explore":
            seen.append(rover.bridge.sessions[transport.session_id].started)
        return original(name, body)

    rover.cockpit.command = watching  # type: ignore[method-assign]
    start = Intent(kind="do", params={"skill": "explore", "action": "start"})
    assert (await transport.send_intent(start)).accepted
    assert seen == ["explore"], "the bridge knew before the cockpit was asked"
    _dies(transport)
    bus = rover.bridge.bus
    _wait(lambda: "true /stop_explore_cmd#std_msgs.Bool" in bus.events, B.SESSION_MISSES * 2.0)
    await transport.close()


async def test_a_start_whose_answer_is_lost_is_kept_and_stopped(rover: Rover) -> None:
    """The cockpit applied the start and its answer never came: the session keeps its word that
    it started, so the deadman still covers it, and a stop goes out for it at once."""
    transport = rover.transport()
    await transport.connect()
    original = rover.cockpit.command

    def lost(name: str, body: dict[str, Any]) -> Any:
        got = original(name, body)
        if name == "explore" and body.get("action") == "start":
            time.sleep(client.CALL_TIMEOUT_S * 2 + 0.5)  # applied, and the answer lost
        return got

    rover.cockpit.command = lost  # type: ignore[method-assign]
    start = Intent(kind="do", params={"skill": "explore", "action": "start"})
    ack = await transport.send_intent(start)
    assert not ack.accepted and "quackd sent a stop" in (ack.reason or ""), ack
    assert rover.cockpit.posted("stop"), "the stop went out for the start it could not see"
    assert not rover.cockpit.exploring
    await transport.close()


async def test_a_slow_cockpit_never_widens_the_deadmans_window(rover: Rover) -> None:
    """The period a session declares is the configured one, never a measured round trip, and
    the bridge caps it, so a cockpit that takes a second to answer leaves the window as it was."""
    original = rover.cockpit.state

    def slow() -> Any:
        time.sleep(1.0)
        return original()

    transport = rover.transport()
    await transport.connect()
    rover.cockpit.state = slow  # type: ignore[method-assign]
    start = Intent(kind="do", params={"skill": "explore", "action": "start"})
    assert (await transport.send_intent(start)).accepted
    for _ in range(3):
        await transport.heartbeat()
    session = rover.bridge.sessions[transport.session_id]
    assert session.keepalive_s == client.HEARTBEAT_S <= B.MAX_KEEPALIVE_S
    _dies(transport)
    last = session.last
    bus = rover.bridge.bus
    _wait(lambda: "true /stop_explore_cmd#std_msgs.Bool" in bus.events, B.MAX_KEEPALIVE_S * 4)
    assert time.monotonic() - last <= B.SESSION_MISSES * B.MAX_KEEPALIVE_S + 0.5
    await transport.close()


async def test_a_stop_lost_to_a_blip_keeps_its_word_for_the_close(rover: Rover) -> None:
    """Both halves of a stop failed: the session still says it started the exploration, so a
    close once the link is back sends the soft stop for it, and the deadman covered it
    meanwhile."""
    transport = rover.transport()
    await transport.connect()
    start = Intent(kind="do", params={"skill": "explore", "action": "start"})
    assert (await transport.send_intent(start)).accepted
    good_cockpit, good_bridge = transport.cockpit, transport.bridge
    transport.cockpit = client.CockpitClient("127.0.0.1", _closed_port())
    transport.bridge = client.BridgeClient("127.0.0.1", _closed_port(), TOKEN)
    await transport.stop()
    assert transport.stop_error is not None and transport.started == "explore"
    transport.cockpit, transport.bridge = good_cockpit, good_bridge
    stops = len(rover.cockpit.posted("stop"))
    await transport.close()
    assert len(rover.cockpit.posted("stop")) == stops + 1 and not rover.cockpit.exploring


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
        assert "nobody read whether it holds" in transport.close_note, "a done cycle read none"
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


async def test_with_moss_arm_newest_drive_to_reads_the_base_stack_off_the_bridge() -> None:
    """The documented start order runs moss-arm after the base stack, and the cockpit reads only
    the newest run: its nav shows no exploration, and it would send a goal straight under the
    explorer. The bridge reads the base stack's own nav off the bus, so drive_to sees the
    exploration, stops it itself, waits out the explorer's own stay here goal, and its goal then
    holds until the planner reaches it."""
    with _rover_with() as rover:
        rover.cockpit.newest = "moss-arm"
        adapter = MossAdapter(rover.transport())
        await adapter.connect()
        start = Intent(kind="do", params={"skill": "explore", "action": "start"})
        assert (await adapter.send_intent(start)).accepted
        state = await adapter.get_state()
        assert state.extras["nav"]["exploring"] is True, "the bridge's reading of the base"
        assert state.extras["cockpit_nav"]["exploring"] is False, "the cockpit's, of moss-arm"
        params = moss_verbs.DriveToParams(x_m=0.5, y_m=0.0)
        task = asyncio.ensure_future(moss_verbs.drive_to(VerbContext(transport=adapter), params))
        deadline = time.monotonic() + 10.0
        while not rover.cockpit.posted("goal"):
            assert time.monotonic() < deadline and not task.done(), "no goal was sent"
            await asyncio.sleep(0.1)
        await asyncio.sleep(0.6)
        rover.cockpit.reach_goal(True)
        result = await asyncio.wait_for(task, 10.0)
        assert result.ok, result.summary
        actions = [r.body.get("action") for r in rover.cockpit.posted("explore") if r.body]
        assert actions == ["start", "stop"], actions
        stopped, sent = rover.cockpit.posted("explore")[-1].t, rover.cockpit.posted("goal")[0].t
        assert sent - stopped >= float(client.up.GOAL_AFTER_STOP_S.name) - 0.1
        await adapter.close()


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
async def test_on_moss_litter_a_stop_ends_the_missions_own_pick_and_the_arm_never_plays_it(
    rover: Rover,
) -> None:
    """The mission is picking, its script still starting, when quackd stops. The stop ends
    that script before it cancels, so the trajectory never goes out after the stop, and the
    close's word that the mission is stopped is true of its arm too."""
    adapter = MossAdapter(rover.transport())
    await adapter.connect()
    bus = rover.bridge.bus
    bus.start_mission_pick(startup_s=1.5)
    await adapter.stop()
    assert adapter.stop_error is None, adapter.stop_error
    await asyncio.sleep(2.0)
    bus.arm_joints()
    assert not bus.executing, "the mission's trajectory went out after the stop"
    assert bus.pick is not None and not bus.pick.alive
    await adapter.close()
    assert "litter mission is stopped" in (adapter.close_note or "")


class _PersonReading:
    """The CLI's kill switch as a person at the terminal is: `ask` blocks the loop's own thread
    until the line is typed (`quackd.safety.KillSwitch.ask`)."""

    def __init__(self, seconds: float) -> None:
        self.seconds = seconds

    def ask(self, prompt: str) -> str:
        _block_the_loop(self.seconds)
        return "y"


@pytest.mark.parametrize("rover", ["moss-litter"], indirect=True)
async def test_collect_litter_survives_a_person_reading_its_confirm(
    rover: Rover, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The confirm blocks the event loop while the person reads it, longer than the bridge's
    window. The session's keepalives come from the transport's own thread, so the bridge never
    takes the reading person for a dead quackd, and the mission is the verb's to run."""
    monkeypatch.setattr(cli, "_SWITCH", _PersonReading(B.SESSION_MISSES * client.HEARTBEAT_S * 2))
    monkeypatch.setattr(cli, "_can_prompt", lambda: True)
    duck = parse_duck_text(
        "---\nduck: 0\nname: t\ndescription: d\nverbs:\n  allow: [observe, report_state, "
        "stop, collect_litter]\n  confirm: []\nsuccess: [x]\nabort_when: []\n---\n# Task\nx\n"
    ).frontmatter
    adapter = MossAdapter(rover.transport())
    manifest = await adapter.connect()
    ex = Executor(
        registry_from_manifest(manifest, adapter),
        adapter,
        contract=duck,
        detector=ColorBlobDetector(),
        confirm=cli._confirm_prompt,
        manifest=manifest,
    )
    beat = Heartbeat(adapter, asyncio.Event(), period_s=client.HEARTBEAT_S)
    beat.start()
    try:
        result = await ex.run_verb("collect_litter", {"minutes": 0.05})
    finally:
        await beat.stop()
    assert rover.bridge.halts == 0, "the bridge never halted the session of the person reading"
    assert result.ok, result.summary
    await adapter.close()


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
    assert rover.bridge.halts == 0
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
