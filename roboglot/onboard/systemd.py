"""The systemd unit that keeps a robot's onboard service running, and the commands that put it in.

A system unit with `User=`, rather than a user unit, for three reasons: it starts at boot with
nobody logged in and no lingering to switch on, it can add the `bluetooth` group BlueZ's D-Bus
policy admits, and it matches the units Roboglot's bridges ship. It yields to the robot: a lower
priority than its control loop, a memory ceiling, and first in line for the OOM killer, since a
robot that falls because its passport service ate the memory would be the worst outcome here.
Everything Roboglot writes stays in the robot's directory, which is the one place it may write.

Nothing here runs a command. `install_commands` returns them, so the wizard prints every one
before it runs it with sudo, and a person can run them by hand instead.

The user, the robot directory and roboglot's own path go into the unit the way systemd reads them
back as given (systemd.syntax(5), systemd.unit(5), systemd.exec(5)): a word of a list or of the
command line in systemd's own quoting, `EnvironmentFile=`'s pattern with glob's characters
escaped, and every `%` doubled, so the service runs as, and where, the wizard says. A value that
needs none of it is written as it always was. A control character, or a name whose bytes are not
UTF-8, no writing carries through, so the unit is refused (`UnitError`), naming which value holds
it.
"""

from __future__ import annotations

import importlib
import shlex
import unicodedata
from pathlib import Path, PurePath, PureWindowsPath

UNIT_NAME = "roboglot-onboard.service"
UNIT_PATH = Path("/etc/systemd/system") / UNIT_NAME
#: The group BlueZ's D-Bus policy lets talk to it, on the images a Jetson ships with.
BLUETOOTH_GROUP = "bluetooth"
#: How long systemd waits, once it has asked the service to stop, before it kills it: the
#: unit's `TimeoutStopSec`, which the service's own close keeps inside (`service.CLOSE_S`).
STOP_TIMEOUT_S = 20
#: What systemd's own quoting reads as something other than itself in a list of words or a
#: command line (systemd.syntax(5), "Quoting"): the whitespace it splits at, the two quotes it
#: takes away, and the backslash that begins an escape.
_QUOTING = frozenset(" \t\"'\\")
#: What glob(7) reads as a pattern rather than a name, in `EnvironmentFile=`, which takes a
#: pattern: the backslash among them, which keeps the character after it as itself.
_GLOB = frozenset("*?[\\")


class UnitError(ValueError):
    """A user, robot directory or path to roboglot that no writing of the unit carries through
    systemd's reading as itself, said with which one it is and why."""


def group_exists(name: str) -> bool:
    """Whether the system has group `name`. Windows has no groups of this kind at all, and no
    `grp` module, which is why it is imported here and not at the top."""
    try:
        groups = importlib.import_module("grp")
        groups.getgrnam(name)
    except (ImportError, KeyError):
        return False
    return True


def unit_text(
    *, user: str, directory: PurePath, roboglot: str, bluetooth_group: bool = True
) -> str:
    """The unit, for `user`, running `roboglot` (an absolute path) on the robot directory
    `directory`. Without the bluetooth group the line is left out, since systemd refuses to
    start a unit that names a group the system lacks; status then says why it hears nobody.

    Each value is written so that systemd reads it back as given, and one it cannot is refused
    with a `UnitError` naming it: a newline would end its line and begin a directive of its own
    in a file installed with sudo and never shown."""
    where = str(directory)
    for what, value in (
        ("the robot directory", where),
        ("the user", user),
        ("roboglot's own path", roboglot),
    ):
        _refuse_unwritable(what, value)
    if ".." in directory.parts:
        # systemd refuses a WorkingDirectory= that is not a normal path, and resolving the
        # folder here could name another than the one meant, by a link on the way
        raise UnitError(
            f"the robot directory {where!r} has '..' parts, which a systemd unit refuses in a "
            "path: give the folder without '..' parts"
        )
    # a backslash is part of a name on a Linux robot, and systemd's quoting has to keep it; on
    # Windows, where no unit is ever installed and only the suite writes one, it is what parts
    # the folders of a path, and stays as it is
    native = "\\" if isinstance(directory, PureWindowsPath) else ""
    groups = f"SupplementaryGroups={BLUETOOTH_GROUP}\n" if bluetooth_group else ""
    return f"""# Written by `roboglot onboard`; running it again rewrites this file.
[Unit]
Description=Roboglot onboard: this robot's passport, its talk, and its brain
Documentation=https://github.com/rokbenko/quackd/blob/main/docs/passport/roboglot.md
Wants=network-online.target bluetooth.service
After=network-online.target bluetooth.service
StartLimitIntervalSec=300
StartLimitBurst=5

[Service]
Type=simple
User={_user(user)}
{groups}WorkingDirectory={_whole(where, native)}
Environment=PYTHONUNBUFFERED=1
Environment={_word(f"ROBOGLOT_ONBOARD_DIR={where}", native)}
Environment={_word(f"ROBOGLOT_MEMORY_DIR={where}/memory", native)}
# the brain's key and nothing else, in a file only {user} can read
EnvironmentFile=-{_pattern(where, native)}/robot.env
ExecStart={_word(roboglot, native)} onboard serve
Restart=on-failure
RestartSec=5
TimeoutStopSec={STOP_TIMEOUT_S}
# yields to the robot: lower priority than its control loop, a ceiling, first for the OOM killer
Nice=10
MemoryMax=1G
OOMScoreAdjust=300
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths={_word(where, native)}
PrivateTmp=yes
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6 AF_NETLINK AF_BLUETOOTH

[Install]
WantedBy=multi-user.target
"""


