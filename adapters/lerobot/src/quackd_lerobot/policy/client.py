"""The arm's half of the policy protocol: `RemoteRunner`, a policy another process serves.

No quackd command loads a checkpoint in the process that owns the serial bus
(`upstream_api.PROCESSOR_CLASS_IMPORT`), so the arm reaches its policy over HTTP, at a server
the user starts (`quackd policy serve`, `server.py`). The one thing that would load one there is
`real.load_policy()`, which nothing in quackd calls (the known gap ADR-0048 names).
`RemoteRunner` is that server as the policy loop sees it: `reset` starts a session, `features`
and `latency_s` say what the server said at that reset, and `next_chunk` sends one observation
and returns the chunk that answered it. The loop calls it on its own worker thread (`loop.py`),
so every call here may block, and each is bounded instead.

**It imports the standard library, numpy and PIL and nothing else.** The arm's process gains
no dependency from a policy: no torch, no LeRobot, no HTTP library.

**What it refuses to take on faith**, because a reply from this server moves an arm:

- **where it is going.** Plain `http://` only to the literals `127.0.0.1` and `::1`, where
  nothing on the network can read or change a reply. A policy on another machine is reached
  through `ssh -L`, which looks like loopback here, or over `https://` with the certificate
  verified. `localhost` is refused with a sentence rather than looked up, because a lookup of
  it costs about two seconds per call on the Windows machine this was written on, and a
  policy is called ten times a second.
- **the path there.** `http.client` follows no proxy and no redirect, so the token header only
  ever goes to the address given. A redirect is an error.
- **the token's secrecy.** It rides in one header. It is scrubbed out of every error this module
  raises, and the URL is held redacted from the moment it is given (`quackd.command`). One a
  header cannot carry, or too short to guard an arm, is refused before anything is sent
  (`protocol.clean_token`), in a sentence that never quotes it.
- **the reply.** At most `MAX_REPLY_BYTES`, parsed with every non-finite number refused, then
  validated strictly (`protocol.py`). A reply whose session or sequence is not the request's
  is dropped, and the loop asks again.
- **the time.** A connect has a timeout, and a whole reply a deadline that every read of it is
  held to (`_BoundedReader`), so a server that trickles its reply is cut off as surely as one
  that stops. On the arm a step's is longer than any patience the loop has, so a server that
  stops answering starves the segment, which ends it with the arm held, before the call gives
  up, unless what was still queued when it stopped plays for longer than that deadline less
  the loop's patience: then the call's error ends the segment, and the verb stops the arm on
  it. On the simulator the loop's patience is on the world's clock, which stands still while
  the loop waits for a chunk, so there this deadline is what ends the segment, and the
  segment says the policy raised the `PolicyServerError` it ended on.

**Whether the policy fits the arm** is asked at connect, before the arm is energised
(`fit_arm`, and `fit.py` for what is checked): the arm hands the client its bus's motors, a
frame's size from each camera and its calibrated travel, and a policy that could not drive it
refuses the connect. Two of the refusals can be overridden by a caller who knows better: a
policy trained on an arm calibrated another way (`accept_other_frame`, which a run's
`--accept-other-frame` sets), and from Python alone a frame of another size
(`accept_frame_size`), whose refusal says to give the camera that size instead. The record
says when either was taken. It is asked again at every
reset, of what the server says then, so a server started again with another policy since the
connect starts no segment (`_refit`).

A keep-alive socket the server has closed while the loop was idle is found only when it is
next used, so a call on a reused socket that fails that way is sent once more on a new one.
The server answers a repeated step from what it answered the first time (its sequence
number), so a step whose reply was lost is never inferred twice. A reset names the session it
replaces, which the server takes from this client however recently it was used, and `close`
ends the session, so another client need not wait out the server's lease on it.
"""

from __future__ import annotations

import contextlib
import http.client
import io
import math
import numbers
import os
import socket
import ssl
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, TypeVar
from urllib.parse import urlsplit

from pydantic import BaseModel

