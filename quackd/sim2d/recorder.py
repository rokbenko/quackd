"""Turns a run into a shareable GIF: world view | one duck's cam, with a caption strip.

The recorder hangs off the shared clock's tick hook so motion between LLM turns is
visible, not just the snapshots a model saw. For flocks, `set_focus` switches the right
pane to the duck that matters (the coordinator focuses the claimant) and `set_caption`
carries the phase line. The GIF is the demo; the transcript is the proof.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from quackd.sim2d.render import render_duckcam, render_topdown
from quackd.transport.base import spatial_of

CAPTION_H = 22
MAX_FRAMES = 400


class FrameRecorder:
    def __init__(
        self, transport: Any, *, size: int = 256, every_s: float = 0.25, fps: int = 8
    ) -> None:
        self.transport = transport
        self.size = size
        # a transport whose world is expensive to draw asks for a coarser cadence
        self.every_s = float(getattr(transport, "recorder_every_s", every_s))
        self.fps = fps
        self.caption = "start"
        self.focus_duck = getattr(transport, "duck_index", 0)
        self.frames: list[Image.Image] = []
        self._last_t = -1e9
        hook = getattr(transport, "add_tick_hook", None)
        if hook is not None:
            hook(self._on_tick)

    @property
    def world(self) -> Any:
        """Read late, not at construction: the physics transport builds its world in
        `connect()`, after the CLI has already made the recorder."""
        return getattr(self.transport, "world", None)

    def set_focus(self, duck_index: int) -> None:
        self.focus_duck = duck_index

    def set_caption(self, text: str) -> None:
        self.caption = text[:80]

    # the loop calls this with each frame it captured; we only keep the caption
    def capture(self, _img: Image.Image, caption: str) -> None:
        self.set_caption(caption)
        if self.world is not None:
            self._append()

    def _on_tick(self, world: Any) -> None:
        if world.t - self._last_t >= self.every_s:
            self._append()

    def _cam_label(self) -> str:
        if self.world is None or len(self.world.ducks) <= 1:
            return "duck cam"
        duck = self.world.ducks[self.focus_duck]
        return f"duck cam {chr(ord('A') + self.focus_duck)} ({duck.colorway})"

    def _append(self) -> None:
        world = self.world
        if world is None:
            return
        self._last_t = world.t
        # a transport that draws its own world (the physics one) supplies both panes;
        # the cartoon is drawn here. The CLI hands over the adapter, so look through it
        panes = getattr(self.transport, "render_panes", None) or getattr(
            getattr(self.transport, "transport", None), "render_panes", None
        )
        if panes is not None:
            top, cam, label = panes(self.size)
        else:
            # a simulated map of the world's room, when the duck keeps one, shades what it has
            # not seen yet
            seen = getattr(spatial_of(self.transport), "grid_now", None)
            top = render_topdown(world, self.size, seen=seen() if callable(seen) else None)
            cam = render_duckcam(world, self.size, duck_index=self.focus_duck)
            label = self._cam_label()
        frame = Image.new("RGB", (self.size * 2 + 4, self.size + CAPTION_H), (30, 30, 30))
        frame.paste(top, (0, CAPTION_H))
        frame.paste(cam, (self.size + 4, CAPTION_H))
        draw = ImageDraw.Draw(frame)
        draw.text((6, 5), f"t={world.t:5.1f}s  {self.caption}", fill=(240, 240, 240))
        draw.text((self.size + 10, 5), label, fill=(180, 180, 180))
        self.frames.append(frame)

    def save_gif(self, path: Path) -> Path | None:
        """Write the run as a GIF at `path`, or write nothing and return None when there is
        no frame to write: a world that was never built, because the run ended before its
        connect did, has nothing to draw. A GIF of no frames cannot be encoded, and the run it
        belongs to has already finished, so this is not a reason to fail it."""
        frames = self.frames
        if not frames and self.world is not None:
            self._append()
            frames = self.frames
        if not frames:
            return None
        if len(frames) > MAX_FRAMES:
            stride = len(frames) / MAX_FRAMES
            frames = [frames[int(i * stride)] for i in range(MAX_FRAMES)]
        path.parent.mkdir(parents=True, exist_ok=True)
        palette = [f.quantize(colors=64, method=Image.Quantize.MEDIANCUT) for f in frames]
        hold = [1000 // self.fps] * len(palette)
        if hold:
            hold[-1] = 1500  # linger on the ending
        palette[0].save(
            path,
            save_all=True,
            append_images=palette[1:],
            duration=hold,
            loop=0,
            optimize=False,
        )
        return path
