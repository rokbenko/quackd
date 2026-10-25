"""`quackd policy serve` and `quackd policy check`: a policy in a process of its own.

**This is the first network service whose replies move an arm.** A checkpoint's processors are
code (`upstream_api.PROCESSOR_CLASS_IMPORT`), so no quackd command loads a checkpoint or runs
its inference in the process that owns the serial bus. Only `real.load_policy()` would, and
nothing in quackd calls it (`upstream_api.LOAD_POLICY`). The user starts this server, in a
terminal of its own on the laptop or on a rented GPU reached through `ssh -L`, the way a board's
daemon is started, and `client.py` reaches it with the protocol in `protocol.py`. It serves a
LeRobot checkpoint named `--policy REPO@REVISION`, loaded and run by `pipeline.py`
(`upstream_api.POLICY_PIPELINE`), and scripted policies (`--policy scripted:NAME`,
`scripted.py`), which need no torch.

It is a standard library `ThreadingHTTPServer` in the shape of the Jetson host daemon
(`bridge/jetson/quackd_jetson_hostd.py`), and its bounds are that daemon's, copied here as
named constants because a daemon under `bridge/` never imports quackd and quackd never imports
a daemon:

- **a token, always.** Read from one header and compared with `hmac.compare_digest`. With no
  `--token-file` the server writes one to `~/.quackd/policy.token`, readable by its owner alone
  where the OS allows, and the client on the same machine reads it from there.
- **loopback, unless told otherwise.** A bind to anything but `127.0.0.1` or `::1` is refused
  unless `--behind-tls` says a TLS proxy stands in front of the server: plain HTTP across a
  network would let anyone on it read a frame of the room and change a goal on its way to the
  arm, and the client sends plain HTTP to those two addresses alone.
- **a bound on everything a client can make it hold:** connections, the head, the body, and
  the time a request takes to arrive whole. A request without the token is answered on its
  head alone, and its body is never read.
- **one session at a time.** A reset starts a session and ends the one it replaces, and a
  step for an ended session is refused. A reset from another client is refused while the live
  session is in use (`session_lease_s`), so checking a server an arm is driving through never
  ends the arm's segment, and a client that is done ends its session (`POST /v1/end`). One lock
  guards the policy, so a reset and a step never run it at once, and a step sent twice (the
  client's retry on a stale socket) is answered from the first answer rather than inferred
  again.
- **a slow step holds up nothing but the next step.** A reset is answered at once, even while
  a step its client gave up on is still inferring: the policy's own reset waits for that step
  and runs before the new session's first one, and the old step's chunk is dropped as it ends.
  Another client's reset waits only until the step has outlived its client's patience
  (`ABANDONED_S`). Stopping the server refuses every request after it, and waits
  `CLOSE_WAIT_S` for such a step and no longer, since a step on a CPU can take minutes.
- **no SO_REUSEADDR on Windows**, where it would let a second server bind a port another is
  listening on and answer none of its requests.

`quackd policy check` asks a server what it serves and, with `--bench`, times one step on its
own, then streams synthetic observations at the policy's rate through the real client, and says
the rate it achieved, how often the arm would have had nothing to send, the round trip, and the
latency to declare with `--latency-s`, read at a high quantile of every step it timed
(`LATENCY_QUANTILE`). A rate is only ever measured on the wall's clock, and this is one of the
two places it is (the other is the bench).
"""

from __future__ import annotations

import concurrent.futures
import contextlib
import hmac
import io
import ipaddress
import logging
import math
import os
import re
import secrets
import socket
import statistics
import sys
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, TypeVar

import numpy as np
from pydantic import BaseModel

from quackd_lerobot import __version__
from quackd_lerobot.policy import pipeline
from quackd_lerobot.policy import protocol as wire
from quackd_lerobot.policy.client import STEP_TIMEOUT_S
from quackd_lerobot.policy.fit import (
    LATENCY_STEP_S,
    _ticks,
    chunk_outrun,
    chunk_starved,
    latency_too_long,
    longest_latency_s,
)
from quackd_lerobot.policy.loop import (
    EXACT,
    FIRST_CHUNK_S,
    latency_ticks,
    longest_latency,
    rate_refusal,
    refill_due,
)
from quackd_lerobot.policy.runner import Chunk, Features, Observation, PolicyRunner
from quackd_lerobot.policy.scripted import SCRIPTS, named

log = logging.getLogger("quackd.policy")
M = TypeVar("M", bound=BaseModel)

# The Jetson host daemon's bounds, copied as they are there (bridge/jetson/quackd_jetson_hostd.py)
REQUEST_TIMEOUT_S = 5.0
"""How long a connection may wait between one byte and the next. A client idle for longer, as
the arm's is between two segments, has its kept-alive connection closed, and opens another."""
REQUEST_DEADLINE_S = 10.0
"""How long one request may take to arrive whole, head and body, from when the connection
starts waiting for it. `REQUEST_TIMEOUT_S` bounds each wait, and a client that sends a byte
every four seconds never trips it, so this bounds all the waits together."""
MAX_HEAD_BYTES = 32 << 10
"""The most a request line and its headers may take. The client sends a few hundred bytes, and
http.server alone reads a hundred header lines of 64 KB before any code here runs."""
MAX_CONNECTIONS = 16
"""How many connections are served at once, each a thread and whatever it has read. A
connection past this is answered 503 and closed."""
LINGER_S = 2.0
"""How long a connection closed on a body it did not read waits for the client to finish
sending, dropping what arrives, before it closes anyway. See `_linger`."""
DRAIN_CHUNK_BYTES = 64 << 10
"""A body read only to be dropped is read this much at a time, never all at once."""

RUNNER_WAIT_S = REQUEST_DEADLINE_S
"""How long a step waits for the policy's lock, held by a step still inferring, before it is
refused as busy. The client asks one step at a time, so a wait at all is a retry of a step, or
the first step after a reset that came while a slow one was still inferring."""
CLOSE_WAIT_S = 1.0
"""How long stopping the server waits for a step still inferring to end before it closes the
policy anyway. A step on a CPU can take minutes, and a server being stopped answers nobody, so
the policy is closed under it: the step finishes with nobody to hear it, and every request
after the stop is refused, a step waiting its turn behind that one too."""
ABANDONED_S = STEP_TIMEOUT_S
"""How long a step may infer before nobody is waiting for it: its client gives up on a step
after this long (`client.STEP_TIMEOUT_S`), and the segment it was for ends there. Until then
another client's reset is refused, and after it the step no longer keeps its session in use,
so a new client is not locked out for as long as torch takes. The step's chunk is dropped as
it ends, since its session is over by then."""
BENCH_S = 10.0
"""How long `policy check --bench` streams observations for, unless it is told."""
BENCH_FRAME = (640, 480)
"""The width and height of a synthetic frame for a camera whose policy declares no size: the
mode a webcam most often starts in."""
BENCH_INSTRUCTION = "quackd policy check --bench"
"""What a bench's reset tells the policy, so a server's log says what the session was."""
LATENCY_QUANTILE = 0.95
"""The share of the steps a bench timed that the `--latency-s` it suggests covers. It is read
at this quantile of every step timed from its request to its chunk back, the warm one timed on
its own and each of the stream's, so most chunks land within the latency the simulator holds
each back by. One step timed alone is one draw, and two benches of one checkpoint on one laptop
suggested latencies too far apart to serve with either."""


class Refused(Exception):
    """A request the server will not serve, as the status and the sentence it answers with."""

    def __init__(self, status: int, reason: str) -> None:
        super().__init__(reason)
        self.status = status
        self.reason = reason


class ServeRefused(ValueError):
    """`policy serve` or `policy check` refusing to start, in one sentence that says what to do."""


# ── what is served ──────────────────────────────────────────────────────────────────────

SCRIPTED = "scripted:"
CHECKPOINT = pipeline.REPO_AT_REVISION
"""`owner/name@revision`: a Hub repository, always at a revision, so what a server loads cannot
change under it between two runs."""


