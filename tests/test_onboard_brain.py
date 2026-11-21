"""The run slot and the control connection: which run gets the robot, and who may ask.

A run here is a stand-in with the loop's two handles the slot touches, its executor's abort and
its stop reason, so the rules are checked without an LLM or a body: one run at a time, a goal
takes the slot from a meeting and never from a goal, meetings are bounded by the hour, by robot
and by the owner's word on strangers, and a meeting its robots left ends after the linger.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from roboglot.onboard import brain, config, control
from roboglot.onboard.brain import COOLDOWN_S, LINGER_S, RunSlot

#: The most any test here waits on a thread, a run or a condition: an outcome, never a timing.
WAIT_S = 10.0
#: How long a stuck meeting holds out unless released: longer than the slot ever waits for one.
STUCK_S = 3 * WAIT_S


class Executor:
    def __init__(self) -> None:
        self.abort = asyncio.Event()


class Run:
    """A run that waits until its abort is set, as a stopped run does, and says why it ended."""

    def __init__(self, glot: Any, kind: str) -> None:
        self.glot, self.kind = glot, kind
        self.executor = Executor()
        self.stop_reason: str | None = None
        self.run_dir = Path(".")

    async def run(self) -> Any:
        await self.executor.abort.wait()
        return type("Result", (), {"outcome": "aborted", "reason": self.stop_reason})()


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def robot_file(**brain_and_meeting: Any) -> config.RobotFile:
    text = config.template(
        name="g1-edu-01",
        vendor="unitree",
        model="g1-edu",
        owner_label="Example lab",
        owner_contact=None,
        embodiment="humanoid",
        mobility="legged",
        llm="fake",
    )
    file = config.loads(text)
    for key, value in brain_and_meeting.items():
        setattr(file.meeting, key, value)
    return file


def slot(file: config.RobotFile | None = None, clock: Clock | None = None) -> RunSlot:
    built: list[Run] = []

    def make(glot: Any, kind: str, front: Any) -> Run:
        run = Run(glot, kind)
        built.append(run)
        return run

    return RunSlot(
        file or robot_file(),
        make,
        safe_verbs=lambda: ["report_state", "stop"],
        clock=clock or Clock(),
    )


def wait_idle(s: RunSlot) -> None:
    running = s.running
    if running is not None and running.thread is not None:
        running.thread.join(timeout=5)
        assert not running.thread.is_alive()


def wait_until(check: Any, seconds: float = WAIT_S) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if check():
            return True
        time.sleep(0.01)
    return bool(check())


def test_a_robot_near_an_idle_robot_starts_a_meeting_and_a_second_one_joins_it() -> None:
    s = slot()
    assert s.robot_near("did:a", "known") == "a meeting starts"
    assert s.state()["run"] == "meeting"
    assert s.robot_near("did:b", "stranger") == "it joins the meeting in hand"
    assert s.state()["with"] == ["did:a", "did:b"]
    assert s.stop()
    wait_idle(s)
    assert s.state() == {"run": "idle"}


def test_a_goal_takes_the_slot_from_a_meeting_and_never_from_a_goal() -> None:
    s = slot()
    s.robot_near("did:a", "known")
    meeting = s.running
    assert s.goal("Wait by the door.") is None
    assert meeting is not None and meeting.agent.stop_reason == brain.FOR_A_GOAL
    assert s.state()["run"] == "goal"
    assert s.goal("Another errand.") == "a goal is running: roboglot onboard stop ends it"
    assert "joins the goal" in s.robot_near("did:c", "known")
    s.stop()
    wait_idle(s)


class Stuck(Run):
    """A meeting whose teardown does not end on its abort, as one in a body connect that does not
    read the abort, or a state read with no deadline, does: it ends when the test releases it."""

    def __init__(self, glot: Any, kind: str, release: threading.Event) -> None:
        super().__init__(glot, kind)
        self.release = release

    async def run(self) -> Any:
        await asyncio.to_thread(self.release.wait, STUCK_S)
        return type("Result", (), {"outcome": "aborted", "reason": self.stop_reason})()


def hooked_slot(release: threading.Event, calls: list[str]) -> RunSlot:
    """A slot whose meetings are `Stuck` and whose hooks say which run held and let go."""

    def make(glot: Any, kind: str, front: Any) -> Run:
        calls.append(f"made({kind})")
        return Stuck(glot, kind, release) if kind == "meeting" else Run(glot, kind)

    return RunSlot(
        robot_file(),
        make,
        safe_verbs=lambda: ["report_state", "stop"],
        holding=lambda agent: calls.append(f"holding({agent.kind})"),
        released=lambda: calls.append("released"),
    )


def test_a_goal_never_runs_beside_a_meeting_that_has_not_stopped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A goal stopped the meeting in hand, waited for it and then started whether it had ended or
    not: two runs on one body, the meeting's teardown sending its own stop in the middle of the
    goal, and its released hook clearing the courtesy the goal's holding hook had just set. A
    meeting still running when the wait is over refuses the goal now, with a reason that says so,
    and stays the slot's one run."""
    monkeypatch.setattr(brain, "MEETING_STOPS_S", 0.2)
    release, calls = threading.Event(), []
    s = hooked_slot(release, calls)
    try:
        assert s.robot_near("did:a", "known") == "a meeting starts"
        meeting = s.running
        assert s.goal("Wait by the door.") == brain.STILL_STOPPING
        assert meeting is not None and meeting.thread is not None and meeting.thread.is_alive()
        assert s.running is meeting and s.state()["run"] == "meeting"
    finally:
        release.set()
        wait_idle(s)
    assert calls == ["made(meeting)", "holding(meeting)", "released"]


