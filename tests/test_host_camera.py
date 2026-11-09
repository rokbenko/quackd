"""The camera on the board `--host` names, joined to a body: `quackd.adapters.host_camera`.

The board is the fake daemon in `tests/fake_jetson_hostd.py`, served over real HTTP on
loopback; its default frame is a grey floor with an orange ball the colour detector can see.
The bodies are the mocks, one of them built without a camera, which is the case the host camera
exists for. Nothing here says anything about a Jetson, which no test here has seen.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from quackd.adapters.base import RobotAdapter
from quackd.adapters.factory import (
    adapter_names,
    describe,
    info,
    is_installed,
    make_adapter,
    parse_robot_spec,
)
from quackd.adapters.host_camera import (
    EXTRAS_KEY,
    HOST_CAMERA_NAME,
    HostCameraAdapter,
    with_host_camera,
)
from quackd.agent.loop import RunConfig, run_duck
from quackd.agent.providers.base import ToolCall
from quackd.agent.providers.fake import FakeProvider
from quackd.agent.transcript import Transcript
from quackd.cli import app
from quackd.duckfile.parser import parse_duck_text
from quackd.duckfile.validate import validate_duck
from quackd.host import STALE_AFTER_S, HostClient, HostHello
from quackd.perception import explicit_detector
from quackd.perception.host import HostDetector
from quackd.safety import Executor, allow_all
from quackd.transport.base import TransportError, camera_names_of, frames_of
from quackd.verbs.registry import registry_from_manifest
from quackd_lerobot.real import CAMERA_NAME as ARM_CAMERA
from quackd_toddlerbot import ToddlerBotAdapter
from quackd_toddlerbot.mock import ToddlerBotMock
from tests.fake_jetson_hostd import FRAME_SIZE, FakeHostd

runner = CliRunner()

MOVERS = ("go_to", "search_scan", "approach_and")
"""The core verbs a camera unlocks that move the body, beside `observe`, which does not."""


@pytest.fixture
def hostd() -> Iterator[FakeHostd]:
    with FakeHostd() as fake:
        yield fake


def _board(hostd: FakeHostd) -> tuple[HostClient, HostHello]:
    client = HostClient(hostd.address)
    return client, client.hello()


def _blind_toddlerbot(hostd: FakeHostd) -> HostCameraAdapter:
    """A ToddlerBot built without a camera, that can walk and turn its neck, with the board's
    camera added: a body whose own adapter has no picture to give at all."""
    client, hello = _board(hostd)
    return HostCameraAdapter(ToddlerBotAdapter(ToddlerBotMock(camera=False)), client, hello)


# ── the manifest ────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("blind", ["xlerobot:zmq", "alohamini:zmq"])
def test_a_blind_body_gains_what_its_own_camera_build_would_have(
    hostd: FakeHostd, blind: str
) -> None:
    """Two wheeled bodies whose real backends describe no camera, and whose mocks describe
    one. The board's camera gives the blind description the same verbs and the same gates as
    the camera build's, because both follow from `core_requirements_unmet` and from `move`."""
    _client, hello = _board(hostd)
    adapter = blind.split(":", 1)[0]
    before = describe(parse_robot_spec(blind))
    seeing = describe(parse_robot_spec(f"{adapter}:mock"))
    assert "camera" not in before.sensors and "observe" not in before.verb_names()

    after = with_host_camera(before, hello)

    assert "camera" in after.sensors
    assert set(after.verb_names()) - set(before.verb_names()) == {"observe", *MOVERS}
    assert set(after.verb_names()) == set(seeing.verb_names())
    for name in MOVERS:
        assert after.preconditions[name] == before.preconditions["move"], name
        assert after.preconditions[name] == seeing.preconditions[name], name
    assert "observe" not in after.preconditions, "looking gates nothing"
    assert after.limits["camera_fov_deg"] == 62.2, "the lens the daemon was started with"
    assert after.extras[EXTRAS_KEY] == {"primary": True, "size": list(FRAME_SIZE), "fov_deg": 62.2}
    assert with_host_camera(after, hello) is after, "once is enough"
    # and the vocabulary builds, with the adapter's own implementations behind it
    registry_from_manifest(after, make_adapter(f"{adapter}:mock"))


