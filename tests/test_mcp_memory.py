"""`robot_recall` / `robot_remember` over MCP: one memory per robot, keyed adapter:backend."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from quackd.adapters.factory import RobotSpec, make_adapter
from quackd.mcp_server import TOOL_NAMES
from quackd.memory import RobotMemory
from tests.test_mcp_fleet import _data, connected


def two_robots() -> dict[str, Any]:
    """Two distinct bodies, so memory keyed by adapter:backend lands in two files."""
    duck = make_adapter(RobotSpec("microduck", "sim2d", "duck"), seed=1)
    other = make_adapter(RobotSpec("open_duck", "mock", "other"))
    return {"duck": duck, "other": other}


def test_memory_tools_are_registered() -> None:
    assert "robot_recall" in TOOL_NAMES and "robot_remember" in TOOL_NAMES


async def test_remember_then_recall_per_robot(tmp_path: Path) -> None:
    async with connected(two_robots(), memory_dir=tmp_path) as (client, fleet):
        tools = {t.name for t in (await client.list_tools()).tools}
        assert {"robot_recall", "robot_remember"} <= tools
        empty = _data(await client.call_tool("robot_recall", {}))
        assert empty["ok"] and "nothing remembered yet" in empty["summary"]
        saved = _data(
            await client.call_tool(
                "robot_remember", {"text": "the ball hides behind the sofa", "tags": ["place"]}
            )
        )
        assert saved["ok"] and saved["notes"] == 1 and saved["robot"] == "duck"
        again = _data(
            await client.call_tool("robot_remember", {"text": "The ball hides behind the sofa"})
        )
        assert again["notes"] == 1, "same sentence twice is one note"
        recalled = _data(await client.call_tool("robot_recall", {}))
        assert recalled["notes"] == ["the ball hides behind the sofa"]
        # the other body has its own file
        other_robot = next(name for name in fleet.sessions if name != "duck")
        other = _data(await client.call_tool("robot_recall", {"robot": other_robot}))
        assert other["ok"] and other["notes"] == []
        assert fleet.sessions["duck"].memory is not None
        assert fleet.sessions["duck"].memory.path == tmp_path / "microduck-sim2d.jsonl"
        assert fleet.sessions[other_robot].memory is not None
        assert fleet.sessions[other_robot].memory.path != fleet.sessions["duck"].memory.path
    # and it survives the server: a CLI run on the same robot would read the same file
    assert [e.text for e in RobotMemory("microduck:sim2d", tmp_path).notes()] == [
        "the ball hides behind the sofa"
    ]


async def test_memory_off(tmp_path: Path) -> None:
    async with connected(two_robots(), memory=False, memory_dir=tmp_path) as (client, fleet):
        off = _data(await client.call_tool("robot_recall", {}))
        assert not off["ok"] and "off" in off["summary"]
        saved: dict[str, Any] = _data(await client.call_tool("robot_remember", {"text": "x"}))
        assert not saved["ok"]
        assert fleet.sessions["duck"].memory is None
    assert os.listdir(tmp_path) == []


async def test_the_tool_descriptions_only_ask_for_a_recall_when_memory_is_on(
    tmp_path: Path,
) -> None:
    """Both tools stay registered with `--no-memory`, and robot_recall's description still said
    to call it before planning, which a pilot reads as an instruction to spend a turn on an
    answer that says memory is off. With memory off both descriptions say so instead."""

    async def described(**kwargs: Any) -> dict[str, str]:
        async with connected(two_robots(), memory_dir=tmp_path, **kwargs) as (client, _fleet):
            return {t.name: t.description or "" for t in (await client.list_tools()).tools}

    on = await described()
    assert "Call it before planning" in on["robot_recall"]
    assert "Memory is off" not in on["robot_remember"]
    off = await described(memory=False)
    for name in ("robot_recall", "robot_remember"):
        assert "Memory is off" in off[name] and "Call it" not in off[name], off[name]


async def test_the_instructions_only_sell_memory_when_it_is_on(tmp_path: Path) -> None:
    """Both tools stay registered with `--no-memory` and both answer "memory is off", so an
    instruction to call robot_recall early is an instruction to waste a turn."""
    from quackd.mcp_server import _instructions

    async with connected(two_robots(), memory_dir=tmp_path) as (_client, fleet):
        assert "robot_recall" in _instructions(fleet)
    async with connected(two_robots(), memory=False, memory_dir=tmp_path) as (_client, fleet):
        prompt = _instructions(fleet)
        assert "robot_recall" not in prompt and "robot_remember" not in prompt
        assert "robot_list" in prompt  # the rest of the briefing is untouched
    # and the same for a lone robot, whose prompt is a different template
    one = {"duck": two_robots()["duck"]}
    async with connected(one, memory=False, memory_dir=tmp_path) as (_client, fleet):
        assert "robot_recall" not in _instructions(fleet)
