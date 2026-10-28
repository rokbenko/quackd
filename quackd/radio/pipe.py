"""What every pipe shares, whichever radio it runs over: its callbacks, and one close.

A pipe is handed to whoever takes a link before that code has said where datagrams go: BlueZ
makes a peripheral's pipe when a central's first write arrives, and that write is the link's
OPEN. So a pipe keeps what arrives before it has a callback, a few datagrams and no more, and
hands them over when it gets one. It closes once, from whichever end, and every callback hears
why, even one set after it closed.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable

#: Datagrams a pipe keeps for a callback not set yet. A link's handshake is one datagram; a
#: central that sends more before it has been accepted is not owed them.
EARLY_DATAGRAMS = 16


class BasePipe:
    """The callback half of a `quackd.radio.Pipe`; a radio's pipe adds `send` and `close`."""

    def __init__(self, datagram_size: int) -> None:
        self.datagram_size = datagram_size
        self._on_datagram: Callable[[bytes], None] | None = None
        self._on_close: list[Callable[[str], None]] = []
        self._early: deque[bytes] = deque()
        #: why it closed, once it has
        self.closed: str | None = None
        #: datagrams that arrived with nobody to take them and no room to keep them
        self.dropped = 0

    def on_datagram(self, cb: Callable[[bytes], None]) -> None:
        self._on_datagram = cb
        while self._early and self.closed is None:
            cb(self._early.popleft())

    def on_close(self, cb: Callable[[str], None]) -> None:
        if self.closed is not None:
            cb(self.closed)
            return
        self._on_close.append(cb)

    def _received(self, datagram: bytes) -> None:
        """A datagram from the other end, to the callback or kept for it."""
        if self.closed is not None:
            return
        if self._on_datagram is not None:
            self._on_datagram(datagram)
        elif len(self._early) < EARLY_DATAGRAMS:
            self._early.append(datagram)
        else:
            self.dropped += 1

    def _ended(self, reason: str) -> bool:
        """Mark the pipe closed and tell every callback why. Whether this call closed it."""
        if self.closed is not None:
            return False
        self.closed = reason
        self._early.clear()
        callbacks, self._on_close = self._on_close, []
        for cb in callbacks:
            cb(reason)
        return True
