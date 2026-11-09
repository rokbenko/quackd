"""The rosarm contract job: the transport against a real rosbridge and a real action server.

`rosarm:mock` rehearses every task against a fake bridge written from reading
(`FAKE_FROM_READING` in `quackd_rosarm.upstream_api`). This holds the transport to the real
thing instead, in the official ROS 2 image of a distribution, Jazzy or Humble: rosbridge_server
and rosapi as that distribution releases them, roslibpy as quackd locks it, a
robot_state_publisher serving the arm's description, and a stand-in for the arm's driver in rclpy
(`tests/rosarm_contract/arm.py`), whose action server and status topic are ROS 2's own. What a
JointTrajectoryController or MotoROS2 does with a goal is the stand-in's here, so nothing below
speaks for either of them, and nothing here has met an arm.

It needs ROS, so it is opt-in and skipped everywhere else. From the repository's root:

    docker run --rm -v "$PWD":/repo:ro -w /repo ros:jazzy bash tests/rosarm_contract/run.sh

and `.github/workflows/rosarm-contract.yml` runs the same on Jazzy and on Humble.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import math
import os
import signal
import socket
import subprocess
import sys
import time
from collections.abc import Awaitable, Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml

from quackd.safety import Executor
from quackd.transport.base import HeartbeatError, Intent, TransportError
from quackd.verbs.registry import registry_from_manifest
from quackd_rosarm import RosArmAdapter
from quackd_rosarm.bodies import BODIES, ArmBody, arm_body
from quackd_rosarm.fake import description_urdf
from quackd_rosarm.transport import RosArmWs, trajectory_goal
from quackd_rosarm.verbs import TOL_DEG

pytestmark = pytest.mark.skipif(
    os.environ.get("QUACKD_ROSARM_CONTRACT") != "1",
    reason="the contract job is opt-in: it runs in an official ROS 2 image (tests/rosarm_contract)",
)

DISTRO = os.environ.get("ROS_DISTRO", "")
ARM = Path(__file__).resolve().parent / "rosarm_contract" / "arm.py"
ACTION_TYPE = "control_msgs/action/FollowJointTrajectory"
READY_S = 60.0
"""How long a cell may take to come up: the bridge's port, and the action among rosapi's list."""
DOMAINS = itertools.count(11)
"""A ROS graph of its own for each cell, so nothing one case started is seen by the next."""


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _ros_env(domain: int) -> dict[str, str]:
    """The environment a ROS process gets: the distribution's own Python and packages, never the
    suite's virtual environment, in a graph of its own on this machine alone."""
    env = {k: v for k, v in os.environ.items() if k not in ("VIRTUAL_ENV", "PYTHONHOME")}
    env.pop("PYTHONPATH", None)
    suite = str(Path(sys.executable).parent)
    env["PATH"] = os.pathsep.join(p for p in env.get("PATH", "").split(os.pathsep) if p != suite)
    env["ROS_DOMAIN_ID"] = str(domain)
    if DISTRO == "humble":
        env["ROS_LOCALHOST_ONLY"] = "1"
    else:
        env["ROS_AUTOMATIC_DISCOVERY_RANGE"] = "LOCALHOST"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONUNBUFFERED"] = "1"
    return env


class _Process:
    """One ROS process, in a session of its own so that its children stop with it."""

    def __init__(self, name: str, argv: list[str], env: dict[str, str], logs: Path) -> None:
        self.name = name
        self.log_path = logs / f"{name}.log"
        self._log = self.log_path.open("w", encoding="utf-8")
        self.proc = subprocess.Popen(
            ["bash", "-c", 'source "/opt/ros/$ROS_DISTRO/setup.bash" && exec "$@"', name, *argv],
            env=env,
            stdout=self._log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )

    def signal(self, number: int) -> None:
        os.killpg(self.proc.pid, number)

    def stop(self) -> None:
        if self.proc.poll() is None:
            self.signal(signal.SIGINT)
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                self.signal(signal.SIGKILL)
                self.proc.wait(10)
        self._log.close()

    def tail(self, lines: int = 40) -> str:
        text = self.log_path.read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(text[-lines:])


def _a_person(_name: str, _params: dict[str, Any]) -> bool:
    return True


_a_person.asks_a_person = True  # type: ignore[attr-defined]
"""A person answering at a terminal: on `rosarm:ws` a standing answer does not clear a move."""


def _middle(body: ArmBody) -> list[float]:
    """Mid travel on every joint, where the suite's fake arm starts too."""
    return [(joint.lower + joint.upper) / 2.0 for joint in body.description]


