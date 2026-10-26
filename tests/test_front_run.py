"""`quackd run --front jsonl`, started as the page `quackd ui` starts it: a child process whose
stdout is JSON lines and whose stdin is the page's ops (`quackd.front`).

Real children, so each test is a process start: few of them, each run on the mock duck with the
scripted pilot, and each one about a way the run can end."""

from __future__ import annotations

import json
import os
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

REPO = Path(__file__).resolve().parents[1]


def _env() -> dict[str, str]:
    env = dict(os.environ, PYTHONIOENCODING="utf-8", TTY_COMPATIBLE="0")
    env.pop("FORCE_COLOR", None)
    return env


def _spawn_kw() -> dict[str, Any]:
    if sys.platform == "win32":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW}
    return {"start_new_session": True}


class _Page:
    """The page's end of one child: its JSON lines read as they come, and its ops sent."""

    def __init__(self, args: list[str], cwd: Path) -> None:
        self.proc = subprocess.Popen(
            [sys.executable, "-P", "-m", "quackd", *args, "--front", "jsonl"],
            cwd=cwd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=_env(),
            **_spawn_kw(),
        )
        self.lines: list[dict[str, Any]] = []
        self.not_json: list[str] = []
        self.stderr: list[str] = []
        self._readers = [
            threading.Thread(target=self._read_out, daemon=True),
            threading.Thread(target=self._read_err, daemon=True),
        ]
        for reader in self._readers:
            reader.start()

    def _read_out(self) -> None:
        assert self.proc.stdout is not None
        for raw in self.proc.stdout:
            text = raw.decode("utf-8", "replace").strip()
            try:
                self.lines.append(json.loads(text))
            except ValueError:
                self.not_json.append(text)

    def _read_err(self) -> None:
        assert self.proc.stderr is not None
        for raw in self.proc.stderr:
            self.stderr.append(raw.decode("utf-8", "replace"))

    def send(self, **op: Any) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write((json.dumps(op) + "\n").encode())
        self.proc.stdin.flush()

    def close(self) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.close()

    def wait_for(self, match: Callable[[dict[str, Any]], bool], within_s: float = 60) -> Any:
        """The first line `match` takes, once it has come: a bound on a wait, not a figure."""
        deadline = time.monotonic() + within_s
        while time.monotonic() < deadline:
            for line in list(self.lines):
                if match(line):
                    return line
            if self.proc.poll() is not None and not any(r.is_alive() for r in self._readers):
                break
            time.sleep(0.02)
        raise AssertionError(f"never came; stderr: {''.join(self.stderr)[-2000:]}")

    def finish(self, within_s: float = 60) -> int:
        if self.proc.stdin is not None and not self.proc.stdin.closed:
            self.close()
        code = self.proc.wait(within_s)
        for reader in self._readers:
            reader.join(within_s)
        return code


def _transcript(run_dir: str) -> list[dict[str, Any]]:
    path = Path(run_dir) / "transcript.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _gated_duck(tmp_path: Path) -> Path:
    """The smoke test with its walk behind a confirm gate. Still named hello-world, which is
    how the scripted pilot knows to quack, walk and quack again."""
    text = (REPO / "ducks" / "hello-world.duck").read_text(encoding="utf-8")
    assert "  confirm: []" in text
    duck = tmp_path / "gated.duck"
    duck.write_text(text.replace("  confirm: []", "  confirm: [walk]"), encoding="utf-8")
    return duck


def _run_args(tmp_path: Path, *task: str) -> list[str]:
    runs = str(tmp_path / "runs")
    return [
        "run",
        *task,
        "--robot",
        "microduck:mock",
        "--llm",
        "fake",
        "--no-gif",
        "--runs-dir",
        runs,
    ]


def test_a_run_from_the_page_says_hello_first_end_last_and_nothing_but_json(
    tmp_path: Path,
) -> None:
    page = _Page(_run_args(tmp_path, "hello-world"), tmp_path)
    end = page.wait_for(lambda line: line["ev"] == "end")
    assert page.finish() == 0
    assert page.not_json == [], "stdout carries the protocol's lines and nothing else"
    assert page.lines[0]["ev"] == "hello"
    assert (page.lines[0]["protocol"], page.lines[0]["command"]) == (PROTOCOL, "run")
    assert page.lines[-1] is end
    assert (end["outcome"], end["exit_code"]) == ("success", 0)
    kinds = {line["kind"] for line in page.lines if line["ev"] == "event"}
    assert {"run_start", "verb_start", "verb_end", "run_end"} <= kinds
    assert any(line["ev"] == "status" for line in page.lines)
    start = next(r for r in _transcript(end["run_dir"]) if r["kind"] == "run_start")
    assert start["front"] == "jsonl"
    assert "--front" not in start["command"], "the record names the command to type at a terminal"
    assert "Stop in the page stops the duck." in "".join(page.stderr)


@pytest.mark.parametrize("yes", [True, False], ids=["yes", "no"])
def test_a_confirm_gate_is_answered_in_the_page_as_a_person(tmp_path: Path, yes: bool) -> None:
    page = _Page(_run_args(tmp_path, str(_gated_duck(tmp_path))), tmp_path)
    ask = page.wait_for(lambda line: line["ev"] == "ask")
    assert (ask["kind"], ask["question"]) == (
        "confirm",
        "run walk(vx=0.1, vy=0, wz=0, duration_s=1)?",
    )
    page.send(op="answer", id=ask["id"], yes=yes)
    end = page.wait_for(lambda line: line["ev"] == "end")
    page.finish()
    records = _transcript(end["run_dir"])
    prompts = [r for r in records if r["kind"] == "prompt"]
    assert [(p["answer"], p["channel"]) for p in prompts] == [(yes, "web")]
    walk = next(r for r in records if r["kind"] == "gate" and r["name"] == "walk")
    assert walk["reason"] == f"a human said {'yes' if yes else 'no'}"