def test_an_arm_that_cannot_move_gains_observe_only(hostd: FakeHostd) -> None:
    """A LeRobot arm on its real backend describes no camera and no mobility: a camera lets it
    look and nothing else, because every other verb a camera unlocks drives a base it has not
    got."""
    _client, hello = _board(hostd)
    before = describe(parse_robot_spec("lerobot:real"))
    after = with_host_camera(before, hello)
    assert set(after.verb_names()) - set(before.verb_names()) == {"observe"}
    assert after.preconditions == before.preconditions


def test_every_body_quackd_describes_takes_the_boards_camera(hostd: FakeHostd) -> None:
    """The manifest is validated again with the camera in it, so a body whose own sheet did not
    survive that round trip would refuse to connect with a board beside it. Every installed
    body, on every backend it has, and each ends up able to look."""
    _client, hello = _board(hostd)
    seen = 0
    for name in adapter_names():
        if not is_installed(name):
            continue
        for backend in info(name).backends:
            before = describe(parse_robot_spec(f"{name}:{backend}"))
            after = with_host_camera(before, hello)
            assert "camera" in after.sensors and after.provides("observe"), f"{name}:{backend}"
            assert after.extras[EXTRAS_KEY]["primary"] is ("camera" not in before.sensors)
            seen += 1
    assert seen >= 7


def test_a_board_without_a_camera_changes_nothing(hostd: FakeHostd) -> None:
    hostd.without_camera()
    _client, hello = _board(hostd)
    before = describe(parse_robot_spec("xlerobot:zmq"))
    assert with_host_camera(before, hello) is before


def test_a_camera_task_on_a_blind_body_passes_validation_with_the_board(hostd: FakeHostd) -> None:
    """`run` judges a task file against the static manifest, so a camera task on a blind body
    would be refused for a camera the run will have. With the board's camera applied first it
    is not."""
    _client, hello = _board(hostd)
    duck = parse_duck_text(
        "---\nduck: 1\nname: look\ndescription: d\nrequires: [observe, go_to]\n"
        "verbs:\n  allow: [observe, go_to, stop]\nsuccess: [x]\n---\n# Task\nLook.\n"
    )
    blind = describe(parse_robot_spec("xlerobot:zmq"))
    assert validate_duck(duck, [blind]), "refused without the board"
    assert validate_duck(duck, [with_host_camera(blind, hello)]) == []


# ── the wrapper ─────────────────────────────────────────────────────────────────────────


async def test_a_blind_body_gains_a_primary_host_camera_and_the_verbs_that_need_it(
    hostd: FakeHostd,
) -> None:
    body = _blind_toddlerbot(hostd)
    manifest = await body.connect()
    assert body.manifest is manifest and body.host_is_primary
    assert {"observe", *MOVERS} <= set(manifest.verb_names())
    assert manifest.preconditions["go_to"] == manifest.preconditions["move"]

    frames = await frames_of(body)
    assert [(f.name, f.primary) for f in frames] == [(HOST_CAMERA_NAME, True)]
    assert frames[0].image.size == FRAME_SIZE
    frame = await body.get_frame()
    assert frame is not None and frame.size == FRAME_SIZE
    assert camera_names_of(body) == [HOST_CAMERA_NAME]
    assert body.camera_error is None
    await body.close()


async def test_a_described_body_gains_the_boards_camera_and_observe_and_nothing_more(
    hostd: FakeHostd,
) -> None:
    """A body quackd only describes reads none of its robot's sensors, so a board beside it is
    its one camera, as ADR-0077 allows: `observe` comes with it, and its two verbs stay its
    only others, since a picture moves nothing. Nobody had tried it, and 0.33.0 said so."""
    client, _hello = _board(hostd)
    body = make_adapter("described:unitree-b2", host=client)
    assert isinstance(body, HostCameraAdapter)
    manifest = await body.connect()
    assert body.host_is_primary
    assert set(manifest.verb_names()) == {"report_state", "stop", "observe"}
    frames = await frames_of(body)
    assert [(f.name, f.primary) for f in frames] == [(HOST_CAMERA_NAME, True)]
    assert frames[0].image.size == FRAME_SIZE
    await body.close()


