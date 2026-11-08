"""The only file in quackd allowed to spell a method name Huebot's shim answers.

Huebot's own API is closed. Its maker, Leif Rydenfalk, wrote to Rok Benko on 2026-10-15 of a
shim to run on the robot that answers the JSON-RPC the Microduck's `robotd` speaks, and named
`hello`, `robot.move`, `robot.stop`, `robot.head`, `robot.state` and `robot.health`. So the
protocol here is robotd's, read in the Microduck's own source at the pin the Microduck adapter
reads it at, daemon-v0.18.8 (4a763dd), and every ref that says what that protocol is stays
VERIFIED, as a fact about the protocol.

Whether Huebot's shim answers it that way is another matter. Nobody has seen the shim, Huebot's own
API is closed, and the maker's page publishes neither joint names nor a joint order, so every claim
about Huebot is an UNVERIFIED ref, sourced to the maker's email of 2026-10-15 and page,
ce-net.com/huebot, read on 2026-10-20: that the shim answers the calls quackd makes, that it does
not answer `robot.look` yet, the order of its joints, its deadman, its skills, that its own
behaviour code is off while the shim is driven, and what `robot.enable` does for a Huebot on the
floor. `docs/adapters/huebot/README.md` is the human readable version of this file, and its section
on what the shim must answer is what the maker is asked to build. `tests/test_upstream_api.py`
proves that the UNVERIFIED names are reachable from the `jsonrpc` backend alone.

The protocol refs are `quackd_microduck.upstream_api`'s own, imported by name, so a re-pin of the
Microduck moves both by construction (ADR-0075). This module keeps what is Huebot's: the refs below
sourced to its maker, and the words its lines use for what each of robotd's labels means.

The maker's own code, github.com/Leif-Rydenfalk/walking-robot-kit, is the servo bus alone: a
daemon serving an HTTP control page on port 8938, with no JSON-RPC, which quackd does not speak.

Nothing here has been run against a Huebot.
"""

from __future__ import annotations

from quackd.upstream import Status, UpstreamRef
from quackd_microduck.upstream_api import (
    API_FLOOR,
    API_VERSION,
    DEADMAN,
    DEADMAN_S,
    ERR_INVALID_PARAMS,
    ERR_METHOD_NOT_FOUND,
    FRAMING,
    HEAD_PARAMS,
    HELLO,
    IPC_PROTO,
    JOINT_NAMES,
    JSONRPC_VERSION,
    LIMP_FALL_LABELS,
    LINES_IN_ORDER,
    MOVE_ECHO,
    PIN,
    POLICY_LABELS_DRIVEN,
    POLICY_LABELS_NOT_DRIVEN,
    READ_ON,
    RELEASE,
    REPO,
    ROBOT_DO,
    ROBOT_ENABLE,
    ROBOT_HEAD,
    ROBOT_HEALTH,
    ROBOT_INIT,
    ROBOT_LOOK,
    ROBOT_MOVE,
    ROBOT_RELAX,
    ROBOT_SOUND,
    ROBOT_STATE,
    ROBOT_STOP,
    ROBOT_SUBSCRIBE,
    SAFETY_PICKED_UP,
    SITTING_LABELS,
    SKILL_SOURCE,
    SKILLS,
    SOUND_TAG_LIST,
    SOUND_TAGS,
    STANDING_LABELS,
    STATE_NEEDS_SUBSCRIBE,
    UNWIND_SUFFIX,
    VERSION_SKEW,
    src,
)

MAKER = "https://ce-net.com/huebot"
"""The maker's page, where every UNVERIFIED ref here is sourced."""
MAKER_READ_ON = "2026-10-20"
MAKER_EMAIL_ON = "2026-10-15"
"""When Leif Rydenfalk wrote to Rok Benko about the shim: not published, and the only word there
is on what the shim answers."""


__all__ = [
    "API_FLOOR",
    "API_VERSION",
    "DEADMAN",
    "DEADMAN_S",
    "ERR_INVALID_PARAMS",
    "ERR_METHOD_NOT_FOUND",
    "FRAMING",
    "HEAD_PARAMS",
    "HELLO",
    "IPC_PROTO",
    "JOINT_NAMES",
    "JSONRPC_VERSION",
    "LIMP_FALL_LABELS",
    "LINES_IN_ORDER",
    "MOVE_ECHO",
    "PIN",
    "POLICY_LABELS_DRIVEN",
    "POLICY_LABELS_NOT_DRIVEN",
    "READ_ON",
    "RELEASE",
    "REPO",
    "ROBOT_DO",
    "ROBOT_ENABLE",
    "ROBOT_HEAD",
    "ROBOT_HEALTH",
    "ROBOT_INIT",
    "ROBOT_LOOK",
    "ROBOT_MOVE",
    "ROBOT_RELAX",
    "ROBOT_SOUND",
    "ROBOT_STATE",
    "ROBOT_STOP",
    "ROBOT_SUBSCRIBE",
    "SAFETY_PICKED_UP",
    "SITTING_LABELS",
    "SKILLS",
    "SKILL_SOURCE",
    "SOUND_TAGS",
    "SOUND_TAG_LIST",
    "STANDING_LABELS",
    "STATE_NEEDS_SUBSCRIBE",
    "UNWIND_SUFFIX",
    "VERSION_SKEW",
    "Status",
    "UpstreamRef",
    "src",
]  # re-exported: this file's own refs are Huebot's, and the protocol's are read through it

