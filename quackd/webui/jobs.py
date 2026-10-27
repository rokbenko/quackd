"""The page's runs, each a child process of its own, started as a terminal would start it.

A job is `python -P -m quackd <command> ... --front jsonl`: the quackd installed beside the
page's own Python, in the working folder the page was started in, talking JSON lines
(`quackd.front`, ADR-0069). The page never imports the robot stack to run anything, so nothing a
run does can take the page down, and nothing the page does, a rerun, a refresh, a closed tab, can
take a run down with it.

**The table is the process's, not a tab's.** Streamlit runs each tab as a session of its own and
restarts a session after a phone's screen has been locked, so a run held by a session would be
lost to the page while the robot went on. Every job lives here instead, for the life of the page's
process, and any tab finds the robot it started and its Stop button.

**One job per robot.** Two runs driving one body would fight over it, and a probe opening a serial
port a run is using puts packets on its bus, so a robot that has a job running is given no
second one, of any kind, until that one has ended.

**A stop is a message, and a kill is the last resort.** `stop` sends the page's Stop, which the
child acts on as the kill switch's first press: the verb is ended, a stop sent, the arm folded,
the record closed. A job that does not end is then given the end of its input, which the child
takes as the page going away, and only one that still does not end is killed, which is reported
as what it is: a run killed before it could fold its arm. The page's process going away closes
every child's input, which is a stop too, so `stop_all` at exit asks and waits and never kills.
"""

from __future__ import annotations

import atexit
import contextlib
import itertools
import json
import subprocess
import sys
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any

from quackd.front import PROTOCOL

EVENTS_KEPT = 5000
"""The newest records a job keeps for the page. The run directory keeps all of them."""

STDERR_KEPT = 400
"""The newest lines of a child's terminal output a job keeps."""

STOP_GRACE_S = 60.0
"""How long `shutdown` waits on a stopped run before it ends its input. A fold on an arm takes
seconds and a teardown a few more; a run still going a minute after its Stop is a run that is not
going to answer one."""

END_GRACE_S = 15.0
"""How long `shutdown` waits on a run whose input has ended before it kills it."""

UNWATCHED_CHAT_S = 15 * 60.0
"""A chat nobody has looked at for this long is ended: the robot stopped, folded, let go of."""

BREAKAWAY = 0x01000000
"""CREATE_BREAKAWAY_FROM_JOB, which keeps a child alive when a job object the page was started in
is closed, as `uv run` and some terminals close theirs. Tried first, and dropped where the job
does not allow it."""

KINDS = ("goal", "chat", "policy", "probe", "command")


def _child_environment(extra: Mapping[str, str] | None) -> dict[str, str]:
    """The page's own environment, as the shell gave it, plus `extra`: the session's keys.

    UTF-8 on every stream, because a Windows pipe is cp1252 otherwise, and no colour: a
    `FORCE_COLOR` would make Rich take the pipe for a terminal and draw a live status line into
    it. Nothing a `.env` gave the page goes along: the child loads the working folder's `.env`
    itself, as a terminal run does, and so still knows which values a file gave it, which the
    settings a project's `.env` may not decide are read by (`dotenv_names`)."""
    from quackd import dotenv_names

    env = dotenv_names.shell_environment()
    env.pop("STREAMLIT_SERVER_COOKIE_SECRET", None)  # the page's own, and no run's business
    env.pop("FORCE_COLOR", None)
    env.update({"PYTHONIOENCODING": "utf-8", "TTY_COMPATIBLE": "0", "NO_COLOR": "1"})
    if extra:
        env.update({k: v for k, v in extra.items() if v})
    return env


def _spawn(argv: Sequence[str], *, cwd: Path, env: Mapping[str, str], **kw: Any) -> Any:
    """Start a child that outlives nothing it should: its own process group, no console of its
    own on Windows, so closing the page's terminal window does not reach it, and out of the
    page's job object where that is allowed."""
    common: dict[str, Any] = {
        "cwd": str(cwd),
        "env": dict(env),
        "stdin": subprocess.PIPE,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        **kw,
    }
    if sys.platform != "win32":
        return subprocess.Popen(list(argv), start_new_session=True, **common)
    flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    try:
        return subprocess.Popen(list(argv), creationflags=flags | BREAKAWAY, **common)
    except OSError:
        # a job object that does not allow breaking away refuses the flag outright
        return subprocess.Popen(list(argv), creationflags=flags, **common)


