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
a console read this way echoes nothing by itself, and a person answering a question has to see
what they typed."""

from __future__ import annotations

import atexit
import codecs
import contextlib
import importlib
import marshal
import os
import select
import signal
import subprocess
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
GUARD_GONE_S = 1.0
"""How long a run's close waits for the guard it has just told to stand down, which is told once and
has nothing to do but exit (`_Guard`)."""
GUARD_UP_S = 1.0
"""How long a run's start waits for its guard to be up, before the terminal leaves line mode. It
takes a few dozen milliseconds, and a machine that needs longer goes on without the wait."""
GUARD_WATCHES_S = 600.0
"""How long a guard that lost its race goes on looking for the mode this process left (`_Guard`)."""
GUARD_IDLE_S = 0.05
"""How often it looks when it has heard no key, which costs nothing a machine can measure. A key can
be read before it has seen it, since a shell is waiting for the same one, so hearing one cannot
be all it looks for."""
GUARD_LOOKS_S = 0.25
"""How long it looks closely after a key, or after it wakes: long enough for the command that key
may have started to be under way, and what it does the rest of the time is sleep."""
GUARD_TICK_S = 0.002
"""How often it looks while it looks closely. The mode it is after lasts as long as the command that
runs in it, which can be a couple of milliseconds, and it gets another go at the next one."""
ENTER = ("\r", "\n")
BACKSPACE = ("\b", "\x7f")
CTRL_C = "\x03"


def _has_a_use(key: str) -> bool:
    return key in ENTER or key in BACKSPACE or key == CTRL_C or key.isprintable()


class _Echoing:
    """What a key looks like on the screen, where nothing else draws it."""

    def _put(self, text: str) -> None:
        raise NotImplementedError

    def _echo(self, key: str) -> None:
        if key in ENTER:
            self._put("\r\n")
        elif key.isprintable():
            self._put(key)

    def erase(self) -> None:
        self._put("\b \b")

    def newline(self) -> None:
        self._put("\r\n")


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


class WindowsKeys(_Echoing):
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

    def _put(self, text: str) -> None:
        for key in text:
            self._console.putwch(key)

    def close(self) -> None:
        self._closed.set()


class PosixKeys(_Echoing):
    """A terminal out of its line mode (`ICANON`), and echoing nothing by itself (`ECHO`), so that
    this draws what has a use and nothing else: left to the terminal, an arrow key moved the cursor
    and the lines after it could be written over a question still standing. Ctrl-C is still a
    signal, and Ctrl-Z no longer is (`VSUSP`, and `VDSUSP` on macOS): it stopped the whole process,
    with an arm on its last goal and nothing reading `q`. Nor does Ctrl-S stop the output (`IXON`),
    which froze a run at its next line, and `q` with it. Enter stays the `\\r` it was sent as
    (`ICRNL`), so a link that sends `\\r\\n` for one is seen to have sent one Enter.

    Put back by `close`, at exit for a run that never got there, on the signals that kill a
    process with no exit (SIGTERM, SIGHUP, Ctrl-\\), and for one nothing can catch, a SIGKILL or a
    crash in native code, by a process of its own (`_Guard`), since a wrapper such as `uv run`
    stands between the shell and that death, and then it was `stty sane`, typed unseen.

    Kept, too, while the run lasts: a SIGSTOP from outside and an `fg` leave the terminal as the
    shell wanted it, in lines again, with `q` doing nothing until somebody pressed Enter."""

    def __init__(self, fd: int) -> None:
        termios: Any = importlib.import_module("termios")  # no such module to type on Windows
        self._fd = fd
        self._closed = threading.Event()
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="ignore")
        self._ahead = b""
        self._termios = termios
        self._mode_lock = threading.RLock()
        self._as_found = termios.tcgetattr(fd)
        mode = termios.tcgetattr(fd)
        mode[0] &= ~(termios.IXON | termios.ICRNL)
        mode[3] &= ~(termios.ICANON | termios.ECHO)
        mode[6][termios.VMIN] = 1
        mode[6][termios.VTIME] = 0
        off = _no_character(fd)
        if off is not None:
            for name in ("VSUSP", "VDSUSP"):
                if hasattr(termios, name):
                    mode[6][getattr(termios, name)] = off
        self._mode = mode
        self._screen: int | None = None
        self._guard: _Guard | None = None
        try:
            self._screen = _for_drawing(fd)
            # first, and up by the time this returns: a kill from here on finds a guard waiting, and
            # one before the mode changes has nothing to put back
            self._guard = _Guard.start(fd, self._as_found, mode)
            termios.tcsetattr(fd, termios.TCSANOW, mode)
        except Exception:
            # the caller takes this for no console, with the terminal out of line mode for good
            with contextlib.suppress(OSError, termios.error):
                termios.tcsetattr(fd, termios.TCSANOW, self._as_found)
            if self._guard is not None:
                self._guard.stand_down()
            if self._screen not in (None, fd):
                with contextlib.suppress(OSError):
                    os.close(self._screen)
            raise
        atexit.register(self.close)
        self._before: dict[int, Any] = {}
        for name in ("SIGTERM", "SIGHUP", "SIGQUIT"):
            with contextlib.suppress(ValueError):  # not the main thread, so nothing to catch it on
                number = getattr(signal, name)
                self._before[number] = signal.signal(number, self._killed)

    def _killed(self, number: int, _frame: Any) -> None:
        """Put the terminal back, and let the signal do what it was going to."""
        self.close()
        signal.raise_signal(number)

    def read(self) -> str:
        while not self._closed.is_set():
            data = self._byte(POLL_S)
            if data is None:
                self._keep_the_mode()
                continue
            if not data:  # the terminal went away
                return ""
            if data == b"\x1b":
                self._skip_escape()
                continue
            key = self._decoder.decode(data)  # nothing yet in the middle of a character
            if key and _has_a_use(key):
                self._echo(key)
                return key
        return ""

    def _keep_the_mode(self) -> None:
        """Back out of line mode, should something have put the terminal back in it under this.
        Only what this asked for is looked at, since a BSD kernel sets `PENDIN` by itself. Not from
        the background, where changing the mode stops the whole process, and it is somebody else's
        until an `fg` makes it this one's again."""
        termios = self._termios
        with self._mode_lock, contextlib.suppress(OSError, termios.error):
            if self._closed.is_set() or _behind_the_terminal(self._fd):
                return
            found = termios.tcgetattr(self._fd)
            if found[0] & (termios.IXON | termios.ICRNL) or found[3] & (
                termios.ICANON | termios.ECHO
            ):
                termios.tcsetattr(self._fd, termios.TCSANOW, self._mode)

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

    def _put(self, text: str) -> None:
        # the terminal turns a new line into both by itself (`ONLCR`)
        if self._screen is not None:
            with contextlib.suppress(OSError):
                os.write(self._screen, text.replace("\r\n", "\n").encode(errors="replace"))

    def close(self) -> None:
        with self._mode_lock:
            if self._closed.is_set():
                return
            self._closed.set()
            atexit.unregister(self.close)
            for number, before in self._before.items():
                with contextlib.suppress(ValueError):
                    signal.signal(number, before)
            # the attempt would stop the process from behind the terminal, and it is not ours
            if not _behind_the_terminal(self._fd):
                with contextlib.suppress(OSError, self._termios.error):
                    self._termios.tcsetattr(self._fd, self._termios.TCSANOW, self._as_found)
        if self._screen not in (None, self._fd):
            with contextlib.suppress(OSError):
                os.close(self._screen)
        self._screen = None
        if self._guard is not None:
            self._guard.stand_down()


