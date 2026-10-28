"""The radio's seam: `open_radio` picks a radio or says plainly why this machine cannot have it,
and the stand-in a robot keeps instead never fails to start and never pretends to dial."""

from __future__ import annotations

import asyncio
import dataclasses
import importlib.util
import platform
import sys

import pytest

import quackd.radio as radio
from quackd.radio import (
    ONBOARD_INSTALL,
    NoRadio,
    RadioStatus,
    RadioUnavailable,
    Sighting,
    open_radio,
)


def test_off_is_a_radio_that_starts_hears_nothing_and_refuses_to_dial() -> None:
    off = open_radio("off")
    assert isinstance(off, NoRadio)
    heard: list[Sighting] = []

    async def go() -> RadioStatus:
        status = await off.start(advert=bytes(10), on_sighting=heard.append, on_link=print)
        await off.set_advert(bytes(10))
        with pytest.raises(RadioUnavailable) as refused:
            await off.connect("sim:anybody", timeout=1.0)
        assert refused.value.state == "disabled"
        await off.stop()
        return status

    status = asyncio.run(go())
    assert status.state == "disabled" and not status.ready and not status.hears
    assert not (status.advertising or status.scanning or status.accepting)
    assert "LAN" in status.detail
    assert heard == []


def test_a_radio_that_could_not_be_had_leaves_a_stand_in_that_says_why() -> None:
    error = RadioUnavailable("not-installed", "no bleak here", ONBOARD_INSTALL)
    stand_in = NoRadio.instead_of(error, kind="bluez")
    status = stand_in.status()
    assert (status.state, status.kind, status.detail, status.hint) == (
        "not-installed",
        "bluez",
        "no bleak here",
        ONBOARD_INSTALL,
    )
    assert asyncio.run(stand_in.start(advert=bytes(10), on_sighting=print, on_link=print)) == status


def test_bluez_off_linux_is_refused_naming_the_platform_and_the_sim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    with pytest.raises(RadioUnavailable) as refused:
        open_radio("bluez")
    assert refused.value.state == "unsupported"
    assert "Linux" in str(refused.value) and "Windows" in str(refused.value)
    assert "--board sim" in (refused.value.hint or "")


def test_bluez_without_the_extra_names_the_extra_and_imports_nothing_bluetooth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    real = importlib.util.find_spec
    monkeypatch.setattr(
        importlib.util,
        "find_spec",
        lambda name, *a: None if name in radio.BLUEZ_PACKAGES else real(name, *a),
    )
    before = set(sys.modules)
    with pytest.raises(RadioUnavailable) as refused:
        open_radio("bluez")
    assert refused.value.state == "not-installed"
    assert refused.value.hint == ONBOARD_INSTALL == 'uv tool install "quackd[onboard]"'
    for name in radio.BLUEZ_PACKAGES:
        assert name in str(refused.value)
    assert not [m for m in set(sys.modules) - before if m.startswith("quackd.radio.bluez")]


def test_a_radio_nobody_wrote_is_a_value_error() -> None:
    with pytest.raises(ValueError, match="bluez, sim, fake or off"):
        open_radio("wifi")  # type: ignore[arg-type]


def test_what_a_radio_reports_is_plain_frozen_data() -> None:
    seen = Sighting("00112233aabbccdd", 0x03, -61.5, "sim:b", 12.0)
    assert seen.true_m is None
    with pytest.raises(dataclasses.FrozenInstanceError):
        seen.rssi = -40.0  # type: ignore[misc]
    degraded = RadioStatus("degraded", "bluez", scanning=True, detail="advertising refused")
    assert degraded.hears and not degraded.ready
    assert not RadioStatus("degraded", "bluez").hears


def test_on_linux_with_the_extra_bluez_is_the_real_radio_built_and_not_started(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in radio.BLUEZ_PACKAGES:
        pytest.importorskip(name, reason="the onboard extra installs it, on Linux")
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    from quackd.radio.bluez.radio import BlueZRadio

    built = open_radio("bluez", adapter="hci1", bus="session")
    assert isinstance(built, BlueZRadio) and built.kind == "bluez" and built.adapter == "hci1"
    assert built.status().state == "off" and built.status().detail == "not started"


def test_a_central_gone_closes_only_the_links_this_robot_dialled_to_it() -> None:
    for name in radio.BLUEZ_PACKAGES:
        pytest.importorskip(name, reason="the onboard extra installs it, on Linux")
    from quackd.radio.bluez.core import ClientPipe
    from quackd.radio.bluez.radio import BlueZRadio

    class Client:
        def __init__(self, address: str) -> None:
            self.address = address

    built = BlueZRadio(bus="session")
    to_it = ClientPipe(Client("11:22:33:44:55:66"), "rx", "tx", 20)  # type: ignore[arg-type]
    elsewhere = ClientPipe(Client("AA:BB:CC:DD:EE:FF"), "rx", "tx", 20)  # type: ignore[arg-type]
    nameless = ClientPipe(Client(""), "rx", "tx", 20)  # type: ignore[arg-type]
    built._pipes = {to_it, elsewhere, nameless}
    built._device_gone("/org/bluez/hci0/dev_11_22_33_44_55_66")
    assert to_it.closed and not elsewhere.closed and not nameless.closed