from quackd.command import readable_url, redacted_url
from quackd_lerobot.policy import protocol as wire
from quackd_lerobot.policy.fit import Fit, PolicyMisfit, fit
from quackd_lerobot.policy.loop import FIRST_CHUNK_S
from quackd_lerobot.policy.runner import Chunk, Features, Observation

M = TypeVar("M", bound=BaseModel)

CONNECT_TIMEOUT_S = 2.0
"""How long a connection may take to open. On loopback, and through a tunnel's local end, a
connect is immediate, so anything slower is nothing listening or a tunnel that is down."""
CALL_TIMEOUT_S = 3.0
"""How long `GET /v1/policy` and `POST /v1/reset` may take to answer: both are answered from
what the server holds, and a reset only clears a policy's queue."""
STEP_TIMEOUT_S = 2 * FIRST_CHUNK_S
"""How long a step may take to answer. Longer than the loop's own patience (`FIRST_CHUNK_S`
for a first chunk and less after it), so on the arm a server that stops answering starves the
segment and the loop says so, holding the arm, before this call gives up and frees the loop's
worker, as long as what was still queued when it stopped plays out within this less that
patience. A queue longer than that ends on this call's `PolicyServerError` instead, which the
verb stops the arm on. On the simulator, whose clock stands still while the loop waits, this
is what ends the wait, as a `PolicyServerError` the segment ends on."""
READ_CHUNK_BYTES = 64 << 10
RETRIED = (ConnectionResetError, ConnectionAbortedError, BrokenPipeError)
"""What a kept-alive socket the server has since closed fails with. `RemoteDisconnected` is one
of the resets."""


class PolicyServerError(RuntimeError):
    """Anything that went wrong reaching the policy server, as one sentence that names it and
    never the token. `status` is the HTTP status when the server answered at all."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


# ── a deadline on a whole reply ─────────────────────────────────────────────────────────


class _Deadline:
    """When the call in flight has to have its whole reply by, on `time.monotonic`'s clock."""

    at = math.inf


class _BoundedReader(io.RawIOBase):
    """The socket's own reader, as a reply is read through it, with each read given what is
    left of the call's deadline, the way the server's `_Reader` bounds a request. A socket
    timeout bounds one read and not their sum, so without this a server sending a byte just
    often enough to keep each read alive would hold the call as long as it liked, in the
    status line, the headers or the body, and the loop's worker with it."""

    def __init__(self, raw: Any, sock: socket.socket, deadline: _Deadline) -> None:
        super().__init__()
        self._raw = raw
        self._sock = sock
        self._deadline = deadline

    def readable(self) -> bool:
        return True

    def readinto(self, buffer: Any) -> Any:
        left = self._deadline.at - time.monotonic()
        if left <= 0:
            raise TimeoutError
        self._sock.settimeout(left)
        return self._raw.readinto(buffer)

    def close(self) -> None:
        if not self.closed:
            # the socket's own reader holds it open while a reply is read, so it is closed here
            self._raw.close()
        super().close()


def _bounded_reply(deadline: _Deadline) -> type[http.client.HTTPResponse]:
    """An `HTTPResponse` that reads through a `_BoundedReader` on `deadline`, for a connection's
    `response_class`, which is how `http.client` makes the reply to every request on it."""

    class BoundedReply(http.client.HTTPResponse):
        def __init__(self, sock: socket.socket, *args: Any, **kwargs: Any) -> None:
            super().__init__(sock, *args, **kwargs)
            self.fp = io.BufferedReader(_BoundedReader(self.fp.detach(), sock, deadline))

    return BoundedReply


# ── the address and the token ───────────────────────────────────────────────────────────


