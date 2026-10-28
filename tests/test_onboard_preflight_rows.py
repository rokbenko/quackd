"""`quackd onboard preflight`, the radio's rows: what each finding says and what it tells a person
to do, from facts a test writes, and the collector run for real on this machine, where it can
only ever read."""

from __future__ import annotations

import asyncio
import json
import platform
from dataclasses import replace
from pathlib import Path

import pytest

from quackd.radio import ONBOARD_INSTALL
from quackd.radio.bluez.core import AdapterFacts
from quackd.radio.preflight import Facts, Row, as_json, collect, passed, render, rows

ADAPTER = AdapterFacts(
    path="/org/bluez/hci0",
    address="AA:BB:CC:DD:EE:FF",
    address_type="public",
    alias="robot",
    powered=True,
    roles=("central", "peripheral"),
    advertising=True,
    supported_instances=5,
    gatt=True,
)
PACKAGES = {"zeroconf": "0.151.5", "openai": "2.1.0", "bleak": "3.0.2", "dbus-fast": "5.2.0"}
HEALTHY = Facts(
    system="Linux",
    packages=PACKAGES,
    bluez_running=True,
    bluez_version="5.72",
    adapter=ADAPTER,
    rfkill=False,
    main_conf="[General]\nPrivacy = device\n",
    in_bluetooth_group=True,
    lan_address="192.0.2.20",
)


def by_name(found: list[Row]) -> dict[str, Row]:
    return {r.name: r for r in found}


def test_a_healthy_robot_passes_and_says_what_it_found() -> None:
    found = rows(HEALTHY)
    assert passed(found)
    named = by_name(found)
    assert named["bluetoothd"] == Row("bluetoothd", "ok", "BlueZ 5.72")
    assert named["adapter"].detail == "hci0 AA:BB:CC:DD:EE:FF, central, peripheral"
    assert named["LE advertising"].level == "ok" and "5 instances" in named["LE advertising"].detail
    assert named["advert interval"].level == "info"
    # main.conf asks for privacy and BlueZ reports a public identity address: said, not claimed
    assert named["address privacy"].level == "warn"
    assert "cannot be seen without root" in named["address privacy"].detail
    assert named["LAN"] == Row("LAN", "ok", "192.0.2.20, announced over DNS-SD")
    assert json.loads(json.dumps(as_json(found)))[0]["name"] == "onboard extra"


def test_a_board_row_comes_first_as_the_board_check_wrote_it() -> None:
    board = Row("board", "ok", "NVIDIA Jetson AGX Orin, L4T 36.4")
    assert rows(replace(HEALTHY, board=board))[0] == board


def test_bluez_off_linux_fails_and_points_at_the_sim() -> None:
    found = rows(replace(HEALTHY, system="Windows"))
    row = by_name(found)["bluetooth"]
    assert row.level == "fail" and "Windows" in row.detail and "--board sim" in (row.hint or "")
    assert not passed(found)


def test_a_missing_package_fails_naming_the_extra_but_the_sim_needs_no_bluetooth_packages() -> None:
    lacking = {**PACKAGES, "bleak": None, "dbus-fast": None}
    row = by_name(rows(replace(HEALTHY, packages=lacking)))["onboard extra"]
    assert row.level == "fail" and "bleak, dbus-fast" in row.detail and row.hint == ONBOARD_INSTALL
    sim = Facts(system="Windows", radio="sim", packages=lacking, air="C:/air", air_writable=True)
    assert by_name(rows(sim))["onboard extra"].level == "ok"
    bare = replace(sim, packages={**lacking, "zeroconf": None, "openai": None})
    assert by_name(rows(bare))["onboard extra"].level == "warn"  # the sim goes on without them


