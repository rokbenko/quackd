"""Which detector a run uses, who chose it, and where the run says so.

The policy: the board's detector is used by itself only on a real body whose `--host` daemon can
detect, never on a simulator; `--detector color` opts out, `--detector host` is honoured even on
a simulator, and `--detector yolo` runs YOLO on this machine. A choice that cannot run here is
refused before anything connects, as is a `--host` whose daemon does not answer. The header, the
record and the MCP server all name the detector.

The board is the fake daemon in `tests/fake_jetson_hostd.py`; nothing here has seen a Jetson.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from typer.testing import CliRunner

from quackd import host as host_module
from quackd.adapters.host_camera import HostCameraAdapter
from quackd.agent.loop import RunConfig, run_duck
from quackd.agent.providers.base import ToolCall
from quackd.agent.providers.fake import FakeProvider
from quackd.cli import app
from quackd.duckfile.parser import parse_duck_text
from quackd.host import HOST_ENV, HostClient, HostHello
from quackd.log import LogEvent, render_lines
from quackd.mcp_server import build_fleet_server, fleet_from_flags
from quackd.perception import detector_for, explicit_detector, lens
from quackd.perception.color_blob import ColorBlobDetector
from quackd.perception.host import HostDetector
from quackd.perception.yolo import YoloDetector
from quackd.registry import Registry, RobotEntry
from quackd.transport.mock import MockTransport
from quackd_toddlerbot import ToddlerBotAdapter
from quackd_toddlerbot.mock import ToddlerBotMock
from tests.fake_jetson_hostd import FakeHostd, dead_address
from tests.test_mcp_fleet import _data, connected

runner = CliRunner()

REAL = ("bridge", "real", "ws", "zmq", "jsonrpc", "websocket")
SIMULATED = ("sim2d", "mujoco", "mock")


@pytest.fixture
def hostd() -> Iterator[FakeHostd]:
    with FakeHostd() as fake:
        yield fake


@pytest.fixture
def quick_hello(monkeypatch: pytest.MonkeyPatch) -> None:
    """A board that is not there costs one hello timeout, and on Windows a refused loopback
    connect takes about the whole of it; a shorter one keeps the refusals fast to test."""
    monkeypatch.setattr(host_module, "HELLO_TIMEOUT_S", 0.3)


@pytest.fixture
def nothing_connects(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """Every body the CLI asks to have built, recorded, and refused: a refusal that comes after
    one was built came after the point where a real body would have been reached."""
    built: list[Any] = []

    def refuse(spec: Any, **kw: Any) -> Any:
        built.append(spec)
        raise AssertionError(f"{spec} was built before the refusal")

    monkeypatch.setattr("quackd.flock.pilots.make_adapter", refuse)
    monkeypatch.setattr("quackd.adapters.factory.make_adapter", refuse)
    return built


def _board(hostd: FakeHostd) -> tuple[HostClient, HostHello]:
    client = HostClient(hostd.address)
    return client, client.hello()


def _run(tmp_path: Path, *args: str, log: bool = False, task: str = "hello-world") -> Any:
    return runner.invoke(
        app,
        [
            "run",
            task,
            "--llm",
            "fake",
            "--no-gif",
            "--log" if log else "--no-log",
            "--runs-dir",
            str(tmp_path / "runs"),
            "--memory-dir",
            str(tmp_path / "mem"),
            "--registry-dir",
            str(tmp_path / "reg"),
            *args,
        ],
        env={"COLUMNS": "200"},
    )


def _flat(result: Any) -> str:
    return " ".join(result.output.split())


def _run_line(start: dict[str, Any]) -> str:
    """The `run` line `render_lines` draws for this `run_start`: what `quackd log` prints and an
    MCP result carries. The live console draws a panel instead, so it is read from the record.
    The connect time is pinned so the line can be compared whole."""
    event = LogEvent("run_start", 0.0, {**start, "connect_s": 0.0})
    return str(render_lines(event, prompt=False)[0][0])


def _start(tmp_path: Path) -> dict[str, Any]:
    transcript = next((tmp_path / "runs").rglob("transcript.jsonl"))
    first = json.loads(transcript.read_text(encoding="utf-8").splitlines()[0])
    assert first["kind"] == "run_start"
    return dict(first)


class _BridgeMock(ToddlerBotMock):
    """The ToddlerBot's mock standing in for its daemon on the `bridge` backend, so quackd
    treats it as the real body it stands for: `describe("bridge")` names no camera, and the
    daemon reports one at connect when it owns one, exactly as the real bridge does."""

    name = "bridge"


def _toddlerbot_on_its_bridge(monkeypatch: pytest.MonkeyPatch, *, camera: bool) -> None:
    """Every body `run` or `serve-mcp` builds is a ToddlerBot on its bridge with the mock
    behind it, wrapped with the board's camera the way `make_adapter` wraps any body."""

    def build(spec: Any, **kw: Any) -> Any:
        body = ToddlerBotAdapter(_BridgeMock(camera=camera))
        host = kw.get("host")
        if host is not None and (hello := host.hello()).has_camera:
            return HostCameraAdapter(body, host, hello)
        return body

    monkeypatch.setattr("quackd.adapters.factory.make_adapter", build)


