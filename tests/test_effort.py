"""`--effort`: how hard the pilot's model reasons, said on the command line rather than in `.env`.

Both vendors that take it were reachable only by an environment variable, one value a process,
which a bench session comparing a quick model with a careful one had to restart a shell to
change. The flag reaches the three pilots that take such a field, beats both variables, is
refused in a sentence where it cannot reach, and the record says what was asked and whose word
it was.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from quackd import cli
from quackd.agent.loop import RunConfig, run_duck
from quackd.agent.providers.base import Exchange, ProviderError, ProviderTurn, ToolCall
from quackd.agent.providers.factory import EFFORTS, effort_refusal, make_provider
from quackd.agent.transcript import Transcript
from quackd.duckfile.schema import DuckFile
from quackd.transport.mock import MockTransport
from tests import fake_openai_sdk as fake_sdk

runner = CliRunner()


def _claude(monkeypatch: pytest.MonkeyPatch) -> None:
    """Anthropic's SDK, as much of it as building a provider touches: CI installs none."""

    class FakeAnthropic:
        def __init__(self, **_kwargs: Any) -> None:
            pass

    module = types.ModuleType("anthropic")
    module.AsyncAnthropic = FakeAnthropic  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "anthropic", module)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-not-a-real-key")


def test_the_flag_reaches_claude_beats_the_variable_and_says_whose_word_it_was(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`QUACKD_EFFORT` and quackd's own medium still stand where the flag is absent, and the
    record names which of the three set the level every request was sent at."""
    _claude(monkeypatch)
    monkeypatch.delenv("QUACKD_EFFORT", raising=False)
    flagged = make_provider("anthropic", effort=" LOW ")
    assert flagged._params("system", [], [])["output_config"] == {"effort": "low"}
    assert flagged.asked_effort == {"value": "low", "from": "--effort"}  # type: ignore[attr-defined]

    assert make_provider("anthropic").asked_effort == {  # type: ignore[attr-defined]
        "value": "medium",
        "from": "default",
    }
    monkeypatch.setenv("QUACKD_EFFORT", "high")
    assert make_provider("anthropic").asked_effort == {  # type: ignore[attr-defined]
        "value": "high",
        "from": "QUACKD_EFFORT",
    }
    over = make_provider("anthropic", effort="low")
    assert over.asked_effort == {"value": "low", "from": "--effort"}  # type: ignore[attr-defined]


def test_the_flag_reaches_openai_and_a_server_of_your_own(monkeypatch: pytest.MonkeyPatch) -> None:
    """OpenAI's reasoning effort, sent on either API, and the same field to a model server of
    your own, which speaks OpenAI's API. Where nothing names a level, nothing is sent and the
    record says so with a null rather than guessing at the model's own default."""
    fake_sdk.install(monkeypatch, fake_sdk.FakeOpenAI())
    monkeypatch.setenv("OPENAI_API_KEY", "sk-not-a-real-key")
    monkeypatch.delenv("QUACKD_OPENAI_REASONING_EFFORT", raising=False)
    gpt = make_provider("openai", effort="high")
    assert gpt.reasoning_effort == "high"  # type: ignore[attr-defined]
    assert gpt.asked_effort == {"value": "high", "from": "--effort"}  # type: ignore[attr-defined]
    assert make_provider("openai").asked_effort is None  # type: ignore[attr-defined]
    monkeypatch.setenv("QUACKD_OPENAI_REASONING_EFFORT", "medium")
    assert make_provider("openai").asked_effort == {  # type: ignore[attr-defined]
        "value": "medium",
        "from": "QUACKD_OPENAI_REASONING_EFFORT",
    }
    own = make_provider("ollama", effort="low")
    assert own.reasoning_effort == "low"  # type: ignore[attr-defined]
    assert own.asked_effort == {"value": "low", "from": "--effort"}  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("name", "model", "effort", "needle"),
    [
        pytest.param(
            "anthropic", None, "max", "one of low, medium, high", id="a-level-only-one-has"
        ),
        pytest.param("fake", None, "low", "the scripted pilot", id="fake"),
        pytest.param("gemini", None, "low", "gemini documents no such field", id="gemini"),
        pytest.param("openrouter", None, "low", "which --extra-body carries", id="openrouter"),
        pytest.param("grok", None, "low", "grok documents no such field", id="grok"),
        pytest.param(
            "anthropic", "claude-haiku-4-5", "low", "takes no effort setting", id="haiku-4-5"
        ),
    ],
)
def test_the_flag_is_refused_where_it_cannot_reach(
    name: str, model: str | None, effort: str, needle: str
) -> None:
    """Refused before anything is built, so no SDK, key or network is involved: a level sent to
    a field the vendor does not document is ignored at best and a 400 at the first turn at
    worst, with the robot already connected."""
    with pytest.raises(ProviderError) as refused:
        make_provider(name, model=model, effort=effort)
    assert needle in str(refused.value), refused.value


