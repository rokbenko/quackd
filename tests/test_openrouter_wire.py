"""`--llm openrouter` through the real `openai` SDK and a real `quackd run`, against a stand-in.

Skipped wherever the SDK is not installed, which includes CI: nothing in the suite installs a
provider SDK. To run it, sync an environment with the extra and run pytest there:

    UV_PROJECT_ENVIRONMENT=<scratch> uv sync --frozen --extra dev --extra openrouter

No key and no network. `tests/fake_openrouter.py` answers every request on 127.0.0.1, so what
this proves is what quackd puts on the wire through the SDK and what it makes of an answer in
OpenRouter's documented shape. It proves nothing about OpenRouter, and no OpenRouter model has
answered a real quackd request.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("openai")

from typer.testing import CliRunner

from quackd.agent.providers.catalogue import Price, find_model
from quackd.agent.providers.pricing import cost_usd
from quackd.agent.transcript import Transcript
from quackd.cli import app
from tests.conftest import REPO
from tests.fake_openrouter import FakeOpenRouter, reasoning_details

runner = CliRunner()
KEY = "sk-or-v1-stub"


@pytest.fixture(autouse=True)
def _a_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", KEY)


def _run(
    stand_in: FakeOpenRouter,
    tmp_path: Path,
    *flags: str,
    llm: str = "openrouter",
    robot: str = "microduck:mock",
    duck: str = "hello-world",
) -> Any:
    return runner.invoke(
        app,
        [
            "--no-color",
            "run",
            duck,
            "--llm",
            llm,
            "--base-url",
            stand_in.base_url,
            "--robot",
            robot,
            "--runs-dir",
            str(tmp_path),
            "--no-gif",
            "--no-memory",
            *flags,
        ],
    )


def _events(tmp_path: Path) -> list[dict[str, Any]]:
    return Transcript.read(next(tmp_path.rglob("transcript.jsonl")))


def _summary(tmp_path: Path) -> dict[str, Any]:
    import json

    return json.loads(next(tmp_path.rglob("summary.json")).read_text(encoding="utf-8"))


def _images(body: dict[str, Any]) -> int:
    return sum(
        1
        for m in body["messages"]
        if isinstance(m.get("content"), list)
        for part in m["content"]
        if part.get("type") == "image_url"
    )


def test_every_request_says_who_is_asking_and_what_it_insists_on(tmp_path: Path) -> None:
    with FakeOpenRouter() as stand_in:
        result = _run(stand_in, tmp_path)
    assert result.exit_code == 0, result.output
    posts = stand_in.posts()
    assert len(posts) == 5 and stand_in.gets() == [], "the default row needs no list"
    for post in posts:
        assert post.path == "/api/v1/chat/completions"
        assert post.headers["authorization"] == f"Bearer {KEY}"
        assert post.headers["http-referer"] == "https://github.com/rokbenko/quackd"
        assert post.headers["x-openrouter-title"] == "quackd"
        assert post.headers["user-agent"].startswith("AsyncOpenAI/Python")
        assert post.body["model"] == "openai/gpt-6-sol"
        assert post.body["tool_choice"] == "required"
        assert post.body["provider"] == {"require_parameters": True}
        assert "parallel_tool_calls" not in post.body
        assert "reasoning_effort" not in post.body


def test_each_turns_reasoning_goes_back_on_its_own_call_unchanged(tmp_path: Path) -> None:
    """The SDK passes a key it does not know on a message it sends: request k carries every
    earlier turn's `reasoning_details`, deep-equal and in order, on the assistant message that
    quotes that turn's own tool-call id."""
    with FakeOpenRouter() as stand_in:
        result = _run(stand_in, tmp_path)
    assert result.exit_code == 0, result.output
    for k, post in enumerate(stand_in.posts(), start=1):
        assistants = [m for m in post.body["messages"] if m["role"] == "assistant"]
        assert [m["tool_calls"][0]["id"] for m in assistants] == [f"call_{j}" for j in range(1, k)]
        assert [m["reasoning_details"] for m in assistants] == [
            reasoning_details(j) for j in range(1, k)
        ]


