"""The MCP server, driven in-process by the SDK's own client over memory streams.

Proves the tool list, image content, and — the point — that the same executor rules apply
to an MCP session: no contract → safe verbs; contract loaded → allowlist and budgets.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import pytest
from mcp.client.session import ClientSession
from mcp.shared.memory import create_client_server_memory_streams
from PIL import Image

from quackd.mcp_server import DuckSession, build_server
from quackd.transport.base import DEFAULT_CAMERA_NAME, CameraFrame, TransportError
from quackd.transport.mock import MockTransport
from quackd.transport.sim2d import Sim2DTransport
from quackd_lerobot import LeRobotAdapter
from quackd_lerobot.mock import REST, LeRobotMock

TOOLS = {
    # 0.4: six fleet tools
    "robot_list",
    "robot_list_verbs",
    "robot_run_verb",
    # 0.9: the pilot says whether the body can do the task before it moves
    "robot_assess_task",
    "robot_observe",
    "robot_say",
    "robot_load_duckfile",
    # memory between sessions (docs/guides/memory.md)
    "robot_recall",
    "robot_remember",
}


@contextlib.asynccontextmanager
async def connected(
    transport: Any = None, **kwargs: Any
) -> AsyncIterator[tuple[ClientSession, DuckSession, Any]]:
    """The server over one robot, driven by a real client. `transport` defaults to the
    simulator; a test that needs a body which misbehaves on purpose passes its own."""
    transport = Sim2DTransport(seed=1) if transport is None else transport
    server, session = build_server(transport, heartbeat_period_s=0.05, **kwargs)
    async with create_client_server_memory_streams() as (client_streams, server_streams):
        low = server._lowlevel_server
        task = asyncio.create_task(
            low.run(server_streams[0], server_streams[1], low.create_initialization_options())
        )
        try:
            async with ClientSession(client_streams[0], client_streams[1]) as client:
                await client.initialize()
                yield client, session, transport
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task


async def _cleared(client: Any, robot: str | None = None) -> dict[str, Any]:
    """Record a feasible verdict, which is what the executor wants before anything moves."""
    args: dict[str, Any] = {"verdict": "feasible", "reason": "a test: the body fits the task"}
    if robot is not None:
        args["robot"] = robot
    return _data(await client.call_tool("robot_assess_task", args))


def _data(result: Any) -> dict[str, Any]:
    assert not result.is_error, result
    assert result.structured_content is not None
    return result.structured_content


async def test_tools_and_basic_calls() -> None:
    async with connected() as (client, session, transport):
        tools = await client.list_tools()
        assert {t.name for t in tools.tools} == TOOLS
        from quackd.mcp_server import TOOL_NAMES

        assert set(TOOL_NAMES) == TOOLS  # the docs test reads the same constant
        assert not [t.name for t in tools.tools if t.name.startswith("duck_")], (
            "the 0.3 duck_* aliases were promised for removal in 0.5"
        )
        verbs = _data(await client.call_tool("robot_list_verbs", {}))
        names = {v["name"] for v in verbs["verbs"]}
        assert {"move", "kick", "go_to", "quack"} <= names
        aliases = {v["name"]: v["aliases"] for v in verbs["verbs"]}
        assert aliases["move"] == ["walk"] and aliases["go_to"] == ["walk_to"]
        assert verbs["contract"] is None
        # which verbs a pilot may run before it has judged the task, by the same rule the
        # gate applies: the tool description points a model here rather than reciting a list
        # that a third-party body's own sensing verb could never be in (#26)
        marked = {v["canonical"]: v["before_verdict"] for v in verbs["verbs"]}
        assert marked["observe"] and marked["report_state"] and marked["quack"] and marked["stop"]
        assert not marked["move"] and not marked["kick"] and not marked["go_to"]

        quack = _data(
            await client.call_tool(
                "robot_run_verb", {"verb": "quack", "params": {"text": "hello there"}}
            )
        )
        assert quack["ok"] and "greet" in quack["summary"]
        assert transport.world.quacks

        frame = await client.call_tool("robot_observe", {})
        kinds = [c.type for c in frame.content]
        assert "image" in kinds and "text" in kinds
        image = next(c for c in frame.content if c.type == "image")
        assert image.mime_type == "image/png" and len(image.data) > 100  # v2: snake_case

        state = _data(await client.call_tool("robot_run_verb", {"verb": "report_state"}))
        assert state["ok"] and "standing" in state["summary"]

        before = (transport.world.duck.x, transport.world.duck.y)
        assert (await _cleared(client))["verdict"] == "feasible"
        moved = _data(
            await client.call_tool(
                "robot_run_verb", {"verb": "move", "params": {"vx": 0.2, "duration_s": 1.0}}
            )
        )
        assert moved["ok"]
        assert (transport.world.duck.x, transport.world.duck.y) != before
        assert _data(await client.call_tool("robot_run_verb", {"verb": "stop"}))["ok"]
        # quack, observe, report_state, move and stop all go through the executor. The 0.3
        # duck_get_frame bypassed it; robot_observe is a verb like any other.
        assert session.calls == 5


async def test_contract_is_enforced_after_loading_a_duck() -> None:
    async with connected() as (client, _session, _transport):
        await _cleared(client)
        assert _data(await client.call_tool("robot_run_verb", {"verb": "kick"}))["ok"] is True
        loaded = _data(await client.call_tool("robot_load_duckfile", {"path": "hello-world"}))
        assert loaded["ok"] and loaded["name"] == "hello-world"
        assert "Task" in loaded["instructions"]
        refused = _data(await client.call_tool("robot_run_verb", {"verb": "kick"}))
        assert refused["ok"] is False and "allowlist" in refused["summary"]
        verbs = _data(await client.call_tool("robot_list_verbs", {}))
        allowed = {v["name"] for v in verbs["verbs"] if v["allowed"]}
        assert allowed == {"quack", "walk", "stop"}
        # budgets: hello-world allows 5 steps; the refused kick did not count, quacks do
        results = [
            _data(await client.call_tool("robot_run_verb", {"verb": "quack"})) for _ in range(6)
        ]
        assert all(r["ok"] for r in results[:5])
        assert results[5]["ok"] is False and "budget" in results[5]["summary"]
        bad = _data(await client.call_tool("robot_load_duckfile", {"path": "nope.duck"}))
        assert bad["ok"] is False


async def test_reloading_a_duck_does_not_refund_the_budget() -> None:
    # regression: `robot_load_duckfile` is a tool the model holds, and adopting a contract
    # built a fresh Budget, so a pilot out of steps could load a wider duck and carry on
    async with connected() as (client, session, _transport):
        assert _data(await client.call_tool("robot_load_duckfile", {"path": "hello-world"}))["ok"]
        results = [
            _data(await client.call_tool("robot_run_verb", {"verb": "quack"})) for _ in range(6)
        ]
        assert results[5]["ok"] is False and "budget" in results[5]["summary"]
        spent = session.executor.budget
        assert spent is not None and spent.steps == 5

        # find-and-kick allows more steps than hello-world, and the five are still gone
        loaded = _data(await client.call_tool("robot_load_duckfile", {"path": "find-and-kick"}))
        assert loaded["ok"] and "already spent" in loaded["note"]
        carried = session.executor.budget
        assert carried is not None and carried is not spent
        assert carried.steps == 5 and carried.started_at == spent.started_at
        assert carried.limits.max_steps > 5  # a real widening, not a budget that happens to match


async def test_reloading_a_duck_keeps_the_failure_tally() -> None:
    # the same escape by another door: abort_when counts consecutive failures, and adopting
    # a contract used to clear them, so a reload reset the count as well as the budget
    async with connected() as (client, session, _transport):
        assert _data(await client.call_tool("robot_load_duckfile", {"path": "hello-world"}))["ok"]
        session.executor.consecutive_failures["walk"] = 2
        assert _data(await client.call_tool("robot_load_duckfile", {"path": "find-and-kick"}))["ok"]
        assert session.executor.consecutive_failures == {"walk": 2}


async def test_load_duckfile_refuses_flock_ducks() -> None:
    # regression: only serve() guarded flock ducks; the load tool adopted them silently
    async with connected() as (client, session, _transport):
        res = _data(await client.call_tool("robot_load_duckfile", {"path": "flock-kick"}))
        assert res["ok"] is False and "flock" in res["error"]
        assert session.duck is None  # nothing was adopted


async def test_dry_run_sends_nothing() -> None:
    async with connected(dry_run=True) as (client, _session, transport):
        assert (await _cleared(client))["verdict"] == "feasible"
        res = _data(
            await client.call_tool(
                "robot_run_verb", {"verb": "move", "params": {"vx": 0.2, "duration_s": 1.0}}
            )
        )
        assert res["ok"] and res["data"].get("dry_run") is True
        assert not transport.world.moving and transport.world.steps == 0


async def test_confirm_gated_verbs_need_yes() -> None:
    from quackd.verbs.learned import LearnedVerbSpec, register_learned_verb
    from quackd.verbs.registry import default_registry

    registry = default_registry()
    register_learned_verb(
        registry, LearnedVerbSpec(name="moonwalk", description="d", policy_path="m.onnx")
    )
    async with connected(registry=registry) as (client, _session, _transport):
        await _cleared(client)
        res = _data(await client.call_tool("robot_run_verb", {"verb": "moonwalk"}))
        assert res["ok"] is False and "--yes" in res["summary"]
    async with connected(registry=registry, yes=True) as (client, _session, _transport):
        await _cleared(client)
        res = _data(await client.call_tool("robot_run_verb", {"verb": "moonwalk"}))
        assert res["ok"] is False and "v2" in res["summary"]  # allowed through; no runner yet


@pytest.mark.parametrize("path", ["hello-world", "find-and-kick"])
async def test_bundled_ducks_load_by_name(path: str) -> None:
    async with connected() as (client, _session, _transport):
        assert _data(await client.call_tool("robot_load_duckfile", {"path": path}))["ok"]


def _flat(lines: list[str]) -> str:
    """The log as one string, with the label column's padding squeezed out."""
    return " | ".join(" ".join(line.split()) for line in lines)


