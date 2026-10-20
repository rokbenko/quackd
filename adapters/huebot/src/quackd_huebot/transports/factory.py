"""A backend name → a `DuckTransport`.

Two, and the first is the mock, because a Huebot runs only where its maker's shim answers. The
robot-facing transport is reachable only by name, which is how the "no UNVERIFIED upstream call
without `--robot huebot:jsonrpc`" promise is kept, and it has no default address: the shim is
wherever its maker put it, so `--address` says where, and building it without one is refused.
"""

from __future__ import annotations

from quackd.transport.base import DuckTransport, TransportError

TRANSPORT_NAMES = ("mock", "jsonrpc")


def make_transport(
    name: str,
    *,
    address: str | None = None,
    camera_url: str | None = None,
) -> DuckTransport:
    name = name.lower()
    if name == "mock":
        from quackd.transport.mock import MockTransport

        return MockTransport()
    if name == "jsonrpc":
        from quackd_huebot.transports.jsonrpc_unix import NO_ADDRESS, JsonRpcUnixTransport

        # Refused here, as the body is built, and not at its connect: a run and a pilot flock
        # build every body before they write anything, so the refusal leaves nothing on disk.
        # A description builds no transport, so `validate` and `doctor` answer without one.
        if not address:
            raise TransportError(NO_ADDRESS)
        return JsonRpcUnixTransport(address=address, camera_url=camera_url)
    raise TransportError(f"unknown transport {name!r}; choose one of {', '.join(TRANSPORT_NAMES)}")