@dataclass(frozen=True)
class ServeOptions:
    """Everything `quackd policy serve` takes, and `quackd policy check` when it serves the
    policy it checks itself. Each is as the flag gave it, and checked by `served_policy`."""

    policy: str
    fps: float | None = None
    cameras: str | None = None
    latency_s: float | None = None
    bind: str = "127.0.0.1"
    port: int = wire.DEFAULT_PORT
    token_file: str | None = None
    behind_tls: bool = False
    threads: int | None = None
    jpeg_quality: int | None = None
    pins: tuple[str, ...] = ()
    """`--pin REPO@REVISION`, each a model the checkpoint names inside itself, at the revision
    to fetch it at, a commit or a tag (`pipeline.pinned_models`, `pipeline.check_fixed`)."""


def parse_cameras(text: str | None) -> dict[str, str]:
    """`--cameras NAME=KEY,...`: which of the arm's cameras is which of the policy's images."""
    if text is None or not text.strip():
        return {}
    mapped: dict[str, str] = {}
    for pair in text.split(","):
        name, sep, key = (part.strip() for part in pair.partition("="))
        if not sep or not re.match(wire.NAME_PATTERN, name) or not re.match(wire.NAME_PATTERN, key):
            raise ServeRefused(
                f"--cameras takes NAME=KEY pairs separated by commas, each the arm's camera name "
                f"and the policy's image key (front=observation.images.front), not {pair!r}"
            )
        if name in mapped:
            raise ServeRefused(f"--cameras maps the {name} camera twice")
        mapped[name] = key
    if len(mapped) > wire.MAX_CAMERAS:
        raise ServeRefused(f"--cameras maps {len(mapped)} cameras, and at most {wire.MAX_CAMERAS}")
    return mapped


def bind_refusal(bind: str, behind_tls: bool) -> str | None:
    """Why the server must not listen on `bind`, or None. `127.0.0.1` and `::1`, the two
    addresses the client sends plain http to (`protocol.LOOPBACK`), need nothing; any other
    needs `--behind-tls`, the rest of 127/8 included, since no client could reach a server
    there without the TLS proxy in front of it; and a name needs writing as an address."""
    if bind.strip().lower() == "localhost":
        return (
            "--bind localhost: write 127.0.0.1 (or ::1) instead, the address the client is "
            "pointed at, since a name is looked up and localhost may not be the one you meant"
        )
    try:
        address = ipaddress.ip_address(bind)
    except ValueError:
        return f"--bind takes an address, such as 127.0.0.1, and not {bind!r}"
    if bind in wire.LOOPBACK or behind_tls:
        return None
    if address.is_loopback:
        return (
            f"--bind {bind} is a loopback address no quackd client would reach, since the client "
            "sends plain http to 127.0.0.1 and ::1 alone: bind 127.0.0.1 or ::1 instead"
        )
    return (
        f"--bind {bind} would serve a policy whose answers move an arm, and frames of the room "
        "it is in, in plain HTTP to that whole network. Keep --bind 127.0.0.1 and reach it "
        "through ssh -L from the laptop, or put a TLS proxy in front of it and pass --behind-tls"
    )


def served_policy(options: ServeOptions) -> tuple[PolicyRunner, wire.PolicyInfo]:
    """The runner `options` names and what the server will say it is serving, or a
    `ServeRefused` for anything that would make a server not worth starting."""
    spec = options.policy.strip()
    name = spec.removeprefix(SCRIPTED)
    checkpoint = bool(CHECKPOINT.match(spec))
    if spec.startswith(SCRIPTED):
        if name not in SCRIPTS:
            raise ServeRefused(
                f"there is no scripted policy called {name!r}: the scripted ones are "
                + ", ".join(f"{SCRIPTED}{n} ({what})" for n, what in SCRIPTS.items())
            )
    elif checkpoint:
        if len(spec) > pipeline.MAX_SPEC_CHARS:
            raise ServeRefused(f"--policy {spec[:60]}... is longer than a Hub id and a revision")
    elif "/" in spec and "@" not in spec:
        raise ServeRefused(
            f"{spec} has no revision: name a checkpoint as REPO@REVISION, a commit or a tag, so "
            "what is served cannot change under you between two runs"
        )
    else:
        raise ServeRefused(
            f"--policy takes REPO@REVISION or scripted:NAME, not {spec!r}. The scripted ones are "
            + ", ".join(f"{SCRIPTED}{n}" for n in SCRIPTS)
        )
    if (
        options.fps is not None
        and (refusal := rate_refusal(Features(options.fps, "--fps"))) is not None
    ):
        raise ServeRefused(refusal.replace("the policy was not started", "not serving"))
    latency = 0.0 if options.latency_s is None else options.latency_s
    if not (math.isfinite(latency) and 0 <= latency < wire.MAX_LATENCY_S):
        raise ServeRefused(
            f"--latency-s {latency!r} is not a number of seconds, 0 or more and under "
            f"{wire.MAX_LATENCY_S:g}: it is how long the policy takes to answer a step, and a "
            f"segment waits {wire.MAX_LATENCY_S:g} s for its first chunk before it gives up"
        )
    if options.threads is not None and options.threads < 1:
        raise ServeRefused(f"--threads {options.threads} is not a number of threads")
    quality = options.jpeg_quality
    if quality is None and options.behind_tls:
        quality = wire.DEFAULT_JPEG_QUALITY
    if quality is not None and not wire.MIN_JPEG_QUALITY <= quality <= 100:
        raise ServeRefused(
            f"--jpeg-quality {quality} is outside {wire.MIN_JPEG_QUALITY} to 100: below that a "
            "policy sees artefacts it never saw in training"
        )
    cameras = parse_cameras(options.cameras)
    if options.pins and not checkpoint:
        raise ServeRefused(
            "--pin fixes the revision of a model a checkpoint names inside itself, and a "
            "scripted policy names none"
        )
    runner: PolicyRunner
    loaded: pipeline.LeRobotRunner | None = None
    if checkpoint:
        try:
            loaded = pipeline.load(
                spec,
                fps=options.fps,
                pins=options.pins,
                cameras=cameras,
                threads=options.threads,
                latency_s=latency,
            )
        except pipeline.PipelineRefused as e:
            raise ServeRefused(str(e)) from None
        runner, spec, cameras = loaded, loaded.checkpoint.spec, loaded.cameras
        chunk = loaded.chunk
    else:
        scripted = named(name, rate_hz=options.fps)
        runner, chunk = scripted, scripted.chunk or 1
    features = runner.features()
    if (refusal := rate_refusal(features)) is not None:
        runner.close()
        raise ServeRefused(refusal.replace("the policy was not started", "not serving"))
    rate = float(features.rate_hz)
    if (why := latency_too_long(latency, rate, chunk, features.per_tick)) is not None:
        runner.close()
        raise ServeRefused(
            f"--latency-s {latency:g} is {_ticks(latency_ticks(latency, rate))} at {rate:g} "
            f"Hz, and each chunk of {spec} holds {chunk} actions, one a tick, so {why}. Give a "
            f"--latency-s of at most {longest_latency_s(rate, chunk):g} s, or serve the policy "
            "where it answers faster"
        )
    info = wire.PolicyInfo(
        protocol=wire.PROTOCOL,
        protocol_version=wire.PROTOCOL_VERSION,
        server_version=__version__,
        policy=spec,
        features=loaded.policy_features() if loaded else wire.PolicyFeatures(),
        rate_hz=float(features.rate_hz),
        rate_source=features.rate_source,
        chunk_size=loaded.chunk_size if loaded else chunk,
        n_action_steps=chunk,
        per_tick=features.per_tick,
        gpu=loaded.gpu if loaded else False,
        state_quantiles=loaded.state_quantiles if loaded else None,
        action_quantiles=loaded.action_quantiles if loaded else None,
        latency_s=float(latency),
        threads=loaded.threads if loaded else options.threads,
        jpeg_quality=quality,
        cameras=cameras,
        loaded=list(loaded.loaded) if loaded else [],
    )
    return runner, info


# ── the token ───────────────────────────────────────────────────────────────────────────