async def test_a_call_comes_back_with_what_happened_behind_it() -> None:
    """Over MCP the model is the pilot, so its own reasoning is not quackd's to show. What
    quackd can see, it says: the verb, the intents, what came back, and how long it took."""
    async with connected() as (client, _session, _transport):
        await _cleared(client)
        result = _data(
            await client.call_tool(
                "robot_run_verb", {"verb": "move", "params": {"vx": 0.2, "duration_s": 1.0}}
            )
        )
        log_text = _flat(result["log"])
        assert "move(vx=0.2" in log_text
        assert "-> move" in log_text
        assert "-> stop" in log_text  # `move` stops the robot when it is done
        assert "<- move ok" in log_text and "walked" in log_text
        assert "step 1/" in log_text  # the budget it just spent


async def test_a_refusal_says_which_rule_refused_it() -> None:
    async with connected() as (client, _session, _transport):
        assert _data(await client.call_tool("robot_load_duckfile", {"path": "hello-world"}))["ok"]
        refused = _data(await client.call_tool("robot_run_verb", {"verb": "kick"}))
        assert refused["ok"] is False
        assert "allowlist" in _flat(refused["log"])


async def test_the_observe_tool_appends_its_log_as_text() -> None:
    async with connected() as (client, _session, _transport):
        frame = await client.call_tool("robot_observe", {})
        kinds = [c.type for c in frame.content]
        assert kinds == ["text", "image", "text"]  # summary, picture, log
        assert frame.content[0].text.startswith("duck camera:") or "camera" in frame.content[0].text
        assert frame.content[-1].text.startswith("log:")
        assert "observe" in frame.content[-1].text


