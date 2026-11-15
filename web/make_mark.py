"""Cut Roboglot's mark out of the duck, and write the two transparent exports of it.

    uv run python web/make_mark.py                  # needs a roboglot-web checkout beside this one
    uv run python web/make_mark.py --source PATH    # or point it at duck-source.png yourself

The mark is the duck's head, and it is not drawn here or anywhere else in this repository. The
only original is `src/assets/duck-source.png` in roboglot-web, 1086x1448 of rendered 3D duck on
an opaque near-white plate, and every head crop is a derivative of it. Two of those crops were
committed here with the bottom of the head sliced off at the image boundary, taking the chin,
the lower jaw and the orange mouth with them, and nothing recorded how to make them again.
This is that recipe.

The framing is deliberately the old one. The 0.9 export mapped one mark pixel to 2.357 source
pixels, which is a 603x603 window, and that number is recovered by measuring the dark visor
panel in both images rather than copied from a note. The window is kept at 603 and only moved:
the head is 550x553, so it always fit, and the old export had simply placed the window twenty
rows too high. Keeping the scale means the mark's weight on the README and on the social card
does not change, only the part that was missing.

Three things are cut away, each for its own reason. The body, because this is a head, and the
neck is where they join. The heart speech bubble, because it is the duck being pleased about
something rather than part of its face, and it is a separate region so it lifts out cleanly.
The stub of dark neck the body cut leaves hanging under the jaw, because a mark should end at
the chin and not at a tube.

Transparency is a flood fill from the corner rather than a test for whether a pixel is white,
which would have punched out the eye. The downsample is premultiplied, so the near-white the
duck was rendered against cannot creep back in as a pale fringe around the edge.

`assets/apple-touch-icon.png` is not written here. Its head was already whole, and it sits on
a lavender plate this script has no business redrawing.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
ASSETS = HERE / "assets"
DEFAULT_SOURCE = HERE.parent.parent / "roboglot-web" / "src" / "assets" / "duck-source.png"

#: How far from the corner's colour a pixel may sit and still be the plate behind the duck.
#: The plate is (254, 254, 254) with a little dither in it. The duck's lightest pixel is the
#: white of its eye, which this never reaches, because the fill cannot get inside the head.
BG_THRESH = 18

#: A point inside the heart bubble. Seeding a fill here is how the bubble is found, so nothing
#: depends on where its bounding box happens to fall.
BUBBLE_SEED = (233, 305)

#: The row the head comes off the body at: under the lowest pixel of the bill, which is 726,
#: and above the shoulders, which start at 732.
NECK_CUT = 727

#: What that cut leaves is a dark stub of neck under the jaw. Anything darker than this, below
#: this row, is that stub. The visor is darker still and sits a hundred rows higher.
NECK_TOP, NECK_DARK = 680, 80

#: The side of the source window one mark is cut from, in source pixels. This is the 0.9
#: export's own scale, kept so the mark does not change size. See the docstring.
WINDOW = 603

#: What gets written, and how wide.
EXPORTS = {"duck-mark.png": 256, "favicon-96.png": 96}


def head_mask(src: Image.Image) -> np.ndarray:
    """True where the head is: the duck, minus the plate, the bubble, the body and the neck."""
    filled = src.copy()
    ImageDraw.floodfill(filled, (0, 0), (255, 0, 255), thresh=BG_THRESH)
    flooded = np.array(filled)
    plate = (flooded[:, :, 0] == 255) & (flooded[:, :, 1] == 0) & (flooded[:, :, 2] == 255)
    subject = ~plate

    # `.copy()` is load bearing: an image made straight from a numpy buffer is not writable
    # through ImageDraw, and floodfill returns quietly having changed nothing. The bubble then
    # stays in the mark, which the edge check at the end of main() catches.
    stencil = Image.fromarray(np.where(subject, 255, 0).astype(np.uint8), "L").copy()
    ImageDraw.floodfill(stencil, BUBBLE_SEED, 128, thresh=0)
    bubble = np.array(stencil) == 128
    if not bubble.any():
        raise SystemExit(f"nothing filled at {BUBBLE_SEED}: the bubble seed has missed the bubble")

    rgb = np.array(src).astype(int)
    neck = (rgb[:, :, 0] < NECK_DARK) & (rgb[:, :, 1] < NECK_DARK) & (rgb[:, :, 2] < NECK_DARK)
    neck[:NECK_TOP, :] = False

    head = subject & ~bubble & ~neck
    head[NECK_CUT:, :] = False
    return head


def cut(src: Image.Image, head: np.ndarray, size: int) -> Image.Image:
    """One square export, centred on the head, premultiplied so no pale fringe survives."""
    ys, xs = np.nonzero(head)
    mid_x = (int(xs.min()) + int(xs.max())) / 2
    mid_y = (int(ys.min()) + int(ys.max())) / 2
    left, top = round(mid_x - WINDOW / 2), round(mid_y - WINDOW / 2)
    window = (slice(top, top + WINDOW), slice(left, left + WINDOW))

    alpha = np.where(head, 255.0, 0.0)
    premultiplied = np.array(src).astype(np.float64) * (alpha[:, :, None] / 255.0)
    lanczos = Image.Resampling.LANCZOS
    colour = Image.fromarray(premultiplied[window].astype(np.uint8), "RGB")
    opacity = Image.fromarray(alpha[window].astype(np.uint8), "L")
    small_rgb = np.array(colour.resize((size, size), lanczos)).astype(np.float64)
    small_a = np.array(opacity.resize((size, size), lanczos)).astype(np.float64)

    out = np.zeros((size, size, 4), np.uint8)
    lit = small_a > 0
    for channel in range(3):
        plane = np.zeros((size, size))
        plane[lit] = np.clip(small_rgb[:, :, channel][lit] / (small_a[lit] / 255.0), 0, 255)
        out[:, :, channel] = plane.astype(np.uint8)
    out[:, :, 3] = small_a.astype(np.uint8)
    return Image.fromarray(out, "RGBA")


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--source", type=Path, default=DEFAULT_SOURCE, help=f"default: {DEFAULT_SOURCE}"
    )
    args = ap.parse_args()
    if not args.source.is_file():
        raise SystemExit(
            f"no duck at {args.source}.\n"
            "The original lives in roboglot-web, which this repository does not vendor. Clone it "
            "beside this checkout, or pass --source."
        )

    src = Image.open(args.source).convert("RGB")
    head = head_mask(src)
    ys, xs = np.nonzero(head)
    width = int(xs.max()) - int(xs.min()) + 1
    height = int(ys.max()) - int(ys.min()) + 1
    print(f"head {width}x{height} source px, in a {WINDOW}x{WINDOW} window")

    for name, size in EXPORTS.items():
        image = cut(src, head, size)
        ink = image.getbbox()
        if ink is None or ink[0] == 0 or ink[1] == 0 or ink[2] == size or ink[3] == size:
            raise SystemExit(f"{name}: ink {ink} reaches the edge, which is the bug being fixed")
        path = ASSETS / name
        image.save(path, optimize=True)
        print(f"wrote {path} ({path.stat().st_size // 1024} KB, ink {ink})")

    print("roboglot-web keeps its own copies of these two. They need the same fix.")


if __name__ == "__main__":
    main()
