"""Record the README's simulator figure: the same sentence, the same duck, with and
without quackd.

This was the README hero until 2026-09-15, when a real SO-101 arm ran quackd and the
recording of that became the picture at the top of the page
([ADR-0038](../adr/0038-the-readme-hero-is-a-real-run.md)). It is still what argues for
the simulator, under *No robot yet? Try it in 60 seconds*, and it is still the one file
here that renders upstream's model and carries its CC BY-NC-SA terms.

Two runs of one arena, side by side.

**Left, quackd on.** A real run through the real loop. The scripted pilot is handed the goal
and the Microduck's verbs, picks one per turn, and the executor checks each against the
contract before the robot moves. Nobody writes a square: the pilot walks a leg, reads the
pose the robot actually reached, and corrects.

**Right, quackd off.** The identical world, the identical robot, the identical walking
policy, and no quackd. Nothing in a Microduck reads English. It takes a twist, which is
three numbers, and nine learned policies, so a typed sentence has nowhere to go and the duck
stands there. That is not a rigged comparison against a worse model. There is no model,
because before quackd there was nowhere to put one.

A chase camera on a 25 cm robot shows a duck walking rather than a square, so each pane
carries a top-down inset of the path so far. The inset is a plot drawn over the frame, not
part of the simulation.

    uv run python docs/assets/hero3d.py        # writes docs/assets/quackd-on-off.gif

Needs `quackd[mujoco]`. The first run downloads upstream's model and policies into
`~/.quackd/cache` (about 10 MB) and prints their licence. Nothing of theirs is written here
except the frames of this recording, which render a CC BY-NC-SA model and are labelled in
`docs/assets/README.md` and `docs/reference/licenses.md`.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import math
import sys
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from quackd.adapters.factory import parse_robot_spec, registry_for
from quackd.agent.loop import RunConfig, run_duck
from quackd.agent.providers.fake import FakeProvider
from quackd.duckfile.parser import duck_from_goal
from quackd.perception.color_blob import ColorBlobDetector
from quackd_microduck import MicroduckAdapter
from quackd_microduck.sim3d.render import render_overview
from quackd_microduck.sim3d.scene import ARENA_HALF
from quackd_microduck.sim3d.world import CONTROL_DT, MujocoWorld
from quackd_microduck.transports.mujoco import MujocoTransport

GOAL = "walk in a square, half a metre a side"
SAMPLE_S = 0.4  # sim seconds between recorded frames
MAX_BYTES = 2_097_152  # 2048 KB, exactly the --maxkb the pre-commit hook is configured with

CAPTION_H = 34
GUTTER = 3
INSET_FRACTION = 0.36
INK = (238, 238, 238)
DIM = (150, 156, 164)
DUCK_YELLOW = (245, 197, 66)
OFF_ORANGE = (255, 157, 92)
BACKDROP = (22, 24, 28)
FLOOR = (14, 16, 19)
WALL = (78, 84, 92)
TRAIL = (255, 170, 60)
BALL = (255, 140, 0)


def inset(frame: Image.Image, world: Any, trail: list[tuple[float, float]], size: int) -> None:
    """Draw the arena from above, with the path walked so far, into the frame's corner."""
    box = int(size * INSET_FRACTION)
    left = top = 6
    draw = ImageDraw.Draw(frame)
    draw.rectangle([left, top, left + box, top + box], fill=FLOOR, outline=WALL)

    def at(x: float, y: float) -> tuple[float, float]:
        scale = box / (2 * ARENA_HALF)
        return left + (x + ARENA_HALF) * scale, top + (ARENA_HALF - y) * scale

    marks = [(world.ball_x, world.ball_y, BALL, 2)]
    for x, y, colour, radius in marks:
        cx, cy = at(x, y)
        draw.ellipse([cx - radius, cy - radius, cx + radius, cy + radius], fill=colour)
    if len(trail) > 1:
        draw.line([at(x, y) for x, y in trail], fill=TRAIL, width=2, joint="curve")
    cx, cy = at(world.x, world.y)
    draw.ellipse([cx - 2, cy - 2, cx + 2, cy + 2], fill=(255, 255, 255))


class PaneRecorder:
    """One pane: the overview render, sampled on sim time, with the path drawn on it."""

    def __init__(self, size: int, world: Any = None) -> None:
        self.world = world  # the transport builds its world in connect(), so this arrives late
        self.size = size
        self.frames: list[Image.Image] = []
        self.trail: list[tuple[float, float]] = []
        self._last = -1e9

    def maybe_capture(self, world: Any | None = None) -> None:
        world = world if world is not None else self.world
        self.world = world
        if world.t - self._last < SAMPLE_S:
            return
        self._last = world.t
        self.trail.append((world.x, world.y))
        frame = render_overview(world, self.size)
        inset(frame, world, self.trail, self.size)
        self.frames.append(frame)

    @property
    def walked(self) -> float:
        return sum(math.dist(a, b) for a, b in zip(self.trail, self.trail[1:], strict=False))