def _read_token(path: Path, named_by: str) -> str:
    try:
        token = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError) as e:
        raise ServeRefused(
            f"cannot read the token file {path} ({named_by}): {e}. Refusing to serve a policy "
            "with no token"
        ) from None
    if not token:
        raise ServeRefused(
            f"the token file {path} ({named_by}) is empty: write a token into it, or delete it "
            "and quackd policy serve writes a new one. Refusing to serve a policy with no token"
        )
    try:
        return wire.clean_token(token, f"the token file {path} ({named_by})")
    except ValueError as e:
        raise ServeRefused(f"{e}. Refusing to serve a policy with that token") from None


def server_token(token_file: str | None) -> tuple[str, Path, bool]:
    """The token the server wants, where it lives, and whether it was written just now.

    A named `--token-file` must hold one: a missing, unreadable or empty file refuses to start
    rather than serve with no token. With none named, the default file is read when it is
    there and written when it is not, readable by its owner alone where the OS allows (on
    Windows the file takes the permissions of the profile it sits in, which is the owner's)."""
    if token_file:
        path = Path(os.path.expanduser(token_file))
        return _read_token(path, "--token-file"), path, False
    path = Path(os.path.expanduser(wire.DEFAULT_TOKEN_FILE))
    if path.exists():
        return _read_token(path, "the default"), path, False
    token = secrets.token_hex(32)
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(token + "\n")
    except OSError as e:
        raise ServeRefused(
            f"cannot write a token to {path}: {e}. Give quackd policy serve --token-file with a "
            "file you wrote a token into"
        ) from None
    return token, path, True


# ── the server's state ──────────────────────────────────────────────────────────────────


def session_lease_s(info: wire.PolicyInfo) -> float:
    """How long a session stays in use after its last request, for the policy `info` describes:
    one chunk's span on the arm's clock, and the loop's grace for a first chunk. A segment asks
    again before its chunk runs out, or ends on its patience, which is never longer than that
    grace, so a session quiet for longer has no segment driving through it."""
    return info.n_action_steps / info.rate_hz + FIRST_CHUNK_S


@dataclass
class _Session:
    id: str
    motors: tuple[str, ...]
    cameras: dict[str, wire.CameraInfo]
    seen: float
    """When the session last had a request answered, on `time.monotonic`'s clock."""
    replaced: str | None = None
    """The session the reset that made this one said its client held. When that reset's reply
    is lost, its client still holds that id, and it is the only one that could: ids are the
    server's secret random hex and never printed. So a reset or an end naming it is this
    session's own client's, and is served however recently the session was used."""
    instruction: str = ""
    """What the reset that made it told the policy, for the policy's own reset (`_shape`)."""
    last_seq: int = 0
    last_reply: wire.StepReply | None = None
    done: bool = False
    asked: float | None = None
    """When the step of this session's that is inferring was asked, on `time.monotonic`'s
    clock, or None with none inferring. Such a step keeps the session in use until it has
    outlived its client's patience (`ABANDONED_S`)."""

    def owned_by(self, session_id: str) -> bool:
        """Whether a client holding `session_id` is this session's own: it holds this one, or
        the one this replaced, from a reset whose reply it never had."""
        return session_id == self.id or (self.replaced is not None and session_id == self.replaced)


