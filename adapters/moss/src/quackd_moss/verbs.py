"""MOSS's own verbs, and the named preconditions its manifest references.

Five verbs, none of them a core verb overridden. Two drive the base through the cockpit's own
planner (`drive_to`, `explore`), one hands the base and the arm to MOSS's litter mission for a
while (`collect_litter`), and two play the maker's arm cycle through quackd's bridge
(`pick_and_drop`, `stow_arm`). Each one sends an intent the transport maps to one surface, so
the mock and the real transport are driven the same way:

| intent | what it is |
|---|---|
| `pose` with x, y in the world frame | a goal for the planner, the cockpit's `/cmd/goal` |
| `do` explore, start or stop | the cockpit's `/cmd/explore` |
| `do` collect_litter, start | the explorer started for the mission |
| `do` arm_cycle, a pick pose and a phase to start from | the bridge's `/arm/cycle` |

Every rule below derives from a constant read upstream at the pin (`upstream_api`) or from
what the rover reports about itself. The numbers that are quackd's own are said to be.
"""

from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from quackd.transport.base import DuckState, Intent
from quackd.verbs.core import send_or_fail
from quackd.verbs.registry import NoParams, Precondition, Verb, VerbContext, VerbResult
from quackd_moss import upstream_api as up

# ── the vocabulary between the verbs and the transports ─────────────────────────────────

SKILL_EXPLORE = "explore"
SKILL_COLLECT = "collect_litter"
SKILL_ARM_CYCLE = "arm_cycle"
MISSION_STOPPED = (
    "the mission is stopped until the stack restarts: support the arm, then restart the stack "
    f"on the rover with {up.FLY_STOP.name} and {up.FLY_LITTER}"
)
"""What a stopped mission says, wherever it is met: the stop that ends the stack ends the
arm's run too, and with it the arm's torque."""

JOB_RUNNING = ("starting", "running")
"""An arm job the bridge is still playing."""
JOB_ENDINGS = ("done", "lag", "refused", "cancelled", "failed")
"""How a job ends: the script's own exit 0, its ARM LAG (4), a refusal by the script or by the
coordinator (2 and 3), a cancel quackd asked for, or anything else (`up.ARM_PLAY_EXIT_CODES`)."""

PICK_POSE_NAMES: tuple[str, ...] = tuple(
    name.strip().strip('"') for name in up.PICK_POSES.name.split(",")
)
"""The poses the cycle can play as its pick, as `config/arm_drop_poses.json` names them."""
STOW_FROM_PHASE = up.CYCLE_FROM_BACK_TO_CLEAR.name

# ── the numbers, each from the rover or from upstream ───────────────────────────────────

MAX_VX = 0.3
"""quackd's schema cap. MOSS's own full power is `up.MPS_AT_LIMIT` (0.32 m/s), above it, so the
schema binds here and not the rover."""
MAX_VY = 0.0
"""Two tracks: nothing slides sideways (`up.TWIST_TO_TRACKS` drops vy)."""
MAX_WZ = float(up.SERVO_W_MAX.name)
"""The litter mission's own fastest servo turn: the turn rate the maker drives this body at."""

STALE_PERIODS = 4
"""quackd's rule for a stream that has stopped, the same four periods its camera daemons and the
Microduck's frame expiry use: wide enough that ordinary jitter never trips it."""
MIN_STALE_S = 2.0
"""...floored, so a fast stream is not called stopped over one late message."""


def stale_after_s(period_s: float) -> float:
    """How old a reading of a stream with this period may be and still count as current."""
    return max(MIN_STALE_S, STALE_PERIODS * max(0.0, period_s))


TELEMETRY_LIMIT_S = stale_after_s(float(up.TELEMETRY_PERIOD_S.name))
"""Four of the coordinator's telemetry periods."""
FIRMWARE_LIMIT_S = float(up.IDLE_FIRMWARE_AGE_S.name)
"""The cockpit's own rule for a coordinator repeating its last line with the board lost."""
BATTERY_FLOOR_V = float(up.BATTERY_FLOOR_V.name)
"""The launcher's floor: the flight check refuses to start under it."""