async def test_the_tools_that_never_reach_the_robot_carry_no_log() -> None:
    """A log on `robot_list` would be two lines of envelope, read by the model, saying
    nothing about a robot."""
    async with connected() as (client, _session, _transport):
        for tool, args in (
            ("robot_list", {}),
            ("robot_list_verbs", {}),
            ("robot_recall", {}),
            ("robot_remember", {"text": "the ball lives by the sofa"}),
        ):
            assert "log" not in _data(await client.call_tool(tool, args)), tool


async def test_the_log_can_be_turned_off() -> None:
    async with connected(log=False) as (client, _session, _transport):
        assert "log" not in _data(await client.call_tool("robot_run_verb", {"verb": "quack"}))
        frame = await client.call_tool("robot_observe", {})
        assert [c.type for c in frame.content] == ["text", "image"]


async def test_two_calls_at_once_never_swap_logs() -> None:
    """The SDK runs every tool call as its own task. A buffer on the session would put one
    call's intents into the other call's result."""
    async with connected() as (client, _session, _transport):
        await _cleared(client)
        slow, fast = await asyncio.gather(
            client.call_tool(
                "robot_run_verb", {"verb": "move", "params": {"vx": 0.1, "duration_s": 2.0}}
            ),
            client.call_tool("robot_run_verb", {"verb": "quack", "params": {"text": "hi"}}),
        )
        moved, quacked = _flat(_data(slow)["log"]), _flat(_data(fast)["log"])
        assert "-> move" in moved and "quack" not in moved
        assert "quack" in quacked and "-> move" not in quacked


def _blocks(caplog: Any) -> list[list[str]]:
    """The stderr log split into one block per tool call (each opens with a `tool` line)."""
    blocks: list[list[str]] = []
    for message in caplog.messages:
        if ": tool " in message:
            blocks.append([])
        if blocks:
            blocks[-1].append(message)
    return blocks


async def test_the_stderr_view_logs_a_call_as_one_block_at_its_end(caplog: Any) -> None:
    caplog.set_level(logging.INFO, logger="quackd.mcp")
    async with connected() as (client, _session, _transport):
        await client.call_tool("robot_run_verb", {"verb": "quack", "params": {"text": "hi"}})
    flat = " | ".join(" ".join(m.split()) for m in caplog.messages)
    assert "duck: tool robot_run_verb" in flat
    assert "duck: -> sound" in flat
    assert "duck: <- quack ok" in flat
    assert "duck: done ok" in flat


async def test_two_calls_at_once_log_two_blocks_not_an_interleaving(caplog: Any) -> None:
    """One coalescing view shared by concurrent calls merged their bursts and attributed one
    call's intents to the other. Each call is logged as its own block when it ends."""
    caplog.set_level(logging.INFO, logger="quackd.mcp")
    async with connected() as (client, _session, _transport):
        await asyncio.gather(
            client.call_tool(
                "robot_run_verb", {"verb": "move", "params": {"vx": 0.1, "duration_s": 2.0}}
            ),
            client.call_tool("robot_run_verb", {"verb": "quack", "params": {"text": "hi"}}),
        )
    for block in _blocks(caplog):
        flat = " ".join(" ".join(m.split()) for m in block)
        assert not ("-> move" in flat and "quack" in flat), f"two calls in one block: {flat}"