def _refuse_unwritable(what: str, value: str) -> None:
    """Refuse a value no writing carries through systemd's reading of a unit: a control
    character, which a newline above all turns into the end of a line and the start of a
    directive of its own, or a name whose bytes are not UTF-8 (the surrogates Python decodes
    them to), which systemd does not read as a unit's text."""
    for char in value:
        kind = unicodedata.category(char)
        if kind == "Cc":
            raise UnitError(
                f"{what} {value!r} holds a control character, which a systemd unit cannot hold"
            )
        if kind == "Cs":
            raise UnitError(f"{what} {value!r} is not UTF-8, which a systemd unit has to be")


def _percent(value: str) -> str:
    """`value` with every `%` doubled, which systemd reads as one `%` in every directive here,
    where a lone one begins a specifier it expands (systemd.unit(5), "Specifiers")."""
    return value.replace("%", "%%")


def _user(user: str) -> str:
    """`user` for `User=`, which takes one name and no quotes: the rest of the line, once systemd
    has dropped the spaces at either end and joined a line that ends in a backslash to the next.
    A name it would read as another is refused, and so is an empty one, which runs as root."""
    if not user:
        raise UnitError("the user's name is empty, and a unit with an empty User= runs as root")
    if user.strip(" ") != user:
        raise UnitError(
            f"the user {user!r} begins or ends with a space, which User= drops, so naming "
            "another account"
        )
    if user.endswith("\\"):
        raise UnitError(
            f"the user {user!r} ends in a backslash, which joins the line after User= to it"
        )
    return _percent(user)


def _whole(value: str, native: str) -> str:
    """`value` as the whole of `WorkingDirectory=`, which takes no quotes: systemd reads the rest
    of the line once it has dropped the spaces at either end, and joins a line that ends in a
    backslash to the next. A path that ends in either is written with a slash after it, which
    systemd takes off a path once it has read it. `native` is the backslash on Windows, where it
    parts folders (`unit_text`)."""
    ends = tuple({" ", "\\"} - set(native))
    return _percent(f"{value}/" if value.endswith(ends) else value)


def _word(value: str, native: str) -> str:
    """`value` as one word of a list or a command line in systemd's own quoting: as it is when
    nothing in it reads as anything else, so an ordinary path is written as it always was, and
    otherwise in double quotes, with a backslash before each double quote and backslash in it.
    `native` is the backslash on Windows, where it parts folders (`unit_text`)."""
    special = _QUOTING - set(native)
    if value and special.isdisjoint(value):
        return _percent(value)
    escaped = {'"', "\\"} - set(native)
    inner = "".join(f"\\{char}" if char in escaped else char for char in value)
    return _percent(f'"{inner}"')


def _pattern(value: str, native: str) -> str:
    """`value` as the start of `EnvironmentFile=`'s glob(7) pattern, each character glob reads as
    a pattern kept as itself by a backslash before it. `native` is the backslash on Windows,
    where it parts folders (`unit_text`)."""
    special = _GLOB - set(native)
    return _percent("".join(f"\\{char}" if char in special else char for char in value))


def install_commands(staged: Path) -> list[list[str]]:
    """What puts the unit at `staged` in place and starts it, each a command for sudo."""
    return [
        ["sudo", "install", "-m", "0644", "-o", "root", "-g", "root", str(staged), str(UNIT_PATH)],
        ["sudo", "systemctl", "daemon-reload"],
        ["sudo", "systemctl", "enable", "--now", UNIT_NAME],
    ]


def uninstall_commands() -> list[list[str]]:
    return [
        ["sudo", "systemctl", "disable", "--now", UNIT_NAME],
        ["sudo", "rm", "-f", str(UNIT_PATH)],
        ["sudo", "systemctl", "daemon-reload"],
    ]


def shown(command: list[str]) -> str:
    """A command as a person would type it."""
    return " ".join(shlex.quote(part) for part in command)
