"""Talk, step one: messages, and the hello that opens a session (spec 6.1 to 6.4).

A message is a small JSON object, signed as a compact JWS by its sender's robot key: an unsigned
one is refused. Two robots open a session with a three-step hello in which each carries its
owner-signed passport and signs a nonce the other has just made up, so each proves it holds the
key its passport names, and every later message carries both nonces, so one replayed from
another session matches nothing and is refused. A session needs a clock that counts the seconds
it has been quiet, and reading a passport needs one that knows the date (spec 4.5).

The transport is the caller's. An endpoint takes what arrived and returns what to send back, so
the same session runs over an HTTP inbox, a Bluetooth link or a test, and quackd's glue decides
what a person and a pilot get to see of it.
"""

from __future__ import annotations

import os
import re
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from quackd.passport import jws, strict
from quackd.passport.document import MAJOR, VERSION, Reading, check_id, read_jws
from quackd.passport.errors import PassportError
from quackd.passport.keys import SigningKey

MESSAGE_TYP = "robot-passport-msg+jws"
#: The largest message, and the largest hello, which carries a whole signed passport (spec 6.1).
MAX_MESSAGE_BYTES = 4096
MAX_HELLO_BYTES = 16_384
TYPES = ("hello", "say", "bye", "ask", "answer", "share")
SAY_CHARS = 400
#: quackd ends a session after ten quiet minutes, and gives a hello half a minute to finish.
IDLE_S = 600.0
HELLO_S = 30.0
#: How many sessions, and half-finished hellos, one robot holds at once. New keys cost nothing,
#: so without a bound a flood of hellos would be a flood of memory.
MAX_SESSIONS = 64
MAX_PENDING = 64
#: How many messages a robot remembers having seen, to drop a second copy: hellos and anything
#: outside a session in one memory, and each session's messages in its own, so 64 sessions at
#: most hold 16,384 of them.
REMEMBERED = 1024
REMEMBERED_PER_SESSION = 256
SIGNED_TYPE = "application/jose"
PLAIN_TYPE = "application/json"

_VERSION = re.compile(r"^(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})$")
_TOKEN = re.compile(r"^[A-Za-z0-9_-]{16,64}$")
_NONCE = re.compile(r"^[A-Za-z0-9_-]{22,64}$")
_SKILL = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
#: The beacon id a robot's radio advertises now: eight random bytes, which rotate (spec 5.6).
_BEACON = re.compile(r"^[0-9a-f]{16}$")
#: The runtime members a hello may carry beside its passport, which change without the owner
#: signing anything: the software that wrote it, its beacon id and how to reach it (6.3).
RUNTIME = ("software", "beacon", "reach")


@dataclass(frozen=True)
class Message:
    """A message as a receiver read it: its envelope, its body, and whether it was signed."""

    type: str
    sender: str
    msg: str
    body: dict[str, Any]
    signed: bool
    to: str | None = None
    re: str | None = None
    nonces: tuple[str, str] | None = None
    version: str = VERSION


class VersionUnknown(PassportError):
    """A message in a MAJOR this robot does not read. It is answered, not dropped (spec 6.7)."""

    def __init__(self, version: str, sender: str | None, msg: str | None) -> None:
        super().__init__("6.7", f"version {version} has a MAJOR this robot does not read")
        self.version, self.sender, self.msg = version, sender, msg


class Answered(PassportError):
    """A hello the other robot answered with a status rather than a hello. `status` says whether
    to try again: 403 (it has not heard this robot by radio yet), 429 and 503 mean not now, and
    anything else means no."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__("6.3", message)
        self.status = status


def new_token() -> str:
    """16 random bytes in base64url: a nonce, or a message's `msg`."""
    return jws.b64url(os.urandom(16))


def envelope(
    kind: str,
    sender: str,
    body: dict[str, Any],
    *,
    to: str | None = None,
    re: str | None = None,
    nonces: tuple[str, str] | None = None,
    msg: str | None = None,
) -> dict[str, Any]:
    """A message's members, in the order spec 6.1 lists them."""
    doc: dict[str, Any] = {"passport": VERSION, "type": kind, "from": sender}
    if to is not None:
        doc["to"] = to
    doc["msg"] = msg or new_token()
    if re is not None:
        doc["re"] = re
    if nonces is not None:
        doc["nonces"] = list(nonces)
    doc["body"] = body
    return doc


