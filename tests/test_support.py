"""The shared helpers in `tests/support.py`, held to what the copies they replaced did.

Each helper here stood in for a copy in several test modules, so a change to one now reaches
all of them at once. These tests pin what those modules rely on: when a wait looks and when it
gives up, what a failure says, what a loaded script is registered as, and which executor a
test gets when it asks for nothing more.
"""

from __future__ import annotations

import asyncio
import socket
import sys
from pathlib import Path

import pytest

from roboglot.perception.color_blob import ColorBlobDetector
from roboglot.safety import allow_all, deny_all
from roboglot.verbs.registry import registry_from_manifest
from roboglot_open_duck import OpenDuckAdapter
from roboglot_open_duck.mock import OpenDuckMock
from tests.support import (
    UNTIL_WITHIN_S,
    executor_for,
    load_script,
    one_line,
    until,
    until_async,
    unused_port,
    verb_column,
)


class _Turns:
    """A condition that comes true on its `true_on`th look, counting every look."""

    def __init__(self, true_on: int) -> None:
        self.true_on = true_on
        self.looks = 0

    def __call__(self) -> bool:
        self.looks += 1
        return self.looks >= self.true_on


# ── until and until_async ───────────────────────────────────────────────────────────────


def test_until_returns_on_the_look_its_condition_comes_true() -> None:
    """A wait that looked once more after its condition held, or stopped a look early, would
    be a different wait from the copies it replaced, whose tests count on the first."""
    done = _Turns(true_on=3)
    until(done, every_s=0)
    assert done.looks == 3


def test_until_asks_the_condition_before_the_clock() -> None:
    """A condition already true returns even past the deadline: the condition is asked first,
    so a test whose work finished on the last look passes rather than failing on the clock."""
    done = _Turns(true_on=1)
    until(done, within_s=-1.0)
    assert done.looks == 1


def test_until_fails_naming_what_it_waited_for_and_how_long() -> None:
    """The failure is an `AssertionError`, raised by hand because pytest does not rewrite the
    asserts of a helper module, and it says what never came and the bound it was given."""
    with pytest.raises(AssertionError, match=r"^the fake's reply within -1 s$"):
        until(lambda: False, within_s=-1.0, what="the fake's reply")


def test_until_says_it_never_came_when_nothing_is_named() -> None:
    """The default words, which every file that binds no `what` of its own fails with."""
    with pytest.raises(AssertionError, match=r"^it never came within -1 s$"):
        until(lambda: False, within_s=-1.0)


def test_until_waits_ten_seconds_unless_told() -> None:
    """The default bound the files that bind none rely on: `until` and `until_async` share it,
    so a test moved from one to the other keeps its limit."""
    assert UNTIL_WITHIN_S == 10.0
    assert until.__defaults__ == (UNTIL_WITHIN_S,)
    assert until_async.__defaults__ == (UNTIL_WITHIN_S,)


async def test_until_async_lets_the_loop_run_the_work_it_waits_for() -> None:
    """The asynchronous wait sleeps on the event loop, so a task on the same loop, which is
    what most of its callers wait for, runs while it waits. A blocking sleep would starve the
    task and fail every such test at its bound."""
    landed = asyncio.Event()

    async def work() -> None:
        await asyncio.sleep(0)
        landed.set()

    task = asyncio.create_task(work())
    await until_async(landed.is_set, every_s=0)
    await task


async def test_until_async_fails_as_until_does() -> None:
    """The same words as the blocking wait, so a failure reads the same in either kind of test."""
    done = _Turns(true_on=1_000_000)
    with pytest.raises(AssertionError, match=r"^the frame within -1 s$"):
        await until_async(done, within_s=-1.0, what="the frame")
    assert done.looks == 1


# ── unused_port ─────────────────────────────────────────────────────────────────────────


def test_unused_port_is_a_loopback_port_nothing_holds() -> None:
    """The port is let go before it is returned, so a server can bind it at once and a client
    finds nothing listening there. A port still held would fail the first and hang neither."""
    port = unused_port()
    assert 0 < port < 2**16
    with socket.socket() as server:
        server.bind(("127.0.0.1", port))


# ── one_line ────────────────────────────────────────────────────────────────────────────