async def test_lerobot_mock_keeps_its_primary_and_the_keys_end_with_host(
    hostd: FakeHostd,
) -> None:
    """A body with a camera of its own keeps it as the primary: its bearings are calibrated for
    that lens, and a board on a bench says nothing about where the arm points. The board's
    frame is an extra view, named, and last."""
    client, _hello = _board(hostd)
    body = make_adapter("lerobot:mock", host=client)
    assert isinstance(body, HostCameraAdapter)
    own = make_adapter("lerobot:mock")
    manifest = await body.connect()
    assert not body.host_is_primary
    assert manifest.verb_names() == (await own.connect()).verb_names(), "nothing unlocked"
    assert manifest.extras[EXTRAS_KEY]["primary"] is False

    # the mock's view is the real backend's camera, under the name the arm's own takes
    assert body.camera_keys == (ARM_CAMERA, HOST_CAMERA_NAME)
    frames = await frames_of(body)
    assert [(f.name, f.primary) for f in frames] == [
        (ARM_CAMERA, True),
        (HOST_CAMERA_NAME, False),
    ]
    primary = await body.get_frame()
    assert primary is not None and primary.size == frames[0].image.size
    assert frames[1].image.size == FRAME_SIZE
    await body.close()
    await own.close()


def test_no_board_camera_means_no_wrapper(hostd: FakeHostd) -> None:
    hostd.without_camera()
    client, _hello = _board(hostd)
    assert not isinstance(make_adapter("lerobot:mock", host=client), HostCameraAdapter)


async def test_a_stale_or_failing_snapshot_costs_the_picture_not_the_run(
    hostd: FakeHostd,
) -> None:
    """The daemon's own stale 503, a frame the daemon still stamps as older than the client
    accepts, and a daemon that has not captured yet: each is no picture and a reason in
    `camera_error`, and `observe` says it rather than raising."""
    body = _blind_toddlerbot(hostd)
    manifest = await body.connect()
    ex = Executor(registry_from_manifest(manifest, body), body, contract=None, confirm=allow_all)

    hostd.camera_stopped(age_s=3.2)
    assert await body.get_frames() == [] and await body.get_frame() is None
    assert body.camera_error is not None and "the camera has stopped" in body.camera_error
    looked = await ex.run_verb("observe", {})
    assert not looked.ok and "the camera gave no frame" in looked.summary
    assert "the camera has stopped" in looked.summary

    hostd.snapshot_reason = None
    hostd.frame_age_s = STALE_AFTER_S + 0.5
    assert await body.get_frames() == []
    assert body.camera_error is not None and "old (stale after" in body.camera_error

    hostd.no_frame_yet()
    assert await body.get_frame() is None
    assert body.camera_error is not None and "no frame captured yet" in body.camera_error
    await body.close()


async def test_a_failing_extra_view_leaves_the_bodys_own_camera_working(
    hostd: FakeHostd,
) -> None:
    client, _hello = _board(hostd)
    body = make_adapter("lerobot:mock", host=client)
    await body.connect()
    hostd.camera_stopped(age_s=3.2)
    frames = await frames_of(body)
    assert [f.name for f in frames] == [ARM_CAMERA], "the arm's own view survives"
    error = getattr(body, "camera_error", None)
    assert error is not None and error.startswith("host camera: ")
    await body.close()


async def test_the_wrapper_is_still_a_robot_adapter(hostd: FakeHostd) -> None:
    """A structural protocol checked at runtime, so what matters is that every member is there
    and reaches the body: the name and backend the prompt and the memory key read, and the
    arm's own attributes that no protocol lists."""
    client, _hello = _board(hostd)
    body = make_adapter("lerobot:mock", host=client)
    own = make_adapter("lerobot:mock")
    assert isinstance(body, RobotAdapter)
    assert (body.name, body.backend) == (own.name, own.backend) == ("lerobot", "mock")
    assert body.manifest is None, "until it connects"
    assert getattr(body, "supports_hand_off", False) is True, "delegated, never copied"
    assert body.implementations().keys() == own.implementations().keys()
    await body.connect()
    state = await body.get_state()
    assert state.posture is not None
    assert (await body.go_to_rest()).how == (await own.go_to_rest()).how
    await body.close()


