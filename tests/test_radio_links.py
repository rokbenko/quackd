"""Links over pipes made of queues: requests both ways, every bound a peer could push on, and
the loop thread that other threads reach a link through.

Every clock and count is read off `LinkPolicy` and `Limits`; where a test shortens a clock it
builds its own policy, so nothing waits thirty real seconds to see a link closed."""

from __future__ import annotations

import asyncio
import hashlib
import threading
import time
from dataclasses import replace

import pytest

from quackd.passport.link import (
    MAX_LINKS,
    Accept,
    CloseReason,
    Data,
    Head,
    Limits,
    Media,
    Open,
    Refusal,
    Refuse,
    Role,
    decode,
    encode,
    new_tag,
    split,
)
from quackd.passport.talk import MAX_HELLO_BYTES
from quackd.radio.fake import MemoryPipe
from quackd.radio.links import (
    Link,
    LinkChange,
    LinkDown,
    LinkInbox,
    LinkPolicy,
    LinkRefused,
    LinkTable,
    LinkTransport,
    post_timeout,
    talk_inbox,
)
from quackd.radio.loop import LoopThread

A = "0001020304050607"  # dials, being the lower
B = "08090a0b0c0d0e0f"
FAST = LinkPolicy(accept_s=0.5, tick_s=0.02)


def echo(body: bytes, media: Media) -> tuple[int, bytes | None, Media]:
    return 200, b"echo:" + body, media


async def linked(
    size: int = 182,
    *,
    inbox_c: LinkInbox | None = echo,
    inbox_p: LinkInbox | None = echo,
    policy: LinkPolicy = FAST,
    limits: Limits | None = None,
    latency: float = 0.0,
) -> tuple[Link, Link, MemoryPipe, MemoryPipe]:
    c_pipe, p_pipe = MemoryPipe.pair(size, latency=latency)
    accepting = asyncio.create_task(
        Link.accept(p_pipe, me=B, inbox=inbox_p, policy=policy, limits=limits)
    )
    central = await Link.open(c_pipe, me=A, expect=B, inbox=inbox_c, policy=policy, limits=limits)
    peripheral = await accepting
    assert peripheral is not None
    return central, peripheral, c_pipe, p_pipe


async def test_a_link_opens_naming_both_robots_and_the_smaller_datagram() -> None:
    c_pipe, p_pipe = MemoryPipe.pair(244, other_size=182)
    accepting = asyncio.create_task(Link.accept(p_pipe, me=B, inbox=echo, policy=FAST))
    central = await Link.open(c_pipe, me=A, expect=B, inbox=echo, policy=FAST)
    peripheral = await accepting
    assert peripheral is not None
    assert (central.peer_beacon, peripheral.peer_beacon) == (B, A)
    assert central.tag == peripheral.tag and central.route == f"ble://{central.tag:08x}"
    # the peripheral notifies at the smaller of what the central takes and what it can send
    assert (central.send_size, peripheral.send_size) == (244, 182)
    await central.close()


async def test_requests_go_both_ways_at_once_and_each_gets_its_own_answer() -> None:
    central, peripheral, _, _ = await linked()
    asks = [central.request(f"c{i}".encode(), Media.JOSE, timeout=2) for i in range(6)]
    asks += [peripheral.request(f"p{i}".encode(), Media.JSON, timeout=2) for i in range(6)]
    answers = await asyncio.gather(*asks)
    assert [a[1] for a in answers[:6]] == [f"echo:c{i}".encode() for i in range(6)]
    assert [a[1] for a in answers[6:]] == [f"echo:p{i}".encode() for i in range(6)]
    assert {a[0] for a in answers} == {200}
    assert [a[2] for a in answers] == [Media.JOSE] * 6 + [Media.JSON] * 6


