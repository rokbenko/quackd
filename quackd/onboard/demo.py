"""`quackd onboard demo`: two simulated robots on this computer meet, check each other's
passports, and their brains talk.

The two robots are the files in `quackd/onboard/demo/`: a mock Unitree G1 EDU as a courier and a
mock Agile ONE SN as a household's robot, each with the figures its maker published. Each runs as
its own `quackd onboard serve --board sim` process, as it would on its own computer, and the sim
radio puts them in one room: the household's robot stands inside the door and the courier walks
up to it, so the signal rises until the radio says near, and the hello follows. The acts:

- `strangers`: neither owner pinned the other, and they share no network, so they meet over the
  Bluetooth link; each checks the other's passport, each is a stranger to the other, and each
  brain is told only the other's closed facts while its sentences go to the log.
- `appointment`: the household pinned the courier company, as it would when booking a delivery,
  and the courier pinned the household, so their words reach each other's brain.
- `office`: the same two on one network, so the hello goes over the LAN instead.
- `tampered`: the courier comes back with one byte of its passport changed after its owner
  signed it; the household's robot refuses it and nothing of it reaches a brain. A robot's own
  service would never serve such a passport, so this courier is played by the demo itself, with
  the service's parts and without that check, as anybody's own code could be.

Every robot's records stay in the workspace (`--keep DIR`), the transcripts among them, and the
demo ends with what each act expected beside what was observed: exit 0 only when every one
matched. `--offline` flies both robots on the scripted pilot, which needs no key and no network,
and is what CI runs.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any

COURIER = "g1-edu-mock"
HOUSEHOLD = "agile-one-sn-mock"
ROLES = {COURIER: "courier", HOUSEHOLD: "household"}
ACTS = ("strangers", "appointment", "office", "tampered")
#: Where each robot is in the room, metres at seconds into an act: the household stands inside
#: the door, and the courier walks up from seven metres to one and stays.
STANDS = "0,0@0"
WALKS = "7,0@0 1,0@8 1,0@3600"
#: How long an act may take: a scripted pilot's meetings are over in seconds, and a model's
#: twelve steps each way can take minutes.
ACT_S = {"offline": 60.0, "live": 300.0}
#: Scene seconds a wall-clock second, with --fast.
FAST = 4.0
#: A passphrase for the demo's own owner keys, which are thrown away with the workspace.
DEMO_PASSPHRASE = "a demo key nobody keeps"
#: The lines of a robot's log the demo tells as they happen.
TOLD = (" is near", " met", " said:", "refused", "meeting starts", " ended", " link ", " LAN")


@dataclass
class Robot:
    name: str
    home: Path
    process: subprocess.Popen[bytes] | None = None
    log: Path | None = None
    read_to: int = 0


@dataclass
class Act:
    name: str
    expected: str
    observed: str = ""
    ok: bool = False
    seconds: float = 0.0
    events: list[str] = field(default_factory=list)

    def row(self) -> dict[str, Any]:
        return {
            "act": self.name,
            "expected": self.expected,
            "observed": self.observed,
            "ok": self.ok,
            "seconds": round(self.seconds, 1),
        }


EXPECTED = {
    "strangers": "they meet over the link as strangers, and neither brain sees the other's name",
    "appointment": "they meet as known robots, and each brain hears the other by name",
    "office": "they share a network, so the hello goes over the LAN",
    "tampered": "the household refuses the changed passport under rule 4.4, and meets nothing",
}


# ── the robots ──────────────────────────────────────────────────────────────────────────


def demo_file(name: str) -> str:
    return (resources.files("quackd.onboard") / "demo" / f"{name}.yaml").read_text(encoding="utf-8")


def _set_brain(text: str, llm: str, base_url: str | None) -> str:
    """The demo file with the brain the demo was run with, written where the file leaves room."""
    lines = [f"brain:\n  llm: {json.dumps(llm)}\n"]
    if base_url:
        lines.append(f"  base_url: {json.dumps(base_url)}\n")
    return text.replace("\nmeeting:\n", "\n" + "".join(lines) + "\nmeeting:\n", 1)


def prepare(
    workspace: Path, *, llm: str, base_url: str | None, key_env: str | None
) -> dict[str, Robot]:
    """Both robots' directories: robot.yaml with the demo's brain, keys, and a signed passport
    saying `sim`. The key for a brain that needs one is copied from this process's environment
    into each robot.env, never onto a command line."""
    from quackd.boards import named
    from quackd.onboard import config, wizard
    from quackd.onboard.store import OnboardDir

    sim = named("sim").detect()
    assert sim is not None
    passphrase = workspace / "passphrase.txt"
    passphrase.write_text(DEMO_PASSPHRASE + "\n", encoding="utf-8")
    with contextlib.suppress(OSError):
        os.chmod(passphrase, 0o600)
    robots: dict[str, Robot] = {}
    for name in (COURIER, HOUSEHOLD):
        home = OnboardDir(workspace / name)
        home.ensure()
        text = _set_brain(demo_file(name), llm, base_url)
        home.robot_yaml.write_text(text, encoding="utf-8")
        if key_env and os.environ.get(key_env):
            wizard.write_key_file(home.robot_env, key_env, os.environ[key_env])
        wizard.sign(
            home,
            config.load(home.robot_yaml),
            sim.passport(),
            ask=_NoQuestions(),
            passphrase_file=passphrase,
            days=1,
        )
        robots[name] = Robot(name, home.root)
    return robots


class _NoQuestions:
    """The demo asks nothing: its passphrase comes from its own file."""

    def text(self, question: str, default: str | None = None) -> str:
        return default or ""

    def secret(self, question: str, *, twice: bool = False) -> str:
        raise RuntimeError("the demo reads its passphrase from a file")

    def yes(self, question: str, default: bool = False) -> bool:
        return default


def pin_each_other(robots: dict[str, Robot]) -> None:
    """Each robot pins the other's owner, as two owners who agreed a delivery would."""
    from datetime import date

    from quackd.onboard import config
    from quackd.passport import document

    owners = {}
    for name, robot in robots.items():
        token = (robot.home / "passport.jws").read_text(encoding="utf-8").strip()
        reading = document.read_jws(token)
        owners[name] = (reading.owner_id, reading.document["owner"].get("label", name))
    for name, robot in robots.items():
        other = COURIER if name == HOUSEHOLD else HOUSEHOLD
        owner_id, label = owners[other]
        pin = config.Pin(
            kind="owner", id=owner_id, name=str(label)[:64], pinned=date.today().isoformat()
        )
        with contextlib.suppress(config.FileError):
            config.add_pin(robot.home / "robot.yaml", pin)