# ── a whole run on a blind body ─────────────────────────────────────────────────────────


async def test_the_agent_loop_runs_a_camera_task_on_a_blind_body_through_the_host(
    hostd: FakeHostd, tmp_path: Path
) -> None:
    """A ToddlerBot with no camera, a task that requires one, and the scripted pilot. The loop
    connects, finds `observe` in the vocabulary the board's camera unlocked, builds the colour
    detector at the lens the daemon reported, and sees the fake frame's ball."""
    body = _blind_toddlerbot(hostd)
    duck = parse_duck_text(
        "---\nduck: 1\nname: look-for-the-ball\ndescription: d\nrequires: [observe]\n"
        "verbs:\n  allow: [observe, report_state, stop]\nsuccess: [x]\n---\n# Task\nLook.\n"
    )
    result = await run_duck(
        RunConfig(
            duck=duck,
            provider=FakeProvider(
                script=[
                    ToolCall(name="observe", arguments={}),
                    ToolCall(name="declare_success", arguments={"reason": "saw it"}),
                ]
            ),
            transport=body,
            runs_dir=tmp_path,
        )
    )
    assert result.outcome == "success", result.reason
    events = Transcript.read(result.run_dir / "transcript.jsonl")
    start = next(e for e in events if e["kind"] == "run_start")
    assert "observe" in start["tools"]
    assert "camera" in start["robot"]["sensors"]
    assert start["detector"] == "color_blob"
    (looked,) = [e for e in events if e["kind"] == "verb_end" and e["name"] == "observe"]
    assert looked["ok"] and "ball" in looked["summary"], looked
    assert hostd.requests_to("/snapshot.jpg"), "the frames came from the board"
    assert list(result.run_dir.rglob("*.png")), "and were written into the run"


async def test_the_agent_loop_calibrates_the_boards_detector_at_connect(
    hostd: FakeHostd, tmp_path: Path
) -> None:
    """The board's detector is built before the body connects, when nobody knows yet which
    camera the primary view comes through. The body's live manifest carries the board camera's
    field of view once the board's camera is its only one, and the loop hands that to the
    detector `explicit_detector` built, which it keeps."""
    body = _blind_toddlerbot(hostd)
    detector = explicit_detector(
        "host",
        client=body.host,
        hello=body.host.hello(),
        fov_deg=None,
        backend=body.backend,
        has_camera=False,
    )
    assert isinstance(detector, HostDetector)
    assert (detector.fov_deg, detector.calibrated) == (90.0, True), "the simulator's, for now"
    duck = parse_duck_text(
        "---\nduck: 1\nname: look-for-the-ball\ndescription: d\nrequires: [observe]\n"
        "verbs:\n  allow: [observe, stop]\nsuccess: [x]\n---\n# Task\nLook.\n"
    )
    result = await run_duck(
        RunConfig(
            duck=duck,
            provider=FakeProvider(
                script=[
                    ToolCall(name="observe", arguments={}),
                    ToolCall(name="declare_success", arguments={"reason": "saw it"}),
                ]
            ),
            transport=body,
            detector=detector,
            runs_dir=tmp_path,
        )
    )
    assert result.outcome == "success", result.reason
    assert (detector.fov_deg, detector.calibrated) == (62.2, True)
    events = Transcript.read(result.run_dir / "transcript.jsonl")
    assert next(e for e in events if e["kind"] == "run_start")["detector"] == "yolo@host"
    (looked,) = [e for e in events if e["kind"] == "verb_end" and e["name"] == "observe"]
    assert "ball" in looked["summary"] and "uncalibrated" not in looked["summary"], looked
    assert hostd.requests_to("/detect"), "the board's model saw the board's frame"


# ── doctor ──────────────────────────────────────────────────────────────────────────────


def _probe_with_host(spec: str, hostd: FakeHostd, **kw: Any) -> Any:
    from quackd import doctor

    return doctor.probe(
        spec,
        describe(parse_robot_spec(spec)),
        "COM9",
        None,
        None,
        host=HostClient(hostd.address),
        **kw,
    )


def _rows(report: Any) -> dict[str, tuple[str, str]]:
    return {row.what: (row.value, row.state) for row in report.rows}