class PolicyServer:
    """What the HTTP handler serves: one policy, its description, and the current session.

    `runner` is only ever called under `_runner_lock`, and `_lock` guards which session is the
    current one. Both are held briefly except the first, which a step holds for its inference.
    A reset takes the second alone, so it is answered however long a step is taking, and the
    runner is reset for its session when the first is next free (`_shape`)."""

    def __init__(self, runner: PolicyRunner, info: wire.PolicyInfo, token: str) -> None:
        self.token = wire.clean_token(token, "the server's caller")
        self.runner = runner
        self.info = info
        self.lease_s = session_lease_s(info)
        self._lock = threading.Lock()
        self._runner_lock = threading.Lock()
        self._session: _Session | None = None
        self._shaped: str | None = None
        """The session the runner was last reset for, read and written under `_runner_lock`."""
        self._closed = False
        """Set under `_lock` by `close`, after which every reset and step is refused."""
        self.steps = 0
        self.resets = 0

    def authorised(self, given: str | None) -> bool:
        if not given:
            return False
        return hmac.compare_digest(given.encode("utf-8"), self.token.encode("utf-8"))

    def close(self) -> None:
        """Refuse every request from now on, and close the policy once a step still inferring
        has ended or `CLOSE_WAIT_S` has passed, whichever is first: never a wait on the
        inference itself, which on a CPU can outlast anybody's patience with a server they
        asked to stop. A step waiting its turn behind that one, or sent on a connection kept
        alive past the stop, is refused rather than run on a policy that is closed."""
        with self._lock:
            self._closed = True
            self._session = None
        held = self._runner_lock.acquire(timeout=CLOSE_WAIT_S)
        try:
            with contextlib.suppress(Exception):
                self.runner.close()
        finally:
            if held:
                self._runner_lock.release()

    # ── the calls ───────────────────────────────────────────────────────────────────────

    def policy(self) -> wire.PolicyInfo:
        return self.info

    def reset(self, request: wire.ResetRequest) -> wire.ResetReply:
        """Start a session in place of the last, and answer at once. The policy is reset for it
        now when no step is inferring, and otherwise by the first step of the new session,
        once the step inferring has ended (`_shape`): that step is most often one its client
        gave up waiting for, and a reset that waited for it would outlast the client's patience
        too, and every reset after it, until the step ended."""
        self._refuse_another_arm(request)
        session = _Session(
            id=secrets.token_hex(16),
            motors=tuple(request.motors),
            cameras={camera.name: camera for camera in request.cameras},
            seen=time.monotonic(),
            replaced=request.replaces,
            instruction=request.instruction,
        )
        with self._lock:
            self._refuse_if_closed()
            self._refuse_a_second_client(request.replaces)
            self._session = session
            self.resets += 1
        if self._runner_lock.acquire(blocking=False):
            try:
                self._shape(session)
            finally:
                self._runner_lock.release()
        return wire.ResetReply(session=session.id)

    def end(self, request: wire.EndRequest) -> wire.EndReply:
        with self._lock:
            live = self._session is not None and self._session.owned_by(request.session)
            if live:
                self._session = None
        return wire.EndReply(ended=live)

    def step(self, request: wire.StepRequest) -> wire.StepReply:
        asked = time.monotonic()
        session = self._current(request.session)
        with self._runner():
            with self._lock:
                self._refuse_if_closed()
                if self._session is not session:
                    raise Refused(409, "that session ended while this step waited: reset again")
                if request.seq == session.last_seq and session.last_reply is not None:
                    session.seen = time.monotonic()
                    return session.last_reply  # a step sent again: the answer it already had
                if request.seq <= session.last_seq:
                    raise Refused(
                        409,
                        f"step {request.seq} is older than step {session.last_seq}, which this "
                        "session has already answered",
                    )
                session.asked = asked
            try:
                observation = self._observation(session, request)
                if not self._shape(session):
                    raise Refused(409, "that session ended while this step waited: reset again")
                started = time.perf_counter()
                chunk = None if session.done else self._ask(session, observation, request.sent)
                inferred = time.perf_counter() - started
            finally:
                with self._lock:
                    session.asked = None
                    session.seen = time.monotonic()
                    stale = self._session is not session
            if stale:
                with self._lock:
                    self._refuse_if_closed()  # stopped while the policy inferred
                # reset or ended while the policy inferred, most often by a client that gave up
                # waiting for this step: its chunk is for a segment that is over, and is dropped
                raise Refused(
                    409,
                    "that session was reset or ended while this step was inferring, so its "
                    "chunk was dropped: reset again",
                )
            reply = wire.StepReply(
                session=session.id, seq=request.seq, chunk=chunk, inference_s=inferred
            )
            session.last_seq, session.last_reply = request.seq, reply
            self.steps += 1
            return reply

    # ── the parts ───────────────────────────────────────────────────────────────────────

    @contextlib.contextmanager
    def _runner(self) -> Any:
        if not self._runner_lock.acquire(timeout=RUNNER_WAIT_S):
            raise Refused(
                503, f"busy: the policy was still answering a step after {RUNNER_WAIT_S:g} s"
            )
        try:
            yield
        finally:
            self._runner_lock.release()

    def _shape(self, session: _Session) -> bool:
        """Reset the policy for `session`, under `_runner_lock`, unless it already is: its
        queue and its processors cleared, told the instruction, and a checkpoint's runner
        shaped for the arm the reset declared, its motors in its bus's order and its cameras
        (`pipeline.LeRobotRunner.begin`). False, with nothing reset, for a session that is no
        longer the current one, since the reset that replaced it shapes the runner in its turn.
        Refused once the server is stopping, since a runner's `begin` would undo its close."""
        with self._lock:
            self._refuse_if_closed()
            if self._session is not session:
                return False
        if self._shaped == session.id:
            return True
        try:
            begin = getattr(self.runner, "begin", None)
            if callable(begin):
                begin(session.motors, session.cameras)
            self.runner.reset(session.instruction)
        except Exception as e:
            self._shaped = None
            raise Refused(500, f"the policy's reset raised {type(e).__name__}: {e}") from None
        self._shaped = session.id
        return True

    def _refuse_if_closed(self) -> None:
        """Refuse a request that came after `close`. Called under `_lock`."""
        if self._closed:
            raise Refused(
                503,
                "the policy server is stopping and serves nothing more: start quackd policy "
                "serve again, and reset",
            )

    def _current(self, session_id: str) -> _Session:
        with self._lock:
            self._refuse_if_closed()
            session = self._session
        if session is None or session.id != session_id:
            raise Refused(
                409, "that session is over: it was ended or replaced, or none was started"
            )
        return session

    def _refuse_another_arm(self, request: wire.ResetRequest) -> None:
        """Refuse a reset from an arm the policy cannot drive: one with another number of
        motors than it learned from, or without a camera for an image it needs. The arm's own
        connect refuses both first (`fit.py`), so this is for a client that did not ask. A
        policy that pads a missing image (`PolicyFeatures.pads_images`) needs one camera of its
        own and no more, and a scripted one needs every camera `--cameras` maps."""
        features = self.info.features
        declared = {camera.name for camera in request.cameras}
        if features.state is not None and len(request.motors) != features.state:
            raise Refused(
                400,
                f"the arm declared {len(request.motors)} motors, and {self.info.policy} takes a "
                f"state of {features.state}: it learned from another arm",
            )
        if not features.images:
            for name, key in self.info.cameras.items():
                if name not in declared:
                    raise Refused(
                        400,
                        f"this server maps the {name} camera to {key}, and the arm declared "
                        f"{', '.join(sorted(declared)) or 'no camera'}: give the arm that camera, "
                        "or start the server with --cameras naming the arm's",
                    )
            return
        seen = {key for name, key in self.info.cameras.items() if name in declared}
        unseen = [image.key for image in features.images if image.key not in seen]
        if unseen and (not features.pads_images or len(unseen) == len(features.images)):
            raise Refused(
                400,
                f"{self.info.policy} looks at {', '.join(unseen)}, and no camera the arm declared "
                f"({', '.join(sorted(declared)) or 'none'}) is mapped to it: give the arm that "
                "camera, or start the server with --cameras NAME=KEY naming one it has",
            )

    def _refuse_a_second_client(self, replaces: str | None) -> None:
        """Refuse a reset that would end a session another client is still using. Called under
        `_lock`, so no step can start or end between the look and the reset. The session's own
        client replaces it freely, even one whose last reset's reply was lost
        (`_Session.replaced`). Anyone else waits out the lease after its last answered request,
        and a step of its still inferring until that step has outlived its client's patience
        (`ABANDONED_S`): past that nobody is waiting for the step, and a server whose policy
        takes minutes a step would otherwise turn every new client away for as long."""
        live = self._session
        if live is None or (replaces is not None and live.owned_by(replaces)):
            return
        now = time.monotonic()
        quiet = now - live.seen
        inferring = None if live.asked is None else now - live.asked
        waited = inferring is not None and inferring < ABANDONED_S
        if not waited and quiet >= self.lease_s:
            return
        if inferring is not None and waited:
            # in so long, its client has given up on the step: said short, since a client
            # shows the first 200 characters of a refusal
            left = max(ABANDONED_S - inferring, self.lease_s - quiet)
            last, again = (
                f"a step of its inferring for {inferring:.1f} s",
                f"in {math.ceil(left)} s",
            )
        else:
            last, again = (
                f"its last request {quiet:.1f} s ago",
                f"once it has been quiet {math.ceil(self.lease_s)} s",
            )
        raise Refused(
            409,
            f"another client's session is live, {last}: a reset would end a segment an arm may "
            f"be driving through it. Try again {again}, or serve on another --port",
        )

    def _observation(self, session: _Session, request: wire.StepRequest) -> Observation:
        """The step as the runner sees it: `<motor>.pos` for every motor and each camera's frame
        under its name, the reading the arm itself would give. Anything that is not what the
        session's reset declared is refused."""
        if len(request.state) != len(session.motors):
            raise Refused(
                400,
                f"the state has {len(request.state)} numbers, and the session's arm has "
                f"{len(session.motors)} motors",
            )
        unknown = sorted(set(request.sent) - set(session.motors))
        if unknown:
            raise Refused(400, f"the command sent names {', '.join(unknown)}, not motors here")
        reading: dict[str, Any] = {
            f"{motor}.pos": value
            for motor, value in zip(session.motors, request.state, strict=True)
        }
        names = [frame.name for frame in request.frames]
        if sorted(names) != sorted(session.cameras):
            raise Refused(
                400,
                f"the step has frames from {', '.join(sorted(names)) or 'no camera'}, and the "
                f"session declared {', '.join(sorted(session.cameras)) or 'no camera'}",
            )
        want = "raw" if self.info.jpeg_quality is None else "jpeg"
        for frame in request.frames:
            if frame.encoding != want:
                raise Refused(
                    400,
                    f"the {frame.name} camera's frame is {frame.encoding}, and this server asks "
                    f"for {want} frames ({wire.POLICY_PATH}'s jpeg_quality)",
                )
            try:
                reading[frame.name] = wire.decode_frame(frame, session.cameras[frame.name])
            except wire.ProtocolError as e:
                raise Refused(400, str(e)) from None
        return Observation(request.tick, reading)

    def _ask(
        self, session: _Session, observation: Observation, sent: Mapping[str, float]
    ) -> list[dict[str, float]] | None:
        """The runner's chunk for `observation`, cut to `n_action_steps`, as the reply carries
        it, or None once the policy has said it is done. Whatever the runner answers is checked
        here before it is sent, so this server never sends a goal the protocol would refuse."""
        try:
            answer = self.runner.next_chunk(observation, dict(sent))
        except Exception as e:
            raise Refused(500, f"the policy raised {type(e).__name__}: {e}") from None
        if not isinstance(answer, Chunk) or answer.tick != observation.tick:
            raise Refused(500, "the policy answered something that is not a chunk for this tick")
        if answer.done:
            session.done = True
            if not answer.actions:
                return None
        actions: list[dict[str, float]] = []
        for action in answer.actions[: self.info.n_action_steps]:
            if not isinstance(action, Mapping) or not action:
                raise Refused(500, f"the policy answered an action that names no motor: {action!r}")
            goals: dict[str, float] = {}
            for key, value in action.items():
                if str(key) not in session.motors:
                    raise Refused(500, f"the policy answered a goal for {key!r}, not a motor here")
                if isinstance(value, bool) or not isinstance(value, int | float | np.number):
                    raise Refused(500, f"the policy answered {key}={value!r}, not a number")
                if not math.isfinite(float(value)):
                    raise Refused(
                        500, f"the policy answered {key}={value!r}, which is not a finite number"
                    )
                goals[str(key)] = float(value)
            actions.append(goals)
        return actions


# ── the wire ────────────────────────────────────────────────────────────────────────────


class _Cutoff(TimeoutError):
    """A request cut off for taking too long or growing too large. A TimeoutError, because that
    is what http.server treats as "discard this connection", without a traceback."""


