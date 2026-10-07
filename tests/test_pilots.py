"""`run_pilot_flock`: N agent loops on wall-clock time, and what happens when one of them
does not come back.

Everything here runs on `mock` backends, which is what a pilot flock has ever run on.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from quackd.adapters.factory import RobotSpec, describe
from quackd.agent.providers.base import Exchange, ProviderTurn, ToolCall, Usage
from quackd.agent.providers.fake import FakeProvider
from quackd.duckfile.parser import load_duck, parse_duck_text
from quackd.flock.pilots import (
    MAX_MEMBERS,
    SpecEntry,
    aggregate_outcome,
    roster_from_specs,
    run_pilot_flock,
    trim_contract,
    union_problems,
)
from quackd.memory import RobotMemory

MIXED = {
    "duck": RobotSpec("microduck", "mock", "duck"),
    "arm": RobotSpec("lerobot", "mock", "arm"),
}


def _roster(specs: dict[str, RobotSpec] | None = None) -> dict[str, SpecEntry]:
    return roster_from_specs(specs or MIXED)


def _providers(roster: dict[str, Any], duck_name: str = "flock-hello") -> dict[str, Any]:
    # one instance per member: a shared strategy closure would be shared state
    return {name: FakeProvider.for_duck(duck_name) for name in roster}


async def _run(tmp_path: Path, roster: dict[str, Any] | None = None, **kw: Any) -> Any:
    roster = roster if roster is not None else _roster()
    return await asyncio.wait_for(
        run_pilot_flock(
            load_duck("flock-hello"),
            roster,
            providers=kw.pop("providers", None) or _providers(roster),
            runs_dir=tmp_path,
            **kw,
        ),
        timeout=120,
    )


def _summary(result: Any) -> dict[str, Any]:
    return json.loads((result.run_dir / "summary.json").read_text(encoding="utf-8"))


def _flock_lines(result: Any) -> list[dict[str, Any]]:
    text = (result.run_dir / "flock.jsonl").read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


async def test_a_pilot_flock_takes_the_name_you_gave_the_run(tmp_path: Path) -> None:
    """`--run-name` is for the afternoon somebody runs a hundred examples on one bench, and a
    flock is as much a run as a solo pilot is: it gets the same label on the same directory and
    the name as typed in the same summary field."""
    result = await _run(tmp_path, run_name="Bench 7")
    assert result.run_dir.name.endswith("-flock-hello-bench-7"), result.run_dir.name
    assert _summary(result)["run_name"] == "Bench 7"


async def test_a_pilot_flock_without_a_name_is_named_as_it_always_was(tmp_path: Path) -> None:
    """Every flock recorded before there was a flag for it, and every one that does not ask,
    keeps exactly the directory name it had."""
    result = await _run(tmp_path)
    assert result.run_dir.name.endswith("-flock-hello"), result.run_dir.name
    assert _summary(result)["run_name"] is None


async def test_a_pilot_flock_adds_up_what_its_members_cost(tmp_path: Path) -> None:
    """The flock sums money where it already sums tokens. Its members are scripted here, so
    each of them is free and the total is a real zero rather than a missing number."""
    result = await _run(tmp_path)
    summary = _summary(result)
    assert summary["cost_usd"] == 0.0
    assert result.cost_usd == 0.0
    per_member = summary["per_member"]
    assert all(m["cost_usd"] == 0.0 for m in per_member.values())
    assert sum(m["cost_usd"] for m in per_member.values()) == summary["cost_usd"]


async def test_the_price_you_passed_reaches_every_member(tmp_path: Path) -> None:
    """`--price` is validated on `quackd run` whether or not there is a flock, so a flock that
    quietly ignored it would be a flag that checks your typing and then does nothing. Each
    member is costed at the one rate: it is a rate for the run, not one per robot."""
    result = await _run(tmp_path, price="in=1000000,out=0")
    summary = _summary(result)
    for member in summary["per_member"].values():
        assert member["cost_usd"] == member["usage"]["input_tokens"], (
            "a million dollars per million input tokens makes the bill the token count, "
            "which is the cheapest way to prove the rate arrived rather than a default"
        )
    assert summary["cost_usd"] == sum(m["cost_usd"] for m in summary["per_member"].values())


async def test_one_unpriced_member_makes_the_whole_flock_bill_unpriced(tmp_path: Path) -> None:
    """A total that quietly left a robot out would read as a cheaper flock rather than an
    incomplete one, so one member quackd cannot price makes the flock figure None. The member
    is made unpriceable the way a real one would be: a real vendor name and a model id no
    catalogue has ever listed."""
    roster = _roster()
    providers = _providers(roster)
    first = next(iter(providers.values()))
    first.name = "openai"
    first.model = "a-model-no-catalogue-lists"
    result = await _run(tmp_path, roster=roster, providers=providers)
    summary = _summary(result)
    assert summary["cost_usd"] is None
    assert result.cost_usd is None
    assert any(m["cost_usd"] is None for m in summary["per_member"].values())


# ── the contract, per body ──────────────────────────────────────────────────────────────


def test_each_member_is_handed_the_half_of_the_contract_its_body_can_answer_for() -> None:
    fm = load_duck("flock-hello").frontmatter
    duck = trim_contract(fm, describe(MIXED["duck"]))
    arm = trim_contract(fm, describe(MIXED["arm"]))
    assert "say" in duck.verbs.allow and "say" not in arm.verbs.allow
    assert "move_joints" in arm.verbs.allow and "move_joints" not in duck.verbs.allow
    assert "report_state" in duck.verbs.allow and "report_state" in arm.verbs.allow
    assert duck.requires == ["report_state"] and arm.requires == ["report_state"]
    assert fm.verbs.allow == load_duck("flock-hello").frontmatter.verbs.allow, "the file is not"


def test_a_trimmed_contract_is_still_a_contract() -> None:
    """Rebuilt through the model, so every cross-field rule in the schema still holds."""
    text = (
        "---\nduck: 1\nname: t\ndescription: d\nverbs:\n  allow: [stop, say, move_joints]\n"
        "  confirm: [move_joints]\nsuccess: [x]\nrequires: [say]\n---\n# Task\nx\n"
    )
    fm = parse_duck_text(text).frontmatter
    duck = trim_contract(fm, describe(MIXED["duck"]))
    assert duck.verbs.confirm == [], "a gated verb this body lacks is not gated on it"
    assert duck.requires == ["say"]
    arm = trim_contract(fm, describe(MIXED["arm"]))
    assert arm.verbs.confirm == ["move_joints"] and arm.requires == []


def test_a_body_that_can_do_none_of_it_still_gets_stop() -> None:
    text = (
        "---\nduck: 1\nname: t\ndescription: d\nverbs:\n  allow: [move_joints]\n"
        "success: [x]\n---\n# Task\nx\n"
    )
    trimmed = trim_contract(parse_duck_text(text).frontmatter, describe(MIXED["duck"]))
    assert trimmed.verbs.allow == ["stop"]


def test_what_no_body_can_do_is_refused_and_what_one_can_is_not() -> None:
    text = (
        "---\nduck: 1\nname: t\ndescription: d\nverbs:\n  allow: [stop, kick, move_joints]\n"
        "success: [x]\nrequires: [kick]\n---\n# Task\nx\n"
    )
    manifests = {n: describe(s) for n, s in MIXED.items()}
    assert union_problems(parse_duck_text(text), manifests) == []
    nobody = text.replace("requires: [kick]", "requires: [kick, move_joints]").replace(
        "allow: [stop, kick, move_joints]", "allow: [stop, kick, move_joints, grab]"
    )
    assert union_problems(parse_duck_text(nobody), manifests) == []
    lacking = (
        "---\nduck: 1\nname: t\ndescription: d\nverbs:\n  allow: [stop, pick]\n"
        "success: [x]\nrequires: [pick]\n---\n# Task\nx\n"
    )
    only_duck = {"duck": manifests["duck"]}
    assert "requires pick" in union_problems(parse_duck_text(lacking), only_duck)[0]


# ── the outcome ─────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("members", "outcome"),
    [
        ({"a": ("success", "done"), "b": ("success", "done")}, "success"),
        ({"a": ("success", "done"), "b": ("failure", "no")}, "failure"),
        ({"a": ("budget", "out"), "b": ("failure", "no")}, "budget"),
        ({"a": ("infeasible", "too heavy"), "b": ("budget", "out")}, "infeasible"),
        ({"a": ("error", "boom"), "b": ("infeasible", "too heavy")}, "error"),
        # one member raised and the rest were stopped because it did: the cause is the headline
        ({"a": ("aborted", "stopped"), "b": ("error", "boom")}, "error"),
        # a person pressed something: nothing errored and everything aborted
        ({"a": ("aborted", "stopped"), "b": ("aborted", "stopped")}, "aborted"),
        ({}, "error"),
    ],
)
def test_the_worst_outcome_wins(members: dict[str, Any], outcome: str) -> None:
    assert aggregate_outcome(members)[0] == outcome


def test_the_reason_names_every_member_that_did_not_succeed_worst_first() -> None:
    _, reason = aggregate_outcome(
        {"a": ("failure", "no ball"), "b": ("success", "done"), "c": ("aborted", "stopped")}
    )
    assert reason == "c aborted: stopped; a failure: no ball"
    assert not reason.startswith("b "), "a member that succeeded is not in the reason"


def test_success_names_everyone() -> None:
    outcome, reason = aggregate_outcome({"a": ("success", "x"), "b": ("success", "y")})
    assert outcome == "success" and reason == "every member declared success: a, b"


# ── a whole run ─────────────────────────────────────────────────────────────────────────


async def test_two_different_bodies_talk_and_both_declare(tmp_path: Path) -> None:
    result = await _run(tmp_path)
    assert result.outcome == "success", result.reason
    assert result.messages == 2, "each said one thing"
    assert result.notices == 2, "and the runner said when each of them ended"
    assert {r["outcome"] for r in result.per_member.values()} == {"success"}
    assert result.per_member["arm"]["robot"] == "lerobot:mock"


async def test_two_bodies_of_one_kind_are_a_flock_too(tmp_path: Path) -> None:
    """The case the auction could always do and nothing else could: same body, twice."""
    twins = _roster(
        {
            "duck-a": RobotSpec("microduck", "mock", "duck-a"),
            "duck-b": RobotSpec("microduck", "mock", "duck-b"),
        }
    )
    result = await _run(tmp_path, twins)
    assert result.outcome == "success", result.reason
    assert list(result.per_member) == ["duck-a", "duck-b"]


AFTER_HELLO_S = 0.1
"""How long after the duck says hello the arm starts connecting. A scripted check on a mock
body takes a few milliseconds, so with no wait between checks the duck spends all three of
them inside this and gives up on an arm that is about to answer."""


async def test_a_member_that_connects_late_is_still_heard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CI's macOS runners failed two flock tests with "nobody answered after 3 checks": one
    member had told the flock and spent all of the scripted pilot's checks before a slower peer
    had said anything. An arm that connects AFTER_HELLO_S after the duck's hello does that on
    any machine, and the scripted pilot's checks wait on the wall clock now, so the duck still
    hears the arm."""
    from quackd.adapters import factory
    from quackd.flock.bus import InProcessBus

    hello = asyncio.Event()

    def bus_factory(tap: Any) -> InProcessBus:
        bus = InProcessBus(tap=tap)
        publish = bus.publish

        def spied(msg: Any) -> None:
            publish(msg)
            if getattr(msg, "src", None) == "duck":
                hello.set()

        monkeypatch.setattr(bus, "publish", spied)
        return bus

    real = factory.make_adapter

    def late(spec: Any, **kw: Any) -> Any:
        adapter = real(spec, **kw)
        if spec.name == "arm":
            connect = adapter.connect

            async def after_hello(*args: Any, **kwargs: Any) -> Any:
                await asyncio.wait_for(hello.wait(), timeout=30)
                await asyncio.sleep(AFTER_HELLO_S)
                return await connect(*args, **kwargs)

            monkeypatch.setattr(adapter, "connect", after_hello)
        return adapter

    monkeypatch.setattr("quackd.flock.pilots.make_adapter", late)
    result = await _run(tmp_path, bus_factory=bus_factory)
    assert result.outcome == "success", result.reason
    assert result.per_member["duck"]["reason"].endswith("heard back from arm")


