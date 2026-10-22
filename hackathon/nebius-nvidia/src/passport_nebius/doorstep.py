"""The doorstep: a courier robot and a home robot that have never met, each with its own owner,
registry and Nemotron pilot, meet by Robot Passport and settle what happens to two parcels.

Every step is quackd 0.23 as released; this module only sets the scene and starts the runs.

* `stranger`: nobody pinned anybody. Each robot is a stranger to the other, so quackd shows
  each pilot the other's passport in closed words and numbers only and withholds what the other
  says. No handoff can be agreed, which is the point: a robot's words reach another robot's
  model only after a person decided to trust it.
* `appointment`: each side pinned the other first, as a delivery booking would record both
  robots, so each is `known` to the other, their words reach each other's pilots, and they can
  agree who carries what.

One machine, one or two terminals: the courier serves its passport and talk inbox on 8790, and
the home robot serves its own on 8791 and says hello to the courier as it starts. quackd sends a
reply only to a talk channel on the host a passport came from, and a run names the address its
machine routes to the LAN by, so the home robot meets the courier on that address.
"""

from __future__ import annotations

import os
import subprocess
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from .nebius import Model, NebiusError, Settings
from .pilot import QUACKD, Launch, run_launch, start, wait

ACTS = ("stranger", "appointment")
ROLES = ("both", "courier", "home")
SCENARIOS = Path(__file__).resolve().parents[2] / "scenarios"
SERVE_WITHIN_S = 90.0


@dataclass(frozen=True)
class Robot:
    """One side of the doorstep, and what its owner says about it in its passport."""

    name: str
    spec: str
    owner: str
    estop: str
    how_to_stop: str
    duck: str
    port: int


#: AlohaMini, a mobile robot with two arms on a lift, as the courier; ToddlerBot, a 56 cm
#: humanoid, as the home's robot. Both run on quackd's 2D simulator, and both publish a payload
#: with a source, which is what the two pilots compare. The stop lines say what is true of a
#: simulated robot, since a passport's safety lines are for a person standing next to it.
COURIER = Robot(
    name="courier-01",
    spec="alohamini:sim2d",
    owner="Courier company (demo)",
    estop="none",
    how_to_stop="Simulated: press Ctrl+C in the terminal running it.",
    duck="courier.duck",
    port=8790,
)
HOME = Robot(
    name="home-01",
    spec="toddlerbot:sim2d",
    owner="Household (demo)",
    estop="none",
    how_to_stop="Simulated: press Ctrl+C in the terminal running it.",
    duck="home.duck",
    port=8791,
)


def registry(state: Path, robot: Robot) -> Path:
    return state / f"{robot.name}-registry"


def passport_file(state: Path, robot: Robot) -> Path:
    return state / f"{robot.name}.jws"


def scenario(robot: Robot) -> Path:
    path = SCENARIOS / robot.duck
    if not path.is_file():
        raise NebiusError(f"{path} is missing: run passport-nebius from its folder's checkout")
    return path


