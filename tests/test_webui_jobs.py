"""The page's job table: children started, watched and stopped, one robot at a time
(`quackd.webui.jobs`). Plain Python: runs without Streamlit, on every operating system."""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from quackd.front import PROTOCOL
from quackd.webui import jobs as jobs_mod
from quackd.webui.jobs import Job, Jobs, _child_environment
from tests.test_front_run import _gated_duck

FAKE_CHILD = textwrap.dedent(
    """
    import json, os, sys, time
    mode = sys.argv[1]

    def say(**line):
        sys.stdout.write(json.dumps(line) + "\\n")
        sys.stdout.flush()

    say(ev="hello", protocol=int(os.environ.get("FAKE_PROTOCOL", "1")), command="run")
    say(ev="ask", id=1, kind="confirm", question="run kick()?")
    print("not a protocol line", flush=True)
    if mode == "deaf":
        while True:  # hears neither a stop nor the end of its input
            time.sleep(0.1)
    for raw in sys.stdin:
        op = json.loads(raw)
        say(ev="event", kind="got", data=op)
        if op.get("op") == "answer":
            say(ev="asked", id=op["id"], answer=op["yes"])
        if op.get("op") == "stop" and mode == "obedient":
            say(ev="end", outcome="aborted", reason="stop button", exit_code=1)
            sys.exit(1)
        if op.get("op") == "quit":
            say(ev="end", outcome="success", reason="the session ended", exit_code=0)
            sys.exit(0)
    say(ev="end", outcome="aborted", reason="the page went away", exit_code=1)
    sys.exit(1)
    """
)


