"""Core verbs: the vocabulary any robot can carry, and what each one requires.

`observe`, `report_state`, `stop`, `say`, `move`, `go_to`, `search_scan` and
`approach_and` exist on every robot whose manifest meets their requirement (a camera, a
`twist` intent, a `sound` intent). The composite ones are the steering loop, written as
ordinary Python: `go_to` closes the approach on detections at ~10 Hz so the LLM decides
*that* the robot should go to the ball and never *how*; `search_scan` turns in place on a
robot that can move and sweeps the head on one that can only look (ADR-0018).

The bodies of `move`, `go_to`, `search_scan` and `approach_and` are the 0.3 `walk`,
`walk_to`, `search_scan` and `approach_and`, renamed and otherwise untouched.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from PIL import Image
from pydantic import BaseModel, ConfigDict, Field

from quackd.perception.base import Detection, summarize_detections
from quackd.transport.base import Intent
from quackd.verbs.registry import NoParams, Verb, VerbContext, VerbResult

if TYPE_CHECKING:
    from quackd.adapters.manifest import RobotManifest

MOVE_RESEND_S = 0.1
MAX_VX = 0.3
MAX_VY = 0.2
MAX_WZ = 1.5
TICK_S = 0.1
TURN_RATE = 1.0  # rad/s used for scanning
DEFAULT_GAZE_LIMIT_DEG = 60.0

# ── parameters ──────────────────────────────────────────────────────────────────────────


class MoveParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    vx: float = Field(
        default=0.15, ge=-MAX_VX, le=MAX_VX, description="Forward m/s (negative = back)."
    )
    vy: float = Field(default=0.0, ge=-0.2, le=0.2, description="Left m/s (negative = right).")
    wz: float = Field(default=0.0, ge=-MAX_WZ, le=MAX_WZ, description="Turn rate rad/s (+ = left).")
    duration_s: float = Field(
        default=1.0, gt=0, le=10, description="How long to hold this velocity."
    )


class SayParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(..., min_length=1, max_length=200, description="What to say.")


class SearchScanParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: str = Field(default="ball", description="Label to look for (e.g. ball, person).")
    step_deg: float = Field(default=45.0, ge=15, le=120, description="Rotation per step.")
    max_steps: int = Field(
        default=8, ge=1, le=16, description="Steps before giving up (8 x 45° = full turn)."
    )


class GoToParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: str = Field(default="ball", description="Label to approach.")
    stop_distance: float = Field(
        default=0.25, ge=0.1, le=1.5, description="Stop this far away (m)."
    )
    timeout_s: float = Field(default=20.0, gt=0, le=60)


class ApproachAndParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: str = Field(default="ball")
    stop_distance: float = Field(default=0.25, ge=0.1, le=1.5)
    then: str = Field(..., description="Verb to run once close (e.g. kick, grab).")


# ── helpers shared with adapter verbs ───────────────────────────────────────────────────


async def send_or_fail(ctx: VerbContext, intent: Intent) -> VerbResult | None:
    """Send an intent; return a failure result if the robot refused it."""
    ack = await ctx.transport.send_intent(intent)
    if not ack.accepted:
        return VerbResult.fail(f"{intent.kind} refused: {ack.reason or 'no reason given'}")
    return None


async def _look_ahead(ctx: VerbContext) -> None:
    await ctx.transport.send_intent(Intent.look(1.0, 0.0, 0.0))


async def _see(
    ctx: VerbContext, label: str, caption: str
) -> tuple[Image.Image | None, list[Detection]]:
    img = await ctx.transport.get_frame()
    if img is None:
        return None, []
    ctx.on_frame(img, caption)
    dets = ctx.detector.detect(img) if ctx.detector else []
    return img, [d for d in dets if d.label == label]


def speed_limits(manifest: RobotManifest | None) -> tuple[float, float, float]:
    """(max_vx, max_vy, max_wz): the manifest's `limits` when it names them, else the
    schema bounds, which are the Microduck's, so its runs stay byte-identical."""
    limits = manifest.limits if manifest is not None else {}
    return (
        float(limits.get("max_vx", MAX_VX)),
        float(limits.get("max_vy", MAX_VY)),
        float(limits.get("max_wz", MAX_WZ)),
    )


def _clamped(ctx: VerbContext, vx: float, vy: float, wz: float) -> tuple[float, float, float]:
    max_vx, max_vy, max_wz = speed_limits(ctx.manifest)
    return (
        max(-max_vx, min(max_vx, vx)),
        max(-max_vy, min(max_vy, vy)),
        max(-max_wz, min(max_wz, wz)),
    )