def seal(doc: dict[str, Any], key: SigningKey) -> str:
    """A message as it goes on the wire: a compact JWS signed by the robot key its `from` holds,
    since every message is signed (spec 6.1)."""
    if doc.get("from") != key.did:
        raise ValueError("a message is signed by the key its from holds")
    return jws.sign(strict.dumps(doc), key, MESSAGE_TYP)


def media_type(wire: str) -> str:
    return SIGNED_TYPE if jws.looks_like_jws(wire) else PLAIN_TYPE


def read_message(data: bytes | str, *, limit: int = MAX_HELLO_BYTES) -> Message:
    """A message as it arrived, checked against spec 6.1 and its type's body.

    Every message is signed by its sender's robot key, and the signature is verified before
    anything in the message is believed: an unsigned one is refused, and a signed one must be
    signed by the key its `from` holds."""
    raw = data.encode("utf-8") if isinstance(data, str) else data
    if len(raw) > limit:
        raise PassportError("6.1", f"{len(raw)} bytes, over the {limit} a message may be")
    text = raw.decode("utf-8", errors="replace")
    if not jws.looks_like_jws(text):
        raise PassportError("6.1", "an unsigned message: every one is signed by its robot key")
    verified = jws.verify_compact(text, MESSAGE_TYP, rule="6.1", payload_limit=limit)
    doc = strict.loads(verified.payload, limit=limit)
    kid = verified.kid
    if not isinstance(doc, dict):
        raise PassportError("6.1", "a message is a JSON object")
    sender, msg, version = doc.get("from"), doc.get("msg"), doc.get("passport")
    if not isinstance(version, str) or not _VERSION.match(version):
        raise PassportError("6.1", f"passport is {version!r:.40}, not a MAJOR.MINOR version")
    if int(version.split(".")[0]) != MAJOR:
        # answered only at an id, never at whatever text a message wrote in its `from`
        try:
            answer_to: str | None = _id(sender, "from")
        except PassportError:
            answer_to = None
        raise VersionUnknown(
            version, answer_to, msg if isinstance(msg, str) and _TOKEN.match(msg) else None
        )
    kind = doc.get("type")
    if not isinstance(kind, str) or not 0 < len(kind) <= 16:
        raise PassportError("6.1", "type is not a short string")
    sender = _id(sender, "from")
    if kid != sender:
        raise PassportError("6.1", "it was signed by a key other than the one its from holds")
    to = doc.get("to")
    if to is not None and to != "*":
        to = _id(to, "to")
    if not isinstance(msg, str) or not _TOKEN.match(msg):
        raise PassportError("6.1", "msg is not a token of 16 to 64 base64url characters")
    answers = doc.get("re")
    if answers is not None and (not isinstance(answers, str) or not _TOKEN.match(answers)):
        raise PassportError("6.1", "re is not a token of 16 to 64 base64url characters")
    nonces = doc.get("nonces")
    pair: tuple[str, str] | None = None
    if nonces is not None:
        if not (
            isinstance(nonces, list)
            and len(nonces) == 2
            and all(isinstance(n, str) and _NONCE.match(n) for n in nonces)
        ):
            raise PassportError("6.1", "nonces is not the session's two nonces")
        pair = (nonces[0], nonces[1])
    body = doc.get("body")
    if not isinstance(body, dict):
        raise PassportError("6.1", "body is not an object")
    if kind != "hello" and len(raw) > MAX_MESSAGE_BYTES:
        raise PassportError(
            "6.1", f"{len(raw)} bytes, over the {MAX_MESSAGE_BYTES} any message but a hello may be"
        )
    message = Message(kind, sender, msg, body, True, to, answers, pair, version)
    if kind in _BODIES:
        _BODIES[kind](message)
    return message


def _id(value: object, where: str) -> str:
    try:
        return check_id(value, where)
    except PassportError as e:
        raise PassportError("6.1", str(e)) from e


