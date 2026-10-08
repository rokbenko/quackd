"""The WebSocket agent transport upstream designed but has not shipped. STUB.

architecture.md §5.3 (draft, 2026-07-22) says a server-side agent should "open a WebSocket,
poll a frame, send intents" with `get_frame` returning a JPEG. The roadmap (2026-08-26)
lists that SDK surface as in progress. This file exists so the CLI can name the transport,
`quackd doctor` can explain its status, and the day upstream ships it there is exactly one
place to fill in. It never invents a method name.

By daemon-v0.16.1 upstream had dropped that design: §5.3 sends a server-side agent over a
rendezvous control lane instead (`upstream_api.WEBSOCKET_GATEWAY`), so the refusal below says
there is nothing to connect to rather than something to wait for.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from PIL import Image

from quackd.transport.base import Ack, DuckState, Intent, TransportError
from quackd_microduck import upstream_api as up

STATUS = "STUB — waiting for upstream"


class WebSocketTransport:
    name = "websocket"

    def __init__(self, address: str | None = None) -> None:
        self.address = address or "ws://<robot>:<port>"

    def _not_shipped(self) -> TransportError:
        return TransportError(
            "upstream ships no WebSocket agent gateway to connect to "
            f"({up.WEBSOCKET_GATEWAY.status}: {up.WEBSOCKET_GATEWAY.note}). "
            f"See {up.WEBSOCKET_GATEWAY.source} and {up.ROADMAP}. "
            "Use --robot microduck:jsonrpc (experimental) or --robot microduck:sim2d."
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