async def test_the_artifacts_are_what_the_docs_say(tmp_path: Path) -> None:
    result = await _run(tmp_path)
    kinds = [line["kind"] for line in _flock_lines(result)]
    assert kinds[0] == "flock_start" and kinds[-1] == "flock_end"
    assert kinds.count("member_end") == 2
    talks = [
        line["msg"] for line in _flock_lines(result) if line.get("msg", {}).get("kind") == "TALK"
    ]
    assert len(talks) == 4, "two between the members, two from the runner"
    assert all("t" in line and "sim_t" not in line for line in _flock_lines(result)), (
        "a pilot flock runs on wall-clock seconds and must not stamp them as sim time"
    )
    for name in ("duck", "arm"):
        member = result.run_dir / "ducks" / name
        assert (member / "transcript.jsonl").exists()
        assert not (member / "summary.json").exists(), "a member is not a solo run"
    summary = _summary(result)
    assert summary["flock"] == {"members": ["duck", "arm"], "method": "pilots", "name": None}
    assert summary["transport"] == "mock"
    assert summary["messages"] == 2 and summary["notices"] == 2
    assert set(summary["per_member"]["duck"]) == {
        "outcome",
        "reason",
        "steps",
        "llm_calls",
        "usage",
        # what this member cost and how long it took, beside the tokens it already reported
        "cost_usd",
        "wall_s",
        "llm_latency_s",
        "provider",
        "model",
        "robot",
        "run_dir",
        "log_dropped",
    }
    start = _flock_lines(result)[0]
    assert start["clock"] == "wall"
    assert start["contracts"]["arm"]["allow"] == ["report_state", "observe", "stop", "move_joints"]