def _fake(tmp_path: Path, mode: str) -> Callable[..., Any]:
    """A spawn that starts the scripted child instead of quackd, with the env and cwd it gets."""
    script = tmp_path / "fake_child.py"
    script.write_text(FAKE_CHILD, encoding="utf-8")

    def spawn(_argv: list[str], *, cwd: Path, env: dict[str, str], **_kw: Any) -> Any:
        return subprocess.Popen(
            [sys.executable, str(script), mode],
            cwd=str(cwd),
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    return spawn


def _until(done: Callable[[], bool], within_s: float = 60.0) -> None:
    """Wait until `done()`: a bound on a wait, never a figure a test checks."""
    deadline = time.monotonic() + within_s
    while not done():
        if time.monotonic() > deadline:
            raise AssertionError("it never came")
        time.sleep(0.02)


def test_a_job_reads_the_childs_lines_and_keeps_the_rest_as_its_terminal(tmp_path: Path) -> None:
    jobs = Jobs(cwd=tmp_path, spawn=_fake(tmp_path, "obedient"))
    job = jobs.start("goal", ["run", "hello-world"], robot="duck")
    _until(lambda: bool(job.snapshot()["asks"]))
    view = job.snapshot()
    assert view["asks"] == [{"ev": "ask", "id": 1, "kind": "confirm", "question": "run kick()?"}]
    assert job.hello is not None and job.hello["protocol"] == PROTOCOL
    _until(lambda: "not a protocol line" in job.stderr)
    assert job.answer(1, True)
    _until(lambda: not job.snapshot()["asks"])
    assert job.stop()
    assert job.wait(30)
    end = job.snapshot()["end"]
    assert (end["outcome"], end["reason"]) == ("aborted", "stop button")
    got = [line["data"] for line in job.since(0) if line.get("kind") == "got"]
    assert got == [{"op": "answer", "id": 1, "yes": True}, {"op": "stop", "now": False}]
    assert [line["i"] for line in job.since(0)] == list(range(job.seen))


def test_every_stop_in_a_chat_is_a_brake_and_a_chat_shut_down_ends_with_quit(
    tmp_path: Path,
) -> None:
    """In a chat a Stop brakes, as an empty Enter does at drive's keyboard, and the session goes
    on: every Stop is the same brake, never the quit at once a run's second Stop is, so the
    button stays a Stop. Shutting a chat down ends it in order, with quit."""
    jobs = Jobs(cwd=tmp_path, spawn=_fake(tmp_path, "listening"))
    job = jobs.start("chat", ["drive"])
    assert job.stop() and job.stop(now=True)
    assert not job.stop_sent, "the button stays a Stop"
    job.shutdown(stop_grace_s=30, end_grace_s=30)
    sent = [x["data"] for x in job.since(0) if x.get("kind") == "got"]
    assert sent == [{"op": "stop"}, {"op": "stop"}, {"op": "quit"}]
    assert job.snapshot()["end"]["reason"] == "the session ended"


def test_a_second_stop_asks_to_quit_at_once_and_only_a_second(tmp_path: Path) -> None:
    jobs = Jobs(cwd=tmp_path, spawn=_fake(tmp_path, "listening"))
    job = jobs.start("goal", ["run", "hello-world"])
    assert job.stop(now=True), "the first stop is the orderly one, whatever it asks"
    assert job.stop(now=True)
    _until(lambda: len([x for x in job.since(0) if x.get("kind") == "got"]) == 2)
    sent = [x["data"] for x in job.since(0) if x.get("kind") == "got"]
    assert sent == [{"op": "stop", "now": False}, {"op": "stop", "now": True}]
    job.end_input()
    assert job.wait(30)


def test_a_robot_with_a_job_gets_no_second_one_until_it_ends(tmp_path: Path) -> None:
    jobs = Jobs(cwd=tmp_path, spawn=_fake(tmp_path, "listening"))
    first = jobs.start("chat", ["drive"], robot="arm-01")
    with pytest.raises(RuntimeError, match="arm-01 is busy with chat"):
        jobs.start("probe", ["robot", "list"], robot="arm-01")
    other = jobs.start("goal", ["run", "x"], robot="duck")
    assert jobs.busy("arm-01") is first and jobs.busy("duck") is other
    first.quit()
    assert first.wait(30)
    assert jobs.busy("arm-01") is None
    again = jobs.start("goal", ["run", "y"], robot="arm-01")
    for job in (other, again):
        job.end_input()
        job.wait(30)


def test_a_child_deaf_to_stop_and_input_is_killed_and_said_to_be(tmp_path: Path) -> None:
    jobs = Jobs(cwd=tmp_path, spawn=_fake(tmp_path, "deaf"))
    job = jobs.start("goal", ["run", "hello-world"])
    _until(lambda: job.hello is not None)
    job.shutdown(stop_grace_s=0.5, end_grace_s=0.5)
    assert not job.running and job.killed
    assert any("may not have been stopped or folded" in n for n in job.notices)


def test_a_child_that_ends_on_its_input_is_not_killed(tmp_path: Path) -> None:
    jobs = Jobs(cwd=tmp_path, spawn=_fake(tmp_path, "listening"))
    job = jobs.start("goal", ["run", "hello-world"])
    job.shutdown(stop_grace_s=0.5, end_grace_s=30)
    assert not job.running and not job.killed
    assert job.snapshot()["end"]["reason"] == "the page went away"


def test_stop_all_at_exit_asks_and_waits_and_never_kills(tmp_path: Path) -> None:
    jobs = Jobs(cwd=tmp_path, spawn=_fake(tmp_path, "deaf"))
    job = jobs.start("goal", ["run", "hello-world"])
    _until(lambda: job.hello is not None)
    jobs.stop_all(wait_s=0.5)
    try:
        assert job.running and not job.killed, "a child left to stop by itself, never killed"
    finally:
        job.proc.kill()
        job.wait(30)


def test_a_chat_nobody_has_looked_at_is_ended_in_order(tmp_path: Path) -> None:
    now = [1000.0]
    jobs = Jobs(cwd=tmp_path, spawn=_fake(tmp_path, "listening"), clock=lambda: now[0])
    chat = jobs.start("chat", ["drive"], robot="duck")
    goal = jobs.start("goal", ["run", "x"], robot="arm")
    chat.last_seen = goal.last_seen = 1000.0
    now[0] += jobs_mod.UNWATCHED_CHAT_S - 1
    assert jobs.end_unwatched_chats() == []
    now[0] += 2
    assert jobs.end_unwatched_chats() == [chat], "a goal ends by its own budgets"
    assert chat.wait(30)
    assert chat.snapshot()["end"]["reason"] == "the session ended"
    goal.end_input()
    goal.wait(30)


def test_a_child_speaking_another_protocol_is_said_to(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_PROTOCOL", str(PROTOCOL + 1))
    jobs = Jobs(cwd=tmp_path, spawn=_fake(tmp_path, "listening"))
    job = jobs.start("goal", ["run", "x"])
    _until(lambda: bool(job.notices))
    assert job.notices == [f"the child speaks protocol {PROTOCOL + 1}, the page {PROTOCOL}"]
    job.end_input()
    job.wait(30)


def test_a_line_the_page_cannot_read_is_kept_as_text_and_the_reader_goes_on() -> None:
    job = Job(id="goal-1", kind="goal", robot=None, argv=[], proc=None)
    job.take(json.dumps({"ev": "asked"}) + "\n")  # no id
    job.take(json.dumps({"ev": "status", "text": "observing"}) + "\n")
    assert job.status == {"ev": "status", "text": "observing"}
    assert job.stderr[-1] == '{"ev": "asked"}'


def test_the_child_gets_the_page_environment_with_utf8_no_colour_and_the_session_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FORCE_COLOR", "1")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-from-the-shell")
    env = _child_environment({"ANTHROPIC_API_KEY": "sk-session", "OPENAI_API_KEY": ""})
    assert "FORCE_COLOR" not in env
    assert (env["PYTHONIOENCODING"], env["TTY_COMPATIBLE"]) == ("utf-8", "0")
    assert env["ANTHROPIC_API_KEY"] == "sk-session"
    assert env["OPENAI_API_KEY"] == "sk-from-the-shell", "an empty session key overrides nothing"


def test_a_real_run_through_the_table_answers_in_the_page_and_holds_its_robot(
    tmp_path: Path,
) -> None:
    """The table starting quackd itself: a run waiting on a question holds its robot, and the
    page's answer lets it finish."""
    jobs = Jobs(cwd=tmp_path)
    args = ["run", str(_gated_duck(tmp_path)), "--robot", "microduck:mock", "--llm", "fake"]
    job = jobs.start(
        "goal", [*args, "--no-gif", "--runs-dir", str(tmp_path / "runs")], robot="mock"
    )
    _until(lambda: bool(job.snapshot()["asks"]))
    with pytest.raises(RuntimeError, match="mock is busy"):
        jobs.start("goal", args, robot="mock")
    (ask,) = job.snapshot()["asks"]
    assert job.answer(ask["id"], True)
    assert job.wait(60)
    view = job.snapshot()
    assert (view["end"]["outcome"], view["returncode"]) == ("success", 0)
    assert view["run_dir"] and Path(view["run_dir"]).is_dir()
    assert job.argv[1:4] == ["-P", "-m", "quackd"] and job.argv[-2:] == ["--front", "jsonl"]


def test_two_presses_at_once_start_one_job_on_a_robot(tmp_path: Path) -> None:
    """Two tabs, or a tab and a phone, pressing Run together: the busy check, the start and the
    job's place in the table are one step for the robot, so one of them is refused."""
    started = threading.Event()
    real = _fake(tmp_path, "listening")

    def slow_spawn(argv: list[str], **kw: Any) -> Any:
        started.set()
        time.sleep(0.3)
        return real(argv, **kw)

    jobs = Jobs(cwd=tmp_path, spawn=slow_spawn)
    outcomes: list[str] = []

    def press() -> None:
        try:
            jobs.start("goal", ["run", "hello-world"], robot="arm", holds="lerobot:real@com5")
            outcomes.append("started")
        except RuntimeError as e:
            outcomes.append(str(e))

    first = threading.Thread(target=press)
    first.start()
    started.wait(10)
    press()
    first.join(30)
    assert sorted(outcomes, key=len) == ["started", "arm is busy: a job is starting on it"]
    (job,) = jobs.all()
    with pytest.raises(RuntimeError, match="arm-01 is busy with goal"):
        jobs.start("chat", ["drive"], robot="arm-01", holds="lerobot:real@com5")
    job.end_input()
    assert job.wait(30)


def test_a_policy_server_is_ended_when_the_page_exits(tmp_path: Path) -> None:
    """It holds no robot and reads no input, so the end of its input never ended it, and it
    kept its port and its GPU after the page had gone."""
    jobs = Jobs(cwd=tmp_path, spawn=_fake(tmp_path, "deaf"))
    server = jobs.start("policy", ["policy", "serve"], front=False)
    _until(lambda: server.hello is not None)
    jobs.stop_all(wait_s=30)
    assert not server.running
