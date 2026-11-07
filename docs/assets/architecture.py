"""Draw `architecture-light.svg` and `architecture-dark.svg`: the four layers an intent passes
through on its way to a robot, from the adapter catalogue.

The README and `docs/concepts/architecture.md` show this picture. It is generated, because a
picture of the adapters that somebody has to redraw is a picture that stops being true the day
the tenth one lands. Each adapter is drawn where its row in `quackd/adapters/catalogue.py` says:
its `family` puts it in a box of the second layer, and its `route` in a box of the third. So a
new adapter joins the picture by naming those two, and a name this script has no label for
stops it with a message rather than drawing a box nobody wrote.

    python docs/assets/architecture.py          # write both files
    python docs/assets/architecture.py --check  # exit 1 if either would change

`tests/test_docs.py` runs the check on every commit and reads the light file back. Nothing here
measures text, because nothing here can: the width of a line is an estimate from the size of its
letters, on the safe side, and `layout` refuses a line that estimate says does not fit. The same
two files come out on every machine, since nothing depends on an installed font.

It imports the catalogue and nothing else of quackd's, and reads no environment.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from html import escape
from pathlib import Path

from quackd.adapters.catalogue import OFFICIAL, AdapterInfo

ASSETS = Path(__file__).resolve().parent
REPO = ASSETS.parents[1]
FILES = {"light": ASSETS / "architecture-light.svg", "dark": ASSETS / "architecture-dark.svg"}

CANVAS = 880
MARGIN = 20
PAD = 16
GAP = 12
TITLE_PX = 17
BAND_PX = 15
NAME_PX = 14
TEXT_PX = 13
LINE = 18
ARROW_GAP = 34
SANS = "-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif"
MONO = "ui-monospace,SFMono-Regular,'SF Mono',Menlo,Consolas,monospace"

TITLE = "Only the body layer is one per robot: below it, each robot sits by its kind and its route"

FAMILIES: dict[str, tuple[str, str]] = {
    "arm": ("Servo arm", "rest pose, fold first, hand-off, travel clip, paced joint moves"),
    "biped": ("Biped on a runtime", "a twist re-sent while it walks, skills, a posture, a deadman"),
    "wheeled": ("Wheeled base", "a twist, a stop that zeroes it, places where odometry allows"),
    "humanoid": (
        "Humanoid, own loop",
        "a loop quackd ships, safe-pose slew, keyframes, clamps",
    ),
}
"""The boxes of the second layer, in the order they are drawn: title, what bodies of that kind
do alike. The catalogue's `family` names one of these for every adapter."""

ROUTES: dict[str, tuple[str, str]] = {
    "sdk": ("Import the SDK", "upstream ships a package with a client"),
    "wire": ("Speak its wire", "upstream ships a host process; the framing is ours"),
    "bridge": ("A bridge quackd ships", "quackd runs a daemon of its own on the robot"),
}
"""The boxes of the third layer: title, when that route is the one a robot leaves you."""

WIRES: dict[str, str] = {
    "jsonrpc": "robotd JSON-RPC",
    "zmq": "ZeroMQ hosts",
    "cockpit": "MOSS cockpit",
}
"""What a backend of a `wire` adapter speaks, named for the box's own line."""

TRANSPORT = (
    "Transport: USB serial, Unix socket, TCP, HTTP, WebSocket. "
    "Bytes only: it knows nothing about the body."
)
BODY_TITLE = "Body: data, one per robot"
BODY_NAME = "Manifest, datasheet, verbs offered, limits, safety authority, rest pose, cannot"
BODY_TEXT = (
    "Of the four, the one layer the LLM reads: what this body is, what it can do and what it "
    "cannot. Described bodies stop here, as one YAML of their maker's figures that nothing drives."
)
FAMILY_TITLE = "Behaviour family: what bodies of a kind do alike"
_COUNTS = {2: "two", 3: "three", 4: "four", 5: "five", 6: "six"}
ROUTE_TITLE = (
    "Binding: how an intent reaches the robot, by one of "
    f"{_COUNTS.get(len(ROUTES), str(len(ROUTES)))} routes"
)


@dataclass(frozen=True)
class Theme:
    """GitHub's own palette, so the picture reads as part of the page it sits on. Each file
    paints its own canvas, because PyPI shows only the light one, on whatever page it has."""

    canvas: str
    text: str
    muted: str
    border: str
    accent: str
    accent_fill: str
    tint: str


