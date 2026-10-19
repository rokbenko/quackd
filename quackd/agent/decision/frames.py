"""What a decision LLM is shown of the camera, and the one rule for whether it may be shown any.

A decision LLM is sent text: the state quackd builds and the questions quackd writes.
`--decision-frames` is how a run asks for more. Asked, the stepper shows its decision LLM the
primary camera's picture with each request it sends, the picture the detections describe, as one
JPEG beside the state and never inside it, and the run keeps a copy of every one it sends. This
module is the three pieces of that:

- `may_send_frames`, the gate every frame passes, whoever sends it: true of quackd's own client
  for OpenAI's Decisions API alone, built to be shown frames, at OpenAI's own address.
- `encode`, the picture as it goes: a copy of it in RGB, its longest side shrunk to
  `MAX_SIDE_PX`, as a JPEG at `JPEG_QUALITY`, to be looked at in `DETAIL`.
- `Snapshot` and `keep`, the copy: the JPEG is written beside the transcript before the request
  that carries it is sent, so the record of a turn that was cut off still says what left.

Rok's rule for all of it, as ADR-0053 records it: a camera frame reaches a decision LLM only when
the run says so, only for a row that can see, and only at OpenAI's own hosts, and the record
keeps what was sent.

Nothing here imports PIL or a backend at module scope. `stepper.py` imports this on every run,
and the client this asks about is imported only by a run that shows frames.
"""

from __future__ import annotations

import contextlib
import io
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from quackd.agent.decision.base import DecisionFrame

if TYPE_CHECKING:
    from PIL import Image

MAX_SIDE_PX = 512
"""The longest side of a frame as it is sent, in pixels. A picture larger than this is shrunk to
fit, keeping its shape, and a smaller one is sent as it is: never enlarged, since enlarging adds
bytes and nothing to look at. A starting value rather than a measured one. What a turn asks is
which call to make, not what is written on a label, and every byte is sent inside the turn's one
second, while how the API counts a picture for this model is not published (`IMAGE_TOKENS` on
docs/guides/decision-llms/openai.md). Every turn records the size it sent, so a run at another
size can be read against this one."""

JPEG_QUALITY = 85
"""What a frame is encoded at: the figure quackd already sends a board's detector a frame at
(`perception/host.py`), where a 640 by 480 frame comes to tens of kilobytes."""

MEDIA_TYPE = "image/jpeg"

DETAIL = "low"
"""How closely the API is asked to look. OpenAI's reference takes `low`, `high`, `auto` or
`original` for an image part, and `auto`, its default, leaves the choice to the API picture by
picture. `low` asks for the least. What any of them costs on this model is not published, so it
is a starting value like `MAX_SIDE_PX`, and every turn records it, so a run at another setting
can be read apart from this one."""

MAX_BYTES = 256_000
"""The most a frame may come to as a JPEG, in bytes, 256 kB. Over it, the frame is not sent and
the turn records why (`frame_skipped`). At today's size and quality no picture comes near it:
random noise filling a 512 by 512 frame, the hardest thing there is to compress, came to 198,928
bytes on 2026-10-19, and a simulator's 256 pixel frame to under 2,000. So it is a backstop, which
holds the size of a request if `MAX_SIDE_PX` or `JPEG_QUALITY` is ever raised."""


def may_send_frames(llm: Any) -> bool:
    """Whether `llm` may be shown a camera frame: the gate every frame quackd sends passes.

    True of quackd's own client for OpenAI's Decisions API alone (`decisions_api.DecisionsAPILLM`,
    that class and no subclass of it), built with `frames` true, at one of OpenAI's own hosts.
    The host is read by `decisions_api.is_openai_host`, the rule that decides where
    `OPENAI_API_KEY` goes and what a turn costs, so the key, the price and the frames are decided
    by one function on one string. False for anything else whatever it says about itself: a
    plugin whose `frames` is true, the same client at a server of your own, loopback included,
    and an object that merely looks like the client.

    `Stepper.build` asks this before it builds a stepper that shows frames, and the stepper asks
    again before every frame goes, so a stepper put together by hand cannot show one to anything
    else either. The client is imported here rather than at the top, because only a run that has
    named OpenAI's row has any reason to load it."""
    from quackd.agent.decision import decisions_api

    url = getattr(llm, "url", None)
    return (
        type(llm) is decisions_api.DecisionsAPILLM
        and getattr(llm, "frames", False) is True
        and isinstance(url, str)
        and decisions_api.is_openai_host(url)
    )


def encode(image: Image.Image, camera: str) -> DecisionFrame:
    """`image` as it is sent: an RGB copy, shrunk until its longest side is at most
    `MAX_SIDE_PX`, as a JPEG at `JPEG_QUALITY`, to be looked at in `DETAIL`.

    A copy, so the picture the transcript keeps as the step's PNG and the one the pilot is shown
    are untouched, whatever mode the camera handed over: `convert` returns a new image even when
    the picture is RGB already. CPU work on a whole frame, so the stepper calls this in a worker
    thread rather than on the loop that keeps a body's deadman fed. It raises whatever PIL raises
    for a picture it cannot encode, and the stepper records that as the reason the turn went
    without one."""
    picture = image.convert("RGB")
    picture.thumbnail((MAX_SIDE_PX, MAX_SIDE_PX))
    buf = io.BytesIO()
    picture.save(buf, format="JPEG", quality=JPEG_QUALITY)
    return DecisionFrame(
        camera=camera,
        media_type=MEDIA_TYPE,
        data=buf.getvalue(),
        width=picture.width,
        height=picture.height,
        detail=DETAIL,
    )


def too_large(frame: DecisionFrame) -> str | None:
    """Why `frame` is not sent, in the words the record keeps, or None when it may be."""
    if len(frame.data) > MAX_BYTES:
        return (
            f"the picture came to {len(frame.data)} bytes as a JPEG, over the {MAX_BYTES} a "
            "turn may send"
        )
    return None


@dataclass(frozen=True)
class Snapshot:
    """The primary camera's picture on one turn, and where the record keeps what is sent of it.

    The loop makes one for a turn of a run that shows frames, from the picture its observation
    read, and hands it to the stepper, which encodes it, keeps it and sends it, in that order.
    `path` is where the JPEG is written and `where` is the same file as the record names it,
    relative to the run's directory: `frames/0003-decision.jpg` beside `frames/0003.png`, the
    step's own picture, so the picture the robot saw and the one its decision LLM was shown sit
    side by side under one number."""

    image: Image.Image
    camera: str
    path: Path
    where: str


def write_jpeg(path: Path, data: bytes) -> None:
    """The file write `keep` makes, apart so that a test can stand a failing disk in its place."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def keep(path: Path, frame: DecisionFrame) -> bool:
    """Write the bytes about to be sent to `path`, and say whether the disk took them.

    Called just before the request that carries the frame, on the loop's own thread, and before
    the record line that names the frame is written and flushed (`Stepper.advise`'s `on_frame`),
    so no frame is handed over without that line. A process that dies between the two leaves a
    copy that nothing was sent with, and never a frame sent with nothing naming it. False rather
    than an exception for a disk that refused: the frame still goes, and the record says so and
    that its copy is missing (`kept: false`), because a full disk is a reason to lose a copy and
    never a reason to end a run with a robot connected. A refused write may have left part of the
    file behind, and that part is removed, so no copy sits beside a record whose sha256 it does
    not match."""
    try:
        write_jpeg(path, frame.data)
    except OSError:
        with contextlib.suppress(OSError):
            path.unlink(missing_ok=True)
        return False
    return True