def test_each_turn_is_recorded_at_what_it_was_billed_and_the_rest_at_the_rate(
    tmp_path: Path,
) -> None:
    """The stand-in bills its five turns five ways (`fake_openrouter.BILLS`): a plain bill, a
    bring-your-own-key bill with its upstream charge, an upstream figure on a turn that is not
    BYOK, no `cost` at all, and BYOK with its upstream charge missing. The first three are the
    bill as OpenRouter's documents define it; the last two fall back to the catalogue's rate."""
    with FakeOpenRouter() as stand_in:
        result = _run(stand_in, tmp_path)
    assert result.exit_code == 0, result.output
    events = _events(tmp_path)
    calls = [e for e in events if e["kind"] == "llm"]
    rate = find_model("openrouter", "openai/gpt-6-sol").price  # type: ignore[union-attr]
    assert rate is not None
    expected = [0.0011, 0.0002 + 0.003, 0.0013, None, None]
    running = 0.0
    for call, bill in zip(calls, expected, strict=True):
        if bill is None:
            assert "billed" not in call
            assert call["cost_usd"] == cost_usd(call["usage"], rate)
        else:
            assert call["billed"] is True and call["cost_usd"] == pytest.approx(bill, abs=1e-12)
        running = round(running + call["cost_usd"], 6)
        assert call["cost_usd_total"] == running
    summary = _summary(tmp_path)
    assert summary["cost_usd"] == running and summary["billed_calls"] == 3
    assert events[0]["price"]["checked"] == "2026-10-06", "the row's own date, not the global one"
    assert calls[1]["usage"]["cache_read_tokens"] == 100, "the cached slice still reaches usage"


def test_a_price_on_the_run_beats_every_bill(tmp_path: Path) -> None:
    with FakeOpenRouter() as stand_in:
        result = _run(stand_in, tmp_path, "--price", "in=3,out=15")
    assert result.exit_code == 0, result.output
    calls = [e for e in _events(tmp_path) if e["kind"] == "llm"]
    named = Price(3.0, 15.0, source="--price")
    assert [c["cost_usd"] for c in calls] == [cost_usd(c["usage"], named) for c in calls]
    assert all("billed" not in c for c in calls) and "billed_calls" not in _summary(tmp_path)


def test_an_unlisted_id_is_checked_and_priced_against_the_list_once_and_asked(
    tmp_path: Path,
) -> None:
    with FakeOpenRouter() as stand_in:
        result = _run(stand_in, tmp_path, llm="openrouter:quackd-stub/tool-model")
    assert result.exit_code == 0, result.output
    kinds = [r.method for r in stand_in.requests]
    assert kinds[0] == "GET" and kinds.count("GET") == 1, kinds
    (asked,) = stand_in.gets()
    assert asked.path == "/api/v1/models" and "authorization" not in asked.headers
    assert all(post.body["tool_choice"] == "auto" for post in stand_in.posts())
    events = _events(tmp_path)
    rate = events[0]["price"]
    assert rate["source"] == "openrouter" and (rate["input"], rate["output"]) == (2.0, 10.0)
    calls = [e for e in events if e["kind"] == "llm"]
    listed = Price(2.0, 10.0, 0.2, source="openrouter")
    assert calls[3]["cost_usd"] == cost_usd(calls[3]["usage"], listed)


def test_an_unlisted_id_whose_entry_names_no_tool_choice_is_sent_none(tmp_path: Path) -> None:
    with FakeOpenRouter() as stand_in:
        result = _run(stand_in, tmp_path, llm="openrouter:quackd-stub/no-tool-choice")
    assert result.exit_code == 0, result.output
    assert all("tool_choice" not in post.body for post in stand_in.posts())


def test_a_text_only_entry_is_sent_no_frames_where_a_seeing_one_is(tmp_path: Path) -> None:
    """On the simulator, which has a camera: the control run proves frames were there to send."""
    with FakeOpenRouter() as stand_in:
        seeing = _run(
            stand_in,
            tmp_path / "seeing",
            llm="openrouter:quackd-stub/tool-model",
            robot="microduck:sim2d",
        )
    assert seeing.exit_code == 0, seeing.output
    assert sum(_images(post.body) for post in stand_in.posts()) > 0
    with FakeOpenRouter() as stand_in:
        blind = _run(
            stand_in,
            tmp_path / "blind",
            llm="openrouter:quackd-stub/text-only",
            robot="microduck:sim2d",
        )
    assert blind.exit_code == 0, blind.output
    assert all(_images(post.body) == 0 for post in stand_in.posts())


