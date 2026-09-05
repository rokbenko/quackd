"""The bridge daemon that runs on the duck's Pi, exercised with no Pi and no duck.

The daemon is stdlib plus numpy on purpose, so the parts that matter most (the deadman, the
clamps, the protocol, and the fact that nothing can reach torque) are all testable here. The
last test in this file drives the real daemon and the real client against each other over
loopback, which is the only way to know the two halves agree.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import ModuleType

import pytest

from quackd.adapters.open_duck import OpenDuckAdapter
from quackd.adapters.open_duck.bridge import PROTOCOL, PROTOCOL_VERSION, OpenDuckBridge
from quackd.duckfile.parser import parse_duck_text
from quackd.perception.color_blob import ColorBlobDetector
from quackd.safety import Executor, allow_all
from quackd.verbs.registry import registry_from_manifest

REPO = Path(__file__).resolve().parents[1]
DAEMON = REPO / "bridge" / "open_duck" / "quackd_duck_bridge.py"

DUCK = parse_duck_text(
    "---\nduck: 1\nname: t\ndescription: d\nrequires: [move]\nverbs:\n"
    "  allow: [move, gaze, say, quack, express, report_state, stop]\n"
    "success: [x]\n---\n# Task\nx\n"
)


def load_daemon() -> ModuleType:
    """It lives outside the package on purpose: quackd's core must never import it."""
    spec = importlib.util.spec_from_file_location("quackd_duck_bridge", DAEMON)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolves annotations through sys.modules
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def daemon() -> ModuleType:
    return load_daemon()


class Clock:
    def __init__(self) -> None:
        self.t = 100.0

    def __call__(self) -> float:
        return self.t


def hello(core, token: str | None = None) -> dict:
    params = {"protocol": PROTOCOL, "protocol_version": PROTOCOL_VERSION}
    if token is not None:
        params["token"] = token
    reply, _ = core.handle({"id": 1, "method": "duck.hello", "params": params}, authed=False)
    return reply


# ── the deadman, which is the whole safety story ────────────────────────────────────────


def test_the_deadman_zeroes_velocity_and_holds_the_head(daemon: ModuleType) -> None:
    clock = Clock()
    core = daemon.BridgeCore(now=clock, limits=daemon.Limits(head_enabled=True))
    hello(core)
    core.handle(
        {"method": "duck.command", "params": {"vx": 0.1, "vyaw": 0.4, "head": {"head_yaw": 0.2}}},
        authed=True,
    )
    fresh = core.command_for_tick()
    assert fresh.vx == pytest.approx(0.1) and fresh.vyaw == pytest.approx(0.4)
    # the head is slewed by the control loop now, so it needs ticks to get anywhere
    for _ in range(30):
        clock.t += 0.02
        fresh = core.command_for_tick()
    held = fresh.head
    assert held[2] == pytest.approx(0.2, abs=1e-6), "and it does arrive, given the time"

    clock.t += core.deadman_s + 0.01
    stale = core.command_for_tick()
    assert (stale.vx, stale.vy, stale.vyaw) == (0.0, 0.0, 0.0)
    assert stale.head == held, "the head holds; a neck that snaps is what upstream warns about"
    assert core.deadman_tripped


def test_the_deadman_is_evaluated_by_the_consumer_not_a_timer(daemon: ModuleType) -> None:
    """A wedged, starved or dead server thread must still leave a duck that stops, so the
    check has to live in the call the control loop makes, not in a thread of its own."""
    clock = Clock()
    core = daemon.BridgeCore(now=clock)
    hello(core)
    core.handle({"method": "duck.command", "params": {"vx": 0.15}}, authed=True)
    clock.t += 5.0  # nothing else ran at all in that time
    assert core.command_for_tick().vx == 0.0


def test_a_fallen_duck_stops_and_nothing_can_start_it_again(daemon: ModuleType) -> None:
    core = daemon.BridgeCore()
    hello(core)
    core.fallen = True
    core.handle({"method": "duck.command", "params": {"vx": 0.15}}, authed=True)
    assert core.command_for_tick().vx == 0.0
    assert core.health()["healthy"] is False
    assert "get-up policy" in core.health()["reason"]


# ── clamps: never trust the client ──────────────────────────────────────────────────────


def test_a_hostile_command_is_clamped_on_the_bridge_too(daemon: ModuleType) -> None:
    core = daemon.BridgeCore()
    hello(core)
    core.handle(
        {"method": "duck.command", "params": {"vx": 99.0, "vy": -99.0, "vyaw": 99.0}}, authed=True
    )
    snap = core.command_for_tick()
    assert snap.vx == pytest.approx(daemon.VX[1])
    assert snap.vy == pytest.approx(daemon.VY[0])
    assert snap.vyaw == pytest.approx(daemon.VYAW[1])


def test_the_head_is_pinned_to_neutral_unless_head_control_was_asked_for(
    daemon: ModuleType,
) -> None:
    core = daemon.BridgeCore()  # head_enabled defaults to False
    hello(core)
    core.handle({"method": "duck.command", "params": {"head": {"head_yaw": 0.5}}}, authed=True)
    assert core.command_for_tick().head == (0.0, 0.0, 0.0, 0.0)
    assert core.hello()["capabilities"]["head"] is False
    assert core.hello()["limits"]["head_yaw"] == [0.0, 0.0]


