"""Whether the policy a server serves fits the arm about to be handed to it, asked at connect.

The arm's side asks `GET /v1/policy` once its cameras are open and before its motors are
energised (`real.LeRobotReal._fit_policy`), and refuses to connect over a policy that could not
drive this arm, in a sentence that says what to do. Nothing here is typed in: the motors are
the bus's, in its order, each camera's size is a frame it gave, the travel is read off this
arm's calibration file, and the slack past it is the backend's own (`real.OUT_OF_RANGE_DEG`).

- **The state and the action** are as long as the bus has motors, and where the checkpoint
  names its action's dimensions (`upstream_api.ACTION_FEATURE_NAMES`), they are the bus's
  motors in the bus's order. Where it does not, the order is trusted rather than checked: the
  policy's state and action are taken to be in the order of the bus that recorded its data.
- **The cameras.** Every image a policy looks at needs a camera of this arm's mapped to it,
  unless the policy pads a missing one (`upstream_api.MISSING_IMAGES_PADDED`), which is said
  instead. A frame whose height and width are not the image's is refused, and the refusal says
  to give the camera that size, which every camera URL takes. A caller from Python may accept
  it instead (`accept_frame_size`), knowing an ACT runs on another size and sees what it never
  saw.
- **The frame.** The 1st and 99th percentiles of the state the policy learned from lie inside
  this arm's calibrated travel. A policy trained on an arm calibrated another way asks for
  goals that pin this one at its limits, so it is refused, unless the person running it knows
  better (`accept_other_frame`, which `--accept-other-frame` sets). Its goals are clipped to
  this arm's travel either way, so the override lets the policy connect and never moves the
  arm anywhere it could not go without it.
- **The latency** the server declares is one its chunks can carry, at most half a chunk, as
  `roboglot policy serve` holds it to (`latency_too_long`). A segment asks for the next chunk
  only once the last has landed, so past half a chunk the arm has nothing to play for part of
  every chunk. `serve` refuses such a latency, and a server an earlier Roboglot started may still
  declare one, so the connect refuses it too, naming the ticks every chunk would leave the arm
  nothing to play for and the longest latency that fits. Nothing overrides it.

This module needs nothing but the protocol's messages and the loop's arithmetic
(`loop.starved_each_chunk`), so it runs in the arm's process, which never imports torch.
`roboglot policy serve` and `roboglot policy check` judge a latency with the same functions.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from roboglot_lerobot.policy import protocol as wire
from roboglot_lerobot.policy.loop import latency_ticks, longest_latency, starved_each_chunk

POS = ".pos"
"""What a motor's name is followed by in LeRobot's observation and action keys."""
LATENCY_STEP_S = 0.01
"""What a measured latency is rounded up to a whole number of, so the `--latency-s` a bench
suggests is never shorter than what was measured, and reads as a figure somebody would type."""


class PolicyMisfit(ValueError):
    """A policy that does not fit this arm, in one sentence that says what to do."""


@dataclass(frozen=True)
class Fit:
    """What the check found: why the arm must not connect over this policy, or None, and what
    the record should say either way."""

    refusal: str | None
    notes: tuple[str, ...] = ()


def _joined(names: Sequence[str]) -> str:
    return ", ".join(names) if names else "none"


def _ticks(n: int) -> str:
    return f"{n} tick" if n == 1 else f"{n} ticks"


# ── what a latency leaves a segment to play ─────────────────────────────────────────────


def chunk_outrun(latency_s: float, rate_hz: float, chunk: int, per_tick: bool) -> bool:
    """Whether a chunk of `chunk` actions that takes `latency_s` to come back lands after its
    last action's tick. The loop plays a chunk's actions from the tick it lands at, so an arm
    would play none of them. A policy that answers one action a tick is never outrun this way."""
    return not per_tick and latency_ticks(latency_s, rate_hz) >= chunk


def chunk_starved(latency_s: float, rate_hz: float, chunk: int, per_tick: bool) -> int:
    """How many ticks of every chunk after a segment's first the arm would have nothing to play
    for, for a policy whose chunks play `chunk` actions and that takes `latency_s` to answer:
    the loop's own rule (`loop.starved_each_chunk`), so what is refused here is what a segment
    would starve on. A policy that answers one action a tick is asked every tick and has to
    answer within one, which the loop holds it to itself."""
    if per_tick:
        return 0
    return starved_each_chunk(latency_ticks(latency_s, rate_hz), chunk)


def longest_latency_s(rate_hz: float, chunk: int) -> float:
    """The longest `--latency-s` a policy whose chunks play `chunk` actions can be served with
    at `rate_hz`: half a chunk, in whole ticks, the most the loop plays without starving
    (`loop.longest_latency`), rounded down to the `LATENCY_STEP_S` a bench suggests one in."""
    return math.floor(longest_latency(chunk) / rate_hz / LATENCY_STEP_S + 1e-9) * LATENCY_STEP_S


def latency_too_long(latency_s: float, rate_hz: float, chunk: int, per_tick: bool) -> str | None:
    """Why a policy whose chunks play `chunk` actions cannot be played with `latency_s` to
    answer, said as what would happen to its chunks, or None where the loop plays it without
    starving. `roboglot policy serve` refuses such a latency, `roboglot policy check` says so of a
    server started with one and of any latency its bench would suggest, and the arm's connect
    refuses a server that declares one (`fit`)."""
    late = latency_ticks(latency_s, rate_hz)
    if chunk_outrun(latency_s, rate_hz, chunk, per_tick):
        return "every chunk would land after its last action's tick and none would play"
    if starved := chunk_starved(latency_s, rate_hz, chunk, per_tick):
        return (
            f"a segment asks for the next chunk only once the last has landed, "
            f"{_ticks(late)} into it, so what is left of it lasts {chunk - late} of the "
            f"{_ticks(late)} the next one takes to land, and the arm would have nothing to play "
            f"for {_ticks(starved)} of every chunk"
        )
    return None


def _latency_misfit(info: wire.PolicyInfo) -> str | None:
    """Why no segment could play the latency the server declares, said after the policy's
    name, or None. `roboglot policy serve` refuses such a latency, and a server an earlier Roboglot
    started may still declare one, whatever policy it serves."""
    rate, chunk = float(info.rate_hz), info.n_action_steps
    why = latency_too_long(info.latency_s, rate, chunk, info.per_tick)
    if why is None:
        return None
    return (
        f"declares {info.latency_s:g} s to answer, {_ticks(latency_ticks(info.latency_s, rate))} "
        f"at {rate:g} Hz, and each chunk plays {chunk} actions, one a tick, so {why}. roboglot "
        "policy serve refuses such a latency, and a server an earlier Roboglot started may still "
        "declare one: start it again with a --latency-s of at most "
        f"{longest_latency_s(rate, chunk):g} s, half a chunk's {_ticks(longest_latency(chunk))}, "
        "or serve the policy where it answers faster"
    )


def fit(
    info: wire.PolicyInfo,
    *,
    motors: Sequence[str],
    cameras: Sequence[wire.CameraInfo],
    travel: Mapping[str, tuple[float, float]],
    slack_deg: float,
    where: str,
    accept_frame_size: bool = False,
    accept_other_frame: bool = False,
) -> Fit:
    """Whether the policy `info` describes fits an arm whose bus lists `motors`, whose cameras
    gave `cameras`, and whose calibration gives `travel` (degrees, the gripper 0..100), with
    `slack_deg` of reading past the travel forgiven. `where` is the server, as it is named in a
    sentence. The first thing that does not fit is the refusal; everything else goes in the
    notes, the overrides taken included."""
    features = info.features
    notes = [
        f"the policy at {where} is {info.policy}, at {info.rate_hz:g} Hz from {info.rate_source}"
    ]
    if info.loaded:
        notes.append(f"the policy server loaded {'; '.join(info.loaded)}")

    def refused(why: str) -> Fit:
        return Fit(f"the policy at {where}, {info.policy}, {why}", tuple(notes))

    # the latency, which is the server's and no arm's: one no segment could play
    if (misfit := _latency_misfit(info)) is not None:
        return refused(misfit)

    # the state and the action, by count and, where the checkpoint says, by name
    for what, size in (("state", features.state), ("action", features.action)):
        if size is not None and size != len(motors):
            return refused(
                f"takes {'a' if what == 'state' else 'an'} {what} of {size} numbers, and this "
                f"arm's bus has {len(motors)} motors ({_joined(motors)}): it learned from another "
                "arm, so serve a checkpoint trained on this one"
            )
    if features.action_names is not None:
        named = [name.removesuffix(POS) for name in features.action_names]
        if named != list(motors):
            return refused(
                f"names its actions {_joined(named)}, and this arm's bus lists "
                f"{_joined(motors)}: its goals would go to the wrong joints"
            )

    # the cameras: every image needs one, unless the policy pads it, and one of its size
    given = {camera.name: camera for camera in cameras}
    seen = {key: given[name] for name, key in info.cameras.items() if name in given}
    if features.images and not any(image.key in seen for image in features.images):
        return refused(
            f"looks at {_joined([i.key for i in features.images])}, and no camera of this arm "
            f"({_joined(sorted(given))}) is mapped to any of them: give the arm the cameras it "
            "was trained with, or start the server with --cameras NAME=KEY for the ones it has"
        )
    for image in features.images:
        camera = seen.get(image.key)
        if camera is None:
            if not features.pads_images:
                return refused(
                    f"looks at {image.key}, and no camera of this arm "
                    f"({_joined(sorted(given))}) is mapped to it: give the arm the camera it "
                    f"was trained with, or start the server with --cameras NAME={image.key}"
                )
            notes.append(f"{image.key} has no camera on this arm, so the policy sees it padded")
            continue
        if image.height is None or image.width is None:
            continue
        if (camera.height, camera.width) != (image.height, image.width):
            said = (
                f"the {camera.name} camera gives {camera.width}x{camera.height} frames, and the "
                f"policy learned {image.key} at {image.width}x{image.height}"
            )
            if not accept_frame_size:
                return refused(
                    f"cannot use them as they come: {said}. Give the camera that size with "
                    f"--camera-url's width={image.width} and height={image.height}"
                )
            notes.append(f"{said}, which was accepted (accept_frame_size)")

    # the frame: what the policy learned from lies inside this arm's travel
    quantiles = info.state_quantiles
    unchecked = "so whether it learned from an arm calibrated like this one was not checked"
    if quantiles is None:
        if features.state is not None:
            notes.append(f"the policy reports no state quantiles, {unchecked}")
    elif len(quantiles.q01) != len(motors):
        notes.append(
            f"the policy reports {len(quantiles.q01)} state quantiles for {len(motors)} "
            f"motors, {unchecked}"
        )
    elif not travel:
        notes.append(f"this arm has no calibrated travel to hold them against, {unchecked}")
    else:
        outside = []
        for motor, low, high in zip(motors, quantiles.q01, quantiles.q99, strict=True):
            span = travel.get(motor)
            if span is None:
                continue
            if low < span[0] - slack_deg or high > span[1] + slack_deg:
                outside.append(
                    f"{motor} {low:.1f}..{high:.1f} against {span[0]:.1f}..{span[1]:.1f}"
                )
        if outside:
            said = (
                f"learned from an arm whose readings (q01..q99) lie outside this arm's "
                f"calibrated travel: {'; '.join(outside)}"
            )
            if not accept_other_frame:
                return refused(
                    f"{said}. It was trained on an arm calibrated another way, and its goals "
                    "would pin this one at its limits: serve a checkpoint trained on this arm, "
                    "or give --accept-other-frame if you know the two frames match, which lets "
                    "it connect with every goal still clipped to this arm's travel"
                )
            notes.append(f"the policy {said}, which was accepted (--accept-other-frame)")
        missing = [m for m in motors if m not in travel]
        if missing:
            notes.append(
                f"{_joined(missing)} {'has' if len(missing) == 1 else 'have'} no travel in the "
                "calibration, so the policy's readings there were not checked"
            )
    return Fit(None, tuple(notes))


__all__ = [
    "LATENCY_STEP_S",
    "Fit",
    "PolicyMisfit",
    "chunk_outrun",
    "chunk_starved",
    "fit",
    "latency_too_long",
    "longest_latency_s",
]
