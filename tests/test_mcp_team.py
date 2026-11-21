"""A team over MCP: nine robot_* tools, one executor, budget and heartbeat per robot.

Driven in-process by the SDK's own client over memory streams, exactly like the one-robot
tests.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from typing import Any

import pytest
from mcp.client.session import ClientSession
from mcp.shared.memory import create_client_server_memory_streams

from roboglot.adapters.factory import RobotSpec, make_adapter
from roboglot.mcp_server import ServedTeam, build_team_server
from roboglot.transport.base import TransportError
from roboglot.transport.mock import MockTransport


def one_duck() -> dict[str, Any]:
    return {"duck": make_adapter(RobotSpec("microduck", "mock", "duck"))}


@contextlib.asynccontextmanager
async def connected(
    robots: dict[str, Any] | None = None, **kwargs: Any
) -> AsyncIterator[tuple[ClientSession, ServedTeam]]:
    server, team = build_team_server(robots or one_duck(), heartbeat_period_s=0.05, **kwargs)
    async with create_client_server_memory_streams() as (client_streams, server_streams):
        low = server._lowlevel_server
        task = asyncio.create_task(
            low.run(server_streams[0], server_streams[1], low.create_initialization_options())
        )
        try:
            async with ClientSession(client_streams[0], client_streams[1]) as client:
                await client.initialize()
                yield client, team
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task


def _data(result: Any) -> dict[str, Any]:
    assert not result.is_error, result
    assert result.structured_content is not None
    return result.structured_content


async def test_dry_run_still_observes_but_moves_nothing() -> None:
    async with connected(dry_run=True) as (client, team):
        seen = await client.call_tool("robot_observe", {"robot": "duck"})
        assert {c.type for c in seen.content} == {"text", "image"}  # read-only verbs run
        _data(
            await client.call_tool(
                "robot_assess_task",
                {"verdict": "feasible", "reason": "a test: the body fits the task"},
            )
        )
        moved = _data(
            await client.call_tool("robot_run_verb", {"verb": "move", "params": {"vx": 0.2}})
        )
        assert moved["ok"] and moved["data"].get("dry_run") is True
        assert team.sessions["duck"].transport.transport.intents == []


class _Dead:
    name = "mock"

    def __init__(self) -> None:
        self.closed = False

    async def connect(self) -> None:
        raise TransportError("no robot answered")

    async def stop(self) -> None:
        pass

    async def close(self) -> None:
        self.closed = True

    def now(self) -> float:
        return 0.0

    async def heartbeat(self) -> None:
        pass


async def test_connect_is_fail_fast_and_closes_what_did_connect() -> None:
    first = MockTransport()
    _server, team = build_team_server({"a": first, "b": _Dead()}, heartbeat_period_s=0.05)
    with pytest.raises(TransportError, match="no robot answered"):
        await team.connect_all()
    assert first.connected is False  # connected, then closed again
    assert team.default == "a"


HANG_GUARD_S = 60.0
"""How long a test of the close waits for the event it asserts before it calls the close hung.
Nothing here is timed by it: each event comes in a few turns of the loop, and this only turns a
close that never comes into a failure rather than a run that never ends."""


class _Body(MockTransport):
    """A robot that writes what it is sent into one list the whole team shares, so a test reads
    the order across robots. Its stop and its close each wait one turn of the loop, as a real
    write waits for its port or socket, and its fold at the close waits on `release` when
    `hold` is set, as a real fold waits on its clock between sends. `on_stop` runs as the stop
    lands, before anything after it in the close."""

    def __init__(self, tag: str, log: list[str], *, rest: bool = False, hold: bool = True):
        super().__init__()
        self.tag, self.log, self.hold = tag, log, hold
        if rest:
            self.rest_pose = {"joint": 0.0}
        self.closing = False
        self.fold_started = asyncio.Event()
        self.release = asyncio.Event()
        self.stopped = asyncio.Event()
        self.beat = asyncio.Event()
        """Set at its first heartbeat: the session is up, and the robot is being watched."""
        self.on_stop: Any = None
        self.answers = asyncio.Event()
        """Cleared, the stop goes out and is never answered, as over a link that has gone."""
        self.answers.set()
        self.in_the_fold: BaseException | None = None
        """Raised where the closing fold starts: a press, or a cancel, landing in it."""
        self.note_at_close: str | None = None
        """What its close says of the body, as `close_note`: an arm's torque, kept or let go."""
        self.close_started = asyncio.Event()
        self.close_gate = asyncio.Event()
        """Cleared, the close waits on it, as a real close waits on its last read of the arm."""
        self.close_gate.set()
        self.hold_beat = False
        """Set, each beat waits on a link that does not answer, takes the cancel the heartbeat's
        stop sends it, as Python 3.11's `wait_for` can lose one, and answers once let go."""
        self.beat_cancelled = asyncio.Event()
        self.beat_release = asyncio.Event()

    async def go_to_rest(self) -> Any:
        from roboglot.adapters.base import RestResult

        if not self.closing:
            return RestResult("already", "stand-in: already at the rest pose")
        self.log.append(f"{self.tag}:rest-start")
        self.fold_started.set()
        if self.in_the_fold is not None:
            raise self.in_the_fold
        if self.hold:
            await self.release.wait()
        await asyncio.sleep(0)
        self.log.append(f"{self.tag}:rest-end")
        return RestResult("arrived", "stand-in: folded")

    async def stop(self) -> None:
        self.log.append(f"{self.tag}:stop-begin")
        await asyncio.sleep(0)
        await self.answers.wait()
        await super().stop()
        self.log.append(f"{self.tag}:stop")
        if self.on_stop is not None:
            self.on_stop()
        self.stopped.set()

    async def close(self) -> None:
        self.close_started.set()
        await asyncio.sleep(0)
        await self.close_gate.wait()
        self.log.append(f"{self.tag}:close")
        self.close_note = self.note_at_close
        await super().close()

    async def heartbeat(self) -> None:
        await super().heartbeat()
        self.beat.set()
        if self.hold_beat:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.beat_cancelled.set()
                await self.beat_release.wait()