def _text(body: dict[str, Any], name: str, limit: int, *, rule: str, required: bool) -> None:
    if name not in body:
        if required:
            raise PassportError(rule, f"its body has no {name}")
        return
    value = body[name]
    if not isinstance(value, str) or not 0 < len(value) <= limit:
        raise PassportError(rule, f"{name} is not text of 1 to {limit} characters")


def _in_session(m: Message, rule: str) -> None:
    if m.nonces is None:
        raise PassportError(rule, f"a {m.type} belongs to a session and carries its nonces")


def _hello(m: Message) -> None:
    passport = m.body.get("passport")
    if passport is not None and not isinstance(passport, str):
        raise PassportError("6.3", "a hello carries its sender's passport as text")
    if m.re is None:
        if passport is None:
            raise PassportError("6.3", "the first hello carries its sender's passport")
        nonce = m.body.get("nonce")
        if not isinstance(nonce, str) or not _NONCE.match(nonce):
            raise PassportError("6.3", "the first hello carries a nonce of 16 random bytes")
    else:
        _in_session(m, "6.3")
    _runtime(m.body)


def _runtime(body: dict[str, Any]) -> None:
    """A hello's runtime members (6.3): which software wrote it, the beacon id its radio
    advertises now, and how to reach it, a few short scalars that no owner signs."""
    software = body.get("software")
    if software is not None and (not isinstance(software, str) or not 0 < len(software) <= 64):
        raise PassportError("6.3", "software is not text of 1 to 64 characters")
    beacon = body.get("beacon")
    if beacon is not None and (not isinstance(beacon, str) or not _BEACON.match(beacon)):
        raise PassportError("6.3", "beacon is not 16 lower-case hexadecimal characters")
    reach = body.get("reach")
    if reach is None:
        return
    if not isinstance(reach, dict) or len(reach) > 4:
        raise PassportError("6.3", "reach is not an object of at most four members")
    for key, value in reach.items():
        short = isinstance(value, int | bool) or (isinstance(value, str) and len(value) <= 64)
        if not isinstance(key, str) or len(key) > 16 or not short:
            raise PassportError("6.3", "reach holds something other than a few short scalars")
    port = reach.get("http_port")
    if port is not None and not (
        isinstance(port, int) and not isinstance(port, bool) and 0 < port < 65_536
    ):
        raise PassportError("6.3", "reach.http_port is not a port")


def runtime_of(body: dict[str, Any]) -> dict[str, Any]:
    """The runtime members of a hello's body, already checked by `read_message`."""
    return {k: body[k] for k in RUNTIME if k in body}


def _say(m: Message) -> None:
    _in_session(m, "6.4")
    _text(m.body, "text", SAY_CHARS, rule="6.4", required=True)


def _bye(m: Message) -> None:
    _in_session(m, "6.4")


def _ask(m: Message) -> None:
    _in_session(m, "6.5")
    skill = m.body.get("skill")
    if not isinstance(skill, str) or not _SKILL.match(skill):
        raise PassportError("6.5", "skill is not a skill name")
    if "args" in m.body and not isinstance(m.body["args"], dict):
        raise PassportError("6.5", "args is not an object")
    _text(m.body, "why", 120, rule="6.5", required=False)


def _answer(m: Message) -> None:
    if m.re is None:
        raise PassportError("6.5", "an answer names the message it answers")
    if m.body.get("result") not in ("ok", "no", "later"):
        raise PassportError("6.5", "result is not ok, no or later")
    _text(m.body, "reason", 120, rule="6.5", required=False)
    versions = m.body.get("versions")
    if versions is not None and not (
        isinstance(versions, list)
        and len(versions) <= 8
        and all(isinstance(v, str) and 0 < len(v) <= 16 for v in versions)
    ):
        raise PassportError("6.7", "versions is not a list of versions")


