"""Links: talk between two robots over their radios, requests out and answers back, either end.

A link carries what an HTTP inbox carries, so talk itself never learns which one it went over: a
request is a talk message, its response is the status and the reply the other robot's inbox
gave, and either end may send requests. `quackd.passport.link` is the wire, frames and their
reassembly; this is the link over a `Pipe`, on the radio's event loop. The robot that dialled
opens it, checking that the robot that answers is the one whose advert it heard; the robot that
was dialled accepts it, or refuses it when it is busy. `LinkTable` holds a robot's links, at most
four, and `LinkTransport` is the `ble://` half of talk's transport (`quackd.radio.transport`).

A link is a door anybody in radio range can knock on, so it holds still for very little. A
central whose first request has not been answered within half a minute of opening is closed, and
so is a link that has carried nothing answered for as long as a talk session lasts. It takes
twenty requests in ten seconds and four in the inbox at once, answers at most eight more with a
status, and drops whatever comes past that unanswered. And a link the other robot opened says
nothing about which robot that is until a hello over it bears out the beacon id its OPEN claimed:
until then it is not reported to proximity or to the meeting planner at all, so a stranger who
opens a link in another robot's name and drops it makes nobody near, far or met.
"""

from __future__ import annotations

import asyncio
import contextlib
import enum
import time
from collections import OrderedDict, deque
from collections.abc import Callable, Coroutine
from concurrent.futures import Executor, ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

from quackd.passport.errors import PassportError
from quackd.passport.link import (
    FRAMING,
    HEADER_BYTES,
    MAX_LINKS,
    PERIPHERAL_BIT,
    Abort,
    AbortReason,
    Accept,
    Close,
    CloseReason,
    Data,
    FrameError,
    Given,
    Limits,
    Media,
    Message,
    Open,
    Reassembler,
    Refusal,
    Refuse,
    Role,
    decode,
    encode,
    media_of,
    new_tag,
    split,
)
from quackd.passport.talk import HELLO_S, IDLE_S, json_payload, media_type
from quackd.radio import Pipe, PipeClosed, Radio, RadioUnavailable
from quackd.radio.loop import LoopThread

ROUTE_PREFIX = "ble://"
#: Message ids the other end aborted while this end was still answering them, remembered so the
#: answer is not sent; a peer that aborts more than this many at once is owed no memory of them.
ABORTED_REMEMBERED = 64

#: What a link hands a request to: its bytes and media in, a status, a reply and its media out.
#: It runs on a worker thread, never the radio's loop, since talk's inbox verifies signatures.
LinkInbox = Callable[[bytes, Media], tuple[int, bytes | None, Media]]


class LinkDown(PassportError):
    """A link that failed to carry a message: it dropped, timed out or gave the message up.
    Worth trying again later, as a host that could not be reached is (spec 6.2)."""

    def __init__(self, message: str) -> None:
        super().__init__("6.2", message)


class LinkRefused(PassportError):
    """A robot that would not take a link: busy for now, or not a robot this one should talk to
    over this link at all (spec 5.6). `reason` is its REFUSE, or None when this end refused."""

    def __init__(self, message: str, reason: int | None = None) -> None:
        super().__init__("5.6", message)
        self.reason = reason

    @property
    def busy(self) -> bool:
        return self.reason == Refusal.BUSY


@dataclass(frozen=True)
class LinkPolicy:
    """The clocks and counts a link keeps, each a bound a peer cannot talk it out of."""

    #: how long the central waits for ACCEPT, and the peripheral for OPEN
    accept_s: float = 5.0
    #: how long a link the other robot opened may go before a request of its is answered 2xx:
    #: a hello comes first
    first_request_s: float = HELLO_S
    #: how long a link may carry nothing answered: as long as a quiet talk session lasts
    idle_s: float = IDLE_S
    #: requests one link may have in the inbox at once; the next is answered 503
    inflight: int = 4
    #: requests one link may send in `rate_s`; the next is answered 429
    rate: int = 20
    rate_s: float = 10.0
    #: answers of a status alone (429, 503, 413 and the like) and aborts a link queues at once;
    #: a request past them is dropped unanswered, so a flood costs a bounded queue
    queued: int = 8
    #: how often a link checks its clocks
    tick_s: float = 0.25