def test_a_stop_while_the_page_is_asked_takes_the_question_back(tmp_path: Path) -> None:
    """Withdrawn, never answered no: the gate says the prompt raised, no `prompt` row claims a
    person said anything, and the run ends as the Stop button ended it."""
    page = _Page(_run_args(tmp_path, str(_gated_duck(tmp_path))), tmp_path)
    page.wait_for(lambda line: line["ev"] == "ask")
    page.send(op="stop")
    end = page.wait_for(lambda line: line["ev"] == "end")
    assert page.finish() == 1
    assert (end["outcome"], end["reason"], end["exit_code"]) == ("aborted", "stop button", 1)
    assert any(line.get("withdrawn") == "the run was stopped" for line in page.lines)
    records = _transcript(end["run_dir"])
    assert not [r for r in records if r["kind"] == "prompt"]
    walk = next(r for r in records if r["kind"] == "gate" and r["name"] == "walk")
    assert walk["reason"] == "the prompt raised QuestionWithdrawn: the run was stopped"
    assert records[-1]["kind"] == "run_end"


def test_the_end_of_the_page_input_is_a_stop_nobody_pressed(tmp_path: Path) -> None:
    page = _Page(_run_args(tmp_path, str(_gated_duck(tmp_path))), tmp_path)
    page.wait_for(lambda line: line["ev"] == "ask")
    page.close()
    end = page.wait_for(lambda line: line["ev"] == "end")
    assert page.finish() == 1
    assert (end["outcome"], end["reason"]) == ("aborted", "the page went away")
    walk = next(r for r in _transcript(end["run_dir"]) if r["kind"] == "gate")
    assert walk["reason"] == "the prompt raised QuestionWithdrawn: the page went away"


@pytest.mark.parametrize(
    ("task", "said"),
    [
        (["--goal", "fetch the ball", "--by-hand"], "--by-hand waits for somebody at the arm"),
        (["flock-hello"], "is a task for a flock, and the page drives one robot at a time"),
    ],
    ids=["by-hand", "a flock's task"],
)
def test_the_page_refuses_what_it_cannot_drive_before_anything_is_built(
    tmp_path: Path, task: list[str], said: str
) -> None:
    page = _Page(_run_args(tmp_path, *task), tmp_path)
    end = page.wait_for(lambda line: line["ev"] == "end")
    assert page.finish() == 1
    (refused,) = [line for line in page.lines if line["ev"] == "refused"]
    assert said in refused["message"]
    assert (end["outcome"], end["exit_code"]) == ("refused", 1)
    assert not (tmp_path / "runs").exists(), "a refused run leaves no run directory"


def test_a_stray_write_to_stdout_lands_on_stderr_and_never_breaks_a_line(tmp_path: Path) -> None:
    script = textwrap.dedent(
        """
        import os, sys
        from quackd.front import Channel
        channel = Channel.open()
        print("stray print")
        os.write(1, b"raw write\\n")
        sys.stdout.write("stdout write\\n")
        channel.send("event", n=1)
        channel.close({"ev": "end"})
        """
    )
    done = subprocess.run(
        [sys.executable, "-P", "-c", script],
        cwd=tmp_path,
        capture_output=True,
        env=_env(),
        timeout=60,
        check=True,
    )
    lines = done.stdout.decode().splitlines()
    assert [json.loads(line) for line in lines] == [{"ev": "event", "n": 1}, {"ev": "end"}]
    err = done.stderr.decode()
    assert "stray print" in err and "raw write" in err and "stdout write" in err


def test_a_run_whose_page_dies_still_stops_and_closes_its_record(tmp_path: Path) -> None:
    """The page's process killed outright: the child's input ends with it, the question the run
    was waiting on is taken back, and the run ends and writes its record as for any stop."""
    out_file = tmp_path / "child-out.jsonl"
    child = [sys.executable, "-P", "-m", "quackd"]
    child += [*_run_args(tmp_path, str(_gated_duck(tmp_path))), "--front", "jsonl"]
    parent = tmp_path / "page.py"
    parent.write_text(
        textwrap.dedent(
            f"""
            import subprocess, threading, time
            child = subprocess.Popen(
                {child!r},
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            )
            with open({str(out_file)!r}, "wb") as out:
                def copy():
                    for raw in child.stdout:
                        out.write(raw)
                        out.flush()
                threading.Thread(target=copy, daemon=True).start()
                time.sleep(600)
            """
        ),
        encoding="utf-8",
    )
    page = subprocess.Popen(
        [sys.executable, str(parent)], cwd=tmp_path, env=_env(), stdin=subprocess.DEVNULL
    )
    try:
        deadline = time.monotonic() + 60
        run_dir = None
        while run_dir is None or '"ev": "ask"' not in out_file.read_text(encoding="utf-8"):
            assert time.monotonic() < deadline, "the child never asked"
            time.sleep(0.05)
            if out_file.exists():
                for line in out_file.read_text(encoding="utf-8").splitlines():
                    if '"ev": "run_dir"' in line:
                        run_dir = json.loads(line)["path"]
    finally:
        page.kill()
        page.wait(30)
    transcript = Path(run_dir) / "transcript.jsonl"
    deadline = time.monotonic() + 60
    while '"run_end"' not in transcript.read_text(encoding="utf-8"):
        assert time.monotonic() < deadline, "the child never ended its run"
        time.sleep(0.05)
    end = _transcript(run_dir)[-1]
    assert (end["kind"], end["outcome"], end["reason"]) == (
        "run_end",
        "aborted",
        "the page went away",
    )
