"""The systemd unit that keeps a robot's onboard service running, and the commands that put it in.

A system unit with `User=`, rather than a user unit, for three reasons: it starts at boot with
nobody logged in and no lingering to switch on, it can add the `bluetooth` group BlueZ's D-Bus
policy admits, and it matches the units quackd's bridges ship. It yields to the robot: a lower
priority than its control loop, a memory ceiling, and first in line for the OOM killer, since a
robot that falls because its passport service ate the memory would be the worst outcome here.
Everything quackd writes stays in the robot's directory, which is the one place it may write.

Nothing here runs a command. `install_commands` returns them, so the wizard prints every one
before it runs it with sudo, and a person can run them by hand instead.
"""

from __future__ import annotations

import importlib
import shlex
from pathlib import Path

UNIT_NAME = "quackd-onboard.service"
UNIT_PATH = Path("/etc/systemd/system") / UNIT_NAME
#: The group BlueZ's D-Bus policy lets talk to it, on the images a Jetson ships with.
BLUETOOTH_GROUP = "bluetooth"


def group_exists(name: str) -> bool:
    """Whether the system has group `name`. Windows has no groups of this kind at all, and no
    `grp` module, which is why it is imported here and not at the top."""
    try:
        groups = importlib.import_module("grp")
        groups.getgrnam(name)
    except (ImportError, KeyError):
        return False
    return True


def unit_text(*, user: str, directory: Path, quackd: str, bluetooth_group: bool = True) -> str:
    """The unit, for `user`, running `quackd` (an absolute path) on the robot directory
    `directory`. Without the bluetooth group the line is left out, since systemd refuses to
    start a unit that names a group the system lacks; status then says why it hears nobody."""
    groups = f"SupplementaryGroups={BLUETOOTH_GROUP}\n" if bluetooth_group else ""
    where = str(directory)
    return f"""# Written by `quackd onboard`; running it again rewrites this file.
[Unit]
Description=quackd onboard: this robot's passport, its talk, and its brain
Documentation=https://github.com/rokbenko/quackd/blob/main/docs/passport/quackd.md
Wants=network-online.target bluetooth.service
After=network-online.target bluetooth.service
StartLimitIntervalSec=300
StartLimitBurst=5

[Service]
Type=simple
User={user}
{groups}WorkingDirectory={where}
Environment=PYTHONUNBUFFERED=1
Environment=QUACKD_ONBOARD_DIR={where}
Environment=QUACKD_CACHE_DIR={where}/cache
Environment=QUACKD_MEMORY_DIR={where}/memory
# the brain's key and nothing else, in a file only {user} can read
EnvironmentFile=-{where}/robot.env
ExecStart={quackd} onboard serve
Restart=on-failure
RestartSec=5
TimeoutStopSec=20
# yields to the robot: lower priority than its control loop, a ceiling, first for the OOM killer
Nice=10
MemoryMax=1G
OOMScoreAdjust=300
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths={where}
PrivateTmp=yes
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6 AF_NETLINK AF_BLUETOOTH

[Install]
WantedBy=multi-user.target
"""


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
