"""quackd's MOSS bridge on its own: what it does on the rover, driven with no rover.

The bridge is loaded by path, the way the rover runs it, and driven through its pure parts with
a clock of the test's own wherever timing is the claim: the twist deadman and its silence, the
maker's pulses, a frame taken after a request. The arm cycle runs a real subprocess, the fake
script `--fake` uses in place of the maker's, with the same lines and exit codes. What none of
this can prove is anything about MOSS: nothing here has run on a rover.
"""

from __future__ import annotations

import ast
import contextlib
import http.client
import importlib.util
import itertools
import json
import os
import socket
import subprocess
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from quackd_moss import upstream_api as up
from tests.conftest import REPO

BRIDGE = REPO / "bridge" / "moss" / "quackd_moss_bridge.py"


def load_bridge() -> ModuleType:
    """It lives outside the package on purpose: quackd's core must never import it."""
    spec = importlib.util.spec_from_file_location("quackd_moss_bridge", BRIDGE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


B = load_bridge()


class Clock:
    def __init__(self, t: float = 0.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


class Recorder:
    """A bus that keeps what was published, with the clock it was published at."""

    def __init__(self, clock: Clock) -> None:
        self.clock = clock
        self.twists: list[tuple[float, str, float, float]] = []
        self.trues: list[tuple[float, str]] = []

    def publish_twist(self, chan: str, vx: float, wz: float) -> None:
        self.twists.append((self.clock(), chan, vx, wz))

    def publish_true(self, chan: str) -> None:
        self.trues.append((self.clock(), chan))


def _up(**kw: Any) -> Any:
    return replace(B.FAKE_UPSTREAM, **kw)


def _all_on(clock: Clock, bridge: Any) -> None:
    """The bridge, its job runner and the fake bus behind them on the test's clock, which
    every sleep of theirs moves, a silent RPC's wait among them."""

    def advance(s: float) -> None:
        clock.t += s

    bridge.clock, bridge.sleep = clock, advance
    bridge.bus.clock, bridge.bus.sleep = clock, advance
    if bridge.jobs is not None:
        bridge.jobs.clock, bridge.jobs.sleep = clock, advance


def _at_its_timeout(clock: Clock, fn: Any, seconds: float) -> Any:
    """`fn`, answering only as its timeout runs out, the slowest an RPC can answer."""

    def slow(*args: Any) -> Any:
        clock.t += seconds
        return fn(*args)

    return slow


def _wait(predicate: Any, limit_s: float = 10.0) -> None:
    deadline = time.monotonic() + limit_s
    while not predicate():
        assert time.monotonic() < deadline, "timed out waiting"
        time.sleep(0.02)


# ── the rules this file lives by ────────────────────────────────────────────────────────


def test_the_bridge_never_imports_quackd() -> None:
    """quackd's dependencies do not belong on a rover, and this file is the proof."""
    for line in BRIDGE.read_text(encoding="utf-8").splitlines():
        stripped = line.split("#", 1)[0].strip()
        if stripped.startswith(("import ", "from ")):
            assert "quackd" not in stripped, f"the bridge imports quackd: {stripped}"


def test_upstream_is_imported_only_inside_functions() -> None:
    """lcm, dimos_lcm, dimos and moss_dimos at import time would mean a rover missing one could
    not even say so in /hello."""
    tree = ast.parse(BRIDGE.read_text(encoding="utf-8"))
    stdlib = set(sys.stdlib_module_names) | {"__future__"}
    for node in tree.body:
        if isinstance(node, ast.Import):
            names = [a.name.split(".")[0] for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [(node.module or "").split(".")[0]]
        else:
            continue
        assert all(n in stdlib for n in names), f"a top level import that is not stdlib: {names}"


def test_the_bridge_runs_with_upstream_absent() -> None:
    script = (
        "import sys\n"
        "for name in ('lcm', 'dimos_lcm', 'dimos', 'moss_dimos', 'quackd', 'numpy', 'PIL'):\n"
        "    sys.modules[name] = None\n"
        "import importlib.util\n"
        f"spec = importlib.util.spec_from_file_location('b', r'{BRIDGE}')\n"
        "m = importlib.util.module_from_spec(spec); sys.modules['b'] = m\n"
        "spec.loader.exec_module(m)\n"
        "raise SystemExit(m.main(['--fake', '--once']))\n"
    )
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    hello = json.loads(result.stdout)
    assert (hello["protocol"], hello["protocol_version"]) == (B.PROTOCOL, B.PROTOCOL_VERSION)
    assert hello["fake"] is True


def test_it_ships_in_the_sdist_and_never_in_the_wheel() -> None:
    import tomllib

    build = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["hatch"][
        "build"
    ]["targets"]
    assert build["wheel"]["packages"] == ["quackd"]
    assert "bridge" in build["sdist"]["include"]


def test_a_rover_without_moss_dimos_says_so_rather_than_failing_to_start(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setitem(sys.modules, "moss_dimos", None)
    with pytest.raises(B.UpstreamMissing, match="moss_dimos is not importable"):
        B.read_upstream({})
    bridge = B.live_bridge(moss=str(tmp_path), token=None, runs=str(tmp_path))
    hello = bridge.hello()
    assert hello["publisher"]["loaded"] is False
    assert "moss_dimos is not importable" in hello["publisher"]["error"]
    assert hello["stack"]["running"] is False
    bridge.close()


def test_the_pins_the_bridge_spells_are_the_ones_read_upstream() -> None:
    """The bridge's names sit outside upstream_api's rule, so they are pinned here instead."""
    src = BRIDGE.read_text(encoding="utf-8")
    assert B.MISSION_RPC == "LitterMission"
    upstream = (up.ARM_PLAY.name, up.MISSION_PICK_CMD.name)
    assert upstream == (B.PICK_SCRIPT, B.PICK_CMD_ENV)
    assert B.CAMERA_STREAMS == {"nose": "color_image", "wrist": "wrist_image"}
    assert B.channel(B.PAD_STREAM_EXPLORE, B.TWIST_MSG) == "/tele_cmd_vel#geometry_msgs.Twist"
    assert B.channel(B.PAD_STREAM_NAV, B.TWIST_MSG) == "/cmd_vel#geometry_msgs.Twist"
    assert B.channel(B.STOP_EXPLORE_STREAM, B.BOOL_MSG) == "/stop_explore_cmd#std_msgs.Bool"
    assert B.channel(B.STOP_MOVEMENT_STREAM, B.BOOL_MSG) == "/stop_movement#std_msgs.Bool"
    assert '"tools" / "moss_arm_play.py"' in src
    assert '"--pick-pose", pick_pose' in src and '["--from", from_phase]' in src
    assert "None, ControlCoordinator, remote_name=up.arm_coordinator, rpc=self._rpc" in src
    assert "mission.SERVO_W_MAX" in src and "mission.PULSE_S" in src
    assert "moss_adapter.FIRMWARE_COMMAND_TIMEOUT_S" in src
    assert "arm_feetech.DEFAULT_STILL_TICKS_PER_S" in src
    assert "robot_profile.current(env)" in src
    assert "arm.calibration_path(None, env if arm_env is None else arm_env)" in src
    assert "MOSS_ARM_CALIB" not in src, "the bridge passes the arm run's environment, whole"
    assert up.ARM_CALIBRATION_ENV.name == "MOSS_ARM_CALIB"
    assert "hmac.compare_digest" in src


# ── the token and the bind ──────────────────────────────────────────────────────────────


def test_a_named_token_file_that_is_missing_or_empty_refuses_to_start(tmp_path: Path) -> None:
    with pytest.raises(B.TokenFileError, match="cannot read"):
        B.read_token(str(tmp_path / "absent.token"))
    empty = tmp_path / "empty.token"
    empty.write_text("  \n", encoding="utf-8")
    with pytest.raises(B.TokenFileError, match="is empty"):
        B.read_token(str(empty))
    good = tmp_path / "good.token"
    good.write_text("s3cret\n", encoding="utf-8")
    assert B.read_token(str(good)) == "s3cret"
    assert B.main(["--fake", "--once", "--token-file", str(empty)]) == 2


@pytest.mark.parametrize(
    ("bind", "token", "warned"),
    [
        ("127.0.0.1", None, False),
        ("::1", None, False),
        ("0.0.0.0", None, True),
        ("0.0.0.0", "s3cret", False),
    ],
)
def test_a_wide_bind_with_no_token_is_said(bind: str, token: str | None, warned: bool) -> None:
    said = B.wide_bind_warning(bind, token)
    assert (said is not None) is warned
    if said:
        assert "drive the rover" in said


def test_the_token_is_compared_and_an_empty_one_is_no_token() -> None:
    bridge = B.fake_bridge(token="s3cret", fps=0)
    assert (
        bridge.authorised("s3cret")
        and not bridge.authorised("s3cre")
        and not bridge.authorised(None)
    )
    assert B.fake_bridge(token="", fps=0).authorised(None)


# ── hello ───────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("blueprint", "chan"),
    [
        ("moss-explore", "/tele_cmd_vel#geometry_msgs.Twist"),
        ("moss-nav", "/cmd_vel#geometry_msgs.Twist"),
    ],
)
def test_hello_says_the_stack_and_the_pad_channel_it_drives(blueprint: str, chan: str) -> None:
    bridge = B.fake_bridge(blueprint=blueprint, fps=0)
    hello = bridge.hello()
    assert hello["stack"]["blueprint"] == blueprint
    assert hello["publisher"] == {"loaded": True, "error": None, "channel": chan}
    assert hello["arm"]["answering"] is True and hello["mission"] is None
    assert hello["shaping"]["max_wz"] == B.FAKE_UPSTREAM.max_wz
    assert hello["stop_ramp_s"] == pytest.approx(B.FAKE_UPSTREAM.ramp_ms[1] * 100 / 1000)
    bridge.close()


def test_hello_on_moss_litter_reports_the_mission_and_an_arm_that_does_not_answer() -> None:
    bridge = B.fake_bridge(blueprint="moss-litter", arm=False, fps=0)
    hello = bridge.hello()
    assert hello["mission"]["answering"] is True and hello["mission"]["status"]["state"]
    assert hello["arm"]["answering"] is False and "no moss-arm run" in hello["arm"]["error"]
    bridge.close()


def test_with_no_moss_arm_run_nothing_waits_on_an_rpc_that_cannot_answer() -> None:
    """A coordinator that is not running makes every RPC wait out its timeout, and the arm is
    read before every verb and cancelled in every stop, so neither is asked at all."""
    bridge = B.fake_bridge(arm=False, fps=0)
    started = time.monotonic()
    doc = bridge.stop()
    assert doc["ok"] and doc["arm"] is None
    assert any("nothing to cancel" in d for d in doc["did"])
    assert bridge.arm_doc()["answering"] is False
    assert time.monotonic() - started < 0.5
    assert bridge.bus.cancels == 0 and bridge.bus.arm_asks == 0
    bridge.close()


def test_an_arm_that_answers_with_no_cycle_to_play_still_reports_its_pose() -> None:
    """A wrong --moss leaves no cycle to play, and the arm's drive pose must still be read, so
    the base is never driven with the arm out because the checkout path was wrong."""
    bridge = B.fake_bridge(fps=0)
    bridge.jobs, bridge.arm_error = None, "no MOSS checkout: pass --moss PATH"
    hello = bridge.hello()
    assert hello["arm"]["answering"] is True and hello["arm"]["cycle"] is False
    bridge.bus.joints["arm/a"] = 1.0
    assert bridge.arm_doc()["folded"] is False
    assert bridge.arm_cycle({})[0] == 503
    bridge.close()


def test_the_base_stack_and_the_arm_run_are_read_apart(tmp_path: Path) -> None:
    """The cockpit shows only the newest run, and a moss-arm started after the base hides it."""
    me = os.getpid()
    entries = [
        ("base", "moss-dimos.moss-explore", "2026-10-20T10:00:00", me),
        ("arm", "moss-dimos.moss-arm", "2026-10-20T10:05:00", me),
        ("dead", "moss-dimos.moss-nav", "2026-10-20T11:00:00", 2**22 + 12345),
    ]
    for run_id, blueprint, started, pid in entries:
        doc = {"run_id": run_id, "blueprint": blueprint, "started_at": started, "pid": pid}
        (tmp_path / f"{run_id}.json").write_text(json.dumps(doc), encoding="utf-8")
    (tmp_path / "torn.json").write_text("{not json", encoding="utf-8")
    stack = B.read_stack(str(tmp_path))
    assert (stack.running, stack.blueprint, stack.run_id) == (True, "moss-explore", "base")
    assert stack.arm_run is not None and stack.arm_run.run_id == "arm"
    assert B.read_stack(str(tmp_path / "nothing")).running is False


@pytest.mark.parametrize(("env", "bus"), [({}, "lcm"), ({"TRANSPORT": "zenoh"}, "zenoh")])
def test_the_stacks_bus_is_read_in_dimos_own_order(env: dict[str, str], bus: str) -> None:
    both = {"DIMOS_TRANSPORT": "lcm", **env}
    assert B.StackInfo(running=True, env=env).transport == bus
    assert B.StackInfo(running=True, env=both).transport == "lcm"


# ── the twist: shaped, re-sent while fresh, then one zero and silence ───────────────────


@pytest.mark.parametrize("track", [0.3, 0.2])
@pytest.mark.parametrize(("vx", "wz"), [(0.05, 0.4), (-0.1, 1.5), (0.2, -0.9)])
def test_an_arc_is_clipped_so_its_inner_track_never_reverses(
    track: float, vx: float, wz: float
) -> None:
    up = _up(track_m=track)
    svx, swz, mode, clip = B.shape(vx, wz, up)
    limit = 2 * abs(vx) / track
    assert svx == vx and mode == "arc"
    assert abs(swz) == pytest.approx(min(abs(wz), limit)) and (swz > 0) is (wz > 0)
    assert (clip is not None) is (abs(wz) > limit)
    inner = abs(svx) - abs(swz) * track / 2
    assert inner >= -1e-12, "the inner track would reverse into a full power pivot"


@pytest.mark.parametrize("track_m", [0.0, B.TRACK_MIN_M / 2, B.TRACK_MAX_M * 2, float("nan")])
def test_a_track_width_no_rover_has_refuses_the_twist_and_never_divides(track_m: float) -> None:
    """The arc clip divides by the track width, and a misread profile gives one no rover
    has: the twist is refused with a sentence, nothing goes out, and shaping never divides
    by zero."""
    stacks = _Stacks(B.StackInfo(running=False))
    bus = B.FakeBus(B.FAKE_UPSTREAM, B.FrameStore(), fps=0)
    bridge = B.Bridge(
        stack=stacks,
        up=B.FAKE_UPSTREAM,
        bus=bus,
        reread=lambda stack: (_up(track_m=track_m), None),
    )
    stacks.info = _running("run-1")
    code, doc = bridge.twist({"vx": 0.1, "wz": 0.5})
    assert code == 503 and "track width" in doc["reason"], doc
    assert bus.published == [], "nothing went out shaped by a track no rover has"
    assert B.shape(0.1, 0.5, _up(track_m=0.0)) is not None
    bridge.close()


def test_a_spin_keeps_to_the_makers_ceiling_and_a_still_twist_is_a_zero() -> None:
    up = _up(max_wz=0.5)
    assert B.shape(0.0, 0.0, up)[2] == "zero"
    _, wz, mode, clip = B.shape(0.0, -2.0, up)
    assert (mode, wz) == ("spin", -0.5) and clip == {"wz_asked": -2.0, "wz_sent": -0.5}
    assert B.shape(0.1, 0.0, up)[2] == "straight"


@pytest.mark.parametrize("deadman", [0.3, 0.5])
def test_the_twist_is_resent_while_fresh_then_one_zero_then_silence(deadman: float) -> None:
    clock = Clock()
    bus = Recorder(clock)
    pump = B.TwistPump(bus, _up(deadman_s=deadman), "/tele_cmd_vel#geometry_msgs.Twist", clock)
    pump.post(0.1, 0.0)
    for k in range(1, 50):
        if k * 0.1 >= deadman - 1e-6:
            break
        clock.t = k * 0.1
        pump.tick()
    assert len(bus.twists) >= 2, "re-sent while it is fresh"
    assert all(vx == 0.1 for _, _, vx, _ in bus.twists), "the twist while it is fresh"
    clock.t = deadman + 0.05  # past the deadman
    pump.tick()
    assert bus.twists[-1][2:] == (0.0, 0.0), "one zero"
    count = len(bus.twists)
    for _ in range(20):
        clock.t += 0.1
        pump.tick()
    assert len(bus.twists) == count, "then nothing: every message on the pad cancels navigation"


@pytest.mark.parametrize(
    ("max_wz", "rest", "wz"), [(0.4, 0.4, 0.4), (0.4, 0.4, 0.2), (0.5, 0.3, 0.1)]
)
def test_a_spin_is_the_makers_pulses_timed_on_the_rover(
    max_wz: float, rest: float, wz: float
) -> None:
    up = _up(max_wz=max_wz, rest_s=rest, pulse_s=0.1, deadman_s=10.0)
    clock = Clock()
    bus = Recorder(clock)
    pump = B.TwistPump(bus, up, "/tele_cmd_vel#geometry_msgs.Twist", clock)
    pump.post(0.0, wz)
    for _ in range(400):
        clock.t += 0.01
        pump.tick()
    on = [t for t, _, _, w in bus.twists if w != 0.0]
    starts = [on[0]] + [b for a, b in itertools.pairwise(on) if b - a > 0.05]
    period = up.pulse_s + rest * max_wz / wz
    assert B.rest_s(wz, up) == pytest.approx(rest * max_wz / wz)
    gaps = [b - a for a, b in itertools.pairwise(starts)]
    assert gaps and all(g == pytest.approx(period, abs=0.02) for g in gaps)
    assert pump.pulses == len(starts)


@pytest.mark.parametrize("hz", [10.0, 7.0, 3.0])
def test_a_pulse_is_on_for_exactly_its_pulse_s_whatever_the_resend_rate(hz: float) -> None:
    """The scheduler wakes on the pump's own edges, so a pulse ends pulse_s after it began, not
    at the first resend after that, and the next one begins a whole period after it. The
    turn a pulse gives is its on time, so that time is the maker's and nobody else's."""
    up_ = _up(max_wz=0.4, rest_s=0.4, pulse_s=0.1, deadman_s=10.0, pump_hz=hz)
    clock = Clock(5.0)
    bus = Recorder(clock)
    pump = B.TwistPump(bus, up_, "/tele_cmd_vel#geometry_msgs.Twist", clock)
    clock.t += 0.033  # a twist that lands between two of the scheduler's wakes
    pump.post(0.0, 0.2)
    for _ in range(1000):
        due = pump.tick()
        if due is None:
            break
        assert due > clock.t, "the scheduler is told its next edge, always ahead of now"
        clock.t = due
    else:
        raise AssertionError("the deadman never ended the spin")
    assert bus.twists[-1][2:] == (0.0, 0.0), "the deadman's one zero"
    on_times, began = [], None
    for t, _, _, wz in bus.twists:
        if wz != 0.0 and began is None:
            began = t
        elif wz == 0.0 and began is not None:
            on_times.append(t - began)
            began = None
    assert len(on_times) >= 5
    assert all(s == pytest.approx(up_.pulse_s, abs=1e-5) for s in on_times), on_times


def test_a_pump_built_after_the_bridge_started_still_repeats_and_zeroes_on_its_own() -> None:
    """A bridge started at boot has no pump until a stack it can reach starts. The scheduler
    runs from the start all the same, so that pump's twist repeats and its deadman zeroes."""
    stacks = _Stacks(B.StackInfo(running=False))
    built: list[Any] = []

    def make_bus(up_: Any) -> Any:
        bus = B.FakeBus(up_, B.FrameStore(), fps=0)
        built.append(bus)
        return bus

    bridge = B.Bridge(
        stack=stacks,
        up=None,
        bus=None,
        up_error="no stack ran when the bridge started",
        make_bus=make_bus,
        reread=lambda stack: (B.FAKE_UPSTREAM, None),
    )
    bridge.start()
    try:
        stacks.info = _running("run-1")
        assert bridge.twist({"vx": 0.1, "wz": 0.0})[0] == 200
        time.sleep(B.FAKE_UPSTREAM.deadman_s + 0.3)
        twists = [v for _, _, v in built[0].published]
        assert len(twists) >= 2, "repeated while fresh"
        assert twists[-1] == (0.0, 0.0), "and the deadman's one zero, sent on its own"
    finally:
        bridge.close()


# ── frames ──────────────────────────────────────────────────────────────────────────────


def test_a_frame_is_the_first_one_taken_after_the_request_arrived() -> None:
    clock = Clock(1000.0)
    store = B.FrameStore(clock)
    store.put("nose", 999.8, b"old", 999.85)
    store.put("nose", 999.9, b"older-but-later", 999.95)
    got = store.first_after("nose", 1000.0, 0.01)
    assert got is None, "nothing taken after the request yet"
    clock.t = 1000.2
    store.put("nose", 1000.1, b"new", 1000.15)
    assert store.first_after("nose", 1000.0, 0.01) == (1000.1, b"new", True)


def test_a_frame_received_before_the_request_is_never_handed_out_for_it() -> None:
    """A stamp ahead of the bridge's own clock once passed the trust check by up to a frame
    period, and a frame received before the request was then called taken after it, with a
    frame_taken_at later than its capture. A frame that reached the bridge before the request
    is never handed out for it, and a stamp further ahead than the slack is not trusted."""
    clock = Clock(100.0)
    store = B.FrameStore(clock)
    period, ahead = 0.2, 0.15  # synthetic: past the slack, inside a frame period
    for k in range(4):
        received = 100.0 + period * k
        store.put("nose", received + ahead, b"jpeg-%d" % k, received)
    arrived = 100.0 + period * 3 + period / 4  # after the last of them reached the bridge
    assert store.first_after("nose", arrived, 0.0) is None, "none reached it after the request"
    later = arrived + period
    store.put("nose", later + ahead, b"jpeg-4", later)
    assert store.first_after("nose", arrived, 0.0) == (later + ahead, b"jpeg-4", False)


def test_a_trusted_frame_is_after_a_request_only_by_the_slack_past_it() -> None:
    clock = Clock(100.0)
    store = B.FrameStore(clock)
    arrived, slack = 100.0, B.STAMP_SLACK_S
    store.put("nose", arrived + slack / 2, b"close", arrived + slack / 2)
    assert store.first_after("nose", arrived, 0.0) is None
    store.put("nose", arrived + 2 * slack, b"after", arrived + 2 * slack)
    assert store.first_after("nose", arrived, 0.0) == (arrived + 2 * slack, b"after", True)


def test_a_camera_on_its_own_clock_is_never_said_to_be_taken_after_a_request() -> None:
    clock = Clock(1_760_000_000.0)
    store = B.FrameStore(clock)
    arrived = clock.t
    clock.t += 0.2
    store.put("nose", 1234.5, b"device-clock", clock.t)  # seconds since the camera booted
    assert store.first_after("nose", arrived, 0.01) == (1234.5, b"device-clock", False)
    assert store.heard("nose")["stamp_trusted"] is False


def test_the_frame_route_names_a_frame_taken_after_it_and_refuses_an_unknown_camera() -> None:
    bridge = B.fake_bridge(fps=10.0)
    code, jpeg, headers = bridge.frame("nose")
    assert code == 200 and isinstance(jpeg, bytes) and jpeg[:2] == b"\xff\xd8"
    assert headers[B.FRAME_AFTER_HEADER] == "1"
    assert bridge.frame("tail")[0] == 404
    bridge.close()
    device = B.fake_bridge(fps=10.0, stamps="device")
    assert device.frame("wrist")[2][B.FRAME_AFTER_HEADER] == "0"
    device.close()


# ── the arm cycle, a real subprocess ────────────────────────────────────────────────────


def test_a_restarted_bridge_never_hands_out_an_id_a_close_from_before_holds() -> None:
    """A close cancels its own cycle by id. Ids drawn from a count alone come back after a
    restart, and a session from before it would cancel another session's cycle, so each
    process draws a prefix of its own."""
    first = B.fake_bridge(cycle_s=0.2, fps=0)
    ok, mine = first.jobs.start("pick", None)
    assert ok, mine
    _wait(lambda: first.jobs.doc()["state"] == "done")
    first.close()
    second = B.fake_bridge(cycle_s=30.0, fps=0)
    ok, theirs = second.jobs.start("pick", None)
    assert ok, theirs
    _wait(lambda: second.bus.executing)
    assert mine != theirs
    code, doc = second.arm_cancel({"id": mine})
    assert code == 200 and doc["cancelled"] is False and second.jobs.running(), doc
    second.stop()
    second.close()


def test_the_makers_script_runs_in_the_arm_runs_own_environment() -> None:
    """The mission runs the maker's script in its own environment, so quackd runs it in the
    moss-arm run's, its calibration among it, never the bridge's. Unreadable, the cycle is
    refused rather than played on another calibration."""
    stack = B.StackInfo(
        running=True,
        blueprint="moss-explore",
        pid=os.getpid(),
        run_id="r",
        env={"DIMOS_TRANSPORT": "lcm"},
        arm_run=B.Run(pid=os.getpid(), blueprint="moss-arm", run_id="a", started=0.0),
        arm_env={**os.environ, "MOSS_ARM_CALIB": "/the/arm/runs/own.json"},
    )
    script = "import os; print('running: ' + os.environ.get('MOSS_ARM_CALIB', 'none'))"
    bridge = B.Bridge(
        stack=lambda: stack,
        up=B.FAKE_UPSTREAM,
        bus=B.FakeBus(B.FAKE_UPSTREAM, B.FrameStore(), fps=0),
        arm_argv=lambda pose, phase: [sys.executable, "-c", script],
    )
    assert bridge.arm_cycle({})[0] == 200
    _wait(lambda: bridge.jobs.doc()["state"] not in B.JOB_RUNNING)
    assert "running: /the/arm/runs/own.json" in bridge.jobs.doc()["lines"]
    unread = replace(stack, arm_env=None)
    bridge._stack = lambda: unread
    code, doc = bridge.arm_cycle({})
    assert code == 503 and "unreadable" in doc["reason"], doc
    bridge.close()


def test_the_drive_pose_is_read_with_the_arm_runs_own_calibration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """upstream reads the calibration from the environment it is given, and from its own
    process's otherwise, which on the rover is the bridge's. So the drive pose is read with
    the run that holds the arm's, and only with the stack's when there is no other."""
    asked: list[dict[str, str]] = []

    def calibration_path(explicit: Any = None, env: Any = None) -> str:
        asked.append(dict(env or {}))
        return str((env or {}).get("MOSS_ARM_CALIB", "default"))

    poses = {"the-arms": [0.5, 0.25], "the-stacks": [0.125, 0.0625]}  # synthetic
    fakes: dict[str, dict[str, Any]] = {
        "arm": {
            "TICKS_PER_RAD": 600.0,
            "START_TOLERANCE_RAD": 0.04,
            "HW_ID": "limb",
            "ARM_JOINTS": ("a", "b"),
            "GRIPPER": "g",
            "ARM_COORDINATOR_NAME": "Coordinator",
            "calibration_path": calibration_path,
            "drive_pose_rad": lambda calibration=None: poses.get(str(calibration), [9.0, 9.0]),
        },
        "arm_feetech": {"DEFAULT_STILL_TICKS_PER_S": 15.0},
        "mission": {
            "SERVO_W_MAX": 0.3,
            "PULSE_S_ENV": "PULSE",
            "PULSE_S": 0.2,
            "REST_S_ENV": "REST",
            "REST_S": 0.3,
            "TICK_HZ": 8.0,
        },
        "moss_adapter": {"FIRMWARE_COMMAND_TIMEOUT_S": 0.25, "FIRMWARE_MAX_PCT": 90.0},
        "robot_profile": {"current": lambda env: type("P", (), {"track_m": 0.31})()},
    }
    package = ModuleType("moss_dimos")
    for name, attrs in fakes.items():
        module = ModuleType(f"moss_dimos.{name}")
        for key, value in attrs.items():
            setattr(module, key, value)
        setattr(package, name, module)
        monkeypatch.setitem(sys.modules, f"moss_dimos.{name}", module)
    monkeypatch.setitem(sys.modules, "moss_dimos", package)
    read = B.read_upstream({"MOSS_ARM_CALIB": "the-stacks"}, {"MOSS_ARM_CALIB": "the-arms"})
    assert read.drive_pose == tuple(poses["the-arms"]) and asked[-1]["MOSS_ARM_CALIB"] == "the-arms"
    alone = B.read_upstream({"MOSS_ARM_CALIB": "the-stacks"})
    assert alone.drive_pose == tuple(poses["the-stacks"])


def test_a_cycle_runs_the_script_and_ends_with_its_exit_code() -> None:
    bridge = B.fake_bridge(cycle_s=0.3, fps=0)
    code, doc = bridge.arm_cycle({"pick_pose": "pick_low"})
    assert code == 200 and doc["ok"]
    _wait(lambda: bridge.arm_doc()["job"]["state"] == "done")
    job = bridge.arm_doc()["job"]
    assert (
        job["id"] == doc["id"] and job["exit"] == 0 and "running: fake trajectory" in job["lines"]
    )
    bridge.close()


@pytest.mark.parametrize(
    ("flag", "ending", "exit"), [("--lag", "lag", 4), ("--refuse", "refused", 2)]
)
def test_a_lag_and_a_refusal_end_the_way_the_script_says(flag: str, ending: str, exit: int) -> None:
    bridge = B.fake_bridge(cycle_s=0.3, fps=0, script_flags=(flag,))
    assert bridge.arm_cycle({})[0] == 200
    _wait(lambda: bridge.arm_doc()["job"]["state"] in B.JOB_ENDINGS)
    job = bridge.arm_doc()["job"]
    assert (job["state"], job["exit"]) == (ending, exit)
    assert ("ARM LAG" if flag == "--lag" else "refused") in job["reason"]
    bridge.close()


def test_a_cancel_stops_the_script_then_cancels_then_reads_the_arm_holding() -> None:
    bridge = B.fake_bridge(cycle_s=30.0, fps=0)
    bus = bridge.bus
    bridge.arm_cycle({})
    _wait(lambda: bus.executing)
    doc = bridge.stop()
    assert doc["ok"], doc
    arm = doc["arm"]
    assert arm["script"] is True and arm["cancel"] == "CANCELLED" and arm["read_still"] is True
    _wait(lambda: bridge.arm_doc()["job"]["state"] == "cancelled")
    assert bus.events.index("arm cancel") < len(bus.events) - 1, "the zero twist came after"
    bridge.close()


def _watch_to_its_end(bridge: Any, clock: Clock) -> tuple[dict[str, Any], list[bool]]:
    """The bridge's watch after a stop, one look at a time on the test's clock, to its word,
    with whether the fake arm was executing after each look."""
    executing: list[bool] = []
    for _ in range(int(bridge.arm_guard_s / B.ARM_GUARD_POLL_S) + 10):
        said = bridge.guard_tick()
        executing.append(bridge.bus.executing)
        if said is not None:
            return said, executing
        clock.t += B.ARM_GUARD_POLL_S
    raise AssertionError("the watch never ended")


def test_no_hold_is_claimed_and_no_cycle_starts_before_the_window_after_a_stop_has_passed() -> None:
    """A stop's two still reads, and a cancel that finds nothing, say the arm read still and
    not that it holds, since a trajectory sent before the stop can still land. Until
    ARM_GUARD_S has passed the cycle's hold is unread and a new cycle is refused, and once it
    has passed with the joints still, the cycle holds."""
    bridge = B.fake_bridge(cycle_s=30.0, fps=0)
    bus = bridge.bus
    assert bridge.arm_cycle({})[0] == 200
    _wait(lambda: bus.executing)
    clock = Clock()
    _all_on(clock, bridge)
    doc = bridge.stop()
    assert doc["arm"]["held"] is None and doc["arm"]["read_still"] is True, doc["arm"]
    _wait(lambda: bridge.arm_doc()["job"]["state"] == "cancelled")
    assert bridge.arm_doc()["job"]["held"] is None, "no hold before the window has passed"
    assert bridge.arm_doc()["guard"]["watching"] is True
    code, said = bridge.arm_cycle({})
    assert code == 409 and "watched" in said["reason"], said
    word, _ = _watch_to_its_end(bridge, clock)
    assert word["held"] is True and not word["moved"], word
    assert bridge.arm_doc()["job"]["held"] is True
    assert bridge.arm_doc()["guard"] == word
    bridge.close()


def test_a_cycle_cancelled_while_starting_is_cancelled_again_for_a_trajectory_in_flight() -> None:
    """A script ended while it starts may already have sent its execute_trajectory, and a
    request still in flight lands after the cancel that found nothing: the cancel goes out
    once more a little later, so that trajectory does not play."""
    clock = Clock()
    stack = B.StackInfo(
        running=True,
        blueprint="moss-explore",
        pid=os.getpid(),
        run_id="r",
        env={"DIMOS_TRANSPORT": "lcm"},
        arm_run=B.Run(pid=os.getpid(), blueprint="moss-arm", run_id="a", started=0.0),
        arm_env=dict(os.environ),
    )
    bus = B.FakeBus(B.FAKE_UPSTREAM, B.FrameStore(), fps=0)
    bridge = B.Bridge(
        stack=lambda: stack,
        up=B.FAKE_UPSTREAM,
        bus=bus,
        # a script still importing, so its cycle is still starting when the stop comes
        arm_argv=lambda pose, phase: [sys.executable, "-c", "import time; time.sleep(30)"],
    )
    _all_on(clock, bridge)
    assert bridge.arm_cycle({})[0] == 200
    assert bridge.jobs.doc()["state"] == "starting"
    bus.in_flight(B.STARTING_RECANCEL_S / 2)
    doc = bridge.stop()
    assert doc["arm"]["cancel"] == "ALREADY_STOPPED", "the first cancel found nothing"
    assert doc["arm"]["late"]["cancel"] == "CANCELLED", doc["arm"]
    assert doc["arm"]["read_still"] is True, doc["arm"]
    clock.t += 5.0
    bus.arm_joints()
    assert not bus.executing, "the trajectory that landed after the first cancel is cancelled"
    bridge.close()


def test_an_arm_that_moves_on_after_its_cancel_is_said_and_cancelled_again() -> None:
    bridge = B.fake_bridge(cycle_s=30.0, fps=0, arm_ignores_cancel=True)
    bridge.arm_cycle({})
    _wait(lambda: bridge.bus.executing)
    doc = bridge.stop()
    assert not doc["ok"] and any("did not hold after its cancel" in e for e in doc["errors"])
    assert doc["arm"]["held"] is False and doc["arm"]["cancel_again"] == "CANCELLED"
    assert bridge.bus.cancels == 2
    bridge.close()


@pytest.mark.parametrize("session", [False, True])
def test_a_bridge_closed_mid_cycle_cancels_the_cycle(session: bool) -> None:
    """A unit stop or a restart reaches the bridge alone, so its close is the cancel, with a
    session still open or with none left to say the cycle was started."""
    bridge = B.fake_bridge(cycle_s=30.0, fps=0)
    bus = bridge.bus
    if session:
        _beat(bridge, "run", 0.5, None)
    bridge.arm_cycle({})
    _wait(lambda: bus.executing)
    bridge.close()
    assert "arm cancel" in bus.events and not bus.executing


DIES = (
    "import sys, time\n"
    "print('cycle: fake', flush=True)\n"
    "print('running: fake trajectory', flush=True)\n"
    "time.sleep(0.3)\n"
    "raise RuntimeError('get_joint_positions returned nothing')\n"
)
"""A maker's script that dies after its trajectory went out, any way but its own."""


def test_a_script_that_dies_mid_cycle_has_its_trajectory_cancelled_and_the_arm_read() -> None:
    """Its one trajectory plays on with nothing watching it for lag. The bridge cancels it as
    soon as it reads the exit, reads the hold, and only then ends the job, so no twist or new
    cycle goes out under a moving arm."""
    bridge = B.fake_bridge(cycle_s=30.0, fps=0)
    bus = bridge.bus
    bridge.jobs.argv_for = lambda pose, phase: [sys.executable, "-c", DIES]
    assert bridge.arm_cycle({})[0] == 200
    _wait(lambda: (bridge.jobs.doc() or {}).get("state") == "failed")
    job = bridge.jobs.doc()
    assert job["exit"] == 1 and job["held"] is True, job
    assert bus.cancels >= 1 and not bus.executing, "the trajectory it left playing was cancelled"
    bridge.close()


def test_a_script_killed_by_a_signal_has_its_trajectory_cancelled_too() -> None:
    """A Ctrl-C on the bridge's terminal no longer reaches the script, which has a session of
    its own. A signal that does reach it, the OOM killer's say, is an ending like any other
    death, and the bridge cancels and reads the arm after it."""
    bridge = B.fake_bridge(cycle_s=30.0, fps=0)
    bus = bridge.bus
    assert bridge.arm_cycle({})[0] == 200
    _wait(lambda: bus.executing)
    proc = bridge.jobs._proc
    assert proc is not None
    proc.terminate()
    _wait(lambda: (bridge.jobs.doc() or {}).get("state") == "failed")
    assert bridge.jobs.doc()["held"] is True and not bus.executing
    assert "start_new_session=True" in BRIDGE.read_text(encoding="utf-8")
    bridge.close()


@pytest.mark.parametrize(
    ("code", "cancelled", "ending"),
    [
        (0, False, "done"),
        (2, False, "refused"),
        (3, False, "refused"),
        (4, False, "lag"),
        (1, False, "failed"),
        (-9, False, "failed"),
        (-15, False, "failed"),
        (-15, True, "cancelled"),
    ],
)
def test_every_ending_but_done_and_refused_leaves_a_trajectory_to_cancel(
    code: int, cancelled: bool, ending: str
) -> None:
    """A negative exit is a signal, and a signal is a death like any other."""
    assert B.ending_of(code, cancelled) == ending


def test_the_arm_and_the_tracks_never_move_together() -> None:
    bridge = B.fake_bridge(cycle_s=30.0, fps=0)
    assert bridge.twist({"vx": 0.1, "wz": 0.0})[0] == 200
    code, doc = bridge.arm_cycle({})
    assert code == 409 and "twist is running" in doc["reason"]
    time.sleep(B.FAKE_UPSTREAM.deadman_s + 0.1)
    assert bridge.arm_cycle({})[0] == 200
    code, doc = bridge.twist({"vx": 0.1, "wz": 0.0})
    assert code == 409 and "cycle" in doc["reason"]
    code, doc = bridge.arm_cycle({})
    assert code == 409 and "another arm job" in doc["reason"]
    bridge.stop()
    bridge.close()


def test_a_twist_asked_while_a_cycle_asks_the_coordinator_waits_and_is_refused() -> None:
    """The cycle checks that no twist runs, asks the coordinator, then starts: a twist that
    lands during that round trip waits for the cycle's start, then sees it, so the arm and
    the tracks never both pass their check."""
    bridge = B.fake_bridge(cycle_s=5.0, fps=0)
    bus = bridge.bus
    joints = bus.arm_joints
    asking = threading.Event()

    def slow() -> dict[str, float]:
        asking.set()
        time.sleep(0.3)  # the coordinator's round trip on a loaded rover
        return dict(joints())

    bus.arm_joints = slow
    out: dict[str, Any] = {}
    cycle = threading.Thread(target=lambda: out.setdefault("cycle", bridge.arm_cycle({})))
    cycle.start()
    assert asking.wait(5.0)
    out["twist"] = bridge.twist({"vx": 0.2, "wz": 0.0})
    cycle.join()
    assert out["cycle"][0] == 200, out
    assert out["twist"][0] == 409 and "cycle" in out["twist"][1]["reason"], out
    assert all(values == (0.0, 0.0) for _, _, values in bus.published), "no twist went out"
    bridge.stop()
    bridge.close()


def test_on_moss_litter_the_mission_owns_the_base_and_the_arm() -> None:
    bridge = B.fake_bridge(blueprint="moss-litter", fps=0)
    assert bridge.twist({"vx": 0.1})[0] == 409
    code, doc = bridge.arm_cycle({})
    assert code == 409 and "mission owns the arm" in doc["reason"]
    bridge.close()


# ── the stop ────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("blueprint", "chans"),
    [
        ("moss-explore", ["/tele_cmd_vel#geometry_msgs.Twist"]),
        ("moss-nav", ["/tele_cmd_vel#geometry_msgs.Twist", "/cmd_vel#geometry_msgs.Twist"]),
    ],
)
def test_the_stops_zero_goes_where_the_cockpits_own_soft_stop_sends_it(
    blueprint: str, chans: list[str]
) -> None:
    bridge = B.fake_bridge(blueprint=blueprint, fps=0)
    doc = bridge.stop()
    assert doc["ok"], doc
    sent = [chan for _, chan, values in bridge.bus.published if values == (0.0, 0.0)]
    assert sent[: len(chans)] == chans
    bridge.close()


def test_off_moss_litter_the_zero_goes_before_an_arm_that_does_not_answer() -> None:
    """The zero ends a twist and a planner goal at once. An arm whose coordinator hangs holds
    its cancel for the RPC's whole timeout, so the cancel comes after the zero, never before
    it."""
    clock = Clock()
    bridge = B.fake_bridge(fps=0)
    _all_on(clock, bridge)
    bus = bridge.bus
    bus.arm_answers = False
    doc = bridge.stop()
    zeros = [t for t, _, values in bus.published if values == (0.0, 0.0)]
    assert zeros and zeros[0] == 0.0, f"the first zero went out at {zeros[:1]}"
    assert clock.t >= B.RPC_TIMEOUT_S, "the arm's cancel waited out its timeout after it"
    assert "the arm's coordinator did not answer, and no cycle of quackd's ran" in doc["did"]
    bridge.close()


def test_an_arm_that_reads_still_mid_trajectory_is_never_said_to_hold() -> None:
    """A trajectory in one of its pauses reads as still twice running. The cancel that
    confirms the hold finds it still executing, so the arm is not said to hold, however
    still it read."""
    bridge = B.fake_bridge(fps=0, arm_ignores_cancel=True, arm_still_while_executing=True)
    bridge.bus.executing = True
    doc = bridge.stop()
    assert doc["arm"]["held"] is False and not doc["ok"], doc
    assert any("did not hold after its cancel" in e for e in doc["errors"]), doc["errors"]
    bridge.close()


def test_a_mission_stop_lost_on_the_bus_is_sent_again_while_the_mission_answers() -> None:
    """A stop goes out without an answer, so a lossy bus can drop it. Each probe that still
    hears the mission sends it again, and the second one stops it."""
    bridge, clock = _litter_on_fake_time()
    bus = bridge.bus
    sent = bus.mission_stop
    asked: list[float] = []

    def lossy() -> None:
        asked.append(clock.t)
        if len(asked) > 1:  # the first one never arrived
            sent()

    bus.mission_stop = lossy
    doc = bridge.stop()
    assert doc["mission"] == "stopped" and doc["ok"], doc
    assert len(asked) >= 2 and bus.mission_stops >= 1
    bridge.close()


def test_a_cancel_with_every_rpc_at_its_timeout_takes_its_named_worst_case() -> None:
    """Each RPC answering only as its timeout runs out, reads that stay still, and a
    coordinator that still runs the trajectory at every confirming cancel: the slowest a
    cancel and its reads can be, which the stop's own bound adds up."""
    clock = Clock()
    bus = B.FakeBus(
        B.FAKE_UPSTREAM,
        B.FrameStore(),
        fps=0,
        arm_ignores_cancel=True,
        arm_still_while_executing=True,
        clock=clock,
    )
    bus.executing = True
    bus.arm_cancel = _at_its_timeout(clock, bus.arm_cancel, B.RPC_TIMEOUT_S)
    bus.arm_joints = _at_its_timeout(clock, bus.arm_joints, B.RPC_TIMEOUT_S)

    def advance(s: float) -> None:
        clock.t += s

    out = B.cancel_and_read(bus, B.FAKE_UPSTREAM, advance)
    assert out["held"] is False and out["confirm"] == "CANCELLED", out
    assert clock.t == pytest.approx(B._CANCEL_AND_READ_WORST_S)


def test_the_slowest_litter_stop_answers_inside_the_bound_the_client_waits_for() -> None:
    """A pick that outlives both signals, every RPC answering at its timeout, an arm that
    never holds and a mission whose probe waits its whole timeout: the stop still answers
    inside STOP_WORST_S, the bound the client's own wait for a stop is held to."""

    class Stubborn:
        def picks(self, stack: Any) -> list[int]:
            return [4321]

        def descendants(self, pid: int) -> list[int]:
            return []

        def alive(self, pid: int) -> bool:
            return True

        def signal(self, pid: int, sig: int) -> None:
            pass

    clock = Clock()
    bridge = B.fake_bridge(
        blueprint="moss-litter",
        fps=0,
        mission_stop_s=0.2,
        arm_ignores_cancel=True,
        arm_still_while_executing=True,
    )
    _all_on(clock, bridge)
    assert bridge.mission_doc()["answering"] is True
    bridge.procs = Stubborn()
    bus = bridge.bus
    bus.executing = True
    bus.arm_cancel = _at_its_timeout(clock, bus.arm_cancel, B.RPC_TIMEOUT_S)
    bus.arm_joints = _at_its_timeout(clock, bus.arm_joints, B.RPC_TIMEOUT_S)
    status = bus.mission_status
    bus.mission_status = lambda timeout_s: _at_its_timeout(clock, status, timeout_s)(timeout_s)
    doc = bridge.stop()
    assert doc["pick"]["left"] == [4321] and doc["arm"]["held"] is False, doc
    assert 0.0 < clock.t <= B.STOP_WORST_S, clock.t
    bridge.close()


def test_a_mission_that_falls_silent_with_the_arm_is_a_bus_down_not_a_stop() -> None:
    """Silence confirms the mission's stop only when another module answers in the same
    breath: with the arm's coordinator silent too, the bus itself may be down, and the stop
    is unconfirmed."""
    clock = Clock()
    bridge = B.fake_bridge(blueprint="moss-litter", fps=0, mission_stop_s=0.2)
    _all_on(clock, bridge)
    assert bridge.mission_doc()["answering"] is True, "heard, as quackd's connect hears it"
    bus = bridge.bus

    def down() -> None:
        bus.mission_answers = False
        bus.arm_answers = False

    bus.mission_stop = down
    doc = bridge.stop()
    assert doc["mission"] == "unconfirmed" and not doc["ok"], doc
    assert any("the bus itself may be down" in e for e in doc["errors"]), doc["errors"]
    bridge.close()


def test_a_stop_for_a_run_that_has_gone_touches_nothing_of_the_new_one() -> None:
    """The owner restarted the stack. quackd's stop names the run it connected to, and the
    new run is sent no mission stop and no zero, and says why. Its arm cycle is cancelled
    only for the session that started it. A stop that names the run running, or none,
    stops as ever."""
    bridge = B.fake_bridge(cycle_s=30.0, fps=0)
    bus = bridge.bus
    code, started = bridge.arm_cycle({"session": "the-new-runs"})
    assert code == 200, started
    _wait(lambda: bus.executing)
    doc = bridge.stop("the-run-before", "the-old-session")
    assert doc["run_changed"] and doc["run"] == "fake-run" and not doc["ok"], doc
    assert any("touches nothing of it" in e for e in doc["errors"]), doc["errors"]
    assert bus.published == [] and bus.events == [], "nothing went out to the new run"
    assert bus.executing and bridge.jobs.running(), "another session's cycle plays on"
    mine = bridge.stop("the-run-before", "the-new-runs")
    assert mine["arm"]["cancel"] == "CANCELLED" and not bus.executing, mine
    assert not [e for e in bus.events if e.startswith("twist")], "still no zero"
    assert bridge.stop("fake-run")["ok"] and bus.published, "the run running is stopped"
    bridge.close()


def test_on_moss_litter_a_stop_for_a_run_that_has_gone_sends_no_mission_stop() -> None:
    bridge = B.fake_bridge(blueprint="moss-litter", fps=0)
    doc = bridge.stop("the-run-before")
    assert doc["run_changed"] and bridge.bus.mission_stops == 0, doc
    assert bridge.mission_doc()["stopped"] is False
    bridge.close()


def test_on_moss_litter_the_zero_goes_out_before_the_pick_is_ended_and_the_arm_cancelled() -> None:
    """Ending a pick that outlives its SIGTERM and an arm slow to answer take seconds, and the
    base stops at once all the same: its zero goes out right after the mission's stop."""
    clock = Clock()
    bridge = B.fake_bridge(blueprint="moss-litter", fps=0, mission_stop_s=0.2)
    _all_on(clock, bridge)
    assert bridge.mission_doc()["answering"] is True
    bus = bridge.bus
    bus.start_mission_pick(startup_s=60.0, ignores_term=True)
    bus.arm_cancel = _at_its_timeout(clock, bus.arm_cancel, B.RPC_TIMEOUT_S)
    began, before = clock.t, len(bus.events)
    bridge.stop()
    zeros = [t for t, _, values in bus.published if values == (0.0, 0.0)]
    assert zeros and zeros[0] == began, f"the first zero went out at {zeros[:1]}"
    assert clock.t > began + B.SCRIPT_GRACE_S, "the pick's end took its grace after it"
    assert _sends(bus.events[before:])[:2] == [
        "mission stop",
        "twist /tele_cmd_vel#geometry_msgs.Twist",
    ]
    bridge.close()


def test_a_mission_still_answering_is_sent_its_stop_again_less_and_less_often() -> None:
    """Every probe still hears it, and the resends back off, doubling the gap each time, so a
    few go out in the confirmation's window rather than one every probe."""
    clock = Clock()
    bridge = B.fake_bridge(blueprint="moss-litter", fps=0, mission_ignores_stop=True)
    _all_on(clock, bridge)
    assert bridge.mission_doc()["answering"] is True
    doc = bridge.stop()
    assert doc["mission"] == "unconfirmed", doc
    doublings, gap = 0, B.MISSION_RESEND_FIRST_S
    while gap < B.MISSION_CONFIRM_S:
        doublings, gap = doublings + 1, gap * 2
    sent = bridge.bus.mission_stops
    assert 2 <= sent <= 1 + doublings + 1, f"{sent} stops in {B.MISSION_CONFIRM_S:g} s"
    bridge.close()


def test_every_stop_cancels_and_reads_and_watches_only_what_was_in_play() -> None:
    """Every stop does the same arm step, a verb's own at its end as much as the stop verb's.
    cancel_trajectory goes out, and when it finds nothing executing, with nothing of quackd's
    in play, that is the whole step: no reads and no watch, so the next cycle is not refused.
    A trajectory it cancels, the owner's own in one of its pauses among them, has the arm read
    and watched."""
    clock = Clock()
    bridge = B.fake_bridge(fps=0, arm_still_while_executing=True)
    _all_on(clock, bridge)
    bus = bridge.bus
    asks = bus.arm_asks
    doc = bridge.stop()
    assert bus.cancels == 1 and bus.arm_asks == asks + 1, "one cancel, and no reads"
    assert doc["arm"]["executing"] is False and bridge.arm_doc()["guard"] is None, doc["arm"]
    assert any(v == (0.0, 0.0) for _, _, v in bus.published), "the zero went out first"
    bus.executing = True  # the owner's own trajectory, in one of its pauses
    doc = bridge.stop()
    assert doc["arm"]["cancel"] == "CANCELLED" and "confirm" in doc["arm"], doc["arm"]
    assert bridge.arm_doc()["guard"]["watching"] is True
    bridge.close()


def test_on_moss_litter_the_mission_stops_first_then_the_zero_then_the_arm() -> None:
    bridge = B.fake_bridge(blueprint="moss-litter", fps=0, mission_stop_s=0.2)
    bus = bridge.bus
    assert bridge.mission_doc()["answering"] is True, "heard, as quackd's connect hears it"
    bus.executing = True  # the mission's own pick is mid cycle
    doc = bridge.stop()
    assert doc["ok"] and doc["mission"] == "stopped", doc
    sent = _sends(bus.events)
    cancel = sent.index("arm cancel")
    assert sent[0] == "mission stop" and cancel >= 2, sent
    assert all(e.startswith("twist ") for e in sent[1:cancel]), "the zeros, before the arm"
    assert bridge.mission_doc()["stopped"] is True
    sent = bus.mission_stops
    again = bridge.stop()
    assert again["ok"] and "the mission was already stopped" in again["did"]
    assert bus.mission_stops == sent, "stopped stays stopped: the stop is not sent again"
    bridge.close()


def test_on_moss_litter_the_arm_is_cancelled_before_the_missions_stop_is_confirmed() -> None:
    """A mission slow to confirm its stop holds nothing else back: its stop goes out first, the
    arm's cancel and the zero follow at once, and only then does the bridge wait for the
    mission's RPC to fall silent. This mission takes three quarters of the confirmation."""
    clock = Clock()
    slow = B.MISSION_CONFIRM_S * 0.75
    bridge = B.fake_bridge(blueprint="moss-litter", fps=0, mission_stop_s=slow)
    _fake_time(bridge, clock)
    bus = bridge.bus
    assert bridge.mission_doc()["answering"] is True, "heard, as quackd's connect hears it"
    bus.executing = True  # the mission's own pick is mid cycle
    cancelled_at: list[float] = []
    cancel = bus.arm_cancel

    def timed() -> str:
        cancelled_at.append(clock.t)
        return str(cancel())

    bus.arm_cancel = timed
    doc = bridge.stop()
    assert doc["ok"] and doc["mission"] == "stopped", doc
    sent = _sends(bus.events)
    cancel = sent.index("arm cancel")
    assert sent[0] == "mission stop" and cancel >= 2, sent
    assert all(e.startswith("twist ") for e in sent[1:cancel]), "the zeros, before the arm"
    assert cancelled_at and cancelled_at[0] < slow <= clock.t, "the cancel waited on the mission"
    bridge.close()


def test_a_stop_whose_zeros_fail_still_confirms_the_missions_stop() -> None:
    """A bus that refuses every twist: each zero's failure is said, and the stop still answers
    with the mission's own word rather than raising before it was asked."""
    clock = Clock()
    bridge = B.fake_bridge(blueprint="moss-litter", fps=0, mission_stop_s=0.2)
    _fake_time(bridge, clock)
    assert bridge.mission_doc()["answering"] is True

    def refused(_chan: str, _vx: float, _wz: float) -> None:
        raise OSError("the bus refused the publish")

    bridge.bus.publish_twist = refused
    doc = bridge.stop()
    assert doc["mission"] == "stopped" and not doc["ok"], doc
    assert any("the pump's zero" in e for e in doc["errors"]), doc["errors"]
    bridge.close()


# ── the mission's own pick, which its stop leaves running ───────────────────────────────


def _sends(events: list[str]) -> list[str]:
    """What went out, in order, each kind once however many times in a row it was sent."""
    kinds = [" ".join(e.split()[:2]) for e in events]
    return [k for i, k in enumerate(kinds) if i == 0 or kinds[i - 1] != k]


def _litter_on_fake_time() -> tuple[Any, Clock]:
    clock = Clock()
    bridge = B.fake_bridge(blueprint="moss-litter", fps=0, mission_stop_s=0.2)
    _fake_time(bridge, clock)
    assert bridge.mission_doc()["answering"] is True, "heard, as quackd's connect hears it"
    return bridge, clock


def test_on_moss_litter_a_stop_ends_the_missions_pick_before_its_trajectory_goes_out() -> None:
    """The mission's stop leaves its pick running (`up.MISSION_STOP_LEAVES_PICK`), and a pick
    still starting has sent nothing a cancel could find. Its trajectory would go out after the
    cancel and play the whole cycle, so the stop ends the maker's script first."""
    bridge, clock = _litter_on_fake_time()
    bus = bridge.bus
    pid = bus.start_mission_pick(startup_s=0.5)
    doc = bridge.stop()
    clock.t += 5.0  # long past the script's startup
    bus.arm_joints()
    assert not bus.executing, "the trajectory never went out"
    assert "the mission's script: execute_trajectory" not in bus.events
    assert doc["ok"] and doc["mission"] == "stopped", doc
    assert doc["pick"] == {"terminated": [pid], "killed": [], "left": []}
    assert doc["arm"]["read_still"] is True, "read twice, never assumed"
    sent = [e for e in bus.events if e.startswith(("mission stop", "pick script", "arm cancel"))]
    assert sent[:3] == ["mission stop", "pick script SIGTERM", "arm cancel"]
    bridge.close()


def test_on_moss_litter_a_pick_already_playing_is_ended_and_its_trajectory_cancelled() -> None:
    bridge, clock = _litter_on_fake_time()
    bus = bridge.bus
    bus.start_mission_pick(startup_s=0.1)
    clock.t += 0.5
    bus.arm_joints()
    assert bus.executing, "the mission's trajectory is out"
    doc = bridge.stop()
    assert not bus.executing and doc["arm"]["cancel"] == "CANCELLED", doc
    assert doc["arm"]["read_still"] is True and doc["pick"]["terminated"]
    bridge.close()


def test_on_moss_litter_a_pick_started_after_the_first_look_is_ended_too() -> None:
    """The mission's last tick can start a pick after the bridge looked for one and before
    the mission's stop took effect, and that pick's trajectory would go out after every
    cancel. Once the stop has had its time the bridge looks again, ends it while it is still
    starting, and cancels and reads the arm once more."""
    clock = Clock()
    bridge = B.fake_bridge(blueprint="moss-litter", fps=0, mission_stop_s=0.2)
    _all_on(clock, bridge)
    assert bridge.mission_doc()["answering"] is True
    bus = bridge.bus
    looks = bus.picks

    def a_tick_too_late(stack: Any) -> list[int]:
        found = looks(stack)
        if bus.pick is None:  # none yet, and the mission's last tick starts one right after
            bus.start_mission_pick(startup_s=1.0)
        return list(found)

    bus.picks = a_tick_too_late
    doc = bridge.stop()
    clock.t += 5.0  # long past the late pick's startup
    bus.arm_joints()
    assert not bus.executing, "the late pick never sent its trajectory"
    assert doc["pick"] is None and doc["late"]["pick"]["terminated"], doc
    assert doc["arm"]["read_still"] is True and doc["mission"] == "stopped", doc
    bridge.close()


def test_on_moss_litter_a_trajectory_that_lands_after_the_first_cancel_is_cancelled() -> None:
    """A pick ended just after it sent its trajectory leaves that trajectory to land after
    the first cancel, which found nothing. The cancel that follows the mission's stop, no
    sooner than STARTING_RECANCEL_S after the first, finds it and ends it."""
    clock = Clock()
    bridge = B.fake_bridge(blueprint="moss-litter", fps=0, mission_stop_s=0.2)
    _all_on(clock, bridge)
    assert bridge.mission_doc()["answering"] is True
    bus = bridge.bus
    cancel = bus.arm_cancel

    def first_then_one_in_flight() -> str:
        answer = str(cancel())
        if bus.cancels == 1:
            bus.in_flight(B.STARTING_RECANCEL_S / 2)
        return answer

    bus.arm_cancel = first_then_one_in_flight
    doc = bridge.stop()
    clock.t += 5.0
    bus.arm_joints()
    assert not bus.executing, "the trajectory that landed late was cancelled"
    assert "a trajectory that landed after the first cancel was cancelled" in doc["did"], doc
    assert doc["arm"]["read_still"] is True, doc["arm"]
    bridge.close()


def test_a_trajectory_landing_after_the_late_cancel_is_cancelled_inside_the_window() -> None:
    """A pick of the mission's that the stop ends just after it sent its trajectory leaves
    that trajectory to land later than the late cancel, which then found nothing and read the
    arm still. The bridge watches the arm for ARM_GUARD_S from the stop's last cancel: joints
    that start to move are cancelled again at the look that sees them, and no hold is claimed
    before the window has passed."""
    clock = Clock()
    bridge = B.fake_bridge(blueprint="moss-litter", fps=0, mission_stop_s=0.2)
    _all_on(clock, bridge)
    assert bridge.mission_doc()["answering"] is True
    bus = bridge.bus
    cancel = bus.arm_cancel

    def first_then_one_landing_late() -> str:
        answer = str(cancel())
        if bus.cancels == 1:
            bus.in_flight(B.ARM_GUARD_S / 2)  # long after the late cancel, inside the window
        return answer

    bus.arm_cancel = first_then_one_landing_late
    bus.start_mission_pick(startup_s=60.0)  # its script, its trajectory already sent
    doc = bridge.stop()
    assert doc["pick"]["terminated"], doc
    assert doc["arm"]["held"] is None and doc["arm"]["read_still"] is True, doc["arm"]
    assert bridge.arm_doc()["guard"]["watching"] is True
    word, executing = _watch_to_its_end(bridge, clock)
    assert "a trajectory in flight lands" in bus.events, "it landed inside the window"
    assert not any(executing), "and was cancelled at the look that saw it"
    assert word["moved"] and word["cancels"] >= 1 and word["held"] is True, word
    bridge.close()


def test_a_pick_script_that_outlives_its_sigterm_is_killed_after_its_grace() -> None:
    bridge, clock = _litter_on_fake_time()
    bus = bridge.bus
    pid = bus.start_mission_pick(startup_s=60.0, ignores_term=True)
    doc = bridge.stop()
    assert doc["pick"] == {"terminated": [], "killed": [pid], "left": []}, doc
    signals = [e for e in bus.events if e.startswith("pick script")]
    assert signals == ["pick script SIGTERM", "pick script SIGKILL"]
    assert clock.t >= B.SCRIPT_GRACE_S, "it had its grace before the kill"
    bridge.close()


def test_a_pick_that_outlives_both_signals_is_said_and_the_arm_is_still_cancelled() -> None:
    class Stubborn:
        def __init__(self) -> None:
            self.sent: list[int] = []

        def picks(self, stack: Any) -> list[int]:
            return [4321]

        def descendants(self, pid: int) -> list[int]:
            return []

        def alive(self, pid: int) -> bool:
            return True

        def signal(self, pid: int, sig: int) -> None:
            self.sent.append(sig)

    bridge, _ = _litter_on_fake_time()
    bridge.procs = Stubborn()
    doc = bridge.stop()
    assert doc["pick"]["left"] == [4321] and doc["late"]["pick"]["left"] == [4321]
    assert len(bridge.procs.sent) == 4, "both signals, and both again after the mission's stop"
    outlived = [e for e in doc["errors"] if "outlived SIGKILL" in e]
    assert not doc["ok"] and len(outlived) == 1, doc["errors"]
    assert "arm cancel" in bridge.bus.events, "the cancel goes out all the same"
    bridge.close()


def test_a_cancel_never_says_the_arm_holds_without_reading_it() -> None:
    """ALREADY_STOPPED says nothing executed at that instant. A hold is a reading, and an arm
    whose joints did not answer after the cancel is unread."""
    bridge = B.fake_bridge(fps=0)

    def silent() -> dict[str, float]:
        raise TimeoutError("the coordinator stopped answering its reads")

    bridge.bus.arm_joints = silent
    doc = bridge.stop()
    assert doc["arm"]["cancel"] == "ALREADY_STOPPED" and doc["arm"]["held"] is None, doc
    bridge.close()


def _proc(root: Path, pid: int, ppid: int, *argv: str, state: str = "S") -> None:
    d = root / str(pid)
    d.mkdir()
    (d / "stat").write_text(f"{pid} (py (x) y) {state} {ppid} 1 1 0 -1 4194560", encoding="utf-8")
    (d / "cmdline").write_bytes(b"".join(a.encode() + b"\0" for a in argv))


def test_the_process_table_finds_only_the_missions_own_picks(tmp_path: Path) -> None:
    """Among the stack's own descendants, the maker's script, or the stack's own MOSS_PICK_CMD,
    and nothing else: never the stack, its workers, a script outside it, or a zombie."""
    _proc(tmp_path, 100, 1, "/home/m/moss/.venv/bin/python", "/home/m/moss/.venv/bin/dimos", "run")
    _proc(tmp_path, 101, 100, "/home/m/moss/.venv/bin/python", "-c", "worker")
    _proc(
        tmp_path, 102, 101, "/home/m/moss/.venv/bin/python", "/home/m/moss/tools/moss_arm_play.py"
    )
    _proc(tmp_path, 103, 1, "python", "/home/m/moss/tools/moss_arm_play.py")
    _proc(tmp_path, 104, 101, state="Z")
    _proc(tmp_path, 105, 101, "/opt/pick", "--fast", "--sim")
    table = B.ProcTable(str(tmp_path))
    stack = B.StackInfo(running=True, blueprint="moss-litter", pid=100, run_id="r", env={})
    assert table.picks(stack) == [102]
    custom = B.StackInfo(
        running=True,
        blueprint="moss-litter",
        pid=100,
        run_id="r",
        env={"MOSS_PICK_CMD": "/opt/pick --fast"},
    )
    assert sorted(table.picks(custom)) == [102, 105]
    assert table.alive(102) and not table.alive(104) and not table.alive(999)
    assert table.picks(B.StackInfo(running=False)) == []


def test_the_arm_cancel_route_cancels_its_own_cycle_and_nothing_else() -> None:
    """quackd's close names the cycle it started. Another id, or a cycle that has ended, is left
    alone, so a close never cancels a trajectory it did not send, and it sends nothing else."""
    bridge = B.fake_bridge(cycle_s=30.0, fps=0)
    bus = bridge.bus
    code, started = bridge.arm_cycle({})
    assert code == 200, started
    _wait(lambda: bus.executing)
    assert bridge.arm_cancel({})[0] == 400
    code, doc = bridge.arm_cancel({"id": "a-cycle-of-another-session"})
    assert code == 200 and doc["cancelled"] is False and bus.executing
    assert "arm cancel" not in bus.events
    code, doc = bridge.arm_cancel({"id": started["id"]})
    assert code == 200 and doc["ok"] and doc["cancelled"] is True, doc
    assert doc["arm"]["read_still"] is True and not bus.executing
    _wait(lambda: bridge.jobs.doc()["state"] == "cancelled")
    code, doc = bridge.arm_cancel({"id": started["id"]})
    assert doc["cancelled"] is False, "a cycle that has ended is not cancelled again"
    assert bus.events.count("arm cancel") == 2, "its cancel, and the one that found it stopped"
    assert not [e for e in bus.events if e.startswith(("twist", "true"))], "nothing else went out"
    bridge.close()


def test_a_mission_still_answering_after_its_stop_is_a_failure_and_the_rest_still_runs() -> None:
    clock = Clock()
    bridge = B.fake_bridge(blueprint="moss-litter", fps=0, mission_ignores_stop=True)
    _fake_time(bridge, clock)
    bus = bridge.bus
    bus.executing = True
    doc = bridge.stop()
    assert not doc["ok"] and doc["mission"] == "unconfirmed"
    assert any("still answers" in e for e in doc["errors"])
    assert "arm cancel" in bus.events and any(e.startswith("twist") for e in bus.events)
    assert clock.t >= B.MISSION_CONFIRM_S, "it waited the whole confirm, and no longer"
    assert clock.t <= B.MISSION_CONFIRM_S + 1.0
    bridge.close()


def test_a_mission_never_heard_cannot_have_its_stop_confirmed_by_silence() -> None:
    """A mission whose RPC never answered is as silent after a stop as one that obeyed it."""
    clock = Clock()
    bridge = B.fake_bridge(blueprint="moss-litter", fps=0, mission=False)
    _fake_time(bridge, clock)
    bus = bridge.bus
    bus.executing = True
    doc = bridge.stop()
    assert not doc["ok"] and doc["mission"] == "unconfirmed", doc
    assert any("cannot confirm its stop" in e for e in doc["errors"])
    assert "arm cancel" in bus.events and any(e.startswith("twist") for e in bus.events)
    assert bridge.mission_stopped_run is None, "an unconfirmed stop is not remembered"
    bridge.close()


@pytest.mark.parametrize("heard_first", [True, False])
def test_silence_confirms_a_stop_only_from_a_mission_heard_before_it(heard_first: bool) -> None:
    """A mission that has closed its RPC service before the first probe after its stop: heard
    before, that silence is its stop, and never heard, it is nothing."""
    clock = Clock()
    bridge = B.fake_bridge(blueprint="moss-litter", fps=0, mission_stop_s=0.0)
    _fake_time(bridge, clock)
    if heard_first:
        assert bridge.mission_doc()["answering"] is True
    doc = bridge.stop()
    assert doc["mission"] == ("stopped" if heard_first else "unconfirmed"), doc
    assert bridge.bus.mission_stops == 1, "the stop goes out either way"
    bridge.close()


def test_a_stop_with_no_bus_says_so_and_never_raises() -> None:
    bridge = B.Bridge(
        stack=lambda: B.StackInfo(running=False), up=None, bus=None, bus_error="no lcm"
    )
    doc = bridge.stop()
    assert doc["ok"] is False and "no lcm" in doc["errors"][0]


# ── a stack that starts after the bridge ────────────────────────────────────────────────


class _Stacks:
    """The rover's runs as the bridge reads them, changed by the test."""

    def __init__(self, info: Any) -> None:
        self.info = info

    def __call__(self) -> Any:
        return self.info


def _running(run_id: str, **env: str) -> Any:
    return B.StackInfo(
        running=True,
        blueprint="moss-explore",
        pid=os.getpid(),
        run_id=run_id,
        env={"DIMOS_TRANSPORT": "lcm", **env},
    )


@pytest.mark.parametrize("track_m", [0.25, 0.4])
def test_a_stack_started_after_the_bridge_is_driven_with_its_own_track(track_m: float) -> None:
    """The service starts at boot, before any stack, so what it read then is nobody's. The arc
    clip is sound only with the track width MOSS's base adapter itself uses."""
    stacks = _Stacks(B.StackInfo(running=False))
    reads: list[str | None] = []

    def reread(stack: Any) -> tuple[Any, str | None]:
        reads.append(stack.run_id)
        return _up(track_m=track_m), None

    bus = B.FakeBus(B.FAKE_UPSTREAM, B.FrameStore(), fps=0)
    bridge = B.Bridge(stack=stacks, up=B.FAKE_UPSTREAM, bus=bus, reread=reread)
    stacks.info = _running("run-1")
    vx = 0.1
    code, doc = bridge.twist({"vx": vx, "wz": 5.0})
    assert code == 200, doc
    assert doc["sent"]["wz"] == pytest.approx(2 * vx / track_m)
    assert bridge.hello()["track_m"] == track_m
    assert reads == ["run-1"], "read once per run, not once per request"
    stacks.info = _running("run-2")
    bridge.hello()
    assert reads == ["run-1", "run-2"]
    bridge.close()


def test_a_bridge_started_with_no_bus_builds_one_when_a_stack_it_can_reach_starts() -> None:
    """Started at boot beside a stack on another bus, or none, the bridge has no bus to
    publish to. When an LCM stack on its own address starts, it opens one, and drives that
    stack, as its page says it does."""
    stacks = _Stacks(B.StackInfo(running=False))
    built: list[Any] = []

    def make_bus(up: Any) -> Any:
        bus = B.FakeBus(up, B.FrameStore(), fps=0)
        built.append(bus)
        return bus

    bridge = B.Bridge(
        stack=stacks,
        up=None,
        bus=None,
        up_error="no stack ran when the bridge started",
        make_bus=make_bus,
        reread=lambda stack: (B.FAKE_UPSTREAM, None),
    )
    assert bridge.twist({"vx": 0.1})[0] == 503
    stacks.info = _running("run-1")
    code, doc = bridge.twist({"vx": 0.1, "wz": 0.0})
    assert code == 200, doc
    assert len(built) == 1 and built[0].published, "the new bus carried the twist"
    assert bridge.stop()["ok"]
    bridge.twist({"vx": 0.1, "wz": 0.0})
    assert len(built) == 1, "one bus, built once"
    bridge.close()


def test_the_numbers_are_read_again_when_the_arm_run_changes() -> None:
    """The drive pose is the arm run's, and moss-arm starts after the base stack, so a new arm
    run is read again as a new base run is."""
    arm = B.Run(pid=os.getpid(), blueprint="moss-arm", run_id="arm-1", started=0.0)
    stacks = _Stacks(B.StackInfo(running=False))
    reads: list[Any] = []

    def reread(stack: Any) -> tuple[Any, str | None]:
        reads.append((stack.run_id, stack.arm_run.run_id if stack.arm_run else None))
        return B.FAKE_UPSTREAM, None

    bus = B.FakeBus(B.FAKE_UPSTREAM, B.FrameStore(), fps=0)
    bridge = B.Bridge(stack=stacks, up=B.FAKE_UPSTREAM, bus=bus, reread=reread)
    stacks.info = _running("run-1")
    bridge.hello()
    stacks.info = replace(stacks.info, arm_run=arm, arm_env={})
    bridge.hello()
    stacks.info = replace(stacks.info, arm_run=replace(arm, run_id="arm-2"))
    bridge.hello()
    bridge.hello()
    assert reads == [("run-1", None), ("run-1", "arm-1"), ("run-1", "arm-2")]
    bridge.close()


@pytest.mark.parametrize(
    "refusal",
    ["the stack runs on zenoh, and this bridge speaks LCM only", "another address, say"],
)
def test_a_stack_the_bridge_cannot_publish_to_refuses_the_twist_and_the_arm(refusal: str) -> None:
    stacks = _Stacks(B.StackInfo(running=False))
    bus = B.FakeBus(B.FAKE_UPSTREAM, B.FrameStore(), fps=0)
    bridge = B.Bridge(
        stack=stacks,
        up=B.FAKE_UPSTREAM,
        bus=bus,
        arm_argv=lambda pose, phase: [sys.executable, "-c", "pass"],
        reread=lambda stack: (None, refusal),
    )
    stacks.info = _running("run-1")
    code, doc = bridge.twist({"vx": 0.1})
    assert code == 503 and doc["reason"] == refusal
    code, doc = bridge.arm_cycle({})
    assert code == 503 and doc["reason"] == refusal
    assert bridge.hello()["publisher"] == {"loaded": False, "error": refusal, "channel": None}
    assert bus.published == [], "nothing went out to a stack it cannot reach"
    assert "zero twist on /tele_cmd_vel#geometry_msgs.Twist" in bridge.stop()["did"]
    bridge.close()


@pytest.mark.parametrize(
    ("env", "own_url", "said"),
    [
        ({"DIMOS_TRANSPORT": "zenoh"}, "", "speaks LCM only"),
        ({"TRANSPORT": "ros2"}, "", "speaks LCM only"),
        ({"LCM_DEFAULT_URL": "udpm://239.255.10.1:7001?ttl=0"}, "", "LCM_DEFAULT_URL"),
        ({}, "udpm://239.255.10.2:7002?ttl=0", "LCM_DEFAULT_URL"),
        (
            {"LCM_DEFAULT_URL": "udpm://239.255.10.2:7002?ttl=0"},
            "udpm://239.255.10.2:7002?ttl=0",
            None,
        ),
        ({}, "", None),
    ],
)
def test_the_bus_rule_refuses_another_bus_and_another_address(
    env: dict[str, str], own_url: str, said: str | None
) -> None:
    stack = B.StackInfo(running=True, blueprint="moss-explore", run_id="r", env=env)
    refusal = B.bus_refusal(stack, own_url)
    if said is None:
        assert refusal is None
    else:
        assert refusal is not None and said in refusal


# ── the session ─────────────────────────────────────────────────────────────────────────


def _beat(bridge: Any, session: str, period: float = 0.1, started: str | None = None) -> Any:
    code, doc = bridge.keepalive({"session": session, "keepalive_s": period, "started": started})
    assert code == 200, doc
    return doc


@pytest.mark.parametrize("period", [0.05, 0.1])
def test_three_missed_keepalives_halt_a_started_exploration(period: float) -> None:
    clock = Clock()
    bridge = B.fake_bridge(fps=0)
    bridge.clock = clock
    bus = bridge.bus
    _beat(bridge, "run", period, "explore")
    clock.t += B.SESSION_MISSES * period * 0.9
    assert bridge.session_tick() == []
    clock.t += B.SESSION_MISSES * period * 0.2
    docs = bridge.session_tick()
    assert len(docs) == 1 and docs[0]["ok"]
    assert ("true /stop_explore_cmd#std_msgs.Bool") in bus.events
    assert ("true /stop_movement#std_msgs.Bool") in bus.events
    assert bridge.halts == 1 and bridge.sessions == {}
    bridge.close()


def test_a_session_that_started_nothing_halts_nothing_of_the_owners() -> None:
    clock = Clock()
    bridge = B.fake_bridge(fps=0)
    bridge.clock = clock
    _beat(bridge, "probe")
    clock.t += 1.0
    assert len(bridge.session_tick()) == 1
    assert bridge.bus.events == [], "no zero twist: that would cancel the owner's own goal"
    bridge.close()


def _registered(root: Path, name: str, blueprint: str, run_id: str, started: str) -> None:
    """One run in dimOS's own registry, as `dimos run` writes it."""
    named = f"moss-dimos.{blueprint}" if blueprint.startswith("moss") else blueprint
    entry = {"pid": os.getpid(), "blueprint": named, "run_id": run_id, "started_at": started}
    (root / f"{name}.json").write_text(json.dumps(entry), encoding="utf-8")


@pytest.fixture
def two_runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The run quackd connected to, and a second dimOS run started by hand after it."""
    monkeypatch.setattr(B, "proc_environ", lambda pid: {"DIMOS_TRANSPORT": "lcm"})
    monkeypatch.setattr(B, "proc_cmdline", lambda pid: ())
    _registered(tmp_path, "a", "moss-explore", "run-explore", "2026-10-21T10:00:00Z")
    _registered(tmp_path, "b", "unitree-go2-sim", "run-other", "2026-10-21T10:05:00Z")
    return tmp_path


def test_a_stop_reaches_the_run_quackd_connected_to_while_it_runs_beside_a_newer_one(
    two_runs: Path,
) -> None:
    """A second dimOS run started by hand, of any blueprint, is the newest, and the run quackd
    connected to still runs behind it: quackd's stop is for that run and reaches it. Once that
    run has ended, the stop touches nothing of the other."""
    bus = B.FakeBus(B.FAKE_UPSTREAM, B.FrameStore(), fps=0)
    bridge = B.Bridge(
        stack=lambda: B.read_stack(str(two_runs)), up=B.FAKE_UPSTREAM, bus=bus, procs=bus
    )
    assert bridge.stack().run_id == "run-other", "the newest run is the other one"
    doc = bridge.stop("run-explore", "s1")
    assert not doc.get("run_changed"), doc
    zeros = [chan for _, chan, v in bus.published if v == (0.0, 0.0)]
    assert "/tele_cmd_vel#geometry_msgs.Twist" in zeros, "the explore run's own channel"
    assert any("still runs beside the newer run" in d for d in doc["did"]), doc["did"]
    code, said = bridge.keepalive({"session": "s1", "keepalive_s": 1.0, "run": "run-explore"})
    assert code == 200 and said["session"]["run_alive"] is True
    (two_runs / "a.json").unlink()  # the explore run ended
    published = len(bus.published)
    gone = bridge.stop("run-explore", "s1")
    assert gone["run_changed"] and len(bus.published) == published, gone
    code, said = bridge.keepalive({"session": "s1", "keepalive_s": 1.0, "run": "run-explore"})
    assert said["session"]["run_alive"] is False
    bridge.close()


def test_a_session_whose_run_runs_beside_a_newer_one_has_its_exploration_halted(
    two_runs: Path,
) -> None:
    clock = Clock()
    bus = B.FakeBus(B.FAKE_UPSTREAM, B.FrameStore(), fps=0)
    bridge = B.Bridge(
        stack=lambda: B.read_stack(str(two_runs)), up=B.FAKE_UPSTREAM, bus=bus, procs=bus
    )
    bridge.clock = clock
    code, _ = bridge.keepalive(
        {"session": "s1", "keepalive_s": 0.1, "started": "explore", "run": "run-explore"}
    )
    assert code == 200
    clock.t += 1.0
    docs = bridge.session_tick()
    assert docs and docs[0]["halted"] == ["its exploration"], docs
    assert "true /stop_explore_cmd#std_msgs.Bool" in bus.events
    bridge.close()


def test_a_dropped_session_of_a_run_that_has_gone_halts_nothing_of_the_new_one() -> None:
    """A session kept on a run the owner has since restarted: when it drops, what it
    started went with the old run, and nothing of the new one is halted for it."""
    clock = Clock()
    bridge = B.fake_bridge(fps=0)
    bridge.clock = clock
    code, _ = bridge.keepalive(
        {"session": "old", "keepalive_s": 0.1, "started": "explore", "run": "the-run-before"}
    )
    assert code == 200
    clock.t += 1.0
    docs = bridge.session_tick()
    assert len(docs) == 1 and docs[0]["ok"], docs
    assert bridge.bus.events == [] and bridge.bus.published == [], "the new run untouched"
    assert any("nothing of run fake-run is halted" in d for d in docs[0]["did"]), docs
    bridge.close()


def test_a_halted_session_hears_what_was_halted_in_every_keepalive_after() -> None:
    """The client learns of its halt from the next keepalive answer: what was halted, or that
    nothing was, which a session that started nothing gets. Only a halt that halted something
    is counted, and the record goes with the session's end."""
    clock = Clock()
    bridge = B.fake_bridge(fps=0)
    bridge.clock = clock
    _beat(bridge, "run", 0.1, "explore")
    clock.t += 1.0
    bridge.session_tick()
    said = _beat(bridge, "run", 0.1, "explore")["session"]["halted"]
    assert said["halted"] == ["its exploration"] and "missed" in said["why"], said
    assert bridge.halts == 1 and bridge.expired == 1
    bridge.keepalive({"session": "run", "end": True})
    assert "run" not in bridge.halted, "the record goes with the session's end"
    _beat(bridge, "probe", 0.1, None)
    clock.t += 1.0
    bridge.session_tick()
    assert _beat(bridge, "probe")["session"]["halted"]["halted"] == []
    assert bridge.halts == 1 and bridge.expired == 2
    bridge.close()


def test_a_long_halt_never_holds_back_another_sessions_expiry() -> None:
    """The bridge's own watch halts each expired session in a thread of its own: a litter
    mission's whole stop, slow to confirm, never delays the end of another session that
    expired after it."""
    bridge = B.fake_bridge(blueprint="moss-litter", fps=0, mission_ignores_stop=True)
    assert bridge.mission_doc()["answering"] is True
    bridge.start()
    try:
        _beat(bridge, "collector", 0.1, "litter")
        _wait(lambda: "mission stop" in bridge.bus.events, 5.0)
        _beat(bridge, "watcher", 0.1, None)
        began = time.monotonic()
        _wait(lambda: "watcher" in bridge.halted, B.MISSION_CONFIRM_S / 2)
        assert time.monotonic() - began < B.MISSION_CONFIRM_S / 2
        assert "collector" not in bridge.halted, "while the litter halt still runs"
    finally:
        bridge.close()


def test_a_session_ended_by_its_client_halts_nothing() -> None:
    clock = Clock()
    bridge = B.fake_bridge(fps=0)
    bridge.clock = clock
    _beat(bridge, "run", started="goal")
    bridge.keepalive({"session": "run", "end": True})
    clock.t += 5.0
    assert bridge.session_tick() == [] and bridge.bus.events == []
    bridge.close()


def test_a_keepalive_names_its_session() -> None:
    bridge = B.fake_bridge(fps=0)
    for body in ({"keepalive_s": 0.5}, {"session": "", "keepalive_s": 0.5}, {"session": 7}):
        code, doc = bridge.keepalive(body)
        assert code == 400 and "names its session" in doc["reason"], body
    assert bridge.sessions == {}
    bridge.close()


def test_one_sessions_keepalive_or_end_never_touches_another() -> None:
    """A doctor beside a run, or a second quackd that started nothing: neither can strip the
    run's deadman, by overwriting what it started or by ending it."""
    clock = Clock()
    bridge = B.fake_bridge(fps=0)
    bridge.clock = clock
    _beat(bridge, "run", 0.1, "explore")
    _beat(bridge, "doctor", 0.1, None)
    bridge.keepalive({"session": "doctor", "end": True})
    assert bridge.sessions["run"].started == "explore"
    for _ in range(10):  # the second quackd beats on, having started nothing
        clock.t += 0.1
        _beat(bridge, "second", 0.1, None)
        bridge.session_tick()
    assert bridge.halts == 1, "the run that went quiet was halted, the one beating was not"
    assert "true /stop_explore_cmd#std_msgs.Bool" in bridge.bus.events
    assert set(bridge.sessions) == {"second"}
    bridge.close()


@pytest.mark.parametrize("declared", [2.5, 30.0, float("inf"), float("nan")])
def test_a_declared_period_is_capped_so_the_window_stays_short(declared: float) -> None:
    clock = Clock()
    bridge = B.fake_bridge(fps=0)
    bridge.clock = clock
    doc = _beat(bridge, "slow", declared, "explore")
    assert doc["session"]["keepalive_s"] == B.MAX_KEEPALIVE_S
    clock.t += B.SESSION_MISSES * B.MAX_KEEPALIVE_S + 0.1
    assert len(bridge.session_tick()) == 1, "halted three capped periods after its last word"
    bridge.close()


def test_a_dropped_session_halts_only_the_cycle_that_session_started() -> None:
    clock = Clock()
    bridge = B.fake_bridge(cycle_s=30.0, fps=0)
    bridge.clock = clock
    bus = bridge.bus
    code, doc = bridge.arm_cycle({"pick_pose": "pick", "session": "theirs"})
    assert code == 200, doc
    _wait(lambda: bus.executing)
    _beat(bridge, "mine", 0.1, None)
    clock.t += 1.0
    assert len(bridge.session_tick()) == 1
    assert "arm cancel" not in bus.events and bus.executing, "another session's cycle plays on"
    _beat(bridge, "theirs", 0.1, None)
    clock.t += 1.0
    assert len(bridge.session_tick()) == 1
    assert "arm cancel" in bus.events and not bus.executing
    bridge.close()


def _fake_time(bridge: Any, clock: Clock) -> None:
    """The bridge, and the fake bus behind it, on the test's clock, which a sleep moves."""
    bridge.clock = clock
    bridge.bus.clock = clock
    bridge.sleep = lambda s: setattr(clock, "t", clock.t + s)


def test_a_dropped_session_that_started_the_mission_is_the_whole_stop() -> None:
    clock = Clock()
    bridge = B.fake_bridge(blueprint="moss-litter", fps=0, mission_stop_s=0.2)
    _fake_time(bridge, clock)
    _beat(bridge, "collector", 0.1, "litter")
    clock.t += 1.0
    docs = bridge.session_tick()
    assert len(docs) == 1 and docs[0]["mission"] == "stopped", docs
    assert bridge.bus.events[0] == "mission stop"
    bridge.close()


def test_on_moss_litter_a_dropped_session_that_only_watched_leaves_the_mission_running() -> None:
    """A run that only looked, or a probe, started nothing: when its link drops, the owner's
    mission goes on. Only an explicit stop, the end of every run among them, ends it."""
    clock = Clock()
    bridge = B.fake_bridge(blueprint="moss-litter", fps=0, mission_stop_s=0.2)
    _fake_time(bridge, clock)
    _beat(bridge, "watcher", 0.1, None)
    clock.t += 1.0
    assert len(bridge.session_tick()) == 1 and bridge.expired == 1
    assert bridge.halts == 0, "it halted nothing, so no halt is counted"
    assert "mission stop" not in bridge.bus.events and bridge.bus.mission_stops == 0
    assert bridge.mission_doc()["answering"] is True
    bridge.close()


# ── over HTTP ───────────────────────────────────────────────────────────────────────────


@pytest.fixture
def served() -> Any:
    bridge = B.fake_bridge(token="s3cret", fps=10.0, cycle_s=0.3)
    server = B.serve(bridge, "127.0.0.1", 0)
    bridge.start()
    try:
        yield bridge, int(server.server_address[1])
    finally:
        server.shutdown()
        server.server_close()
        bridge.close()


def _ask(port: int, method: str, path: str, body: Any = None, token: str | None = "s3cret") -> Any:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers[B.TOKEN_HEADER] = token
    data = (
        None if body is None else (body if isinstance(body, bytes) else json.dumps(body).encode())
    )
    conn.request(method, path, body=data, headers=headers)
    resp = conn.getresponse()
    raw = resp.read()
    conn.close()
    kind = resp.getheader("Content-Type", "")
    return resp.status, (json.loads(raw) if kind.startswith("application/json") else raw)


def test_every_route_wants_the_token(served: Any) -> None:
    _, port = served
    for method, path in (("GET", "/hello"), ("POST", "/stop"), ("GET", "/frame?cam=nose")):
        status, doc = _ask(port, method, path, {} if method == "POST" else None, token=None)
        assert status == 401 and doc["reason"] == "bad or missing token"
        assert _ask(port, method, path, {} if method == "POST" else None, token="nope")[0] == 401


PY = "/home/rover/moss/.venv/bin/python"
FORKSERVER = (PY, "-c", "from multiprocessing.forkserver import main; main(9, 10, [])")


class _Tree(B.ProcTable):
    """A /proc tree written in tmp_path, in dimOS's own shape at the pin: the registered pid,
    its forkserver, a worker forked from it, and the mission's pick under the worker. A signal
    is recorded and ends the process in the tree, and never reaches one of this machine's."""

    def __init__(self, root: Path, pick: tuple[str, ...]) -> None:
        super().__init__(str(root))
        self.sent: list[tuple[int, int]] = []
        self.add(100, 1, (PY, "/home/rover/moss/.venv/bin/dimos", "run", "moss-dimos.moss-litter"))
        self.add(101, 100, FORKSERVER)
        self.add(102, 101, FORKSERVER)
        self.add(103, 102, pick)
        self.add(200, 1, (PY, "/home/rover/moss/tools/moss_arm_play.py", "--sim"))  # the owner's

    def add(self, pid: int, ppid: int, argv: tuple[str, ...]) -> None:
        d = Path(self.root, str(pid))
        d.mkdir()
        (d / "stat").write_text(f"{pid} (python3) S {ppid} {pid} {pid} 0 -1", encoding="utf-8")
        (d / "cmdline").write_bytes(b"\0".join(a.encode() for a in argv) + b"\0")

    def signal(self, pid: int, sig: int) -> None:
        self.sent.append((pid, sig))
        stat = Path(self.root, str(pid), "stat")
        stat.write_text(stat.read_text(encoding="utf-8").replace(") S ", ") Z "), encoding="utf-8")


def _litter_with(tree: _Tree, pick_cmd: str | None) -> Any:
    env = {"DIMOS_TRANSPORT": "lcm"} | ({B.PICK_CMD_ENV: pick_cmd} if pick_cmd else {})
    stack = B.StackInfo(running=True, blueprint="moss-litter", pid=100, run_id="r", env=env)
    bus = B.FakeBus(B.FAKE_UPSTREAM, B.FrameStore(), fps=0)
    return B.Bridge(stack=lambda: stack, up=B.FAKE_UPSTREAM, bus=bus, procs=tree), stack


def test_the_missions_pick_is_ended_with_the_processes_under_it(tmp_path: Path) -> None:
    """A MOSS_PICK_CMD that is a shell line runs the pick under the shell, whose command line
    alone names it: the shell's child is ended with it, and nothing outside the pick, the
    stack's workers and the owner's own run of the script among them."""
    tree = _Tree(tmp_path, ("/bin/sh", "-c", "exec-pick", "--sim"))
    tree.add(104, 103, (PY, "-m", "moss_pick"))
    bridge, stack = _litter_with(tree, "/bin/sh -c exec-pick")
    did: list[str] = []
    errors: list[str] = []
    ended = bridge._end_mission_pick(stack, did, errors)
    assert sorted({pid for pid, _ in tree.sent}) == [103, 104], tree.sent
    assert ended is not None and sorted(ended["terminated"]) == [103, 104], ended
    assert any("under the mission's pick: pid 104" in d for d in did), did
    assert errors == [], errors
    bridge.close()


def test_a_pick_command_of_one_argument_is_refused_at_hello_and_at_the_stop(
    tmp_path: Path,
) -> None:
    """MOSS_PICK_CMD as the interpreter alone matches the stack's own forkserver and worker,
    which run it too, and ending those ends the stack. So it is refused, picks are looked for
    by the maker's script alone, and the hello and the stop both say why."""
    tree = _Tree(tmp_path, (PY, "-u", "/opt/picks/pick.py"))
    bridge, stack = _litter_with(tree, PY)
    assert tree.picks(stack) == [], "the stack's own workers are no picks"
    said = bridge.hello()["mission"]["pick_cmd_refused"]
    assert said and "one argument" in said and B.PICK_SCRIPT in said, said
    did: list[str] = []
    errors: list[str] = []
    assert bridge._end_mission_pick(stack, did, errors) is None
    assert tree.sent == [] and errors == [said], (tree.sent, errors)
    doc = bridge.stop()
    assert not doc["ok"] and said in doc["errors"] and tree.sent == [], doc
    assert doc["errors"].count(said) == 1, "both looks met it, and it is said once"
    bridge.close()


def test_a_token_refusal_reaches_a_client_whose_body_comes_after_its_headers(
    served: Any,
) -> None:
    """A POST refused for its token has its body read and dropped before the refusal goes, as
    one refused for its length does: a close with the body unread, or still to come, resets
    the connection, and the refusal is lost to a client that reads it a moment later."""
    _, port = served
    body = b'{"session": "s1"}'
    head = (
        f"POST {B.STOP_PATH} HTTP/1.1\r\nHost: 127.0.0.1\r\n{B.TOKEN_HEADER}: nope\r\n"
        f"Content-Type: application/json\r\nContent-Length: {len(body)}\r\n\r\n"
    ).encode()
    got = b""
    with socket.create_connection(("127.0.0.1", port), timeout=10) as s:
        s.sendall(head)
        time.sleep(0.2)  # the body after its headers, as a client's two sends may land
        with contextlib.suppress(OSError):
            s.sendall(body)
        time.sleep(0.2)  # and the answer read a moment later
        with contextlib.suppress(OSError):
            while chunk := s.recv(65536):
                got += chunk
    status = got.split(b"\r\n", 1)[0]
    assert b" 401 " in status and b"bad or missing token" in got, got[:120]


def test_refusals_are_json_with_a_reason(served: Any) -> None:
    _, port = served
    assert _ask(port, "GET", "/nowhere")[0] == 404
    status, doc = _ask(port, "GET", "/stop")
    assert status == 405 and "answers POST" in doc["reason"]
    status, doc = _ask(port, "POST", "/twist", b"x" * (B.MAX_BODY_BYTES + 1))
    assert status == 413
    status, doc = _ask(port, "POST", "/twist", b"[1, 2]")
    assert status == 400 and "JSON object" in doc["reason"]
    status, doc = _ask(port, "POST", "/twist", {"vx": "fast"})
    assert status == 400


def test_the_protocol_end_to_end_over_loopback(served: Any) -> None:
    _, port = served
    status, hello = _ask(port, "GET", "/hello")
    assert status == 200 and hello["protocol"] == B.PROTOCOL
    status, doc = _ask(port, "POST", "/twist", {"vx": 0.05, "wz": 0.4})
    assert status == 200 and doc["clipped"]["wz_sent"] < 0.4
    status, frame = _ask(port, "GET", "/frame?cam=nose")
    assert status == 200 and frame[:2] == b"\xff\xd8"
    time.sleep(B.FAKE_UPSTREAM.deadman_s + 0.1)
    status, doc = _ask(port, "POST", "/arm/cycle", {"pick_pose": "pick"})
    assert status == 200
    _wait(lambda: _ask(port, "GET", "/arm")[1]["job"]["state"] == "done")
    status, doc = _ask(port, "POST", "/stop", {})
    assert status == 200 and doc["ok"]
    beat = {"session": "s1", "keepalive_s": 0.5, "started": "goal"}
    status, doc = _ask(port, "POST", "/session/keepalive", beat)
    assert status == 200 and doc["session"]["started"] == "goal" and doc["stack"]["running"]
    status, doc = _ask(port, "POST", "/session/keepalive", {"session": "s1", "end": True})
    assert status == 200 and doc["session"] is None and doc["sessions"]["live"] == 0
    status, doc = _ask(port, "POST", "/session/keepalive", {"keepalive_s": 0.5})
    assert status == 400 and "names its session" in doc["reason"]


def test_the_pump_runs_on_its_own_thread_at_the_missions_rate(served: Any) -> None:
    bridge, port = served
    _ask(port, "POST", "/twist", {"vx": 0.1, "wz": 0.0})
    time.sleep(B.FAKE_UPSTREAM.deadman_s + 0.3)
    twists = [v for _, _, v in bridge.bus.published if len(v) == 2]
    assert twists[-1] == (0.0, 0.0), "the deadman's one zero, sent by the pump on its own"
    count = len(twists)
    time.sleep(0.3)
    assert len([v for _, _, v in bridge.bus.published if len(v) == 2]) == count
