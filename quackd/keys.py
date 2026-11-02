"""One key at a time from a real console, for the kill switch's key thread.

A terminal in its usual mode hands a program its input a line at a time, so `q` stopped nothing
until somebody pressed Enter as well: under every line that says `Ctrl-C or q stops it`, with an
arm moving and a person who has no reason to know about the second key. So at a console the key
thread reads keys as they are pressed, through what is here.

A key thread has to be able to end, too. A blocking read cannot be told to stop, and one left
behind by a switch that had finished went on taking keys from the next, so both of these look
for a key a few dozen times a second and end when they are closed.

Only the keys the switch has a use for come out: what can be typed, Enter and Backspace, and
Ctrl-C where it reads as a key. Nothing else means anything here. An arrow or a function key
above all, which arrives as several characters and can end in `Q`, and Ctrl-D and Ctrl-Z, which
a console reading lines turned into the end of the stream: one stray press then took `q` away
for the rest of the run, with the line that promises it still on the screen.

Nothing here knows what a key means: that is `KillSwitch._watch_keys`. Echoing is here, because
a console that is no longer reading lines may no longer be echoing them either, and a person
answering a question has to see what they typed."""

from __future__ import annotations

import atexit
import codecs
import contextlib
import importlib
import os
import select
import sys
import threading
from typing import Any, Protocol

POLL_S = 0.02
"""How long a key can wait to be seen, and well inside the tenth of a second a verb's own tick
takes to act on the stop it causes. Looking this often costs nothing a laptop or a Jetson can
measure."""
ESCAPE_GAP_S = 0.05
"""How long the rest of an arrow or a function key may take to come in behind its escape. Longer
than a terminal needs and shorter than anybody presses Esc and then another key."""
ENTER = ("\r", "\n")
BACKSPACE = ("\b", "\x7f")
CTRL_C = "\x03"


def _has_a_use(key: str) -> bool:
    return key in ENTER or key in BACKSPACE or key == CTRL_C or key.isprintable()


class ConsoleKeys(Protocol):
    """A console read a key at a time."""

    def read(self) -> str:
        """The next key, waiting for one. Empty when there will be no more, or once closed."""
        ...

    def erase(self) -> None:
        """Take the last key off the screen, for a backspace."""
        ...

    def newline(self) -> None:
        """End the line on the screen, for a key that ends it without an Enter."""
        ...

    def close(self) -> None:
        """Put the console back as it was found, and end a `read` that is waiting."""
        ...


class WindowsKeys:
    """`msvcrt`, which reads the console's keys directly and leaves its mode alone, so Ctrl-C is
    still the signal it always was. It echoes nothing, so this does."""

    def __init__(self, console: Any) -> None:
        self._console = console
        self._closed = threading.Event()

    def read(self) -> str:
        while not self._closed.is_set():
            if not self._console.kbhit():
                self._closed.wait(POLL_S)
                continue
            key = self._console.getwch()
            if key in ("\x00", "\xe0") and self._second_half_waiting():
                self._console.getwch()
                continue
            if _has_a_use(key):
                self._echo(key)
                return key
        return ""

    def _second_half_waiting(self) -> bool:
        """An arrow or a function key reads as `\\x00` or `\\xe0` and then a second character, and
        `kbhit` does not see that one, because the C runtime has put it by for the next read: left
        there it comes out with the next key pressed, as a key, and PageDown's is `Q`. `à` reads
        as the same first half with no second, so a read for one would sit on the next key. The
        runtime has one such place, which is how to tell: pushing a character back fails where
        one is waiting there."""
        try:
            self._console.ungetwch("\x00")
        except OSError:
            return True
        self._console.getwch()
        return False

    def _echo(self, key: str) -> None:
        if key in ENTER:
            self._console.putwch("\r")
            self._console.putwch("\n")
        elif key.isprintable():
            self._console.putwch(key)

    def erase(self) -> None:
        for key in ("\b", " ", "\b"):
            self._console.putwch(key)

    def newline(self) -> None:
        self._echo("\n")

    def close(self) -> None:
        self._closed.set()


