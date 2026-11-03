"""Any other Linux computer, by what it says of itself, and only when `--board generic` names it.

A robot's own computer need not be a Jetson, and what Robot Passport asks of one is small: Linux,
BlueZ and a clock, which preflight checks row by row. So this board matches nothing. It takes the
computer's word for its name, and its passport says kind `other`, with that name beside it, for a
reader to weigh. It is never found by itself, as sim is not, because nothing here can tell a robot's
computer from a desk's: `qcom,qcm6490` is a RUBIK Pi 3 and a Fairphone 5, and an x86 module and a
laptop read alike. No computer says it is a robot's, so this refuses only the ones whose firmware
says they are something else, a laptop above all, and a desktop still passes, its real name in the
passport it signs.

A computer the Jetson board knows stays the Jetson board's. One it accepts is told to leave out
`--board`, so no Jetson signs `other`, and one it refuses is refused here too, for the same reason,
so a Xavier does not come in by this door. The one exception is a chip it supports with no release
file at all, which is no JetPack it can read, as an IGX running NVIDIA's IGX software may be.

Every string is read from the computer, and none is matched. Where each comes from:

- `/proc/device-tree/model`, `compatible` and `chassis-type`: the Devicetree schema's root node,
  https://github.com/devicetree-org/dt-schema/blob/main/dtschema/schemas/root-node.yaml
- `/sys/class/dmi/id/*`: the kernel's `drivers/firmware/dmi-id.c`, which keeps the serials and the
  uuid from everyone but root. This never reads them: a passport says what a computer is, not which.
- What each `chassis_type` number means: systemd's `src/hostname/hostnamed.c`, after SMBIOS.
- The names firmware leaves in when nobody filled them: systemd's `hwdb.d/20-dmi-id.hwdb`.

Written from that documentation, and run on nothing: no board has been read, and no vendor's image
has been seen, so what any computer says here is not known until somebody runs it.
"""

from __future__ import annotations

import platform
import re

from quackd.boards import BoardFacts, RadioCheck, Unsupported, jetson
from quackd.boards.linux import (
    MODEL,
    Lister,
    Reader,
    compatible_entries,
    device_tree_text,
    list_dir,
    radio_check,
    read_file,
)

NAME = "generic"
CHASSIS = "/proc/device-tree/chassis-type"
DMI = "/sys/class/dmi/id"
OS_RELEASES = ("/etc/os-release", "/usr/lib/os-release")
UNNAMED = "a Linux computer that does not name itself"
"""What a passport says of a computer with no device tree model and no usable DMI name. It names
nothing, which is what was found."""

NOT_ON_A_ROBOT = frozenset(
    {
        "all-in-one",
        "convertible",
        "handheld",
        "handset",
        "headset",
        "laptop",
        "spectacles",
        "tablet",
        "television",
        "watch",
    }
)
"""The device tree's `chassis-type` values a robot's own computer is not. That leaves `desktop`,
`embedded` and `server`, and a tree that does not say."""

NOT_ON_A_ROBOT_SMBIOS = {
    8: "portable",
    9: "laptop",
    10: "notebook",
    11: "handheld",
    13: "all-in-one",
    14: "sub notebook",
    30: "tablet",
    31: "convertible",
    32: "detachable",
}
"""The same, by the number in `/sys/class/dmi/id/chassis_type`."""

PLACEHOLDERS = frozenset(
    {
        "defaultstring",
        "n/a",
        "o.e.m.",
        "oem",
        "systemmanufacturer",
        "systemname",
        "systemproductname",
        "tobefilledbyo.e.m.",
    }
)
"""systemd's list of DMI names that mean nobody filled them in, with the spaces out and in lower
case, as `_placeholder` compares them."""

_SOC = re.compile(r"[a-z0-9][a-z0-9._+-]*")
_PRETTY_NAME = re.compile(r"""^PRETTY_NAME=(["']?)(.*)\1\s*$""", re.MULTILINE)