THEMES = {
    "light": Theme(
        canvas="#ffffff",
        text="#1f2328",
        muted="#59636e",
        border="#d0d7de",
        accent="#9a6700",
        accent_fill="#fff8c5",
        tint="#f6f8fa",
    ),
    "dark": Theme(
        canvas="#0d1117",
        text="#e6edf3",
        muted="#9198a1",
        border="#3d444d",
        accent="#d29922",
        accent_fill="#2b2410",
        tint="#151b23",
    ),
}


# ── what goes in each box ───────────────────────────────────────────────────────────────


def families_of(info: AdapterInfo) -> tuple[str, ...]:
    """The boxes of the second layer this adapter sits in, its own kind first."""
    return tuple(info.family)


def routes_of(info: AdapterInfo) -> tuple[str, ...]:
    """The boxes of the third layer this adapter sits in."""
    return tuple(info.route)


def adapters() -> tuple[AdapterInfo, ...]:
    """The rows the picture draws: those that drive a robot, in the catalogue's own order."""
    return tuple(info for info in OFFICIAL if info.kind == "adapter")


@dataclass(frozen=True)
class Member:
    """A name in a box. `adapter` is what a test reads back: None for a word that is only prose."""

    text: str
    adapter: str | None = None


def _named(names: Sequence[str]) -> list[Member]:
    out: list[Member] = []
    for index, name in enumerate(names):
        if index:
            out.append(Member(", "))
        out.append(Member(name, name))
    return out


def family_members(family: str) -> list[Member]:
    """Who sits in a family's box: the adapters whose own kind it is. A second family a row names,
    the arms a cart carries, is not drawn, since the box says what that family's bodies do and a
    cart's arms do none of it: their hosts drive them."""
    return _named([i.name for i in adapters() if families_of(i)[:1] == (family,)])


def _spoken(info: AdapterInfo) -> str:
    """What a `wire` adapter speaks, by the first of its backends `WIRES` names."""
    for backend in info.backends:
        if backend in WIRES:
            return WIRES[backend]
    raise SystemExit(f"{info.name} takes the wire route, and WIRES names none of its backends")


def route_members(route: str) -> list[Member]:
    """Who takes a route, in the catalogue's order, with what each speaks or imports where that
    says something its name does not. A daemon under `bridge/` that is no adapter's moves no
    robot, the Jetson's among them, so it is not a binding and is not drawn."""
    members: list[Member] = []
    for info in adapters():
        if route not in routes_of(info):
            continue
        if members:
            members.append(Member(", "))
        members.append(Member(info.name, info.name))
        said = _spoken(info) if route == "wire" else info.sdk if route == "sdk" else None
        if said and said != info.name:
            members.append(Member(f" ({said})"))
    return members


def check_labels() -> None:
    """Every family and route the catalogue names has a box here: a new one stops the script
    with what to add, rather than leaving an adapter out of the picture."""
    for info in adapters():
        for family in families_of(info):
            if family not in FAMILIES:
                raise SystemExit(
                    f"{info.name}: docs/assets/architecture.py has no box for family {family!r}"
                )
        for route in routes_of(info):
            if route not in ROUTES:
                raise SystemExit(
                    f"{info.name}: docs/assets/architecture.py has no box for route {route!r}"
                )
        if not families_of(info) or not routes_of(info):
            raise SystemExit(f"{info.name}: its catalogue row names no family or no route")


# ── how wide a line is, and where it breaks ─────────────────────────────────────────────


BOLD = 1.12
"""How much wider a title in weight 600 draws than the same text in 400, with room to spare: Arial
Bold measures about 8 percent wider, and a box's title is the one line drawn bold."""


def advance(text: str, px: float, mono: bool = False, *, bold: bool = False) -> float:
    """How wide `text` draws, as an estimate on the safe side. Nothing measures a font here, so
    this is the width a wide sans would take: capitals wider than the rest, and every character
    of a monospace the same."""
    if mono:
        return len(text) * px * 0.62
    wide = sum(1 for c in text if c.isupper() or c in "mw@")
    narrow = sum(1 for c in text if c in " ,.:;'iljtf()")
    rest = len(text) - wide - narrow
    return (wide * 0.74 + narrow * 0.34 + rest * 0.56) * px * (BOLD if bold else 1.0)