def test_head_control_is_clamped_inside_upstream_and_rate_limited(daemon: ModuleType) -> None:
    """The rate limit is per second, in the control loop. It used to be
    `HEAD_SLEW_RAD_S * deadman_s` applied once per received *message*, which is not a rate at
    all: one `gaze` sends exactly one message, so the head moved 0.3 rad and stopped short of
    a target the verb then reported as reached, while a 10 Hz sender got 3 rad/s on the joint
    the constant exists to protect."""
    clock = Clock()
    core = daemon.BridgeCore(now=clock, limits=daemon.Limits(head_enabled=True))
    hello(core)
    ceiling = daemon.HEAD_YAW[1] * daemon.HEAD_SAFETY
    assert ceiling < daemon.HEAD_YAW[1], "quackd stays inside upstream's own clamp"

    # one command, then let the loop run: the head arrives, which it never used to
    core.handle({"method": "duck.command", "params": {"head": {"head_yaw": 9.0}}}, authed=True)
    core.command_for_tick()  # the first tick only seeds the clock
    yaw = 0.0
    for _ in range(100):
        clock.t += 0.02
        yaw = core.command_for_tick().head[2]
    assert yaw == pytest.approx(ceiling), "one gaze must reach its target, not stop 0.3 rad in"

    # and no client can buy extra speed by sending more often
    fast_clock = Clock()
    fast = daemon.BridgeCore(now=fast_clock, limits=daemon.Limits(head_enabled=True))
    hello(fast)
    fast.command_for_tick()
    for _ in range(10):
        fast.handle({"method": "duck.command", "params": {"head": {"head_yaw": 9.0}}}, authed=True)
    fast_clock.t += 0.1
    assert fast.command_for_tick().head[2] == pytest.approx(daemon.HEAD_SLEW_RAD_S * 0.1, abs=1e-6)


def test_a_resumed_stall_does_not_become_one_catch_up_leap(daemon: ModuleType) -> None:
    clock = Clock()
    core = daemon.BridgeCore(now=clock, limits=daemon.Limits(head_enabled=True))
    hello(core)
    core.handle({"method": "duck.command", "params": {"head": {"head_yaw": 9.0}}}, authed=True)
    core.command_for_tick()
    clock.t += 5.0  # the loop was starved for five seconds
    moved = core.command_for_tick().head[2]
    assert moved == pytest.approx(daemon.HEAD_SLEW_RAD_S * daemon.HEAD_SLEW_MAX_DT_S, abs=1e-6)


# ── the protocol ────────────────────────────────────────────────────────────────────────


def test_a_protocol_mismatch_is_refused(daemon: ModuleType) -> None:
    core = daemon.BridgeCore()
    reply, authed = core.handle(
        {
            "id": 1,
            "method": "duck.hello",
            "params": {"protocol": PROTOCOL, "protocol_version": PROTOCOL_VERSION + 1},
        },
        authed=False,
    )
    assert "refusing rather than guessing" in reply["error"]["message"] and not authed


def test_a_token_is_required_when_one_is_configured(daemon: ModuleType) -> None:
    core = daemon.BridgeCore(token="s3cret")
    assert hello(core)["error"]["code"] == 2
    assert hello(core, token="wrong")["error"]["code"] == 2
    assert "result" in hello(core, token="s3cret")


def test_nothing_is_accepted_before_hello(daemon: ModuleType) -> None:
    core = daemon.BridgeCore()
    reply, _ = core.handle({"id": 2, "method": "duck.state"}, authed=False)
    assert reply["error"]["code"] == 2
    assert core.command_for_tick().vx == 0.0


def test_the_protocol_has_no_word_that_reaches_torque(daemon: ModuleType) -> None:
    """The strongest safety property here is structural: there is no method to refuse."""
    core = daemon.BridgeCore()
    hello(core)
    for method in ("duck.relax", "duck.torque", "duck.disable", "duck.limp", "robot.relax"):
        reply, _ = core.handle({"id": 9, "method": method}, authed=True)
        assert reply["error"]["code"] == -32601
    assert core.handle({"id": 9, "method": "duck.stop"}, authed=True)[0]["result"]["limp"] is False


def test_a_verb_a_duck_was_not_built_for_is_refused_with_the_reason(daemon: ModuleType) -> None:
    core = daemon.BridgeCore(capabilities={"speaker": False, "antennas": False})
    hello(core)
    reply, _ = core.handle(
        {"id": 3, "method": "duck.sound", "params": {"mood": "greet"}}, authed=True
    )
    assert reply["error"]["code"] == 4 and "duck_config.json" in reply["error"]["message"]


def test_capabilities_come_from_duck_config(daemon: ModuleType) -> None:
    caps = daemon.capabilities_from(
        {"expression_features": {"camera": True, "speaker": True, "antennas": False}}
    )
    assert caps == {"camera": True, "speaker": True, "antennas": False, "microphone": False}
    assert daemon.capabilities_from({}) == {
        "camera": False,
        "speaker": False,
        "antennas": False,
        "microphone": False,
    }


# ── the shim upstream's loop constructs ─────────────────────────────────────────────────


def test_the_controller_answers_the_shape_upstream_unpacks(daemon: ModuleType) -> None:
    core = daemon.BridgeCore()
    hello(core)
    controller = daemon.NetworkController(core, 20)
    core.handle({"method": "duck.command", "params": {"vx": 0.1, "vy": -0.05}}, authed=True)
    commands, buttons, left, right = controller.get_last_command()
    assert len(commands) == 7, "the seven floats upstream's policy consumes"
    assert float(commands[0]) == pytest.approx(0.1)
    assert float(commands[1]) == pytest.approx(-0.05)
    assert (left, right) == (0.0, 0.0)
    for name in ("A", "B", "X", "Y", "LB", "RB", "dpad_up", "dpad_down"):
        assert hasattr(buttons, name)
    assert core.controller_built_at is not None


