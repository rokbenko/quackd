"""The only file in quackd allowed to spell a name from Pollen's `mapd` (ADR-0022, ADR-0068).

`mapd` is the Microduck's onboard mapper. It builds an occupancy grid from robotd's state and
tofd's depth frames, keeping a keyframe each time the duck stands still, and says where the duck
is in it, or why it does not know. It is on one upstream branch, `maploc-v2`, read at the commit
below on the day below, and in no robotd release: daemon-v0.17.9, tagged that day, is API 44 and
has no `map.*`. So every ref here is UNVERIFIED: read from a branch that may change before it
ships, and never answered by a robot quackd has spoken to. When a release carries `mapd`, these
are read again at that release's tag and the rows say VERIFIED.

The client that speaks it, `transports/mapd.py`, and the simulated map the simulators stand in
with, which takes these numbers as plain floats (`sim_map_params`), are the only code that reads
this file; `tests/test_upstream_api.py` holds every other file to that.
"""

from __future__ import annotations

from quackd.upstream import Status, UpstreamRef

REPO = "https://github.com/pollen-robotics/microduck"
BRANCH = "maploc-v2"
PIN = "7e0be18d13e5f220647d2ae5d8d39fb50e721dc5"  # maploc-v2, 2026-10-16
READ_ON = "2026-10-26"
API_VERSION = 45
"""What the branch's `duck-ipc-proto` says (lib.rs:480): `map.*` is its v45. robotd's own
handshake says nothing of mapd, which runs as a service of its own and answers no `hello`."""


def src(path: str, line: int | None = None) -> str:
    return f"{REPO}/blob/{PIN}/{path}" + (f"#L{line}" if line else "")


def all_refs() -> list[UpstreamRef]:
    return [v for v in globals().values() if isinstance(v, UpstreamRef)]


def refs_by_status(status: Status) -> list[UpstreamRef]:
    return [r for r in all_refs() if r.status == status]


_PROTO = "duck-ipc-proto/src/lib.rs"
_SERVER = "mapd/src/server.rs"
_MAPPER = "maploc/src/mapper.rs"
_PROJECT = "maploc/src/project.rs"

# ── the socket and the calls ────────────────────────────────────────────────────────────

MAPD_SOCKET = UpstreamRef(
    "/run/mapd/map.sock",
    "UNVERIFIED",
    src(_PROTO, 544),
    "mapd's own socket, 'every method in the map.* namespace' (lib.rs:542-544), beside robotd's "
    "and tofd's. A laptop reaches it as it reaches robotd's, through an ssh -L forward of its "
    "own: quackd takes it as --map-address",
)
MAPD_STATUS = UpstreamRef(
    "map.status",
    "UNVERIFIED",
    src(_PROTO, 1014),
    "request, no params -> MapStatusResult {state: MapState, unavailable?: why it is not mapping "
    "at all, loops, relocalizations, island_keyframes, path: where the map is kept, saved_at?} "
    "(lib.rs:1014, 5111-5126). 'Where the robot is in its map, whether it knows, and how big the "
    "map is'",
)
MAPD_STREAM = UpstreamRef(
    "map.stream",
    "UNVERIFIED",
    src(_PROTO, 1017),
    "request, no params: answered with the status, then map.state notifications 'at a few "
    "hertz until the connection closes' (lib.rs:1015-1017); every 250 ms at the pin "
    "(server.rs:20, STREAM_PERIOD)",
)
MAPD_STATE = UpstreamRef(
    "map.state",
    "UNVERIFIED",
    src(_PROTO, 5087),
    "notification MapState {t_ns: the CLOCK_MONOTONIC of the robot state the pose was computed "
    "from, pose?: {x, y, yaw} absent while lost, lost?: why it does not know, collecting: a stop "
    "is being collected, keyframes: stops in the map proper} (lib.rs:5087-5106). A map's frame "
    "is 'wherever its first stop was, facing the way the robot faced then: a map has no north' "
    "(lib.rs:5074-5076)",
)
MAPD_GRID = UpstreamRef(
    "map.grid",
    "UNVERIFIED",
    src(_PROTO, 1021),
    "request {res_m?} -> MapGridResult {res_m, origin: [x, y] of cell (0, 0)'s low corner, width, "
    "height, cells: one character per cell, row-major from the lowest y, keyframes: [MapPose], "
    "pose?} (lib.rs:5131-5153), 'as mapd renders it from its keyframes now'. res_m is 0.05 when "
    "not given and clamped to 0.025..0.5 (server.rs:24-26, 85)",
)
MAPD_WIPE = UpstreamRef(
    "map.wipe",
    "UNVERIFIED",
    src(_PROTO, 1023),
    "request, no params -> {keyframes: how many stops it forgot}: 'Forget the map and start a "
    "new one from where the robot stands' (lib.rs:1022-1023, 5159-5162). quackd sends it only "
    "for a person at a terminal (`quackd map --wipe`), never for a pilot",
)
MAPD_NO_HELLO = UpstreamRef(
    "mapd answers map.* and nothing else",
    "UNVERIFIED",
    src(_SERVER, 111),
    "every call it does not know, `hello` among them, is METHOD_NOT_FOUND (server.rs:83-111), so "
    "quackd checks for it with map.stream and never sends a hello there",
)