def _arm_and_base(log: list[str], *, hold: bool = True) -> tuple[ServedTeam, _Body, _Body]:
    """An arm with a rest pose, served first, and a base with none, which may be moving."""
    arm = _Body("arm", log, rest=True, hold=hold)
    base = _Body("base", log)
    _server, team = build_team_server({"arm": arm, "base": base}, heartbeat_period_s=0.05)
    return team, arm, base


async def test_a_team_close_stops_every_robot_before_it_folds_any() -> None:
    """`close_all` closed one robot after another, and the arm's close ran its whole fold before
    the base, which may be driving on its last twist, was sent any stop. A stdio client kills
    the server a fixed grace after it closes its stdin, so a robot late in the list could be
    left moving. The arm's fold waits here until the test lets it go, so the order shows without
    a clock: every robot is stopped before any robot folds."""
    log: list[str] = []
    team, arm, _base = _arm_and_base(log)
    await team.connect_all()
    log.clear()
    arm.closing = True
    closing = asyncio.ensure_future(team.close_all())
    try:
        await asyncio.wait_for(arm.fold_started.wait(), HANG_GUARD_S)
        assert "base:stop" in log, f"the base got no stop while the arm folds: {log}"
        assert log.index("base:stop") < log.index("arm:rest-start"), log
    finally:
        arm.release.set()
        await asyncio.wait_for(closing, HANG_GUARD_S)
    assert log[-2:] == ["arm:close", "base:close"], log


async def test_a_pause_granted_on_one_robot_does_not_hold_back_the_next_robots_stop() -> None:
    """A pause the arm granted a robot it met (`pause_for`, up to the longest of the courtesy's
    `PAUSE_S`) was waited out inside the arm's close, before the base was sent anything, past the
    grace a stdio client gives a server it is ending. The base's stop now comes while the pause
    still holds and before the arm's fold. The pause is withdrawn as the base's stop lands, so
    the close does not wait it out: what the stop saw is all this test asserts."""
    from roboglot.passport_courtesy import PAUSE_S

    log: list[str] = []
    team, arm, base = _arm_and_base(log, hold=False)
    await team.connect_all()
    log.clear()
    arm.closing = True
    paused = team.sessions["arm"].executor
    paused.pause_for(max(PAUSE_S.values()))
    seen: dict[str, Any] = {}

    def as_the_base_stops() -> None:
        import time

        seen["pause_held"] = paused.paused_until > time.monotonic()
        seen["sent_before"] = list(log)
        paused.paused_until = 0.0

    base.on_stop = as_the_base_stops
    await asyncio.wait_for(team.close_all(), HANG_GUARD_S)
    assert seen["pause_held"], f"the base's stop waited out the arm's pause: {seen}"
    assert "arm:rest-start" not in seen["sent_before"], seen
    assert {"arm:stop", "arm:close", "base:stop", "base:close"} <= set(log), log


