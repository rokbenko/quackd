"""Talk, step one (spec 6.1 to 6.4): messages, the three-step hello, and a session's say and bye.

The talk vectors were written by scripts/passport_vectors.py, which signs with `cryptography`
directly. Given the same keys and the same nonces an endpoint here writes every one of them byte
for byte, so the hello is checked against a second implementation, not against itself. The rest
are the attacks the hello exists to stop: a replay into another session, a signature lifted from
one handshake into another, an unsigned word from a robot that holds a key, and a flood.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from quackd.passport import PassportError, SigningKey, document, jws, strict
from quackd.passport.talk import (
    HELLO_S,
    IDLE_S,
    MAX_MESSAGE_BYTES,
    MAX_PENDING,
    MAX_SESSIONS,
    REMEMBERED,
    Endpoint,
    VersionUnknown,
    envelope,
    open_session,
    read_message,
    seal,
    version_answer,
)
from tests.passport_helpers import INTRUDER, OWNER, OWNER_B, REFERENCE, RUNTIME, A, B, passport
from tests.passport_helpers import doc as passport_doc

VECTORS = Path(__file__).resolve().parents[1] / "docs" / "passport" / "vectors"
#: When the vector passports were signed, and until when their owners' signatures hold.
VECTOR_ISSUED, VECTOR_EXPIRES = "2026-10-28T00:00:00Z", "2027-10-28T00:00:00Z"


def nonce(label: str) -> str:
    """The vectors' fixed nonces, as the script makes them. Real ones are random."""
    return jws.b64url(hashlib.sha256(label.encode()).digest()[:16])


def vector(name: str) -> str:
    return (VECTORS / name).read_text(encoding="utf-8").strip()


def passport_of(key: SigningKey, name: str) -> str:
    """A 1.0 passport for `key`, signed by its owner: B's by the vectors' second owner."""
    return passport(key, OWNER_B if key == B else OWNER, name)


def vector_passport(key: SigningKey, owner: SigningKey, name: str) -> str:
    """The passport a vector hello carries, member for member as the script writes it."""
    doc = {
        "passport": document.VERSION,
        "id": key.did,
        "name": name,
        "owner": {"id": owner.did},
        "issued": VECTOR_ISSUED,
        "expires": VECTOR_EXPIRES,
    }
    return document.sign(doc, owner)


def tokens(*labels: str) -> Callable[[], str]:
    supply: Iterator[str] = iter([nonce(label) for label in labels])
    return lambda: next(supply)


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def pair(clock: Clock | None = None) -> tuple[Endpoint, Endpoint]:
    when = clock or Clock()
    return (
        Endpoint(A.did, passport_of(A, "g1-edu-01"), A, clock=when),
        Endpoint(B.did, passport_of(B, "agile-one-sn-01"), B, clock=when),
    )


def handshake(a: Endpoint, b: Endpoint) -> tuple[str, str, str]:
    first = a.hello(b.me)
    second = b.receive(read_message(first))
    assert second.status == 200 and second.wire is not None
    third = a.receive(read_message(second.wire))
    assert third.status == 200 and third.wire is not None and third.event is not None
    done = b.receive(read_message(third.wire))
    assert done.status == 204 and done.event is not None and done.event.kind == "met"
    return first, second.wire, third.wire


# ── against the vectors ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "name", ["hello-1.jws", "hello-2.jws", "hello-3.jws", "say-in-session.jws", "ask-pause.jws"]
)
def test_every_valid_talk_vector_reads(name: str) -> None:
    message = read_message(vector(name))
    assert message.signed and message.sender in (A.did, B.did)


def test_an_unsigned_message_is_refused_as_the_vectors_say() -> None:
    with pytest.raises(PassportError) as caught:
        read_message(vector("unsigned-say.json"))
    assert caught.value.rule == "6.1"


