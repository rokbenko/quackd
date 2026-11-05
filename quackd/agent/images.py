"""Pictures that come with the task: `quackd run --image sketch.png`.

A camera frame is perception and arrives every step. One of these is neither: it is a file a
person named on the command line, it is fixed for the whole run, and it is what the task is
about. "Draw what is in the picture" is not a sentence a robot's own camera can answer.

Everything here is re-encoded to PNG before it goes anywhere. Every provider quackd speaks to
already takes a PNG, `NamedPng` promises one, and the copy kept beside the transcript is then
byte for byte what the LLM was sent rather than a source file it was derived from. The cost
is that a photograph is re-compressed on its way in, which is also where it is made small
enough to send.
"""

from __future__ import annotations

import io
import warnings
from collections.abc import Sequence
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

from quackd.agent.providers.base import NamedPng

MAX_SIDE_PX = 1568
"""Longest edge a task picture is sent at. Above roughly this, the vendors that publish a
figure resize server side anyway and charge for the tokens; below it, a sketch is still
legible. Not a vendor's number for every vendor, so it is quackd's own choice."""

MAX_BYTES = 1_500_000
"""How large one encoded picture may be. Several of these ride in every single request for
the whole run, so the cap is on quackd's side of the wire rather than on the vendor's."""

SHRINK = 0.8
"""How much smaller to try when the encoded picture is still over the cap."""

GROUND = (255, 255, 255)
"""What a transparent pixel becomes. White, because a drawing exported with a transparent
background is a drawing on paper, and paper is what the arm is going to be looking at."""

MIN_SIDE_PX = 64
"""Stop shrinking here and refuse instead: a picture this small says nothing, and a loop that
kept halving would turn one bad file into a silent blank."""

WIDE_MODES = ("I", "I;16", "I;16B", "I;16L", "I;16N", "F")
"""Modes whose samples do not fit in a byte. A depth map, a scientific scan and a 16-bit
photograph all land here, and converting one straight to RGB clips rather than scales."""

FORMATS = ("PNG", "JPEG", "WEBP", "GIF", "BMP", "TIFF")
"""What PIL is allowed to have decoded. Named so the refusal can list them, and so a PDF or a
video handed to `--image` is one line rather than a traceback from inside a decoder."""


class TaskImageError(ValueError):
    """A `--image` that cannot be sent, in words that name the file and the fix."""


