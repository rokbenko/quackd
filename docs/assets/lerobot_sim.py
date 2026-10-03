"""Record `docs/assets/lerobot-sim.gif`: the arm's simulator, told what the real arm was told.

The README's hero is a phone pointed at an SO-101 on a bench, told *wave to the camera with an
extended arm*. This is the same sentence on `lerobot:mujoco`, the arm's simulator, which is the
real backend's own code over the maker's model of the arm in MuJoCo. It is a real run through
the real loop: the pilot is handed the goal and the arm's verbs, picks one per turn, and the
executor checks each against the contract before the simulated arm moves. The robot is a bare
`lerobot:mujoco` with no calibration file, the generic arm whose travel is the model's own, so
anybody can run this and get the arm the recording shows.

Two views, side by side. **Left**, a fixed view of the arm on its table from in front and to
one side, which no camera in the run has. **Right**, the scene's `front` camera, the one the run
opens and sends the pilot its frames from, drawn at the same moments as the left. A strip above
them names the verb that was running.

Frames are taken on the simulator's clock and nowhere else: a tick hook on the clock draws both
views every `SAMPLE_S` of simulated time, so a frame exists only while the world steps. A
pilot's thinking costs the simulator no time at all (`quackd_lerobot/sim/clock.py`), so the
recording skips it, and the drawing happens on the event loop's thread, the one a GL context
there belongs to, between two steps of a clock that does not advance while it waits.

    uv run --extra lerobot-sim python docs/assets/lerobot_sim.py
    uv run --extra lerobot-sim --extra openai python docs/assets/lerobot_sim.py --llm gpt-6-sol

Without `--llm` the pilot is a script built below, as `hero3d.py` builds its own, so the first
line needs no key. `--llm` takes what `quackd run --llm` takes, and that vendor's key in the
environment: the second line is OpenAI's `gpt-6-sol`, the pilot the committed file flew with.
Memory is off, as `quackd run --no-memory` has it. The run's directory is written under
`--runs-dir` like any other, transcript and frames included, and a run that does not declare
success writes no GIF.

Needs `quackd[lerobot-sim]`. The first run downloads the SO-101's model, the maker's own from
TheRobotStudio's SO-ARM100 at a pinned commit (about 16 MB), into `~/.quackd/cache`, checks
each file against its sha256 and prints its licence, which is Apache-2.0. Nothing of theirs is
written here except the frames of this recording, which render that model and are labelled in
`docs/assets/README.md` and `docs/reference/licenses.md`.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import math
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from quackd.adapters.factory import make_adapter, parse_robot_spec, registry_for
from quackd.agent.loop import RunConfig, RunResult, run_duck
from quackd.agent.providers.base import (
    Exchange,
    LLMProvider,
    Observation,
    ProviderError,
    ToolCall,
)
from quackd.agent.providers.factory import make_provider, parse_llm
from quackd.agent.providers.fake import FakeProvider
from quackd.agent.providers.pricing import fmt_usd
from quackd.duckfile.parser import duck_from_goal
from quackd.log import LogEvent, fan_out
from quackd.transport.base import TransportError
from quackd_lerobot import REACH
from quackd_lerobot.sim.camera import open_renderer, render
from quackd_lerobot.verbs import JOINTS

GOAL = "Wave to the camera with an extended arm"
"""The sentence the README's hero was given on the real arm, word for word."""
FRONT = "front"
CAMERA_URL = f"opencv://0?name={FRONT}"
"""The scene's front view, and the run's only camera. No size, so the frame the pilot is sent
is the one a run given `--camera-url opencv://0?name=front` would be sent."""

SAMPLE_S = 0.2  # simulated seconds between recorded frames
MAX_BYTES = 2_097_152  # 2048 KB, exactly the --maxkb the pre-commit hook is configured with
EXACT = 1e-6
"""How near a frame's due time the clock has to be to take it, for the float sum of steps."""

# ── the fixed view ────────────────────────────────────────────────────────────────────────
#
# Placed from the scene rather than by hand: the arm's pan axis and table from the loaded
# model, and its height from the shoulder's anchor plus the reach the datasheet publishes, so
# the arm raised as high as it goes is inside the frame on whatever model is loaded.

