"""A stand-in for OpenRouter's API on 127.0.0.1, for the tests and for a keyless run by hand.

Standard library only, so it runs wherever the suite does. It records every request it is sent
(method, path, headers, body), serves `GET /api/v1/models` from a list it is given, and answers
each `POST /api/v1/chat/completions` with the next turn of a script, in the shape OpenRouter
documents: a tool call, `reasoning` and `reasoning_details` beside it, and a `usage` carrying
`cost`. Anything else is a 404, which is how a stray `/responses` request shows up.

It proves what quackd sends and what it makes of an answer shaped like OpenRouter's. It proves
nothing about OpenRouter, and nothing that passes against it has ever reached a real model.

By hand, from the repository root:

    python tests/fake_openrouter.py --port 8765 --record requests.jsonl --script hello-world

then point a run at `--base-url http://127.0.0.1:8765/api/v1` with any `OPENROUTER_API_KEY`.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

PREFIX = "/api/v1"

#: What OpenRouter's preflight answered on 2026-10-06, for the browser test.
CORS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET,OPTIONS,PATCH,DELETE,POST,PUT",
    "Access-Control-Allow-Headers": (
        "Authorization,User-Agent,Content-Type,HTTP-Referer,X-Title,X-OpenRouter-Title"
    ),
}


def model(
    model_id: str,
    *,
    tools: bool = True,
    tool_choice: bool = True,
    image: bool = True,
    prompt: str = "0.000002",
    completion: str = "0.00001",
    cache_read: str | None = "0.0000002",
    expires: str | None = None,
) -> dict[str, Any]:
    """One entry of the model list, in the shape `GET /api/v1/models` answers with."""
    supported = ["max_tokens", "temperature"]
    supported += ["tools"] if tools else []
    supported += ["tool_choice"] if tool_choice else []
    pricing = {"prompt": prompt, "completion": completion}
    if cache_read is not None:
        pricing["input_cache_read"] = cache_read
    return {
        "id": model_id,
        "canonical_slug": model_id,
        "name": f"Stand-in: {model_id}",
        "context_length": 131072,
        "architecture": {
            "modality": "text+image->text" if image else "text->text",
            "input_modalities": ["text", "image"] if image else ["text"],
            "output_modalities": ["text"],
        },
        "pricing": pricing,
        "supported_parameters": supported,
        "expiration_date": expires,
    }


#: The list a test runs against unless it hands in its own: one entry per way an id quackd does
#: not carry can be taken or refused. Every id is under an author no real list has.
MODELS: list[dict[str, Any]] = [
    model("quackd-stub/tool-model"),
    model("quackd-stub/tool-model:free", prompt="0", completion="0", cache_read=None),
    model("quackd-stub/no-tool-choice", tool_choice=False),
    model("quackd-stub/text-only", image=False),
    model("quackd-stub/no-tools", tools=False, tool_choice=False),
    model("quackd-stub/expired", expires="2000-01-01"),
    model("quackd-stub/variable", prompt="-1", completion="-1", cache_read=None),
]

#: Each script is the calls a pilot makes, in order, one per POST. The first is always the
#: verdict, which a run asks for before anything moves.
VERDICT = ("assess_task", {"verdict": "feasible", "reason": "a quack and a step fit this body"})
SCRIPTS: dict[str, list[tuple[str, dict[str, Any]]]] = {
    "hello-world": [
        VERDICT,
        ("quack", {"text": "hello!"}),
        ("walk", {"vx": 0.1, "duration_s": 1.0}),
        ("quack", {"text": "done"}),
        ("declare_success", {"reason": "quacked and walked one step"}),
    ],
    # past the eighth exchange, where a model that binds its thinking would see a trim
    "long": [
        VERDICT,
        *[("quack", {"text": f"quack {n}"}) for n in range(1, 12)],
        ("declare_success", {"reason": "quacked eleven times"}),
    ],
}

#: What each turn's `usage` carries, cycled through a script: an ordinary bill, a
#: bring-your-own-key bill with its upstream charge, an upstream figure on a turn that is NOT
#: BYOK (which must not be added), a turn with no `cost` at all, and a BYOK turn whose upstream
#: charge is missing. Distinct amounts, so a sum that took the wrong one is visibly wrong.
BILLS: list[dict[str, Any]] = [
    {"cost": 0.0011},
    {"cost": 0.0002, "is_byok": True, "cost_details": {"upstream_inference_cost": 0.003}},
    {"cost": 0.0013, "cost_details": {"upstream_inference_cost": 0.5}},
    {},
    {"cost": 0.0001, "is_byok": True},
]


def reasoning_details(n: int) -> list[dict[str, Any]]:
    """Both kinds a replay has to hand back untouched: Claude's signed text and Gemini's
    encrypted signature."""
    return [
        {
            "type": "reasoning.text",
            "text": f"turn {n}: the next verb",
            "signature": f"sig-{n}",
            "format": "anthropic-claude-v1",
            "index": 0,
        },
        {
            "type": "reasoning.encrypted",
            "data": f"enc-{n}",
            "id": f"rd-{n}",
            "format": "google-gemini-v1",
            "index": 1,
        },
    ]


def completion(
    n: int, name: str, arguments: dict[str, Any], model_id: str, bill: dict[str, Any]
) -> dict[str, Any]:
    """Turn `n` (from 1) as OpenRouter would answer it."""
    usage: dict[str, Any] = {
        "prompt_tokens": 1000 + n,
        "completion_tokens": 20,
        "total_tokens": 1020 + n,
        "prompt_tokens_details": {"cached_tokens": 100},
        "completion_tokens_details": {"reasoning_tokens": 5},
        **bill,
    }
    return {
        "id": f"gen-stand-in-{n}",
        "object": "chat.completion",
        "created": 1_791_000_000 + n,
        "model": model_id,
        "provider": "Stand-in",
        "choices": [
            {
                "index": 0,
                "finish_reason": "tool_calls",
                "message": {
                    "role": "assistant",
                    "content": None,
                    "refusal": None,
                    "reasoning": f"turn {n}: calling {name}",
                    "reasoning_details": reasoning_details(n),
                    "tool_calls": [
                        {
                            "id": f"call_{n}",
                            "type": "function",
                            "function": {"name": name, "arguments": json.dumps(arguments)},
                        }
                    ],
                },
            }
        ],
        "usage": usage,
    }


@dataclass
class Recorded:
    method: str
    path: str
    headers: dict[str, str]
    body: Any


@dataclass
class FakeOpenRouter:
    """The stand-in, as a context manager that serves on a free port in a daemon thread."""

    script: str | list[tuple[str, dict[str, Any]]] = "hello-world"
    models: list[dict[str, Any]] = field(default_factory=lambda: list(MODELS))
    models_body: Any = None
    """Served instead of `{"data": models}` when set: another shape, for the refusals."""
    chat: Callable[[int, dict[str, Any]], tuple[int, Any]] | None = None
    """Answers a POST instead of the script when set: `(turn, request body) -> (status, body)`."""
    record: Path | None = None
    port: int = 0
    """0 picks a free one, which is what a test wants."""
    requests: list[Recorded] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._turns = SCRIPTS[self.script] if isinstance(self.script, str) else self.script
        self._posts = 0
        self._lock = threading.Lock()
        self._server: ThreadingHTTPServer | None = None

    @property
    def base_url(self) -> str:
        assert self._server is not None, "start the stand-in first"
        return f"http://127.0.0.1:{self._server.server_address[1]}{PREFIX}"

    def posts(self) -> list[Recorded]:
        return [r for r in self.requests if r.method == "POST"]

    def gets(self) -> list[Recorded]:
        return [r for r in self.requests if r.method == "GET"]

    def _answer(self, method: str, path: str, body: Any) -> tuple[int, Any]:
        if method == "OPTIONS":
            return 204, None
        if method == "GET" and path == f"{PREFIX}/models":
            return 200, self.models_body if self.models_body is not None else {"data": self.models}
        if method == "POST" and path == f"{PREFIX}/chat/completions":
            with self._lock:
                self._posts += 1
                n = self._posts
            if self.chat is not None:
                return self.chat(n, body)
            if n > len(self._turns):
                return 500, {"error": {"code": 500, "message": "the stand-in's script ran out"}}
            name, arguments = self._turns[n - 1]
            bill = BILLS[(n - 1) % len(BILLS)]
            return 200, completion(n, name, arguments, str((body or {}).get("model")), bill)
        return 404, {"error": {"code": 404, "message": f"the stand-in has no {method} {path}"}}

    def __enter__(self) -> FakeOpenRouter:
        stand_in = self

        class Handler(BaseHTTPRequestHandler):
            def _serve(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                try:
                    body = json.loads(raw.decode("utf-8")) if raw else None
                except (UnicodeDecodeError, json.JSONDecodeError):
                    body = raw.decode("utf-8", "replace")
                path = self.path.split("?", 1)[0]
                seen = Recorded(
                    self.command, path, {k.lower(): v for k, v in self.headers.items()}, body
                )
                with stand_in._lock:
                    stand_in.requests.append(seen)
                    if stand_in.record is not None:
                        with stand_in.record.open("a", encoding="utf-8") as f:
                            f.write(json.dumps(seen.__dict__) + "\n")
                status, answer = stand_in._answer(self.command, path, body)
                payload = b"" if answer is None else json.dumps(answer).encode("utf-8")
                self.send_response(status)
                for key, value in CORS.items():
                    self.send_header(key, value)
                if answer is not None:
                    self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            do_GET = do_POST = do_OPTIONS = _serve

            def log_message(self, format: str, *args: Any) -> None:
                return None

        self._server = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        self._server.daemon_threads = True
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *exc: object) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--record", type=Path, default=None, help="append each request here")
    parser.add_argument("--script", choices=sorted(SCRIPTS), default="hello-world")
    args = parser.parse_args()
    stand_in = FakeOpenRouter(script=args.script, record=args.record, port=args.port)
    with stand_in:
        print(f"stand-in for OpenRouter at {stand_in.base_url} (Ctrl+C stops it)", flush=True)
        with contextlib.suppress(KeyboardInterrupt):
            threading.Event().wait()


if __name__ == "__main__":
    main()
