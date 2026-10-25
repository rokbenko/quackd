"""The pilot's client is warmed while the robot connects, and nothing waits for it.

A client of Anthropic's or OpenAI's SDK starts its first request by asking which platform it is
on, which on Windows is a WMI query or a subprocess, paid in the seconds a person waits for the
robot to move. Asked in a thread of its own while the robot connects, it is paid inside a wait
that was happening anyway. These prove the overlap, that the run never waits on it or fails for
it, and that the record says what became of it, with no clock of the test's own in any claim.
"""

from __future__ import annotations

import asyncio
import sys
import threading
import types
from pathlib import Path
from typing import Any

import pytest

from quackd.agent.loop import RunConfig, run_duck
from quackd.agent.providers.base import Exchange, ProviderTurn, ToolCall
from quackd.agent.providers.fake import FakeProvider
from quackd.agent.transcript import Transcript
from quackd.duckfile.schema import DuckFile
from quackd.transport.mock import MockTransport

BOUND = 500
"""How many hundredths of a second a test waits on the other thread before it fails rather than
hangs. A bound and never a claim: every assertion here is about order and outcome."""


class _Warms:
    """A pilot whose warm-up runs the test's function, and which declares at once."""

    name = "warms"
    model = "test"
    supports_vision = False

    def __init__(self, warm: Any) -> None:
        self._warm = warm

    def warm(self) -> None:
        self._warm()

    async def step(
        self, system: str, history: list[Exchange], tools: list[dict[str, Any]]
    ) -> ProviderTurn:
        return ProviderTurn(
            tool_calls=[ToolCall(name="declare_success", arguments={"reason": "warmed"})]
        )


def _warming() -> list[threading.Thread]:
    return [t for t in threading.enumerate() if t.name == "quackd-pilot-warm" and t.is_alive()]


class _ConnectsBesideIt(MockTransport):
    """A body whose connect waits to see the warm-up begin, lets it go, and waits for its thread
    to end, which it could do only if the warm-up was already running beside the connect. The
    thread writes down how it went before it ends, so by `run_start` the record is settled."""

    def __init__(self, began: threading.Event, release: threading.Event) -> None:
        super().__init__()
        self.began, self.release = began, release
        self.saw_it_begin = False

    async def connect(self) -> None:
        for _ in range(BOUND):
            if self.began.is_set():
                break
            await asyncio.sleep(0.01)
        self.saw_it_begin = self.began.is_set()
        self.release.set()
        for _ in range(BOUND):
            if not _warming():
                break
            await asyncio.sleep(0.01)
        await super().connect()


def _start(run_dir: Path) -> dict[str, Any]:
    start = Transcript.read(run_dir / "transcript.jsonl")[0]
    assert start["kind"] == "run_start"
    return start


async def test_the_warm_up_runs_in_its_own_thread_while_the_robot_connects(
    hello_duck: DuckFile, tmp_path: Path
) -> None:
    began, release = threading.Event(), threading.Event()
    ran_on: list[int] = []

    def warm() -> None:
        ran_on.append(threading.get_ident())
        began.set()
        release.wait(BOUND / 100)

    body = _ConnectsBesideIt(began, release)
    result = await run_duck(
        RunConfig(duck=hello_duck, provider=_Warms(warm), transport=body, runs_dir=tmp_path)
    )
    assert result.outcome == "success", result.reason
    assert body.saw_it_begin, "the warm-up did not begin until the connect was over"
    assert ran_on and ran_on[0] != threading.get_ident(), "warmed on the event loop's thread"
    warmed = _start(result.run_dir)["pilot_warm"]
    assert warmed["ok"] is True and isinstance(warmed["s"], float), warmed


async def test_a_warm_up_that_fails_is_a_line_in_the_record_and_nothing_else(
    hello_duck: DuckFile, tmp_path: Path
) -> None:
    began, release = threading.Event(), threading.Event()

    def warm() -> None:
        began.set()
        release.wait(BOUND / 100)
        raise OSError("ver would not start")

    body = _ConnectsBesideIt(began, release)
    result = await run_duck(
        RunConfig(duck=hello_duck, provider=_Warms(warm), transport=body, runs_dir=tmp_path)
    )
    assert result.outcome == "success", result.reason
    warmed = _start(result.run_dir)["pilot_warm"]
    assert warmed["failed"] == "OSError" and "ok" not in warmed, warmed


async def test_the_run_never_waits_for_a_warm_up_still_going(
    hello_duck: DuckFile, tmp_path: Path
) -> None:
    """Waiting for it would only move its seconds from the first call to before it, and a probe
    that hung would hang the run. So a warm-up still going when the run starts is left going, the
    run goes on without it, and the record says it had not finished."""
    held = threading.Event()

    def warm() -> None:
        held.wait(BOUND / 100)

    try:
        result = await run_duck(
            RunConfig(
                duck=hello_duck, provider=_Warms(warm), transport=MockTransport(), runs_dir=tmp_path
            )
        )
    finally:
        held.set()
    assert result.outcome == "success", result.reason
    assert _start(result.run_dir)["pilot_warm"] == {"finished": False}


async def test_a_pilot_with_no_warm_up_writes_the_record_it_always_did(
    hello_duck: DuckFile, tmp_path: Path
) -> None:
    result = await run_duck(
        RunConfig(
            duck=hello_duck,
            provider=FakeProvider.for_duck(hello_duck.name),
            transport=MockTransport(),
            runs_dir=tmp_path,
        )
    )
    assert "pilot_warm" not in _start(result.run_dir)


class _KeepsItsPlatform:
    """A client as the SDK builds one: no answer about its platform until something asks."""

    _platform: Any = None


@pytest.mark.parametrize("sdk", ["anthropic", "openai"])
def test_each_sdks_own_look_at_its_platform_is_handed_to_the_pilots_client(
    sdk: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both SDKs keep the answer in `_platform` and ask `get_platform` for it before their first
    request, so the pilot's warm-up asks the SDK's own function and hands its client the answer,
    which leaves that request nothing to ask."""
    asked: list[str] = []

    def get_platform() -> str:
        asked.append(sdk)
        return "Windows"

    module = types.ModuleType(f"{sdk}._base_client")
    module.get_platform = get_platform  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, f"{sdk}._base_client", module)
    client = _KeepsItsPlatform()
    if sdk == "anthropic":
        from quackd.agent.providers.anthropic import AnthropicProvider

        AnthropicProvider(model="claude-opus-5-5", client=client).warm()
    else:
        from quackd.agent.providers.openai import OpenAIProvider

        OpenAIProvider(model="gpt-6-sol", client=client).warm()
    assert asked == [sdk]
    assert client._platform == "Windows"
