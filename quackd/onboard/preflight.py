"""`quackd onboard preflight`: what this computer has for a robot's onboard side, check by check.

Each check reads, and none changes anything: the board, its radio and the Bluetooth libraries,
the LAN, the clock, the robot's file, keys and passport, its brain, and whether the service is
running. A check is `ok`, `warn` (it runs, with less) or `fail` (it does not run until this is
fixed), with the sentence that says why and, where there is one, what to do.
"""

from __future__ import annotations

import importlib.util
import platform
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from quackd.onboard.store import OnboardDir

State = Literal["ok", "warn", "fail"]
#: A clock before this year cannot be right: every passport quackd signs is later.
EARLIEST_YEAR = 2026


@dataclass(frozen=True)
class Check:
    name: str
    state: State
    detail: str
    hint: str | None = None

    def row(self) -> dict[str, Any]:
        out: dict[str, Any] = {"check": self.name, "state": self.state, "detail": self.detail}
        if self.hint:
            out["hint"] = self.hint
        return out


def preflight(
    home: OnboardDir,
    board: str | None,
    *,
    now: datetime | None = None,
    service: Callable[[], dict[str, Any] | None] | None = None,
) -> list[Check]:
    """Every check, in the order a person fixes them. `service` asks the running service for
    its status, None when none answers."""
    checks: list[Check] = []
    facts, radio_kind, radio = _board(board, checks)
    if radio_kind == "bluez":
        checks.append(_libraries())
    checks.append(_lan())
    checks.append(_clock(now or datetime.now(UTC)))
    file = _file(home, checks)
    if file is not None:
        _keys_and_passport(home, file, facts, checks)
        checks.append(_brain(home, file))
    if radio is not None:
        checks.insert(1, radio)
    running = service() if service is not None else None
    start = (
        "sudo systemctl start quackd-onboard"
        if radio_kind == "bluez"
        else f"quackd onboard serve{' --board sim' if radio_kind == 'sim' else ''}"
    )
    checks.append(
        Check("service", "ok", f"running, pid {running.get('pid')}")
        if running
        else Check("service", "warn", "not running here", start)
    )
    return checks


def _board(name: str | None, checks: list[Check]) -> tuple[Any, str | None, Check | None]:
    from quackd.boards import NotARobot, UnknownBoard, Unsupported, detect

    try:
        chosen, facts = detect(name)
    except (NotARobot, Unsupported, UnknownBoard) as e:
        checks.append(Check("board", "fail", str(e)))
        return None, None, None
    release = f", {facts.release}" if facts.release else ""
    checks.append(Check("board", "ok", f"{facts.model} ({facts.kind}{release})"))
    found = chosen.radio()
    if found.ok:
        adapter = found.adapter if found.adapter not in (None, chosen.radio_kind) else None
        radio = Check("radio", "ok", chosen.radio_kind + (f", {adapter}" if adapter else ""))
    else:
        radio = Check(
            "radio",
            "warn",
            found.why or "no Bluetooth adapter",
            "robots are met over the LAN only until it has one",
        )
    return facts, chosen.radio_kind, radio


def _libraries() -> Check:
    from quackd.radio import BLUEZ_PACKAGES, ONBOARD_INSTALL

    if platform.system() != "Linux":
        return Check("bluetooth", "fail", "BlueZ runs on Linux, and this is not Linux")
    missing = [name for name in BLUEZ_PACKAGES if importlib.util.find_spec(name) is None]
    if missing:
        return Check("bluetooth", "fail", f"{' and '.join(missing)} missing", ONBOARD_INSTALL)
    return Check("bluetooth", "ok", "bleak and dbus-fast installed")


def _lan() -> Check:
    if importlib.util.find_spec("zeroconf") is None:
        return Check(
            "lan",
            "warn",
            "zeroconf missing, so no LAN record: robots are met over the link only",
            'uv tool install "quackd[onboard]"',
        )
    from quackd.lan.announce import local_ip

    address = local_ip()
    if address.startswith("127."):
        return Check("lan", "warn", "no network, so robots are met over the link only")
    return Check("lan", "ok", f"on a network as {address}")


def _clock(now: datetime) -> Check:
    if now.year < EARLIEST_YEAR:
        return Check(
            "clock",
            "fail",
            f"it says {now:%Y-%m-%d}, before any passport quackd signs",
            "set the time: timedatectl set-ntp true",
        )
    return Check("clock", "ok", f"{now:%Y-%m-%d %H:%M} UTC")


def _file(home: OnboardDir, checks: list[Check]) -> Any:
    from quackd.onboard import config

    if not home.robot_yaml.exists():
        checks.append(Check("robot.yaml", "fail", f"none in {home.root}", "run quackd onboard"))
        return None
    try:
        file = config.load(home.robot_yaml)
    except config.FileError as e:
        checks.append(Check("robot.yaml", "fail", str(e)))
        return None
    checks.append(Check("robot.yaml", "ok", f"{file.passport.name}, in {home.root}"))
    return file


def _keys_and_passport(home: OnboardDir, file: Any, facts: Any, checks: list[Check]) -> None:
    from quackd.onboard import wizard
    from quackd.onboard.keys import KeysError, read_robot_key
    from quackd.onboard.service import check_board, check_own_passport

    try:
        robot = read_robot_key(home.robot_key)
    except (KeysError, OSError) as e:
        checks.append(Check("keys", "fail", f"no robot key: {e}", "run quackd onboard"))
        return
    owner = "and the owner key" if home.owner_key.exists() else "and no owner key"
    checks.append(
        Check("keys", "ok" if home.owner_key.exists() else "warn", f"the robot key {owner}")
    )
    token = (
        home.passport_jws.read_text(encoding="utf-8").strip()
        if home.passport_jws.exists()
        else None
    )
    check = check_own_passport(token, robot.did)
    if facts is not None:
        check = check_board(check, facts)
    if not check.ok or check.reading is None:
        checks.append(Check("passport", "fail", check.why, "quackd onboard sign"))
        return
    expires = check.reading.document["expires"]
    checks.append(Check("passport", "ok", f"signed by its owner, until {expires}"))
    if facts is not None:
        why = wizard.needs_signing(home, file, facts.passport())
        if why is not None:
            checks.append(Check("signed", "warn", why, "quackd onboard sign"))


def _brain(home: OnboardDir, file: Any) -> Check:
    llm = file.brain.llm
    if not llm:
        return Check("brain", "warn", "none, so robots are met and nobody talks", "brain.llm")
    where = f" at {file.brain.base_url}" if file.brain.base_url else ""
    variable = key_variable(llm)
    if variable is not None and not _env_has(home, variable):
        return Check(
            "brain",
            "fail",
            f"{llm}{where}, and robot.env holds no {variable}",
            f"put {variable}=... in {home.robot_env}, readable by this user alone",
        )
    return Check("brain", "ok", f"{llm}{where}")


def key_variable(llm: str) -> str | None:
    """Which environment variable a brain's key goes in, or None for a brain that needs none."""
    from quackd.agent.providers.factory import KEY_ENV

    vendor = llm.split(":", 1)[0].strip().lower()
    if vendor == "fake":
        return None
    return KEY_ENV.get(vendor) or "LOCAL_API_KEY"


def _env_has(home: OnboardDir, variable: str) -> bool:
    import os

    if os.environ.get(variable):
        return True
    try:
        lines = home.robot_env.read_text(encoding="utf-8").splitlines()
    except OSError:
        return False
    return any(line.split("=", 1)[0].strip() == variable for line in lines if "=" in line)


__all__ = ["EARLIEST_YEAR", "Check", "key_variable", "preflight"]
