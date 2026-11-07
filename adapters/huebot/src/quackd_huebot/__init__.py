"""The Huebot adapter: a small biped from Leif Rydenfalk, over the shim its maker is writing.

Huebot does not run the Microduck runtime, and its own API is closed. Its maker is writing a shim
for the robot that is to answer the same calls the Microduck's `robotd` answers, the same way, so
this is a body of the Microduck's family (ADR-0075): the protocol, the version gate, the hold on a
walk the robot does not take and the reliance on the robot's own deadman are `quackd_microduck`'s,
and `body.py` says what differs. What it leaves out is what Huebot's shim does not answer yet,
`robot.look`, so there is no `gaze` and no `watch`, and the Microduck's simulators, which draw the
Microduck's body and gait and not Huebot's. It has no `grab` either, until its maker says what
`ground_pick` does on Huebot. Two backends: `mock`, the core's scripted body, and `jsonrpc`, the
shim, which nobody has run (docs/adapters/huebot/README.md).
"""

from __future__ import annotations

from collections.abc import Collection, Sequence

from quackd.adapters.base import one_camera_url, refuse_rest_pose
from quackd.adapters.manifest import RobotManifest
from quackd.verbs.registry import Precondition, Verb
from quackd_huebot.body import BLURB, DATASHEET, HUEBOT, SKILLS_OF
from quackd_microduck import MicroduckAdapter
from quackd_microduck import manifest_for as _manifest_for
from quackd_microduck import offered_skill_verbs as _offered_skill_verbs
from quackd_microduck.verbs import conditions_for, verbs_for

__version__ = "0.33.0"
"""Kept in step with quackd's own version by scripts/set_version.py. It lives here
rather than being read from the core, because this file is all an adapter's sdist
contains."""

BACKENDS = HUEBOT.backends

DEADMAN_SEEN = False
"""Whether a Huebot has passed step 3 of docs/adapters/huebot/hardware-checklist.md, which is
where its legs are seen to stop once `robot.move` stops arriving, on a stand. Until one has, the
description of a real Huebot claims no deadman, as the Open Duck's bridge and the ToddlerBot's
daemon claim none they have not reported, and quackd relies on the shim's all the same, as the
deadman's UNVERIFIED ref in `upstream_api.py` says: nothing else stops a walk whose link died.
The mock is not a robot, and claims what every offline backend claims."""

HUEBOT_VERBS: dict[str, Verb] = verbs_for(HUEBOT)


def huebot_conditions() -> dict[str, Precondition]:
    return conditions_for(HUEBOT)


def offered_skill_verbs(skills: Collection[str] | None) -> list[str]:
    """The verbs that send `robot.do` a Huebot offers: every one while nothing has said which
    skills it has, which is the description `validate` reads and the mock, and on a connected
    robot each one whose every skill its shim listed (`SKILLS_OF`)."""
    return _offered_skill_verbs(HUEBOT, skills)


def huebot_manifest(
    backend: str,
    robot_id: str = "huebot",
    *,
    camera: bool = True,
    skills: Collection[str] | None = None,
) -> RobotManifest:
    """Huebot as data. Static: no connection needed (validate, announce, doctor).

    `camera` is what a description cannot settle on its own. The shim's socket carries no
    picture, so on `jsonrpc` a frame exists only when `--camera-url` names something serving
    one, and a frame has arrived from it. Advertising `observe` either way promises the pilot
    vision it may not have.

    `skills` is what the shim's `robot.subscribe` answer listed, on a connected robot, and None
    everywhere else. Each verb that sends `robot.do` is declared only when every skill it sends
    is among them, so a skill Huebot does not have is never a verb the pilot is offered."""
    return _manifest_for(HUEBOT, backend, robot_id, camera=camera, skills=skills)


class HuebotAdapter(MicroduckAdapter):
    """A `RobotAdapter` over either Huebot transport: the family's, for this body."""

    name = "huebot"
    body = HUEBOT


# ── what the factory calls (the same four names on every adapter package) ───────────────


def describe(backend: str, robot_id: str | None = None) -> RobotManifest:
    return huebot_manifest(backend, robot_id or "huebot")


def implementations() -> dict[str, Verb]:
    return dict(HUEBOT_VERBS)


def conditions() -> dict[str, Precondition]:
    return huebot_conditions()


def make(
    backend: str,
    *,
    robot_id: str | None = None,
    seed: int | None = None,
    address: str | None = None,
    live: bool = False,
    camera_url: str | Sequence[str] | None = None,
    token: str | None = None,
    rest_pose: dict[str, float] | None = None,
) -> HuebotAdapter:
    refuse_rest_pose("huebot", rest_pose)
    url = one_camera_url(camera_url, spec=f"huebot:{backend}")
    # `token`, `seed` and `live` are accepted and unused, deliberately: the factory calls every
    # adapter's `make` with the same keywords. The protocol has no authentication at all, so the
    # shim is reached over ssh or on the robot itself, never through an open port.
    from quackd_microduck.transports.factory import make_transport

    transport = make_transport(backend, body=HUEBOT, address=address, camera_url=url)
    return HuebotAdapter(transport, robot_id=robot_id)


__all__ = [
    "BACKENDS",
    "BLURB",
    "DATASHEET",
    "HUEBOT",
    "HUEBOT_VERBS",
    "SKILLS_OF",
    "HuebotAdapter",
    "conditions",
    "describe",
    "huebot_conditions",
    "huebot_manifest",
    "implementations",
    "make",
    "offered_skill_verbs",
]


# What this adapter reads from upstream, for `quackd doctor`: the protocol Huebot's shim answers,
# read in the Microduck's source, beside what its maker says of Huebot. Declared here rather
# than in a table in the core (ADR-0022).
def _upstream_rows() -> tuple[tuple[str, object, str, str], ...]:
    from quackd_huebot import upstream_api as shim

    return (
        (
            "huebot",
            shim,
            "docs/adapters/huebot/README.md",
            "a Huebot's shim (the jsonrpc backend)",
        ),
    )


UPSTREAMS = _upstream_rows()