def test_an_unknown_button_never_raises_inside_the_control_loop(daemon: ModuleType) -> None:
    buttons = daemon._Buttons()
    assert buttons.some_future_button.is_pressed is False


def test_a_queued_sound_becomes_one_button_press(daemon: ModuleType) -> None:
    core = daemon.BridgeCore(capabilities={"speaker": True})
    hello(core)
    controller = daemon.NetworkController(core, 20)
    reply, _ = core.handle(
        {"id": 4, "method": "duck.sound", "params": {"mood": "greet"}}, authed=True
    )
    assert reply["result"]["how"] == "the pad's sound button"
    _, buttons, _, _ = controller.get_last_command()
    assert buttons.B.triggered is True
    _, buttons, _, _ = controller.get_last_command()
    assert buttons.B.triggered is False, "one sound is one press, not a stuck button"


# ── the daemon must stay installable on a 512 MB Pi ─────────────────────────────────────


def test_the_daemon_needs_nothing_but_the_standard_library_and_numpy() -> None:
    """`pip install --no-deps quackd` has to work on a Pi Zero 2 W, so none of quackd's own
    dependencies may reach it, and neither may the robot's runtime."""
    script = f"""
import sys
for name in ('pydantic', 'typer', 'rich', 'mcp', 'cv2', 'PIL', 'torch', 'onnxruntime',
             'mini_bdx_runtime', 'quackd'):
    sys.modules[name] = None
import importlib.util
spec = importlib.util.spec_from_file_location('b', r'{DAEMON}')
m = importlib.util.module_from_spec(spec)
sys.modules['b'] = m
spec.loader.exec_module(m)
core = m.BridgeCore()
core.handle({{'id': 1, 'method': 'duck.hello',
              'params': {{'protocol': m.PROTOCOL, 'protocol_version': m.PROTOCOL_VERSION}}}},
            authed=False)
c = m.NetworkController(core, 20)
assert len(c.get_last_command()[0]) == 7
"""
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=120, check=False
    )
    assert result.returncode == 0, result.stderr


# ── the two halves, against each other ──────────────────────────────────────────────────


async def test_the_real_daemon_and_the_real_client_agree(daemon: ModuleType) -> None:
    core = daemon.BridgeCore(
        capabilities={"camera": False, "speaker": True, "antennas": True, "microphone": False}
    )
    server = daemon.Server(core, "127.0.0.1", 0)
    server.start()
    controller = daemon.NetworkController(core, 20)
    try:
        adapter = OpenDuckAdapter(OpenDuckBridge(f"tcp://127.0.0.1:{server.port}"))
        manifest = await adapter.connect()
        # this duck has a speaker and antennas but no camera, and head control is off
        assert set(manifest.verb_names()) == {
            "report_state",
            "stop",
            "move",
            "say",
            "quack",
            "express",
        }
        ex = Executor(
            registry_from_manifest(manifest, adapter),
            adapter,
            contract=DUCK.frontmatter,
            detector=ColorBlobDetector(),
            confirm=allow_all,
        )
        # the verb ends by stopping, so watch a single command instead of the whole verb
        from quackd.transport.base import Intent

        await adapter.send_intent(Intent.move(0.3, 0.0, 0.0))  # over this robot's limit
        await asyncio.sleep(0.05)
        assert controller.get_last_command()[0][0] == pytest.approx(daemon.VX[1])

        assert (await ex.run_verb("move", {"vx": 0.1, "duration_s": 0.5})).ok
        await asyncio.sleep(0.05)
        assert controller.get_last_command()[0][0] == 0.0, "a move ends stopped, not coasting"

        assert (await ex.run_verb("say", {"text": "hello there!"})).ok
        assert core.sounds or controller.get_last_command()[1].B.triggered
        await adapter.disconnect()
    finally:
        server.stop()
        server.join(timeout=2)


# ── antenna gestures have to actually reach the antennas ────────────────────────────────


def test_a_gesture_drives_the_triggers_and_then_rests(daemon: ModuleType) -> None:
    """Upstream drives the antennas from the pad's triggers, so a gesture that never
    changes a trigger is an accepted no-op, which is the worst kind of answer."""
    clock = Clock()
    core = daemon.BridgeCore(now=clock, capabilities={"antennas": True})
    hello(core)
    controller = daemon.NetworkController(core, 20)
    assert controller.get_last_command()[2:] == (0.0, 0.0)

    reply, _ = core.handle(
        {"id": 5, "method": "duck.antennas", "params": {"gesture": "perk"}}, authed=True
    )
    assert reply["result"]["accepted"] and reply["result"]["seconds"] == daemon.GESTURE_S
    assert controller.get_last_command()[2:] == (1.0, 1.0)

    clock.t += daemon.GESTURE_S + 0.01
    assert controller.get_last_command()[2:] == (0.0, 0.0), "a gesture is a transient"


def test_a_wiggle_actually_wiggles(daemon: ModuleType) -> None:
    clock = Clock()
    core = daemon.BridgeCore(now=clock, capabilities={"antennas": True})
    hello(core)
    core.handle({"id": 6, "method": "duck.antennas", "params": {"gesture": "wiggle"}}, authed=True)
    seen = set()
    for _ in range(10):
        clock.t += 0.05
        left, _right = core.triggers_for(clock.t)
        seen.add(round(left, 2))
    assert len(seen) > 3, "the antennas move over time rather than sitting at one value"


