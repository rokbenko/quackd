"""The nine robots quackd publishes, as data, importing none of them.

`factory.py` builds robots; this file only says which ones exist. The split matters because
`quackd list-adapters` and `quackd doctor` have to render the whole table on a machine where
none of the adapters is installed, and a table that had to import an adapter to name it could
not. Everything here is a string.

An adapter that is installed also describes itself, through the `quackd.adapters` entry point
group, and a third party's adapter is discovered that way and nowhere else. The nine below
are only the ones quackd ships and supports, listed in a fixed order because the tables that
print them are order sensitive and `tests/test_adapters.py` pins it.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace

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
    connect: Mapping[str, Connect] = field(default_factory=dict)
    """What connecting each real backend takes, by backend: the page `quackd ui` shows it beside
    the address field. A simulator or a mock needs nothing, and has no entry."""


@dataclass(frozen=True)
class Connect:
    """What connecting one real backend takes, said without importing it, in plain words.

    `default` and `token_env` are the transport's own, which a test reads back from it, so this
    can never say a default the code does not use. A third party's adapter may declare a
    `CONNECT` mapping of these, by backend, beside its `BACKENDS`."""

    address: str
    """What `--address` is for this backend, and the shapes it takes."""
    default: str | None
    """The address used when none is given, or None where one must be."""
    example: str
    """A real one, as the adapter's own docs give it."""
    token_env: str | None = None
    """The variable its token is read from where `--token` is not given, or None for none."""
    camera: str = ""
    """What a camera URL is for it, or where its frames come from instead."""
    on_robot: str = ""
    """What has to be running on the robot for a connect to succeed."""
    first: str = ""
    """What to do once, before the first connect."""
    docs: str = ""
    """The adapter's page, from the repository's root."""
    caution: str = ""
    """What a connect does to the body that somebody near it has to know first, said before the
    page offers to check the connection, or nothing."""


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
    # Huebot's own API is closed, and its maker is writing a shim for the robot that is to answer
    # the Microduck's JSON-RPC, so the client is stdlib and there is no SDK to probe.
    AdapterInfo(
        name="huebot",
        backends=("mock", "jsonrpc"),
        status=(
            "✅ in the package: mock · 🧪 jsonrpc (robotd's protocol, spoken to a shim its maker "
            "is writing for the robot, never run on a robot)"
        ),
        summary="a 25 cm biped from Leif Rydenfalk",
        extra="quackd[huebot]",
    ),
    # MOSS runs dimOS on the rover, and its only network surface is its own cockpit. quackd
    # speaks the cockpit, and a bridge of its own on the rover for what the cockpit has no
    # command for, both over HTTP with the standard library (ADR-0056).
    AdapterInfo(
        name="moss",
        backends=("mock", "cockpit"),
        status=(
            "✅ in the package: mock · 🧪 cockpit (MOSS's own cockpit and quackd's own bridge "
            "on the rover, never run against a rover)"
        ),
        summary="a small tracked rover that picks up litter",
        extra="quackd[moss]",
    ),
)

