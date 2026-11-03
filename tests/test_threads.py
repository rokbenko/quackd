"""Threads no exit waits for (`quackd.adapters.threads`), and what has to run on them."""

from __future__ import annotations

import asyncio
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from quackd.adapters.threads import DaemonThreads
from quackd_lerobot.policy.loop import PolicyLoop
from quackd_lerobot.real import TORQUE_RELEASE_NOT_KNOWN, LeRobotReal
from tests.test_lerobot_adapter import FOLDED, FakeArm

ROOT = Path(__file__).resolve().parent.parent


def _ended(script: str, *, limit_s: float = 60.0) -> subprocess.CompletedProcess[str]:
    """`script` in a process of its own, which has to end by itself."""
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(script)],
        capture_output=True,
        text=True,
        timeout=limit_s,
        cwd=ROOT,
    )


def test_a_thread_is_a_daemon_and_goes_back_to_work() -> None:
    """A bus called a hundred times a second must not start a hundred threads."""
    threads = DaemonThreads("test")
    first = threads.submit(threading.current_thread)
    seen = first.result(10)
    assert seen.daemon
    assert seen.name == "test-1"
    for _ in range(200):
        threads.submit(threading.current_thread).result(10)
    assert threads._started <= 4, "one a call, or close to it"


def test_a_call_that_is_stuck_does_not_hold_up_the_next() -> None:
    threads = DaemonThreads("test")
    free = threading.Event()
    stuck = threads.submit(free.wait)
    try:
        assert threads.submit(lambda: "answered").result(10) == "answered"
        assert not stuck.done()
    finally:
        free.set()
    assert stuck.result(10) is True


def test_a_call_that_raises_says_so_to_whoever_asked() -> None:
    def fails() -> None:
        raise ValueError("no such motor")

    with pytest.raises(ValueError, match="no such motor"):
        DaemonThreads("test").submit(fails).result(10)


def test_a_call_that_never_comes_back_does_not_hold_the_process() -> None:
    """Both of the joins this is here for: `asyncio.run`'s of its default pool, for five minutes on
    3.12 and for ever on 3.11, and the interpreter's of `concurrent.futures`' pools, with no limit,
    which `threading._shutdown` runs. The test's own timeout is the failure."""
    done = _ended(
        """
        import asyncio, sys, threading
        from quackd.adapters.threads import DaemonThreads

        async def main():
            never = threading.Event()
            loop = asyncio.get_running_loop()
            call = loop.run_in_executor(DaemonThreads("stuck"), never.wait)
            try:
                await asyncio.wait_for(asyncio.shield(call), 0.05)
            except TimeoutError:
                print("the wait is over", flush=True)

        asyncio.run(main())
        print("the run is over", flush=True)
        sys.exit(7)
        """
    )
    assert done.returncode == 7, done.stderr
    assert done.stdout.split("\n")[:2] == ["the wait is over", "the run is over"]


def test_a_real_arm_whose_bus_call_never_returns_lets_the_process_end() -> None:
    """A run's last steps on an arm that has stopped answering: its call runs out of time, the stop
    and the close say they could not, and `asyncio.run` and the exit have nothing to wait for. On
    Windows a Ctrl-C did nothing to the join, so only closing the window ended it."""
    done = _ended(
        """
        import asyncio, sys, threading
        from tests.test_lerobot_adapter import FakeArm
        from quackd_lerobot.real import LeRobotReal

        never = threading.Event()

        class Wedged(FakeArm):
            def get_observation(self):
                never.wait()

        async def main():
            transport = LeRobotReal("COM99", robot=Wedged(), timeout_s=0.05)
            try:
                await transport._call(transport._robot.get_observation)
            except TimeoutError as e:
                print("timed out", flush=True)
            await transport.stop()
            print(transport.stop_error, flush=True)
            await transport.close()
            print("closed", flush=True)

        asyncio.run(main())
        print("the verdict goes here", flush=True)
        sys.exit(7)
        """
    )
    assert done.returncode == 7, done.stderr
    lines = done.stdout.split("\n")
    assert lines[0] == "timed out"
    assert "has not come back" in lines[1]
    assert lines[2:4] == ["closed", "the verdict goes here"]


