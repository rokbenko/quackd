"""Cut `docs/assets/lerobot.gif` and draw `docs/assets/lerobot-what-it-saw.png`.

    uv run python docs/assets/lerobot_hero.py --video PATH   # the phone recording
    uv run python docs/assets/lerobot_hero.py --run PATH     # the run directory

Both outputs come from one afternoon: 2026-09-15, run `20260915-145349-goal`, an SO-101
follower arm on `lerobot:real` told *wave to the camera with an extended arm* and piloted by
OpenAI's `gpt-6-astra`. The GIF is a phone pointed at the bench. The PNG is three of the ten
webcam frames the LLM itself was sent, which is a different picture of the same minute: one
shows what the arm did, the other shows what the pilot had to go on.

Neither input is in this repository and neither ever will be. The phone video is 23 MB and the
run directory is 3 MB of PNGs and a 70 KB transcript, all of it on the machine that drove the
arm, beside the other eleven runs of that afternoon. So this script is reproducible there and
nowhere else, which `docs/assets/README.md` says in the row for each file. What is committed is
the output and the recipe.

**The GIF is the one file in `docs/assets` allowed over the general 2048 KB cap**, and it has a
cap of its own here, named in `.pre-commit-config.yaml`'s exclude and checked by
`tests/test_pypi_readme.py`. The reason is what it is a picture of. A render spends bytes on
what moved and nothing on the rest, so the simulator hero fits in 1441 KB at 579 px. A
photograph spends them on every pixel of every frame: the same nine seconds of bench, at a
width worth putting at the top of a page, is megabytes however it is encoded, and squeezing it
under 2048 KB means either 320 px or throwing away two thirds of the wave. Neither is a hero.
Every other asset here stays under the general cap.

Encoding choices, all of them the cap talking rather than taste. Bayer dither rather than error
diffusion, because the pattern is stable frame to frame and a diffused one re-dithers every
frame and inflates the file by about a third. `stats_mode=diff` so the palette is chosen from
what moves, which is the arm rather than the wall behind it. `diff_mode=rectangle` so each
frame stores the box that changed. A light `hqdn3d`, because sensor noise in a dim lab is
motion as far as the encoder is concerned and it is the most expensive thing in the file.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
OUT_GIF = HERE / "lerobot.gif"
OUT_PNG = HERE / "lerobot-what-it-saw.png"

#: The hero's own cap, in bytes. `.pre-commit-config.yaml` excludes this one file from the
#: 2048 KB hook and `tests/test_pypi_readme.py` holds the two numbers together, so moving it
#: means moving it in both places. Everything else in this directory keeps the general cap.
HERO_MAX_BYTES = 12_582_912  # 12288 KB
#: The general cap, for the frames sheet, which has no reason to be special.
MAX_BYTES = 2_097_152  # 2048 KB, exactly the --maxkb the pre-commit hook is configured with

WIDTH = 640  # displayed at this width in the README, so encoded at it rather than resampled
FPS = 10
COLOURS = 128
DENOISE = "hqdn3d=4:3:6:4.5"
FINAL_DELAY_CS = 100  # a beat on the last frame before the loop restarts

#: Which of the run's ten frames the sheet shows, and what each one is of. `0000` is the
#: observation before the first call, `0003` the one after the arm extended, `0009` the one
#: after `stop`. The middle one is why the LLM's success reason says it checked the wave
#: against joint angles: the arm it had just raised runs off the top of its own picture.
PICKS: tuple[tuple[str, str], ...] = (
    ("0000", "step 0, before the first call"),
    ("0003", "step 2, extended and cut off at the top"),
    ("0009", "step 8, after stop: a hand waves back"),
)
PANE_W = 392
GUTTER = 12
CAPTION_H = 34
BACKDROP = (22, 24, 28)
INK = (238, 238, 238)

#: Regular face, first readable path wins: Windows first, then macOS, then the usual Linux
#: packages. `font` names every path it tried when none of them is on this machine.
_FACES: tuple[str, ...] = (
    "C:/Windows/Fonts/segoeui.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
)


def font(size: int) -> ImageFont.FreeTypeFont:
    """The first face on this machine. The captions are the only type on the sheet."""
    for path in _FACES:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    raise SystemExit(
        f"no font found. Tried: {', '.join(_FACES)}.\n"
        "Add the path of a TTF on this machine to _FACES and run it again."
    )


def ffmpeg() -> str:
    """Where ffmpeg is. On PATH, or the place the Windows installer puts it."""
    found = shutil.which("ffmpeg") or shutil.which("ffmpeg.exe")
    if found:
        return found
    fallback = Path("C:/ffmpeg/bin/ffmpeg.exe")
    if fallback.exists():
        return str(fallback)
    raise SystemExit(
        "no ffmpeg on PATH. It is the only thing here that is not a Python dependency: "
        "winget install Gyan.FFmpeg, brew install ffmpeg, or apt install ffmpeg."
    )


def graph(width: int, fps: int, colours: int) -> str:
    """The one filter graph, palette and all, so there is no palette file to leave behind."""
    common = f"fps={fps},scale={width}:-1:flags=lanczos,{DENOISE}"
    return (
        f"[0:v]{common},split[a][b];"
        f"[a]palettegen=max_colors={colours}:stats_mode=diff[p];"
        f"[b][p]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle"
    )


def cut(video: Path, out: Path, *, width: int, fps: int, colours: int) -> int:
    """The whole clip, one pass. Returns the size in bytes."""
    done = subprocess.run(
        [
            ffmpeg(),
            "-y",
            "-i",
            str(video),
            "-an",
            "-filter_complex",
            graph(width, fps, colours),
            "-loop",
            "0",
            "-final_delay",
            str(FINAL_DELAY_CS),
            str(out),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if done.returncode != 0:
        # ffmpeg's last few lines name the actual fault, a missing file or a filter it would
        # not build. Without them this is an exit code and nothing to act on.
        tail = "\n".join((done.stderr or "").strip().splitlines()[-6:])
        raise SystemExit(f"ffmpeg exited {done.returncode}:\n{tail}")
    return out.stat().st_size


def compose(run_dir: Path, out: Path) -> int:
    """Three frames of the run side by side, each captioned with the step it belongs to."""
    frames = run_dir / "frames"
    pane_h = 0
    panes = []
    for index, label in PICKS:
        source = frames / f"{index}.png"
        if not source.exists():
            raise SystemExit(f"{source} is not there. --run wants the run directory itself.")
        image = Image.open(source).convert("RGB")
        pane_h = round(image.height * PANE_W / image.width)
        panes.append((image.resize((PANE_W, pane_h), Image.Resampling.LANCZOS), index, label))
    width = len(panes) * PANE_W + (len(panes) + 1) * GUTTER
    sheet = Image.new("RGB", (width, pane_h + CAPTION_H + 2 * GUTTER), BACKDROP)
    draw = ImageDraw.Draw(sheet)
    face = font(16)
    for i, (image, index, label) in enumerate(panes):
        x = GUTTER + i * (PANE_W + GUTTER)
        sheet.paste(image, (x, GUTTER))
        draw.text((x + 2, GUTTER + pane_h + 9), f"{index}  {label}", fill=INK, font=face)
    sheet.save(out, optimize=True)
    return out.stat().st_size


def report(out: Path, size: int, cap: int) -> int:
    """Say what was written and how close to its cap it came."""
    print(f"{out} — {size // 1024} KB of {cap // 1024} KB")
    if size > cap:
        print(
            f"{out.name} is over its cap. For the GIF: lower --width, then --fps, then "
            "--colours. For the sheet: lower PANE_W.",
            file=sys.stderr,
        )
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--video", type=Path, help="the phone recording to cut the hero from")
    parser.add_argument("--run", type=Path, help="the run directory to take the frames from")
    parser.add_argument("--width", type=int, default=WIDTH)
    parser.add_argument("--fps", type=int, default=FPS)
    parser.add_argument("--colours", type=int, default=COLOURS)
    args = parser.parse_args()
    if args.video is None and args.run is None:
        parser.error("nothing to do: pass --video, --run, or both")
    status = 0
    if args.video is not None:
        size = cut(args.video, OUT_GIF, width=args.width, fps=args.fps, colours=args.colours)
        frames = getattr(Image.open(OUT_GIF), "n_frames", 0)
        print(f"  {frames} frames at {args.width} px, {args.fps} fps, {args.colours} colours")
        status |= report(OUT_GIF, size, HERO_MAX_BYTES)
    if args.run is not None:
        status |= report(OUT_PNG, compose(args.run, OUT_PNG), MAX_BYTES)
    return status


if __name__ == "__main__":
    raise SystemExit(main())
