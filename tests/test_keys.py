"""`q` with no Enter behind it: the console read a key at a time, and what the key thread makes
of that.

Windows' half runs everywhere over a stand-in for `msvcrt`, and POSIX's over a real pty where
there is one. What neither can show is a real Windows console, which is checked by hand through
ConPTY before a release that touches this: a test here would need a package nothing else does.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import os
import subprocess
import sys
import textwrap
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any

import pytest

from quackd.keys import (
    ESCAPE_GAP_S,
    POLL_S,
    PosixKeys,
    WindowsKeys,
    console_keys,
    is_a_console,
)
from quackd.safety import ASK_POLL_S, TYPED_AHEAD_S, KillSwitch


class FakeConsole:
    """`msvcrt` as `WindowsKeys` uses it, and as the C runtime behaves under it. `keys` are the
    keys pressed so far, one string each: an arrow or a function key is two characters, the first
    of which a read returns while the second goes into the one place the runtime has for a
    character put by. `kbhit` does not look there, the next read takes it from there first, and
    `ungetwch` fails while it is taken."""

    def __init__(self, *keys: str) -> None:
        self.keys: deque[str] = deque(keys)
        self.put_by: str | None = None
        self.echoed: list[str] = []

    def kbhit(self) -> bool:
        return bool(self.keys)

    def getwch(self) -> str:
        if self.put_by is not None:
            key, self.put_by = self.put_by, None
            return key
        assert self.keys, "a read with no key waiting blocks the thread until the next one"
        key = self.keys.popleft()
        if len(key) == 2:
            self.put_by = key[1]
        return key[0]

    def ungetwch(self, key: str) -> None:
        if self.put_by is not None:
            raise OSError("no room")
        self.put_by = key

    def putwch(self, key: str) -> None:
        self.echoed.append(key)


def test_a_key_comes_without_an_enter_and_is_echoed() -> None:
    console = FakeConsole("q")
    assert WindowsKeys(console).read() == "q"
    assert console.echoed == ["q"]


def test_enter_echoes_a_new_line() -> None:
    console = FakeConsole("\r")
    assert WindowsKeys(console).read() == "\r"
    assert console.echoed == ["\r", "\n"]


@pytest.mark.parametrize("key", ["\x00H", "\xe0H", "\xe0P", "\xe0Q", "\x00Q"])
def test_an_arrow_or_a_function_key_is_no_key_at_all(key: str) -> None:
    """Two characters, and neither of them is typed. The second is put by where `kbhit` cannot see
    it, so it came out with the next key pressed, as a key: PageDown's is `Q`, which stopped the
    run, and PageDown and Enter at the question that opens the jaws left them shut."""
    console = FakeConsole(key, "\r")
    keys = WindowsKeys(console)
    assert keys.read() == "\r"
    assert console.echoed == ["\r", "\n"]


def test_a_grave_is_a_key_and_does_not_sit_on_the_next() -> None:
    """`à` reads as the first half of an arrow key. A read for its other half would block with
    nothing there until the next key, and take that one too, which may be the `q`."""
    console = FakeConsole("\xe0", "q")
    keys = WindowsKeys(console)
    assert [keys.read(), keys.read()] == ["\xe0", "q"]
    assert console.echoed == ["\xe0", "q"]


@pytest.mark.parametrize("key", ["\x1a", "\x04", "\x1b", "\t", "\x01"])
def test_a_control_key_is_no_key(key: str) -> None:
    """Ctrl-Z and Ctrl-D above all. Read as the end of the keys, one stray press took `q` away for
    the rest of the run under the line that promises it, and left a `[y/N]` nothing could answer."""
    console = FakeConsole(key, "q")
    assert WindowsKeys(console).read() == "q"
    assert console.echoed == ["q"]


def test_closing_ends_a_read_that_is_waiting() -> None:
    """The thread of a switch that has finished used to stay blocked on stdin for good, and it
    took keys from whatever read the console next."""
    keys = WindowsKeys(FakeConsole())
    got: list[str] = []
    reading = threading.Thread(target=lambda: got.append(keys.read()), daemon=True)
    reading.start()
    keys.close()
    reading.join(2)
    assert not reading.is_alive() and got == [""]


def test_a_backspace_and_the_end_of_a_line_are_drawn() -> None:
    console = FakeConsole()
    WindowsKeys(console).erase()
    assert console.echoed == ["\b", " ", "\b"]
    console.echoed.clear()
    WindowsKeys(console).newline()
    assert console.echoed == ["\r", "\n"]


# ── what is and is not a console ──


def test_a_stream_that_is_no_console_keeps_its_lines() -> None:
    assert console_keys(io.StringIO("q\n")) is None
    assert console_keys(None) is None
    assert not is_a_console(io.StringIO())


def test_nul_is_no_console(tmp_path: Path) -> None:
    """Windows' `isatty` says yes to NUL, because it is a character device."""
    with open(os.devnull, encoding="utf-8") as nul:
        assert console_keys(nul) is None