def _loops(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """Every loop `run` starts, kept, so a test can read the detector it ended with."""
    from quackd.agent import loop as loop_module

    made: list[Any] = []

    class Kept(loop_module.AgentLoop):
        def __init__(self, *args: Any, **kw: Any) -> None:
            super().__init__(*args, **kw)
            made.append(self)

    monkeypatch.setattr(loop_module, "AgentLoop", Kept)
    return made


def _task(tmp_path: Path, *, look: bool) -> str:
    """A task file for the ToddlerBot: one that needs a camera, or one that needs none."""
    path = tmp_path / ("look.duck" if look else "wait.duck")
    needs = "requires: [observe]\nverbs:\n  allow: [observe, stop]\n"
    if not look:
        needs = "verbs:\n  allow: [report_state, stop]\n"
    path.write_text(
        f"---\nduck: 1\nname: {path.stem}\ndescription: d\n{needs}success: [x]\n---\n# Task\n"
        "Look around.\n",
        encoding="utf-8",
    )
    return str(path)


def _lens_warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        r.getMessage()
        for r in caplog.records
        if r.name == "quackd.perception" and "field of view" in r.getMessage()
    ]


# ── the policy ──────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("backend", REAL)
def test_the_board_detects_by_itself_on_a_real_body(hostd: FakeHostd, backend: str) -> None:
    client, hello = _board(hostd)
    chosen = explicit_detector(None, client=client, hello=hello, fov_deg=62.2, backend=backend)
    assert isinstance(chosen, HostDetector)
    assert (chosen.fov_deg, chosen.calibrated) == (62.2, True)


@pytest.mark.parametrize("backend", [*SIMULATED, None])
def test_the_board_never_detects_by_itself_on_a_simulator(
    hostd: FakeHostd, backend: str | None
) -> None:
    """YOLO does not see a cartoon ball, and the colour detector is tuned for exactly what a
    simulator draws, so a board that can detect still leaves a simulator to the colour one."""
    client, hello = _board(hostd)
    assert (
        explicit_detector(None, client=client, hello=hello, fov_deg=None, backend=backend) is None
    )


def test_a_board_that_cannot_detect_is_never_chosen_by_itself(hostd: FakeHostd) -> None:
    hostd.without_detector()
    client, hello = _board(hostd)
    assert explicit_detector(None, client=client, hello=hello, fov_deg=None, backend="real") is None
    assert explicit_detector(None, client=None, hello=None, fov_deg=None, backend="real") is None


def test_detector_color_opts_out_of_the_boards_detector(hostd: FakeHostd) -> None:
    client, hello = _board(hostd)
    kept = explicit_detector("color", client=client, hello=hello, fov_deg=62.2, backend="bridge")
    assert kept is None
    fallback = detector_for(["camera"], kept, fov_deg=62.2, backend="bridge")
    assert isinstance(fallback, ColorBlobDetector)


def test_detector_host_is_honoured_on_a_simulator(hostd: FakeHostd) -> None:
    """Asked for by name it runs, and the simulator's own lens is known, so it is calibrated."""
    client, hello = _board(hostd)
    chosen = explicit_detector("host", client=client, hello=hello, fov_deg=None, backend="sim2d")
    assert isinstance(chosen, HostDetector)
    assert (chosen.fov_deg, chosen.calibrated) == (90.0, True)


def test_detector_host_says_why_it_cannot_run(hostd: FakeHostd) -> None:
    with pytest.raises(ValueError, match="no board is named: add --host"):
        explicit_detector("host", client=None, hello=None, fov_deg=None, backend="real")
    hostd.without_detector("ultralytics is not installed on this board")
    client, hello = _board(hostd)
    with pytest.raises(
        ValueError,
        match=r"cannot detect \(detect=false: ultralytics is not installed on this board\)",
    ):
        explicit_detector("host", client=client, hello=hello, fov_deg=None, backend="real")


# ── run: refused before anything connects ───────────────────────────────────────────────


def test_detector_host_without_detect_is_refused_before_connecting(
    hostd: FakeHostd, tmp_path: Path, nothing_connects: list[Any]
) -> None:
    hostd.without_detector()
    result = _run(
        tmp_path, "--robot", "microduck:mock", "--host", hostd.address, "--detector", "host"
    )
    assert result.exit_code == 1, result.output
    flat = _flat(result)
    assert f"the daemon at {hostd.address} cannot detect" in flat, flat
    assert "detect=false: started with --no-detect" in flat
    assert nothing_connects == []
    assert not (tmp_path / "runs").exists(), "the refusal came after the run directory"


def test_detector_host_without_a_board_is_refused_before_connecting(
    tmp_path: Path, nothing_connects: list[Any]
) -> None:
    result = _run(tmp_path, "--robot", "microduck:mock", "--detector", "host")
    assert result.exit_code == 1, result.output
    assert "no board is named: add --host" in _flat(result)
    assert nothing_connects == []


def test_detector_yolo_without_the_extra_names_the_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, nothing_connects: list[Any]
) -> None:
    monkeypatch.setitem(sys.modules, "ultralytics", None)
    result = _run(tmp_path, "--robot", "microduck:mock", "--detector", "yolo")
    assert result.exit_code == 1, result.output
    assert "uv pip install 'quackd[yolo]'" in _flat(result)
    assert nothing_connects == []