async def test_the_bus_is_on_threads_no_exit_waits_for() -> None:
    transport = LeRobotReal("COM99", robot=FakeArm())
    assert await transport._call(lambda: threading.current_thread().daemon) is True


class _Camera:
    def __init__(self) -> None:
        self.daemon: list[bool] = []

    def _note(self) -> None:
        self.daemon.append(threading.current_thread().daemon)

    def connect(self) -> None:
        self._note()

    def disconnect(self) -> None:
        self._note()

    def read_latest(self) -> Any:
        import numpy as np

        self._note()
        return np.zeros((4, 4, 3), dtype=np.uint8)


async def test_the_cameras_are_on_threads_no_exit_waits_for() -> None:
    """One that hangs is abandoned with a word said, and it held the process all the same."""
    transport = LeRobotReal("COM99", robot=FakeArm())
    camera = _Camera()
    transport._cameras["front"] = camera
    transport.camera_keys = ("front",)
    await transport._camera_call(camera.connect, timeout_s=5.0)
    assert await transport._read_frame("front") is not None
    assert await transport._loop_frames({}) is None
    assert camera.daemon == [True, True, True]


async def test_a_policy_keeps_a_worker_the_exit_waits_for() -> None:
    """Its close goes out on that worker with nothing waiting for it, and that is what tells the
    server the session is over: cut off, the next client's reset is refused for the lease."""
    loop = PolicyLoop.__new__(PolicyLoop)
    loop._executor = None
    thread = await asyncio.wrap_future(loop._submit(threading.current_thread))
    assert not thread.daemon


def _disconnects_slowly(seconds: float, *, raises: bool = False) -> FakeArm:
    class Slow(FakeArm):
        def disconnect(self) -> None:
            time.sleep(seconds)
            if raises:
                raise OSError("the bus lost it")
            super().disconnect()

    return Slow()


async def _closed_at_rest(arm: FakeArm) -> LeRobotReal:
    transport = LeRobotReal("COM99", robot=arm, rest_pose=dict(FOLDED))
    await transport.connect()
    result = await transport.go_to_rest()
    assert result.reached, result.reason
    await transport.close()
    return transport


async def test_a_disconnect_that_takes_torque_off_is_waited_for() -> None:
    """Its thread is a daemon, and the exit cut off one that ran past the close's old 5 s, with some
    motors let go and some not, a success on the panel and nothing said."""
    arm = _disconnects_slowly(0.3)
    transport = await _closed_at_rest(arm)
    assert arm.torque is False
    assert transport.close_note is None


async def test_a_disconnect_that_does_not_come_back_is_said_of(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("quackd_lerobot.real.DISCONNECT_S", 0.05)
    transport = await _closed_at_rest(_disconnects_slowly(0.5))
    assert transport.close_note == TORQUE_RELEASE_NOT_KNOWN


async def test_a_second_close_says_nothing_of_a_port_it_finds_shut() -> None:
    """The first let go of the arm, and a disconnect that raises because it did is no failed one."""

    class Upstream(FakeArm):
        def disconnect(self) -> None:
            if not self.connected:  # `check_if_not_connected`
                raise ConnectionError("SO101Follower is not connected")
            super().disconnect()

    arm = Upstream()
    transport = await _closed_at_rest(arm)
    assert arm.torque is False
    await transport.close()
    assert transport.close_note != TORQUE_RELEASE_NOT_KNOWN


async def test_a_disconnect_that_raises_is_said_of() -> None:
    transport = await _closed_at_rest(_disconnects_slowly(0.0, raises=True))
    assert transport.close_note == TORQUE_RELEASE_NOT_KNOWN