def policy_address(url: str) -> tuple[str, str, int, str]:
    """`--policy-url` as (scheme, host, port, path prefix), or a ValueError in a sentence that
    says what to write instead. The sentence quotes the URL redacted, and one redaction cannot
    read not at all, since that one would be quoted as typed, password and all."""
    given = url.strip()
    if not readable_url(given):
        raise ValueError(
            "--policy-url is not a URL: give the policy server as http://127.0.0.1:PORT"
        )
    shown = redacted_url(given)
    try:
        parts = urlsplit(given)
        port = parts.port
    except ValueError:
        raise ValueError(
            f"{shown} is not a URL: give the policy server as http://127.0.0.1:PORT"
        ) from None
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        raise ValueError(
            f"{shown} is not http:// or https://: give the policy server as http://127.0.0.1:PORT"
        )
    if parts.username is not None or parts.password is not None:
        raise ValueError(
            f"{shown} carries a user or a password, which quackd never sends: the policy server "
            "takes its token in a header, from --policy-token or "
            f"{wire.TOKEN_ENV}"
        )
    if parts.query or parts.fragment:
        raise ValueError(
            f"{shown} has a query or a fragment, and the policy server's address is its "
            "scheme, host and port alone: a token goes in --policy-token"
        )
    host = (parts.hostname or "").lower()
    if not host:
        raise ValueError(f"{shown} names no host: give the policy server as http://127.0.0.1:PORT")
    if host == "localhost":
        raise ValueError(
            f"{shown} says localhost: write 127.0.0.1 (or ::1) instead. Looking localhost up "
            "costs about two seconds a call on some machines, and a policy is called many times "
            "a second"
        )
    if scheme == "http" and host not in wire.LOOPBACK:
        raise ValueError(
            f"{shown} is plain http to {host}, and a reply from a policy server moves the arm: "
            "plain http goes only to 127.0.0.1 or ::1. Reach a policy on another machine "
            "through a tunnel (ssh -L PORT:127.0.0.1:PORT that-machine, then "
            "http://127.0.0.1:PORT), or over https:// with a certificate this machine trusts"
        )
    default = 443 if scheme == "https" else wire.DEFAULT_PORT
    return scheme, host, port if port is not None else default, parts.path.rstrip("/")


def client_token(given: str | None) -> str:
    """The token the client sends: `--policy-token`, else `$QUACKD_POLICY_TOKEN`, else the file
    `quackd policy serve` writes when it is given no `--token-file` (`DEFAULT_TOKEN_FILE`). A
    ValueError saying where to put one when none of them has one: the server always wants a
    token, so a client without one could only be refused. The first that has one is checked
    (`protocol.clean_token`): one a header cannot carry, or one too short, is a ValueError that
    names where it came from and never quotes it."""
    for token, source in (
        (given, "--policy-token"),
        (os.environ.get(wire.TOKEN_ENV), wire.TOKEN_ENV),
    ):
        if token is not None and token.strip():
            return wire.clean_token(token, source)
    path = Path(os.path.expanduser(wire.DEFAULT_TOKEN_FILE))
    try:
        token = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        token = ""
    if token:
        return wire.clean_token(token, str(path))
    raise ValueError(
        f"no token for the policy server: pass --policy-token, set {wire.TOKEN_ENV}, or run "
        f"quackd policy serve on this machine, which writes one to {path}"
    )


Identity = tuple[str, tuple[str, ...]]
"""What a policy is, as `fit_arm` keeps it: its name and every repository its server loaded."""


def _identity(info: wire.PolicyInfo) -> Identity:
    return info.policy, tuple(info.loaded)


# ── the runner ──────────────────────────────────────────────────────────────────────────


