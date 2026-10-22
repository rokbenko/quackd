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
import os
import socket
import sys
import threading
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
GUARD_S = 1.0
"""The bridge's watch on the arm after a stop, short here so a test can wait it out."""
CLOCK_TICK_S = 0.02
"""How far short of what it was asked a wait can measure on `time.monotonic`. Windows' timers
tick about every 15.6 ms, and the bridge's wait of `RPC_TIMEOUT_S` once measured
1.9999999999998863 s against 2.0 on CI's Windows runner, a wait that had not ended early at all."""


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
        bridge = B.fake_bridge(
            blueprint=blueprint, token=TOKEN, cycle_s=0.3, fps=10.0, arm_guard_s=GUARD_S
        )
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
        bridge_kw.setdefault("arm_guard_s", GUARD_S)
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
    assert mock_module.ARM_GUARD_S == B.ARM_GUARD_S == B.ARM_PLAY_STARTUP_S + B.RPC_TIMEOUT_S


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


def test_the_stop_verbs_own_timeout_covers_the_transports_wait() -> None:
    """The core stop verb gives up at 5 s, and the transport may wait for the bridge far longer:
    a stop cut short there is followed by the executor's own, a second one nothing bounds. The
    manifest gives the stop verb a timeout that covers the whole wait."""
    spec = next(v for v in describe("cockpit").verbs if v.name == "stop")
    assert spec.timeout_s is not None
    assert client.STOP_TIMEOUT_S + 1.0 < spec.timeout_s <= 600
    assert spec.timeout_s == moss_verbs.STOP_VERB_TIMEOUT_S


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


class _BusyArm(B.FakeBus):
    """A coordinator that answers every RPC, each a second and a half late: a busy serial bus."""

    def arm_joints(self) -> Any:
        time.sleep(1.5)
        return super().arm_joints()

    def arm_cancel(self) -> Any:
        time.sleep(1.5)
        return super().arm_cancel()


async def test_the_stop_verb_waits_out_a_slow_stop_and_never_sends_a_second() -> None:
    """Every RPC to the arm takes a second and a half, and a trajectory plays, so the bridge's
    stop cancels it and reads the arm and takes longer than the core stop verb's own 5 s. The
    stop verb is waited out, reports ok, and the bridge saw one stop and no second from the
    executor."""
    bus = _BusyArm(B.FAKE_UPSTREAM, B.FrameStore(), fps=10.0)
    stack = B.StackInfo(
        running=True,
        blueprint="moss-explore",
        pid=os.getpid(),
        run_id="fake-run",
        env={"DIMOS_TRANSPORT": "lcm"},
        arm_run=B.Run(pid=os.getpid(), blueprint="moss-arm", run_id="fake-arm", started=0.0),
        arm_env=dict(os.environ),
    )
    bridge = B.Bridge(
        stack=lambda: stack,
        up=B.FAKE_UPSTREAM,
        bus=bus,
        token=TOKEN,
        fake=True,
        arm_argv=lambda pose, phase: [sys.executable, "-c", "pass"],
    )
    with FakeCockpit(blueprint="moss-explore") as cockpit:
        cockpit.bus, cockpit.run_id = bus, "fake-run"
        server = B.serve(bridge, "127.0.0.1", 0)
        bridge.start()
        try:
            _wait(lambda: bus.frames.heard("nose")["heard"])
            rover = Rover(cockpit, bridge, int(server.server_address[1]))
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
            bus.executing = True  # a trajectory playing, which the stop cancels and reads after
            began = time.monotonic()
            result = await ex.run_verb("stop", {})
            took = time.monotonic() - began
            assert result.ok, result.summary
            assert took > 5.0, "the stop outlasted the core verb's own timeout"
            assert bridge._stops == 1, "and no second stop followed it"
            await adapter.close()
        finally:
            server.shutdown()
            server.server_close()
            bridge.close()


