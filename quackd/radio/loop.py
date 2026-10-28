"""The radio's event loop, on a thread of its own, and the one safe way in from other threads.

bleak and dbus-fast are asyncio libraries, and BlueZ calls back on the loop they run on, so the
radio, its links and everything they call back into live on one loop. The rest of a robot's
service is threads: the passport server answers HTTP on its own, a run's pilot has its own, and
a hello sent over a Bluetooth link starts on whichever thread the talk code is on. `call` is how
any of them waits on the loop, and the one mistake it refuses is the loop waiting on itself,
which would hang the radio with nothing in the log to say why.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import threading
from collections.abc import Callable, Coroutine
from typing import Any, TypeVar

T = TypeVar("T")


class LoopThread:
    """An asyncio loop running on a daemon thread from `start` until `stop`."""

    def __init__(self, name: str = "quackd-radio") -> None:
        self.name = name
        self.loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run, name=name, daemon=True)
        self._ready = threading.Event()

    def start(self) -> LoopThread:
        self._thread.start()
        if not self._ready.wait(5.0):
            raise RuntimeError(f"the {self.name} loop did not start within 5 s")
        return self

    def _run(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.call_soon(self._ready.set)
        try:
            self.loop.run_forever()
        finally:
            # whatever was still running is cancelled and allowed to clean up, so a link's
            # close or a radio's unregister gets its chance before the loop goes
            pending = [t for t in asyncio.all_tasks(self.loop) if not t.done()]
            for task in pending:
                task.cancel()
            if pending:
                self.loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            self.loop.run_until_complete(self.loop.shutdown_asyncgens())
            self.loop.close()

    @property
    def in_loop(self) -> bool:
        """Whether the caller is running on this loop's own thread."""
        return threading.current_thread() is self._thread

    @property
    def running(self) -> bool:
        return self._thread.is_alive() and self.loop.is_running()

    def call(self, coro: Coroutine[Any, Any, T], timeout: float) -> T:
        """Run `coro` on the loop and wait at most `timeout` seconds for what it returns, from
        any thread but the loop's own. A coroutine still running at the deadline is cancelled
        and the caller gets a `TimeoutError`."""
        if self.in_loop:
            coro.close()
            raise RuntimeError(
                "LoopThread.call was made from the loop's own thread, where it would wait on "
                "itself for ever: await the coroutine there instead"
            )
        future = asyncio.run_coroutine_threadsafe(coro, self.loop)
        try:
            return future.result(timeout)
        except concurrent.futures.TimeoutError:
            future.cancel()
            raise TimeoutError(f"the radio did not answer within {timeout:g} s") from None

    def submit(self, coro: Coroutine[Any, Any, T]) -> concurrent.futures.Future[T]:
        """Run `coro` on the loop without waiting for it; safe from any thread, the loop's too."""
        return asyncio.run_coroutine_threadsafe(coro, self.loop)

    def soon(self, fn: Callable[..., Any], *args: Any) -> None:
        """Call `fn(*args)` on the loop as soon as it can, from any thread."""
        self.loop.call_soon_threadsafe(fn, *args)

    def stop(self, timeout: float = 5.0) -> None:
        """Stop the loop, after cancelling what runs on it, and wait for the thread to end."""
        if not self._thread.is_alive():
            return
        with contextlib.suppress(RuntimeError):  # a loop already closed
            self.loop.call_soon_threadsafe(self.loop.stop)
        if not self.in_loop:
            self._thread.join(timeout)