def tampered(robot: Robot) -> str:
    """The robot's passport with one byte of its name changed after its owner signed it, and
    the signature left as it was."""
    from quackd.passport import jws, strict

    head, body, sig = (robot.home / "passport.jws").read_text(encoding="utf-8").strip().split(".")
    doc = strict.loads(jws.b64url_decode(body, rule="4.4"), limit=1 << 16)
    doc["name"] = str(doc["name"]).replace("mock", "mocK")
    return f"{head}.{jws.b64url(strict.dumps(doc))}.{sig}"


# ── one act ─────────────────────────────────────────────────────────────────────────────


def start(
    robot: Robot,
    *,
    air: Path,
    path: str,
    network: str,
    quackd: Sequence[str],
    extra: Sequence[str],
    log: Path,
) -> None:
    robot.log, robot.read_to = log, 0
    out = log.open("ab")
    command = [
        *quackd,
        "onboard",
        "serve",
        "--board",
        "sim",
        "--dir",
        str(robot.home),
        "--air",
        str(air),
        "--path",
        path,
        "--network",
        network,
        *extra,
    ]
    try:
        robot.process = subprocess.Popen(command, stdout=out, stderr=subprocess.STDOUT)
    finally:
        out.close()  # the child holds its own handle


def stop(robot: Robot) -> None:
    from quackd.onboard.control import ControlError, request

    process = robot.process
    if process is None:
        return
    with contextlib.suppress(ControlError, OSError):
        request(robot.home / "control.json", {"op": "shutdown"}, timeout=3.0)
    try:
        process.wait(timeout=20)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)
    robot.process = None


def met(robot: Robot) -> list[dict[str, Any]]:
    rows = []
    try:
        lines = (robot.home / "met.jsonl").read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for line in lines:
        with contextlib.suppress(ValueError):
            rows.append(json.loads(line))
    return rows


