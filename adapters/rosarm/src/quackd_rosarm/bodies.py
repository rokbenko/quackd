"""The four industrial arms this family knows, as data (ADR-0075).

What each arm is, its maker, its mass, its payload and its reach, lives in one place: the YAML
under `quackd/adapters/bodies/` that the described row reads too (`described.body_data`). What a
YAML may not carry is what a driver answers to, since ADR-0077's loader refuses `limits`, `extras`
and the rest by name, and that is a record here: the driver, its joints in the controller's order,
the action, where the readings are, the status it publishes about itself, whether it replaces a
running goal, what a person does at the cell before the first goal, and the description the fake
serves. Every value is a stock bringup's default, read in the driver's own files at a pin. The
address sets the action, the readings topic and a prefix for every joint's name, and turns the
status read off (`up.STOCK_NAMES`).

The owner rule holds per body (ADR-0076). `ws` is the backend that reaches a real arm, and a body
with no owner and no date for its first hardware run is described there and refused at `make()`,
as the Koch's arm type is refused (`quackd_lerobot.arms`). All four are, today. The `mock` builds
every one: it is the same transport over a fake bridge and a fake arm, and it reaches nothing.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

from quackd.adapters.base import AdapterError
from quackd.adapters.catalogue import Tier
from quackd.upstream import UpstreamRef
from quackd_rosarm import upstream_api as up
from quackd_rosbridge.upstream_api import DESCRIPTION_PARAM, DESCRIPTION_TOPIC

ROBOT_TYPE = "ur5e"
"""The arm a robot is when nobody says, the first of the catalogue's robot types."""

Controller = Literal["jtc", "motoros2"]
StatusReader = Literal["ur", "kuka_events", "robot_status"]


@dataclass(frozen=True)
class Status:
    """What the driver publishes about itself, where quackd reads it, and how to read it."""

    reader: StatusReader
    topics: tuple[tuple[str, str], ...]
    """Each topic and its message type, in the order the reader takes them."""
    ref: UpstreamRef


@dataclass(frozen=True)
class DescribedJoint:
    """One joint of the description the fake serves: its travel and its rated speed, in
    radians and radians a second, as the arm's own description package gives them."""

    name: str
    lower: float
    upper: float
    velocity: float


def _deg(name: str, lower: float, upper: float, velocity: float) -> DescribedJoint:
    """A joint read in degrees, as two of the four description packages write them."""
    return DescribedJoint(name, math.radians(lower), math.radians(upper), math.radians(velocity))


DESCRIBED_ONLY = Tier("described", note="bound code, refused at make() until an owner names a date")
"""The tier of `ws` on a body nobody has named a date for, which is every body today."""


@dataclass(frozen=True)
class ArmBody:
    """One industrial arm, as this family drives it."""

    robot_type: str
    """The body file's name, which `--robot-type` takes and `described:` reaches too."""
    driver: str
    controller: Controller
    """What answers the action: a JointTrajectoryController, or MotoROS2's own server."""
    joints: tuple[str, ...]
    """In the controller's order, without the address's prefix."""
    action: str
    joint_states: str
    status: Status | None
    replaces_goal: bool
    """Whether a goal sent over a running one replaces it. quackd sends none either way."""
    before_a_goal: str
    """What the person at the cell does before the arm takes a goal, none of which quackd does."""
    readings: str | None
    """What `/joint_states` is on this body when it is not simply where the arm is."""
    description: tuple[DescribedJoint, ...]
    """The joints of the description the fake serves. A real bridge's own is read at connect."""
    rate_hz: float
    """How often the driver publishes `/joint_states`, which the fake keeps to as its clock
    allows."""
    tier: Tier
    """How far `ws` has got on this body, with who answers for it and when."""
    refs: tuple[UpstreamRef, ...]
    """Where each of the values above was read."""
    readings_are_commands: bool = False
    """Whether `/joint_states` reports what was sent rather than where the arm is: the iiwa's, under
    the stack's stock open_loop (`up.IIWA_OPEN_LOOP`)."""
    description_param: str = DESCRIPTION_PARAM.name
    description_topic: str = DESCRIPTION_TOPIC.name
    """Where the bridge is asked for the robot's description: the parameter first, the latched
    topic second, as the base adapter's introspection asks."""

    @property
    def drivable(self) -> bool:
        return self.tier.owner is not None and self.tier.date is not None

    @property
    def refusal(self) -> str:
        """Why `make()` will not build `ws` for this arm, in the owner rule's words."""
        return (
            f"rosarm:ws on a {self.robot_type} is described and not driven: bound code needs a "
            "named owner and a date for its first hardware run (docs/adr/0076), and nobody has "
            "named either for this arm; rosarm:mock rehearses it "
            "(docs/adapters/rosarm/README.md#status)"
        )


