"""The only file in Roboglot allowed to spell a `microduck_rl` name (ADR-0022).

`microduck_rl` is where Pollen Robotics trains the Microduck's walking policy, at
https://github.com/pollen-robotics/microduck_rl. Roboglot reads one fact there: the velocity
commands the walking task trains with (`TRAINING_TWIST`). The body's turning cap is the turn in
it (`body.MICRODUCK`), and the contact odometry on `jsonrpc` takes the fastest the duck can walk
from it (`CMD_MAX_VX`, `CMD_MAX_VY`): a step longer than that speed allows, between two readings,
is no walk, and the odometry's frame begins again (`roboglot.space.odometry.OdometryTrack`).

Every reference is tagged VERIFIED (read from upstream source, link given) or UNVERIFIED (an
assumption of ours, with what Roboglot does about it), and `tests/test_upstream_api.py` keeps
each UNVERIFIED name out of every other file. Read at the commit below on the day below, on
`develop`, the repository's default branch.

Roboglot never imports `microduck_rl`: it requires Python 3.12 exactly, pulls torch, warp and
mjlab, and needs a CUDA GPU to train.
"""

from __future__ import annotations

from roboglot.upstream import Status, UpstreamRef

REPO = "https://github.com/pollen-robotics/microduck_rl"
PIN = "bae39f5900b318f7e87fc485c30a31213cfa14eb"  # develop, 2026-10-27
BRANCH = "develop"
"""The branch the pin sits on, which is also the repository's default: what the nightly drift
job (`scripts/upstream_drift.py`) compares the pin against, whatever the default becomes."""
READ_ON = "2026-11-08"

_VELOCITY_TASK = "src/mjlab_microduck/tasks/microduck_velocity_env_cfg.py"


def src(path: str, line: int | None = None) -> str:
    return f"{REPO}/blob/{PIN}/{path}" + (f"#L{line}" if line else "")


def all_refs() -> list[UpstreamRef]:
    return [v for v in globals().values() if isinstance(v, UpstreamRef)]


def refs_by_status(status: Status) -> list[UpstreamRef]:
    return [r for r in all_refs() if r.status == status]


TRAINING_TWIST = UpstreamRef(
    "vx ±0.4, vy ±0.3, wz ±1.0 (rehearsal keyboard: ±0.3, ±0.3, ±1.5)",
    "VERIFIED",
    src(_VELOCITY_TASK),
    "the velocity command ranges the walking task trains with, and the rehearsal script's "
    "full-scale keys",
)

ROBOT_POLICY_RANGES = UpstreamRef(
    "the policy a robotd walks on is asked for no faster twist than TRAINING_TWIST",
    "UNVERIFIED",
    src(_VELOCITY_TASK),
    "an assumption of Roboglot's: these are the velocity task's ranges, and a robotd from "
    "daemon-v0.13.0 walks on velstand.onnx from pollen-robotics/microduck-policies, which "
    "Roboglot has not traced to this config. Roboglot's own caps stay inside them (body.py), and "
    "its contact odometry takes the duck's top speed from them",
)

CMD_MAX_VX = 0.40
CMD_MAX_VY = 0.30
"""`TRAINING_TWIST`'s forward and sideways ranges: the fastest the walking policy is trained to
follow, which the contact odometry on `jsonrpc` takes for the fastest the duck can walk."""