#: Every other label robotd itself names, and what the robot is doing while it says it. A
#: configured skill reports its own name instead (the robot's list is SKILL_SOURCE), then that
#: name with UNWIND_SUFFIX while it unwinds.
NOT_STANDING_LABELS = {
    "rise": "the sit-stand network is getting the robot up off its seat",
    "ground_pick": "the ground pick is scooping at the floor",
    "skill": "a skill whose definition could not be found is running",
    "held": "no network is driving the legs: none is enabled, a pick-up paused it, or it failed",
    "homing": "the joints are ramping to the home pose",
    "rest": "the robot is easing into its rest pose to shut down",
    "limp_fall": "the robot is riding a fall down at limp gain",
    "limp_pose": "the robot is ramping to its pose after a fall",
}

# ── Huebot's side: what its maker says, and what nobody has seen ────────────────────────

SHIM_CALLS = UpstreamRef(
    "Huebot's shim answers the calls quackd makes as robotd does",
    "UNVERIFIED",
    MAKER,
    "Leif Rydenfalk's email of 2026-10-15 says the shim to run on Huebot will answer the "
    "JSON-RPC quackd speaks to robotd, and names hello, robot.move, robot.stop, robot.head, "
    "robot.state and robot.health. quackd also needs robot.subscribe, without which the connect "
    "fails as not robotd, and robot.do, robot.sound and robot.enable for its verbs, whose "
    "answers it reads a boolean accepted in. Nobody has seen the shim answer any of them. "
    "docs/adapters/huebot/README.md says what each call has to carry.",
)
SHIM_LOOK = UpstreamRef(
    "robot.look, which Huebot's shim does not answer yet",
    "UNVERIFIED",
    MAKER,
    "The email of 2026-10-15 says robot.look is not on Huebot's side yet, and that the head "
    "turns with four servos. So quackd answers a look itself, refused, and writes nothing, there "
    "is no gaze and no watch, and the head is not centred before go_to and search_scan steer. "
    "Since no look goes out, neither waits for a glance to fade after one: head_fade_s is 0.",
)
JOINT_ORDER = UpstreamRef(
    "robot.state.joints in the Microduck's order, head_yaw at index 7, positive to the left",
    "UNVERIFIED",
    MAKER,
    "Huebot has 15 servos, the maker's page says, and it publishes neither their names nor "
    "their order. quackd reads joints by robotd's JOINT_NAMES, so the shim is asked to report "
    "them in that order, with head_yaw at index 7 in radians and positive when the head looks "
    "left, as a Microduck's does. The head yaw becomes extras.head_yaw_deg and nothing else: no "
    "verb steers on it.",
)
SHIM_DEADMAN = UpstreamRef(
    "Huebot stops when robot.move notifications stop arriving",
    "UNVERIFIED",
    MAKER,
    "robotd zeroes a twist older than 500 ms (DEADMAN), and quackd relies on that: a walk "
    "re-sends robot.move every 100 ms, a twist quackd holds back is not replaced by a zero, and "
    "a stop that cannot be delivered leaves the robot's deadman to stop it. That the shim keeps "
    "one is an assumption: answering robotd's calls is not the same as stopping as robotd does. "
    "Step 3 of the hardware checklist tests it, on a stand, before any walk on the floor, and "
    "the description of a real Huebot claims a deadman only once a Huebot has passed that step "
    "(DEADMAN_SEEN).",
)
SHIM_SKILLS = UpstreamRef(
    "the skills Huebot's robot.do answers to are the ones its robot.subscribe lists",
    "UNVERIFIED",
    MAKER,
    "Huebot runs behaviour code of its own, its maker says, and quackd assumes nothing about "
    "which skills it has. It offers each verb that sends robot.do only when every skill the verb "
    "sends is in the skills of the shim's robot.subscribe answer: sit and stand send "
    "sit_toggle, and kick sends kick_left and kick_right. grab, which sends ground_pick, is "
    "offered on no Huebot until its maker says what ground_pick does there and how the state "
    "reports that the robot holds something. The transport refuses a robot.do for any name the "
    "shim did not list without writing it, and a shim that lists none is offered none of the "
    "three.",
)
SHIM_BEHAVIOURS = UpstreamRef(
    "Huebot's own behaviour code is off while quackd drives it through the shim",
    "UNVERIFIED",
    MAKER,
    "The maker's email of 2026-10-15 says Huebot runs behaviour code of its own: needs, an LED "
    "face, coming to your hand. That code can move the robot without quackd, and what it does "
    "while the shim is driven is the maker's to say. quackd assumes it is off, since nothing "
    "it reads tells that motion from its own. The shim is asked to report a policy label other "
    "than walk or stand while anything but quackd's twists drives the legs, which quackd reads "
    "as unknown and sends nothing on, and to stop whatever moves the robot on robot.stop. The "
    "hardware checklist switches the behaviours off, and sees that they are off, before its "
    "step 1.",
)
STAND_UP_RPC = UpstreamRef(
    "stand_up asks Huebot's shim to drive (robot.enable), which nobody has said gets a fallen "
    "Huebot up",
    "UNVERIFIED",
    MAKER,
    "There is no stand-up call. On robotd, robot.enable {on: true} is policy execution, which "
    "'can bring a limp robot up as a side effect of being asked to drive' (ROBOT_ENABLE), and "
    "what a shim does with it is the shim's. The maker says Huebot stands and steps, and "
    "whether it gets up from a fall is not published. stand_up reads the robot every 0.25 s "
    "after the request, for up to 10 s, sending nothing more, calls it upright as soon as it "
    "reads standing, and says it may need a hand only once the 10 s are up.",
)


def all_refs() -> list[UpstreamRef]:
    return [v for v in globals().values() if isinstance(v, UpstreamRef)]


def refs_by_status(status: Status) -> list[UpstreamRef]:
    return [r for r in all_refs() if r.status == status]