async def test_a_mixed_flock_reports_its_backends_as_mixed(tmp_path: Path) -> None:
    roster = _roster(
        {
            "duck": RobotSpec("microduck", "sim2d", "duck"),
            "arm": RobotSpec("lerobot", "mock", "arm"),
        }
    )
    assert _summary(await _run(tmp_path, roster))["transport"] == "mixed"


async def test_the_runners_notice_reaches_the_survivors(tmp_path: Path) -> None:
    """A pilot waiting on somebody who has stopped has to be told, or it waits forever."""
    result = await _run(tmp_path)
    heard = [
        line["msg"]
        for line in _flock_lines(result)
        if line.get("msg", {}).get("kind") == "TALK" and line["msg"]["src"] == "flock"
    ]
    assert len(heard) == 2
    assert all("declared success" in m["text"] for m in heard)


# ── when one member does not come back ──────────────────────────────────────────────────


class _Boom:
    """A transport that will not connect."""

    name = "microduck"
    backend = "mock"

    async def connect(self) -> None:
        raise RuntimeError("no socket")

    async def close(self) -> None:
        return None

    def now(self) -> float:
        return 0.0


async def test_the_first_exception_stops_the_others_and_names_itself(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from quackd.adapters import factory

    real = factory.make_adapter

    def maybe_boom(spec: Any, **kw: Any) -> Any:
        return _Boom() if spec.name == "arm" else real(spec, **kw)

    monkeypatch.setattr(factory, "make_adapter", maybe_boom)
    monkeypatch.setattr("quackd.flock.pilots.make_adapter", maybe_boom)
    result = await _run(tmp_path)
    assert result.outcome == "error", result.reason
    assert result.per_member["arm"]["outcome"] == "error"
    assert "no socket" in result.per_member["arm"]["reason"]
    assert "arm" in result.reason
    others = [n for n in result.per_member if n != "arm"]
    assert all(result.per_member[n]["outcome"] in ("aborted", "success") for n in others)
    assert (result.run_dir / "ducks" / "arm" / "transcript.jsonl").exists()


async def test_a_provider_that_raises_is_that_members_error(tmp_path: Path) -> None:
    class _Angry:
        name, model, supports_vision = "angry", "none", False

        async def step(self, system: str, history: list[Exchange], tools: list[Any]) -> Any:
            raise RuntimeError("429 from the vendor")

    roster = _roster()
    providers: dict[str, Any] = _providers(roster)
    providers["arm"] = _Angry()
    result = await _run(tmp_path, roster, providers=providers, price="in=1000000,out=0")
    assert result.outcome == "error"
    assert "429 from the vendor" in result.per_member["arm"]["reason"]
    # A member that raised never reaches its own `return RunResult(...)`, and the flock used
    # to throw away the wall clock, the model seconds and the bill that member had already
    # measured. A flock of priced models then read `cost_usd: null` on the strength of one of
    # them being interrupted, which is a bill going missing rather than a bill being unknown.
    for name in ("duck", "arm"):
        member = result.per_member[name]
        assert member["cost_usd"] is not None, f"{name} measured a bill before it stopped"
        assert member["wall_s"] is not None and member["llm_latency_s"] is not None
    assert result.cost_usd is not None


async def test_the_kill_switch_reaches_every_member(tmp_path: Path) -> None:
    """One event, every executor: Ctrl-C stops every body, not the one in front.

    A pilot mid-verb is cancelled and sent a stop; one waiting on its model notices at its
    next turn, exactly as a solo run does."""

    roster = _roster()
    master = asyncio.Event()
    master.set()  # already pressed: mock bodies finish faster than any sleep could race
    result = await _run(
        tmp_path,
        roster,
        providers={n: FakeProvider(strategy=_always_report) for n in roster},
        abort=master,
    )
    assert result.outcome == "aborted", result.reason
    assert {r["outcome"] for r in result.per_member.values()} == {"aborted"}
    assert "kill switch" in result.reason
    assert all(r["steps"] == 0 for r in result.per_member.values()), "nothing moved"


# ── the flags ───────────────────────────────────────────────────────────────────────────


async def test_dry_run_reaches_every_member(tmp_path: Path) -> None:
    result = await _run(tmp_path, dry_run=True)
    assert _summary(result)["dry_run"] is True
    assert result.outcome == "success", result.reason


async def test_max_steps_applies_to_each_member(tmp_path: Path) -> None:
    roster = _roster()
    providers = {n: FakeProvider(strategy=_always_report) for n in roster}
    result = await _run(tmp_path, roster, providers=providers, max_steps=1)
    assert result.outcome == "budget", result.reason
    assert all(r["steps"] <= 1 for r in result.per_member.values())


def _always_report(obs: Any, step: int, history: list[Exchange]) -> ToolCall:
    return ToolCall(name="report_state", arguments={})


async def test_memory_is_keyed_by_the_name_or_the_body_and_can_be_off(tmp_path: Path) -> None:
    roster = _roster()
    memories = {name: RobotMemory(name, tmp_path / "mem") for name in roster}
    await _run(tmp_path, roster, memories=memories)
    assert (tmp_path / "mem" / "duck.jsonl").exists()
    assert (tmp_path / "mem" / "arm.jsonl").exists()
    off = tmp_path / "mem2"
    await _run(tmp_path, _roster(), memories=None)
    assert not off.exists()


async def test_a_flock_of_one_or_of_nine_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="2 to 8 members"):
        await _run(tmp_path, _roster({"duck": MIXED["duck"]}))
    big = {f"duck-{i}": RobotSpec("microduck", "mock", f"duck-{i}") for i in range(MAX_MEMBERS + 1)}
    with pytest.raises(ValueError, match="2 to 8 members"):
        await _run(tmp_path, _roster(big))


