"""The generic board: any other Linux computer, by what it calls itself, only when named.

The computers here are files, as in `tests/linux_board_fixtures.py`, and what is tested is the
reading, never a board: nobody on this project has run this on one.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from quackd import boards
from quackd.boards import generic, jetson
from quackd.boards.generic import GenericBoard
from quackd.boards.jetson import JetsonBoard
from quackd.boards.linux import BLUETOOTH
from quackd.boards.sim import SimBoard
from tests.jetson_fixtures import COMPATIBLE as ORIN_NANO_COMPATIBLE
from tests.jetson_fixtures import ORIN_NANO, RELEASE_36_4_3
from tests.linux_board_fixtures import (
    DRAGON_Q6A,
    IDEACENTRE_MINI,
    IGX_ORIN,
    INDUSTRIAL_X86,
    IQ10_RRD,
    NUL,
    ROCK_5B,
    RUBIK_PI_3,
    THINKPAD_T14S,
    UBUNTU_24,
    dmi,
    tree,
)

ORIN = tree(ORIN_NANO, *ORIN_NANO_COMPATIBLE.split(NUL)) | {jetson.RELEASE: RELEASE_36_4_3}
XAVIER = tree("Jetson-AGX", "nvidia,p2972-0000", "nvidia,tegra194")
XAVIER_ON_JETPACK_5 = XAVIER | {
    jetson.RELEASE: RELEASE_36_4_3.replace("R36 ", "R35 ").replace("4.3", "4.1")
}


def found(files: Mapping[str, str]) -> boards.BoardFacts:
    facts = generic.facts(files.get)
    assert facts is not None
    return facts


def _among(files: Mapping[str, str]) -> dict[str, Any]:
    """quackd's own boards, every one reading `files`."""
    return {
        "jetson": JetsonBoard(read=files.get),
        boards.SIM: SimBoard(),
        boards.GENERIC: GenericBoard(read=files.get),
    }


# ── what it reads ───────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("files", "model", "soc"),
    [
        (DRAGON_Q6A, "Radxa Dragon Q6A", "qcm6490"),
        (RUBIK_PI_3, "Thundercomm RUBIK Pi 3", "qcm6490"),
        (IQ10_RRD, "Qualcomm Technologies, Inc. IQ10 RRD", "nord"),
        (ROCK_5B, "Radxa ROCK 5B", "rk3588"),
    ],
    ids=["dragon-q6a", "rubik-pi-3", "iq10-rrd", "rock-5b"],
)
def test_a_device_tree_names_the_computer_and_its_chip(
    files: Mapping[str, str], model: str, soc: str
) -> None:
    facts = found(files)
    assert (facts.kind, facts.model, facts.soc) == ("other", model, soc)


def test_dmi_names_a_computer_with_no_device_tree_by_its_maker_and_product() -> None:
    facts = found(INDUSTRIAL_X86)
    assert (facts.kind, facts.model, facts.soc) == ("other", "Acme Embedded Inc. AE-7000", None)


def test_a_device_tree_wins_over_dmi_where_a_computer_has_both() -> None:
    assert found(ROCK_5B | INDUSTRIAL_X86).model == "Radxa ROCK 5B"


@pytest.mark.parametrize(
    "placeholder",
    [
        "To be filled by O.E.M.",
        "To Be Filled By O.E.M.",
        "Default string",
        "System Product Name",
        "System manufacturer",
        "System Name",
        "O.E.M.",
        "OEM",
        "N/A",
        "  default STRING ",
    ],
)
def test_a_name_nobody_filled_in_is_passed_over_for_the_boards(placeholder: str) -> None:
    """systemd's list of DMI names that mean nothing: a module's firmware often fills in the board
    and leaves the product as its template had it."""
    files = dmi(
        sys_vendor=placeholder,
        product_name=placeholder,
        board_vendor="Acme",
        board_name="AE-7000-CB",
    )
    assert found(files).model == "Acme AE-7000-CB"


def test_a_maker_nobody_filled_in_is_left_out_and_one_already_named_is_not_said_twice() -> None:
    assert found(dmi(sys_vendor="OEM", product_name="AE-7000")).model == "AE-7000"
    said_twice = dmi(sys_vendor="Acme", product_name="acme AE-7000")
    assert found(said_twice).model == "acme AE-7000"


@pytest.mark.parametrize(
    "files",
    [{}, dmi(sys_vendor="Default string", product_name="Default string"), {"/etc/hostname": "x"}],
    ids=["nothing", "only-placeholders", "other-files"],
)
def test_a_computer_that_names_itself_nowhere_is_said_to_be_unnamed(
    files: Mapping[str, str],
) -> None:
    """Nothing is made up for it: the sentence names no model, which is what was found."""
    facts = found(files)
    assert (facts.model, facts.soc) == (generic.UNNAMED, None)
    assert facts.passport() == {"kind": "other", "model": generic.UNNAMED}


