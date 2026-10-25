"""The two halves of the policy protocol, talking to each other, and the arm's loop behind one.

The server here is the real one (`quackd_lerobot.policy.server`), in-process on port 0 on
loopback, serving a scripted policy by the name `quackd policy serve --policy scripted:NAME`
takes, and the client is the real `RemoteRunner`. So a field renamed on one side, a shape one
side sends and the other refuses, or a bound one side moved is caught here, and the segments
below run the real policy loop over the backend and the test suite's `FakeArm`, through HTTP,
end to end. It runs in-process rather than as a subprocess for the reason the Jetson daemon's
contract test gives: subprocess servers flake on this project's Windows machine.

What it cannot catch is anything about a checkpoint, which needs torch: CI's torch job loads a
tiny one through this same server in `tests/test_policy_pipeline.py`. What the arm's side
checks of a policy at connect (`fit.py`) is here, against a server that says what a checkpoint
would say about itself, since the check reads only what the server says.
"""

from __future__ import annotations

import contextlib
import http.client
import importlib.util
import json
import math
import re
import socket
import sys
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

import numpy as np
import pytest
from typer.testing import CliRunner

from quackd import host
from quackd.cli import app
from quackd.command import HIDDEN
from quackd.safety import Executor, allow_all
from quackd.transport.base import TransportError
from quackd.verbs.registry import registry_from_manifest
from quackd_lerobot import LeRobotAdapter
from quackd_lerobot.policy import protocol as wire
from quackd_lerobot.policy import server as S
from quackd_lerobot.policy.client import (
    STEP_TIMEOUT_S,
    PolicyServerError,
    RemoteRunner,
    client_token,
    policy_address,
)
from quackd_lerobot.policy.fit import PolicyMisfit, fit
from quackd_lerobot.policy.loop import (
    FIRST_CHUNK_S,
    STARVE_S,
    Plan,
    latency_ticks,
    longest_latency,
    refill_at,
    starved_each_chunk,
)
from quackd_lerobot.policy.runner import Chunk, Observation
from quackd_lerobot.policy.scripted import (
    SCRIPTED_HZ,
    SCRIPTS,
    SWEEP_DEG,
    SWEEP_JOINT,
    ScriptedRunner,
)
from quackd_lerobot.real import (
    CAMERA_ROTATIONS,
    OUT_OF_RANGE_DEG,
    LeRobotReal,
    joint_ranges,
    parse_camera_url,
)
from quackd_lerobot.verbs import JOINTS, MANIPULATE_S
from tests.test_lerobot_adapter import FakeArm, FakeCamera, SteppedClock, _segment_arm
from tests.test_policy_loop import STEP, AnsweredClock, LockstepClock, _segment_end

REPO = Path(__file__).resolve().parents[1]
HOSTD = REPO / "bridge" / "jetson" / "quackd_jetson_hostd.py"
TOKEN = "5f0c2a8e9d7b41c3a6e8f0d2b4c6a8e0f1d3b5c7a9e1f3d5b7c9a1e3f5d7b9c1"
"""Shaped like `secrets.token_hex(32)`, which is what the server writes."""
FRAME = wire.CameraInfo(name="front", height=24, width=32)
"""A small camera, so a raw frame is a few kilobytes and a test does not measure base64."""


# ── a server, a client, and an arm behind the client ────────────────────────────────────


@dataclass
class Serving:
    app: S.PolicyServer
    http: Any

    @property
    def port(self) -> int:
        return int(self.http.server_address[1])

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def client(self, *, token: str = TOKEN, **kw: Any) -> RemoteRunner:
        return RemoteRunner(self.url, token=token, motors=kw.pop("motors", JOINTS), **kw)

    def raw(
        self,
        method: str,
        path: str,
        body: bytes | None = None,
        *,
        token: str | None = TOKEN,
        headers: Mapping[str, str] | None = None,
    ) -> tuple[int, dict[str, Any], bool]:
        """One request by hand, as (status, the reply's JSON, whether the server closed)."""
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            sent = dict(headers or {})
            if token is not None:
                sent[wire.TOKEN_HEADER] = token
            if body is not None:
                sent.setdefault("Content-Type", "application/json")
            conn.request(method, path, body=body, headers=sent)
            reply = conn.getresponse()
            payload = json.loads(reply.read() or b"{}")
            return reply.status, payload, reply.will_close
        finally:
            conn.close()


def _serving(options: S.ServeOptions, runner: Any = None, token: str = TOKEN) -> Serving:
    built, info = S.served_policy(options)
    app_ = S.PolicyServer(runner if runner is not None else built, info, token)
    return Serving(app_, S.serve(app_, "127.0.0.1", 0))


@pytest.fixture
def served() -> Iterator[Serving]:
    """`quackd policy serve --policy scripted:sweep`, on loopback, with a token."""
    serving = _serving(S.ServeOptions(policy="scripted:sweep"))
    try:
        yield serving
    finally:
        serving.http.shutdown()
        serving.http.server_close()
        serving.app.close()


def _reading(value: float = 0.0, **frames: Any) -> dict[str, Any]:
    return {**{f"{m}.pos": value for m in JOINTS}, **frames}


async def _arm_on(
    runner: RemoteRunner, arm: FakeArm | None = None, clock: Any = None
) -> tuple[Any, Any, Any]:
    """The real backend over the fake arm, its policy the server behind `runner`, on a clock
    that moves only when slept, so a segment of many seconds costs none of the wall's. Unless a
    test gives its own, the clock waits for an answer still out before it moves
    (`AnsweredClock`), so every chunk lands on the tick after it was asked, however long the
    server takes on the machine running the test."""
    arm = arm if arm is not None else _segment_arm()
    clock = clock if clock is not None else AnsweredClock()
    transport = LeRobotReal("COM5", robot=arm, policy=runner, clock=clock, max_step_deg=STEP)
    if isinstance(clock, AnsweredClock):
        clock.loop = transport._policy_loop
    adapter = LeRobotAdapter(transport)
    manifest = await adapter.connect()
    ex = Executor(registry_from_manifest(manifest, adapter), adapter, confirm=allow_all)
    return transport, adapter, ex


def _hostd() -> ModuleType:
    spec = importlib.util.spec_from_file_location("quackd_jetson_hostd_policy", HOSTD)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# ── what the two sides agree on ─────────────────────────────────────────────────────────


def test_the_port_sits_next_to_the_daemons_and_every_page_that_lists_ports_has_it() -> None:
    """9871 and 9872 are the Open Duck Mini's, 9873 the ToddlerBot daemon's and 9874 the Jetson
    host daemon's, and SECURITY.md tells people which ports quackd opens."""
    assert wire.DEFAULT_PORT == host.DEFAULT_PORT + 1
    assert str(wire.DEFAULT_PORT) in (REPO / "SECURITY.md").read_text(encoding="utf-8")
    result = CliRunner().invoke(app, ["policy", "serve", "--help"])
    assert result.exit_code == 0 and str(wire.DEFAULT_PORT) in result.output


def test_the_servers_bounds_are_the_jetson_daemons() -> None:
    """Copied as named constants, because a daemon under `bridge/` never imports quackd, so a
    change to one has to be a change to the other, and this is where it shows."""
    hostd = _hostd()
    for name in (
        "REQUEST_TIMEOUT_S",
        "REQUEST_DEADLINE_S",
        "MAX_HEAD_BYTES",
        "MAX_CONNECTIONS",
        "LINGER_S",
        "DRAIN_CHUNK_BYTES",
    ):
        assert getattr(S, name) == getattr(hostd, name), name
    assert wire.TOKEN_HEADER == hostd.TOKEN_HEADER
    assert S._Server.allow_reuse_address is (sys.platform != "win32")


def test_a_camera_declares_the_rotations_a_camera_url_takes() -> None:
    assert wire.ROTATIONS == CAMERA_ROTATIONS
    for rotation in CAMERA_ROTATIONS:
        assert wire.CameraInfo(name="front", height=4, width=4, rotation=rotation)
    with pytest.raises(wire.ProtocolError):
        wire.validate(wire.CameraInfo, {"name": "front", "height": 4, "width": 4, "rotation": 45})


def test_the_handshake_says_what_is_served(served: Serving) -> None:
    info = served.client().policy()
    assert (info.protocol, info.protocol_version) == (wire.PROTOCOL, wire.PROTOCOL_VERSION)
    assert info.policy == "scripted:sweep" and info.rate_hz == SCRIPTED_HZ
    assert "scripted:sweep" in info.rate_source
    assert 1 <= info.n_action_steps <= info.chunk_size <= wire.MAX_CHUNK
    assert info.per_tick is False and info.gpu is False and info.latency_s == 0
    assert info.jpeg_quality is None, "frames travel raw on loopback"
    assert info.state_quantiles is None and info.features.state is None


# ── a segment, end to end ───────────────────────────────────────────────────────────────


async def test_manipulate_runs_its_segment_through_the_server(served: Serving) -> None:
    """The verb, the executor, the policy loop, the client, HTTP and the server's scripted
    policy, and back: the segment runs its time, ok, and the arm got the sweep's goals."""
    arm = _segment_arm()
    start = arm.positions[SWEEP_JOINT]
    runner = served.client()
    _, adapter, ex = await _arm_on(runner, arm)
    try:
        ran = await ex.run_verb("manipulate", {"instruction": "wave"})
        assert ran.ok and ran.data["ended"] == "time", ran.summary
        assert ran.data["seconds"] == pytest.approx(MANIPULATE_S)
        assert ran.data["chunks"] >= 2 and ran.data["hz"] == pytest.approx(SCRIPTED_HZ, abs=0.1)
        goals = [a[f"{SWEEP_JOINT}.pos"] for a in arm.actions if f"{SWEEP_JOINT}.pos" in a]
        assert goals and max(abs(g - start) for g in goals) <= SWEEP_DEG + 1e-6
        assert max(goals) > start and min(goals) < start, "it swung both ways"
        assert served.app.runner.instruction == "wave"  # type: ignore[attr-defined]
        assert served.app.steps == runner.seq and runner.dropped == 0
    finally:
        await adapter.close()


async def test_a_manipulate_of_hold_ends_on_a_stall_and_is_ok() -> None:
    """What the arm's page says `scripted:hold` is for: a policy that answers and leaves the arm
    where it is, so the segment ends as an arm that stopped moving, which is a segment that ran."""
    serving = _serving(S.ServeOptions(policy="scripted:hold"))
    arm = _segment_arm()
    here = dict(arm.positions)
    _, adapter, ex = await _arm_on(serving.client(), arm)
    try:
        ran = await ex.run_verb("manipulate", {"instruction": "stay"})
        assert ran.ok and ran.data["ended"] == "stall", ran.summary
        assert arm.positions == pytest.approx(here)
    finally:
        await adapter.close()
        serving.http.shutdown()
        serving.http.server_close()


async def test_a_segment_ends_on_its_chunks(served: Serving) -> None:
    runner = served.client()
    transport, adapter, _ = await _arm_on(runner)
    try:
        ended = await _segment_end(transport, max_s=1e6, max_chunks=3)
        assert ended.how == "chunks" and ended.stats.chunks == 3, ended
        assert served.app.steps == 3
    finally:
        await adapter.close()


