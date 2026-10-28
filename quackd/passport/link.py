"""Link frames: talk between two robots that share no network, over one Bluetooth link.

A link is one GATT connection. The robot that dials, the central, writes to the other robot's rx
characteristic, and the other, the peripheral, answers by notifying its tx one (spec section
5.6). Each write and each notification carries one datagram of 20 to 512 bytes, so a talk
message, up to a hello of 16,384 bytes with a whole signed passport in it, goes as a run of DATA
frames and is put back together at the other end. This module is that and nothing else: the
frames, how a message is split into them, and the reassembler that puts it back together. It has
no radio, no clock and no thread of its own, so every rule is tested against plain bytes, and
`quackd.radio.links` runs it over whatever radio there is.

Every frame carries the 4-byte tag the central drew when it opened the link, because BlueZ
notifies a characteristic's new value to every central subscribed to it, and a central keeps only
the frames with its own tag.

What it guards against is a peer that sends anything at all. `decode` raises `FrameError` and
nothing else, whatever a datagram holds. The reassembler holds at most four messages half
received, each at most the largest hello, and refuses one whose stated size is over that before
it holds a byte of it. It wants every chunk in order and on time, and drops a response nobody
asked for without keeping a byte of it, as talk drops a repeat (spec 6.1).
"""

from __future__ import annotations

import enum
import secrets
import struct
from collections.abc import Callable
from dataclasses import dataclass

from quackd.passport.beacon import BEACON_ID_BYTES, is_beacon_id
from quackd.passport.errors import PassportError
from quackd.passport.talk import MAX_HELLO_BYTES

RULE = "5.6"
#: The framing version, in the high four bits of every frame's first byte.
FRAMING = 1
#: The first byte (framing and kind) and the tag.
HEADER_BYTES = 5
#: The ATT default MTU, 23, less the 3 bytes of a write's own header: what every link carries.
MIN_DATAGRAM = 20
#: The most an attribute's value may be (ATT): what no link carries more of in one datagram.
MAX_DATAGRAM = 512
#: How many links a peripheral holds at once. Each costs a connection slot on the controller,
#: which also has to keep advertising and scanning, and a meeting needs one.
MAX_LINKS = 4
#: Bit 15 of a message id says the peripheral sent the request it belongs to, so the ids each
#: end draws for its own requests never meet the other end's.
PERIPHERAL_BIT = 0x8000
_DATA_HEAD = struct.Struct(">HH")
_FIRST_HEAD = struct.Struct(">BHBI")
_HELLO = struct.Struct(">HI")
_ABORT = struct.Struct(">HB")


class Kind(enum.IntEnum):
    OPEN = 1
    ACCEPT = 2
    REFUSE = 3
    DATA = 4
    ABORT = 5
    CLOSE = 6


class Refusal(enum.IntEnum):
    """Why a peripheral turned an OPEN down."""

    BUSY = 1
    VERSION = 2
    POLICY = 3


class AbortReason(enum.IntEnum):
    """Why one message, and only that one, was given up on."""

    TOO_LARGE = 1
    OUT_OF_ORDER = 2
    TIMED_OUT = 3
    OVERFLOW = 4
    TOO_MANY = 5


class CloseReason(enum.IntEnum):
    """Why a link ended."""

    DONE = 0
    FAR = 1
    IDLE = 2
    SHUTDOWN = 3
    PROTOCOL = 4


class Role(enum.IntEnum):
    REQUEST = 0
    RESPONSE = 1


class Media(enum.IntEnum):
    """What a message's bytes are: the content types talk's HTTP inbox speaks, numbered."""

    NONE = 0
    JOSE = 1
    JSON = 2
    TEXT = 3


_MEDIA_NUMBERS = frozenset(int(m) for m in Media)
MEDIA_TYPES = {
    Media.NONE: "",
    Media.JOSE: "application/jose",
    Media.JSON: "application/json",
    Media.TEXT: "text/plain",
}


def media_of(content_type: str) -> Media:
    """The media number for a content type, by its main part; anything unknown is text."""
    main = content_type.split(";", 1)[0].strip().lower()
    for media, name in MEDIA_TYPES.items():
        if name and name == main:
            return media
    return Media.NONE if not main else Media.TEXT


class FrameError(PassportError):
    """A datagram that is not a link frame this robot reads. The one exception `decode` raises."""

    def __init__(self, message: str) -> None:
        super().__init__(RULE, message)


