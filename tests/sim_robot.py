"""One robot's radio in a process of its own, on the sim air, for test_radio_sim_processes.py.

It advertises a beacon id and listens. The robot whose beacon id is the lower dials the other the
moment it hears it, asks it one thing over a link and closes the link; the other answers whoever
dials it and waits for the link to close. Either way it writes what happened as JSON and exits:
outcomes only, never how long anything took, so the test holds on a slow machine too.

    python tests/sim_robot.py --air DIR --beacon HEX --expect HEX --t0 SECONDS --out FILE
"""

from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import sys
import time
from typing import Any

from quackd.passport.beacon import ble_service_data, initiates
from quackd.passport.errors import PassportError
from quackd.passport.link import Media
from quackd.radio import Pipe, Sighting
from quackd.radio.links import LinkInbox, LinkTable
from quackd.radio.sim import Path, SimOptions, SimRadio

#: How long the robot that dialled keeps advertising after its link closed: five of the air's
#: ticks, for the other robot to have heard it whatever the order the two started in.
LINGER_S = 1.0


async def run(args: argparse.Namespace) -> dict[str, Any]:
    loop = asyncio.get_running_loop()
    radio = SimRadio(
        SimOptions(
            air=pathlib.Path(args.air),
            path=Path.parse(args.path),
            t0=args.t0,
            seed=args.seed,
            token=args.token,
        )
    )
    closed = asyncio.Event()
    table = LinkTable(on_change=lambda change: None if change.opened else closed.set())
    accepting: set[asyncio.Task[object]] = set()
    heard: dict[str, list[float]] = {}
    answered: list[str] = []
    found: asyncio.Future[Sighting] = loop.create_future()

    def answer(body: bytes, media: Media) -> tuple[int, bytes | None, Media]:
        answered.append(body.decode("utf-8"))
        return 200, f"hello back from {args.beacon}".encode(), Media.TEXT

    def inbox_for(route: str) -> LinkInbox:
        return answer

    def on_sighting(sighting: Sighting) -> None:
        heard.setdefault(sighting.beacon_id, []).append(sighting.rssi)
        if sighting.beacon_id == args.expect and not found.done():
            found.set_result(sighting)

    def on_link(pipe: Pipe, address: str) -> None:
        task = loop.create_task(table.accept(pipe, me=args.beacon, inbox_for=inbox_for))
        accepting.add(task)
        task.add_done_callback(accepting.discard)

    advert = ble_service_data(args.beacon, lan=False, link=True)
    status = await radio.start(advert=advert, on_sighting=on_sighting, on_link=on_link)
    result: dict[str, Any] = {"radio": status.state, "address": radio.address, "reply": None}
    deadline = time.monotonic() + args.seconds

    def left() -> float:
        return max(deadline - time.monotonic(), 0.1)

    result["dialled"] = initiates(args.beacon, args.expect)
    try:
        if result["dialled"]:
            sighting = await asyncio.wait_for(found, left())
            link = await table.dial(
                radio, sighting.address, me=args.beacon, expect=args.expect, inbox_for=inbox_for
            )
            code, reply, _media = await link.request(
                f"hello from {args.beacon}".encode(), Media.JOSE, timeout=10
            )
            result["reply"] = [code, reply.decode("utf-8")]
            result["datagram_size"] = link.send_size
            await link.close()
            # still advertising a little longer, so the robot it dialled hears it too
            await asyncio.sleep(LINGER_S)
        else:
            await asyncio.wait_for(closed.wait(), left())
            await asyncio.wait_for(found, left())
    except TimeoutError:
        result["error"] = f"nothing happened within {args.seconds:g} s"
    except PassportError as e:
        result["error"] = f"{type(e).__name__}: {e}"
    finally:
        result["answered"] = answered
        result["heard"] = {
            beacon: {"count": len(rssi), "min": min(rssi), "max": max(rssi)}
            for beacon, rssi in heard.items()
        }
        await table.close_all()
        table.shutdown()
        await radio.stop()
    return result


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--air", required=True)
    parser.add_argument("--beacon", required=True)
    parser.add_argument("--expect", required=True)
    parser.add_argument("--t0", type=float, required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--path", default="0,0@0")
    parser.add_argument("--token", default=None)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--seconds", type=float, default=30.0)
    args = parser.parse_args(argv)
    result = asyncio.run(run(args))
    pathlib.Path(args.out).write_text(json.dumps(result), encoding="utf-8")
    return 0 if "error" not in result else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