@dataclass(eq=False)
class Job:
    """One child: what it was asked to do, everything it has said so far, and its pipe."""

    id: str
    kind: str
    robot: str | None
    argv: list[str]
    proc: Any
    started_at: float = field(default_factory=time.time)
    events: deque[dict[str, Any]] = field(default_factory=lambda: deque(maxlen=EVENTS_KEPT))
    seen: int = 0
    """How many records the child has sent: the index the next one will have."""
    stderr: deque[str] = field(default_factory=lambda: deque(maxlen=STDERR_KEPT))
    hello: dict[str, Any] | None = None
    end: dict[str, Any] | None = None
    run_dir: str | None = None
    status: dict[str, Any] | None = None
    frame: dict[str, Any] | None = None
    asks: dict[int, dict[str, Any]] = field(default_factory=dict)
    notices: list[str] = field(default_factory=list)
    stop_sent: bool = False
    killed: bool = False
    last_seen: float = field(default_factory=time.monotonic)
    holds: str | None = None
    """The robot this job holds, as one key for the body itself (`catalog.robot_identity`), so a
    registered name and the spec at the same address are one robot. Its `robot` when not given."""
    lock: threading.Lock = field(default_factory=threading.Lock)
    readers: list[threading.Thread] = field(default_factory=list)
    sent: list[str] = field(default_factory=list)
    """A chat's lines, in the order sent: line n is drive's command n."""

    # ── what the child says ──

    def take(self, raw: str) -> None:
        """One line of the child's stdout. A line that is not the protocol's is kept with its
        terminal output, so nothing it said is lost and nothing breaks the page."""
        try:
            line = json.loads(raw)
        except ValueError:
            line = None
        if not isinstance(line, dict) or not isinstance(line.get("ev"), str):
            if raw.strip():
                self.stderr.append(raw.rstrip("\r\n"))
            return
        try:
            self._dispatch(line)
        except (KeyError, TypeError, ValueError):
            # a line of the protocol's that this page cannot read: kept as text, never a reader
            # thread lost, which would leave the page deaf to the rest of the run
            self.stderr.append(raw.rstrip("\r\n"))

    def _dispatch(self, line: dict[str, Any]) -> None:
        ev = line["ev"]
        with self.lock:
            if ev == "hello":
                self.hello = line
                if line.get("protocol") != PROTOCOL:
                    self.notices.append(
                        f"the child speaks protocol {line.get('protocol')!r}, the page {PROTOCOL}"
                    )
            elif ev == "run_dir":
                self.run_dir = str(line.get("path") or "") or None
            elif ev == "status":
                self.status = line
            elif ev == "frame":
                self.frame = line
            elif ev == "ask":
                self.asks[int(line["id"])] = line
            elif ev == "asked":
                self.asks.pop(int(line["id"]), None)
                self._keep(line)
            elif ev == "end":
                self.end = line
                self.asks.clear()
                self._keep(line)
            elif ev == "notice":
                self.notices.append(str(line.get("text") or ""))
                self._keep(line)
            else:
                self._keep(line)

    def _keep(self, line: dict[str, Any]) -> None:
        line = {**line, "i": self.seen}
        self.seen += 1
        self.events.append(line)

    def since(self, index: int) -> list[dict[str, Any]]:
        """The records kept from `index` on, oldest first. A page that fell behind further than
        `EVENTS_KEPT` gets the oldest still kept, and the run directory has the rest."""
        with self.lock:
            return [line for line in self.events if line["i"] >= index]

    def snapshot(self) -> dict[str, Any]:
        """Everything a page draws of this job, read at one moment."""
        with self.lock:
            self.last_seen = time.monotonic()
            return {
                "id": self.id,
                "kind": self.kind,
                "robot": self.robot,
                "argv": list(self.argv),
                "running": self.running,
                "status": self.status,
                "frame": self.frame,
                "asks": [self.asks[k] for k in sorted(self.asks)],
                "end": self.end,
                "run_dir": self.run_dir,
                "seen": self.seen,
                "notices": list(self.notices),
                "stop_sent": self.stop_sent,
                "killed": self.killed,
                "returncode": self.proc.poll(),
                "stderr": list(self.stderr)[-60:],
            }

    # ── what the page says ──

    @property
    def running(self) -> bool:
        return self.proc.poll() is None

    def send(self, **op: Any) -> bool:
        """One op down the child's stdin. False where the child has gone or its input ended."""
        stdin: IO[bytes] | None = self.proc.stdin
        if stdin is None or stdin.closed or not self.running:
            return False
        try:
            stdin.write((json.dumps(op) + "\n").encode("utf-8"))
            stdin.flush()
        except (OSError, ValueError):
            return False
        return True

    def stop(self, *, now: bool = False) -> bool:
        """The page's Stop. In a run the first is the kill switch's first press, and `now` after
        it quits at once, as a second Ctrl-C does. In a chat every Stop is a brake, as an empty
        Enter is at drive's keyboard, and the session goes on until `quit`: a brake that turned
        the button into a quit would end the session the next time somebody braked."""
        if self.kind == "chat":
            return self.send(op="stop")
        with self.lock:
            first = not self.stop_sent
            self.stop_sent = True
        return self.send(op="stop", now=bool(now and not first))

    def answer(self, question_id: int, yes: bool) -> bool:
        return self.send(op="answer", id=int(question_id), yes=bool(yes))

    def line(self, text: str) -> bool:
        """A chat line, as if typed at drive's keyboard."""
        with self.lock:
            sent = self.send(op="line", text=str(text))
            if sent:
                self.sent.append(str(text))
        return sent

    def quit(self) -> bool:
        """End a chat session in order: the robot stopped, folded, and let go of."""
        return self.send(op="quit")

    def end_input(self) -> None:
        """Close the child's stdin, which it takes as the page going away."""
        stdin = self.proc.stdin
        if stdin is not None and not stdin.closed:
            with contextlib.suppress(OSError):
                stdin.close()

    def wait(self, timeout_s: float) -> bool:
        """Whether the child has ended within `timeout_s`."""
        try:
            self.proc.wait(timeout_s)
        except subprocess.TimeoutExpired:
            return False
        for reader in self.readers:
            reader.join(5)
        return True

    def shutdown(
        self, *, stop_grace_s: float = STOP_GRACE_S, end_grace_s: float = END_GRACE_S
    ) -> None:
        """Stop the child, then end its input, then, only if it is still there, kill it.

        A run killed here could not fold its arm, and the page says so: it is a fault to see,
        not a way to end a run."""
        if not self.running:
            return
        if self.kind in ("goal", "chat"):
            if self.kind == "chat":
                self.quit()  # a chat's Stop only brakes: the session ends in order with quit
            else:
                self.stop()
            if self.wait(stop_grace_s):
                return
        self.end_input()
        if self.wait(end_grace_s):
            return
        with self.lock:
            self.killed = True
            if self.kind in ("goal", "chat"):
                self.notices.append(
                    "the run did not end when asked and was killed: the robot may not have "
                    "been stopped or folded"
                )
        with contextlib.suppress(OSError):
            self.proc.kill()
        self.wait(10)