async def test_a_member_with_no_pilot_is_refused(tmp_path: Path) -> None:
    roster = _roster()
    with pytest.raises(ValueError, match="no pilot for arm"):
        await _run(tmp_path, roster, providers={"duck": FakeProvider()})


async def test_a_task_no_body_can_do_refuses_before_anything_connects(tmp_path: Path) -> None:
    """Two arms asked to kick. Neither can, so nobody connects and nothing is written."""
    text = (
        "---\nduck: 1\nname: impossible\ndescription: d\nverbs:\n"
        "  allow: [stop, kick]\nsuccess: [x]\nrequires: [kick]\n"
        "flock:\n  members: [left, right]\n  allocation:\n    method: pilots\n"
        "---\n# Task\nx\n"
    )
    roster = _roster(
        {
            "left": RobotSpec("lerobot", "mock", "left"),
            "right": RobotSpec("lerobot", "mock", "right"),
        }
    )
    with pytest.raises(ValueError, match="requires kick"):
        await asyncio.wait_for(
            run_pilot_flock(
                parse_duck_text(text), roster, providers=_providers(roster), runs_dir=tmp_path
            ),
            timeout=30,
        )
    # checked off the event loop: the point is that the directory is empty, not when
    made = await asyncio.to_thread(lambda: list(tmp_path.glob("*flock*")))
    assert not made, "nothing on disk to explain away"


