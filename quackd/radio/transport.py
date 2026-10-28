"""Where talk goes: one `post` for a route over the LAN or over a Bluetooth link.

Talk sends a message and gets back a status and perhaps a reply, the shape of an HTTP inbox, and
since a link carries that same shape (`quackd.radio.links`), the talk code never has to know
which of the two a robot is on. A route is `http://host:port/robot-passport/inbox` for a robot
found on the LAN, or `ble://<link id>` for one on a link this robot holds, and a reply goes back
only the way its message came: a robot's own Talker keeps the route each session arrived on.
"""

from __future__ import annotations

from collections.abc import Callable

from quackd.passport import fetch
from quackd.passport.errors import PassportError
from quackd.passport.talk import MAX_HELLO_BYTES, media_type
from quackd.radio.links import ROUTE_PREFIX, LinkDown, LinkTransport

#: How a message is sent: to a route, as wire text, for a status and perhaps a reply.
Send = Callable[[str, str], tuple[int, str | None]]


def http_post(route: str, wire: str) -> tuple[int, str | None]:
    """A message posted to an HTTP inbox, under the fetch rules' clock and byte budget: one URL,
    no redirect followed, five seconds for all of it."""
    status, body = fetch.post(route, wire.encode("utf-8"), media_type(wire), limit=MAX_HELLO_BYTES)
    return status, body.decode("utf-8", errors="replace") if body else None


class Transports:
    """Every way a robot sends talk: HTTP for `http://` routes, its links for `ble://` ones."""

    def __init__(self, *, http: Send, link: LinkTransport | None = None) -> None:
        self.http = http
        #: set once the radio is up; until then a `ble://` route is a link that is down
        self.link = link

    def post(self, route: str, wire: str) -> tuple[int, str | None]:
        if route.startswith(ROUTE_PREFIX):
            if self.link is None:
                raise LinkDown("this robot has no radio to send that over")
            return self.link.post(route, wire)
        if route.startswith(("http://", "https://")):
            return self.http(route, wire)
        raise PassportError("6.2", f"{route[:80]!r} is not a route talk goes over")


__all__ = ["Send", "Transports", "http_post"]
