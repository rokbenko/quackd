"""`quackd serve-mcp --lock-duckfile`: a contract the client may narrow and may not widen.

Off, which is the default, a client's `robot_load_duckfile` replaces the contract as it always has,
and `tests/test_mcp_server.py` holds that unchanged. On, the operator's file is the most a pilot can
talk its way to."""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from mcp.client.session import ClientSession
from mcp.shared.memory import create_client_server_memory_streams
from typer.testing import CliRunner

from quackd.cli import app
from quackd.duckfile.schema import DuckFrontmatter
from quackd.duckfile.widen import widened_by
from quackd.mcp_server import build_server
from quackd.transport.sim2d import Sim2DTransport


def _duck(path: Path, front: str, name: str = "task") -> str:
    target = path / f"{name}.duck"
    target.write_text(f"---\n{front}---\nDo the task.\n", encoding="utf-8")
    return str(target)


NARROW = """duck: 1
name: narrow
description: The operator's contract.
verbs:
  allow: [quack, walk, stop]
  confirm: [walk]
budgets:
  max_steps: 5
  max_minutes: 1
  max_llm_calls: 5
abort_when:
  - Battery below 20%
  - Same verb fails 2 times in a row
success:
  - It quacked.
"""


@contextlib.asynccontextmanager
async def _connected(**kwargs: Any) -> AsyncIterator[tuple[ClientSession, Any]]:
    server, session = build_server(Sim2DTransport(seed=1), heartbeat_period_s=0.05, **kwargs)
    async with create_client_server_memory_streams() as (client_streams, server_streams):
        low = server._lowlevel_server
        task = asyncio.create_task(
            low.run(server_streams[0], server_streams[1], low.create_initialization_options())
        )
        try:
            async with ClientSession(client_streams[0], client_streams[1]) as client:
                await client.initialize()
                yield client, session
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task


async def _load(client: ClientSession, path: str) -> dict[str, Any]:
    result = await client.call_tool("robot_load_duckfile", {"path": path})
    return json.loads(result.content[0].text)  # type: ignore[union-attr]


async def test_locked_a_wider_file_is_refused_and_the_contract_stands(tmp_path: Path) -> None:
    locked = _duck(tmp_path, NARROW, "narrow")
    async with _connected(duckfile=locked, lock_duckfile=True) as (client, session):
        answer = await _load(client, "find-and-kick")
        assert answer["ok"] is False
        assert "--lock-duckfile" in answer["error"] and "it allows" in answer["error"]
        assert session.duck.frontmatter.name == "narrow", "the contract did not move"
        assert session.executor.contract.name == "narrow"


async def test_locked_a_file_asking_for_no_more_still_loads(tmp_path: Path) -> None:
    """A lock on widening, not on moving: a pilot can still take up a smaller task."""
    locked = _duck(tmp_path, NARROW, "narrow")
    smaller = _duck(
        tmp_path,
        NARROW.replace("name: narrow", "name: smaller")
        .replace("allow: [quack, walk, stop]", "allow: [quack, stop]")
        .replace("  confirm: [walk]\n", "")
        .replace("max_steps: 5", "max_steps: 3"),
        "smaller",
    )
    async with _connected(duckfile=locked, lock_duckfile=True) as (client, session):
        answer = await _load(client, smaller)
        assert answer["ok"] is True, answer
        assert session.duck.frontmatter.name == "smaller"


async def test_off_a_wider_file_loads_as_it_always_has(tmp_path: Path) -> None:
    """The default. Nothing about a server started without the flag has changed."""
    started = _duck(tmp_path, NARROW, "narrow")
    async with _connected(duckfile=started) as (client, session):
        answer = await _load(client, "find-and-kick")
        assert answer["ok"] is True, answer
        assert session.duck.frontmatter.name == "find-and-kick"
        assert session.locked_to is None


def _front(**changes: Any) -> DuckFrontmatter:
    base: dict[str, Any] = {
        "duck": 3,
        "name": "x",
        "description": "x",
        "verbs": {"allow": ["quack", "walk", "stop", "manipulate"], "confirm": ["walk"]},
        "budgets": {"max_steps": 5, "max_minutes": 1.0, "max_llm_calls": 5},
        "abort_when": ["Battery below 20%", "Same verb fails 2 times in a row"],
        "success": ["done"],
        "policy": {"instructions": ["pick up the cube"], "segment_s": 5.0, "total_s": 30.0},
    }
    base.update(changes)
    return DuckFrontmatter(**base)


LOCKED = _front()


def _same(name: str) -> str:
    return name