VIEW_AZIMUTH_DEG = 135.0
"""From in front of the arm and to its right, where the front camera is straight ahead."""
VIEW_ELEVATION_DEG = -20.0
"""A little above the table, looking down on it."""
VIEW_MARGIN = 1.05
"""How much room the frame leaves around the sphere the arm can sweep."""
VIEW_AHEAD = 0.25
"""How far in front of the pan axis the view is centred, as a share of the reach across the
table, so the objects laid out there are in the frame with the arm."""

# ── the scripted pilot ────────────────────────────────────────────────────────────────────
#
# A rule, not a judgement. Every angle is a share of the travel the arm published to the pilot
# (`joint_range_deg`), which on the generic arm is the model's own range, never a number read
# off an arm.

LEAN_SHARE = 0.2
"""How far `shoulder_lift` goes from the middle of its travel towards its high end, which leans
the upper arm towards the front camera on this model at quackd's assumed joint signs
(`JOINT_SIGN`)."""
EXTEND_SHARE = 0.6
"""How far `elbow_flex` goes from the middle of its travel towards its low end, which raises
the forearm on this model at the same signs: with the lean, the hand ends up out in front of
the arm and above it, far enough from the pan axis that a swing of the pan moves it."""
WAVE_SHARE = 0.25
"""How far `shoulder_pan` swings the raised arm either side of the middle of its travel, as a
share of the way from the middle to either end."""
WAVES = 4
EXTEND_S = 3.0
WAVE_S = 1.2

# ── the frame ─────────────────────────────────────────────────────────────────────────────

CAPTION_H = 20
GUTTER = 4
FONT_PX = 13
DIM = (150, 156, 164)
DUCK_YELLOW = (245, 197, 66)
BACKDROP = (22, 24, 28)
OPENING = "the arm as it starts, before the first verb"
OPEN_HOLD_MS = 1200  # the arm as it starts, before anything has moved
FINAL_HOLD_MS = 2000  # the arm where the pilot left it, before the loop restarts

#: Regular face, first readable path wins, the same table as `lerobot_hero.py`: Windows first,
#: then macOS, then the usual Linux packages.
_FACES: tuple[str, ...] = (
    "C:/Windows/Fonts/segoeui.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
)


def font(size: int) -> ImageFont.FreeTypeFont:
    """The first face on this machine. The caption strip is the only type in the film."""
    for path in _FACES:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    raise SystemExit(
        f"no font found. Tried: {', '.join(_FACES)}.\n"
        "Add the path of a TTF on this machine to _FACES and run it again."
    )


# ── the pilot ─────────────────────────────────────────────────────────────────────────────


def wave_pilot(adapter: Any) -> FakeProvider:
    """The scripted pilot: read the arm, raise it, swing it side to side `WAVES` times, bring
    it back to the middle and declare success, or declare failure the moment a verb fails.

    The angles come from the travel the connected arm published to the pilot, read at the
    first call that needs one, which is after the connect."""

    def plan() -> list[ToolCall]:
        travel = {
            joint: (float(lo), float(hi))
            for joint, (lo, hi) in adapter.manifest.extras["joint_range_deg"].items()
        }

        def towards(joint: str, end: int, share: float) -> float:
            """`share` of the way from the middle of the joint's travel to its low end (0) or
            its high end (1). A share of 0 is the middle."""
            middle = sum(travel[joint]) / 2
            return round(middle + share * (travel[joint][end] - middle), 1)

        def move(duration_s: float, **positions: float) -> ToolCall:
            return ToolCall(
                name="move_joints", arguments={"positions": positions, "duration_s": duration_s}
            )

        extend = move(
            EXTEND_S,
            shoulder_lift=towards("shoulder_lift", 1, LEAN_SHARE),
            elbow_flex=towards("elbow_flex", 0, EXTEND_SHARE),
            wrist_flex=towards("wrist_flex", 0, 0.0),
        )
        # out to one side and the other in turn, starting towards the high end of the pan
        waves = [
            move(WAVE_S, shoulder_pan=towards("shoulder_pan", 1 - i % 2, WAVE_SHARE))
            for i in range(WAVES)
        ]
        return [
            ToolCall(name="report_state", arguments={}),
            extend,
            *waves,
            move(WAVE_S, shoulder_pan=towards("shoulder_pan", 0, 0.0)),
            ToolCall(
                name="declare_success",
                arguments={
                    "reason": f"scripted pilot: raised the arm and swung it {WAVES} times",
                },
            ),
        ]

    calls: list[ToolCall] = []

    def strategy(obs: Observation, step: int, history: list[Exchange]) -> ToolCall:
        last = obs.features.get("last_result") or {}
        if last.get("verb") and not last.get("ok"):
            return ToolCall(
                name="declare_failure",
                arguments={"reason": f"{last['verb']} failed, so there is no wave to show"},
            )
        if not calls:
            calls.extend(plan())
        return calls[min(step, len(calls) - 1)]

    return FakeProvider(strategy=strategy, model="scripted:wave")