DRIVE_TO_MAX_M = 2.0
"""quackd's own cap on how far one `drive_to` goal may be. Without the bridge nothing on the
rover halts a goal when quackd goes quiet, MOSS finishes it, so a goal is kept short enough that
finishing it is never far."""
GOAL_ECHO_TOL_M = 1e-3
"""The cockpit echoes a goal rounded to three decimals (`up.STATE_NAV`), so its own goal reads
back within half a millimetre. Anything further is somebody else's goal."""
DRIVE_TO_GIVE_UP_S = float(up.EXPLORER_GOAL_TIMEOUT_S.name) + float(up.GOAL_AFTER_STOP_S.name)
"""The explorer's own time for one goal, plus the cockpit's deferral while exploring."""
DRIVE_TO_POLL_S = 0.5

EXPLORE_MAX_MIN = 8.0
"""quackd's own cap on one exploration and one litter run. With the stop after it at its
slowest, VERB_MARGIN_S, it fits inside the 600 s a manifest may give one verb, and a test
holds that."""
EXPLORE_POLL_S = 1.0
"""Once a second, as `watch` does: how soon a brake pulled beside the verb is heard."""

ARM_GIVE_UP_S = float(up.PICK_TIMEOUT_S.name)
"""The mission gives one pick this long, and quackd gives a cycle the same."""
ARM_POLL_S = 0.5

VERB_MARGIN_S = 60.0
"""What a verb's executor timeout keeps beyond the verb's own give up, so the verb's own stop
runs and reports rather than the executor's. That stop is the transport's, which waits up to
the bridge's own worst case for its answer, and the contract tests hold this above that wait,
the state read before it and one poll."""

# ── which stacks quackd connects to ─────────────────────────────────────────────────────

KNOWN_STACKS = (up.NAV_STACK, up.EXPLORE_STACK, up.LITTER_STACK)


_START_ONE = f"start one with {up.FLY_STARTS}"
"""The launcher's own words for the three stacks quackd drives (moss-fly.sh usage)."""


def stack_refusal(
    *,
    running: bool,
    blueprint: str,
    bus: str,
    bridge: bool,
    mission: bool,
    mission_error: str | None = None,
    bridge_error: str | None = None,
) -> str | None:
    """Why quackd will not drive this stack, in a sentence that names the fix, or None.

    One function for both backends, so the mock refuses exactly what the rover does."""
    if not running:
        return (
            f"no stack is running on the rover: {_START_ONE}, without EXPLORE=1, which would "
            "set the rover exploring before quackd asks"
        )
    if not blueprint.startswith(up.BLUEPRINT_PREFIX):
        return (
            f"the running stack is {blueprint or 'unnamed'}, which is not one of MOSS's: "
            f"stop it and {_START_ONE}"
        )
    if blueprint in (up.GAMEPAD_STACK, up.BASE_STACK):
        return (
            f"{blueprint} is the pad and the base alone, with no odometry for quackd to read: "
            f"start {up.NAV_STACK} or {up.EXPLORE_STACK} ({up.FLY_BASE_STARTS})"
        )
    if blueprint == up.ARM_STACK:
        return (
            f"the cockpit reads only the newest dimOS run, and that is {up.ARM_STACK}, which hides "
            "any base stack from it: run quackd's bridge on the rover, which reads the base stack "
            f"and the arm's run apart, or with no base stack running, stop {up.ARM_STACK}, start "
            f"the base stack first ({up.FLY_STARTS}), then {up.ARM_STACK}"
        )
    if blueprint not in KNOWN_STACKS:
        return (
            f"quackd knows {up.NAV_STACK}, {up.EXPLORE_STACK} and {up.LITTER_STACK}, not "
            f"{blueprint}: stop it and "
            f"{_START_ONE}"
        )
    if bus != "lcm":
        return (
            f"the stack's bus is {bus or 'unknown'}, and the cockpit and quackd's bridge speak "
            f"LCM only: set {up.BUS_VAR_NAMES[0]} and {up.BUS_VAR_NAMES[1]} to lcm, or unset both"
        )
    if blueprint == up.LITTER_STACK and not bridge:
        why = f" ({bridge_error})" if bridge_error else ""
        return (
            f"on {up.LITTER_STACK} the mission listens to no stop the cockpit can send, and only "
            "quackd's bridge can call the mission's own: start the bridge on the rover "
            f"(bridge/moss/README.md){why}"
        )
    if blueprint == up.LITTER_STACK and not mission:
        return (
            f"on {up.LITTER_STACK} the mission's RPC does not answer the bridge "
            f"({mission_error or 'no reason given'}), so nothing could stop it: restart the stack"
        )
    return None


