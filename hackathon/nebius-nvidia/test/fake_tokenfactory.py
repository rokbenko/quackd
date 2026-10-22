"""A stand-in for Nebius Token Factory on 127.0.0.1, for this folder's tests.

Standard library only. It records every request (method, path, query, headers, body), serves
`GET /v1/models` in the rich shape `verbose=true` documents, and answers each `POST
/v1/chat/completions` from a callable, a list of text answers, or a script of tool calls.

It proves what this edition sends and what it makes of an answer in Token Factory's documented
shape. It proves nothing about Token Factory, and nothing that passes against it has reached a
real model. It is modelled on quackd's own `tests/fake_openrouter.py`, copied rather than
imported, since quackd's tests are not part of the wheel this folder installs.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlsplit

PREFIX = "/v1"


def row(
    model_id: str,
    *,
    modality: str | None = "text->text",
    features: tuple[str, ...] = ("tools",),
    context: int = 131072,
) -> dict[str, Any]:
    """One entry of the model list, in the shape `GET /v1/models?verbose=true` documents."""
    entry: dict[str, Any] = {
        "id": model_id,
        "object": "model",
        "created": 1_790_000_000,
        "owned_by": "stand-in",
        "name": f"Stand-in: {model_id}",
        "description": "a row no real list has",
        "context_length": context,
        "pricing": {"prompt": "0.0000003", "completion": "0.0000009"},
        "supported_features": list(features),
        "status": "active",
    }
    if modality is not None:
        entry["architecture"] = {
            "modality": modality,
            "tokenizer": "stand-in",
            "instruct_type": None,
        }
    return entry


#: Every id is a stand-in's: two under NVIDIA's prefix, one text-only and one reading images,
#: and one under somebody else's, which the edition must never pick.
TEXT_MODEL = "nvidia/stand-in-nemotron-text"
VISION_MODEL = "nvidia/stand-in-nemotron-vl"
OTHER_MODEL = "stand-in-author/other-model"
MODELS: list[dict[str, Any]] = [
    row(TEXT_MODEL),
    row(VISION_MODEL, modality="text+image->text"),
    row(OTHER_MODEL),
]


def text_completion(n: int, content: str, model: str) -> dict[str, Any]:
    """Turn `n` (from 1) answered in words."""
    return {
        "id": f"chatcmpl-stand-in-{n}",
        "object": "chat.completion",
        "created": 1_790_000_000 + n,
        "model": model,
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": content},
            }
        ],
        "usage": {"prompt_tokens": 100 + n, "completion_tokens": 20, "total_tokens": 120 + n},
    }


def tool_completion(n: int, name: str, arguments: dict[str, Any], model: str) -> dict[str, Any]:
    """Turn `n` (from 1) answered with one tool call."""
    answer = text_completion(n, "", model)
    answer["choices"][0]["finish_reason"] = "tool_calls"
    answer["choices"][0]["message"] = {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": f"call_{n}",
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(arguments)},
            }
        ],
    }
    return answer


@dataclass
class Recorded:
    method: str
    path: str
    query: dict[str, list[str]]
    headers: dict[str, str]
    body: Any


@dataclass
class FakeTokenFactory:
    """The stand-in, as a context manager serving on a free port in a daemon thread."""

    key: str | None = "stand-in-key"
    """The one key it takes, or None to take any."""
    models: list[dict[str, Any]] = field(default_factory=lambda: list(MODELS))
    answers: list[str] = field(default_factory=list)
    """Text answers, one per POST, in order."""
    script: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    """Tool calls, one per POST, in order, used when there are no text answers."""
    chat: Callable[[int, dict[str, Any]], tuple[int, Any]] | None = None
    """Answers a POST instead of either list when set: `(turn, body) -> (status, body)`."""
    requests: list[Recorded] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._posts = 0
        self._lock = threading.Lock()
        self._server: ThreadingHTTPServer | None = None

    @property
    def base_url(self) -> str:
        assert self._server is not None, "start the stand-in first"
        return f"http://127.0.0.1:{self._server.server_address[1]}{PREFIX}"

    def posts(self) -> list[Recorded]:
        return [r for r in self.requests if r.method == "POST"]

    def _answer(self, seen: Recorded) -> tuple[int, Any]:
        if self.key is not None and seen.headers.get("authorization") != f"Bearer {self.key}":
            return 401, {"detail": "the stand-in does not know this key"}
        if seen.method == "GET" and seen.path == f"{PREFIX}/models":
            return 200, {"object": "list", "data": self.models}
        if seen.method == "POST" and seen.path == f"{PREFIX}/chat/completions":
            with self._lock:
                self._posts += 1
                n = self._posts
            body = seen.body if isinstance(seen.body, dict) else {}
            if self.chat is not None:
                return self.chat(n, body)
            model = str(body.get("model"))
            if self.answers:
                if n > len(self.answers):
                    return 500, {"detail": "the stand-in's answers ran out"}
                return 200, text_completion(n, self.answers[n - 1], model)
            if n > len(self.script):
                return 500, {"detail": "the stand-in's script ran out"}
            name, arguments = self.script[n - 1]
            return 200, tool_completion(n, name, arguments, model)
        return 404, {"detail": f"the stand-in has no {seen.method} {seen.path}"}

    def __enter__(self) -> FakeTokenFactory:
        stand_in = self

        class Handler(BaseHTTPRequestHandler):
            def _serve(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                try:
                    body = json.loads(raw.decode("utf-8")) if raw else None
                except (UnicodeDecodeError, json.JSONDecodeError):
                    body = raw.decode("utf-8", "replace")
                parts = urlsplit(self.path)
                seen = Recorded(
                    self.command,
                    parts.path,
                    parse_qs(parts.query),
                    {k.lower(): v for k, v in self.headers.items()},
                    body,
                )
                with stand_in._lock:
                    stand_in.requests.append(seen)
                status, answer = stand_in._answer(seen)
                payload = json.dumps(answer).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            do_GET = do_POST = _serve

            def log_message(self, format: str, *args: Any) -> None:
                return None

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *exc: object) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
