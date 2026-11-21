"""The bridge daemon that runs on the duck's Pi, exercised with no Pi and no duck.

The daemon is stdlib plus numpy on purpose, so the parts that matter most (the deadman, the
clamps, the protocol, and the fact that nothing can reach torque) are all testable here. The
last test in this file drives the real daemon and the real client against each other over
loopback, which is the only way to know the two halves agree.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import subprocess
import sys
import threading
import time
from functools import partial
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from roboglot.glotfile.parser import parse_glot_text
from roboglot.perception.color_blob import ColorBlobDetector
from roboglot.safety import Executor, allow_all
from roboglot.verbs.registry import registry_from_manifest
from roboglot_open_duck import OpenDuckAdapter
from roboglot_open_duck.bridge import PROTOCOL, PROTOCOL_VERSION, OpenDuckBridge
from tests.support import load_script

REPO = Path(__file__).resolve().parents[1]
DAEMON = REPO / "bridge" / "open_duck" / "roboglot_duck_bridge.py"

GLOT = parse_glot_text(
    "---\nglot: 1\nname: t\ndescription: d\nrequires: [move]\nverbs:\n"
    "  allow: [move, gaze, say, quack, express, report_state, stop]\n"
    "success: [x]\n---\n# Task\nx\n"
)


# It lives outside the package on purpose: Roboglot's core must never import it.
load_daemon = partial(load_script, DAEMON, "roboglot_duck_bridge")


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


def test_a_number_that_is_not_finite_is_refused_rather_than_clamped_to_a_bound(
    daemon: ModuleType,
) -> None:
    """`clamp` was `max(lo, min(hi, v))`, and `min(hi, nan)` keeps `hi`, while the server's
    `json.loads` takes the `NaN` and `Infinity` literals. So `{"vx": NaN}` walked the duck
    forward at the full clamp on every axis it named, Infinity went to the bound it pointed at,
    and a NaN head target went to the end of its travel. Each command here is parsed from the
    wire's own text, and each must be refused whole: the duck goes on with the last command it
    took, which the deadman ends as it would a lost packet."""
    clock = Clock()
    core = daemon.BridgeCore(now=clock, limits=daemon.Limits(head_enabled=True))
    hello(core)
    core.handle({"method": "duck.command", "params": {"vx": 0.05}}, authed=True)
    took = core.command_for_tick()
    head_target = core.head_target
    nan_head = ", ".join(f'"{name}": NaN' for name in daemon.HEAD_ORDER)
    for text in (
        '{"vx": NaN, "vy": NaN, "vyaw": NaN}',
        '{"vx": Infinity, "vyaw": -Infinity}',
        '{"vx": "nan"}',
        '{"vx": 0.05, "head": {' + nan_head + "}}",
        '{"vx": 0.05, "head": {"head_yaw": Infinity}}',
    ):
        core.handle({"method": "duck.command", "params": json.loads(text)}, authed=True)
        clock.t += 0.02
        snap = core.command_for_tick()
        assert (snap.seq, snap.vx, snap.vy, snap.vyaw) == (took.seq, took.vx, 0.0, 0.0), text
        assert core.head_target == head_target, text

    # and the clamp is safe on its own: it never turns a number that is not finite into a bound
    for value in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(ValueError):
            daemon.clamp(value, daemon.VX)


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
    assert ceiling < daemon.HEAD_YAW[1], "Roboglot stays inside upstream's own clamp"

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
    """`pip install --no-deps roboglot` has to work on a Pi Zero 2 W, so none of Roboglot's own
    dependencies may reach it, and neither may the robot's runtime."""
    script = f"""
import sys
for name in ('pydantic', 'typer', 'rich', 'mcp', 'cv2', 'PIL', 'torch', 'onnxruntime',
             'mini_bdx_runtime', 'roboglot'):
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
            contract=GLOT.frontmatter,
            detector=ColorBlobDetector(),
            confirm=allow_all,
        )
        # the verb ends by stopping, so watch a single command instead of the whole verb
        from roboglot.transport.base import Intent

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
    args = daemon.parser().parse_args(["serve", "--duck-config", str(config), "--token-file", ""])
    assert daemon.build_core(args).capabilities["camera"] is False

    args = daemon.parser().parse_args(
        [
            "serve",
            "--duck-config",
            str(config),
            "--token-file",
            "",
            "--camera-url",
            "http://127.0.0.1:9872/snapshot.jpg",
        ]
    )
    core = daemon.build_core(args)
    assert core.capabilities["camera"] is True
    assert core.hello()["camera"]["url"].endswith("/snapshot.jpg")


def test_the_documented_camera_setup_actually_has_a_camera(daemon: ModuleType, tmp_path) -> None:
    """`expression_features.camera` says who owns the *device*, not whether Roboglot can see.

    roboglot_duck_camd.py refuses to start while that flag is true, so install.sh and
    docs/adapters/open_duck/README.md both tell the owner to set it false and let camd serve frames.
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
            "",
            "--camera-url",
            "http://127.0.0.1:9872/snapshot.jpg",
        ]
    )
    core = daemon.build_core(args)
    assert core.capabilities["camera"] is True
    assert core.hello()["camera"]["url"].endswith("/snapshot.jpg")

    # and with the device free but nothing serving it, there is still no camera
    args = daemon.parser().parse_args(["serve", "--duck-config", str(config), "--token-file", ""])
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
    unit = REPO / "bridge" / "open_duck" / "roboglot-duck-bridge.service"
    argv = exec_start(unit)
    assert argv[1].endswith("roboglot_duck_bridge.py")
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
    path = REPO / "bridge" / "open_duck" / "roboglot-duck-bridge.service"
    workdir = next(
        line.split("=", 1)[1].strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.startswith("WorkingDirectory=")
    )
    assert workdir.endswith("/scripts"), workdir
    script = next(a for a in exec_start(path) if a.endswith("v2_rl_walk_mujoco.py"))
    assert script.rsplit("/", 1)[0] == workdir, "the unit must run the script from its own dir"