# ── params ──────────────────────────────────────────────────────────────────────────────


class DriveToParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x_m: float = Field(
        ...,
        ge=-DRIVE_TO_MAX_M,
        le=DRIVE_TO_MAX_M,
        description=(
            "Metres. In the body frame, forward of the rover (negative is behind). In the world "
            "frame, along the map's x axis from where the rover is now."
        ),
    )
    y_m: float = Field(
        ...,
        ge=-DRIVE_TO_MAX_M,
        le=DRIVE_TO_MAX_M,
        description=(
            "Metres. In the body frame, to the rover's left (negative is right). In the world "
            "frame, along the map's y axis from where the rover is now."
        ),
    )
    frame: Literal["body", "world"] = Field(
        default="body",
        description=(
            "body: forward and left of the rover. world: along the map's axes, as report_state's "
            "pose is. Either way the goal is at most 2 m from where the rover is."
        ),
    )


class MinutesParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    minutes: float = Field(
        default=1.0,
        gt=0,
        le=EXPLORE_MAX_MIN,
        description="How long to run, in minutes, at most 8. It always ends with a stop.",
    )


class PickAndDropParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pick_pose: up.PickPose = Field(
        default=up.PICK_POSE,
        description=(
            "Where the jaws close: pick for an object standing up, pick_low for one lying on "
            "the floor. Either way it is one fixed point in front of the rover."
        ),
    )


# ── preconditions the manifest references by name ───────────────────────────────────────


def _number(value: Any) -> float | None:
    if isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value):
        return float(value)
    return None


def _cockpit_fresh(state: DuckState) -> str | None:
    """The base's telemetry arrives from the coordinator every half second, and the cockpit
    stamps it on arrival. Older than four periods, the coordinator has stopped. A firmware line
    older than the cockpit's own one second rule is the coordinator repeating itself with the
    motor board gone."""
    age = _number(state.extras.get("telemetry_age_s"))
    if age is None:
        return (
            "the cockpit has no base telemetry from the coordinator, so nothing says the tracks "
            f"would answer: is the stack's base connected ({up.FLY_LOG})?"
        )
    limit = _number(state.extras.get("telemetry_limit_s")) or TELEMETRY_LIMIT_S
    if age > limit:
        return (
            f"the base's telemetry is {age:.1f} s old (stale after {limit:g} s): the "
            "coordinator has stopped publishing it"
        )
    firmware = _number(state.extras.get("firmware_age_s"))
    firmware_limit = _number(state.extras.get("firmware_limit_s")) or FIRMWARE_LIMIT_S
    if firmware is not None and firmware > firmware_limit:
        return (
            f"the motor board's last line is {firmware:.1f} s old: the coordinator is repeating "
            "it with the board lost. Check the QT Py's cable"
        )
    return None


def _pose_fresh(state: DuckState) -> str | None:
    """The pose is the lidar odometry's, once per revolution. Its freshness rule comes from the
    rate the cockpit itself measured on /odom."""
    age = _number(state.extras.get("pose_age_s"))
    if age is None or state.x is None or state.y is None:
        return "the lidar odometry has published no pose, so a goal has nothing to start from"
    limit = _number(state.extras.get("pose_limit_s")) or MIN_STALE_S
    if age > limit:
        return (
            f"the pose is {age:.1f} s old (stale after {limit:g} s at the rate /odom ran): the "
            "lidar odometry has stopped"
        )
    return None