async def run_with_quackd(seed: int, size: int) -> PaneRecorder:
    """The left pane: the whole loop, the scripted pilot, the contract, the robot's gait."""
    spec = parse_robot_spec("microduck:mujoco")
    allowed = [v.name for v in registry_for(spec).verbs() if v.safety_class == "safe"]
    transport = MujocoTransport(seed=seed, body="microduck")
    adapter = MicroduckAdapter(transport)
    pane = PaneRecorder(size)

    def hook(world: Any) -> None:
        pane.maybe_capture(world)

    transport.add_tick_hook(hook)
    result = await run_duck(
        RunConfig(
            duck=duck_from_goal(GOAL, allowed),
            provider=FakeProvider.for_duck("", goal=GOAL),
            transport=adapter,
            detector=ColorBlobDetector(),
            runs_dir="runs",
        )
    )
    print(f"  quackd on:  {result.outcome} in {result.steps} steps, {result.reason[:60]}")
    if result.outcome != "success":
        raise SystemExit("the left pane must be a successful run")
    return pane


def run_without_quackd(seed: int, size: int, seconds: float) -> PaneRecorder:
    """The right pane: the same world and the same policy, and nothing that reads English.

    The duck is sent no twist at all, which is what a Microduck receives when no brain is
    attached to it. Its standing policy keeps it upright, and that is the whole behaviour.
    """
    world = MujocoWorld(seed=seed, body="microduck")
    pane = PaneRecorder(size, world)
    for _ in range(round(seconds / CONTROL_DT)):
        world.step()
        pane.maybe_capture()
    print(f"  quackd off: stood for {world.t:.0f} s and walked {pane.walked:.2f} m")
    return pane


def compose(left: PaneRecorder, right: PaneRecorder, size: int) -> list[Image.Image]:
    count = min(len(left.frames), len(right.frames))
    if not count:
        raise SystemExit("no frames recorded")
    width = size * 2 + GUTTER
    out = []
    for i in range(count):
        frame = Image.new("RGB", (width, size + CAPTION_H), BACKDROP)
        frame.paste(left.frames[i], (0, CAPTION_H))
        frame.paste(right.frames[i], (size + GUTTER, CAPTION_H))
        draw = ImageDraw.Draw(frame)
        draw.text((7, 5), "quackd ON", fill=DUCK_YELLOW)
        draw.text((7, 18), f'you type: "{GOAL.split(",")[0]}"', fill=INK)
        draw.text((size + GUTTER + 7, 5), "quackd OFF", fill=OFF_ORANGE)
        draw.text((size + GUTTER + 7, 18), "same duck, same sentence, no brain", fill=DIM)
        out.append(frame)
    return out


def save(frames: list[Image.Image], path: Path, *, keep: int, colours: int, fps: int) -> Path:
    if len(frames) > keep:
        stride = len(frames) / keep
        frames = [frames[int(i * stride)] for i in range(keep)]
    palette = [f.quantize(colors=colours, method=Image.Quantize.MEDIANCUT) for f in frames]
    hold = [1000 // fps] * len(palette)
    hold[-1] = 2000  # linger on the finished square beside the duck that never moved
    path.parent.mkdir(parents=True, exist_ok=True)
    palette[0].save(path, save_all=True, append_images=palette[1:], duration=hold, loop=0)
    return path


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="docs/assets/quackd-on-off.gif")
    parser.add_argument("--seed", type=int, default=6)
    parser.add_argument("--pane", type=int, default=288, help="pixels per pane")
    parser.add_argument("--keep", type=int, default=84, help="frames to keep")
    parser.add_argument("--colours", type=int, default=64)
    parser.add_argument("--fps", type=int, default=10)
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(message)s")

    left = await run_with_quackd(args.seed, args.pane)
    if left.world is None:
        # `PaneRecorder.world` is set by the first capture, so without this the next line
        # raises an AttributeError about NoneType rather than saying what went wrong.
        raise SystemExit("no frames were captured: the tick hook never ran, or rendering failed")
    right = run_without_quackd(args.seed, args.pane, seconds=left.world.t)
    out = save(
        compose(left, right, args.pane),
        Path(args.out),
        keep=args.keep,
        colours=args.colours,
        fps=args.fps,
    )
    size = out.stat().st_size
    # `n_frames` exists on the multi-frame plugins, not on the `ImageFile` base the stubs
    # declare, and this is always a GIF because `save_gif` wrote it.
    saved = getattr(Image.open(out), "n_frames", 0)
    print(f"{out} — {size / 1000:.0f} kB, {saved} frames")
    print(f"  left walked {left.walked:.2f} m, right walked {right.walked:.2f} m")
    if size > MAX_BYTES:
        print("too big for the pre-commit cap: lower --keep or --pane", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