def test_the_slot_answers_while_a_goal_waits_for_its_meeting_to_end(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """goal() waited for the meeting it stopped while it held the slot's lock, so status, stop,
    the service's tick and a robot coming near all waited with it, for as long as the meeting
    took, and `roboglot onboard status` and `stop` gave up on a service that was running. They
    answer now while the goal waits, no meeting starts in the gap, and once the meeting ends
    the goal holds the body after the meeting let go of it, never before."""
    monkeypatch.setattr(brain, "MEETING_STOPS_S", WAIT_S)
    release, calls = threading.Event(), []
    s = hooked_slot(release, calls)
    answers: list[str | None] = []
    giver = threading.Thread(target=lambda: answers.append(s.goal("Wait by the door.")))
    try:
        s.robot_near("did:a", "known")
        meeting = s.running
        assert meeting is not None
        giver.start()
        assert wait_until(lambda: meeting.stop_wanted == brain.FOR_A_GOAL)
        meanwhile: list[Any] = []
        other = threading.Thread(
            target=lambda: meanwhile.extend(
                [s.state()["run"], s.tick(), s.robot_near("did:b", "known"), s.goal("Another.")]
            )
        )
        other.start()
        other.join(WAIT_S)
        assert not other.is_alive() and giver.is_alive(), "the slot waited with the goal"
        assert meanwhile == ["meeting", None, brain.NO_MEETING_FOR_A_GOAL, brain.GOAL_WAITING]
        release.set()
        giver.join(WAIT_S)
        assert answers == [None] and s.state()["run"] == "goal"
        assert wait_until(lambda: "holding(goal)" in calls)
        assert calls == [
            "made(meeting)",
            "holding(meeting)",
            "released",
            "made(goal)",
            "holding(goal)",
        ]
    finally:
        release.set()
        if giver.ident is not None:
            giver.join(WAIT_S)
        s.stop()
        wait_idle(s)


def test_a_run_that_is_no_longer_the_slots_changes_nothing_of_the_one_that_is() -> None:
    """The released hook of a run the slot no longer held cleared the courtesy the slot's run had
    set, so every pause a robot met asked for was refused while that run moved the body. Its
    hooks now act only while it is the slot's run; here another run is put in its place by hand,
    as the slot itself no longer lets happen."""
    release, calls = threading.Event(), []
    s = hooked_slot(release, calls)
    try:
        s.robot_near("did:a", "known")
        meeting = s.running
        assert meeting is not None and wait_until(lambda: "holding(meeting)" in calls)
        s.running = brain.Running("goal", "goal", 0.0, set(), Run(None, "goal"))
        release.set()
        assert meeting.thread is not None
        meeting.thread.join(WAIT_S)
        assert not meeting.thread.is_alive()
        assert calls == ["made(meeting)", "holding(meeting)"]
    finally:
        release.set()


def test_meetings_are_bounded_by_the_hour_by_robot_and_by_strangers() -> None:
    clock = Clock()
    s = slot(robot_file(per_hour=2, with_strangers=False), clock)
    assert "meets no strangers" in s.robot_near("did:x", "stranger")
    for peer in ("did:a", "did:b"):
        assert s.robot_near(peer, "known") == "a meeting starts"
        s.stop()
        wait_idle(s)
    assert "2 meetings this hour" in s.robot_near("did:c", "known")
    clock.now += 3601
    assert s.robot_near("did:c", "known") == "a meeting starts"
    s.stop()
    wait_idle(s)
    assert "met this robot" in s.robot_near("did:c", "known")
    clock.now += COOLDOWN_S
    assert s.robot_near("did:c", "known") == "a meeting starts"
    s.stop()
    wait_idle(s)


def test_a_robot_without_a_brain_holds_no_meetings() -> None:
    file = robot_file()
    file.brain.llm = None
    assert "no brain configured" in slot(file).robot_near("did:a", "known")


def test_a_meeting_its_robots_left_ends_after_the_linger() -> None:
    clock = Clock()
    s = slot(clock=clock)
    s.robot_near("did:a", "known")
    s.robot_left("did:a")
    s.tick()
    assert s.state()["run"] == "meeting"
    clock.now += LINGER_S
    s.tick()
    wait_idle(s)
    assert s.state() == {"run": "idle"}


# ── the control connection ──────────────────────────────────────────────────────────────


def test_only_a_client_with_the_token_is_answered(tmp_path: Path) -> None:
    seen: list[dict[str, Any]] = []

    def handle(op: Any, _conn: Any) -> dict[str, Any]:
        seen.append(dict(op))
        return {"ev": "status", "robot": "g1-edu-01"}

    where = tmp_path / "control.json"
    server = control.ControlServer(handle, hello={"robot": "g1-edu-01"}).start(where)
    try:
        assert control.request(where, {"op": "status"}) == {"ev": "status", "robot": "g1-edu-01"}
        forged = tmp_path / "forged.json"
        forged.write_text(json.dumps({"port": server.port, "token": "not-it"}), encoding="utf-8")
        with pytest.raises(control.ControlError, match="token"):
            control.request(forged, {"op": "status"})
        assert [op["op"] for op in seen] == ["status"]
    finally:
        server.close()


@pytest.mark.parametrize("left", ["a file", "a link"])
def test_the_token_goes_through_a_file_of_its_own_whatever_is_left_at_its_old_temporary_name(
    tmp_path: Path, left: str
) -> None:
    """The token went to control.json through the one fixed name control.json.tmp, opened
    without an exclusive create and following a link: a file someone left there kept its own
    mode and owner and was renamed into place holding the token, and a link sent the token
    wherever it pointed. Whatever is at that name now stays as it was, the token's file is its
    owner's alone, and no temporary file is left beside it."""
    where = tmp_path / "control.json"
    old = tmp_path / "control.json.tmp"
    elsewhere = tmp_path / "elsewhere.json"
    elsewhere.write_text("not the token\n", encoding="utf-8")
    if left == "a file":
        old.write_text("not the token\n", encoding="utf-8")
        if os.name != "nt":
            old.chmod(0o644)
    elif os.name == "nt":
        pytest.skip("Windows makes links only with a privilege")
    else:
        old.symlink_to(elsewhere)
    server = control.ControlServer(lambda op, conn: {"ev": "ok"}, hello={}).start(where)
    try:
        assert json.loads(where.read_text(encoding="utf-8"))["token"] == server.token
        assert (old.is_symlink(), old.exists()) == (left == "a link", True)
        assert old.read_text(encoding="utf-8") == elsewhere.read_text(encoding="utf-8")
        assert elsewhere.read_text(encoding="utf-8") == "not the token\n"
        if os.name != "nt":
            assert where.stat().st_mode & 0o077 == 0, oct(where.stat().st_mode)
        names = sorted(path.name for path in tmp_path.iterdir())
        assert names == sorted({where.name, old.name, elsewhere.name})
    finally:
        server.close()


def test_a_client_that_trickles_bytes_inside_every_read_timeout_is_cut_off_after_hello_s(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The cut-off meant to give the hello and the op `HELLO_S` in all closed the socket from
    a timer, which only marked it closed while the connection's two files held it open, and the
    read went on. So a client without the token that sent a byte inside every per-read timeout
    kept its slot for as long as it liked, and eight of them turned away status, stop and goal.
    This one sends a byte every 0.6 x `HELLO_S`, never a newline, for six times `HELLO_S`."""
    import socket
    import time

    hello_s = 1.0
    monkeypatch.setattr(control, "HELLO_S", hello_s)
    server = control.ControlServer(lambda op, conn: {"ev": "ok"}, hello={"robot": "r"})
    server.start(tmp_path / "control.json")
    slow = socket.create_connection(("127.0.0.1", server.port), timeout=WAIT_S)
    slow.settimeout(0.6 * hello_s)
    started, cut_at = time.monotonic(), None
    try:
        while cut_at is None and time.monotonic() - started < 6 * hello_s:
            try:
                slow.sendall(b"x")
                data = slow.recv(100)
            except TimeoutError:
                continue  # nothing came back: still connected, still holding its slot
            except OSError:
                data = b""
            if data == b"" or b"refused" in data:
                cut_at = time.monotonic() - started
        assert cut_at is not None, f"still connected after {6 * hello_s:g} s of trickling"
        assert wait_until(lambda: server._clients._value == control.CLIENTS)  # type: ignore[attr-defined]
    finally:
        with contextlib.suppress(OSError):
            slow.close()
        server.close()


@pytest.mark.parametrize("op_lands", ["just before the cut-off", "just as the cut-off fires"])
def test_an_op_is_either_handled_and_answered_or_cut_off_and_never_handled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, op_lands: str
) -> None:
    """The cut-off was cancelled only once the op had been read, and a timer already firing
    cannot be cancelled: an op read just as it fired was handled, a goal started or a run
    stopped, while its client was cut off and told the service had not answered. When the op
    lands as the cut-off fires, the read here holds the moment between the op's read and its
    acceptance open until the cut has happened; when it lands before, `HELLO_S` is longer than
    the test. Nothing waits on a clock: the test waits for the cut, and for the connection's
    slot to come back, which is when its handler, if any, has run."""
    late = op_lands == "just as the cut-off fires"
    monkeypatch.setattr(control, "HELLO_S", 0.5 if late else WAIT_S * 10)
    cut, real_cut, real_read = threading.Event(), control._cut, control.Connection.read

    def cutting(sock: Any) -> None:
        real_cut(sock)
        cut.set()

    def read(self: Any) -> Any:
        got = real_read(self)
        if late and not isinstance(self, control._Client) and got and got.get("op") == "status":
            cut.wait(WAIT_S)  # the op is read; the cut-off fires before it is accepted
        return got

    monkeypatch.setattr(control, "_cut", cutting)
    monkeypatch.setattr(control.Connection, "read", read)
    handled: list[str] = []
    server = control.ControlServer(
        lambda op, conn: handled.append(str(op.get("op"))) or {"ev": "status"},
        hello={"robot": "r"},
    ).start(tmp_path / "control.json")
    try:
        try:
            reply: Any = control.request(
                tmp_path / "control.json", {"op": "status"}, timeout=WAIT_S
            )
        except control.ControlError as e:
            reply = e
        # the connection's slot comes back once its thread is done, a handler and all
        assert wait_until(lambda: server._clients._value == control.CLIENTS)  # type: ignore[attr-defined]
        if late:
            assert isinstance(reply, control.ControlError) and handled == [], (reply, handled)
        else:
            assert reply == {"ev": "status"} and handled == ["status"], (reply, handled)
    finally:
        server.close()


def test_with_no_service_running_a_client_is_told_so(tmp_path: Path) -> None:
    with pytest.raises(control.ControlError, match="no service is running"):
        control.request(tmp_path / "control.json", {"op": "status"})


def test_a_kept_connection_carries_lines_until_the_handler_closes_it(tmp_path: Path) -> None:
    """A goal keeps its connection: the run's lines go out on it until the run ends."""
    done = threading.Event()

    def handle(op: Any, conn: Any) -> None:
        def later() -> None:
            conn.send({"ev": "event", "kind": "step"})
            conn.send({"ev": "end", "outcome": "success"})
            conn.close()
            done.set()

        threading.Thread(target=later).start()

    where = tmp_path / "control.json"
    server = control.ControlServer(handle, hello={}).start(where)
    try:
        with control.connect(where) as conn:
            conn.send({"op": "goal", "text": "wave"})
            lines = [conn.read(), conn.read()]
        assert [line["ev"] for line in lines if line] == ["event", "end"]
        assert done.wait(5)
    finally:
        server.close()


def test_a_stop_takes_back_the_question_a_goal_waits_on() -> None:
    """A run waiting on its question holds its own loop, where the abort cannot land until the
    question is answered, so a stop takes the question back, as a page's Stop does. Before, a
    stop said it worked and the goal waited up to five minutes for its answer."""

    class Questions:
        closed: str | None = None

        def close(self, why: str) -> None:
            self.closed = why

    class Front:
        questions = Questions()

    slot = RunSlot(robot_file(), lambda glot, kind, front: Run(glot, kind), safe_verbs=list)
    hold = threading.Event()
    thread = threading.Thread(target=hold.wait, daemon=True)
    thread.start()
    front = Front()
    slot.running = brain.Running("goal", "goal", 0.0, set(), None, front)
    slot.running.thread = thread
    try:
        assert slot.stop("stopped by roboglot onboard stop")
        assert front.questions.closed == "stopped by roboglot onboard stop"
    finally:
        hold.set()


def test_a_stop_says_whether_the_run_it_stopped_has_ended() -> None:
    """`roboglot onboard stop` said "stopped the run in hand" as soon as the stop was posted, so a
    run whose stop had not landed, or whose teardown was still sending, read as stopped. The
    slot waits now for the run it stopped, a bound at most and holding nothing the slot needs,
    and says whether it ended: whether there was a run, and whether it has ended."""
    s = slot()
    assert s.stop_and_wait(WAIT_S) == (False, True)
    s.robot_near("did:a", "known")
    assert s.stop_and_wait(WAIT_S) == (True, True)
    release, calls = threading.Event(), []
    stuck = hooked_slot(release, calls)
    try:
        stuck.robot_near("did:a", "known")
        assert stuck.stop_and_wait(0.2) == (True, False)
        assert stuck.state()["run"] == "meeting"
    finally:
        release.set()
        wait_idle(stuck)


def test_a_client_that_says_hello_slowly_is_cut_off_and_a_goals_reads_wait(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A client without the token held a slot for as long as it sent a byte every few seconds,
    and eight of them locked the owner out of stop and status. And a goal's reads kept the
    hello's five seconds, so any LLM turn longer than that ended the goal."""
    import socket
    import time

    monkeypatch.setattr(control, "HELLO_S", 0.5)
    server = control.ControlServer(lambda op, conn: {"ev": "ok"}, hello={"robot": "r"})
    server.start(tmp_path / "control.json")
    try:
        slow = socket.create_connection(("127.0.0.1", server.port), timeout=5)
        started = time.monotonic()
        slow.sendall(b"{")
        time.sleep(0.3)
        with contextlib.suppress(OSError):
            slow.sendall(b'"')  # still a byte inside every per-read timeout
        try:
            data = slow.recv(100)
        except OSError:
            data = b""
        assert data == b"" and time.monotonic() - started < 3
        slow.close()
        with control.connect(tmp_path / "control.json") as conn:
            assert conn.sock.gettimeout() is None
        odd = socket.create_connection(("127.0.0.1", server.port), timeout=5)
        odd.sendall(json.dumps({"op": "hello", "token": "\u00e9t\u00e9"}).encode() + b"\n")
        assert b"refused" in odd.recv(1000)  # a token that is not ASCII is a refusal, not a crash
        odd.close()
    finally:
        server.close()