def runs(robot: Robot) -> set[Path]:
    return set((robot.home / "runs").glob("*/summary.json"))


def pilot_saw(robot: Robot, since: set[Path]) -> str:
    """What the robot's pilot was shown in the runs that ended since `since`: its observations,
    which are the part of a record a model reads."""
    seen = []
    for summary in sorted(runs(robot) - since):
        transcript = summary.parent / "transcript.jsonl"
        with contextlib.suppress(OSError):
            for line in transcript.read_text(encoding="utf-8", errors="replace").splitlines():
                with contextlib.suppress(ValueError):
                    row = json.loads(line)
                    if row.get("kind") == "observation":
                        seen.append(str(row.get("text", "")))
    return "\n".join(seen)


def new_lines(robot: Robot) -> list[str]:
    """What the robot's service logged since this was last asked."""
    if robot.log is None:
        return []
    try:
        with robot.log.open("rb") as f:
            f.seek(robot.read_to)
            data = f.read()
    except OSError:
        return []
    end = data.rfind(b"\n") + 1
    robot.read_to += end
    return data[:end].decode("utf-8", errors="replace").splitlines()


@contextlib.contextmanager
def _intruder(courier: Robot, air: Path, speed: float) -> Iterator[Any]:
    """The courier with its tampered passport, walking up: the service's radio half and a
    Talker, with no check of its own passport, ticking on a thread of this process."""
    from quackd.onboard.keys import read_robot_key
    from quackd.onboard.radio_side import proximity_policy, sim_side
    from quackd.passport.keys import short_id_of
    from quackd.passport_talk import Talker
    from quackd.passport_trust import Judgement

    key = read_robot_key(courier.home / "keys" / "robot.json")
    side = sim_side(
        air=air,
        path=WALKS,
        network="none",
        speed=speed,
        policy=proximity_policy(meet_within_m=2.0, rssi_at_1m=-59.0, speed=speed),
    )
    talker = Talker(
        COURIER,
        key,
        tampered(courier),
        judge=lambda _reading: Judgement("stranger", "the demo's intruder trusts nobody", ()),
        send=side.send,
        heard=side.heard,
        on_session=side.on_session,
        runtime={"software": "quackd demo"},
    )
    side.start(talker, short_id=short_id_of(key.did) or "0" * 16, digest="0" * 64, http_port=0)
    done = threading.Event()

    def tick() -> None:
        while not done.wait(0.25):
            with contextlib.suppress(Exception):
                side.tick()

    thread = threading.Thread(target=tick, name="demo-intruder", daemon=True)
    thread.start()
    try:
        yield talker
    finally:
        done.set()
        thread.join(5)
        side.close()


def judge_act(
    act: str, robots: dict[str, Robot], before: dict[str, set[Path]], intruder: Any
) -> tuple[bool, str]:
    """Whether what was observed is what the act expects, and what was observed, in words."""
    household, courier = robots[HOUSEHOLD], robots[COURIER]
    if act == "tampered":
        refusals = [r for r in met(household) if "refused" in r]
        found = [r for r in met(household) if "id" in r]
        if not refusals:
            return False, "nothing refused yet"
        why = str(refusals[0].get("why", ""))
        ok = "4.4" in why and not found and not runs(household) - before[HOUSEHOLD]
        met_any = intruder is not None and bool(intruder.peers_open())
        return ok and not met_any, f"refused: {why}"
    seen = {name: [r for r in met(robot) if "id" in r] for name, robot in robots.items()}
    if not all(seen.values()):
        return False, "they have not met yet"
    ended = {name: bool(runs(robot) - before[name]) for name, robot in robots.items()}
    if not all(ended.values()):
        return False, "met, and a meeting is still running"
    h, c = seen[HOUSEHOLD][0], seen[COURIER][0]
    ways = {"ble": "the link", "http": "the LAN"}
    said = (
        f"met over {ways.get(str(h.get('via')), h.get('via'))}, the household's robot sees "
        f"{h.get('shown')} ({h.get('tier')}) and the courier sees {c.get('shown')} "
        f"({c.get('tier')})"
    )
    if act == "strangers":
        hidden = COURIER not in pilot_saw(household, before[HOUSEHOLD]) and HOUSEHOLD not in (
            pilot_saw(courier, before[COURIER])
        )
        ok = h["tier"] == c["tier"] == "stranger" and h["via"] == c["via"] == "ble" and hidden
        return ok, said + ("" if hidden else ", and a brain saw a stranger's own name")
    if act == "appointment":
        heard = COURIER in pilot_saw(household, before[HOUSEHOLD])
        ok = h["tier"] == c["tier"] == "known" and heard
        return ok, said + ("" if heard else ", and the household's brain never saw its name")
    return h["via"] == c["via"] == "http", said


