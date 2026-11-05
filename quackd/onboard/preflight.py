"""`quackd onboard preflight`: what this computer has for a robot's onboard side, check by check.

Each check reads, and none changes anything: the board; the radio's own rows from
`quackd.radio.preflight` (the packages, BlueZ, the adapter and its address, or the sim air, and
the LAN); the clock, and whether anything keeps it right; the robot's file, keys and passport; its
brain; and whether the service is running. `live` is the one exception, and only when asked for:
the radio's check that serves the advert and the GATT service for two seconds. A check is `ok`,
`warn` (it runs, with less) or `fail` (it does not run until this is fixed), with the sentence that
says why and, where there is one, what to do.
"""

from __future__ import annotations

import contextlib
import importlib.util
import platform
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from quackd.boards.linux import list_dir
from quackd.onboard.store import OnboardDir

State = Literal["ok", "warn", "fail"]
#: What installs everything a robot's onboard side may import.
ONBOARD_INSTALL = 'uv tool install "quackd[onboard]"'
#: A clock before this year cannot be right: every passport quackd signs is later.
EARLIEST_YEAR = 2026
#: What to do about a clock that is wrong, which signing again would only write down.
SET_THE_TIME = "set the time: timedatectl set-ntp true"
#: The clocks that survive a reboot, as the kernel lists them.
RTC = "/sys/class/rtc"


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
    live: bool = False,
    radio_rows: Callable[[str, str | None, bool], list[Check]] | None = None,
    clock_state: Callable[[], tuple[bool | None, bool | None]] | None = None,
) -> list[Check]:
    """Every check, in the order a person fixes them. `service` asks the running service for
    its status, None when none answers; `radio_rows` and `clock_state` stand in for the radio's
    own and for what this computer says of its clock, for tests."""
    checks: list[Check] = []
    facts, radio_kind, adapter = _board(home.board_named(board), checks)
    if radio_kind is not None:
        checks += (radio_rows or _radio_rows)(radio_kind, adapter, live)
    checks.append(_clock(now or datetime.now(UTC), *(clock_state or _clock_state)()))
    file = _file(home, checks)
    if file is not None:
        _keys_and_passport(home, file, facts, checks)
        checks.append(_brain(home, file))
    running = service() if service is not None else None
    # with no board, serve refuses as the board's row does, so that row says what to do; a
    # robot set up with --no-service has no unit, so the terminal is offered beside it
    start = (
        None
        if radio_kind is None
        else "sudo systemctl start quackd-onboard, or quackd onboard serve"
        if radio_kind == "bluez"
        else f"quackd onboard serve{' --board sim' if radio_kind == 'sim' else ''}"
    )
    checks.append(
        Check("service", "ok", f"running, pid {running.get('pid')}")
        if running
        else Check("service", "warn", "not running here", start)
    )
    return checks


def _board(name: str | None, checks: list[Check]) -> tuple[Any, str | None, str | None]:
    """This computer's facts, its radio's kind and the adapter to check, each None with no board."""
    from quackd.boards import RESERVED, NotARobot, UnknownBoard, Unsupported, detect, radio_of

    try:
        chosen, facts = detect(name)
    except (NotARobot, Unsupported, UnknownBoard) as e:
        checks.append(Check("board", "fail", str(e)))
        return None, None, None
    release = f", {facts.release}" if facts.release else ""
    checks.append(Check("board", "ok", f"{facts.model} ({facts.kind}{release})"))
    found = radio_of(chosen)
    if not found.ok and found.why and chosen.name not in RESERVED:
        # the radio's own rows cannot say why a plugin's board found no radio
        checks.append(Check("board radio", "warn", found.why))
    return facts, chosen.radio_kind, found.adapter_to_open


def _radio_rows(radio_kind: str, adapter: str | None, live: bool) -> list[Check]:
    """The radio's half, as `quackd.radio.preflight` checks it, in this module's terms."""
    import asyncio

    from quackd.lan.announce import local_ip
    from quackd.radio.preflight import collect, rows
    from quackd.radio.sim import default_air_dir

    kind: Any = radio_kind if radio_kind in ("bluez", "sim") else "off"
    facts = asyncio.run(
        collect(
            radio=kind,
            adapter=adapter if adapter and adapter.startswith("hci") else "hci0",
            air=default_air_dir() if kind == "sim" else None,
            lan_address=local_ip(),
            live=live,
        )
    )
    return [
        Check(row.name, "ok" if row.level == "info" else row.level, row.detail, row.hint)
        for row in rows(facts)
    ]


def _clock(now: datetime, synced: bool | None = None, rtc: bool | None = None) -> Check:
    """`synced` is whether anything set the clock from a network, and `rtc` whether a clock here
    survives a reboot, each None where it cannot be told. A passport is refused by a clock
    more than 300 s behind its `issued` or past its `expires`, so a wrong one refuses good
    passports and signs wrong dates. It runs all the same, which is why these are warnings."""
    when = f"{now:%Y-%m-%d %H:%M} UTC"
    if now.year < EARLIEST_YEAR:
        said = f"it says {now:%Y-%m-%d}, before any passport quackd signs"
        return Check("clock", "fail", said, SET_THE_TIME)
    if synced is False:
        said = f"{when}, and nothing has set it from a network, so it may be wrong"
        return Check("clock", "warn", said, SET_THE_TIME)
    if rtc is False:
        said = f"{when}, with no clock here that survives a reboot"
        return Check("clock", "warn", said, "give it a network at every boot, or a clock battery")
    return Check("clock", "ok", when)