class Cell:
    """One arm's stand-in driver, its description and a bridge, in a ROS graph of their own.

    `new_thread` is the bridge's `send_action_goals_in_new_thread`: true, false, or None for
    what the distribution's own launch file says. `active` false is a controller that rejects
    every goal."""

    def __init__(
        self, body: ArmBody, logs: Path, *, new_thread: bool | None = True, active: bool = True
    ) -> None:
        self.body = body
        self.port = _free_port()
        self.address = f"ws://127.0.0.1:{self.port}"
        env = _ros_env(next(DOMAINS))
        node = body.description_param.split(":", 1)[0]
        namespace = node.rsplit("/", 1)[0] or "/"
        params = logs / "description.yaml"
        params.write_text(
            yaml.safe_dump(
                {"/**": {"ros__parameters": {"robot_description": description_urdf(body)}}}
            ),
            encoding="utf-8",
        )
        self.processes = [
            _Process(
                "robot_state_publisher",
                [
                    "ros2",
                    "run",
                    "robot_state_publisher",
                    "robot_state_publisher",
                    "--ros-args",
                    "-r",
                    f"__ns:={namespace}",
                    "--params-file",
                    str(params),
                ],
                env,
                logs,
            ),
            _Process(
                "arm",
                [
                    "python3",
                    str(ARM),
                    "--ros-args",
                    "-p",
                    f"joints:={json.dumps(list(body.joints))}",
                    "-p",
                    f"positions:={json.dumps(_middle(body))}",
                    "-p",
                    f"action:={body.action}",
                    "-p",
                    f"joint_states:={body.joint_states}",
                    "-p",
                    f"rate_hz:={float(body.rate_hz)}",
                    "-p",
                    f"active:={str(active).lower()}",
                ],
                env,
                logs,
            ),
        ]
        bridge = [
            "ros2",
            "launch",
            "rosbridge_server",
            "rosbridge_websocket_launch.xml",
            f"port:={self.port}",
        ]
        if new_thread is not None:
            bridge.append(f"send_action_goals_in_new_thread:={str(new_thread).lower()}")
        self.processes.append(_Process("rosbridge", bridge, env, logs))

    @property
    def arm(self) -> _Process:
        return self.processes[1]

    def wait_ready(self) -> None:
        """The bridge's port answers and rosapi lists the arm's action: what a connect needs."""
        deadline = time.monotonic() + READY_S
        while True:
            for process in self.processes:
                assert process.proc.poll() is None, f"{process.name} exited:\n{process.tail()}"
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=1.0):
                    pass
                if self.body.action in _action_servers(self.port):
                    return
            except Exception:  # not up yet, and the deadline below says when that is a failure
                pass
            assert time.monotonic() < deadline, "\n\n".join(
                f"{p.name}:\n{p.tail()}" for p in self.processes
            )
            time.sleep(0.5)

    def close(self) -> None:
        for process in reversed(self.processes):
            process.stop()

    def logs(self) -> str:
        return "\n\n".join(f"── {p.name} ──\n{p.tail()}" for p in self.processes)


def _raw_client(port: int) -> Any:
    """roslibpy itself, for what quackd never does: a goal from another client."""
    import roslibpy

    ros = roslibpy.Ros("127.0.0.1", port)
    ros.run(10)
    return ros


def _action_servers(port: int) -> list[str]:
    import roslibpy

    ros = _raw_client(port)
    try:
        service = roslibpy.Service(
            ros, "/rosapi/action_servers", "rosapi_msgs/srv/GetActionServers"
        )
        answer = service.call(roslibpy.ServiceRequest({}), timeout=5)
        return [str(name) for name in answer.get("action_servers") or []]
    finally:
        ros.close()


@pytest.fixture
def cells(tmp_path: Path) -> Iterator[Callable[..., Cell]]:
    """Start a cell; every one started is stopped after the test, and its logs are printed, which
    pytest shows when the test failed."""
    started: list[Cell] = []

    def start(body: ArmBody, **kwargs: Any) -> Cell:
        logs = tmp_path / f"cell{len(started)}"
        logs.mkdir()
        cell = Cell(body, logs, **kwargs)
        started.append(cell)
        cell.wait_ready()
        return cell

    yield start
    for cell in started:
        cell.close()
        print(cell.logs())


async def _until(check: Callable[[], Awaitable[Any]], seconds: float) -> Any:
    """What `check` answers once it answers something, or its last answer at the deadline."""
    deadline = time.monotonic() + seconds
    while True:
        if found := await check():
            return found
        if time.monotonic() >= deadline:
            return found
        await asyncio.sleep(0.1)


