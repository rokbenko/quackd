"""What a vendor SDK's client does before its first request, done while nothing waits on it.

Anthropic's and OpenAI's SDKs are built from one generator, and a client of either starts its
first request by asking which platform it runs on, `self._platform = await
asyncify(get_platform)()`, in a worker thread, while it has no answer (`<sdk>/_base_client.py`:
the eleven openai releases from 1.66.0 to 3.22.1 uv had cached, read 2026-10-10, and anthropic
0.122.0 and 1.16.0). `get_platform` reads `platform.system()` and `platform.platform()`, and on
Linux the distribution's own file. On Windows `platform.platform()` asks the system for its
version, by a WMI query from Python 3.12 and by `cmd /c ver` in a subprocess on 3.11. CPython
keeps the answer for the life of the process, so it is paid once, and it used to be paid inside
the first request: a decision LLM's first turn, against that turn's one second, and the pilot's
first call, in the seconds a person waits for the robot to move.

The first request paid one more thing that has nothing to do with the platform. The SDK's HTTP
client reaches the network through `anyio`, which imports its asyncio backend, a module of its
own, the first time it is asked for one (`ANYIO_BACKEND`). Timed on 2026-10-10 on a machine
running the whole suite beside it, the first turn imported it and took some 10 to 15 ms longer
than the turns after it.

`warm_client` asks both now: the decision client's build calls it, before the robot connects,
and the pilot's client is warmed in a thread while the robot connects (`AgentLoop.run`). Nothing
here sends a request, reads a key or opens a connection, and nothing here may fail what called
it, because all it ever does is save a first request some time: a client left to ask for itself
is the client as it was.
"""

from __future__ import annotations

import contextlib
import importlib
import platform
from typing import Any

ANYIO_BACKEND = "anyio._backends._asyncio"
"""The module `anyio` imports the first time it is asked for its asyncio backend, which the
SDK's HTTP client asks for on its first connection (`warm_client`)."""

_UNSET = object()
"""What `getattr` hands back for an attribute a client does not have, which no answer can be."""


def warm_client(client: Any, sdk: str) -> None:
    """Ask now the one question `sdk`'s client asks before its first request, and import what its
    first connection would import, so that request finds both answered.

    The SDK's own `get_platform` is asked rather than the standard library's functions alone, so
    whatever a release reads gets read, and its answer is handed to the client where the client
    keeps one, `_platform`, which spares the first request even the worker thread it would have
    started to ask again; under a release that kept it elsewhere the caches are warm all the
    same. Where the SDK has no such function, the standard library's two are asked instead, which
    is most of the cost on every platform. Never raises."""
    try:
        probe = getattr(importlib.import_module(f"{sdk}._base_client"), "get_platform", None)
    except Exception:
        probe = None
    try:
        if not callable(probe):
            platform.system()
            platform.platform()
        else:
            found = probe()
            if getattr(client, "_platform", _UNSET) is None:
                client._platform = found
    except Exception:
        pass
    with contextlib.suppress(Exception):
        importlib.import_module(ANYIO_BACKEND)