_UR_JOINTS = (
    "shoulder_pan_joint",
    "shoulder_lift_joint",
    "elbow_joint",
    "wrist_1_joint",
    "wrist_2_joint",
    "wrist_3_joint",
)
_SIX = tuple(f"joint_{n}" for n in range(1, 7))
_LBR = tuple(f"lbr_A{n}" for n in range(1, 8))

BODIES: dict[str, ArmBody] = {
    body.robot_type: body
    for body in (
        ArmBody(
            robot_type="ur5e",
            driver="ur_robot_driver, the Universal Robots ROS 2 driver, on Humble or Jazzy",
            controller="jtc",
            joints=_UR_JOINTS,
            action=up.UR_ACTION.name,
            joint_states="/joint_states",
            status=Status(
                "ur",
                (
                    ("/io_and_status_controller/robot_program_running", "std_msgs/msg/Bool"),
                    ("/io_and_status_controller/safety_mode", up.UR_SAFETY_MODES.name),
                ),
                up.UR_STATUS,
            ),
            replaces_goal=True,
            before_a_goal=(
                "the arm powered on and its brakes released on the pendant, and the External "
                "Control program playing, unless the driver runs headless"
            ),
            readings=None,
            # every joint 360 degrees each way but the elbow, which upstream halves for planning
            description=tuple(
                _deg(name, -span, span, 180.0)
                for name, span in zip(
                    _UR_JOINTS, (360.0, 360.0, 180.0, 360.0, 360.0, 360.0), strict=True
                )
            ),
            rate_hz=500.0,
            tier=DESCRIBED_ONLY,
            refs=(up.UR_ACTION, up.UR_JOINTS, up.UR_STATUS, up.UR_BEFORE_A_GOAL, up.UR_LIMITS),
        ),
        ArmBody(
            robot_type="kuka-lbr-iisy-3-r760",
            driver="kuka_rsi_driver from kuka-ros/kuka_drivers, built from source, on iiQKA.OS2",
            controller="jtc",
            joints=_SIX,
            action="/joint_trajectory_controller/follow_joint_trajectory",
            joint_states="/joint_states",
            status=Status(
                "kuka_events",
                (
                    (
                        "/event_broadcaster/hardware_event",
                        "kuka_driver_interfaces/msg/HardwareEvent",
                    ),
                ),
                up.IISY_EVENTS,
            ),
            replaces_goal=True,
            before_a_goal=(
                "robot_manager configured and activated, and then, within the ten seconds the "
                "driver waits, KUKA's RSI program started on the controller, on which the brakes "
                "release and external control starts"
            ),
            readings="positions alone: velocity and effort read NaN unless the RSI configuration "
            "maps them",
            description=(
                DescribedJoint("joint_1", -3.2288591, 3.2288591, 3.490656),
                DescribedJoint("joint_2", -4.0142572796, 0.8726646, 3.490656),
                DescribedJoint("joint_3", -2.61799387799, 2.61799387799, 3.490656),
                DescribedJoint("joint_4", -3.05432619, 3.05432619, 5.235988),
                DescribedJoint("joint_5", -1.919862177, 1.919862177, 5.235988),
                DescribedJoint("joint_6", -3.839724354, 3.839724354, 6.981310),
            ),
            rate_hz=250.0,
            tier=DESCRIBED_ONLY,
            refs=(
                up.IISY_JOINTS,
                up.IISY_EVENTS,
                up.IISY_EVENT_CODES,
                up.IISY_BEFORE_A_GOAL,
                up.IISY_NAN_READINGS,
                up.IISY_LIMITS,
            ),
        ),
        ArmBody(
            robot_type="kuka-lbr-iiwa-7-r800",
            driver="the LBR-Stack (lbr_fri_ros2_stack) over FRI, with LBRServer on the cabinet",
            controller="jtc",
            joints=_LBR,
            action=f"{up.IIWA_NAMESPACE.name}/joint_trajectory_controller/follow_joint_trajectory",
            joint_states=f"{up.IIWA_NAMESPACE.name}/joint_states",
            status=None,
            replaces_goal=True,
            before_a_goal=(
                "LBRServer launched on the smartPAD with the client's address, in T1 first, and "
                "the stack's hardware bringup running"
            ),
            readings="the commands sent, not the arm: the stack's stock open_loop is true",
            description=(
                _deg("lbr_A1", -169.0, 169.0, 98.0),
                _deg("lbr_A2", -119.0, 119.0, 98.0),
                _deg("lbr_A3", -169.0, 169.0, 100.0),
                _deg("lbr_A4", -119.0, 119.0, 130.0),
                _deg("lbr_A5", -169.0, 169.0, 140.0),
                _deg("lbr_A6", -119.0, 119.0, 180.0),
                _deg("lbr_A7", -174.0, 174.0, 180.0),
            ),
            rate_hz=100.0,
            tier=DESCRIBED_ONLY,
            readings_are_commands=True,
            description_param=f"{up.IIWA_NAMESPACE.name}{DESCRIPTION_PARAM.name}",
            description_topic=f"{up.IIWA_NAMESPACE.name}{DESCRIPTION_TOPIC.name}",
            refs=(
                up.IIWA_JOINTS,
                up.IIWA_NAMESPACE,
                up.IIWA_OPEN_LOOP,
                up.IIWA_BEFORE_A_GOAL,
                up.IIWA_LIMITS,
            ),
        ),
        ArmBody(
            robot_type="yaskawa-motomini",
            driver=f"MotoROS2 {up.MOTOROS2_VERSION} on the YRC1000micro, with its micro-ROS agent "
            "on a PC",
            controller="motoros2",
            joints=_SIX,
            action=f"/{up.MOTO_ACTION.name}",
            joint_states="/joint_states",
            status=Status(
                "robot_status",
                (("/robot_status", up.ROBOT_STATUS.name),),
                up.MOTO_STATUS,
            ),
            replaces_goal=False,
            before_a_goal=(
                "the controller in AUTO and REMOTE with no e-stop and no alarm, and trajectory "
                "mode started with start_traj_mode, which turns the servos on, and a "
                "robot_state_publisher serving Yaskawa's own MotoMini description, since "
                "MotoROS2 publishes none"
            ),
            readings=None,
            description=(
                _deg("joint_1", -170.0, 170.0, 315.0),
                _deg("joint_2", -85.0, 90.0, 315.0),
                _deg("joint_3", -50.0, 90.0, 420.0),
                _deg("joint_4", -140.0, 140.0, 600.0),
                _deg("joint_5", -30.0, 210.0, 600.0),
                _deg("joint_6", -360.0, 360.0, 600.0),
            ),
            rate_hz=100.0,
            tier=DESCRIBED_ONLY,
            refs=(
                up.MOTO_ACTION,
                up.MOTO_JOINTS,
                up.MOTO_STATUS,
                up.MOTO_BEFORE_A_GOAL,
                up.MOTO_LIMITS,
            ),
        ),
    )
}
"""Every arm this family knows, the default first. `quackd.adapters.catalogue` lists the same names
as strings, for a machine with no adapter installed, and a test holds the two together."""

ROBOT_TYPES: tuple[str, ...] = tuple(BODIES)


def arm_body(robot_type: str) -> ArmBody:
    """The record for a type, or a refusal listing the ones there are."""
    body = BODIES.get(robot_type)
    if body is None:
        raise AdapterError(
            f"rosarm has no arm type {robot_type!r}: choose one of {', '.join(ROBOT_TYPES)}"
        )
    return body


def ws_tier() -> Tier:
    """The catalogue's tier for `ws`: the best any body has, since the row has one per backend.
    A test holds the catalogue to this."""
    order = ("described", "rehearsed", "bound", "verified")
    return max((body.tier for body in BODIES.values()), key=lambda tier: order.index(tier.name))