@pytest.mark.parametrize(
    ("changes", "name", "words", "hint"),
    [
        ({"bus": "denied", "bus_detail": "AccessDenied"}, "system bus", "AccessDenied", "usermod"),
        ({"bus": "missing", "bus_detail": "no socket"}, "system bus", "no socket", "systemctl"),
        ({"bluez_running": False}, "bluetoothd", "not running", "enable --now bluetooth"),
        ({"bluez_version": "5.53"}, "bluetoothd", "older than 5.55", None),
        ({"adapter": None}, "adapter", "no hci0", "bluetoothctl list"),
        ({"adapter": replace(ADAPTER, powered=False)}, "adapter", "powered off", "power on"),
        ({"rfkill": True}, "rfkill", "blocked", "rfkill unblock bluetooth"),
        ({"adapter": replace(ADAPTER, roles=("central",))}, "peripheral role", "cannot", None),
        (
            {"adapter": replace(ADAPTER, advertising=False)},
            "LE advertising",
            "no LEAdvertising",
            None,
        ),
        (
            {"adapter": replace(ADAPTER, supported_instances=0)},
            "LE advertising",
            "no advertising",
            None,
        ),
        ({"adapter": replace(ADAPTER, gatt=False)}, "GATT server", "no GattManager1", None),
    ],
)
def test_each_failure_says_what_was_found_and_what_to_do(
    changes: dict[str, object], name: str, words: str, hint: str | None
) -> None:
    found = rows(replace(HEALTHY, **changes))  # type: ignore[arg-type]
    row = by_name(found)[name]
    assert row.level == "fail" and words in row.detail
    assert (hint is None and row.hint is None) or (hint is not None and hint in (row.hint or ""))
    assert not passed(found)


def test_warnings_are_for_a_person_to_weigh_and_do_not_fail_preflight() -> None:
    older = rows(replace(HEALTHY, bluez_version="5.58"))
    assert (
        by_name(older)["bluetoothd"].level == "warn"
        and "20 bytes" in by_name(older)["bluetoothd"].detail
    )
    assert passed(older)
    outsider = by_name(rows(replace(HEALTHY, in_bluetooth_group=False)))["permission"]
    assert outsider.level == "warn" and outsider.hint == "sudo usermod -aG bluetooth $USER"
    public = by_name(rows(replace(HEALTHY, main_conf="")))["address privacy"]
    assert public.level == "warn" and "does not stop tracking" in public.detail
    unread = by_name(rows(replace(HEALTHY, main_conf=None)))["address privacy"]
    assert "could not be read" in unread.detail
    private = by_name(rows(replace(HEALTHY, adapter=replace(ADAPTER, address_type="random"))))
    assert private["address privacy"].level == "ok"


def test_the_lan_row_says_when_robots_can_only_be_met_over_bluetooth() -> None:
    assert by_name(rows(replace(HEALTHY, lan_address=None)))["LAN"].level == "warn"
    assert by_name(rows(replace(HEALTHY, lan_address="127.0.0.1")))["LAN"].level == "warn"
    nozc = by_name(rows(replace(HEALTHY, packages={**PACKAGES, "zeroconf": None})))["LAN"]
    assert nozc.level == "warn" and nozc.hint == ONBOARD_INSTALL


def test_the_sim_and_no_radio_have_rows_of_their_own() -> None:
    sim = Facts(system="Windows", radio="sim", packages=PACKAGES, air="C:/air", air_writable=False)
    air = by_name(rows(sim))["sim air"]
    assert air.level == "fail" and "--air" in (air.hint or "")
    off = by_name(rows(Facts(system="Linux", radio="off", packages=PACKAGES)))["radio"]
    assert off.level == "info" and "LAN only" in off.detail


def test_rows_render_aligned_with_a_step_under_each_that_is_not_fine() -> None:
    found = rows(replace(HEALTHY, rfkill=True))
    lines = render(found)
    assert any(line.startswith("fail  rfkill") for line in lines)
    step = lines[next(i for i, line in enumerate(lines) if "rfkill " in line) + 1]
    assert step.strip() == "-> rfkill unblock bluetooth"
    # every finding starts in one column, after the longest name
    column = 4 + 2 + max(len(r.name) for r in found) + 2
    findings = [line for line in lines if not line.lstrip().startswith("->")]
    assert [line[column:] for line in findings] == [r.detail for r in found]
    assert not any("->" in line for line in render([Row("x", "ok", "fine", "never shown")]))


def test_collect_reads_this_machine_and_changes_nothing(tmp_path: Path) -> None:
    sim = asyncio.run(collect(radio="sim", air=tmp_path / "air"))
    assert sim.system == platform.system() and sim.air_writable is True
    assert set(sim.packages) == {"zeroconf", "openai", "bleak", "dbus-fast"}
    assert [p.name for p in (tmp_path / "air").iterdir()] == []  # its probe file is gone again
    assert render(rows(sim))
    if platform.system() != "Linux":
        bluez = asyncio.run(collect(radio="bluez"))
        assert bluez.bus == "ok" and bluez.adapter is None
        assert by_name(rows(bluez))["bluetooth"].level == "fail"
