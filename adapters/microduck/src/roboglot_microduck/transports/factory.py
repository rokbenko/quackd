"""A backend name → a `RobotTransport`.

The robot-facing transport is reachable only by name, which is how the "no UNVERIFIED upstream
call without `--robot microduck:jsonrpc`" promise is kept.

A body of the family takes the backends it declares from here (`Body.backends`).
"""

from __future__ import annotations

from roboglot.adapters.base import AdapterError
from roboglot.transport.base import RobotTransport, TransportError
from roboglot_microduck.body import MICRODUCK, Body

TRANSPORT_NAMES = ("mock", "jsonrpc")
TRANSPORT_STATUS = {
    "mock": "✅ scripted, for tests",
    "jsonrpc": "🧪 experimental — real robot over robotd's unix socket (or a TCP forward)",
}


def make_transport(
    name: str,
    *,
    body: Body = MICRODUCK,
    address: str | None = None,
    camera_url: str | None = None,
    map_address: str | None = None,
) -> RobotTransport:
    """`map_address` is where the real duck's own `mapd` answers; the adapter refuses it on
    any other backend."""
    name = name.lower()
    if name not in body.backends:
        raise TransportError(
            f"unknown transport {name!r}; choose one of {', '.join(body.backends)}"
        )
    if name == "mock":
        from roboglot.transport.mock import MockTransport

        # a mock is not kinder than its robot: it says where it is only on a body that does
        return MockTransport(spatial=name in body.places_backends)
    if name == "jsonrpc":
        from roboglot_microduck.transports.jsonrpc_unix import JsonRpcUnixTransport

        # Refused here, as the body is built, and not at its connect: a run and a pilot team
        # build every body before they write anything, so the refusal leaves nothing on disk.
        # A description builds no transport, so `validate` and `doctor` answer without one. It
        # is an adapter's refusal, which is a TransportError too, because `roboglot robot release`
        # and `rest-pose` build a robot catching only that, and anything else was a traceback.
        if not address and body.default_address is None:
            raise AdapterError(body.no_address)
        return JsonRpcUnixTransport(
            address=address, body=body, camera_url=camera_url, map_address=map_address
        )
    raise TransportError(f"unknown transport {name!r}; choose one of {', '.join(TRANSPORT_NAMES)}")
