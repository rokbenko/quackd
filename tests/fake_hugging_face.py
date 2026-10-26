"""Stand-ins for Hugging Face on 127.0.0.1, for the tests and for a run by hand: the Inference
Endpoints API with the Hub and PyPI beside it, the proxy in front of an endpoint, and the
container behind it.

The standard library and quackd itself, nothing else, so it runs wherever the suite does:

- `FakeAPI` answers the routes `quackd policy endpoint` asks (`endpoint_upstream_api`): the
  provider listing with no token and every other route only with the account's token as
  `Authorization: Bearer`, whoami-v2, a repository's visibility and its raw `config.json`, and
  PyPI's JSON for a release. An endpoint it creates passes `pending`, then `initializing`, then
  `running`, one state further each time it is read, and starts its container when it
  reaches `running`; one scaled to zero wakes on a request to its proxy. It records every
  request.
- `FakeContainer` is what Hugging Face would run from the body an endpoint was created with. It
  asserts the `uvx` command installs this quackd's release with an `--exclude-newer`, parses the
  arguments with `quackd policy serve`'s own parser and builds its options with
  `cli._serve_options`, as the command does, checks them as the server would, and then serves
  `scripted:sweep` in place of the checkpoint, on 127.0.0.1, with the token the body's secrets
  carry, and says so: the checkpoint itself would need torch and a GPU.
- `FakeProxy` is what a client of an endpoint talks to: it wants the account's token as the
  bearer, answers 503 while the endpoint has no replica serving, and otherwise forwards each
  request to the server behind it, passing the client's `Authorization` header on or stripping
  it, as told. It records every request.

It proves what quackd sends and what it makes of answers shaped like the ones Hugging Face
documents. It proves nothing about Hugging Face: what the real proxy answers while a replica
starts, whether it passes `Authorization` on, and whether an endpoint pulls the image are
written down nowhere quackd could read (`endpoint_upstream_api`'s UNVERIFIED rows), and nothing
that passes against this has met a real endpoint.

By hand, from the repository root, to see what `quackd policy endpoint` prints against it:

    python tests/fake_hugging_face.py -- policy endpoint up arm-act --policy OWNER/NAME@REV --yes

Each `--` starts one more quackd command, run in turn against the same stand-ins, in a scratch
registry, memory and cache that are thrown away after, with a stand-in account token in
`HF_TOKEN`. That is how the quoted output in docs/guides/policies.md was made.
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import http.client
import io
import json
import logging
import os
import shlex
import sys
import tempfile
import threading
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

if __package__ in (None, ""):  # run by path, by hand: the repository root is what imports tests
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

HOP_BY_HOP = frozenset({"connection", "keep-alive", "transfer-encoding", "host", "content-length"})
"""Headers a proxy answers for itself rather than passing on."""
ACCOUNT_TOKEN = "hf_" + "StandInAccountToken" * 2
"""The stand-in account's token, shaped like a Hugging Face token, and nobody's."""
ACCOUNT = "rok"
UPLOADED = "2026-10-25T13:08:57.887490Z"
"""When the stand-in PyPI says this quackd's release was uploaded: quackd-lerobot 0.25.0's last
file, as PyPI said on 2026-10-25."""


@dataclass
class Recorded:
    """One request a stand-in was sent: its method, its path with any query, its headers by
    lower-case name, and its body."""

    method: str
    path: str
    headers: dict[str, str]
    body: bytes

    def json(self) -> Any:
        return json.loads(self.body.decode("utf-8")) if self.body else None


Reply = tuple[int, dict[str, str], bytes]
"""A status, the headers to send, and the body."""


def _json_reply(status: int, body: Any) -> Reply:
    return status, {"Content-Type": "application/json"}, json.dumps(body).encode("utf-8")


class _Stand(ThreadingHTTPServer):
    daemon_threads = True


def _listen(answer: Callable[[Recorded], Reply], record: Callable[[Recorded], None]) -> _Stand:
    """A server on a free port of 127.0.0.1 that records each request and sends what `answer`
    makes of it, on a daemon thread."""

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _serve(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""
            seen = Recorded(
                self.command, self.path, {k.lower(): v for k, v in self.headers.items()}, body
            )
            record(seen)
            status, headers, payload = answer(seen)
            self.send_response(status)
            for name, value in headers.items():
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        do_GET = do_POST = do_PUT = do_DELETE = _serve

        def log_message(self, format: str, *args: Any) -> None:
            return None

    server = _Stand(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, name="fake-hugging-face", daemon=True).start()
    return server


def _close(server: ThreadingHTTPServer | None) -> None:
    if server is not None:
        server.shutdown()
        server.server_close()


# ── the proxy ───────────────────────────────────────────────────────────────────────────


@dataclass
class FakeProxy:
    """Hugging Face's proxy in front of one endpoint, as a context manager on a free port.

    `token` is the account token it wants as the bearer, and a request without it is answered
    401 in the shape Hugging Face's own errors take, a JSON `error`. `backend` is the server
    behind it, `http://127.0.0.1:PORT`, or None while the endpoint has no replica serving, when
    every request is answered `down` (503, as the autoscaling page says, unless a test asks
    for 502). `forward_authorization` says whether the client's `Authorization` header reaches
    the server. `woken` is called on every request that arrives while there is no backend, which
    is how `FakeAPI` wakes an endpoint that scaled to zero. `answer`, when it returns a reply, is
    sent instead, for the refusals a test wants by hand."""

    token: str
    backend: str | None = None
    forward_authorization: bool = True
    down: int = 503
    woken: Callable[[], None] | None = None
    answer: Callable[[Recorded], Reply | None] | None = None
    requests: list[Recorded] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._lock = threading.Lock()
        self._server: ThreadingHTTPServer | None = None

    @property
    def url(self) -> str:
        assert self._server is not None, "start the stand-in first"
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    def _record(self, seen: Recorded) -> None:
        with self._lock:
            self.requests.append(seen)

    def _reply(self, seen: Recorded) -> Reply:
        if seen.headers.get("authorization") != f"Bearer {self.token}":
            return _json_reply(401, {"error": "Invalid credentials in Authorization header"})
        if self.answer is not None and (said := self.answer(seen)) is not None:
            return said
        backend = self.backend
        if backend is None:
            if self.woken is not None:
                self.woken()
            return self.down, {"Content-Type": "text/plain"}, b"Service Unavailable"
        return self._forward(backend, seen)

    def _forward(self, backend: str, seen: Recorded) -> Reply:
        parts = urlsplit(backend)
        conn = http.client.HTTPConnection(parts.hostname or "127.0.0.1", parts.port, timeout=30)
        headers = {
            name: value
            for name, value in seen.headers.items()
            if name not in HOP_BY_HOP and (self.forward_authorization or name != "authorization")
        }
        try:
            conn.request(seen.method, seen.path, body=seen.body or None, headers=headers)
            response = conn.getresponse()
            body = response.read()
            kept = {
                name: value
                for name, value in response.getheaders()
                if name.lower() not in HOP_BY_HOP
            }
            return response.status, kept, body
        except OSError:
            return 502, {"Content-Type": "text/plain"}, b"Bad Gateway"
        finally:
            conn.close()

    def __enter__(self) -> FakeProxy:
        self._server = _listen(self._reply, self._record)
        return self

    def __exit__(self, *exc: object) -> None:
        _close(self._server)
        self._server = None


# ── the container ───────────────────────────────────────────────────────────────────────


class FakeContainer:
    """The container an endpoint's body describes, standing in for one Hugging Face would run.

    It holds the body to what quackd's real container needs: a `uvx` that installs this
    quackd's release of the server's extra, `version`, with an `--exclude-newer`, and runs
    `quackd policy serve`. The arguments are parsed by that command's own parser and made into
    options by `cli._serve_options`, as the command makes them, and checked as the server checks
    them before it builds anything. Then, in place of the checkpoint, which would need torch and
    a GPU, it serves `scripted:sweep` with the serve flags it was given, on 127.0.0.1, with the
    token from the body's secrets, and its `/v1/policy` says so: the policy it names is
    `scripted:sweep`. `gpu` is what it says about a GPU, true as an endpoint's would be, and
    false for the test of an endpoint whose torch found none."""

    def __init__(self, payload: Mapping[str, Any], *, gpu: bool = True) -> None:
        import typer.main

        from quackd import cli
        from quackd_lerobot import __version__
        from quackd_lerobot.policy import protocol as wire
        from quackd_lerobot.policy import server

        model = payload["model"]
        command, args, held = list(model["command"]), list(model["args"]), model["secrets"]
        assert command[:3] == ["uvx", "--python", "3.12"], command
        assert command[-3:] == ["quackd", "policy", "serve"], command
        assert f"quackd[lerobot-vla]=={__version__}" in command, command
        assert f"quackd-lerobot[vla]=={__version__}" in command, command
        self.exclude_newer = command[command.index("--exclude-newer") + 1]
        datetime.fromisoformat(self.exclude_newer.replace("Z", "+00:00"))
        serve = typer.main.get_command(cli.app).commands["policy"].commands["serve"]  # type: ignore[attr-defined]
        self.options = cli._serve_options(**serve.make_context("serve", args).params)
        assert self.options.token_env == wire.TOKEN_ENV, self.options
        assert (self.options.bind, self.options.port) == ("0.0.0.0", wire.DEFAULT_PORT)
        assert self.options.behind_tls, self.options
        server.check_options(self.options)
        stand_in = dataclasses.replace(
            self.options,
            policy="scripted:sweep",
            pins=(),
            bind="127.0.0.1",
            port=0,
            token_env=None,
        )
        self.served = server.open_server(stand_in, token=held[wire.TOKEN_ENV])
        app = self.served.app
        app.info = app.info.model_copy(update={"gpu": gpu})
        self.log = [
            f"stand-in container: uvx installed quackd {__version__} as of {self.exclude_newer}",
            f"stand-in container: serving scripted:sweep in place of {self.options.policy}, "
            "which would need torch and a GPU",
        ]
        # what the server logs goes to the endpoint's log, as a container's would, and not to
        # the terminal of the run that reached it
        self._kept = _Kept(self.log)
        logging.getLogger("quackd.policy").addHandler(self._kept)

    @property
    def url(self) -> str:
        return self.served.url

    def close(self) -> None:
        logging.getLogger("quackd.policy").removeHandler(self._kept)
        self.served.close()


class _Kept(logging.Handler):
    """A container's own log, as lines."""

    def __init__(self, lines: list[str]) -> None:
        super().__init__(logging.INFO)
        self.lines = lines

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(f"{record.levelname} {record.getMessage()}")


