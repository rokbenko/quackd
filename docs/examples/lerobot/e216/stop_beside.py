"""E216: a `stop` sent beside a running move over MCP, on a clock rather than from a chat.

Standard library only. It starts `quackd serve-mcp` on the robot it is given and speaks the server's
own protocol to it, one JSON line at a time: the verdict on the task, `move_joints` for 12 seconds,
and `stop` while that is still running. A chat waits for one tool before it sends the next, which is
why E196 can only show this when the model happens to send both at once.

    python stop_beside.py --robot arm-01 --after 2

It prints what each call answered, and how long after the move began. Connecting moves the arm to
its rest pose and the server folds it again as it ends, as every session does, so keep clear of it.
The move is `shoulder_pan` to 20 degrees, from the rest pose: change `--degrees` to suit your table.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
import time
from typing import Any


class Server:
    """`quackd serve-mcp`, and the answers to what it was asked, by the number each was asked by."""

    def __init__(self, robot: str) -> None:
        self.process = subprocess.Popen(
            ["quackd", "serve-mcp", "--robot", robot],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )
        self.next_id = 0
        self.lock = threading.Lock()
        self.answers: dict[int, dict[str, Any]] = {}
        self.arrived = threading.Condition()
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        assert self.process.stdout is not None
        for line in self.process.stdout:
            message = json.loads(line)
            if "id" in message:
                with self.arrived:
                    self.answers[message["id"]] = message
                    self.arrived.notify_all()

    def _write(self, message: dict[str, Any]) -> None:
        assert self.process.stdin is not None
        with self.lock:
            self.process.stdin.write(json.dumps({"jsonrpc": "2.0", **message}) + "\n")
            self.process.stdin.flush()

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        self._write({"method": method, "params": params or {}})

    def ask(self, method: str, params: dict[str, Any], *, limit_s: float = 90.0) -> dict[str, Any]:
        with self.lock:
            self.next_id += 1
            number = self.next_id
        self._write({"id": number, "method": method, "params": params})
        end = time.monotonic() + limit_s
        with self.arrived:
            while number not in self.answers:
                left = end - time.monotonic()
                if left <= 0:
                    raise TimeoutError(f"{method} did not answer in {limit_s:g} s")
                self.arrived.wait(left)
            return self.answers[number]

    def call(self, tool: str, **arguments: Any) -> str:
        said = self.ask("tools/call", {"name": tool, "arguments": arguments})
        if "error" in said:
            return f"error: {said['error']}"
        return " ".join(part.get("text", "") for part in said["result"].get("content", []))

    def close(self) -> None:
        assert self.process.stdin is not None
        self.process.stdin.close()
        try:
            self.process.wait(60)
        except subprocess.TimeoutExpired:
            self.process.terminate()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("--robot", default="arm-01")
    parser.add_argument("--after", type=float, default=2.0, help="seconds into the move to stop it")
    parser.add_argument("--degrees", type=float, default=20.0, help="where the move is going")
    args = parser.parse_args()

    server = Server(args.robot)
    try:
        server.ask(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "e216", "version": "1"},
            },
        )
        server.notify("notifications/initialized")
        print(server.call("robot_assess_task", verdict="feasible", reason="one short move"))

        began = time.monotonic()
        moved: dict[str, str] = {}

        def move() -> None:
            moved["said"] = server.call(
                "robot_run_verb",
                verb="move_joints",
                params={"positions": {"shoulder_pan": args.degrees}, "duration_s": 12},
            )
            moved["at"] = f"{time.monotonic() - began:.2f}"

        mover = threading.Thread(target=move)
        mover.start()
        time.sleep(args.after)
        stopped = server.call("robot_run_verb", verb="stop")
        print(f"stop answered at {time.monotonic() - began:.2f} s: {stopped}")
        mover.join(60)
        print(f"move_joints answered at {moved.get('at')} s: {moved.get('said')}")
    finally:
        server.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
