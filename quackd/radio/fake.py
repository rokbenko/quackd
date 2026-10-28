"""Stand-ins for the air, for the tests: pipes made of queues, in one process and one loop.

`MemoryPipe.pair` is a link's two ends with nothing between them but an optional delay, so a
link, a reassembler and everything above them run in a test exactly as they would over a radio,
in milliseconds. `overheard_by` is BlueZ's one surprise for a link: a peripheral's notification
reaches every central subscribed to its characteristic, not only the one it was meant for.
"""

from __future__ import annotations

import asyncio

from quackd.radio import PipeClosed
from quackd.radio.pipe import BasePipe

_STOP = object()


class MemoryPipe(BasePipe):
    """One end of a pair of queues. Datagrams arrive in order, each after `latency` seconds;
    a send with `barrier=True` returns once the other end has taken the datagram."""

    def __init__(self, datagram_size: int, *, latency: float = 0.0) -> None:
        super().__init__(datagram_size)
        self.latency = latency
        self.peer: MemoryPipe | None = None
        #: other ends that hear what this end sends too, as every central subscribed to a
        #: peripheral hears each notification it sends
        self.overheard_by: list[MemoryPipe] = []
        #: datagrams this end sent, for a test to count
        self.sent = 0
        self._queue: asyncio.Queue[object] | None = None

    @classmethod
    def pair(
        cls, size: int = 182, *, latency: float = 0.0, other_size: int | None = None
    ) -> tuple[MemoryPipe, MemoryPipe]:
        """Two ends joined to each other: the first sends datagrams of up to `size` bytes, the
        second of up to `other_size` (`size` unless given)."""
        a = cls(size, latency=latency)
        b = cls(other_size if other_size is not None else size, latency=latency)
        a.peer, b.peer = b, a
        return a, b

    def _incoming(self) -> asyncio.Queue[object]:
        if self._queue is None:
            self._queue = asyncio.Queue()
            asyncio.get_running_loop().create_task(self._pump(self._queue))
        return self._queue

    async def _pump(self, queue: asyncio.Queue[object]) -> None:
        while (item := await queue.get()) is not _STOP:
            assert isinstance(item, tuple)
            datagram, done = item
            if self.latency:
                await asyncio.sleep(self.latency)
            if self.closed is None:
                self._received(datagram)
                if done is not None and not done.done():
                    done.set_result(None)
            elif done is not None and not done.done():
                done.set_exception(PipeClosed(self.closed))

    async def send(self, datagram: bytes, *, barrier: bool = True) -> None:
        peer = self.peer
        if self.closed is not None or peer is None or peer.closed is not None:
            raise PipeClosed(self.closed or "the other end is gone")
        if len(datagram) > self.datagram_size:
            raise ValueError(f"{len(datagram)} bytes, over this pipe's {self.datagram_size}")
        self.sent += 1
        done = asyncio.get_running_loop().create_future() if barrier else None
        peer._incoming().put_nowait((datagram, done))
        for other in self.overheard_by:
            if other.closed is None:
                other._incoming().put_nowait((datagram, None))
        if done is not None:
            await done

    def _close(self, reason: str) -> None:
        if self._ended(reason) and self._queue is not None:
            self._queue.put_nowait(_STOP)  # the pump fails what is left behind it, then ends

    async def close(self) -> None:
        self._close("closed by this end")
        if self.peer is not None:
            self.peer._close("closed by the other end")

    def cut(self, reason: str = "link lost") -> None:
        """Both ends lose the link at once, the way a radio drops one out of range."""
        self._close(reason)
        if self.peer is not None:
            self.peer._close(reason)