# ── the API, the Hub and PyPI ───────────────────────────────────────────────────────────


def _compute(
    vendor: str, region: str, kind: str, accelerator: str, price: float, gpu_gb: int | None
) -> dict[str, Any]:
    return {
        "id": f"{vendor}-{region}-{kind}-x1",
        "accelerator": accelerator,
        "numAccelerators": 1,
        "memoryGb": 32.0,
        "gpuMemoryGb": gpu_gb,
        "instanceType": kind,
        "instanceSize": "x1",
        "architecture": kind,
        "status": "available",
        "pricePerHour": price,
        "quota": {"maxAccelerators": 0, "usedAccelerators": 0},
    }


def provider_listing() -> dict[str, Any]:
    """A provider listing in the shape `GET /v2/provider` answers, a few computes of the one it
    answered on 2026-10-25, at the prices it gave them."""
    return {
        "vendors": [
            {
                "name": "aws",
                "status": "available",
                "regions": [
                    {
                        "name": "us-east-1",
                        "label": "N. Virginia",
                        "status": "available",
                        "computes": [
                            _compute("aws", "us-east-1", "intel-spr", "cpu", 0.033, None),
                            _compute("aws", "us-east-1", "nvidia-t4", "gpu", 0.5, 16),
                            _compute("aws", "us-east-1", "nvidia-l4", "gpu", 0.8, 24),
                            _compute("aws", "us-east-1", "nvidia-a10g", "gpu", 1.0, 24),
                            _compute("aws", "us-east-1", "inf2", "neuron", 1.95, 32),
                        ],
                    },
                    {
                        "name": "eu-west-1",
                        "label": "Ireland",
                        "status": "available",
                        "computes": [
                            _compute("aws", "eu-west-1", "nvidia-t4", "gpu", 0.5, 16),
                            _compute("aws", "eu-west-1", "nvidia-a10g", "gpu", 1.0, 24),
                            {
                                **_compute("aws", "eu-west-1", "nvidia-a100", "gpu", 2.5, 80),
                                "status": "not_available",
                            },
                        ],
                    },
                ],
            }
        ]
    }


