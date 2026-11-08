"""The arm types this adapter knows, as data (ADR-0075).

What drives an arm here reads LeRobot's `Robot` and `SerialMotorsBus`, and the SO follower's own
behaviour and its sts3215 registers where it has to (`up.SO_CONNECT_REFUSES_WHILE_OPEN`,
`up.STS3215_REGISTERS` and their like). So an arm of that class comes as a record in this file, and
an arm of another class needs those read again first. A record is what differs: what the manifest
calls it, its sheet, and how far it has got. The owner rule holds here as it does for a backend
(ADR-0076): a type with no owner and no first-run date is described and not driven, and `make()`
refuses it.

The SO-101's sheet is the one the arm has always had. The SO-100 runs the same class at LeRobot's
pin (`up.ROBOT_TYPE_SO100`), so it is drivable on that code, and nobody has run one.
"""

from __future__ import annotations

from dataclasses import dataclass

from quackd.adapters.base import AdapterError
from quackd.adapters.catalogue import Tier
from quackd.adapters.manifest import Datasheet, Figure
from quackd_lerobot import upstream_api as up

ROBOT_TYPE = up.ROBOT_TYPE_SO101.name
"""The type an arm is when nobody says, which is every arm registered before there was a choice."""

BLURB = (
    "a six-joint desktop robot arm with a parallel gripper (an SO-101 class arm driven "
    "by LeRobot), bolted to a table"
)

REACH = Figure(
    value=0.4,
    confidence="estimate",
    source="the maker's URDF, TheRobotStudio/SO-ARM100 Simulation/SO101/so101_new_calib.urdf",
    note="link lengths from the shoulder to the gripper frame, summed with the arm straight and "
    "rounded down",
)
"""How far the gripper gets from the shoulder, read off the maker's own robot description.

Nobody publishes a reach for the SO-101, and the sheet used to say so, which on an arm told
the pilot to decline anything that turned on reaching: every task an arm has. The URDF gives
each joint's origin in its parent link's frame, so the distance from one joint to the next is
the length of that origin vector. From `shoulder_lift` outwards, read on 2026-09-23:

    elbow_flex          (-0.11257, -0.028,     0)          0.116 m
    wrist_flex          (-0.1349,   0.0052,    0)          0.135 m
    wrist_roll          ( 0,       -0.0611,    0.0181)     0.064 m
    gripper_frame_joint (-0.0079,  -0.000218, -0.0981274)  0.098 m

They sum to 0.413 m. That is an upper bound, since the links only add up in full when they
are collinear, and a grid sweep of `elbow_flex`, `wrist_flex` and `wrist_roll` through their
URDF limits put the farthest the gripper frame gets from the `shoulder_lift` axis at about
0.41 m. So 0.4, rounded down, and an estimate rather than official: it is quackd's arithmetic
on the maker's file, not a figure the maker states. It is measured from the shoulder joint
rather than the base, and to the gripper frame rather than the fingertips."""

DATASHEET = Datasheet(
    height_m=Figure(
        value=0.53,
        confidence="estimate",
        source="one vendor's listing",
        note="reaching straight up",
    ),
    dof=Figure(
        value=6,
        confidence="official",
        source="the LeRobot SO-101 docs",
        note="five joints and a gripper",
    ),
    payload_kg=Figure(value=0.5, confidence="estimate", source="one vendor's listing"),
    reach_m=REACH,
    manipulator="gripper",
    arms=1,
    tethered=True,
    cannot=[
        "go anywhere: it is bolted to a table and has no base",
        # This used to end "and nothing whose weight is not known", which is nearly every
        # object a task names: nobody tells the pilot what a pen weighs. It needs a scale to
        # judge an object by, not a ban on everything unweighed
        "lift or hold more than about half a kilogram: a pen, an empty cup or a wooden block "
        "weighs far less than that, and a full bottle or a tool may weigh more",
        f"reach anything more than about {REACH.value:g} m from its shoulder: that is the arm "
        "held straight out, and any bent pose reaches less",
        "feel what it holds: nothing reports grip force, so holding is inferred from the "
        "gripper stopping short of shut, which an empty hand that binds also does",
        "know its own mass: vendor listings disagree by a factor of three",
    ],
    notes=[
        "How wide the gripper opens and how hard it grips are not published",
        "No sensors beyond the servo positions unless a camera is configured, and the servo "
        "temperature, which quackd reads off the bus because LeRobot does not",
        "Vendor listings give the mass as anything from 0.8 to 2.5 kg and nobody official "
        "publishes one, so it is listed as not published rather than picked from a hat",
        "There are 7.4 V and 12 V builds of the same arm, with different stall torque and "
        "different supplies. Which one is on the desk is not something quackd can ask",
    ],
)

_SO100_URDF = (
    "the maker's URDF, TheRobotStudio/SO-ARM100 Simulation/SO100/so100.urdf, read 2026-11-05"
)

_SO100_REACH = Figure(
    value=0.33,
    confidence="estimate",
    source=_SO100_URDF,
    note="the farthest the moving jaw's pivot gets from the shoulder over the joints' limits, "
    "rounded down; the fingers go further, and the file does not say how far",
)
"""As `REACH` is for the SO-101, from `shoulder_lift` outwards, read at the simulator's pin:

    elbow_flex    (0,        0.11257, 0.028)   0.116 m
    wrist_flex    (0,        0.0052,  0.1349)  0.135 m
    wrist_roll    (0,       -0.0601,  0)       0.060 m
    gripper       (-0.0202, -0.0244,  0)       0.032 m

They sum to 0.343 m, to the pivot of the moving jaw, since this file has no gripper frame. That is
an upper bound the joints' limits never reach: a grid sweep of `elbow_flex`, `wrist_flex` and
`wrist_roll` through them puts the pivot at most 0.338 m from the `shoulder_lift` axis, which the
same sweep says is 0.411 m for the SO-101's gripper frame, as `REACH` records. So 0.33, rounded
down, and a short estimate: it says what the file shows and leaves the fingers out."""

