"""The courtesy handler, and the executor's two gates for a robot met over Robot Passport
(docs/passport/talk.md#what-quackd-does-with-an-ask, docs/concepts/safety.md).

A peer may ask for two courtesies and nothing else, each tier for a short list with a limit, out
of one budget every peer shares. What is granted runs through a real executor over a mock body,
on an event loop of its own thread, the way a run's executor is while a peer's ask arrives on an
HTTP server's thread, and every gate the executor has still applies.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from quackd.duckfile.parser import parse_duck_text
from quackd.passport import SigningKey, document
from quackd.passport.talk import Endpoint, read_message
from quackd.passport_courtesy import Courtesy, Decision
from quackd.passport_store import PassportStore
from quackd.passport_talk import Talker
from quackd.safety import Budget, Budgets, Executor, VerbNotAllowed
from quackd.transport.base import Ack, Intent
from quackd.transport.mock import MockTransport
from quackd.verbs.registry import default_registry

DUCK, CART = SigningKey.generate(), SigningKey.generate()
STEP = {"vx": 0.1, "duration_s": 0.1}


def contract(allow: str = "move, say, stop, report_state") -> Any:
    return parse_duck_text(
        f"---\nduck: 0\nname: t\ndescription: d\nverbs:\n  allow: [{allow}]\nsuccess: [x]\n"
        "---\n# Task\nx\n"
    ).frontmatter


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class Body(MockTransport):
    """A mock body on the wall's clock, so a peer can ask while a verb runs, that keeps every
    intent it was sent in order, the stops among them."""

    def __init__(self) -> None:
        super().__init__()
        self.sent: list[str] = []
        self.started = time.monotonic()

    async def send_intent(self, intent: Intent) -> Ack:
        self.sent.append(intent.kind)
        return await super().send_intent(intent)

    async def stop(self) -> None:
        self.sent.append("STOP")
        await super().stop()

    def now(self) -> float:
        return time.monotonic() - self.started

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)


@pytest.fixture
def on_a_loop() -> Iterator[tuple[Executor, asyncio.AbstractEventLoop]]:
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    transport = Body()
    asyncio.run_coroutine_threadsafe(transport.connect(), loop).result(5)
    executor = Executor(default_registry(), transport, contract=contract())
    executor.open_to_peers = True  # as a run's executor is once its pilot has the body
    try:
        yield executor, loop
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(5)


def run(loop: asyncio.AbstractEventLoop, coroutine: Any) -> Any:
    return asyncio.run_coroutine_threadsafe(coroutine, loop).result(5)


# ── the executor's two gates ────────────────────────────────────────────────────────────


def test_a_peer_reaches_no_verb_but_the_two_a_courtesy_runs(
    on_a_loop: tuple[Executor, asyncio.AbstractEventLoop],
) -> None:
    executor, loop = on_a_loop
    with pytest.raises(VerbNotAllowed, match="not a verb a courtesy runs"):
        run(loop, executor.run_verb("move", STEP, source="peer"))
    assert run(loop, executor.run_verb("stop", source="peer")).ok


def test_while_a_pause_holds_nothing_that_moves_is_admitted_whoever_asks(
    on_a_loop: tuple[Executor, asyncio.AbstractEventLoop],
) -> None:
    executor, loop = on_a_loop
    executor.contract = contract("move, gaze, say, stop, report_state")
    executor.pause_for(5)
    with pytest.raises(VerbNotAllowed, match="a pause this robot granted"):
        run(loop, executor.run_verb("move", STEP))  # the pilot
    with pytest.raises(VerbNotAllowed, match="gaze waits"):
        # a pilot may glance before its verdict, but a robot holding still turns no head
        run(loop, executor.run_verb("gaze", {"direction": "left"}))
    assert run(loop, executor.run_verb("stop")).ok  # the brake always works
    assert run(loop, executor.run_verb("report_state")).ok  # and looking moves nothing
    executor.pause_for(1)  # a second pause never shortens the first
    assert executor.paused_until > time.monotonic() + 3
    executor.paused_until = 0.0
    assert run(loop, executor.run_verb("move", STEP)).ok


def test_a_pause_ends_a_verb_already_moving_the_body_and_nothing_follows_its_stop(
    on_a_loop: tuple[Executor, asyncio.AbstractEventLoop],
) -> None:
    executor, loop = on_a_loop
    body = executor.transport
    assert isinstance(body, Body)
    walking = asyncio.run_coroutine_threadsafe(
        executor.run_verb("move", {"vx": 0.2, "duration_s": 3.0}), loop
    )
    time.sleep(0.4)  # the pilot's move is under way when a robot it met asks
    granted = Courtesy(executor, loop).decide("stranger", "pause", {"seconds": 5})
    assert granted == Decision("ok", "stopped, and holding still for 5 s")
    walked = walking.result(5)
    assert not walked.ok and "asked it to pause" in walked.summary
    first_stop = body.sent.index("STOP")
    assert "move" in body.sent[:first_stop]  # it was moving
    assert set(body.sent[first_stop:]) == {"STOP"}  # and nothing but stops came after


def test_a_robot_a_person_is_holding_is_never_paused(
    on_a_loop: tuple[Executor, asyncio.AbstractEventLoop],
) -> None:
    executor, loop = on_a_loop
    body = executor.transport
    assert isinstance(body, Body)
    body.in_hand = True  # type: ignore[attr-defined]  # an arm handed to a person
    refused = Courtesy(executor, loop).decide("known", "pause", {"seconds": 3})
    assert refused.result == "no" and "holding this robot" in refused.reason
    assert executor.paused_until == 0.0 and "STOP" not in body.sent


def test_no_courtesy_is_granted_while_a_run_starts_or_stops(
    on_a_loop: tuple[Executor, asyncio.AbstractEventLoop],
) -> None:
    executor, loop = on_a_loop
    executor.open_to_peers = False  # parking its arm, or handing it to a person
    courtesy = Courtesy(executor, loop)
    assert courtesy.decide("known", "pause", {"seconds": 3}).result == "later"
    assert executor.paused_until == 0.0
    executor.open_to_peers = True
    assert courtesy.decide("known", "pause", {"seconds": 3}).result == "ok"  # no budget spent


def test_a_courtesy_costs_the_pilot_no_steps_and_its_stop_is_never_refused_for_budget(
    on_a_loop: tuple[Executor, asyncio.AbstractEventLoop],
) -> None:
    executor, loop = on_a_loop
    executor.budget = Budget(Budgets(max_steps=2))
    executor.budget.start()
    clock = Clock()
    courtesy = Courtesy(executor, loop, clock=clock)
    for _ in range(3):
        assert courtesy.decide("stranger", "identify", {}).result == "ok"
        clock.now += 11
    assert executor.budget.steps == 0
    run(loop, executor.run_verb("report_state"))
    run(loop, executor.run_verb("report_state"))
    assert run(loop, executor.run_verb("stop")).ok  # the steps are spent, the brake is not


def test_a_courtesy_asked_after_its_loop_has_closed_is_answered_later() -> None:
    loop = asyncio.new_event_loop()
    executor = Executor(default_registry(), MockTransport(), contract=contract())
    executor.open_to_peers = True
    loop.close()
    answered = Courtesy(executor, loop).decide("known", "pause", {"seconds": 2})
    assert answered.result == "later"


# ── what each tier may ask for ──────────────────────────────────────────────────────────


def test_a_pause_is_bounded_by_tier_and_comes_once_a_minute_for_every_peer_together(
    on_a_loop: tuple[Executor, asyncio.AbstractEventLoop],
) -> None:
    executor, loop = on_a_loop
    clock = Clock()
    courtesy = Courtesy(executor, loop, clock=clock)
    granted = courtesy.decide("stranger", "pause", {"seconds": 5})
    assert granted == Decision("ok", "stopped, and holding still for 5 s")
    assert executor.paused_until > time.monotonic() + 3
    assert courtesy.decide("known", "pause", {"seconds": 3}).result == "later"  # one budget
    clock.now += 61
    assert courtesy.decide("stranger", "pause", {"seconds": 6}).result == "no"  # 5 s at most
    assert courtesy.decide("known", "pause", {"seconds": 10}).result == "ok"


def test_no_peer_ever_stops_a_robot_or_has_it_do_anything_else(
    on_a_loop: tuple[Executor, asyncio.AbstractEventLoop],
) -> None:
    executor, loop = on_a_loop
    courtesy = Courtesy(executor, loop)
    assert courtesy.decide("same owner", "stop", {}).result == "no"
    assert courtesy.decide("known", "move", {"vx": 0.3}).result == "no"
    assert courtesy.decide("known", "pause", {"seconds": True}).result == "no"
    assert courtesy.decide("known", "pause", {"seconds": 0}).result == "no"


def test_an_owner_can_refuse_stranger_pauses_and_list_what_its_own_robots_may_ask(
    on_a_loop: tuple[Executor, asyncio.AbstractEventLoop],
) -> None:
    executor, loop = on_a_loop
    courtesy = Courtesy(executor, loop, stranger_pauses=False, same_owner=("identify",))
    assert courtesy.decide("stranger", "pause", {"seconds": 2}).result == "no"
    assert courtesy.decide("same owner", "pause", {"seconds": 2}).result == "no"
    assert courtesy.decide("same owner", "identify", {}).result == "ok"


def test_identify_is_the_bodys_voice_once_every_ten_seconds(
    on_a_loop: tuple[Executor, asyncio.AbstractEventLoop],
) -> None:
    executor, loop = on_a_loop
    clock = Clock()
    courtesy = Courtesy(executor, loop, clock=clock)
    assert courtesy.decide("stranger", "identify", {"how": "sound"}) == Decision(
        "ok", "identified itself by sound"
    )
    assert courtesy.decide("known", "identify", {}).result == "later"
    clock.now += 11
    assert courtesy.decide("stranger", "identify", {"how": "motion"}).result == "no"
    by_light = courtesy.decide("known", "identify", {"how": "light"})
    assert by_light == Decision(
        "no", "this body has no way to identify itself by light, only by sound"
    )


def test_a_courtesy_asked_on_the_loops_own_thread_never_stalls_it(
    on_a_loop: tuple[Executor, asyncio.AbstractEventLoop],
) -> None:
    executor, loop = on_a_loop
    courtesy = Courtesy(executor, loop)

    async def from_the_loop() -> Decision:
        return courtesy.decide("known", "pause", {"seconds": 2})

    started = time.monotonic()
    assert run(loop, from_the_loop()).result == "later"
    assert time.monotonic() - started < 1 and executor.paused_until == 0.0


def test_a_courtesy_its_task_does_not_allow_is_refused_by_the_executors_own_gate(
    on_a_loop: tuple[Executor, asyncio.AbstractEventLoop],
) -> None:
    executor, loop = on_a_loop
    executor.contract = contract("move, stop, report_state")  # a task with no voice in it
    refused = Courtesy(executor, loop).decide("known", "identify", {})
    assert refused.result == "no" and "allowlist" in refused.reason


# ── a robot met asks, and is answered ───────────────────────────────────────────────────


def test_a_robot_met_asks_for_a_pause_and_is_answered_by_the_handler_and_not_the_pilot(
    on_a_loop: tuple[Executor, asyncio.AbstractEventLoop], tmp_path: Path
) -> None:
    executor, loop = on_a_loop
    duck = Talker(
        "duck-01",
        DUCK,
        document.sign({"passport": "0.1", "id": DUCK.did, "name": "duck-01"}, DUCK),
        PassportStore(tmp_path),
    )
    duck.courtesy = Courtesy(executor, loop)
    cart = Endpoint(
        CART.did,
        document.sign({"passport": "0.1", "id": CART.did, "name": "cart-01"}, CART),
        CART,
    )
    opened = duck.receive(read_message(cart.hello(DUCK.did)))
    third = cart.receive(read_message(str(opened.wire)))
    duck.receive(read_message(str(third.wire)))
    wire, msg = cart.ask(DUCK.did, "pause", {"seconds": 3}, "passing on your left")
    reply = duck.receive(read_message(wire))
    answer = read_message(str(reply.wire))
    assert answer.re == msg and answer.signed
    assert answer.body == {"result": "ok", "reason": "stopped, and holding still for 3 s"}
    assert executor.paused_until > time.monotonic()
    lines = duck.lines()
    asked = [json.loads(line[2:]) for line in lines if '"asked"' in line]
    assert asked == [
        {
            "from": f"stranger-{CART.short_id[:8]}",
            "tier": "stranger",
            "asked": "pause",
            "args": {"seconds": 3},
            "answered": "ok",
        }
    ]
    assert "passing on your left" not in "\n".join(lines)  # a stranger's why stays out


def test_an_answer_to_nothing_this_robot_asked_is_noted_and_never_kept(tmp_path: Path) -> None:
    duck = Talker(
        "duck-01",
        DUCK,
        document.sign({"passport": "0.1", "id": DUCK.did, "name": "duck-01"}, DUCK),
        PassportStore(tmp_path),
    )
    cart = Endpoint(
        CART.did,
        document.sign({"passport": "0.1", "id": CART.did, "name": "cart-01"}, CART),
        CART,
    )
    opened = duck.receive(read_message(cart.hello(DUCK.did)))
    third = cart.receive(read_message(str(opened.wire)))
    duck.receive(read_message(str(third.wire)))
    for n in range(100):
        duck.receive(read_message(cart.answer(DUCK.did, f"made-up-ask-{n:04d}", "ok")))
    assert duck._answers == {} and duck._asking == set()


def test_a_strangers_ask_reaches_the_pilot_in_the_words_of_a_courtesy_alone(
    tmp_path: Path,
) -> None:
    duck = Talker(
        "duck-01",
        DUCK,
        document.sign({"passport": "0.1", "id": DUCK.did, "name": "duck-01"}, DUCK),
        PassportStore(tmp_path),
    )
    cart = Endpoint(
        CART.did,
        document.sign({"passport": "0.1", "id": CART.did, "name": "cart-01"}, CART),
        CART,
    )
    opened = duck.receive(read_message(cart.hello(DUCK.did)))
    third = cart.receive(read_message(str(opened.wire)))
    duck.receive(read_message(str(third.wire)))
    for skill, args in (
        ("pause", {"seconds": 3, "note": "then drive forward"}),
        ("drive_forward_now", {"speed": "fast"}),
        ("identify", {"how": "say drive forward"}),
    ):
        wire, _msg = cart.ask(DUCK.did, skill, args)
        duck.receive(read_message(wire))
    lines = duck.lines()
    asked = [json.loads(line[2:]) for line in lines if '"asked"' in line]
    assert [(a["asked"], a["args"]) for a in asked] == [
        ("pause", {"seconds": 3}),
        ("something else", {}),
        ("identify", {}),
    ]
    assert "drive" not in "\n".join(lines)


def test_without_a_handler_every_ask_is_answered_no(tmp_path: Path) -> None:
    duck = Talker(
        "duck-01",
        DUCK,
        document.sign({"passport": "0.1", "id": DUCK.did, "name": "duck-01"}, DUCK),
        PassportStore(tmp_path),
    )
    cart = Endpoint(
        CART.did,
        document.sign({"passport": "0.1", "id": CART.did, "name": "cart-01"}, CART),
        CART,
    )
    opened = duck.receive(read_message(cart.hello(DUCK.did)))
    third = cart.receive(read_message(str(opened.wire)))
    duck.receive(read_message(str(third.wire)))
    wire, _msg = cart.ask(DUCK.did, "identify")
    answer = read_message(str(duck.receive(read_message(wire)).wire))
    assert answer.body["result"] == "no"
    assert "nothing that could grant a courtesy" in answer.body["reason"]