def test_install_sh_puts_its_token_file_in_the_unit_and_changes_no_directory_but_its_own(
    daemon: ModuleType, tmp_path: Path
) -> None:
    """`TOKEN_FILE=... bash install.sh` wrote the token where TOKEN_FILE said and never put
    `--token-file` into the unit, so the bridge read its default path, found no file and served
    with no token, admitting any client, while the script said the service user could read its
    token. Its chgrp and chmod 0750 ran on whatever directory held TOKEN_FILE, so
    `TOKEN_FILE=/etc/duck.token` changed the group and mode of /etc itself. Its own directory,
    /etc/roboglot, it makes and puts right on every run; any other it only checks, and stops
    with what to run when that one is not safe for a secret. install.sh is never run here: its
    own sed expressions are applied to the shipped unit, the ExecStart is read by the daemon's
    own parser, and its token step is read line by line."""
    import argparse
    import posixpath
    import re

    here = REPO / "bridge" / "open_duck"
    script = (here / "install.sh").read_text(encoding="utf-8")
    shipped = (here / "roboglot-duck-bridge.service").read_text(encoding="utf-8")
    found = re.search(r'^TOKEN_FILE="\$\{TOKEN_FILE:-(.*)\}"$', script, re.M)
    substitute = re.search(r"^substitute\(\) \{.*?^\}", script, re.M | re.S)
    camera = re.search(r'sudo sed -i "(s\|--bind[^"]*)" "\$BRIDGE_UNIT"', script)
    assert found and substitute and camera
    default = found.group(1)
    edits = [*re.findall(r'-e "(s\|[^"]*)"', substitute.group(0)), camera.group(1)]

    def installed(token_file: str) -> argparse.Namespace:
        env = {
            "SVC_USER": "duckowner",
            "VENV": "/home/duckowner/venv",
            "RUNTIME_DIR": "/home/duckowner/Open_Duck_Mini_Runtime",
            "ONNX_PATH": "/home/duckowner/walk.onnx",
            "CAMERA_URL": "http://127.0.0.1:9872/snapshot.jpg",
            "TOKEN_FILE": token_file,
        }
        text = shipped
        for edit in edits:
            parts = re.fullmatch(r"s\|(.*?)\|(.*?)\|(g?)", edit)
            assert parts, edit
            pattern, replacement, every = parts.groups()
            replacement = re.sub(r"\$(\w+)", lambda m: env[m.group(1)], replacement)
            replacement = replacement.replace("\\", "\\\\").replace("&", r"\g<0>")
            text = re.sub(pattern, replacement, text, count=0 if every else 1, flags=re.M)
        unit = tmp_path / "roboglot-duck-bridge.service"
        unit.write_text(text, encoding="utf-8")
        return daemon.parser().parse_args(exec_start(unit)[2:])

    for token_file in (default, f"{posixpath.dirname(default)}/duck.token", "/etc/duck.token"):
        args = installed(token_file)
        assert args.token_file == token_file, "the unit reads another file than the one written"
        assert (args.bind, args.deadman_ms) == ("127.0.0.1", 300)
        assert args.camera_url == "http://127.0.0.1:9872/snapshot.jpg"

    # The script's own directory is made and put right on every run, owner, group and mode.
    token_dir = re.search(r"^TOKEN_DIR=(\S+)$", script, re.M)
    assert token_dir and posixpath.dirname(default) == token_dir.group(1)
    lines = [line.strip() for line in script.splitlines()]
    own = lines.index('if [ "$TOKEN_FILE_DIR" = "$TOKEN_DIR" ]; then')
    other = lines.index("else", own)
    end = lines.index("fi", other)
    for repair in ('sudo chown root:"$SVC_USER" "$TOKEN_DIR"', 'sudo chmod 0750 "$TOKEN_DIR"'):
        assert repair in lines[own + 1 : other], f"not repaired on every run: {repair}"
    # Any other directory is only checked, and no line anywhere changes one but the script's own.
    assert lines[other + 1 : end] == ['secret_dir_or_die "$TOKEN_FILE_DIR"']
    for i, line in enumerate(lines):
        changes = re.match(r"sudo (chgrp|chmod|chown|install -d)\b", line)
        if changes and re.search(r"TOKEN_DIR|TOKEN_FILE_DIR|dirname|\$dir\b", line):
            assert own < i < other and line.endswith('"$TOKEN_DIR"'), f"line {i + 1}: {line}"
    check = re.search(r"^secret_dir_or_die\(\) \{.*?^\}", script, re.M | re.S)
    assert check, "the check of a directory that is not the script's own"
    for checked in (
        'sudo test -d "$dir"',  # it exists and is a directory
        'sudo test -L "$dir"',  # and is no symlink to somewhere else
        "stat -c '%U %a'",  # its owner and mode
        '"$owner" != root',  # owned by root, so nobody else can swap the token for a symlink
        "8#022",  # writable by neither its group nor anyone else
        'sudo -u "$SVC_USER" test -x "$dir"',  # and searchable by the service user
    ):
        assert checked in check.group(0), checked
    assert check.group(0).count('die "') >= 4, "each failed check stops and says what to run"
    assert not re.search(r"^\s*sudo (chgrp|chmod|chown|install)\b", check.group(0), re.M)
    # and the token is created with its mode before a byte of it is written
    assert not any('tee "$TOKEN_FILE"' in line for line in lines)