def test_an_unknown_gesture_is_refused_rather_than_silently_dropped(daemon: ModuleType) -> None:
    core = daemon.BridgeCore(capabilities={"antennas": True})
    hello(core)
    reply, _ = core.handle(
        {"id": 7, "method": "duck.antennas", "params": {"gesture": "moonwalk"}}, authed=True
    )
    assert reply["error"]["code"] == -32602


def test_a_gesture_outlives_a_stale_command_but_not_a_stop(daemon: ModuleType) -> None:
    """The deadman used to rest the antennas, which sounds careful and made `express` a
    guaranteed no-op: `duck.antennas` does not refresh the command timestamp, and verbs are
    separate tool calls with an LLM round trip between them, so the command was always stale
    by the time a gesture arrived. Two 9 g servos on a GPIO pin are not motion; the deadman is
    about the legs. A gesture self-expires, and `duck.stop` still rests them at once."""
    clock = Clock()
    core = daemon.BridgeCore(now=clock, capabilities={"antennas": True})
    hello(core)
    clock.t += 30.0  # an LLM turn: nothing has commanded the legs in ages
    core.handle({"id": 8, "method": "duck.antennas", "params": {"gesture": "perk"}}, authed=True)

    assert core.command_for_tick().triggers == (1.0, 1.0), "the gesture must actually play"
    assert core.deadman_tripped is True, "even though the legs are quite rightly stopped"

    clock.t += daemon.GESTURE_S + 0.01
    assert core.command_for_tick().triggers == (0.0, 0.0), "and then it rests, on its own"

    core.handle({"id": 9, "method": "duck.antennas", "params": {"gesture": "perk"}}, authed=True)
    assert core.command_for_tick().triggers == (1.0, 1.0)
    core.handle({"id": 10, "method": "duck.stop"}, authed=True)
    assert core.command_for_tick().triggers == (0.0, 0.0), "a stop still rests them at once"


def test_droop_is_a_different_position_from_rest(daemon: ModuleType) -> None:
    """`droop` returned the antennas exact resting value, so it was an accepted no-op that
    reported success. Upstream maps -1..1 with 0 as rest, so a droop needs a negative number,
    one a physical trigger axis cannot produce."""
    clock = Clock()
    core = daemon.BridgeCore(now=clock, capabilities={"antennas": True})
    hello(core)
    rest = core.command_for_tick().triggers
    seen = {}
    for gesture in ("perk", "droop", "wiggle"):
        core.handle(
            {"id": 1, "method": "duck.antennas", "params": {"gesture": gesture}}, authed=True
        )
        clock.t += 0.02
        seen[gesture] = core.command_for_tick().triggers
        assert seen[gesture] != rest, gesture + " must move the antennas somewhere"
    assert len(set(seen.values())) == 3, "and the three must be distinguishable"
    assert seen["droop"][0] < 0 < seen["perk"][0]


def test_a_camera_with_no_snapshot_url_is_not_advertised(daemon: ModuleType, tmp_path) -> None:
    """Otherwise the manifest promises observe, go_to, search_scan and approach_and, and
    every one of them fails at runtime instead of simply not existing."""
    config = tmp_path / "duck_config.json"
    config.write_text('{"expression_features": {"camera": true, "speaker": true}}')
    args = daemon.parser().parse_args(
        ["serve", "--duck-config", str(config), "--token-file", str(tmp_path / "none")]
    )
    assert daemon.build_core(args).capabilities["camera"] is False

    args = daemon.parser().parse_args(
        [
            "serve",
            "--duck-config",
            str(config),
            "--token-file",
            str(tmp_path / "none"),
            "--camera-url",
            "http://127.0.0.1:9872/snapshot.jpg",
        ]
    )
    core = daemon.build_core(args)
    assert core.capabilities["camera"] is True
    assert core.hello()["camera"]["url"].endswith("/snapshot.jpg")


def test_the_documented_camera_setup_actually_has_a_camera(daemon: ModuleType, tmp_path) -> None:
    """`expression_features.camera` says who owns the *device*, not whether quackd can see.

    quackd_duck_camd.py refuses to start while that flag is true, so install.sh and
    docs/adapters/open_duck.md both tell the owner to set it false and let camd serve frames.
    Reading the capability from the same flag meant that a duck configured exactly as
    documented reported no camera, dropped observe, go_to, search_scan and approach_and at
    connect, and refused both starter tasks — with no configuration anywhere that produced
    frames and the verbs that use them at the same time. The snapshot URL decides."""
    config = tmp_path / "duck_config.json"
    config.write_text('{"expression_features": {"camera": false, "speaker": true}}')
    args = daemon.parser().parse_args(
        [
            "serve",
            "--duck-config",
            str(config),
            "--token-file",
            str(tmp_path / "none"),
            "--camera-url",
            "http://127.0.0.1:9872/snapshot.jpg",
        ]
    )
    core = daemon.build_core(args)
    assert core.capabilities["camera"] is True
    assert core.hello()["camera"]["url"].endswith("/snapshot.jpg")

    # and with the device free but nothing serving it, there is still no camera
    args = daemon.parser().parse_args(
        ["serve", "--duck-config", str(config), "--token-file", str(tmp_path / "none")]
    )
    assert daemon.build_core(args).capabilities["camera"] is False


# ── the units, which are the first thing a human runs ───────────────────────────────────


def exec_start(path: Path) -> list[str]:
    """systemd joins backslash continuations; so does this."""
    import shlex

    out: list[str] = []
    collecting = False
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw
        if line.startswith("ExecStart="):
            collecting, line = True, line[len("ExecStart=") :]
        elif not collecting:
            continue
        more = line.rstrip().endswith("\\")
        out.append(line.rstrip().rstrip("\\"))
        if not more:
            break
    return shlex.split(" ".join(out))