_CONNECT: dict[str, dict[str, Connect]] = {
    "microduck": {
        "jsonrpc": Connect(
            address="robotd's socket: `unix:///PATH` with quackd on the duck itself, or "
            "`tcp://HOST:PORT` for the socket forwarded over ssh",
            default="unix:///run/robotd.sock",
            example="tcp://127.0.0.1:9870",
            camera="One camera URL: mediad's `http://HOST:8080/frame`, `webrtc://HOST:8443` with "
            "`quackd[microduck-camera]`, or any HTTP snapshot. Without one the duck runs blind.",
            on_robot="Pollen's robotd, at API 23 or later, and mediad for the camera. quackd "
            "installs nothing on the duck.",
            first="Forward the socket with `ssh -L 9870:/run/robotd.sock microduck@DUCK` and use "
            "`tcp://127.0.0.1:9870`, always on Windows, which refuses a unix socket. The ssh "
            "account must be in the group `robot`.",
            docs="docs/adapters/microduck/README.md",
        ),
    },
    "lerobot": {
        "real": Connect(
            address="the arm's USB serial port on this computer: `COM5` or the like on Windows, "
            "`/dev/ttyACM0` elsewhere",
            default=None,
            example="COM5",
            camera="Any number of cameras, as `opencv://N?name=front`, each with a name of its "
            "own.",
            on_robot="Nothing: the SO-101 is on USB, and LeRobot runs inside quackd.",
            first="Python 3.12 and `quackd[lerobot]`. LeRobot's calibration, `lerobot-calibrate "
            "--robot.type=so101_follower --robot.port=COM5 --robot.id=arm-01`, with the id the "
            "robot's registered name. Then `quackd robot add arm-01 lerobot:real --address COM5` "
            "and `quackd robot rest-pose arm-01`.",
            docs="docs/adapters/lerobot/README.md",
            caution="Connecting takes torque off every motor for a moment, as any connect does, "
            "a check included: hold an arm that is holding itself up before you connect it.",
        ),
    },
    "rosbridge": {
        "ws": Connect(
            address="rosbridge_server's WebSocket, which carries the topics too: "
            "`ws://HOST:9090?cmd_vel=/cmd_vel&odom=/odom&image=/camera/image/compressed`",
            default="ws://localhost:9090",
            example="ws://robot.local:9090?cmd_vel=/cmd_vel&odom=/odom&image=/camera/image/compressed",
            camera="An image topic in the address, as `?image=`. A camera URL is not read.",
            on_robot="ROS 2 with rosbridge_server and rosapi, on a base that takes "
            "`geometry_msgs/msg/Twist` and publishes odometry.",
            docs="docs/adapters/rosbridge/README.md",
        ),
    },
    "open_duck": {
        "bridge": Connect(
            address="quackd's bridge daemon on the duck's Pi, `tcp://HOST:PORT`",
            default="tcp://open-duck.local:9871",
            example="tcp://127.0.0.1:9871",
            token_env="QUACKD_DUCK_TOKEN",
            camera="The snapshot the bridge names, `http://HOST:9872/snapshot.jpg`, unless a "
            "camera URL is given.",
            on_robot="quackd's bridge and camera daemons, installed by "
            "`bridge/open_duck/install.sh` and started from their systemd units.",
            first="`ssh -L 9871:127.0.0.1:9871 -L 9872:127.0.0.1:9872 PI`, and the token from "
            "`/etc/quackd/duck-bridge.token` on the Pi.",
            docs="docs/adapters/open_duck/README.md",
        ),
    },
    "xlerobot": {
        "zmq": Connect(
            address="the robot's ZeroMQ host, `tcp://HOST:5555`, with `?variant=omni3`, `diff2` "
            "or `mecanum`",
            default="tcp://127.0.0.1:5555",
            example="tcp://192.168.1.42:5555",
            camera="From the host's observations. A camera URL is not read.",
            on_robot="Upstream's XLerobotHost, uncommented from its package, on the machine wired "
            "to both buses. It exits after an hour.",
            first="Start the host, restart it each hour, and reach it through an ssh tunnel.",
            docs="docs/adapters/xlerobot/README.md",
        ),
    },
    "alohamini": {
        "zmq": Connect(
            address="the robot's ZeroMQ host, `tcp://HOST:5555`, with `?model=alohamini1`, "
            "`alohamini2` or `alohamini2pro`",
            default="tcp://127.0.0.1:5555",
            example="tcp://192.168.1.50:5555",
            camera="From the host's replies. A camera URL is not read.",
            on_robot="quackd's host wrapper from `bridge/alohamini`, or upstream's host.",
            first="Calibrate the robot, install upstream on it, start the host, and reach it "
            "through an ssh tunnel.",
            docs="docs/adapters/alohamini/README.md",
        ),
    },
    "toddlerbot": {
        "bridge": Connect(
            address="quackd's daemon on the robot's Jetson, `tcp://HOST:PORT`",
            default="tcp://toddlerbot.local:9873",
            example="tcp://127.0.0.1:9873",
            token_env="QUACKD_TODDLERBOT_TOKEN",
            camera="From the daemon, started with `--camera left` or `right`. A camera URL is not "
            "read.",
            on_robot="quackd's daemon from `bridge/toddlerbot`, in upstream's environment.",
            first="Upstream's `calibrate_zero`, the robot on its stand, an ssh tunnel to 9873, "
            "and the same token on both sides.",
            docs="docs/adapters/toddlerbot/README.md",
        ),
    },
    "huebot": {
        "jsonrpc": Connect(
            address="the maker's shim, which speaks robotd's protocol: `tcp://HOST:PORT`, or "
            "`unix:///PATH` with quackd on the robot itself",
            default=None,
            example="tcp://127.0.0.1:9870",
            camera="Any HTTP URL that answers each request with one still image.",
            on_robot="The maker's shim.",
            first="Forward the shim with `ssh -L 9870:SOCKET HUEBOT` and use "
            "`tcp://127.0.0.1:9870`.",
            docs="docs/adapters/huebot/README.md",
        ),
    },
    "moss": {
        "cockpit": Connect(
            address="MOSS's cockpit on the rover, `http://HOST:8904`, with quackd's bridge port "
            "as `?bridge=9876`",
            default="http://127.0.0.1:8904?bridge=9876",
            example="http://127.0.0.1:8904",
            token_env="QUACKD_MOSS_TOKEN",
            camera="None to give: the nose and wrist cameras come from the rover.",
            on_robot="The MOSS stack with its cockpit, and quackd's bridge from `bridge/moss` as "
            "the `quackd-moss-bridge` service.",
            first="`ssh -L 8904:127.0.0.1:8904 -L 9876:127.0.0.1:9876 ROVER`, and the token from "
            "`/etc/quackd/moss-bridge.token` on the rover.",
            docs="docs/adapters/moss/README.md",
        ),
    },
}
"""What connecting each real backend of the nine takes (`AdapterInfo.connect`)."""

OFFICIAL = tuple(replace(info, connect=_CONNECT.get(info.name, {})) for info in OFFICIAL)

BY_NAME: dict[str, AdapterInfo] = {info.name: info for info in OFFICIAL}