async def test_the_largest_hello_crosses_the_smallest_link_whole() -> None:
    def digest(body: bytes, media: Media) -> tuple[int, bytes | None, Media]:
        return 200, hashlib.sha256(body).hexdigest().encode(), Media.TEXT

    central, _peripheral, c_pipe, _ = await linked(20, inbox_p=digest)
    hello = bytes(range(256)) * (MAX_HELLO_BYTES // 256)
    assert len(hello) == MAX_HELLO_BYTES
    sent_before = c_pipe.sent
    status, reply, _ = await central.request(hello, Media.JOSE, timeout=post_timeout(len(hello)))
    assert (status, reply) == (200, hashlib.sha256(hello).hexdigest().encode())
    expected = split(
        hello, tag=1, msg_id=1, role=Role.REQUEST, status=0, media=Media.JOSE, datagram_size=20
    )
    assert c_pipe.sent - sent_before == len(expected)


async def test_a_request_larger_than_the_other_robot_takes_is_never_sent() -> None:
    central, *_ = await linked()
    with pytest.raises(Exception, match="over the") as refused:
        await central.request(bytes(central.peer_max + 1), Media.JOSE, timeout=1)
    assert not isinstance(refused.value, LinkDown)


async def raw_peripheral(
    *, policy: LinkPolicy = FAST, limits: Limits | None = None, inbox: LinkInbox | None = echo
) -> tuple[MemoryPipe, list[bytes], asyncio.Task[Link | None]]:
    """A peripheral link, and the bare other end of its pipe for a test to speak frames on."""
    raw, p_pipe = MemoryPipe.pair(182)
    heard: list[bytes] = []
    raw.on_datagram(heard.append)
    task = asyncio.create_task(Link.accept(p_pipe, me=B, inbox=inbox, policy=policy, limits=limits))
    return raw, heard, task


async def until(condition: object, within_s: float = 2.0) -> None:
    deadline = time.monotonic() + within_s
    while not (condition() if callable(condition) else condition):
        if time.monotonic() > deadline:
            raise AssertionError("the condition never came true")
        await asyncio.sleep(0.005)


async def test_a_request_that_says_it_is_too_large_is_answered_413_at_once() -> None:
    limits = Limits()
    raw, heard, task = await raw_peripheral(limits=limits)
    tag = new_tag()
    await raw.send(encode(Open(tag, A, 182, limits.max_message)))
    await until(lambda: heard)
    assert isinstance(decode(heard[0]), Accept)
    await raw.send(
        encode(Data(tag, 3, 0, b"x", Head(Role.REQUEST, 0, Media.JOSE, limits.max_message + 1)))
    )
    await until(lambda: len(heard) > 1)
    answer = decode(heard[1])
    assert isinstance(answer, Data) and answer.head is not None
    assert (answer.msg_id, answer.head.role, answer.head.status) == (3, Role.RESPONSE, 413)
    link = await task
    assert link is not None and link.closed is None


async def test_a_full_inbox_answers_503_and_a_failing_one_500() -> None:
    gate = threading.Event()

    def slow(body: bytes, media: Media) -> tuple[int, bytes | None, Media]:
        if body == b"boom":
            raise RuntimeError("the inbox fell over")
        gate.wait(5)
        return 200, b"done", Media.TEXT

    central, peripheral, _, _ = await linked(inbox_p=slow)
    held = [
        asyncio.create_task(central.request(b"wait", Media.JOSE, timeout=5))
        for _ in range(FAST.inflight)
    ]
    await until(lambda: peripheral._inflight == FAST.inflight)
    status, reply, media = await central.request(b"one more", Media.JOSE, timeout=2)
    assert (status, media) == (503, Media.TEXT) and reply
    gate.set()
    assert [r[0] for r in await asyncio.gather(*held)] == [200] * FAST.inflight
    status, _reply, _media = await central.request(b"boom", Media.JOSE, timeout=2)
    assert status == 500


async def test_too_many_requests_in_the_window_are_answered_429() -> None:
    policy = replace(FAST, rate=3)
    central, *_ = await linked(policy=policy)
    statuses = [(await central.request(b"hi", Media.JOSE, timeout=1))[0] for _ in range(4)]
    assert statuses == [200, 200, 200, 429]


async def test_a_link_that_drops_mid_message_fails_what_was_waiting_at_once() -> None:
    gate = threading.Event()

    def stuck(body: bytes, media: Media) -> tuple[int, bytes | None, Media]:
        gate.wait(5)
        return 200, b"late", Media.TEXT

    central, peripheral, c_pipe, _ = await linked(inbox_p=stuck)
    closed: list[Link] = []
    central.on_closed(closed.append)
    waiting = asyncio.create_task(central.request(b"hello", Media.JOSE, timeout=5))
    await asyncio.sleep(0.05)
    started = time.monotonic()
    c_pipe.cut()
    with pytest.raises(LinkDown, match="link lost"):
        await waiting
    assert time.monotonic() - started < 1.0
    assert closed == [central] and central.closed and peripheral.closed
    with pytest.raises(LinkDown):
        await central.request(b"again", Media.JOSE, timeout=1)
    gate.set()
    # and a link that drops while a long message is still going out fails that send too
    slow_c, _slow_p, slow_pipe, _ = await linked(20, latency=0.001)
    sending = asyncio.create_task(slow_c.request(bytes(4000), Media.JOSE, timeout=10))
    await asyncio.sleep(0.05)
    slow_pipe.cut()
    with pytest.raises(LinkDown):
        await sending


async def test_two_centrals_overhearing_each_other_never_take_each_others_answers() -> None:
    def named(who: bytes) -> LinkInbox:
        return lambda body, media: (200, who + b":" + body, Media.TEXT)

    c1, p1 = MemoryPipe.pair(182)
    c2, p2 = MemoryPipe.pair(182)
    # BlueZ notifies every subscribed central, so each central hears the other's answers too
    p1.overheard_by.append(c2)
    p2.overheard_by.append(c1)
    accepts = [
        asyncio.create_task(Link.accept(p, me=B, inbox=named(n), policy=FAST))
        for p, n in ((p1, b"one"), (p2, b"two"))
    ]
    first = await Link.open(c1, me=A, expect=B, inbox=None, policy=FAST)
    second = await Link.open(c2, me="0001020304050608", expect=B, inbox=None, policy=FAST)
    await asyncio.gather(*accepts)
    answers = await asyncio.gather(
        *[first.request(f"{i}".encode(), Media.JOSE, timeout=2) for i in range(5)],
        *[second.request(f"{i}".encode(), Media.JOSE, timeout=2) for i in range(5)],
    )
    assert [a[1] for a in answers[:5]] == [f"one:{i}".encode() for i in range(5)]
    assert [a[1] for a in answers[5:]] == [f"two:{i}".encode() for i in range(5)]
    assert first.foreign > 0 and second.foreign > 0


async def test_a_central_that_never_asks_anything_is_closed_as_idle() -> None:
    policy = replace(FAST, first_request_s=0.2)
    central, peripheral, _, _ = await linked(policy=policy)
    await until(lambda: central.closed is not None, within_s=2.0)
    assert peripheral.closed == "closed here (idle)"
    assert central.closed == "closed by the other robot (idle)"


async def test_a_quiet_link_is_closed_after_the_idle_time() -> None:
    policy = replace(FAST, idle_s=0.3)
    central, peripheral, _, _ = await linked(policy=policy)
    assert (await central.request(b"hello", Media.JOSE, timeout=1))[0] == 200
    await until(lambda: peripheral.closed is not None and central.closed is not None, 2.0)
    assert "idle" in (central.closed or "")


async def test_no_answer_in_time_is_a_link_down_and_the_late_answer_is_dropped() -> None:
    def late(body: bytes, media: Media) -> tuple[int, bytes | None, Media]:
        time.sleep(0.4)
        return 200, b"too late", Media.TEXT

    central, *_ = await linked(inbox_p=late)
    with pytest.raises(LinkDown, match="no answer"):
        await central.request(b"hello", Media.JOSE, timeout=0.1)
    await until(lambda: central._reassembler.dropped > 0, 2.0)
    assert central.closed is None


async def test_an_answer_from_another_beacon_id_than_the_advert_is_refused() -> None:
    c_pipe, p_pipe = MemoryPipe.pair(182)
    accepting = asyncio.create_task(Link.accept(p_pipe, me=B, inbox=echo, policy=FAST))
    with pytest.raises(LinkRefused, match="another beacon id") as refused:
        await Link.open(c_pipe, me=A, expect="ffffffffffffffff", inbox=echo, policy=FAST)
    assert not refused.value.busy
    await accepting
    await until(lambda: p_pipe.closed is not None)


async def test_a_busy_robot_refuses_and_a_silent_one_is_unreachable() -> None:
    table = LinkTable(max_links=0, policy=FAST)
    c_pipe, p_pipe = MemoryPipe.pair(182)
    accepting = asyncio.create_task(table.accept(p_pipe, me=B, inbox_for=lambda route: echo))
    with pytest.raises(LinkRefused) as refused:
        await Link.open(c_pipe, me=A, expect=B, inbox=echo, policy=FAST)
    assert refused.value.busy and refused.value.reason == Refusal.BUSY
    assert await accepting is None
    silent, _never = MemoryPipe.pair(182)
    started = time.monotonic()
    with pytest.raises(LinkDown, match="did not take the link"):
        await Link.open(silent, me=A, expect=B, inbox=echo, policy=FAST)
    # asyncio may fire a timer up to one tick of the clock early, 15.6 ms on Windows
    assert time.monotonic() - started >= FAST.accept_s - 0.05


async def test_a_central_in_another_framing_version_is_told_so() -> None:
    raw, heard, task = await raw_peripheral()
    tag = new_tag()
    await raw.send(bytes((0x21,)) + tag.to_bytes(4, "big") + bytes(14))
    assert await task is None
    await until(lambda: heard)
    assert decode(heard[0]) == Refuse(tag, Refusal.VERSION)


async def test_a_table_holds_at_most_its_links_and_says_when_they_change() -> None:
    changes: list[LinkChange] = []
    table = LinkTable(policy=FAST, on_change=changes.append)
    assert table.max_links == MAX_LINKS
    centrals = []
    for i in range(MAX_LINKS + 1):
        c_pipe, p_pipe = MemoryPipe.pair(182)
        accepting = asyncio.create_task(table.accept(p_pipe, me=B, inbox_for=lambda route: echo))
        me = f"{i:016x}"
        try:
            centrals.append(await Link.open(c_pipe, me=me, expect=B, inbox=None, policy=FAST))
        except LinkRefused as e:
            assert i == MAX_LINKS and e.busy
        await accepting
    assert len(table) == MAX_LINKS and table.full
    assert [c.opened for c in changes] == [True] * MAX_LINKS
    assert all(c.inbound for c in changes)
    assert len(table.by_beacon(f"{0:016x}")) == 1
    assert await table.close_beacon(f"{0:016x}") == 1
    await until(lambda: len(table) == MAX_LINKS - 1)
    assert changes[-1].opened is False and changes[-1].beacon_id == f"{0:016x}"
    await table.close_all()
    await until(lambda: len(table) == 0)
    table.shutdown()


async def test_a_tag_already_in_use_is_refused() -> None:
    table = LinkTable(policy=FAST)
    tag = new_tag()
    for attempt in range(2):
        c_pipe, p_pipe = MemoryPipe.pair(182)
        accepting = asyncio.create_task(table.accept(p_pipe, me=B, inbox_for=lambda route: echo))
        if attempt == 0:
            await Link.open(c_pipe, me=A, expect=B, inbox=None, policy=FAST, tag=tag)
        else:
            with pytest.raises(LinkRefused) as refused:
                await Link.open(c_pipe, me=A, expect=B, inbox=None, policy=FAST, tag=tag)
            assert refused.value.reason == Refusal.POLICY
        await accepting
    table.shutdown()


def test_the_loop_thread_refuses_to_wait_on_itself_and_gives_up_at_its_deadline() -> None:
    loop = LoopThread("test-radio").start()
    try:

        async def value() -> int:
            return 42

        assert loop.call(value(), timeout=2) == 42

        async def inside() -> str:
            try:
                loop.call(value(), timeout=1)
            except RuntimeError as e:
                return str(e)
            return "it waited on itself"

        assert "own thread" in loop.call(inside(), timeout=2)

        async def forever() -> None:
            await asyncio.sleep(60)

        started = time.monotonic()
        with pytest.raises(TimeoutError):
            loop.call(forever(), timeout=0.2)
        assert time.monotonic() - started < 2
    finally:
        loop.stop()
    assert not loop.running


def test_a_message_posted_from_any_thread_rides_a_link_on_the_loop_and_back() -> None:
    loop = LoopThread("test-radio").start()
    try:
        replies: list[tuple[str, str]] = []

        def talker(wire: str, origin: str) -> tuple[int, str | None]:
            replies.append((wire, origin))
            return 200, '{"answered":true}'

        async def setup() -> tuple[LinkTable, Link]:
            table = LinkTable(policy=LinkPolicy())
            c_pipe, p_pipe = MemoryPipe.pair(182)
            accepting = asyncio.create_task(
                table.accept(p_pipe, me=B, inbox_for=lambda route: talk_inbox(talker, route))
            )
            central = await Link.open(c_pipe, me=A, expect=B, inbox=None)
            peripheral = await accepting
            assert peripheral is not None
            return table, central

        table, central = loop.call(setup(), timeout=5)
        dialler = LinkTable()
        dialler._links[central.id] = central  # as dial would have put it
        status, reply = LinkTransport(dialler, loop).post(central.route, "header.payload.sig")
        assert (status, reply) == (200, '{"answered":true}')
        assert replies == [("header.payload.sig", f"ble://{central.id}")]
        with pytest.raises(LinkDown, match="closed"):
            LinkTransport(dialler, loop).post("ble://00000000", "x.y.z")
        loop.call(table.close_all(), timeout=5)
        table.shutdown()
        dialler.shutdown()
    finally:
        loop.stop()


def test_talks_inbox_labels_a_reply_by_what_it_is() -> None:
    answers = {
        "jws": (200, "a.b.c"),
        "json": (200, '{"x":1}'),
        "refused": (403, "this robot has not heard you by radio"),
        "none": (204, None),
    }
    for name, (status, reply) in answers.items():
        handle = talk_inbox(lambda wire, origin, s=status, r=reply: (s, r), "ble://00000001")
        got = handle(b"msg", Media.JOSE)
        assert got[0] == status
        expected = {
            "jws": Media.JOSE,
            "json": Media.JSON,
            "refused": Media.TEXT,
            "none": Media.NONE,
        }[name]
        assert got[2] == expected, name


def test_a_post_waits_longer_for_a_longer_message_up_to_a_cap() -> None:
    assert post_timeout(0) == 5.0
    assert post_timeout(MAX_HELLO_BYTES) > post_timeout(4096) > post_timeout(0)
    assert post_timeout(10**9) == 20.0


async def test_closing_tells_the_other_end_why() -> None:
    central, peripheral, _, _ = await linked()
    await central.close(CloseReason.FAR)
    await until(lambda: peripheral.closed is not None)
    assert peripheral.closed == "closed by the other robot (far)"


async def test_a_request_sent_the_moment_accept_arrives_finds_the_link_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A radio may carry the ACCEPT, and the central's first request after it, while this end
    is still in the send that put the ACCEPT on the air: BlueZ's notification goes out, and the
    peripheral paces before its next one. The link has its inbox and its place in the table by
    then, or that first request is answered 404 by a link that has no inbox yet."""
    table = LinkTable(policy=FAST)
    c_pipe, p_pipe = MemoryPipe.pair(182)
    tag = new_tag()
    first = split(
        b"hello",
        tag=tag,
        msg_id=1,
        role=Role.REQUEST,
        status=0,
        media=Media.JOSE,
        datagram_size=182,
    )
    in_table: list[bool] = []
    send = p_pipe.send

    async def accept_then_hear(datagram: bytes, *, barrier: bool = True) -> None:
        await send(datagram, barrier=barrier)
        if isinstance(decode(datagram), Accept):
            in_table.append(len(table) == 1)
            for d in first:
                p_pipe._received(d)

    monkeypatch.setattr(p_pipe, "send", accept_then_hear)
    heard: list[bytes] = []
    c_pipe.on_datagram(heard.append)
    accepting = asyncio.create_task(table.accept(p_pipe, me=B, inbox_for=lambda route: echo))
    await c_pipe.send(encode(Open(tag, A, 182, Limits().max_message)))
    assert await accepting is not None
    await until(lambda: len(heard) > 1)
    answer = decode(heard[1])
    assert isinstance(answer, Data) and answer.head is not None
    assert answer.head.status == 200 and answer.chunk == b"echo:hello"
    assert in_table == [True]
    table.shutdown()