@pytest.mark.parametrize(
    ("model_id", "why"),
    [
        ("quackd-stub/no-tools", "without tool calling"),
        ("quackd-stub/expired", "expired on OpenRouter on 2000-01-01"),
        ("quackd-stub/not-there", "is not on OpenRouter's model list"),
    ],
)
def test_an_id_the_list_says_cannot_pilot_is_refused_before_any_turn(
    model_id: str, why: str, tmp_path: Path
) -> None:
    with FakeOpenRouter() as stand_in:
        result = _run(stand_in, tmp_path, llm=f"openrouter:{model_id}")
    assert result.exit_code == 1
    assert why in " ".join(result.output.split())
    assert len(stand_in.gets()) == 1 and stand_in.posts() == []


def test_a_refused_shape_asks_nothing_at_all_and_needs_no_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    with FakeOpenRouter() as stand_in:
        result = _run(stand_in, tmp_path, llm="openrouter:~anthropic/claude-opus-latest")
    assert result.exit_code == 1
    assert "newest model of its family" in " ".join(result.output.split())
    assert stand_in.requests == []


def test_a_200_carrying_only_an_error_ends_the_run_in_openrouters_words(tmp_path: Path) -> None:
    """Through the real SDK, which builds a response with no `choices` without complaint."""
    error = {
        "error": {
            "code": 502,
            "message": "Provider returned error",
            "metadata": {"provider_name": "Stand-in", "raw": "upstream said no"},
        }
    }
    with FakeOpenRouter(chat=lambda n, body: (200, error)) as stand_in:
        result = _run(stand_in, tmp_path)
    flat = " ".join(result.output.split())
    assert "502: Provider returned error (from Stand-in): upstream said no" in flat
    assert _summary(tmp_path)["outcome"] == "error"
    assert len(stand_in.posts()) == 1, "no retry of an answer that was an error"


def test_a_turn_that_failed_after_it_was_billed_is_in_the_run_total(tmp_path: Path) -> None:
    """A provider that gave out partway can still be billed. Through the real SDK, the third turn
    ends with `finish_reason: "error"` and a bill, and the run's total keeps that bill."""
    from tests.fake_openrouter import SCRIPTS, completion

    def chat(n: int, body: dict[str, Any]) -> tuple[int, Any]:
        name, arguments = SCRIPTS["hello-world"][n - 1]
        if n < 3:
            return 200, completion(n, name, arguments, body["model"], {"cost": 0.01})
        failed = completion(n, name, arguments, body["model"], {"cost": 0.25})
        failed["choices"][0]["finish_reason"] = "error"
        return 200, failed

    with FakeOpenRouter(chat=chat) as stand_in:
        result = _run(stand_in, tmp_path)
    assert "failed partway through the answer" in " ".join(result.output.split())
    summary = _summary(tmp_path)
    assert summary["outcome"] == "error"
    assert summary["cost_usd"] == pytest.approx(0.27) and summary["billed_calls"] == 3
    failed = [e for e in _events(tmp_path) if e["kind"] == "llm" and "error" in e]
    assert len(failed) == 1 and failed[0]["cost_usd"] == 0.25 and failed[0]["billed"] is True


def test_a_claude_row_is_asked_and_keeps_its_reasoning_past_eight_exchanges(
    tmp_path: Path,
) -> None:
    """Where the native provider trims old frames in steps of eight for these models, this one
    trims on every call, on OpenRouter's word that binding is not enforced through it. What can
    be checked here is that every turn's reasoning still goes back, in order, on a run long
    enough to cross that line, and that the row is asked with `auto` from first to last."""
    hello = (REPO / "ducks" / "hello-world.duck").read_text(encoding="utf-8")
    longer = (
        hello.replace("name: hello-world", "name: long-quack")
        .replace("max_steps: 5", "max_steps: 20")
        .replace("max_llm_calls: 5", "max_llm_calls: 20")
        .replace("max_minutes: 1", "max_minutes: 5")
    )
    assert longer != hello and "max_llm_calls: 20" in longer
    duck = tmp_path / "long-quack.duck"
    duck.write_text(longer, encoding="utf-8")
    with FakeOpenRouter(script="long") as stand_in:
        result = _run(
            stand_in,
            tmp_path / "runs",
            llm="openrouter:anthropic/claude-opus-5.5",
            robot="microduck:sim2d",
            duck=str(duck),
        )
    assert result.exit_code == 0, result.output
    posts = stand_in.posts()
    assert len(posts) == 13
    assert all(post.body["tool_choice"] == "auto" for post in posts)
    last = [m for m in posts[-1].body["messages"] if m["role"] == "assistant"]
    assert [m["reasoning_details"] for m in last] == [reasoning_details(j) for j in range(1, 13)]
    assert _images(posts[-1].body) > 0, "a body with a camera, so old frames had something to trim"
