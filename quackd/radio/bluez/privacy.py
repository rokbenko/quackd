"""BlueZ address privacy: `Privacy = device` in /etc/bluetooth/main.conf, and what to say about it.

A robot's beacon id rotates, but the radio's own address goes out with every advert too, and a
public address never changes. While it stays public, anybody can follow the robot by it and the
rotating id buys nothing. BlueZ rotates the address as well when main.conf asks for privacy, so
the wizard offers to set it: this module makes the edited file, byte for byte the old one but
for that one line, the commands that install it with a backup (run with sudo, after asking), and
the rule `quackd onboard uninstall` follows to put the old file back.

Whether the controller then really sends a rotating address cannot be seen without root, so
status and preflight report what main.conf says and what BlueZ says the adapter's address type
is, and say plainly that the rest is unverified. Pure text in and out; nothing here touches the
file system or runs a command, and none of it has been tried on a robot's own BlueZ.
"""

from __future__ import annotations

import re

MAIN_CONF = "/etc/bluetooth/main.conf"
BACKUP = "/etc/bluetooth/main.conf.quackd-backup"
WANTED = "device"
#: The values main.conf's Privacy takes, from BlueZ's own main.conf comments: anything but off
#: has the controller use a resolvable private address that it rotates.
PRIVATE = ("device", "network", "on", "limited-network", "limited-device")
_GROUP = re.compile(r"^\s*\[([^\]]*)\]\s*$")
_PRIVACY = re.compile(r"^(\s*)Privacy(\s*)=(\s*)([^\r\n]*?)(\s*)$")


def _lines(text: str) -> list[str]:
    return text.splitlines(keepends=True)


def _newline(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"


def privacy_setting(text: str) -> str | None:
    """What main.conf sets `Privacy` to under `[General]`, or None when it does not set it,
    which BlueZ reads as off. A later line wins over an earlier one, as GKeyFile reads it."""
    group, value = None, None
    for line in _lines(text):
        header = _GROUP.match(line)
        if header:
            group = header.group(1).strip()
            continue
        match = _PRIVACY.match(line)
        if group == "General" and match:
            value = match.group(4).strip()
    return value


def with_privacy(text: str, value: str = WANTED) -> str:
    """main.conf with `Privacy = value` under `[General]`: the line that sets it replaced, or a
    line added under the group's header, or the group added at the top when there is none.
    Every other byte is the file's own."""
    if value not in PRIVATE:
        raise ValueError(f"{value!r} is not a private Privacy setting: {', '.join(PRIVATE)}")
    nl = _newline(text)
    lines = _lines(text)
    group, header_at, setting_at = None, None, None
    for i, line in enumerate(lines):
        header = _GROUP.match(line)
        if header:
            group = header.group(1).strip()
            if group == "General" and header_at is None:
                header_at = i
            continue
        if group == "General" and _PRIVACY.match(line):
            setting_at = i  # the last one wins, so that is the one to change
    line = f"Privacy = {value}"
    if setting_at is not None:
        old = lines[setting_at]
        ending = old[len(old.rstrip("\r\n")) :] or ""
        lines[setting_at] = line + ending
        return "".join(lines)
    if header_at is not None:
        group_line = lines[header_at]
        if not group_line.endswith(("\n", "\r")):
            lines[header_at] = group_line + nl
        lines.insert(header_at + 1, line + nl)
        return "".join(lines)
    return f"[General]{nl}{line}{nl}{nl}" + text


def install_commands(new_file: str, *, backup_exists: bool) -> list[list[str]]:
    """The commands that put the edited main.conf in place, for the wizard to show and run
    with sudo once the person has said yes: a backup of the original (never over an earlier
    backup, which holds the file as it was before quackd touched it), the new file as root's
    with mode 0644, and bluetoothd restarted, since it reads main.conf only when it starts."""
    commands = []
    if not backup_exists:
        commands.append(["sudo", "cp", "-p", MAIN_CONF, BACKUP])
    commands += [
        ["sudo", "install", "-m", "0644", "-o", "root", "-g", "root", new_file, MAIN_CONF],
        ["sudo", "systemctl", "restart", "bluetooth"],
    ]
    return commands


def should_restore(current: str, backup: str | None) -> bool:
    """Whether uninstall puts the backup back: only when there is one and main.conf is still
    exactly what quackd made of it. A file somebody has edited since is theirs, and is left."""
    return backup is not None and current == with_privacy(backup)


def restore_commands() -> list[list[str]]:
    """The commands that put the original main.conf back and restart bluetoothd."""
    return [
        ["sudo", "install", "-m", "0644", "-o", "root", "-g", "root", BACKUP, MAIN_CONF],
        ["sudo", "rm", BACKUP],
        ["sudo", "systemctl", "restart", "bluetooth"],
    ]


def privacy_words(setting: str | None, address_type: str | None) -> tuple[bool, str]:
    """Whether the address is private as far as can be told, and a sentence saying how far
    that is: what main.conf asks for and what BlueZ reports, never more."""
    asked = setting in PRIVATE
    if address_type == "random":
        return True, (
            "the adapter's address is random; whether the controller rotates it cannot be "
            "seen without root"
        )
    if asked:
        return False, (
            f"main.conf asks for address privacy (Privacy = {setting}), and BlueZ still reports "
            f"a {address_type or 'unknown'} address type for the adapter: that is its identity "
            "address, and whether the controller sends a rotating one instead cannot be seen "
            "without root"
        )
    return False, (
        "the adapter's address is public, the same in every advert, so rotating the beacon id "
        "alone does not stop tracking: quackd onboard can set Privacy = device in "
        f"{MAIN_CONF}"
    )


__all__ = [
    "BACKUP",
    "MAIN_CONF",
    "PRIVATE",
    "install_commands",
    "privacy_setting",
    "privacy_words",
    "restore_commands",
    "should_restore",
    "with_privacy",
]