def pilot_for(spec: str | None, adapter: Any) -> LLMProvider:
    """`--llm` as `quackd run` reads it, with no `--llm` and `fake` both meaning the script."""
    vendor, model = parse_llm(spec)
    if vendor == "fake":
        return wave_pilot(adapter)
    return make_provider(vendor, model=model, goal=GOAL)


def pilot_label(provider: LLMProvider) -> str:
    return "the scripted pilot" if isinstance(provider, FakeProvider) else provider.model


def front_header(provider: LLMProvider) -> str:
    """What the right view is to this pilot. A model that takes images is sent that camera's
    frame at every turn, and the scripted pilot, which reads numbers, is sent none."""
    if getattr(provider, "supports_vision", False):
        return f"{FRONT} camera, what {pilot_label(provider)} is sent"
    return f"{FRONT} camera, which {pilot_label(provider)} never sees"


# ── the film ──────────────────────────────────────────────────────────────────────────────


def what_ran(name: str, params: Mapping[str, Any]) -> str:
    """The verb, and for a move the goal of each joint it was given, for the caption. The
    arguments are the pilot's as it sent them, before the verb has checked any of them, so a
    goal that is not a number is shown as it came."""

    def goal(value: Any) -> str:
        try:
            return f"{float(value):.0f}"
        except (TypeError, ValueError):
            return str(value)

    positions = params.get("positions")
    if isinstance(positions, Mapping) and positions:
        return f"{name}  {', '.join(f'{j} {goal(v)}' for j, v in positions.items())}"
    return name


