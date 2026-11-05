"""`tell`, `TALK`, and what a pilot is told about the others.

Two `AgentLoop`s on one bus is the smallest real pilot flock, and it is what most of this
file runs: no runner, no registry, just the link the loop was handed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError

from quackd.adapters.factory import RobotSpec, describe
from quackd.agent.loop import AgentLoop, RunConfig
from quackd.agent.providers.base import Exchange, Observation, ProviderTurn, ToolCall, Usage
from quackd.agent.transcript import Transcript
from quackd.duckfile.parser import parse_duck_text
from quackd.flock.bus import InProcessBus
from quackd.flock.messages import FlockMessage, TalkMsg
from quackd.flock.talk import FLOCK_SRC, FlockLink, Peer, make_links, notice
from quackd.transport.mock import MockTransport

DUCK = (
    "---\nduck: 1\nname: talker\ndescription: d\nverbs:\n  allow: [report_state, stop]\n"
    "success: [said hello]\n---\n# Task\nSay hello to the others.\n"
)


def _duck() -> Any:
    return parse_duck_text(DUCK)


def _peer(name: str, spec: str = "microduck:mock") -> Peer:
    adapter, backend = spec.split(":")
    return Peer(name, spec, describe(RobotSpec(adapter, backend, name)))


class _Script:
    """A provider that plays a fixed list of tool calls and records what it was told."""

    name = "fake"
    llm_id = "scripted"
    supports_vision = False

    def __init__(self, calls: list[ToolCall]) -> None:
        self.calls = calls
        self.systems: list[str] = []
        self.observations: list[Observation] = []
        self.i = 0

    async def step(
        self, system: str, history: list[Exchange], tools: list[dict[str, Any]]
    ) -> ProviderTurn:
        self.systems.append(system)
        self.observations.append(history[-1].observation)
        self.tools = [t["name"] for t in tools]
        call = self.calls[min(self.i, len(self.calls) - 1)]
        self.i += 1
        return ProviderTurn(
            tool_calls=[call.model_copy(update={"id": f"s-{self.i}"})],
            usage=Usage(),
            stop_reason="tool_use",
        )


def _config(
    name: str, calls: list[ToolCall], link: Any, run_dir: Path, **kw: Any
) -> tuple[RunConfig, _Script]:
    provider = _Script(calls)
    run_dir.mkdir(parents=True, exist_ok=True)
    cfg = RunConfig(
        duck=_duck(),
        provider=provider,
        transport=MockTransport(),
        run_dir=run_dir,
        link=link,
        summary_file=False,
        **kw,
    )
    return cfg, provider


VERDICT = ToolCall(name="assess_task", arguments={"verdict": "feasible", "reason": "fine"})
DONE = ToolCall(name="declare_success", arguments={"reason": "said hello"})


def _records(run_dir: Path) -> list[dict[str, Any]]:
    return Transcript.read(run_dir / "transcript.jsonl")


# ── the message ─────────────────────────────────────────────────────────────────────────


def test_a_talk_message_round_trips_through_the_union() -> None:
    adapter = TypeAdapter(FlockMessage)
    message = TalkMsg(t=1.0, src="duck-a", task_id="t", to="arm", text="hello")
    assert adapter.validate_python(adapter.dump_python(message, mode="json")) == message


def test_a_talk_message_has_to_say_something_and_not_too_much() -> None:
    with pytest.raises(ValidationError):
        TalkMsg(t=0.0, src="a", task_id="t", text="")
    with pytest.raises(ValidationError):
        TalkMsg(t=0.0, src="a", task_id="t", text="x" * 401)


# ── the link ────────────────────────────────────────────────────────────────────────────


def _pair() -> tuple[InProcessBus, dict[str, FlockLink]]:
    bus = InProcessBus()
    peers = {"duck": _peer("duck"), "arm": _peer("arm", "lerobot:mock")}
    return bus, make_links(["duck", "arm"], peers, bus=bus, task_id="t", now=lambda: 0.0)


def test_what_one_pilot_says_the_other_hears_and_the_sender_does_not() -> None:
    _bus, links = _pair()
    links["duck"].send("all", "I have the ball")
    assert links["duck"].drain() == []
    assert links["arm"].drain() == [{"from": "duck", "to": None, "text": "I have the ball"}]
    assert links["arm"].drain() == [], "a message is heard once"


def test_a_message_addressed_to_one_member_is_not_heard_by_a_third() -> None:
    bus = InProcessBus()
    peers = {n: _peer(n) for n in ("duck", "arm", "cart")}
    links = make_links(["duck", "arm", "cart"], peers, bus=bus, task_id="t", now=lambda: 0.0)
    links["duck"].send("arm", "you spot")
    assert links["arm"].drain() == [{"from": "duck", "to": "arm", "text": "you spot"}]
    assert links["cart"].drain() == []


def test_the_sender_is_told_which_names_exist() -> None:
    _bus, links = _pair()
    with pytest.raises(ValueError, match="no member called 'ghost'; this flock is arm"):
        links["duck"].send("ghost", "hello")
    with pytest.raises(ValueError, match="you are duck"):
        links["duck"].send("duck", "hello")
    with pytest.raises(ValueError, match="nothing to say"):
        links["duck"].send("all", "   ")


def test_a_message_is_collapsed_and_capped() -> None:
    _bus, links = _pair()
    sent = links["duck"].send("all", "  two   spaces\nand a newline  ")
    assert sent.text == "two spaces and a newline"
    assert len(links["duck"].send("all", "x" * 500).text) == 400


def test_a_duplicate_delivery_is_heard_once() -> None:
    """The MQTT bus is at-least-once, and one sentence twice reads as a peer repeating itself."""
    bus = InProcessBus()
    peers = {"duck": _peer("duck"), "arm": _peer("arm")}
    links = make_links(["duck", "arm"], peers, bus=bus, task_id="t", now=lambda: 0.0)
    message = TalkMsg(t=1.0, src="duck", task_id="t", to=None, text="hello")
    bus.publish(message)
    bus.publish(message)
    assert len(links["arm"].drain()) == 1


def test_the_runners_notice_comes_from_the_flock_and_not_from_a_member() -> None:
    bus, links = _pair()
    notice(bus, task_id="t", t=1.0, text="duck declared success: said hello")
    heard = links["arm"].drain()
    assert heard == [{"from": FLOCK_SRC, "to": None, "text": "duck declared success: said hello"}]


def test_the_prompt_section_names_every_peer_and_its_body() -> None:
    _bus, links = _pair()
    text = links["duck"].prompt_section()
    assert "## Your flock" in text
    assert "You are `duck`, one of 2 pilots" in text
    assert "`arm` (lerobot:mock)" in text
    assert "lerobot-so101" in text, "a peer is described by its own datasheet"
    assert "gripper" in text
    assert "`duck`" not in text.split("The others:")[1], "you are not your own peer"
    assert "YOUR part" in text and "declare_success" in text
    assert links["duck"].describe() == {"me": "duck", "members": ["arm", "duck"]}


def test_a_flock_of_one_has_nothing_to_say() -> None:
    bus = InProcessBus()
    link = FlockLink("duck", [], bus=bus, task_id="t", now=lambda: 0.0)
    assert link.prompt_section() == ""


# ── the loop ────────────────────────────────────────────────────────────────────────────


async def test_a_tell_lands_in_the_peers_next_observation(tmp_path: Path) -> None:
    _bus, links = _pair()
    duck_cfg, _ = _config(
        "duck",
        [VERDICT, ToolCall(name="tell", arguments={"to": "all", "text": "I have the ball"}), DONE],
        links["duck"],
        tmp_path / "duck",
    )
    arm_cfg, arm_provider = _config(
        "arm",
        [VERDICT, ToolCall(name="report_state", arguments={}), DONE],
        links["arm"],
        tmp_path / "arm",
    )
    await AgentLoop(duck_cfg).run()
    await AgentLoop(arm_cfg).run()
    heard = [o for o in arm_provider.observations if "Messages from your flock" in o.text]
    assert heard, "the arm never saw what the duck said"
    assert "duck -> all: I have the ball" in heard[0].text
    assert heard[0].features["inbox"] == [{"from": "duck", "to": None, "text": "I have the ball"}]
    assert heard[0].features["flock"] == {"me": "arm", "members": ["duck", "arm"]}


async def test_a_tell_costs_no_step_and_is_recorded_as_talk(tmp_path: Path) -> None:
    _bus, links = _pair()
    cfg, _ = _config(
        "duck",
        [VERDICT, ToolCall(name="tell", arguments={"to": "arm", "text": "you spot"}), DONE],
        links["duck"],
        tmp_path / "duck",
    )
    result = await AgentLoop(cfg).run()
    assert result.outcome == "success"
    assert result.steps == 0, "talking moves nothing"
    assert result.llm_calls == 3, "and still costs a call"
    talk = [r for r in _records(tmp_path / "duck") if r["kind"] == "talk"]
    assert talk == [
        {
            "t": talk[0]["t"],
            "kind": "talk",
            "step": 0,
            "src": "duck",
            "to": "arm",
            "text": "you spot",
            "ok": True,
            "summary": "told arm: you spot",
        }
    ]


async def test_a_tell_to_nobody_is_refused_and_the_run_goes_on(tmp_path: Path) -> None:
    _bus, links = _pair()
    cfg, provider = _config(
        "duck",
        [VERDICT, ToolCall(name="tell", arguments={"to": "ghost", "text": "hello"}), DONE],
        links["duck"],
        tmp_path / "duck",
    )
    result = await AgentLoop(cfg).run()
    assert result.outcome == "success", "a refused message is feedback, not a failure"
    last = provider.observations[-1]
    assert "no member called 'ghost'" in last.text
    assert next(r for r in _records(tmp_path / "duck") if r["kind"] == "talk")["ok"] is False


async def test_the_tell_tool_and_the_flock_section_are_solo_runs_absent(tmp_path: Path) -> None:
    cfg, provider = _config("duck", [VERDICT, DONE], None, tmp_path / "solo")
    await AgentLoop(cfg).run()
    assert "tell" not in provider.tools
    assert "Your flock" not in provider.systems[0]
    assert "inbox" not in provider.observations[0].features
    assert "flock" not in provider.observations[0].features
    start = next(r for r in _records(tmp_path / "solo") if r["kind"] == "run_start")
    assert start["flock"] is None


async def test_a_flock_run_offers_tell_and_carries_the_section(tmp_path: Path) -> None:
    _bus, links = _pair()
    cfg, provider = _config("duck", [VERDICT, DONE], links["duck"], tmp_path / "duck")
    await AgentLoop(cfg).run()
    assert "tell" in provider.tools
    assert "## Your flock" in provider.systems[0]
    start = next(r for r in _records(tmp_path / "duck") if r["kind"] == "run_start")
    assert start["flock"] == {"me": "duck", "members": ["arm", "duck"]}
    assert "tell" in start["tools"]


async def test_talking_still_works_under_dry_run(tmp_path: Path) -> None:
    """A dry run of a flock in which nobody may speak is not a dry run of a flock."""
    _bus, links = _pair()
    cfg, _ = _config(
        "duck",
        [VERDICT, ToolCall(name="tell", arguments={"to": "all", "text": "pretending"}), DONE],
        links["duck"],
        tmp_path / "duck",
        dry_run=True,
    )
    await AgentLoop(cfg).run()
    assert links["arm"].drain() == [{"from": "duck", "to": None, "text": "pretending"}]


async def test_a_member_stopped_by_the_flock_records_why(tmp_path: Path) -> None:
    _bus, links = _pair()
    cfg, _ = _config("duck", [VERDICT, DONE], links["duck"], tmp_path / "duck")
    loop = AgentLoop(cfg)
    links["duck"].abort_reason = "stopped by the flock: arm raised TransportError: no socket"
    loop.executor.abort.set()
    result = await loop.run()
    assert result.outcome == "aborted"
    assert result.reason == "stopped by the flock: arm raised TransportError: no socket"


async def test_a_member_stopped_by_a_person_still_says_kill_switch(tmp_path: Path) -> None:
    _bus, links = _pair()
    cfg, _ = _config("duck", [VERDICT, DONE], links["duck"], tmp_path / "duck")
    loop = AgentLoop(cfg)
    loop.executor.abort.set()
    result = await loop.run()
    assert result.outcome == "aborted" and result.reason == "kill switch"


async def test_a_member_writes_no_summary_of_its_own(tmp_path: Path) -> None:
    """A member's directory must not read as a solo run: the rollup is the flock's."""
    _bus, links = _pair()
    cfg, _ = _config("duck", [VERDICT, DONE], links["duck"], tmp_path / "duck")
    await AgentLoop(cfg).run()
    assert (tmp_path / "duck" / "transcript.jsonl").exists()
    assert not (tmp_path / "duck" / "summary.json").exists()


async def test_a_solo_run_still_writes_its_summary(tmp_path: Path) -> None:
    cfg, _ = _config("duck", [VERDICT, DONE], None, tmp_path / "solo")
    cfg.summary_file = True
    await AgentLoop(cfg).run()
    summary = json.loads((tmp_path / "solo" / "summary.json").read_text(encoding="utf-8"))
    assert summary["outcome"] == "success"
