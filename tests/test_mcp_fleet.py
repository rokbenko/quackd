"""A fleet over MCP: eight robot_* tools, one executor, budget and heartbeat per robot.

Driven in-process by the SDK's own client over memory streams, exactly like the one-robot
tests. Two bodies: a simulated Microduck and a mocked Reachy Mini, each with its own
manifest, contract and budget.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from typing import Any

import pytest
from mcp.client.session import ClientSession
from mcp.shared.memory import create_client_server_memory_streams

from quackd.adapters.factory import RobotSpec, make_adapter
from quackd.mcp_server import TOOL_NAMES, Fleet, build_fleet_server
from quackd.transport.base import TransportError
from quackd.transport.mock import MockTransport


def two_robots(*, reachy_first: bool = False) -> dict[str, Any]:
    duck = make_adapter(RobotSpec("microduck", "sim2d", "duck"), seed=1)
    reachy = make_adapter(RobotSpec("reachy_mini", "mock", "reachy"))
    return {"reachy": reachy, "duck": duck} if reachy_first else {"duck": duck, "reachy": reachy}


@contextlib.asynccontextmanager
async def connected(
    robots: dict[str, Any] | None = None, **kwargs: Any
) -> AsyncIterator[tuple[ClientSession, Fleet]]:
    server, fleet = build_fleet_server(robots or two_robots(), heartbeat_period_s=0.05, **kwargs)
    async with create_client_server_memory_streams() as (client_streams, server_streams):
        low = server._lowlevel_server
        task = asyncio.create_task(
            low.run(server_streams[0], server_streams[1], low.create_initialization_options())
        )
        try:
            async with ClientSession(client_streams[0], client_streams[1]) as client:
                await client.initialize()
                yield client, fleet
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task


def _data(result: Any) -> dict[str, Any]:
    assert not result.is_error, result
    assert result.structured_content is not None
    return result.structured_content


async def test_robot_list_names_every_robot_and_the_default() -> None:
    async with connected() as (client, fleet):
        tools = await client.list_tools()
        assert {t.name for t in tools.tools} == set(TOOL_NAMES)
        listed = _data(await client.call_tool("robot_list", {}))
        assert listed["default"] == "duck" == fleet.default
        rows = {r["name"]: r for r in listed["robots"]}
        assert set(rows) == {"duck", "reachy"}
        assert rows["duck"]["adapter"] == "microduck" and rows["duck"]["backend"] == "sim2d"
        assert rows["duck"]["default"] is True and rows["reachy"]["default"] is False
        reachy = rows["reachy"]
        assert (reachy["vendor"], reachy["model"]) == ("pollen-robotics", "reachy-mini")
        assert reachy["embodiment"] == "stationary_head" and reachy["mobility"] == "none"
        assert reachy["manifest_id"] == "reachy" and len(reachy["digest"]) == 16
        assert reachy["healthy"] is True and reachy["contract"] is None
        # the fleet instructions name every robot and the default
        from quackd.mcp_server import _instructions

        prompt = _instructions(fleet)
        assert "duck, reachy" in prompt and "the default, duck" in prompt
        assert "duck_" not in prompt, "the prompt must not name tools that no longer exist"


async def test_verbs_come_from_each_robots_own_manifest() -> None:
    async with connected() as (client, _fleet):
        reachy = _data(await client.call_tool("robot_list_verbs", {"robot": "reachy"}))
        names = {v["name"] for v in reachy["verbs"]}
        assert {"observe", "gaze", "express", "say", "search_scan", "stop"} <= names
        assert not names & {"kick", "go_to", "move", "walk", "quack"}
        assert reachy["robot"] == "reachy" and reachy["manifest_id"] == "reachy"
        duck = _data(await client.call_tool("robot_list_verbs", {"robot": "duck"}))
        assert {"kick", "go_to", "quack"} <= {v["name"] for v in duck["verbs"]}
        # no robot named: the default
        default = _data(await client.call_tool("robot_list_verbs", {}))
        assert default["robot"] == "duck"


async def test_refusals_are_data_not_errors() -> None:
    async with connected() as (client, _fleet):
        res = _data(await client.call_tool("robot_run_verb", {"robot": "reachy", "verb": "kick"}))
        assert res["ok"] is False and "kick" in res["summary"]
        nobody = _data(await client.call_tool("robot_run_verb", {"robot": "cat", "verb": "stop"}))
        assert nobody["ok"] is False and "unknown robot 'cat'" in nobody["error"]
        assert "duck, reachy" in nobody["error"]
        gone = await client.call_tool("robot_observe", {"robot": "cat"})
        assert [c.type for c in gone.content] == ["text"]


async def test_contracts_and_budgets_are_per_robot() -> None:
    async with connected() as (client, fleet):
        assert _data(await client.call_tool("robot_run_verb", {"verb": "kick", "robot": "duck"}))[
            "ok"
        ]
        loaded = _data(
            await client.call_tool("robot_load_duckfile", {"path": "hello-world", "robot": "duck"})
        )
        assert loaded["ok"] and loaded["robot"] == "duck" and "Task" in loaded["instructions"]
        refused = _data(await client.call_tool("robot_run_verb", {"verb": "kick", "robot": "duck"}))
        assert refused["ok"] is False and "allowlist" in refused["summary"]
        # the other robot did not adopt anything: no allowlist, and the default budget it
        # started with rather than the duck's. A contractless session is not an unlimited
        # one — that used to mean an MCP client had uncounted control of a physical robot.
        reachy = fleet.sessions["reachy"]
        assert reachy.duck is None
        assert reachy.executor.budget is not None
        assert reachy.executor.budget.limits != fleet.sessions["duck"].executor.budget.limits
        for _ in range(6):
            ok = _data(
                await client.call_tool("robot_run_verb", {"verb": "observe", "robot": "reachy"})
            )
            assert ok["ok"], ok
        rows = {r["name"]: r for r in _data(await client.call_tool("robot_list", {}))["robots"]}
        assert rows["duck"]["contract"] == "hello-world" and rows["reachy"]["contract"] is None
        # hello-world allows 5 steps on the duck
        results = [
            _data(await client.call_tool("robot_run_verb", {"verb": "quack", "robot": "duck"}))
            for _ in range(6)
        ]
        assert all(r["ok"] for r in results[:5]) and "budget" in results[5]["summary"]


async def test_load_duckfile_checks_requires_against_that_robots_manifest() -> None:
    async with connected() as (client, fleet):
        res = _data(
            await client.call_tool(
                "robot_load_duckfile", {"path": "find-and-kick", "robot": "reachy"}
            )
        )
        assert res["ok"] is False
        assert "requires kick, but reachy (reachy-mini) does not provide it" in res["error"]
        assert fleet.sessions["reachy"].duck is None
        # the same file is fine on the duck, and a flock duck is refused everywhere
        assert _data(
            await client.call_tool(
                "robot_load_duckfile", {"path": "find-and-kick", "robot": "duck"}
            )
        )["ok"]
        flock = _data(
            await client.call_tool("robot_load_duckfile", {"path": "flock-kick", "robot": "duck"})
        )
        assert flock["ok"] is False and "flock" in flock["error"]
        # a Reachy contract loads on the Reachy
        spotter = _data(
            await client.call_tool(
                "robot_load_duckfile", {"path": "reachy-spotter", "robot": "reachy"}
            )
        )
        assert spotter["ok"], spotter


async def test_say_and_observe_go_through_each_executor() -> None:
    async with connected() as (client, fleet):
        said = _data(
            await client.call_tool("robot_say", {"text": "hello there", "robot": "reachy"})
        )
        assert said["ok"], said
        mock = fleet.sessions["reachy"].transport.transport  # the adapter's backend
        assert mock.speech and mock.speech[0][0] == "hello there"

        seen = await client.call_tool("robot_observe", {"robot": "reachy"})
        kinds = [c.type for c in seen.content]
        assert "image" in kinds and "text" in kinds
        text = next(c for c in seen.content if c.type == "text").text
        assert text.startswith("reachy camera:")
        assert fleet.sessions["reachy"].calls == 2  # say and observe, both through the executor
        assert fleet.sessions["reachy"].frames == 1
        assert fleet.sessions["duck"].calls == 0

        # a body without a sound intent refuses to say anything, as data
        session = fleet.sessions["reachy"]
        assert session.manifest is not None
        session.manifest = session.manifest.model_copy(update={"intents": ["gaze"]})
        mute = _data(await client.call_tool("robot_say", {"text": "x", "robot": "reachy"}))
        assert mute["ok"] is False and "no sound intent" in mute["summary"]


async def test_dry_run_still_observes_but_moves_nothing() -> None:
    async with connected(dry_run=True) as (client, fleet):
        seen = await client.call_tool("robot_observe", {"robot": "duck"})
        assert {c.type for c in seen.content} == {"text", "image"}  # read-only verbs run
        moved = _data(
            await client.call_tool("robot_run_verb", {"verb": "move", "params": {"vx": 0.2}})
        )
        assert moved["ok"] and moved["data"].get("dry_run") is True
        assert fleet.sessions["duck"].transport.world.steps == 0


async def test_a_tool_without_a_robot_acts_on_the_default() -> None:
    """0.4 kept eight duck_* aliases pinned to the default robot and promised to remove them
    in 0.5. They are gone; omitting `robot` is how you address the default now."""
    async with connected(two_robots(reachy_first=True)) as (client, fleet):
        assert fleet.default == "duck"  # not the first declared: the first Microduck
        listed = _data(await client.call_tool("robot_list_verbs", {}))
        assert listed["robot"] == "duck"
        quack = _data(
            await client.call_tool("robot_run_verb", {"verb": "quack", "params": {"text": "hello"}})
        )
        assert quack["ok"] and fleet.sessions["duck"].transport.world.quacks
        assert fleet.sessions["reachy"].calls == 0
        tools = {t.name for t in (await client.list_tools()).tools}
        assert not [n for n in tools if n.startswith("duck_")]
        assert tools == set(TOOL_NAMES)


async def test_a_lone_reachy_is_its_own_default_and_quack_is_refused() -> None:
    reachy = make_adapter(RobotSpec("reachy_mini", "mock", "reachy"))
    async with connected({"reachy": reachy}) as (client, fleet):
        assert fleet.default == "reachy"
        quack = _data(await client.call_tool("robot_run_verb", {"verb": "quack"}))
        assert quack["ok"] is False  # a Microduck verb on a head: a rule, not a crash
        assert _data(await client.call_tool("robot_run_verb", {"verb": "stop"}))["ok"]


class _Dead:
    name = "mock"

    def __init__(self) -> None:
        self.closed = False

    async def connect(self) -> None:
        raise TransportError("no robot answered")

    async def stop(self) -> None:
        pass

    async def close(self) -> None:
        self.closed = True

    def now(self) -> float:
        return 0.0

    async def heartbeat(self) -> None:
        pass


async def test_connect_is_fail_fast_and_closes_what_did_connect() -> None:
    first = MockTransport()
    _server, fleet = build_fleet_server({"a": first, "b": _Dead()}, heartbeat_period_s=0.05)
    with pytest.raises(TransportError, match="no robot answered"):
        await fleet.connect_all()
    assert first.connected is False  # connected, then closed again
    assert fleet.default == "a"


def test_build_needs_a_robot_and_a_known_default() -> None:
    with pytest.raises(ValueError, match="at least one robot"):
        build_fleet_server({})
    with pytest.raises(ValueError, match="default robot"):
        build_fleet_server({"a": MockTransport()}, default="zz")


@pytest.mark.parametrize("spec", ["open_duck:mock", "microduck:mock", "reachy_mini:mock"])
async def test_a_camera_robot_that_is_not_the_simulator_gets_a_detector(spec: str) -> None:
    """The 0.5 fix landed in `quackd run` and missed this entry point.

    `build_fleet_server` still keyed the detector on the backend being `sim2d`, so every
    hardware body over MCP fetched frames, detected nothing because nothing was detecting,
    and reported that it could not see. The decision now happens after connect, against
    what the robot said it has."""
    robot = make_adapter(spec)
    async with connected({"r": robot}) as (client, fleet):
        session = fleet.sessions["r"]
        assert robot.manifest is not None and "camera" in robot.manifest.sensors
        assert session.detector is not None, f"{spec} runs blind over MCP"
        assert session.executor.detector is session.detector
        # and the verbs that need one no longer refuse
        res = _data(await client.call_tool("robot_run_verb", {"verb": "search_scan"}))
        assert "needs a detector" not in res["summary"]


def test_the_detector_policy_is_the_camera_and_nothing_else() -> None:
    """Both entry points call one function so they cannot drift again. Every body that can
    connect offline has a camera, so the two negative cases are pinned here: a body with no
    camera gets nothing, and an explicit `--detector` is never overruled."""
    from quackd.perception import detector_for
    from quackd.perception.color_blob import ColorBlobDetector

    assert detector_for(["camera", "imu"]) is not None
    assert detector_for(["joint_state"]) is None  # lerobot:real
    assert detector_for(["odometry"]) is None  # rosbridge:ws before it connects
    mine = ColorBlobDetector()
    assert detector_for(["camera"], mine) is mine
    assert detector_for(["odometry"], mine) is mine