def wrap(text: str, px: float, width: float) -> list[str]:
    """`text` broken to fit `width`, each line the longest that fits by `advance`."""
    lines: list[str] = []
    line = ""
    for word in text.split():
        trial = f"{line} {word}".strip()
        if advance(trial, px) <= width or not line:
            line = trial
        else:
            lines.append(line)
            line = word
    if line:
        lines.append(line)
    return lines


def wrap_members(members: Sequence[Member], px: float, width: float) -> list[list[Member]]:
    """A box's members broken to fit. A name is never split across two lines, because it is the
    thing a reader looks for and a test reads back, and what is said of it in brackets and the
    comma after it stay with it. A name is in the monospace stack and the words around it in the
    sans, so each is measured as such."""
    chunks: list[list[Member]] = []
    for member in members:
        if member.adapter is not None:
            chunks.append([member])
        elif chunks and (member.text.strip() in (",", ";") or member.text.startswith(" (")):
            chunks[-1].append(member)
        else:
            chunks.extend([Member(word)] for word in re.findall(r"\s*\S+\s*", member.text))
    lines: list[list[Member]] = [[]]
    used = 0.0
    for chunk in chunks:
        size = sum(advance(m.text, px, mono=m.adapter is not None) for m in chunk)
        if lines[-1] and used + size > width:
            lines.append([])
            used = 0.0
        lines[-1].extend(chunk)
        used += size
    return [_trimmed(line) for line in lines if line]


def _trimmed(line: list[Member]) -> list[Member]:
    """A line with no space at either end: a space only means something between two words."""
    out = list(line)
    out[0] = Member(out[0].text.lstrip(), out[0].adapter)
    out[-1] = Member(out[-1].text.rstrip(), out[-1].adapter)
    return out


# ── the layout ──────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Box:
    id: str
    name: str
    note: list[str]
    members: list[list[Member]]
    x: float
    width: float


def _boxes(
    prefix: str, table: dict[str, tuple[str, str]], members: dict[str, list[Member]]
) -> list[Box]:
    count = len(table)
    inner = CANVAS - 2 * (MARGIN + PAD)
    width = (inner - GAP * (count - 1)) / count
    boxes = []
    for index, (key, (name, note)) in enumerate(table.items()):
        text_width = width - 2 * PAD
        boxes.append(
            Box(
                id=f"{prefix}-{key}",
                name=name,
                note=wrap(note, TEXT_PX, text_width),
                members=wrap_members(members[key], TEXT_PX, text_width),
                x=MARGIN + PAD + index * (width + GAP),
                width=width,
            )
        )
        if advance(name, NAME_PX, bold=True) > text_width:
            raise SystemExit(f"{prefix}-{key}: the title {name!r} does not fit its box")
    return boxes


def _box_height(box: Box) -> int:
    return 2 * PAD - 2 + LINE + LINE * len(box.note) + 6 + LINE * len(box.members)


@dataclass(frozen=True)
class Layout:
    height: int
    body_y: int
    body_h: int
    family_y: int
    family_h: int
    route_y: int
    route_h: int
    transport_y: int
    transport_h: int
    box_family_h: int
    box_route_h: int
    families: list[Box]
    routes: list[Box]
    body_text: list[str]
    transport: list[str]


