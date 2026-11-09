"""The ten robots quackd publishes and the bodies it only describes, as data, importing none.

`factory.py` builds robots; this file only says which ones exist. The split matters because
`quackd list-adapters` and `quackd doctor` have to render the whole table on a machine where
none of the adapters is installed, and a table that had to import an adapter to name it could
not. Everything here is a string.

An adapter that is installed also describes itself, through the `quackd.adapters` entry point
group, and a third party's adapter is discovered that way and nowhere else. The ten below
are only the ones quackd ships and supports, listed in a fixed order because the tables that
print them are order sensitive and `tests/test_adapters.py` pins it. The last row is not an
adapter: it lists the bodies quackd describes from one YAML each and drives no part of
(ADR-0077), so that `--robot described:<name>` parses, registers and describes like any other.

Every backend carries a tier (ADR-0076): described, rehearsed, bound or verified, with an owner
and a date where the rule asks for one. The marks the tables print come from the tier, so a ✅
can never sit on a backend nobody has run.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Literal

ENTRY_POINT_GROUP = "quackd.adapters"
"""Where an installed adapter announces itself. One entry per adapter, its name to its
module, and that module carries `describe`, `make`, `implementations` and `conditions`."""

TierName = Literal["described", "rehearsed", "bound", "verified"]
TIERS: tuple[TierName, ...] = ("described", "rehearsed", "bound", "verified")
"""How far a backend has got, in one word (ADR-0076). `described`: a body quackd lists and
reasons about from one YAML and drives no part of. `rehearsed`: a mock or a simulator, run in
the suite. `bound`: a backend that reaches a real robot, whose upstream names were read at a
pin, and that has never met one. `verified`: run on hardware, by a named person, on a date."""

MARK: dict[str, str] = {"described": "📄", "rehearsed": "✅", "bound": "🧪", "verified": "🤖"}
"""The mark each tier prints with, the same in every table that prints one."""

Kind = Literal["adapter", "described"]
Family = Literal["arm", "biped", "wheeled", "humanoid", "described"]
FAMILIES: tuple[Family, ...] = ("arm", "biped", "wheeled", "humanoid", "described")
"""The behaviour families bodies share code by (ADR-0075); `described` is the kind of body
that shares none, since nothing drives it."""
Route = Literal["sdk", "wire", "bridge"]
ROUTES: tuple[Route, ...] = ("sdk", "wire", "bridge")
"""How an intent reaches the robot: an SDK quackd imports, a wire quackd speaks, or a bridge
quackd ships for the robot's own computer."""

RULE_ADOPTED = "2026-11-05"
"""The day ADR-0076's rule was adopted: the date every grandfathered backend carries."""
GRANDFATHERED = "grandfathered 0.33.0"
"""The owner of a bound backend that was here before the rule: a statement that nobody has
named a date, not a date."""


@dataclass(frozen=True)
class Tier:
    """One backend's tier, and who answers for it.

    `owner` and `date` are who answers for a bound backend's first hardware run and when, or who
    ran a verified one and when; both None on a rehearsed or described backend. `note` is what
    the status cell used to say of this backend alone: what it needs, what it has met."""

    name: TierName
    owner: str | None = None
    date: str | None = None
    note: str = ""

    @property
    def mark(self) -> str:
        return MARK[self.name]

    def to_dict(self) -> dict[str, str | None]:
        return {"tier": self.name, "owner": self.owner, "date": self.date, "note": self.note}

    @classmethod
    def from_dict(cls, data: Mapping[str, str | None]) -> Tier:
        """The record `to_dict` wrote, read back: what a table is handed in a `--json` row."""
        name = data.get("tier")
        if name not in TIERS:
            raise ValueError(f"no tier is called {name!r}")
        return cls(name, data.get("owner"), data.get("date"), data.get("note") or "")


