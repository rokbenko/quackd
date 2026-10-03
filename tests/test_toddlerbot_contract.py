"""The contract job: quackd's real client against quackd's real daemon on a real physics body.

This is the only test in the repository that needs upstream installed, so it is opt-in and
skipped everywhere else. The main suite must stay installable on Windows with nothing but
quackd's own dependencies, which is why this cannot simply be another loopback test.

What it adds over `test_toddlerbot_daemon.py` is a body that pushes back. The fake body holds
exactly what it was told, so it can prove the protocol and the safety machinery and nothing
about physics. Here the daemon drives upstream's own MuJoCo model, through upstream's own
`MuJoCoSim`, and the pose it commands is not the pose it reads back.

Run it with:

    QUACKD_TODDLERBOT_CONTRACT=1 TODDLERBOT_ROOT=~/toddlerbot uv run pytest \
        tests/test_toddlerbot_contract.py

It still proves nothing about 3 kg of servos, and `docs/adapters/status.md` says so.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import socket
import subprocess
import sys
import threading

import pytest

from quackd.transport.base import Intent
from quackd_toddlerbot import ToddlerBotAdapter
from quackd_toddlerbot.bridge import ToddlerBotBridge
from tests.conftest import REPO

DAEMON = REPO / "bridge" / "toddlerbot" / "quackd_toddlerbot_bridge.py"
ROBOT = "toddlerbot_2xc"


def _daemon_constant(name: str) -> float:
    """One module-level float out of the daemon, read rather than imported.

    Importing it here would pull in upstream at test-collection time, and this module is
    collected on every machine, including the ones that skip. Reading the source keeps the
    number in one place without that: a deadline hard-coded here drifts away from the
    daemon's own arithmetic, which is exactly what made `stand` look stuck."""
    import ast

    tree = ast.parse(DAEMON.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            getattr(t, "id", None) == name for t in node.targets
        ):
            return float(ast.literal_eval(node.value))
    raise AssertionError(f"{DAEMON.name} no longer defines {name}")


#: The longest the daemon lets a slew run before calling the body stuck. A `stand` is done
#: when it arrives and not on a clock, so this is the only thing bounding the wait, and the
#: test used to bound it at 25 s instead. A slew is handed out one 50 Hz tick at a time and
#: each tick costs a bus round trip, which the daemon's own SETTLE_OVERHEAD puts at over six
#: times its arithmetic on a loaded runner: anything past about 1.25 rad of travel then takes
#: longer than 25 s, and the nightly job read that as a body that never finished standing.
SETTLE_CEILING_S = _daemon_constant("SETTLE_MAX_S")