def _battery_ok(state: DuckState) -> str | None:
    """Judged by volts, the launcher's own way: under its floor the flight check refuses."""
    volts = _number(state.extras.get("battery_v"))
    floor = _number(state.extras.get("battery_floor_v")) or BATTERY_FLOOR_V
    if volts is None:
        return (
            "the battery cannot be read: the telemetry carries no bus voltage (is the INA219 "
            "connected?), so nothing says the pack is above its cut"
        )
    if volts < floor:
        return (
            f"the battery reads {volts:.2f} V, under the {floor:g} V the launcher refuses to "
            "start at: charge it"
        )
    return None


def _not_stalled(state: DuckState) -> str | None:
    """The base's stall guard stops a commanded track whose encoder did not move. The next
    accepted twist clears it, so `move` stays allowed to back away."""
    if state.extras.get("stalled") is True:
        return (
            "the base's stall guard stopped the tracks against something: back away with move "
            "first, then try again"
        )
    return None


def _bridge_up(state: DuckState) -> str | None:
    if state.extras.get("bridge_up"):
        return None
    why = state.extras.get("bridge_error") or "it did not answer"
    return (
        f"quackd's bridge on the rover is not answering ({why}). Start it there "
        "(bridge/moss/README.md) and keep its port in the ssh tunnel"
    )


def _arm(state: DuckState) -> dict[str, Any]:
    arm = state.extras.get("arm")
    return arm if isinstance(arm, dict) else {}


def _arm_idle(state: DuckState) -> str | None:
    job = _arm(state).get("job")
    if isinstance(job, dict) and job.get("state") in JOB_RUNNING:
        return "the arm is playing a cycle: wait for it to end, or stop it"
    return None


def _arm_folded(state: DuckState) -> str | None:
    """Every verb that moves the base needs the arm in its drive pose, by the coordinator's own
    start tolerance, `collect_litter` among them, whose mission drives off at once. With no
    moss-arm run nothing reports the arm and nothing holds it, so this can only refuse an arm
    it can see. On moss-litter the mission owns the arm and quackd stows nothing, so the
    refusal says how to stow it there."""
    arm = _arm(state)
    if not arm.get("answering"):
        return None
    if arm.get("folded") is False:
        if state.extras.get("stack") == up.LITTER_STACK:
            return (
                f"the arm is out of its drive pose, and on {up.LITTER_STACK} the mission owns the "
                "arm, so quackd stows nothing there: stow it on the rover with the maker's own "
                f"cycle, {up.ARM_STOW} in the MOSS checkout, then ask again"
            )
        return (
            "the arm is out of its drive pose: stow_arm first, so the rover does not drive with "
            "it out"
        )
    return None


def _explore_stack(state: DuckState) -> str | None:
    stack = state.extras.get("stack")
    if stack == up.EXPLORE_STACK:
        return None
    return (
        f"the running stack is {stack or 'unknown'}, and only {up.EXPLORE_STACK} has the planner "
        f"and the explorer: {up.FLY_EXPLORE}"
    )


def _mission_up(state: DuckState) -> str | None:
    mission = state.extras.get("mission")
    if not isinstance(mission, dict) or not mission.get("answering"):
        if isinstance(mission, dict) and mission.get("stopped"):
            return MISSION_STOPPED
        return "the litter mission is not answering its RPC, so nothing could stop it"
    if mission.get("stopped"):
        return MISSION_STOPPED
    return None


def moss_conditions() -> dict[str, Precondition]:
    return {
        "cockpit_fresh": _cockpit_fresh,
        "pose_fresh": _pose_fresh,
        "battery_ok": _battery_ok,
        "not_stalled": _not_stalled,
        "bridge_up": _bridge_up,
        "arm_idle": _arm_idle,
        "arm_folded": _arm_folded,
        "explore_stack": _explore_stack,
        "mission_up": _mission_up,
    }