def run_act(
    act: str,
    robots: dict[str, Robot],
    *,
    workspace: Path,
    speed: float,
    deadline_s: float,
    quackd: Sequence[str],
    tell: Callable[[str], None],
) -> Act:
    from quackd.radio.sim import write_scene

    result = Act(act, EXPECTED[act])
    air = workspace / "air" / act
    air.mkdir(parents=True, exist_ok=True)
    write_scene(air, t0=time.time(), speed=speed)
    before = {name: runs(robot) for name, robot in robots.items()}
    network = "home" if act == "office" else "none"
    started = time.monotonic()
    walking = [] if act == "tampered" else [COURIER]
    intruder: Any = None
    with contextlib.ExitStack() as stack:
        for name in (HOUSEHOLD, *walking):
            robot = robots[name]
            start(
                robot,
                air=air,
                path=STANDS if name == HOUSEHOLD else WALKS,
                network=network,
                quackd=quackd,
                extra=[],
                log=robot.home / f"serve-{act}.log",
            )
            stack.callback(stop, robot)
        if act == "tampered":
            intruder = stack.enter_context(_intruder(robots[COURIER], air, speed))
        while True:
            for name in (HOUSEHOLD, *walking):
                for line in new_lines(robots[name]):
                    if any(word in line for word in TOLD):
                        said = f"{ROLES[name]:>9} | {line}"
                        result.events.append(said)
                        tell(said)
            result.ok, result.observed = judge_act(act, robots, before, intruder)
            if result.ok or time.monotonic() - started > deadline_s:
                break
            time.sleep(0.5)
    result.seconds = time.monotonic() - started
    for robot in robots.values():
        with contextlib.suppress(OSError):
            os.replace(robot.home / "met.jsonl", robot.home / f"met-{act}.jsonl")
    return result


def run_demo(
    *,
    workspace: Path,
    llm: str,
    base_url: str | None,
    key_env: str | None,
    acts: Sequence[str],
    fast: bool,
    tell: Callable[[str], None],
    quackd: Sequence[str] | None = None,
) -> list[Act]:
    """Every act in `acts`, in order, the robots' files set again before each."""
    robots = prepare(workspace, llm=llm, base_url=base_url, key_env=key_env)
    plain = {
        name: (robot.home / "robot.yaml").read_text(encoding="utf-8")
        for name, robot in robots.items()
    }
    deadline = ACT_S["offline" if llm == "fake" else "live"]
    speed = FAST if fast else 1.0
    done = []
    for act in acts:
        for name, robot in robots.items():
            (robot.home / "robot.yaml").write_text(plain[name], encoding="utf-8")
        if act == "appointment":
            pin_each_other(robots)
        tell(f"act {act}: {EXPECTED[act]}")
        done.append(
            run_act(
                act,
                robots,
                workspace=workspace,
                speed=speed,
                deadline_s=deadline / (speed if fast else 1.0) + 30.0,
                quackd=quackd or quackd_command(),
                tell=tell,
            )
        )
    return done


def quackd_command() -> list[str]:
    """How a child quackd starts: this interpreter, isolated from the working directory."""
    return [sys.executable, "-P", "-m", "quackd"]


def workspace_for(keep: Path | None) -> Path:
    if keep is not None:
        keep.mkdir(parents=True, exist_ok=True)
        return keep
    return Path(tempfile.mkdtemp(prefix="quackd-onboard-demo-"))


def clean(workspace: Path, keep: Path | None) -> None:
    if keep is None:
        shutil.rmtree(workspace, ignore_errors=True)


__all__ = [
    "ACTS",
    "COURIER",
    "EXPECTED",
    "HOUSEHOLD",
    "Act",
    "Robot",
    "clean",
    "prepare",
    "run_demo",
    "workspace_for",
]