def _share(m: Message) -> None:
    _in_session(m, "6.6")
    _text(m.body, "about", 64, rule="6.6", required=True)
    _text(m.body, "fact", 200, rule="6.6", required=True)
    _text(m.body, "place", 64, rule="6.6", required=False)
    _text(m.body, "confidence", 16, rule="6.6", required=False)
    age = m.body.get("age_s")
    if age is not None and (not isinstance(age, int) or isinstance(age, bool) or age < 0):
        raise PassportError("6.6", "age_s is not a number of seconds")


_BODIES: dict[str, Callable[[Message], None]] = {
    "hello": _hello,
    "say": _say,
    "bye": _bye,
    "ask": _ask,
    "answer": _answer,
    "share": _share,
}


# ── sessions ────────────────────────────────────────────────────────────────────────────


@dataclass
class Session:
    """Two robots that finished a hello: who the other is, the session's nonces, its passport,
    and when it last spoke. `route` is the caller's, for where to reach the other robot, and
    `runtime` what its hello said about itself that no owner signs (6.3)."""

    peer: str
    nonces: tuple[str, str]
    started_here: bool
    passport: Reading
    last: float
    route: Any = None
    runtime: dict[str, Any] = field(default_factory=dict)
    #: the caller's too: what it made of the other robot, its tier, kept for the session's life
    judged: Any = None
    #: the messages seen in this session, so a repeat is dropped, which no other peer's
    #: messages can push out (`REMEMBERED_PER_SESSION`)
    seen: OrderedDict[str, None] = field(default_factory=OrderedDict)


@dataclass(frozen=True)
class Event:
    """Something a person or a pilot may be told: a robot met, one that spoke, one that left."""

    kind: str
    session: Session
    text: str | None = None
    message: Message | None = None


@dataclass(frozen=True)
class Reply:
    """What receiving a message comes to: a status as HTTP spells it, perhaps a message to send
    back, perhaps an event, and why, for a log."""

    status: int
    wire: str | None = None
    event: Event | None = None
    reason: str = ""


@dataclass
class _Started:
    """A hello this robot sent, waiting for the second step."""

    peer: str | None
    nonce: str
    at: float


@dataclass
class _Answered:
    """A hello this robot answered, waiting for the third step."""

    peer: str
    nonces: tuple[str, str]
    passport: Reading
    first: str
    wire: str
    at: float
    runtime: dict[str, Any] = field(default_factory=dict)