def test_the_shipped_unit_is_a_command_the_daemon_accepts(daemon: ModuleType) -> None:
    """The unit is the only `serve --script` invocation shipped anywhere, so it is the one
    an owner copies. Written as `--script-arg --onnx_model_path`, argparse refuses a value
    beginning with a dash and `systemctl start` exits 2 before the bridge binds a socket,
    with Restart=no leaving it failed.

    This also pins the flags that a `nargs=REMAINDER` fix would have silently eaten: --bind
    and --deadman-ms would revert to defaults and nothing would say so."""
    unit = REPO / "bridge" / "open_duck" / "quackd-duck-bridge.service"
    argv = exec_start(unit)
    assert argv[1].endswith("quackd_duck_bridge.py")
    args = daemon.parser().parse_args(argv[2:])
    assert args.command == "serve"
    assert args.script_arg == ["--onnx_model_path", "/home/pi/BEST_WALK_ONNX_2.onnx"]
    assert args.bind == "127.0.0.1"
    assert args.deadman_ms == 300
    assert args.script.endswith("v2_rl_walk_mujoco.py")


def test_the_unit_runs_upstream_from_the_directory_its_data_is_in() -> None:
    """Upstream opens "./polynomial_coefficients.pkl" and "../mini_bdx_runtime/assets/"
    relative to the working directory, and the pkl is read inside RLWalk.__init__ *after*
    the servo bus is powered. A WorkingDirectory one level up is a traceback over fourteen
    energised joints, so the unit and `script_workdir()` have to agree on scripts/."""
    path = REPO / "bridge" / "open_duck" / "quackd-duck-bridge.service"
    workdir = next(
        line.split("=", 1)[1].strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.startswith("WorkingDirectory=")
    )
    assert workdir.endswith("/scripts"), workdir
    script = next(a for a in exec_start(path) if a.endswith("v2_rl_walk_mujoco.py"))
    assert script.rsplit("/", 1)[0] == workdir, "the unit must run the script from its own dir"


def test_serve_refuses_before_it_binds_when_upstreams_data_is_not_there(
    daemon: ModuleType, tmp_path
) -> None:
    """The refusal has to happen before the socket and before the servos, so preflight() is
    what main() calls first."""
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    script = scripts / "v2_rl_walk_mujoco.py"
    script.write_text("pass\n")
    args = daemon.parser().parse_args(["serve", "--script", str(script)])

    problem = daemon.preflight(args)
    assert problem is not None and "polynomial_coefficients.pkl" in problem

    (scripts / "polynomial_coefficients.pkl").write_bytes(b"")
    assert daemon.preflight(args) is None
    assert daemon.script_workdir(args) == str(scripts)

    missing = daemon.parser().parse_args(["serve", "--script", str(tmp_path / "nope.py")])
    assert "does not exist" in (daemon.preflight(missing) or "")


# ── a stop has to outlive the commands already in flight ────────────────────────────────


def test_a_stop_is_not_undone_by_the_next_command(daemon: ModuleType) -> None:
    """`duck.stop` used to publish zeros and nothing else, so the very next `duck.command` —
    one already in flight when the operator hit the brake, or the tail of a verb that had not
    noticed yet — put the velocity straight back 100 ms later. `stopped_upto` was computed
    for exactly this and then never read by anything."""
    clock = Clock()
    core = daemon.BridgeCore(now=clock)
    hello(core)

    core.handle({"method": "duck.command", "params": {"vx": 0.1}}, authed=True)
    assert core.command_for_tick().vx == pytest.approx(0.1)

    reply, _ = core.handle({"id": 9, "method": "duck.stop"}, authed=True)
    assert reply["result"]["stopped"] is True
    assert reply["result"]["latched_ms"] == int(core.deadman_s * 1000)

    epoch = reply["result"]["stop_epoch"]

    # the command that was already on the wire when the stop was sent: it carries the epoch
    # its sender knew about, which is now stale
    clock.t += 0.05
    core.handle({"method": "duck.command", "params": {"vx": 0.1, "epoch": epoch - 1}}, authed=True)
    snap = core.command_for_tick()
    assert snap.vx == 0.0, "a stop that lasts one tick is not a stop"
    assert core.state()["stop_latched"] is True

    # ...but a command from a client that has *heard* the stop is deliberate, and must drive
    # immediately. A blunt time window cannot tell these apart, and swallowing this one would
    # make the duck under-rotate through every step of a search_scan, because `_turn` ends
    # each step with a stop.
    core.handle({"method": "duck.command", "params": {"vx": 0.1, "epoch": epoch}}, authed=True)
    assert core.command_for_tick().vx == pytest.approx(0.1)


def test_a_client_that_sends_no_epoch_is_held_to_the_window(daemon: ModuleType) -> None:
    """It cannot prove it has heard the stop, so it does not get the benefit of the doubt."""
    clock = Clock()
    core = daemon.BridgeCore(now=clock)
    hello(core)
    core.handle({"id": 1, "method": "duck.stop"}, authed=True)
    core.handle({"method": "duck.command", "params": {"vx": 0.1}}, authed=True)
    assert core.command_for_tick().vx == 0.0

    clock.t += core.stop_latch_s + 0.01
    core.handle({"method": "duck.command", "params": {"vx": 0.1}}, authed=True)
    assert core.command_for_tick().vx == pytest.approx(0.1), "the window bounds it"


