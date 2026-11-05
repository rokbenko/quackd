"""Pictures that come with the task: what loads, what is refused, and what each one is called.

`quackd run --image sketch.png` reads a file a person named, and everything it accepts has to
leave here as PNG bytes small enough to ride in every request of the whole run. So these tests
build real files with PIL and read the loader's answer back through PIL, rather than trusting
that a function that returned without raising produced something a provider could carry.
"""

from __future__ import annotations

import io
import warnings
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

from quackd.agent import images
from quackd.agent.images import TaskImageError, load_task_images

PNG_MAGIC = b"\x89PNG"


def drawing(path: Path, size: tuple[int, int] = (320, 240), fmt: str | None = None) -> Path:
    """A flat picture: a red block on white, which encodes to a few kB at any size.

    The byte cap can never fire on one of these, so a test about the edge cap is about the
    edge cap and nothing else."""
    img = Image.new("RGB", size, "white")
    block = Image.new("RGB", (size[0] // 4, size[1] // 4), "red")
    img.paste(block, (size[0] // 8, size[1] // 8))
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, format=fmt)
    return path


def noise(path: Path, side: int) -> Path:
    """A square of random pixels, which is the one thing PNG cannot compress.

    Its encoded size therefore tracks its area, which is what makes the byte cap reachable
    from a file small enough for a test to write."""
    rng = np.random.default_rng(7)
    pixels = rng.integers(0, 256, (side, side, 3), dtype=np.uint8)
    Image.fromarray(pixels, "RGB").save(path)
    return path


def decoded(png: bytes) -> tuple[str, tuple[int, int]]:
    """The format and the size PIL reads back out of whatever the loader produced."""
    with Image.open(io.BytesIO(png)) as img:
        return img.format or "", img.size


def test_every_format_quackd_takes_comes_back_as_png(tmp_path: Path) -> None:
    """A JPEG is lossy, a WebP is a different codec and a GIF arrives in palette mode, and
    every provider quackd speaks to is handed a PNG. The magic bytes are the only proof that
    the re-encode happened rather than the source file being passed along."""
    paths = [
        str(drawing(tmp_path / "sketch.png")),
        str(drawing(tmp_path / "photo.jpg", fmt="JPEG")),
        str(drawing(tmp_path / "logo.webp", fmt="WEBP")),
        str(drawing(tmp_path / "loop.gif", fmt="GIF")),
    ]
    loaded = load_task_images(paths)

    assert [p.name for p in loaded] == ["sketch.png", "photo.jpg", "logo.webp", "loop.gif"]
    for picture in loaded:
        assert picture.png.startswith(PNG_MAGIC)
        assert decoded(picture.png)[0] == "PNG"


def test_a_picture_larger_than_the_cap_comes_back_smaller(tmp_path: Path) -> None:
    """The long edge is what costs tokens, so it is the long edge that comes down, and the
    shape has to survive it: a picture four times as wide as it is tall is still that when the
    LLM sees it."""
    wide = drawing(tmp_path / "wide.png", size=(2400, 600))

    png = load_task_images([str(wide)])[0].png

    assert decoded(png)[1] == (images.MAX_SIDE_PX, images.MAX_SIDE_PX // 4)


def test_a_picture_already_under_the_cap_keeps_its_own_size(tmp_path: Path) -> None:
    """Nothing is scaled up and nothing is scaled down for the sake of it: a sketch a person
    drew at 320x240 is legible at 320x240 and arrives at 320x240."""
    small = drawing(tmp_path / "small.png", size=(320, 240))

    png = load_task_images([str(small)])[0].png

    assert decoded(png)[1] == (320, 240)


def test_a_picture_too_heavy_to_send_is_shrunk_until_it_fits(tmp_path: Path) -> None:
    """A picture can be well inside the edge cap and still be megabytes, and these ride in
    every request for the whole run. A thousand pixels of random noise is already over the
    byte cap at a size no edge cap would touch, so only the second loop can save it."""
    heavy = noise(tmp_path / "heavy.png", 1000)
    assert heavy.stat().st_size > images.MAX_BYTES

    png = load_task_images([str(heavy)])[0].png

    assert len(png) <= images.MAX_BYTES
    width, height = decoded(png)[1]
    assert width == height  # a square went in, and shrinking keeps it square
    assert images.MIN_SIDE_PX <= width < 1000


def test_shrinking_stops_before_the_picture_is_unreadable_and_names_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The refusal exists so that one bad file is one line rather than a blank thumbnail the
    LLM is asked to describe. No real picture stays over 1.5 MB at 64 pixels, so the cap is
    moved down to where a picture a test can write does, and the loop has to give up at
    MIN_SIDE_PX with the path in its message."""
    monkeypatch.setattr(images, "MAX_BYTES", 2_000)
    stubborn = noise(tmp_path / "stubborn.png", 200)

    with pytest.raises(TaskImageError) as refusal:
        load_task_images([str(stubborn)])

    said = str(refusal.value)
    assert str(stubborn) in said
    assert f"{images.MIN_SIDE_PX}x{images.MIN_SIDE_PX}" in said
    assert "unreadable" in said


def test_a_path_that_is_not_there_is_refused_by_name(tmp_path: Path) -> None:
    """Refused here, before the robot is connected and before a run directory exists: the
    alternative is a run that reaches its first request without the thing the task is about."""
    missing = tmp_path / "nope.png"

    with pytest.raises(TaskImageError) as refusal:
        load_task_images([str(missing)])

    assert str(missing) in str(refusal.value)
    assert "no such file" in str(refusal.value)


def test_a_directory_is_refused_by_name(tmp_path: Path) -> None:
    """A directory exists, so the missing-file check passes it straight through to PIL, which
    would raise something from inside a decoder about a permission error instead."""
    folder = tmp_path / "pictures"
    folder.mkdir()

    with pytest.raises(TaskImageError) as refusal:
        load_task_images([str(folder)])

    assert str(folder) in str(refusal.value)
    assert "that is a directory, not a picture" in str(refusal.value)


def test_a_file_that_is_not_a_picture_is_refused_by_name(tmp_path: Path) -> None:
    """PIL raises UnidentifiedImageError on a text file, and a traceback out of a decoder is
    not a sentence about the command line a person typed."""
    notes = tmp_path / "notes.txt"
    notes.write_bytes(b"this is not a picture, it is a shopping list\n")

    with pytest.raises(TaskImageError) as refusal:
        load_task_images([str(notes)])

    assert str(notes) in str(refusal.value)
    assert "not a picture quackd can read" in str(refusal.value)


def test_a_format_quackd_does_not_send_is_refused_and_lists_the_ones_it_does(
    tmp_path: Path,
) -> None:
    """PIL reads far more than quackd sends. A netpbm file decodes perfectly well and is still
    refused, because the refusal's job is to tell a person which file to convert."""
    netpbm = tmp_path / "raw.ppm"
    Image.new("RGB", (16, 16), "white").save(netpbm, format="PPM")

    with pytest.raises(TaskImageError) as refusal:
        load_task_images([str(netpbm)])

    said = str(refusal.value)
    assert str(netpbm) in said and "PPM" in said
    for fmt in images.FORMATS:
        assert fmt in said


def test_two_pictures_with_one_name_are_numbered_and_a_unique_name_is_left_alone(
    tmp_path: Path,
) -> None:
    """The LLM is told a name and the task refers to the picture by it, so two directories
    each holding a `sketch.png` must not both arrive as `sketch.png`. The file that clashes
    with nothing keeps the name the person typed, because a number there would be noise."""
    paths = [
        str(drawing(tmp_path / "left" / "sketch.png")),
        str(drawing(tmp_path / "right" / "sketch.png")),
        str(drawing(tmp_path / "cat.png")),
    ]

    loaded = load_task_images(paths)

    assert [p.name for p in loaded] == ["1-sketch.png", "2-sketch.png", "cat.png"]


def test_the_number_on_a_clashing_name_is_its_place_in_the_whole_list(tmp_path: Path) -> None:
    """The prefix is the picture's position among all of them, not a count of the clashes, so
    the same two files behind one unique file are `2-` and `3-`. That is what makes the number
    answer "which --image was that", which is the question a person asking has."""
    paths = [
        str(drawing(tmp_path / "cat.png")),
        str(drawing(tmp_path / "left" / "sketch.png")),
        str(drawing(tmp_path / "right" / "sketch.png")),
    ]

    loaded = load_task_images(paths)

    assert [p.name for p in loaded] == ["cat.png", "2-sketch.png", "3-sketch.png"]


def test_the_pictures_come_back_in_the_order_they_were_given(tmp_path: Path) -> None:
    """A task says "copy the first picture onto the second", and the names and the bytes both
    have to line up with the order of the flags for that sentence to mean anything."""
    sizes = {"wide.png": (240, 120), "tall.png": (120, 240), "square.png": (160, 160)}
    order = ["tall.png", "square.png", "wide.png"]
    for name, size in sizes.items():
        drawing(tmp_path / name, size=size)

    loaded = load_task_images([str(tmp_path / name) for name in order])

    assert [p.name for p in loaded] == order
    assert [decoded(p.png)[1] for p in loaded] == [sizes[name] for name in order]


# ── two things a plain convert("RGB") gets wrong, both of them silently ─────────────────
#
# Found by writing these tests rather than by reading the code, which is the point of them:
# neither failure raises, neither warns, and both leave a run that succeeds while the LLM
# answers about something other than the file it was handed.


def test_a_drawing_on_a_transparent_background_arrives_as_a_drawing(tmp_path: Path) -> None:
    """The likeliest file this flag will ever be given, and the one that used to break.

    A sketch exported the ordinary way is strokes on a transparent background, and a
    transparent pixel still stores a colour underneath: for a drawing saved out of most tools
    that colour is black. `convert("RGB")` keeps it and throws the alpha away, so the circle
    and its paper both became black and the LLM was handed a solid rectangle as the thing it
    had been asked to draw. Nothing raised, and the run succeeded.

    The picture has to composite onto white first, because a drawing with no background is a
    drawing on paper, and paper is what the arm is about to be looking at."""
    path = tmp_path / "sketch.png"
    img = Image.new("RGBA", (200, 200), (0, 0, 0, 0))
    ImageDraw.Draw(img).ellipse([40, 40, 160, 160], outline=(0, 0, 0, 255), width=6)
    img.save(path)

    with Image.open(io.BytesIO(load_task_images([str(path)])[0].png)) as out:
        assert out.mode == "RGB"
        colours = out.getcolors(maxcolors=1 << 16) or []
        assert len(colours) > 1, "one colour means the whole picture went the same way"
        by_count = sorted(colours, reverse=True)
        assert by_count[0][1] == images.GROUND, "the paper is white, not the alpha's black"
        assert any(sum(colour) < 200 for _, colour in colours), "and the strokes survived"


def test_a_palette_picture_with_one_transparent_colour_is_flattened_too(tmp_path: Path) -> None:
    """A GIF, and a PNG saved with a palette, carry transparency as an index rather than as a
    channel, so the mode is `P` and the alpha test that reads `RGBA` misses it. PIL records
    which index it was under `info["transparency"]`, which is what this reads instead."""
    path = tmp_path / "flag.gif"
    img = Image.new("P", (60, 60), 0)
    img.putpalette([0, 0, 0] + [200, 30, 30] * 255)
    ImageDraw.Draw(img).rectangle([10, 10, 50, 50], fill=1)
    img.save(path, transparency=0)

    with Image.open(io.BytesIO(load_task_images([str(path)])[0].png)) as out:
        colours = out.getcolors(maxcolors=1 << 16) or []
        assert images.GROUND in [colour for _, colour in colours], (
            "the transparent index became white rather than the palette's black"
        )


def test_a_photograph_that_says_it_is_rotated_arrives_the_way_up_it_looks(
    tmp_path: Path,
) -> None:
    """A phone stores its rotation in an EXIF tag rather than in the pixels, so every viewer
    shows the picture upright and the pixels are on their side. Re-encoding to PNG drops the
    tag, so the LLM used to be handed the sideways pixels with nothing left to say so.

    The rotation has to be moved into the pixels before the tag is lost. Asserted on the shape
    rather than on the tag, because the shape is what the LLM sees."""
    path = tmp_path / "bench.jpg"
    img = Image.new("RGB", (400, 200), (10, 120, 200))
    exif = img.getexif()
    exif[274] = 6  # "rotate 90 clockwise on display", the common portrait phone value
    img.save(path, exif=exif)

    assert decoded(load_task_images([str(path)])[0].png)[1] == (200, 400), (
        "400x200 tagged as rotated is a portrait picture, and is sent as one"
    )


def test_a_picture_with_no_orientation_tag_is_left_exactly_as_it_is(tmp_path: Path) -> None:
    """The other half of the one above: most files carry no such tag, and a transform that
    fired on them anyway would turn every ordinary picture on its side."""
    path = drawing(tmp_path / "plain.jpg", size=(400, 200), fmt="JPEG")
    assert decoded(load_task_images([str(path)])[0].png)[1] == (400, 200)


# ── what an adversarial pass found, once each ───────────────────────────────────────────


def test_a_picture_claiming_to_be_enormous_is_one_line_and_not_a_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PIL raises `DecompressionBombError` from inside `Image.open`, before any check of
    quackd's own runs. It is not an `OSError`, so every clause here used to miss it and a
    `quackd run --image` ended in a traceback rather than in the one line every other bad
    file gets.

    The cap is lowered rather than a real bomb being written, because the point is the class
    of the exception and not how many pixels it takes to provoke it."""
    path = drawing(tmp_path / "big.png")
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 100)
    with pytest.raises(TaskImageError) as caught:
        load_task_images([str(path)])
    assert str(path) in str(caught.value)
    assert "larger than anything quackd will decode" in str(caught.value)


def test_a_sixteen_bit_picture_is_scaled_into_a_byte_rather_than_clipped(
    tmp_path: Path,
) -> None:
    """A depth map, a scan and a 16-bit photograph all decode to a mode whose samples do not
    fit in a byte, and `convert("RGB")` on one of those clips rather than scales. Only the
    darkest 0.4% of a 16-bit range survived; everything above 255 came out white, so the
    picture the LLM was handed was a white rectangle with a dark corner.

    Asserted on the ordering and the ends rather than on exact values, because the stretch is
    what matters and the rounding is not."""
    path = tmp_path / "depth.png"
    values = np.array([[0, 100, 255, 256, 300, 1000, 65535]], dtype=np.uint16)
    Image.fromarray(values).save(path)

    out = np.asarray(Image.open(io.BytesIO(load_task_images([str(path)])[0].png)))[0, :, 0]
    assert out[0] == 0 and out[-1] == 255, "the ends of the range are the ends of the byte"
    assert list(out) == sorted(out), "and nothing crosses over on the way"
    assert len(set(out.tolist())) > 2, "clipping would have flattened almost all of it to 255"


def test_a_flat_sixteen_bit_picture_does_not_divide_by_its_own_zero_range(
    tmp_path: Path,
) -> None:
    """The degenerate input for the test above: every sample identical, so there is no range
    to stretch it across."""
    path = tmp_path / "flat.png"
    Image.fromarray(np.full((8, 8), 4000, dtype=np.uint16)).save(path)
    out = np.asarray(Image.open(io.BytesIO(load_task_images([str(path)])[0].png)))
    assert len(np.unique(out)) == 1, "one value in, one value out, and no exception"


def test_an_invented_name_never_lands_on_one_a_file_already_has(tmp_path: Path) -> None:
    """The numbering that disambiguates two files called `sketch.png` used to invent a name
    without checking whether anything already had it. Somebody who really does have a
    `2-sketch.png` next to two `sketch.png`s ended up with two pictures under one name, which
    is precisely what the numbering exists to prevent: the task says "the one called
    2-sketch.png" and two different pictures answer to it."""
    paths = [
        str(drawing(tmp_path / "a" / "sketch.png")),
        str(drawing(tmp_path / "b" / "sketch.png")),
        str(drawing(tmp_path / "2-sketch.png")),
    ]
    names = [picture.name for picture in load_task_images(paths)]
    assert len(set(names)) == 3, names
    assert "2-sketch.png" in names, "the file that really is called that keeps its name"


def test_an_empty_path_names_itself_rather_than_the_current_directory(tmp_path: Path) -> None:
    """`Path("")` is the current directory, which exists and is a directory, so an unset shell
    variable (`--image "$SKETCH"`) used to be refused as "that is a directory, not a picture",
    naming no file and giving a reason that was not the reason."""
    with pytest.raises(TaskImageError) as caught:
        load_task_images([""])
    assert "empty path" in str(caught.value)


def test_a_picture_over_the_bomb_limit_refuses_instead_of_printing_a_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PIL raises above twice its limit and merely *warns* between the limit and twice it, and
    that warning went to the terminal as a raw site-packages line in the middle of quackd's own
    output, after which the run carried on as though nothing had happened."""
    path = drawing(tmp_path / "big.png", size=(400, 400))
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 100)
    with warnings.catch_warnings():
        warnings.simplefilter("error")  # any warning that escaped would raise here instead
        with pytest.raises(TaskImageError) as caught:
            load_task_images([str(path)])
    assert "larger than anything quackd will decode" in str(caught.value)