class _Guard:
    """A process that puts a terminal back if this one dies with no word said.

    Nothing in a process runs at a SIGKILL or a crash in native code, so this holds the read end of
    a pipe whose write end the process alone holds (it does not pass to what quackd spawns, and a
    child forked with no exec lets go of it, `_let_go_of_the_words`), and the kernel closes that
    with the process. A close says so down the pipe first, and the guard exits having touched
    nothing. It is in a session of its own, so a Ctrl-C or a Ctrl-\\ at the terminal never reaches
    it, and with no terminal to answer to it can change this one's mode from anywhere.

    It is up before the terminal leaves line mode (`start`), since a shell could get there while it
    started: 7 kills in 8 in a run's first moments under a wrapper. Now the kill finds a terminal
    nothing has changed, or a guard already waiting.

    It puts the terminal back only as this process left it: anything else on it is somebody else's
    by then, a full-screen program in the shell's foreground above all, and not to be reset under
    them. So on a machine with no CPU to spare it can still come second, behind a wrapper such as
    `uv run` and the shell behind that. Then bash has taken the mode it found for its own, readline
    has asked for its own on top of that, and from here that looks like any prompt: the person types
    unseen. So a guard that comes second does not leave. Bash runs the next command in the mode it
    took, which is this process's, so the guard looks for that mode, closely after a key, puts
    the terminal back under that command, and bash takes that for its own when the command ends:
    a line or two typed unseen, where it was `stty sane` as ever. It gives up after
    `GUARD_WATCHES_S`, or as soon as the terminal is as this process found it, or gone. Linux says
    so by refusing to read the mode. macOS goes on answering for a terminal whose window has
    closed, which stays readable with nothing to read, where a key somebody else has read is off
    the terminal in a moment (`gone` in the script).

    Interpreter flags that skip the site packages and the environment, since it has to be up in
    milliseconds and nothing it needs is outside the standard library."""

    _SCRIPT = (
        "import fcntl, marshal, os, select, struct, sys, termios, time\n"
        "as_found, left, ignored, watch_s, look_s, tick_s, idle_s = "
        "marshal.loads(bytes.fromhex(sys.argv[2]))\n"
        "try:\n"
        "    os.write(1, b'.')\n"
        "    os.close(1)\n"
        "except OSError:\n"  # nobody is left to hear it, which the read below says as well
        "    pass\n"
        "def gone():\n"
        "    for _ in range(2):\n"
        "        if not select.select([0], [], [], 0)[0]:\n"
        "            return False\n"
        "        if struct.unpack('i', fcntl.ioctl(0, termios.FIONREAD, b'0000'))[0]:\n"
        "            return False\n"
        "        time.sleep(tick_s)\n"
        "    return True\n"
        "if not os.read(int(sys.argv[1]), 1):\n"
        "    found = [*as_found[:3], as_found[3] & ~ignored, *as_found[4:]]\n"
        "    until = time.monotonic() + watch_s\n"
        "    closely = time.monotonic() + look_s\n"
        "    try:\n"
        "        while True:\n"
        "            now = termios.tcgetattr(0)\n"
        "            now[3] &= ~ignored\n"
        "            if now == left:\n"
        "                termios.tcsetattr(0, termios.TCSANOW, as_found)\n"
        "                break\n"
        "            clock = time.monotonic()\n"
        "            if now == found or clock >= until:\n"
        "                break\n"
        "            if clock < closely:\n"
        "                time.sleep(tick_s)\n"
        "            elif select.select([0], [], [], min(idle_s, until - clock))[0]:\n"
        "                if gone():\n"
        "                    break\n"
        "                closely = time.monotonic() + look_s\n"
        "    except (OSError, termios.error):\n"  # the terminal has gone
        "        pass\n"
    )

    def __init__(self, process: subprocess.Popen[bytes], word: int) -> None:
        self._process = process
        self._word = word

    @classmethod
    def start(cls, fd: int, as_found: list[Any], left: list[Any]) -> _Guard | None:
        """A guard over the terminal `fd`, up, or None where one will not start, which costs what
        having none always cost: a kill leaves the terminal to whoever comes next. `left` is the
        mode it is to look for, which is the one the caller is about to set."""
        ignored = getattr(importlib.import_module("termios"), "PENDIN", 0)
        left = [*left[:3], left[3] & ~ignored, *left[4:]]
        said = marshal.dumps(
            (as_found, left, ignored, GUARD_WATCHES_S, GUARD_LOOKS_S, GUARD_TICK_S, GUARD_IDLE_S)
        ).hex()
        try:
            ears, word = os.pipe()  # no descriptors left is a process that can go on without one
        except OSError:
            return None
        try:
            process = subprocess.Popen(
                [sys.executable, "-I", "-S", "-c", cls._SCRIPT, str(ears), said],
                stdin=fd,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                pass_fds=(ears,),
                start_new_session=True,
            )
        except Exception:  # an odd interpreter's `sys.executable` is a TypeError, say
            os.close(word)
            return None
        finally:
            os.close(ears)
        _WORDS.add(word)
        guard = cls(process, word)
        if not guard._up():
            guard.stand_down()
            return None
        return guard

    def _up(self) -> bool:
        """Whether it is up or may yet be, once it has had `GUARD_UP_S`. Only one that has ended
        without a word is no guard: a machine too busy to start one in that time gets it late."""
        heard = self._process.stdout
        if heard is None:
            return False
        try:
            ready, _, _ = select.select([heard], [], [], GUARD_UP_S)
            return not ready or bool(os.read(heard.fileno(), 1))
        except (OSError, ValueError):
            return True

    def stand_down(self) -> None:
        with contextlib.suppress(OSError):
            os.write(self._word, b".")
        with contextlib.suppress(OSError):
            os.close(self._word)
        _WORDS.discard(self._word)
        if self._process.stdout is not None:
            with contextlib.suppress(OSError):
                self._process.stdout.close()
        try:
            self._process.wait(GUARD_GONE_S)
        except subprocess.TimeoutExpired:
            self._process.kill()
            with contextlib.suppress(subprocess.TimeoutExpired):
                self._process.wait(GUARD_GONE_S)