def test_a_regular_file_is_no_console(tmp_path: Path) -> None:
    path = tmp_path / "keys"
    path.write_text("q\n", encoding="utf-8")
    with path.open(encoding="utf-8") as file:
        assert console_keys(file) is None


# ── what the key thread makes of a key at a time ──


class Pressed:
    """A console that hands over `keys` in order, then says there are no more. A float in them
    is a pause, in seconds, before the next key."""

    def __init__(self, *keys: str | float) -> None:
        self.keys = deque(keys)
        self.erased = 0
        self.ended_lines = 0
        self.closed = False

    def read(self) -> str:
        while self.keys:
            key = self.keys.popleft()
            if isinstance(key, str):
                return key
            time.sleep(key)
        return ""

    def erase(self) -> None:
        self.erased += 1

    def newline(self) -> None:
        self.ended_lines += 1

    def close(self) -> None:
        self.closed = True


async def press(*keys: str | float) -> tuple[KillSwitch, list[str], Pressed]:
    """The whole of the key thread's loop over `keys`, in a thread as it really runs."""
    said: list[str] = []
    ks = KillSwitch(asyncio.Event(), log=said.append)
    ks._loop = asyncio.get_running_loop()
    console = Pressed(*keys)
    ks._keys = console
    await asyncio.to_thread(ks._watch_keys)
    await asyncio.sleep(0)
    return ks, said, console


async def test_q_fires_the_moment_it_is_pressed() -> None:
    """The key the panel names. With no Enter behind it, a press stopped nothing until the next
    Enter, and nobody standing at an arm knows to press one."""
    ks, said, console = await press("q")
    assert ks.presses == 1 and ks.abort.is_set()
    assert said and "'q' pressed" in said[0]
    assert not ks.entered.is_set(), "and no Enter came with it"
    assert console.ended_lines == 1, "so the line it was echoed on is ended for it"


async def test_a_q_fires_before_the_next_key_is_read() -> None:
    said: list[str] = []
    ks = KillSwitch(asyncio.Event(), log=said.append)
    ks._loop = asyncio.get_running_loop()
    gate = threading.Event()

    class Held(Pressed):
        def read(self) -> str:
            if not self.keys:
                gate.wait(2)
                return ""
            return super().read()

    ks._keys = Held("q")
    reader = asyncio.ensure_future(asyncio.to_thread(ks._watch_keys))
    await asyncio.wait_for(ks.pressed.wait(), timeout=2)
    assert said, "fired with the reader still waiting for its next key"
    gate.set()
    await reader


async def test_the_old_habit_of_enter_after_q_answers_nothing() -> None:
    """Somebody who has always pressed Enter after `q`. The press ends the run, the teardown puts
    its question in a moment, and the hand still finishing that habit must not answer it."""
    ks, _, _ = await press("q", "\r")
    assert not ks.entered.is_set()
    assert ks._line_since(0.0) is None


async def test_a_slow_habit_answers_nothing_either() -> None:
    """The question is up within a moment of the press. An Enter past the half second a question
    gives anybody, and still behind the `q`, opened the jaws on what was in them."""
    ks, _, _ = await press("q", TYPED_AHEAD_S + 0.05, "\r")
    assert not ks.entered.is_set()
    assert ks._line_since(0.0) is None