@dataclass(frozen=True)
class AdapterInfo:
    """One robot quackd publishes, said without importing it, or the row of bodies it only
    describes (`kind == "described"`)."""

    name: str
    backends: tuple[str, ...]
    """In order. The first is what `--robot <adapter>` means with no backend after it."""
    status: str
    """The cell `list-adapters` and `doctor` print: how far each backend has actually got. A row
    that carries tiers has no status of its own: whatever is passed, it is the tiers rendered
    (`status_line`), so the two can never disagree."""
    summary: str
    """One line for a message that has to say what this body is, in a sentence."""
    extra: str | None = None
    """The pip extra that makes the SDK backends work, where one is needed."""
    sdk: str | None = None
    """The import name that says whether that extra is installed here."""
    connect: Mapping[str, Connect] = field(default_factory=dict)
    """What connecting each real backend takes, by backend: the page `quackd ui` shows it beside
    the address field. A simulator or a mock needs nothing, and has no entry."""
    kind: Kind = "adapter"
    """`adapter` for a package that drives a robot; `described` for the one row whose backends
    are bodies quackd describes and drives no part of."""
    tiers: Mapping[str, Tier] = field(default_factory=dict)
    """Each backend's tier, by backend. Empty only on a third party's row that declares none."""
    family: tuple[str, ...] = ()
    """The behaviour family, first the body's own and then its arms' where it has arms of a
    kind another family drives: `("wheeled", "arm")` for a cart with two arms."""
    route: tuple[str, ...] = ()
    """How its real backends reach the robot, among `ROUTES`; two where a body takes two."""
    robot_types: tuple[str, ...] = ()
    """The kinds of robot one adapter drives through one SDK, which `quackd robot add
    --robot-type` chooses among, the default first: LeRobot's robot types on the arm, the four body
    files on rosarm. Only the ones it builds on some backend, so a type it describes and builds
    nowhere is never registered, and a backend that refuses one is refused at the door too
    (`factory.refusal`). Empty on every adapter whose body is one kind (ADR-0075)."""

    def __post_init__(self) -> None:
        if not self.tiers:
            return
        untiered = [backend for backend in self.backends if backend not in self.tiers]
        if untiered:
            raise ValueError(f"{self.name}: no tier for {', '.join(untiered)}")
        for backend, tier in self.tiers.items():
            if tier.name not in TIERS:
                raise ValueError(f"{self.name}:{backend} has no tier called {tier.name!r}")
        # a row rebuilt with fewer backends (`dataclasses.replace`) keeps the tiers of those it
        # kept, in the backends' own order, and says what those say
        object.__setattr__(self, "tiers", {b: self.tiers[b] for b in self.backends})
        object.__setattr__(self, "status", status_line(self))


def tier_groups(backends: tuple[str, ...], tiers: Mapping[str, Tier]) -> list[str]:
    """One string per tier a row has a backend in: verified first, then rehearsed, bound and
    described, each tier's backends in the row's own order, with each backend's note and who
    answers for it in brackets after it.

    `✅ rehearsed: sim2d, mock` and `🧪 bound: jsonrpc (never run on a duck; grandfathered
    0.33.0)`. A grandfathered backend prints no date, since its date is the day the rule was
    adopted and says nothing of that backend. A tier that holds every backend of the row names
    none of them, which is the described row: its backends are already the column beside it."""
    groups: list[str] = []
    for tier_name in ("verified", "rehearsed", "bound", "described"):
        named = [b for b in backends if tiers[b].name == tier_name]
        if not named:
            continue
        if len(named) == len(backends) and not any(_said(tiers[b]) for b in named):
            groups.append(f"{MARK[tier_name]} {tier_name}")
            continue
        parts = []
        for backend in named:
            said = _said(tiers[backend])
            parts.append(backend + (f" ({'; '.join(said)})" if said else ""))
        groups.append(f"{MARK[tier_name]} {tier_name}: {', '.join(parts)}")
    return groups


def _said(tier: Tier) -> list[str]:
    """What a tier says of its backend beyond its name: the note, then who answers for it."""
    said = [tier.note] if tier.note else []
    if tier.owner == GRANDFATHERED:
        said.append(GRANDFATHERED)
    elif tier.owner or tier.date:
        said.append(", ".join(p for p in (tier.owner, tier.date) if p))
    return said


def status_line(info: AdapterInfo) -> str:
    """The status cell as one line, rendered from the tiers (`tier_groups`), which is what the
    `status` key of a `--json` row carries. The tables print the same groups one to a line."""
    return " · ".join(tier_groups(info.backends, info.tiers))


def _rehearsed(note: str = "") -> Tier:
    return Tier("rehearsed", note=note)