# ── the verbs ───────────────────────────────────────────────────────────────────────────


def _goal_of(nav: Any) -> tuple[float, float] | None:
    goal = nav.get("goal") if isinstance(nav, dict) else None
    if not isinstance(goal, dict):
        return None
    x, y = _number(goal.get("x")), _number(goal.get("y"))
    return None if x is None or y is None else (x, y)


def world_goal(state: DuckState, p: DriveToParams) -> tuple[float, float]:
    """The goal in the world frame, from the pose the cockpit reports."""
    x, y, theta = float(state.x or 0.0), float(state.y or 0.0), float(state.theta or 0.0)
    if p.frame == "world":
        return x + p.x_m, y + p.y_m
    c, s = math.cos(theta), math.sin(theta)
    return x + c * p.x_m - s * p.y_m, y + s * p.x_m + c * p.y_m


async def drive_to(ctx: VerbContext, p: DriveToParams) -> VerbResult:
    """A goal for MOSS's own planner, and a wait for the planner's own word on it.

    goal_reached on the cockpit is anybody's: the explorer sends goals too. So the verb trusts
    it only once the cockpit echoes the goal this verb sent, and fails when a goal it did not
    send replaces that one. It always ends with a stop, which also clears what quackd started."""
    away = math.hypot(p.x_m, p.y_m)
    if away > DRIVE_TO_MAX_M:
        return VerbResult.fail(
            f"drive_to: the goal is {away:.2f} m away and one goal may be at most "
            f"{DRIVE_TO_MAX_M:g} m: split it into shorter ones"
        )
    state = await ctx.transport.get_state()
    if state.x is None or state.y is None:
        return VerbResult.fail("drive_to needs the rover's pose, and the cockpit reports none")
    gx, gy = world_goal(state, p)
    nav, seen = state.extras.get("nav") or {}, state.extras.get("cockpit_nav")
    if nav.get("exploring") is True and isinstance(seen, dict) and not seen.get("exploring"):
        # the base stack explores and the cockpit cannot see it: it reads only the newest dimOS
        # run, a moss-arm started after the base, so it would send this goal straight under
        # the explorer. drive_to stops the explorer itself, and waits out its own stay here
        # goal, which the cockpit's deferral is for
        stop = Intent(kind="do", params={"skill": SKILL_EXPLORE, "action": "stop"})
        if (fail := await send_or_fail(ctx, stop)) is not None:
            return fail
        await ctx.transport.sleep(float(up.GOAL_AFTER_STOP_S.name))
    goal = Intent(kind="pose", params={"x": gx, "y": gy, "frame": "world"})
    if (fail := await send_or_fail(ctx, goal)) is not None:
        return fail
    t0 = ctx.transport.now()
    echoed = False
    ended: str | None = None
    try:
        while ctx.transport.now() - t0 < DRIVE_TO_GIVE_UP_S:
            if (why := ctx.interrupted()) is not None:
                ended = why
                break
            await ctx.transport.sleep(DRIVE_TO_POLL_S)
            state = await ctx.transport.get_state()
            nav = state.extras.get("nav") or {}
            seen = _goal_of(nav)
            mine = seen is not None and math.hypot(seen[0] - gx, seen[1] - gy) <= GOAL_ECHO_TOL_M
            if mine:
                echoed = True
            elif echoed and seen is not None:
                ended = (
                    f"another goal, ({seen[0]:.2f}, {seen[1]:.2f}), replaced drive_to's: "
                    "something else is driving the planner"
                )
                break
            if state.extras.get("stalled") is True:
                ended = "the base's stall guard stopped the tracks against something"
                break
            if echoed and nav.get("goal_reached") is True:
                left = None
                if state.x is not None and state.y is not None:
                    left = math.hypot(gx - state.x, gy - state.y)
                said = f", {left:.2f} m from it by the odometry" if left is not None else ""
                return VerbResult.success(
                    f"reached the goal ({gx:.2f}, {gy:.2f}) in the world frame{said}",
                    goal=[round(gx, 3), round(gy, 3)],
                    pose=[state.x, state.y, state.theta],
                    seconds=round(ctx.transport.now() - t0, 1),
                )
            if echoed and nav.get("goal_reached") is False:
                ended = "the planner gave the goal up"
                break
        else:
            ended = f"timed out after {DRIVE_TO_GIVE_UP_S:g} s"
    finally:
        await ctx.transport.stop()
    return VerbResult.fail(
        f"drive_to did not reach ({gx:.2f}, {gy:.2f}): {ended}. The rover is stopped.",
        goal=[round(gx, 3), round(gy, 3)],
        echoed=echoed,
    )


