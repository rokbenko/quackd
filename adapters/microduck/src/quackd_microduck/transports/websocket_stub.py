"""The WebSocket agent transport upstream designed and then dropped. STUB, and deprecated.

architecture.md §5.3 (draft, 2026-07-22) said a server-side agent should "open a WebSocket,
poll a frame, send intents" with `get_frame` returning a JPEG, and the roadmap (2026-08-26)
listed that SDK surface as in progress. This file existed so the CLI could name the transport,
`quackd doctor` could explain its status, and the day upstream shipped it there would be
exactly one place to fill in. It never invents a method name.

By daemon-v0.16.1 upstream had dropped that design: §5.3 sends a server-side agent over a
rendezvous control lane instead (`upstream_api.WEBSOCKET_GATEWAY`), which carries no pixels and
which upstream's own relay calls not a teleop lane (`upstream_api.RENDEZVOUS_LANE`). quackd does
not drive over it, so there is nothing for this stub to become. It is deprecated and is removed
in 0.19.0, and until then `quackd run`, `doctor`, `serve-mcp` and `robot list --probe` print one
line on stderr naming `microduck:jsonrpc` just before they connect through it, and never for a
robot they do not connect (`quackd_microduck.DEPRECATED_BACKENDS`). The refusal below ends with
the same line, so a program that connects it without any of them is told too.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from PIL import Image

from quackd.transport.base import Ack, DuckState, Intent, TransportError
from quackd_microduck import DEPRECATED_BACKENDS
from quackd_microduck import upstream_api as up

STATUS = "STUB — deprecated, removed in 0.19.0: upstream dropped the gateway"


class WebSocketTransport:
    name = "websocket"

    def __init__(self, address: str | None = None) -> None:
        self.address = address or "ws://<robot>:<port>"

    def _not_shipped(self) -> TransportError:
        return TransportError(
            "upstream ships no WebSocket agent gateway to connect to "
            f"({up.WEBSOCKET_GATEWAY.status}: {up.WEBSOCKET_GATEWAY.note}). "
            f"See {up.WEBSOCKET_GATEWAY.source}, {up.RENDEZVOUS_LANE.source} and {up.ROADMAP}. "
            f"{DEPRECATED_BACKENDS[self.name]}."
        )

    async def connect(self) -> None:
        raise self._not_shipped()

    async def close(self) -> None:
        return None

    async def get_frame(self) -> Image.Image | None:
        raise self._not_shipped()

    async def get_state(self) -> DuckState:
        raise self._not_shipped()

    async def send_intent(self, intent: Intent) -> Ack:
        raise self._not_shipped()

    async def subscribe(self, topic: str) -> AsyncIterator[dict[str, Any]]:  # type: ignore[override]
        raise self._not_shipped()
        yield {}  # pragma: no cover - makes this an async generator

    async def heartbeat(self) -> None:
        raise self._not_shipped()

    async def stop(self) -> None:
        return None

    def now(self) -> float:
        import time

        return time.monotonic()

    async def sleep(self, seconds: float) -> None:
        import asyncio

        await asyncio.sleep(seconds)