def layout() -> Layout:
    """Where everything goes, derived from what goes in it, so a longer list of robots makes its
    band taller rather than running out of its box."""
    check_labels()
    if advance(TITLE, TITLE_PX, bold=True) > CANVAS - 2 * MARGIN:
        raise SystemExit("the title no longer fits on one line")
    families = _boxes("family", FAMILIES, {key: family_members(key) for key in FAMILIES})
    routes = _boxes("route", ROUTES, {key: route_members(key) for key in ROUTES})
    inner = CANVAS - 2 * (MARGIN + PAD)
    body_name_lines = wrap(BODY_NAME, NAME_PX, inner)
    if len(body_name_lines) != 1:
        raise SystemExit("the body layer's line no longer fits on one line")
    body_text = wrap(BODY_TEXT, TEXT_PX, inner)
    transport = wrap(TRANSPORT, TEXT_PX, inner)

    head = 46
    band_head = 34
    body_y = head
    body_h = band_head + LINE + LINE * len(body_text) + PAD
    family_y = body_y + body_h + ARROW_GAP
    box_family_h = max(_box_height(b) for b in families)
    family_h = band_head + box_family_h + PAD
    route_y = family_y + family_h + ARROW_GAP
    box_route_h = max(_box_height(b) for b in routes)
    route_h = band_head + box_route_h + PAD
    transport_y = route_y + route_h + ARROW_GAP
    transport_h = LINE * len(transport) + 2 * 12
    height = transport_y + transport_h + MARGIN

    for box in (*families, *routes):
        for line in box.members:
            used = sum(advance(m.text, TEXT_PX, mono=m.adapter is not None) for m in line)
            if used > box.width - 2 * PAD + 1:
                # a name longer than the box, which wrapping cannot break: an exit and not an
                # assert, so `python -O` stops on it too
                raise SystemExit(f"{box.id}: {''.join(m.text for m in line)} does not fit its box")
    return Layout(
        height=height,
        body_y=body_y,
        body_h=body_h,
        family_y=family_y,
        family_h=family_h,
        route_y=route_y,
        route_h=route_h,
        transport_y=transport_y,
        transport_h=transport_h,
        box_family_h=box_family_h,
        box_route_h=box_route_h,
        families=families,
        routes=routes,
        body_text=body_text,
        transport=transport,
    )


# ── the drawing ─────────────────────────────────────────────────────────────────────────


def _text(x: float, y: float, text: str, *, px: int, fill: str, weight: int = 400) -> str:
    bold = f' font-weight="{weight}"' if weight != 400 else ""
    return f'<text x="{x:g}" y="{y:g}" font-size="{px}" fill="{fill}"{bold}>{escape(text)}</text>'


def _members(x: float, y: float, line: Sequence[Member], theme: Theme) -> str:
    """One line of a box's names. Each adapter is a `tspan` of its own, named by what it is, so a
    test can read the name back rather than look for it inside `xlerobot`."""
    spans = []
    for member in line:
        if member.adapter is not None:
            spans.append(
                f'<tspan data-adapter="{escape(member.adapter)}" font-family="{MONO}">'
                f"{escape(member.text)}</tspan>"
            )
        else:
            spans.append(f"<tspan>{escape(member.text)}</tspan>")
    return (
        f'<text x="{x:g}" y="{y:g}" font-size="{TEXT_PX}" fill="{theme.text}" '
        f'xml:space="preserve">{"".join(spans)}</text>'
    )


def _box(box: Box, y: int, height: int, theme: Theme) -> list[str]:
    left = box.x + PAD
    baseline = y + PAD + 6
    out = [
        f'<g id="{box.id}">',
        f'<rect x="{box.x:g}" y="{y}" width="{box.width:g}" height="{height}" rx="8" '
        f'fill="{theme.canvas}" stroke="{theme.border}"/>',
        _text(left, baseline, box.name, px=NAME_PX, fill=theme.text, weight=600),
    ]
    baseline += LINE
    for line in box.note:
        out.append(_text(left, baseline, line, px=TEXT_PX, fill=theme.muted))
        baseline += LINE
    baseline += 6
    for members in box.members:
        out.append(_members(left, baseline, members, theme))
        baseline += LINE
    out.append("</g>")
    return out


def _arrow(x: float, y1: float, y2: float, theme: Theme) -> str:
    return (
        f'<path d="M{x:g} {y1:g}V{y2:g}" fill="none" stroke="{theme.muted}" stroke-width="1.25" '
        'marker-end="url(#head)"/>'
    )


