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
import math
import os
import signal
import subprocess
import sys
import textwrap
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any

import pytest

from quackd import keys as keys_module
from quackd.keys import (
    ESCAPE_GAP_S,
    POLL_S,
    PosixKeys,
    WindowsKeys,
    console_keys,
    is_a_console,
)
from quackd.safety import (
    ASK_POLL_S,
    ENTER_ALONE_S,
    ENTER_QUIET_S,
    TYPED_AHEAD_S,
    KillSwitch,
    _Enter,
)


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


def heard(ks: KillSwitch) -> list[str]:
    """What was typed on each line whose Enter goes to a wait."""
    return [enter.text for enter in ks._enters if enter.answers]


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


async def test_the_stop_goes_before_anything_is_drawn() -> None:
    """Drawing can block, on a terminal somebody has frozen, and the stop must not wait for it."""
    ks = KillSwitch(asyncio.Event())
    ks._loop = asyncio.get_running_loop()
    presses_when_drawn: list[int] = []

    class Drawing(Pressed):
        def newline(self) -> None:
            presses_when_drawn.append(ks.presses)

    ks._keys = Drawing("q")
    await asyncio.to_thread(ks._watch_keys)
    assert presses_when_drawn == [1]


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
    assert ks.presses == 1, "fired with the reader still waiting for its next key"
    gate.set()
    await reader


async def test_the_old_habit_of_enter_after_q_answers_nothing() -> None:
    """Somebody who has always pressed Enter after `q`. The press ends the run, the teardown puts
    its question in a moment, and the hand still finishing that habit must not answer it."""
    ks, _, _ = await press("q", "\r")
    assert not ks.entered.is_set()
    assert heard(ks) == []


async def test_a_slow_habit_answers_nothing_either() -> None:
    """The question is up within a moment of the press. An Enter past the half second a question
    gives anybody, and still behind the `q`, opened the jaws on what was in them."""
    ks, _, _ = await press("q", TYPED_AHEAD_S + 0.05, "\r")
    assert not ks.entered.is_set()
    assert heard(ks) == []


async def test_a_later_enter_is_a_bare_one(monkeypatch: pytest.MonkeyPatch) -> None:
    """`q` is no part of its line at a console: the question that comes a minute later takes this
    Enter as the bare one it is, rather than a line with `q` typed on it, which is a no."""
    monkeypatch.setattr("quackd.safety.ENTER_BEHIND_Q_S", 0.05)
    ks, _, _ = await press("q", 0.1, "\r")
    assert ks.entered.is_set()
    assert heard(ks) == [""]


