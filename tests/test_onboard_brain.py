"""The run slot and the control connection: which run gets the robot, and who may ask.

A run here is a stand-in with the loop's two handles the slot touches, its executor's abort and
its stop reason, so the rules are checked without a model or a body: one run at a time, a goal
takes the slot from a meeting and never from a goal, meetings are bounded by the hour, by robot
and by the owner's word on strangers, and a meeting its robots left ends after the linger.
"""

from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path
from typing import Any

import pytest

from quackd.onboard import brain, config, control
from quackd.onboard.brain import COOLDOWN_S, LINGER_S, RunSlot


class Executor:
    def __init__(self) -> None:
        self.abort = asyncio.Event()


class Run:
    """A run that waits until its abort is set, as a stopped run does, and says why it ended."""

    def __init__(self, duck: Any, kind: str) -> None:
        self.duck, self.kind = duck, kind
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

    def make(duck: Any, kind: str, front: Any) -> Run:
        run = Run(duck, kind)
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
    assert s.goal("Another errand.") == "a goal is running: quackd onboard stop ends it"
    assert "joins the goal" in s.robot_near("did:c", "known")
    s.stop()
    wait_idle(s)


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