def _bound(note: str, owner: str = GRANDFATHERED, date: str = RULE_ADOPTED) -> Tier:
    return Tier("bound", owner=owner, date=date, note=note)


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
        status="",
        summary="a 25 cm biped duck from Pollen Robotics",
        extra="quackd[microduck]",
        tiers={
            "sim2d": _rehearsed(),
            "mujoco": _rehearsed("physics, needs quackd[mujoco]"),
            "mock": _rehearsed(),
            "jsonrpc": _bound("every method VERIFIED at a pin, never run on a duck"),
        },
        family=("biped",),
        route=("wire",),
    ),
    AdapterInfo(
        name="lerobot",
        backends=("mock", "real", "mujoco"),
        status="",
        summary="an SO-101 class desktop arm driven by LeRobot",
        extra="quackd[lerobot]",
        sdk="lerobot",
        # the Koch is described in the adapter and not here: nothing is registered as one
        robot_types=("so101_follower", "so100_follower"),
        tiers={
            "mock": _rehearsed(),
            "real": Tier(
                "verified",
                owner="rok",
                date="2026-09-15",
                note="one SO-101, driven on LeRobot 0.6.1 on 2026-09-15 and again on 2026-09-23, "
                "and LeRobot 0.7.0's bus has not met it; an SO-100 runs the same code and none "
                "has; Python 3.12+",
            ),
            "mujoco": _rehearsed(
                "the real backend over a physics model of the SO-101, needs "
                "quackd[lerobot-sim]; passes a seeded grasp sweep on the maker's model, never "
                "compared against the arm"
            ),
        },
        family=("arm",),
        route=("sdk",),
    ),
    AdapterInfo(
        name="rosbridge",
        backends=("mock", "ws"),
        status="",
        summary="any wheeled base that speaks rosbridge",
        extra="quackd[rosbridge]",
        sdk="roslibpy",
        tiers={
            "mock": _rehearsed(),
            "ws": _bound("via roslibpy, verified names, never run against a bridge"),
        },
        family=("wheeled",),
        route=("sdk",),
    ),
    # Appended, never inserted: the tables that print these are order-sensitive and
    # tests/test_adapters.py pins the order. No extra: the client is stdlib, and the robot's
    # own runtime is not installable here.
    AdapterInfo(
        name="open_duck",
        backends=("sim2d", "mock", "bridge"),
        status="",
        summary="an Open Duck Mini v2, the one you can build yourself",
        extra="quackd[open_duck]",
        tiers={
            "sim2d": _rehearsed(),
            "mock": _rehearsed(),
            "bridge": _bound("quackd's own daemon on the duck's Pi, never run on a robot"),
        },
        family=("biped",),
        route=("bridge",),
    ),
    # XLeRobot is not an installable package, so quackd speaks its ZeroMQ host protocol rather
    # than importing it: the extra is pyzmq and nothing else (ADR-0026).
    AdapterInfo(
        name="xlerobot",
        backends=("mock", "zmq"),
        status="",
        summary="a dual-arm wheeled cart",
        extra="quackd[xlerobot]",
        sdk="zmq",
        tiers={
            "mock": _rehearsed(),
            "zmq": _bound(
                "wire format VERIFIED at a pinned commit, exercised against a fake host over "
                "loopback, never run on a cart"
            ),
        },
        family=("wheeled", "arm"),
        route=("wire",),
    ),
    # Also not an installable package: a fork of LeRobot that calls itself lerobot and is not
    # on PyPI, so quackd speaks its ZeroMQ host protocol too (ADR-0027).
    AdapterInfo(
        name="alohamini",
        backends=("mock", "sim2d", "zmq"),
        status="",
        summary="two arms on a lift, on a wheeled base",
        extra="quackd[alohamini]",
        sdk="zmq",
        tiers={
            "mock": _rehearsed(),
            "sim2d": _rehearsed(),
            "zmq": _bound(
                "wire format VERIFIED at a pinned commit, exercised against a fake host over "
                "loopback, never run on a robot"
            ),
        },
        family=("wheeled", "arm"),
        # quackd's host runs on the robot in place of upstream's and wraps it, so the base and
        # the lift answer upstream's wire as they always did, and the arms, which upstream's own
        # host leaves limp, answer too (`bridge/alohamini/`)
        route=("wire", "bridge"),
    ),
    # No network API of any kind upstream: no socket, no daemon, no IPC. So quackd ships the
    # daemon, as it does for the Open Duck Mini, and the client is stdlib (ADR-0028).
    AdapterInfo(
        name="toddlerbot",
        backends=("mock", "sim2d", "bridge"),
        status="",
        summary="a small open-source humanoid",
        extra="quackd[toddlerbot]",
        tiers={
            "mock": _rehearsed(),
            "sim2d": _rehearsed(),
            "bridge": _bound("quackd's own daemon on the robot, never run on a robot"),
        },
        family=("humanoid",),
        route=("bridge",),
    ),
    # Huebot's own API is closed, and its maker is writing a shim for the robot that is to answer
    # the Microduck's JSON-RPC, so the client is stdlib and there is no SDK to probe.
    AdapterInfo(
        name="huebot",
        backends=("mock", "jsonrpc"),
        status="",
        summary="a 25 cm biped from Leif Rydenfalk",
        extra="quackd[huebot]",
        tiers={
            "mock": _rehearsed(),
            "jsonrpc": _bound(
                "robotd's protocol, spoken to a shim its maker is writing for the robot, never "
                "run on a robot"
            ),
        },
        family=("biped",),
        route=("wire",),
    ),
    # MOSS runs dimOS on the rover, and its only network surface is its own cockpit. quackd
    # speaks the cockpit, and a bridge of its own on the rover for what the cockpit has no
    # command for, both over HTTP with the standard library (ADR-0056).
    AdapterInfo(
        name="moss",
        backends=("mock", "cockpit"),
        status="",
        summary="a small tracked rover that picks up litter",
        extra="quackd[moss]",
        tiers={
            "mock": _rehearsed(),
            "cockpit": _bound(
                "MOSS's own cockpit and quackd's own bridge on the rover, never run against a rover"
            ),
        },
        family=("wheeled",),
        route=("wire", "bridge"),
    ),
    # An industrial arm on ROS 2, a family over the rosbridge package's binding (ADR-0075). Its
    # bodies are described ones too, and `ws` is bound code refused at `make()` for every one
    # until an owner names a date (ADR-0076), so it is tiered described, the best tier any body
    # has, which a test holds to `quackd_rosarm.bodies.ws_tier`.
    AdapterInfo(
        name="rosarm",
        backends=("mock", "ws"),
        status="",
        summary="an industrial arm on ROS 2, driven through rosbridge",
        extra="quackd[rosarm]",
        sdk="roslibpy",
        robot_types=("ur5e", "kuka-lbr-iisy-3-r760", "kuka-lbr-iiwa-7-r800", "yaskawa-motomini"),
        tiers={
            "mock": _rehearsed("the ws transport over a fake rosbridge and a fake arm"),
            "ws": Tier(
                "described", note="bound code, refused at make() until an owner names a date"
            ),
        },
        family=("arm",),
        route=("sdk",),
    ),
)