def test_one_line_undoes_a_terminals_wrapping_and_nothing_else() -> None:
    """Line breaks, tabs and runs of spaces become one space and the ends are trimmed, which is
    what lets a plain `in` find a sentence Rich wrapped; colour codes are left for
    `plain_text`, whose job they are."""
    wrapped = "  no robot\n   answered\tat\r\n  \x1b[1mCOM3\x1b[0m  \n"
    assert one_line(wrapped) == "no robot answered at \x1b[1mCOM3\x1b[0m"


# ── load_script ─────────────────────────────────────────────────────────────────────────


def test_load_script_registers_the_module_before_it_runs(tmp_path: Path) -> None:
    """A dataclass with string annotations resolves them through `sys.modules` while its
    module runs: the daemons and scripts loaded by path have them, and fail to load if the
    module is registered only afterwards, or not at all."""
    script = tmp_path / "daemon.py"
    script.write_text(
        "from __future__ import annotations\n"
        "import dataclasses\n"
        "import sys\n"
        "@dataclasses.dataclass\n"
        "class Reading:\n"
        "    value: int\n"
        "REGISTERED = sys.modules[__name__] is not None\n",
        encoding="utf-8",
    )
    name = "roboglot_test_support_daemon"
    try:
        module = load_script(script, name)
        assert sys.modules[name] is module
        assert module.REGISTERED
        assert module.Reading(3).value == 3
    finally:
        sys.modules.pop(name, None)


def test_load_script_names_a_file_it_cannot_load(tmp_path: Path) -> None:
    """A path Python has no loader for fails with the path in the message, not with an
    `AttributeError` on a `None` deep inside importlib."""
    notes = tmp_path / "notes.txt"
    notes.write_text("not Python", encoding="utf-8")
    with pytest.raises(AssertionError, match=r"notes\.txt could not be loaded as a module"):
        load_script(notes, "roboglot_test_support_notes")
    assert "roboglot_test_support_notes" not in sys.modules


# ── executor_for ────────────────────────────────────────────────────────────────────────


async def test_executor_for_keeps_every_safe_default_unless_asked() -> None:
    """A test that asks for nothing gets the executor's own defaults: no contract, no detector,
    no manifest, every confirmation denied. A helper that quietly allowed what the executor
    refuses would let a test pass that a run would fail."""
    adapter = OpenDuckAdapter(OpenDuckMock())
    manifest = await adapter.connect()
    executor = executor_for(adapter)
    assert executor.registry.names() == registry_from_manifest(manifest, adapter).names()
    assert executor.transport is adapter
    assert executor.contract is None
    assert executor.detector is None
    assert executor.manifest is None
    assert executor.confirm is deny_all


async def test_executor_for_gives_what_a_test_asks_for() -> None:
    """`seeing` gives each executor a detector of its own, `with_manifest` hands the verbs the
    manifest, and any other field goes through as given."""
    adapter = OpenDuckAdapter(OpenDuckMock())
    manifest = await adapter.connect()
    first = executor_for(adapter, manifest, seeing=True, with_manifest=True, confirm=allow_all)
    second = executor_for(adapter, seeing=True)
    assert isinstance(first.detector, ColorBlobDetector)
    assert isinstance(second.detector, ColorBlobDetector)
    assert first.detector is not second.detector
    assert first.manifest is manifest
    assert first.confirm is allow_all


def test_executor_for_refuses_an_adapter_with_no_manifest() -> None:
    """An adapter not yet connected has no manifest to take the verbs from: the helper says so
    rather than building an executor with no verbs, whose every call would be refused."""
    adapter = OpenDuckAdapter(OpenDuckMock())
    with pytest.raises(AssertionError, match="has no manifest"):
        executor_for(adapter)


# ── verb_column ─────────────────────────────────────────────────────────────────────────


def test_verb_column_reads_the_first_column_only() -> None:
    """A verb named in another verb's description is not a verb the robot has: the column is
    read, not the whole output."""
    table = "\n".join(
        [
            "┏━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓",
            "┃ verb         ┃ description                        ┃",
            "┡━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩",
            "│ approach_and │ walk up to it, then kick or grab   │",
            "│ stop         │ stop every motor                   │",
            "└──────────────┴────────────────────────────────────┘",
        ]
    )
    assert verb_column(table) == {"approach_and", "stop"}


def test_verb_column_fails_when_a_name_was_cut_short() -> None:
    """Rich ends a cell too narrow for its text with an ellipsis: a test that then missed the
    verb would blame the robot for the terminal's width."""
    table = "│ approach_a… │ walk up to it │\n│ stop        │ stop          │"
    with pytest.raises(AssertionError, match="rendered too narrow"):
        verb_column(table)