@pytest.mark.parametrize("rover", ["moss-litter"], indirect=True)
async def test_on_moss_litter_close_after_a_clean_stop_sends_nothing(rover: Rover) -> None:
    """A run on moss-litter ends with its stop and then its close: the close has nothing left to
    stop, so it sends nothing, rather than a second stop as long as the first."""
    transport = rover.transport()
    await transport.connect()
    start = Intent(kind="do", params={"skill": "collect_litter", "action": "start"})
    assert (await transport.send_intent(start)).accepted
    await transport.stop()
    assert transport.stop_error is None, transport.stop_error
    stops, sent = rover.bridge._stops, rover.bridge.bus.mission_stops
    posted = len(rover.cockpit.posted("stop"))
    await transport.close()
    assert rover.bridge._stops == stops and rover.bridge.bus.mission_stops == sent
    assert len(rover.cockpit.posted("stop")) == posted
    assert "litter mission is stopped" in (transport.close_note or ""), transport.close_note


async def test_close_after_a_stop_still_halts_what_was_started_since(rover: Rover) -> None:
    """A clean stop leaves nothing for the close only until something starts again: an
    exploration started after it is ended by the close."""
    transport = rover.transport()
    await transport.connect()
    start = Intent(kind="do", params={"skill": "explore", "action": "start"})
    assert (await transport.send_intent(start)).accepted
    await transport.stop()
    assert transport.stop_error is None and not rover.cockpit.exploring
    assert (await transport.send_intent(start)).accepted and rover.cockpit.exploring
    await transport.close()
    assert not rover.cockpit.exploring, "the close ended what was started after the stop"


async def test_a_long_move_and_then_pick_and_drop_both_run() -> None:
    """A move that reads no state for 3 s ends with a stop of its own, whose cancel finds the
    arm executing nothing: that is the whole arm step, it starts no watch, and the
    pick_and_drop after it runs, with the bridge's own window. The stop verb's stop does the
    same."""
    with _rover_with(cycle_s=0.3, arm_guard_s=B.ARM_GUARD_S) as rover:
        transport = rover.transport()
        adapter = MossAdapter(transport)
        manifest = await adapter.connect()
        beat = _alive(transport)  # the session kept alive while the verbs run, as a run's is
        ex = Executor(
            registry_from_manifest(manifest, adapter),
            adapter,
            contract=None,
            detector=ColorBlobDetector(),
            confirm=allow_all,
            manifest=manifest,
        )
        ex.require_verdict = False
        bus = rover.bridge.bus
        moved = await ex.run_verb("move", {"vx": 0.1, "duration_s": 3.0})
        assert moved.ok, moved.summary
        assert "arm cancel" in bus.events, "its stop sent the cancel"
        assert rover.bridge.arm_doc()["guard"] is None, "and started no watch"
        picked = await ex.run_verb("pick_and_drop", {"pick_pose": "pick"})
        assert picked.ok, picked.summary
        stopped = await ex.run_verb("stop", {})
        assert stopped.ok and rover.bridge.arm_doc()["guard"] is None, stopped.summary
        await beat.stop()
        await adapter.close()


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
    assert took >= B.RPC_TIMEOUT_S - CLOCK_TICK_S, "the bridge's stop waited on the arm"
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
    with contextlib.suppress(HeartbeatError):  # its keepalive tells it, then it fails
        await transport.heartbeat()
    assert (transport._session_doc.get("stack") or {}).get("run_id") == "restarted-run"
    bus = rover.bridge.bus
    published, events = len(bus.published), len(bus.events)
    await transport.stop()
    assert rover.cockpit.posted("stop") == [], "no soft stop to the new run"
    assert len(bus.published) == published and len(bus.events) == events, "no zero either"
    said = transport.stop_error or ""
    assert "restarted-run" in said and "touches nothing of it" in said, said
    await transport.close()
    assert "restarted as run restarted-run" in (transport.close_note or "")


async def test_a_second_run_beside_the_connected_one_neither_fails_nor_skips_the_soft_stop(
    rover: Rover,
) -> None:
    """A dimOS run of another blueprint started by hand is the newest, and the run quackd
    connected to still runs behind it: the bridge says so in every keepalive answer, so the
    heartbeat goes on, and the stop's soft stop and the bridge's stop both reach the run it
    started its exploration on."""
    from dataclasses import replace

    transport = rover.transport()
    await transport.connect()
    start = Intent(kind="do", params={"skill": "explore", "action": "start"})
    assert (await transport.send_intent(start)).accepted
    old = rover.bridge.stack()
    runs = (
        B.Run(pid=os.getpid(), blueprint="moss-explore", run_id="fake-run", started=0.0),
        B.Run(pid=os.getpid(), blueprint="unitree-go2-sim", run_id="other-run", started=1.0),
    )
    newer = replace(old, blueprint="unitree-go2-sim", run_id="other-run", runs=runs)
    rover.bridge._stack = lambda: newer
    await transport.heartbeat()  # its run still runs, so this does not fail
    await transport.stop()
    assert transport.stop_error is None, transport.stop_error
    assert rover.cockpit.posted("stop"), "the soft stop went out"
    bus = rover.bridge.bus
    assert any(
        c == "/tele_cmd_vel#geometry_msgs.Twist" and v == (0.0, 0.0) for _, c, v in bus.published
    )
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