async def test_a_stop_that_is_never_answered_does_not_hold_the_close(caplog: Any) -> None:
    """The close's stop was awaited with no bound, so a link that had gone held the close, and
    every robot after it, for good. It is waited for as long as the executor waits for a stop it
    owes (the `stop` verb's `timeout_s`, `Executor._answer_of`), said on stderr, and left to go on
    by itself: the arm still folds and closes, and so does the base whose stop never answered."""
    import logging

    caplog.set_level(logging.WARNING, logger="roboglot.mcp")
    log: list[str] = []
    team, arm, base = _arm_and_base(log, hold=False)
    await team.connect_all()
    log.clear()
    arm.closing = True
    base.answers.clear()
    # waited for rather than cancelled at the bound: the close outlasts any cancel it is sent
    closing = asyncio.ensure_future(team.close_all())
    try:
        await asyncio.wait({closing}, timeout=HANG_GUARD_S)
        assert closing.done(), f"the close waits on a stop that is never answered: {log}"
        closing.result()
        assert log[-4:] == ["arm:rest-start", "arm:rest-end", "arm:close", "base:close"], log
        assert "base:stop" not in log
        said = [m for m in caplog.messages if m.startswith("base: the close's stop: ")]
        assert len(said) == 1 and "goes on by itself" in said[0], caplog.messages
    finally:
        base.answers.set()  # the stop that went on by itself lands, and nothing is left running
        await asyncio.wait_for(base.stopped.wait(), HANG_GUARD_S)
        await asyncio.wait({closing}, timeout=HANG_GUARD_S)


@pytest.mark.parametrize("press", [KeyboardInterrupt, asyncio.CancelledError])
def test_a_press_in_the_first_robots_fold_still_stops_folds_and_closes_the_rest(
    press: type[BaseException],
) -> None:
    """A `KeyboardInterrupt` or a `CancelledError` in one robot's close, a person's second Ctrl-C
    landing in the arm's fold, passed the `suppress(Exception)` in `close_all`, and every robot
    after it got no stop, no fold and no close, while the arm's own transport was left open. The
    press ends the fold it lands in and nothing else: the arm is still closed, the base still
    gets its stop, its fold and its close, and the press is raised once they have run.

    On a loop of its own, run as `serve-mcp`'s is: a `KeyboardInterrupt` raised in a task leaves
    the event loop, which is what it does in the server, and would end the suite's own loop."""
    log: list[str] = []

    async def a_session_ended_by_its_client() -> None:
        team, arm, base = _arm_and_base(log)
        base.rest_pose = {"joint": 0.0}
        await team.connect_all()
        log.clear()
        arm.closing = base.closing = True
        base.hold = False
        arm.in_the_fold = press()
        await asyncio.wait_for(team.close_all(), HANG_GUARD_S)

    with pytest.raises(press):
        asyncio.run(a_session_ended_by_its_client())
    assert log.index("base:stop") < log.index("arm:rest-start"), log
    assert "arm:close" in log, f"the arm's transport was left open: {log}"
    assert log[-3:] == ["base:rest-start", "base:rest-end", "base:close"], log


TORQUE_KEPT = "stand-in: the arm is not at rest, so its torque is kept"


def _arm_alone(log: list[str], *, hold: bool = True) -> tuple[Any, _Body]:
    """One arm with a rest pose behind its own session, closed by `RobotSession.close`, as
    `roboglot drive` closes one; its close says what it did with torque."""
    from roboglot.mcp_server import build_server

    arm = _Body("arm", log, rest=True, hold=hold)
    arm.note_at_close = TORQUE_KEPT
    _server, session = build_server(arm, heartbeat_period_s=0.05)
    return session, arm


def _said(caplog: Any, session: Any) -> list[str]:
    return [m.removeprefix(f"{session.name}: ") for m in caplog.messages]


async def test_a_cancel_in_the_closing_fold_holds_the_arm_closes_it_and_says_so(
    caplog: Any,
) -> None:
    """(a) A cancel of the task closing the session, landing in the closing fold, passed the
    `suppress(Exception)` round the fold, and the transport's close and its note never came: the
    arm stayed connected and energised partway through its fold, and nothing said so. The fold
    stops where it is, the arm is held there with a stop, that is said (`FOLD_STOPPED`), the
    transport is closed and its note said, and the cancel is raised after them."""
    import logging

    from roboglot.release import FOLD_STOPPED

    caplog.set_level(logging.WARNING, logger="roboglot.mcp")
    log: list[str] = []
    session, arm = _arm_alone(log)
    await session.connect()
    log.clear()
    arm.closing = True
    closing = asyncio.ensure_future(session.close())
    await asyncio.wait_for(arm.fold_started.wait(), HANG_GUARD_S)
    closing.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(closing, HANG_GUARD_S)
    held = ["arm:stop-begin", "arm:stop"]
    assert log == [*held, "arm:rest-start", *held, "arm:close"], log
    said = _said(caplog, session)
    assert said[-2:] == [FOLD_STOPPED, TORQUE_KEPT], said


