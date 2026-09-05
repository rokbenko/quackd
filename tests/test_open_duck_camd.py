"""The camera server that runs on the duck's Pi, exercised with no Pi and no camera.

The last test is the one that matters: it stands up the real camera server, tells the real
bridge daemon where it is, and has the real quackd client fetch a frame through the whole
chain. That is what turns `observe` from a promise into a picture.
"""

from __future__ import annotations

import importlib.util
import io
import json
import logging
import sys
import threading
import time
import urllib.request
from pathlib import Path
from types import ModuleType

import pytest
from PIL import Image

from quackd.adapters.open_duck import OpenDuckAdapter
from quackd.adapters.open_duck.bridge import OpenDuckBridge

REPO = Path(__file__).resolve().parents[1]
CAMD = REPO / "bridge" / "open_duck" / "quackd_duck_camd.py"
DAEMON = REPO / "bridge" / "open_duck" / "quackd_duck_bridge.py"


def _load(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def camd() -> ModuleType:
    return _load(CAMD, "quackd_duck_camd")


@pytest.fixture(scope="module")
def daemon() -> ModuleType:
    return _load(DAEMON, "quackd_duck_bridge")


def get(url: str, timeout: float = 3.0) -> tuple[int, bytes, str]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status, r.read(), r.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        return e.code, e.read(), e.headers.get("Content-Type", "")


def wait_for_frame(store, timeout: float = 5.0) -> None:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if store.get()[0] is not None:
            return
        time.sleep(0.02)
    raise AssertionError("no frame was captured in time")


# ── the store ───────────────────────────────────────────────────────────────────────────


def test_the_store_starts_empty_and_reports_it(camd: ModuleType) -> None:
    store = camd.FrameStore()
    assert store.get() == (None, 0.0)
    health = store.health(now=10.0)
    assert health["ok"] is False and health["age_s"] is None and health["frames"] == 0


def test_the_store_keeps_only_the_newest_frame(camd: ModuleType) -> None:
    store = camd.FrameStore()
    store.put(b"old", (8, 8), now=100.0)
    store.put(b"new", (8, 8), now=101.0)
    jpeg, at = store.get()
    assert jpeg == b"new" and at == 101.0
    assert store.health(now=101.5)["age_s"] == 0.5
    assert store.health(now=101.5)["frames"] == 2


def test_a_capture_failure_is_recorded_and_does_not_lose_the_last_frame(camd: ModuleType) -> None:
    store = camd.FrameStore()
    store.put(b"good", (8, 8), now=1.0)
    store.fail("the camera unplugged itself")
    assert store.get()[0] == b"good", "a hiccup must not blank the feed"
    health = store.health(now=2.0)
    assert health["errors"] == 1 and "unplugged" in health["last_error"]


# ── the http surface ────────────────────────────────────────────────────────────────────


def test_a_snapshot_before_the_first_capture_is_a_clean_503(camd: ModuleType) -> None:
    store = camd.FrameStore()
    server = camd.serve(store, "127.0.0.1", 0)
    try:
        port = server.server_address[1]
        code, body, ctype = get(f"http://127.0.0.1:{port}/snapshot.jpg")
        assert code == 503 and "json" in ctype
        assert json.loads(body)["reason"] == "no frame captured yet"
    finally:
        server.shutdown()


def test_it_serves_a_jpeg_and_a_health_page(camd: ModuleType) -> None:
    store = camd.FrameStore()
    stop = threading.Event()
    threading.Thread(
        target=camd.capture_loop, args=(store, camd.FakeCamera(64), 20.0, stop), daemon=True
    ).start()
    server = camd.serve(store, "127.0.0.1", 0)
    try:
        wait_for_frame(store)
        port = server.server_address[1]
        code, body, ctype = get(f"http://127.0.0.1:{port}/snapshot.jpg")
        assert code == 200 and ctype == "image/jpeg"
        assert body[:2] == b"\xff\xd8", "a real JPEG, not a description of one"

        code, body, _ = get(f"http://127.0.0.1:{port}/healthz")
        health = json.loads(body)
        assert code == 200 and health["ok"] is True
        assert health["frames"] >= 1 and health["bytes"] > 0 and health["age_s"] < 5

        assert get(f"http://127.0.0.1:{port}/anything-else")[0] == 404
    finally:
        stop.set()
        server.shutdown()


def test_nothing_in_this_server_can_move_the_robot(camd: ModuleType) -> None:
    """It reads a camera and answers GET. There is no control path to get wrong."""
    code = "\n".join(
        line for line in CAMD.read_text(encoding="utf-8").splitlines() if not line.startswith("#")
    )
    for forbidden in ("do_POST", "do_PUT", "do_DELETE", "mini_bdx_runtime", "HWI(", "set_position"):
        assert forbidden not in code, f"camd should not contain {forbidden!r}"
    # and the only request verb it answers at all
    assert code.count("def do_") == 1 and "def do_GET" in code


# ── two processes cannot own one camera ─────────────────────────────────────────────────


def test_the_camera_flag_warns_rather_than_refusing(camd: ModuleType, tmp_path, caplog) -> None:
    """It used to `return 2` on `expression_features.camera`, to avoid fighting the robot's
    own runtime for the device. Reading upstream at the pin on 2026-09-05 settled it: the
    walk loop the bridge runs references no camera at all, so the process quackd starts
    cannot be the other owner. Refusing was avoiding a collision that could not happen — and
    it was the flag the docs tell owners to leave true if they want the runtime's own eyes.

    It still warns, because another upstream script could open the device, and it still fails
    when there is genuinely no camera. What it must not do is fail *because of the flag*."""
    config = tmp_path / "duck_config.json"
    config.write_text('{"expression_features": {"camera": true}}')
    assert camd.runtime_owns_the_camera(str(config)) is True

    with caplog.at_level(logging.WARNING, logger="quackd-duck-camd"):
        code = camd.main(["--duck-config", str(config), "--seconds", "0.1"])
    warned = " ".join(r.message for r in caplog.records)
    assert "walk loop opens the camera" in warned or "no camera" in warned.lower()
    if code == 2:
        # this machine has no picamzero, so it fails on the camera itself; the point is that
        # the reason is the hardware and not the configuration flag
        assert "no camera" in warned.lower(), warned

    config.write_text('{"expression_features": {"camera": false}}')
    assert camd.runtime_owns_the_camera(str(config)) is False
    assert camd.runtime_owns_the_camera(str(tmp_path / "absent.json")) is False


def test_fake_mode_runs_even_where_the_runtime_owns_the_camera(camd: ModuleType, tmp_path) -> None:
    config = tmp_path / "duck_config.json"
    config.write_text('{"expression_features": {"camera": true}}')
    assert (
        camd.main(["--duck-config", str(config), "--fake", "--port", "0", "--seconds", "0.2"]) == 0
    )


# ── the whole chain ─────────────────────────────────────────────────────────────────────


async def test_quackd_sees_through_the_camera_server(camd: ModuleType, daemon: ModuleType) -> None:
    """camd serves a frame, the bridge advertises where, and quackd fetches a real image.

    Without this the duck can walk and chirp but not see, and `observe`, `go_to`,
    `search_scan` and `approach_and` do not exist for it at all."""
    store = camd.FrameStore()
    stop = threading.Event()
    threading.Thread(
        target=camd.capture_loop, args=(store, camd.FakeCamera(96), 20.0, stop), daemon=True
    ).start()
    cam_server = camd.serve(store, "127.0.0.1", 0)
    cam_port = cam_server.server_address[1]
    wait_for_frame(store)

    core = daemon.BridgeCore(
        capabilities={"camera": True, "speaker": True, "antennas": False, "microphone": False},
        camera_url=f"http://127.0.0.1:{cam_port}/snapshot.jpg",
    )
    bridge_server = daemon.Server(core, "127.0.0.1", 0)
    bridge_server.start()
    try:
        adapter = OpenDuckAdapter(OpenDuckBridge(f"tcp://127.0.0.1:{bridge_server.port}"))
        manifest = await adapter.connect()
        # the camera is what brings these four verbs into existence
        assert {"observe", "go_to", "search_scan", "approach_and"} <= set(manifest.verb_names())
        frame = await adapter.get_frame()
        assert frame is not None and frame.size == (96, 96)
        assert frame.mode == "RGB"
        await adapter.disconnect()
    finally:
        stop.set()
        cam_server.shutdown()
        bridge_server.stop()
        bridge_server.join(timeout=2)


async def test_a_duck_with_no_camera_server_simply_has_no_camera_verbs(daemon: ModuleType) -> None:
    core = daemon.BridgeCore(capabilities={"camera": False, "speaker": True})
    server = daemon.Server(core, "127.0.0.1", 0)
    server.start()
    try:
        adapter = OpenDuckAdapter(OpenDuckBridge(f"tcp://127.0.0.1:{server.port}"))
        manifest = await adapter.connect()
        assert not {"observe", "go_to", "search_scan", "approach_and"} & set(manifest.verb_names())
        assert await adapter.get_frame() is None
        await adapter.disconnect()
    finally:
        server.stop()
        server.join(timeout=2)


async def test_a_narrowed_robot_refuses_the_run_instead_of_crashing(
    daemon: ModuleType, tmp_path
) -> None:
    """`validate` checks the STATIC manifest, which describes a fully built duck. A duck
    that reports no camera at connect has a narrower vocabulary, and the agent loop used to
    reach tool_schemas and raise a bare VerbNotFound with the robot already connected."""
    from quackd.agent.loop import AgentLoop, RunConfig
    from quackd.duckfile.parser import load_duck
    from quackd.transport.base import TransportError

    core = daemon.BridgeCore(capabilities={"camera": False, "speaker": True})
    server = daemon.Server(core, "127.0.0.1", 0)
    server.start()
    try:
        adapter = OpenDuckAdapter(OpenDuckBridge(f"tcp://127.0.0.1:{server.port}"))
        loop = AgentLoop(
            RunConfig(
                duck=load_duck("open-duck-scout"),  # needs search_scan, which needs a camera
                provider=None,  # never reached: we refuse before the first turn
                transport=adapter,
                runs_dir=tmp_path,
            )
        )
        with pytest.raises(TransportError) as caught:
            await loop.run()
        message = str(caught.value)
        # observe and go_to are what the task *requires* and a camera is what provides them
        assert "requires observe, go_to" in message and "does not provide" in message
        assert "narrower than its description" in message
        await adapter.disconnect()
    finally:
        server.stop()
        server.join(timeout=2)


async def test_a_verb_a_task_merely_allows_is_dropped_not_fatal(
    daemon: ModuleType, tmp_path
) -> None:
    """A v1 task may allow more than it needs. `open-duck-scout` allows gaze, but head
    control is off by default, and a duck with no head should still do the task."""
    from quackd.agent.loop import AgentLoop, RunConfig
    from quackd.agent.providers.fake import FakeProvider
    from quackd.duckfile.parser import load_duck

    core = daemon.BridgeCore(
        capabilities={"camera": True, "speaker": True, "antennas": False, "microphone": False},
        camera_url=None,
    )
    core.capabilities["camera"] = True  # a camera, but deliberately no head and no antennas
    server = daemon.Server(core, "127.0.0.1", 0)
    server.start()
    controller = daemon.NetworkController(core, 20)
    # A stand-in for upstream's loop, so the bridge reports a healthy control rate.
    #
    # The rate is a fixture, so state it rather than trying to achieve it. A `time.sleep`
    # loop in a Python thread is not a real-time loop: sleeping 0.02 to imitate the robot's
    # 50 Hz measured 25 Hz on a loaded macOS runner, the client refused a loop under
    # MIN_LOOP_HZ (35), and the test failed for a reason that says nothing about the duck.
    # Pinning it each tick is stable at any load, because the daemon's own EWMA moves at
    # most 10% toward the instantaneous rate, so a health poll can never read below
    # 0.9 * 49.8 however starved the machine is. Everything else here stays real: the
    # snapshot, the sequence numbers and the deadman all run on the wall clock.
    core.loop_hz = 49.8  # before the thread starts, so the first health poll is never 0.0
    ticking = threading.Event()

    def tick() -> None:
        while not ticking.is_set():
            controller.get_last_command()
            core.loop_hz = 49.8
            time.sleep(0.005)

    threading.Thread(target=tick, daemon=True).start()
    lines: list[str] = []
    try:
        adapter = OpenDuckAdapter(OpenDuckBridge(f"tcp://127.0.0.1:{server.port}"))
        loop = AgentLoop(
            RunConfig(
                duck=load_duck("open-duck-scout"),
                provider=FakeProvider.for_duck("open-duck-scout"),
                transport=adapter,
                runs_dir=tmp_path,
                log=lines.append,
            )
        )
        result = await loop.run()
        assert result.outcome in ("success", "failure", "budget"), result.reason
        assert any("does not have gaze" in line for line in lines), lines
    finally:
        ticking.set()
        server.stop()
        server.join(timeout=2)


# ── the camera is not on the network unless you ask ─────────────────────────────────────


def test_the_camera_binds_loopback_by_default(camd: ModuleType) -> None:
    """It serves a live view of wherever the robot is, with no authentication, so the
    default must not be the LAN. The shipped systemd unit has to agree."""
    assert camd.parser().parse_args([]).bind == "127.0.0.1"
    unit = (REPO / "bridge" / "open_duck" / "quackd-duck-camd.service").read_text(encoding="utf-8")
    assert "--bind 127.0.0.1" in unit and "--bind 0.0.0.0" not in unit


# ── the transforms --fake never runs, and the geometry nobody could set ─────────────────


def test_the_chain_test_now_actually_looks_at_the_frame(camd: ModuleType) -> None:
    """The one end-to-end camera test asserted a size and a mode and never constructed a
    detector, so `--fake` would have passed unchanged with the colours inverted, the
    rotation wrong and the field of view wrong — while checklist step 4 told the operator
    "everything except the robot itself works"."""
    from quackd.perception.color_blob import ColorBlobDetector

    jpeg, size = camd.FakeCamera(96).jpeg()
    frame = Image.open(io.BytesIO(jpeg)).convert("RGB")
    assert size == (96, 96)
    balls = [d for d in ColorBlobDetector().detect(frame) if d.label == "ball"]
    assert balls, "the synthetic scene has to be one quackd's own detector can see"
    assert balls[0].est_distance_m is not None


def test_the_colour_order_the_real_camera_path_applies(camd: ModuleType) -> None:
    """`--no-swap-rb` is offered in the README as the fix for frames that "come out wrong".
    It is not: the default is correct by a double negative (picamzero hands back RGB-ordered
    data, cvtColor reverses it to the BGR cv2.imencode expects), and setting the flag inverts
    the image — which lands an orange ball at H≈103, inside the *person* hue range. None of
    cv2.resize, cvtColor, rotate or imencode run under --fake, so nothing caught it."""
    import numpy as np

    from quackd.perception.color_blob import ColorBlobDetector

    # a bright orange disc on a pale floor, in the RGB order picamzero returns
    frame = np.full((240, 320, 3), (236, 229, 212), dtype=np.uint8)
    cv2 = pytest.importorskip("cv2")
    cv2.circle(frame, (160, 150), 40, (255, 140, 0), -1)

    class Stub(camd.PiCamera):
        def __init__(self, swap_rb: bool) -> None:  # no picamzero, no camera
            self.camera = None
            self.size = 128
            self.rotate = 0
            self.swap_rb = swap_rb

        def capture_array(self):  # type: ignore[no-untyped-def]
            return frame

    def label_for(swap_rb: bool) -> str | None:
        stub = Stub(swap_rb)
        stub.camera = type("C", (), {"capture_array": lambda _self: frame})()
        jpeg, _ = stub.jpeg()
        seen = ColorBlobDetector().detect(Image.open(io.BytesIO(jpeg)).convert("RGB"))
        return seen[0].label if seen else None

    assert label_for(True) == "ball", "the shipped default has to see an orange ball"
    assert label_for(False) != "ball", "and flipping it is not a remedy, it is a relabelling"


def test_a_camera_that_stops_capturing_stops_being_served(camd: ModuleType) -> None:
    """After one successful capture the only 503 was unreachable, so a ribbon working loose
    on a walking duck left camd answering 200 with the same JPEG forever. `go_to` then
    visually servos on a photograph, and `lost > 30` cannot save it because the frozen frame
    still contains the ball."""
    store = camd.FrameStore()
    store.put(camd._TINY_JPEG, (2, 2), now=time.monotonic())
    server = camd.serve(store, "127.0.0.1", 0, 5.0)
    port = server.server_address[1]
    try:
        status, body, kind = get(f"http://127.0.0.1:{port}/snapshot.jpg")
        assert status == 200 and kind == "image/jpeg"

        # the capture loop dies; nothing replaces the frame
        store._at = time.monotonic() - 30.0
        status, body, kind = get(f"http://127.0.0.1:{port}/snapshot.jpg")
        assert status == 503, "a frame that old is not a picture of now"
        assert b"stale" in body or b"stopped" in body
    finally:
        server.shutdown()


def test_a_served_frame_says_how_old_it_is(camd: ModuleType) -> None:
    store = camd.FrameStore()
    store.put(camd._TINY_JPEG, (2, 2), now=time.monotonic())
    server = camd.serve(store, "127.0.0.1", 0, 5.0)
    port = server.server_address[1]
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/snapshot.jpg", timeout=3) as r:
            assert r.headers.get("X-Frame-Age") is not None
            assert float(r.headers["X-Frame-Age"]) < 1.0
    finally:
        server.shutdown()


async def test_quackd_refuses_a_frozen_frame_and_says_why(camd: ModuleType) -> None:
    """The client half. camd expires the frame; the bridge client must not then treat the
    503 as a hard error that ends the session — `jsonrpc`'s own docstring records that one
    dropped HTTP response did exactly that once — nor walk on regardless."""
    from quackd.adapters.open_duck.bridge import OpenDuckBridge

    store = camd.FrameStore()
    store.put(camd.FakeCamera(96).jpeg()[0], (96, 96), now=time.monotonic())
    server = camd.serve(store, "127.0.0.1", 0, 5.0)
    port = server.server_address[1]
    try:
        t = OpenDuckBridge(camera_url=f"http://127.0.0.1:{port}/snapshot.jpg")
        assert await t.get_frame() is not None
        assert t.camera_health()["error"] is None
        assert t.camera_health()["frames"] == 1

        store._at = time.monotonic() - 30.0
        assert await t.get_frame() is None, "a photograph is not something to steer on"
        assert "stale" in (t.camera_health()["error"] or "") or "old" in (
            t.camera_health()["error"] or ""
        )
    finally:
        server.shutdown()


async def test_doctor_can_finally_probe_this_robots_camera(camd: ModuleType) -> None:
    """`doctor` gates its frame probe on a `camera_health` method that only the Microduck
    transport had, so `--camera-url` was accepted here, never checked, and a typo passed the
    checklist's go/no-go step to fail at the first observe with the duck on the floor."""
    from quackd.adapters.open_duck.bridge import OpenDuckBridge

    assert callable(getattr(OpenDuckBridge(), "camera_health", None))
    unreachable = OpenDuckBridge(camera_url="http://127.0.0.1:1/snapshot.jpg")
    assert await unreachable.get_frame() is None
    assert unreachable.camera_health()["error"], "an unreachable camera has to be visible"