class RemoteRunner:
    """A `PolicyRunner` whose policy is served at `url`, for an arm whose motors, in its bus's
    order, are `motors`, and whose cameras are `cameras`.

    `url` is held redacted (`url`), and `token` privately. The timeouts are `CONNECT_TIMEOUT_S`,
    `CALL_TIMEOUT_S` and `STEP_TIMEOUT_S`, as attributes a test can shorten. `dropped` counts
    replies thrown away for a session or a sequence that was not the request's, and
    `last_rtt_s` and `last_inference_s` are the last step's round trip and the inference time
    the server reported for it, for `quackd policy check --bench` to read. Every step's round
    trip is added up as well, for a run's record (`round_trips`), which also names the server
    and what it serves (`record`). `accept_frame_size` and `accept_other_frame` are the two
    overrides `fit_arm` takes, and the second is `--accept-other-frame`."""

    def __init__(
        self,
        url: str,
        *,
        token: str,
        motors: Sequence[str],
        cameras: Sequence[wire.CameraInfo] = (),
        accept_frame_size: bool = False,
        accept_other_frame: bool = False,
    ) -> None:
        self.scheme, self.host, self.port, self._prefix = policy_address(url)
        self.url = redacted_url(url.strip())
        self._token = wire.clean_token(token, "the caller")
        self.motors = tuple(motors)
        self.cameras = tuple(cameras)
        self.accept_frame_size = accept_frame_size
        self.accept_other_frame = accept_other_frame
        self.connect_timeout_s = CONNECT_TIMEOUT_S
        self.call_timeout_s = CALL_TIMEOUT_S
        self.step_timeout_s = STEP_TIMEOUT_S
        self.info: wire.PolicyInfo | None = None
        self.session: str | None = None
        self.seq = 0
        self.dropped = 0
        self.last_rtt_s: float | None = None
        self.last_inference_s: float | None = None
        self._trips = 0
        self._trip_total_s = 0.0
        self._trip_longest_s = 0.0
        self._fitted: tuple[dict[str, tuple[float, float]], float, Identity] | None = None
        self._conn: http.client.HTTPConnection | None = None
        self._deadline = _Deadline()
        self._reply_class = _bounded_reply(self._deadline)

    def __repr__(self) -> str:
        return f"RemoteRunner({self.url!r})"

    # ── the protocol ────────────────────────────────────────────────────────────────────

    def policy(self) -> wire.PolicyInfo:
        """What the server is serving, asked afresh, and kept as `info`."""
        raw = self._exchange("GET", wire.POLICY_PATH, None, self.call_timeout_s)
        said = self._json(raw, wire.POLICY_PATH)
        if not isinstance(said, dict) or said.get("protocol") != wire.PROTOCOL:
            raise self._fail(
                f"{self.url} answered {wire.POLICY_PATH}, and not as {wire.PROTOCOL}: is quackd "
                "policy serve what is listening there?"
            )
        if said.get("protocol_version") != wire.PROTOCOL_VERSION:
            version = self._said(repr(said.get("protocol_version")), 40)
            raise self._fail(
                f"{self.url} speaks {wire.PROTOCOL} version {version}, "
                f"and this quackd speaks version {wire.PROTOCOL_VERSION}: run the same quackd "
                "on both ends"
            )
        self.info = self._read(wire.PolicyInfo, said, wire.POLICY_PATH)
        return self.info

    @property
    def answer_within_s(self) -> float:
        """The longest one call on the server can take, the second try on a stale socket
        included, and one call's time more: a caller that waits this long for a call always
        hears this client's own sentence about what went wrong before its own patience ends."""
        return 2 * (self.connect_timeout_s + self.call_timeout_s) + self.call_timeout_s

    def fit_arm(
        self,
        motors: Sequence[str],
        cameras: Sequence[wire.CameraInfo],
        travel: Mapping[str, tuple[float, float]],
        slack_deg: float,
    ) -> tuple[str, ...]:
        """Take the arm as its connect found it, `motors` in its bus's order and `cameras` as a
        frame of each gave them, ask the server what it serves, and say whether that fits an
        arm of `travel` (`fit.fit`). What the record should say comes back; a policy that does
        not fit is a `PolicyMisfit`, and a server that cannot be asked a `PolicyServerError`.
        Called at connect, before the arm is energised, on the loop's worker. The arm and the
        policy it was fitted to are kept, and every `reset` asks again (`_refit`)."""
        self.motors = tuple(motors)
        self.cameras = tuple(cameras)
        self._fitted = None
        info = self.policy()
        verdict = self._fit(info, dict(travel), float(slack_deg))
        if verdict.refusal is not None:
            raise PolicyMisfit(verdict.refusal)
        self._fitted = (dict(travel), float(slack_deg), _identity(info))
        return verdict.notes

    def _fit(
        self, info: wire.PolicyInfo, travel: Mapping[str, tuple[float, float]], slack_deg: float
    ) -> Fit:
        return fit(
            info,
            motors=self.motors,
            cameras=self.cameras,
            travel=travel,
            slack_deg=slack_deg,
            where=self.url,
            accept_frame_size=self.accept_frame_size,
            accept_other_frame=self.accept_other_frame,
        )

    def _refit(self, info: wire.PolicyInfo) -> None:
        """The check `fit_arm` made at connect, made again on what the server says now, before a
        session starts. The arm's process lives as long as a pilot's session, and the server may
        have been started again on the same port and token with another policy since: one the
        connect never checked, which the record does not name, and which might not fit. So a
        policy other than the one fitted is refused with a sentence saying to connect again, and
        the same one is checked again, since the server may map its cameras otherwise now."""
        if self._fitted is None:
            return
        travel, slack_deg, fitted = self._fitted
        now = _identity(info)
        if now != fitted:
            raise PolicyMisfit(
                f"the policy at {self.url} is now {info.policy}, and the arm was connected to "
                f"{fitted[0]}: the server was started again with another policy since, and "
                "the check at connect and the record are about the one before. Connect the arm "
                "again, so the new one is checked and named"
            )
        verdict = self._fit(info, travel, slack_deg)
        if verdict.refusal is not None:
            raise PolicyMisfit(verdict.refusal)

    # ── PolicyRunner ────────────────────────────────────────────────────────────────────

    def reset(self, instruction: str) -> None:
        """Ask the server what it serves, then start a session for `instruction` in place of
        this client's last. A segment reads its features and latency right after, so they are
        the server's as of now. Another client's session still in use refuses it (409). An arm
        fitted at connect is fitted again first, and a policy that is not the one fitted, or no
        longer fits, is a `PolicyMisfit` before any session starts (`_refit`)."""
        self._refit(self.policy())
        request = self._message(
            wire.ResetRequest,
            {
                "instruction": instruction,
                "motors": list(self.motors),
                "cameras": list(self.cameras),
                "replaces": self.session,
            },
        )
        raw = self._exchange("POST", wire.RESET_PATH, wire.dumps(request), self.call_timeout_s)
        reply = self._read(wire.ResetReply, self._json(raw, wire.RESET_PATH), wire.RESET_PATH)
        self.session = reply.session
        self.seq = 0

    def features(self) -> Features:
        info = self._info()
        return Features(info.rate_hz, info.rate_source, per_tick=info.per_tick)

    def latency_s(self) -> float:
        return self._info().latency_s

    def next_chunk(self, observation: Observation, sent: Mapping[str, float]) -> Chunk:
        """One observation to the server and the chunk it answered, stamped with the
        observation's tick. A reply for another session or sequence is dropped, counted in
        `dropped`, and answered here as an empty chunk, which the loop asks again after."""
        info = self._info()
        if self.session is None:
            raise PolicyServerError("the policy was asked for a chunk before any reset")
        self.seq += 1
        seq = self.seq
        reading = observation.reading
        request = self._message(
            wire.StepRequest,
            {
                "session": self.session,
                "seq": seq,
                "tick": observation.tick,
                "state": self._state(reading),
                "frames": [
                    wire.encode_frame(c.name, self._frame(reading, c.name), info.jpeg_quality)
                    for c in self.cameras
                ],
                "sent": {k: _number(v) for k, v in sent.items() if k in self.motors},
            },
        )
        body = wire.dumps(request)
        if len(body) > wire.MAX_BODY_BYTES:
            raise ValueError(
                f"a step of {len(body)} bytes is more than the {wire.MAX_BODY_BYTES} the policy "
                "server reads: give the cameras smaller frames"
            )
        started = time.perf_counter()
        raw = self._exchange("POST", wire.STEP_PATH, body, self.step_timeout_s)
        self.last_rtt_s = time.perf_counter() - started
        self._trips += 1
        self._trip_total_s += self.last_rtt_s
        self._trip_longest_s = max(self._trip_longest_s, self.last_rtt_s)
        reply = self._read(wire.StepReply, self._json(raw, wire.STEP_PATH), wire.STEP_PATH)
        self.last_inference_s = reply.inference_s
        if reply.session != self.session or reply.seq != seq:
            self.dropped += 1
            return Chunk(observation.tick)
        if reply.chunk is None:
            return Chunk(observation.tick, done=True)
        unknown = sorted({k for action in reply.chunk for k in action} - set(self.motors))
        if unknown:
            raise self._fail(
                f"{self.url} answered with goals for {', '.join(unknown)}, which are not motors "
                "of this arm"
            )
        return Chunk(observation.tick, tuple(dict(action) for action in reply.chunk))

    # ── the record ──────────────────────────────────────────────────────────────────────

    def record(self) -> dict[str, Any]:
        """The server and what it serves, as a run's record names them, from what it said when
        it was last asked and with nothing asked now: its address, redacted, whether the arm
        took a policy learned on another arm's frame (`--accept-other-frame`), the policy, the
        rate it runs at and where that rate came from, every repository it loaded at the
        revision it loaded it at, and the JPEG quality frames go to it at, or None for raw
        frames. The address and the override alone before the server has answered."""
        said: dict[str, Any] = {
            "server": self.url,
            "accept_other_frame": self.accept_other_frame,
        }
        info = self.info
        if info is not None:
            said.update(
                policy=info.policy,
                rate_hz=info.rate_hz,
                rate_source=info.rate_source,
                loaded=list(info.loaded),
                jpeg_quality=info.jpeg_quality,
            )
        return said

    def round_trips(self) -> dict[str, Any] | None:
        """How many steps went to the server and came back, and their mean and longest round
        trip in milliseconds, on the wall's clock and the whole exchange included, or None before
        the first. A report, and never a latency anything is set from: the latency a segment is
        paced by is the one the server declares."""
        if not self._trips:
            return None
        return {
            "count": self._trips,
            "mean": round(1000.0 * self._trip_total_s / self._trips, 1),
            "max": round(1000.0 * self._trip_longest_s, 1),
        }

    def close(self) -> None:
        """End this client's session, so another client may start one at once rather than
        after the session has been quiet for the server's lease, and close the connection. A
        server that is gone or does not answer is no error here: its session ends with it."""
        session, self.session = self.session, None
        try:
            if session is not None:
                request = wire.dumps(wire.EndRequest(session=session))
                self._exchange("POST", wire.END_PATH, request, self.call_timeout_s)
        except PolicyServerError:
            pass
        finally:
            self._drop()

    # ── the observation ─────────────────────────────────────────────────────────────────

    def _state(self, reading: Mapping[str, Any]) -> list[float]:
        state: list[float] = []
        for motor in self.motors:
            value = reading.get(f"{motor}.pos")
            number = _number(value)
            if not math.isfinite(number):
                raise ValueError(
                    f"the arm's reading of {motor} is {value!r}, not a finite number, so it was "
                    "not sent to the policy"
                )
            state.append(number)
        return state

    @staticmethod
    def _message(model: type[M], fields: dict[str, Any]) -> M:
        """A request, validated as the server will validate it, so one the server would refuse
        is a ValueError here and never sent: a number that is not finite above all."""
        try:
            return wire.validate(model, fields)
        except wire.ProtocolError as e:
            raise ValueError(f"the policy could not be asked: {e}") from None

    @staticmethod
    def _frame(reading: Mapping[str, Any], name: str) -> Any:
        if name not in reading:
            raise ValueError(f"the observation has no frame from the {name} camera")
        return reading[name]

    def _info(self) -> wire.PolicyInfo:
        if self.info is None:
            raise PolicyServerError("the policy was asked about itself before any reset")
        return self.info

    # ── the wire ────────────────────────────────────────────────────────────────────────

    def _fail(self, message: str, *, status: int | None = None) -> PolicyServerError:
        """Every error this client raises is built here, with the token taken out of it."""
        return PolicyServerError(message.replace(self._token, "<token>"), status=status)

    def _said(self, text: str, limit: int = 200) -> str:
        """A string the server sent, made safe for a sentence on somebody's terminal: the token
        out first, since a cut could fall inside it, then control characters, then the length."""
        flat = "".join(c if c.isprintable() else " " for c in text.replace(self._token, "<token>"))
        return flat if len(flat) <= limit else flat[: limit - 3] + "..."

    def _json(self, raw: bytes, path: str) -> Any:
        try:
            return wire.loads(raw)
        except wire.ProtocolError as e:
            said = self._said(str(e))
            raise self._fail(
                f"{self.url} answered {path} with a reply the protocol refuses: {said}"
            ) from None

    def _read(self, model: type[M], obj: Any, path: str) -> M:
        try:
            return wire.validate(model, obj)
        except wire.ProtocolError as e:
            said = self._said(str(e))
            raise self._fail(
                f"{self.url} answered {path} with a reply the protocol refuses: {said}"
            ) from None

    def _connection(self, timeout_s: float) -> tuple[http.client.HTTPConnection, bool]:
        """The kept-alive connection, or a new one, and whether it is new."""
        if self._conn is not None:
            return self._conn, False
        conn: http.client.HTTPConnection
        if self.scheme == "https":
            conn = http.client.HTTPSConnection(
                self.host,
                self.port,
                timeout=self.connect_timeout_s,
                context=ssl.create_default_context(),
            )
        else:
            conn = http.client.HTTPConnection(self.host, self.port, timeout=self.connect_timeout_s)
        conn.response_class = self._reply_class
        conn.connect()
        if conn.sock is not None:
            conn.sock.settimeout(timeout_s)
        self._conn = conn
        return conn, True

    def _drop(self) -> None:
        conn, self._conn = self._conn, None
        if conn is not None:
            with contextlib.suppress(Exception):
                conn.close()

    def _exchange(self, method: str, path: str, body: bytes | None, timeout_s: float) -> bytes:
        """One request and its whole reply, which is a 200's body or an error that says why
        not. A reused connection that turns out to be closed is replaced once."""
        headers = {wire.TOKEN_HEADER: self._token, "Accept": "application/json"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        for attempt in (0, 1):
            deadline = time.monotonic() + timeout_s
            try:
                conn, fresh = self._connection(timeout_s)
            except TimeoutError:
                self._drop()
                raise self._fail(
                    f"{self.url} did not take a connection within {self.connect_timeout_s:g} s: "
                    "is quackd policy serve running there, and the tunnel up?"
                ) from None
            except ConnectionRefusedError:
                self._drop()
                raise self._fail(
                    f"nothing is listening at {self.url}: start quackd policy serve, or point "
                    "--policy-url at the port it printed"
                ) from None
            except (OSError, http.client.HTTPException) as e:
                self._drop()
                raise self._fail(
                    f"{self.url} could not be reached: {self._said(str(e) or type(e).__name__)}"
                ) from None
            try:
                if conn.sock is not None:
                    conn.sock.settimeout(timeout_s)  # a sendall's whole send, since Python 3.5
                self._deadline.at = deadline  # and the reply's whole read (`_BoundedReader`)
                self._send(conn, method, path, body, headers)
                response = conn.getresponse()
                status, raw = self._whole(response, path)
            except RETRIED:
                self._drop()
                if fresh or attempt:
                    raise self._fail(
                        f"{self.url} closed the connection before it answered {path}"
                    ) from None
                continue  # a kept-alive socket the server had closed: once more on a new one
            except TimeoutError:
                self._drop()
                raise self._fail(
                    f"{self.url} did not answer {path} within {timeout_s:g} s"
                ) from None
            except PolicyServerError:
                self._drop()
                raise
            except (OSError, http.client.HTTPException) as e:
                self._drop()
                raise self._fail(
                    f"{self.url} failed on {path}: {self._said(str(e) or type(e).__name__)}"
                ) from None
            if response.will_close:
                self._drop()
            if status != 200:
                raise self._refusal(status, raw, path)
            return raw
        raise AssertionError("unreachable: the second attempt returns or raises")

    def _send(
        self,
        conn: http.client.HTTPConnection,
        method: str,
        path: str,
        body: bytes | None,
        headers: Mapping[str, str],
    ) -> None:
        """One request onto `conn`. `http.client` refuses a header it cannot carry with a
        ValueError that quotes the header's value, which could be the token, so that one is
        answered here without it. `clean_token` keeps it from happening at all, and a
        ValueError anywhere after the send is the reply's, not the request's."""
        try:
            conn.request(method, self._prefix + path, body=body, headers=headers)
        except ValueError:
            raise self._fail(
                f"{self.url} was not asked {path}: the request had a character HTTP cannot carry"
            ) from None

    def _whole(self, response: http.client.HTTPResponse, path: str) -> tuple[int, bytes]:
        """A reply's status and body, read a chunk at a time and never past `MAX_REPLY_BYTES`,
        so a server sending too many is cut off. The call's deadline is enforced under every
        read (`_BoundedReader`), so a server trickling bytes is cut off too. A Content-Length
        that is not a count of bytes in ASCII digits is refused rather than read, which
        `http.client` would do by reading to the end instead: this server never sends one."""
        declared = response.getheader("Content-Length")
        if declared is not None and not (declared.isascii() and declared.isdigit()):
            raise self._fail(
                f"{self.url} answered {path} with a Content-Length that is not a byte count, "
                "which quackd policy serve never sends: is it what is listening there?"
            )
        if declared is not None and (
            len(declared) > wire.MAX_INT_DIGITS or int(declared) > wire.MAX_REPLY_BYTES
        ):
            raise self._fail(
                f"{self.url} sent {self._said(declared, 24)} bytes for {path}, more than the "
                f"{wire.MAX_REPLY_BYTES} any reply of {wire.PROTOCOL}'s comes near"
            )
        chunks: list[bytes] = []
        size = 0
        while True:
            chunk = response.read(READ_CHUNK_BYTES)
            if not chunk:
                break
            size += len(chunk)
            if size > wire.MAX_REPLY_BYTES:
                raise self._fail(
                    f"{self.url} sent more than {wire.MAX_REPLY_BYTES} bytes for {path}, more "
                    f"than any reply of {wire.PROTOCOL}'s comes near"
                )
            chunks.append(chunk)
        return int(response.status), b"".join(chunks)

    def _refusal(self, status: int, raw: bytes, path: str) -> PolicyServerError:
        try:
            said = wire.loads(raw)
        except wire.ProtocolError:
            said = None
        reason = said.get("reason") if isinstance(said, dict) else None
        text = self._said(reason) if isinstance(reason, str) and reason.strip() else None
        if status == 401:
            message = (
                f"{self.url} refused the token it was given: give it the one in the token file "
                f"quackd policy serve printed, with --policy-token or {wire.TOKEN_ENV}"
            )
        elif 300 <= status < 400:
            message = (
                f"{self.url} answered {path} with a redirect, which {wire.PROTOCOL} never sends, "
                "so it was not followed"
            )
        elif text:
            message = f"{self.url} refused {path}: {text}"
        else:
            message = f"{self.url} answered {path} with HTTP {status}"
        return self._fail(message, status=status)


def _number(value: Any) -> float:
    """`value` as a float when it is a real number, whatever library made it, and never a bool,
    which Python counts as one; NaN otherwise, which the request's validation refuses."""
    if isinstance(value, numbers.Real) and not isinstance(value, bool):
        return float(value)
    return math.nan


__all__ = [
    "CALL_TIMEOUT_S",
    "CONNECT_TIMEOUT_S",
    "STEP_TIMEOUT_S",
    "PolicyServerError",
    "RemoteRunner",
    "client_token",
    "policy_address",
]
