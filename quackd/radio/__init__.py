"""The radio a robot hears other robots on: Bluetooth LE on its own computer, or a stand-in.

Robot Passport on a robot's own board starts with the radio. Two robots that come close hear
each other's adverts, and how loud decides that they are near (`proximity.py`); when they share
no network, a link over the same radio carries their talk (`links.py`). What the radio never
decides is who a robot is. An advert is ten bytes anybody can send and a signal strength a relay
can fake, so the passport and its signatures settle that, exactly as they do over a LAN.

This module is the seam and nothing more: what a radio reports (`Sighting`, `RadioStatus`), what
it does (`Radio`, `Pipe`), and `open_radio`, which picks one. The real radio is BlueZ, Linux's
Bluetooth stack, over D-Bus (`bluez/`). It is imported only by `open_radio("bluez")`, only on
Linux and only where the `onboard` extra installed bleak and dbus-fast, so a default install and
every other platform import none of it. The sim radio (`sim.py`) carries the same advert bytes
and the same link frames between processes on one laptop, and the fake (`fake.py`) between
objects in one test. None of it has run over a real radio.
"""

from __future__ import annotations

import importlib.util
import platform
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal, Protocol

RadioState = Literal[
    "ready", "degraded", "off", "missing", "denied", "not-installed", "unsupported", "disabled"
]
RadioKind = Literal["bluez", "sim", "fake", "off"]

#: The packages the Bluetooth half needs, as `import` spells them, all from the `onboard` extra.
BLUEZ_PACKAGES = ("bleak", "dbus_fast")
ONBOARD_INSTALL = 'uv tool install "quackd[onboard]"'
#: At most one new advert this often. BlueZ changes an advert by registering it again, so a robot
#: that rotated its beacon id and flipped a flag in one second would register twice; a radio
#: keeps the last advert it was handed and applies it when this much time has passed.
ADVERT_EVERY_S = 5.0


@dataclass(frozen=True)
class Sighting:
    """One advert heard: the beacon id it carried, its flags, how loud it was, where it came
    from, and when.

    `beacon_id` is 16 lower-case hex characters, the advert's 8 random bytes, which rotate and
    prove nothing: anybody can send any. `address` is what `Radio.connect` takes to open a link
    back, a Bluetooth address or `sim:<token>`. `at` is this robot's `time.monotonic()`.
    `true_m` is how far apart the sim placed the two robots, for its narration and nothing else:
    no real radio knows it, so nothing may decide on it."""

    beacon_id: str
    flags: int
    rssi: float
    address: str
    at: float
    true_m: float | None = None


@dataclass(frozen=True)
class RadioStatus:
    """What a radio is doing, in words a person can act on.

    `state` is `ready` when it advertises, scans and accepts links as it was asked to,
    `degraded` when some of that failed (`detail` says which), and otherwise why it does nothing:
    `off` (powered off or blocked), `missing` (no adapter, or no bluetoothd), `denied` (D-Bus
    said no), `not-installed` (no `onboard` extra), `unsupported` (not Linux, or a controller
    that cannot be a peripheral) or `disabled` (`--radio off`). `hint` is the one command or
    step that would change it, when there is one."""

    state: RadioState
    kind: str
    adapter: str | None = None
    address: str | None = None
    #: `public` or `random`, as BlueZ reports the adapter's own address; a public address is
    #: the same on every advert, which a rotating beacon id does nothing about
    address_type: str | None = None
    advertising: bool = False
    scanning: bool = False
    accepting: bool = False
    links: int = 0
    detail: str = ""
    hint: str | None = None

    @property
    def ready(self) -> bool:
        return self.state == "ready"

    @property
    def hears(self) -> bool:
        """Whether sightings can arrive at all: a degraded radio may still scan."""
        return self.state in ("ready", "degraded") and self.scanning


class RadioUnavailable(Exception):
    """A radio that cannot be had here: the reason, as a `RadioStatus` state and a sentence,
    and the step that would change it. `open_radio` raises it for a radio this machine cannot
    have; `Radio.start` never does, and reports the same thing in the status it returns."""

    def __init__(self, state: RadioState, detail: str, hint: str | None = None) -> None:
        super().__init__(detail)
        self.state: RadioState = state
        self.detail = detail
        self.hint = hint


class PipeClosed(ConnectionError):
    """A pipe that closed, from either end, before a datagram could go on it."""


class Pipe(Protocol):
    """One connection between two robots' radios, carrying datagrams of at most
    `datagram_size` bytes each way, in order: a GATT link over Bluetooth, a loopback socket in
    the sim, a pair of queues in a test. What the datagrams mean is `quackd.passport.link`'s.

    `send` with `barrier=True` returns once the other end's stack took the datagram (a GATT
    write request), and with `barrier=False` as soon as it is queued (a write without
    response): a message's last datagram is a barrier, so a slow peer pushes back once a
    message rather than once a datagram. Callbacks run on the radio's event loop."""

    datagram_size: int

    async def send(self, datagram: bytes, *, barrier: bool = True) -> None: ...

    def on_datagram(self, cb: Callable[[bytes], None]) -> None: ...

    def on_close(self, cb: Callable[[str], None]) -> None: ...

    async def close(self) -> None: ...


