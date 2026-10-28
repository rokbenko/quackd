"""Which L4T release a Jetson runs, and which JetPack that is, from `/etc/nv_tegra_release`.

Text in, versions out, and no file read here. `quackd doctor` parses the board files a host
daemon sends it over the network, since doctor never reads the machine it runs on (ADR-0046),
and `quackd onboard` parses the same files on the board itself (`boards/jetson.py`). One table
and one parser serve both, so the two cannot name a different JetPack for the same release file.
"""

from __future__ import annotations

import re

L4T_LINE = re.compile(r"R(\d+)\s*\(release\),\s*REVISION:\s*(\d+(?:\.\d+)*)")
"""`# R36 (release), REVISION: 4.3, GCID: ...` is the first line of `/etc/nv_tegra_release`."""

JETPACK_FOR_L4T = {
    "36.5.2": "6.2.3",
    "36.5.0": "6.2.2",
    "36.4.4": "6.2.1",
    "36.4.3": "6.2",
    "36.4.0": "6.1",
    "36.3.0": "6.0",
    "39.2.1": "7.2.1",
    "39.2.0": "7.2",
    "38.4.0": "7.1",
    "38.2.1": "7.0",
    "38.2.0": "7.0",
}
"""Exact releases only, read off NVIDIA's JetPack archive on 2026-09-22.

`docs/guides/jetson.md` prints this same table and `tests/test_docs.py` holds the two to each other,
so a board whose JetPack shipped after this was written cannot read as one version in the
documentation and another on the screen."""

JETPACK_MAJOR = {"32": "4.x", "35": "5.x", "36": "6.x", "38": "7.x", "39": "7.x"}
"""What to say about a revision the table above has not heard of. The major version only, on
purpose: L4T 35.1 was JetPack 5.0.2 and 35.2.1 was 5.1, so a guessed minor here would be wrong
about a board somebody owns."""


def parse_l4t(text: str) -> str | None:
    """`36.4.3` out of the release file's text, or None where it names no release."""
    match = L4T_LINE.search(text)
    return f"{match.group(1)}.{match.group(2)}" if match else None


def jetpack_for(l4t: str) -> str | None:
    """The JetPack an L4T release belongs to: exact where the table knows the release, the major
    alone where it does not, and None for a major NVIDIA has not published."""
    parts = [p for p in l4t.split(".") if p]
    if not parts:
        return None
    # padded to three, because NVIDIA writes JetPack 6.1's as "36.4" and the archive's own
    # table spells the same release "36.4.0"
    exact = ".".join([*parts, "0", "0"][:3])
    return JETPACK_FOR_L4T.get(exact) or JETPACK_MAJOR.get(parts[0])