async def _turn(ctx: VerbContext, radians: float) -> None:
    """Rotate in place by `radians`, feeding the deadman every tick."""
    rate = min(TURN_RATE, speed_limits(ctx.manifest)[2])
    wz = rate if radians >= 0 else -rate
    duration = abs(radians) / rate
    ticks = max(1, round(duration / TICK_S))
    for _ in range(ticks):
        await ctx.transport.send_intent(Intent.move(0.0, 0.0, wz))
        await ctx.transport.sleep(duration / ticks)
    await ctx.transport.stop()


# ── the verbs ───────────────────────────────────────────────────────────────────────────


async def observe(ctx: VerbContext, _: NoParams) -> VerbResult:
    img = await ctx.transport.get_frame()
    if img is None:
        return VerbResult.fail("this transport has no camera")
    ctx.on_frame(img, "observe")
    detections = ctx.detector.detect(img) if ctx.detector else []
    return VerbResult.success(
        f"frame captured; {summarize_detections(detections)}",
        detections=[d.model_dump() for d in detections],
    )


async def report_state(ctx: VerbContext, _: NoParams) -> VerbResult:
    state = await ctx.transport.get_state()
    return VerbResult.success(state.summary(), state=state.model_dump())


async def stop(ctx: VerbContext, _: NoParams) -> VerbResult:
    await ctx.transport.stop()
    # `stop` is the verb the pilot is told to reach for when anything looks wrong, and it is
    # asked for most often when the link is the thing that is wrong. A transport that knows it
    # could not deliver says so; reporting success anyway would be the one reassuring line in
    # the log that was not true. Transports that cannot tell report nothing and are unchanged.
    if undelivered := getattr(ctx.transport, "stop_error", None):
        return VerbResult.fail(
            f"stop could not be delivered: {undelivered}. If this robot has a deadman it is "
            "what stops the legs now; otherwise use the hardware switch."
        )
    return VerbResult.success("stopped (velocity zeroed)")


async def say(ctx: VerbContext, p: SayParams) -> VerbResult:
    """The generic voice: hand the text to the robot's sound channel. Robots without a voice
    override this (the Microduck picks one of its seven tones)."""
    if (fail := await send_or_fail(ctx, Intent.sound("say", p.text))) is not None:
        return fail
    return VerbResult.success(f"said {p.text!r}", text=p.text)


async def move(ctx: VerbContext, p: MoveParams) -> VerbResult:
    vx, vy, wz = _clamped(ctx, p.vx, p.vy, p.wz)  # the manifest's limits, if any
    slices = max(1, round(p.duration_s / MOVE_RESEND_S))
    step = p.duration_s / slices
    for _ in range(slices):
        if (fail := await send_or_fail(ctx, Intent.move(vx, vy, wz))) is not None:
            await ctx.transport.stop()
            return fail
        await ctx.transport.sleep(step)
    await ctx.transport.stop()
    clamped = (vx, vy, wz) != (p.vx, p.vy, p.wz)
    return VerbResult.success(
        f"walked vx={vx:.2f} vy={vy:.2f} wz={wz:.2f} for {p.duration_s:.1f}s"
        + (" (clamped to this robot's limits)" if clamped else ""),
        duration_s=p.duration_s,
    )


ScanMode = Literal["turn", "gaze", "none"]


def scan_mode(manifest: RobotManifest | None) -> ScanMode:
    """How this robot looks around: a bare transport and any mobile robot turn in place."""
    if manifest is None or (manifest.mobility != "none" and "twist" in manifest.intents):
        return "turn"
    if "gaze" in manifest.intents:
        return "gaze"
    return "none"


def _wrap_deg(deg: float) -> float:
    return (deg + 180.0) % 360.0 - 180.0


def gaze_sweep_yaws(
    centre_deg: float, step_deg: float, max_steps: int, limit_deg: float
) -> list[float]:
    """The head-sweep schedule: the current yaw, then outward alternation (+s, -s, +2s, ...),
    wrapped to (-180, 180], clipped to the head's range, duplicates dropped."""
    yaws: list[float] = []
    for k in range(max_steps + 1):
        n = (k + 1) // 2
        offset = 0.0 if k == 0 else (n * step_deg if k % 2 == 1 else -n * step_deg)
        yaw = _wrap_deg(centre_deg + offset)
        if abs(yaw) > limit_deg + 1e-9:
            continue
        if any(abs(_wrap_deg(yaw - seen)) < 1e-6 for seen in yaws):
            continue
        yaws.append(yaw)
    return yaws