_SO100_SHEET = Datasheet(
    dof=Figure(
        value=6,
        confidence="official",
        source=_SO100_URDF,
        note="five joints and a gripper",
    ),
    reach_m=_SO100_REACH,
    manipulator="gripper",
    arms=1,
    tethered=True,
    cannot=[
        "go anywhere: it is bolted to a table and has no base",
        f"reach anything more than about {_SO100_REACH.value:g} m from its shoulder to its "
        "gripper's pivot: that is as far as its joints let it, and most poses reach less",
        "feel what it holds: nothing reports grip force, so holding is inferred from the "
        "gripper stopping short of shut, which an empty hand that binds also does",
    ],
    notes=[
        "Nobody publishes what it lifts, its mass, how wide the gripper opens or how hard it grips",
        "No sensors beyond the servo positions unless a camera is configured, and the servo "
        "temperature, which quackd reads off the bus because LeRobot does not",
        "There are 7.4 V and 12 V builds of its servos, with different stall torque and "
        "different supplies. Which one is on the desk is not something quackd can ask",
        "No SO-100 has run quackd: it runs the code that drove an SO-101, through the same "
        "LeRobot class",
    ],
)

_KOCH_PAGE = "jess-moss/koch-v1-1 README, read 2026-11-05"

_KOCH_SHEET = Datasheet(
    dof=Figure(
        value=6,
        confidence="official",
        source=_KOCH_PAGE,
        note="five joints and a gripper, on two Dynamixel XL430-W250 and four XL330-M288",
    ),
    manipulator="gripper",
    arms=1,
    tethered=True,
    cannot=["go anywhere: it has no base"],
    notes=[
        "Its maker's page publishes no reach, payload or mass, so none is listed",
        "quackd drives no part of it. LeRobot has a class for it on a Dynamixel bus, and quackd "
        "has not read how that bus fails, which it does for the Feetech bus of an SO arm",
    ],
)


@dataclass(frozen=True)
class ArmType:
    """One of LeRobot's robot types, as this adapter treats it."""

    robot_type: str
    """LeRobot's registered name for it, which `--robot-type` takes."""
    config: str
    """LeRobot's config class for it, as `module.Class` (`up.SO101_CONFIG` and its like)."""
    model: str
    """What the manifest calls it."""
    blurb: str
    datasheet: Datasheet
    tier: Tier
    """How far it has got, in the catalogue's words, with who answers for it and when."""

    @property
    def drivable(self) -> bool:
        return self.tier.owner is not None and self.tier.date is not None

    @property
    def refusal(self) -> str:
        """Why `make()` will not build one, in the owner rule's words."""
        return (
            f"lerobot: {self.robot_type} is described and not driven: bound code needs a named "
            "owner and a date for its first hardware run (docs/adr/0076), and nobody has named "
            "either for this arm (docs/adapters/lerobot/README.md#which-arms)"
        )


ARMS: dict[str, ArmType] = {
    arm.robot_type: arm
    for arm in (
        ArmType(
            robot_type=ROBOT_TYPE,
            config=up.SO101_CONFIG.name,
            model="lerobot-so101",
            blurb=BLURB,
            datasheet=DATASHEET,
            tier=Tier(
                "verified",
                owner="rok",
                date="2026-09-15",
                note="one SO-101, driven on LeRobot 0.6.1 on 2026-09-15 and again on "
                "2026-09-23; LeRobot 0.7.0's bus has not met it",
            ),
        ),
        ArmType(
            robot_type=up.ROBOT_TYPE_SO100.name,
            config=up.SO100_CONFIG.name,
            model="lerobot-so100",
            blurb=(
                "a six-joint desktop robot arm with a parallel gripper (an SO-100, the SO-101's "
                "predecessor, driven by LeRobot), bolted to a table"
            ),
            datasheet=_SO100_SHEET,
            tier=Tier(
                "bound",
                owner="rok",
                date="2026-09-15",
                note="no SO-100 has run: it runs the code that drove the SO-101, through the same "
                "LeRobot class",
            ),
        ),
        ArmType(
            robot_type=up.ROBOT_TYPE_KOCH.name,
            config=up.KOCH_CONFIG.name,
            model="lerobot-koch",
            blurb=(
                "a six-joint desktop robot arm with a parallel gripper on Dynamixel servos (a Koch "
                "v1.1 follower), which quackd describes and does not drive"
            ),
            datasheet=_KOCH_SHEET,
            tier=Tier("described", note="data only, until an owner names a date"),
        ),
    )
}
"""Every arm type this adapter knows, the default first. `quackd.adapters.catalogue` lists the same
names as strings, for a machine with no adapter installed, and a test holds the two together."""


def arm_type(robot_type: str) -> ArmType:
    """The record for a type, or a refusal listing the ones that can be chosen, which are the ones
    that are built: a type that is only described is no choice anybody can make."""
    arm = ARMS.get(robot_type)
    if arm is None:
        built = ", ".join(name for name, one in ARMS.items() if one.drivable)
        raise AdapterError(f"lerobot has no arm type {robot_type!r}: choose one of {built}")
    return arm
