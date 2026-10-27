"""The page itself, driven headlessly through Streamlit's AppTest: its screens, its token, and a
goal, a chat and a Stop run through it end to end (`quackd.webui.app`).

Skipped where Streamlit is not installed, which is every job of CI's test matrix: the CI job `ui`
installs the extra and runs this file."""

from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("streamlit")

from streamlit.testing.v1 import AppTest

from quackd.webui import jobs, launch, state, widgets
from tests.test_front_run import _gated_duck

APP = str(Path(__file__).resolve().parents[1] / "quackd" / "webui" / "app.py")
SCREENS = ("setup", "goal", "chat", "history", "robots", "memory", "policies")


@pytest.fixture(autouse=True)
def page(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A page of its own for every test: its working folder, its setup and its job table."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(state, "_SETUP", None)
    monkeypatch.setattr(jobs, "_TABLE", None)
    monkeypatch.setattr(widgets, "_RENDERED", {})
    monkeypatch.setattr(
        launch,
        "CONFIG",
        launch.Config(
            folder=tmp_path,
            bind="127.0.0.1",
            port=8501,
            token=None,
            urls=("http://127.0.0.1:8501",),
        ),
    )
    yield tmp_path
    if jobs._TABLE is not None:
        for job in jobs._TABLE.running():
            job.shutdown(stop_grace_s=10, end_grace_s=10)


def run_screen(name: str) -> None:
    import importlib

    from quackd.webui import widgets

    widgets.style()
    widgets.sync()
    importlib.import_module(f"quackd.webui.screens.{name}").screen()
    widgets.floating_stop()


def _screen(name: str) -> AppTest:
    at = AppTest.from_function(run_screen, args=(name,), default_timeout=120)
    at.run()
    assert not at.exception, [str(e.value) for e in at.exception]
    return at


def _until(at: AppTest, done: Any, within_s: float = 90) -> None:
    """Draw the screen again until `done()`: what a fragment polling every half second does."""
    deadline = time.monotonic() + within_s
    while not done():
        assert time.monotonic() < deadline, "it never came"
        time.sleep(0.3)
        at.run()
        assert not at.exception, [str(e.value) for e in at.exception]


@pytest.mark.parametrize("name", SCREENS)
def test_every_screen_draws_without_an_error(name: str) -> None:
    _screen(name)


def test_the_page_asks_for_its_token_and_takes_it_from_the_address(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        launch,
        "CONFIG",
        launch.Config(folder=tmp_path, bind="0.0.0.0", port=8501, token="s3cret", urls=()),
    )
    at = AppTest.from_file(APP, default_timeout=120)
    at.run()
    assert [e.value for e in at.error] and "needs its token" in at.error[0].value
    assert not at.button and not at.selectbox, "nothing but the refusal is drawn"
    at = AppTest.from_file(APP, default_timeout=120)
    at.query_params["token"] = "wrong"
    at.run()
    assert "needs its token" in at.error[0].value
    at = AppTest.from_file(APP, default_timeout=120)
    at.query_params["token"] = "s3cret"
    at.run()
    assert not at.error and [t.value for t in at.title] == ["Setup"]
    at.query_params.clear()  # what a page switch does to the address
    at.run()
    assert at.query_params["token"] == "s3cret", "put back, for a refresh or a lapsed session"
    at = AppTest.from_file(APP, default_timeout=120)
    at.query_params["token"] = "s3crët"
    at.run()
    assert not at.exception, "a token that is not ASCII is refused, not raised"
    assert "needs its token" in at.error[0].value


def test_the_sidebar_gives_a_phone_the_address_with_its_token_and_says_how_on_loopback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    at = AppTest.from_file(APP, default_timeout=120)
    at.run()
    (shown,) = [e for e in at.sidebar.expander if e.label == "Open on your phone"]
    assert "start it with quackd ui --bind 0.0.0.0" in " ".join(c.value for c in shown.caption)
    address = "http://192.168.1.23:8501/?token=s3cret"
    monkeypatch.setattr(
        launch,
        "CONFIG",
        launch.Config(folder=tmp_path, bind="0.0.0.0", port=8501, token="s3cret", urls=(address,)),
    )
    at = AppTest.from_file(APP, default_timeout=120)
    at.query_params["token"] = "s3cret"
    at.run()
    (shown,) = [e for e in at.sidebar.expander if e.label == "Open on your phone"]
    assert [c.value for c in shown.code] == [address]


def test_the_qr_code_a_phone_scans_reads_back_as_the_address() -> None:
    import cv2
    import numpy as np

    address = "http://192.168.1.23:8501/?token=s3cret"
    picture = np.array(launch.qr_image(address).convert("L"))
    read, _points, _code = cv2.QRCodeDetector().detectAndDecode(picture)
    assert read == address


def test_an_ended_run_shows_the_last_frame_it_saved_and_its_folder_under_the_page(
    page: Path,
) -> None:
    """The stream keeps its pace by dropping frames, and a fast run's last ones go: once the
    run has ended, the picture is the last one it saved."""
    frames = page / "runs" / "r1" / "frames"
    frames.mkdir(parents=True)
    for name in ("0000.png", "0003.png", "0001.png"):
        (frames / name).write_bytes(b"")
    view = {"end": {"outcome": "success"}, "run_dir": "runs/r1"}
    assert widgets.run_folder(view) == page / "runs" / "r1"
    assert widgets.last_saved_frame(page / "runs" / "r1") == frames / "0003.png"
    assert widgets.last_saved_frame(page / "nothing") is None


def test_a_paid_endpoint_starts_only_once_its_box_is_ticked_and_then_with_yes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The box is the page's question, so the command it sends answers its own with --yes:
    with no terminal, `policy endpoint up` asks nothing and refuses without it."""
    sent: list[list[str]] = []
    started: list[tuple[str, list[str]]] = []

    def run_command(argv: list[str], **_kw: Any) -> Any:
        sent.append(list(argv))
        return SimpleNamespace(returncode=0, stdout="", stderr="")  # no endpoint recorded

    def start(_table: Any, kind: str, args: list[str], **_kw: Any) -> Any:
        started.append((kind, list(args)))
        raise RuntimeError("not started here")

    monkeypatch.setattr(jobs, "run_command", run_command)
    monkeypatch.setattr(jobs.Jobs, "start", start)
    at = _screen("policies")
    next(t for t in at.text_input if t.label == "Name").input("arm-act").run()
    next(t for t in at.text_input if t.label == "Policy, REPO@REVISION").input("o/act@abc").run()

    def endpoint_start() -> Any:  # the local server's form has a Start it of its own
        return next(b for b in at.button if b.label == "Start it" and not b.form_id)

    assert endpoint_start().disabled, "not until the box is ticked"
    at.checkbox[0].check().run()
    endpoint_start().click().run()
    assert not [argv for argv in sent if argv[:3] == ["policy", "endpoint", "up"]]
    (kind, up), *_ = [(k, a) for k, a in started if a[:3] == ["policy", "endpoint", "up"]]
    assert kind == "command", "a job, watched from the screen, which never waits on it"
    assert up[3:] == [
        "arm-act",
        "--policy",
        "o/act@abc",
        "--instance",
        "nvidia-l4",
        "--region",
        "us-east-1",
        "--yes",
    ]


def test_setup_says_before_a_check_that_connecting_the_arm_drops_its_torque() -> None:
    """The check is `quackd robot list --probe` for one robot, and the arm's connect takes torque
    off every motor for a moment: an arm holding itself up can drop."""
    for name, value in (("_robot_from", "adapter"), ("_adapter", "lerobot"), ("_backend", "real")):
        state.setup().set(name, value)
    at = _screen("setup")
    warned = [w.value for w in at.warning if "torque off every motor" in w.value]
    assert warned, [w.value for w in at.warning]
    assert any(b.label == "Check the connection" for b in at.button)


def test_the_same_as_line_names_only_what_was_chosen_and_a_vla_only_when_chosen() -> None:
    at = _screen("setup")
    lines = [c.value for c in at.code if c.value.startswith("quackd ")]
    assert lines and not any("--controller" in line for line in lines), lines
    next(s for s in at.selectbox if s.label == "Vendor").select("anthropic").run()
    next(s for s in at.segmented_control if s.label == "Who flies it").set_value("vla").run()
    goal = next(c.value for c in at.code if c.value.startswith("quackd run"))
    assert "--controller vla" in goal
    assert "--llm" not in goal, "run refuses a model's flag beside --controller vla"


def test_quit_at_once_is_offered_for_a_stopped_run_while_a_chat_goes_on(
    page: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A chat's Stop only brakes, so a chat never stops wanting its button, and a run already
    stopped beside it must still be offered its second press."""
    from tests.test_webui_jobs import _fake, _until

    table = jobs.Jobs(cwd=page, spawn=_fake(page, "listening"))
    monkeypatch.setattr(jobs, "_TABLE", table)
    run = table.start("goal", ["run", "hello-world"], robot="a")
    chat = table.start("chat", ["drive"], robot="b")
    run.stop()
    at = _screen("setup")
    labels = [b.label for b in at.button]
    assert "Stop" in labels and "Quit at once" in labels, labels
    next(b for b in at.button if b.label == "Quit at once").click().run()
    quit_sent = {"op": "stop", "now": True}
    _until(lambda: quit_sent in [x["data"] for x in run.since(0) if x.get("kind") == "got"])
    for job in (run, chat):
        job.end_input()
        job.wait(30)


def test_a_chat_is_not_started_under_a_vla_pilot_drive_cannot_fly() -> None:
    """drive takes no VLA pilot yet, and a chat started under one would be flown by whatever LLM
    the robot or the environment names, without a word."""
    state.setup().set("controller", "vla")
    at = _screen("chat")
    start = next(b for b in at.button if b.label == "Start a chat")
    assert start.disabled
    assert any("quackd drive takes no VLA pilot" in i.value for i in at.info)


def test_a_goal_runs_from_the_page_to_its_outcome() -> None:
    at = _screen("goal")
    at.text_area[0].input("find the ball and kick it").run()
    next(b for b in at.button if b.label == "Run").click().run()
    table = jobs.table()
    (job,) = table.all()
    assert job.kind == "goal" and job.argv[3:5] == ["quackd", "run"]
    _until(at, lambda: not job.running)
    at.run()
    assert [s.value for s in at.success] == ["success: ball displaced by the kick"]


def test_a_chat_starts_takes_a_line_and_ends_in_order() -> None:
    at = _screen("chat")
    next(b for b in at.button if b.label == "Start a chat").click().run()
    (job,) = jobs.table().all()
    assert job.kind == "chat"
    at.chat_input[0].set_value("quack").run()
    assert job.sent == ["quack"]
    _until(at, lambda: any(line.get("kind") == "ended" for line in job.since(0)))
    next(b for b in at.button if b.label == "End the session").click().run()
    assert job.wait(60)
    assert job.snapshot()["end"]["reason"] == "the session ended"


def test_the_floating_stop_stops_a_run_waiting_on_a_question(page: Path) -> None:
    state.setup().set("_task", "file")
    state.setup().set("_file_from", "bundled")
    duck = _gated_duck(page)
    table = jobs.table(page)
    job = table.start(
        "goal",
        ["run", str(duck), "--robot", "microduck:mock", "--llm", "fake", "--no-gif"],
        robot="microduck:mock",
    )
    at = _screen("goal")
    _until(at, lambda: bool(job.snapshot()["asks"]))
    at.run()
    stop = next(b for b in at.button if b.label == "Stop")
    stop.click().run()
    assert job.stop_sent
    assert job.wait(60)
    assert job.snapshot()["end"]["outcome"] == "aborted"


def test_a_key_typed_in_the_page_never_reaches_the_same_as_line() -> None:
    at = _screen("setup")
    vendor = next(s for s in at.selectbox if s.label == "Vendor")
    vendor.select("anthropic").run()
    key = next(t for t in at.text_input if t.label.startswith("ANTHROPIC_API_KEY"))
    key.input("sk-ant-very-secret-key").run()
    assert state.setup().keys["ANTHROPIC_API_KEY"] == "sk-ant-very-secret-key"
    lines = " ".join(c.value for c in at.code)
    assert "quackd run" in lines and "--llm anthropic" in lines
    assert "sk-ant-very-secret-key" not in lines
    assert "sk-ant-very-secret-key" not in (state.setup().path or Path()).read_text(
        encoding="utf-8"
    ), "never written to the saved setup"