def quackd(*args: str) -> subprocess.CompletedProcess[str]:
    """One quackd command, its output kept. Nothing here needs a model, so no key is passed."""
    return subprocess.run(
        [*QUACKD, *args],
        env={**os.environ, "PYTHONIOENCODING": "utf-8", "NO_COLOR": "1"},
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


#: What frames quackd's error panels, which is not the error.
_BOX = "│┃┌┐└┘├┤─━ \t"


def last_words(text: str) -> str:
    """The last line quackd said in words: its error panel's message rather than the border
    under it, and never the usage line above it."""
    for line in reversed(text.strip().splitlines()):
        words = line.strip(_BOX)
        if words and not words.startswith(("Usage:", "Try '", "Error")):
            return words
    return ""


def outcome(result: subprocess.CompletedProcess[str]) -> str:
    """What a quackd command came to, in its own last words: an answer on stdout when it
    succeeded, an error panel on stderr when it did not."""
    first, then = (
        (result.stdout, result.stderr)
        if result.returncode == 0
        else (
            result.stderr,
            result.stdout,
        )
    )
    return last_words(first) or last_words(then) or f"exit {result.returncode}"


def _must(result: subprocess.CompletedProcess[str], what: str) -> None:
    if result.returncode != 0:
        raise NebiusError(f"{what} failed: {outcome(result)}")


def _pinned(where: str) -> set[str]:
    from quackd.passport_store import PassportStore

    return {name for pin in PassportStore(where).known() for name in (pin.name, pin.id)}


def setup(state: Path, act: str) -> list[str]:
    """Both robots registered, owned and with their stop lines said, then pinned to each other
    for `appointment` or unpinned for `stranger`. Safe to repeat. Returns what it changed."""
    from quackd.registry import Registry

    if act not in ACTS:
        raise NebiusError(f"--act is one of {', '.join(ACTS)}")
    done: list[str] = []
    state.mkdir(parents=True, exist_ok=True)
    for robot in (COURIER, HOME):
        where = str(registry(state, robot))
        if Registry(where).get_robot(robot.name) is None:
            _must(
                quackd("robot", "add", robot.name, robot.spec, "--registry-dir", where),
                f"registering {robot.name}",
            )
            done.append(f"registered {robot.name} as {robot.spec}")
        _must(
            quackd("passport", "owner", "--label", robot.owner, "--registry-dir", where),
            f"naming {robot.name}'s owner",
        )
        _must(
            quackd(
                "passport",
                "set",
                "--robot",
                robot.name,
                "--estop",
                robot.estop,
                "--how-to-stop",
                robot.how_to_stop,
                "--registry-dir",
                where,
            ),
            f"saying how to stop {robot.name}",
        )
        _must(
            quackd(
                "passport",
                "show",
                "--robot",
                robot.name,
                "--out",
                str(passport_file(state, robot)),
                "--registry-dir",
                where,
            ),
            f"writing {robot.name}'s signed passport",
        )
    for robot, other in ((HOME, COURIER), (COURIER, HOME)):
        where = str(registry(state, robot))
        known = other.name in _pinned(where)
        if act == "appointment" and not known:
            _must(
                quackd(
                    "meet",
                    str(passport_file(state, other)),
                    "--pin",
                    "--yes",
                    "--registry-dir",
                    where,
                ),
                f"{robot.name} pinning {other.name}",
            )
            done.append(f"{robot.name} pinned {other.name}")
        elif act == "stranger" and known:
            _must(
                quackd("passport", "forget", other.name, "--registry-dir", where),
                f"{robot.name} forgetting {other.name}",
            )
            done.append(f"{robot.name} forgot {other.name}")
    return done


def is_set_up(state: Path) -> bool:
    from quackd.registry import Registry

    return all(
        Registry(str(registry(state, robot))).get_robot(robot.name) is not None
        for robot in (COURIER, HOME)
    )


def run_args(robot: Robot, state: Path, act: str, *, meet: str | None) -> list[str]:
    """`quackd run`'s arguments for one side, everything but the pilot."""
    args = [
        str(scenario(robot)),
        "--robot",
        robot.name,
        "--registry-dir",
        str(registry(state, robot)),
        "--talk",
        "--talk-port",
        str(robot.port),
        "--runs-dir",
        str(state / "runs"),
        "--run-name",
        f"{robot.name}-{act}",
        "--no-memory",
    ]
    if meet is not None:
        args += ["--meet", meet]
    return args


def launches(
    settings: Settings,
    model: Model,
    state: Path,
    act: str,
    role: str,
    *,
    courier_at: str,
    vision: bool = False,
) -> list[tuple[Robot, Launch]]:
    """The runs `role` starts, in the order they start: the courier first, since the home
    robot says hello to it as it begins."""
    if role not in ROLES:
        raise NebiusError(f"--role is one of {', '.join(ROLES)}")
    if act not in ACTS:
        raise NebiusError(f"--act is one of {', '.join(ACTS)}")
    chosen: list[tuple[Robot, Launch]] = []
    if role in ("both", "courier"):
        args = run_args(COURIER, state, act, meet=None)
        chosen.append((COURIER, run_launch(settings, model, args, vision=vision)))
    if role in ("both", "home"):
        args = run_args(HOME, state, act, meet=courier_at)
        chosen.append((HOME, run_launch(settings, model, args, vision=vision)))
    return chosen


def served(
    address: str,
    *,
    within: float = SERVE_WITHIN_S,
    alive: subprocess.Popen[bytes] | None = None,
) -> None:
    """Wait until a passport answers at `address` (HOST:PORT), or say why it never did."""
    from quackd.passport.errors import PassportError
    from quackd.passport.fetch import fetch_passport, target

    url = target(f"http://{address}").url
    deadline = time.monotonic() + within
    while time.monotonic() < deadline:
        if alive is not None and alive.poll() is not None:
            raise NebiusError(
                f"the courier's run ended ({alive.returncode}) before it served its passport"
            )
        try:
            fetch_passport(url, timeout=2.0)
            return
        except (PassportError, OSError):
            time.sleep(0.5)
    raise NebiusError(f"no passport answered at {address} within {within:.0f} s")


def pause(state: Path, home_at: str, seconds: int) -> subprocess.CompletedProcess[str]:
    """The courier asks the home robot for a pause, as itself: a hello signed by the courier's
    own key, then the ask. The home robot's owner policy decides, by fixed code, and a pause
    brakes the body at once. A peer can never make a robot stop outright."""
    return quackd(
        "meet",
        home_at,
        "--as",
        COURIER.name,
        "--ask",
        f"pause:{seconds}",
        "--registry-dir",
        str(registry(state, COURIER)),
    )


def play(
    started: Sequence[tuple[Robot, Launch]],
    *,
    courier_at: str,
    logs: Path | None,
    on_home_started: Callable[[], None] | None = None,
) -> dict[str, int]:
    """Start the runs, the home robot only once the courier's passport is served, and wait for
    every one of them. With `logs`, each run's output goes to a file there, since two runs
    writing to one terminal cannot be read."""
    processes: list[tuple[Robot, subprocess.Popen[bytes]]] = []
    files = []
    try:
        for robot, launch in started:
            if robot is HOME:
                courier = processes[0][1] if processes else None
                served(courier_at, alive=courier)
            if logs is not None:
                logs.mkdir(parents=True, exist_ok=True)
                f = (logs / f"{robot.name}.log").open("wb")
                files.append(f)
                processes.append((robot, start(launch, stdout=f, stderr=subprocess.STDOUT)))
            else:
                processes.append((robot, start(launch)))
            if robot is HOME and on_home_started is not None:
                on_home_started()
        return {robot.name: wait(process) for robot, process in processes}
    finally:
        for f in files:
            f.close()