class _Reader(io.RawIOBase):
    """The socket as a request reads it, with one deadline and one allowance per request, as in
    the Jetson host daemon: a request has to arrive whole by `REQUEST_DEADLINE_S`, and may send
    a head of `MAX_HEAD_BYTES` and whatever body the handler chose to read, and nothing more."""

    def __init__(self, sock: socket.socket, timeout: float | None) -> None:
        super().__init__()
        self._sock = sock
        self._timeout = timeout
        self._deadline = 0.0
        self._allowed = 0

    def begin(self) -> None:
        self._deadline = time.monotonic() + REQUEST_DEADLINE_S
        self._allowed = MAX_HEAD_BYTES

    def allow(self, n: int) -> None:
        self._allowed += n

    def readable(self) -> bool:
        return True

    def readinto(self, buffer: Any) -> int:
        left = self._deadline - time.monotonic()
        if left <= 0:
            raise _Cutoff(f"the request did not arrive whole within {REQUEST_DEADLINE_S:g}s")
        if self._allowed <= 0:
            raise _Cutoff("the request is longer than this server reads")
        self._sock.settimeout(left if self._timeout is None else min(self._timeout, left))
        try:
            got = self._sock.recv_into(memoryview(buffer)[: self._allowed])
        finally:
            self._sock.settimeout(self._timeout)
        self._allowed -= got
        return got


def _linger(sock: socket.socket) -> None:
    """Close a connection whose request body was left unread without resetting the reply away:
    an end of stream after the reply, and what the client still sends read and dropped until it
    closes, for `LINGER_S` at most."""
    end = time.monotonic() + LINGER_S
    with contextlib.suppress(OSError):
        sock.shutdown(socket.SHUT_WR)
        while (left := end - time.monotonic()) > 0:
            sock.settimeout(left)
            if not sock.recv(DRAIN_CHUNK_BYTES):
                return


GET_PATHS = frozenset({wire.POLICY_PATH})
POST_PATHS = frozenset({wire.RESET_PATH, wire.STEP_PATH, wire.END_PATH})


def make_handler(app: PolicyServer) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = f"quackd-policy/{__version__}"
        protocol_version = "HTTP/1.1"
        timeout = REQUEST_TIMEOUT_S
        _consumed = False
        _linger_on_close = False
        _reader: _Reader

        def setup(self) -> None:
            super().setup()
            self.rfile.close()
            self._reader = _Reader(self.connection, self.timeout)
            self.rfile = io.BufferedReader(self._reader)

        def handle_one_request(self) -> None:
            self._consumed = False
            self._linger_on_close = False
            self._reader.begin()
            try:
                super().handle_one_request()
            except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
                # a client that went away between two requests, as the arm's does when it
                # closes its connection on Windows, is no fault of this server's to print
                self.close_connection = True

        def finish(self) -> None:
            super().finish()
            if self._linger_on_close:
                _linger(self.connection)

        def do_GET(self) -> None:
            self._route("GET")

        def do_POST(self) -> None:
            self._route("POST")

        def send_error(
            self, code: int, message: str | None = None, explain: str | None = None
        ) -> None:
            """Every refusal as JSON, the ones http.server makes by itself included, and a verb
            with no `do_` method routed, so its token is checked first like any other's."""
            if code == HTTPStatus.NOT_IMPLEMENTED and self.command:
                self._route(self.command)
                return
            self._json(code, _refusal(message or "the request could not be read"), close=True)

        def _route(self, method: str) -> None:
            self._consumed = False
            path = self.path.partition("?")[0]
            if not app.authorised(self.headers.get(wire.TOKEN_HEADER)):
                # answered on the head alone and closed: a client without the token is never
                # waited on for its body, and nothing it sends is kept (see _linger)
                self._json(401, _refusal("bad or missing token"), close=True)
                return
            allowed = "GET" if path in GET_PATHS else "POST" if path in POST_PATHS else None
            if allowed is None:
                self._json(404, _refusal(f"nothing at {path}"))
                return
            if method != allowed:
                reason = f"{path} answers {allowed}, not {method}"
                self._json(405, _refusal(reason), allow=allowed)
                return
            try:
                if path == wire.POLICY_PATH:
                    reply: BaseModel = app.policy()
                elif path == wire.RESET_PATH:
                    reply = app.reset(self._body(wire.ResetRequest))
                elif path == wire.END_PATH:
                    reply = app.end(self._body(wire.EndRequest))
                else:
                    reply = app.step(self._body(wire.StepRequest))
                payload = reply.model_dump()
            except Refused as e:
                self._json(e.status, _refusal(e.reason), close=not self._consumed)
                return
            except Exception as e:
                # a fault of this server's own, answered in a sentence rather than dropped: a
                # connection closed without a reply would read to the client as a stale socket
                log.exception("%s failed", path)
                reason = f"the server failed on {path}: {type(e).__name__}: {e}"
                self._json(500, _refusal(reason), close=True)
                return
            self._json(200, payload)

        def _body(self, model: type[M]) -> M:
            declared = self.headers.get("Content-Length")
            if declared is None:
                raise Refused(411, f"POST {self.path} needs a Content-Length")
            try:
                length = int(declared)
            except ValueError:
                length = -1
            if length < 0:
                raise Refused(400, f"Content-Length {declared!r} is not a byte count")
            if length > wire.MAX_BODY_BYTES:
                raise Refused(
                    413,
                    f"the body is {length} bytes and this server reads at most "
                    f"{wire.MAX_BODY_BYTES}",
                )
            self._reader.allow(length)
            try:
                raw = self.rfile.read(length)
            except OSError:
                self.close_connection = True
                raise Refused(400, "the body did not arrive") from None
            self._consumed = True
            if len(raw) < length:
                raise Refused(400, f"the body ended after {len(raw)} of {length} bytes")
            try:
                return wire.parse(model, raw)
            except wire.ProtocolError as e:
                raise Refused(400, f"{self.path}: {e}") from None

        def _settle_body(self) -> None:
            """Leave the connection at the start of the next request, or mark it to close, as
            the Jetson host daemon does: a body within the cap is read and dropped, and anything
            else closes and lingers."""
            if self._consumed:
                return
            self._consumed = True
            declared = self.headers.get("Content-Length")
            if declared is None:
                if self.headers.get("Transfer-Encoding"):
                    self.close_connection = self._linger_on_close = True
                return
            try:
                length = int(declared)
            except ValueError:
                self.close_connection = self._linger_on_close = True
                return
            if length < 0 or length > wire.MAX_BODY_BYTES:
                self.close_connection = self._linger_on_close = True
                return
            self._reader.allow(length)
            try:
                while length > 0 and (chunk := self.rfile.read(min(length, DRAIN_CHUNK_BYTES))):
                    length -= len(chunk)
            except OSError:
                self.close_connection = True

        def _json(
            self,
            code: int,
            body: dict[str, Any],
            *,
            allow: str | None = None,
            close: bool = False,
        ) -> None:
            payload = wire.dumps(body)
            if close:
                self.close_connection = True
                self._linger_on_close = not self._consumed
            else:
                self._settle_body()
            try:
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("Cache-Control", "no-store")
                if allow is not None:
                    self.send_header("Allow", allow)
                if self.close_connection:
                    self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(payload)
            except OSError:
                self.close_connection = True  # the client hung up; nothing to tell it

        def log_message(self, fmt: str, *args: Any) -> None:
            log.debug("%s %s", self.address_string(), fmt % args)

    return Handler


def _refusal(reason: str) -> dict[str, Any]:
    return wire.Refusal(reason=reason).model_dump()


def _refuse_busy(sock: socket.socket) -> None:
    """A 503 written straight to a connection there is no thread for, never waiting on it."""
    reason = f"busy: this server is already serving {MAX_CONNECTIONS} connections; try again"
    body = wire.dumps(_refusal(reason))
    head = (
        "HTTP/1.1 503 Service Unavailable\r\n"
        "Content-Type: application/json\r\n"
        f"Content-Length: {len(body)}\r\n"
        "Cache-Control: no-store\r\n"
        "Connection: close\r\n\r\n"
    )
    with contextlib.suppress(OSError):
        sock.setblocking(False)
        sock.send(head.encode("ascii") + body)


