"""A backend name → a `DuckTransport`.

The default is the simulator, because the north-star demo needs no hardware. The
robot-facing transport is reachable only by name, which is how the "no UNVERIFIED upstream
call without `--robot microduck:jsonrpc`" promise is kept.
"""

from __future__ import annotations

import os

from quackd.transport.base import DuckTransport, TransportError

TRANSPORT_NAMES = ("sim2d", "mujoco", "mock", "jsonrpc")
TRANSPORT_STATUS = {
    "sim2d": "✅ built-in simulator (default)",
    "mujoco": "✅ physics: upstream's model and walk policy (needs quackd[mujoco])",
    "mock": "✅ scripted, for tests",
    "jsonrpc": "🧪 experimental — real robot over robotd's unix socket (or a TCP forward)",
}


def make_transport(
    name: str,
    *,
    seed: int | None = None,
    address: str | None = None,
    live: bool = False,
    camera_url: str | None = None,
    room: str | None = None,
) -> DuckTransport:
    """`room` is a room's name or file, for a simulator to lay out and keep a map of the way the
    duck's own `mapd` would (`transports.mapd`); the adapter refuses it on any other backend."""
    name = name.lower()
    if name == "sim2d":
        from quackd.transport.sim2d import Sim2DTransport

        if room is None:
            return Sim2DTransport(seed=seed if seed is not None else 0, live=live)
        from quackd.sim2d.world import DUCK_R
        from quackd.space.room import load_room
        from quackd_microduck.transports.mapd import sim_map_params

        return Sim2DTransport(
            seed=seed if seed is not None else 0,
            live=live,
            room=load_room(room),
            map_params=sim_map_params(DUCK_R),
        )
    if name == "mujoco":
        from quackd_microduck.transports.mujoco import BODY_ENV, DEFAULT_BODY, MujocoTransport

        # The real duck by default: it is the whole point of a physics backend. The
        # kinematic stand-in is one env var away for anyone who would rather not spend ten
        # megabytes on upstream's meshes, and is what the tests use.
        if room is None:
            return MujocoTransport(
                seed=seed if seed is not None else 0,
                live=live,
                body=os.environ.get(BODY_ENV) or DEFAULT_BODY,
            )
        from quackd.space.room import load_room
        from quackd_microduck.sim3d.scene import DUCK_R as BODY_R
        from quackd_microduck.transports.mapd import sim_map_params

        return MujocoTransport(
            seed=seed if seed is not None else 0,
            live=live,
            body=os.environ.get(BODY_ENV) or DEFAULT_BODY,
            room=load_room(room),
            map_params=sim_map_params(BODY_R),
        )
    if name == "mock":
        from quackd.transport.mock import MockTransport

        return MockTransport(spatial=True)
    if name == "jsonrpc":
        from quackd_microduck.transports.jsonrpc_unix import JsonRpcUnixTransport

        return JsonRpcUnixTransport(address=address, camera_url=camera_url)
    raise TransportError(f"unknown transport {name!r}; choose one of {', '.join(TRANSPORT_NAMES)}")