@dataclass(frozen=True)
class Open:
    """A central opening a link: its own beacon id, the largest datagram it takes in a
    notification, and the largest message it takes."""

    tag: int
    beacon_id: str
    datagram_size: int
    max_message: int


@dataclass(frozen=True)
class Accept:
    """A peripheral taking the link: its own beacon id, which must be the one its advert
    carried, the datagram size it will notify in, and the largest message it takes."""

    tag: int
    beacon_id: str
    datagram_size: int
    max_message: int


@dataclass(frozen=True)
class Refuse:
    tag: int
    reason: int


@dataclass(frozen=True)
class Head:
    """What the first chunk of a message says about all of it."""

    role: Role
    status: int
    media: Media
    total: int


@dataclass(frozen=True)
class Data:
    """One chunk of a message: its id, its place in it, its bytes, and on the first chunk the
    message's head."""

    tag: int
    msg_id: int
    seq: int
    chunk: bytes
    head: Head | None = None


@dataclass(frozen=True)
class Abort:
    tag: int
    msg_id: int
    reason: int


@dataclass(frozen=True)
class Close:
    tag: int
    reason: int


Frame = Open | Accept | Refuse | Data | Abort | Close


def new_tag() -> int:
    """A link's tag: 32 random bits, never 0, drawn by the central when it opens the link."""
    return secrets.randbelow(0xFFFF_FFFF) + 1


def encode(frame: Frame) -> bytes:
    """A frame as one datagram."""
    if not 0 < frame.tag <= 0xFFFF_FFFF:
        raise ValueError("a link's tag is a nonzero 32-bit number")
    if isinstance(frame, Open | Accept):
        kind = Kind.OPEN if isinstance(frame, Open) else Kind.ACCEPT
        if not is_beacon_id(frame.beacon_id):
            raise ValueError(f"{frame.beacon_id!r} is not a beacon id")
        body = bytes.fromhex(frame.beacon_id) + _HELLO.pack(frame.datagram_size, frame.max_message)
    elif isinstance(frame, Refuse | Close):
        kind = Kind.REFUSE if isinstance(frame, Refuse) else Kind.CLOSE
        body = bytes((frame.reason,))
    elif isinstance(frame, Abort):
        kind, body = Kind.ABORT, _ABORT.pack(frame.msg_id, frame.reason)
    else:
        kind = Kind.DATA
        body = _DATA_HEAD.pack(frame.msg_id, frame.seq)
        if frame.head is not None:
            h = frame.head
            body += _FIRST_HEAD.pack(h.role, h.status, h.media, h.total)
        body += frame.chunk
    datagram = struct.pack(">BI", FRAMING << 4 | kind, frame.tag) + body
    if len(datagram) > MAX_DATAGRAM:
        raise ValueError(
            f"a frame of {len(datagram)} bytes is over the {MAX_DATAGRAM} of a datagram"
        )
    return datagram