class _Server(ThreadingHTTPServer):
    """ThreadingHTTPServer with at most `MAX_CONNECTIONS` connections, and so threads, at once."""

    # a connection's thread is never joined, by server_close() or at exit: one inferring a step
    # holds its thread for as long as torch takes, minutes on a CPU (`Served.close`)
    daemon_threads = True
    # SO_REUSEADDR lets a restart bind past the last run's TIME_WAIT on Linux. On Windows it
    # lets a second server bind a port another one is listening on, so a server started on a
    # busy port there would say it was serving while every request went to the other one.
    allow_reuse_address = sys.platform != "win32"

    def __init__(self, address: Any, handler: Any) -> None:
        self._connections = threading.BoundedSemaphore(MAX_CONNECTIONS)
        super().__init__(address, handler)

    def verify_request(self, request: Any, client_address: Any) -> bool:
        if self._connections.acquire(blocking=False):
            return True
        _refuse_busy(request)
        return False

    def process_request(self, request: Any, client_address: Any) -> None:
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._connections.release()
            raise

    def process_request_thread(self, request: Any, client_address: Any) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._connections.release()


class _Server6(_Server):
    address_family = socket.AF_INET6


def serve(app: PolicyServer, host: str, port: int) -> ThreadingHTTPServer:
    """Serve `app` on a thread and return the server, so a caller (or a test) can shut it down."""
    server_class = _Server6 if ":" in host else _Server
    server = server_class((host, port), make_handler(app))
    threading.Thread(target=server.serve_forever, name="quackd-policy-server", daemon=True).start()
    return server


# ── running it ──────────────────────────────────────────────────────────────────────────


@dataclass
class Served:
    """A policy server that is up: what it serves, where, and the token a client needs."""

    app: PolicyServer
    http: ThreadingHTTPServer
    host: str
    token_path: Path | None
    token_written: bool

    @property
    def port(self) -> int:
        return int(self.http.server_address[1])

    @property
    def url(self) -> str:
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"http://{host}:{self.port}"

    @property
    def local_url(self) -> str | None:
        """Where a client on this machine reaches the server, which is only ever over loopback,
        since that is all plain http goes to (`client.policy_address`): `url` for a bind to
        `127.0.0.1` or `::1`, the loopback address of its family for `0.0.0.0` or `::`, and
        None for a bind to one other address, the rest of 127/8 included, which a client
        reaches through the TLS proxy in front of it."""
        try:
            address = ipaddress.ip_address(self.host)
        except ValueError:
            return None
        if self.host in wire.LOOPBACK:
            return self.url
        if address.is_unspecified:
            loopback = "[::1]" if address.version == 6 else "127.0.0.1"
            return f"http://{loopback}:{self.port}"
        return None

    @property
    def token(self) -> str:
        return self.app.token

    def wait(self) -> None:
        """Serve until Ctrl+C, which arrives here as a KeyboardInterrupt. A sleep in a loop,
        because a wait on an event with no timeout is one Windows does not interrupt."""
        while True:
            time.sleep(0.5)

    def close(self) -> None:
        """Stop listening and close the policy, within one turn of the listening loop and
        `CLOSE_WAIT_S`: a request still being served, a step inferring above all, is left to
        end on its own daemon thread rather than waited for (`_Server`)."""
        self.http.shutdown()
        self.http.server_close()
        self.app.close()


def open_server(options: ServeOptions, *, token: str | None = None) -> Served:
    """Check `options`, build the policy, settle the token and listen, or a `ServeRefused`
    that says why not. `token` is one held in memory rather than a file, which is what `policy
    check` serves the policy it checks with."""
    if (why := bind_refusal(options.bind, options.behind_tls)) is not None:
        raise ServeRefused(why)
    if not 0 <= options.port <= 65535:
        raise ServeRefused(f"--port {options.port} is not a port")
    runner, info = served_policy(options)
    if token is not None:
        path: Path | None = None
        written = False
    else:
        token, path, written = server_token(options.token_file)
    app = PolicyServer(runner, info, token)
    try:
        http = serve(app, options.bind, options.port)
    except OSError as e:
        app.close()
        raise ServeRefused(
            f"cannot listen on {options.bind}:{options.port}: {e}. Is another server on that "
            "port? Pick another with --port"
        ) from None
    return Served(app, http, options.bind, path, written)


def describe(info: wire.PolicyInfo) -> list[tuple[str, str]]:
    """What `policy check` prints about a policy, as (label, text) rows."""
    images = ", ".join(
        f"{image.key} {image.width}x{image.height}" if image.width else f"{image.key} any size"
        for image in info.features.images
    )
    features = (
        "whatever the arm has (a scripted policy)"
        if info.features.state is None and not info.features.images
        else f"state {info.features.state}, action {info.features.action}, images "
        f"{images or 'none'}"
        + (", a missing one padded" if info.features.pads_images else "")
        + (
            f", actions named {', '.join(info.features.action_names)}"
            if info.features.action_names
            else ""
        )
    )

    def quantiles(q: wire.Quantiles | None) -> str:
        if q is None:
            return "not reported"
        pairs = zip(q.q01, q.q99, strict=True)
        return ", ".join(f"{lo:g}..{hi:g}" for lo, hi in pairs)

    served = (
        f"{info.policy} ({info.protocol} {info.protocol_version}, quackd {info.server_version})"
    )
    return [
        ("policy", served),
        ("features", features),
        ("rate", f"{info.rate_hz:g} Hz, from {info.rate_source}"),
        (
            "chunks",
            "asked every tick"
            if info.per_tick
            else f"{info.chunk_size} actions, {info.n_action_steps} played from each",
        ),
        ("latency", _declared(info)),
        ("gpu", "yes" if info.gpu else "no"),
        ("threads", str(info.threads) if info.threads is not None else "not set"),
        (
            "frames",
            "raw" if info.jpeg_quality is None else f"JPEG at quality {info.jpeg_quality}",
        ),
        ("cameras", ", ".join(f"{k}={v}" for k, v in info.cameras.items()) or "none mapped"),
        ("state q01..q99", quantiles(info.state_quantiles)),
        ("action q01..q99", quantiles(info.action_quantiles)),
        ("loaded", "; ".join(info.loaded) or "nothing, a scripted policy loads no repository"),
    ]


def _declared(info: wire.PolicyInfo) -> str:
    """The latency a server declares, and why a segment could not play it where it could not:
    a server started before `quackd policy serve` refused such a latency still serves one."""
    said = f"{info.latency_s:g} s declared"
    why = latency_too_long(info.latency_s, info.rate_hz, info.n_action_steps, info.per_tick)
    if why is None:
        return said
    return (
        f"{said}, which is {_ticks(latency_ticks(info.latency_s, info.rate_hz))} at "
        f"{info.rate_hz:g} Hz, and each chunk plays {info.n_action_steps} actions, one a tick, "
        f"so {why}. quackd policy serve refuses it: serve with a --latency-s of at most "
        f"{longest_latency_s(info.rate_hz, info.n_action_steps):g} s"
    )