def _named(kind: type[enum.IntEnum], value: int) -> str:
    """A reason's name for a log line, or its number when this version has no name for it."""
    return kind(value).name.lower() if value in {int(v) for v in kind} else str(value)


def post_timeout(size: int) -> float:
    """How long a request of `size` bytes may take, there and back: five seconds, and a second
    more for every 1,500 bytes, up to 20. A guess at what a slow link needs, not a measurement:
    no link has been timed on a robot's radio."""
    return min(5.0 + size / 1500, 20.0)


def wire_media(wire: str) -> Media:
    """The media of a talk message as text: a JWS or JSON, as talk's own inbox would label it."""
    return media_of(media_type(wire))


def hello_beacon(body: bytes) -> str | None:
    """The beacon id a first hello says its robot advertises, read without checking anything:
    for use only once talk's own inbox has accepted that hello, which checks every signature."""
    try:
        doc = json_payload(body.decode("ascii"))
    except Exception:  # anything that is not a compact JWS of a JSON object names no beacon
        return None
    if doc.get("type") != "hello" or doc.get("re") is not None:
        return None
    payload = doc.get("body")
    beacon = payload.get("beacon") if isinstance(payload, dict) else None
    return beacon if isinstance(beacon, str) else None


class Link:
    """One link, from either end. Built only by `open` (the central) and `accept` (the
    peripheral); every method runs on the radio's loop."""

    def __init__(
        self,
        pipe: Pipe,
        *,
        central: bool,
        me: str,
        inbox: LinkInbox | None,
        limits: Limits,
        policy: LinkPolicy,
        executor: Executor | None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.pipe = pipe
        self.central = central
        self.me = me
        self.inbox = inbox
        self.limits = limits
        self.policy = policy
        self.clock = clock
        self._executor = executor
        self.tag = 0
        self.peer_beacon = ""
        #: the most this end may send in one datagram, and the largest message the other takes
        self.send_size = pipe.datagram_size
        self.peer_max = limits.max_message
        self.state = "opening" if central else "accepting"
        #: why it closed, once it has
        self.closed: str | None = None
        #: when it opened, and when it last carried something answered either way
        self.opened_at = self.last = clock()
        #: whether a request either way has had an answer of 2xx: a link that is talking
        self.answered = False
        #: whether its table has reported it opened (`LinkTable`)
        self.announced = False
        self.requests_in = 0
        #: frames with another link's tag: notifications for another central
        self.foreign = 0
        #: requests dropped unanswered because the answers already queued were enough
        self.dropped = 0
        self._handshake: asyncio.Future[Accept | Refuse | Open] = (
            asyncio.get_running_loop().create_future()
        )
        self._expect: str | None = None
        self._pending: dict[int, asyncio.Future[Message]] = {}
        self._next_id = 0
        self._inflight = 0
        self._queued = 0
        self._aborted: OrderedDict[int, None] = OrderedDict()
        self._window: deque[float] = deque()
        self._send_lock = asyncio.Lock()
        self._on_closed: list[Callable[[Link], None]] = []
        self._ticker: asyncio.Task[None] | None = None
        #: the tag of a first frame in another framing version, which is refused as such
        self._other_framing: int | None = None
        #: work started and not waited for, held so that none is collected half done
        self._tasks: set[asyncio.Task[None]] = set()
        self._reassembler = Reassembler(
            limits, peer_bit=PERIPHERAL_BIT if central else 0, awaiting=self._pending.__contains__
        )
        pipe.on_datagram(self._on_datagram)
        pipe.on_close(self._on_pipe_closed)

    # ── opening ──────────────────────────────────────────────────────────────────────────

    @property
    def id(self) -> str:
        """This link's name in its robot's table and in a route: its tag, in hex."""
        return f"{self.tag:08x}"

    @property
    def route(self) -> str:
        return ROUTE_PREFIX + self.id

    @property
    def inbound(self) -> bool:
        """Whether the other robot opened it."""
        return not self.central

    @classmethod
    async def open(
        cls,
        pipe: Pipe,
        *,
        me: str,
        expect: str,
        inbox: LinkInbox | None,
        limits: Limits | None = None,
        policy: LinkPolicy | None = None,
        executor: Executor | None = None,
        tag: int | None = None,
    ) -> Link:
        """Open a link as the central over `pipe` to the robot whose advert carried `expect`.
        `LinkRefused` when it refuses, or answers as another beacon id; `LinkDown` when it
        does not answer at all. The pipe is closed on every way out but a link."""
        link = cls(
            pipe,
            central=True,
            me=me,
            inbox=inbox,
            limits=limits or Limits(),
            policy=policy or LinkPolicy(),
            executor=executor,
        )
        link.tag = tag if tag is not None else new_tag()
        link._expect = expect
        try:
            await pipe.send(encode(Open(link.tag, me, pipe.datagram_size, link.limits.max_message)))
            answer = await asyncio.wait_for(link._handshake, link.policy.accept_s)
        except (TimeoutError, PipeClosed, LinkDown) as e:
            await link._shut(f"no answer to the link's OPEN: {e or 'timed out'}", None)
            raise LinkDown(
                f"the robot at {expect} did not take the link within {link.policy.accept_s:g} s"
            ) from e
        except BaseException:
            # cancelled, most likely by a caller's own deadline: the connection goes all the same
            link._finish("cancelled while opening")
            link._spawn(link._close_pipe())
            raise
        if isinstance(answer, Refuse):
            await link._shut("refused", None)
            why = _named(Refusal, answer.reason)
            raise LinkRefused(f"the robot at {expect} refused the link ({why})", answer.reason)
        if answer.beacon_id != expect or link.state != "open":
            await link._shut("it answered as another robot", CloseReason.PROTOCOL)
            raise LinkRefused(
                f"the link to {expect} was answered as {answer.beacon_id}, another beacon id than "
                "the advert it was dialled from"
            )
        return link

    @classmethod
    async def accept(
        cls,
        pipe: Pipe,
        *,
        me: str,
        inbox: LinkInbox | None = None,
        admit: Callable[[Open], int | None] = lambda _open: None,
        inbox_for: Callable[[str], LinkInbox | None] | None = None,
        on_open: Callable[[Link], None] | None = None,
        limits: Limits | None = None,
        policy: LinkPolicy | None = None,
        executor: Executor | None = None,
    ) -> Link | None:
        """Take a link as the peripheral over a `pipe` a central opened, once its OPEN arrives
        and `admit` lets it in (None) rather than naming a `Refusal`. None when it was refused
        or never sent an OPEN. `inbox_for(route)` names the inbox once the route is known, and
        `on_open(link)` hears of the link, both before its ACCEPT goes out."""
        link = cls(
            pipe,
            central=False,
            me=me,
            inbox=inbox,
            limits=limits or Limits(),
            policy=policy or LinkPolicy(),
            executor=executor,
        )
        try:
            opened = await asyncio.wait_for(link._handshake, link.policy.accept_s)
        except (TimeoutError, LinkDown, LinkRefused) as e:
            if link._other_framing is not None:
                # another framing version: said so under the tag it chose, then closed
                with contextlib.suppress(PipeClosed):
                    await pipe.send(encode(Refuse(link._other_framing, Refusal.VERSION)))
            await link._shut(f"no OPEN arrived: {e or 'timed out'}", None)
            return None
        assert isinstance(opened, Open)
        link.tag = opened.tag
        refusal = admit(opened)
        if refusal is not None:
            with contextlib.suppress(PipeClosed):
                await pipe.send(encode(Refuse(opened.tag, refusal)))
            await link._shut(f"refused ({_named(Refusal, refusal)})", None)
            return None
        size = min(opened.datagram_size, pipe.datagram_size)
        # open, with its inbox and in its table, before ACCEPT goes: a central may send its
        # first request the moment it reads ACCEPT, and a notification may reach it while this
        # end is still pacing the next one
        if inbox_for is not None:
            link.inbox = inbox_for(link.route)
        link._begin(opened.beacon_id, size, opened.max_message)
        if on_open is not None:
            on_open(link)
        try:
            await link._send(encode(Accept(opened.tag, me, size, link.limits.max_message)))
        except LinkDown:
            return None
        return link

    def _begin(self, peer_beacon: str, send_size: int, peer_max: int) -> None:
        self.peer_beacon, self.send_size, self.peer_max = peer_beacon, send_size, peer_max
        self.state = "open"
        self.opened_at = self.last = self.clock()
        self._ticker = asyncio.get_running_loop().create_task(self._tick())

    # ── what arrives ──────────────────────────────────────────────────────────────────────

    def _spawn(self, coro: Coroutine[Any, Any, None]) -> None:
        task = asyncio.get_running_loop().create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _on_datagram(self, datagram: bytes) -> None:
        if self.closed is not None:
            return
        if self.state == "accepting":
            self._first_datagram(datagram)
            return
        try:
            frame = decode(datagram)
        except FrameError as e:
            self._protocol_error(f"a datagram that is no frame: {e}")
            return
        if frame.tag != self.tag:
            self.foreign += 1
            return
        if self.state == "opening":
            self._answer_to_open(frame)
            return
        if isinstance(frame, Data):
            self._on_data(frame)
        elif isinstance(frame, Abort):
            self._on_abort(frame)
        elif isinstance(frame, Close):
            self._finish(f"closed by the other robot ({_named(CloseReason, frame.reason)})")
        else:
            self._protocol_error(f"a {type(frame).__name__.upper()} on a link already open")

    def _answer_to_open(self, frame: Any) -> None:
        """The central's ACCEPT or REFUSE. An ACCEPT from the robot that was dialled opens the
        link here and now, so that a request it sends right behind its ACCEPT, which may arrive
        in the same breath, finds a link that is open."""
        if self._handshake.done() or not isinstance(frame, Accept | Refuse):
            return
        if isinstance(frame, Accept) and frame.beacon_id == self._expect:
            self._begin(frame.beacon_id, self.send_size, frame.max_message)
        self._handshake.set_result(frame)

    def _first_datagram(self, datagram: bytes) -> None:
        """A peripheral's first datagram, which has to be an OPEN in a framing it reads."""
        if self._handshake.done():
            return
        try:
            frame = decode(datagram)
        except FrameError as e:
            if len(datagram) >= HEADER_BYTES and datagram[0] >> 4 != FRAMING:
                self._other_framing = int.from_bytes(datagram[1:HEADER_BYTES], "big") or 1
            self._handshake.set_exception(LinkRefused(f"the first frame was not an OPEN: {e}"))
            return
        if not isinstance(frame, Open):
            self._handshake.set_exception(LinkRefused("the first frame was not an OPEN"))
            return
        self._handshake.set_result(frame)

    def _on_data(self, frame: Data) -> None:
        result = self._reassembler.feed(frame, self.clock())
        if result is None:
            return
        if isinstance(result, Given):
            self._gave_up(result)
            return
        if result.role == Role.RESPONSE:
            waiting = self._pending.get(result.msg_id)
            if waiting is not None and not waiting.done():
                waiting.set_result(result)
            return
        self._on_request(result)

    def _gave_up(self, given: Given) -> None:
        if given.reason == AbortReason.TOO_LARGE and given.role == Role.REQUEST:
            self._status_answer(given.msg_id, 413, b"too large for this robot\n")
            return
        self._queue(self._abort(given.msg_id, given.reason))
        waiting = self._pending.get(given.msg_id)
        if waiting is not None and not waiting.done():
            waiting.set_exception(LinkDown(f"its answer broke off: {given.reason.name.lower()}"))

    def _on_abort(self, frame: Abort) -> None:
        waiting = self._pending.get(frame.msg_id)
        if waiting is not None and not waiting.done():
            why = _named(AbortReason, frame.reason)
            waiting.set_exception(LinkDown(f"the other robot gave the message up ({why})"))
            return
        # a request of the other end's, half received or being answered: not sent, then
        self._reassembler.abandon(frame.msg_id)
        self._aborted[frame.msg_id] = None
        while len(self._aborted) > ABORTED_REMEMBERED:
            self._aborted.popitem(last=False)

    def _on_request(self, message: Message) -> None:
        now = self.clock()
        self.requests_in += 1
        while self._window and now - self._window[0] > self.policy.rate_s:
            self._window.popleft()
        if len(self._window) >= self.policy.rate:
            self._status_answer(message.msg_id, 429, b"too many requests on this link\n")
            return
        self._window.append(now)
        if self.inbox is None:
            self._status_answer(message.msg_id, 404, b"no inbox here\n")
            return
        if self._inflight >= self.policy.inflight:
            self._status_answer(message.msg_id, 503, b"busy, ask again\n")
            return
        self._inflight += 1
        self._spawn(self._answer(message, self.inbox))

    def _status_answer(self, msg_id: int, status: int, body: bytes) -> None:
        self._queue(self._respond(msg_id, status, body, Media.TEXT))

    def _queue(self, coro: Coroutine[Any, Any, None]) -> None:
        """Send `coro`'s answer or abort, unless enough are queued already, when it is dropped:
        what a peer floods a link with costs this end at most `policy.queued` of them."""
        if self._queued >= self.policy.queued:
            coro.close()
            self.dropped += 1
            return
        self._queued += 1

        async def counted() -> None:
            try:
                await coro
            finally:
                self._queued -= 1

        self._spawn(counted())

    async def _answer(self, message: Message, inbox: LinkInbox) -> None:
        loop = asyncio.get_running_loop()
        try:
            status, body, media = await loop.run_in_executor(
                self._executor, inbox, message.body, message.media
            )
        except Exception:  # whatever the inbox raised, the other robot gets a status
            status, body, media = 500, b"this robot could not handle that\n", Media.TEXT
        finally:
            self._inflight -= 1
        if body is not None and len(body) > self.peer_max:
            status, body, media = 500, b"the answer was too large for this link\n", Media.TEXT
        if status < 300:
            self.answered, self.last = True, self.clock()
        await self._respond(message.msg_id, status, body or b"", media)

    # ── what this end sends ───────────────────────────────────────────────────────────────

    # the timeout is the link's own rather than a cancellation from outside, because running
    # out of it is something to tell the other end: an ABORT, so it can forget the message and
    # not send what it has not sent of its answer
    async def request(
        self,
        body: bytes,
        media: Media,
        *,
        timeout: float,  # noqa: ASYNC109
    ) -> tuple[int, bytes, Media]:
        """Send a request and wait for its response: its status, bytes and media. `LinkDown`
        when the link drops, the answer breaks off or none comes within `timeout` seconds."""
        if self.state != "open":
            raise LinkDown(f"the link is {self.closed or self.state}")
        if len(body) > self.peer_max:
            raise PassportError(
                "6.1", f"{len(body)} bytes, over the {self.peer_max} the other robot takes"
            )
        msg_id = self._new_id()
        answer: asyncio.Future[Message] = asyncio.get_running_loop().create_future()
        self._pending[msg_id] = answer
        try:
            async with asyncio.timeout(timeout):
                await self._send_message(msg_id, Role.REQUEST, 0, media, body)
                message = await answer
        except TimeoutError:
            if self.closed is None:
                self._queue(self._abort(msg_id, AbortReason.TIMED_OUT))
            raise LinkDown(f"no answer on the link within {timeout:g} s") from None
        finally:
            self._pending.pop(msg_id, None)
        self.last = self.clock()
        if message.status < 300:
            self.answered = True
        return message.status, message.body, message.media

    def _new_id(self) -> int:
        mine = 0 if self.central else PERIPHERAL_BIT
        for _ in range(0x8000):
            self._next_id = self._next_id % 0x7FFF + 1
            if (mine | self._next_id) not in self._pending:
                return mine | self._next_id
        raise LinkDown("every message id on this link is in use")  # pragma: no cover

    async def _respond(self, msg_id: int, status: int, body: bytes, media: Media) -> None:
        if msg_id in self._aborted:
            del self._aborted[msg_id]
            return
        with contextlib.suppress(LinkDown):
            await self._send_message(msg_id, Role.RESPONSE, status, media, body)

    async def _abort(self, msg_id: int, reason: AbortReason) -> None:
        with contextlib.suppress(LinkDown):
            await self._send(encode(Abort(self.tag, msg_id, reason)))

    async def _send_message(
        self, msg_id: int, role: Role, status: int, media: Media, body: bytes
    ) -> None:
        datagrams = split(
            body,
            tag=self.tag,
            msg_id=msg_id,
            role=role,
            status=status,
            media=media,
            datagram_size=self.send_size,
        )
        async with self._send_lock:
            for i, datagram in enumerate(datagrams):
                # a write without response for each but the last, which is a write request:
                # the other end's stack pushes back once a message rather than once a datagram
                await self._raw(datagram, barrier=i == len(datagrams) - 1)

    async def _send(self, datagram: bytes) -> None:
        async with self._send_lock:
            await self._raw(datagram, barrier=True)

    async def _raw(self, datagram: bytes, *, barrier: bool) -> None:
        if self.closed is not None:
            raise LinkDown(f"the link is {self.closed}")
        try:
            await self.pipe.send(datagram, barrier=barrier)
        except PipeClosed as e:
            self._finish(f"lost: {e}")
            raise LinkDown(f"the link dropped: {e}") from e

    # ── clocks and closing ────────────────────────────────────────────────────────────────

    async def _tick(self) -> None:
        while self.closed is None:
            await asyncio.sleep(self.policy.tick_s)
            if self.closed is not None:
                return
            now = self.clock()
            for given in self._reassembler.expire(now):
                self._gave_up(given)
            unheard = self.inbound and not self.answered
            if unheard and now - self.opened_at > self.policy.first_request_s:
                await self.close(CloseReason.IDLE)
                return
            if now - self.last > self.policy.idle_s:
                await self.close(CloseReason.IDLE)
                return

    def on_closed(self, cb: Callable[[Link], None]) -> None:
        """Call `cb(link)` once the link has closed, from whichever end, at once if it has."""
        if self.closed is not None:
            cb(self)
        else:
            self._on_closed.append(cb)

    async def close(self, reason: CloseReason = CloseReason.DONE) -> None:
        """End the link, telling the other end why when it can still be told."""
        if self.closed is not None:
            return
        await self._shut(f"closed here ({reason.name.lower()})", reason)

    async def _shut(self, why: str, reason: CloseReason | None) -> None:
        if reason is not None and self.closed is None and self.tag:
            with contextlib.suppress(LinkDown, PipeClosed, TimeoutError):
                async with asyncio.timeout(1.0):
                    await self._raw(encode(Close(self.tag, reason)), barrier=True)
        self._finish(why)
        await self._close_pipe()

    async def _close_pipe(self) -> None:
        with contextlib.suppress(Exception):
            await self.pipe.close()

    def _protocol_error(self, why: str) -> None:
        self._spawn(self._shut(why, CloseReason.PROTOCOL))

    def _on_pipe_closed(self, reason: str) -> None:
        self._finish(f"lost: {reason}")

    def _finish(self, why: str) -> None:
        if self.closed is not None:
            return
        self.closed, self.state = why, "closed"
        if not self._handshake.done():
            self._handshake.set_exception(LinkDown(why))
            self._handshake.exception()  # retrieved, so an unawaited one is not logged
        for waiting in self._pending.values():
            if not waiting.done():
                waiting.set_exception(LinkDown(f"the link {why}"))
        if self._ticker is not None and self._ticker is not asyncio.current_task():
            self._ticker.cancel()
        callbacks, self._on_closed = self._on_closed, []
        for cb in callbacks:
            cb(self)


@dataclass(frozen=True)
class LinkChange:
    """A link that opened or closed, for whatever keeps count: proximity holds a robot near
    while a link to it is open, and the meeting planner learns that a robot dialled it.

    A link the other robot opened is reported only once a hello over it has borne out the
    beacon id its OPEN claimed, and only such a link's close is reported after it. `lost` says
    the radio dropped a link this robot dialled, which is the one drop that means its robot went
    out of range; one the other robot opened that drops says only `dropped`, since the other
    robot can drop its own link whenever it likes."""

    opened: bool
    link_id: str
    beacon_id: str
    inbound: bool
    reason: str = ""
    lost: bool = False


class LinkTable:
    """A robot's links, at most `max_links` with the dials still under way, each by its id; the
    place both ends start from."""

    def __init__(
        self,
        *,
        max_links: int = MAX_LINKS,
        limits: Limits | None = None,
        policy: LinkPolicy | None = None,
        executor: Executor | None = None,
        on_change: Callable[[LinkChange], None] | None = None,
    ) -> None:
        self.max_links = max_links
        self.limits = limits or Limits()
        self.policy = policy or LinkPolicy()
        self.executor = executor or ThreadPoolExecutor(
            max_workers=self.policy.inflight, thread_name_prefix="passport-link"
        )
        self.on_change = on_change
        self._links: dict[str, Link] = {}
        #: dials under way, each holding a slot and the tag it will open with
        self._dialling: set[str] = set()

    def __len__(self) -> int:
        return len(self._links)

    @property
    def full(self) -> bool:
        return len(self._links) + len(self._dialling) >= self.max_links

    def get(self, link_id: str) -> Link | None:
        return self._links.get(link_id)

    def links(self) -> list[Link]:
        return list(self._links.values())

    def by_beacon(self, beacon_id: str) -> list[Link]:
        return [link for link in self._links.values() if link.peer_beacon == beacon_id]

    def _admit(self, opened: Open) -> int | None:
        if self.full:
            return Refusal.BUSY
        tag = f"{opened.tag:08x}"
        if tag in self._links or tag in self._dialling:
            return Refusal.POLICY  # a tag already in use here, by chance or by copying
        return None

    async def accept(
        self, pipe: Pipe, *, me: str, inbox_for: Callable[[str], LinkInbox | None]
    ) -> Link | None:
        """Take a link a central opened over `pipe`, or refuse it when this robot is busy.
        `inbox_for(route)` is the inbox for requests that arrive on it."""
        return await Link.accept(
            pipe,
            me=me,
            admit=self._admit,
            inbox_for=self._checked(inbox_for),
            on_open=self._add,
            limits=self.limits,
            policy=self.policy,
            executor=self.executor,
        )

    def _checked(
        self, inbox_for: Callable[[str], LinkInbox | None]
    ) -> Callable[[str], LinkInbox | None]:
        """`inbox_for`, with a look at each answer: once talk's inbox has accepted a first hello
        over a link the other robot opened, and that hello names the beacon id the link's OPEN
        claimed, the link is reported opened."""
        loop = asyncio.get_running_loop()

        def made(route: str) -> LinkInbox | None:
            inner = inbox_for(route)
            if inner is None:
                return None
            link_id = route[len(ROUTE_PREFIX) :]

            def handle(body: bytes, media: Media) -> tuple[int, bytes | None, Media]:
                answer = inner(body, media)
                link = self._links.get(link_id)
                if (
                    answer[0] == 200
                    and link is not None
                    and not link.announced
                    and hello_beacon(body) == link.peer_beacon
                ):
                    loop.call_soon_threadsafe(self._announce, link)
                return answer

            return handle

        return made

    async def dial(
        self,
        radio: Radio,
        address: str,
        *,
        me: str,
        expect: str,
        inbox_for: Callable[[str], LinkInbox | None],
        timeout: float = 10.0,  # noqa: ASYNC109 - the radio's own connect timeout, as in Radio
    ) -> Link:
        """Dial the robot at `address`, whose advert carried `expect`, and open a link to it.
        `LinkDown` when it cannot be reached, `LinkRefused` when it will not take one. The dial
        holds a slot from the start, so links taken meanwhile cannot fill the table under it."""
        if self.full:
            raise LinkRefused("this robot holds as many links as it can", Refusal.BUSY)
        tag = new_tag()
        while f"{tag:08x}" in self._links or f"{tag:08x}" in self._dialling:
            tag = new_tag()
        held = f"{tag:08x}"
        self._dialling.add(held)
        try:
            try:
                pipe = await radio.connect(address, timeout=timeout)
            except (OSError, RadioUnavailable) as e:
                raise LinkDown(f"could not connect to {address}: {e}") from e
            link = await Link.open(
                pipe,
                me=me,
                expect=expect,
                inbox=inbox_for(ROUTE_PREFIX + held),
                limits=self.limits,
                policy=self.policy,
                executor=self.executor,
                tag=tag,
            )
            self._add(link)
            return link
        finally:
            self._dialling.discard(held)

    def _add(self, link: Link) -> None:
        self._links[link.id] = link
        if not link.inbound:
            self._announce(link)  # dialled from its advert, and the ACCEPT matched it
        link.on_closed(self._gone)

    def _announce(self, link: Link) -> None:
        if link.announced or link.closed is not None or self._links.get(link.id) is not link:
            return
        link.announced = True
        self._tell(LinkChange(True, link.id, link.peer_beacon, link.inbound))

    def confirm(self, link_id: str) -> None:
        """Report a link opened whose robot this one has met over it: a hello this robot sent
        over a link the other robot had opened bears its beacon id out as well."""
        link = self._links.get(link_id)
        if link is not None:
            self._announce(link)

    def _gone(self, link: Link) -> None:
        if self._links.get(link.id) is not link:
            return
        del self._links[link.id]
        if not link.announced:
            return
        reason = link.closed or ""
        lost = not link.inbound and reason.startswith("lost")
        if link.inbound and reason.startswith("lost"):
            reason = "dropped" + reason[len("lost") :]
        self._tell(LinkChange(False, link.id, link.peer_beacon, link.inbound, reason, lost))

    def _tell(self, change: LinkChange) -> None:
        if self.on_change is not None:
            self.on_change(change)

    async def close_beacon(self, beacon_id: str, reason: CloseReason = CloseReason.FAR) -> int:
        """Close every link to the robot with `beacon_id`. How many there were."""
        links = self.by_beacon(beacon_id)
        for link in links:
            await link.close(reason)
        return len(links)

    async def close_all(self, reason: CloseReason = CloseReason.SHUTDOWN) -> None:
        for link in list(self._links.values()):
            await link.close(reason)

    def shutdown(self) -> None:
        """Let the inbox's worker threads go; for when the robot's service stops."""
        self.executor.shutdown(wait=False, cancel_futures=True)


class LinkTransport:
    """The `ble://` routes of talk's transport: a message posted from any thread to a link on
    the radio's loop, and the answer it got, as an HTTP inbox would give it."""

    def __init__(self, table: LinkTable, loop: LoopThread) -> None:
        self.table = table
        self.loop = loop

    def post(self, route: str, wire: str) -> tuple[int, str | None]:
        if not route.startswith(ROUTE_PREFIX):
            raise ValueError(f"{route!r} is not a link")
        link = self.table.get(route[len(ROUTE_PREFIX) :])
        if link is None or link.closed is not None:
            raise LinkDown(f"{route} is closed")
        body = wire.encode("utf-8")
        timeout = post_timeout(len(body))
        try:
            status, reply, _media = self.loop.call(
                link.request(body, wire_media(wire), timeout=timeout), timeout + 1.0
            )
        except TimeoutError as e:  # the loop itself did not answer: as good as down
            raise LinkDown(f"{route} did not answer: {e}") from e
        return status, reply.decode("utf-8", errors="replace") if reply else None


def talk_inbox(inbox: Callable[[str, str], tuple[int, str | None]], route: str) -> LinkInbox:
    """A link's inbox over talk's: `inbox(wire, origin)` with the link's route as the origin, so
    a reply to the robot on the other end goes back over the same link and nowhere else."""

    def handle(body: bytes, media: Media) -> tuple[int, bytes | None, Media]:
        status, reply = inbox(body.decode("utf-8", errors="replace"), route)
        if reply is None:
            return status, None, Media.NONE
        kind = wire_media(reply) if status < 400 else Media.TEXT
        return status, reply.encode("utf-8"), kind

    return handle


__all__ = [
    "ROUTE_PREFIX",
    "Link",
    "LinkChange",
    "LinkDown",
    "LinkInbox",
    "LinkPolicy",
    "LinkRefused",
    "LinkTable",
    "LinkTransport",
    "hello_beacon",
    "post_timeout",
    "talk_inbox",
    "wire_media",
]
