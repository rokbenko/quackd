"""Helpers the test modules share, so that each lives once and not in a copy per file.

Imported as the other helper modules here are: `from tests.support import until`. Not the
conftest, which is for fixtures and which pytest's documentation advises against importing.

Two rules keep it cheap and honest. Nothing at the top level is heavier than the standard
library, so a module that wants one helper pays for nothing else: `executor_for` imports
Roboglot inside. And a failure raises `AssertionError` with its own message, because pytest
rewrites a bare `assert` only in test modules and the conftest, and here it would fail with no
word of what was waited for.

A file that needs its own limit, poll interval or path binds it with `functools.partial`, so
the number stays beside the tests that chose it: `_until = partial(until_async, within_s=3.0)`.
"""

from __future__ import annotations

import asyncio
import importlib.util
import socket
import sys
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path
    from types import ModuleType

    from roboglot.safety import Executor

UNTIL_WITHIN_S = 10.0
"""How long `until` waits by default: a bound that ends a test whose condition never comes, not
a figure any test checks. A loaded machine that is slow to get there is slow here too, never
wrong, which a fixed sleep cannot say."""

UNTIL_EVERY_S = 0.01
"""How often `until` looks by default: the interval most of the copies it replaced used."""

UNTIL_WHAT = "it never came"
"""What `until` says it waited for when the caller names nothing."""

TABLE_BAR = chr(9474)
"""The vertical bar Rich draws between a table's columns."""

ELLIPSIS = chr(8230)
"""What Rich puts in place of the end of a cell too narrow for its text."""


def until(
    done: Callable[[], object],
    within_s: float = UNTIL_WITHIN_S,
    *,
    every_s: float = UNTIL_EVERY_S,
    what: str = UNTIL_WHAT,
) -> None:
    """Return as soon as `done()` is true; fail if it is still false after `within_s`.

    `done()` is asked before the clock is read, so a condition already true returns without a
    sleep, and one that comes true on the last look still counts. The clock is
    `time.monotonic`, which no change of the wall's time can move.
    """
    deadline = time.monotonic() + within_s
    while not done():
        if time.monotonic() > deadline:
            raise AssertionError(f"{what} within {within_s:g} s")
        time.sleep(every_s)


async def until_async(
    done: Callable[[], object],
    within_s: float = UNTIL_WITHIN_S,
    *,
    every_s: float = UNTIL_EVERY_S,
    what: str = UNTIL_WHAT,
) -> None:
    """`until` for a test on an event loop: it sleeps with `asyncio.sleep`, so the loop's other
    tasks, which are what `done()` waits for, run while it waits.

    The bound is in the wall's seconds and never a count of the loop's turns: a slow machine
    spends turns before the work it waits for gets there, and the work may be on a thread, a
    socket or a child process the loop does not run.
    """
    deadline = time.monotonic() + within_s
    while not done():
        if time.monotonic() > deadline:
            raise AssertionError(f"{what} within {within_s:g} s")
        await asyncio.sleep(every_s)


def unused_port() -> int:
    """A loopback TCP port bound, read and let go, so nothing listens on it when this returns.

    A connection to it is refused, which is what most callers want. A caller that starts a
    server on it can race another process for it in the moment between, so such a test belongs
    where nothing else is running (the ROS contract runs in a container).
    """
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def one_line(text: str) -> str:
    """`text` with every run of whitespace, line breaks included, as one space, and none at the
    ends: what a terminal's wrapping does to a sentence, undone, so a plain `in` finds it.

    It leaves colour codes and table borders alone. Output that has them goes through
    `tests.conftest.plain_text`.
    """
    return " ".join(text.split())


def jpeg_claiming(width: int, height: int) -> bytes:
    """A 16 by 16 baseline JPEG whose frame header (SOF0) says `width` by `height`.

    `Image.open` reads only that header, so this is how a test serves a frame that claims to be
    enormous without making one: a decoder that went on would find 16 by 16 pixels of data
    behind a header promising millions, and a picture that size is never allocated by a test.
    """
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (16, 16), (90, 90, 90)).save(buf, format="JPEG", quality=80)
    data = bytearray(buf.getvalue())
    at = data.index(b"\xff\xc0")  # SOF0: marker, length (2), precision (1), height, width
    data[at + 5 : at + 7] = height.to_bytes(2, "big")
    data[at + 7 : at + 9] = width.to_bytes(2, "big")
    return bytes(data)


def load_script(path: Path, name: str) -> ModuleType:
    """The Python file at `path`, loaded as the module `name`, without importing it through any
    package: how a daemon, a bridge or a script that lives outside Roboglot is run.

    The module is put in `sys.modules` under `name` before it runs, because a dataclass with
    string annotations resolves them through `sys.modules` and fails for a module that is not
    there. A file loaded under its own name in each test module keeps one module's patches out
    of the other's.
    """
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise AssertionError(f"{path} could not be loaded as a module")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def executor_for(
    adapter: Any,
    manifest: Any = None,
    *,
    seeing: bool = False,
    with_manifest: bool = False,
    **fields: Any,
) -> Executor:
    """An executor over `adapter`, with the verbs `manifest` names (the adapter's own when none
    is given) and every other field at the executor's own default unless `fields` sets it.

    The executor's defaults are the safe ones: no contract, no detector, no manifest, and every
    confirmation denied. A test that needs more says so: `seeing` gives it a colour-blob
    detector of its own, `with_manifest` hands the manifest to the verbs (composites pick a
    strategy by it), and `contract=`, `confirm=` and the rest go through as given.
    """
    from roboglot.perception.color_blob import ColorBlobDetector
    from roboglot.safety import Executor
    from roboglot.verbs.registry import registry_from_manifest

    if manifest is None:
        manifest = adapter.manifest
    if manifest is None:
        raise AssertionError(f"{adapter!r} has no manifest: connect it first, or pass one")
    if seeing:
        fields.setdefault("detector", ColorBlobDetector())
    if with_manifest:
        fields.setdefault("manifest", manifest)
    return Executor(registry_from_manifest(manifest, adapter), adapter, **fields)


def verb_column(output: str) -> set[str]:
    """The names in the first column of a table `list-verbs` rendered.

    Searching the whole output is wrong: `approach_and`'s own description names `kick` and
    `grab` as example follow-up verbs, so a substring check finds verbs that are not there. A
    name Rich cut short for want of width fails here, rather than as a verb that is missing.
    """
    names = {
        line.split(TABLE_BAR)[1].strip()
        for line in output.splitlines()
        if line.count(TABLE_BAR) > 2
    }
    if any(ELLIPSIS in name for name in names):
        raise AssertionError(f"rendered too narrow, names elided: {names}")
    return names