def test_a_stop_leaves_the_duck_stopped_even_with_nobody_commanding(daemon: ModuleType) -> None:
    """Dropping the command rather than zeroing it leaves `snap.at` stale, so the deadman
    keeps the duck down after the latch expires until something deliberately drives."""
    clock = Clock()
    core = daemon.BridgeCore(now=clock)
    hello(core)
    core.handle({"method": "duck.command", "params": {"vx": 0.1}}, authed=True)
    core.handle({"id": 1, "method": "duck.stop"}, authed=True)
    clock.t += 5.0
    assert core.command_for_tick().vx == 0.0
    assert core.deadman_tripped is True


# ── only the client that was driving gets to stop the duck ──────────────────────────────


def test_a_watching_client_does_not_stop_a_walking_duck(daemon: ModuleType) -> None:
    """The hardware checklist tells the operator to run `quackd doctor` in a second terminal
    while the duck walks, and doctor disconnects with a bare FIN. Zeroing on *any* drop cut
    the velocity mid-stride, and looked exactly like the Wi-Fi latency step 5 warns about."""
    clock = Clock()
    core = daemon.BridgeCore(now=clock)
    server = daemon.Server(core, "127.0.0.1", 0)
    try:
        hello(core)
        driver = daemon._Client(conn=_FakeConn(), commanded=True)
        watcher = daemon._Client(conn=_FakeConn())

        core.handle({"method": "duck.command", "params": {"vx": 0.1}}, authed=True)
        server._drop(watcher)
        assert core.snapshot.vx == pytest.approx(0.1), "a watcher must not zero the duck"
        assert core.state()["stop_latched"] is False

        server._drop(driver)
        assert core.snapshot.vx == 0.0, "the client that was driving must"
    finally:
        server.stop()


class _FakeConn:
    """Enough socket for `_drop`, which unregisters and closes."""

    def close(self) -> None:
        self.closed = True

    def fileno(self) -> int:
        return -1


# ── shutting the bridge down must not topple it ─────────────────────────────────────────


def test_a_shutdown_settles_the_duck_while_the_loop_is_still_running(daemon: ModuleType) -> None:
    """There was no signal handling at all, so `systemctl stop` killed the interpreter between
    two 20 ms ticks with the servos holding their last goal and torque on — a duck stopped
    mid-stride topples with rigid legs. The `finally` in main() looked like it covered this
    and did not: it runs after the loop has already exited, so its zeros had no reader."""
    import signal as signal_module

    signals = (signal_module.SIGTERM, signal_module.SIGINT)
    previous = {s: signal_module.getsignal(s) for s in signals}
    interrupted = threading.Event()
    clock = Clock()
    core = daemon.BridgeCore(now=clock)
    controller = daemon.NetworkController(core)
    try:
        daemon.install_settle(core, seconds=0.05, interrupt=interrupted.set)
        hello(core)
        core.handle({"method": "duck.command", "params": {"vx": 0.1}}, authed=True)
        assert controller.get_last_command()[0][0] == pytest.approx(0.1)

        handler = signal_module.getsignal(signal_module.SIGTERM)
        assert callable(handler)
        handler(signal_module.SIGTERM, None)  # what systemctl stop delivers

        # the loop is still ticking, and what it now reads is zero
        for _ in range(5):
            assert controller.get_last_command()[0][0] == 0.0
        assert interrupted.wait(timeout=2.0), "the loop must then be asked to exit"
    finally:
        for sig, handler in previous.items():
            signal_module.signal(sig, handler)


async def test_a_stop_that_cannot_be_delivered_fails_the_verb(daemon: ModuleType) -> None:
    """End to end, through the real client and the real adapter, which is where this broke.

    `verbs/core.py` reads `stop_error` off the object it was handed — the adapter — and no
    adapter forwarded it, so an undeliverable stop was recorded as `ok: true` with the words
    "stopped (velocity zeroed)" in the transcript the hardware checklist asks people to
    attach. The link being down is exactly when `stop` is asked for."""
    core = daemon.BridgeCore(capabilities={"camera": False, "speaker": True})
    server = daemon.Server(core, "127.0.0.1", 0)
    server.start()
    try:
        adapter = OpenDuckAdapter(OpenDuckBridge(f"tcp://127.0.0.1:{server.port}"))
        manifest = await adapter.connect()
        ex = Executor(registry_from_manifest(manifest, adapter), adapter, contract=DUCK.frontmatter)
        assert (await ex.run_verb("stop")).ok
        assert adapter.stop_error is None

        server.stop()
        server.join(timeout=2)
        await asyncio.sleep(0.2)  # let the client's pump see the close

        result = await ex.run_verb("stop")
        assert not result.ok, "a stop that never left must not be reported as a stop"
        assert "could not be delivered" in result.summary
        assert adapter.stop_error, "and the adapter must carry the reason"
        assert (await adapter.get_state()).extras["stop_error"] == adapter.stop_error
    finally:
        server.stop()
        server.join(timeout=2)