# ── each arm, over a threaded bridge ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("robot_type", list(BODIES))
async def test_each_arm_connects_moves_and_stops_over_a_real_bridge(
    robot_type: str, cells: Callable[..., Cell]
) -> None:
    """Every check at connect passes against the real thing: the bridge's own parameter read
    through rosapi in the `node:parameter` form, the action among rosapi's list, readings by name
    in an order other than the controller's, and the description off robot_state_publisher's
    parameter. A driver status whose message package the image does not have is read as no status
    at all. A confirmed `move_joints` arrives, judged off the readings, and a stop in the middle of
    the next move is answered CANCELED, after which the arm holds."""
    body = arm_body(robot_type)
    cell = cells(body)
    adapter = RosArmAdapter(RosArmWs(cell.address, body=body), robot_id="cell-01")
    manifest = await adapter.connect()
    try:
        transport = adapter.transport
        state = await adapter.get_state()
        assert set(state.extras["joints"]) == set(body.joints)
        assert state.extras["stale"] is None
        assert state.extras["driver"]["read"] is False, "no driver status crossed the bridge"
        for joint, described in zip(body.joints, body.description, strict=True):
            lower, upper = transport.travel_deg[joint]
            assert lower == pytest.approx(math.degrees(described.lower), abs=1e-5)
            assert upper == pytest.approx(math.degrees(described.upper), abs=1e-5)
        executor = Executor(
            registry_from_manifest(manifest, adapter), adapter, contract=None, confirm=_a_person
        )
        first = body.joints[0]
        here = state.extras["joints"][first]
        moved = await executor.run_verb(
            "move_joints", {"positions": {first: here + 5.0}, "duration_s": 2}
        )
        assert moved.ok, moved.summary
        arrived = (await adapter.get_state()).extras["joints"][first]
        assert arrived == pytest.approx(here + 5.0, abs=TOL_DEG)

        goal = dict((await adapter.get_state()).extras["joints"])
        goal[first] -= 10.0
        sent = await transport.send_intent(Intent.joint(goal, 6.0))
        assert sent.accepted, sent.reason

        async def under_way() -> bool:
            return bool((await adapter.get_state()).extras["joints"][first] < arrived - 1.5)

        assert await _until(under_way, 8.0), "the move never got under way"
        await transport.stop()
        assert transport.stop_error is None, transport.stop_error
        assert (await adapter.get_state()).extras["move"]["status"] == "canceled"
        # a reading is a few hundredths of a second old when the cancel's answer comes, so the
        # arm is read again once the readings have caught up with where the cancel left it
        await asyncio.sleep(0.5)
        held = (await adapter.get_state()).extras["joints"][first]
        assert arrived - 10.0 + 0.5 < held < arrived - 0.5, "it stopped part of the way"
        await asyncio.sleep(1.0)
        later = (await adapter.get_state()).extras["joints"][first]
        assert later == pytest.approx(held, abs=0.05), "and held there"
    finally:
        await adapter.close()


# ── what the bridge does that the design rests on ────────────────────────────────────────────


async def test_the_stock_launch_file_decides_whether_a_bridge_is_refused(
    cells: Callable[..., Cell],
) -> None:
    """The distribution's own launch file, with nothing said of the goal thread: Humble's says
    false, so quackd refuses that bridge in words naming rosbridge_suite#909, and Jazzy's says
    true, so it connects (`GOAL_THREAD_DEFAULTS`)."""
    body = arm_body("ur5e")
    cell = cells(body, new_thread=None)
    transport = RosArmWs(cell.address, body=body)
    if DISTRO == "humble":
        with pytest.raises(TransportError, match=r"#909") as refused:
            await transport.connect()
        assert "send_action_goals_in_new_thread:=true" in str(refused.value)
    else:
        await transport.connect()
        await transport.close()