class Film:
    """Both views on the simulator's clock, and the verb that was running as each was taken.

    It is the run's view (every event of the run passes through it, and it keeps the verbs) and
    its `on_frame` (the first frame the pilot is sent gives the right view its shape and starts
    the film, on the arm as it stands before anything has moved). From then on it is a tick
    hook on the simulator's clock, and it stops at the pilot's declaration, before the close."""

    def __init__(self, adapter: Any, pane_w: int) -> None:
        self.adapter = adapter
        self.pane_w = pane_w
        self.pane_h = 0
        self.frames: list[tuple[float, str, Image.Image, Image.Image]] = []
        self.failure: Exception | None = None
        self._running: list[str] = []
        self._rolling = False
        self._done = False
        self._due = 0.0
        self._side: Any = None
        self._front: Any = None
        self._eye: Any = None
        self._camera = -1

    # the run's view
    def __call__(self, event: LogEvent) -> None:
        data = event.data
        if event.kind == "verb_start" and not data.get("nested"):
            self._running.append(what_ran(str(data.get("name")), data.get("params") or {}))
        elif event.kind == "verb_end" and not data.get("nested") and self._running:
            self._running.pop()
        elif event.kind == "declare":
            self._done = True

    # the run's on_frame
    def seen(self, image: Image.Image, _caption: str) -> None:
        if self._rolling or self._done or self.failure is not None:
            return
        self.pane_h = round(self.pane_w * image.height / image.width)
        world = self.adapter.transport.sim_world
        clock = self.adapter.transport.clock
        try:
            self._open(world)
            self._shoot(world, clock.now(), OPENING)
        except Exception as e:
            self.failure = e
            return
        self._due = clock.now() + SAMPLE_S
        self._rolling = True
        clock.add_tick_hook(self.tick)

    # the clock's tick hook, on the event loop's thread after every step of the clock
    def tick(self, stepper: Any) -> None:
        if not self._rolling or self._done or self.failure is not None:
            return
        if stepper.t < self._due - EXACT:
            return
        while self._due <= stepper.t + EXACT:
            self._due += SAMPLE_S
        try:
            self._shoot(stepper.world, stepper.t)
        except Exception as e:
            # a render that fails ends the film and never the run, which is driving an arm
            self.failure = e

    def _open(self, world: Any) -> None:
        """Both renderers, on this thread, and the fixed view placed from the scene."""
        import mujoco

        self._side = open_renderer(world, self.pane_w, self.pane_h)
        self._front = open_renderer(world, self.pane_w, self.pane_h)
        self._camera = int(world.arm.model.camera(FRONT).id)
        workspace = world.arm.workspace
        with world.locked() as (model, data):
            shoulder = float(data.xanchor[model.joint(JOINTS[1]).id][2]) - workspace.table_top
            fovy = float(model.vis.global_.fovy)
        # the sphere the arm sweeps raised: from the table up to the shoulder plus the reach
        height = shoulder + float(REACH.value)
        radius = math.hypot(height / 2, workspace.reach / 2)
        eye = mujoco.MjvCamera()
        eye.type = mujoco.mjtCamera.mjCAMERA_FREE
        x, y = workspace.center
        eye.lookat[:] = [x + VIEW_AHEAD * workspace.reach, y, workspace.table_top + height / 2]
        eye.distance = VIEW_MARGIN * radius / math.sin(math.radians(fovy) / 2)
        eye.azimuth = VIEW_AZIMUTH_DEG
        eye.elevation = VIEW_ELEVATION_DEG
        self._eye = eye

    def _shoot(self, world: Any, t: float, caption: str | None = None) -> None:
        with world.locked() as (_, data):
            self._side.update_scene(data, camera=self._eye)
        side = Image.fromarray(np.asarray(self._side.render()))
        front = Image.fromarray(render(self._front, world, self._camera))
        if caption is None:
            caption = self._running[-1] if self._running else ""
        self.frames.append((t, caption, side, front))

    def close(self) -> None:
        for renderer in (self._side, self._front):
            if renderer is not None:
                renderer.close()
        self._side = self._front = None


def compose(film: Film, header: str) -> list[Image.Image]:
    width = film.pane_w * 2 + GUTTER
    face = font(FONT_PX)
    out = []
    for _, verb, side, front in film.frames:
        frame = Image.new("RGB", (width, film.pane_h + CAPTION_H), BACKDROP)
        frame.paste(side, (0, CAPTION_H))
        frame.paste(front, (film.pane_w + GUTTER, CAPTION_H))
        draw = ImageDraw.Draw(frame)
        draw.text((6, 2), clip(draw, verb, face, film.pane_w - 12), fill=DUCK_YELLOW, font=face)
        right = clip(draw, header, face, film.pane_w - 12)
        draw.text((film.pane_w + GUTTER + 6, 2), right, fill=DIM, font=face)
        out.append(frame)
    return out


def clip(draw: ImageDraw.ImageDraw, text: str, face: ImageFont.FreeTypeFont, room: int) -> str:
    """`text`, cut short with an ellipsis where it would run past `room` pixels."""
    if draw.textlength(text, font=face) <= room:
        return text
    while text and draw.textlength(text + "…", font=face) > room:
        text = text[:-1]
    return text.rstrip(" ,") + "…"