async def test_a_server_that_stops_answering_starves_the_segment() -> None:
    """The server takes the third step and never answers it. The loop's patience runs out long
    before the client's timeout, so the segment ends starved, with the arm held, and says so.
    The sweep keeps the arm moving, so nothing before the hang ends it as a stall. The first
    two steps land by the tick after they are asked, and the third is asked with half of the
    second chunk still queued, which the arm plays out before it starves."""
    gate = threading.Event()
    built, _ = S.served_policy(S.ServeOptions(policy="scripted:sweep"))
    script = built.script

    def stalls(observation: Observation, sent: Mapping[str, float]) -> Any:
        if built.requests > 2:
            gate.wait(30)
        return script(observation, sent)

    built.script = stalls
    serving = _serving(S.ServeOptions(policy="scripted:sweep"), runner=built)
    runner = serving.client()
    assert runner.step_timeout_s == STEP_TIMEOUT_S > FIRST_CHUNK_S > STARVE_S
    assert built.chunk is not None
    every = built.chunk - refill_at(0, built.chunk)  # the ticks from one ask to the next
    # the third step is asked at the second ask's tick after the first, and left unanswered
    transport, adapter, _ = await _arm_on(runner, clock=AnsweredClock(leave=2 * every))
    loop = transport._policy_loop
    assert loop is not None
    loop.starve_s = 0.3  # the arm's seconds, which this clock spends none of the wall's on
    try:
        ended = await _segment_end(transport, max_s=1e6)
        assert ended.how == "starved", ended
        assert "gave no action for 0.3 s, waiting for a chunk" in ended.reason
        assert ended.stats.chunks == 2, ended.stats
        _until(lambda: built.requests == 3)  # the one it never answers
    finally:
        gate.set()
        await adapter.close()
        serving.http.shutdown()
        serving.http.server_close()


async def test_manipulate_is_not_ok_when_the_server_stops_answering() -> None:
    gate = threading.Event()
    built, _ = S.served_policy(S.ServeOptions(policy="scripted:sweep"))
    script = built.script

    def stalls(observation: Observation, sent: Mapping[str, float]) -> Any:
        if built.requests > 1:
            gate.wait(30)
        return script(observation, sent)

    built.script = stalls
    serving = _serving(S.ServeOptions(policy="scripted:sweep"), runner=built)
    # its second step is never answered, and a clock that waited for answers would wait on it
    transport, adapter, ex = await _arm_on(serving.client(), clock=SteppedClock())
    assert transport._policy_loop is not None
    transport._policy_loop.starve_s = 0.3
    try:
        ran = await ex.run_verb("manipulate", {"instruction": "wave"})
        assert not ran.ok and ran.data["ended"] == "starved", ran.summary
    finally:
        gate.set()
        await adapter.close()
        serving.http.shutdown()
        serving.http.server_close()


# ── sessions and sequences ──────────────────────────────────────────────────────────────


def test_a_second_client_waits_for_the_live_session_and_a_stale_one_is_refused(
    served: Serving,
) -> None:
    """A check run against a server an arm is driving through must not end the arm's segment:
    another client's reset is refused while the live session is in use, the session's own
    client replaces it freely, and one quiet for the lease is anyone's. A step for the session
    a reset replaced is refused."""
    first, second = served.client(), served.client()
    first.reset("wave")
    first.next_chunk(Observation(0, _reading()), {})
    with pytest.raises(PolicyServerError) as busy:
        second.reset("bench")
    assert busy.value.status == 409 and "another client's session is live" in str(busy.value)
    assert str(busy.value).endswith("--port"), "the whole sentence, not one cut short"
    first.reset("wave again")  # its own client replaces it, however recently it was used
    assert first.next_chunk(Observation(0, _reading()), {}).actions
    served.app.lease_s = 0.0  # as if it had been quiet for the lease
    second.reset("bench")
    with pytest.raises(PolicyServerError) as stale:
        first.next_chunk(Observation(1, _reading()), {})
    assert stale.value.status == 409 and "session is over" in str(stale.value)
    assert second.next_chunk(Observation(0, _reading()), {}).actions


def test_a_client_that_closes_ends_its_session_so_another_starts_at_once(
    served: Serving,
) -> None:
    first, second = served.client(), served.client()
    first.reset("wave")
    first.close()
    assert first.session is None
    second.reset("bench")  # no lease to wait out
    assert second.next_chunk(Observation(0, _reading()), {}).actions
    stale = wire.dumps(wire.EndRequest(session="0" * 32))
    assert served.raw("POST", wire.END_PATH, stale)[:2] == (200, {"ended": False})
    first.close()  # a client with no session sends nothing, and ending one twice is no error


def _until(done: Callable[[], bool], within: float = 5.0) -> None:
    end = time.monotonic() + within
    while not done():
        assert time.monotonic() < end, "it did not happen in time"
        time.sleep(0.02)


def test_a_reset_whose_reply_was_lost_leaves_its_client_the_session_it_made() -> None:
    """A reset the server finished after its client stopped waiting for the reply made a
    session the client never heard of, and the client still holds the one before. Its next
    reset and its close are still its own, never another client's, which is refused while the
    session is in use as before."""
    built, _ = S.served_policy(S.ServeOptions(policy="scripted:hold"))
    real = built.reset
    slow = threading.Event()

    def reset(instruction: str) -> None:
        if slow.is_set():
            slow.clear()
            time.sleep(1.0)
        real(instruction)

    built.reset = reset
    serving = _serving(S.ServeOptions(policy="scripted:hold"), runner=built)

    def moved_on(held: str | None) -> bool:
        live = serving.app._session
        return live is not None and live.id != held

    try:
        arm, other = serving.client(), serving.client()
        arm.reset("first")
        held = arm.session
        arm.call_timeout_s = 0.5
        slow.set()
        with pytest.raises(PolicyServerError, match="did not answer /v1/reset"):
            arm.reset("second")
        _until(lambda: moved_on(held))
        assert arm.session == held, "the reply never came, so the client holds what it held"
        with pytest.raises(PolicyServerError) as busy:
            other.reset("bench")
        assert busy.value.status == 409
        arm.reset("third")  # its own, however recently the session it never heard of was made
        assert arm.next_chunk(Observation(0, _reading()), {}).actions
        held = arm.session
        slow.set()
        with pytest.raises(PolicyServerError, match="did not answer /v1/reset"):
            arm.reset("fourth")
        _until(lambda: moved_on(held))
        arm.close()
        assert serving.app._session is None, "its close ends the session its lost reset made"
        other.reset("bench")
    finally:
        serving.http.shutdown()
        serving.http.server_close()


def test_a_step_its_client_gave_up_on_holds_up_no_reset_and_no_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A step that outlives its client's patience, as a checkpoint on a CPU can. The client's
    reset after it is answered within the client's own timeout, with the policy left alone
    under the step. The slow step's chunk is answered to nobody as a chunk, the next step
    starts from a policy reset after it, and stopping the server returns while a step is still
    inferring, rather than when torch is done. A step waiting its turn behind that one, and a
    reset after the stop, are refused, and the policy closed under them is never run again."""
    hold, inferring, gate = threading.Event(), threading.Event(), threading.Event()
    built, info = S.served_policy(S.ServeOptions(policy="scripted:sweep"))
    script, reset = built.script, built.reset
    calls: list[str] = []
    after_close: list[str] = []

    def steps(observation: Observation, sent: Mapping[str, float]) -> Any:
        calls.append(f"step {observation.tick}")
        if built.closed:
            after_close.append(calls[-1])
        if hold.is_set():
            inferring.set()
            gate.wait(30)
        return script(observation, sent)

    def resets(instruction: str) -> None:
        calls.append(f"reset {instruction}")
        if built.closed:
            after_close.append(calls[-1])
        reset(instruction)

    built.script = steps
    built.reset = resets
    app_ = S.PolicyServer(built, info, TOKEN)
    serving = Serving(app_, S.serve(app_, "127.0.0.1", 0))
    answered: list[tuple[int, dict[str, Any], bool]] = []

    def slow_step(session: str, seq: int, tick: int) -> threading.Thread:
        body = wire.dumps(
            wire.StepRequest(session=session, seq=seq, tick=tick, state=[0.0] * len(JOINTS))
        )
        worker = threading.Thread(
            target=lambda: answered.append(serving.raw("POST", wire.STEP_PATH, body)),
            daemon=True,
        )
        hold.set()
        worker.start()
        assert inferring.wait(5), "the step never reached the policy"
        return worker

    try:
        client = serving.client()
        client.reset("first")
        assert client.session is not None
        first = slow_step(client.session, 1, 0)
        started = time.monotonic()
        client.reset("second")  # the client has given up on the step, and resets
        assert time.monotonic() - started < client.call_timeout_s
        assert calls == ["reset first", "step 0"], "the policy is never reset under a step"
        hold.clear()
        gate.set()
        first.join(5)
        ((status, said, _),) = answered
        assert status == 409 and "its chunk was dropped" in said["reason"], said
        assert client.next_chunk(Observation(1, _reading()), {}).actions
        assert calls[-2:] == ["reset second", "step 1"], "the next step starts from a reset"
        # and stopping the server waits on no inference
        gate.clear()
        inferring.clear()
        answered.clear()
        slow = slow_step(client.session, client.seq + 1, 2)
        client.reset("third")  # its first step waits its turn behind the slow one
        waiting = threading.Event()
        turn = app_._runner

        @contextlib.contextmanager
        def queued_turn() -> Iterator[None]:
            waiting.set()
            with turn():
                yield

        monkeypatch.setattr(app_, "_runner", queued_turn)
        assert client.session is not None
        body = wire.dumps(
            wire.StepRequest(session=client.session, seq=1, tick=3, state=[0.0] * len(JOINTS))
        )
        queued = threading.Thread(
            target=lambda: answered.append(serving.raw("POST", wire.STEP_PATH, body)),
            daemon=True,
        )
        queued.start()
        assert waiting.wait(5), "the next step never reached the policy's lock"
        served = S.Served(app_, serving.http, "127.0.0.1", None, False)
        started = time.monotonic()
        served.close()
        assert time.monotonic() - started < S.CLOSE_WAIT_S + 2.0
        assert built.closed and not gate.is_set(), "closed with the step still inferring"
        with pytest.raises(S.Refused) as stopped:  # as a connection kept alive past it asks
            app_.reset(wire.ResetRequest(instruction="fourth", motors=list(JOINTS)))
        assert stopped.value.status == 503 and "stopping" in str(stopped.value)
        gate.set()
        slow.join(5)
        queued.join(S.RUNNER_WAIT_S)
        assert sorted(status for status, _, _ in answered) == [503, 503], answered
        assert all("stopping" in said["reason"] for _, said, _ in answered), answered
        assert not after_close, f"the policy ran after it was closed: {after_close}"
    finally:
        gate.set()
        with contextlib.suppress(Exception):
            serving.http.shutdown()
            serving.http.server_close()


def test_a_step_nobody_waits_for_any_more_keeps_no_new_client_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A step still inferring keeps its session in use while its client may be waiting for it,
    and no longer: the client gives up on a step after `STEP_TIMEOUT_S`, and a server whose
    policy takes minutes a step would otherwise turn every new client away for as long. The
    refusal says how long the step has been inferring and when to try again, and the new
    client's reset is answered once the step has outlived its client's patience, with the step
    still inferring and its chunk dropped as it ends."""
    assert S.ABANDONED_S >= STEP_TIMEOUT_S, "a session is anyone's only once its client gave up"
    patience = 1.0  # synthetic: a client that gives up on a step after a second
    monkeypatch.setattr(S, "ABANDONED_S", patience)
    hold, inferring, gate = threading.Event(), threading.Event(), threading.Event()
    built, info = S.served_policy(S.ServeOptions(policy="scripted:sweep"))
    script = built.script

    def steps(observation: Observation, sent: Mapping[str, float]) -> Any:
        if hold.is_set():
            hold.clear()
            inferring.set()
            gate.wait(30)
        return script(observation, sent)

    built.script = steps
    app_ = S.PolicyServer(built, info, TOKEN)
    app_.lease_s = 0.0  # the lease alone would let anyone in at once
    serving = Serving(app_, S.serve(app_, "127.0.0.1", 0))
    answered: list[tuple[int, dict[str, Any], bool]] = []

    def let_in(client: RemoteRunner) -> bool:
        try:
            client.reset("run 2")
        except PolicyServerError as e:
            assert e.status == 409, e
            return False
        return True

    try:
        first, second = serving.client(), serving.client()
        first.reset("run 1")
        assert first.session is not None
        body = wire.dumps(
            wire.StepRequest(session=first.session, seq=1, tick=0, state=[0.0] * len(JOINTS))
        )
        hold.set()
        orphan = threading.Thread(
            target=lambda: answered.append(serving.raw("POST", wire.STEP_PATH, body)),
            daemon=True,
        )
        orphan.start()
        assert inferring.wait(5), "the step never reached the policy"
        with pytest.raises(PolicyServerError) as busy:
            second.reset("run 2")
        said = str(busy.value)
        assert busy.value.status == 409 and "a step of its inferring for" in said, said
        assert f"Try again in {math.ceil(patience)} s" in said and said.endswith("--port"), said
        _until(lambda: let_in(second), within=patience + 5.0)
        assert not gate.is_set(), "let in while the step it outlived was still inferring"
        gate.set()
        orphan.join(5)
        ((status, reply, _),) = answered
        assert status == 409 and "its chunk was dropped" in reply["reason"], reply
        assert second.next_chunk(Observation(0, _reading()), {}).actions
    finally:
        gate.set()
        serving.http.shutdown()
        serving.http.server_close()