def test_every_level_the_flag_takes_is_one_both_vendors_have() -> None:
    assert EFFORTS == ("low", "medium", "high")
    for level in EFFORTS:
        assert effort_refusal("anthropic", "claude-opus-5-5", level) is None
        assert effort_refusal("openai", "gpt-6-sol", level) is None


class _Asking:
    """A pilot that says what it asks its model to reason at, the way the real ones do."""

    name = "asking"
    model = "test"
    supports_vision = False
    asked_effort = {"value": "low", "from": "--effort"}

    async def step(
        self, system: str, history: list[Exchange], tools: list[dict[str, Any]]
    ) -> ProviderTurn:
        return ProviderTurn(
            tool_calls=[ToolCall(name="declare_success", arguments={"reason": "x"})]
        )


async def test_the_record_says_what_was_asked_and_whose_word_it_was(
    hello_duck: DuckFile, tmp_path: Path
) -> None:
    result = await run_duck(
        RunConfig(duck=hello_duck, provider=_Asking(), transport=MockTransport(), runs_dir=tmp_path)
    )
    start = Transcript.read(result.run_dir / "transcript.jsonl")[0]
    assert start["kind"] == "run_start"
    assert start["effort"] == {"value": "low", "from": "--effort"}


def test_the_header_names_the_level_only_when_the_flag_asked() -> None:
    """A header quoted anywhere from a run without the flag stays what it was."""
    pilot = types.SimpleNamespace(name="anthropic", model="claude-opus-5-5")
    plain = cli._header_rows(provider=pilot, robot="duck", seed=None, dry_run=False, memory=None)
    assert "effort" not in dict(plain)
    rows = dict(
        cli._header_rows(
            provider=pilot, robot="duck", seed=None, dry_run=False, memory=None, effort="Low"
        )
    )
    assert rows["effort"] == "low (--effort)"


def test_a_run_refuses_the_flag_for_the_scripted_pilot_before_it_connects(tmp_path: Path) -> None:
    result = runner.invoke(
        cli.app,
        [
            "run",
            "hello-world",
            "--llm",
            "fake",
            "--effort",
            "low",
            "--robot",
            "microduck:mock",
            "--no-gif",
            "--no-memory",
            "--runs-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 1, result.output
    assert "the scripted pilot" in " ".join(result.output.split())
    assert not list(tmp_path.glob("*/transcript.jsonl")), "nothing ran"


def test_a_vla_run_refuses_the_flag_since_it_asks_no_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--controller vla` hands the arm to a policy server and reads it the task's steps from a
    script, so a reasoning level has nobody to reach. Refused before the arm is built."""
    monkeypatch.setattr(cli, "_can_prompt", lambda: True)
    result = runner.invoke(
        cli.app,
        [
            "run",
            "--goal",
            "hold still",
            "--robot",
            "lerobot:mujoco",
            "--policy-url",
            "http://127.0.0.1:1",
            "--policy-token",
            "not-a-real-token",
            "--controller",
            "vla",
            "--effort",
            "low",
            "--no-memory",
            "--runs-dir",
            str(tmp_path / "runs"),
        ],
    )
    assert result.exit_code == 1, result.output
    assert "--controller vla asks no model" in " ".join(result.output.split())
    runs = tmp_path / "runs"
    assert not runs.exists() or not any(runs.iterdir()), "nothing ran"