def test_firmware_cannot_put_a_control_character_in_a_passport() -> None:
    """A stranger reads this next, and a terminal may read it after them."""
    escape = chr(27)
    files = dmi(sys_vendor=f"Acme{escape}[31m", product_name=f"AE{chr(7)}\n7000\t")
    model = found(files).model
    assert model.isprintable() and "  " not in model
    assert model == "Acme [31m AE 7000"
    assert found(tree(f"Board{chr(8)} Two", "acme,two")).model == "Board Two"


def test_a_compatible_entry_no_chip_is_named_by_leaves_the_chip_out() -> None:
    assert found(tree("A board", "acme,board", "Acme,NOT A CHIP!")).soc is None
    assert found(tree("A board", "tegra264")).soc == "tegra264", "no maker in front of it"


# ── what goes in a passport ─────────────────────────────────────────────────────────────


def test_a_passport_carries_kind_model_and_chip_and_never_the_release() -> None:
    facts = found(DRAGON_Q6A | UBUNTU_24)
    assert facts.release == "Ubuntu 24.04.4 LTS"
    assert facts.passport() == {"kind": "other", "model": "Radxa Dragon Q6A", "soc": "qcm6490"}


@pytest.mark.parametrize(
    ("text", "release"),
    [
        ('PRETTY_NAME="Debian GNU/Linux 13 (trixie)"\n', "Debian GNU/Linux 13 (trixie)"),
        ("PRETTY_NAME='Radxa OS'\n", "Radxa OS"),
        ("ID=x\nPRETTY_NAME=Plain\nVERSION=1\n", "Plain"),
        ("NAME=No pretty name here\n", None),
        ('PRETTY_NAME=""\n', None),
    ],
    ids=["double", "single", "bare", "absent", "empty"],
)
def test_the_release_is_what_os_release_calls_the_system(text: str, release: str | None) -> None:
    assert found({generic.OS_RELEASES[0]: text}).release == release


def test_the_system_is_read_from_usr_lib_where_etc_has_no_os_release() -> None:
    files = {generic.OS_RELEASES[1]: 'PRETTY_NAME="Yocto"\n'}
    assert found(files).release == "Yocto"
    assert found({}).release is None


def test_a_long_model_and_chip_are_cut_to_what_a_passport_may_hold() -> None:
    words = "Acme Embedded Industrial Robot Computer Carrier Board Revision Twelve Extended"
    assert len(words) > boards.MODEL_LIMIT
    long_chip = "c" * (2 * boards.SOC_LIMIT)
    cut = found(tree(words, f"acme,{long_chip}")).passport()
    assert len(cut["model"]) <= boards.MODEL_LIMIT and words.startswith(cut["model"] + " ")
    assert cut["soc"] == "c" * boards.SOC_LIMIT


def test_it_never_reads_a_serial_or_a_uuid() -> None:
    """A passport says what a computer is, not which one: the kernel keeps these from everyone but
    root, and this must not come to need them."""
    asked: list[str] = []

    def reader(path: str) -> str | None:
        asked.append(path)
        return (INDUSTRIAL_X86 | UBUNTU_24).get(path)

    assert generic.facts(reader) is not None
    assert asked, "it read nothing"
    assert not [path for path in asked if "serial" in path or "uuid" in path]


# ── what it refuses ─────────────────────────────────────────────────────────────────────


def test_a_laptop_by_its_device_tree_is_refused_and_pointed_at_sim() -> None:
    with pytest.raises(boards.Unsupported) as caught:
        generic.facts(THINKPAD_T14S.get)
    said = str(caught.value)
    assert "a laptop" in said and generic.CHASSIS in said and "--board sim" in said


@pytest.mark.parametrize("chassis", sorted(generic.NOT_ON_A_ROBOT))
def test_every_chassis_no_robot_carries_is_refused(chassis: str) -> None:
    with pytest.raises(boards.Unsupported, match="no robot's own"):
        generic.facts(tree("A computer", "acme,computer", chassis=chassis).get)


@pytest.mark.parametrize("number", sorted(generic.NOT_ON_A_ROBOT_SMBIOS))
def test_every_smbios_number_no_robot_carries_is_refused(number: int) -> None:
    files = dmi(sys_vendor="Acme", product_name="Thin 14", chassis_type=str(number))
    with pytest.raises(boards.Unsupported, match="/sys/class/dmi/id/chassis_type"):
        generic.facts(files.get)


@pytest.mark.parametrize("chassis", ["embedded", "desktop", "server", "EMBEDDED"])
def test_a_computer_a_robot_might_carry_passes_on_its_device_tree(chassis: str) -> None:
    assert found(tree("A computer", "acme,computer", chassis=chassis)).model == "A computer"


@pytest.mark.parametrize(
    "number",
    # other, unknown, desktop, main server, embedded PC, mini PC; and what is no number at all
    ["1", "2", "3", "17", "34", "35", "", "not a number", "999"],
)
def test_a_computer_a_robot_might_carry_passes_on_its_smbios_number(number: str) -> None:
    files = dmi(sys_vendor="Acme", product_name="AE-7000", chassis_type=number)
    assert found(files).model == "Acme AE-7000"