# ── the bench ───────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class BenchResult:
    """What `policy check --bench` measured, on the wall's clock: the ticks paced at the
    policy's rate, the ones that had an action to play, the ones with nothing (starved), the
    ones the pacer skipped for running late, and each request's round trip and the inference
    time the server reported for it. `before_first` is how many of the starved ticks came
    before the stream's first chunk was back. `latency_s` is one warm step timed on its own
    before the stream, from the request going out to its chunk back. `declared_s` is the
    `--latency-s` the server was started with, and `waited` whether the bench, with nothing left
    to play, waited out a chunk in the tick that asked for it, its first chunk among them, as a
    segment waits for a policy that declares none. `waited_ticks` is how many of the skipped
    ticks passed while it did, and the rest are ticks the pacer woke too late for. `waits_each`
    is chunks one at a time (`--no-async-chunks`), each played out before the next is asked for
    while the arm holds still, which are starved ticks rather than skipped ones.
    `chunk` is how many actions a chunk the server answers plays, and `per_tick` whether it
    answers one action a tick, which bound the `--latency-s` it can be served with
    (`latency_too_long`)."""

    seconds: float
    rate_hz: float
    ticks: int
    played: int
    starved: int
    skipped: int
    rtt_s: tuple[float, ...] = ()
    inference_s: tuple[float, ...] = ()
    dropped: int = 0
    latency_s: float | None = None
    declared_s: float = 0.0
    waited: bool = False
    before_first: int = 0
    chunk: int | None = None
    per_tick: bool = False
    waited_ticks: int = 0
    waits_each: bool = False

    @property
    def achieved_hz(self) -> float:
        return self.played / self.seconds if self.seconds > 0 else 0.0

    @property
    def timed_s(self) -> tuple[float, ...]:
        """Every step the bench timed from its request going out to its chunk back, which is
        what a `--latency-s` declares: the warm one timed on its own and each of the stream's."""
        return (*(() if self.latency_s is None else (self.latency_s,)), *self.rtt_s)

    @property
    def measured_s(self) -> float | None:
        """`LATENCY_QUANTILE` of `timed_s`, or None when nothing was timed."""
        timed = self.timed_s
        return _percentile(timed, LATENCY_QUANTILE) if timed else None

    @property
    def declare_s(self) -> float | None:
        """`measured_s` rounded up to `LATENCY_STEP_S`, the `--latency-s` to serve with."""
        if self.measured_s is None:
            return None
        return math.ceil(self.measured_s / LATENCY_STEP_S - 1e-9) * LATENCY_STEP_S


def _percentile(values: Sequence[float], share: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, math.ceil(share * len(ordered)) - 1))]


def _starved(result: BenchResult) -> str:
    said = f"{_ticks(result.starved)} with nothing to send"
    if not result.starved:
        return said
    said += ", waiting for a chunk to come back"
    if result.waits_each:
        return said + ", as --no-async-chunks has the arm hold still while each is on its way"
    if result.before_first == result.starved:
        return said + ", every one before the first came back, which a segment gives time for"
    if result.before_first:
        return said + f", {result.before_first} of them before the first came back"
    return said


def _skipped(result: BenchResult) -> str:
    """What a bench's skipped ticks were, said as what each would have been on the arm: the
    ones that passed while a chunk was waited for in the tick that asked for it, and the ones
    the pacer woke too late for."""
    waited = min(result.skipped, result.waited_ticks) if result.waited else 0
    late = result.skipped - waited
    if not late:
        return (
            "the policy declares no --latency-s, so a chunk asked for with nothing left to play, "
            "its first among them, was waited for in the tick that asked for it, as a segment "
            "waits for one, and the ticks that passed meanwhile sent nothing: the arm would have "
            "held still through them"
        )
    if not waited:
        return (
            "the pacer woke too late for them, and they sent nothing, as a machine too busy to "
            "keep the policy's rate would leave the arm"
        )
    why = (
        "a chunk asked for with nothing left to play, its first among them, was waited for in "
        "the tick that asked for it, since the policy declares no --latency-s, as a segment "
        "waits for one"
    )
    return (
        f"{waited} passed while {why}, and the pacer woke too late for {late} more, as a machine "
        "too busy to keep the policy's rate would leave the arm. None of them sent anything: "
        "the arm would have held still through them"
    )


def describe_bench(result: BenchResult) -> list[tuple[str, str]]:
    """What `policy check --bench` prints about a bench, as (label, text) rows. A tick the
    pacer skipped sent nothing, as a starved one did, and each is said as what it would have
    been on the arm."""
    rows = [
        (
            "achieved",
            f"{result.achieved_hz:.1f} Hz of {result.rate_hz:g}: {result.played} of the "
            f"{result.ticks + result.skipped} ticks in {result.seconds:.1f} s sent an action",
        ),
        ("starved", _starved(result)),
    ]
    if result.skipped:
        rows.append(("skipped", f"{_ticks(result.skipped)}: {_skipped(result)}"))
    if result.rtt_s:
        ms = [1000 * t for t in result.rtt_s]
        rows.append(
            (
                "round trip",
                f"median {statistics.median(ms):.1f} ms, p99 {_percentile(ms, 0.99):.1f} ms, "
                f"max {max(ms):.1f} ms over {len(ms)} requests",
            )
        )
    if result.inference_s:
        ms = [1000 * t for t in result.inference_s]
        rows.append(("inference", f"median {statistics.median(ms):.1f} ms on the server"))
    if result.dropped:
        rows.append(("dropped", f"{result.dropped} replies for another session or sequence"))
    measured, declare = result.measured_s, result.declare_s
    if measured is not None and declare is not None:
        said = (
            f"{1000 * measured:.1f} ms or less for {100 * LATENCY_QUANTILE:g}% of the "
            f"{len(result.timed_s)} steps timed, from the request to its chunk back"
        )
        if (too_slow := _too_slow(declare, result)) is not None:
            said += f": {too_slow}"
        elif (unplayable := _unplayable(result)) is not None:
            said += f": {unplayable}. Serve it with --latency-s {declare:.2f}, and bench again"
        elif result.declared_s > 0 and result.declared_s >= declare - 1e-9:
            said += f": the --latency-s {result.declared_s:g} it is served with covers that"
            if (outran := _outran(result)) is not None:
                said += f", but {outran}"
        else:
            said += (
                f": serve with --latency-s {declare:.2f}, so the simulator holds each chunk "
                "back as long, and bench again with it"
            )
        rows.append(("latency", said))
    return rows


def _too_slow(declare_s: float, result: BenchResult) -> str | None:
    """Why `quackd policy serve` would refuse `declare_s` as a `--latency-s`, said with what to
    do instead, or None when it would take it. The bench suggests no latency the server
    refuses: a step slower than either bound completes a bench, since the client waits longer
    for a step than a segment waits for a chunk."""
    if declare_s >= wire.MAX_LATENCY_S:
        return (
            f"a --latency-s of {declare_s:.2f} is past the {wire.MAX_LATENCY_S:g} s a segment "
            "waits for its first chunk, and quackd policy serve refuses it. This policy answers "
            "too slowly to drive an arm from this machine: serve it on a GPU"
        )
    if result.chunk is not None and (
        why := latency_too_long(declare_s, result.rate_hz, result.chunk, result.per_tick)
    ):
        return (
            f"a --latency-s of {declare_s:.2f} is "
            f"{_ticks(latency_ticks(declare_s, result.rate_hz))} at {result.rate_hz:g} Hz, and "
            f"each chunk plays {result.chunk} actions, one a tick, so {why}, and quackd policy "
            "serve refuses it. This policy answers too slowly to drive an arm from this "
            "machine: serve it on a GPU"
        )
    return None


def _outran(result: BenchResult) -> str | None:
    """Why a latency that covers most of the steps a bench timed still does not keep the arm
    fed, or None. However early the loop asks, the one request out goes no sooner than the last
    chunk lands, so only an answer within half a chunk (`loop.longest_latency`) is sure to land
    with something still queued, and the slowest step timed is judged against that: the one
    step in twenty a declared latency does not cover is the one that runs past it."""
    if result.chunk is None or result.per_tick or not result.timed_s:
        return None
    slowest = max(result.timed_s)
    late, room = latency_ticks(slowest, result.rate_hz), longest_latency(result.chunk)
    if late <= room:
        return None
    return (
        f"its slowest step took {1000 * slowest:.1f} ms, {_ticks(late)} at "
        f"{result.rate_hz:g} Hz, and only an answer within half a chunk of {result.chunk} "
        f"actions, {_ticks(room)}, is sure to land before the arm has nothing to play. A step "
        "that slow can leave it holding still while it runs over: serve the policy where it "
        "answers faster"
    )


def _unplayable(result: BenchResult) -> str | None:
    """Why the `--latency-s` the benched server was started with is one a segment could not
    play, or None. `quackd policy serve` refuses one, and a server an older quackd started may
    still declare it, whatever the bench measures."""
    if result.chunk is None or result.declared_s <= 0:
        return None
    why = latency_too_long(result.declared_s, result.rate_hz, result.chunk, result.per_tick)
    if why is None:
        return None
    return (
        f"the --latency-s {result.declared_s:g} it is served with is "
        f"{_ticks(latency_ticks(result.declared_s, result.rate_hz))} at {result.rate_hz:g} Hz, "
        f"and each chunk plays {result.chunk} actions, one a tick, so {why}"
    )