DESCRIBED = "described"
"""The last row's name, and the adapter a described body is reached through:
`--robot described:<body>`."""

DESCRIBED_BODIES: tuple[str, ...] = (
    "franka-research-3",
    "kuka-lbr-iisy-3-r760",
    "kuka-lbr-iiwa-7-r800",
    "menlo-asimov-1",
    "noetix-bumi",
    "rhoban-microban",
    "unitree-b2",
    "unitree-g1-edu",
    "unitree-go2-edu",
    "ur5e",
    "ur7e",
    "yaskawa-motomini",
)
"""The bodies quackd describes and drives no part of, one YAML each under
`quackd/adapters/bodies/`, in alphabetical order. Strings here and files there, and a test
holds the two lists equal: the catalogue must stay importable on a machine with nothing else,
so it names them rather than reading the directory."""

OFFICIAL = (
    *OFFICIAL,
    # The row that is not an adapter: every backend is a body quackd lists, registers and reasons
    # about from its maker's figures, with report_state and stop as its only verbs (ADR-0077).
    # Appended after the adapters so that the tables' order and the count of adapters stay as
    # they were; `kind` is what keeps the row out of the count of adapters.
    AdapterInfo(
        name=DESCRIBED,
        backends=DESCRIBED_BODIES,
        status="",
        summary="robots quackd describes from one YAML each and drives no part of",
        kind="described",
        tiers={body: Tier("described") for body in DESCRIBED_BODIES},
        family=("described",),
        route=(),
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
            "`quackd[microduck-camera]`, or any HTTP snapshot, a JPEG or a PNG. Without one the "
            "duck runs blind.",
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
            "camera URL is given, which serves a JPEG or a PNG.",
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
            camera="Any HTTP URL that answers each request with one still image, a JPEG or a PNG.",
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
"""What connecting each real backend of the ten takes (`AdapterInfo.connect`). `rosarm:ws` has
none: nobody can connect it until an owner names a date, and the page offers no fields for it."""

OFFICIAL = tuple(replace(info, connect=_CONNECT.get(info.name, {})) for info in OFFICIAL)

BY_NAME: dict[str, AdapterInfo] = {info.name: info for info in OFFICIAL}
"""Every row by name, the ten adapters first and the described row last."""

ADAPTERS: tuple[AdapterInfo, ...] = tuple(info for info in OFFICIAL if info.kind == "adapter")
"""The ten rows that are adapters: what the count of adapters, the extras and the
distributions follow (`tests/test_docs.py` holds the badge and the pages to it)."""