async def explore(ctx: VerbContext, p: MinutesParams) -> VerbResult:
    """MOSS's own explorer for a while, then its stop and the soft stop, every time."""
    start = Intent(kind="do", params={"skill": SKILL_EXPLORE, "action": "start"})
    if (fail := await send_or_fail(ctx, start)) is not None:
        return fail
    t0 = ctx.transport.now()
    seen_exploring = False
    ended = "the minutes ran out"
    finished = False
    try:
        while ctx.transport.now() - t0 < p.minutes * 60.0:
            if (why := ctx.interrupted()) is not None:
                ended = why
                break
            await ctx.transport.sleep(EXPLORE_POLL_S)
            state = await ctx.transport.get_state()
            if (refusal := _cockpit_fresh(state) or _battery_ok(state)) is not None:
                ended = refusal
                break
            nav = state.extras.get("nav") or {}
            if nav.get("explore_done") is True and seen_exploring:
                ended = "the explorer found no frontier left"
                finished = True
                break
            if nav.get("exploring") is True:
                seen_exploring = True
            elif seen_exploring:
                ended = "the explorer stopped by itself (a pad touch, or another stop)"
                break
    finally:
        stop = Intent(kind="do", params={"skill": SKILL_EXPLORE, "action": "stop"})
        await ctx.transport.send_intent(stop)
        await ctx.transport.stop()
    seconds = round(ctx.transport.now() - t0, 1)
    ok = finished or ended == "the minutes ran out"
    summary = f"explored for {seconds:g} s, then stopped: {ended}"
    return VerbResult(ok=ok, summary=summary, data={"seconds": seconds, "explore_done": finished})


def _mission(state: DuckState) -> dict[str, Any]:
    mission = state.extras.get("mission")
    return mission if isinstance(mission, dict) else {}


async def collect_litter(ctx: VerbContext, p: MinutesParams) -> VerbResult:
    """The maker's litter mission, given the rover for a while, then stopped by its own stop.

    The mission listens to no stop of quackd's: only its own RPC ends it, and once ended it
    stays ended until the stack restarts. So this runs once per stack start."""
    start = Intent(kind="do", params={"skill": SKILL_COLLECT, "action": "start"})
    if (fail := await send_or_fail(ctx, start)) is not None:
        return fail
    t0 = ctx.transport.now()
    states: list[str] = []
    status: dict[str, Any] = {}
    ended = "the minutes ran out"
    try:
        while ctx.transport.now() - t0 < p.minutes * 60.0:
            if (why := ctx.interrupted()) is not None:
                ended = why
                break
            await ctx.transport.sleep(EXPLORE_POLL_S)
            state = await ctx.transport.get_state()
            mission = _mission(state)
            if not mission.get("answering"):
                ended = "the mission stopped answering"
                break
            status = dict(mission.get("status") or {})
            now_state = str(status.get("state") or "")
            if now_state and (not states or states[-1] != now_state):
                states.append(now_state)
    finally:
        await ctx.transport.stop()
    seconds = round(ctx.transport.now() - t0, 1)
    after = _mission(await ctx.transport.get_state())
    undelivered = getattr(ctx.transport, "stop_error", None)
    picked = int(status.get("picked") or 0)
    skipped = int(status.get("skipped") or 0)
    data = {"seconds": seconds, "picked": picked, "skipped": skipped, "states": states}
    if undelivered or not after.get("stopped"):
        why = undelivered or "the bridge could not confirm it"
        return VerbResult.fail(
            f"the mission ran {seconds:g} s and picked {picked}, but its stop was not confirmed "
            f"({why}). Stop the stack on the rover with {up.FLY_STOP.name}, and support "
            "the arm first: that stop ends the arm's run too, which turns its torque off",
            **data,
        )
    return VerbResult.success(
        f"the litter mission ran {seconds:g} s ({ended}): picked {picked}, skipped {skipped}. "
        "It is stopped now, and runs again only after the stack restarts",
        **data,
    )