# ── the views ───────────────────────────────────────────────────────────────────────────


async def test_each_member_gets_its_own_view_and_the_flock_gets_one(tmp_path: Path) -> None:
    seen: dict[str, list[str]] = {}

    def view(name: str) -> Any:
        seen.setdefault(name, [])
        return lambda event: seen[name].append(event.kind)

    await _run(tmp_path, view=view)
    assert set(seen) == {"duck", "arm", "flock"}
    assert "run_start" in seen["duck"] and "run_start" not in seen["flock"]
    assert seen["flock"] == ["talk", "talk"], "the flock view carries the runner's own notices"


def test_usage_is_summed_across_the_members(tmp_path: Path) -> None:
    class _Costly:
        name, model, supports_vision = "costly", "none", False

        async def step(self, system: str, history: list[Exchange], tools: list[Any]) -> Any:
            return ProviderTurn(
                tool_calls=[ToolCall(id="x", name="declare_success", arguments={"reason": "ok"})],
                usage=Usage(input_tokens=10, output_tokens=1),
                stop_reason="tool_use",
            )

    roster = _roster()
    result = asyncio.run(
        asyncio.wait_for(
            run_pilot_flock(
                load_duck("flock-hello"),
                roster,
                providers={n: _Costly() for n in roster},
                runs_dir=tmp_path,
            ),
            timeout=60,
        )
    )
    assert result.usage.input_tokens == 20 and result.usage.output_tokens == 2
    assert result.llm_calls == 2