async def test_out_of_call_events_reach_stderr_at_once(caplog: Any) -> None:
    """The heartbeat's stop is the last event of a dying session. Buffered behind a
    coalescing view it waited for a next event that never came."""
    caplog.set_level(logging.INFO, logger="quackd.mcp")
    async with connected() as (_client, session, _transport):
        assert session.event_log is not None
        session.event_log.emit("note", text="heartbeat failed: gone — sending stop")
        session.event_log.emit("intent", intent="stop", params={}, accepted=True)
    flat = " | ".join(" ".join(m.split()) for m in caplog.messages)
    assert "duck: note heartbeat failed" in flat
    assert "duck: -> stop" in flat


async def test_the_heartbeat_narrates_to_stderr_and_lands_in_no_calls_log(caplog: Any) -> None:
    """An event that belongs to no call must not be attributed to whichever call happens to
    be open. The heartbeat runs in a task that predates every capture block, so its note and
    the stop it sends go straight to stderr; the call that comes afterwards carries its own
    refusal and nothing of the link that died."""
    caplog.set_level(logging.INFO, logger="quackd.mcp")
    async with connected(MockTransport(fail_heartbeat_after=1)) as (client, session, _transport):
        await asyncio.wait_for(session.executor.abort.wait(), timeout=2.0)
        stderr = " | ".join(" ".join(m.split()) for m in caplog.messages)
        assert "duck: note heartbeat failed" in stderr
        assert "duck: -> stop" in stderr  # and the emergency stop, not only the note
        refused = _data(await client.call_tool("robot_run_verb", {"verb": "quack"}))
    log_text = _flat(refused["log"])
    assert refused["ok"] is False and "session_aborted" in log_text
    assert "note heartbeat failed" not in log_text
    assert "-> stop" not in log_text


async def test_cap_lines_at_the_real_defaults_through_a_long_call(caplog: Any) -> None:
    """An uncapped log_text would put a megabyte of text into the model's context window on
    one call. stderr keeps every line; the result keeps the first few, the last many, and a
    line saying how many are missing so the reader knows to go and look."""
    from quackd.log import MCP_LOG_MAX_LINES

    caplog.set_level(logging.INFO, logger="quackd.mcp")
    async with connected() as (client, _session, _transport):
        await _cleared(client)
        caplog.clear()  # the verdict is not part of the call being measured
        # a full turn looking for something that is not there: sixteen turn-and-stop pairs,
        # and a burst is only coalesced while the kind stays the same
        result = _data(
            await client.call_tool(
                "robot_run_verb",
                {"verb": "search_scan", "params": {"target": "unicorn", "max_steps": 16}},
            )
        )
    logged = [m.removeprefix("duck: ") for m in caplog.messages if m.startswith("duck: ")]
    log_text = result["log"]
    assert len(logged) > MCP_LOG_MAX_LINES, f"only {len(logged)} lines; nothing to cap"
    assert len(log_text) == MCP_LOG_MAX_LINES == 30
    head = log_text.index(next(line for line in log_text if "more lines" in line))
    assert log_text[:head] == logged[:head]
    assert log_text[head + 1 :] == logged[-(len(log_text) - head - 1) :]
    assert f"... {len(logged) - len(log_text) + 1} more lines" in log_text[head]
    assert "stderr" in log_text[head]  # where the omitted lines really are


async def test_the_result_key_is_log_and_the_old_spelling_is_nowhere_beside_it() -> None:
    """This key was `trace` until 0.11, and it is the one that changed outright rather than
    carrying both spellings for a release. The flags and the variables got that release of
    grace because a person types those and can be told; the wire is read by a model that was
    handed the payload, so a second key would be two names for one list with nothing to say
    which to read. The flags have one spelling again since 0.12."""
    async with connected() as (client, _session, _transport):
        await _cleared(client)
        result = _data(
            await client.call_tool(
                "robot_run_verb", {"verb": "move", "params": {"vx": 0.2, "duration_s": 1.0}}
            )
        )
        assert result["log"], "the key the model reads"
        assert "trace" not in json.dumps(result), f"the old spelling is still on the wire: {result}"


async def test_the_observe_block_is_headed_log() -> None:
    """`robot_observe` returns content rather than a dict, so its log arrives behind a header
    line instead of a key, and that line is the whole of what names it there."""
    async with connected() as (client, _session, _transport):
        frame = await client.call_tool("robot_observe", {})
        block = frame.content[-1].text
        assert block.splitlines()[0] == "log:"
        assert "trace" not in block, block


async def test_no_tool_description_sends_a_model_looking_for_a_trace() -> None:
    """There is no system prompt on this surface: these strings are everything a pilot is told
    about the server. Two of them name the field that comes back, and a description still
    saying `trace` would name a key the result no longer has."""
    async with connected() as (client, _session, _transport):
        said = {
            t.name: (t.description or "") + json.dumps(t.input_schema)
            for t in (await client.list_tools()).tools
        }
        assert "`log` lists what happened" in said["robot_run_verb"]
        assert "a log block of what happened" in said["robot_observe"]
        for name, text in said.items():
            assert "trace" not in text.lower(), f"{name} still says trace: {text}"