def test_the_lease_outlasts_every_wait_between_a_segments_requests(served: Serving) -> None:
    """A segment asks again before its chunk runs out, or ends on its patience, which is at
    most the first chunk's grace, so a session quiet for longer has no segment behind it."""
    info = served.client().policy()
    assert served.app.lease_s >= info.n_action_steps / info.rate_hz + FIRST_CHUNK_S
    assert FIRST_CHUNK_S >= STARVE_S


def test_a_step_sent_twice_is_inferred_once_and_an_older_one_is_refused(served: Serving) -> None:
    """The client sends a step again on a new socket when the kept-alive one turns out closed,
    and the first may have been answered with the reply lost: the second gets that reply."""
    runner = served.client()
    runner.reset("wave")
    assert runner.session is not None
    step = wire.dumps(
        wire.StepRequest(session=runner.session, seq=1, tick=0, state=[0.0] * len(JOINTS))
    )
    asked = served.app.runner.requests  # type: ignore[attr-defined]
    status, once, _ = served.raw("POST", wire.STEP_PATH, step)
    status2, twice, _ = served.raw("POST", wire.STEP_PATH, step)
    assert status == status2 == 200 and once == twice
    assert served.app.runner.requests == asked + 1  # type: ignore[attr-defined]
    newer = wire.dumps(
        wire.StepRequest(session=runner.session, seq=2, tick=1, state=[0.0] * len(JOINTS))
    )
    assert served.raw("POST", wire.STEP_PATH, newer)[0] == 200
    status, said, _ = served.raw("POST", wire.STEP_PATH, step)
    assert status == 409 and "older than step 2" in said["reason"]


@pytest.mark.parametrize("wrong", ["seq", "session"])
def test_a_reply_for_another_sequence_or_session_is_dropped(
    served: Serving, monkeypatch: pytest.MonkeyPatch, wrong: str
) -> None:
    real = served.app.step

    def answers_another(request: wire.StepRequest) -> wire.StepReply:
        reply = real(request)
        if wrong == "seq":
            return reply.model_copy(update={"seq": request.seq + 1})
        return reply.model_copy(update={"session": "0" * 32})

    monkeypatch.setattr(served.app, "step", answers_another)
    runner = served.client()
    runner.reset("wave")
    chunk = runner.next_chunk(Observation(0, _reading()), {})
    assert chunk == Chunk(0) and runner.dropped == 1


def test_a_policy_that_is_done_answers_null_and_is_not_asked_again() -> None:
    built, _ = S.served_policy(S.ServeOptions(policy="scripted:hold"))
    built.script = lambda observation, sent: None
    serving = _serving(S.ServeOptions(policy="scripted:hold"), runner=built)
    try:
        runner = serving.client()
        runner.reset("wave")
        assert runner.next_chunk(Observation(0, _reading()), {}).done
        assert runner.next_chunk(Observation(1, _reading()), {}).done
        assert built.requests == 1
    finally:
        serving.http.shutdown()
        serving.http.server_close()


# ── the token ───────────────────────────────────────────────────────────────────────────