def render(theme_name: str) -> str:
    """One whole file. Nothing in it depends on the machine, the clock or the environment."""
    theme = THEMES[theme_name]
    at = layout()
    left = MARGIN
    wide = CANVAS - 2 * MARGIN
    inner_x = MARGIN + PAD
    down, up = CANVAS * 0.30, CANVAS * 0.70
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {CANVAS} {at.height}" '
        f'width="{CANVAS}" height="{at.height}" role="img" font-family="{SANS}" '
        f'aria-label="{escape(TITLE)}: an intent goes down through the body, a behaviour family, '
        'a binding and a transport, and state and frames come back up">',
        f"<title>{escape(TITLE)}</title>",
        f'<defs><marker id="head" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" '
        f'markerHeight="7" orient="auto"><path d="M0 0L10 5L0 10z" fill="{theme.muted}"/></marker>'
        "</defs>",
        f'<rect width="{CANVAS}" height="{at.height}" fill="{theme.canvas}"/>',
        _text(left, 30, TITLE, px=TITLE_PX, fill=theme.text, weight=600),
    ]

    # the body: the one layer that is one per robot, so the one that is coloured
    out += [
        '<g id="layer-body">',
        f'<rect x="{left}" y="{at.body_y}" width="{wide}" height="{at.body_h}" rx="8" '
        f'fill="{theme.accent_fill}" stroke="{theme.accent}" stroke-width="2"/>',
        _text(inner_x, at.body_y + 24, BODY_TITLE, px=BAND_PX, fill=theme.text, weight=600),
        _text(inner_x, at.body_y + 24 + LINE + 4, BODY_NAME, px=NAME_PX, fill=theme.text),
    ]
    baseline = at.body_y + 24 + 2 * LINE + 4
    for line in at.body_text:
        out.append(_text(inner_x, baseline, line, px=TEXT_PX, fill=theme.muted))
        baseline += LINE
    out.append("</g>")

    # the family and the binding: shared, so plain
    out += [
        '<g id="layer-family">',
        f'<rect x="{left}" y="{at.family_y}" width="{wide}" height="{at.family_h}" rx="8" '
        f'fill="{theme.tint}" stroke="{theme.border}"/>',
        _text(inner_x, at.family_y + 24, FAMILY_TITLE, px=BAND_PX, fill=theme.text, weight=600),
    ]
    for box in at.families:
        out += _box(box, at.family_y + 34, at.box_family_h, theme)
    out.append("</g>")
    out += [
        '<g id="layer-binding">',
        f'<rect x="{left}" y="{at.route_y}" width="{wide}" height="{at.route_h}" rx="8" '
        f'fill="{theme.tint}" stroke="{theme.border}"/>',
        _text(inner_x, at.route_y + 24, ROUTE_TITLE, px=BAND_PX, fill=theme.text, weight=600),
    ]
    for box in at.routes:
        out += _box(box, at.route_y + 34, at.box_route_h, theme)
    out.append("</g>")

    # the transport: bytes, and nothing about the robot
    out += [
        '<g id="layer-transport">',
        f'<rect x="{left}" y="{at.transport_y}" width="{wide}" height="{at.transport_h}" rx="8" '
        f'fill="{theme.tint}" stroke="{theme.border}" stroke-dasharray="4 3"/>',
    ]
    baseline = at.transport_y + 12 + 13
    for line in at.transport:
        out.append(_text(inner_x, baseline, line, px=TEXT_PX, fill=theme.text))
        baseline += LINE
    out.append("</g>")

    # the arrows between the layers, and what travels along them
    gaps = (
        (at.body_y + at.body_h, at.family_y),
        (at.family_y + at.family_h, at.route_y),
        (at.route_y + at.route_h, at.transport_y),
    )
    out.append('<g id="arrows">')
    for index, (top, bottom) in enumerate(gaps):
        out.append(_arrow(down, top + 2, bottom - 2, theme))
        out.append(_arrow(up, bottom - 2, top + 2, theme))
        if index == 0:
            middle = (top + bottom) / 2 + 4
            out.append(
                _text(down + 10, middle, "an intent goes down", px=TEXT_PX, fill=theme.muted)
            )
            out.append(
                _text(up + 10, middle, "state and frames come up", px=TEXT_PX, fill=theme.muted)
            )
    out.append("</g>")
    out.append("</svg>")
    return "\n".join(out) + "\n"


def main(argv: Sequence[str]) -> int:
    stale = []
    for name, path in FILES.items():
        drawn = render(name)
        with_lf = path.read_bytes().decode("utf-8").replace("\r\n", "\n") if path.exists() else ""
        if with_lf != drawn:
            stale.append(path)
            if "--check" not in argv:
                with open(path, "w", encoding="utf-8", newline="\n") as f:
                    f.write(drawn)
                print(f"wrote docs/assets/{path.name}")
    if stale and "--check" in argv:
        names = ", ".join(path.name for path in stale)
        print(f"docs/assets/{names} stale: run python docs/assets/architecture.py")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