async def _gaze_sweep(ctx: VerbContext, p: SearchScanParams, limit_deg: float) -> VerbResult:
    state = await ctx.transport.get_state()
    centre = float(state.extras.get("head_yaw_deg") or 0.0)
    yaws = gaze_sweep_yaws(centre, p.step_deg, p.max_steps, limit_deg)
    for i, yaw in enumerate(yaws):
        rad = math.radians(yaw)
        await ctx.transport.send_intent(Intent.look(math.cos(rad), math.sin(rad), 0.0))
        await ctx.transport.sleep(TICK_S)  # let the head settle; the sim clock advances
        img, hits = await _see(ctx, p.target, f"search_scan gaze {yaw:+.0f}")
        if img is None:
            return VerbResult.fail("this transport has no camera")
        if hits:
            best = hits[0]
            return VerbResult.success(
                f"{p.target} found: {best.summary()} (gaze {yaw:+.0f}°)",
                detections=[d.model_dump() for d in hits],
                steps=i,
                gaze_yaw_deg=yaw,
            )
    span = (len(yaws) - 1) * p.step_deg
    return VerbResult.fail(
        f"{p.target} not found in a gaze sweep of {len(yaws)} looks ({span:.0f}°)"
    )


async def search_scan(ctx: VerbContext, p: SearchScanParams) -> VerbResult:
    if ctx.detector is None:
        return VerbResult.fail("search_scan needs a detector (none configured)")
    mode = scan_mode(ctx.manifest)
    if mode == "gaze":
        limit = (ctx.manifest.limits.get("gaze_yaw_deg") if ctx.manifest else None) or (
            DEFAULT_GAZE_LIMIT_DEG
        )
        return await _gaze_sweep(ctx, p, float(limit))
    if mode == "none":
        return VerbResult.fail("search_scan needs locomotion or gaze; this robot has neither")
    await _look_ahead(ctx)
    for i in range(p.max_steps + 1):
        img, hits = await _see(ctx, p.target, f"search_scan {i}")
        if img is None:
            return VerbResult.fail("this transport has no camera")
        if hits:
            best = hits[0]
            return VerbResult.success(
                f"{p.target} found: {best.summary()} (after {i} turn steps)",
                detections=[d.model_dump() for d in hits],
                steps=i,
            )
        if i == p.max_steps:
            break
        await _turn(ctx, math.radians(p.step_deg))
        await ctx.transport.sleep(TICK_S)  # let the view settle
    return VerbResult.fail(
        f"{p.target} not found after {p.max_steps} steps ({p.max_steps * p.step_deg:.0f}°)"
    )


async def go_to(ctx: VerbContext, p: GoToParams) -> VerbResult:
    if ctx.detector is None:
        return VerbResult.fail("go_to needs a detector (none configured)")
    await _look_ahead(ctx)
    t0 = ctx.transport.now()
    lost = 0
    last_bearing = 0.0
    tick = 0
    while ctx.transport.now() - t0 < p.timeout_s:
        tick += 1
        img, hits = await _see(ctx, p.target, f"go_to {p.target}")
        if img is None:
            return VerbResult.fail("this transport has no camera")
        if not hits:
            lost += 1
            if lost > 30:
                await ctx.transport.stop()
                return VerbResult.fail(f"lost the {p.target}; try search_scan")
            # turn toward where it was last seen
            wz = 0.8 if last_bearing >= 0 else -0.8
            await ctx.transport.send_intent(Intent.move(*_clamped(ctx, 0.0, 0.0, wz)))
            await ctx.transport.sleep(TICK_S)
            continue
        lost = 0
        d = hits[0]
        bearing = d.bearing_deg or 0.0
        dist = d.est_distance_m
        last_bearing = bearing
        if dist is not None and dist <= p.stop_distance:
            await ctx.transport.stop()
            await ctx.transport.sleep(TICK_S)
            return VerbResult.success(
                f"reached the {p.target}: ~{dist:.2f} m away, bearing {bearing:+.0f}°",
                distance_m=dist,
                bearing_deg=bearing,
                ticks=tick,
            )
        wz = max(-1.0, min(1.0, bearing * 0.05))
        vx = 0.2 if abs(bearing) < 25 else 0.05
        if dist is not None and dist < p.stop_distance + 0.15:
            vx = min(vx, 0.1)  # creep in
        await ctx.transport.send_intent(Intent.move(*_clamped(ctx, vx, 0.0, wz)))
        await ctx.transport.sleep(TICK_S)
    await ctx.transport.stop()
    return VerbResult.fail(
        f"go_to timed out after {p.timeout_s:g}s without reaching the {p.target}"
    )