async def test_close_ends_though_its_cancel_of_the_frame_loop_is_lost(rover: Rover) -> None:
    """The bridge has gone, so a frame fetch and close's own last call to it fail in one turn
    of the loop, as two connects to one closed port do. Python 3.11's wait_for then loses the
    cancel close sends the frame loop, which used to leave close waiting on that loop for
    good. The loop ends on its own flag as well, so close ends."""
    transport = rover.transport()
    await transport.connect()
    frames = transport._frames_task
    assert frames is not None, "the bridge hears the nose camera, so frames are fetched"
    fetching, ending = threading.Event(), threading.Event()
    frame_fails, end_fails = threading.Event(), threading.Event()
    fetches = {"n": 0}

    def frame(cam: str) -> tuple[bytes, bool, str | None]:
        fetches["n"] += 1
        fetching.set()
        frame_fails.wait(10.0)
        raise client.LinkDown("the bridge has gone")

    def keepalive(*args: Any, **kw: Any) -> dict[str, Any]:
        ending.set()
        end_fails.wait(10.0)
        raise client.LinkDown("the bridge has gone")

    transport.bridge.frame = frame  # type: ignore[method-assign]
    transport.bridge.keepalive = keepalive  # type: ignore[method-assign]
    closing: asyncio.Task[None] | None = None
    try:
        assert await asyncio.to_thread(fetching.wait, 5.0), "a fetch is out"
        closing = asyncio.create_task(transport.close())
        assert await asyncio.to_thread(ending.wait, 5.0), "close's last call is out"
        # both fail while the loop runs nothing, close's call first, so both land in one turn
        end_fails.set()
        _block_the_loop(0.2)
        frame_fails.set()
        _block_the_loop(0.2)
        done, _ = await asyncio.wait({closing}, timeout=10.0)
        assert closing in done, "close waited on the frame loop for good"
        assert frames.done(), "the frame loop has ended"
        fetched = fetches["n"]
        await asyncio.sleep(3 * client.CAMERA_PERIOD_S)
        assert fetches["n"] == fetched, "and fetches nothing after close"
    finally:
        frame_fails.set()
        end_fails.set()
        for _ in range(100):
            if frames.done():
                break
            frames.cancel()
            await asyncio.wait({frames}, timeout=0.05)
        if closing is not None and not closing.done():
            closing.cancel()
            await asyncio.wait({closing}, timeout=1.0)


async def test_the_frame_loop_ends_at_a_cancel_its_fetch_lost(rover: Rover) -> None:
    """A loop that shuts down cancels every task, the frame loop of a transport nobody closed
    among them. Python 3.11's wait_for loses a cancel that arrives in the turn after its call
    returned, and the frame loop used to fetch on, so the shutdown waited on it for good. The
    cancel is still asked for, and the loop honours it at its next turn."""
    transport = rover.transport()
    await transport.connect()
    frames = transport._frames_task
    assert frames is not None, "the bridge hears the nose camera, so frames are fetched"
    loop = asyncio.get_running_loop()
    real = transport.bridge.frame
    fetched, go = threading.Event(), threading.Event()

    def frame(cam: str) -> tuple[bytes, bool, str | None]:
        got = real(cam)
        if not fetched.is_set():
            fetched.set()
            go.wait(10.0)
            # the cancel lands in the turn after this fetch's own answer, before wait_for has
            # resumed the loop: the turn it loses a cancel in
            loop.call_soon_threadsafe(lambda: loop.call_soon(frames.cancel))
        return got

    transport.bridge.frame = frame  # type: ignore[method-assign]
    try:
        assert await asyncio.to_thread(fetched.wait, 5.0), "a fetch is out"
        go.set()
        _block_the_loop(0.3)  # the cancel and the fetch's answer queue up in that order
        done, _ = await asyncio.wait({frames}, timeout=10.0)
        assert frames in done and frames.cancelled(), "the frame loop fetched on past its cancel"
    finally:
        transport._frames_on = False
        await asyncio.wait({frames}, timeout=5.0)
        await transport.close()