def _synthetic_cameras(info: wire.PolicyInfo) -> list[wire.CameraInfo]:
    """A camera for every one the server maps, at the size the policy declares for its image,
    or `BENCH_FRAME` where it declares none."""
    sizes = {image.key: (image.width, image.height) for image in info.features.images}
    cameras = []
    for name, key in info.cameras.items():
        width, height = sizes.get(key, (None, None))
        if width is None or height is None:
            width, height = BENCH_FRAME
        cameras.append(wire.CameraInfo(name=name, height=height, width=width))
    return cameras


def bench(
    runner: Any,
    *,
    seconds: float = BENCH_S,
    clock: Callable[[], float] = time.perf_counter,
    sleep: Callable[[float], None] = time.sleep,
    async_chunks: bool = True,
) -> BenchResult:
    """Stream synthetic observations through `runner` (a `RemoteRunner`) at the rate its server
    declares, for `seconds` of the wall's clock, paced and queued as the policy loop paces and
    queues a segment (`loop.py`): a tick every period from the start, a request when what is
    left of the last chunk is down to half of it or to twice the ticks the declared latency
    comes to, whichever is more (`refill_due`), one request out at most, and a chunk's actions
    for ticks already played dropped as it lands. A request to a policy that declares no
    latency is waited for in the tick that asked when nothing is left to play, the first among
    them, as the loop waits for it, and any other is left to land while the ticks go on and the
    queue plays, and a wait for a chunk that outlasted its tick sends nothing in it, as a
    segment's does. With `async_chunks` off (`--no-async-chunks`) a policy that plays chunks
    has each played out before the next is asked for, the ticks between holding still, as a
    segment plays chunks one at a time (`loop._Run.one_at_a_time`). The observation is every
    motor at 0 and a frame of seeded noise per camera
    the server maps, which JPEG compresses worst, so a round trip measured here is not
    flattered by an easy picture. Before the stream one warm step is timed on its own
    (`BenchResult.latency_s`), and the latency the bench suggests is read over it and every
    step of the stream (`BenchResult.declare_s`)."""
    info = runner.policy()
    runner.cameras = tuple(_synthetic_cameras(info))
    if info.features.state is not None and len(runner.motors) != info.features.state:
        raise ServeRefused(
            f"{info.policy} takes a state of {info.features.state}, and the bench streams the "
            f"SO-101's {len(runner.motors)} motors: bench it on the arm it learned from"
        )
    runner.reset(BENCH_INSTRUCTION)
    features = runner.features()
    if (refusal := rate_refusal(features)) is not None:
        raise ServeRefused(refusal)
    rate = float(features.rate_hz)
    period = 1.0 / rate
    declared = float(runner.latency_s())
    late = latency_ticks(declared, rate)
    one_at_a_time = not async_chunks and not features.per_tick
    waits = late == 0
    noise = np.random.default_rng(0)
    frames = {
        camera.name: noise.integers(0, 256, (camera.height, camera.width, 3), dtype=np.uint8)
        for camera in runner.cameras
    }
    state = {f"{motor}.pos": 0.0 for motor in runner.motors}
    # One step to warm the policy up, since a first inference pays for whatever torch does
    # lazily, then one timed on its own: the whole wait for a chunk, both ways of the wire and
    # the inference, which is what a declared latency stands for. Then a fresh session, so the
    # stream starts from a policy that has seen nothing.
    runner.next_chunk(Observation(0, {**state, **frames}), {})
    started = clock()
    runner.next_chunk(Observation(1, {**state, **frames}), {})
    measured = clock() - started
    runner.reset(BENCH_INSTRUCTION)
    rtts: list[float] = []
    inferences: list[float] = []

    def ask(tick: int, sent: dict[str, float]) -> Chunk:
        chunk = runner.next_chunk(Observation(tick, {**state, **frames}), sent)
        if runner.last_rtt_s is not None:
            rtts.append(runner.last_rtt_s)
        if runner.last_inference_s is not None:
            inferences.append(runner.last_inference_s)
        return chunk

    queue: deque[tuple[int, Mapping[str, Any]]] = deque()
    inflight: tuple[int, concurrent.futures.Future[Chunk]] | None = None
    sent: dict[str, float] = {}
    done = False
    last_len = ticks = played = starved = skipped = waited_ticks = before_first = 0
    worker = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="quackd-bench")

    def take_in(tick: int) -> None:
        nonlocal inflight, queue, done, last_len
        if inflight is None or not inflight[1].done():
            return
        asked, future = inflight
        inflight = None
        chunk = future.result()  # a server that failed fails the check, and says so
        done = done or chunk.done
        if one_at_a_time:
            # played from its first action at the tick it lands, as a segment plays it so
            fresh = [(tick + i, a) for i, a in enumerate(chunk.actions)]
        else:
            fresh = [(asked + i, a) for i, a in enumerate(chunk.actions) if asked + i >= tick]
        last_len = len(chunk.actions) or last_len
        if fresh:
            queue = deque(e for e in queue if e[0] < fresh[0][0])
            queue.extend(fresh)

    start = clock()
    tick = 0
    try:
        while clock() - start < seconds:
            take_in(tick)
            left = sum(1 for at, _ in queue if at >= tick)
            each = one_at_a_time and last_len != 1  # as `_Run.one_at_a_time`
            due = not left if each else refill_due(left, late, last_len)
            waited_here = False
            if inflight is None and not done and (features.per_tick or due):
                inflight = (tick, worker.submit(ask, tick, dict(sent)))
                if waits and not left and not each:
                    concurrent.futures.wait([inflight[1]])
                    take_in(tick)
                    waited_here = True
            # a wait for a chunk that outlasted its tick sends nothing in it, as a segment's does
            waited_out = (
                waited_here and last_len != 1 and clock() >= start + (tick + 1) * period - EXACT
            )
            while queue and queue[0][0] < tick:
                queue.popleft()
            if waited_out:
                if queue and queue[0][0] == tick:
                    queue.popleft()  # its tick has passed by the time the wait ended
            elif queue and queue[0][0] == tick:
                sent = {k: float(v) for k, v in queue.popleft()[1].items()}
                played += 1
            else:
                starved += 1
                if not played:
                    before_first += 1  # the first chunk still on its way
            ticks += 1
            tick += 1
            now = clock()
            if now > start + tick * period:
                after = max(tick + 1, math.floor((now - start) / period) + 1)
                skipped += after - tick
                if waited_here:
                    waited_ticks += after - tick  # the wait's, rather than a late pacer's
                tick = after
            sleep(max(0.0, start + tick * period - clock()))
        elapsed = clock() - start
    finally:
        worker.shutdown(wait=True)
    return BenchResult(
        seconds=elapsed,
        rate_hz=rate,
        ticks=ticks,
        played=played,
        starved=starved,
        skipped=skipped,
        rtt_s=tuple(rtts),
        inference_s=tuple(inferences),
        dropped=int(getattr(runner, "dropped", 0)),
        latency_s=measured,
        declared_s=declared,
        waited=waits,
        before_first=before_first,
        chunk=info.n_action_steps,
        per_tick=info.per_tick,
        waited_ticks=waited_ticks,
        waits_each=one_at_a_time,
    )


__all__ = [
    "ABANDONED_S",
    "BENCH_S",
    "CLOSE_WAIT_S",
    "MAX_CONNECTIONS",
    "MAX_HEAD_BYTES",
    "REQUEST_DEADLINE_S",
    "REQUEST_TIMEOUT_S",
    "BenchResult",
    "PolicyServer",
    "Refused",
    "ServeOptions",
    "ServeRefused",
    "Served",
    "bench",
    "bind_refusal",
    "chunk_outrun",
    "chunk_starved",
    "describe",
    "describe_bench",
    "latency_too_long",
    "longest_latency_s",
    "open_server",
    "parse_cameras",
    "serve",
    "served_policy",
    "server_token",
]