def test_the_two_lines_the_log_writes_about_itself_say_log() -> None:
    """Every other line in the list comes from the robot; these two come from the renderer and
    are what a pilot reads when the list is shorter than the call it just made. `cap_lines` and
    `call_lines` are the pair `_call` runs, so this is the wording that reaches the wire."""
    from quackd.log import LogEvent, call_lines, cap_lines

    cut = next(line for line in cap_lines([f"line {i}" for i in range(100)]) if "more" in line)
    assert "the full log is on the server's stderr" in cut
    assert "trace" not in cut

    # an elapsed_s the line formatter cannot render: the guard in `call_lines` answers instead
    (failed,) = call_lines([LogEvent("verb_end", 0.0, {"elapsed_s": "soon", "intents": {}})])
    assert failed.startswith("(the log could not be rendered: ")
    assert "trace" not in failed


async def test_an_aborted_sessions_refusal_says_the_heartbeat_failed() -> None:
    """The heartbeat's task predates every capture block, so its own note reaches no call's
    log_text. Without this the pilot was told the session had aborted and never why."""
    from quackd.mcp_server import build_server

    transport = MockTransport(fail_heartbeat_after=0)
    _server, session = build_server(transport, heartbeat_period_s=0.01)
    await session.connect()
    try:
        await asyncio.wait_for(session.executor.abort.wait(), timeout=2.0)
        refused = await session.run("walk", {"vx": 0.1})
    finally:
        await session.close()
    assert refused["ok"] is False
    assert "heartbeat" in refused["summary"]
    assert "heartbeat" in _flat(refused["log"])


async def test_an_aborted_session_says_why_it_refused() -> None:
    async with connected() as (client, session, _transport):
        session.executor.abort.set()
        refused = _data(await client.call_tool("robot_run_verb", {"verb": "walk"}))
        assert refused["ok"] is False
        assert "session_aborted" in _flat(refused["log"])


async def test_stop_still_works_after_the_session_aborts() -> None:
    """The abort gate refused every verb by name, `stop` included. But the abort is set
    exactly when the pilot needs the brake — the heartbeat has just failed, and a verb that
    was already walking may still be finishing — so this closed the only control the tool
    surface offers at the one moment it mattered. Everything else stays refused."""
    async with connected() as (client, session, _transport):
        session.executor.abort.set()

        walked = await client.call_tool("robot_run_verb", {"verb": "walk", "params": {"vx": 0.1}})
        assert not _data(walked)["ok"]
        assert "aborted" in _data(walked)["summary"]

        stopped = await client.call_tool("robot_run_verb", {"verb": "stop", "params": {}})
        assert _data(stopped)["ok"], "stop must survive the abort"
        assert "stopped" in _data(stopped)["summary"]


async def test_nothing_moves_until_the_pilot_has_judged_the_task() -> None:
    async with connected() as (client, session, transport):
        refused = _data(
            await client.call_tool(
                "robot_run_verb", {"verb": "move", "params": {"vx": 0.2, "duration_s": 1.0}}
            )
        )
        assert refused["ok"] is False
        assert "robot_assess_task" in refused["summary"]
        assert "moves the body" in refused["summary"]
        assert any("verdict" in line for line in refused["log"])
        assert transport.world.steps == 0, "nothing was sent"

        # looking and speaking are how a pilot works out what it is being asked to do
        assert _data(await client.call_tool("robot_run_verb", {"verb": "report_state"}))["ok"]
        assert _data(await client.call_tool("robot_run_verb", {"verb": "quack"}))["ok"]
        assert _data(await client.call_tool("robot_run_verb", {"verb": "stop"}))["ok"]

        assert (await _cleared(client))["note"] == "verbs that move the body now run."
        assert _data(
            await client.call_tool(
                "robot_run_verb", {"verb": "move", "params": {"vx": 0.2, "duration_s": 1.0}}
            )
        )["ok"]
        assert session.executor.verdict is not None


async def test_a_new_task_file_is_a_new_question_about_the_body() -> None:
    from quackd.adapters.factory import make_adapter

    async with connected(make_adapter("microduck:sim2d", seed=1)) as (client, session, _t):
        await _cleared(client)
        loaded = _data(await client.call_tool("robot_load_duckfile", {"path": "hello-world"}))
        assert loaded["ok"]
        assert "assess it with robot_assess_task" in loaded["note"]
        assert loaded["datasheet_text"].startswith("microduck: ")
        assert session.executor.verdict is None
        refused = _data(
            await client.call_tool("robot_run_verb", {"verb": "walk", "params": {"vx": 0.1}})
        )
        assert refused["ok"] is False and "robot_assess_task" in refused["summary"]