async def approach_and(ctx: VerbContext, p: ApproachAndParams) -> VerbResult:
    if ctx.run_verb is None:
        return VerbResult.fail("approach_and needs an executor")
    first = await ctx.run_verb("go_to", {"target": p.target, "stop_distance": p.stop_distance})
    if not first.ok:
        return first
    second = await ctx.run_verb(p.then, {})
    return VerbResult(
        ok=second.ok,
        summary=f"{first.summary}; then {p.then}: {second.summary}",
        data={"go_to": first.data, p.then: second.data},
    )


# ── requirements: what a manifest must offer for a core verb to exist ───────────────────


@dataclass(frozen=True)
class Requirement:
    intents: frozenset[str] = frozenset()
    """Every one of these intents."""
    any_intents: frozenset[str] = frozenset()
    """At least one of these intents."""
    mobility: bool = False
    """`mobility` must not be `none`."""
    camera: bool = False
    """`camera` must be among the sensors."""


REQUIREMENTS: dict[str, Requirement] = {
    "observe": Requirement(camera=True),
    "report_state": Requirement(),
    "stop": Requirement(),
    "say": Requirement(intents=frozenset({"sound"})),
    "move": Requirement(intents=frozenset({"twist"}), mobility=True),
    "go_to": Requirement(intents=frozenset({"twist"}), mobility=True, camera=True),
    "search_scan": Requirement(camera=True, any_intents=frozenset({"twist", "gaze"})),
    "approach_and": Requirement(intents=frozenset({"twist"}), mobility=True, camera=True),
}


def core_requirements_unmet(name: str, manifest: RobotManifest) -> str | None:
    """Why `name` cannot be a core verb of `manifest`, or None when it can."""
    req = REQUIREMENTS.get(name)
    if req is None:
        return "is not a core verb quackd knows (declare it with core: false)"
    missing: list[str] = []
    if req.camera and "camera" not in manifest.sensors:
        missing.append("a camera")
    if req.mobility and manifest.mobility == "none":
        missing.append("mobility")
    for intent in sorted(req.intents):
        if intent not in manifest.intents:
            missing.append(f"the {intent} intent")
    if req.any_intents and not (set(req.any_intents) & set(manifest.intents)):
        missing.append("one of the " + " or ".join(sorted(req.any_intents)) + " intents")
    return None if not missing else "needs " + ", ".join(missing)


# ── templates: what `registry_from_manifest` instantiates ────────────────────────────────

CORE: dict[str, Verb] = {
    v.name: v
    for v in (
        Verb(
            "observe",
            "Capture a camera frame and report what is detected in it.",
            observe,
            NoParams,
            timeout_s=10,
            read_only=True,
            core=True,
        ),
        Verb(
            "report_state",
            "Report the robot's state: posture, battery, pose, whatever it knows.",
            report_state,
            NoParams,
            timeout_s=5,
            read_only=True,
            core=True,
        ),
        Verb(
            "stop",
            "Stop moving immediately (zero velocity). Always allowed.",
            stop,
            NoParams,
            timeout_s=5,
            core=True,
        ),
        Verb("say", "Say something out loud.", say, SayParams, timeout_s=5, core=True),
        Verb(
            "move",
            "Move with a velocity for a duration. Use small values.",
            move,
            MoveParams,
            timeout_s=15,
            core=True,
            done_condition="the duration has elapsed and the robot has stopped.",
        ),
        Verb(
            "go_to",
            "Go toward a detected target and stop at a distance. Closes the loop on the "
            "camera itself.",
            go_to,
            GoToParams,
            timeout_s=70,
            kind="composite",
            core=True,
            done_condition="within stop_distance of the target, the target was lost, or timed out.",
        ),
        Verb(
            "search_scan",
            "Look around in steps for a target (turn in place, or sweep the head). Returns "
            "where it was seen.",
            search_scan,
            SearchScanParams,
            timeout_s=60,
            kind="composite",
            core=True,
            done_condition="the target was detected, or a full sweep found nothing.",
        ),
        Verb(
            "approach_and",
            "go_to a target, then run another verb (kick, grab).",
            approach_and,
            ApproachAndParams,
            timeout_s=90,
            kind="composite",
            core=True,
        ),
    )
}