def _hold_sentence(held: Any) -> str:
    """What a reading of the arm after its cancel says, and nothing a reading did not."""
    if held is True:
        return " The arm holds where it stopped."
    if held is False:
        return (
            " The arm did not hold after its cancel, read moving or still executing: support it, "
            "or cut its power."
        )
    return " Whether the arm holds was not read: support it."


def arm_note(held: Any) -> str:
    """The close's word on an arm this session moved and left out of its drive pose:
    holding only after the hold was read, and otherwise what reading there was."""
    later = (
        f"The cockpit's idle stop, or {up.FLY_STOP.name}, will later end the arm's stack, "
        "and that turns its torque off"
    )
    if held is True:
        return (
            "the arm is holding where the run left it, out of its drive pose. "
            f"{later}: stow it first (stow_arm), or support it"
        )
    if held is False:
        return (
            "the arm is out of its drive pose and did not hold after its cancel, read moving or "
            "still executing: support it, or cut its power"
        )
    return (
        "the arm is out of its drive pose, and nobody read whether it holds there. "
        f"{later}: support it, or stow it (stow_arm)"
    )


async def _arm_cycle(
    ctx: VerbContext, what: str, pick_pose: str, from_phase: str | None
) -> VerbResult:
    params: dict[str, Any] = {"skill": SKILL_ARM_CYCLE, "pick_pose": pick_pose}
    if from_phase is not None:
        params["from_phase"] = from_phase
    if (fail := await send_or_fail(ctx, Intent(kind="do", params=params))) is not None:
        return fail
    job_id = getattr(ctx.transport, "arm_job_id", None)
    t0 = ctx.transport.now()
    job: dict[str, Any] = {}
    arm: dict[str, Any] = {}
    ended: str | None = None
    while ctx.transport.now() - t0 < ARM_GIVE_UP_S:
        if (why := ctx.interrupted()) is not None:
            ended = why
            break
        await ctx.transport.sleep(ARM_POLL_S)
        arm = _arm(await ctx.transport.get_state())
        got = arm.get("job")
        job = got if isinstance(got, dict) else {}
        if job_id is not None and job.get("id") != job_id:
            ended = "another arm job replaced this one"
            break
        if job.get("state") in JOB_ENDINGS:
            break
    else:
        ended = f"timed out after {ARM_GIVE_UP_S:g} s"
    if ended is not None:
        await ctx.transport.stop()
        held = (_arm(await ctx.transport.get_state()).get("job") or {}).get("held")
        return VerbResult.fail(f"{what} was stopped: {ended}.{_hold_sentence(held)}", job=job)
    ending = str(job.get("state"))
    reason = str(job.get("reason") or "")
    if ending == "done":
        folded = arm.get("folded")
        where = " The arm is back in its drive pose." if folded is True else ""
        return VerbResult.success(
            f"{what} done in {round(ctx.transport.now() - t0, 1):g} s.{where}",
            job=job,
            folded=folded,
        )
    # whatever else ended the cycle, the body is stopped before the verb says so: a script that
    # died may have left its trajectory playing, and the stop cancels it and reads the arm
    await ctx.transport.stop()
    held = (_arm(await ctx.transport.get_state()).get("job") or {}).get("held")
    said = f"{what} ended {ending}: {reason or 'no reason given'}.{_hold_sentence(held)}"
    return VerbResult.fail(said, job=job)


