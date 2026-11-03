"""What a board reads on any Linux computer: files by their paths, and what the kernel lists.

`jetson.py` and `generic.py` share this. Nothing here decides what a computer is: each board asks
through a `Reader` and a `Lister`, so every case is a test that needs no board, and only `read_file`
and `list_dir` touch the machine. Doctor reads none of it (ADR-0046).
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

from quackd.boards import RadioCheck

Reader = Callable[[str], str | None]
"""A file's text by its path, or None where it cannot be read."""
Lister = Callable[[str], list[str] | None]
"""A folder's entries by its path, or None where there is no such folder."""

MODEL = "/proc/device-tree/model"
COMPATIBLE = "/proc/device-tree/compatible"
BLUETOOTH = "/sys/class/bluetooth"

_NUL = "\x00"


def compatible_entries(read: Reader) -> list[str]:
    """The device tree's `compatible` entries, which are NUL-separated, most specific first."""
    return [part.strip() for part in (read(COMPATIBLE) or "").split(_NUL)]


def device_tree_text(read: Reader, path: str) -> str:
    """A device tree string, whose bytes end in a NUL, or nothing where it cannot be read."""
    return (read(path) or "").replace(_NUL, "").strip()


def radio_check(listdir: Lister) -> RadioCheck:
    """The first Bluetooth adapter the kernel lists, which the radio opens and preflight checks:
    `hci0` as a rule, and not on a computer whose only adapter came up as `hci1`."""
    adapters = sorted(name for name in listdir(BLUETOOTH) or [] if name.startswith("hci"))
    if adapters:
        return RadioCheck(ok=True, adapter=adapters[0])
    return RadioCheck(ok=False, why=f"no Bluetooth adapter: {BLUETOOTH} lists no hci device")


def read_file(path: str) -> str | None:
    try:
        return Path(path).read_bytes().decode("utf-8", "replace")
    except OSError:
        return None


def list_dir(path: str) -> list[str] | None:
    try:
        return os.listdir(path)
    except OSError:
        return None