def test_a_desktop_still_passes_with_its_real_name_in_the_passport() -> None:
    """Nothing here can tell a robot's computer from a desk's. What stops a desktop passing for
    something else is that its passport says what it is."""
    assert found(IDEACENTRE_MINI).passport() == {
        "kind": "other",
        "model": "Lenovo IdeaCentre Mini 01Q8X10",
        "soc": "x1e80100",
    }


def test_a_jetson_quackd_finds_by_itself_is_told_to_leave_out_the_flag() -> None:
    """Otherwise a Jetson could sign `other`, and a bare `quackd onboard sign` there, which finds
    the Jetson, would sign `jetson` over it and take its talk away."""
    with pytest.raises(boards.Unsupported, match="run it without --board") as caught:
        generic.facts(ORIN.get)
    assert ORIN_NANO in str(caught.value)


@pytest.mark.parametrize(
    ("files", "said"),
    [
        (XAVIER, "a Xavier"),
        (XAVIER_ON_JETPACK_5, "a Xavier"),
        (ORIN | {jetson.RELEASE: RELEASE_36_4_3.replace("R36 ", "R35 ")}, "on L4T 35"),
        ({jetson.RELEASE: RELEASE_36_4_3}, "names no NVIDIA Tegra chip"),
        (ORIN | {jetson.RELEASE: "something nobody has seen"}, "names no L4T release"),
    ],
    ids=["xavier", "xavier-jp5", "orin-jp5", "release-file-no-chip", "unreadable-release"],
)
def test_what_the_jetson_board_refuses_is_refused_here_for_the_same_reason(
    files: Mapping[str, str], said: str
) -> None:
    with pytest.raises(boards.Unsupported, match=said):
        generic.facts(files.get)


def test_a_supported_chip_with_no_release_file_may_use_it() -> None:
    """Which is what an IGX on NVIDIA's IGX software would be, if that software has no such file: no
    NVIDIA page says. The Jetson board refuses it, and this names it for what it says it is."""
    with pytest.raises(boards.Unsupported, match="with no /etc/nv_tegra_release"):
        JetsonBoard(read=IGX_ORIN.get).detect()
    facts = found(IGX_ORIN)
    assert facts.passport() == {
        "kind": "other",
        "model": "NVIDIA IGX Orin Development Kit",
        "soc": "tegra234",
    }


def test_an_igx_with_a_release_file_reads_as_the_jetson_board_today() -> None:
    """Keys on the chip and the release file alone, so this is what it does, written down."""
    files = IGX_ORIN | {jetson.RELEASE: RELEASE_36_4_3}
    facts = JetsonBoard(read=files.get).detect()
    assert facts is not None and facts.kind == "jetson" and "IGX" in facts.model
    with pytest.raises(boards.Unsupported, match="run it without --board"):
        generic.facts(files.get)


@pytest.mark.parametrize("system", ["Windows", "Darwin"])
def test_off_linux_it_is_no_board_at_all(system: str) -> None:
    assert generic.facts(DRAGON_Q6A.get, system=system) is None


def test_asked_for_off_linux_it_says_so_and_points_at_sim(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(generic, "system_name", lambda: "Windows")
    with pytest.raises(boards.NotARobot) as caught:
        boards.detect(boards.GENERIC, among=_among(DRAGON_Q6A))
    said = str(caught.value)
    assert "it found no generic board here" in said and "--board sim" in said


# ── how it is found ─────────────────────────────────────────────────────────────────────


def test_it_is_only_ever_found_by_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(generic, "system_name", lambda: "Linux")
    assert boards.boards()[boards.GENERIC].explicit_only
    for files in (DRAGON_Q6A, INDUSTRIAL_X86, ROCK_5B, {}):
        with pytest.raises(boards.NotARobot):
            boards.detect(among=_among(files))
    board, facts = boards.detect(boards.GENERIC, among=_among(DRAGON_Q6A))
    assert (board.name, board.radio_kind, facts.kind) == (boards.GENERIC, "bluez", "other")


def test_a_jetson_is_still_found_by_itself_with_the_board_installed() -> None:
    board, facts = boards.detect(among=_among(ORIN))
    assert (board.name, facts.kind) == ("jetson", "jetson")


def test_detecting_this_computer_never_comes_back_as_generic() -> None:
    """The real files, through the default path: whatever this machine is, it is never generic."""
    try:
        board, _ = boards.detect()
    except (boards.NotARobot, boards.Unsupported):
        return
    assert board.name != boards.GENERIC


def test_it_is_quackds_own_and_adds_no_kind_a_passport_may_say() -> None:
    from quackd.passport.document import BOARDS

    assert boards.GENERIC in boards.RESERVED and boards.GENERIC not in boards.OFFICIAL
    assert boards.KINDS == BOARDS == ("jetson", "sim", "other")


def test_its_radio_is_the_first_hci_adapter_and_none_is_said_plainly() -> None:
    folders = {BLUETOOTH: ["hci1", "hci0"]}
    check = GenericBoard(listdir=folders.get).radio()
    assert check == boards.RadioCheck(ok=True, adapter="hci0")
    none = GenericBoard(listdir={}.get).radio()
    assert not none.ok and "no Bluetooth adapter" in (none.why or "")