async def test_an_uncertain_verdict_waits_for_the_person_in_the_chat() -> None:
    """`--yes` clears a confirm gate because there is no terminal to ask on. Here there is a
    person, reachable through the model, which is a better answer than a flag."""
    async with connected(yes=True) as (client, session, transport):
        answer = _data(
            await client.call_tool(
                "robot_assess_task",
                {
                    "verdict": "uncertain",
                    "reason": "the basket is out of frame, so its weight is a guess",
                    "needs": {"payload_kg": 3.0},
                },
            )
        )
        assert answer["pending"] is True
        assert "ask the person you are chatting with" in answer["note"]
        refused = _data(
            await client.call_tool("robot_run_verb", {"verb": "move", "params": {"vx": 0.1}})
        )
        assert refused["ok"] is False and "uncertain" in refused["summary"]
        assert transport.world.steps == 0
        assert session.executor.verdict is not None


async def test_the_mcp_verdict_tool_says_what_the_prompt_says() -> None:
    """There is no system prompt here, so this description is the whole of what an MCP pilot
    is told about the gate. #25's correction and #24's check both have to be in it, or the two
    surfaces teach different rules, and this is the surface where `uncertain` is the only route
    to the person who could know an unpublished figure. An audit of the first attempt found the
    carve-out replaced by its negation on exactly this one."""
    async with connected() as (client, _session, _transport):
        tool = next(t for t in (await client.list_tools()).tools if t.name == "robot_assess_task")
        said = tool.description or ""
        assert "not by itself" in said, "it states #25's rule absolutely"
        assert "mass or size" in said, "it drops the figure that does decide a limit"
        assert "not published" in said
        assert "own datasheet does not meet is refused" in said, "#24's check, where it is read"
        assert "before_verdict" in said, "which verbs run first is a field, not a fixed list"


async def test_the_mcp_verdict_tool_spells_out_the_words_needs_takes() -> None:
    """MCP types `needs` as a bare dict, so a client is shown no enum and none of the loop's
    field descriptions. An MCP pilot asked to fill `needs` in had to guess the words, and the
    one it most needs on an arm, `mobility: none`, did not exist. The description names every
    word the checker accepts, read from the same tuples, and says a 0 or a none asks for
    nothing."""
    from quackd.verdict import MANIPULATOR_WORDS, MOBILITY_WORDS, TERRAIN_ORDER

    assert "none" in MOBILITY_WORDS and "none" in MANIPULATOR_WORDS, "the words an arm needs"
    async with connected() as (client, _session, _transport):
        tool = next(t for t in (await client.list_tools()).tools if t.name == "robot_assess_task")
        said = tool.description or ""
        for key, words in (
            ("mobility", MOBILITY_WORDS),
            ("manipulator", MANIPULATOR_WORDS),
            ("terrain", TERRAIN_ORDER),
        ):
            assert f"{key} is " in said, key
            for word in words:
                assert word in said, f"{key} never names {word}"
        assert "none means the task needs no locomotion" in said
        assert "a body that does not move meets indoor_flat" in said
        assert "give 0 or none, when the task does not need it" in said
        assert "work_height_m is a height the hands must reach, not a minimum" in said


async def test_a_model_cannot_answer_for_the_human() -> None:
    """The tool has no `human` field to fill in, so the pilot cannot clear its own doubt."""
    async with connected() as (client, session, _transport):
        tool = next(t for t in (await client.list_tools()).tools if t.name == "robot_assess_task")
        assert "human" not in tool.input_schema["properties"]
        answer = _data(
            await client.call_tool(
                "robot_assess_task",
                {"verdict": "uncertain", "reason": "not sure", "human": "go"},
            )
        )
        assert answer["pending"] is True, "an uncertain verdict stays uncertain"
        assert session.executor.verdict is not None
        assert session.executor.verdict.human is None
        assert not session.executor.cleared