def _encode(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _flatten(img: Image.Image) -> Image.Image:
    """The picture as RGB, the right way up, with nothing transparent left in it.

    Two things a plain `convert("RGB")` gets wrong, and both of them silently.

    A transparent pixel keeps whatever colour is stored underneath it, which for a drawing
    exported the ordinary way is black. So a sketch of a circle on a transparent background
    became a solid black rectangle: one colour, no circle, nothing raised, and the LLM was
    handed that as the thing it had been asked to draw. Compositing onto white first is what
    makes the exported drawing look like the drawing.

    And a photograph from a phone stores its rotation in an EXIF tag rather than in the
    pixels, so every viewer shows it upright and a re-encode that drops the tag shows it on
    its side. `exif_transpose` moves the rotation into the pixels before the tag is lost."""
    img = ImageOps.exif_transpose(img) or img
    if img.mode in WIDE_MODES:
        # `convert("RGB")` on these does not rescale, it clips every sample into 0..255, so a
        # 16-bit depth map or scan came out with everything above 255 flattened to white. Only
        # the darkest 0.4% of a 16-bit range survived, which is a picture of nothing.
        # `autocontrast` cannot take these modes either, so the stretch is done by hand.
        img = _to_byte(img)
    if img.mode in ("RGBA", "LA", "PA") or "transparency" in img.info:
        img = img.convert("RGBA")
        ground = Image.new("RGBA", img.size, (*GROUND, 255))
        img = Image.alpha_composite(ground, img)
    return img.convert("RGB")


def _to_byte(img: Image.Image) -> Image.Image:
    """A wide-sample picture as 8-bit grey, stretched rather than clipped.

    PIL's own `convert("L")` from `I` truncates the same way `convert("RGB")` does, so the
    range is found and applied here. A flat picture (every sample the same) has no range to
    stretch, and becomes the mid grey it actually is rather than a division by zero."""
    lo, hi = img.getextrema()
    if not isinstance(lo, (int, float)) or not isinstance(hi, (int, float)):
        return img  # a multi-band wide image: leave it to the convert below
    if hi <= lo:
        return Image.new("L", img.size, min(255, max(0, int(lo))) if hi <= 255 else 128)
    # a plain linear expression, because `point` on a wide mode probes the callable with a
    # transform object to build a scale and an offset: anything else in it raises
    scale = 255.0 / (hi - lo)
    return img.point(lambda v: (v - lo) * scale).convert("L")


def _fit(img: Image.Image, path: str) -> bytes:
    """One picture as PNG bytes, inside both caps.

    The long edge comes down first, which is what actually costs tokens, and only then the
    file size, which is what costs bandwidth. A sketch is mostly flat colour and lands far
    under the byte cap at full size; a photograph of a desk may not, and shrinks again."""
    img = _flatten(img)
    img.thumbnail((MAX_SIDE_PX, MAX_SIDE_PX))
    png = _encode(img)
    while len(png) > MAX_BYTES:
        width = int(img.width * SHRINK)
        height = int(img.height * SHRINK)
        if min(width, height) < MIN_SIDE_PX:
            raise TaskImageError(
                f"--image {path}: this picture is still {len(png) // 1000} kB at "
                f"{img.width}x{img.height} and will not fit under {MAX_BYTES // 1000} kB "
                "without becoming unreadable; save a smaller or flatter copy"
            )
        img = img.resize((width, height))
        png = _encode(img)
    return png


def _names(paths: Sequence[str]) -> list[str]:
    """What each picture is called on the wire: its file name, and its place in the list when
    two files share one.

    The LLM is told a name and the task refers to the picture by what it shows, so the name
    has to be the one the person typed. Two directories with a `sketch.png` in each would
    otherwise both arrive as `sketch.png`, and a task naming one of them would be ambiguous in
    exactly the way a label exists to prevent."""
    bases = [Path(p).name for p in paths]
    taken = set(bases)
    out: list[str] = []
    for i, base in enumerate(bases):
        if bases.count(base) == 1:
            out.append(base)
            continue
        # the index first, then on until the invented name is one nothing else has: a person
        # who really does have a `sketch.png` and a `2-sketch.png` would otherwise end up with
        # two pictures under one name, which is the thing this exists to prevent
        n = i + 1
        candidate = f"{n}-{base}"
        while candidate in taken or candidate in out:
            n += 1
            candidate = f"{n}-{base}"
        out.append(candidate)
    return out


def load_task_images(paths: Sequence[str]) -> list[NamedPng]:
    """Every `--image` as a PNG the providers can carry, in the order they were given.

    A file that is missing, unreadable, or not a picture is refused here, before the robot is
    connected and before a run directory exists. The alternative is a run that reaches the
    first request without the one thing the task is about."""
    names = _names(paths)
    out: list[NamedPng] = []
    for path, name in zip(paths, names, strict=True):
        if not path.strip():
            # `Path("")` is the current directory, so an unset shell variable used to be
            # refused as "that is a directory, not a picture", naming no file at all
            raise TaskImageError("--image was given an empty path")
        file = Path(path)
        if not file.exists():
            raise TaskImageError(f"--image {path}: no such file")
        if file.is_dir():
            raise TaskImageError(f"--image {path}: that is a directory, not a picture")
        try:
            with warnings.catch_warnings():
                # PIL warns rather than raises between its own limit and twice it, and that
                # warning used to land in the terminal as a raw site-packages line in the
                # middle of quackd's own output. Promoted so it takes the refusal below.
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(file) as img:
                    if img.format not in FORMATS:
                        raise TaskImageError(
                            f"--image {path}: quackd sends {', '.join(FORMATS)} and this is "
                            f"{img.format or 'not a picture'}"
                        )
                    # GIF and TIFF can hold several; the first frame is the one a person means
                    img.seek(0)
                    png = _fit(img, path)
        except TaskImageError:
            raise
        except UnidentifiedImageError as e:
            raise TaskImageError(
                f"--image {path}: this is not a picture quackd can read ({', '.join(FORMATS)})"
            ) from e
        except Image.DecompressionBombError as e:
            # Not an OSError, and raised inside `Image.open` before any check of quackd's own,
            # so it used to come out of `quackd run` as a traceback rather than as one line.
            raise TaskImageError(
                f"--image {path}: this picture claims to be far larger than anything quackd "
                f"will decode ({e})"
            ) from e
        except OSError as e:
            raise TaskImageError(f"--image {path}: could not be read: {e}") from e
        out.append(NamedPng(name=name, png=png))
    return out