pytestmark = pytest.mark.skipif(
    os.environ.get("QUACKD_TODDLERBOT_CONTRACT") != "1",
    reason="the contract job is opt-in: it needs upstream and its MuJoCo model installed",
)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class _Daemon:
    """The real daemon, as a real process, exactly as it runs on a robot."""

    def __init__(self) -> None:
        root = os.environ.get("TODDLERBOT_ROOT")
        assert root, "TODDLERBOT_ROOT must point at an upstream checkout"
        self.port = _free_port()
        self.output: list[str] = []
        self.proc = subprocess.Popen(
            [
                sys.executable,
                str(DAEMON),
                "--sim",
                "mujoco",
                "--robot",
                ROBOT,
                "--toddlerbot",
                os.path.abspath(os.path.expanduser(root)),
                "--host",
                "127.0.0.1",
                "--port",
                str(self.port),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        # Drain it. A full pipe buffer blocks the daemon's next log call, which would look
        # exactly like a hung control loop and be diagnosed as one.
        self._drain = threading.Thread(target=self._read, daemon=True, name="daemon-output")
        self._drain.start()

    def _read(self) -> None:
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            self.output.append(line.rstrip())

    def say_why(self) -> str:
        return "\n".join(self.output[-40:]) or "(the daemon printed nothing)"

    @property
    def address(self) -> str:
        return f"tcp://127.0.0.1:{self.port}"

    def __enter__(self) -> _Daemon:
        return self

    def __exit__(self, *exc: object) -> None:
        self.proc.terminate()
        try:
            self.proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            self.proc.kill()

    def alive(self) -> bool:
        return self.proc.poll() is None


async def _connect(daemon: _Daemon, tries: int = 60) -> ToddlerBotBridge:
    """MuJoCo compiles the model and loads 47 meshes before the socket opens."""
    last: Exception | None = None
    for _ in range(tries):
        assert daemon.alive(), daemon.proc.stdout.read() if daemon.proc.stdout else ""
        link = ToddlerBotBridge(address=daemon.address)
        try:
            await link.connect()
        except Exception as e:
            last = e
            await asyncio.sleep(1.0)
            continue
        return link
    raise AssertionError(f"the daemon never accepted a connection: {last}")


async def test_the_handshake_describes_the_simulated_body() -> None:
    with _Daemon() as daemon:
        link = await _connect(daemon)
        assert link.hello is not None
        assert link.robot_name == ROBOT
        assert link.motors == 30, "upstream's own model has thirty motors"
        assert link.neck_available
        # No checkpoint and no camera on a bare checkout, so neither is offered.
        assert not link.walk_available
        assert not link.camera_available
        await link.close()


async def test_the_loop_runs_at_fifty_hertz_against_real_physics() -> None:
    with _Daemon() as daemon:
        link = await _connect(daemon)
        # MuJoCo's first steps are slower than its steady state, and loop_hz is an average
        # over the run, so give it real ticks rather than waiting on nothing.
        seen = 0.0
        for _ in range(40):
            seen = float((await link.get_state()).extras["loop_hz"])
            if seen > 40.0:
                break
            await asyncio.sleep(0.25)
        assert seen > 40.0, f"the loop settled at {seen:.1f} Hz\n{daemon.say_why()}"
        state = await link.get_state()
        assert state.extras["calibrated"] is True
        # There is no odometry at this boundary even in simulation, because the adapter
        # reports what the real robot could report and no more.
        assert (state.x, state.y, state.theta) == (None, None, None)
        assert state.battery_percent is None
        await link.close()


async def test_stand_settles_on_a_body_that_pushes_back() -> None:
    """The fake body holds exactly what it is told. This one does not, so `stand` finishing
    means the slew actually moved thirty joints under gravity and the PD gains upstream
    ships, and then stopped moving them."""
    with _Daemon() as daemon:
        link = await _connect(daemon)
        before = dict((await link.get_state()).extras["joints"])
        assert (await link.send_intent(Intent.do("stand"))).accepted

        moved = False
        settled = False
        loop = asyncio.get_running_loop()
        deadline = loop.time() + SETTLE_CEILING_S
        while loop.time() < deadline:
            extras = (await link.get_state()).extras
            moved = moved or bool(extras["moving"])
            if moved and not extras["moving"]:
                settled = True
                break
            await asyncio.sleep(0.1)
        assert moved, f"stand never reported moving\n{daemon.say_why()}"
        assert settled, f"stand never finished\n{daemon.say_why()}"

        after = dict((await link.get_state()).extras["joints"])
        assert before.keys() == after.keys()
        changed = [k for k in before if abs(after[k] - before[k]) > 1e-3]
        assert changed, f"the slew commanded no joint at all\n{daemon.say_why()}"

        await link.stop()
        assert not (await link.get_state()).extras["deadman_tripped"]
        await link.close()


async def test_the_manifest_narrows_to_what_this_body_really_has() -> None:
    with _Daemon() as daemon:
        adapter = ToddlerBotAdapter(ToddlerBotBridge(address=daemon.address))
        for _ in range(60):
            assert daemon.alive(), daemon.say_why()
            try:
                manifest = await adapter.connect()
                break
            except Exception:
                await asyncio.sleep(1.0)
        else:
            raise AssertionError(f"the daemon never accepted a connection\n{daemon.say_why()}")
        # No walk checkpoint on a bare checkout, so locomotion does not exist here at all.
        assert manifest.mobility == "none"
        for verb in ("move", "go_to", "approach_and"):
            assert not manifest.provides(verb), verb
        assert manifest.provides("stand") and manifest.provides("report_state")
        await adapter.transport.close()


async def test_a_client_that_goes_quiet_trips_the_deadman_on_real_physics() -> None:
    """The failure the deadman exists for, against a body that will actually fall over if it
    is wrong. It must slew and hold, and it must never torque off."""
    with _Daemon() as daemon:
        link = await _connect(daemon)
        assert (await link.send_intent(Intent.do("stand"))).accepted

        # Going quiet means the client stops existing, not the client stopping talking: the
        # transport sends a keepalive on its own timer precisely so a long verb is not
        # cancelled underneath itself. Kill the keepalive and the socket, then watch.
        assert link._alive is not None
        link._alive.cancel()
        assert link._writer is not None
        link._writer.close()
        await asyncio.sleep(2.0)  # four times the daemon's 500 ms deadman

        # and the daemon is still alive, still looping, and holding rather than limp
        # Asked over a second connection, because the first one is the thing under test: its
        # socket was closed on purpose two statements ago, and `request` on it raises
        # `ConnectionResetError` from the writer's own drain. The point being made is about
        # the daemon rather than the link, so a live connection is the honest way to ask.
        watcher = ToddlerBotBridge(address=daemon.address)
        await watcher.connect()
        state = await watcher.get_state()
        assert state.extras["deadman_tripped"] is True, daemon.say_why()
        assert state.extras["loop_hz"] > 40.0, daemon.say_why()
        health = await watcher.request("bot.health")
        assert isinstance(health, dict)
        assert health.get("loop_hz", 0) > 40.0, "it is still running the loop, not stopped"
        assert daemon.alive(), "and the daemon is still alive rather than having exited"
        await watcher.close()

        # The killed link is left alone. Asking it anything is a race with its own socket:
        # `request` drains before it waits, so a writer the OS has already torn down raises
        # `ConnectionResetError` and one it has not yet noticed buffers the write and times
        # out three seconds later as a `TransportError`. Which of the two happens says
        # nothing about the daemon, and the daemon is the subject here.
        with contextlib.suppress(Exception):
            await link.close()


async def test_the_motions_the_workflow_fetched_actually_loaded() -> None:
    """The job spends five sparse-checkout patterns and two packages on the motion library.
    If upstream moves or renames a keyframe file, the daemon degrades honestly to no
    `perform` at all and nothing else would notice."""
    with _Daemon() as daemon:
        link = await _connect(daemon)
        assert link.motions, f"no keyframe motion loaded\n{daemon.say_why()}"
        assert set(link.motions) <= {"hold", "kneel", "cuddle", "push_up", "crawl"}

        # A second connect on the SAME bridge would open a second socket and orphan the first
        # reader, so the adapter gets its own link.
        adapter = ToddlerBotAdapter(ToddlerBotBridge(address=daemon.address))
        manifest = await adapter.connect()
        assert manifest.provides("perform")
        assert manifest.extras["motions"] == sorted(link.motions)
        await adapter.transport.close()
        await link.close()