class Radio(Protocol):
    """A robot's radio: it advertises one advert, reports every advert it hears, accepts links
    from robots that dial it and dials robots it heard.

    `advert` is the service data of `quackd.passport.beacon.ble_service_data`, ten bytes; the
    radio wraps it however its air needs. `start` never raises for want of Bluetooth: it returns
    a status that says what is missing, and a robot whose radio is not ready still talks over its
    LAN. Every callback and every method runs on one event loop, the radio's
    (`quackd.radio.loop.LoopThread`)."""

    kind: str

    async def start(
        self,
        *,
        advert: bytes,
        on_sighting: Callable[[Sighting], None],
        on_link: Callable[[Pipe, str], None],
    ) -> RadioStatus: ...

    async def set_advert(self, advert: bytes) -> None: ...

    # A timeout the radio hands its own stack rather than a cancellation from outside: bleak
    # tears down a BlueZ connection that timed out itself, and one cancelled half way through
    # can leave the device connecting in bluetoothd after the caller has given up on it.
    async def connect(self, address: str, *, timeout: float) -> Pipe: ...  # noqa: ASYNC109

    async def stop(self) -> None: ...

    def status(self) -> RadioStatus: ...


class NoRadio:
    """A radio that hears nothing and is heard by nobody: `--radio off`, or what a robot keeps
    in place of a radio it could not have, so that its LAN half goes on and its status says
    why. Starting it never fails; dialling anybody always does."""

    def __init__(
        self,
        state: RadioState = "disabled",
        detail: str = "the radio is off, as asked: this robot meets others over its LAN only",
        hint: str | None = None,
        *,
        kind: str = "off",
    ) -> None:
        self.kind = kind
        self._status = RadioStatus(state=state, kind=kind, detail=detail, hint=hint)

    @classmethod
    def instead_of(cls, error: RadioUnavailable, *, kind: str) -> NoRadio:
        """The stand-in for a radio `open_radio(kind)` refused, saying why it was refused."""
        return cls(error.state, error.detail, error.hint, kind=kind)

    async def start(
        self,
        *,
        advert: bytes,
        on_sighting: Callable[[Sighting], None],
        on_link: Callable[[Pipe, str], None],
    ) -> RadioStatus:
        return self._status

    async def set_advert(self, advert: bytes) -> None:
        return None

    async def connect(self, address: str, *, timeout: float) -> Pipe:  # noqa: ASYNC109
        raise RadioUnavailable(self._status.state, self._status.detail, self._status.hint)

    async def stop(self) -> None:
        return None

    def status(self) -> RadioStatus:
        return self._status


def open_radio(kind: RadioKind, **options: Any) -> Radio:
    """The radio called `kind`, built and not yet started.

    `off` is a `NoRadio`. `bluez` is the real one, and a machine that cannot have it is told so
    by a `RadioUnavailable` before anything Bluetooth is imported: not Linux, or no `onboard`
    extra. `sim` is the sim air on this machine (`quackd.radio.sim.SimOptions`, as `options=`
    or as keywords), and `fake` a radio on a test's `quackd.radio.fake.FakeAir` (`air=`,
    `name=`, `at=`). `options` go to the radio's constructor."""
    if kind == "off":
        return NoRadio()
    if kind == "bluez":
        return _bluez(**options)
    if kind == "sim":
        from quackd.radio.sim import SimRadio

        return SimRadio(**options)
    if kind == "fake":
        from quackd.radio.fake import FakeAir

        air = options.pop("air", None)
        if not isinstance(air, FakeAir):
            raise TypeError("a fake radio is on a FakeAir: pass air=")
        return air.radio(**options)
    raise ValueError(f"there is no radio called {kind!r}: bluez, sim, fake or off")


def _bluez(**options: Any) -> Radio:
    # platform.system() rather than sys.platform: mypy reads a sys.platform test as a platform
    # guard and skips what follows it on every other platform, and this is the one place the
    # Bluetooth half is reached from, so it has to be checked everywhere
    system = platform.system()
    if system != "Linux":
        raise RadioUnavailable(
            "unsupported",
            f"Bluetooth on a robot is BlueZ, which runs on Linux, and this machine runs "
            f"{system or 'something else'}",
            "to try quackd onboard here, run it with --board sim, whose radio is simulated",
        )
    missing = [name for name in BLUEZ_PACKAGES if importlib.util.find_spec(name) is None]
    if missing:
        raise RadioUnavailable(
            "not-installed",
            f"the Bluetooth half needs {' and '.join(missing)}, which the onboard extra installs",
            ONBOARD_INSTALL,
        )
    raise RadioUnavailable(
        "unsupported",
        "this build of quackd has no BlueZ radio yet: it meets other robots over its LAN only",
    )


__all__ = [
    "ADVERT_EVERY_S",
    "BLUEZ_PACKAGES",
    "ONBOARD_INSTALL",
    "NoRadio",
    "Pipe",
    "PipeClosed",
    "Radio",
    "RadioKind",
    "RadioState",
    "RadioStatus",
    "RadioUnavailable",
    "Sighting",
    "open_radio",
]
