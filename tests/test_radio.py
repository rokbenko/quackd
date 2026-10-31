"""The radio's seam: `open_radio` picks a radio or says plainly why this machine cannot have it,
and the stand-in a robot keeps instead never fails to start and never pretends to dial."""

from __future__ import annotations

import asyncio
import dataclasses
import importlib.util
import platform
import sys
from typing import Any

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


def _bluez_radio() -> object:
    for name in radio.BLUEZ_PACKAGES:
        pytest.importorskip(name, reason="the onboard extra installs it, on Linux")
    from quackd.radio.bluez.radio import BlueZRadio

    return BlueZRadio(bus="session")


async def test_every_advert_handed_in_while_one_registers_goes_out_the_newest_last(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    built: Any = _bluez_radio()
    import quackd.radio.bluez.radio as bluez_radio

    monkeypatch.setattr(bluez_radio, "ADVERT_EVERY_S", 0.0)
    gate, registered = asyncio.Event(), []

    class Peripheral:
        async def advertise(self, advert: bytes) -> None:
            registered.append(advert)
            await gate.wait()

    built._peripheral = Peripheral()
    await built.set_advert(b"first")
    await asyncio.sleep(0.01)  # the first is with BlueZ now
    await built.set_advert(b"second")
    await built.set_advert(b"third")
    gate.set()
    await asyncio.wait_for(built._advert_task, 2)
    assert registered == [b"first", b"third"]


async def test_dials_at_once_pause_the_scanner_once_and_the_last_starts_it_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    built: Any = _bluez_radio()
    import quackd.radio.bluez.radio as bluez_radio

    calls: list[str] = []
    gates = [asyncio.Event(), asyncio.Event()]

    class Scanner:
        running = True
        devices: dict[str, object] = {}

        async def stop(self) -> None:
            calls.append("stop")
            self.running = False

        async def start(self) -> None:
            calls.append("start")
            self.running = True

    async def open_pipe(target: str, *, adapter: str, timeout: float) -> object:  # noqa: ASYNC109
        await gates[int(target)].wait()
        calls.append(f"open {target}")
        raise OSError("no one there")

    monkeypatch.setattr(bluez_radio, "open_pipe", open_pipe)
    built._scanner, built._bus = Scanner(), object()
    built._status = radio.RadioStatus("ready", "bluez")
    first = asyncio.create_task(built.connect("0", timeout=1))
    second = asyncio.create_task(built.connect("1", timeout=1))
    await asyncio.sleep(0.01)
    gates[0].set()
    with pytest.raises(ConnectionError):
        await first
    assert calls == ["stop", "open 0"]  # the second dial is still under way: still paused
    gates[1].set()
    with pytest.raises(ConnectionError):
        await second
    assert calls == ["stop", "open 0", "open 1", "start"]


async def test_closing_a_link_a_robot_opened_never_drops_one_this_robot_dialled_to_it() -> None:
    from quackd.radio.bluez.core import ClientPipe

    built: Any = _bluez_radio()
    asked: list[str] = []

    class Peripheral:
        async def disconnect(self, device: str) -> None:
            asked.append(device)

    class Client:
        address = "11:22:33:44:55:66"

    built._peripheral = Peripheral()
    built._pipes = {ClientPipe(Client(), "rx", "tx", 20)}  # type: ignore[arg-type]
    built._disconnect("/org/bluez/hci0/dev_11_22_33_44_55_66")
    built._disconnect("/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF")
    await asyncio.sleep(0)
    assert asked == ["/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF"]