async def test_a_second_move_right_after_a_stop_still_drives(daemon: ModuleType) -> None:
    """Every `move` ends with a stop, and `_turn` ends every step of a `search_scan` with
    one. So the command that opens the *next* move arrives milliseconds after a stop, and it
    is deliberate — it has to take effect.

    A stop latch that went purely on elapsed time would swallow it and leave the duck
    under-rotating through the whole scan, which is why the bridge matches on the stop epoch
    the client echoes rather than on the clock alone."""
    core = daemon.BridgeCore(capabilities={"camera": False, "speaker": False})
    server = daemon.Server(core, "127.0.0.1", 0)
    server.start()
    controller = daemon.NetworkController(core, 20)
    try:
        adapter = OpenDuckAdapter(OpenDuckBridge(f"tcp://127.0.0.1:{server.port}"))
        manifest = await adapter.connect()
        ex = Executor(registry_from_manifest(manifest, adapter), adapter, contract=DUCK.frontmatter)
        assert (await ex.run_verb("move", {"vx": 0.1, "duration_s": 0.2})).ok
        await asyncio.sleep(0.05)
        assert controller.get_last_command()[0][0] == 0.0, "a move ends stopped"

        # immediately again, exactly as search_scan's next turn step would
        moving = asyncio.create_task(ex.run_verb("move", {"vx": 0.12, "duration_s": 0.4}))
        await asyncio.sleep(0.15)
        assert controller.get_last_command()[0][0] == pytest.approx(0.12), (
            "the stop latch must not swallow the next deliberate command"
        )
        assert (await moving).ok
        await adapter.disconnect()
    finally:
        server.stop()
        server.join(timeout=2)


# ── a paused policy, a wedged loop, and a token nobody can read ─────────────────────────


def test_a_paused_policy_is_named_rather_than_blamed_on_the_pi(daemon: ModuleType) -> None:
    """Upstream calls get_last_command() *before* its pause check and then sleeps 0.1 s a
    tick, so a paused loop still ticks — at about 10 Hz. That tripped the heartbeat's
    MIN_LOOP_HZ floor and killed the session in under a second with "the Pi is starved and
    the gait is degrading", which is the wrong subsystem and the wrong remedy."""
    core = daemon.BridgeCore()
    core.paused = True
    health = core.health()
    assert health["healthy"] is False
    assert health["paused"] is True
    assert "paused" in health["reason"] and "start_paused" in health["reason"]
    assert "A button" in health["reason"], "and it says why quackd cannot just unpause it"


def test_serve_refuses_to_start_a_policy_it_could_never_release(
    daemon: ModuleType, tmp_path
) -> None:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "v2_rl_walk_mujoco.py").write_text("pass\n")
    (scripts / "polynomial_coefficients.pkl").write_bytes(b"")
    config = tmp_path / "duck_config.json"
    config.write_text('{"start_paused": true, "expression_features": {}}')
    code = daemon.main(
        [
            "serve",
            "--script",
            str(scripts / "v2_rl_walk_mujoco.py"),
            "--duck-config",
            str(config),
            "--token-file",
            str(tmp_path / "none"),
            "--port",
            "0",
        ]
    )
    assert code == 2, "better not to start than to be diagnosed as a starved Pi all evening"


def test_a_wedged_control_loop_stops_reporting_itself_healthy(daemon: ModuleType) -> None:
    """loop_hz, ticks and _last_tick are all written by the control thread, so if it blocks
    inside a Feetech read they freeze at whatever they were and the server thread keeps
    answering "healthy, 50 Hz" forever — while the deadman, which lives in that same
    function, cannot fire either. Only the clock keeps moving."""
    clock = Clock()
    core = daemon.BridgeCore(now=clock)
    controller = daemon.NetworkController(core)
    hello(core)
    for _ in range(5):
        clock.t += 0.02
        controller.get_last_command()
    assert core.health()["healthy"] is True

    clock.t += daemon.TICK_STALE_S + 0.05  # the loop stops calling us
    health = core.health()
    assert health["healthy"] is False
    assert "not ticked" in health["reason"]
    assert health["tick_age_ms"] >= int(daemon.TICK_STALE_S * 1000)


def test_a_token_the_service_cannot_read_refuses_to_start(daemon: ModuleType, tmp_path) -> None:
    """`os.path.exists()` returned False for a token behind a directory the service user
    could not traverse, so the bridge started with authentication silently off — and from
    the client's side that is indistinguishable from a bridge that checked its token."""
    missing = tmp_path / "nope.token"
    assert daemon.read_token(str(missing)) is None, "no file is honestly no token"

    empty = tmp_path / "empty.token"
    empty.write_text("   \n")
    assert daemon.read_token(str(empty)) is None, "an empty token must not authenticate"

    good = tmp_path / "good.token"
    good.write_text("s3cret\n")
    assert daemon.read_token(str(good)) == "s3cret"

    unreadable = tmp_path / "unreadable"  # a directory where a file is expected: EISDIR
    unreadable.mkdir()
    with pytest.raises(SystemExit) as caught:
        daemon.read_token(str(unreadable))
    assert "silently disabled" in str(caught.value)


def test_the_hello_says_whether_there_is_any_authentication(daemon: ModuleType) -> None:
    assert daemon.BridgeCore().hello()["safety"]["auth"] == "none"
    assert daemon.BridgeCore(token="s3cret").hello()["safety"]["auth"] == "token"
    assert daemon.BridgeCore().hello()["safety"]["fall_detection"] is False


# ── the monkeypatch the entire bridge rests on ──────────────────────────────────────────

FAKE_PAD = """class XBoxController:
    def __init__(self, command_freq=20, only_head_control=False):
        raise AssertionError("the real pad was constructed; the shim did not take")
"""

FAKE_BUTTONS = """class Buttons:
    def __init__(self):
        raise AssertionError("Buttons.__init__ has side effects we have not read")
"""

FAKE_LOOP = """import json
import os
import time

time.sleep(BOOT_DELAY)
from mini_bdx_runtime.xbox_controller import XBoxController

pad = XBoxController(20)
seen = []
# Written every tick rather than in a finally: Windows terminate() is TerminateProcess, so
# no handler runs and no exit path is guaranteed. Ticks on disk while it runs is also the
# stronger claim - it proves the loop is being fed, not merely that it once exited tidily.
while True:
    cmds, buttons, lt, rt = pad.get_last_command()
    seen.append([float(c) for c in cmds])
    buttons.a_button_upstream_added_later.triggered
    with open(os.environ["TICKS"], "w") as fh:
        json.dump(seen[-40:], fh)
    time.sleep(0.02)
"""


