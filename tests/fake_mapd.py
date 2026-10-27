"""A stand-in for the Microduck's `mapd` on a TCP port, which quackd's client reaches as it reaches
the real one through a tunnel (`quackd_microduck.transports.mapd`).

It answers what mapd answers at the branch quackd read (`mapd_upstream_api`): `map.status`,
`map.stream` (the status, then a `map.state` every `period_s` until the link closes), `map.grid`
and `map.wipe`, and METHOD_NOT_FOUND for every other call, `hello` among them. Each state is built
from its attributes when it is sent, so a test can lose the robot, take a keyframe or move it
while a walk is under way. Its clock is this machine's monotonic clock, as the fake robotd's is,
so the two agree as a robot's mapd and robotd do.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections.abc import Callable
from typing import Any

from quackd_microduck import mapd_upstream_api as mapd
from quackd_microduck import upstream_api as up


class FakeMapd:
    def __init__(self, *, period_s: float = mapd.STREAM_PERIOD_S) -> None:
        self.period_s = period_s
        self.pose: dict[str, float] | None = {"x": 0.0, "y": 0.0, "yaw": 0.0}
        """Where the next state puts the robot; None leaves `pose` out with no word for why."""
        self.lost: str | None = None
        """The word the next state gives for not knowing where the robot is, or None."""
        self.collecting = False
        self.keyframes: list[dict[str, float]] = [
            {"x": 0.0, "y": 0.0, "yaw": 0.0},
            {"x": 0.8, "y": 0.1, "yaw": 0.3},
            {"x": 1.4, "y": -0.2, "yaw": -0.5},
        ]
        """The map's stops in its own frame, the first at its origin."""
        self.unavailable: str | None = None
        """Why it is not mapping at all, which the status says, or None."""
        self.missing: set[str] = set()
        """Calls answered METHOD_NOT_FOUND, as a mapd from before they existed would."""
        self.silent = False
        """Take each call and answer nothing, as something that holds the socket and is not mapd."""
        self.slow_s = 0.0
        """How long every answer but the stream's waits before it goes out."""
        self.lag_s = 0.0
        """How far behind this machine's clock each state's `t_ns` is, as mapd's work takes."""
        self.ahead_s = 0.0
        """How far ahead of it, as a mapd reading another robot's clock would be."""
        self.clock: Callable[[], int] = time.monotonic_ns
        self.res_m = mapd.GRID_RES_M
        self.size = 20
        """Cells across and up the square grid `map.grid` answers with, all floor."""
        self.requests: list[dict[str, Any]] = []
        """Every call taken, in order, on any link."""
        self.states_sent = 0
        self.writers: list[asyncio.StreamWriter] = []
        self.server: asyncio.AbstractServer | None = None
        self.port = 0
        self._streams: list[asyncio.Task[None]] = []

    async def start(self) -> None:
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]

    @property
    def address(self) -> str:
        return f"tcp://127.0.0.1:{self.port}"

    async def stop(self) -> None:
        assert self.server is not None
        self.hang_up()
        self.server.close()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self.server.wait_closed(), 2.0)

    def hang_up(self) -> None:
        """Close every link from this end, as a mapd that exits does."""
        for task in self._streams:
            task.cancel()
        self._streams.clear()
        for writer in self.writers:
            writer.close()
        self.writers.clear()

    def methods(self) -> list[str]:
        return [str(r.get("method")) for r in self.requests]

    def state(self) -> dict[str, Any]:
        t_ns = self.clock() - int(self.lag_s * 1e9) + int(self.ahead_s * 1e9)
        out: dict[str, Any] = {
            "t_ns": t_ns,
            "collecting": self.collecting,
            "keyframes": len(self.keyframes),
        }
        if self.lost is not None:
            out["lost"] = self.lost
        elif self.pose is not None:
            out["pose"] = dict(self.pose)
        return out

    def status(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "state": self.state(),
            "loops": 0,
            "relocalizations": 0,
            "island_keyframes": 0,
            "path": "/var/lib/mapd/map",
        }
        if self.unavailable is not None:
            out["unavailable"] = self.unavailable
        return out

    def grid(self) -> dict[str, Any]:
        half = self.size * self.res_m / 2
        return {
            "res_m": self.res_m,
            "origin": [-half, -half],
            "width": self.size,
            "height": self.size,
            "cells": "." * (self.size * self.size),
            "keyframes": [dict(k) for k in self.keyframes],
            "pose": dict(self.pose) if self.pose is not None and self.lost is None else None,
        }

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.writers.append(writer)
        try:
            while line := await reader.readline():
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue
                self.requests.append(msg)
                if self.silent:
                    continue
                await self._answer(msg, writer)
        except (ConnectionError, asyncio.CancelledError):
            pass
        finally:
            with contextlib.suppress(Exception):
                writer.close()

    async def _answer(self, msg: dict[str, Any], writer: asyncio.StreamWriter) -> None:
        method, req_id = msg.get("method"), msg.get("id")
        if method in self.missing or method not in (
            mapd.MAPD_STATUS.name,
            mapd.MAPD_STREAM.name,
            mapd.MAPD_GRID.name,
            mapd.MAPD_WIPE.name,
        ):
            self._send(
                writer,
                {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": up.ERR_METHOD_NOT_FOUND, "message": f"no method {method}"},
                },
            )
            return
        if method == mapd.MAPD_STREAM.name:
            self._send(writer, {"jsonrpc": "2.0", "id": req_id, "result": self.status()})
            self._streams.append(asyncio.create_task(self._stream(writer)))
            return
        if self.slow_s:
            await asyncio.sleep(self.slow_s)
        if method == mapd.MAPD_STATUS.name:
            result: Any = self.status()
        elif method == mapd.MAPD_GRID.name:
            result = self.grid()
        else:
            result = {"keyframes": len(self.keyframes)}
            self.keyframes = []
        self._send(writer, {"jsonrpc": "2.0", "id": req_id, "result": result})

    async def _stream(self, writer: asyncio.StreamWriter) -> None:
        while not writer.is_closing():
            await asyncio.sleep(self.period_s)
            self._send(
                writer, {"jsonrpc": "2.0", "method": mapd.MAPD_STATE.name, "params": self.state()}
            )
            self.states_sent += 1

    def _send(self, writer: asyncio.StreamWriter, msg: dict[str, Any]) -> None:
        if not writer.is_closing():
            writer.write(json.dumps(msg).encode() + b"\n")
