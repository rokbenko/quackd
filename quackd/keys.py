"""One key at a time from a real console, for the kill switch's key thread.

A terminal in its usual mode hands a program its input a line at a time, so `q` stopped nothing
until somebody pressed Enter as well: under every line that says `Ctrl-C or q stops it`, with an
arm moving and a person who has no reason to know about the second key. So at a console the key
thread reads keys as they are pressed, through what is here. A pipe or a file has no keys to
wait for and goes on being read as it always was, which is why this answers None for them.

A key thread has to be able to end, too. A blocking read cannot be told to stop, and one left
behind by a switch that had finished went on taking keys from the next, so both of these look
for a key a few dozen times a second and end when they are closed.

Nothing here knows what a key means: that is `KillSwitch._watch_keys`. Echoing is here, because
a console that is no longer reading lines may no longer be echoing them either, and a person
answering a question has to see what they typed."""

from __future__ import annotations

import atexit
import codecs
import contextlib
import importlib
import os
import sys
import threading
from typing import Any, Protocol

POLL_S = 0.02
"""How long a key can wait to be seen, and well inside the tenth of a second a verb's own tick
takes to act on the stop it causes. Looking this often costs nothing a laptop or a Jetson can
measure."""

END_OF_INPUT = {"win32": "\x1a"}
"""The key that says there is no more, Ctrl-Z on Windows and Ctrl-D everywhere else. A console
reading lines turned it into the end of the stream itself."""


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
            if key in ("\x00", "\xe0"):
                # An arrow or a function key comes as two, and is no character. `à` reads as the
                # first of them and has no second, which is why this looks before it reads: a
                # read here would sit on the next key, and the next key may be `q`.
                if self._console.kbhit():
                    self._console.getwch()
                continue
            if key == END_OF_INPUT["win32"]:
                return ""
            self._echo(key)
            return key
        return ""

    def _echo(self, key: str) -> None:
        if key in ("\r", "\n"):
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

    Ctrl-D is an ordinary key once lines are off, so it is read as the end of the stream here."""

    def __init__(self, fd: int) -> None:
        termios: Any = importlib.import_module("termios")  # no such module to type on Windows
        self._fd = fd
        self._closed = threading.Event()
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="ignore")
        self._termios = termios
        self._as_found = termios.tcgetattr(fd)
        mode = termios.tcgetattr(fd)
        mode[3] &= ~(termios.ICANON | getattr(termios, "ECHOCTL", 0))
        mode[6][termios.VMIN] = 1
        mode[6][termios.VTIME] = 0
        termios.tcsetattr(fd, termios.TCSANOW, mode)
        atexit.register(self.close)

    def read(self) -> str:
        import select

        while not self._closed.is_set():
            try:
                ready, _, _ = select.select([self._fd], [], [], POLL_S)
                if not ready:
                    continue
                data = os.read(self._fd, 1)
            except (OSError, ValueError):  # the terminal went away
                return ""
            if not data:
                return ""
            key = self._decoder.decode(data)
            if key == "\x04":
                return ""
            if key:  # nothing yet in the middle of a character
                return key
        return ""

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
        with contextlib.suppress(OSError, self._termios.error):
            self._termios.tcsetattr(self._fd, self._termios.TCSANOW, self._as_found)


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
    """`stream`'s keys one at a time, or None where it is to be read as it always was: anything
    that is no console, and a terminal this process is in the background of, which it must not
    change the mode of (the attempt stops the whole process, robot and all)."""
    try:
        if not is_a_console(stream):
            return None
        if sys.platform == "win32":
            import msvcrt

            return WindowsKeys(msvcrt)
        fd = stream.fileno()
        if os.tcgetpgrp(fd) != os.getpgrp():
            return None
        return PosixKeys(fd)
    except Exception:
        return None


__all__ = ["ConsoleKeys", "PosixKeys", "WindowsKeys", "console_keys", "is_a_console"]