async def test_a_later_enter_is_a_bare_one(monkeypatch: pytest.MonkeyPatch) -> None:
    """`q` is no part of its line at a console: the question that comes a minute later takes this
    Enter as the bare one it is, rather than a line with `q` typed on it, which is a no."""
    monkeypatch.setattr("quackd.safety.ENTER_BEHIND_Q_S", 0.05)
    ks, _, _ = await press("q", 0.1, "\r")
    assert ks.entered.is_set()
    assert ks._line_since(0.0) == ""


async def test_a_line_with_something_on_it_is_still_that_line() -> None:
    ks, _, _ = await press("n", "o", "\r")
    assert ks._line_since(0.0) == "no"
    assert ks.presses == 0


async def test_a_backspace_takes_a_key_off_the_line() -> None:
    """A console read a key at a time does none of this for anybody: a deliberate question takes
    only a bare Enter, and `n`, backspace, Enter is one."""
    ks, _, console = await press("n", "\b", "\r")
    assert ks._line_since(0.0) == ""
    assert console.erased == 1

    ks, _, console = await press("n", "\x7f", "\x7f", "\r")
    assert ks._line_since(0.0) == ""
    assert console.erased == 1, "the second had nothing to take"


async def test_a_q_in_an_answer_is_part_of_the_answer() -> None:
    """Every key belongs to the question being asked, and a backspace to the answer."""
    said: list[str] = []
    ks = KillSwitch(asyncio.Event(), log=said.append)
    ks._loop = asyncio.get_running_loop()
    ks._line_wanted.set()
    ks._keys = Pressed("q", "x", "\b", "u", "\r")
    await asyncio.to_thread(ks._watch_keys)
    assert ks._lines.get_nowait() == "qu"
    assert ks.presses == 0


async def test_ctrl_c_read_as_a_key_is_still_a_press() -> None:
    ks, _, _ = await press("\x03")
    assert ks.presses == 1 and ks.abort.is_set()


def _reader(ks: KillSwitch) -> threading.Event:
    """A key thread that stays alive until the event this returns is set."""
    done = threading.Event()
    ks._thread = threading.Thread(target=done.wait, daemon=True)
    ks._thread.start()
    return done


async def test_a_press_at_a_question_answers_no() -> None:
    """The question sits on the loop's thread. An untimed wait there could not be interrupted on
    Windows, so Ctrl-C at a `[y/N]` did nothing until somebody answered it."""
    ks = KillSwitch(asyncio.Event())
    ks._loop = asyncio.get_running_loop()
    done = _reader(ks)
    try:
        asking = asyncio.create_task(asyncio.to_thread(ks.ask, ""))
        await asyncio.sleep(ASK_POLL_S)
        ks._fire("Ctrl-C")
        assert await asyncio.wait_for(asking, timeout=5) == ""
    finally:
        done.set()


async def test_a_question_the_key_thread_has_gone_from_answers_no() -> None:
    """Nothing would ever put a line there, and it held the loop's thread for good, heartbeat and
    all, over an arm with torque on."""
    ks = KillSwitch(asyncio.Event())
    ks._loop = asyncio.get_running_loop()
    done = _reader(ks)
    asking = asyncio.create_task(asyncio.to_thread(ks.ask, ""))
    await asyncio.sleep(ASK_POLL_S)
    done.set()
    assert await asyncio.wait_for(asking, timeout=5) == ""


async def test_a_question_still_takes_its_line() -> None:
    ks = KillSwitch(asyncio.Event())
    ks._loop = asyncio.get_running_loop()
    done = _reader(ks)
    try:
        asking = asyncio.create_task(asyncio.to_thread(ks.ask, ""))
        await asyncio.sleep(ASK_POLL_S)
        ks._lines.put("y")
        assert await asyncio.wait_for(asking, timeout=5) == "y"
    finally:
        done.set()


async def test_the_end_of_the_keys_is_said_once() -> None:
    ks, _, _ = await press()
    assert ks.keys_ended.is_set() or await _soon(ks.keys_ended)


async def _soon(event: asyncio.Event) -> bool:
    await asyncio.wait_for(event.wait(), timeout=2)
    return True


async def test_uninstalling_ends_the_thread_and_says_nothing_of_it() -> None:
    """A switch that has finished stops reading, and that is no end of stdin for anybody."""
    ks = KillSwitch(asyncio.Event())
    ks._loop = asyncio.get_running_loop()
    console = FakeConsole()
    ks._keys = WindowsKeys(console)
    ks._thread = threading.Thread(target=ks._watch_keys, daemon=True)
    ks._thread.start()
    ks.uninstall()
    ks._thread.join(2)
    assert not ks._thread.is_alive()
    await asyncio.sleep(0)
    assert not ks.keys_ended.is_set()