def decode(datagram: bytes) -> Frame:
    """The frame in one datagram, or a `FrameError` saying what is wrong with it.

    Every length is checked before a byte is read, a control frame has exactly its own length,
    and a value with a meaning this version does not define is refused rather than guessed at,
    except a reason, which only says why and changes nothing."""
    size = len(datagram)
    if size > MAX_DATAGRAM:
        raise FrameError(f"a datagram of {size} bytes, over the {MAX_DATAGRAM} a link carries")
    if size < HEADER_BYTES:
        raise FrameError(f"a datagram of {size} bytes is too short to be a frame")
    first = datagram[0]
    if first >> 4 != FRAMING:
        raise FrameError(f"framing version {first >> 4}, and this robot reads {FRAMING}")
    kind, tag = first & 0x0F, int.from_bytes(datagram[1:5], "big")
    if tag == 0:
        raise FrameError("a frame's tag is never 0")
    body = datagram[HEADER_BYTES:]
    if kind in (Kind.OPEN, Kind.ACCEPT):
        _exactly(body, BEACON_ID_BYTES + _HELLO.size, kind)
        beacon_id = bytes(body[:BEACON_ID_BYTES]).hex()
        datagram_size, max_message = _HELLO.unpack_from(body, BEACON_ID_BYTES)
        if not MIN_DATAGRAM <= datagram_size <= MAX_DATAGRAM:
            raise FrameError(f"a datagram size of {datagram_size}, outside 20 to 512")
        if max_message < 1:
            raise FrameError("a link that takes no message at all")
        if kind == Kind.OPEN:
            return Open(tag, beacon_id, datagram_size, max_message)
        return Accept(tag, beacon_id, datagram_size, max_message)
    if kind in (Kind.REFUSE, Kind.CLOSE):
        _exactly(body, 1, kind)
        return Refuse(tag, body[0]) if kind == Kind.REFUSE else Close(tag, body[0])
    if kind == Kind.ABORT:
        _exactly(body, _ABORT.size, kind)
        msg_id, reason = _ABORT.unpack_from(body)
        return Abort(tag, msg_id, reason)
    if kind == Kind.DATA:
        if len(body) < _DATA_HEAD.size:
            raise FrameError("a DATA frame too short for its message id and sequence")
        msg_id, seq = _DATA_HEAD.unpack_from(body)
        rest = body[_DATA_HEAD.size :]
        if seq > 0:
            if not rest:
                raise FrameError("a DATA frame after the first carries at least one byte")
            return Data(tag, msg_id, seq, bytes(rest))
        if len(rest) < _FIRST_HEAD.size:
            raise FrameError("a message's first DATA frame too short for its head")
        role, status, media, total = _FIRST_HEAD.unpack_from(rest)
        chunk = bytes(rest[_FIRST_HEAD.size :])
        if role not in (Role.REQUEST, Role.RESPONSE):
            raise FrameError(f"a message's role {role}, neither request nor response")
        if role == Role.REQUEST and status != 0:
            raise FrameError("a request carries no status")
        if role == Role.RESPONSE and not 100 <= status <= 599:
            raise FrameError(f"a response's status {status}, which is no status")
        if media not in _MEDIA_NUMBERS:
            raise FrameError(f"media {media}, which this robot does not know")
        if len(chunk) > total:
            raise FrameError("a first chunk longer than the whole message it begins")
        return Data(tag, msg_id, 0, chunk, Head(Role(role), status, Media(media), total))
    raise FrameError(f"a frame of kind {kind}, which this robot does not know")


def _exactly(body: bytes, size: int, kind: int) -> None:
    if len(body) != size:
        raise FrameError(
            f"a {Kind(kind).name} frame is {HEADER_BYTES + size} bytes, not "
            f"{HEADER_BYTES + len(body)}"
        )


def first_room(datagram_size: int) -> int:
    """How many of a message's bytes the first datagram of it carries."""
    return datagram_size - HEADER_BYTES - _DATA_HEAD.size - _FIRST_HEAD.size


def later_room(datagram_size: int) -> int:
    """How many of a message's bytes each datagram after the first carries."""
    return datagram_size - HEADER_BYTES - _DATA_HEAD.size


def split(
    body: bytes,
    *,
    tag: int,
    msg_id: int,
    role: Role,
    status: int,
    media: Media,
    datagram_size: int,
) -> list[bytes]:
    """A message as the datagrams that carry it, none longer than `datagram_size`. An empty
    message is one datagram, its head."""
    if not MIN_DATAGRAM <= datagram_size <= MAX_DATAGRAM:
        raise ValueError(f"a datagram size of {datagram_size}, outside 20 to 512")
    if not 0 <= msg_id <= 0xFFFF:
        raise ValueError("a message id is 16 bits")
    first, later = first_room(datagram_size), later_room(datagram_size)
    if len(body) > first + 0xFFFF * later:
        raise ValueError("a message too long to number its chunks in 16 bits")
    head = Head(role, status, media, len(body))
    datagrams = [encode(Data(tag, msg_id, 0, body[:first], head))]
    for seq, at in enumerate(range(first, len(body), later), start=1):
        datagrams.append(encode(Data(tag, msg_id, seq, body[at : at + later])))
    return datagrams


@dataclass(frozen=True)
class Limits:
    """What one end of a link holds still for: the largest message (the largest hello, since a
    hello carries a whole signed passport), how many messages half received at once, how long
    between two chunks of one and how long for the whole of it, and the datagram sizes a link
    may have."""

    max_message: int = MAX_HELLO_BYTES
    max_partial: int = 4
    gap_s: float = 3.0
    message_s: float = 15.0
    min_datagram: int = MIN_DATAGRAM
    max_datagram: int = MAX_DATAGRAM


@dataclass(frozen=True)
class Message:
    """A whole message, reassembled."""

    msg_id: int
    role: Role
    status: int
    media: Media
    body: bytes


@dataclass(frozen=True)
class Given:
    """A message the reassembler gave up on, and why: the link answers a request that was too
    large with a 413 and aborts anything else."""

    msg_id: int
    role: Role
    reason: AbortReason