def test_doctor_lists_the_host_camera_as_an_extra_view_beside_a_bodys_own(
    hostd: FakeHostd,
) -> None:
    report = _probe_with_host("lerobot:mock", hostd)
    rows = _rows(report)
    assert rows[f"camera {HOST_CAMERA_NAME}"] == (
        f"{FRAME_SIZE[0]}x{FRAME_SIZE[1]} (extra view)",
        "ok",
    )
    assert "camera" not in rows, "the arm's own camera is read only with --camera-url"
    assert report.ok, report.rows


def test_doctor_lists_the_host_camera_as_the_primary_of_a_blind_body(
    hostd: FakeHostd, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No blind body connects offline, so the factory is handed one: a ToddlerBot built
    without a camera, wrapped the way `make_adapter` wraps any body when the board has one."""

    def blind(spec: Any, **kw: Any) -> Any:
        host = kw.get("host")
        inner = ToddlerBotAdapter(ToddlerBotMock(camera=False))
        return HostCameraAdapter(inner, host, host.hello()) if host is not None else inner

    monkeypatch.setattr("quackd.adapters.factory.make_adapter", blind)
    report = _probe_with_host("toddlerbot:mock", hostd)
    rows = _rows(report)
    assert rows[f"camera {HOST_CAMERA_NAME}"][0].endswith("(primary)")
    hostd.camera_stopped(age_s=3.2)
    stopped = _probe_with_host("toddlerbot:mock", hostd)
    assert _rows(stopped)[f"camera {HOST_CAMERA_NAME}"] == ("no frame (primary)", "fail")
    assert not stopped.ok
    assert any("the camera on the --host board gave no frame" in a for a in stopped.advisories)


def test_doctor_hands_the_board_it_asked_to_the_probe(
    hostd: FakeHostd, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`doctor --robot X --address Y --host Z` end to end through `collect`: the board answers
    its hello, and the probe of the body lists its camera. A board that does not answer is not
    handed on, because its own row has already failed the report."""
    from quackd import doctor

    monkeypatch.setattr(doctor, "_probe_llms", lambda url, timeout_s=1.5: ("down", "not running"))
    monkeypatch.setattr(doctor, "_probe_placement", lambda root_url, timeout_s=1.5: ([], "none"))
    report = doctor.collect("lerobot:mock", address="COM9", host=hostd.address)
    assert report.host is not None and report.host.ok
    assert report.robot is not None and report.robot.probe is not None
    rows = _rows(report.robot.probe)
    assert rows[f"camera {HOST_CAMERA_NAME}"] == (
        f"{FRAME_SIZE[0]}x{FRAME_SIZE[1]} (extra view)",
        "ok",
    )


# ── run: the task file is judged with the board's camera ────────────────────────────────


def test_run_judges_a_camera_task_on_a_blind_body_with_the_boards_camera(
    hostd: FakeHostd, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ToddlerBot's bridge backend describes no camera. Without the board the task is
    refused in the validator's words; with it the run gets past validation to building the
    body, which is stopped there because no ToddlerBot is on this machine to connect to."""

    def stop_here(spec: Any, **kw: Any) -> Any:
        raise TransportError("stopped where the body would have been built")

    monkeypatch.setattr("quackd.adapters.factory.make_adapter", stop_here)
    duck = tmp_path / "look.duck"
    duck.write_text(
        "---\nduck: 1\nname: look\ndescription: d\nrequires: [observe]\n"
        "verbs:\n  allow: [observe, stop]\nsuccess: [x]\n---\n# Task\nLook.\n",
        encoding="utf-8",
    )
    args = ["run", str(duck), "--robot", "toddlerbot:bridge", "--llm", "fake", "--no-log"]
    args += ["--runs-dir", str(tmp_path / "runs"), "--memory-dir", str(tmp_path / "mem")]
    without = runner.invoke(app, args)
    assert without.exit_code == 1 and "cannot run on" in " ".join(without.output.split())
    hosted = runner.invoke(app, [*args, "--host", hostd.address])
    flat = " ".join(hosted.output.split())
    assert hosted.exit_code == 1 and "cannot run on" not in flat, flat
    assert "stopped where the body would have been built" in flat