ACT_CONFIG: dict[str, Any] = {
    "type": "act",
    "input_features": {
        "observation.state": {"type": "STATE", "shape": [6]},
        "observation.images.front": {"type": "VISUAL", "shape": [3, 480, 640]},
    },
    "output_features": {"action": {"type": "ACTION", "shape": [6]}},
    "chunk_size": 100,
    "n_action_steps": 100,
}
"""A `config.json` in the shape an SO-101 ACT's takes, which `pipeline.check_config` serves."""


@dataclass
class FakeEndpoint:
    namespace: str
    name: str
    payload: dict[str, Any]
    proxy: FakeProxy
    state: str = "pending"
    ahead: list[str] = field(default_factory=list)
    """The states it passes on its way, one a read of it."""
    container: FakeContainer | None = None
    created: str = ""
    log: list[str] = field(default_factory=list)
    url: str | None = None
    """The address the API gives it, its proxy's unless told: one no stand-in serves, or `""`
    for none, for an endpoint a test puts there with `FakeAPI.seed`."""


@dataclass
class FakeAPI:
    """Hugging Face's Inference Endpoints API, the parts of the Hub `up` reads, and PyPI's JSON,
    on one free port of 127.0.0.1, as a context manager. Point `endpoint.API`, `endpoint.HUB`
    and `endpoint.PYPI` at `url`.

    `token` is the account's, wanted on every route but `/v2/provider` and PyPI's, and
    `hub_token` a second one the Hub also takes, for a gated checkpoint. `models` says a
    repository's visibility by name, `{"private": ..., "gated": ...}`, and None for one the Hub
    does not have; any other is public. `configs` gives a repository's `config.json`, an ACT's
    unless told. `releases` are the versions PyPI has, by project, this quackd's unless told.
    `quota` is how many GPUs of the kind asked the account may run, and `fail` has an endpoint
    reach `failed` where it would reach `running`. `gpu` is what each container says. `account`
    and `account_type` are whoami's answer."""

    token: str = ACCOUNT_TOKEN
    hub_token: str | None = None
    account: str = ACCOUNT
    account_type: str = "user"
    models: dict[str, dict[str, Any] | None] = field(default_factory=dict)
    configs: dict[str, Any] = field(default_factory=dict)
    releases: dict[str, list[str]] | None = None
    quota: int | None = 1
    fail: bool = False
    stuck: bool = False
    """An endpoint that stays `pending`, for the wait that times out."""
    refuse_create: int | None = None
    """A status every create is refused with, 402 for an account with no way to pay."""
    gpu: bool = True
    log: list[str] = field(default_factory=list)
    """The lines every endpoint's log starts with."""
    forward_authorization: bool = True
    requests: list[Recorded] = field(default_factory=list)
    endpoints: dict[tuple[str, str], FakeEndpoint] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._lock = threading.RLock()
        self._server: ThreadingHTTPServer | None = None

    @property
    def url(self) -> str:
        assert self._server is not None, "start the stand-in first"
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    @property
    def proxies(self) -> list[str]:
        """The address of every endpoint's proxy, which a test tells the client's rule is an
        endpoint's."""
        return [e.proxy.url for e in self.endpoints.values() if e.proxy._server is not None]

    def sent(self, path: str) -> list[Recorded]:
        """Every request whose path starts with `path`."""
        return [r for r in self.requests if r.path.startswith(path)]

    def _record(self, seen: Recorded) -> None:
        with self._lock:
            self.requests.append(seen)

    # ── the state machine ───────────────────────────────────────────────────────────────

    def _status(self, ep: FakeEndpoint) -> dict[str, Any]:
        return {
            "name": ep.name,
            "type": ep.payload.get("type"),
            "provider": ep.payload.get("provider"),
            "compute": ep.payload.get("compute"),
            "model": {**ep.payload.get("model", {}), "secrets": {}},
            "tags": ep.payload.get("tags", []),
            "status": {
                "state": ep.state,
                "url": ""
                if ep.state == "pending"
                else ep.url
                if ep.url is not None
                else ep.proxy.url,
                "message": f"Endpoint is {ep.state}",
                "createdAt": ep.created,
                "updatedAt": ep.created,
                "readyReplica": 1 if ep.state == "running" else 0,
                "targetReplica": 0 if ep.state in ("paused", "scaledToZero") else 1,
            },
        }

    def _start(self, ep: FakeEndpoint) -> None:
        try:
            ep.container = FakeContainer(ep.payload, gpu=self.gpu)
        except Exception as e:
            ep.state = "failed"
            ep.log.append(f"stand-in container failed to start: {type(e).__name__}: {e}")
            return
        ep.container.log[:0] = ep.log  # the endpoint's log goes on in the container's
        ep.log = ep.container.log
        ep.proxy.backend = ep.container.url

    def _stop(self, ep: FakeEndpoint) -> None:
        ep.proxy.backend = None
        if ep.container is not None:
            ep.container.close()
            ep.container = None

    def _advance(self, ep: FakeEndpoint) -> None:
        """One state further, after a read has said the last: `running` starts the container."""
        if not ep.ahead:
            return
        ep.state = ep.ahead.pop(0)
        if ep.state == "running":
            self._start(ep)

    def _wake(self, ep: FakeEndpoint) -> None:
        with self._lock:
            if ep.state == "scaledToZero":
                ep.state, ep.ahead = "initializing", ["running"]

    def seed(
        self,
        namespace: str,
        name: str,
        *,
        url: str,
        tags: Sequence[str] | None = None,
        mark: str | None = None,
        state: str = "running",
    ) -> FakeEndpoint:
        """An endpoint the API has that no `up` against it created, for a test of what a run
        finds: at `url`, an address no stand-in serves, or `""` for none, with quackd's tag and
        `mark`, the tag a record gives its endpoint (`endpoint.mark`), unless `tags` says
        otherwise, in `state`. Its proxy is never started: nothing should reach it."""
        from quackd_lerobot.policy import endpoint

        given = [endpoint.TAG, *([mark] if mark else [])] if tags is None else list(tags)
        payload = {"name": name, "type": "authenticated", "tags": given}
        ep = FakeEndpoint(namespace, name, payload, FakeProxy(self.token), state=state, url=url)
        with self._lock:
            self.endpoints[(namespace, name)] = ep
        return ep

    def _ahead(self) -> list[str]:
        if self.stuck:
            return []
        return ["initializing", "failed" if self.fail else "running"]

    # ── the routes ──────────────────────────────────────────────────────────────────────

    def _authorised(self, seen: Recorded, *tokens: str | None) -> bool:
        given = seen.headers.get("authorization")
        return any(given == f"Bearer {t}" for t in tokens if t)

    def _answer(self, seen: Recorded) -> Reply:
        parts = urlsplit(seen.path)
        path = [unquote(p) for p in parts.path.split("/") if p]
        query = {k: v[-1] for k, v in parse_qs(parts.query).items()}
        with self._lock:
            if path[:2] == ["v2", "provider"] and len(path) == 2:
                return _json_reply(200, provider_listing())
            if path[:1] == ["pypi"]:
                return self._pypi(path)
            hub = (path[:1] == ["api"] and path[1:2] == ["models"]) or "raw" in path[2:3]
            allowed = (self.token, self.hub_token) if hub else (self.token,)
            if not self._authorised(seen, *allowed):
                return _json_reply(401, {"error": "Invalid credentials in Authorization header"})
            if path == ["api", "whoami-v2"]:
                return _json_reply(200, {"type": self.account_type, "name": self.account})
            if path[:2] == ["api", "models"] and len(path) == 6 and path[4] == "revision":
                return self._model(f"{path[2]}/{path[3]}", path[5])
            if len(path) == 5 and path[2] == "raw":
                return self._raw(f"{path[0]}/{path[1]}", path[4])
            if path[:3] == ["v2", "provider", "quotas"] and len(path) == 4:
                return _json_reply(200, self._quotas())
            if path[:2] == ["v2", "endpoint"]:
                return self._endpoint(seen.method, path[2:], seen, query)
        return _json_reply(404, {"error": f"the stand-in has no {seen.method} {parts.path}"})

    def _pypi(self, path: list[str]) -> Reply:
        from quackd_lerobot import __version__

        releases = self.releases
        if releases is None:
            releases = {"quackd": [__version__], "quackd-lerobot": [__version__]}
        if len(path) == 4 and path[3] == "json" and path[2] in releases.get(path[1], []):
            files = [{"filename": f"{path[1]}-{path[2]}.tar.gz", "upload_time_iso_8601": UPLOADED}]
            return _json_reply(200, {"info": {"version": path[2]}, "urls": files})
        return _json_reply(404, {"message": "Not Found"})

    def _model(self, repo: str, revision: str) -> Reply:
        info = self.models.get(repo, {"private": False, "gated": False})
        if info is None:
            return _json_reply(404, {"error": "Repository Not Found"})
        return _json_reply(200, {"id": repo, "sha": revision, **info})

    def _raw(self, repo: str, filename: str) -> Reply:
        if filename != "config.json":
            return _json_reply(404, {"error": "Entry not found"})
        config = self.configs.get(repo, ACT_CONFIG)
        return 200, {"Content-Type": "text/plain"}, json.dumps(config).encode("utf-8")

    def _quotas(self) -> dict[str, Any]:
        running = sum(1 for e in self.endpoints.values() if e.state not in ("paused",))
        quotas = (
            []
            if self.quota is None
            else [
                {
                    "instanceType": kind,
                    "architecture": kind,
                    "maxAccelerators": self.quota,
                    "usedAccelerators": running,
                }
                for kind in ("nvidia-t4", "nvidia-l4", "nvidia-a10g")
            ]
        )
        return {"vendors": [{"name": "aws", "quotas": quotas}]}

    def _endpoint(
        self, method: str, path: list[str], seen: Recorded, query: dict[str, str]
    ) -> Reply:
        if len(path) == 1 and method == "POST":
            return self._create(path[0], seen.json())
        if len(path) == 1 and method == "GET":
            tags = set(query.get("tags", "").split(",")) - {""}
            items = [
                self._status(e)
                for (ns, _), e in self.endpoints.items()
                if ns == path[0] and (not tags or tags & set(e.payload.get("tags", [])))
            ]
            return _json_reply(200, {"items": items, "totalItems": len(items), "nextCursor": None})
        ep = self.endpoints.get((path[0], path[1])) if len(path) >= 2 else None
        if ep is None:
            return _json_reply(404, {"error": "Endpoint not found"})
        verb = path[2] if len(path) == 3 else None
        if verb is None and method == "GET":
            # said as it is, then one state further for the next read
            said = self._status(ep)
            self._advance(ep)
            return _json_reply(200, said)
        if verb is None and method == "DELETE":
            self._stop(ep)
            ep.proxy.__exit__()
            del self.endpoints[(path[0], path[1])]
            return _json_reply(200, {})
        if verb == "pause" and method == "POST":
            self._stop(ep)
            ep.state, ep.ahead = "paused", []
            return _json_reply(200, self._status(ep))
        if verb == "resume" and method == "POST":
            if ep.state == "running":
                return _json_reply(400, {"error": "Endpoint is already running"})
            ep.state, ep.ahead = "initializing", ["running"]
            return _json_reply(200, self._status(ep))
        if verb == "scale-to-zero" and method == "POST":
            self._stop(ep)
            ep.state, ep.ahead = "scaledToZero", []
            return _json_reply(200, self._status(ep))
        if verb == "logs" and method == "GET":
            tail = int(query.get("tail", "50"))
            return 200, {"Content-Type": "text/plain"}, "\n".join(ep.log[-tail:]).encode()
        return _json_reply(404, {"error": f"the stand-in has no {method} for that"})

    def _create(self, namespace: str, body: Any) -> Reply:
        if self.refuse_create is not None:
            return _json_reply(self.refuse_create, {"error": "no payment method on the account"})
        if not isinstance(body, dict):
            return _json_reply(400, {"error": "the body is not an endpoint"})
        missing = [k for k in ("name", "type", "provider", "compute", "model") if k not in body]
        if missing:
            return _json_reply(422, {"error": f"missing {', '.join(missing)}"})
        name = str(body["name"])
        if (namespace, name) in self.endpoints:
            return _json_reply(409, {"error": f"an endpoint called {name} already exists"})
        proxy = FakeProxy(self.token, forward_authorization=self.forward_authorization)
        proxy.__enter__()
        ep = FakeEndpoint(
            namespace,
            name,
            body,
            proxy,
            ahead=self._ahead(),
            created=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            log=list(self.log),
        )
        proxy.woken = lambda: self._wake(ep)
        self.endpoints[(namespace, name)] = ep
        return _json_reply(200, self._status(ep))

    def __enter__(self) -> FakeAPI:
        self._server = _listen(self._answer, self._record)
        return self

    def __exit__(self, *exc: object) -> None:
        for ep in list(self.endpoints.values()):
            self._stop(ep)
            ep.proxy.__exit__()
        _close(self._server)
        self._server = None


