"""Beacon rotation on a clock the test turns: a new id each period, never while something is
open, tried again after the retry, and never the same id twice in a row."""

from __future__ import annotations

import itertools

import pytest

from quackd.passport.beacon import is_beacon_id
from quackd.radio.rotation import RETRY_S, ROTATE_S, BeaconRotor


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def rotor(busy: list[bool], rotated: list[str], clock: Clock, **kw: object) -> BeaconRotor:
    return BeaconRotor(busy=lambda: busy[0], on_rotate=rotated.append, clock=clock, **kw)  # type: ignore[arg-type]


def test_a_new_id_each_period_and_never_before() -> None:
    clock, busy, rotated = Clock(), [False], []
    r = rotor(busy, rotated, clock)
    first = r.current
    assert is_beacon_id(first) and r.period_s == ROTATE_S
    clock.now = ROTATE_S - 1
    assert r.tick() is None and r.current == first and rotated == []
    assert r.due_in() == 1
    clock.now = ROTATE_S
    fresh = r.tick()
    assert fresh is not None and fresh != first and is_beacon_id(fresh)
    assert rotated == [fresh] and r.current == fresh and r.rotations == 1
    assert r.due_in() == ROTATE_S


def test_while_anything_is_open_the_id_stays_and_rotation_retries() -> None:
    clock, busy, rotated = Clock(), [True], []
    r = rotor(busy, rotated, clock)
    first = r.current
    for step in range(10):
        clock.now = ROTATE_S + step * RETRY_S
        assert r.tick() is None
    assert r.current == first and rotated == [] and r.deferred == 10
    busy[0] = False
    clock.now += RETRY_S - 1
    assert r.tick() is None  # the retry is not due yet
    clock.now += 1
    assert r.tick() is not None and len(rotated) == 1 and r.current != first


def test_ids_never_repeat_back_to_back_even_when_the_draw_does() -> None:
    draws = itertools.cycle(["aa" * 8, "aa" * 8, "bb" * 8, "bb" * 8, "aa" * 8])
    clock, busy, rotated = Clock(), [False], []
    r = rotor(busy, rotated, clock, draw=lambda: next(draws), period_s=10.0)
    assert r.current == "aa" * 8
    for step in (1, 2):
        clock.now = 10.0 * step
        r.tick()
    assert rotated == ["bb" * 8, "aa" * 8]


def test_a_rotor_refuses_nonsense() -> None:
    with pytest.raises(ValueError):
        BeaconRotor(busy=lambda: False, on_rotate=print, period_s=0)
    with pytest.raises(ValueError):
        BeaconRotor(busy=lambda: False, on_rotate=print, current="not hex")
    kept = BeaconRotor(busy=lambda: False, on_rotate=print, current="0123456789abcdef")
    assert kept.current == "0123456789abcdef"