def test_a_detector_nobody_offers_is_refused(tmp_path: Path, nothing_connects: list[Any]) -> None:
    result = _run(tmp_path, "--robot", "microduck:mock", "--detector", "sonar")
    assert result.exit_code == 1, result.output
    assert "--detector is one of color, host, yolo, not 'sonar'" in _flat(result)
    assert nothing_connects == []


@pytest.mark.parametrize("robots", ["a=microduck:mock,b=microduck:mock", "a=microduck:mock"])
def test_a_detector_is_for_one_robot(
    tmp_path: Path, nothing_connects: list[Any], robots: str
) -> None:
    """Every flock builds its members' detectors itself, so a choice here would be dropped
    without a word; it is refused instead, as `--image` is.

    `--robots` with one member is refused too. It is the fleet spelling, as it is for `--host`,
    and `serve-mcp` serves it as a fleet and refuses it there: ADR-0046 and the CHANGELOG say
    both commands refuse `--detector` for `--robots`, and `run` once took it from one member."""
    result = _run(tmp_path, "--robots", robots, "--detector", "color")
    assert result.exit_code == 1, result.output
    assert "--detector is for one robot, and a fleet has several bodies" in _flat(result)
    assert nothing_connects == []


def test_a_daemon_that_is_down_refuses_the_run_before_the_body_moves(
    tmp_path: Path, nothing_connects: list[Any], quick_hello: None
) -> None:
    """A dry run moves nothing and would still have been told it had a camera and a detector
    it did not have, so the board not answering refuses it too."""
    result = _run(tmp_path, "--robot", "microduck:mock", "--host", "127.0.0.1:1", "--dry-run")
    assert result.exit_code == 1, result.output
    flat = _flat(result)
    assert "--host 127.0.0.1:1 did not answer" in flat, flat
    assert "drop --host to run without it" in flat
    assert nothing_connects == []
    assert not (tmp_path / "runs").exists()