@pytest.mark.parametrize(
    ("changes", "says"),
    [
        (
            {
                "verbs": {
                    "allow": ["quack", "walk", "stop", "manipulate", "kick"],
                    "confirm": ["walk"],
                }
            },
            "allows kick",
        ),
        ({"verbs": {"allow": ["quack", "walk", "stop", "manipulate"]}}, "stops asking before walk"),
        ({"budgets": {"max_steps": 6, "max_minutes": 1.0, "max_llm_calls": 5}}, "max_steps"),
        ({"budgets": {"max_steps": 5, "max_minutes": 2.0, "max_llm_calls": 5}}, "max_minutes"),
        ({"budgets": {"max_steps": 5, "max_minutes": 1.0, "max_llm_calls": 6}}, "max_llm_calls"),
        ({"abort_when": ["Same verb fails 2 times in a row"]}, "battery abort"),
        ({"abort_when": ["Battery below 5%", "Same verb fails 2 times in a row"]}, "battery abort"),
        ({"abort_when": ["Battery below 20%"]}, "failures of a verb"),
        (
            {"abort_when": ["Battery below 20%", "Same verb fails 9 times in a row"]},
            "failures of a verb",
        ),
        (
            {"policy": {"instructions": ["pick up the cube"], "segment_s": 6.0, "total_s": 30.0}},
            "segment_s",
        ),
        (
            {"policy": {"instructions": ["pick up the cube"], "segment_s": 5.0, "total_s": 31.0}},
            "total_s",
        ),
        ({"policy": {"instructions": [], "segment_s": 5.0, "total_s": 30.0}}, "word the policy"),
        (
            {"policy": {"instructions": ["pick up the knife"], "segment_s": 5.0, "total_s": 30.0}},
            "pick up the knife",
        ),
        ({"datasheet": {"payload_kg": {"value": 5.0}}}, "datasheet"),
    ],
)
def test_each_way_a_file_can_ask_for_more_is_named(changes: dict[str, Any], says: str) -> None:
    wider = widened_by(LOCKED, _front(**changes), _same)
    assert wider and any(says in line for line in wider), wider


@pytest.mark.parametrize(
    "changes",
    [
        {},
        {"name": "another", "description": "Reworded.", "success": ["something else"]},
        {"persona": "brisk"},
        {"verbs": {"allow": ["quack", "stop", "manipulate"]}},
        {"verbs": {"allow": ["quack", "walk", "stop", "manipulate"], "confirm": ["walk", "quack"]}},
        {"budgets": {"max_steps": 1, "max_minutes": 0.5, "max_llm_calls": 1}},
        {"abort_when": ["Battery below 50%", "Same verb fails 1 times in a row", "It gets dark"]},
        {"policy": {"instructions": ["pick up the cube"], "segment_s": 1.0, "total_s": 2.0}},
    ],
)
def test_a_file_asking_for_no_more_is_not_called_wider(changes: dict[str, Any]) -> None:
    assert widened_by(LOCKED, _front(**changes), _same) == []


def test_a_file_that_drops_the_policys_verbs_is_not_held_to_the_policys_defaults() -> None:
    """A file with no `manipulate` and no `pick` gets no policy. Reading its section's defaults, a
    file that asks for almost nothing was refused for raising limits it never touched."""
    assert widened_by(LOCKED, _front(verbs={"allow": ["stop"]}, policy=None), _same) == []


def test_a_file_that_keeps_the_verb_is_held_to_its_defaults() -> None:
    """Keeping `manipulate` with no section means the defaults, which may be wider than the lock."""
    wider = widened_by(LOCKED, _front(policy=None), _same)
    assert any("word the policy" in line for line in wider), wider


def test_an_empty_datasheet_section_corrects_nothing() -> None:
    assert widened_by(LOCKED, _front(datasheet={}), _same) == []


def test_a_verb_under_its_alias_is_the_same_verb() -> None:
    canonical = {"honk": "quack"}.get
    offered = _front(verbs={"allow": ["honk", "walk", "stop", "manipulate"], "confirm": ["walk"]})
    assert widened_by(LOCKED, offered, lambda name: canonical(name, name)) == []


def test_the_flag_wants_a_contract_to_hold_to() -> None:
    result = CliRunner().invoke(app, ["serve-mcp", "--lock-duckfile"])
    assert result.exit_code != 0 and "--duckfile" in result.output


@pytest.mark.parametrize("fleet", [["--robots", "a=microduck:sim2d"], ["--flock", "kitchen"]])
def test_the_flag_holds_one_robot_and_refuses_a_flock(fleet: list[str]) -> None:
    result = CliRunner().invoke(
        app, ["serve-mcp", "--duckfile", "hello-world", "--lock-duckfile", *fleet]
    )
    assert result.exit_code != 0 and "flock" in result.output