async def test_an_enter_held_down_behind_a_q_answers_nothing_when_the_habit_is_over(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Enters passed by behind a `q` were not counted as Enters at all, so the first past the
    window looked as though it had come out of a long quiet, and it opened the jaws."""
    monkeypatch.setattr("quackd.safety.ENTER_BEHIND_Q_S", 0.1)
    monkeypatch.setattr("quackd.safety.ENTER_ALONE_S", 0.5)  # room for a loaded runner's sleeps
    repeats = [key for _ in range(20) for key in ("\r", 0.02)]
    ks, _, _ = await press("q", *repeats)
    assert len(heard(ks)) > 5, "the window has passed, and they go to a wait"
    await asyncio.sleep(0.6)
    assert ks._line_since(0.0) is None, "and none of them stands alone"


async def test_an_enter_passed_by_behind_a_q_is_still_the_enter_before_the_next(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The second of two quick Enters, the first inside the window and the second just past it, is
    no answer. Were the first never counted, the second would seem to come out of a long quiet."""
    monkeypatch.setattr("quackd.safety.ENTER_BEHIND_Q_S", 0.05)
    ks, _, _ = await press("q", "\r", 0.1, "\r")
    first, second = ks._enters
    assert not first.answers and second.answers, "the first passed by, the second to a wait"
    assert second.quiet == second.at - first.at, "and it is counted from the first"


async def _asked_and_pressed(
    *keys: str | float, key_first: bool = False
) -> tuple[bool, KillSwitch]:
    """A deliberate question put a moment after `keys` begin, or a moment after the first of them
    has been read, however long a loaded runner took to start reading."""
    ks = KillSwitch(asyncio.Event())
    ks._loop = asyncio.get_running_loop()
    ks._keys = Pressed(*keys)
    reading = asyncio.ensure_future(asyncio.to_thread(ks._watch_keys))
    while key_first and ks._last_key == -math.inf:  # noqa: ASYNC110
        await asyncio.sleep(0.001)
    await asyncio.sleep(0.05)
    answer = await ks.wait_for_enter(timeout_s=3.0, until_abort=False, deliberate=True)
    await reading
    return answer, ks


def _enters(*gaps: float) -> KillSwitch:
    """A switch that has read one bare Enter after each gap, at no time in particular."""
    ks = KillSwitch(asyncio.Event())
    at = time.monotonic() - 1000.0
    for gap in gaps:
        at += gap
        ks._enters.append(_Enter(at, gap, "", True))
    return ks


def test_a_bare_enter_has_to_come_after_quiet_and_stand_alone() -> None:
    quiet = ENTER_QUIET_S * 1.1
    alone = ENTER_ALONE_S * 1.1
    close = ENTER_ALONE_S * 0.9
    assert _enters(quiet)._line_since(-math.inf) == "", "one press, and nothing since"
    assert _enters(quiet, alone)._line_since(-math.inf) == ""
    assert _enters(quiet, close)._line_since(-math.inf) is None, "pressed twice"
    assert _enters(close)._line_since(-math.inf) is None, "behind another"
    assert _enters(*[close] * 20)._line_since(-math.inf) is None, "a key held down"
    assert _enters(100.0, 0.03, 0.03, quiet)._line_since(-math.inf) == "", (
        "let go, and pressed again"
    )


@pytest.mark.parametrize("repeat", [0.6, 1.0, 1.8])
def test_a_key_set_to_repeat_slowly_is_still_a_key_held_down(repeat: float) -> None:
    """Each of these stands alone for longer than `ENTER_ALONE_S`, so all that gives it away is the
    Enter before it, and the question that drops an arm was answered by the second of them."""
    assert repeat < ENTER_QUIET_S
    assert _enters(*[repeat] * 20)._line_since(-math.inf) is None


def test_the_slowest_repeat_windows_has_is_a_key_held_down() -> None:
    """SPI_SETKEYBOARDSPEED's slowest is about 2.5 repetitions a second."""
    assert ENTER_QUIET_S > 1 / 2.5


def test_an_enter_is_passed_by_for_good_only_when_nothing_can_make_it_an_answer() -> None:
    """What a person is told of. One still standing alone may yet be the answer, and telling them
    it was not would have them press again and spoil it."""
    quiet = ENTER_QUIET_S * 1.1
    close = ENTER_ALONE_S * 0.9
    assert not _enters(quiet)._passed_by(-math.inf), "still waiting to be sure of"
    assert _enters(quiet, close)._passed_by(-math.inf), "another close behind it"
    assert not _enters(quiet, ENTER_QUIET_S * 1.1)._passed_by(-math.inf), "two, each after quiet"
    assert _enters(close)._passed_by(-math.inf), "too close behind another"
    assert not _enters()._passed_by(-math.inf), "no Enter at all"
    typed = KillSwitch(asyncio.Event())
    typed._enters.append(_Enter(time.monotonic(), 0.01, "n", True))
    assert not typed._passed_by(-math.inf), "a line with something on it is a no, and answers"
    habit = KillSwitch(asyncio.Event())
    habit._enters.append(_Enter(time.monotonic(), 0.01, "", False))
    assert not habit._passed_by(-math.inf), "one behind a `q` answers nothing and was asked nothing"


def test_an_enter_as_a_question_goes_up_is_passed_by_as_too_soon() -> None:
    asked = time.monotonic()
    ks = KillSwitch(asyncio.Event())
    ks._enters.append(_Enter(asked + TYPED_AHEAD_S * 0.5, ENTER_QUIET_S * 2, "", True))
    assert ks._passed_by(asked)
    before = KillSwitch(asyncio.Event())
    before._enters.append(_Enter(asked - 1.0, ENTER_QUIET_S * 2, "", True))
    assert not before._passed_by(asked), "from before the question, so said to no question"


def test_a_no_is_a_no_as_soon_as_it_is_read() -> None:
    """`n`, Enter and Enter again a moment later stood alone neither of them, so the no was lost,
    and the next bare Enter was a yes to what they had just refused."""
    ks = KillSwitch(asyncio.Event())
    now = time.monotonic()
    ks._enters.append(_Enter(now - 0.1, 100.0, "n", True))
    ks._enters.append(_Enter(now, 0.1, "", True))
    assert ks._line_since(-math.inf) == "n"


async def test_a_space_is_something_typed() -> None:
    ks, _, _ = await press(" ", "\r")
    assert heard(ks) == [" "], "only a bare Enter is one"


async def test_a_stray_key_from_before_the_question_is_no_answer_to_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Press `x` mid-run, and the Enter that opens the jaws ten minutes later read as a line with
    something typed on it: a no, with a hand on the pencil and the arm folding 3 s on."""
    monkeypatch.setattr("quackd.safety.TYPED_AHEAD_S", 0.02)
    answer, ks = await _asked_and_pressed("x", 0.3, "\r", ENTER_ALONE_S * 3, key_first=True)
    assert answer is True and ks.declined is False


async def test_what_is_typed_after_the_question_is_still_a_no(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("quackd.safety.TYPED_AHEAD_S", 0.02)
    answer, ks = await _asked_and_pressed(0.3, "n", "\r", 0.5)
    assert answer is False and ks.declined is True


async def test_a_key_typed_just_before_the_question_is_still_a_no(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Too close to it to be a stray press from long ago, and a no is the safe reading."""
    monkeypatch.setattr("quackd.safety.TYPED_AHEAD_S", 0.8)
    answer, ks = await _asked_and_pressed("n", 1.2, "\r", 0.5, key_first=True)
    assert answer is False and ks.declined is True


async def test_a_line_with_something_on_it_is_still_that_line() -> None:
    ks, _, _ = await press("n", "o", "\r")
    assert heard(ks) == ["no"]
    assert ks.presses == 0


async def test_a_backspace_takes_a_key_off_the_line() -> None:
    """A console read a key at a time does none of this for anybody: a deliberate question takes
    only a bare Enter, and `n`, backspace, Enter is one."""
    ks, _, console = await press("n", "\b", "\r")
    assert heard(ks) == [""]
    assert console.erased == 1

    ks, _, console = await press("n", "\x7f", "\x7f", "\r")
    assert heard(ks) == [""]
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
        assert ks.unanswered is not None and "kill switch" in ks.unanswered, "the record says so"
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
    assert ks.unanswered is not None and "no key" in ks.unanswered, "the record says so"


def test_the_saved_terminal_says_why_a_question_went_unanswered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """It said `(Enter)` of a `[y/N]` a press had ended, which nobody pressed."""
    from quackd import cli

    class Ended:
        unanswered: str | None = "(the kill switch ended the question: no)"

        def ask(self, _prompt: str) -> str:
            return ""

    notes: list[str] = []
    monkeypatch.setattr(cli, "_SWITCH", Ended())
    monkeypatch.setattr(cli.ui, "note", notes.append)
    assert cli._ask("run it?") is False
    assert notes == ["run it? [y/N]: (the kill switch ended the question: no)"]

    Ended.unanswered = None
    notes.clear()
    assert cli._ask("run it?") is False
    assert notes == ["run it? [y/N]: (Enter)"], "a bare Enter is still one"


async def test_a_question_with_no_key_thread_left_at_a_console_is_a_no(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`input()` on a console read a key at a time takes a `y` nobody can see."""

    def typed(_prompt: str) -> str:
        raise AssertionError("it asked `input()`")

    monkeypatch.setattr("builtins.input", typed)
    ks = KillSwitch(asyncio.Event())
    ks._keys = Pressed()
    done = _reader(ks)
    done.set()
    assert ks._thread is not None
    ks._thread.join(2)
    assert ks.ask("") == ""
    assert ks.unanswered is not None and "no key" in ks.unanswered


async def test_a_question_still_takes_its_line() -> None:
    ks = KillSwitch(asyncio.Event())
    ks._loop = asyncio.get_running_loop()
    done = _reader(ks)
    try:
        asking = asyncio.create_task(asyncio.to_thread(ks.ask, ""))
        await asyncio.sleep(ASK_POLL_S)
        ks._lines.put("y")
        assert await asyncio.wait_for(asking, timeout=5) == "y"
        assert ks.unanswered is None
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
    assert not _lflag(slave) & termios.ECHO, "and drawn by this"
    assert _lflag(slave) & termios.ISIG, "and Ctrl-C is still a signal"
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
        pytest.param(b"\x13", id="ctrl-s"),
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
    print(repr(ks.ask("")), flush=True)
    from quackd import cli
    print(cli._can_prompt(), flush=True)

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
        assert child.stdout.readline().strip() == "''", "a question there is a no, unread"
        assert child.stdout.readline().strip() == "False", "and quackd has nobody there to ask"
        assert child.wait(timeout=20) == 0
    finally:
        child.kill()


@posix_only
def test_only_what_has_a_use_reaches_the_screen() -> None:
    """Left to the terminal, an arrow key moved the cursor, and the lines after it could be written
    over a question still standing."""
    import pty

    master, slave = pty.openpty()
    keys = PosixKeys(slave)
    try:
        os.write(master, b"\x1b[Ax\r")
        assert [keys.read(), keys.read()] == ["x", "\r"]
        keys.erase()
        drawn = b""
        wanted = b"x\r\n\b \b"
        while len(drawn) < len(wanted):  # a terminal hands it over in as many pieces as it likes
            drawn += os.read(master, 64)
        assert drawn == wanted
    finally:
        keys.close()
        os.close(slave)
        os.close(master)


@posix_only
def test_a_link_that_sends_two_characters_for_enter_is_seen_to(pty_pair: Any) -> None:
    """The terminal turned both into `\\n`, so each Enter read as two a moment apart, neither of
    which stood alone, and no question could ever be answered over such a link."""
    master, slave = pty_pair
    keys = PosixKeys(slave)
    try:
        os.write(master, b"\r\n")
        assert [keys.read(), keys.read()] == ["\r", "\n"]
    finally:
        keys.close()


@posix_only
def test_ctrl_s_freezes_nothing(pty_pair: Any) -> None:
    """It froze a run at its next line of output, with `q` frozen behind it."""
    import termios

    _, slave = pty_pair
    assert termios.tcgetattr(slave)[0] & termios.IXON, "the terminal had it on to begin with"
    keys = PosixKeys(slave)
    try:
        assert not termios.tcgetattr(slave)[0] & termios.IXON
    finally:
        keys.close()
    assert termios.tcgetattr(slave)[0] & termios.IXON


@posix_only
def test_ctrl_z_is_no_key_either(pty_pair: Any) -> None:
    """It stopped the whole process, with an arm on its last goal and nothing reading `q`. Put back
    as it was with the rest, which the test above holds it to."""
    import termios

    _, slave = pty_pair
    before = termios.tcgetattr(slave)[6][termios.VSUSP]
    keys = PosixKeys(slave)
    try:
        off = bytes([os.fpathconf(slave, "PC_VDISABLE")])
        assert before != off, "the terminal had it on to begin with"
        assert termios.tcgetattr(slave)[6][termios.VSUSP] == off
    finally:
        keys.close()
    assert termios.tcgetattr(slave)[6][termios.VSUSP] == before


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
    {before_ready}
    print("ready", ks._keys is not None, flush=True)
    await asyncio.wait_for(ks.pressed.wait(), timeout=20)
    print("pressed", flush=True)
    {finish}

asyncio.run(main())
"""


FORK_A_WORKER = """__import__("warnings").simplefilter("ignore")
    worker = os.fork()
    if worker == 0:
        __import__("time").sleep(60)
        os._exit(0)
    print("worker", worker, flush=True)"""


def _at_a_terminal(finish: str, slave: int, *, before_ready: str = "pass") -> subprocess.Popen[str]:
    """A process whose stdin is the pty: a real switch, installed. It does not own the terminal,
    since macOS takes a terminal away from everybody as its owner exits, and then there is nothing
    left to read the settings of."""
    return subprocess.Popen(
        [
            sys.executable,
            "-c",
            textwrap.dedent(CHILD).format(finish=finish, before_ready=before_ready),
        ],
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
def test_a_run_killed_with_no_exit_still_puts_the_terminal_back(pty_pair: Any) -> None:
    """Python runs nothing at exit on a signal it was left to die of, and under a wrapper such as
    `uv run` no shell puts it back either: the next thing typed goes unseen."""
    import signal

    _, slave = pty_pair
    before = _settings(slave)
    child = _at_a_terminal("ks.uninstall()", slave)
    try:
        assert child.stdout is not None
        assert child.stdout.readline().strip() == "ready True"
        assert _settings(slave) != before, "it had the terminal out of its line mode"
        child.terminate()
        assert child.wait(timeout=20) == -signal.SIGTERM, "and it still died of the signal"
    finally:
        child.kill()
    assert _settings(slave) == before


def _settled(slave: int, wanted: list[Any]) -> list[Any]:
    """The terminal's settings, once they are `wanted` or it is clear they will not be."""
    end = time.monotonic() + 20
    while (found := _settings(slave)) != wanted and time.monotonic() < end:
        time.sleep(0.02)
    return found


@posix_only
def test_a_run_killed_outright_still_puts_the_terminal_back(pty_pair: Any) -> None:
    """A SIGKILL runs nothing in the process, and under a wrapper such as `uv run` no shell puts it
    back either: what is typed next goes unseen until somebody types `stty sane`, unseen."""
    _, slave = pty_pair
    before = _settings(slave)
    child = _at_a_terminal("ks.uninstall()", slave)
    try:
        assert child.stdout is not None
        assert child.stdout.readline().strip() == "ready True"
        assert _settings(slave) != before, "it had the terminal out of its line mode"
        child.kill()
        child.wait(timeout=20)
    finally:
        child.kill()
    assert _settled(slave, before) == before


@posix_only
def test_a_guard_leaves_alone_a_terminal_somebody_else_has_since_changed(pty_pair: Any) -> None:
    """A program in the shell's foreground has the terminal the way it wants it by then, and going
    back to the mode this run found would break it under them."""
    import termios

    _, slave = pty_pair
    child = _at_a_terminal("ks.uninstall()", slave)
    try:
        assert child.stdout is not None
        assert child.stdout.readline().strip() == "ready True"
        theirs = termios.tcgetattr(slave)
        theirs[3] &= ~termios.ISIG
        termios.tcsetattr(slave, termios.TCSANOW, theirs)
        theirs = _settings(slave)
        child.kill()
        child.wait(timeout=20)
    finally:
        child.kill()
    time.sleep(1.0)  # what a guard takes to act on the pipe closing, were it going to
    assert _settings(slave) == theirs


@posix_only
def test_the_guard_is_up_before_the_terminal_leaves_line_mode(
    pty_pair: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A kill in a run's first moments under a wrapper left the terminal broken 7 times in 8: the
    shell got there while the guard was still starting. Now such a kill finds a terminal nothing
    has changed, or a guard already waiting."""
    import termios

    _, slave = pty_pair
    order: list[str] = []
    up, setting = keys_module._Guard._up, termios.tcsetattr

    def was_up(self: Any) -> bool:
        order.append("up")
        return up(self)

    def was_set(*args: Any) -> None:
        order.append("mode")
        setting(*args)

    monkeypatch.setattr(keys_module._Guard, "_up", was_up)
    monkeypatch.setattr(termios, "tcsetattr", was_set)
    keys = PosixKeys(slave)
    try:
        assert order[:2] == ["up", "mode"]
    finally:
        keys.close()


def _an_interpreter_that_takes(seconds: float, tmp_path: Path) -> str:
    slow = tmp_path / "slow-python"
    slow.write_text(f'#!/bin/sh\nsleep {seconds}\nexec "{sys.executable}" "$@"\n')
    slow.chmod(0o755)
    return str(slow)


@posix_only
def test_the_terminal_waits_for_a_guard_that_takes_its_time(
    pty_pair: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import termios

    _, slave = pty_pair
    setting = termios.tcsetattr
    started = time.monotonic()
    set_after: list[float] = []

    def was_set(*args: Any) -> None:
        set_after.append(time.monotonic() - started)
        setting(*args)

    monkeypatch.setattr(termios, "tcsetattr", was_set)
    monkeypatch.setattr(sys, "executable", _an_interpreter_that_takes(0.4, tmp_path))
    keys = PosixKeys(slave)
    try:
        assert set_after[0] >= 0.4, "the mode changed with no guard up yet"
    finally:
        keys.close()


@posix_only
def test_a_machine_too_busy_to_start_a_guard_in_time_gets_it_late(
    pty_pair: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A run does not wait on it for long, and the guard is still worth having when it comes."""
    _, slave = pty_pair
    before = _settings(slave)
    monkeypatch.setattr(keys_module, "GUARD_UP_S", 0.05)
    monkeypatch.setattr(sys, "executable", _an_interpreter_that_takes(2.0, tmp_path))
    started = time.monotonic()
    keys = PosixKeys(slave)
    try:
        assert time.monotonic() - started < 1.5
        assert keys._guard is not None
        assert _settings(slave) != before
    finally:
        keys.close()


@posix_only
def test_a_run_killed_the_moment_it_has_the_terminal_still_puts_it_back(pty_pair: Any) -> None:
    _, slave = pty_pair
    before = _settings(slave)
    child = _at_a_terminal("ks.uninstall()", slave)
    try:
        assert child.stdout is not None
        assert child.stdout.readline().strip() == "ready True"
        child.kill()
        child.wait(timeout=20)
    finally:
        child.kill()
    assert _settled(slave, before) == before


@posix_only
def test_a_guard_that_ends_without_a_word_is_no_guard(
    pty_pair: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An odd interpreter, say. The keys work, and a close has nothing to stand down."""
    _, slave = pty_pair
    before = _settings(slave)
    monkeypatch.setattr(sys, "executable", "/bin/false")
    keys = PosixKeys(slave)
    try:
        assert keys._guard is None
        assert _settings(slave) != before
    finally:
        keys.close()
    assert _settings(slave) == before


@posix_only
def test_a_mode_that_will_not_set_stands_the_guard_down_with_the_terminal_as_it_was(
    pty_pair: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    import termios

    _, slave = pty_pair
    before = _settings(slave)
    stood_down: list[bool] = []
    stand_down = keys_module._Guard.stand_down

    def counted(self: Any) -> None:
        stood_down.append(True)
        stand_down(self)

    def refuses(*_args: Any) -> None:
        raise termios.error(25, "Inappropriate ioctl for device")

    monkeypatch.setattr(keys_module._Guard, "stand_down", counted)
    monkeypatch.setattr(termios, "tcsetattr", refuses)
    with pytest.raises(termios.error):
        PosixKeys(slave)
    assert stood_down == [True], "a guard was left behind waiting on a pipe nobody will close"
    assert _settings(slave) == before


def _guard_that_lost(slave: int, *, watch_s: float | None = None) -> Any:
    """A guard that has just woken with `theirs` on the terminal, a shell's by then, and what it
    would put back and what it is looking for. Started from here, so that it is the terminal's
    next mode and not the race itself that the test decides."""
    import termios

    as_found = termios.tcgetattr(slave)
    left = termios.tcgetattr(slave)
    left[3] &= ~(termios.ICANON | termios.ECHO)
    left[6][termios.VMIN] = 1
    left[6][termios.VTIME] = 0
    theirs = [*left[:6], list(left[6])]
    theirs[0] &= ~termios.ICRNL
    guard = keys_module._Guard.start(slave, as_found, left)
    assert guard is not None
    termios.tcsetattr(slave, termios.TCSANOW, theirs)
    os.close(guard._word)
    keys_module._WORDS.discard(guard._word)
    return guard, as_found, left, theirs


@posix_only
def test_a_guard_that_comes_second_puts_the_terminal_back_under_the_next_command(
    pty_pair: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On a machine with no CPU to spare a wrapper and a shell could get there first. Bash took the
    mode this process left for its own, every line typed after it went unseen, and it stayed that
    way, because a guard that found readline's mode there left. Bash puts the mode it took back to
    run the next command, and that is the moment: when the command ends bash takes this instead."""
    import termios

    master, slave = pty_pair
    monkeypatch.setattr(keys_module, "GUARD_LOOKS_S", 0.05)
    guard, as_found, left, theirs = _guard_that_lost(slave)
    try:
        time.sleep(0.4)  # past the close look it wakes with: from here it wants a key
        assert termios.tcgetattr(slave) == theirs, "it reset a mode that was not its own"

        os.write(master, b"l")
        termios.tcsetattr(slave, termios.TCSANOW, left)  # readline hands back for the command
        assert guard._process.wait(10) == 0
        ignored = getattr(termios, "PENDIN", 0)
        assert _settings(slave) == [*as_found[:3], as_found[3] & ~ignored, *as_found[4:]]
    finally:
        guard._process.kill()


@posix_only
def test_a_guard_that_comes_second_catches_a_command_that_lasts_no_time(
    pty_pair: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`ls` holds that mode for a couple of milliseconds, so it looks closely from the key on, and
    gets another go at the next command if it misses."""
    import termios

    master, slave = pty_pair
    monkeypatch.setattr(keys_module, "GUARD_LOOKS_S", 0.05)
    guard, _, left, theirs = _guard_that_lost(slave)
    try:
        time.sleep(0.4)  # past the close look it wakes with
        end = time.monotonic() + 15
        while guard._process.poll() is None and time.monotonic() < end:
            os.write(master, b"l")
            termios.tcsetattr(slave, termios.TCSANOW, left)
            time.sleep(0.03)
            if guard._process.poll() is None:
                termios.tcsetattr(slave, termios.TCSANOW, theirs)
                time.sleep(0.15)  # past the look that key bought, for the next try to need a key
        assert guard._process.poll() == 0, "it never caught the mode it was looking for"
    finally:
        guard._process.kill()


@posix_only
def test_a_guard_that_comes_second_leaves_a_terminal_somebody_has_put_right(
    pty_pair: Any,
) -> None:
    """`stty sane`, typed unseen, or a shell that had it right all along: nothing left to do."""
    import termios

    master, slave = pty_pair
    guard, as_found, _, _ = _guard_that_lost(slave)
    try:
        termios.tcsetattr(slave, termios.TCSANOW, as_found)
        os.write(master, b"\n")
        assert guard._process.wait(10) == 0
    finally:
        guard._process.kill()


@posix_only
def test_a_guard_that_comes_second_gives_up_and_leaves_the_terminal_as_it_is(
    pty_pair: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    import termios

    _, slave = pty_pair
    monkeypatch.setattr(keys_module, "GUARD_WATCHES_S", 0.2)
    guard, _, _, theirs = _guard_that_lost(slave)
    try:
        assert guard._process.wait(10) == 0
        assert termios.tcgetattr(slave) == theirs
    finally:
        guard._process.kill()


@posix_only
def test_a_guard_that_comes_second_catches_a_command_it_heard_no_key_for(
    pty_pair: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A shell is waiting for the same key, and has often read it before the guard has seen it."""
    import termios

    _, slave = pty_pair
    monkeypatch.setattr(keys_module, "GUARD_LOOKS_S", 0.01)
    guard, _, left, _ = _guard_that_lost(slave)
    try:
        time.sleep(0.2)
        termios.tcsetattr(slave, termios.TCSANOW, left)
        assert guard._process.wait(10) == 0
    finally:
        guard._process.kill()


@posix_only
def test_a_key_somebody_else_has_read_is_no_terminal_gone(
    pty_pair: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """It wakes on a key, and a shell has usually read that key by the time it looks: the terminal
    was readable and has nothing to read, which is what one that has hung up looks like on macOS."""
    import termios

    master, slave = pty_pair
    monkeypatch.setattr(keys_module, "GUARD_LOOKS_S", 0.01)
    guard, _, left, _ = _guard_that_lost(slave)
    try:
        time.sleep(0.2)
        for _ in range(30):
            os.write(master, b"k")
            os.read(slave, 1)
            time.sleep(0.02)
        assert guard._process.poll() is None, "it took a key that was read for a window that closed"
        os.write(master, b"k")
        os.read(slave, 1)
        termios.tcsetattr(slave, termios.TCSANOW, left)
        assert guard._process.wait(10) == 0
    finally:
        guard._process.kill()


@posix_only
def test_a_guard_that_comes_second_goes_with_its_terminal() -> None:
    """A window that closes: no process may outlive it by ten minutes for nothing."""
    import pty

    master, slave = pty.openpty()
    guard, _, _, _ = _guard_that_lost(slave)
    try:
        os.close(slave)
        os.close(master)
        assert guard._process.wait(10) == 0
    finally:
        guard._process.kill()


@posix_only
def test_a_child_forked_with_no_exec_does_not_keep_the_guard_asleep(pty_pair: Any) -> None:
    """A policy library's worker, say: it held the pipe, so the kernel had nothing to close at a
    SIGKILL until the worker ended as well, which an orphan need not do."""
    _, slave = pty_pair
    before = _settings(slave)
    child = _at_a_terminal("ks.uninstall()", slave, before_ready=FORK_A_WORKER)
    worker = 0
    try:
        assert child.stdout is not None
        worker = int(child.stdout.readline().split()[1])
        assert child.stdout.readline().strip() == "ready True"
        child.kill()
        child.wait(timeout=20)
        assert _settled(slave, before) == before
    finally:
        child.kill()
        if worker:
            with contextlib.suppress(OSError):
                os.kill(worker, signal.SIGKILL)


@posix_only
def test_a_run_that_closes_stands_its_guard_down(pty_pair: Any) -> None:
    """It says so down the pipe and is gone, and what the mode does after that is not its affair."""
    import termios

    _, slave = pty_pair
    before = _settings(slave)
    keys = PosixKeys(slave)
    guard = keys._guard
    assert guard is not None
    keys.close()
    assert guard._process.poll() is not None, "a guard outlived the run it was guarding"
    assert _settings(slave) == before
    keys.close()  # and again, from the signal or `atexit` that gets there second
    changed = termios.tcgetattr(slave)
    changed[3] &= ~termios.ISIG
    termios.tcsetattr(slave, termios.TCSANOW, changed)
    time.sleep(0.2)
    assert _settings(slave) != before, "a guard stood down put the terminal back all the same"


@posix_only
@pytest.mark.parametrize("executable", ["", None])
def test_a_guard_that_will_not_start_costs_nothing_but_the_guard(
    pty_pair: Any, monkeypatch: pytest.MonkeyPatch, executable: str | None
) -> None:
    """Python leaves `sys.executable` empty or None where it cannot tell, an embedding host's above
    all. Raised here it was swallowed as no console at all, with the terminal already out of line
    mode and nothing left to put it back."""
    _, slave = pty_pair
    before = _settings(slave)
    monkeypatch.setattr(sys, "executable", executable)
    keys = PosixKeys(slave)
    assert keys._guard is None
    keys.close()
    assert _settings(slave) == before


@posix_only
def test_anything_that_fails_out_of_line_mode_puts_the_terminal_back(
    pty_pair: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """It is taken for no console at all, and the keys are read a line at a time: with the terminal
    left out of line mode nothing typed showed, for good."""

    def fails(_fd: int) -> None:
        raise OSError("no descriptors left")

    _, slave = pty_pair
    before = _settings(slave)
    monkeypatch.setattr("quackd.keys._for_drawing", fails)
    with pytest.raises(OSError, match="no descriptors"):
        PosixKeys(slave)
    assert _settings(slave) == before


@posix_only
def test_a_process_with_no_descriptors_left_goes_on_without_a_guard(
    pty_pair: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_pipe() -> tuple[int, int]:
        raise OSError("Too many open files")

    _, slave = pty_pair
    before = _settings(slave)
    monkeypatch.setattr(os, "pipe", no_pipe)
    keys = PosixKeys(slave)
    assert keys._guard is None
    keys.close()
    assert _settings(slave) == before


@posix_only
def test_a_terminal_put_back_in_lines_under_a_run_is_taken_out_again(pty_pair: Any) -> None:
    """A SIGSTOP from outside and an `fg` leave the terminal as the shell wants it, reading lines:
    `q` then did nothing until somebody pressed Enter, under every line that promises it does."""
    import termios

    master, slave = pty_pair
    before = termios.tcgetattr(slave)
    keys = PosixKeys(slave)
    read: list[str] = []
    reading = threading.Thread(target=lambda: read.append(keys.read()), daemon=True)
    reading.start()
    try:
        termios.tcsetattr(slave, termios.TCSANOW, before)  # as the shell leaves it behind an `fg`
        end = time.monotonic() + 20
        while _lflag(slave) & termios.ICANON and time.monotonic() < end:
            time.sleep(0.01)
        assert not _lflag(slave) & termios.ICANON, "still reading lines"
        assert not _lflag(slave) & termios.ECHO
        os.write(master, b"q")
        reading.join(20)
        assert read == ["q"], "the key still took an Enter"
    finally:
        keys.close()
        reading.join(5)


@posix_only
def test_the_mode_is_not_taken_back_from_the_background_or_after_a_close(
    pty_pair: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Changing a terminal's mode from its background stops the whole process, robot and all. And
    a close has put the terminal back for good: one last look must not undo that."""
    import termios

    _, slave = pty_pair
    before = termios.tcgetattr(slave)
    keys = PosixKeys(slave)
    termios.tcsetattr(slave, termios.TCSANOW, before)
    monkeypatch.setattr("quackd.keys._behind_the_terminal", lambda _fd: True)
    keys._keep_the_mode()
    assert _lflag(slave) & termios.ICANON, "it changed the mode from behind the terminal"
    monkeypatch.undo()
    keys._keep_the_mode()
    assert not _lflag(slave) & termios.ICANON
    keys.close()
    assert _lflag(slave) & termios.ICANON
    keys._keep_the_mode()
    assert _lflag(slave) & termios.ICANON, "the look after a close took the terminal back"


@posix_only
def test_a_terminal_opened_only_to_read_still_shows_what_is_typed() -> None:
    """`< /dev/tty` opens it that way. Every key still worked, and nothing typed at a question
    reached the screen, since this drew on the same descriptor it read."""
    import pty

    master, slave = pty.openpty()
    only_reads = os.open(os.ttyname(slave), os.O_RDONLY | os.O_NOCTTY)
    keys = PosixKeys(only_reads)
    try:
        os.write(master, b"x\r")
        assert [keys.read(), keys.read()] == ["x", "\r"]
        drawn = b""
        wanted = b"x\r\n"
        while len(drawn) < len(wanted):
            drawn += os.read(master, 64)
        assert drawn == wanted
    finally:
        keys.close()
        os.close(only_reads)
        os.close(slave)
        os.close(master)


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