def _usable_bash() -> str | None:
    """A bash that runs this machine's paths: not the WSL launcher in System32 on Windows."""
    import shutil

    found = shutil.which("bash")
    if found is None or "system32" in found.lower():
        return None
    try:
        said = subprocess.run(
            [found, "-c", "printf ok"], capture_output=True, text=True, timeout=30
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return found if said.stdout == "ok" else None


#: install.sh's token step run with `sudo` stood in for: every command it is given is logged,
#: `test` and `grep` run against a folder standing in for /, `install` writes into that folder,
#: so nothing on this machine is changed. `stat` answers root and 755 for a directory (as /etc's
#: are), and for a file what a `<file>.stat` beside it says, else root, pi and 640, which is
#: what the script itself leaves a token as.
INSTALL_HARNESS = """set -euo pipefail
SVC_USER=pi
TOKEN_DIR=/etc/roboglot
TOKEN_FILE='@TOKEN_FILE@'
TOKEN_FILE_DIR="$(dirname -- "$TOKEN_FILE")"
say() { :; }
die() { printf 'stopped: %s\\n' "$1" >> '@LOG@'; exit 1; }
openssl() { printf '%064d\\n' 0; }
sudo() {
  printf 'sudo %s\\n' "$*" >> '@LOG@'
  local cmd="$1" a
  local args=()
  shift
  if [ "$cmd" = -u ]; then shift; sudo "$@"; return; fi
  for a in "$@"; do
    case "$a" in /dev/*) args+=("$a") ;; /*) args+=("@ROOT@$a") ;; *) args+=("$a") ;; esac
  done
  local last="${args[${#args[@]}-1]}"
  case "$cmd" in
    test|grep) "$cmd" "${args[@]}" ;;
    stat)
      if [ -d "$last" ]; then printf 'root 755\\n'
      elif [ -f "$last.stat" ]; then cat "$last.stat"
      else printf 'root pi 640\\n'; fi ;;
    install) if [ "$1" = -d ]; then mkdir -p "$last"; else cat > "$last"; fi ;;
    *) return 0 ;;
  esac
}
"""


@pytest.mark.parametrize(
    "token_file, already",
    [
        ("/etc/hostname", "a file of the system's"),
        ("/etc/roboglot", "the script's own directory"),
        ("/etc/roboglot/duck-bridge.token", "a symlink to a file of the system's"),
        ("/etc/roboglot/moss-bridge.token", "another daemon's token"),
        ("/etc/roboglot/duck-bridge.token", "a token an earlier run wrote"),
        ("/etc/duck.token", "a token set right already"),
        ("/etc/roboglot/duck-bridge.token", "nothing"),
        ("/etc/duck.token", "nothing"),
    ],
)
def test_install_sh_changes_no_path_it_did_not_write_and_follows_no_symlink(
    tmp_path: Path, token_file: str, already: str
) -> None:
    """The token step, as root, set its group and mode on whatever TOKEN_FILE named once its
    directory passed: `/etc/hostname` became the bridge's token and lost its mode, and
    `TOKEN_FILE=/etc/roboglot` took the search bit off the script's own directory. Telling its
    own token by its shape then took the MOSS bridge's, which its README writes in the same
    shape, and its daemon's group lost it. Now it changes a token only at its own default path,
    where it is the one an earlier run wrote. At any other path it writes a new one, or uses
    what is there only if it is already root's, the service user's group's and 0640, and else
    stops; it never follows a symlink. The script's own lines run here, cut out of install.sh,
    with `sudo` stood in for, so nothing on this machine is changed."""
    bash = _usable_bash()
    if bash is None:
        pytest.skip("needs a bash that runs this machine's paths")
    import os
    import re

    text = (REPO / "bridge" / "open_duck" / "install.sh").read_text(encoding="utf-8")
    lines = text.splitlines()
    top = next(i for i, line in enumerate(lines) if line.startswith('case "$TOKEN_FILE" in'))
    check = next(i for i, line in enumerate(lines) if line.startswith("secret_dir_or_die() {"))
    ends = next(i for i in range(check, len(lines)) if lines[i].startswith("}"))
    step = next(i for i, line in enumerate(lines) if line.startswith('say "generating a token"'))
    read = next(
        i for i, line in enumerate(lines) if line.startswith("printf '%s can read the token")
    )
    root = tmp_path / "root"
    (root / "etc" / "roboglot").mkdir(parents=True)
    (root / "etc" / "hostname").write_text("duckpi\n", encoding="utf-8")
    (root / "etc" / "hostname.stat").write_text("root root 644\n", encoding="utf-8")
    target = root / token_file.lstrip("/")
    if already == "a symlink to a file of the system's":
        try:
            os.symlink(root / "etc" / "hostname", target)
        except OSError:
            pytest.skip("this machine will not make a symlink for this user")
    elif already == "another daemon's token":
        # as bridge/moss/README.md writes it: the same shape as the duck's, its own group
        target.write_text("cd" * 32 + "\n", encoding="utf-8")
        (root / "etc" / "roboglot" / "moss-bridge.token.stat").write_text("root moss 640\n")
    elif already in ("a token an earlier run wrote", "a token set right already"):
        target.write_text("ab" * 32 + "\n", encoding="utf-8")
    log = tmp_path / "log.txt"
    log.write_text("", encoding="utf-8")
    harness = (
        INSTALL_HARNESS.replace("@TOKEN_FILE@", token_file)
        .replace("@LOG@", log.as_posix())
        .replace("@ROOT@", root.as_posix())
    )
    script = tmp_path / "harness.sh"
    script.write_bytes(
        "\n".join(
            [harness, *lines[top:check], *lines[check : ends + 1], *lines[step : read + 1]]
        ).encode("utf-8")
    )
    run = subprocess.run([bash, str(script)], capture_output=True, text=True, timeout=60)
    said = log.read_text(encoding="utf-8").splitlines()
    changed = [
        line
        for line in said
        if re.match(r"sudo (chgrp|chmod|chown|install)\b", line) and line.split()[-1] == token_file
    ]
    stopped = [line for line in said if line.startswith("stopped:")]
    if already in ("a token an earlier run wrote", "a token set right already", "nothing"):
        assert not stopped and run.returncode == 0, (said, run.stderr)
        assert (root / token_file.lstrip("/")).read_text(encoding="utf-8").strip(), "no token"
        if already == "nothing":
            assert changed == [f"sudo install -m 0640 -o root -g pi /dev/stdin {token_file}"]
        elif already == "a token an earlier run wrote":  # its own path, put right
            assert [line.split()[1] for line in changed] == ["chgrp", "chmod"], changed
        else:  # a path of the person's choosing, used as it is
            assert changed == [], changed
        if token_file.startswith("/etc/roboglot/"):
            assert "sudo chmod 0750 /etc/roboglot" in said, "its own directory is repaired"
        else:
            assert not [line for line in said if line.split()[-1] == "/etc" and "ch" in line]
    else:
        assert stopped and run.returncode != 0, (said, run.stderr)
        assert not changed, f"TOKEN_FILE={token_file} ({already}) was changed as root: {changed}"
        assert (root / "etc" / "hostname").read_text(encoding="utf-8") == "duckpi\n"


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
    """The hardware checklist tells the operator to run `roboglot doctor` in a second terminal
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


def test_nothing_a_client_sends_moves_the_duck_once_a_shutdown_has_begun(
    daemon: ModuleType,
) -> None:
    """The settle's zero was an ordinary stop, which refuses a stale command only for its latch
    (the deadman, 300 ms) inside a settle of `SETTLE_S` (0.5 s). A client still in the middle
    of a `move` re-sends its old epoch at 10 Hz, so the duck walked again once the latch had
    lapsed; a client that had read `duck.state` sent the new epoch and walked it again at once.
    The settle then ended the loop mid-stride with torque on, which is the fall it exists to
    prevent. This sends both while the settle is still running, with a head target, a gesture
    and a walk that raced the signal's zero into the snapshot, and the loop must read zeros and
    a head that stays where it was, up to the moment the settle ends it."""
    import signal as signal_module

    signals = (signal_module.SIGTERM, signal_module.SIGINT)
    previous = {s: signal_module.getsignal(s) for s in signals}
    interrupted = threading.Event()
    clock = Clock()
    core = daemon.BridgeCore(
        now=clock, limits=daemon.Limits(head_enabled=True), capabilities={"antennas": True}
    )
    controller = daemon.NetworkController(core)
    walk = core.limits.vx[1]
    still = (0.0, 0.0, 0.0)

    def command(**params: object) -> None:
        core.handle({"method": "duck.command", "params": params}, authed=True)

    def loop_reads() -> tuple[float, float, float]:
        clock.t += 0.02
        vector = controller.get_last_command()[0]
        return (float(vector[0]), float(vector[1]), float(vector[2]))

    try:
        daemon.install_settle(core, seconds=daemon.SETTLE_S, interrupt=interrupted.set)
        hello(core)
        command(vx=walk, epoch=core.stop_epoch)
        assert loop_reads()[0] == pytest.approx(walk)
        head_at_signal = core.head_now

        handler = signal_module.getsignal(signal_module.SIGTERM)
        assert callable(handler)
        handler(signal_module.SIGTERM, None)  # what systemctl stop delivers
        assert loop_reads() == still

        # the client never heard of this stop, and its next command comes after the latch
        clock.t += core.stop_latch_s
        command(vx=walk, epoch=0)
        assert loop_reads() == still, "a command past the stop's latch walked the duck again"

        # a client that read the state carries the epoch the shutdown moved to
        state, _ = core.handle({"id": 2, "method": "duck.state"}, authed=True)
        command(vx=walk, vyaw=0.5, head={"head_yaw": 0.3}, epoch=state["result"]["stop_epoch"])
        assert loop_reads() == still, "a command with the current epoch walked the duck again"
        assert core.state()["moving"] is False, "the command was taken, not refused"

        reply, _ = core.handle(
            {"id": 3, "method": "duck.antennas", "params": {"gesture": "perk"}}, authed=True
        )
        assert "error" in reply, "a gesture must be refused, not accepted and never played"
        assert core.command_for_tick().triggers == (0.0, 0.0)

        # a command part way through on the server thread when the signal came, stored after
        # the zero: the loop is the last word
        core.snapshot = daemon.Snapshot(seq=99, at=clock.t, vx=walk, vyaw=0.5)
        core.head_target = (0.0, 0.0, 0.3, 0.0)
        assert loop_reads() == still, "a walk that raced the zero reached the loop"
        for _ in range(10):
            loop_reads()
        assert core.head_now == head_at_signal, "the head moved during the shutdown"

        assert not interrupted.is_set(), "setup: all of this has to happen inside the settle"
        assert interrupted.wait(timeout=daemon.SETTLE_S * 10), "the settle must end the loop"
        assert loop_reads() == still
    finally:
        for sig, handler in previous.items():
            signal_module.signal(sig, handler)


def test_state_says_the_duck_is_still_and_stopped_once_a_shutdown_has_begun(
    daemon: ModuleType,
) -> None:
    """A command already past the gate when the shutdown landed can store its walk after the
    zero. The loop reads zeros whatever is stored, but `duck.state` read the stored command and
    said `moving: true` for the whole settle, and `stop_latched` false once the latch had run
    out. State now says what the loop is doing: still, and stopped until the process ends."""
    clock = Clock()
    core = daemon.BridgeCore(now=clock)
    controller = daemon.NetworkController(core)
    hello(core)
    core.begin_shutdown()
    # what a command part way through `_apply` on the server thread stores after the zero
    core.snapshot = daemon.Snapshot(seq=99, at=clock.t, vx=core.limits.vx[1], vyaw=0.5)
    assert controller.get_last_command()[0][0] == 0.0, "setup: the loop reads zeros"
    state = core.state()
    assert state["moving"] is False and state["stop_latched"] is True, state
    clock.t += core.stop_latch_s * 10
    assert core.state()["stop_latched"] is True, "a shutdown's stop does not run out"


async def test_a_client_hears_the_bridge_is_shutting_down_and_its_moves_fail_saying_so(
    daemon: ModuleType,
) -> None:
    """Once the shutdown began, every `duck.command` was refused in silence, since a
    notification has no answer, while `duck.state` said the walk policy was running and
    `duck.health` healthy. So `move`'s preconditions passed and each intent came back accepted,
    and a move shorter than what was left of the settle reported a distance the duck never
    walked. Now the bridge answers a refused command with `duck.refused` and its reason, state
    and health say the bridge is shutting down, and the client refuses every move after it,
    saying so. The core, the server, the client and the precondition are the real ones."""
    from roboglot.transport.base import HeartbeatError, Intent
    from roboglot_open_duck.verbs import _policy_running

    core = daemon.BridgeCore()
    controller = daemon.NetworkController(core)
    server = daemon.Server(core, "127.0.0.1", 0)
    server.start()
    reader = OpenDuckBridge(f"tcp://127.0.0.1:{server.port}")
    walker = OpenDuckBridge(f"tcp://127.0.0.1:{server.port}")
    try:
        await reader.connect()
        await walker.connect()
        assert core.state()["shutting_down"] is False
        core.begin_shutdown()
        assert core.state()["shutting_down"] is True

        state = await reader.get_state()
        assert "shutting down" in str(_policy_running(state)), "the move precondition refuses"
        health = await reader.request("duck.health")
        assert health["healthy"] is False and "shutting down" in health["reason"], health
        with pytest.raises(HeartbeatError, match="shutting down"):
            await reader.heartbeat()

        # a client that only walks hears it from the refusal of its first command
        acks = []
        for _ in range(3):
            acks.append(await walker.send_intent(Intent.move(0.15, 0.0, 0.0)))
            await asyncio.sleep(0.1)
        assert not acks[-1].accepted and "shutting down" in str(acks[-1].reason), acks
        look = await walker.send_intent(Intent.look(1.0, 0.0, 0.0))
        assert not look.accepted and "shutting down" in str(look.reason)
        assert float(controller.get_last_command()[0][0]) == 0.0
    finally:
        await reader.close()
        await walker.close()
        server.stop()
        server.join(timeout=2)


def test_the_settle_ends_the_loop_with_a_keyboard_interrupt_and_a_second_signal_exits_at_once(
    daemon: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`install_settle` ends the settle with `_thread.interrupt_main()` so that a
    KeyboardInterrupt runs upstream's own cleanup and `main()`'s and the unit stops cleanly.
    But its handler is installed for SIGINT as well, and `interrupt_main` runs the installed
    SIGINT handler rather than raising: the handler saw a shutdown already asked for and took
    `os._exit(1)`, so every clean stop was a hard exit and the unit read failed. A second signal
    from a person must still exit at once, during the settle and after it. `interrupt_main`
    simulates SIGINT inside this process only, and `os._exit` is replaced by a recorder."""
    import signal as signal_module

    signals = (signal_module.SIGTERM, signal_module.SIGINT)
    previous = {s: signal_module.getsignal(s) for s in signals}
    exits: list[int] = []
    monkeypatch.setattr(daemon.os, "_exit", exits.append)
    raised = False
    try:
        # a person who asks twice, inside the settle
        never = threading.Event()
        daemon.install_settle(
            daemon.BridgeCore(), seconds=daemon.SETTLE_S * 10, interrupt=never.set
        )
        handler = signal_module.getsignal(signal_module.SIGTERM)
        assert callable(handler)
        handler(signal_module.SIGTERM, None)
        assert exits == []
        handler(signal_module.SIGINT, None)
        assert exits == [1], "a second signal must give up waiting and exit now"
        exits.clear()

        # the settle's own ending, through the default interrupt
        before = set(threading.enumerate())
        daemon.install_settle(daemon.BridgeCore(), seconds=daemon.SETTLE_S)
        settle = [
            t
            for t in threading.enumerate()
            if t not in before and t.name == "roboglot-duck-bridge-settle" and t.is_alive()
        ]
        handler = signal_module.getsignal(signal_module.SIGTERM)
        assert callable(handler)
        try:
            handler(signal_module.SIGTERM, None)
            for thread in settle:
                thread.join(timeout=daemon.SETTLE_S * 20)
            # the simulated SIGINT is run at the next check in this, the main, thread
            deadline = time.monotonic() + daemon.SETTLE_S * 4
            while time.monotonic() < deadline and not exits:
                time.sleep(0.01)
        except KeyboardInterrupt:
            raised = True
        assert raised and exits == [], (
            f"the settle ended with os._exit{tuple(exits)}, and KeyboardInterrupt raised={raised}"
        )
        handler(signal_module.SIGTERM, None)
        assert exits == [1], "a signal after the settle has ended the loop is a second one"
    finally:
        for sig, h in previous.items():
            signal_module.signal(sig, h)


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
        ex = Executor(registry_from_manifest(manifest, adapter), adapter, contract=GLOT.frontmatter)
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
        ex = Executor(registry_from_manifest(manifest, adapter), adapter, contract=GLOT.frontmatter)
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
    assert "A button" in health["reason"], "and it says why Roboglot cannot just unpause it"


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
            "",
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

    # The owner's decision of 2026-11-16: an empty token file refuses to start, as the Jetson
    # daemon's does, where it used to be no token with a warning.
    empty = tmp_path / "empty.token"
    empty.write_text("   \n")
    with pytest.raises(SystemExit) as caught:
        daemon.read_token(str(empty))
    assert "silently disabled" in str(caught.value), "an empty token must not authenticate"

    good = tmp_path / "good.token"
    good.write_text("s3cret\n")
    assert daemon.read_token(str(good)) == "s3cret"

    unreadable = tmp_path / "unreadable"  # a directory where a file is expected: EISDIR
    unreadable.mkdir()
    with pytest.raises(SystemExit) as caught:
        daemon.read_token(str(unreadable))
    assert "silently disabled" in str(caught.value)


def test_a_wide_bind_with_no_token_refuses_to_start_unless_it_is_asked_to_run_open(
    daemon: ModuleType, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The owner's decision of 2026-11-16 for every bridge. This port walks a robot, and on an
    address beyond loopback with no token the bridge warned and then served anyone on that
    network. It now refuses to start there, and names both ways out: a token, or the flag that
    starts it open on purpose. Loopback is as it was, an empty token is no token, and a token
    file named with --token-file that is missing or empty refuses to start, as the Jetson
    daemon's always has. 192.0.2.1 is an address no machine has, so nothing here listens on a
    wide address whether the refusal comes or not."""
    empty = tmp_path / "empty.token"
    empty.write_text("\n")
    with caplog.at_level(logging.ERROR, logger="roboglot-duck-bridge"):
        # an empty name is no token, on purpose
        code = daemon.main(
            ["serve", "--fake", "--bind", "192.0.2.1", "--port", "0", "--token-file", ""]
        )
    assert code == 2
    assert "--allow-no-token" in caplog.text and "--token-file" in caplog.text

    refusal = daemon.wide_bind_refusal
    assert refusal("192.0.2.1", None, allow_no_token=False) is not None
    assert refusal("192.0.2.1", None, allow_no_token=True) is None, "open, on purpose"
    assert refusal("192.0.2.1", "s3cret", allow_no_token=False) is None
    for loopback in ("127.0.0.1", "localhost"):
        assert refusal(loopback, None, allow_no_token=False) is None, "loopback is as it was"
    assert daemon.parser().parse_args(["serve"]).allow_no_token is False

    for named in (tmp_path / "never-written.token", empty):
        with pytest.raises(SystemExit) as caught:
            daemon.read_token(str(named), named=True)
        assert "Refusing to start" in str(caught.value) and str(named) in str(caught.value)
    assert daemon.read_token(str(tmp_path / "never-written.token"), named=False) is None


def test_every_loopback_spelling_starts_as_it_did_and_the_refusal_says_only_what_is_true(
    daemon: ModuleType,
) -> None:
    """The refusal compared the address with ("127.0.0.1", "localhost"), so 127.0.0.2, `127.1`,
    `LOCALHOST` and `::1`, all of them loopback, refused to start, saying anything on this
    network could walk the duck, where the bridge had served there. Loopback is what the
    address module calls loopback, and the name localhost in any case; every other address is
    refused, and the refusal says of it only that it is not loopback."""
    refusal = daemon.wide_bind_refusal
    for loopback in (
        "127.0.0.1",
        "127.0.0.2",
        "127.1",
        "localhost",
        "LOCALHOST",
        "LocalHost",
        "::1",
    ):
        assert refusal(loopback, None, allow_no_token=False) is None, loopback
    for wide in ("0.0.0.0", "::", "", "0", "192.168.1.20", "duck.local", "127.0.0.1.example"):
        said = refusal(wide, None, allow_no_token=False)
        assert said is not None and "not a loopback address" in said, wide
        assert "this network" not in said, said


def test_the_empty_token_file_refusal_names_a_way_out_that_starts(
    daemon: ModuleType, tmp_path: Path
) -> None:
    """An empty token file refused with "remove it to run with no token on purpose", and a file
    named with --token-file, as install.sh's unit now names every one, then refused again as
    missing. Each refusal now names the way out that starts: `--token-file ""` for a named file,
    deleting it for the default path."""
    path = tmp_path / "duck-bridge.token"
    path.write_text("  \n", encoding="utf-8")
    with pytest.raises(SystemExit) as named:
        daemon.read_token(str(path), named=True)
    assert '--token-file ""' in str(named.value) and "remove it" not in str(named.value)
    assert daemon.read_token("", named=True) is None, "the way out it names"
    with pytest.raises(SystemExit) as default:
        daemon.read_token(str(path), named=False)
    assert "delete it" in str(default.value)
    path.unlink()
    assert daemon.read_token(str(path), named=False) is None, "the way out it names"


def test_a_token_file_that_is_not_utf8_refuses_with_a_reason(
    daemon: ModuleType, tmp_path: Path
) -> None:
    """It refused to start, which is safe, but with a UnicodeDecodeError traceback rather than a
    sentence that names the file and what is wrong with it."""
    path = tmp_path / "duck-bridge.token"
    path.write_bytes(b"\xff\xfe\x00token")
    with pytest.raises(SystemExit) as refused:
        daemon.read_token(str(path), named=True)
    assert str(path) in str(refused.value) and "UTF-8" in str(refused.value)


@pytest.mark.parametrize("flag", ["--max-vx", "--max-vy", "--max-vyaw"])
def test_a_speed_limit_that_is_not_a_finite_number_above_nought_refuses_to_start(
    daemon: ModuleType, flag: str
) -> None:
    """`abs(nan) > bound` is False, so `--max-vx nan` passed the start check, the limit became
    (nan, nan), and every command then reached the walk loop as NaN, because a clamp to a bound
    that is not a number is not a number. A limit must be a finite number above nought: a
    negative one used to be taken for its size, and nought, inf and NaN refuse too."""
    for value in ("nan", "inf", "-inf", "0", "-0.1"):
        args = daemon.parser().parse_args(
            ["serve", "--fake", "--token-file", "", f"{flag}={value}"]
        )
        with pytest.raises(SystemExit) as refused:
            daemon.build_core(args)
        assert flag in str(refused.value) and "finite number above 0" in str(refused.value)


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

# The boot is counted as the daemon counts its patch watchdog: from the moment the watchdog
# was armed (run.py notes it), on the same clock, as a share of the same budget. The child's
# own start comes before that moment and is charged to neither.
armed_at = float(os.environ["WATCHDOG_ARMED_AT"])
budget_s = float(os.environ["WATCHDOG_BUDGET_S"])
# Read before the boot, as a walk script reads its numpy at the top, rather than by the pad's
# constructor after it: the daemon reads numpy only when it builds the pad. So what is left of
# the budget after the boot covers building the pad, not a first read of modules from a disk
# that a loaded machine is busy with.
import numpy
import mini_bdx_runtime.buttons

time.sleep(max(0.0, armed_at + BOOT_SHARE * budget_s - time.monotonic()))
from mini_bdx_runtime.xbox_controller import XBoxController

pad = XBoxController(20)
# Upstream holds its pad, so the boot is over and the watchdog has nothing left to wait for.
# This is the fact a test of the watchdog waits on, with how long the boot took.
part = os.environ["BOOTED"] + ".part"
with open(part, "w") as fh:
    json.dump(time.monotonic() - armed_at, fh)
os.replace(part, os.environ["BOOTED"])
seen = []
# Written every tick rather than in a finally: Windows terminate() is TerminateProcess, so
# no handler runs and no exit path is guaranteed. Ticks on disk while it runs is also the
# stronger claim - it proves the loop is being fed, not merely that it once exited tidily.
while True:
    cmds, buttons, lt, rt = pad.get_last_command()
    seen.append([float(c) for c in cmds])
    buttons.a_button_upstream_added_later.triggered
    # Written to a sibling and moved into place, because the reader is another process
    # polling this path. Truncating in place let it observe a half-written file, which is
    # a JSONDecodeError on a good day and a flaky CI job on a bad one. os.replace is
    # atomic on POSIX and Windows alike.
    tmp = os.environ["TICKS"] + ".part"
    with open(tmp, "w") as fh:
        json.dump(seen[-40:], fh)
    os.replace(tmp, os.environ["TICKS"])
    time.sleep(0.02)
"""


def _fake_runtime(root: Path, *, boot_share: float = 0.0) -> Path:
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
    script.write_text(FAKE_LOOP.replace("BOOT_SHARE", repr(boot_share)))
    return script


#: Where the child bridge's stderr goes, beside the files it writes, so that a test can show it
#: when the child ends early. A file rather than a pipe: nothing reads a pipe while the test
#: waits, and a full one would stall the child.
BRIDGE_STDERR = "bridge.stderr"
#: How long a test gives its child bridge to show what the test asserts before calling it
#: hung. Generous because the test waits on that fact, so a quick child costs nothing, and well
#: inside pyproject's faulthandler_timeout of 300 s, so a hung child fails here with its stderr
#: rather than as a hung test.
CHILD_DEADLINE_S = 60.0
#: How often a test looks for what its child writes: `_read_from_child`'s retry period.
CHILD_POLL_S = 0.05


def _serve_in_subprocess(tmp_path: Path, script: Path, watchdog_s: str) -> subprocess.Popen:
    runner = tmp_path / "run.py"
    runner.write_text(
        "import os, sys, time, importlib.util\n"
        f"sys.path.insert(0, {str(tmp_path)!r})\n"
        f'spec = importlib.util.spec_from_file_location("qdb", {str(DAEMON)!r})\n'
        'm = importlib.util.module_from_spec(spec); sys.modules["qdb"] = m\n'
        "spec.loader.exec_module(m)\n"
        # The moment the daemon arms its patch watchdog, and the budget it gives it, noted for
        # the stand-in's boot, which runs in this process and is timed from that moment.
        "watchdog = m.watchdog\n"
        "def armed(core, seconds, server=None):\n"
        '    os.environ["WATCHDOG_ARMED_AT"] = repr(time.monotonic())\n'
        '    os.environ["WATCHDOG_BUDGET_S"] = repr(seconds)\n'
        "    watchdog(core, seconds, server)\n"
        "m.watchdog = armed\n"
        "sys.exit(m.main(["
        f'"serve", "--script", {str(script)!r}, "--port", "0",'
        '"--token-file", "",'
        f'"--patch-watchdog-s", "{watchdog_s}",'
        "]))\n"
    )
    env = {
        **os.environ,
        "TICKS": str(tmp_path / "ticks.json"),
        "BOOTED": str(tmp_path / "booted.json"),
        "PYTHONPATH": str(tmp_path),
    }
    with (tmp_path / BRIDGE_STDERR).open("wb") as stderr:
        return subprocess.Popen([sys.executable, "-X", "utf8", str(runner)], env=env, stderr=stderr)


def _wait_for_child(proc: subprocess.Popen, path: Path, what: str) -> None:
    """Wait until the child bridge has written `path`. The tests that start one slept a fixed
    time and then looked, and a loaded machine that took longer than that to start a Python
    failed them with nothing wrong. A child that ends first fails at once with its exit code
    and stderr, and one still silent at CHILD_DEADLINE_S fails saying so."""
    deadline = time.monotonic() + CHILD_DEADLINE_S
    while not path.exists():
        if proc.poll() is not None and not path.exists():
            pytest.fail(
                f"the bridge exited with {proc.returncode} before {what}; its stderr:\n"
                + _child_stderr(path.parent)
            )
        if time.monotonic() >= deadline:
            pytest.fail(
                f"the bridge ran {CHILD_DEADLINE_S:.0f} s and {what} did not happen; its "
                "stderr:\n" + _child_stderr(path.parent)
            )
        time.sleep(CHILD_POLL_S)


def _child_stderr(folder: Path) -> str:
    return (folder / BRIDGE_STDERR).read_text(encoding="utf-8", errors="replace")


@pytest.mark.serial
def test_the_shim_intercepts_the_import_form_upstream_actually_uses(tmp_path: Path) -> None:
    """PAD_SUBSTITUTION is the single assumption the whole bridge stands on, and nothing
    executed `install_shim`, `runpy` or `main()`'s serve path: NetworkController was always
    constructed directly, so the rebind itself had no test at all.

    It slept a fixed three seconds and then looked for the ticks, and a child Python that a
    loaded machine took longer than that to start had not ticked yet. It waits for the ticks
    now, and reads them once the child is stopped, since a reader holding the file open on
    Windows would make the stand-in's own replace of it fail."""
    script = _fake_runtime(tmp_path)
    proc = _serve_in_subprocess(tmp_path, script, "10")
    ticks = tmp_path / "ticks.json"
    try:
        _wait_for_child(proc, ticks, "upstream's loop wrote a tick")
        assert proc.poll() is None, f"the bridge exited early with {proc.returncode}"
    finally:
        proc.terminate()
        proc.wait(timeout=15)
    seen = _read_from_child(ticks)
    assert seen and all(len(v) == 7 for v in seen), "seven floats, every tick"


def _read_from_child(path: Path, *, within_s: float = 3.0) -> Any:
    """What the fake loop last moved into place. On Windows the file can stay held for a
    moment after the writer is gone, by its last replace or by a scanner opening a file that has
    just appeared, and a read in that moment raises PermissionError. It failed a CI job once
    (PR #74's py3.11 Windows run, 2026-11-06), so the read waits for the file as it would for a
    slow one, and a file still held after `within_s` fails as before."""
    deadline = time.monotonic() + within_s
    while True:
        try:
            return json.loads(path.read_text())
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(CHILD_POLL_S)


#: The patch watchdog's budget in the slow boot test, in the seconds the daemon counts.
SLOW_BOOT_WATCHDOG_S = 6.0
#: The stand-in's boot in that test, as a share of the budget counted from the moment the
#: watchdog is armed: midway between the half at which the daemon warns of a slow boot and
#: the whole at which it exits.
SLOW_BOOT_SHARE = 0.75


def test_a_slow_boot_is_not_mistaken_for_a_missing_shim(tmp_path: Path) -> None:
    """The watchdog was a fixed 20 s covering onnxruntime, the Feetech bus, a hard two second
    settle and the IMU on a 512 MB Pi with a cold page cache — and it exits with os._exit
    into fourteen energised servos.

    It slept a fixed four seconds from starting the child, against a 1.5 s boot and a 6 s
    watchdog that both count from a later moment, the one at which the daemon arms the
    watchdog. So the child's own start was charged to the test's clock and to neither of
    theirs, and on a slow machine the test looked before the boot was over and proved
    nothing. The stand-in now boots for SLOW_BOOT_SHARE of the budget it is given, counted
    from that moment on the daemon's clock, so the boot is past half the budget and short of
    the whole however long the child took to get there, and the test waits for the stand-in
    to say its boot is over before it asks whether the child is still alive."""
    script = _fake_runtime(tmp_path, boot_share=SLOW_BOOT_SHARE)
    proc = _serve_in_subprocess(tmp_path, script, str(SLOW_BOOT_WATCHDOG_S))
    booted = tmp_path / "booted.json"
    try:
        _wait_for_child(proc, booted, "the stand-in's boot was over")
        assert proc.poll() is None, "a boot slower than half the budget must not be killed"
    finally:
        proc.terminate()
        proc.wait(timeout=15)
    boot_s = _read_from_child(booted)
    assert boot_s > SLOW_BOOT_WATCHDOG_S / 2, (
        f"the boot took {boot_s:.2f} s of a {SLOW_BOOT_WATCHDOG_S:g} s budget, so it was not "
        "the slow boot this test is about"
    )


#: How often the margin test samples the bridge's command age. Its sleep is also how the test
#: knows whether the machine ran its event loop, which the client's resends share.
SAMPLE_PERIOD_S = 0.02
#: How late, in sampling periods, the loop may wake within an age for that age to be judged.
#: The bound leaves the client half the deadman less MOVE_RESEND_S, 50 ms or two and a half
#: periods, and the test takes the loop's own lateness off an age as the machine's: up to that
#: slack, and no further, since forgiving more would forgive a client that had used it all.
#: An age whose span holds a later wake was a loop the machine starved, and is not judged.
STARVED_AFTER_PERIODS = 2.5
#: More ages than this must be judged for the sampler to have actually looked: the floor the
#: test has always held for its one walk.
MIN_JUDGED_AGES = 20
#: How many one second walks the test may take to judge more than MIN_JUDGED_AGES ages. A
#: machine that starves the loop through five walks running cannot hold a serial test's margin
#: at all, and saying so is truer than a verdict on the client.
MARGIN_ATTEMPTS = 5


@pytest.mark.serial
async def test_the_client_keeps_the_deadman_fed_with_margin(daemon: ModuleType) -> None:
    """The one real-socket test moved for exactly DEADMAN_S, so `vx == 0` at the end passed
    whether the verb's own stop landed or the client had simply starved the deadman. Sample
    the age *while* it walks instead, and require real margin rather than a survivable miss.

    On a loaded machine the age also grew whenever the machine woke this test's event loop
    late, and the client's resends wait in that loop beside the sampler, so the test blamed the
    client for the machine. The sampler times its own sleeps now. The latest any of them woke
    within an age is the machine's share of it and comes off before the bound is applied, and
    an age within which one woke more than STARVED_AFTER_PERIODS periods late is not judged at
    all. The walk is taken again until more than MIN_JUDGED_AGES ages are judged,
    MARGIN_ATTEMPTS times at most, and a machine that never let the loop keep time fails the
    test saying so."""
    core = daemon.BridgeCore(capabilities={"camera": False, "speaker": False})
    server = daemon.Server(core, "127.0.0.1", 0)
    server.start()
    try:
        adapter = OpenDuckAdapter(OpenDuckBridge(f"tcp://127.0.0.1:{server.port}"))
        manifest = await adapter.connect()
        ex = Executor(registry_from_manifest(manifest, adapter), adapter, contract=GLOT.frontmatter)
        budget = core.deadman_s * 1000
        starved_after_s = STARVED_AFTER_PERIODS * SAMPLE_PERIOD_S

        async def walk() -> tuple[list[tuple[float, int]], list[tuple[float, float]]]:
            """Each age with when it was read, and each wake of the sampler's with how late it
            came, both on perf_counter's clock."""
            ages: list[tuple[float, int]] = []
            wakes: list[tuple[float, float]] = []

            async def sample() -> None:
                # Only once the duck is actually being driven. Before the first command the age
                # is measured from connect, which says nothing about how the verb feeds it.
                start_seq = core.snapshot.seq
                while True:
                    if core.snapshot.seq > start_seq:
                        ages.append((time.perf_counter(), core.state()["command_age_ms"]))
                    asked = time.perf_counter()
                    await asyncio.sleep(SAMPLE_PERIOD_S)
                    woke = time.perf_counter()
                    wakes.append((woke, woke - asked - SAMPLE_PERIOD_S))

            watcher = asyncio.create_task(sample())
            try:
                assert (await ex.run_verb("move", {"vx": 0.1, "duration_s": 1.0})).ok
            finally:
                watcher.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await watcher
            return ages, wakes

        # each judged age: what the bridge read, and how late the loop woke within it, in ms
        judged: list[tuple[int, float]] = []
        excused = 0
        for _ in range(MARGIN_ATTEMPTS):
            ages, wakes = await walk()
            for at, age in ages:
                # The age's span, from when the last command arrived, and one period more for
                # the stall the sampler cannot see and the age's own clock, which can be coarser.
                since = at - age / 1000 - SAMPLE_PERIOD_S
                late = max((lag for woke, lag in wakes if since <= woke <= at), default=0.0)
                if late <= starved_after_s:
                    judged.append((age, max(late, 0.0) * 1000))
                else:
                    excused += 1
            worst = max((a - m for a, m in judged), default=0.0)
            if len(judged) > MIN_JUDGED_AGES or worst >= budget * 0.5:
                break
        else:
            pytest.fail(
                f"no measurement could be made: in {MARGIN_ATTEMPTS} walks the sampler read "
                f"{excused + len(judged)} ages, and within {excused} of them this machine woke a "
                f"{SAMPLE_PERIOD_S * 1000:.0f} ms sleep of the test's event loop more than "
                f"{starved_after_s * 1000:.0f} ms late, which leaves {len(judged)} to judge the "
                f"client by, and the sampler has to have actually looked at more than "
                f"{MIN_JUDGED_AGES}"
            )

        age, machine = max(judged, key=lambda j: j[0] - j[1])
        assert age - machine < budget * 0.5, (
            f"the worst command age was {age} ms against a {budget:.0f} ms deadman, and the "
            f"test's event loop woke at most {machine:.0f} ms late within it, which leaves "
            f"{age - machine:.0f} ms that are the client's; it is not feeding the deadman with "
            "margin"
        )
        await adapter.disconnect()
    finally:
        server.stop()
        server.join(timeout=2)