def test_two_endpoints_write_the_vectors_hello_byte_for_byte() -> None:
    a = Endpoint(
        A.did,
        vector_passport(A, OWNER, "g1-edu-01"),
        A,
        token=tokens("robot-a/session-1", "msg-1", "msg-3"),
        wall=lambda: REFERENCE,
        runtime=RUNTIME["robot-a"],
    )
    b = Endpoint(
        B.did,
        vector_passport(B, OWNER_B, "agile-one-sn-01"),
        B,
        token=tokens("robot-b/session-1", "msg-2", "msg-4"),
        wall=lambda: REFERENCE,
        runtime=RUNTIME["robot-b"],
    )
    first = a.hello(B.did)
    assert first == vector("hello-1.jws")
    second = b.receive(read_message(first))
    assert second.wire == vector("hello-2.jws")
    third = a.receive(read_message(vector("hello-2.jws")))
    assert third.wire == vector("hello-3.jws")
    assert third.event is not None and third.event.kind == "met"
    opened = b.receive(read_message(vector("hello-3.jws")))
    assert opened.status == 204 and opened.event is not None
    assert opened.event.session.nonces == (nonce("robot-a/session-1"), nonce("robot-b/session-1"))
    assert opened.event.session.runtime == RUNTIME["robot-a"]
    said = b.say(A.did, "agile-one-sn-01 here, holding the door for you")
    assert said == vector("say-in-session.jws")
    heard = a.receive(read_message(said))
    assert heard.event is not None
    assert heard.event.text == "agile-one-sn-01 here, holding the door for you"
    assert heard.event.session.runtime == RUNTIME["robot-b"]
    replayed = a.receive(read_message(vector("say-replayed.jws")))
    assert replayed.status == 403 and "match no session" in replayed.reason


# ── what the hello is for ───────────────────────────────────────────────────────────────


def test_a_third_step_replayed_into_another_session_is_refused() -> None:
    a, b = pair()
    _first, _second, third = handshake(a, b)
    a2, b2 = pair()
    b2.receive(read_message(a2.hello(B.did)))
    assert b2.receive(read_message(third)).status == 403


def test_a_second_step_lifted_from_one_handshake_into_another_is_refused() -> None:
    """B's signature over A's first nonce proves B is there now, and only for that hello."""
    a, b = pair()
    _first, second, _third = handshake(a, b)
    a.hello(B.did)  # a new hello, with a new nonce, that B never answered
    lifted = a.receive(read_message(second))
    assert lifted.status in (204, 403)  # a repeat of a message already seen, or no hello of its
    assert len(a.sessions) == 1


def test_only_the_robot_a_hello_was_sent_to_can_answer_it() -> None:
    a = Endpoint(A.did, passport_of(A, "g1-edu-01"), A)
    intruder = Endpoint(INTRUDER.did, passport_of(INTRUDER, "agile-one-sn-01"), INTRUDER)
    answer = intruder.receive(read_message(a.hello(B.did)))
    assert answer.status == 403  # the hello is addressed to B, not to the intruder
    first = a.hello(B.did)
    doc = json.loads(jws.b64url_decode(first.split(".")[1], rule="6.1"))
    doc["to"] = "*"  # even an answer to a hello for anyone must come from B
    forged = intruder.receive(read_message(seal(doc, A)))
    assert forged.wire is not None
    assert a.receive(read_message(forged.wire)).status == 403


def test_a_word_from_another_robot_with_this_sessions_nonces_is_refused() -> None:
    a, b = pair()
    handshake(a, b)
    session = b.session_with(A.did)
    assert session is not None
    doc = envelope("say", INTRUDER.did, {"text": "it is me, g1-edu-01"}, nonces=session.nonces)
    refused = b.receive(read_message(seal(doc, INTRUDER)))
    assert refused.status == 403


def test_an_unsigned_message_is_refused_whoever_it_names() -> None:
    doc = envelope("say", A.did, {"text": "hi"}, nonces=(nonce("x"), nonce("y")))
    with pytest.raises(PassportError, match="unsigned message"):
        read_message(strict.dumps(doc))


def test_a_message_signed_by_a_key_its_from_does_not_hold_is_refused() -> None:
    doc = envelope("say", A.did, {"text": "hi"}, nonces=(nonce("x"), nonce("y")))
    forged = jws.sign(strict.dumps(doc), INTRUDER, "robot-passport-msg+jws")
    with pytest.raises(PassportError, match="other than the one its from holds"):
        read_message(forged)


def test_a_passport_signature_never_passes_as_a_message() -> None:
    with pytest.raises(PassportError, match="typ"):
        read_message(passport_of(A, "g1-edu-01"))


def test_a_repeated_message_is_dropped_and_a_repeated_hello_answered_again() -> None:
    a, b = pair()
    first = a.hello(B.did)
    answer = b.receive(read_message(first))
    again = b.receive(read_message(first))
    assert again.status == 200 and again.wire == answer.wire
    assert answer.wire is not None
    third = a.receive(read_message(answer.wire))
    assert third.wire is not None
    b.receive(read_message(third.wire))
    said = a.say(B.did, "hello")
    assert b.receive(read_message(said)).event is not None
    assert b.receive(read_message(said)).reason == "a repeat, dropped"