def _fake_runtime(root: Path, *, boot_delay: float = 0.0) -> Path:
    """Enough of `mini_bdx_runtime` and upstream's walk script for the shim to be real.

    The import form is the point. Upstream writes
    `from mini_bdx_runtime.xbox_controller import XBoxController`, so rebinding the module
    attribute works only because `install_shim` runs before the script is executed. If that
    ordering broke, or upstream renamed the class, the socket would control nothing and the
    duck would be driven by something its owner could not see — which is why the fake pad
    raises rather than returning zeros."""
    pkg = root / "mini_bdx_runtime"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "xbox_controller.py").write_text(FAKE_PAD)
    (pkg / "buttons.py").write_text(FAKE_BUTTONS)
    scripts = root / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    (scripts / "polynomial_coefficients.pkl").write_bytes(b"")
    script = scripts / "v2_rl_walk_mujoco.py"
    script.write_text(FAKE_LOOP.replace("BOOT_DELAY", str(boot_delay)))
    return script


def _serve_in_subprocess(tmp_path: Path, script: Path, watchdog_s: str) -> subprocess.Popen:
    runner = tmp_path / "run.py"
    runner.write_text(
        "import sys, importlib.util\n"
        f"sys.path.insert(0, {str(tmp_path)!r})\n"
        f'spec = importlib.util.spec_from_file_location("qdb", {str(DAEMON)!r})\n'
        'm = importlib.util.module_from_spec(spec); sys.modules["qdb"] = m\n'
        "spec.loader.exec_module(m)\n"
        "sys.exit(m.main(["
        f'"serve", "--script", {str(script)!r}, "--port", "0",'
        f'"--token-file", {str(tmp_path / "none")!r},'
        f'"--patch-watchdog-s", "{watchdog_s}",'
        "]))\n"
    )
    env = {**os.environ, "TICKS": str(tmp_path / "ticks.json"), "PYTHONPATH": str(tmp_path)}
    return subprocess.Popen([sys.executable, str(runner)], env=env)


def test_the_shim_intercepts_the_import_form_upstream_actually_uses(tmp_path: Path) -> None:
    """PAD_SUBSTITUTION is the single assumption the whole bridge stands on, and nothing
    executed `install_shim`, `runpy` or `main()`'s serve path: NetworkController was always
    constructed directly, so the rebind itself had no test at all."""
    script = _fake_runtime(tmp_path)
    proc = _serve_in_subprocess(tmp_path, script, "10")
    try:
        time.sleep(3.0)
        assert proc.poll() is None, f"the bridge exited early with {proc.returncode}"
    finally:
        proc.terminate()
        proc.wait(timeout=15)
    ticks = tmp_path / "ticks.json"
    assert ticks.exists(), "upstream's loop never ran, so the shim never fed it"
    seen = json.loads(ticks.read_text())
    assert seen and all(len(v) == 7 for v in seen), "seven floats, every tick"


def test_a_slow_boot_is_not_mistaken_for_a_missing_shim(tmp_path: Path) -> None:
    """The watchdog was a fixed 20 s covering onnxruntime, the Feetech bus, a hard two second
    settle and the IMU on a 512 MB Pi with a cold page cache — and it exits with os._exit
    into fourteen energised servos."""
    script = _fake_runtime(tmp_path, boot_delay=1.5)
    proc = _serve_in_subprocess(tmp_path, script, "6")
    try:
        time.sleep(4.0)
        assert proc.poll() is None, "a boot slower than half the budget must not be killed"
    finally:
        proc.terminate()
        proc.wait(timeout=15)


async def test_the_client_keeps_the_deadman_fed_with_margin(daemon: ModuleType) -> None:
    """The one real-socket test moved for exactly DEADMAN_S, so `vx == 0` at the end passed
    whether the verb's own stop landed or the client had simply starved the deadman. Sample
    the age *while* it walks instead, and require real margin rather than a survivable miss."""
    core = daemon.BridgeCore(capabilities={"camera": False, "speaker": False})
    server = daemon.Server(core, "127.0.0.1", 0)
    server.start()
    try:
        adapter = OpenDuckAdapter(OpenDuckBridge(f"tcp://127.0.0.1:{server.port}"))
        manifest = await adapter.connect()
        ex = Executor(registry_from_manifest(manifest, adapter), adapter, contract=DUCK.frontmatter)
        ages: list[int] = []

        async def sample() -> None:
            # Only once the duck is actually being driven. Before the first command the age
            # is measured from connect, which says nothing about how the verb feeds it.
            start_seq = core.snapshot.seq
            while True:
                if core.snapshot.seq > start_seq:
                    ages.append(core.state()["command_age_ms"])
                await asyncio.sleep(0.02)

        watcher = asyncio.create_task(sample())
        assert (await ex.run_verb("move", {"vx": 0.1, "duration_s": 1.0})).ok
        watcher.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await watcher

        assert len(ages) > 20, "the sampler has to have actually looked"
        worst = max(ages)
        budget = core.deadman_s * 1000
        assert worst < budget * 0.5, (
            f"the worst command age was {worst} ms against a {budget:.0f} ms deadman; "
            "the client is not feeding it with margin"
        )
        await adapter.disconnect()
    finally:
        server.stop()
        server.join(timeout=2)