@pytest.mark.parametrize("cancelled", ["the closing task", "the halt's own task", "every task"])
def test_a_cancel_while_the_heartbeat_stops_keeps_the_fold_from_starting(cancelled: str) -> None:
    """(b) `Heartbeat.stop` takes a cancel that lands in it, so a cancel of the task closing the
    session while a beat was still answering was swallowed there, and the close went on to stop
    and fold the arm as if nothing had come. The fold does not start now, the arm is held where
    it stands and that is said (`AgentLoop.HELD_BACK`), its transport is closed, and the cancel
    is raised. Three shapes: the closing task alone; the task the heartbeat's stop runs in
    alone, where the cancel is the one `Heartbeat.stop` takes; and every task at once, as
    asyncio's runner cancels them at a second Ctrl-C. On a loop of its own, as the press test
    above runs."""
    import logging

    from roboglot.agent.loop import AgentLoop

    log: list[str] = []
    said: list[str] = []

    class Said(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            said.append(record.getMessage())

    async def a_session_cancelled_as_its_heartbeat_stops() -> None:
        session, arm = _arm_alone(log, hold=False)
        arm.hold_beat = True
        await session.connect()
        await asyncio.wait_for(arm.beat.wait(), HANG_GUARD_S)
        log.clear()
        arm.closing = True
        closing = asyncio.ensure_future(session.close())
        await asyncio.wait_for(arm.beat_cancelled.wait(), HANG_GUARD_S)
        if cancelled == "every task":
            for task in asyncio.all_tasks():
                if task is not asyncio.current_task():
                    task.cancel()
        elif cancelled == "the halt's own task":
            (halt,) = [t for t in asyncio.all_tasks() if t.get_coro().__qualname__.endswith("halt")]
            halt.cancel()
        else:
            closing.cancel()
        arm.beat_release.set()
        await asyncio.wait_for(closing, HANG_GUARD_S)

    handler = Said(logging.WARNING)
    mcp_log = logging.getLogger("roboglot.mcp")
    mcp_log.addHandler(handler)
    try:
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(a_session_cancelled_as_its_heartbeat_stops())
    finally:
        mcp_log.removeHandler(handler)
    assert log == ["arm:stop-begin", "arm:stop", "arm:close"], log
    assert f"robot: {AgentLoop.HELD_BACK}" in said and f"robot: {TORQUE_KEPT}" in said, said


async def test_a_cancel_in_the_wait_for_a_pause_keeps_the_fold_from_starting(caplog: Any) -> None:
    """(c) A cancel that lands while the close waits out a pause the arm granted a robot it met
    was caught there, and the arm still went down after it. The fold does not start now: the arm
    is held where it stands and that is said, its transport is closed and its note said, and the
    cancel is raised. `put_down` is the half of the close the wait is in; its first step runs to
    that wait, so the cancel lands there."""
    import logging

    from roboglot.agent.loop import AgentLoop
    from roboglot.passport_courtesy import PAUSE_S

    caplog.set_level(logging.WARNING, logger="roboglot.mcp")
    log: list[str] = []
    session, arm = _arm_alone(log, hold=False)
    await session.connect()
    log.clear()
    arm.closing = True
    session.executor.pause_for(max(PAUSE_S.values()))
    await session.halt()
    putting = asyncio.ensure_future(session.put_down())
    await asyncio.sleep(0)
    assert not putting.done() and "arm:rest-start" not in log, log
    putting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(putting, HANG_GUARD_S)
    assert log == ["arm:stop-begin", "arm:stop", "arm:close"], log
    assert _said(caplog, session)[-2:] == [AgentLoop.HELD_BACK, TORQUE_KEPT], caplog.messages


async def test_a_cancel_in_the_transports_close_does_not_cut_it_short(caplog: Any) -> None:
    """A cancel that lands in the transport's close, on its last read of the arm, cut that close
    short: the arm stayed connected and energised, and its note never came. The close runs to
    its end through it now, as a run's does (`AgentLoop._unbroken`), its note is said, and the
    cancel is raised after it."""
    import logging

    caplog.set_level(logging.WARNING, logger="roboglot.mcp")
    log: list[str] = []
    session, arm = _arm_alone(log)
    await session.connect()
    log.clear()
    arm.close_gate.clear()
    closing = asyncio.ensure_future(session.close())
    await asyncio.wait_for(arm.close_started.wait(), HANG_GUARD_S)
    closing.cancel()
    await asyncio.sleep(0)
    arm.close_gate.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(closing, HANG_GUARD_S)
    assert log[-1] == "arm:close" and arm.connected is False, log
    assert _said(caplog, session)[-1] == TORQUE_KEPT, caplog.messages


async def test_a_client_dying_mid_session_still_stops_folds_and_closes_every_robot() -> None:
    """`serve-mcp` runs the server inside the task group `stdio_server` opens, whose
    `stdout_writer` raises when a dead client's end refuses a write. anyio then cancels the
    group's host task again at every turn of the loop until it ends, and the lifespan's close ran
    in that task: cut at the first robot's stop, it left by `CancelledError`, and no robot after
    it was stopped, folded or closed. Every robot is now stopped, put down and closed, and the
    group ends with the client's error alone.

    The stand-in for `stdio_server` has its shape: a task group with a child that raises
    `OSError` once both robots are up, and the real server's `run` in its body. Each robot's
    stop, fold and close waits a turn of the loop, where such a cancel lands."""
    import anyio

    died = "-- the client's end refuses a write --"
    log: list[str] = []
    arm = _Body("arm", log, rest=True, hold=False)
    base = _Body("base", log)
    server, _team = build_team_server({"arm": arm, "base": base}, heartbeat_period_s=0.05)
    arm.closing = True
    low = server._lowlevel_server

    async def stdout_writer() -> None:
        await asyncio.wait_for(arm.beat.wait(), HANG_GUARD_S)
        await asyncio.wait_for(base.beat.wait(), HANG_GUARD_S)
        log.append(died)
        raise OSError(32, "Broken pipe")

    raised: BaseException | None = None
    async with create_client_server_memory_streams() as (_client, server_streams):
        try:
            async with anyio.create_task_group() as group:
                group.start_soon(stdout_writer)
                await low.run(
                    server_streams[0], server_streams[1], low.create_initialization_options()
                )
        except BaseException as e:  # the group's error, read below
            raised = e
    after = log[log.index(died) + 1 :]
    # the two stops go out together, in whatever order their links take them
    assert set(after[:4]) == {"arm:stop-begin", "base:stop-begin", "arm:stop", "base:stop"}, after
    assert after[4:] == ["arm:rest-start", "arm:rest-end", "arm:close", "base:close"], after
    assert isinstance(raised, BaseExceptionGroup), repr(raised)
    assert [type(e) for e in raised.exceptions] == [BrokenPipeError], repr(raised)


def test_build_needs_a_robot_and_a_known_default() -> None:
    with pytest.raises(ValueError, match="at least one robot"):
        build_team_server({})
    with pytest.raises(ValueError, match="default robot"):
        build_team_server({"a": MockTransport()}, default="zz")


@pytest.mark.parametrize("spec", ["open_duck:mock", "microduck:mock"])
async def test_a_camera_robot_gets_a_detector(spec: str) -> None:
    """The 0.5 fix landed in `roboglot run` and missed this entry point.

    `build_team_server` still keyed the detector on one backend alone, so every
    hardware body over MCP fetched frames, detected nothing because nothing was detecting,
    and reported that it could not see. The decision now happens after connect, against
    what the robot said it has."""
    robot = make_adapter(spec)
    async with connected({"r": robot}) as (client, team):
        session = team.sessions["r"]
        assert robot.manifest is not None and "camera" in robot.manifest.sensors
        assert session.detector is not None, f"{spec} runs blind over MCP"
        assert session.executor.detector is session.detector
        # and the verbs that need one no longer refuse
        res = _data(await client.call_tool("robot_run_verb", {"verb": "search_scan"}))
        assert "needs a detector" not in res["summary"]


def test_the_detector_policy_is_the_camera_and_nothing_else() -> None:
    """Both entry points call one function so they cannot drift again. Every body that can
    connect offline has a camera, so the two negative cases are pinned here: a body with no
    camera gets nothing, and an explicit `--detector` is never overruled."""
    from roboglot.perception import detector_for
    from roboglot.perception.color_blob import ColorBlobDetector

    assert detector_for(["camera", "imu"]) is not None
    assert detector_for(["joint_state"]) is None  # lerobot:real
    assert detector_for(["odometry"]) is None  # rosbridge:ws before it connects
    mine = ColorBlobDetector()
    assert detector_for(["camera"], mine) is mine
    assert detector_for(["odometry"], mine) is mine


async def test_an_infeasible_verdict_names_the_robot_that_could() -> None:
    """A duck cannot pick anything up and a cart can. The pilot is told which, by name, so
    the task can be handed over rather than abandoned."""
    robots = {
        "duck": make_adapter(RobotSpec("microduck", "mock", "duck")),
        "cart": make_adapter(RobotSpec("alohamini", "mock", "cart")),
    }
    async with connected(robots) as (client, team):
        answer = _data(
            await client.call_tool(
                "robot_assess_task",
                {
                    "verdict": "infeasible",
                    "reason": "the mug needs a gripper and this body has a beak",
                    "limits_consulted": ["manipulator", "payload_kg"],
                    "needs": {"manipulator": "gripper", "payload_kg": 0.4},
                    "robot": "duck",
                },
            )
        )
        assert answer["verdict"] == "infeasible"
        assert answer["could"] == ["cart"]
        assert "a gripper" in answer["could_datasheets"]["cart"]
        assert "robot_load_glotfile" in answer["note"]
        assert not team.sessions["duck"].executor.cleared

        # and the body that was named can be asked
        cleared = _data(
            await client.call_tool(
                "robot_assess_task",
                {"verdict": "feasible", "reason": "1 kg per arm covers a mug", "robot": "cart"},
            )
        )
        assert cleared["could"] == []
        assert team.sessions["cart"].executor.cleared


async def test_a_described_body_is_never_named_as_the_robot_that_could() -> None:
    """Told a body cannot do a task, a client is told which could and to load it on one of them.
    It was offered a UR5e on its maker's figures, a robot nothing here can load a task on."""
    robots = {
        "duck": make_adapter(RobotSpec("microduck", "mock", "duck")),
        "arm": make_adapter(RobotSpec("described", "ur5e", "arm")),
    }
    async with connected(robots) as (client, _team):
        answer = _data(
            await client.call_tool(
                "robot_assess_task",
                {
                    "verdict": "infeasible",
                    "reason": "three kilos, a long way from the duck",
                    "limits_consulted": ["payload_kg"],
                    "needs": {"payload_kg": 3.0, "reach_m": 0.5},
                    "robot": "duck",
                },
            )
        )
        assert answer["could"] == [], answer
        assert "No robot in this team meets what it needs" in answer["note"]


async def test_a_team_says_which_of_its_bodies_cannot_see_their_fall(caplog: Any) -> None:
    """A team server warns for each member whose body cannot see its own fall and cannot get
    up, by that member's name, on stderr and in its assessment, and for no other member."""
    import logging

    from roboglot_open_duck import OpenDuckAdapter
    from tests.test_mcp_server import FALL_BLIND, FallBlindDuck

    caplog.set_level(logging.WARNING, logger="roboglot.mcp")
    robots = {
        "duck": OpenDuckAdapter(FallBlindDuck()),
        "cart": make_adapter(RobotSpec("alohamini", "mock", "cart")),
    }
    async with connected(robots) as (client, _team):
        warned = [m for m in caplog.messages if FALL_BLIND in m]
        assert [m.split(": ")[0] for m in warned] == ["duck"], caplog.messages
        replies = {
            name: _data(
                await client.call_tool(
                    "robot_assess_task",
                    {"verdict": "feasible", "reason": "a test: the body fits", "robot": name},
                )
            )
            for name in robots
        }
    assert FALL_BLIND in replies["duck"]["warning"], replies["duck"]
    assert "warning" not in replies["cart"], replies["cart"]


async def test_the_instructions_describe_each_body_before_anything_connects() -> None:
    """`_instructions` runs when the server is built, which is before `connect_all`. Read
    from the static descriptions it says what each body is; without them it said `a small
    robot` about every one of them."""
    from roboglot.adapters.factory import describe

    specs = {"duck": RobotSpec("microduck", "mock", "duck")}
    server, _team = build_team_server(
        {"duck": make_adapter(specs["duck"])},
        manifests={"duck": describe(specs["duck"])},
    )
    text = server.instructions or ""
    assert "a small biped duck robot" in text
    assert "0.8 kg (official: the Pollen Robotics README)" in text
    assert "robot_assess_task" in text
    assert "a small robot" not in text