class Jobs:
    """Every job this page process started, for every tab, until the process ends."""

    def __init__(
        self,
        *,
        cwd: Path | str | None = None,
        python: str = sys.executable,
        spawn: Callable[..., Any] = _spawn,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.cwd = Path(cwd) if cwd is not None else Path.cwd()
        self.python = python
        self._spawn = spawn
        self._clock = clock
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._ids = itertools.count(1)
        self._starting: set[str] = set()
        """Robots a job is being started on: held from the busy check to the job's place in the
        table, so two tabs pressing Run at once start one job, not two."""

    def busy(self, robot: str | None) -> Job | None:
        """The job holding `robot`, while it runs."""
        if robot is None:
            return None
        with self._lock:
            return self._holder(robot)

    def _holder(self, robot: str) -> Job | None:
        for job in self._jobs.values():
            if (job.holds or job.robot) == robot and job.running:
                return job
        return None

    def start(
        self,
        kind: str,
        quackd_args: Sequence[str],
        *,
        robot: str | None = None,
        holds: str | None = None,
        env: Mapping[str, str] | None = None,
        front: bool = True,
        module: str = "quackd",
    ) -> Job:
        """Start `quackd <quackd_args>` as a job, behind the page's front end unless `front` is
        False (a probe, or a server that speaks no protocol). Refuses a robot that has a job, or
        one a job is being started on: `holds` names the body itself, `robot` where not given."""
        if kind not in KINDS:
            raise ValueError(f"a job is one of {', '.join(KINDS)}, not {kind!r}")
        key = holds or robot
        if key is not None:
            with self._lock:
                holder = self._holder(key)
                if holder is not None:
                    raise RuntimeError(
                        f"{robot} is busy with {holder.kind} {holder.id}: stop it first"
                    )
                if key in self._starting:
                    raise RuntimeError(f"{robot} is busy: a job is starting on it")
                self._starting.add(key)
        try:
            argv = [self.python, "-P", "-m", module, *quackd_args]
            if front:
                argv += ["--front", "jsonl"]
            proc = self._spawn(argv, cwd=self.cwd, env=_child_environment(env))
            job = Job(
                id=f"{kind}-{next(self._ids)}",
                kind=kind,
                robot=robot,
                argv=argv,
                proc=proc,
                holds=key,
            )
            job.readers = [
                threading.Thread(target=_pump_out, args=(job,), daemon=True, name=f"{job.id}-out"),
                threading.Thread(target=_pump_err, args=(job,), daemon=True, name=f"{job.id}-err"),
            ]
            for reader in job.readers:
                reader.start()
            with self._lock:
                self._jobs[job.id] = job
        finally:
            if key is not None:
                with self._lock:
                    self._starting.discard(key)
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def all(self) -> list[Job]:
        with self._lock:
            return list(self._jobs.values())

    def running(self) -> list[Job]:
        return [job for job in self.all() if job.running]

    def end_unwatched_chats(self, *, after_s: float = UNWATCHED_CHAT_S) -> list[Job]:
        """End each chat nobody has looked at for `after_s`, in order, and return them."""
        now = self._clock()
        ended = []
        for job in self.running():
            if job.kind == "chat" and now - job.last_seen > after_s and job.quit():
                ended.append(job)
        return ended

    def stop_all(self, *, wait_s: float = 30.0) -> None:
        """At the page's exit: stop every child and end its input, then wait for them. Never a
        kill of anything that holds a robot: a run whose input has ended stops, folds and closes
        by itself. A policy server holds none and reads no input, so it is ended outright, or it
        would keep its port and its GPU after the page had gone. A command left starting an
        endpoint is left to finish, since cutting it off could leave a GPU billing unwatched."""
        jobs = self.running()
        for job in jobs:
            if job.kind == "goal":
                job.stop()
            elif job.kind == "chat":
                job.quit()
            elif job.kind == "policy":
                with contextlib.suppress(OSError):
                    job.proc.terminate()
            job.end_input()
        deadline = time.monotonic() + wait_s
        for job in jobs:
            job.wait(max(0.0, deadline - time.monotonic()))


def _pump_out(job: Job) -> None:
    stdout = job.proc.stdout
    if stdout is None:
        return
    for raw in stdout:
        job.take(raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw)


def _pump_err(job: Job) -> None:
    stderr = job.proc.stderr
    if stderr is None:
        return
    for raw in stderr:
        text = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
        job.stderr.append(text.rstrip("\r\n"))


_TABLE: Jobs | None = None
_TABLE_LOCK = threading.Lock()


def table(cwd: Path | str | None = None) -> Jobs:
    """The page process's one job table, made on first use, stopped at exit, and watched for
    chats nobody is looking at."""
    global _TABLE
    with _TABLE_LOCK:
        if _TABLE is None:
            _TABLE = Jobs(cwd=cwd)
            atexit.register(_TABLE.stop_all)
            threading.Thread(target=_janitor, args=(_TABLE,), daemon=True, name="jobs").start()
        return _TABLE


def _janitor(jobs: Jobs, every_s: float = 30.0) -> None:
    while True:
        time.sleep(every_s)
        with contextlib.suppress(Exception):
            jobs.end_unwatched_chats()


def run_command(
    quackd_args: Sequence[str],
    *,
    cwd: Path | str | None = None,
    env: Mapping[str, str] | None = None,
    timeout_s: float = 120.0,
    python: str = sys.executable,
) -> subprocess.CompletedProcess[str]:
    """A short quackd command the page needs an answer from (`robot add`, `memory clear`,
    `discover --json`): run to its end, its input empty, so nothing it asks can wait on a page
    that never answers. A command that asks for a yes is given `--yes` by its caller, and only
    after the page has asked the person itself."""
    return subprocess.run(
        [python, "-P", "-m", "quackd", *quackd_args],
        cwd=str(Path(cwd) if cwd is not None else Path.cwd()),
        env=_child_environment(env),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_s,
        check=False,
    )