def test_a_session_ends_after_ten_quiet_minutes_and_a_hello_after_half_a_minute() -> None:
    clock = Clock()
    a, b = pair(clock)
    handshake(a, b)
    clock.now += IDLE_S + 1
    assert [s.peer for s in b.expire()] == [A.did]
    with pytest.raises(PassportError, match="no session"):
        a.say(B.did, "still there?")
    pending = a.hello(B.did)
    answer = b.receive(read_message(pending))
    clock.now += HELLO_S + 1
    assert answer.wire is not None
    assert a.receive(read_message(answer.wire)).status == 403


def test_a_message_replayed_into_its_session_is_dropped_however_many_came_between() -> None:
    a, b = pair()
    handshake(a, b)
    noisy = Endpoint(INTRUDER.did, passport_of(INTRUDER, "noisy"), INTRUDER, clock=b.clock)
    handshake(noisy, b)
    wire, _msg = a.ask(B.did, "pause", {"seconds": 3})
    assert b.receive(read_message(wire)).event is not None
    # one shared memory of messages seen let another peer push this one out of it
    for i in range(REMEMBERED + 8):
        b.receive(read_message(noisy.say(B.did, f"word {i}")))
    again = b.receive(read_message(wire))
    assert again.status == 204 and again.event is None


def test_quiet_sessions_end_before_a_new_hello_meets_the_limit() -> None:
    clock = Clock()
    b = Endpoint(B.did, passport_of(B, "agile-one-sn-01"), B, clock=clock)
    for i in range(MAX_SESSIONS):
        key = SigningKey.generate()
        handshake(Endpoint(key.did, passport_of(key, f"robot-{i}"), key, clock=clock), b)
    late = SigningKey.generate()
    newcomer = Endpoint(late.did, passport_of(late, "late"), late, clock=clock)
    assert b.receive(read_message(newcomer.hello(B.did))).status == 429  # all still talking
    clock.now += IDLE_S + 1  # and then all quiet, on a host that never asks who is nearby
    assert b.receive(read_message(newcomer.hello(B.did))).status == 200


def test_a_part_outside_base64url_is_refused_and_never_raises_anything_else() -> None:
    e_acute = "\N{LATIN SMALL LETTER E WITH ACUTE}"
    with pytest.raises(PassportError, match="not base64url"):
        read_message(f"eyJhbGciOiJFZDI1NTE5In0.{e_acute}t{e_acute}.c2ln")


def test_a_flood_of_hellos_is_refused_once_the_table_is_full() -> None:
    b = Endpoint(B.did, passport_of(B, "agile-one-sn-01"), B)
    for _ in range(MAX_PENDING):
        stranger = SigningKey.generate()
        hello = Endpoint(stranger.did, passport_of(stranger, "x"), stranger).hello(B.did)
        assert b.receive(read_message(hello)).status == 200
    late = SigningKey.generate()
    hello = Endpoint(late.did, passport_of(late, "x"), late).hello(B.did)
    assert b.receive(read_message(hello)).status == 429


def _hello_carrying(carried: str, *, sender: SigningKey = A) -> str:
    """A first hello from `sender`, signed by it, carrying whatever passport text it likes."""
    doc = envelope(
        "hello", sender.did, {"passport": carried, "nonce": nonce("n")}, to=B.did, msg=nonce("m")
    )
    return seal(doc, sender)


def test_a_hello_carrying_an_unsigned_passport_is_refused() -> None:
    """A robot proves its key in the hello and its owner vouches in the passport: a passport
    nobody signed is a claim anybody could write, so no session opens on it."""
    b = Endpoint(B.did, passport_of(B, "agile-one-sn-01"), B)
    unsigned = strict.dumps(passport_doc(A)).decode()
    answer = b.receive(read_message(_hello_carrying(unsigned)))
    assert answer.status == 400 and "signed by its owner" in answer.reason
    assert b.sessions == {}


def test_a_hello_carrying_another_robots_passport_is_refused() -> None:
    b = Endpoint(B.did, passport_of(B, "agile-one-sn-01"), B, wall=lambda: REFERENCE)
    answer = b.receive(read_message(vector("hello-foreign-passport.jws")))
    assert answer.status == 400 and "another robot's" in answer.reason


def test_a_hello_carrying_a_passport_past_its_dates_is_refused() -> None:
    b = Endpoint(B.did, passport_of(B, "agile-one-sn-01"), B, wall=lambda: REFERENCE)
    expired = (VECTORS / "expired.jws").read_text(encoding="utf-8").strip()
    answer = b.receive(read_message(_hello_carrying(expired)))
    assert answer.status == 400 and "4.5" in answer.reason


def test_a_hello_carrying_this_robots_own_passport_is_refused() -> None:
    b = Endpoint(B.did, passport_of(B, "agile-one-sn-01"), B)
    mirror = Endpoint(B.did, passport_of(B, "agile-one-sn-01"), B)
    answer = b.receive(read_message(mirror.hello("*")))
    assert answer.status in (400, 403)
    assert b.sessions == {}