def save(frames: list[Image.Image], path: Path, *, keep: int, colours: int, fps: int) -> Path:
    """One palette for the whole film and no dither, so a pixel that did not change keeps its
    index and each frame stores only what moved. Thinned by a whole stride, so the frames kept
    are still a fixed interval of simulated time apart."""
    stride = max(1, math.ceil(len(frames) / keep))
    frames = frames[::stride]
    sample = frames[:: max(1, len(frames) // 8)]
    width, height = frames[0].size
    strip = Image.new("RGB", (width, height * len(sample)))
    for i, frame in enumerate(sample):
        strip.paste(frame, (0, i * height))
    palette = strip.quantize(colors=colours, method=Image.Quantize.MEDIANCUT)
    indexed = [f.quantize(palette=palette, dither=Image.Dither.NONE) for f in frames]
    hold = [round(1000 / fps)] * len(indexed)
    hold[0] = OPEN_HOLD_MS
    hold[-1] = FINAL_HOLD_MS
    path.parent.mkdir(parents=True, exist_ok=True)
    indexed[0].save(path, save_all=True, append_images=indexed[1:], duration=hold, loop=0)
    return path


async def record(args: argparse.Namespace) -> tuple[RunResult, Film, LLMProvider]:
    spec = parse_robot_spec("lerobot:mujoco")
    allowed = [v.name for v in registry_for(spec).verbs() if v.safety_class == "safe"]
    adapter = make_adapter(spec, seed=args.seed, camera_url=CAMERA_URL)
    pilot = pilot_for(args.llm, adapter)
    film = Film(adapter, args.pane)

    def narrate(event: LogEvent) -> None:
        data = event.data
        if event.kind == "verb_start" and not data.get("nested"):
            print(f"  {what_ran(str(data.get('name')), data.get('params') or {})}")
        elif event.kind == "declare":
            print(f"  {data.get('outcome')}: {str(data.get('reason'))[:100]}")

    try:
        result = await run_duck(
            RunConfig(
                duck=duck_from_goal(GOAL, allowed),
                provider=pilot,
                transport=adapter,
                runs_dir=args.runs_dir,
                view=fan_out(film, narrate),
                on_frame=film.seen,
            )
        )
    finally:
        film.close()
    return result, film, pilot


def report(result: RunResult) -> None:
    """The run's own numbers, from its summary, for the caption and the assets table."""
    s = result.summary
    print(
        f"  {result.outcome} in {result.steps} steps and {result.llm_calls} LLM calls, "
        f"{s.get('wall_s')} s on the wall and {s.get('elapsed_s')} s of simulated time, "
        f"{fmt_usd(s.get('cost_usd'))}"
    )
    print(f"  run: {result.run_dir}")


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default="docs/assets/lerobot-sim.gif")
    parser.add_argument("--llm", default=None, help="as quackd run takes it; none is the script")
    parser.add_argument("--seed", type=int, default=0, help="lays out the table")
    parser.add_argument("--runs-dir", default="runs")
    parser.add_argument("--pane", type=int, default=296, help="pixels per view, across")
    parser.add_argument("--keep", type=int, default=160, help="most frames to keep")
    parser.add_argument("--colours", type=int, default=96)
    parser.add_argument("--fps", type=int, default=10)
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(message)s")

    try:
        result, film, pilot = await record(args)
    except (ProviderError, TransportError) as e:
        raise SystemExit(str(e)) from None
    report(result)
    if film.failure is not None:
        raise SystemExit(
            f"a render failed ({type(film.failure).__name__}: {film.failure}), so the film "
            f"stopped there while the run went on. Its record is in {result.run_dir}."
        )
    if result.outcome != "success":
        raise SystemExit(
            f"{pilot_label(pilot)} did not declare success, so this is not a recording of a "
            f"wave and nothing was written. Its record is in {result.run_dir}: run it again."
        )
    if not film.frames:
        raise SystemExit(
            "no frames were recorded, because the front camera sent the run no frame to start "
            f"the film on. The run's own frames are in {result.run_dir}."
        )
    out = save(
        compose(film, front_header(pilot)),
        Path(args.out),
        keep=args.keep,
        colours=args.colours,
        fps=args.fps,
    )
    size = out.stat().st_size
    # `n_frames` exists on the multi-frame plugins, not on the `ImageFile` base the stubs
    # declare, and this is always a GIF because `save` wrote it.
    saved = getattr(Image.open(out), "n_frames", 0)
    shown = film.frames[-1][0] - film.frames[0][0]
    print(f"{out} — {size // 1024} KB, {saved} frames, {shown:.1f} s of simulated time")
    if size > MAX_BYTES:
        print("too big for the pre-commit cap: lower --keep, --pane or --colours", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