def _closed_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


# ── the session ─────────────────────────────────────────────────────────────────────────


def _alive(transport: Any) -> Heartbeat:
    """quackd's loop, beating: its own heartbeat, which sends the session's keepalives."""
    beat = Heartbeat(transport, asyncio.Event(), period_s=client.HEARTBEAT_S)
    beat.start()
    return beat


async def _dies(beat: Heartbeat) -> None:
    """quackd killed where it stands: its loop's heartbeat ends, and nothing else is sent."""
    await beat.stop()


WINDOW_S = B.SESSION_MISSES * B.MAX_KEEPALIVE_S
"""How long a session lives without a keepalive: each declares the bridge's own cap."""


async def test_the_loops_heartbeat_feeds_the_bridge_and_its_death_halts_what_quackd_started(
    rover: Rover,
) -> None:
    transport = rover.transport()
    await transport.connect()
    beat = _alive(transport)
    await transport.send_intent(Intent(kind="do", params={"skill": "explore", "action": "start"}))
    assert rover.cockpit.exploring and transport.started == "explore"
    await asyncio.sleep(WINDOW_S + 0.5)
    session = rover.bridge.sessions[transport.session_id]
    assert session.started == "explore" and session.keepalive_s == B.MAX_KEEPALIVE_S
    assert rover.bridge.halts == 0, "kept alive past the window by the loop's own beats"
    await _dies(beat)
    bus = rover.bridge.bus
    _wait(lambda: "true /stop_explore_cmd#std_msgs.Bool" in bus.events, WINDOW_S + 2.0)
    assert "true /stop_movement#std_msgs.Bool" in bus.events
    assert rover.bridge.halts == 1
    await transport.close()


async def test_a_wedged_loop_has_what_its_session_started_halted_and_the_run_stopped(
    rover: Rover,
) -> None:
    """quackd alive and its loop wedged, in a verb that never yields or a call that never
    returns: no heartbeat runs, so no keepalive goes, and the bridge halts the exploration it
    started three periods later. The heartbeat that runs once the loop is free hears of the
    halt, and fails the run with it rather than steer on as if nothing had happened."""
    transport = rover.transport()
    await transport.connect()
    beat = _alive(transport)
    start = Intent(kind="do", params={"skill": "explore", "action": "start"})
    assert (await transport.send_intent(start)).accepted
    _block_the_loop(WINDOW_S + 1.0)
    bus = rover.bridge.bus
    assert "true /stop_explore_cmd#std_msgs.Bool" in bus.events, "halted while it was wedged"
    assert rover.bridge.halts == 1
    await asyncio.wait_for(beat.abort.wait(), 5.0)  # the heartbeat failed, and aborted
    assert "its exploration" in str(beat.failure), beat.failure
    await beat.stop()
    await transport.close()


@pytest.mark.parametrize("rover", ["moss-litter"], indirect=True)
async def test_on_moss_litter_a_wedged_loop_has_the_mission_it_started_stopped(
    rover: Rover,
) -> None:
    transport = rover.transport()
    await transport.connect()
    beat = _alive(transport)
    start = Intent(kind="do", params={"skill": "collect_litter", "action": "start"})
    assert (await transport.send_intent(start)).accepted and transport.started == "litter"
    _block_the_loop(WINDOW_S + 1.0)
    _wait(lambda: rover.bridge.bus.mission_stops >= 1, 5.0)
    await asyncio.wait_for(beat.abort.wait(), 10.0)
    assert "the litter mission it started" in str(beat.failure), beat.failure
    await beat.stop()
    await transport.close()