def test_a_wrong_token_is_refused_in_constant_time_and_never_repeated(
    served: Serving, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Driven rather than grepped, as the ToddlerBot daemon's test is: the server module's own
    `hmac` is replaced, never the standard library's."""
    calls: list[tuple[bytes, bytes]] = []

    class _Watched:
        @staticmethod
        def compare_digest(a: bytes, b: bytes) -> bool:
            calls.append((a, b))
            return a == b

    monkeypatch.setattr(S, "hmac", _Watched)
    wrong = "not-the-token-" + TOKEN[14:]
    with pytest.raises(PolicyServerError) as refused:
        served.client(token=wrong).policy()
    assert refused.value.status == 401 and wrong not in str(refused.value)
    assert "--policy-token" in str(refused.value)
    assert (wrong.encode(), TOKEN.encode()) in calls
    status, said, closed = served.raw("GET", wire.POLICY_PATH, token=None)
    assert status == 401 and said["reason"] == "bad or missing token" and closed
    assert served.client().policy().policy == "scripted:sweep"


def test_a_request_with_no_token_is_answered_on_its_head_and_its_body_is_never_read(
    served: Serving,
) -> None:
    status, _, closed = served.raw(
        "POST", wire.STEP_PATH, b"x" * 1000, token="wrong", headers={"Content-Length": "1000"}
    )
    assert status == 401 and closed and served.app.steps == 0


def test_the_token_is_scrubbed_from_what_the_server_says(
    served: Serving, monkeypatch: pytest.MonkeyPatch
) -> None:
    def echoes(request: wire.ResetRequest) -> wire.ResetReply:
        raise S.Refused(400, f"I was sent {TOKEN} and did not like it")

    monkeypatch.setattr(served.app, "reset", echoes)
    with pytest.raises(PolicyServerError) as refused:
        served.client().reset("wave")
    assert TOKEN not in str(refused.value) and "<token>" in str(refused.value)


def test_the_token_file_is_written_once_readable_by_its_owner_and_read_by_the_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "home" / ".quackd" / "policy.token"
    monkeypatch.setattr(wire, "DEFAULT_TOKEN_FILE", str(path))
    monkeypatch.delenv(wire.TOKEN_ENV, raising=False)
    token, where, written = S.server_token(None)
    assert where == path and written and len(token) == 64
    assert set(token) <= set("0123456789abcdef")
    if sys.platform != "win32":
        assert path.stat().st_mode & 0o777 == 0o600
    again, _, written_again = S.server_token(None)
    assert again == token and not written_again, "a second server reads the same token"
    assert client_token(None) == token
    monkeypatch.setenv(wire.TOKEN_ENV, "from-the-environment")
    assert client_token(None) == "from-the-environment"
    assert client_token("from-the-flag-given") == "from-the-flag-given"


SPLIT = "SECRET-PART-ONE\nSECRET-PART-TWO"
"""A token read from a file of two lines: `http.client` refuses it in a header with an error
that quotes it whole."""


@pytest.mark.parametrize(
    "bad", [SPLIT, "SECRET-PART-ONE SECRET-PART-TWO", "SECRET-PART-ONE\x01", "SECRET-1"]
)
def test_a_token_no_header_can_carry_or_too_short_to_guard_an_arm_is_never_sent_or_quoted(
    served: Serving, monkeypatch: pytest.MonkeyPatch, bad: str
) -> None:
    with pytest.raises(ValueError) as refused:
        served.client(token=bad)
    assert "SECRET" not in str(refused.value) and "openssl rand -hex 32" in str(refused.value)
    for given in (bad, None):
        monkeypatch.setenv(wire.TOKEN_ENV, bad)
        with pytest.raises(ValueError) as refused:
            client_token(given)
        assert "SECRET" not in str(refused.value)
        assert ("--policy-token" if given else wire.TOKEN_ENV) in str(refused.value)
    monkeypatch.delenv(wire.TOKEN_ENV)
    result = CliRunner().invoke(
        app, ["policy", "check", "--policy-url", served.url, "--policy-token", bad]
    )
    assert result.exit_code == 1 and "SECRET" not in result.output, result.output
    assert served.app.steps == served.app.resets == 0


@pytest.mark.parametrize("bad", [SPLIT, "SECRET-1"])
def test_the_server_refuses_to_start_on_a_token_no_client_could_send_or_one_too_short(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bad: str
) -> None:
    named = tmp_path / "policy.token"
    named.write_text(bad + "\n", encoding="utf-8")
    with pytest.raises(S.ServeRefused) as refused:
        S.server_token(str(named))
    assert "SECRET" not in str(refused.value) and str(named) in str(refused.value)
    monkeypatch.setattr(wire, "DEFAULT_TOKEN_FILE", str(named))
    with pytest.raises(S.ServeRefused):
        S.server_token(None)
    with pytest.raises(ValueError) as refused:
        _serving(S.ServeOptions(policy="scripted:hold"), token=bad)
    assert "SECRET" not in str(refused.value)
    result = CliRunner().invoke(
        app, ["policy", "serve", "--policy", "scripted:hold", "--token-file", str(named)]
    )
    assert result.exit_code == 1 and "SECRET" not in result.output, result.output


def test_a_named_token_file_that_is_missing_or_empty_refuses_to_start(tmp_path: Path) -> None:
    with pytest.raises(S.ServeRefused, match="cannot read the token file"):
        S.server_token(str(tmp_path / "nowhere.token"))
    empty = tmp_path / "empty.token"
    empty.write_text("\n", encoding="utf-8")
    with pytest.raises(S.ServeRefused, match="is empty"):
        S.server_token(str(empty))


def test_a_client_with_no_token_anywhere_says_where_to_put_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(wire, "DEFAULT_TOKEN_FILE", str(tmp_path / "none" / "policy.token"))
    monkeypatch.delenv(wire.TOKEN_ENV, raising=False)
    with pytest.raises(ValueError, match="--policy-token") as refused:
        client_token(None)
    assert wire.TOKEN_ENV in str(refused.value)


def test_serve_writes_a_token_and_says_where(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / ".quackd" / "policy.token"
    monkeypatch.setattr(wire, "DEFAULT_TOKEN_FILE", str(path))
    monkeypatch.setattr(S.Served, "wait", lambda self: None)
    result = CliRunner().invoke(
        app, ["policy", "serve", "--policy", "scripted:hold", "--port", "0"]
    )
    assert result.exit_code == 0, result.output
    flat = " ".join(result.output.split())
    assert "(written now)" in flat and path.read_text(encoding="utf-8").strip()
    assert "scripted:hold at http://127.0.0.1:" in flat


# ── numbers, sizes and frames ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("bad", ["NaN", "Infinity", "-Infinity", "1e999", "true", '"1.0"'])
def test_a_number_that_is_not_finite_is_refused_by_the_server(served: Serving, bad: str) -> None:
    runner = served.client()
    runner.reset("wave")
    state = ", ".join(["0.0"] * (len(JOINTS) - 1) + [bad])
    body = (
        f'{{"session": "{runner.session}", "seq": 1, "tick": 0, "state": [{state}], '
        f'"frames": [], "sent": {{}}}}'
    ).encode()
    status, said, _ = served.raw("POST", wire.STEP_PATH, body)
    assert status == 400, said
    assert served.app.steps == 0


def test_a_number_that_is_not_finite_is_refused_by_the_client(
    served: Serving, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On the way out, before anything is sent, and on the way in, from a server that sent one
    anyway."""
    runner = served.client()
    runner.reset("wave")
    for bad in (math.nan, math.inf, True):
        with pytest.raises(ValueError, match="not a finite number"):
            runner.next_chunk(Observation(0, {**_reading(), "elbow_flex.pos": bad}), {})
        with pytest.raises(ValueError, match="could not be asked"):
            runner.next_chunk(Observation(0, _reading()), {"elbow_flex": bad})
    assert served.app.steps == 0
    for literal in (b"NaN", b"1e999"):
        raw = b'{"session": "%s", "seq": 1, "chunk": [{"wrist_flex": %s}], "inference_s": 0}' % (
            runner.session.encode(),  # type: ignore[union-attr]
            literal,
        )
        monkeypatch.setattr(runner, "_exchange", lambda *a, _raw=raw: _raw)
        runner.seq = 0
        with pytest.raises(PolicyServerError, match="the protocol refuses"):
            runner.next_chunk(Observation(0, _reading()), {})


def test_the_schema_refuses_what_neither_side_ever_sends() -> None:
    """A sequence counts from 1 and a tick from 0, a session id is the server's hex, and a
    message with a number that is not finite cannot even be written."""
    step = {"session": "0" * 32, "seq": 1, "tick": 0, "state": [0.0]}
    assert wire.validate(wire.StepRequest, step).seq == 1
    for field, value in (("seq", 0), ("tick", -1), ("state", []), ("session", "x"), ("seq", 1.0)):
        with pytest.raises(wire.ProtocolError):
            wire.validate(wire.StepRequest, {**step, field: value})
    with pytest.raises(ValueError):
        wire.dumps({"state": [math.nan]})


@pytest.mark.parametrize("field", ["protocol", "server_version", "policy", "rate_source"])
@pytest.mark.parametrize(
    "said",
    [
        "0.15.0\x1b[1A\x1b[2K\x1b]52;c;ZWNobyBwd25lZA==\x1b\\",  # cursor up, erase, clipboard
        "\x1b]8;;https://example.com\x1b\\policy\x1b]8;;\x1b\\",  # a link
        "scripted:‮loh",  # a bidi override
        "scripted:hold\n",
        "",
    ],
    ids=["cursor-erase-clipboard", "link", "bidi", "newline", "empty"],
)
def test_a_server_that_describes_itself_in_anything_but_printable_ascii_is_refused(
    monkeypatch: pytest.MonkeyPatch, field: str, said: str
) -> None:
    """`quackd policy check` prints what a server says it serves, and a refusal of its rate
    quotes where the rate came from, so an escape in either would reach the user's terminal:
    both ends refuse the message instead."""
    _, info = S.served_policy(S.ServeOptions(policy="scripted:hold"))
    raw = json.dumps({**info.model_dump(), field: said}).encode()
    runner = RemoteRunner("http://127.0.0.1:9", token=TOKEN, motors=JOINTS)
    monkeypatch.setattr(runner, "_exchange", lambda *a: raw)
    if field == "protocol":
        match = "not as quackd-policy"  # the client knows its own protocol by name first
    else:
        match = f"reply the protocol refuses: {field}"
    with pytest.raises(PolicyServerError, match=match) as refused:
        runner.policy()
    assert str(refused.value).isascii() and str(refused.value).isprintable()
    with pytest.raises(wire.ProtocolError):
        wire.validate(wire.PolicyInfo, {**info.model_dump(), field: said})


def test_a_policy_that_answers_a_non_finite_goal_is_refused_by_its_own_server() -> None:
    """The server checks what its policy answered before it sends it, so it never sends a goal
    the client would have to refuse."""
    built, _ = S.served_policy(S.ServeOptions(policy="scripted:hold"))
    built.script = lambda observation, sent: [{"wrist_flex": math.nan}]
    serving = _serving(S.ServeOptions(policy="scripted:hold"), runner=built)
    try:
        runner = serving.client()
        runner.reset("wave")
        with pytest.raises(PolicyServerError) as refused:
            runner.next_chunk(Observation(0, _reading()), {})
        assert refused.value.status == 500 and "not a finite number" in str(refused.value)
    finally:
        serving.http.shutdown()
        serving.http.server_close()


def test_a_body_too_large_is_refused_before_it_is_read(served: Serving) -> None:
    conn = http.client.HTTPConnection("127.0.0.1", served.port, timeout=10)
    try:
        conn.putrequest("POST", wire.STEP_PATH)
        conn.putheader(wire.TOKEN_HEADER, TOKEN)
        conn.putheader("Content-Length", str(wire.MAX_BODY_BYTES + 1))
        conn.endheaders()
        reply = conn.getresponse()
        said = json.loads(reply.read())
    finally:
        conn.close()
    assert reply.status == 413 and str(wire.MAX_BODY_BYTES) in said["reason"]
    assert reply.will_close


def test_a_reply_too_large_is_cut_off_by_the_client(
    served: Serving, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(wire, "MAX_REPLY_BYTES", 64)
    with pytest.raises(PolicyServerError, match="more than"):
        served.client().policy()


@pytest.mark.parametrize("where", ["head", "body"])
def test_a_server_that_trickles_its_reply_is_cut_off_at_the_calls_deadline(where: str) -> None:
    """A socket timeout bounds one read, and a server sending a byte every tenth of a second
    never trips one: the call's deadline is what cuts it off, in the head or in the body, and
    the loop's worker is free again that soon rather than when the server stops."""
    listener = socket.create_server(("127.0.0.1", 0))
    stop = threading.Event()

    def trickles() -> None:
        conn, _ = listener.accept()
        with conn:
            conn.recv(1 << 16)
            if where == "head":
                conn.sendall(b"HTTP/1.1 200 OK\r\n")
                drip = b"X-Pad: a\r\n" * 20
            else:
                body = b'{"ok":false,"reason":"x"}' + b" " * 40
                conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: %d\r\n\r\n" % len(body))
                drip = body
            for byte in drip:
                if stop.wait(0.1):
                    return
                try:
                    conn.sendall(bytes([byte]))
                except OSError:
                    return

    thread = threading.Thread(target=trickles, daemon=True)
    thread.start()
    runner = RemoteRunner(
        f"http://127.0.0.1:{listener.getsockname()[1]}", token=TOKEN, motors=JOINTS
    )
    runner.call_timeout_s = 0.5
    started = time.monotonic()
    try:
        with pytest.raises(PolicyServerError, match=r"did not answer /v1/policy within 0\.5 s"):
            runner.policy()
        took = time.monotonic() - started
    finally:
        stop.set()
        thread.join(5)
        listener.close()
    assert took < 0.5 + 1.0, f"cut off after {took:.1f} s against a deadline of 0.5 s"
    assert runner._conn is None, "a connection cut off mid-reply is not kept"


@pytest.mark.parametrize(
    ("declared", "said"),
    [
        (b"9" * 5000, "more than"),  # longer than Python reads as an int
        ("²".encode("latin-1"), "not a byte count"),  # a digit to str.isdigit, not to int
        (b"12abc", "not a byte count"),
        (b"2, 2", "not a byte count"),  # the header twice
    ],
    ids=["5000-digits", "superscript-two", "not-digits", "twice"],
)
def test_a_reply_whose_length_is_not_a_byte_count_is_the_servers_fault(
    declared: bytes, said: str
) -> None:
    """A broken reply is said to be the server's. It is never taken for a request that could
    not be sent, which would send the user to their token."""
    listener = socket.create_server(("127.0.0.1", 0))

    def answers() -> None:
        conn, _ = listener.accept()
        with conn:
            conn.recv(1 << 16)
            conn.sendall(
                b"HTTP/1.1 200 OK\r\nContent-Length: "
                + declared
                + b"\r\nConnection: close\r\n\r\n{}"
            )

    thread = threading.Thread(target=answers, daemon=True)
    thread.start()
    runner = RemoteRunner(
        f"http://127.0.0.1:{listener.getsockname()[1]}", token=TOKEN, motors=JOINTS
    )
    try:
        with pytest.raises(PolicyServerError, match=said) as refused:
            runner.policy()
    finally:
        thread.join(5)
        listener.close()
    assert "was not asked" not in str(refused.value) and len(str(refused.value)) < 300


def test_a_request_http_cannot_carry_is_never_quoted(served: Serving) -> None:
    """The guard behind `clean_token`: a header `http.client` refuses is said without its value,
    which would be the token."""
    runner = served.client()
    runner._token = "SECRET-PART-ONE\r\nSECRET-PART-TWO"
    with pytest.raises(PolicyServerError, match="was not asked /v1/policy") as refused:
        runner.policy()
    assert "SECRET" not in str(refused.value)


def test_frames_travel_raw_on_loopback_and_as_jpeg_when_the_server_asks() -> None:
    seen: list[Any] = []
    for quality in (None, wire.DEFAULT_JPEG_QUALITY):
        built, _ = S.served_policy(S.ServeOptions(policy="scripted:hold"))
        hold = built.script

        def looks(observation: Observation, sent: Mapping[str, float], _hold: Any = hold) -> Any:
            seen.append(observation.reading["front"])
            return _hold(observation, sent)

        built.script = looks
        options = S.ServeOptions(
            policy="scripted:hold", cameras="front=observation.images.front", jpeg_quality=quality
        )
        serving = _serving(options, runner=built)
        try:
            runner = serving.client(cameras=[FRAME])
            runner.reset("look")
            assert runner.info is not None and runner.info.jpeg_quality == quality
            picture = np.full((FRAME.height, FRAME.width, 3), 200, dtype=np.uint8)
            chunk = runner.next_chunk(Observation(0, _reading(front=picture)), {})
            assert chunk.actions
            wrong = np.zeros((FRAME.height + 1, FRAME.width, 3), dtype=np.uint8)
            with pytest.raises(PolicyServerError) as refused:
                runner.next_chunk(Observation(1, _reading(front=wrong)), {})
            assert refused.value.status == 400 and "declared it 32x24" in str(refused.value)
        finally:
            serving.http.shutdown()
            serving.http.server_close()
    raw, jpeg = seen
    assert raw.shape == jpeg.shape == (FRAME.height, FRAME.width, 3)
    assert np.array_equal(raw, np.full_like(raw, 200)), "a raw frame arrives exact"
    assert np.abs(jpeg.astype(int) - 200).max() <= 3, "a JPEG of a flat frame arrives near it"


def test_a_camera_the_server_maps_and_the_arm_lacks_refuses_the_reset() -> None:
    serving = _serving(S.ServeOptions(policy="scripted:hold", cameras="wrist=observation.w"))
    try:
        with pytest.raises(PolicyServerError, match="maps the wrist camera"):
            serving.client(cameras=[FRAME]).reset("look")
    finally:
        serving.http.shutdown()
        serving.http.server_close()


# ── where the client goes, and where the server listens ─────────────────────────────────


def test_localhost_is_refused_with_the_address_to_write_instead() -> None:
    with pytest.raises(ValueError, match=r"write 127\.0\.0\.1"):
        RemoteRunner("http://localhost:9875", token=TOKEN, motors=JOINTS)


@pytest.mark.parametrize(
    "url", ["http://10.0.0.5:9875", "http://gpu.example.com:9875", "http://127.0.0.2:9875"]
)
def test_plain_http_to_anything_but_the_loopback_literals_is_refused(url: str) -> None:
    with pytest.raises(ValueError, match="ssh -L") as refused:
        policy_address(url)
    assert "https://" in str(refused.value)


def test_loopback_literals_and_https_anywhere_are_taken() -> None:
    assert policy_address("http://127.0.0.1:9875") == ("http", "127.0.0.1", 9875, "")
    assert policy_address("http://[::1]:9875") == ("http", "::1", 9875, "")
    assert policy_address("http://127.0.0.1")[2] == wire.DEFAULT_PORT
    assert policy_address("https://gpu.example.com/policy/") == (
        "https",
        "gpu.example.com",
        443,
        "/policy",
    )


def test_the_url_is_held_redacted_from_construction() -> None:
    runner = RemoteRunner("http://127.0.0.1:9875", token=TOKEN, motors=JOINTS)
    assert runner.url == "http://127.0.0.1:9875" and TOKEN not in repr(runner)
    for url, secret in (
        ("https://rok:hunter2@gpu.example.com:9875", "hunter2"),
        ("http://127.0.0.1:9875/?token=abc123", "abc123"),
    ):
        with pytest.raises(ValueError) as refused:
            RemoteRunner(url, token=TOKEN, motors=JOINTS)
        assert secret not in str(refused.value) and HIDDEN in str(refused.value)


def test_a_bind_beyond_loopback_needs_behind_tls() -> None:
    for bind in ("127.0.0.1", "::1"):
        assert S.bind_refusal(bind, behind_tls=False) is None
    refusal = S.bind_refusal("0.0.0.0", behind_tls=False)
    assert refusal is not None and "--behind-tls" in refusal and "ssh -L" in refusal
    assert S.bind_refusal("0.0.0.0", behind_tls=True) is None
    for elsewhere, url in (
        ("127.0.0.2", "http://127.0.0.2:9875"),
        ("0:0:0:0:0:0:0:1", "http://[0:0:0:0:0:0:0:1]:9875"),
    ):
        # loopback, and still not an address the client sends plain http to
        refusal = S.bind_refusal(elsewhere, behind_tls=False)
        assert refusal is not None and "bind 127.0.0.1 or ::1" in refusal, elsewhere
        with pytest.raises(ValueError, match="plain http goes only to"):
            policy_address(url)
    localhost = S.bind_refusal("localhost", behind_tls=True)
    assert localhost is not None and "127.0.0.1" in localhost
    result = CliRunner().invoke(
        app, ["policy", "serve", "--policy", "scripted:hold", "--bind", "0.0.0.0"]
    )
    assert result.exit_code == 1 and "--behind-tls" in " ".join(result.output.split())


def test_serve_names_a_url_the_client_takes_whatever_it_binds(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A server behind a TLS proxy may bind 0.0.0.0, and the check it suggests must still be
    one the client runs: plain http goes to loopback alone. The bind itself is made on loopback
    here, so the test opens nothing to the network."""
    token_file = tmp_path / "policy.token"
    token_file.write_text(TOKEN, encoding="utf-8")
    serve = S.serve
    monkeypatch.setattr(S, "serve", lambda app_, host, port: serve(app_, "127.0.0.1", 0))
    monkeypatch.setattr(S.Served, "wait", lambda self: None)
    argv = ["policy", "serve", "--policy", "scripted:hold", "--port", "0"]
    for bind in ("0.0.0.0", "::"):
        result = CliRunner().invoke(
            app, [*argv, "--bind", bind, "--behind-tls", "--token-file", str(token_file)]
        )
        assert result.exit_code == 0, result.output
        flat = " ".join(result.output.split())
        suggested = re.search(r"--policy-url (\S+) asks it", flat)
        assert suggested is not None, flat
        assert policy_address(suggested.group(1))[1] in ("127.0.0.1", "::1"), flat
        assert "TLS proxy's https://" in flat

    def bound(address: str) -> S.Served:
        return S.Served(
            app=None,  # type: ignore[arg-type]
            http=type("H", (), {"server_address": (address, 9875)})(),
            host=address,
            token_path=None,
            token_written=False,
        )

    for bind in ("192.0.2.7", "127.0.0.2"):
        # binds only --behind-tls allows: the client reaches either through the proxy alone
        assert bound(bind).local_url is None, bind
    for bind in wire.LOOPBACK:
        local = bound(bind).local_url
        assert local is not None and policy_address(local)[1] == bind


def test_a_checkpoint_goes_to_the_pipeline_and_anything_else_is_named_or_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A `REPO@REVISION` is handed to the pipeline with every flag that shapes it, and what the
    pipeline refuses the server refuses in the same words. Without torch that is a sentence
    naming the extra (`tests/test_extras_absent.py`), and with it `tests/test_policy_pipeline.py`
    loads one for real."""
    asked: list[tuple[str, dict[str, Any]]] = []

    def load(spec: str, **kw: Any) -> Any:
        asked.append((spec, kw))
        raise S.pipeline.PipelineRefused("the pipeline says no")

    monkeypatch.setattr(S.pipeline, "load", load)
    with pytest.raises(S.ServeRefused, match="the pipeline says no"):
        S.served_policy(
            S.ServeOptions(
                policy="lerobot/smolvla_base@abc123",
                fps=30.0,
                cameras="front=observation.images.top",
                pins=("HuggingFaceTB/SmolVLM2-500M-Video-Instruct@def456",),
                threads=3,
                latency_s=0.2,
            )
        )
    ((spec, kw),) = asked
    assert spec == "lerobot/smolvla_base@abc123"
    assert kw == {
        "fps": 30.0,
        "pins": ("HuggingFaceTB/SmolVLM2-500M-Video-Instruct@def456",),
        "cameras": {"front": "observation.images.top"},
        "threads": 3,
        "latency_s": 0.2,
    }
    for spec, needle in (
        ("lerobot/smolvla_base", "has no revision"),
        ("scripted:dance", "no scripted policy called 'dance'"),
        ("nonsense", "REPO@REVISION or scripted:NAME"),
        ("ówner/policy@main", "REPO@REVISION or scripted:NAME"),
    ):
        with pytest.raises(S.ServeRefused, match=needle):
            S.served_policy(S.ServeOptions(policy=spec))
    with pytest.raises(S.ServeRefused, match="--pin"):
        S.served_policy(S.ServeOptions(policy="scripted:hold", pins=("a/b@c",)))
    for name in SCRIPTS:
        S.served_policy(S.ServeOptions(policy=f"scripted:{name}"))


@pytest.mark.parametrize(
    ("options", "needle"),
    [
        (S.ServeOptions(policy="scripted:hold", fps=math.nan), "Hz"),
        (S.ServeOptions(policy="scripted:hold", fps=1000.0), "Hz"),
        (S.ServeOptions(policy="scripted:hold", latency_s=-1.0), "--latency-s"),
        (S.ServeOptions(policy="scripted:hold", latency_s=FIRST_CHUNK_S), "first chunk"),
        (S.ServeOptions(policy="scripted:hold", jpeg_quality=10), "--jpeg-quality"),
        (S.ServeOptions(policy="scripted:hold", cameras="front"), "--cameras"),
        (S.ServeOptions(policy="scripted:hold", threads=0), "--threads"),
    ],
)
def test_a_server_not_worth_starting_says_why(options: S.ServeOptions, needle: str) -> None:
    with pytest.raises(S.ServeRefused, match=needle):
        S.served_policy(options)


def test_a_latency_the_arm_could_never_play_a_chunk_under_is_refused() -> None:
    """The protocol and the server both hold a declared latency under the loop's grace for a
    first chunk, and the server refuses one as long as a chunk's span: every chunk would land
    after its last action's tick. It says the longest it would take, which is half a chunk."""
    assert wire.MAX_LATENCY_S == FIRST_CHUNK_S
    _, info = S.served_policy(S.ServeOptions(policy="scripted:sweep"))
    with pytest.raises(wire.ProtocolError, match="latency_s"):
        wire.validate(wire.PolicyInfo, {**info.model_dump(), "latency_s": FIRST_CHUNK_S})
    rate, chunk = info.rate_hz, info.n_action_steps
    span = chunk / rate
    with pytest.raises(S.ServeRefused, match="none would play") as refused:
        S.served_policy(S.ServeOptions(policy="scripted:sweep", latency_s=span))
    assert f"at most {S.longest_latency_s(rate, chunk):g} s" in str(refused.value)
    result = CliRunner().invoke(
        app, ["policy", "check", "--policy", "scripted:sweep", "--latency-s", f"{span:g}"]
    )
    assert result.exit_code == 1 and "none would play" in " ".join(result.output.split())


def test_a_latency_past_half_a_chunk_is_refused_by_the_loop_s_own_rule() -> None:
    """A segment asks for the next chunk only once the last has landed, so a chunk has to hold
    twice the latency. Half a chunk is served, and a tick more is refused, by serve and by check
    alike, with the ticks of every chunk the arm would have nothing to play for, which is the
    loop's own count (`starved_each_chunk`)."""
    _, info = S.served_policy(S.ServeOptions(policy="scripted:sweep"))
    rate, chunk = info.rate_hz, info.n_action_steps
    half = chunk // 2
    longest = S.longest_latency_s(rate, chunk)
    assert (
        latency_ticks(longest, rate) == half and S.chunk_starved(longest, rate, chunk, False) == 0
    )
    _, served = S.served_policy(S.ServeOptions(policy="scripted:sweep", latency_s=longest))
    assert served.latency_s == longest
    over = (half + 1) / rate
    starved = starved_each_chunk(half + 1, chunk)
    assert 0 < starved == S.chunk_starved(over, rate, chunk, False)
    with pytest.raises(S.ServeRefused, match="nothing to play") as refused:
        S.served_policy(S.ServeOptions(policy="scripted:sweep", latency_s=over))
    said = str(refused.value)
    assert f"nothing to play for {starved} ticks of every chunk" in said, said
    assert f"at most {longest:g} s" in said, said
    result = CliRunner().invoke(
        app, ["policy", "check", "--policy", "scripted:sweep", "--latency-s", f"{over:g}"]
    )
    printed = " ".join(result.output.split())
    assert result.exit_code == 1 and f"for {starved} ticks of every chunk" in printed, printed
    # a policy asked every tick is held to a tick by the loop, and never to half a chunk
    assert S.chunk_starved(over, rate, chunk, True) == 0


def test_check_says_a_server_declares_a_latency_no_segment_could_play() -> None:
    """A server started before `serve` refused a latency past half a chunk still declares one.
    `check` of it says so in its latency row, and a bench of it says so rather than that the
    latency covers what it timed."""
    _, info = S.served_policy(S.ServeOptions(policy="scripted:sweep"))
    rate, chunk = info.rate_hz, info.n_action_steps
    over = (chunk // 2 + 1) / rate
    starved = starved_each_chunk(chunk // 2 + 1, chunk)
    old = wire.validate(wire.PolicyInfo, {**info.model_dump(), "latency_s": over})
    row = dict(S.describe(old))["latency"]
    assert f"nothing to play for {starved} ticks of every chunk" in row, row
    assert "quackd policy serve refuses it" in row, row
    fine = dict(S.describe(info))["latency"]
    assert fine == f"{info.latency_s:g} s declared", fine
    quick = 1 / rate  # synthetic: every step timed at a tick
    result = S.BenchResult(
        1.0, rate, 1, 1, 0, 0, rtt_s=(quick,), latency_s=quick, declared_s=over, chunk=chunk
    )
    said = dict(S.describe_bench(result))["latency"]
    assert "covers that" not in said, said
    assert f"nothing to play for {starved} ticks of every chunk" in said, said
    assert result.declare_s is not None and f"--latency-s {result.declare_s:.2f}" in said, said


def test_a_bench_says_a_step_slower_than_half_a_chunk_can_leave_the_arm_waiting() -> None:
    """A server declares the longest latency `serve` takes, and a bench times it covering all
    its steps but one, the step in twenty past the quantile it suggests from. That one step is
    within half a chunk, and the bench says the latency covers what it timed and nothing more.
    Or it is a tick past, which no refill lands before the arm has nothing to play, since the
    one request out goes no sooner than the last chunk lands: the bench says so after it says
    the latency covers the rest."""
    _, info = S.served_policy(S.ServeOptions(policy="scripted:sweep"))
    rate, chunk = info.rate_hz, info.n_action_steps
    half = longest_latency(chunk)
    declared = S.longest_latency_s(rate, chunk)
    for slowest, outran in ((half, False), (half + 1, True)):
        steps = (declared,) * 19 + (slowest / rate,)  # synthetic: one step in twenty slow
        result = S.BenchResult(1.0, rate, 1, 1, 0, 0, rtt_s=steps, declared_s=declared, chunk=chunk)
        assert result.declare_s == pytest.approx(declared), "the quantile is not the declared"
        said = dict(S.describe_bench(result))["latency"]
        assert said.endswith(f"--latency-s {declared:g} it is served with covers that") is (
            not outran
        ), said
        if outran:
            assert f"covers that, but its slowest step took {1000 * slowest / rate:.1f} ms, " in (
                said
            ), said
            assert f"{half + 1} ticks at {rate:g} Hz" in said, said
            assert f"half a chunk of {chunk} actions, {half} ticks, is sure" in said, said
            assert said.endswith("serve the policy where it answers faster"), said


async def test_the_longest_latency_the_server_takes_still_plays_on_the_simulators_clock() -> None:
    """Half a chunk, on a lockstep clock, which holds each chunk back its declared latency as
    the simulator does: every chunk after the first lands as the one before runs out, so the
    arm is starved only while the first is on its way, and the segment ends on its chunks."""
    _, info = S.served_policy(S.ServeOptions(policy="scripted:sweep"))
    rate, chunk = info.rate_hz, info.n_action_steps
    latest = S.longest_latency_s(rate, chunk)
    serving = _serving(S.ServeOptions(policy="scripted:sweep", latency_s=latest))
    transport, adapter, _ = await _arm_on(serving.client(), clock=LockstepClock())
    try:
        ended = await _segment_end(transport, max_s=1e6, max_chunks=3)
        assert ended.how == "chunks" and ended.stats.chunks == 3, ended
        assert ended.stats.starved == latency_ticks(latest, rate), ended.stats
    finally:
        await adapter.close()
        serving.http.shutdown()
        serving.http.server_close()


# ── quackd policy check ─────────────────────────────────────────────────────────────────


def test_the_bench_streams_through_the_real_client(served: Serving) -> None:
    result = S.bench(served.client(), seconds=0.5)
    assert result.ticks >= 1 and result.played >= 1 and result.rtt_s
    assert result.rate_hz == SCRIPTED_HZ and result.achieved_hz > 0
    rows = dict(S.describe_bench(result))
    assert "Hz of" in rows["achieved"] and "round trip" in rows
    # one warm step timed on its own and every step of the stream, and the --latency-s the
    # bench would declare, read over all of them and never shorter than that reading
    assert result.latency_s is not None and result.timed_s == (result.latency_s, *result.rtt_s)
    measured, declared = result.measured_s, result.declare_s
    assert measured is not None and declared is not None
    assert measured <= declared < measured + S.LATENCY_STEP_S
    assert f"--latency-s {declared:.2f}" in rows["latency"]


def test_a_bench_of_a_policy_that_declares_no_latency_waits_out_its_first_chunk_alone() -> None:
    """A server started with no --latency-s, whose every step takes `trip`, under half the
    sweep's chunk. The bench waits for the first chunk in the tick that asked for it, as a
    segment does with nothing to play, and leaves every later one to land while the ticks play
    the half of the last still queued, as a segment does. So the ticks it skips are the first
    chunk's, and none after the first has nothing to send. Waiting out every chunk in its tick
    would skip that many again for each."""
    trip = 0.2
    built, _ = S.served_policy(S.ServeOptions(policy="scripted:sweep"))
    script = built.script

    def slow(observation: Observation, sent: Mapping[str, float]) -> Any:
        time.sleep(trip)
        return script(observation, sent)

    built.script = slow
    serving = _serving(S.ServeOptions(policy="scripted:sweep"), runner=built)
    try:
        result = S.bench(serving.client(), seconds=2.0)
    finally:
        serving.http.shutdown()
        serving.http.server_close()
        serving.app.close()
    assert result.waited and result.declared_s == 0, result
    first = math.ceil(trip * result.rate_hz)  # the ticks the first chunk's round trip spans
    assert result.chunk is not None and 2 * first < result.chunk // 2, "a step that slow starves"
    assert result.starved == 0, result
    # room for the first wait to run long on a busy machine, and none for a second wait
    assert 1 <= result.waited_ticks <= 2 * first, result
    assert built.requests >= 2 + 3, "the stream asked for too few chunks to tell"


def test_a_bench_with_no_async_chunks_plays_each_chunk_out_and_holds_between() -> None:
    """`--no-async-chunks` on the bench paces it as a segment given the flag plays: each chunk
    played out before the next is asked for, and the ticks while that one is on its way
    holding still, which are starved rather than skipped, since none is waited for in a tick.
    Its starved row says the flag is why."""
    trip = 0.2
    built, _ = S.served_policy(S.ServeOptions(policy="scripted:sweep"))
    script = built.script

    def slow(observation: Observation, sent: Mapping[str, float]) -> Any:
        time.sleep(trip)
        return script(observation, sent)

    built.script = slow
    serving = _serving(S.ServeOptions(policy="scripted:sweep"), runner=built)
    try:
        result = S.bench(serving.client(), seconds=4.0, async_chunks=False)
    finally:
        serving.http.shutdown()
        serving.http.server_close()
        serving.app.close()
    asked = built.requests - 2  # the warm step and the one timed on its own came first
    assert asked >= 3 and result.waits_each and not result.waited_ticks, result
    # every chunk before the last was played out, each action sent or, on a machine too busy to
    # keep the rate, dropped with a tick the pacer skipped, as CI's macOS runners do
    assert result.chunk is not None, result
    assert result.played + result.skipped >= (asked - 1) * result.chunk, result
    assert result.starved >= asked, result  # a tick at least of every wait held still
    said = dict(S.describe_bench(result))["starved"]
    assert "--no-async-chunks" in said, said


@pytest.mark.parametrize("k", [0, 1], ids=["no latency", "a tick"])
def test_a_bench_of_a_policy_asked_every_tick_plays_each_answer_the_tick_it_lands(k: int) -> None:
    """A server in tick mode, as an ACT trained with temporal ensembling is served. Declaring
    the tick it takes to answer, a segment asks it every tick, takes each answer in on the tick
    after and plays it there (`loop.PolicyLoop._merge`), and the bench paces it the same way, so
    it starves only while the first answer is on its way. Kept for the tick it was asked at,
    every answer was a tick stale when it was taken in and thrown away, and the bench starved
    every tick of a policy the arm plays. Declaring none, each answer is waited for in the tick
    that asked for it and played there, on the arm as in the bench."""
    _, sweep = S.served_policy(S.ServeOptions(policy="scripted:sweep"))
    rate = sweep.rate_hz
    every = ScriptedRunner(
        lambda _observation, _sent: [{SWEEP_JOINT: 0.0}],
        rate_hz=rate,
        latency_ticks=k,
        per_tick=True,
    )
    declared = every.latency_s()
    info = wire.validate(
        wire.PolicyInfo,
        {
            **sweep.model_dump(),
            "per_tick": True,
            "latency_s": declared,
            "chunk_size": 1,
            "n_action_steps": 1,
        },
    )
    app_ = S.PolicyServer(every, info, TOKEN)
    serving = Serving(app_, S.serve(app_, "127.0.0.1", 0))
    try:
        result = S.bench(serving.client(), seconds=2.0)
    finally:
        serving.http.shutdown()
        serving.http.server_close()
        serving.app.close()
    assert result.per_tick and result.declared_s == declared and latency_ticks(declared, rate) == k
    assert every.requests - 2 >= result.ticks - 1, "a policy in tick mode is asked every tick"
    # declaring a tick, the first has nothing yet, and on a busy machine an answer may miss one
    assert result.before_first >= k and result.played >= result.ticks // 2, result


def test_the_suggested_latency_covers_most_steps_timed_and_rests_on_no_one_of_them() -> None:
    """One step timed on its own is one draw, and two benches of one checkpoint on one laptop
    suggested latencies too far apart to serve with either. The suggestion is read at
    `LATENCY_QUANTILE` of every step the bench timed, the warm one and each of the stream's,
    so a quick warm step does not talk it down and the one slowest step does not talk it up."""
    step = S.LATENCY_STEP_S
    stream = tuple(step * k for k in range(1, 101))  # synthetic: one to a hundred steps each
    result = S.BenchResult(1.0, 10.0, 10, 10, 0, 0, rtt_s=stream, latency_s=step / 2)
    timed = sorted(result.timed_s)
    expected = timed[math.ceil(S.LATENCY_QUANTILE * len(timed)) - 1]
    assert result.measured_s == expected
    assert result.latency_s is not None and result.latency_s < expected < max(stream)
    declared = result.declare_s
    assert declared is not None and expected <= declared < expected + step


def test_a_bench_says_what_its_skipped_ticks_were_and_whether_its_latency_covers_it() -> None:
    """A tick the pacer skipped sent nothing, as a starved one did, and is not counted as
    starved, so the bench says what it was: with no --latency-s declared, the wait in the tick
    that asked for a chunk with nothing left to play, as a segment waits, and otherwise a pacer
    that woke late, each of them counted where a bench had both.
    Its latency row says to bench again served with the latency it suggests, and on that
    second bench, that the latency it was served with covers what it timed."""
    rate, seconds, trip = 10.0, 2.0, 0.5  # synthetic: a step takes five ticks
    periods = round(rate * seconds)
    played = round(seconds / trip)  # one tick sent per step waited for, the rest skipped
    first = S.BenchResult(
        seconds,
        rate,
        played,
        played,
        0,
        periods - played,
        rtt_s=(trip,) * played,
        latency_s=trip,
        waited=True,
        waited_ticks=periods - played,
    )
    rows = dict(S.describe_bench(first))
    assert f"{played} of the {periods} ticks" in rows["achieved"], rows
    assert rows["skipped"].startswith(f"{periods - played} ticks:"), rows
    assert "no --latency-s" in rows["skipped"] and "sent nothing" in rows["skipped"], rows
    assert first.declare_s is not None
    assert f"serve with --latency-s {first.declare_s:.2f}" in rows["latency"], rows
    assert "bench again" in rows["latency"], rows
    waiting = round(trip * rate)  # the first chunk on its way, which a segment waits for
    second = S.BenchResult(
        seconds,
        rate,
        periods,
        periods - waiting,
        waiting,
        0,
        rtt_s=(trip,) * played,
        latency_s=trip,
        declared_s=first.declare_s,
        before_first=waiting,
    )
    rows = dict(S.describe_bench(second))
    assert "skipped" not in rows, rows
    assert rows["starved"].startswith(f"{waiting} ticks with nothing to send"), rows
    assert "every one before the first came back" in rows["starved"], rows
    assert f"--latency-s {first.declare_s:g} it is served with covers that" in rows["latency"]
    late = S.BenchResult(seconds, rate, periods - 1, periods - 1, 0, 1, declared_s=trip)
    assert "woke too late" in dict(S.describe_bench(late))["skipped"]
    # a first chunk waited for, and a busy machine's pacer late for two more ticks after it
    both = S.BenchResult(seconds, rate, periods - 7, periods - 7, 0, 7, waited=True, waited_ticks=5)
    said = dict(S.describe_bench(both))["skipped"]
    assert said.startswith("7 ticks: 5 passed while a chunk asked for"), said
    assert "no --latency-s" in said and "woke too late for 2 more" in said, said
    assert said.endswith("the arm would have held still through them"), said


def test_a_bench_suggests_no_latency_the_server_would_refuse() -> None:
    """A step slower than a segment waits for its first chunk, or than half a chunk takes to
    play, still completes a bench, since the client waits longer for a step than either. The
    bench then says the policy answers too slowly to serve from this machine, and never
    suggests a --latency-s that `quackd policy serve` refuses. One inside every bound it
    suggests, and the server takes."""
    _, info = S.served_policy(S.ServeOptions(policy="scripted:hold"))
    rate, chunk = float(info.rate_hz), info.n_action_steps
    past_half = (chunk // 2 + 1) / rate
    past_chunk = (chunk + 1) / rate
    past_wait = (wire.MAX_LATENCY_S + STEP_TIMEOUT_S) / 2
    fits = S.longest_latency_s(rate, chunk)
    assert fits < past_half < past_chunk < wire.MAX_LATENCY_S < past_wait < STEP_TIMEOUT_S
    cases = ((past_half, True), (past_chunk, True), (past_wait, True), (fits, False))
    for trip, too_slow in cases:
        result = S.BenchResult(
            1.0,
            rate,
            1,
            1,
            0,
            0,
            rtt_s=(trip,),
            latency_s=trip,
            chunk=chunk,
            per_tick=info.per_tick,
        )
        declare = result.declare_s
        assert declare is not None
        said = dict(S.describe_bench(result))["latency"]
        serve = S.ServeOptions(policy="scripted:hold", latency_s=declare)
        if too_slow:
            assert "serve with --latency-s" not in said, said
            assert "too slowly" in said and "serve it on a GPU" in said, said
            with pytest.raises(S.ServeRefused):
                S.served_policy(serve)
        else:
            assert f"serve with --latency-s {declare:.2f}" in said, said
            S.served_policy(serve)


def test_a_measured_latency_rounds_up_to_what_a_person_would_declare() -> None:
    step = S.LATENCY_STEP_S
    for measured in (0.0, step / 3, step, 2.5 * step, 7 * step + step / 100):
        declared = S.BenchResult(1.0, 10.0, 1, 1, 0, 0, latency_s=measured).declare_s
        assert declared is not None and measured <= declared + 1e-12
        assert declared - measured < step, measured
        assert math.isclose(declared / step, round(declared / step)), measured


def test_check_serves_a_scripted_policy_for_itself_and_benches_it() -> None:
    result = CliRunner().invoke(
        app, ["policy", "check", "--policy", "scripted:sweep", "--bench", "--seconds", "0.5"]
    )
    assert result.exit_code == 0, result.output
    out = " ".join(result.output.split())
    for needle in ("scripted:sweep", "Hz, from", "achieved", "round trip", "--latency-s"):
        assert needle in out, needle
    assert "a scripted policy loads no repository" in out


def test_check_reaches_a_running_server_with_its_token(served: Serving) -> None:
    runner = CliRunner()
    ok = runner.invoke(
        app, ["policy", "check", "--policy-url", served.url, "--policy-token", TOKEN]
    )
    assert ok.exit_code == 0 and "scripted:sweep" in ok.output, ok.output
    for argv, needle in (
        (["policy", "check"], "--policy-url"),
        (["policy", "check", "--policy-url", "http://10.0.0.5:9875"], "ssh -L"),
        (["policy", "check", "--policy-url", served.url, "--fps", "5"], "--fps"),
        (
            ["policy", "check", "--policy", "scripted:hold", "--policy-token", "x"],
            "--policy-token goes with --policy-url",
        ),
    ):
        refused = runner.invoke(app, argv)
        assert refused.exit_code == 1 and needle in " ".join(refused.output.split()), argv


def test_the_policy_commands_name_the_extra_when_the_adapter_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import quackd_lerobot.policy

    # the package keeps its imported submodule as an attribute, which `from ... import` finds
    # before it looks in sys.modules, so both go
    monkeypatch.delattr(quackd_lerobot.policy, "server")
    monkeypatch.setitem(sys.modules, "quackd_lerobot.policy.server", None)
    result = CliRunner().invoke(app, ["policy", "check", "--policy", "scripted:hold"])
    assert result.exit_code == 1 and "quackd[lerobot]" in result.output


def test_every_scripted_policy_runs_a_chunk_in_process() -> None:
    """The scripts by name, asked directly, so a broken one is found without HTTP between."""
    for name in SCRIPTS:
        runner, info = S.served_policy(S.ServeOptions(policy=f"scripted:{name}"))
        runner.reset("wave")
        chunk = runner.next_chunk(Observation(0, _reading(1.0)), {})
        assert len(chunk.actions) == info.chunk_size, name
        assert all(math.isfinite(v) for a in chunk.actions for v in a.values()), name


def test_a_kept_alive_socket_the_server_closed_is_replaced_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The server closes a connection idle past `REQUEST_TIMEOUT_S`, as the arm's is between two
    segments while the pilot thinks. The client finds out only when it next sends on it, and
    sends once more on a new one rather than failing the segment's start."""
    monkeypatch.setattr(S, "REQUEST_TIMEOUT_S", 0.2)
    serving = _serving(S.ServeOptions(policy="scripted:hold"))
    try:
        runner = serving.client()
        runner.reset("wave")
        before = runner._conn
        time.sleep(0.6)
        runner.reset("wave again")
        assert runner._conn is not None and runner._conn is not before
        assert runner.next_chunk(Observation(0, _reading()), {}).actions
    finally:
        serving.http.shutdown()
        serving.http.server_close()


# ── whether the policy fits the arm, asked at connect before any torque ─────────────────

IMAGE_KEY = "observation.images.front"
"""The image a checkpoint trained with a front camera looks at (`upstream_api.FEATURE_KEYS`)."""
CAMERA_HEIGHT, CAMERA_WIDTH = (int(n) for n in FakeCamera().read_latest().shape[:2])
"""The size of the frames the test suite's camera gives, read off one of them."""


def _as_checkpoint(**said: Any) -> Serving:
    """A server that says of itself what a checkpoint for this six-motor arm and its front
    camera would, and answers with the scripted hold: the arm's check reads only what the
    server says, so this is the check against a checkpoint with no torch anywhere."""
    runner, base = S.served_policy(
        S.ServeOptions(policy="scripted:hold", cameras=f"front={IMAGE_KEY}")
    )
    image = {"key": IMAGE_KEY, "height": CAMERA_HEIGHT, "width": CAMERA_WIDTH}
    info = {
        **base.model_dump(),
        "policy": "owner/act_front@0123abc",
        "features": {"state": len(JOINTS), "action": len(JOINTS), "images": [image]},
        **said,
    }
    app_ = S.PolicyServer(runner, wire.validate(wire.PolicyInfo, info), TOKEN)
    return Serving(app_, S.serve(app_, "127.0.0.1", 0))


def _camera_arm(runner: RemoteRunner) -> tuple[FakeArm, FakeCamera, LeRobotReal]:
    """The fake arm on the synthetic calibration and the rewired bus, with a front camera of
    quackd's own, and the policy behind `runner`."""
    arm, camera = _segment_arm(), FakeCamera()
    transport = LeRobotReal(
        "COM5",
        robot=arm,
        policy=runner,
        clock=SteppedClock(),
        max_step_deg=STEP,
        camera=parse_camera_url("opencv://0?name=front"),
        camera_object=camera,
    )
    return arm, camera, transport


def _quantiles(arm: FakeArm, *, below: str | None = None, above: str | None = None) -> Any:
    """The 1st and 99th percentiles of a state that stayed inside this arm's calibrated travel,
    in its bus's order, except `below`, which reached past its floor, and `above`, past its
    ceiling, each by twice the slack the backend forgives."""
    travel = joint_ranges(arm.calibration)
    q01, q99 = [], []
    for motor in arm.bus.motors:
        low, high = travel[motor]
        middle, quarter = (low + high) / 2, (high - low) / 4
        q01.append(low - 2 * OUT_OF_RANGE_DEG if motor == below else middle - quarter)
        q99.append(high + 2 * OUT_OF_RANGE_DEG if motor == above else middle + quarter)
    return {"q01": q01, "q99": q99}


async def _refused(serving: Serving, **client: Any) -> tuple[str, FakeArm, FakeCamera]:
    arm, camera, transport = _camera_arm(serving.client(**client))
    with pytest.raises(TransportError) as refused:
        await transport.connect()
    return " ".join(str(refused.value).split()), arm, camera


def _stop(serving: Serving) -> None:
    serving.http.shutdown()
    serving.http.server_close()


async def test_a_policy_learned_on_an_arm_calibrated_another_way_is_refused_before_torque() -> None:
    """The synthetic calibration puts one joint's learned readings past its floor and another's
    past its ceiling. Both are named, with the travel each was held against, and the arm is
    never energised: the check runs between the cameras and the arm."""
    arm = _segment_arm()
    serving = _as_checkpoint(
        state_quantiles=_quantiles(arm, below="shoulder_pan", above="wrist_roll")
    )
    try:
        said, arm, camera = await _refused(serving)
        outside = said.split("travel:", 1)[1]
        assert "shoulder_pan" in outside and "wrist_roll" in outside, said
        for motor in ("shoulder_lift", "elbow_flex", "wrist_flex", "gripper"):
            assert motor not in outside.split(". It was trained", 1)[0], said
        assert "--accept-other-frame" in said and "The arm was not touched" in said, said
        assert "still clipped to this arm's travel" in said, said
        assert not arm.connected and not arm.torque_retries and not camera.connected
        # the one who knows the frames match says so, and the record keeps that they did
        runner = serving.client(accept_other_frame=True)
        arm, _, transport = _camera_arm(runner)
        await transport.connect()
        try:
            notes = " ".join(transport.connect_notes)
            assert "accepted (--accept-other-frame)" in notes and "shoulder_pan" in notes
            assert runner.record()["accept_other_frame"] is True
            assert arm.connected
        finally:
            await transport.close()
    finally:
        _stop(serving)


async def test_learned_readings_inside_the_travel_and_its_slack_connect() -> None:
    arm = _segment_arm()
    travel = joint_ranges(arm.calibration)
    within = _quantiles(arm)
    first = next(iter(arm.bus.motors))
    within["q01"][0] = travel[first][0] - OUT_OF_RANGE_DEG / 2  # past the floor, inside the slack
    serving = _as_checkpoint(
        state_quantiles=within,
        loaded=["checkpoint owner/act_front@0123abc", "dataset owner/data@v3.0, its fps"],
    )
    try:
        arm, _, transport = _camera_arm(serving.client())
        await transport.connect()
        try:
            notes = " ".join(transport.connect_notes)
            assert "owner/act_front@0123abc" in notes and "owner/data@v3.0" in notes, notes
            assert "accepted" not in notes and "not checked" not in notes, notes
            assert arm.connected
        finally:
            await transport.close()
    finally:
        _stop(serving)


async def test_a_policy_the_server_swaps_in_after_the_connect_starts_no_segment() -> None:
    """The arm's process lives for a pilot's whole session, and the server on its port can be
    started again with another policy in that time. The check made at connect is made again at
    every segment's reset: another policy than the one checked is refused with a sentence to
    connect again, and the same one refused if it no longer fits, before any session starts."""
    arm = _segment_arm()
    serving = _as_checkpoint(state_quantiles=_quantiles(arm))
    fitted = serving.app.info
    try:
        runner = serving.client()
        arm, _, transport = _camera_arm(runner)
        await transport.connect()
        loop = transport._policy_loop
        assert loop is not None
        try:
            assert isinstance(await loop.start("reach", STEP), Plan), "the one checked starts"
            # the same checkpoint, now saying it learned from an arm calibrated another way
            serving.app.info = fitted.model_copy(
                update={"state_quantiles": wire.Quantiles(**_quantiles(arm, above="gripper"))}
            )
            said = await loop.start("reach", STEP)
            assert isinstance(said, str) and "PolicyMisfit" in said, said
            assert "gripper" in said and "--accept-other-frame" in said, said
            # another checkpoint that would fit is still not the one the connect checked
            serving.app.info = fitted.model_copy(update={"policy": "owner/other_act@4567def"})
            with pytest.raises(PolicyMisfit, match="Connect the arm again") as refused:
                runner.reset("reach")
            assert "owner/other_act@4567def" in str(refused.value)
            assert "owner/act_front@0123abc" in str(refused.value)
            # and with the one checked back, a segment starts again
            serving.app.info = fitted
            assert isinstance(await loop.start("reach", STEP), Plan)
        finally:
            await transport.close()
    finally:
        _stop(serving)


async def test_a_latency_no_chunk_can_carry_refuses_the_connect_before_torque() -> None:
    """`quackd policy serve` refuses a latency past half a chunk, and a server an earlier quackd
    started with one still declares it. The arm's connect refuses such a server before any
    torque, with the ticks of every chunk the arm would have nothing to play for, the loop's own
    count, and the longest latency that fits. That latency connects, and a server started again
    past it on the same port starts no segment. The check reads only what the server declares:
    a chunk's whole span is refused as none playing, and a policy asked every tick is held to a
    tick by the loop and never to half a chunk."""
    runner, info = S.served_policy(S.ServeOptions(policy="scripted:sweep"))
    rate, chunk = info.rate_hz, info.n_action_steps
    half = longest_latency(chunk)
    over = (half + 1) / rate
    starved = starved_each_chunk(half + 1, chunk)
    assert latency_ticks(over, rate) == half + 1 and starved > 0
    fits = S.longest_latency_s(rate, chunk)
    old = wire.validate(wire.PolicyInfo, {**info.model_dump(), "latency_s": over})
    app_ = S.PolicyServer(runner, old, TOKEN)
    serving = Serving(app_, S.serve(app_, "127.0.0.1", 0))
    try:
        said, arm, camera = await _refused(serving)
        assert f"declares {over:g} s to answer" in said, said
        assert f"nothing to play for {starved} ticks of every chunk" in said, said
        assert f"--latency-s of at most {fits:g} s" in said, said
        assert "The arm was not touched" in said, said
        assert not arm.connected and not arm.torque_retries and not camera.connected
        serving.app.info = old.model_copy(update={"latency_s": fits})
        arm, _, transport = _camera_arm(serving.client())
        await transport.connect()
        loop = transport._policy_loop
        assert loop is not None
        try:
            assert isinstance(await loop.start("reach", STEP), Plan), "half a chunk connects"
            serving.app.info = old
            again = await loop.start("reach", STEP)
            assert isinstance(again, str) and "PolicyMisfit" in again, again
            assert f"nothing to play for {starved} ticks of every chunk" in again, again
        finally:
            await transport.close()
    finally:
        _stop(serving)
        serving.app.close()

    def verdict(**said: Any) -> str | None:
        declared = wire.validate(wire.PolicyInfo, {**info.model_dump(), **said})
        return fit(declared, motors=JOINTS, cameras=[], travel={}, slack_deg=0, where="x").refusal

    outrun = verdict(latency_s=chunk / rate)
    assert outrun is not None and "none would play" in outrun, outrun
    assert verdict(latency_s=over, per_tick=True) is None
    assert verdict(latency_s=fits) is None


async def test_a_frame_of_another_size_is_refused_unless_it_is_accepted() -> None:
    image = {"key": IMAGE_KEY, "height": CAMERA_HEIGHT // 2, "width": CAMERA_WIDTH // 2}
    serving = _as_checkpoint(
        features={"state": len(JOINTS), "action": len(JOINTS), "images": [image]}
    )
    try:
        said, arm, _ = await _refused(serving)
        assert f"{CAMERA_WIDTH}x{CAMERA_HEIGHT}" in said, said
        assert f"{CAMERA_WIDTH // 2}x{CAMERA_HEIGHT // 2}" in said, said
        # the fix a person can reach, the camera's own size, and no keyword they cannot
        said_size = f"width={CAMERA_WIDTH // 2} and height={CAMERA_HEIGHT // 2}"
        assert said_size in said and "accept_frame_size" not in said, said
        assert not arm.connected
        _, _, transport = _camera_arm(serving.client(accept_frame_size=True))
        await transport.connect()
        try:
            assert "accepted (accept_frame_size)" in " ".join(transport.connect_notes)
        finally:
            await transport.close()
    finally:
        _stop(serving)


async def test_an_image_with_no_camera_refuses_an_act_and_is_padded_for_one_that_pads() -> None:
    wrist = {"key": "observation.images.wrist", "height": CAMERA_HEIGHT, "width": CAMERA_WIDTH}
    front = {"key": IMAGE_KEY, "height": CAMERA_HEIGHT, "width": CAMERA_WIDTH}
    both = {"state": len(JOINTS), "action": len(JOINTS), "images": [front, wrist]}
    mapped = {"front": IMAGE_KEY, "wrist": "observation.images.wrist"}
    act = _as_checkpoint(features=both, cameras=mapped)
    try:
        said, arm, _ = await _refused(act)
        assert "observation.images.wrist" in said and "--cameras" in said, said
        assert not arm.connected
        # a client that never asked is refused by the server's own reset the same way
        with pytest.raises(PolicyServerError, match=r"observation\.images\.wrist"):
            act.client(cameras=[wire.CameraInfo(name="front", height=4, width=4)]).reset("look")
    finally:
        _stop(act)
    padder = _as_checkpoint(features={**both, "pads_images": True}, cameras=mapped)
    try:
        runner = padder.client()
        _, _, transport = _camera_arm(runner)
        await transport.connect()
        try:
            notes = " ".join(transport.connect_notes)
            assert "observation.images.wrist has no camera" in notes and "padded" in notes
            runner.reset("look")  # and the server takes a session with the wrist left out
            assert runner.session is not None
        finally:
            await transport.close()
        with pytest.raises(PolicyServerError, match="no camera the arm declared"):
            padder.client(cameras=[]).reset("look")  # but not one with no image at all
    finally:
        _stop(padder)


async def test_a_policy_for_another_arm_or_with_its_joints_in_another_order_is_refused() -> None:
    motors = list(_segment_arm().bus.motors)
    for said_of_it, needle in (
        ({"state": len(motors) - 1, "action": len(motors) - 1}, "learned from another arm"),
        ({"state": len(motors), "action": len(motors) + 1}, "learned from another arm"),
        (
            {
                "state": len(motors),
                "action": len(motors),
                "action_names": [f"{m}.pos" for m in reversed(motors)],
            },
            "wrong joints",
        ),
    ):
        serving = _as_checkpoint(features={**said_of_it, "images": []}, cameras={})
        try:
            said, refused_arm, _ = await _refused(serving)
            assert needle in said and "The arm was not touched" in said, said
            assert ", ".join(motors) in said, "the bus's own order is what it is held against"
            assert not refused_arm.connected
        finally:
            _stop(serving)
    # the names in the bus's own order, with or without LeRobot's `.pos`, fit
    for names in ([f"{m}.pos" for m in motors], motors):
        features = {"state": len(motors), "action": len(motors), "action_names": names}
        serving = _as_checkpoint(features={**features, "images": []}, cameras={})
        try:
            _, _, transport = _camera_arm(serving.client())
            await transport.connect()
            await transport.close()
        finally:
            _stop(serving)


async def test_a_policy_server_that_is_not_there_refuses_the_connect_before_torque() -> None:
    serving = _as_checkpoint()
    url = serving.url
    _stop(serving)
    arm, camera, transport = _camera_arm(RemoteRunner(url, token=TOKEN, motors=JOINTS))
    # refused at once where a closed port says so, and after the connect timeout on Windows,
    # which tries a refused port again before it gives up: either way the sentence names it
    with pytest.raises(TransportError, match="quackd policy serve") as refused:
        await transport.connect()
    assert "The arm was not touched" in str(refused.value)
    assert not arm.connected and not camera.connected


def test_the_check_reads_what_it_is_given_and_types_nothing() -> None:
    """The same verdicts from `fit` itself, for motors, cameras and a travel that are nobody's
    arm, in no order anybody's bus lists, and a slack of whatever the caller says."""
    motors = ("elbow", "base", "claw")
    travel = {"elbow": (-30.0, 50.0), "base": (-80.0, 10.0)}
    cam = wire.CameraInfo(name="cam", height=6, width=8)
    info = wire.validate(
        wire.PolicyInfo,
        {
            **S.served_policy(S.ServeOptions(policy="scripted:hold"))[1].model_dump(),
            "features": {
                "state": 3,
                "action": 3,
                "images": [
                    {"key": "observation.images.a", "height": 6, "width": 8},
                    {"key": "observation.images.b", "height": 6, "width": 8},
                ],
                "pads_images": True,
            },
            "cameras": {"cam": "observation.images.a", "gone": "observation.images.b"},
            "state_quantiles": {"q01": [-30.5, -80.0, 0.0], "q99": [49.0, 10.9, 100.0]},
        },
    )
    ok = fit(info, motors=motors, cameras=[cam], travel=travel, slack_deg=1.0, where="there")
    assert ok.refusal is None
    notes = " ".join(ok.notes)
    assert "observation.images.b has no camera" in notes and "claw has no travel" in notes
    tight = fit(info, motors=motors, cameras=[cam], travel=travel, slack_deg=0.25, where="x")
    assert tight.refusal is not None and "elbow" in tight.refusal and "base" in tight.refusal
    blind = fit(info, motors=motors, cameras=[], travel=travel, slack_deg=1.0, where="there")
    assert blind.refusal is not None and "any of them" in blind.refusal