class GenericBoard:
    """The generic board, which reads the real files only when it is handed nothing to read with."""

    name = NAME
    explicit_only = True
    radio_kind = "bluez"

    def __init__(self, read: Reader | None = None, listdir: Lister | None = None) -> None:
        self._read = read
        self._listdir = listdir

    def detect(self) -> BoardFacts | None:
        return facts(self._read or read_file, system=system_name())

    def radio(self) -> RadioCheck:
        return radio_check(self._listdir or list_dir)


def system_name() -> str:
    """The operating system, on one line of its own so a test can say Linux to this board alone."""
    return platform.system()


def facts(read: Reader, *, system: str = "Linux") -> BoardFacts | None:
    """What this computer calls itself. None off Linux, where BlueZ does not run, and `Unsupported`,
    with the reason, for a computer that is the Jetson board's or whose firmware says it is not a
    robot's."""
    if system != "Linux":
        return None
    _leave_a_jetson_to_its_board(read)
    _refuse_a_laptop(read)
    return BoardFacts(kind="other", model=_model(read), soc=_soc(read), release=_release(read))


def _leave_a_jetson_to_its_board(read: Reader) -> None:
    try:
        found = jetson.facts(read)
    except Unsupported:
        if jetson.chip_of(read) in jetson.SUPPORTED and read(jetson.RELEASE) is None:
            return  # a chip it takes, with no JetPack to read: nothing of the Jetson board's
        raise
    if found is not None:
        raise Unsupported(
            f"this computer is {found.model}, an NVIDIA Jetson quackd finds by itself: "
            "run it without --board"
        )


def _refuse_a_laptop(read: Reader) -> None:
    said = _said_by_firmware(read)
    if said is not None:
        raise Unsupported(
            f"its firmware calls this computer {_named(said[0])} ({said[1]}), which is no robot's "
            "own: to try it here as a simulation, which its passport will say, run quackd "
            "onboard --board sim"
        )


def _said_by_firmware(read: Reader) -> tuple[str, str] | None:
    """What the firmware calls a computer no robot carries, and where it says so, or None."""
    chassis = device_tree_text(read, CHASSIS).lower()
    if chassis in NOT_ON_A_ROBOT:
        return chassis, CHASSIS
    number = _dmi(read, "chassis_type")
    if number.isdecimal() and int(number) in NOT_ON_A_ROBOT_SMBIOS:
        return NOT_ON_A_ROBOT_SMBIOS[int(number)], f"{DMI}/chassis_type"
    return None


def _named(word: str) -> str:
    return f"an {word}" if word[:1] in "aeiou" else f"a {word}"


def _model(read: Reader) -> str:
    """The device tree's model; else the DMI product with its maker, or the board with its maker,
    since a module's firmware fills in one and leaves the other; else nothing named."""
    tree = _printable(device_tree_text(read, MODEL))
    if tree:
        return tree
    for maker, name in (("sys_vendor", "product_name"), ("board_vendor", "board_name")):
        named = _dmi(read, name)
        if named and not _placeholder(named):
            by = _dmi(read, maker)
            if by and not _placeholder(by) and not named.lower().startswith(by.lower()):
                return f"{by} {named}"
            return named
    return UNNAMED


def _soc(read: Reader) -> str | None:
    """The last of the device tree's `compatible` entries, which is the chip, without its maker:
    `qcom,qcm6490` is `qcm6490`. None where there is no tree, or the entry is no such name."""
    entries = [entry for entry in compatible_entries(read) if entry]
    if not entries:
        return None
    chip = entries[-1].rpartition(",")[2].lower()
    return chip if _SOC.fullmatch(chip) else None


def _release(read: Reader) -> str | None:
    """The system's name, shown in status and never signed: it changes with every upgrade."""
    for path in OS_RELEASES:
        match = _PRETTY_NAME.search(read(path) or "")
        if match:
            return _printable(match.group(2)) or None
    return None


def _dmi(read: Reader, name: str) -> str:
    return _printable(read(f"{DMI}/{name}") or "")


def _placeholder(text: str) -> bool:
    return "".join(text.split()).lower() in PLACEHOLDERS


def _printable(text: str) -> str:
    """Firmware's bytes on one line and with no control character: a stranger reads them next."""
    return " ".join("".join(c if c.isprintable() or c.isspace() else " " for c in text).split())
