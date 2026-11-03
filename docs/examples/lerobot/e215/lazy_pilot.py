"""E215: a stand-in for a model that never says which way the gripper goes.

Standard library only. It listens on 127.0.0.1 and speaks the one request an OpenAI-compatible
pilot sends, so `quackd run ... --llm local:lazy --base-url http://127.0.0.1:8800/v1` flies with it.
It says the task is feasible, calls `gripper` with no arguments, then with a `null`, reads the arm,
and gives up. A vendor that drops a call's arguments sends what the first call sends, and until
0.30.2 that opened the jaws on whatever was in them.

Serve it in one terminal and run the task file in another:

    python lazy_pilot.py --port 8800

Everything it sends goes through the same gates a real model's does, and neither gripper call should
get past `params`. The worst it can do, were that wrong, is open the jaws, so hold what they hold.
"""

from __future__ import annotations

import argparse
import contextlib
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

TURNS: list[tuple[str, dict[str, Any]]] = [
    ("assess_task", {"verdict": "feasible", "reason": "it is only holding on", "needs": {}}),
    ("gripper", {}),
    ("gripper", {"open": None}),
    ("report_state", {}),
    ("declare_failure", {"reason": "I never did say which way the gripper was to go"}),
]


def answer(turn: int, body: dict[str, Any]) -> dict[str, Any]:
    """One chat completion: the next call, and the last one again for as long as it is asked."""
    name, arguments = TURNS[min(turn, len(TURNS)) - 1]
    call = {
        "id": f"call_{turn}",
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments)},
    }
    return {
        "id": f"lazy-{turn}",
        "object": "chat.completion",
        "created": 1,
        "model": str(body.get("model") or "lazy"),
        "choices": [
            {
                "index": 0,
                "finish_reason": "tool_calls",
                "message": {"role": "assistant", "content": None, "tool_calls": [call]},
            }
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


def serve(port: int) -> None:
    turns = {"n": 0}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            turns["n"] += 1
            print(f"turn {turns['n']}: {TURNS[min(turns['n'], len(TURNS)) - 1]}", flush=True)
            payload = json.dumps(answer(turns["n"], json.loads(raw or b"{}"))).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: Any) -> None:
            return None

    print(f"lazy pilot on http://127.0.0.1:{port}/v1. Ctrl+C stops it.", flush=True)
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    with contextlib.suppress(KeyboardInterrupt):
        server.serve_forever()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("--port", type=int, default=8800)
    serve(parser.parse_args().port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