# ── what its answers mean ───────────────────────────────────────────────────────────────

MAPD_GRID_CELLS = UpstreamRef(
    "map.grid cells ?, ., #",
    "UNVERIFIED",
    src(_PROTO, 5149),
    "'`?` unknown, `.` free, `#` occupied. Text rather than numbers because it is what a grid of "
    "three states is' (lib.rs:5146-5149). quackd reads them into its own three states, which "
    "happen to print the same",
)
MAPD_LOST_WORDS = UpstreamRef(
    "boot, carried, bumped, sat, fell, contradiction",
    "UNVERIFIED",
    src(_PROTO, 5100),
    "MapState.lost: 'boot (it has not recognised anything since it started), carried, bumped "
    "(picked up for a moment ... and looking nearby), sat (stood up, and checking it was not "
    "carried while seated), fell, or contradiction (what it sees stopped matching the map) ... A "
    "client should treat a word it does not know as lost: the list grows' (lib.rs:5093-5100)",
)
MAPD_SETTLE = UpstreamRef(
    "a keyframe after 0.4 s of stillness",
    "UNVERIFIED",
    src(_MAPPER, 185),
    "settle_s 0.4, 'Stillness required before a stop opens a keyframe' (mapper.rs:69-71, 185), "
    "still below 0.03 m/s and 0.08 rad/s of odometry (mapper.rs:72-74, 186-187). Pollen's post "
    "says half a second; the code at the pin says 0.4, which quackd takes",
)
MAPD_CORRECTION_GATE = UpstreamRef(
    "a correction within 10 cm and 3 degrees",
    "UNVERIFIED",
    src(_MAPPER, 231),
    "track_max_correction_m 0.10 and track_max_correction_rad 3 degrees: 'A tracking match "
    "moving the stop further than this from where odometry put it is not believed' "
    "(mapper.rs:146-152, 231-233). quackd re-plans on a correction inside it and ends a walk on "
    "a jump beyond it",
)
MAPD_AGREE = UpstreamRef(
    "two stops agree within 25 cm and 4 degrees",
    "UNVERIFIED",
    src(_MAPPER, 210),
    "a relocalization is 'Accepted only when two consecutive stops agree on the same correction' "
    "(mapper.rs:17-18), within agree_m 0.25 and agree_rad 4 degrees (mapper.rs:106-109, "
    "210-211). A lost duck has to stop twice before it knows where it is again",
)
MAPD_TOF_RANGE = UpstreamRef(
    "mapd places returns 0.08 to 2.0 m away",
    "UNVERIFIED",
    src(_PROJECT, 61),
    "min_range_m 0.08 and max_range_m 2.0, with ranges taken as axial (project.rs:33-40, 60-64): "
    "what the simulated map reveals a cell to at most",
)

# ── the numbers, for the code that keeps to them ────────────────────────────────────────
#
# Each is the figure its ref above quotes, held to it by tests/test_upstream_api.py, so the
# simulated map (`transports/mapd.py:sim_map_params`) and the client never type one of their own.

SETTLE_S = 0.4
"""MAPD_SETTLE: how long the body stands still before a keyframe."""
STILL_SPEED_MPS = 0.03
STILL_TURN_RPS = 0.08
"""MAPD_SETTLE: below these the body counts as still."""
STREAM_PERIOD_S = 0.25
"""MAPD_STREAM: how often map.state comes."""
AGREE_STOPS = 2
"""MAPD_AGREE: how many stops a lost body takes to know where it is again."""
CORRECTION_M = 0.10
CORRECTION_DEG = 3.0
"""MAPD_CORRECTION_GATE: the largest correction believed at a stop."""
GRID_RES_M = 0.05
"""MAPD_GRID: a cell, unless asked for another."""
TOF_MIN_M = 0.08
TOF_MAX_M = 2.0
"""MAPD_TOF_RANGE: the ranges a return is placed at."""