async def test_a_bridge_that_runs_goals_on_its_reader_lands_a_cancel_after_the_move(
    cells: Callable[..., Cell],
) -> None:
    """`GOAL_RUNS_ON_THE_READER`, on the real bridge: with the goal thread off, a cancel sent a
    second into a three second move is reached only once the move has ended, so the goal ends
    SUCCEEDED. That is the bridge quackd refuses at connect."""
    import roslibpy

    body = arm_body("ur5e")
    cell = cells(body, new_thread=False)
    with pytest.raises(TransportError, match=r"#909"):
        await RosArmWs(cell.address, body=body).connect()
    ros = _raw_client(cell.port)
    try:
        client = roslibpy.ActionClient(ros, body.action, ACTION_TYPE)
        start = _middle(body)
        goal = list(start)
        goal[0] += math.radians(10.0)
        results: list[Any] = []
        feedback: list[Any] = []
        goal_id = client.send_goal(
            roslibpy.Goal(trajectory_goal(list(body.joints), start, goal, 3.0)),
            results.append,
            feedback.append,
            results.append,
        )

        async def moving() -> bool:
            return bool(feedback)

        async def answered() -> bool:
            return bool(results)

        # the first goal through a bridge that has just started waits while the bridge makes
        # its action client, so the second is counted from the move's own start
        assert await _until(moving, 15.0), "the move never started"
        await asyncio.sleep(1.0)
        client.cancel_goal(goal_id)
        assert await _until(answered, 10.0), "no result came"
        status = results[0]["status"]
        assert int(getattr(status, "value", status)) == 4, results[0]  # SUCCEEDED
    finally:
        ros.close()


async def test_a_goal_left_running_refuses_the_connect_until_it_ends(
    cells: Callable[..., Cell],
) -> None:
    """A client that sends a goal and goes, as a run that went quiet does, leaves it running:
    the bridge cancels nothing for it (`CLOSED_CLIENT_CANCELS_NOTHING`). The action's own status,
    latched and handed over through the bridge, is what refuses the next connect while it runs
    (`ACTION_STATUS_HEARD`), and once the move has ended the arm connects, standing where that
    goal put it."""
    import roslibpy

    body = arm_body("ur5e")
    cell = cells(body)
    ros = _raw_client(cell.port)
    client = roslibpy.ActionClient(ros, body.action, ACTION_TYPE)
    start = _middle(body)
    goal = list(start)
    goal[0] += math.radians(20.0)
    feedback: list[Any] = []
    client.send_goal(
        roslibpy.Goal(trajectory_goal(list(body.joints), start, goal, 6.0)),
        lambda _result: None,
        feedback.append,
        lambda _error: None,
    )

    async def moving() -> bool:
        return bool(feedback)

    assert await _until(moving, 15.0), "the move never started"
    started_at = time.monotonic()
    ros.close()  # gone, with no cancel
    with pytest.raises(TransportError) as refused:
        await RosArmWs(cell.address, body=body).connect()
    said = str(refused.value)
    assert "is still running a goal sent before quackd connected (executing)" in said, said
    # refused for as long as the move runs, and taken once it has ended
    while True:
        transport = RosArmWs(cell.address, body=body)
        try:
            await transport.connect()
            break
        except TransportError as again:
            assert "is still running a goal" in str(again), str(again)
            assert time.monotonic() < started_at + 6.0 + 10.0, "refused long after the move"
            await asyncio.sleep(0.5)
    assert time.monotonic() >= started_at + 6.0 - 0.5, "taken before the move could have ended"
    try:
        reached = (await transport.get_state()).extras["joints"][body.joints[0]]
        assert reached == pytest.approx(math.degrees(goal[0]), abs=TOL_DEG), "it ran to its end"
    finally:
        await transport.close()


async def test_readings_that_stop_fail_the_heartbeat(cells: Callable[..., Cell]) -> None:
    """The arm's driver frozen: no new `/joint_states` for half a second fails the heartbeat, and
    readings that come again pass it."""
    body = arm_body("ur5e")
    cell = cells(body)
    transport = RosArmWs(cell.address, body=body)
    await transport.connect()
    try:
        await transport.heartbeat()
        cell.arm.signal(signal.SIGSTOP)
        try:
            await asyncio.sleep(1.0)
            with pytest.raises(HeartbeatError, match=r"no new /joint_states"):
                await transport.heartbeat()
        finally:
            cell.arm.signal(signal.SIGCONT)
        await asyncio.sleep(1.0)
        await transport.heartbeat()
    finally:
        await transport.close()


async def test_a_rejected_goal_reads_refused(cells: Callable[..., Cell]) -> None:
    """A controller that rejects the goal, as an inactive one does: the bridge says so with
    `result: false`, and the move reads refused rather than waiting out its deadline."""
    body = arm_body("ur5e")
    cell = cells(body, active=False)
    transport = RosArmWs(cell.address, body=body)
    await transport.connect()
    try:
        goal = dict((await transport.get_state()).extras["joints"])
        goal[body.joints[0]] += 5.0
        assert (await transport.send_intent(Intent.joint(goal, 2.0))).accepted

        async def ended() -> dict[str, Any] | None:
            move = (await transport.get_state()).extras["move"]
            return move if move and move["status"] != "running" else None

        move = await _until(ended, 15.0)
        assert move is not None and move["status"] == "refused", move
    finally:
        await transport.close()
