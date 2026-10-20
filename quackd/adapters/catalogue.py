"""The eight robots quackd publishes, as data, importing none of them.

`factory.py` builds robots; this file only says which ones exist. The split matters because
`quackd list-adapters` and `quackd doctor` have to render the whole table on a machine where
none of the adapters is installed, and a table that had to import an adapter to name it could
not. Everything here is a string.

An adapter that is installed also describes itself, through the `quackd.adapters` entry point
group, and a third party's adapter is discovered that way and nowhere else. The eight below
are only the ones quackd ships and supports, listed in a fixed order because the tables that
print them are order sensitive and `tests/test_adapters.py` pins it.
"""

from __future__ import annotations

from dataclasses import dataclass

ENTRY_POINT_GROUP = "quackd.adapters"
"""Where an installed adapter announces itself. One entry per adapter, its name to its
module, and that module carries `describe`, `make`, `implementations` and `conditions`."""


@dataclass(frozen=True)
class AdapterInfo:
    """One robot quackd publishes, said without importing it."""

    name: str
    backends: tuple[str, ...]
    """In order. The first is what `--robot <adapter>` means with no backend after it."""
    status: str
    """The cell `list-adapters` and `doctor` print: how far each backend has actually got."""
    summary: str
    """One line for a message that has to say what this body is, in a sentence."""
    extra: str | None = None
    """The pip extra that makes the SDK backends work, where one is needed."""
    sdk: str | None = None
    """The import name that says whether that extra is installed here."""


OFFICIAL: tuple[AdapterInfo, ...] = (
    # No extra and no SDK probe, even though `mujoco` needs one: these columns ask whether the
    # *adapter* is usable here, and the Microduck's is. Probing for mujoco made `list-adapters`
    # report the whole robot as missing on a machine that can still run sim2d, mock and a real
    # duck. The extra is named in the status line, in the transports table and in `doctor`'s
    # optional extras, which is where a per-backend answer belongs.
    AdapterInfo(
        name="microduck",
        backends=("sim2d", "mujoco", "mock", "jsonrpc"),
        status=(
            "✅ in the package: sim2d, mock · ✅ mujoco (physics, needs quackd[mujoco]) · "
            "🧪 jsonrpc"
        ),
        summary="a 25 cm biped duck from Pollen Robotics",
        extra="quackd[microduck]",
    ),
    AdapterInfo(
        name="lerobot",
        backends=("mock", "real", "mujoco"),
        status=(
            "✅ in the package: mock · ✅ real (one SO-101, driven on 2026-09-15 and again on "
            "2026-09-23; Python 3.12+) · ✅ mujoco (the real backend over a physics model of "
            "the SO-101, needs quackd[lerobot-sim]; passes a seeded grasp sweep on the maker's "
            "model, never compared against the arm)"
        ),
        summary="an SO-101 class desktop arm driven by LeRobot",
        extra="quackd[lerobot]",
        sdk="lerobot",
    ),
    AdapterInfo(
        name="rosbridge",
        backends=("mock", "ws"),
        status=(
            "✅ in the package: mock · 🧪 ws via roslibpy (verified names, never run against "
            "a bridge)"
        ),
        summary="any wheeled base that speaks rosbridge",
        extra="quackd[rosbridge]",
        sdk="roslibpy",
    ),
    # Appended, never inserted: the tables that print these are order-sensitive and
    # tests/test_adapters.py pins the order. No extra: the client is stdlib, and the robot's
    # own runtime is not installable here.
    AdapterInfo(
        name="open_duck",
        backends=("sim2d", "mock", "bridge"),
        status=(
            "✅ in the package: sim2d, mock · 🧪 bridge (quackd's own daemon on the duck's Pi, "
            "never run on a robot)"
        ),
        summary="an Open Duck Mini v2, the one you can build yourself",
        extra="quackd[open_duck]",
    ),
    # XLeRobot is not an installable package, so quackd speaks its ZeroMQ host protocol rather
    # than importing it: the extra is pyzmq and nothing else (ADR-0026).
    AdapterInfo(
        name="xlerobot",
        backends=("mock", "zmq"),
        status=(
            "✅ in the package: mock · 🧪 zmq (wire format VERIFIED at a pinned commit, exercised "
            "against a fake host over loopback, never run on a cart)"
        ),
        summary="a dual-arm wheeled cart",
        extra="quackd[xlerobot]",
        sdk="zmq",
    ),
    # Also not an installable package: a fork of LeRobot that calls itself lerobot and is not
    # on PyPI, so quackd speaks its ZeroMQ host protocol too (ADR-0027).
    AdapterInfo(
        name="alohamini",
        backends=("mock", "sim2d", "zmq"),
        status=(
            "✅ in the package: mock, sim2d · 🧪 zmq (wire format VERIFIED at a pinned commit, "
            "exercised against a fake host over loopback, never run on a robot)"
        ),
        summary="two arms on a lift, on a wheeled base",
        extra="quackd[alohamini]",
        sdk="zmq",
    ),
    # No network API of any kind upstream: no socket, no daemon, no IPC. So quackd ships the
    # daemon, as it does for the Open Duck Mini, and the client is stdlib (ADR-0028).
    AdapterInfo(
        name="toddlerbot",
        backends=("mock", "sim2d", "bridge"),
        status=(
            "✅ in the package: mock, sim2d · 🧪 bridge (quackd's own daemon on the robot, "
            "never run on a robot)"
        ),
        summary="a small open-source humanoid",
        extra="quackd[toddlerbot]",
    ),
    # Huebot's own API is closed, and its maker runs a shim on the robot that answers the
    # Microduck's JSON-RPC, so the client is stdlib and there is no SDK to probe.
    AdapterInfo(
        name="huebot",
        backends=("mock", "jsonrpc"),
        status=(
            "✅ in the package: mock · 🧪 jsonrpc (robotd's protocol, spoken to a shim its maker "
            "runs on the robot, never run on a robot)"
        ),
        summary="a 25 cm biped from Leif Rydenfalk",
        extra="quackd[huebot]",
    ),
)

BY_NAME: dict[str, AdapterInfo] = {info.name: info for info in OFFICIAL}