async def test_a_cancelled_flock_records_every_member_rather_than_crashing(
    tmp_path: Path,
) -> None:
    """The second Ctrl-C: `asyncio.run` cancels the task. A member that never returned a
    result used to leave a `KeyError` where the interrupt should have been."""

    class _Slow:
        name, model, supports_vision = "slow", "none", False

        async def step(self, system: str, history: list[Exchange], tools: list[Any]) -> Any:
            await asyncio.sleep(30)
            raise AssertionError("never")

    roster = _roster()
    task = asyncio.ensure_future(_run(tmp_path, roster, providers={n: _Slow() for n in roster}))
    await asyncio.sleep(0.4)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    # the record survives the interrupt, which is the whole point of writing it first
    run_dir = await asyncio.to_thread(lambda: sorted(tmp_path.glob("*flock-hello"))[-1])
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["outcome"] == "aborted"
    assert set(summary["per_member"]) == set(roster)
    assert all(m["outcome"] == "aborted" for m in summary["per_member"].values())


# ── the rest pose ───────────────────────────────────────────────────────────────────────

ARM_REST = {
    "shoulder_pan": 12.0,
    "shoulder_lift": -80.0,
    "elbow_flex": 78.0,
    "wrist_flex": 6.0,
    "wrist_roll": 0.0,
}


def _built(monkeypatch: pytest.MonkeyPatch, member: str, **kwargs: Any) -> dict[str, Any]:
    """Keep every adapter a run builds, and hand `member`'s `make()` some extra kwargs.

    The same seam the connect failure above uses: `run_pilot_flock` builds its adapters
    itself, from a roster that carries no rest pose, so this is where one is put on one
    member's body and where the object is caught to read afterwards."""
    from quackd.adapters import factory

    real = factory.make_adapter
    built: dict[str, Any] = {}

    def capture(spec: Any, **kw: Any) -> Any:
        built[spec.name] = real(spec, **(kw | kwargs)) if spec.name == member else real(spec, **kw)
        return built[spec.name]

    monkeypatch.setattr("quackd.flock.pilots.make_adapter", capture)
    return built


async def test_a_member_with_a_rest_pose_is_put_down_before_its_torque_is_released(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A LeRobot arm goes limp the moment it is disconnected, so a member that ended with
    its arm in the air dropped it on the table. The move belongs between the stop, which
    holds the arm where it is, and the close, which is what lets go: that window is the only
    one in which putting the arm down changes whether it falls.

    Each member tears itself down, so this has to hold for every one of them and not only
    for a solo run."""
    built = _built(monkeypatch, "arm", rest_pose=ARM_REST)
    result = await _run(tmp_path)
    assert result.outcome == "success", result.reason

    arm = built["arm"].transport
    # the first `rest` is the start of the run, so a member acts from the same arm every
    # time; the second is the teardown, in the window the docstring names
    assert arm.sequence == ["rest", "stop", "rest", "close"], arm.sequence
    assert arm.actions == [dict(ARM_REST)], "one move, and it really went there"
    assert arm.torque is False, "an arm at its rest pose may be let go of"
    assert arm.close_note is None, "so there is nothing to warn about"


async def test_a_dry_run_never_moves_the_arm_to_its_rest_pose(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--dry-run` promises that nothing reaches the body. The rest move is sent outside the
    executor, which is where the dry-run gate lives, so it has to refuse for itself."""
    built = _built(monkeypatch, "arm", rest_pose=ARM_REST)
    result = await _run(tmp_path, dry_run=True)
    assert result.outcome == "success", result.reason

    arm = built["arm"].transport
    assert "rest" not in arm.sequence, arm.sequence
    assert arm.actions == [], "nothing was driven anywhere"
    assert arm.joints["shoulder_lift"] == -90.0, "still where the mock was built"