async def test_a_feasible_verdict_is_held_to_the_sessions_own_datasheet() -> None:
    """The loop's check, on the other surface. `robot_assess_task` matched `needs` against
    every OTHER robot in the fleet to fill in `could`, and recorded a `feasible` against this
    robot's own sheet without ever looking at it, so over MCP a body still moved on a need
    nobody published. The default body here is a bare simulator with no manifest, which is
    structurally why nothing at this level had ever had a datasheet in hand."""
    from quackd.adapters.factory import make_adapter

    async with connected(make_adapter("microduck:sim2d", seed=1)) as (client, session, adapter):
        refused = _data(
            await client.call_tool(
                "robot_assess_task",
                {
                    "verdict": "feasible",
                    "reason": "it can carry the basket over",
                    "needs": {"payload_kg": 3.0},
                },
            )
        )
        assert refused["ok"] is False
        assert "payload_kg >= 3 (not published)" in refused["summary"]
        assert "Call robot_assess_task" in refused["summary"], "named for this surface"
        assert "uncertain to put it to a person" in refused["summary"], "the way out that asks"
        assert session.executor.verdict is None, "a refused verdict is never recorded"

        moved = _data(
            await client.call_tool("robot_run_verb", {"verb": "move", "params": {"vx": 0.1}})
        )
        assert moved["ok"] is False and "robot_assess_task" in moved["summary"]
        assert adapter.transport.world.steps == 0, "nothing was sent"

        # and a need this body's own sheet does meet still clears the gate
        accepted = _data(
            await client.call_tool(
                "robot_assess_task",
                {
                    "verdict": "feasible",
                    "reason": "it walks, on the floor it is rated for",
                    "needs": {"mobility": "legged", "terrain": "indoor_flat"},
                },
            )
        )
        assert accepted["ok"] is True and accepted["verdict"] == "feasible"
        assert session.executor.cleared

        # and a refusal shuts a gate an earlier verdict opened, or it would refuse the words
        # and not the motion: this pilot is cleared right now, and says the task needs 45
        # minutes of a running time nobody published
        again = _data(
            await client.call_tool(
                "robot_assess_task",
                {
                    "verdict": "feasible",
                    "reason": "and it can patrol for 45 minutes",
                    "needs": {"endurance_min": 45},
                },
            )
        )
        assert again["ok"] is False and "endurance_min >= 45" in again["summary"]
        assert not session.executor.cleared, "the older feasible was left carrying the motion"
        assert session.executor.verdict is None
        stopped = _data(
            await client.call_tool("robot_run_verb", {"verb": "move", "params": {"vx": 0.1}})
        )
        assert stopped["ok"] is False and "robot_assess_task" in stopped["summary"]


async def test_the_robot_list_row_carries_the_body_as_data_and_as_a_sentence() -> None:
    from quackd.adapters.factory import make_adapter

    async with connected(make_adapter("microduck:sim2d", seed=1)) as (client, _s, _t):
        row = _data(await client.call_tool("robot_list", {}))["robots"][0]
        assert row["datasheet"]["mass_kg"]["value"] == 0.8
        assert row["datasheet"]["payload_kg"] is None  # nobody published one
        assert "a beak, no arms" in row["datasheet_text"]


def _arm_away_from_its_rest_pose(**kwargs: Any) -> LeRobotMock:
    """A mock arm with a rest pose recorded and its joints somewhere else, which is where a
    run that ended badly leaves a real one."""
    arm = LeRobotMock(rest_pose=dict(REST), **kwargs)
    arm.joints["shoulder_pan"] = 45.0
    return arm


async def test_a_session_starts_from_and_returns_to_the_rest_pose() -> None:
    """A LeRobot arm goes limp the moment it is disconnected, so one left mid-reach falls at
    the end of every session. The rest move brackets the session: once on connect, so the
    pilot is handed the arm the last session put down rather than wherever it was abandoned,
    and once between the stop and the close, which is the only window where putting the arm
    down changes whether it falls."""
    arm = _arm_away_from_its_rest_pose()
    _server, session = build_server(LeRobotAdapter(arm), heartbeat_period_s=0.05)
    await session.connect()
    try:
        assert arm.sequence[0] == "rest", f"something came before the rest move: {arm.sequence}"
        assert arm.joints["shoulder_pan"] == REST["shoulder_pan"], "the arm never got there"
    finally:
        await session.close()
    assert arm.sequence[-3:] == ["stop", "rest", "close"], arm.sequence
    assert arm.torque is False, "at its rest pose the arm is a thing that can be let go of"
    assert arm.close_note is None


async def test_a_session_whose_rest_pose_lies_past_the_travel_says_so_once_and_starts(
    caplog: Any,
) -> None:
    """An MCP session has no transcript of its own to put the sentence in, so it goes to the
    log where the rest move's own reason goes, once, at connect. The session starts: an arm
    parked at the edge of its travel has reached the pose it can be driven to."""
    from quackd_lerobot.mock import MOCK_RANGES

    floor = MOCK_RANGES["wrist_flex"][0]
    arm = LeRobotMock(rest_pose=dict(REST) | {"wrist_flex": floor - 11.0})
    caplog.set_level(logging.INFO, logger="quackd.mcp")
    _server, session = build_server(LeRobotAdapter(arm), heartbeat_period_s=0.05)
    await session.connect()
    try:
        said = [m for m in caplog.messages if "lerobot-calibrate" in m]
        assert len(said) == 1, caplog.messages
        assert f"driven to {floor:.0f} and no further" in said[0], said[0]
    finally:
        await session.close()
    assert arm.torque is False and arm.close_note is None


async def test_a_dry_run_session_never_moves_the_arm() -> None:
    """`--dry-run` is a promise that nothing is sent, and the rest move is quackd's own send
    rather than the pilot's, which makes it the one most easily forgotten."""
    arm = _arm_away_from_its_rest_pose()
    _server, session = build_server(LeRobotAdapter(arm), dry_run=True, heartbeat_period_s=0.05)
    await session.connect()
    await session.close()
    assert "rest" not in arm.sequence, f"a dry run drove the arm: {arm.sequence}"
    assert arm.actions == [], "a dry run sent a goal"
    assert arm.joints["shoulder_pan"] == 45.0