def test_a_board_nobody_typed_is_refused_by_where_it_was_named(
    tmp_path: Path,
    nothing_connects: list[Any],
    quick_hello: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A board stored with the robot, or set in the environment, did not come from `--host`,
    and the refusal says where it did come from and how to run without it from there."""
    dead = dead_address()
    Registry(tmp_path / "reg").add_robot(RobotEntry(name="jet", spec="microduck:mock", host=dead))
    stored = _flat(_run(tmp_path, "--robot", "jet"))
    assert f"the host {dead} from robot jet (robots.json) did not answer" in stored, stored
    assert "quackd robot edit jet --clear host to run without it" in stored
    # no stored token to carry, so the doctor it names asks the board and leaves the body alone
    assert f"quackd doctor --host {dead} shows what the board says" in stored
    monkeypatch.setenv(HOST_ENV, dead)
    usual = _flat(_run(tmp_path, "--robot", "microduck:mock"))
    assert f"the host {dead} from {HOST_ENV} did not answer" in usual, usual
    assert f"unset {HOST_ENV} to run without it" in usual
    assert nothing_connects == []


# ── naming it ───────────────────────────────────────────────────────────────────────────


def test_the_header_and_run_start_name_the_boards_detector(
    hostd: FakeHostd, tmp_path: Path
) -> None:
    """Asked for on a simulator, which the header says, beside the board: its daemon, and what
    it has. The record keeps the same, with the role the board's camera took at connect, and
    the log's run line names the detector."""
    result = _run(
        tmp_path,
        "--robot",
        "microduck:mock",
        "--host",
        hostd.address,
        "--detector",
        "host",
        log=True,
    )
    assert result.exit_code == 0, result.output
    flat = _flat(result)
    assert (
        f"detector yolo@host {hostd.address} yolov8n.pt on cuda (asked for on a simulator)" in flat
    ), flat
    assert f"host {hostd.address} daemon 0.1.0 camera as an extra view, detect, tegra" in flat
    start = _start(tmp_path)
    assert _run_line(start).endswith("robot=microduck:mock detector=yolo@host connected in 0.00 s")
    assert start["detector"] == "yolo@host"
    assert start["host"] == {
        "address": hostd.address,
        "daemon_version": "0.1.0",
        "capabilities": {"camera": True, "detect": True, "tegra": True},
        "detect": {"model": "yolov8n.pt", "device": "cuda"},
        "camera_role": "extra view",
    }
    assert hostd.requests_to("/detect"), "and the frames went to the board"


def test_a_simulator_with_a_board_keeps_the_colour_detector_and_says_so(
    hostd: FakeHostd, tmp_path: Path
) -> None:
    result = _run(tmp_path, "--robot", "microduck:mock", "--host", hostd.address, log=True)
    assert result.exit_code == 0, result.output
    flat = _flat(result)
    assert "detector color_blob on this machine" in flat, flat
    start = _start(tmp_path)
    line = _run_line(start)
    assert "robot=microduck:mock connected in" in line, line
    assert "detector=" not in line, "the colour detector is never named on the run line"
    assert start["detector"] == "color_blob" and start["host"]["address"] == hostd.address
    assert hostd.requests_to("/detect") == []
    assert hostd.requests_to("/snapshot.jpg"), "the board's camera is still an extra view"


def test_a_run_without_a_board_names_the_colour_detector_and_no_host(tmp_path: Path) -> None:
    result = _run(tmp_path, "--robot", "microduck:mock")
    assert result.exit_code == 0, result.output
    assert "detector color_blob on this machine" in _flat(result)
    start = _start(tmp_path)
    assert start["detector"] == "color_blob" and "host" not in start


# ── a body described blind ──────────────────────────────────────────────────────────────


def test_a_body_described_blind_that_reports_its_own_camera_is_measured_through_its_own_lens(
    hostd: FakeHostd,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The ToddlerBot's bridge describes no camera, and its daemon reports one at connect, so
    the board's camera is then an extra view and its 62.2 degrees say nothing about the lens
    the detections come through. Nobody knows which camera is primary until the body connects.
    A colour detector built before then with the board's lens kept it, marked calibrated and
    with no warning, which is not what the same body gets without --host. It is built at
    connect now, through the body's lens, which nobody gave: the guess, uncalibrated, and the
    warning. The header says the board's camera is primary only if the body has none of its
    own, and the record says which it was."""
    caplog.set_level(logging.WARNING, logger="quackd.perception")
    hostd.without_detector()
    _toddlerbot_on_its_bridge(monkeypatch, camera=True)
    loops = _loops(monkeypatch)
    task = _task(tmp_path, look=True)
    result = _run(
        tmp_path, "--robot", "toddlerbot:bridge", "--host", hostd.address, "--yes", task=task
    )
    assert result.exit_code == 0, result.output
    flat = _flat(result)
    assert "camera as the primary view unless the body reports its own" in flat, flat
    (loop,) = loops
    detector = loop.cfg.detector
    assert isinstance(detector, ColorBlobDetector)
    assert (detector.fov_deg, detector.calibrated) == (90.0, False), "the body's lens, a guess"
    (told,) = _lens_warnings(caplog)
    assert "for a bridge camera" in told
    start = _start(tmp_path)
    assert start["detector"] == "color_blob"
    assert start["host"]["camera_role"] == "extra view"
    assert hostd.requests_to("/snapshot.jpg"), "the board's frames are still shown"


def test_a_body_that_connects_blind_takes_the_boards_lens_and_the_record_says_primary(
    hostd: FakeHostd, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other side of the same rule: a ToddlerBot whose daemon reports no camera has the
    board's as its only one, and the colour detector built at connect measures through the
    lens the board's daemon was started with."""
    hostd.without_detector()
    _toddlerbot_on_its_bridge(monkeypatch, camera=False)
    loops = _loops(monkeypatch)
    task = _task(tmp_path, look=True)
    result = _run(
        tmp_path, "--robot", "toddlerbot:bridge", "--host", hostd.address, "--yes", task=task
    )
    assert result.exit_code == 0, result.output
    (loop,) = loops
    assert (loop.cfg.detector.fov_deg, loop.cfg.detector.calibrated) == (62.2, True)
    assert _start(tmp_path)["host"]["camera_role"] == "primary"


def test_the_boards_detector_waits_for_a_camera_on_a_body_described_without_one(
    hostd: FakeHostd,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A board that can detect and has no camera, beside a body that describes none: the
    ToddlerBot's own arrangement, whose daemon owns the cameras while the board's runs with
    --camera none. The board's detector is still chosen, because the body may report a camera
    at connect, and the header says it reads nothing until then. This body reports one, so
    the detector learns its lens then, and warns once that nobody gave it."""
    caplog.set_level(logging.WARNING, logger="quackd.perception")
    hostd.without_camera()
    hostd.detect_reply = {**hostd.detect_reply, "w": 128, "h": 128, "boxes": []}
    _toddlerbot_on_its_bridge(monkeypatch, camera=True)
    loops = _loops(monkeypatch)
    # a task that needs no camera, because the description has none to judge one by; the
    # loop still shows the pilot what the camera sees, through the detector, every step
    task = _task(tmp_path, look=False)
    result = _run(
        tmp_path, "--robot", "toddlerbot:bridge", "--host", hostd.address, "--yes", task=task
    )
    assert result.exit_code == 0, result.output
    assert (
        f"detector yolo@host {hostd.address} yolov8n.pt on cuda (once the body reports a camera)"
        in _flat(result)
    )
    (loop,) = loops
    assert isinstance(loop.cfg.detector, HostDetector)
    assert (loop.cfg.detector.fov_deg, loop.cfg.detector.calibrated) == (62.0, False)
    (told,) = _lens_warnings(caplog)
    assert "the host detector at" in told
    assert _start(tmp_path)["detector"] == "yolo@host"
    assert hostd.requests_to("/detect"), "the body's frames went to the board"


def test_a_body_with_nothing_to_look_at_is_told_nothing_about_a_lens(
    hostd: FakeHostd,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The same board beside a body that reports no camera at connect either. The detector
    that was waiting reads nothing, so nobody is warned about the field of view of a camera the
    body has not got, and the record names no detector, as it does for any blind body."""
    caplog.set_level(logging.WARNING, logger="quackd.perception")
    hostd.without_camera()
    _toddlerbot_on_its_bridge(monkeypatch, camera=False)
    task = _task(tmp_path, look=False)
    result = _run(
        tmp_path, "--robot", "toddlerbot:bridge", "--host", hostd.address, "--yes", task=task
    )
    assert result.exit_code == 0, result.output
    assert "(once the body reports a camera)" in _flat(result)
    assert _lens_warnings(caplog) == []
    start = _start(tmp_path)
    assert start["detector"] is None
    assert "camera_role" not in start["host"], "the board had no camera to place"
    assert hostd.requests_to("/detect") == []


def test_the_header_names_the_colour_detector_a_body_described_blind_gets_at_connect(
    hostd: FakeHostd, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nothing is chosen before connect for such a body when the colour detector is asked for,
    when the board cannot detect, or when there is no board, and the loop builds the colour
    detector at connect. The header used to have no detector row then, beside a host row that
    says "detect", so a reader could not tell whether the board or the laptop read the frames.
    With the board's camera the body sees from the start; with none, it waits for its own."""
    _toddlerbot_on_its_bridge(monkeypatch, camera=True)
    asked = _run(
        tmp_path,
        "--robot",
        "toddlerbot:bridge",
        "--host",
        hostd.address,
        "--detector",
        "color",
        "--yes",
        task=_task(tmp_path, look=True),
    )
    assert asked.exit_code == 0, asked.output
    flat = _flat(asked)
    assert "detector color_blob on this machine" in flat, flat
    assert "(once the body reports a camera)" not in flat
    assert _start(tmp_path)["detector"] == "color_blob"
    alone = _run(
        tmp_path, "--robot", "toddlerbot:bridge", "--yes", task=_task(tmp_path, look=False)
    )
    assert alone.exit_code == 0, alone.output
    assert "detector color_blob on this machine (once the body reports a camera)" in _flat(alone)


# ── validate and list-verbs ask no board ────────────────────────────────────────────────


def test_validate_and_list_verbs_say_the_boards_camera_is_not_counted(
    hostd: FakeHostd, tmp_path: Path
) -> None:
    """A run adds the board's camera to a body described without one before it judges the
    task. These two ask no board anything, so for a robot stored with a host they refuse a
    camera task the run accepts and leave `observe` out. They still ask nothing, and now say
    so, with the doctor that does ask. A body with a camera of its own is told nothing, since
    the board's changes nothing in its vocabulary."""
    reg = tmp_path / "reg"
    Registry(reg).add_robot(RobotEntry(name="tb", spec="toddlerbot:bridge", host=hostd.address))
    Registry(reg).add_robot(RobotEntry(name="duck", spec="microduck:mock", host=hostd.address))
    look = _task(tmp_path, look=True)
    note = (
        f"does not ask the host {hostd.address} from robot tb (robots.json), so the board's "
        "camera is not counted"
    )

    def cli(*args: str) -> Any:
        return runner.invoke(app, [*args, "--registry-dir", str(reg)], env={"COLUMNS": "200"})

    checked = cli("validate", look, "--robot", "tb")
    assert checked.exit_code == 1, checked.output
    flat = _flat(checked)
    assert f"validate {note}" in flat, flat
    assert f"quackd doctor --host {hostd.address} shows whether it does" in flat
    (row,) = [
        json.loads(line)
        for line in cli("validate", look, "--robot", "tb", "--json").stdout.splitlines()
    ]
    assert row["notes"][0].startswith(f"validate {note}")
    listed = cli("list-verbs", "--robot", "tb")
    assert listed.exit_code == 0, listed.output
    assert f"list-verbs {note}" in _flat(listed)
    quiet = cli("list-verbs", "--robot", "tb", "--json")
    assert all(json.loads(line) for line in quiet.stdout.splitlines()), "stdout stays JSON"
    assert "does not ask" not in _flat(cli("list-verbs", "--robot", "duck"))
    assert hostd.requests == [], "neither command asks the board anything"


# ── serve-mcp ───────────────────────────────────────────────────────────────────────────


def test_serve_mcp_refuses_a_host_whose_daemon_does_not_answer(quick_hello: None) -> None:
    with pytest.raises(SystemExit, match=r"^--host 127\.0\.0\.1:\d+ did not answer"):
        fleet_from_flags(robot="microduck:mock", host=dead_address())


@pytest.mark.parametrize("robots", ["a=microduck:mock,b=microduck:mock", "a=microduck:mock"])
def test_serve_mcp_refuses_a_detector_for_a_fleet(hostd: FakeHostd, robots: str) -> None:
    """In `run`'s words, which count a one-member `--robots` as a fleet too."""
    with pytest.raises(
        SystemExit, match=r"^--detector is for one robot, and a fleet has several bodies"
    ):
        fleet_from_flags(robots=robots, detector="color")


def test_serve_mcp_refuses_detector_host_without_detect(hostd: FakeHostd) -> None:
    hostd.without_detector()
    with pytest.raises(SystemExit, match=r"cannot detect \(detect=false"):
        fleet_from_flags(robot="microduck:mock", host=hostd.address, detector="host")


async def test_serve_mcp_serves_the_boards_camera_and_names_its_detector(
    hostd: FakeHostd, caplog: pytest.LogCaptureFixture
) -> None:
    """The same wiring as `run`: the body wrapped with the board's camera, the detector kept
    through connect, `robot_list` naming it, and the line the server logs when it is up."""
    caplog.set_level(logging.INFO, logger="quackd.mcp")
    plan = fleet_from_flags(robot="microduck:mock", host=hostd.address, detector="host")
    assert isinstance(plan.detector, HostDetector)
    (body,) = plan.adapters.values()
    assert isinstance(body, HostCameraAdapter)
    async with connected(dict(plan.adapters), detector=plan.detector) as (client, fleet):
        (session,) = fleet.sessions.values()
        assert session.detector is plan.detector
        (row,) = _data(await client.call_tool("robot_list", {}))["robots"]
        assert row["detector"] == "yolo@host"
    up = [r.getMessage() for r in caplog.records if "MCP server up" in r.getMessage()]
    assert up and up[0].endswith("detector=yolo@host"), up


class _FakeYOLO:
    """`ultralytics.YOLO`, which is all `YoloDetector` builds. It sees nothing in any frame."""

    def __init__(self, model: str) -> None:
        self.model = model

    def predict(self, image: Any, **kw: Any) -> list[Any]:
        return []


async def test_serve_mcp_gives_yolo_the_lens_of_the_body_that_connected(
    hostd: FakeHostd, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """`--detector yolo` is built before the body connects. For a ToddlerBot on its bridge,
    which describes no camera, the board's lens was baked into it then and kept, marked
    calibrated, though the body's own camera turned out to be the primary view. Now nothing
    is claimed about the lens until connect, where the server tells the detector the lens of
    the body that connected: none given, so the guess, uncalibrated, and one warning."""
    caplog.set_level(logging.WARNING, logger="quackd.perception")
    module = ModuleType("ultralytics")
    module.YOLO = _FakeYOLO  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "ultralytics", module)
    _toddlerbot_on_its_bridge(monkeypatch, camera=True)
    plan = fleet_from_flags(robot="toddlerbot:bridge", host=hostd.address, detector="yolo")
    yolo = plan.detector
    assert isinstance(yolo, YoloDetector)
    assert (yolo.fov_deg, yolo.calibrated) == (62.0, False), "nothing known about the lens yet"
    assert _lens_warnings(caplog) == []
    (body,) = plan.adapters.values()
    assert isinstance(body, HostCameraAdapter)
    async with connected(dict(plan.adapters), detector=yolo) as (_client, fleet):
        (session,) = fleet.sessions.values()
        assert session.detector is yolo
        assert not body.host_is_primary, "the body's own camera is the primary view"
    assert (yolo.fov_deg, yolo.calibrated) == (62.0, False)
    (told,) = _lens_warnings(caplog)
    assert "for a bridge camera, so YOLO's detections use 62 degrees" in told


async def test_serve_mcp_tells_a_body_with_nothing_to_look_at_nothing_about_a_lens(
    hostd: FakeHostd, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The server's side of the blind ToddlerBot: the board's detector waits for a camera that
    never comes, and nobody is warned about the field of view of a camera the body has not
    got, before connect or after."""
    caplog.set_level(logging.WARNING, logger="quackd.perception")
    hostd.without_camera()
    _toddlerbot_on_its_bridge(monkeypatch, camera=False)
    plan = fleet_from_flags(robot="toddlerbot:bridge", host=hostd.address)
    assert isinstance(plan.detector, HostDetector)
    async with connected(dict(plan.adapters), detector=plan.detector) as (_client, fleet):
        (session,) = fleet.sessions.values()
        assert "camera" not in session.transport.manifest.sensors
    assert _lens_warnings(caplog) == []


def test_build_fleet_server_is_given_the_plans_detector(
    hostd: FakeHostd, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`serve` hands the plan's detector on, so `--detector` on the command line is not dropped
    between the flags and the server."""
    seen: dict[str, Any] = {}

    def build(*args: Any, **kw: Any) -> Any:
        seen.update(kw)
        raise SystemExit(0)

    monkeypatch.setattr("quackd.mcp_server.build_fleet_server", build)
    result = runner.invoke(
        app,
        ["serve-mcp", "--robot", "microduck:mock", "--host", hostd.address, "--detector", "host"],
    )
    assert result.exit_code == 0, result.output
    assert isinstance(seen["detector"], HostDetector)
    assert build_fleet_server is not build


# ── a detector handed in from Python ────────────────────────────────────────────────────


def _toddlerbot(backend: str) -> ToddlerBotAdapter:
    """A ToddlerBot with a camera and no field of view anybody gave: the mock as the simulator
    it is, or on its bridge, which quackd treats as the real body it stands for."""
    return ToddlerBotAdapter((_BridgeMock if backend == "bridge" else ToddlerBotMock)(camera=True))


def _yolo_70(monkeypatch: pytest.MonkeyPatch) -> YoloDetector:
    """`YoloDetector` built from Python with a lens of the caller's own, and no `--host`."""
    module = ModuleType("ultralytics")
    module.YOLO = _FakeYOLO  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "ultralytics", module)
    return YoloDetector(fov_deg=70.0)


class _OwnDetector:
    """Somebody's own detector. `Detector` asks for `name` and `detect` and nothing else, so it
    may have a `calibrate` that has nothing to do with a lens."""

    name = "own"

    def __init__(self) -> None:
        self.calls = 0

    def calibrate(self) -> None:
        self.calls += 1

    def detect(self, image: Any) -> list[Any]:
        return []


async def _run_with(detector: Any, body: Any, tmp_path: Path) -> Any:
    duck = parse_duck_text(
        "---\nduck: 1\nname: look\ndescription: d\nrequires: [observe]\n"
        "verbs:\n  allow: [observe, stop]\nsuccess: [x]\n---\n# Task\nLook.\n"
    )
    script = [
        ToolCall(name="observe", arguments={}),
        ToolCall(name="declare_success", arguments={"reason": "looked"}),
    ]
    return await run_duck(
        RunConfig(
            duck=duck,
            provider=FakeProvider(script=script),
            transport=body,
            detector=detector,
            runs_dir=tmp_path,
        )
    )


@pytest.mark.parametrize("backend", ["mock", "bridge"])
async def test_a_yolo_handed_to_a_run_from_python_keeps_the_lens_it_was_built_with(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    backend: str,
) -> None:
    """`YoloDetector(fov_deg=70.0)` in a `RunConfig` is how YOLO was reached before `--detector`
    existed, and still is from Python. The loop told every detector it was given the lens it
    found at connect, so this one's 70 degrees became the simulator's 90 on the mock and, on
    the bridge, the 62-degree guess, uncalibrated, with a warning to pass --fov-deg to somebody
    who never used the command line. Bearings and distances, which `go_to` steers by, changed
    with no `--host` anywhere. Only a detector `explicit_detector` built is told the lens."""
    caplog.set_level(logging.WARNING, logger="quackd.perception")
    yolo = _yolo_70(monkeypatch)
    result = await _run_with(yolo, _toddlerbot(backend), tmp_path)
    assert result.outcome == "success", result.reason
    assert (yolo.fov_deg, yolo.calibrated) == (70.0, True)
    assert _lens_warnings(caplog) == []


@pytest.mark.parametrize("backend", ["mock", "bridge"])
async def test_a_yolo_handed_to_the_mcp_server_keeps_the_lens_it_was_built_with(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, backend: str
) -> None:
    """The server's side, where a caller of `build_fleet_server` had no way out at all: it
    takes no field of view, so a real body without `camera_fov_deg` in its manifest always
    replaced the caller's lens with the guess."""
    caplog.set_level(logging.WARNING, logger="quackd.perception")
    yolo = _yolo_70(monkeypatch)
    async with connected({"toddler": _toddlerbot(backend)}, detector=yolo) as (_client, fleet):
        (session,) = fleet.sessions.values()
        assert session.detector is yolo
        assert "camera" in session.transport.manifest.sensors
    assert (yolo.fov_deg, yolo.calibrated) == (70.0, True)
    assert _lens_warnings(caplog) == []


async def test_a_detector_with_a_calibrate_of_its_own_is_not_called_at_connect(
    tmp_path: Path,
) -> None:
    """The call at connect was duck-typed on any `calibrate`, and passed a lens and a backend
    to it, so a detector of somebody's own whose `calibrate` takes neither stopped the run and
    the server at connect with a TypeError, where both had run it before."""
    own = _OwnDetector()
    result = await _run_with(own, _toddlerbot("mock"), tmp_path)
    assert result.outcome == "success", result.reason
    # a server whose start-up raised never answers the client's hello, so this would wait for
    # ever rather than fail
    async with asyncio.timeout(20):
        async with connected({"toddler": _toddlerbot("mock")}, detector=own) as (_client, fleet):
            (session,) = fleet.sessions.values()
            assert session.detector is own
    assert own.calls == 0


# ── a lens the body assumed, and one it reports only once it has connected ──────────────

#: The words a body gives the assumption its lens rests on, for the tests below.
ASSUMED = "a tall frame is taken for the camera module's own design"


class _ReportsItsLens(MockTransport):
    """A real body's camera that names its lens only once it has connected, as a Microduck's on
    `microduck:jsonrpc` does, and says whether it assumed that lens. It keeps the lens
    `--fov-deg` gave it, as that transport does."""

    name = "jsonrpc"
    camera_url = "http://127.0.0.1:8080/frame"
    camera_working = True

    def __init__(self, fov_deg: float, assumed: str | None) -> None:
        super().__init__()
        self.camera_fov_deg = fov_deg
        self.camera_fov_assumed = assumed
        self.given: list[float] = []

    def lens_given(self, fov_deg: float) -> None:
        self.given.append(fov_deg)


async def _run_looking(
    detector: Any, body: Any, tmp_path: Path, *, fov_deg: float | None = None
) -> Any:
    """`_run_with`, with the `--fov-deg` a run was given."""
    duck = parse_duck_text(
        "---\nduck: 1\nname: look\ndescription: d\nrequires: [observe]\n"
        "verbs:\n  allow: [observe, stop]\nsuccess: [x]\n---\n# Task\nLook.\n"
    )
    script = [
        ToolCall(name="observe", arguments={}),
        ToolCall(name="declare_success", arguments={"reason": "looked"}),
    ]
    return await run_duck(
        RunConfig(
            duck=duck,
            provider=FakeProvider(script=script),
            transport=body,
            detector=detector,
            runs_dir=tmp_path,
            fov_deg=fov_deg,
        )
    )


def test_a_lens_the_body_assumed_is_measured_through_and_called_uncalibrated(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A lens a body reports is the lens, calibrated, unless the body says it assumed it, as a
    Microduck does of the lens it takes from a tall `/frame`. Then the detections through it say
    they are uncalibrated, and the log names the assumption and `--fov-deg`, once, where the
    lens used to pass for a measured one and the advice never came."""
    caplog.set_level(logging.WARNING, logger="quackd.perception")
    assert lens(37.3, "jsonrpc", guess=90.0) == (37.3, True)
    assert lens(37.3, "jsonrpc", guess=90.0, assumed=ASSUMED) == (37.3, False)
    detector = detector_for(["camera"], None, fov_deg=37.3, backend="jsonrpc", assumed=ASSUMED)
    assert isinstance(detector, ColorBlobDetector)
    assert (detector.fov_deg, detector.calibrated) == (37.3, False)
    assert _lens_warnings(caplog) == [], "said once the body has connected, not before"
    for _ in range(2):  # as the loop and the server tell it its lens at connect
        detector.calibrate(37.3, backend="jsonrpc", assumed=ASSUMED)
    (told,) = _lens_warnings(caplog)
    assert ASSUMED in told and "--fov-deg" in told, told


def test_yolo_and_the_boards_detector_take_an_assumed_lens_as_uncalibrated(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The detectors that learn the lens at connect learn whether it was assumed too."""
    caplog.set_level(logging.WARNING, logger="quackd.perception")
    yolo = _yolo_70(monkeypatch)
    board = HostDetector(HostClient("127.0.0.1:1"), fov_deg=62.0)
    for detector in (yolo, board):
        detector.calibrate(37.3, backend="jsonrpc", assumed=ASSUMED)
        assert (detector.fov_deg, detector.calibrated) == (37.3, False)
    told = _lens_warnings(caplog)
    assert len(told) == 2 and all(ASSUMED in said for said in told), told


@pytest.mark.parametrize(
    ("assumed", "calibrated"), [(None, True), (ASSUMED, False)], ids=["reported", "assumed"]
)
async def test_a_run_measures_through_the_lens_its_body_reports_when_it_connects(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    assumed: str | None,
    calibrated: bool,
) -> None:
    """`quackd run` builds its colour detector before the body connects, from the body's own
    description, and a Microduck describes a camera with no lens, which its camera reports only
    once a frame has come. The detector kept the simulator's 90 degrees for the whole run, the
    lens the camera reported unread, while the YOLO and board detectors learned it at connect.
    The colour detector learns it then too: calibrated when the camera reported it, and
    uncalibrated, with the assumption named, when the camera assumed it. Nothing calls the lens
    a guess before connect, when the camera had not yet said it."""
    caplog.set_level(logging.WARNING, logger="quackd.perception")
    loops = _loops(monkeypatch)
    from quackd_microduck import MicroduckAdapter, describe

    built = detector_for(describe("jsonrpc").sensors, None, fov_deg=None, backend="jsonrpc")
    assert isinstance(built, ColorBlobDetector) and (built.fov_deg, built.calibrated) == (
        90.0,
        False,
    ), "what quackd run builds before the body connects"
    result = await _run_looking(built, MicroduckAdapter(_ReportsItsLens(37.3, assumed)), tmp_path)
    assert result.outcome == "success", result.reason
    (loop,) = loops
    assert loop.cfg.detector is built
    assert (built.fov_deg, built.calibrated) == (37.3, calibrated)
    told = _lens_warnings(caplog)
    if assumed is None:
        assert told == [], "a lens the camera reported is no guess, before connect or after"
    else:
        (said,) = told
        assert ASSUMED in said, said


async def test_fov_deg_reaches_the_body_so_its_state_names_the_lens_the_detector_uses(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`--fov-deg` went to the detector alone, and a Microduck went on listing the lens it had
    assumed in every state, so the pilot was told to discount bearings for a lens nobody was
    measuring through. The loop hands the flag on to the body at connect, which says so."""
    loops = _loops(monkeypatch)
    from quackd_microduck import MicroduckAdapter

    body = _ReportsItsLens(37.3, ASSUMED)
    result = await _run_looking(None, MicroduckAdapter(body), tmp_path, fov_deg=50.0)
    assert result.outcome == "success", result.reason
    (loop,) = loops
    assert (loop.cfg.detector.fov_deg, loop.cfg.detector.calibrated) == (50.0, True)
    assert body.given == [50.0]


async def test_serve_mcp_measures_through_an_assumed_lens_uncalibrated(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """`serve-mcp` has no `--fov-deg`, so a body's lens is all it has, and one the body assumed
    is measured through uncalibrated, with the advice to pass a lens in the log."""
    caplog.set_level(logging.WARNING, logger="quackd.perception")
    from quackd_microduck import MicroduckAdapter

    async with connected({"duck": MicroduckAdapter(_ReportsItsLens(37.3, ASSUMED))}) as (
        _client,
        fleet,
    ):
        (session,) = fleet.sessions.values()
        assert isinstance(session.detector, ColorBlobDetector)
        assert (session.detector.fov_deg, session.detector.calibrated) == (37.3, False)
    assert any(ASSUMED in said for said in _lens_warnings(caplog))