# ── a real terminal, where there is one ──

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="no pty on Windows")


def _drain(master: int) -> None:
    with contextlib.suppress(OSError):
        while os.read(master, 1024):
            pass


@pytest.fixture
def pty_pair() -> Any:
    """Both ends of a terminal, with what it echoes read off the far one, as a window does. On
    macOS the process that owns a terminal waits at its exit for the terminal's output to drain,
    and nobody reading it is a child that never exits."""
    import pty

    master, slave = pty.openpty()
    draining = threading.Thread(target=_drain, args=(master,), daemon=True)
    draining.start()
    yield master, slave
    os.close(slave)
    draining.join(2)
    os.close(master)


def _lflag(fd: int) -> int:
    import termios

    return termios.tcgetattr(fd)[3]


def _settings(fd: int) -> list[Any]:
    """A terminal's settings, less `PENDIN`, which a BSD kernel sets by itself on the way back to
    line mode: nothing asked for it, and it only says what was waiting gets reprinted."""
    import termios

    mode = termios.tcgetattr(fd)
    mode[3] &= ~getattr(termios, "PENDIN", 0)
    return mode


@posix_only
def test_a_terminal_hands_over_a_key_with_no_enter(pty_pair: Any) -> None:
    import termios

    master, slave = pty_pair
    before = _settings(slave)
    keys = PosixKeys(slave)
    assert not _lflag(slave) & termios.ICANON, "out of its line mode"
    assert _lflag(slave) & termios.ECHO and _lflag(slave) & termios.ISIG, "and nothing else"
    try:
        os.write(master, b"q")
        assert keys.read() == "q"
        os.write(master, "é".encode())
        assert keys.read() == "é", "a character, not a byte of one"
    finally:
        keys.close()
    assert _settings(slave) == before, "and the terminal is put back as it was found"


@posix_only
@pytest.mark.parametrize(
    "sent",
    [
        pytest.param(b"\x04", id="ctrl-d"),
        pytest.param(b"\x01", id="ctrl-a"),
        pytest.param(b"\t", id="tab"),
        pytest.param(b"\x1bOQ", id="f2"),
        pytest.param(b"\x1b[A", id="up"),
        pytest.param(b"\x1b[6~", id="page-down"),
        pytest.param(b"\x1b[1;5C", id="ctrl-right"),
    ],
)
def test_a_key_that_means_nothing_is_no_key_on_a_terminal(pty_pair: Any, sent: bytes) -> None:
    """F2 ends in `Q`, which stopped the run, and an arrow's characters joined the line, which then
    read as a no to a question that takes only a bare Enter. Ctrl-D ended the keys for good."""
    master, slave = pty_pair
    keys = PosixKeys(slave)
    try:
        os.write(master, sent + b"x")
        assert keys.read() == "x"
    finally:
        keys.close()


@posix_only
@pytest.mark.parametrize(("sent", "key"), [(b"\x1b", "x"), (b"\x1bx", "x"), (b"\x1bq", "q")])
def test_esc_on_its_own_leaves_the_next_key_alone(pty_pair: Any, sent: bytes, key: str) -> None:
    master, slave = pty_pair
    keys = PosixKeys(slave)
    try:
        os.write(master, sent)
        if len(sent) == 1:
            time.sleep(ESCAPE_GAP_S * 2)
            os.write(master, key.encode())
        assert keys.read() == key
    finally:
        keys.close()


BEHIND = """
import asyncio, os, sys
from quackd.keys import in_the_background
from quackd.safety import KillSwitch

async def main():
    ks = KillSwitch(asyncio.Event())
    ks.install()
    print(in_the_background(sys.stdin), ks._thread is None, ks.keys_ended.is_set(), flush=True)

pid = os.fork()
if pid == 0:
    os.setpgid(0, 0)  # a group of its own, with the terminal's foreground left behind it
    asyncio.run(main())
    os._exit(0)
os.waitpid(pid, 0)
"""