class PosixKeys:
    """A terminal out of its line mode (`ICANON`), and nothing else changed: it still echoes, and
    Ctrl-C is still a signal. A control key echoes as nothing rather than `^?` (`ECHOCTL`), so a
    backspace can be drawn here. Put back by `close`, and at exit for a run that never got there.
    A kill that gives Python no say, SIGTERM or Ctrl-\\, leaves it to the shell waiting on it."""

    def __init__(self, fd: int) -> None:
        termios: Any = importlib.import_module("termios")  # no such module to type on Windows
        self._fd = fd
        self._closed = threading.Event()
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="ignore")
        self._ahead = b""
        self._termios = termios
        self._as_found = termios.tcgetattr(fd)
        mode = termios.tcgetattr(fd)
        mode[3] &= ~(termios.ICANON | getattr(termios, "ECHOCTL", 0))
        mode[6][termios.VMIN] = 1
        mode[6][termios.VTIME] = 0
        termios.tcsetattr(fd, termios.TCSANOW, mode)
        atexit.register(self.close)

    def read(self) -> str:
        while not self._closed.is_set():
            data = self._byte(POLL_S)
            if data is None:
                continue
            if not data:  # the terminal went away
                return ""
            if data == b"\x1b":
                self._skip_escape()
                continue
            key = self._decoder.decode(data)  # nothing yet in the middle of a character
            if key and _has_a_use(key):
                return key
        return ""

    def _byte(self, wait_s: float) -> bytes | None:
        """One byte from the terminal, `b""` where it has gone away, None where none came. Not
        while this process is in its background, where a read stops the whole process, robot and
        all, until somebody brings it back."""
        if self._ahead:
            data, self._ahead = self._ahead, b""
            return data
        try:
            ready, _, _ = select.select([self._fd], [], [], wait_s)
            if not ready:
                return None
            if _behind_the_terminal(self._fd):
                self._closed.wait(POLL_S)
                return None
            return os.read(self._fd, 1)
        except (OSError, ValueError):
            return b""

    def _skip_escape(self) -> None:
        """An arrow or a function key is an escape and a run of characters that are no keys: F2
        ends in `Q`, and would stop the run. Esc on its own, or Alt and a key, leaves that key."""
        data = self._byte(ESCAPE_GAP_S)
        if data == b"[":
            while (data := self._byte(ESCAPE_GAP_S)) and not 0x40 <= data[0] <= 0x7E:
                pass
        elif data == b"O":
            self._byte(ESCAPE_GAP_S)
        elif data:
            self._ahead = data

    def erase(self) -> None:
        with contextlib.suppress(OSError):
            os.write(self._fd, b"\b \b")

    def newline(self) -> None:
        with contextlib.suppress(OSError):
            os.write(self._fd, b"\r\n")

    def close(self) -> None:
        if self._closed.is_set():
            return
        self._closed.set()
        atexit.unregister(self.close)
        if _behind_the_terminal(self._fd):  # the attempt would stop the process, and it is not ours
            return
        with contextlib.suppress(OSError, self._termios.error):
            self._termios.tcsetattr(self._fd, self._termios.TCSANOW, self._as_found)


def _behind_the_terminal(fd: int) -> bool:
    if sys.platform == "win32":  # no such thing there
        return False
    try:
        return os.tcgetpgrp(fd) != os.getpgrp()
    except OSError:  # no terminal of this process's at all, so nothing to be behind
        return False


def in_the_background(stream: Any) -> bool:
    """Whether `stream` is a terminal this process is in the background of. A read from it stops
    the whole process, robot and all, and so does changing its mode."""
    try:
        return _behind_the_terminal(stream.fileno())
    except (OSError, ValueError, AttributeError):
        return False


def is_a_console(stream: Any) -> bool:
    """Whether `stream` is a console somebody is at. `isatty` alone is not that on Windows, where
    it says yes to NUL, because NUL is a character device."""
    if stream is None or not stream.isatty():
        return False
    if sys.platform == "win32":
        import ctypes
        import msvcrt

        try:
            handle = msvcrt.get_osfhandle(stream.fileno())
        except (OSError, ValueError):
            return False
        mode = ctypes.c_ulong()
        return bool(ctypes.windll.kernel32.GetConsoleMode(handle, ctypes.byref(mode)))
    return True


def console_keys(stream: Any) -> ConsoleKeys | None:
    """`stream`'s keys one at a time, or None where it is no console to read that way: NUL, which
    says it is a terminal and is at its end at once, and a terminal that will not change mode."""
    try:
        if not is_a_console(stream):
            return None
        if sys.platform == "win32":
            import msvcrt

            return WindowsKeys(msvcrt)
        return PosixKeys(stream.fileno())
    except Exception:
        return None


__all__ = [
    "ConsoleKeys",
    "PosixKeys",
    "WindowsKeys",
    "console_keys",
    "in_the_background",
    "is_a_console",
]
