"""Work on threads an exit does not wait for.

asyncio's pool is joined as `asyncio.run` ends, for five minutes on 3.12 and for ever on 3.11, and
`concurrent.futures`' pools are joined again as the interpreter ends, with no limit at all. So a
call that never came back, one on a serial bus above all, held a run that had finished: its close
done, its record written, its verdict not printed, and with a Ctrl-C doing nothing on Windows the
only way out was to close the window.

The threads here are daemons, so nothing joins them. A call that never comes back stays where it
is until the process ends, which is all that can be done with one: Python cannot interrupt a thread
inside a call. They suit a call that may not come back and whose result nobody needs once the
process is on its way out, and they are no place for a write that must reach a file."""

from __future__ import annotations

import queue
import threading
from collections.abc import Callable
from concurrent.futures import Executor, Future
from typing import Any, ParamSpec, TypeVar

P = ParamSpec("P")
T = TypeVar("T")


class DaemonThreads(Executor):
    """An executor whose threads are daemons, with a new one wherever none is free. A thread that
    goes idle waits for the next call rather than ending, so a bus called a hundred times a second
    does not start a hundred threads."""

    def __init__(self, name: str) -> None:
        self._name = name
        self._work: queue.SimpleQueue[Callable[[], None]] = queue.SimpleQueue()
        self._idle = threading.Semaphore(0)
        self._started = 0
        self._lock = threading.Lock()

    def submit(self, fn: Callable[P, T], /, *args: P.args, **kwargs: P.kwargs) -> Future[T]:
        future: Future[T] = Future()

        def call() -> None:
            if not future.set_running_or_notify_cancel():
                return
            try:
                answer = fn(*args, **kwargs)
            except BaseException as e:
                future.set_exception(e)
            else:
                future.set_result(answer)

        self._work.put(call)
        if not self._idle.acquire(blocking=False):
            self._one_more()
        return future

    def _one_more(self) -> None:
        with self._lock:
            self._started += 1
            number = self._started
        threading.Thread(target=self._serve, name=f"{self._name}-{number}", daemon=True).start()

    def _serve(self) -> None:
        while True:
            call: Any = self._work.get()
            call()
            del call  # what it was handed is not kept alive until the next call
            self._idle.release()


__all__ = ["DaemonThreads"]