async def test_a_session_refuses_to_start_when_the_arm_cannot_reach_its_rest_pose() -> None:
    """Nothing here reads joint angles before it acts, so the rest pose is how the pilot
    knows where the arm is. An arm that stalled on the way to it is at a pose nobody has
    established, and a client is about to drive it: the session is refused rather than handed
    over with a guess, and the transport is closed on the way out."""
    arm = _arm_away_from_its_rest_pose(rest_fails="shoulder_lift stopped 40 deg short")
    _server, session = build_server(LeRobotAdapter(arm), heartbeat_period_s=0.05)
    with pytest.raises(TransportError) as raised:
        await session.connect()
    assert "rest pose" in str(raised.value)
    assert "shoulder_lift stopped 40 deg short" in str(raised.value), "the reason is dropped"
    assert arm.sequence == ["rest", "close"], arm.sequence
    assert arm.connected is False
    assert arm.heartbeats == 0, "a session that never started must not be watching the link"
    assert arm.torque is True, "the arm is not somewhere it can be let go of, so torque stays"


class TwoCameraMock(MockTransport):
    """A body with two cameras. `get_frames` is the seam `observe` reads: a transport that
    has it is asked for every view, and one that does not is asked for its only picture."""

    async def get_frames(self) -> list[CameraFrame]:
        return [
            CameraFrame("top", Image.new("RGB", (32, 32), (200, 40, 40)), primary=True),
            CameraFrame("side", Image.new("RGB", (32, 32), (40, 80, 200))),
        ]


async def test_robot_observe_returns_every_camera_as_its_own_named_image() -> None:
    """The session held a single picture, so a body with two cameras returned whichever was
    read second and called it the view. Every camera comes back now, each behind a text block
    naming it, because an unlabelled pair of images is two views the pilot cannot tell apart
    and the detections belong to exactly one of them."""
    async with connected(TwoCameraMock()) as (client, session, _transport):
        frame = await client.call_tool("robot_observe", {})
        kinds = [c.type for c in frame.content]
        assert kinds == ["text", "text", "image", "text", "image", "text"], kinds
        texts = [c.text for c in frame.content if c.type == "text"]
        images = [c for c in frame.content if c.type == "image"]
        assert "top, side" in texts[0] and "top is the primary" in texts[0], texts[0]
        assert texts[1] == "camera top:" and texts[2] == "camera side:"
        assert texts[-1].startswith("log:")
        assert [i.mime_type for i in images] == ["image/png", "image/png"]
        assert images[0].data != images[1].data, "both cameras returned the same picture"
        assert [f.name for f in session.last_frames] == ["top", "side"]
        assert session.frames == 2


class PrimaryDiedMock(MockTransport):
    """A two-camera body whose primary lens has stopped answering. `camera_keys` still names
    both, because a stalled camera is still a camera the arm was opened with, and only the
    secondary returns a frame."""

    camera_keys = ("top", "side")

    async def get_frames(self) -> list[CameraFrame]:
        return [CameraFrame("side", Image.new("RGB", (32, 32), (40, 80, 200)))]


async def test_the_dead_primary_is_not_renamed_to_whichever_lens_survived() -> None:
    """The worst version of this bug, which is not the unlabelled picture but the mislabelled
    one. With one frame back from a two-camera body the picture has to be named, and naming
    the first picture as the primary is only true when the primary is the lens that answered.

    Here it is not: `top` died, so the detector never ran and there are no detections at all.
    Calling `side` the primary and handing it those empty detections tells the pilot the side
    view was looked at and found nothing, which is a worse lie than saying nothing."""
    async with connected(PrimaryDiedMock()) as (client, session, _transport):
        frame = await client.call_tool("robot_observe", {})
        texts = [c.text for c in frame.content if c.type == "text"]
        assert "side is the primary" not in texts[0], texts[0]
        assert "top is the primary and gave nothing this step" in texts[0], texts[0]
        assert "no detections" in texts[0], texts[0]
        assert texts[1] == "camera side:", "the surviving lens still has to be named"
        assert [f.name for f in session.last_frames] == ["side"]


async def test_a_one_camera_observe_reads_back_exactly_as_it_did_before() -> None:
    """Camera names are for the bodies that have more than one. A single camera's name is
    quackd's own default rather than anything its owner chose, so saying it would put a word
    in the pilot's mouth: one summary line, one picture, and no name anywhere."""
    async with connected() as (client, session, _transport):
        frame = await client.call_tool("robot_observe", {})
        assert [c.type for c in frame.content] == ["text", "image", "text"]
        lead = frame.content[0].text
        assert lead.startswith("duck camera: ")
        assert "frame captured" not in lead, "the prefix is stripped, as it always was"
        labels = [c for c in frame.content if c.type == "text" and c.text.startswith("camera ")]
        assert not labels, f"a one-camera body named its camera: {labels}"
        assert [f.name for f in session.last_frames] == [DEFAULT_CAMERA_NAME]
