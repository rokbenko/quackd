"""Link frames, split and put back together, on plain bytes and a clock the test turns.

The frame vectors in docs/passport/vectors/ble-link.json are written by
scripts/passport_vectors.py with `struct` alone, so the encoding is checked against a second
implementation of it. Every bound is read off `Limits` rather than written here again."""

from __future__ import annotations

import json
import random
from pathlib import Path

import pytest

from quackd.passport import link
from quackd.passport.link import (
    MAX_DATAGRAM,
    MIN_DATAGRAM,
    PERIPHERAL_BIT,
    Abort,
    AbortReason,
    Accept,
    Close,
    Data,
    FrameError,
    Given,
    Head,
    Limits,
    Media,
    Message,
    Open,
    Reassembler,
    Refuse,
    Role,
    decode,
    encode,
    split,
)
from quackd.passport.talk import MAX_HELLO_BYTES

VECTORS = Path(__file__).resolve().parents[1] / "docs" / "passport" / "vectors"
LINK = json.loads((VECTORS / "ble-link.json").read_text(encoding="utf-8"))
TAG = LINK["tag"]
SIZES = (MIN_DATAGRAM, 182, 244, MAX_DATAGRAM)


def central_side(limits: Limits | None = None, awaiting: set[int] | None = None) -> Reassembler:
    """The central's reassembler: the peer is the peripheral, so its requests carry the bit."""
    wanted = awaiting if awaiting is not None else set()
    return Reassembler(limits or Limits(), peer_bit=PERIPHERAL_BIT, awaiting=wanted.__contains__)


def peripheral_side(limits: Limits | None = None, awaiting: set[int] | None = None) -> Reassembler:
    wanted = awaiting if awaiting is not None else set()
    return Reassembler(limits or Limits(), peer_bit=0, awaiting=wanted.__contains__)


def frames_of(body: bytes, size: int, *, msg_id: int = 1, role: Role = Role.REQUEST) -> list[Data]:
    status = 0 if role == Role.REQUEST else 200
    out = []
    for datagram in split(
        body, tag=TAG, msg_id=msg_id, role=role, status=status, media=Media.JOSE, datagram_size=size
    ):
        frame = decode(datagram)
        assert isinstance(frame, Data)
        out.append(frame)
    return out


def test_the_limits_are_the_largest_hello_and_the_datagram_range() -> None:
    limits = Limits()
    assert limits.max_message == MAX_HELLO_BYTES
    assert (limits.min_datagram, limits.max_datagram) == (MIN_DATAGRAM, MAX_DATAGRAM)


