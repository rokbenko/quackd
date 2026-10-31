"""An NVIDIA Jetson, found by the files every one carries.

Three of the files the host daemon sends `quackd doctor` from afar (`doctor._jetson_from_dump`),
read here on the board itself: the device tree's `model` names the board, its `compatible` names
the chip (`nvidia,tegra234` is Orin, `nvidia,tegra264` is Thor), and `/etc/nv_tegra_release`
names the L4T release, which names the JetPack (`l4t.py`). `facts` is pure, over a function that
reads a path, so every case is a test that needs no board; `JetsonBoard` hands it the real files
by default, and nothing else in this module reads one.

quackd onboard supports Orin with JetPack 6 or 7.2 and Thor with JetPack 7, and nothing else
(`SUPPORTED`).
The chip is asked first, so a Xavier is refused for its chip whatever it runs, and then the release
on that chip: an Orin on JetPack 5 is refused for a release it can be upgraded from, and a Thor
that reads as JetPack 6, which no Thor runs, is refused as well, its release file a container's
or wrong.
This project has run none of it on a Jetson: the device tree strings a Thor or an Orin NX carries
were not read off one, which is why detection keys on the chip alone.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from pathlib import Path

from quackd.boards import BoardFacts, RadioCheck, Unsupported
from quackd.boards.l4t import jetpack_for, parse_l4t

Reader = Callable[[str], str | None]
"""A file's text by its path, or None where it cannot be read."""
Lister = Callable[[str], list[str] | None]
"""A folder's entries by its path, or None where there is no such folder."""

MODEL = "/proc/device-tree/model"
COMPATIBLE = "/proc/device-tree/compatible"
RELEASE = "/etc/nv_tegra_release"
BLUETOOTH = "/sys/class/bluetooth"

CHIPS = {
    "tegra264": "Thor",
    "tegra234": "Orin",
    "tegra194": "Xavier",
    "tegra186": "TX2",
    "tegra210": "Nano or TX1",
}
"""The chip a Jetson's device tree names, by the name of the modules NVIDIA builds on it."""

SUPPORTED = {"tegra234": ("36", "39"), "tegra264": ("38", "39")}
"""The L4T majors quackd onboard takes on each chip: 36, JetPack 6, and 39, JetPack 7.2, which
NVIDIA's JetPack 7.2 page says added Orin to JetPack 7, on Orin; and 38 and 39, JetPack 7, on Thor
(`l4t.JETPACK_MAJOR`). JetPack 7.0 and 7.1, L4T 38, are Thor's alone."""
SUPPORTS = "quackd onboard supports Orin with JetPack 6 or 7.2 and Thor with JetPack 7"

_CHIP = re.compile(r"nvidia,(tegra\d+)")
_NUL = "\x00"


class JetsonBoard:
    """The Jetson board. It reads the real files only when it is handed nothing to read with."""

    name = "jetson"
    explicit_only = False
    radio_kind = "bluez"

    def __init__(self, read: Reader | None = None, listdir: Lister | None = None) -> None:
        self._read = read
        self._listdir = listdir

    def detect(self) -> BoardFacts | None:
        return facts(self._read or read_file)

    def radio(self) -> RadioCheck:
        return radio_check(self._listdir or list_dir)


def facts(read: Reader) -> BoardFacts | None:
    """What this Jetson is, None where it is no Jetson at all, and `Unsupported`, with the
    reason, where it is one quackd onboard cannot run on.

    No Jetson means neither file a Jetson carries: no NVIDIA Tegra among the device tree's
    `compatible` entries, which are NUL-separated, and no release file. One without the other is
    a Jetson something hides, and it is refused, saying which."""
    entries = [part.strip() for part in (read(COMPATIBLE) or "").split(_NUL)]
    chip = next((m.group(1) for e in entries if (m := _CHIP.fullmatch(e))), None)
    release_text = read(RELEASE)
    if chip is None and release_text is None:
        return None
    if chip is None:
        raise Unsupported(
            f"{RELEASE} is here, and {COMPATIBLE} names no NVIDIA Tegra chip, which a container "
            "on a Jetson hides unless it runs privileged or with --security-opt "
            "systempaths=unconfined (on any other computer, this is an L4T image and no Jetson): "
            f"{SUPPORTS}, and reads the chip from there"
        )
    family = CHIPS.get(chip)
    what = (
        f"{_article(family)} {family} ({chip})"
        if family
        else f"a Tegra quackd does not know ({chip})"
    )
    if chip not in SUPPORTED:
        raise Unsupported(f"{what}: {SUPPORTS}")
    if release_text is None:
        raise Unsupported(f"{what} with no {RELEASE} to say which JetPack it runs: {SUPPORTS}")
    l4t = parse_l4t(release_text)
    if l4t is None:
        raise Unsupported(
            f"{what} whose {RELEASE} names no L4T release quackd can read: {SUPPORTS}"
        )
    jetpack = jetpack_for(l4t)
    release = f"L4T {l4t} (JetPack {jetpack})" if jetpack else f"L4T {l4t}"
    if l4t.split(".")[0] not in SUPPORTED[chip]:
        raise Unsupported(f"{what} on {release}: {SUPPORTS}")
    model = (read(MODEL) or "").replace(_NUL, "").strip()
    return BoardFacts(
        kind="jetson", model=model or f"NVIDIA Jetson {family}", soc=chip, release=release
    )


def radio_check(listdir: Lister) -> RadioCheck:
    """The first Bluetooth adapter the kernel lists, which is the one BlueZ calls `hci0`."""
    adapters = sorted(name for name in listdir(BLUETOOTH) or [] if name.startswith("hci"))
    if adapters:
        return RadioCheck(ok=True, adapter=adapters[0])
    return RadioCheck(ok=False, why=f"no Bluetooth adapter: {BLUETOOTH} lists no hci device")


def looked_for(system: str) -> str:
    """What `facts` looked for, for the sentence that says this computer is no Jetson."""
    if system != "Linux":
        return f"this computer runs {system}"
    return f"{COMPATIBLE} names no NVIDIA Tegra, and there is no {RELEASE}"


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


def _article(word: str) -> str:
    return "an" if word[:1] in "AEIOU" else "a"