async def pick_and_drop(ctx: VerbContext, p: PickAndDropParams) -> VerbResult:
    """The maker's fixed pick and drop cycle, as delivered."""
    return await _arm_cycle(ctx, "pick_and_drop", p.pick_pose, None)


async def stow_arm(ctx: VerbContext, _: NoParams) -> VerbResult:
    """The cycle's own return from the bin, which ends in the drive pose."""
    return await _arm_cycle(ctx, "stow_arm", PICK_POSE_NAMES[0], STOW_FROM_PHASE)


def moss_verbs() -> dict[str, Verb]:
    verbs = [
        Verb(
            "drive_to",
            "Drive to a point at most 2 m away with MOSS's own planner, around what its map "
            "shows. x_m and y_m are forward and left in the body frame, or along the map's axes "
            "in the world frame. It ends with a stop whatever happens.",
            drive_to,
            DriveToParams,
            timeout_s=DRIVE_TO_GIVE_UP_S + VERB_MARGIN_S,
            done_condition="the planner said the goal was reached, gave it up, or it timed out.",
        ),
        Verb(
            "explore",
            "Let MOSS's own explorer drive into unknown space for some minutes, at most 8, "
            "within its own radius. It ends with the explorer's stop and a soft stop.",
            explore,
            MinutesParams,
            timeout_s=EXPLORE_MAX_MIN * 60.0 + VERB_MARGIN_S,
            safety_class="confirm",
        ),
        Verb(
            "collect_litter",
            "Hand the rover to MOSS's own litter mission for some minutes, at most 8: it "
            "explores, drives to any litter it confirms and runs the arm cycle by itself. It "
            "ends with the mission's own stop, and the mission then stays stopped until the "
            "stack restarts.",
            collect_litter,
            MinutesParams,
            timeout_s=EXPLORE_MAX_MIN * 60.0 + VERB_MARGIN_S,
            safety_class="dangerous",
        ),
        Verb(
            "pick_and_drop",
            "Play the maker's fixed pick and drop cycle, about 37 s: the arm leaves its drive "
            "pose, closes the jaws at one fixed point in front of the rover and drops whatever "
            "they hold in the bin. The arm reaches for nothing, so check the wrist view first.",
            pick_and_drop,
            PickAndDropParams,
            timeout_s=ARM_GIVE_UP_S + VERB_MARGIN_S,
            safety_class="confirm",
        ),
        Verb(
            "stow_arm",
            "Bring the arm back to its drive pose along the cycle's own return from the bin, "
            "first moving it over the bin if it is elsewhere. Nothing checks the scene on the "
            "way.",
            stow_arm,
            NoParams,
            timeout_s=ARM_GIVE_UP_S + VERB_MARGIN_S,
            safety_class="confirm",
        ),
    ]
    return {v.name: v for v in verbs}


__all__ = [
    "ARM_GIVE_UP_S",
    "BATTERY_FLOOR_V",
    "DRIVE_TO_GIVE_UP_S",
    "DRIVE_TO_MAX_M",
    "EXPLORE_MAX_MIN",
    "FIRMWARE_LIMIT_S",
    "JOB_ENDINGS",
    "JOB_RUNNING",
    "MAX_VX",
    "MAX_VY",
    "MAX_WZ",
    "MIN_STALE_S",
    "MISSION_STOPPED",
    "PICK_POSE_NAMES",
    "SKILL_ARM_CYCLE",
    "SKILL_COLLECT",
    "SKILL_EXPLORE",
    "STALE_PERIODS",
    "STOW_FROM_PHASE",
    "TELEMETRY_LIMIT_S",
    "DriveToParams",
    "MinutesParams",
    "PickAndDropParams",
    "moss_conditions",
    "moss_verbs",
    "stale_after_s",
    "world_goal",
]