_WORDS: set[int] = set()
"""The write end of every guard's pipe in this process."""


def _let_go_of_the_words() -> None:
    """In a child forked with no exec, a policy library's worker say, which would otherwise hold a
    guard's pipe open past the death of the process it is guarding, however long it lived on."""
    for word in _WORDS:
        with contextlib.suppress(OSError):
            os.close(word)
    _WORDS.clear()


if hasattr(os, "register_at_fork"):  # not on Windows, which has no guard
    os.register_at_fork(after_in_child=_let_go_of_the_words)


def _for_drawing(fd: int) -> int | None:
    """Where to draw what is typed: the terminal `fd` reads, on `fd` itself unless it was opened
    only to read, as `< /dev/tty` opens it, and then on that terminal opened again. Every key still
    worked there and nothing typed showed. None where it cannot be opened, which is what is left."""
    if sys.platform == "win32":
        return None
    fcntl: Any = importlib.import_module("fcntl")  # no such module to type on Windows
    try:
        if fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_ACCMODE != os.O_RDONLY:
            return fd
        return os.open(os.ttyname(fd), os.O_WRONLY | os.O_NOCTTY)
    except (OSError, ValueError):
        return None


def _no_character(fd: int) -> bytes | None:
    """What this terminal takes in a control character's place to mean it has none."""
    if sys.platform == "win32":
        return None
    try:
        off = os.fpathconf(fd, "PC_VDISABLE")
    except (OSError, ValueError):
        return None
    return bytes([off]) if off >= 0 else None


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