def _clock_state() -> tuple[bool | None, bool | None]:
    """What this computer says of its clock: `timedatectl`, which also speaks for chrony and ntpd,
    and the kernel's list of clocks that survive a reboot. On Linux alone, and only ever read."""
    if platform.system() != "Linux":
        return None, None
    synced = None
    with contextlib.suppress(OSError, subprocess.SubprocessError):
        asked = ["timedatectl", "show", "--property=NTPSynchronized", "--value"]
        done = subprocess.run(asked, capture_output=True, text=True, timeout=3, check=False)
        if done.returncode == 0 and done.stdout.strip() in ("yes", "no"):
            synced = done.stdout.strip() == "yes"
    clocks = list_dir(RTC)
    return synced, None if clocks is None else bool(clocks)


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
    from quackd.onboard.keys import KeysError, open_to_others, read_robot_key
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
    for secret in (home.robot_key, home.robot_env):
        mode = open_to_others(secret)
        if mode is not None:
            open_to = f"{secret} holds a secret, and others can read it (mode {mode})"
            checks.append(Check(secret.name, "warn", open_to, f"chmod 600 {secret}"))
    token = (
        home.passport_jws.read_text(encoding="utf-8", errors="replace").strip()
        if home.passport_jws.exists()
        else None
    )
    check = check_own_passport(token, robot.did)
    if facts is not None:
        check = check_board(check, facts)
    if not check.ok or check.reading is None:
        hint = SET_THE_TIME if check.clock_behind else "quackd onboard sign"
        checks.append(Check("passport", "fail", check.why, hint))
        return
    expires = check.reading.document["expires"]
    checks.append(Check("passport", "ok", f"signed by its owner, until {expires}"))
    if facts is not None:
        why, trouble = wizard.signing_state(home, file, facts.passport())
        said = [text for text in (why, trouble) if text is not None]
        if said:
            # signing needs whatever the trouble is, so it cannot help until that is mended
            sign = "quackd onboard sign" if trouble is None else None
            checks.append(Check("signed", "warn", "; ".join(said), sign))


def _brain(home: OnboardDir, file: Any) -> Check:
    llm = file.brain.llm
    if not llm:
        return Check(
            "brain",
            "warn",
            "none, so robots are met and nobody talks",
            f"set brain.llm in {home.robot_yaml}",
        )
    where = f" at {file.brain.base_url}" if file.brain.base_url else ""
    sdk = sdk_for(llm)
    if sdk is not None and importlib.util.find_spec(sdk) is None:
        return Check(
            "brain", "fail", f"{llm}{where}, and its {sdk} SDK is not installed", ONBOARD_INSTALL
        )
    variable = key_variable(llm)
    if variable is not None and not _env_has(home, variable):
        if not key_needed(llm):
            return Check(
                "brain", "ok", f"{llm}{where}, with no {variable}, for a server needing none"
            )
        return Check(
            "brain",
            "fail",
            f"{llm}{where}, and robot.env holds no {variable}",
            f"put {variable}=... in {home.robot_env}, readable by this user alone",
        )
    return Check("brain", "ok", f"{llm}{where}")


def sdk_for(llm: str) -> str | None:
    """The SDK a brain's provider imports, or None for one quackd has without an extra."""
    vendor = llm.split(":", 1)[0].strip().lower()
    return {"local": "openai", "openai": "openai", "anthropic": "anthropic"}.get(vendor)


def key_variable(llm: str) -> str | None:
    """Which environment variable a brain's key goes in, or None for a brain that needs none."""
    from quackd.agent.providers.factory import KEY_ENV

    vendor = llm.split(":", 1)[0].strip().lower()
    if vendor == "fake":
        return None
    return KEY_ENV.get(vendor) or "LOCAL_API_KEY"


def key_needed(llm: str) -> bool:
    """Whether a brain stops without its key. An LLM server of your own may take none, and its
    provider sends the word `not-needed` when `LOCAL_API_KEY` is unset."""
    from quackd.agent.providers.local import LOCAL_NAMES

    vendor = llm.split(":", 1)[0].strip().lower()
    return key_variable(llm) is not None and vendor not in LOCAL_NAMES


def _env_has(home: OnboardDir, variable: str) -> bool:
    import os

    if os.environ.get(variable):
        return True
    try:
        lines = home.robot_env.read_text(encoding="utf-8").splitlines()
    except OSError:
        return False
    return any(line.split("=", 1)[0].strip() == variable for line in lines if "=" in line)


__all__ = ["EARLIEST_YEAR", "SET_THE_TIME", "Check", "key_needed", "key_variable", "preflight"]