@posix_only
def test_a_terminal_this_process_is_behind_is_not_read(pty_pair: Any) -> None:
    """A read of it stops the whole process, robot and all, and so does changing its mode. The
    switch reads nothing there, and a wait for a key is told none is coming."""
    import fcntl
    import termios

    _, slave = pty_pair

    def own_the_terminal() -> None:
        fcntl.ioctl(slave, termios.TIOCSCTTY, 0)

    child = subprocess.Popen(
        [sys.executable, "-c", BEHIND],
        stdin=slave,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
        preexec_fn=own_the_terminal,
    )
    try:
        assert child.stdout is not None
        assert child.stdout.readline().strip() == "True True True"
        assert child.wait(timeout=20) == 0
    finally:
        child.kill()


@posix_only
def test_a_key_is_not_read_while_this_process_is_behind_the_terminal(
    pty_pair: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ctrl-Z and `bg` put a run there after the switch had started, where a read stops the whole
    process. It waits, and reads what is there once it is in front again."""
    master, slave = pty_pair
    keys = PosixKeys(slave)
    try:
        os.write(master, b"q")
        monkeypatch.setattr("quackd.keys._behind_the_terminal", lambda _fd: True)
        got: list[str] = []
        reading = threading.Thread(target=lambda: got.append(keys.read()), daemon=True)
        reading.start()
        time.sleep(POLL_S * 5)
        assert not got, "read from behind the terminal"
        monkeypatch.setattr("quackd.keys._behind_the_terminal", lambda _fd: False)
        reading.join(2)
        assert got == ["q"]
    finally:
        keys.close()


@posix_only
def test_closing_a_terminal_ends_a_read_that_is_waiting(pty_pair: Any) -> None:
    _, slave = pty_pair
    keys = PosixKeys(slave)
    got: list[str] = []
    reading = threading.Thread(target=lambda: got.append(keys.read()), daemon=True)
    reading.start()
    keys.close()
    reading.join(2)
    assert not reading.is_alive() and got == [""]
    keys.close()  # and at exit, which runs it again for a run that got there first


CHILD = """
import asyncio, atexit, os, signal, sys
from quackd.safety import KillSwitch

async def main():
    ks = KillSwitch(asyncio.Event())
    ks.install()
    print("ready", ks._keys is not None, flush=True)
    await asyncio.wait_for(ks.pressed.wait(), timeout=20)
    print("pressed", flush=True)
    {finish}

asyncio.run(main())
"""


def _at_a_terminal(finish: str, slave: int) -> subprocess.Popen[str]:
    """A process whose stdin is the pty: a real switch, installed. It does not own the terminal,
    since macOS takes a terminal away from everybody as its owner exits, and then there is nothing
    left to read the settings of."""
    return subprocess.Popen(
        [sys.executable, "-c", textwrap.dedent(CHILD).format(finish=finish)],
        stdin=slave,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


@posix_only
def test_a_real_switch_on_a_real_terminal_fires_on_q_alone(pty_pair: Any) -> None:
    master, slave = pty_pair
    before = _settings(slave)
    finish = (
        "ks.uninstall(); import termios; "
        "print('line mode', bool(termios.tcgetattr(0)[3] & termios.ICANON), flush=True)"
    )
    child = _at_a_terminal(finish, slave)
    try:
        assert child.stdout is not None
        assert child.stdout.readline().strip() == "ready True"
        os.write(master, b"q")
        assert child.stdout.readline().strip() == "pressed"
        assert child.stdout.readline().strip() == "line mode True", "before it exits, too"
        assert child.wait(timeout=20) == 0
    finally:
        child.kill()
    assert _settings(slave) == before, "uninstalling put the terminal back"


@posix_only
def test_a_run_that_never_uninstalls_still_puts_the_terminal_back(pty_pair: Any) -> None:
    """A crash on the way out must not leave somebody's shell with no line editing."""
    master, slave = pty_pair
    before = _settings(slave)
    child = _at_a_terminal("sys.exit(3)", slave)
    try:
        assert child.stdout is not None
        assert child.stdout.readline().strip() == "ready True"
        os.write(master, b"q")
        assert child.stdout.readline().strip() == "pressed"
        assert child.wait(timeout=20) == 3
    finally:
        child.kill()
    assert _settings(slave) == before