@dataclass
class _Partial:
    role: Role
    status: int
    media: Media
    total: int
    chunks: list[bytes]
    received: int
    seq: int
    started: float
    last: float


class Reassembler:
    """One end of a link putting messages back together, within `Limits`.

    `peer_bit` is the `PERIPHERAL_BIT` when the other end is the peripheral and 0 when it is the
    central: a request must carry the other end's bit and a response this end's. `awaiting`
    says whether this end is waiting on a response with that id; a response to anything else is
    dropped as it arrives, so a peer cannot fill this end with answers to questions it never
    asked. The clock is the caller's: `feed` and `expire` take `now`."""

    def __init__(self, limits: Limits, *, peer_bit: int, awaiting: Callable[[int], bool]) -> None:
        self.limits = limits
        self.peer_bit = peer_bit
        self.awaiting = awaiting
        self._partial: dict[int, _Partial] = {}
        #: datagrams dropped without being kept: unsolicited, or for no message in hand
        self.dropped = 0

    @property
    def pending(self) -> int:
        return len(self._partial)

    def feed(self, frame: Data, now: float) -> Message | Given | None:
        """The message `frame` completes, the one it made this end give up on, or None while
        a message is still coming, or when the frame was dropped."""
        if frame.seq == 0:
            return self._first(frame, now)
        partial = self._partial.get(frame.msg_id)
        if partial is None:
            self.dropped += 1
            return None
        if frame.seq != partial.seq + 1:
            del self._partial[frame.msg_id]
            return Given(frame.msg_id, partial.role, AbortReason.OUT_OF_ORDER)
        if partial.received + len(frame.chunk) > partial.total:
            del self._partial[frame.msg_id]
            return Given(frame.msg_id, partial.role, AbortReason.OVERFLOW)
        partial.chunks.append(frame.chunk)
        partial.received += len(frame.chunk)
        partial.seq, partial.last = frame.seq, now
        if partial.received < partial.total:
            return None
        del self._partial[frame.msg_id]
        body = b"".join(partial.chunks)
        return Message(frame.msg_id, partial.role, partial.status, partial.media, body)

    def _first(self, frame: Data, now: float) -> Message | Given | None:
        head = frame.head
        if head is None:  # decode never makes one, but a Data built by hand might
            raise ValueError("a message's first chunk carries its head")
        msg_id, role = frame.msg_id, head.role
        mine = (msg_id & PERIPHERAL_BIT) != self.peer_bit
        if (role == Role.REQUEST and mine) or (
            role == Role.RESPONSE and (not mine or not self.awaiting(msg_id))
        ):
            self.dropped += 1
            return None
        if msg_id in self._partial:
            # a message started twice: whichever of the two is right, neither can be trusted
            del self._partial[msg_id]
            return Given(msg_id, role, AbortReason.OUT_OF_ORDER)
        if head.total > self.limits.max_message:
            return Given(msg_id, role, AbortReason.TOO_LARGE)
        if len(frame.chunk) == head.total:
            return Message(msg_id, role, head.status, head.media, frame.chunk)
        if len(self._partial) >= self.limits.max_partial:
            return Given(msg_id, role, AbortReason.TOO_MANY)
        self._partial[msg_id] = _Partial(
            role, head.status, head.media, head.total, [frame.chunk], len(frame.chunk), 0, now, now
        )
        return None

    def abandon(self, msg_id: int) -> bool:
        """Forget a message the other end aborted. Whether there was one to forget."""
        return self._partial.pop(msg_id, None) is not None

    def expire(self, now: float) -> list[Given]:
        """The messages that waited too long for their next chunk, or for all of them, given up
        on and forgotten."""
        late = [
            msg_id
            for msg_id, p in self._partial.items()
            if now - p.last > self.limits.gap_s or now - p.started > self.limits.message_s
        ]
        return [Given(m, self._partial.pop(m).role, AbortReason.TIMED_OUT) for m in late]


__all__ = [
    "FRAMING",
    "MAX_DATAGRAM",
    "MAX_LINKS",
    "MIN_DATAGRAM",
    "PERIPHERAL_BIT",
    "Abort",
    "AbortReason",
    "Accept",
    "Close",
    "CloseReason",
    "Data",
    "Frame",
    "FrameError",
    "Given",
    "Head",
    "Kind",
    "Limits",
    "Media",
    "Message",
    "Open",
    "Reassembler",
    "Refusal",
    "Refuse",
    "Role",
    "decode",
    "encode",
    "media_of",
    "new_tag",
    "split",
]