class Endpoint:
    """One robot's side of talk: its sessions, the hellos it has half finished, and the messages
    it has already seen, so that a repeat is dropped. Safe to call from several threads."""

    def __init__(
        self,
        me: str,
        passport: str,
        key: SigningKey,
        *,
        clock: Callable[[], float] = time.monotonic,
        wall: Callable[[], float] = time.time,
        token: Callable[[], str] = new_token,
        idle_s: float = IDLE_S,
        runtime: dict[str, Any] | None = None,
    ) -> None:
        if key.did != me:
            raise ValueError("an endpoint signs with the key its robot's id holds")
        self.me, self.passport, self.key = me, passport, key
        self.clock, self.wall, self.token, self.idle_s = clock, wall, token, idle_s
        #: what this robot's hellos say beside its passport, which the caller keeps current:
        #: the beacon id rotates, and the port is known only once the server listens (6.3)
        self.runtime: dict[str, Any] = dict(runtime or {})
        self.sessions: dict[tuple[str, str], Session] = {}
        self._started: dict[str, _Started] = {}
        self._answered: dict[str, _Answered] = {}
        self._seen: OrderedDict[tuple[str, str], None] = OrderedDict()
        self._lock = threading.RLock()

    # ── what this robot sends ───────────────────────────────────────────────────────────

    def hello(self, peer: str | None = None) -> str:
        """The first hello, to `peer`, or to anyone listening on a shared channel. Its answer
        goes to `receive`, which returns the third step to send."""
        with self._lock:
            self._forget_stale()
            if len(self._started) >= MAX_PENDING:
                raise PassportError("6.3", "too many hellos are waiting for an answer")
            nonce = self.token()
            doc = envelope(
                "hello",
                self.me,
                {"passport": self.passport, "nonce": nonce, **self.runtime},
                to=peer or "*",
                msg=self.token(),
            )
            self._started[doc["msg"]] = _Started(peer, nonce, self.clock())
            return seal(doc, self.key)

    def say(self, peer: str, text: str) -> str:
        """A `say` to `peer` in the session this robot has with it."""
        if not 0 < len(text) <= SAY_CHARS:
            raise ValueError(f"a say is 1 to {SAY_CHARS} characters")
        return self._in_session_message(peer, "say", {"text": text})

    def ask(
        self, peer: str, skill: str, args: dict[str, Any] | None = None, why: str | None = None
    ) -> tuple[str, str]:
        """An `ask` to `peer` for a courtesy (spec 6.5), and the `msg` its answer will name."""
        body: dict[str, Any] = {"skill": skill}
        if args:
            body["args"] = dict(args)
        if why:
            body["why"] = why[:120]
        wire = self._in_session_message(peer, "ask", body)
        return wire, str(json_payload(wire)["msg"])

    def answer(self, peer: str, re: str, result: str, reason: str = "") -> str:
        """The `answer` to the `ask` whose `msg` is `re`: `ok`, `no` or `later`, and why."""
        body: dict[str, Any] = {"result": result}
        if reason:
            body["reason"] = reason[:120]
        return self._in_session_message(peer, "answer", body, re=re)

    def share(self, peer: str, about: str, fact: str, **more: Any) -> str:
        """A `share` to `peer`: something seen, where and how long ago, and never a coordinate,
        since two robots that just met share no frame of reference (spec 6.6)."""
        body: dict[str, Any] = {"about": about[:64], "fact": fact[:200]}
        body.update({k: v for k, v in more.items() if v is not None})
        return self._in_session_message(peer, "share", body)

    def bye(self, peer: str) -> str:
        """A `bye` to `peer`, ending the session this robot has with it."""
        with self._lock:
            wire = self._in_session_message(peer, "bye", {})
            session = self.session_with(peer)
            if session is not None:
                del self.sessions[session.nonces]
            return wire

    def _in_session_message(
        self, peer: str, kind: str, body: dict[str, Any], *, re: str | None = None
    ) -> str:
        with self._lock:
            session = self.session_with(peer)
            if session is None:
                raise PassportError("6.3", f"no session with {peer}: say hello first")
            session.last = self.clock()
            doc = envelope(
                kind, self.me, body, to=peer, re=re, nonces=session.nonces, msg=self.token()
            )
            return seal(doc, self.key)

    def session_with(self, peer: str) -> Session | None:
        """The live session with `peer`, the most recent if there are two."""
        with self._lock:
            live = [s for s in self.sessions.values() if s.peer == peer and not self._quiet(s)]
            return max(live, key=lambda s: s.last) if live else None

    def expire(self) -> list[Session]:
        """End the sessions that went quiet, and forget hellos nobody finished."""
        with self._lock:
            ended = [s for s in self.sessions.values() if self._quiet(s)]
            for session in ended:
                del self.sessions[session.nonces]
            self._forget_stale()
            return ended

    def _quiet(self, session: Session) -> bool:
        return self.clock() - session.last > self.idle_s

    def _forget_stale(self) -> None:
        now = self.clock()
        for table in (self._started, self._answered):
            for msg in [m for m, pending in table.items() if now - pending.at > HELLO_S]:
                del table[msg]

    # ── what this robot receives ────────────────────────────────────────────────────────

    def receive(self, message: Message) -> Reply:
        """What `message` comes to. A refusal is a reply with a status, never an exception, so
        the caller only has to carry it back the way the message came."""
        with self._lock:
            if message.to not in (None, "*", self.me):
                return Reply(403, reason="it is addressed to another robot")
            if message.type not in TYPES:
                return Reply(204, reason=f"a {message.type} is a type this robot ignores")
            memory, seen, limit = self._memory(message)
            if seen in memory:
                return self._repeat(message) or Reply(204, reason="a repeat, dropped")
            try:
                reply = self._hello(message) if message.type == "hello" else self._spoken(message)
            except PassportError as e:
                return Reply(400, reason=f"{e.rule}: {e}")
            if reply.status < 300:
                memory[seen] = None
                while len(memory) > limit:
                    memory.popitem(last=False)
            return reply

    def _memory(self, message: Message) -> tuple[OrderedDict[Any, None], Any, int]:
        """Where `message` is remembered, to drop a second copy: its session's own memory when
        it belongs to one, and the shared one when it does not, which is a hello. One shared
        memory let any peer push another's messages out of it with a thousand of its own, and
        then replay one into that robot's session as if it were that robot."""
        session = self.sessions.get(message.nonces) if message.nonces is not None else None
        if session is not None and session.peer == message.sender:
            return session.seen, message.msg, REMEMBERED_PER_SESSION
        return self._seen, (message.sender, message.msg), REMEMBERED

    def _repeat(self, message: Message) -> Reply | None:
        """A first hello that came again while its answer is still waiting gets that answer
        again: over HTTP the first answer may simply have been lost on the way back."""
        if message.type == "hello" and message.re is None:
            for answered in self._answered.values():
                if answered.first == message.msg and answered.peer == message.sender:
                    return Reply(200, answered.wire, reason="a repeated hello, answered again")
        return None

    def _hello(self, m: Message) -> Reply:
        self._forget_stale()
        if m.re is None:
            return self._first(m)
        if m.re in self._started:
            return self._second(m)
        if m.re in self._answered:
            return self._third(m)
        return Reply(403, reason="it answers no hello of this robot's, or one that timed out")

    def _first(self, m: Message) -> Reply:
        # Quiet sessions end before the limit is read. A host that never asks who is nearby
        # (`passport serve --talk`) never ended them otherwise, so 64 fresh keys left quiet
        # turned every robot after them away until a restart.
        for quiet in [s for s in self.sessions.values() if self._quiet(s)]:
            del self.sessions[quiet.nonces]
        if len(self._answered) >= MAX_PENDING or len(self.sessions) >= MAX_SESSIONS:
            return Reply(429, reason="this robot is talking to as many robots as it can")
        passport = self._passport_of(m)
        nonces = (str(m.body["nonce"]), self.token())
        doc = envelope(
            "hello",
            self.me,
            {"passport": self.passport, **self.runtime},
            to=m.sender,
            re=m.msg,
            nonces=nonces,
            msg=self.token(),
        )
        wire = seal(doc, self.key)
        self._answered[doc["msg"]] = _Answered(
            m.sender, nonces, passport, m.msg, wire, self.clock(), runtime_of(m.body)
        )
        return Reply(200, wire, reason="hello answered")

    def _second(self, m: Message) -> Reply:
        started = self._started[str(m.re)]
        if m.nonces is None or m.nonces[0] != started.nonce:
            return Reply(403, reason="its nonces are not the ones this robot's hello sent")
        if started.peer is not None and m.sender != started.peer:
            return Reply(403, reason="the robot that answered is not the one the hello was for")
        if "passport" not in m.body:
            raise PassportError("6.3", "the second hello carries its sender's passport")
        passport = self._passport_of(m)
        del self._started[str(m.re)]
        session = self._open(m, passport, started_here=True, runtime=runtime_of(m.body))
        doc = envelope(
            "hello", self.me, {}, to=m.sender, re=m.msg, nonces=m.nonces, msg=self.token()
        )
        return Reply(200, seal(doc, self.key), Event("met", session), "session open")

    def _third(self, m: Message) -> Reply:
        answered = self._answered[str(m.re)]
        if m.sender != answered.peer:
            return Reply(403, reason="the third hello came from another robot than the first")
        if m.nonces != answered.nonces:
            return Reply(403, reason="its nonces are not this session's")
        del self._answered[str(m.re)]
        session = self._open(m, answered.passport, started_here=False, runtime=answered.runtime)
        return Reply(204, event=Event("met", session), reason="session open")

    def _open(
        self, m: Message, passport: Reading, *, started_here: bool, runtime: dict[str, Any]
    ) -> Session:
        assert m.nonces is not None
        session = Session(
            m.sender, m.nonces, started_here, passport, self.clock(), runtime=dict(runtime)
        )
        self.sessions[m.nonces] = session
        return session

    def _passport_of(self, m: Message) -> Reading:
        """The passport a hello carries, read the way a robot reads one: signed by its owner,
        inside its dates (spec 4.4 and 4.5), the passport of the robot that signed the hello, and
        not this robot's own. A passport that needs an extension this robot does not understand
        means no talk (2.6)."""
        carried = m.body.get("passport")
        if not isinstance(carried, str) or not jws.looks_like_jws(carried):
            raise PassportError("6.3", "a hello carries its sender's passport, signed by its owner")
        reading = read_jws(carried, now=int(self.wall()))
        if reading.id != m.sender:
            raise PassportError("6.3", "the passport it carries is another robot's")
        if reading.id == self.me:
            raise PassportError("6.3", "it carries this robot's own passport")
        if not reading.talk_allowed:
            raise PassportError("2.6", "its passport needs an extension this robot cannot read")
        return reading

    def _spoken(self, m: Message) -> Reply:
        if m.nonces is None:
            if m.type == "answer":
                return Reply(204, reason="an answer outside a session, noted")
            return Reply(403, reason="it belongs to no session")
        session = self.sessions.get(m.nonces)
        if session is None or session.peer != m.sender:
            return Reply(403, reason="its nonces match no session of this robot's")
        if self._quiet(session):
            del self.sessions[m.nonces]
            return Reply(403, reason="that session went quiet and ended")
        session.last = self.clock()
        if m.type == "say":
            return Reply(204, event=Event("said", session, str(m.body["text"]), m), reason="said")
        if m.type == "bye":
            del self.sessions[m.nonces]
            return Reply(204, event=Event("left", session, message=m), reason="bye")
        if m.type == "ask":
            # decided by the caller, outside this lock: deciding can take seconds, a pause run
            # through an executor that may itself be waiting to read what this robot heard
            return Reply(204, event=Event("asked", session, message=m), reason="asked")
        return Reply(204, event=Event(m.type, session, message=m), reason=f"a {m.type}, noted")


