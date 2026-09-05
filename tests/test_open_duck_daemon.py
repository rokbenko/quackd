"""The bridge daemon that runs on the duck's Pi, exercised with no Pi and no duck.

The daemon is stdlib plus numpy on purpose, so the parts that matter most (the deadman, the
clamps, the protocol, and the fact that nothing can reach torque) are all testable here. The
last test in this file drives the real daemon and the real client against each other over
loopback, which is the only way to know the two halves agree.
"""

from __future__ import annotations

import asyncio
import importlib.util
import subprocess
import sys
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
    held = fresh.head

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
    core = daemon.BridgeCore(limits=daemon.Limits(head_enabled=True))
    hello(core)
    ceiling = daemon.HEAD_YAW[1] * daemon.HEAD_SAFETY
    for _ in range(50):  # the slew limit means it takes several commands to get there
        core.handle({"method": "duck.command", "params": {"head": {"head_yaw": 9.0}}}, authed=True)
    yaw = core.command_for_tick().head[2]
    assert yaw == pytest.approx(ceiling)
    assert ceiling < daemon.HEAD_YAW[1], "quackd stays inside upstream's own clamp"

    core2 = daemon.BridgeCore(limits=daemon.Limits(head_enabled=True))
    hello(core2)
    core2.handle({"method": "duck.command", "params": {"head": {"head_yaw": 9.0}}}, authed=True)
    assert core2.command_for_tick().head[2] < ceiling, "one command cannot snap the neck"


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

        assert (await ex.run_verb("move", {"vx": 0.1, "duration_s": 0.3})).ok
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


def test_the_deadman_rests_the_antennas_too(daemon: ModuleType) -> None:
    clock = Clock()
    core = daemon.BridgeCore(now=clock, capabilities={"antennas": True})
    hello(core)
    core.handle({"id": 8, "method": "duck.antennas", "params": {"gesture": "perk"}}, authed=True)
    assert core.command_for_tick().triggers == (1.0, 1.0)
    clock.t += core.deadman_s + 0.01
    assert core.command_for_tick().triggers == (0.0, 0.0)


# ── a camera nobody can fetch a frame from is not a camera ──────────────────────────────


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
