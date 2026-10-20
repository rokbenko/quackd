"""quackd's MOSS bridge on its own: what it does on the rover, driven with no rover.

The bridge is loaded by path, the way the rover runs it, and driven through its pure parts with
a clock of the test's own wherever timing is the claim: the twist deadman and its silence, the
maker's pulses, a frame taken after a request. The arm cycle runs a real subprocess, the fake
script `--fake` uses in place of the maker's, with the same lines and exit codes. What none of
this can prove is anything about MOSS: nothing here has run on a rover.
"""

from __future__ import annotations

import ast
import http.client
import importlib.util
import itertools
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

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
    from dataclasses import replace

    return replace(B.FAKE_UPSTREAM, **kw)


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
    assert arm["script"] is True and arm["cancel"] == "CANCELLED" and arm["held"] is True
    _wait(lambda: bridge.arm_doc()["job"]["state"] == "cancelled")
    assert bridge.arm_doc()["job"]["held"] is True
    assert bus.events.index("arm cancel") < len(bus.events) - 1, "the zero twist came after"
    bridge.close()


def test_an_arm_that_moves_on_after_its_cancel_is_said_and_cancelled_again() -> None:
    bridge = B.fake_bridge(cycle_s=30.0, fps=0, arm_ignores_cancel=True)
    bridge.arm_cycle({})
    _wait(lambda: bridge.bus.executing)
    doc = bridge.stop()
    assert not doc["ok"] and any("still moved" in e for e in doc["errors"])
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
        bridge.keepalive({"keepalive_s": 0.5, "started": None})
    bridge.arm_cycle({})
    _wait(lambda: bus.executing)
    bridge.close()
    assert "arm cancel" in bus.events and not bus.executing


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


def test_on_moss_litter_the_mission_stops_first_then_the_arm_then_the_zero() -> None:
    bridge = B.fake_bridge(blueprint="moss-litter", fps=0, mission_stop_s=0.2)
    bus = bridge.bus
    bus.executing = True  # the mission's own pick is mid cycle
    doc = bridge.stop()
    assert doc["ok"] and doc["mission"] == "stopped", doc
    order = [e.split()[0] + " " + e.split()[1] for e in bus.events]
    assert order[:3] == ["mission stop", "arm cancel", "twist /tele_cmd_vel#geometry_msgs.Twist"]
    assert bridge.mission_doc()["stopped"] is True
    again = bridge.stop()
    assert again["ok"] and "the mission was already stopped" in again["did"]
    assert bus.mission_stops == 1, "stopped stays stopped: the stop is not sent twice"
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


@pytest.mark.parametrize("period", [0.05, 0.1])
def test_three_missed_heartbeats_halt_a_started_exploration(period: float) -> None:
    clock = Clock()
    bridge = B.fake_bridge(fps=0)
    bridge.clock = clock
    bus = bridge.bus
    bridge.keepalive({"keepalive_s": period, "started": "explore"})
    clock.t += B.SESSION_MISSES * period * 0.9
    assert bridge.session_tick() is None
    clock.t += B.SESSION_MISSES * period * 0.2
    doc = bridge.session_tick()
    assert doc is not None and doc["ok"]
    assert ("true /stop_explore_cmd#std_msgs.Bool") in bus.events
    assert ("true /stop_movement#std_msgs.Bool") in bus.events
    assert bridge.session.halts == 1 and bridge.session.last is None
    bridge.close()


def test_a_session_that_started_nothing_halts_nothing_of_the_owners() -> None:
    clock = Clock()
    bridge = B.fake_bridge(fps=0)
    bridge.clock = clock
    bridge.keepalive({"keepalive_s": 0.1, "started": None})
    clock.t += 1.0
    assert bridge.session_tick() is not None
    assert bridge.bus.events == [], "no zero twist: that would cancel the owner's own goal"
    bridge.close()


def test_a_session_ended_by_its_client_halts_nothing() -> None:
    clock = Clock()
    bridge = B.fake_bridge(fps=0)
    bridge.clock = clock
    bridge.keepalive({"keepalive_s": 0.1, "started": "goal"})
    bridge.keepalive({"end": True})
    clock.t += 5.0
    assert bridge.session_tick() is None and bridge.bus.events == []
    bridge.close()


def _fake_time(bridge: Any, clock: Clock) -> None:
    """The bridge, and the fake bus behind it, on the test's clock, which a sleep moves."""
    bridge.clock = clock
    bridge.bus.clock = clock
    bridge.sleep = lambda s: setattr(clock, "t", clock.t + s)


def test_a_dropped_session_on_moss_litter_is_the_whole_stop() -> None:
    clock = Clock()
    bridge = B.fake_bridge(blueprint="moss-litter", fps=0, mission_stop_s=0.2)
    _fake_time(bridge, clock)
    bridge.keepalive({"keepalive_s": 0.1, "started": "litter"})
    clock.t += 1.0
    doc = bridge.session_tick()
    assert doc is not None and doc["mission"] == "stopped", doc
    assert bridge.bus.events[0] == "mission stop"
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
    status, doc = _ask(port, "POST", "/session/keepalive", {"keepalive_s": 0.5, "started": "goal"})
    assert status == 200 and doc["session"]["started"] == "goal" and doc["stack"]["running"]
    status, doc = _ask(port, "POST", "/session/keepalive", {"end": True})
    assert status == 200 and doc["session"]["active"] is False


def test_the_pump_runs_on_its_own_thread_at_the_missions_rate(served: Any) -> None:
    bridge, port = served
    _ask(port, "POST", "/twist", {"vx": 0.1, "wz": 0.0})
    time.sleep(B.FAKE_UPSTREAM.deadman_s + 0.3)
    twists = [v for _, _, v in bridge.bus.published if len(v) == 2]
    assert twists[-1] == (0.0, 0.0), "the deadman's one zero, sent by the pump on its own"
    count = len(twists)
    time.sleep(0.3)
    assert len([v for _, _, v in bridge.bus.published if len(v) == 2]) == count