def json_payload(wire: str) -> dict[str, Any]:
    """The members of a message this robot just wrote, without checking it again."""
    return dict(
        strict.loads(jws.b64url_decode(wire.split(".")[1], rule="6.1"), limit=MAX_HELLO_BYTES)
    )


def version_answer(error: VersionUnknown, me: str, key: SigningKey) -> str:
    """The answer to a message in a version this robot does not read (spec 6.7)."""
    doc = envelope(
        "answer",
        me,
        {"result": "no", "reason": "version", "versions": [VERSION]},
        to=error.sender,
        re=error.msg or new_token(),
    )
    return seal(doc, key)


#: A channel whose reply rides back on the response, an HTTP inbox: send one message, get the
#: status and the reply, if there is one.
Post = Callable[[str], tuple[int, str | None]]


def open_session(endpoint: Endpoint, peer: str | None, post: Post) -> Session:
    """Run the three-step hello over a channel whose reply rides back on the response: send the
    first hello, check the second, send the third. A step that fails raises `PassportError`, and
    no session is left open on this side when the other side refused the third step."""
    status, reply = post(endpoint.hello(peer))
    if status != 200 or reply is None:
        said = f": {reply.strip()[:200]}" if reply and status >= 400 else ""
        raise Answered(status, f"the hello was answered {status}, not with a hello{said}")
    answer = endpoint.receive(read_message(reply))
    if answer.status != 200 or answer.wire is None or answer.event is None:
        raise PassportError("6.3", f"the hello's answer was refused: {answer.reason}")
    session = answer.event.session
    status, _ = post(answer.wire)
    if status not in (200, 204):
        with endpoint._lock:
            endpoint.sessions.pop(session.nonces, None)
        raise Answered(status, f"the third hello was answered {status}")
    return session


__all__ = [
    "MAX_HELLO_BYTES",
    "MAX_MESSAGE_BYTES",
    "MESSAGE_TYP",
    "Answered",
    "Endpoint",
    "Event",
    "Message",
    "Reply",
    "Session",
    "VersionUnknown",
    "envelope",
    "media_type",
    "new_token",
    "open_session",
    "read_message",
    "runtime_of",
    "seal",
    "version_answer",
]
