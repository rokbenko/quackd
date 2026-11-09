"""Huebot as a body of the Microduck's family: what is different, and nothing that is the same.

The verbs and the transport are `quackd_microduck`'s (ADR-0075). What is left to say of Huebot is
here: its maker's figures, what it lacks, what quackd calls it and what answers for it, and where
nobody has told quackd enough to do more. Only this module and `upstream_api.py` may spell a
claim about Huebot's side, which `tests/test_upstream_api.py` holds.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Any

from quackd.adapters.manifest import Datasheet, Figure
from quackd_huebot import upstream_api as up
from quackd_microduck.body import (
    GO_TO_TEXT,
    KICK_SKILLS,
    MOVE_TEXT,
    SEARCH_SCAN_TEXT,
    SIT_TOGGLE,
    Body,
    Words,
)

BLURB = "a small biped robot (25 cm)"

_MAKER_PAGE = "ce-net.com/huebot, read 2026-10-20"
"""Where every figure below comes from: the maker's own page, on the day it was read."""

DATASHEET = Datasheet(
    height_m=Figure(value=0.25, confidence="official", source=_MAKER_PAGE),
    dof=Figure(
        value=15,
        confidence="official",
        source=_MAKER_PAGE,
        note="Feetech HD-1910 bus servos on one serial bus",
    ),
    manipulator="none",
    tethered=None,
    cannot=[
        "carry or push anything: its maker's page lists no arms",
        "climb or descend a step",
    ],
    notes=[
        "The speed caps are quackd's own clamp on the twists it sends, not a measured hardware "
        "maximum: nobody has published one for Huebot",
        "Its maker said on 2026-10-15 that it stands and steps, and that the walk's balance is "
        "still being tuned",
        "Its head turns on four servos, its maker wrote on 2026-10-15, and quackd turns none of "
        "them: robot.look is not on Huebot's side yet",
        "It has a speaker and a 13 by 8 LED face, its maker's page says. quack and say send "
        "robot.sound, which its maker has not named among the shim's calls, and quackd does not "
        "drive the face",
        "Its time-of-flight sensor is an 8 by 8 grid, which quackd does not read. The maker's "
        "page lists its battery as coming",
    ],
)

SKILLS_OF: dict[str, tuple[str, ...]] = {
    "sit": (SIT_TOGGLE,),
    "stand": (SIT_TOGGLE,),
    "kick": tuple(KICK_SKILLS.values()),
}
"""Each verb that sends `robot.do`, and every skill it can send. A connected Huebot offers one
only when its shim's `robot.subscribe` answer lists every skill the verb sends, so `kick` needs
both legs' kicks, since either may be asked for (`quackd_huebot.huebot_manifest`)."""

NO_ADDRESS = (
    "huebot:jsonrpc needs --address: quackd knows no default for Huebot's shim, which is "
    "wherever its maker put it. Forward the shim's socket over ssh, "
    "`ssh -L 9870:<socket> <huebot>`, then pass --address tcp://127.0.0.1:9870"
)
"""Why a Huebot with no `--address` is refused, as it is built (`make_transport`) and again at
a connect, and the forward that fixes it."""

LOOK_REFUSED = "Huebot's shim does not answer robot.look yet, so quackd sends it no look"
"""What a look is answered with, before anything is written (`up.SHIM_LOOK`)."""


_NO_LOOK = "its shim does not answer robot.look yet, so quackd turns no head on it"
_NO_PLACE = "nothing on a Huebot says where it is, and this walks by a map"

WITHHELD = MappingProxyType(
    {
        "gaze": _NO_LOOK,
        "watch": _NO_LOOK,
        "grab": (
            "its maker has not said what ground_pick does on a Huebot, or how its state says it "
            "holds something"
        ),
        "explore": _NO_PLACE,
        "find": _NO_PLACE,
    }
)
"""The family's verbs Huebot does not have, and why, which `extras.not_offered` carries to the
pilot beside its body and to a task file that asks for one (`quackd.duckfile.validate`)."""


def _shim_line(shim: dict[str, Any]) -> str:
    """`API v41, build 0.16.1, revision d818af5`: the version, the build and the revision the
    shim answered `hello` with. The protocol lets the last two be null, so either can be
    missing."""
    build = shim.get("daemon_version") or "none named"
    revision = shim.get("revision") or "none"
    return f"API v{shim.get('api_version')}, build {build}, revision {revision}"


def _claims_deadman(backend: str) -> bool:
    """Whether a description claims the robot's own deadman: `quackd_huebot.DEADMAN_SEEN`, read when
    asked, and always on the mock, which is not a robot and claims what every offline backend
    claims."""
    import quackd_huebot

    return backend == "mock" or quackd_huebot.DEADMAN_SEEN