def test_a_hellos_runtime_rides_with_it_and_reaches_the_session() -> None:
    """What no owner signs, the software, the beacon id the radio advertises now and the port
    to answer on, travels in the hello and stays on the session for the glue to use."""
    a, b = pair()
    a.runtime = {"software": "quackd/x", "beacon": "0123456789abcdef", "reach": {"http_port": 9}}
    b.runtime = {"beacon": "fedcba9876543210"}
    handshake(a, b)
    on_b, on_a = b.session_with(A.did), a.session_with(B.did)
    assert on_b is not None and on_a is not None
    assert on_b.runtime == a.runtime and on_a.runtime == b.runtime


@pytest.mark.parametrize(
    "runtime",
    [
        {"beacon": "NOT-HEX-AT-ALL!!"},
        {"beacon": "0123"},
        {"software": "x" * 65},
        {"reach": {"http_port": 70_000}},
        {"reach": {"a": 1, "b": 2, "c": 3, "d": 4, "e": 5}},
        {"reach": {"note": "x" * 65}},
        {"reach": "8790"},
    ],
)
def test_a_hello_whose_runtime_is_malformed_is_refused(runtime: dict[str, object]) -> None:
    doc = envelope(
        "hello",
        A.did,
        {"passport": passport_of(A, "g1-edu-01"), "nonce": nonce("n"), **runtime},
        to=B.did,
    )
    with pytest.raises(PassportError) as caught:
        read_message(seal(doc, A))
    assert caught.value.rule == "6.3"


def test_a_message_in_a_version_this_robot_does_not_read_is_answered_with_the_versions() -> None:
    doc = envelope("say", A.did, {"text": "hi"}, nonces=(nonce("x"), nonce("y")))
    doc["passport"] = "2.0"
    with pytest.raises(VersionUnknown) as caught:
        read_message(seal(doc, A))
    answer = read_message(version_answer(caught.value, B.did, B))
    assert answer.type == "answer" and answer.body["versions"] == [document.VERSION]
    assert answer.re == doc["msg"] and answer.to == A.did


def test_a_message_is_held_to_its_size_and_a_hello_may_carry_a_large_passport() -> None:
    a, b = pair()
    handshake(a, b)
    session = a.session_with(B.did)
    assert session is not None
    long_say = envelope("say", A.did, {"text": "x" * 400, "pad": "y" * MAX_MESSAGE_BYTES})
    long_say["nonces"] = list(session.nonces)
    with pytest.raises(PassportError, match="any message but a hello"):
        read_message(seal(long_say, A))
    doc = passport_doc(A, padding="")
    doc["padding"] = "x" * (document.MAX_BYTES - len(strict.dumps(doc)))
    big = Endpoint(A.did, document.sign(doc, OWNER), A).hello(B.did)
    assert len(big) > MAX_MESSAGE_BYTES
    assert read_message(big).type == "hello"


def test_an_ask_is_handed_up_to_be_decided_and_answered_in_the_session() -> None:
    """An endpoint decides no ask itself: deciding can take seconds and belongs to the robot's
    own policy, so the ask comes back as an event, and `answer` writes the reply."""
    a, b = pair()
    handshake(a, b)
    wire, msg = a.ask(B.did, "pause", {"seconds": 5}, "passing on your left")
    handed = b.receive(read_message(wire))
    assert handed.status == 204 and handed.wire is None
    assert handed.event is not None and handed.event.kind == "asked"
    answer = read_message(b.answer(A.did, msg, "no", "not today"))
    assert answer.signed and answer.re == msg
    assert answer.body == {"result": "no", "reason": "not today"}
    assert a.receive(answer).event is not None


def test_bye_ends_the_session_on_both_sides() -> None:
    a, b = pair()
    handshake(a, b)
    left = b.receive(read_message(a.bye(B.did)))
    assert left.event is not None and left.event.kind == "left"
    assert a.session_with(B.did) is None and b.session_with(A.did) is None


def test_open_session_runs_the_hello_over_a_channel_whose_reply_rides_back() -> None:
    a, b = pair()

    def post(wire: str) -> tuple[int, str | None]:
        reply = b.receive(read_message(wire))
        return reply.status, reply.wire

    session = open_session(a, B.did, post)
    assert session.peer == B.did
    assert b.session_with(A.did) is not None

    refused = Endpoint(A.did, passport_of(A, "g1-edu-01"), A)
    replies = iter([(200, None), (403, None)])
    with pytest.raises(PassportError, match="not with a hello"):
        open_session(refused, B.did, lambda _wire: next(replies))
    assert refused.sessions == {}