@pytest.mark.parametrize("size", SIZES)
@pytest.mark.parametrize("length", [0, 1, MAX_HELLO_BYTES])
def test_a_message_splits_into_datagrams_that_fit_and_comes_back_whole(
    size: int, length: int
) -> None:
    body = random.Random(length * 7 + size).randbytes(length)
    datagrams = split(
        body,
        tag=TAG,
        msg_id=7,
        role=Role.REQUEST,
        status=0,
        media=Media.JOSE,
        datagram_size=size,
    )
    assert all(len(d) <= size for d in datagrams)
    first, later = link.first_room(size), link.later_room(size)
    expected = 1 + max(0, -(-(length - first) // later))
    assert len(datagrams) == expected
    side = peripheral_side()
    results = [side.feed(decode(d), now=0.0) for d in datagrams]  # type: ignore[arg-type]
    assert results[:-1] == [None] * (len(results) - 1)
    assert results[-1] == Message(7, Role.REQUEST, 0, Media.JOSE, body)
    assert side.pending == 0


def test_open_and_accept_fit_the_smallest_datagram() -> None:
    for frame in (Open(TAG, "0" * 16, MAX_DATAGRAM, 2**32 - 1), Accept(TAG, "f" * 16, 20, 1)):
        assert len(encode(frame)) <= MIN_DATAGRAM
        assert decode(encode(frame)) == frame


def test_every_frame_vector_decodes_to_its_fields_and_encodes_back() -> None:
    for vector in LINK["frames"]:
        raw = bytes.fromhex(vector["hex"])
        kind = vector["kind"]
        if kind == "OPEN":
            expected: link.Frame = Open(
                TAG, vector["beacon_id"], vector["datagram_size"], vector["max_message"]
            )
        elif kind == "ACCEPT":
            expected = Accept(
                TAG, vector["beacon_id"], vector["datagram_size"], vector["max_message"]
            )
        elif kind == "REFUSE":
            expected = Refuse(TAG, vector["reason"])
        elif kind == "ABORT":
            expected = Abort(TAG, vector["msg_id"], vector["reason"])
        elif kind == "CLOSE":
            expected = Close(TAG, vector["reason"])
        else:
            head = Head(
                Role(vector["role"]), vector["status"], Media(vector["media"]), vector["total"]
            )
            expected = Data(
                TAG, vector["msg_id"], vector["seq"], bytes.fromhex(vector["chunk_hex"]), head
            )
        assert decode(raw) == expected, vector["name"]
        assert encode(expected) == raw, vector["name"]
    kinds = {v["kind"] for v in LINK["frames"]}
    assert kinds == {k.name for k in link.Kind}


def test_the_message_vector_is_split_exactly_as_this_module_splits_it() -> None:
    m = LINK["message"]
    body = m["body"].encode("ascii")
    made = split(
        body,
        tag=TAG,
        msg_id=m["msg_id"],
        role=Role(m["role"]),
        status=m["status"],
        media=Media(m["media"]),
        datagram_size=m["datagram_size"],
    )
    assert [d.hex() for d in made] == m["datagrams"]
    side = peripheral_side()
    results = [side.feed(decode(bytes.fromhex(d)), 0.0) for d in m["datagrams"]]  # type: ignore[arg-type]
    assert results[-1] == Message(m["msg_id"], Role.REQUEST, 0, Media.JOSE, body)


def test_a_message_over_the_limit_is_refused_at_its_first_chunk_before_a_byte_is_held() -> None:
    limits = Limits()
    first = frames_of(bytes(limits.max_message + 1), MAX_DATAGRAM)[0]
    side = peripheral_side(limits)
    assert side.feed(first, 0.0) == Given(1, Role.REQUEST, AbortReason.TOO_LARGE)
    assert side.pending == 0
    # one that is exactly the limit is taken
    assert side.feed(frames_of(bytes(limits.max_message), MAX_DATAGRAM)[0], 0.0) is None
    assert side.pending == 1


def test_a_chunk_out_of_order_or_twice_gives_the_message_up() -> None:
    chunks = frames_of(bytes(100), MIN_DATAGRAM)
    side = peripheral_side()
    assert side.feed(chunks[0], 0.0) is None
    assert side.feed(chunks[2], 0.0) == Given(1, Role.REQUEST, AbortReason.OUT_OF_ORDER)
    assert side.pending == 0
    assert side.feed(chunks[0], 0.0) is None
    assert side.feed(chunks[1], 0.0) is None
    assert side.feed(chunks[1], 0.0) == Given(1, Role.REQUEST, AbortReason.OUT_OF_ORDER)
    # and a message begun twice is given up as well, since neither start can be trusted
    assert side.feed(chunks[0], 0.0) is None
    assert side.feed(chunks[0], 0.0) == Given(1, Role.REQUEST, AbortReason.OUT_OF_ORDER)


def test_more_bytes_than_the_head_promised_is_an_overflow() -> None:
    chunks = frames_of(bytes(30), MIN_DATAGRAM)
    side = peripheral_side()
    assert side.feed(chunks[0], 0.0) is None
    padded = Data(TAG, 1, 1, bytes(link.later_room(MIN_DATAGRAM)))
    assert side.feed(padded, 0.0) is None
    too_much = Data(TAG, 1, 2, bytes(30))
    assert side.feed(too_much, 0.0) == Given(1, Role.REQUEST, AbortReason.OVERFLOW)


def test_a_fifth_message_half_received_is_one_too_many() -> None:
    limits = Limits()
    side = peripheral_side(limits)
    for msg_id in range(limits.max_partial):
        assert side.feed(frames_of(bytes(50), MIN_DATAGRAM, msg_id=msg_id)[0], 0.0) is None
    extra = frames_of(bytes(50), MIN_DATAGRAM, msg_id=limits.max_partial)[0]
    assert side.feed(extra, 0.0) == Given(limits.max_partial, Role.REQUEST, AbortReason.TOO_MANY)
    assert side.pending == limits.max_partial
    # a message that fits in one datagram holds nothing, so it is never one too many
    whole = frames_of(b"ok", MIN_DATAGRAM, msg_id=99)[0]
    assert isinstance(side.feed(whole, 0.0), Message)


def test_a_response_nobody_asked_for_is_dropped_unkept() -> None:
    side = central_side(awaiting={3})
    stray = frames_of(bytes(80), MIN_DATAGRAM, msg_id=4, role=Role.RESPONSE)
    assert [side.feed(f, 0.0) for f in stray] == [None] * len(stray)
    assert side.pending == 0 and side.dropped == len(stray)
    wanted = frames_of(b"yes", MIN_DATAGRAM, msg_id=3, role=Role.RESPONSE)
    assert side.feed(wanted[0], 0.0) == Message(3, Role.RESPONSE, 200, Media.JOSE, b"yes")


def test_ids_carry_who_asked_and_a_frame_with_the_wrong_one_is_dropped() -> None:
    # the central's requests carry no bit, so a request without one, arriving at the central,
    # claims to come from the central itself
    at_central = central_side()
    assert at_central.feed(frames_of(b"hi", MIN_DATAGRAM, msg_id=5)[0], 0.0) is None
    assert at_central.dropped == 1
    from_peripheral = frames_of(b"hi", MIN_DATAGRAM, msg_id=PERIPHERAL_BIT | 5)[0]
    assert isinstance(at_central.feed(from_peripheral, 0.0), Message)
    at_peripheral = peripheral_side(awaiting={PERIPHERAL_BIT | 9})
    assert at_peripheral.feed(from_peripheral, 0.0) is None
    answer = frames_of(b"ok", MIN_DATAGRAM, msg_id=PERIPHERAL_BIT | 9, role=Role.RESPONSE)[0]
    assert isinstance(at_peripheral.feed(answer, 0.0), Message)


def test_a_chunk_for_no_message_in_hand_is_dropped() -> None:
    side = peripheral_side()
    assert side.feed(Data(TAG, 42, 3, b"orphan"), 0.0) is None
    assert side.dropped == 1 and side.pending == 0


def test_a_message_that_stalls_between_chunks_or_runs_long_times_out() -> None:
    limits = Limits()
    chunks = frames_of(bytes(400), MIN_DATAGRAM)
    side = peripheral_side(limits)
    side.feed(chunks[0], 0.0)
    assert side.expire(limits.gap_s) == []
    late = limits.gap_s + 0.001
    assert side.expire(late) == [Given(1, Role.REQUEST, AbortReason.TIMED_OUT)]
    assert side.pending == 0
    # chunks arriving in time, but the whole taking longer than a message may
    side.feed(chunks[0], 0.0)
    now, step = 0.0, limits.gap_s / 2
    for chunk in chunks[1:]:
        now += step
        if now > limits.message_s:
            break
        assert side.feed(chunk, now) is None
        assert side.expire(now) == []
    assert side.expire(limits.message_s + 0.001) == [Given(1, Role.REQUEST, AbortReason.TIMED_OUT)]


def test_a_message_the_other_end_aborted_is_forgotten() -> None:
    side = peripheral_side()
    side.feed(frames_of(bytes(100), MIN_DATAGRAM)[0], 0.0)
    assert side.abandon(1) and not side.abandon(1)
    assert side.pending == 0


@pytest.mark.parametrize(
    ("datagram", "words"),
    [
        (bytes(4), "too short"),
        (bytes(MAX_DATAGRAM + 1), "over the"),
        (bytes((0x21,)) + TAG.to_bytes(4, "big") + b"\x01", "framing version 2"),
        (bytes((0x17,)) + TAG.to_bytes(4, "big") + b"\x01", "kind 7"),
        (bytes((0x16,)) + bytes(4) + b"\x00", "never 0"),
        (bytes((0x16,)) + TAG.to_bytes(4, "big") + b"\x00\x00", "is 6 bytes, not 7"),
        (encode(Open(TAG, "0" * 16, 20, 1)) + b"\x00", "OPEN frame"),
        (encode(Open(TAG, "0" * 16, 20, 1))[:-6] + (19).to_bytes(2, "big") + bytes(4), "20 to"),
        (bytes((0x14,)) + TAG.to_bytes(4, "big") + b"\x00\x01\x00\x01", "at least one byte"),
        (bytes((0x14,)) + TAG.to_bytes(4, "big") + b"\x00\x01\x00\x00\x00", "head"),
    ],
)
def test_what_is_not_a_frame_is_refused_with_a_frame_error(datagram: bytes, words: str) -> None:
    with pytest.raises(FrameError, match=words) as refused:
        decode(datagram)
    assert refused.value.rule == link.RULE


def test_a_first_chunk_whose_head_breaks_a_rule_is_refused() -> None:
    def first(role: int, status: int, media: int, total: int, chunk: bytes = b"") -> bytes:
        body = (1).to_bytes(2, "big") + bytes(2) + bytes((role,)) + status.to_bytes(2, "big")
        body += bytes((media,)) + total.to_bytes(4, "big") + chunk
        return bytes((0x14,)) + TAG.to_bytes(4, "big") + body

    for raw, words in [
        (first(2, 0, 1, 0), "role 2"),
        (first(0, 200, 1, 0), "a request carries no status"),
        (first(1, 99, 1, 0), "status 99"),
        (first(1, 600, 1, 0), "status 600"),
        (first(0, 0, 4, 0), "media 4"),
        (first(0, 0, 1, 1, b"xy"), "longer than the whole"),
    ]:
        with pytest.raises(FrameError, match=words):
            decode(raw)
    assert isinstance(decode(first(1, 204, 0, 0)), Data)


def test_ten_thousand_datagrams_of_noise_raise_only_frame_errors() -> None:
    rng = random.Random(20261028)
    valid = [bytes.fromhex(v["hex"]) for v in LINK["frames"]]
    valid += [bytes.fromhex(d) for d in LINK["message"]["datagrams"]]
    outcomes = {"frames": 0, "refused": 0}
    for i in range(10_000):
        if i % 2:
            noise = bytes(rng.randrange(256) for _ in range(rng.randrange(MAX_DATAGRAM + 40)))
        else:  # a real frame with a few bytes changed, which reaches far deeper into decode
            noise = bytearray(rng.choice(valid))
            for _ in range(rng.randrange(1, 4)):
                noise[rng.randrange(len(noise))] = rng.randrange(256)
            noise = bytes(noise)
        try:
            frame = decode(noise)
        except FrameError:
            outcomes["refused"] += 1
            continue
        outcomes["frames"] += 1
        assert encode(frame) == noise
    assert outcomes["frames"] and outcomes["refused"]


def test_a_split_refuses_a_datagram_size_no_link_has() -> None:
    for size in (MIN_DATAGRAM - 1, MAX_DATAGRAM + 1):
        with pytest.raises(ValueError, match="20 to 512"):
            split(
                b"x",
                tag=TAG,
                msg_id=1,
                role=Role.REQUEST,
                status=0,
                media=Media.JOSE,
                datagram_size=size,
            )


def test_a_tag_is_never_zero_and_media_types_map_both_ways() -> None:
    assert all(0 < link.new_tag() <= 0xFFFF_FFFF for _ in range(1000))
    with pytest.raises(ValueError, match="tag"):
        encode(Close(0, 0))
    for media, name in link.MEDIA_TYPES.items():
        assert link.media_of(name) is media
    assert link.media_of("application/jose; charset=utf-8") is Media.JOSE
    assert link.media_of("text/html") is Media.TEXT


def test_a_message_half_received_costs_about_its_size_however_small_its_chunks() -> None:
    """A peer may send a message a byte a chunk; what the reassembler holds of it is one buffer,
    not one object per chunk, which cost forty times the message."""
    import tracemalloc

    size = 4000
    side = peripheral_side()
    head = Head(Role.REQUEST, 0, Media.JOSE, size + 1)
    tracemalloc.start()
    try:
        before = tracemalloc.take_snapshot()
        side.feed(Data(TAG, 1, 0, b"x", head), 0.0)
        for seq in range(1, size):
            assert side.feed(Data(TAG, 1, seq, b"x"), 0.0) is None
        after = tracemalloc.take_snapshot()
    finally:
        tracemalloc.stop()
    held = sum(stat.size_diff for stat in after.compare_to(before, "filename"))
    assert side.pending == 1
    assert held < 4 * size, held
