"""Linux computers made of files, for the generic board's tests.

The device trees are the kernel's own strings, byte for byte, from Linux master 6628e7d4c6b2 of
2026-11-01, each from the `.dts` named beside it under `arch/arm64/boot/dts/`. What a vendor's image
ships may differ, and nobody on this project has read one off a board. The DMI values are made up to
the shape of `/sys/class/dmi/id`, since no vendor publishes them.

The thing tested is always the reading and never the hardware.
"""

from __future__ import annotations

from quackd.boards.generic import CHASSIS, DMI, OS_RELEASES
from quackd.boards.linux import COMPATIBLE, MODEL

NUL = chr(0)
NL = chr(10)


def tree(model: str, *compatible: str, chassis: str | None = None) -> dict[str, str]:
    """A device tree, as files, with the NUL terminators it really has."""
    files = {MODEL: model + NUL, COMPATIBLE: NUL.join(compatible) + NUL}
    if chassis is not None:
        files[CHASSIS] = chassis + NUL
    return files


def dmi(**values: str) -> dict[str, str]:
    """DMI, as files, with the newline sysfs ends each one with."""
    return {f"{DMI}/{name}": value + NL for name, value in values.items()}


DRAGON_Q6A = tree(  # qcom/qcs6490-radxa-dragon-q6a.dts
    "Radxa Dragon Q6A", "radxa,dragon-q6a", "qcom,qcm6490", chassis="embedded"
)
RUBIK_PI_3 = tree(  # qcom/qcs6490-thundercomm-rubikpi3.dts
    "Thundercomm RUBIK Pi 3", "thundercomm,rubikpi3", "qcom,qcm6490", chassis="embedded"
)
IQ10_RRD = tree(  # qcom/nord-rrd.dts
    "Qualcomm Technologies, Inc. IQ10 RRD", "qcom,iq10-rrd", "qcom,nord"
)
ROCK_5B = tree("Radxa ROCK 5B", "radxa,rock-5b", "rockchip,rk3588")  # rockchip/rk3588-rock-5b.dts
IDEACENTRE_MINI = tree(  # qcom/hamoa-lenovo-ideacentre-mini-01q8x10.dts
    "Lenovo IdeaCentre Mini 01Q8X10",
    "lenovo,ideacentre-mini-01q8x10",
    "qcom,x1e80100",
    chassis="desktop",
)
THINKPAD_T14S = tree(  # qcom/x1e78100-lenovo-thinkpad-t14s.dtsi
    "Lenovo ThinkPad T14s Gen 6",
    "lenovo,thinkpad-t14s",
    "qcom,x1e78100",
    "qcom,x1e80100",
    chassis="laptop",
)
IGX_ORIN = tree(  # nvidia/tegra234-p3740-0002+p3701-0008.dts
    "NVIDIA IGX Orin Development Kit",
    "nvidia,p3740-0002+p3701-0008",
    "nvidia,p3701-0008",
    "nvidia,tegra234",
)

INDUSTRIAL_X86 = dmi(
    sys_vendor="Acme Embedded Inc.",
    product_name="AE-7000",
    board_vendor="Acme Embedded Inc.",
    board_name="AE-7000-CB",
    chassis_type="34",
)
"""An x86 module's carrier as its firmware might name it, chassis type 34 being an embedded PC."""

UBUNTU_24 = {OS_RELEASES[0]: 'NAME="Ubuntu"\nPRETTY_NAME="Ubuntu 24.04.4 LTS"\nID=ubuntu\n'}