HUEBOT = Body(
    name="huebot",
    vendor="leif-rydenfalk",
    model="huebot",
    blurb=BLURB,
    datasheet=DATASHEET,
    backends=("mock", "jsonrpc"),
    intents=("twist", "skill", "sound"),
    # No lens goes in the limits: the maker publishes none, so a detector measures through a
    # guess, says so, and `--fov-deg` gives it the real one.
    sensors=("tof", "microphone"),
    limits=MappingProxyType({"max_vx": 0.15, "max_vy": 0.1, "max_wz": 0.75}),
    # No `grab`, until its maker says what `ground_pick` does on Huebot and how its state reports
    # that it holds something. No `gaze` or `watch`: they send `robot.look`, which its shim does
    # not answer yet. No `explore` or `find`: nothing says where a Huebot is.
    verbs=frozenset({"sit", "stand", "kick", "stand_up", "quack", "say"}),
    core_texts=MappingProxyType(
        {
            "move": MOVE_TEXT,
            "go_to": GO_TO_TEXT,
            "search_scan": SEARCH_SCAN_TEXT,
            # The core's wording offers `kick, grab`, and Huebot has no `grab`. Its `then`
            # parameter still says so, since a parameter's description is the core's and no
            # manifest can change it.
            "approach_and": "walk_to a target, then run another verb (e.g. kick).",
        }
    ),
    words=Words(
        noun="Huebot",
        it="it",
        peer="the shim",
        peer_s="the shim's",
        named_peer="Huebot's shim",
        this_peer="this shim",
        skew_subject="Huebot's shim",
        a_peer="a peer",
        build="build",
        limp="Huebot's gains are dropped so it yields (safety.limp)",
        forward=(
            "forward the shim's socket with `ssh -L 9870:<socket> <huebot>` and use "
            "--address tcp://127.0.0.1:9870"
        ),
        floor_advice=(
            "Huebot's shim is asked to answer with API v{version}, the version quackd read at "
            "{release}"
        ),
        newer_advice="The shim is asked to answer with API v{version}.",
        older_advice="The shim is asked to answer with API v{version}.",
        no_hello="Huebot's shim: point --address at the socket the shim listens on",
        no_subscribe=(
            " and not a shim quackd can drive: Huebot's shim has to answer it, since no state "
            "arrives until it does (docs/adapters/huebot/README.md)"
        ),
        same_version=(
            "so it does not answer this call as robotd does (docs/adapters/huebot/README.md)"
        ),
        other_client="",
        zero_twist="",
        unhealthy="Huebot's shim says it is unhealthy",
        no_stop=(
            "What stops Huebot now is its own deadman, which zeroes velocity once robot.move "
            "stops arriving, if its shim keeps one as robotd does."
        ),
        sit_unknown=(
            "the protocol has one sit_toggle rather than a sit and a stand, so quackd would be "
            "guessing which way it goes."
        ),
        ask="ask it to drive",
        asked="asked Huebot to drive",
        no_pickup=(
            "cannot stand_up: this robot does not report whether somebody is holding it (no "
            "safety.picked_up), so quackd will not ask it to drive legs that may be in "
            "somebody's hands. Stand it up by hand"
        ),
        fall=(
            "asked Huebot to drive, but it is still answering a fall itself ({fall}), {waited} s "
            "on. Run stand_up again in a moment to see whether it is up"
        ),
        down=(
            "asked Huebot to drive, and it is still down {waited} s later: nobody has said it "
            "gets up from a fall by itself, so it may need a hand"
        ),
        kick_text=(
            "Kick forward with one leg, at a ball close and roughly ahead. Nobody has measured "
            "how close on a Huebot: a Microduck's kick connects under 0.3 m."
        ),
        kick_done="the kick finished; the robot reports nothing about the ball.",
        stand_up_text=(
            "Ask the robot to get up after a fall. It may not manage: the result says whether it "
            "is upright within 10 s, or that it is still answering the fall itself."
        ),
    ),
    not_standing=up.NOT_STANDING_LABELS,
    health_key="shim",
    health_line=_shim_line,
    assumptions=(up.STAND_UP_RPC.name,),
    no_address=NO_ADDRESS,
    look_refused=LOOK_REFUSED,
    skill_verbs=MappingProxyType(SKILLS_OF),
    claims_deadman=_claims_deadman,
    withheld=WITHHELD,
)
"""Huebot: the maker's figures, the verbs it can be offered, and no simulator, map or mediad."""