# ── pointing quackd at it ───────────────────────────────────────────────────────────────


def point(monkeypatch: Any, api: FakeAPI) -> None:
    """quackd's endpoint module pointed at `api` for one test: its three addresses, no wait
    between reads, and the client's rule for where the account's token may go as a bearer
    taking the stand-in proxies' addresses besides an endpoint's own, compared on the whole
    address. `monkeypatch` is pytest's, and puts all of it back."""
    from quackd_lerobot.policy import client, endpoint

    for name in ("API", "HUB", "PYPI"):
        monkeypatch.setattr(endpoint, name, api.url)
    monkeypatch.setattr(endpoint, "POLL_S", 0.0)
    real = client.is_endpoint_host
    monkeypatch.setattr(
        client, "is_endpoint_host", lambda url: url.strip().rstrip("/") in api.proxies or real(url)
    )


@contextlib.contextmanager
def pointed_at(api: FakeAPI, *, poll_s: float = 0.0) -> Iterator[None]:
    """quackd's endpoint module and client pointed at `api`, for as long as the block lasts: its
    three addresses, the rule for where the account's token may go as a bearer, taking the
    stand-in proxies' addresses besides an endpoint's own, and the wait between reads. What a
    test does with `monkeypatch`, for the run by hand."""
    from quackd_lerobot.policy import client, endpoint

    saved = (endpoint.API, endpoint.HUB, endpoint.PYPI, endpoint.POLL_S, client.is_endpoint_host)
    real = client.is_endpoint_host

    def is_endpoint_host(url: str) -> bool:
        return url.strip().rstrip("/") in api.proxies or real(url)

    endpoint.API = endpoint.HUB = endpoint.PYPI = api.url
    endpoint.POLL_S = poll_s
    client.is_endpoint_host = is_endpoint_host
    try:
        yield
    finally:
        endpoint.API, endpoint.HUB, endpoint.PYPI, endpoint.POLL_S = saved[:4]
        client.is_endpoint_host = saved[4]