@pytest.mark.parametrize("rover", ["moss-litter"], indirect=True)
async def test_one_keepalive_late_by_more_than_a_second_never_halts_a_live_session(
    rover: Rover,
) -> None:
    """Each keepalive declares the bridge's own cap, so the window is three seconds while the
    keepalives still go every heartbeat: one round trip slow by over a second, a wifi hiccup,
    leaves a live litter session and its mission alone."""
    transport = rover.transport()
    await transport.connect()
    beat = _alive(transport)
    start = Intent(kind="do", params={"skill": "collect_litter", "action": "start"})
    assert (await transport.send_intent(start)).accepted
    real = transport.bridge.keepalive
    calls = {"n": 0}

    def hiccup(*args: Any, **kwargs: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 3:
            time.sleep(2 * B.MAX_KEEPALIVE_S - 0.8)  # one late round trip
        return real(*args, **kwargs)

    transport.bridge.keepalive = hiccup  # type: ignore[method-assign]
    await asyncio.sleep(WINDOW_S + 1.0)
    assert calls["n"] > 3 and rover.bridge.halts == 0 and rover.bridge.bus.mission_stops == 0
    assert beat.failure is None, beat.failure
    await beat.stop()
    await transport.close()


async def test_a_cockpit_slow_to_answer_never_delays_the_keepalive(rover: Rover) -> None:
    """The heartbeat waits on the cockpit's state, and while it waits the session's keepalive
    goes on every HEARTBEAT_S. A state that comes back just inside the cockpit's own timeout,
    slow enough that a keepalive waiting on it would miss the bridge's window, halts nothing
    of a live session."""
    transport = rover.transport()
    await transport.connect()
    start = Intent(kind="do", params={"skill": "explore", "action": "start"})
    assert (await transport.send_intent(start)).accepted
    original = rover.cockpit.state
    slow_s = WINDOW_S - client.HEARTBEAT_S + 0.1
    assert slow_s < client.CALL_TIMEOUT_S, "a cockpit slow, and not gone"

    def slow_state() -> dict[str, Any]:
        time.sleep(slow_s)
        return original()

    rover.cockpit.state = slow_state  # type: ignore[method-assign]
    beat = _alive(transport)
    await asyncio.sleep(3 * slow_s + 1.0)
    await beat.stop()
    assert rover.bridge.expired == 0 and rover.bridge.halts == 0, rover.bridge.last_halt
    assert beat.failure is None, beat.failure
    rover.cockpit.state = original  # type: ignore[method-assign]
    await transport.close()


async def test_a_heartbeat_held_past_the_window_asks_the_bridge_before_it_fails(
    rover: Rover,
) -> None:
    """A prompt that lands while a heartbeat waits on the cockpit holds the rest of that
    heartbeat past the window. The session expired meanwhile, having started nothing, so the
    heartbeat asks the bridge once more before it says the session is gone, and the halt of
    nothing is no failure."""
    transport = rover.transport()
    await transport.connect()
    original = rover.cockpit.state
    asked = threading.Event()

    def asked_for() -> Any:
        asked.set()
        time.sleep(0.3)
        return original()

    rover.cockpit.state = asked_for  # type: ignore[method-assign]
    beat = asyncio.create_task(transport.heartbeat())
    assert await asyncio.to_thread(asked.wait, 5.0), "the heartbeat asked the cockpit"
    _block_the_loop(WINDOW_S + 1.0)  # the prompt, while the heartbeat waits on the cockpit
    await beat
    assert rover.bridge.expired == 1 and rover.bridge.halts == 0
    assert transport.session_id in rover.bridge.sessions, "the session lives again"
    await transport.close()


async def test_a_halt_that_halted_nothing_is_no_event_and_is_said_in_the_keepalives(
    rover: Rover,
) -> None:
    """A prompt on the terminal blocks the loop too: a session that started nothing while it
    waited expires, halts nothing, and is no event once the loop runs again. Every keepalive
    answer after says so."""
    transport = rover.transport()
    await transport.connect()
    beat = _alive(transport)
    _block_the_loop(WINDOW_S + 1.0)
    await asyncio.sleep(1.0)
    assert beat.failure is None, beat.failure
    assert rover.bridge.expired == 1 and rover.bridge.halts == 0
    said = (transport._session_doc.get("session") or {}).get("halted") or {}
    assert said.get("halted") == [] and "missed" in str(said.get("why")), said
    await beat.stop()
    await transport.close()


async def test_close_ends_the_session_so_nothing_is_halted_after_quackd_has_gone(
    rover: Rover,
) -> None:
    transport = rover.transport()
    await transport.connect()
    await transport.heartbeat()
    await transport.close()
    before = list(rover.bridge.bus.events)
    await asyncio.sleep(WINDOW_S + 0.5)
    assert rover.bridge.halts == 0 and rover.bridge.sessions == {}
    assert [e for e in rover.bridge.bus.events if e not in before] == []


async def test_a_doctor_beside_a_run_never_strips_the_runs_deadman(rover: Rover) -> None:
    """Each transport draws its own session id, so a probe's keepalive and its end are its own,
    and the run's exploration is still halted when the run dies."""
    run = rover.transport()
    await run.connect()
    beat = _alive(run)
    start = Intent(kind="do", params={"skill": "explore", "action": "start"})
    assert (await run.send_intent(start)).accepted
    doctor = MossAdapter(rover.transport())
    await doctor.connect()
    assert (await doctor.health()).ok
    await doctor.close()
    assert rover.bridge.sessions[run.session_id].started == "explore"
    await _dies(beat)
    bus = rover.bridge.bus
    _wait(lambda: "true /stop_explore_cmd#std_msgs.Bool" in bus.events, WINDOW_S + 2.0)
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
    # no heartbeat beats here, so nothing goes after the start, as if quackd died with it
    bus = rover.bridge.bus
    _wait(lambda: "true /stop_explore_cmd#std_msgs.Bool" in bus.events, WINDOW_S + 2.0)
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
    """The period a session declares is the bridge's own cap, never a measured round trip, so
    a cockpit that takes a second to answer leaves the window as it was."""
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
    assert session.keepalive_s == B.MAX_KEEPALIVE_S
    last = session.last  # and no heartbeat beats after the last one asked here
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
        beat = _alive(transport)  # the session kept alive while the cycle runs, as a run's is
        cycle = Intent(kind="do", params={"skill": "arm_cycle", "pick_pose": "pick"})
        assert (await transport.send_intent(cycle)).accepted
        await asyncio.to_thread(
            _wait, lambda: (rover.bridge.jobs.doc() or {}).get("state") == "done"
        )
        await beat.stop()
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
        beat = _alive(adapter)
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
        await beat.stop()
        await adapter.close()


async def test_close_cancels_a_cycle_of_its_own_that_no_stop_ended() -> None:
    """By the id the bridge gave it, through `/arm/cancel`, and the arm holds."""
    with _rover_with(cycle_s=30.0) as rover:
        transport = rover.transport()
        await transport.connect()
        beat = _alive(transport)  # the session kept alive while the cycle starts, as a run's is
        cycle = Intent(kind="do", params={"skill": "arm_cycle", "pick_pose": "pick"})
        assert (await transport.send_intent(cycle)).accepted
        await asyncio.to_thread(_wait, lambda: rover.bridge.bus.executing)
        await beat.stop()
        await transport.close()
        assert "arm cancel" in rover.bridge.bus.events and not rover.bridge.bus.executing
        _wait(lambda: (rover.bridge.jobs.doc() or {}).get("state") == "cancelled")
        _wait(lambda: rover.bridge.jobs.doc()["held"] is True)  # once the watch has passed
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
    transport = rover.transport()
    adapter = MossAdapter(transport)
    manifest = await adapter.connect()
    beat = _alive(transport)  # the session kept alive while the verb runs, as a run's is
    ex = Executor(
        registry_from_manifest(manifest, adapter),
        adapter,
        contract=None,
        detector=ColorBlobDetector(),
        confirm=allow_all,
        manifest=manifest,
    )
    result = await ex.run_verb("pick_and_drop", {"pick_pose": "pick_low"})
    await beat.stop()
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
    """The confirm blocks the event loop while the person reads it, as long as the bridge's
    window. No keepalive goes meanwhile, and nothing has been started while the question is on
    the screen, so a halt then halts nothing and is no event: the mission is the verb's to run
    once the person answers."""
    monkeypatch.setattr(cli, "_SWITCH", _PersonReading(WINDOW_S))
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
    assert rover.bridge.halts == 0, "nothing of the session's was halted while the person read"
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
    await asyncio.sleep(WINDOW_S + 0.5)
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