def run_by_hand(commands: Sequence[Sequence[str]], *, poll_s: float, answer: str | None) -> int:
    """Each of `commands` as `quackd` would run it, against one set of stand-ins, in scratch
    directories that are thrown away after, and printed as a terminal shows it. `answer` is what
    a person at a terminal types to the question `up` asks, when there is one."""
    from quackd import cli

    with tempfile.TemporaryDirectory(prefix="quackd-fake-hf-") as scratch:
        # a home of its own, so nothing it writes lands in the real one, and every path it
        # prints reads as the pages name it, under ~/.quackd
        home = Path(scratch)
        os.environ["HOME"] = os.environ["USERPROFILE"] = str(home)
        os.environ["QUACKD_REGISTRY_DIR"] = str(home / ".quackd")
        os.environ["QUACKD_MEMORY_DIR"] = str(home / ".quackd" / "memory")
        os.environ["QUACKD_CACHE_DIR"] = str(home / ".quackd" / "cache")
        os.environ["HF_HOME"] = str(home / ".cache" / "huggingface")
        os.environ["HF_TOKEN"] = ACCOUNT_TOKEN
        os.environ.pop("HF_TOKEN_PATH", None)
        if answer is not None:
            cli._can_prompt = lambda: True  # type: ignore[assignment]
            sys.stdin = io.StringIO(answer + "\n")
        code = 0
        with FakeAPI() as api, pointed_at(api, poll_s=poll_s):
            for argv in commands:
                print(f"$ quackd {shlex.join(argv)}", flush=True)
                try:
                    said = cli.app(list(argv), prog_name="quackd", standalone_mode=False)
                    code = said if isinstance(said, int) and said else code
                except SystemExit as e:
                    code = int(e.code or 0) or code
                except Exception as e:  # click's own refusals, said as its usage line says them
                    print(f"{type(e).__name__}: {e}", flush=True)
                    code = int(getattr(e, "exit_code", 1) or 1)
                print(flush=True)
        return code


def main() -> None:
    raw = sys.argv[1:]
    cut = raw.index("--") if "--" in raw else len(raw)
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n", 1)[0])
    parser.add_argument("--poll-s", type=float, default=1.0, help="seconds between reads")
    parser.add_argument("--answer", default=None, help="what to type at up's question")
    args = parser.parse_args(raw[:cut])
    commands: list[list[str]] = []
    for part in raw[cut:]:
        if part == "--":
            commands.append([])
        else:
            commands[-1].append(part)
    commands = [c for c in commands if c]
    if not commands:
        parser.error("give a quackd command after --")
    raise SystemExit(run_by_hand(commands, poll_s=args.poll_s, answer=args.answer))


if __name__ == "__main__":
    main()
