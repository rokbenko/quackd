"""`--decision-llm openai` through the real SDK and a real `quackd run`, against a stand-in.

Skipped wherever the SDK is not installed, which includes CI: nothing in the suite installs a
provider SDK. To run it, sync an environment with the extra and run pytest there:

    UV_PROJECT_ENVIRONMENT=<scratch> uv sync --frozen --extra dev --extra openai

It has run, all of it but the tests that send a camera frame, against three releases of the SDK,
on 2026-10-11 and again on 2026-10-19, once the decision client took the pilot's rule for the
SDK's own variables, and against no other: 3.22.1, which the lock carries and that sync installs;
3.1.0, the first release in uv's cache that reads `OPENAI_CUSTOM_HEADERS`; and 1.66.0, the extra's
floor, which sends through `httpx` rather than `httpx2`. Each of the last two was put into an
environment synced that way with `uv pip install`. The tests that send a camera frame, written
with `--decision-frames` on 2026-10-19, have run against 3.22.1 alone. The extra allows every
release from the floor to below 4.0, and what any other one does is read, where anybody read it,
rather than run.

No key and no network. `tests/fake_openai_decisions.py` answers every request on 127.0.0.1, so
what this proves is what quackd puts on the wire through the SDK, what the SDK adds to a request
or leaves off it once quackd has told it, and what quackd makes of an answer in OpenAI's
documented shape. `tests/test_decisions_api.py` holds quackd's half against a fake of the SDK,
and a fake cannot show what the real one does with the headers it is handed. Nothing here proves
anything about OpenAI, and no request from quackd has reached `gpt-6-luna`.

Every run here has the variables the SDK reads for itself set the way somebody who pilots with
OpenAI through a gateway might have them in a `.env`, so every run shows what quackd does about
each of them.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import hashlib
import io
import json
import re
import socket
import time
import urllib.parse
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("openai")

from PIL import Image
from typer.testing import CliRunner

from quackd.agent.decision import decisions_api
from quackd.agent.decision.base import TIMEOUT_S
from quackd.agent.decision.catalogue import PRESETS
from quackd.agent.decision.factory import make_decision_llm, resolve_decision_price
from quackd.agent.decision.stepper import Call, Stepper, build_questions
from quackd.agent.providers.pricing import cost_usd
from quackd.agent.transcript import Transcript
from quackd.cli import app
from tests import fake_decisions_api as fake_api
from tests import test_live_decision as live
from tests.conftest import ReachedOpenAI, _openai_base, plain_text
from tests.fake_openai_decisions import (
    CONFIDENCE,
    PATH,
    FakeOpenAIDecisions,
    Recorded,
    answering,
    failing,
    paced,
    redirecting,
    send_once_through_the_sdk,
)

runner = CliRunner()
OPENAI = PRESETS["openai"]
KEY = "sk-test-not-a-real-key"
"""Never a real one: nothing here leaves the machine, and a fake key is what proves it."""
AMBIENT = "sk-ambient"
"""The `Authorization` line a gateway's `OPENAI_CUSTOM_HEADERS` carries, which must never
replace the key or the word on its way out."""
GATEWAY = "gk-secret"
"""A gateway's own credential, which is that gateway's and goes nowhere from here."""

TYPES = {"choice": "choice", "noul": "predicate", "score": "score"}
"""quackd's question types to OpenAI's: a `noul` goes as a `predicate`, the same question by
another name, and the other two keep theirs."""


@pytest.fixture(scope="module", autouse=True)
def _the_sdk_has_sent_before() -> Recorded:
    """One request through the real SDK to a stand-in of its own, before any test here starts,
    so no test's first turn pays for what the SDK does once in a process
    (`send_once_through_the_sdk`).

    Most tests here give each turn the stepper's own second and then want every turn answered,
    so a first turn that spent its second on the SDK getting ready would fail one on a slow
    machine, for a reason that has nothing to do with what it checks. The SDK's look at its
    platform is taken while each client is built, which covered most of that; what is left
    falls on whichever request a process sends first, and this is that request. For the whole
    module and before anything else, because pytest may be asked for any one test here alone.
    The SDK's own variables are cleared for it, so a developer's never stops it."""
    with pytest.MonkeyPatch.context() as patch:
        for name in ("OPENAI_CUSTOM_HEADERS", "OPENAI_ORG_ID", "OPENAI_PROJECT_ID"):
            patch.delenv(name, raising=False)
        return asyncio.run(send_once_through_the_sdk())


@pytest.fixture(autouse=True)
def _a_developers_openai_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", KEY)
    # Followed by the SDK whenever it is handed no address. Nothing listens on port 1, so a
    # request that went there would be a turn that failed rather than one the stand-in saw.
    monkeypatch.setenv("OPENAI_BASE_URL", "http://127.0.0.1:1/v1")
    monkeypatch.setenv(
        "OPENAI_CUSTOM_HEADERS", f"Authorization: Bearer {AMBIENT}\nX-Gateway-Key: {GATEWAY}"
    )
    monkeypatch.setenv("OPENAI_ORG_ID", "org-x")
    monkeypatch.setenv("OPENAI_PROJECT_ID", "proj-y")


def _run(stand_in: FakeOpenAIDecisions, tmp_path: Path, *extra: str, url: str | None = None) -> Any:
    """The read-only arm task in shadow mode, so the scripted pilot flies it and the stepper is
    asked on every turn without anything it says reaching the arm. `extra` goes on the end of
    the command line."""
    return runner.invoke(
        app,
        [
            "--no-color",
            "run",
            "lerobot-lookout",
            "--robot",
            "lerobot:mock",
            "--llm",
            "fake",
            "--decision-llm",
            "openai",
            "--decision-url",
            url or stand_in.url,
            "--decision-mode",
            "shadow",
            "--runs-dir",
            str(tmp_path),
            "--no-gif",
            "--no-memory",
            *extra,
        ],
    )


def _records(tmp_path: Path) -> list[dict[str, Any]]:
    return Transcript.read(next(tmp_path.rglob("transcript.jsonl")))


def _start(records: list[dict[str, Any]]) -> dict[str, Any]:
    return next(r for r in records if r["kind"] == "run_start")


def _sent(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The stepper turns whose request went out, which are the ones that record `idle_s`, in the
    order they were asked: the same order as the stand-in's requests."""
    return [r for r in records if r["kind"] == "decision" and "idle_s" in r]


def _summary(tmp_path: Path) -> dict[str, Any]:
    summary: dict[str, Any] = json.loads(
        next(tmp_path.rglob("summary.json")).read_text(encoding="utf-8")
    )
    return summary


def _as_openai(monkeypatch: pytest.MonkeyPatch, stand_in: FakeOpenAIDecisions) -> None:
    """Tell quackd's own rule that the stand-in is OpenAI's API, so the key and the SDK's own
    variables go to it as they would to OpenAI. Compared on the authority, so the client's own
    address, which the build reads back with `/v1/` on the end, is the stand-in's as well. The
    suite's guard keeps a rule of its own, sees 127.0.0.1 and lets the request through."""
    real = decisions_api.is_openai_host
    here = urllib.parse.urlsplit(stand_in.url).netloc
    monkeypatch.setattr(
        decisions_api,
        "is_openai_host",
        lambda url: urllib.parse.urlsplit(url.strip()).netloc == here or real(url),
    )


async def _sent_by_the_sdk_alone(stand_in: FakeOpenAIDecisions) -> Recorded:
    """One request from a client the SDK built by itself, handed the key and the stand-in's
    address and nothing else, as it would reach OpenAI's own API from a program that is not
    quackd: what the rule says quackd's client sends there. Its answer is not read."""
    import openai

    before = len(stand_in.posts())
    alone = openai.AsyncOpenAI(api_key=KEY, base_url=f"{stand_in.url}/v1", max_retries=0)
    try:
        with contextlib.suppress(openai.APIError):
            await alone.post(decisions_api.PATH, cast_to=object, body={"model": OPENAI.model})
    finally:
        await alone.close()
    return stand_in.posts()[before]


AMBIENT_NAMES = ("authorization", "x-gateway-key", "openai-organization", "openai-project")
"""Every header the variables the SDK reads for itself fill here: the key's own, the gateway's
two lines, and the two account headers."""


async def _decide_once(url: str, **kwargs: Any) -> Any:
    """One request through the real client, built the way a run builds it."""
    return await make_decision_llm(OPENAI, url=url).decide(
        {"goal": "g"}, {"done": {"type": "noul", "instructions": "i"}}, **kwargs
    )


def _stepper(llm: Any) -> Stepper:
    """A stepper offered the brake and nothing else, built bare, for the tests that want a turn
    without a run around it."""
    stepper = Stepper(
        mode="shadow",
        goal="a goal",
        llm=llm,
        name=llm.name,
        model=llm.model,
        price=resolve_decision_price(OPENAI, url=llm.url),
    )
    stepper.calls["stop"] = Call(name="stop", arguments={}, label="stop")
    stepper.classes["stop"] = "brake"
    stepper.what["stop"] = "the brake"
    stepper.early.add("stop")
    return stepper


def _plain_words(text: str, count: int = 5) -> str:
    """The first `count` words in a row of `text` that are letters alone. Those come through
    every quoting a reply or an exception message can put them through unchanged, so a search
    for them finds them wherever the words went."""
    words = text.split()
    for start in range(len(words) - count + 1):
        run = words[start : start + count]
        if all(word.isascii() and word.isalpha() for word in run):
            return " ".join(run)
    raise AssertionError(f"no {count} plain words in a row to look for")


# ── what goes out ───────────────────────────────────────────────────────────────────────


def test_every_request_is_three_fields_at_v1_decisions_sent_the_word_and_nothing_ambient(
    tmp_path: Path,
) -> None:
    """At an address that is not OpenAI's, the real SDK sends the word rather than the key, the
    gateway's `Authorization` line does not replace it, and nothing else the SDK reads for itself
    rides along: not the gateway's header, not OpenAI's account headers. Every request reached the
    stand-in, so the SDK's own address variable moved nothing, and every one passed the
    stand-in's check of the request OpenAI document."""
    with FakeOpenAIDecisions() as stand_in:
        result = _run(stand_in, tmp_path)
    assert result.exit_code == 0, result.output
    posts = stand_in.posts()
    assert posts and len(stand_in.requests) == len(posts), "something other than the request"
    for post in posts:
        assert post.path == PATH
        assert post.status == 200, post.reply
        # every line under the name, so a second one beside quackd's would show
        assert post.every("authorization") == ["Bearer local"]
        assert post.headers["user-agent"].startswith("AsyncOpenAI/Python"), "not the real SDK"
        for ambient in ("x-gateway-key", "openai-organization", "openai-project"):
            assert ambient not in post.headers, ambient
        assert set(post.body) == {"model", "input", "questions"}
        assert post.body["model"] == OPENAI.model
    start = _start(_records(tmp_path))
    assert start["decision_llm"] == {
        "name": "openai",
        "model": OPENAI.model,
        "url": stand_in.url,
        "frames": False,
    }
    assert start["decision_price"]["source"] == "self-hosted", "a server you run is free"


def test_at_openais_own_address_a_run_sends_what_the_sdk_sends_by_itself(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """What a run sends where the key is meant to go, seen without the network: quackd's own
    rule is told the stand-in is OpenAI's API. The SDK's own variables follow the SDK's own
    address (ADR-0010's amendment of 2026-10-12), so every request carries the key, the gateway's
    lines and the account headers exactly as a client the SDK built by itself sends them, and the
    turns are costed at OpenAI's published rate."""
    rate = OPENAI.price
    assert rate is not None
    with FakeOpenAIDecisions() as stand_in:
        _as_openai(monkeypatch, stand_in)
        result = _run(stand_in, tmp_path)
        posts = stand_in.posts()
        alone = asyncio.run(_sent_by_the_sdk_alone(stand_in))
    assert result.exit_code == 0, result.output
    assert posts
    for post in posts:
        for name in AMBIENT_NAMES:
            assert post.every(name) == alone.every(name), name
    records = _records(tmp_path)
    assert _start(records)["decision_price"] == rate.record()
    for turn in _sent(records):
        assert turn["cost_usd"] > 0 and turn["cost_usd"] == cost_usd(turn["usage"], rate)


@pytest.mark.parametrize("spelling", ["Authorization", "authorization", "AUTHORIZATION"])
async def test_one_authorization_line_reaches_a_server_you_run_and_it_is_the_word(
    spelling: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every line of the request read as it arrived, so a second authorization beside the
    first would show. The gateway's line in each spelling, and the word is the one value that
    goes. `tests/test_decisions_api.py` holds the same rule to each kind of release the extra
    allows; this is the locked one, for real."""
    monkeypatch.setenv(
        "OPENAI_CUSTOM_HEADERS", f"{spelling}: Bearer {AMBIENT}\nX-Gateway-Key: {GATEWAY}"
    )
    with FakeOpenAIDecisions() as stand_in:
        await _decide_once(stand_in.url)
    (post,) = stand_in.posts()
    assert post.every("authorization") == ["Bearer local"]
    assert post.every("x-gateway-key") == []


@pytest.mark.parametrize(
    "lines",
    [
        f"Authorization: Bearer {AMBIENT}\nX-Gateway-Key: {GATEWAY}",
        f"authorization: Bearer {AMBIENT}\nX-Gateway-Key: {GATEWAY}",
        "OpenAI-Organization: gw-org\nOpenAI-Project: gw-proj",
        "openai-organization: gw-org",
        None,
    ],
    ids=[
        "a gateway's authorization",
        "in another spelling",
        "the account headers in the sdk's spelling",
        "in another spelling of its own",
        "unset",
    ],
)
async def test_at_openai_the_sdks_own_variables_arrive_as_from_the_sdk_alone(
    lines: str | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """OpenAI's own API is the one address this row sends the SDK's own variables to, as the
    SDK sends them by itself. So what arrives at a stand-in treated as OpenAI's, under every name
    those variables can fill, is what arrives from a client the SDK built by itself with the key
    and nothing else, whatever the locked release makes of the lines; and away from OpenAI none
    of it arrives at all."""
    if lines is None:
        monkeypatch.delenv("OPENAI_CUSTOM_HEADERS", raising=False)
    else:
        monkeypatch.setenv("OPENAI_CUSTOM_HEADERS", lines)
    with FakeOpenAIDecisions() as yours:
        await _decide_once(yours.url)
    with FakeOpenAIDecisions() as stand_in:
        _as_openai(monkeypatch, stand_in)
        await _decide_once(stand_in.url)
        alone = await _sent_by_the_sdk_alone(stand_in)
    ours = stand_in.posts()[0]
    for name in AMBIENT_NAMES:
        assert ours.every(name) == alone.every(name), name
    (at_yours,) = yours.posts()
    assert at_yours.every("authorization") == ["Bearer local"]
    for name in AMBIENT_NAMES[1:]:
        assert at_yours.every(name) == [], name


async def test_a_redirect_is_never_followed_so_nothing_reaches_where_it_points(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The SDK follows a redirect unless it is told not to, and a 307 tells it to send the same
    request again, the key aside: the account headers, and the body with the state in it, to
    wherever the redirect points, another port here and anybody's host in the world. So the
    stand-in that is treated as OpenAI's answers 307 to a second one on another port, and exactly
    one request arrives anywhere: the turn is an error, as any answer that is not an answer is."""
    import openai

    with FakeOpenAIDecisions() as elsewhere:
        with FakeOpenAIDecisions(script=[redirecting(f"{elsewhere.url}{PATH}")]) as stand_in:
            _as_openai(monkeypatch, stand_in)
            with pytest.raises(openai.APIStatusError) as e:
                await _decide_once(stand_in.url)
        assert e.value.status_code == 307
        assert len(stand_in.requests) == 1
        assert elsewhere.requests == [], "the redirect was followed"


def test_the_state_and_the_questions_on_the_wire_are_the_ones_the_record_describes(
    tmp_path: Path,
) -> None:
    """The input is the state as one JSON string, its length the turn's `state_chars`, and the
    four questions go named, in order, translated, with the options the record says were offered
    and `escalate` last. The string holds the state's letters as letters: the dash in every
    `last` field went to the model as JSON's six-character `\\u2014`, because OpenAI's `input` is
    text the API never decodes, and `state_chars` counted it so."""
    asked = build_questions([], {})
    with FakeOpenAIDecisions() as stand_in:
        result = _run(stand_in, tmp_path)
    assert result.exit_code == 0, result.output
    assert any("—" in post.body["input"] for post in stand_in.posts()), "no turn had a dash"
    for post, turn in zip(stand_in.posts(), _sent(_records(tmp_path)), strict=True):
        sent = post.body["input"]
        assert isinstance(sent, str) and len(sent) == turn["state_chars"]
        assert "\\u" not in sent, "the state went out as JSON's escapes"
        assert {"goal", "now"} <= set(json.loads(sent))
        questions = post.body["questions"]
        assert [q["name"] for q in questions] == list(asked)
        assert [q["type"] for q in questions] == [TYPES[q["type"]] for q in asked.values()]
        named = {q["name"]: q for q in questions}
        assert [c["value"] for c in named["next_verb"]["choices"]] == turn["labels"]
        assert [c["value"] for c in named["feasible"]["choices"]] == list(
            asked["feasible"]["criteria"]
        )


# ── what comes back ─────────────────────────────────────────────────────────────────────


def test_each_answer_is_read_by_its_name_carried_field_to_field_and_billed_as_counted(
    tmp_path: Path,
) -> None:
    """Every turn's record against the reply the stand-in sent for it. The vendor's `confidence`
    is the turn's confidence, which the stand-in puts below its top probability as OpenAI's own
    example does, so a reader that took the top probability for it would show here; the
    distribution comes back a mapping; each yes or no is the probability it was answered with;
    and the usage is what the server counted, never quackd's estimate. A turn after the first
    says how long the connection had been idle."""
    with FakeOpenAIDecisions() as stand_in:
        result = _run(stand_in, tmp_path)
    assert result.exit_code == 0, result.output
    sent = _sent(_records(tmp_path))
    assert len(sent) > 1, "one turn says nothing about the turn after it"
    for post, turn in zip(stand_in.posts(), sent, strict=True):
        answers = {answer["name"]: answer for answer in post.reply["answers"]}
        verb = answers["next_verb"]
        top = max(p["probability"] for p in verb["probabilities"])
        assert turn["choice"] == verb["choice"]
        assert turn["confidence"] == verb["confidence"] == CONFIDENCE != top
        assert turn["probabilities"] == {
            p["value"]: p["probability"] for p in verb["probabilities"]
        }
        assert turn["done"] == answers["done"]["probability"]
        assert turn["need_human"] == answers["need_human"]["probability"]
        assert turn["feasible"] == {
            "choice": answers["feasible"]["choice"],
            "confidence": answers["feasible"]["confidence"],
        }
        assert turn["usage_estimated"] is False
        assert turn["usage"]["input_tokens"] == post.reply["usage"]["input_tokens"]
    assert sent[0]["idle_s"] is None
    assert all(isinstance(turn["idle_s"], float) and turn["idle_s"] >= 0 for turn in sent[1:])


def _strict(constant: str) -> Any:
    """What a strict JSON reader does with `NaN`, `Infinity` and `-Infinity`, which Python's
    `json` reads without complaint."""
    raise ValueError(f"{constant} is not JSON")


def test_a_number_json_cannot_spell_never_reaches_the_events_file(tmp_path: Path) -> None:
    """The SDK's JSON reader takes `NaN` and `Infinity` from a server as readily as numbers, and
    the record carried them through to `transcript.jsonl` as bare `NaN`, which Python reads back
    and `jq` and `JSON.parse` refuse. Here every choice's distribution ends on a NaN and
    `feasible` comes back with an infinite confidence, and every line of the events file is
    strict JSON."""

    def unspellable(body: dict[str, Any]) -> tuple[int, Any]:
        status, reply = answering()(body)
        for answer in reply["answers"]:
            if answer["type"] == "choice":
                answer["probabilities"][-1]["probability"] = float("nan")
            if answer["name"] == "feasible":
                answer["confidence"] = float("inf")
        return status, reply

    with FakeOpenAIDecisions(script=[unspellable]) as stand_in:
        result = _run(stand_in, tmp_path)
    assert result.exit_code == 0, result.output
    assert "NaN" in json.dumps(stand_in.posts()[0].reply), "the stand-in sent no NaN"
    lines = next(tmp_path.rglob("transcript.jsonl")).read_text(encoding="utf-8").splitlines()
    for line in lines:
        json.loads(line, parse_constant=_strict)
    sent = _sent(_records(tmp_path))
    assert sent and all(turn["feasible"]["confidence"] is None for turn in sent)


def test_a_question_declined_on_every_turn_ends_each_on_refused_and_the_summary_counts_them(
    tmp_path: Path,
) -> None:
    with FakeOpenAIDecisions(script=[answering(refuse=("need_human",))]) as stand_in:
        result = _run(stand_in, tmp_path)
    assert result.exit_code == 0, result.output
    sent = _sent(_records(tmp_path))
    assert sent and len(sent) == len(stand_in.posts())
    for turn in sent:
        assert turn["gate"] == "refused" and turn["refused"] == ["need_human"]
    assert _summary(tmp_path)["decision"]["refusals"] == len(sent)


# ── what goes wrong ─────────────────────────────────────────────────────────────────────


def test_a_rate_limit_is_one_request_a_turn_never_retried_and_recorded_by_its_status(
    tmp_path: Path,
) -> None:
    """The SDK retries a 429 twice by default and sleeps before each retry, the first time for
    three eighths to a half of a second and the second for longer. Left to that, a turn here
    sends a second request, runs out of the stepper's second asleep before the third, and is
    recorded as a timeout, the 429 lost. With `max_retries=0` it is one request and the record
    says what it was. A turn the stepper cannot have is the model's, and the run carries on."""
    with FakeOpenAIDecisions(script="rate-limited") as stand_in:
        result = _run(stand_in, tmp_path)
    assert result.exit_code == 0, result.output
    sent = _sent(_records(tmp_path))
    assert sent and len(stand_in.posts()) == len(sent), "a turn sent more than one request"
    for turn in sent:
        assert turn["gate"] == "error" and turn["error"] == "RateLimitError: HTTP 429"
    assert _summary(tmp_path)["decision"]["errors"] == len(sent)


def test_a_reply_that_quotes_the_request_never_reaches_the_record(tmp_path: Path) -> None:
    """The SDK writes a 4xx's whole body into its exception's message, and this one quotes the
    state the stepper sent and carries a data URL besides. The record keeps the type and the
    status, and none of the rest."""
    with FakeOpenAIDecisions(script="quoting") as stand_in:
        result = _run(stand_in, tmp_path)
    assert result.exit_code == 0, result.output
    first = stand_in.posts()[0]
    words = _plain_words(json.loads(first.body["input"])["goal"])
    assert words in json.dumps(first.reply), "the reply did not quote the request"
    sent = _sent(_records(tmp_path))
    assert sent
    for turn in sent:
        assert turn["gate"] == "error" and turn["error"] == "BadRequestError: HTTP 400"
        recorded = json.dumps(turn)
        assert words not in recorded, "the state reached the record"
        assert "data:" not in recorded, "a data URL reached the record"
        assert re.search(r"[A-Za-z0-9+/=]{100,}", recorded) is None, "a base64 run reached it"


def test_a_body_that_is_not_json_costs_the_turn_and_not_the_run(tmp_path: Path) -> None:
    """The SDK hands back the text of a reply that is not JSON, the page a proxy answers with,
    and the backend refuses it as an answer, in quackd's own words, which name the type the real
    SDK handed back and quote nothing of the page."""
    with FakeOpenAIDecisions(script="not-json") as stand_in:
        result = _run(stand_in, tmp_path)
    assert result.exit_code == 0, result.output
    sent = _sent(_records(tmp_path))
    assert sent and len(stand_in.posts()) == len(sent)
    said = "UnreadableReply: POST /v1/decisions answered str rather than a JSON object"
    for turn in sent:
        assert turn["gate"] == "error" and turn["error"] == said
        assert "Bad Gateway" not in json.dumps(turn)


TURNS = 4
"""How many turns the test of a slow server asks. On the old clocks each turn's label was close
to a coin toss, so four of them all coming out right by chance is unlikely, and four seconds of
turns is what the suite pays for asking."""


async def test_a_slow_servers_turns_are_all_recorded_as_the_steppers_timeout() -> None:
    """The client's clock used to be the stepper's own second, the two came due together, and the
    same slow server's turns were recorded some as quackd's `no answer within 1.0 s` and some as
    the SDK's bare `APITimeoutError`. The client's clock sits above the stepper's now, so the
    stepper's own bound is the one that ends a turn, and every turn says the same thing."""
    slow = paced(answering(), wait_s=1.5 * TIMEOUT_S)
    with FakeOpenAIDecisions(script=[slow]) as stand_in:
        stepper = _stepper(make_decision_llm(OPENAI, url=stand_in.url))
        records = [
            (await stepper.advise(SimpleNamespace(features={}), cleared=True, budget="b")).record
            for _ in range(TURNS)
        ]
    said = f"TimeoutError: no answer within {TIMEOUT_S:.1f} s"
    assert [record.get("error") for record in records] == [said] * TURNS


async def test_a_bound_handed_to_decide_ends_a_request_answered_a_piece_at_a_time() -> None:
    """The SDK's own timeout is a limit on each phase of a request, and `decide(timeout_s=...)`
    used to hand it the bound, so a server that answered in pieces, each inside the limit, held
    the request until its last piece. Every piece here comes well inside the bound and the whole
    answer well after it, and the request ends at the bound with `TimeoutError`."""
    bound = TIMEOUT_S / 2
    pieces = 6
    trickle = paced(answering(), pieces=pieces, gap_s=bound / 2)
    whole = (pieces - 1) * bound / 2
    with FakeOpenAIDecisions(script=[trickle]) as stand_in:
        # Built before the clock starts, because what is timed is the request. Inside it, as it
        # used to be, the build could take longer than the request is allowed: the SDK's first
        # import in a cold process, and on the httpx of the 1.x releases a certificate store
        # loaded for every client. So the test failed on 1.66.0, and now and then when it was
        # run alone, for a reason that was not the bound.
        llm = make_decision_llm(OPENAI, url=stand_in.url)
        started = time.perf_counter()
        with pytest.raises(TimeoutError):
            await llm.decide(
                {"goal": "g"}, {"done": {"type": "noul", "instructions": "i"}}, timeout_s=bound
            )
        took = time.perf_counter() - started
    assert len(stand_in.posts()) == 1
    assert took < whole, f"waited {took:.2f} s, for the whole answer rather than the bound"


async def test_no_turn_stops_to_ask_which_platform_the_sdk_is_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Before a client's first request each release of the SDK read asks which platform it is on, in
    a worker thread, and on a cold Windows machine that is a WMI query or a subprocess. Asked inside
    the first stepper turn it could outlast the turn's second, which ended the turn as a timeout
    before anything was sent, and the record called it a request that went out and billed it. Made
    slower here than a whole turn, it is asked while the client is built, the client is handed the
    answer, and no turn asks it again."""
    import openai._base_client as sdk_base

    real = sdk_base.get_platform
    looks: list[float] = []

    def slow_look() -> Any:
        if not looks:
            time.sleep(1.5 * TIMEOUT_S)
        looks.append(time.perf_counter())
        return real()

    monkeypatch.setattr(sdk_base, "get_platform", slow_look)
    with FakeOpenAIDecisions() as stand_in:
        stepper = _stepper(make_decision_llm(OPENAI, url=stand_in.url))
        built = len(looks)
        records = [
            (await stepper.advise(SimpleNamespace(features={}), cleared=True, budget="b")).record
            for _ in range(2)
        ]
        posts = len(stand_in.posts())
    assert built, "nothing looked while the client was built, so this proves nothing"
    assert len(looks) == built, "a turn stopped to ask which platform the SDK is on"
    assert [record.get("error") for record in records] == [None, None]
    assert posts == len(records), "a turn recorded a request that never left"


def test_an_address_with_a_path_stops_the_run_before_the_sdk_sends_anything(
    tmp_path: Path,
) -> None:
    """With the SDK and a key both here, the typo is still refused while the flags are read."""
    with FakeOpenAIDecisions() as stand_in:
        result = _run(stand_in, tmp_path, url=f"{stand_in.url}/v1")
    assert result.exit_code == 1, result.output
    assert f"--decision-url {stand_in.url}/v1 has a path" in plain_text(result.output)
    assert stand_in.requests == []
    assert not list(tmp_path.iterdir()), "the run started before the typo was noticed"


# ── a frame on the wire (`--decision-frames`) ───────────────────────────────────────────


def test_a_frame_goes_as_the_reference_has_an_image_go_and_is_the_one_the_record_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Through the real SDK, with the stand-in treated as OpenAI's, since a frame goes nowhere
    else. Each request's `input` is one user message: the state as `input_text`, the very string
    the turn measured as `state_chars`, and the frame after it as `input_image`, a JPEG data URL
    at detail `low`. The stand-in checks both parts against the reference's shape and took every
    request. The JPEG it was sent is no wider or taller than the limit, and is the file the run
    kept and the hash its record names."""
    from quackd.agent.decision.frames import MAX_SIDE_PX

    with FakeOpenAIDecisions() as stand_in:
        _as_openai(monkeypatch, stand_in)
        result = _run(stand_in, tmp_path, "--decision-frames")
    assert result.exit_code == 0, result.output
    sent = _sent(_records(tmp_path))
    posts = stand_in.posts()
    assert sent and len(sent) == len(posts)
    (run_dir,) = tmp_path.iterdir()
    for post, turn in zip(posts, sent, strict=True):
        assert post.status == 200, post.reply
        (message,) = post.body["input"]
        assert set(message) == {"role", "content"} and message["role"] == "user"
        text, image = message["content"]
        assert text["type"] == "input_text" and len(text["text"]) == turn["state_chars"]
        assert json.loads(text["text"])["picture"].startswith("The image sent with this state")
        assert image["type"] == "input_image" and image["detail"] == "low"
        ((media_type, data, detail),) = post.pictures()
        assert (media_type, detail) == ("image/jpeg", "low")
        with Image.open(io.BytesIO(data)) as decoded:
            assert decoded.format == "JPEG" and max(decoded.size) <= MAX_SIDE_PX
        frame = turn["frame"]
        assert frame["sha256"] == hashlib.sha256(data).hexdigest() and frame["kept"] is True
        assert (run_dir / frame["path"]).read_bytes() == data
    assert _start(_records(tmp_path))["decision_llm"]["frames"] is True
    assert _summary(tmp_path)["decision"]["frames_sent"] == len(sent)


def test_a_reply_that_echoes_the_frame_back_never_puts_it_in_the_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The SDK writes a 400's whole body into its exception's message, and this one quotes the
    request with the frame in it and echoes the frame's `image_url` besides. Every turn records
    the type and the status and never a byte of either: no data URL and no run of base64
    anywhere in the transcript or the summary."""
    with FakeOpenAIDecisions(script="quoting") as stand_in:
        _as_openai(monkeypatch, stand_in)
        result = _run(stand_in, tmp_path, "--decision-frames")
    assert result.exit_code == 0, result.output
    first = stand_in.posts()[0]
    echoed = first.reply["error"]["image_url"]
    assert echoed.startswith("data:image/jpeg;base64,"), "the reply did not echo the frame"
    assert echoed in json.dumps(first.body), "the reply echoed some other picture"
    sent = _sent(_records(tmp_path))
    assert sent and all(turn["error"] == "BadRequestError: HTTP 400" for turn in sent)
    assert all(turn["frame"]["kept"] is True for turn in sent), "no frame went"
    for written in (next(tmp_path.rglob("transcript.jsonl")), next(tmp_path.rglob("summary.json"))):
        text = written.read_text(encoding="utf-8")
        assert "data:" not in text, f"a data URL reached {written.name}"
        assert re.search(r"[A-Za-z0-9+/=]{100,}", text) is None, f"base64 reached {written.name}"


def test_frames_to_a_server_of_your_own_stop_the_run_before_the_sdk_sends_anything(
    tmp_path: Path,
) -> None:
    """The stand-in at its own address is a server of your own, as `--decision-url
    http://127.0.0.1:8765` would be, and a frame goes to OpenAI's hosts alone: the run stops
    while the flags are read, with the SDK and a key both here."""
    with FakeOpenAIDecisions() as stand_in:
        result = _run(stand_in, tmp_path, "--decision-frames")
    assert result.exit_code == 1, result.output
    assert f"{stand_in.url} is not one of its hosts" in plain_text(result.output)
    assert stand_in.requests == []
    assert not list(tmp_path.iterdir()), "the run started before the refusal"


# ── what the suite's other halves lean on ───────────────────────────────────────────────


@pytest.mark.parametrize("call", list(fake_api.REFUSED.values()), ids=list(fake_api.REFUSED))
async def test_the_real_sdk_refuses_every_request_the_fake_refuses_on_its_behalf(
    call: dict[str, Any],
) -> None:
    """`tests/fake_decisions_api.py` refuses these because each release of the SDK that was read
    refuses them (`REFUSED`), and it is all CI ever runs, so this is where its word is checked
    against the real client's: each is a `TypeError` before anything is sent."""
    with FakeOpenAIDecisions() as stand_in:
        llm: Any = make_decision_llm(OPENAI, url=stand_in.url)
        with pytest.raises(TypeError):
            await llm._client.post(
                decisions_api.PATH, cast_to=object, body={"model": OPENAI.model}, **call
            )
    assert stand_in.requests == []


async def test_what_the_sdk_does_once_a_process_is_done_before_any_turn_here(
    _the_sdk_has_sent_before: Recorded, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A process's first request through the SDK does work no later request does, and every
    turn here has one second. That work is made slower than the second here, as a slow or busy
    machine could make it: the next request any client of the SDK sends stops for longer than a
    turn before it goes on. The request the module sends before its first test
    (`_the_sdk_has_sent_before`, `send_once_through_the_sdk`) takes that stop, and the stepper's
    first turn after it is answered. Left to that turn, it would have run out of its second
    before anything was sent."""
    import openai._base_client as sdk_base

    assert _the_sdk_has_sent_before.status == 200, "the module's request was never answered"
    request = sdk_base.AsyncAPIClient.request
    stopped: list[bool] = []

    async def first_request(self: Any, *args: Any, **kwargs: Any) -> Any:
        if not stopped:
            stopped.append(True)
            # Blocking, as an import from disk inside the first request blocks the event loop.
            time.sleep(1.5 * TIMEOUT_S)  # noqa: ASYNC251
        return await request(self, *args, **kwargs)

    monkeypatch.setattr(sdk_base.AsyncAPIClient, "request", first_request)
    await send_once_through_the_sdk()
    with FakeOpenAIDecisions() as stand_in:
        stepper = _stepper(make_decision_llm(OPENAI, url=stand_in.url))
        advice = await stepper.advise(SimpleNamespace(features={}), cleared=True, budget="b")
    assert stopped, "nothing stopped, so this proves nothing"
    assert advice.record.get("error") is None, advice.record.get("error")
    assert len(stand_in.posts()) == 1


# ── the suite's own guard, on the real client ───────────────────────────────────────────


@pytest.fixture
def _nothing_leaves_the_machine(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every lookup of a name and every connection made the standard library's way fails, so a
    request that got past the guard would end here rather than reach anybody."""

    def refuse(*args: Any, **kwargs: Any) -> Any:
        raise OSError("this test lets nothing leave the machine")

    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


@pytest.mark.usefixtures("_nothing_leaves_the_machine")
@pytest.mark.parametrize(
    "url", [None, "https://chatgpt.openai.com"], ids=["openais api", "another openai host"]
)
async def test_the_suites_guard_stops_the_real_clients_request_before_it_is_sent(
    url: str | None, _no_request_reaches_openai: list[str]
) -> None:
    """`tests/conftest.py` stands in front of the SDK's own `BaseClient._build_request`, where
    every request any of its clients sends is built, and this is the test that notices the SDK
    moving it: the guard is on the very module the client comes from, and the client, built at
    OpenAI's API with the key and at another host under openai.com with the word, has its
    request stopped there, built and never sent. `tests/test_decisions_api.py` shows the same
    guard firing where the SDK is not installed, on the fake's own `BaseClient`."""
    import openai._base_client as sdk_base

    assert _openai_base() is sdk_base, "the guard stands somewhere other than the SDK"
    llm: Any = make_decision_llm(OPENAI, url=url)
    with pytest.raises(ReachedOpenAI):
        await llm.decide({"goal": "g"}, {"done": {"type": "noul", "instructions": "i"}})
    assert _no_request_reaches_openai == [f"POST {url or OPENAI.url}/v1/decisions"]
    _no_request_reaches_openai.clear()


@pytest.mark.usefixtures("_nothing_leaves_the_machine")
async def test_the_suites_guard_stops_the_pilots_request_as_well(
    monkeypatch: pytest.MonkeyPatch, _no_request_reaches_openai: list[str]
) -> None:
    """The pilot reaches OpenAI through the same SDK, and the guard used to stand in front of the
    stepper's backend alone, so a test that built the real pilot with a key was free to pay OpenAI
    for a turn. Its request is stopped where every other one is, and the pilot reports the call
    failed, as it reports any call that does."""
    from quackd.agent.providers.base import ProviderError
    from quackd.agent.providers.openai import OpenAIProvider

    # the pilot is handed no address, so the SDK's own variable would otherwise be its address
    monkeypatch.delenv("OPENAI_BASE_URL")
    pilot = OpenAIProvider(api_key=KEY, api="chat")
    with pytest.raises(ProviderError, match="ReachedOpenAI"):
        await pilot.step("You fly a mock arm.", [], [])
    assert _no_request_reaches_openai == [f"POST {OPENAI.url}/v1/chat/completions"]
    _no_request_reaches_openai.clear()


# ── the live tests, held to their word against the stand-in ─────────────────────────────


def _row_at(stand_in: FakeOpenAIDecisions) -> Any:
    """The `openai` row with the stand-in for its address: a server you run, sent the word
    `local`, so a live test driven here needs no key and reaches nothing but this machine."""
    return dataclasses.replace(OPENAI, url=stand_in.url)


LIVE = {
    "the measurement": (
        live.test_a_real_decision_llm_answers_an_arm_state_and_says_how_long_it_took
    ),
    "the fields": live.test_a_real_answer_carries_the_fields_the_router_reads,
    "the safety claim": live.test_a_real_answer_before_a_verdict_cannot_reach_a_moving_verb,
    "many requests": live.test_openais_answers_come_back_named_and_how_long_they_take_over_many,
    "the frames": live.test_openais_answers_with_a_frame_beside_the_state_and_how_long_they_take,
    "the options": live.test_how_many_options_one_choice_may_carry,
}
"""Every live test, called as a function with the row it is handed, so it can be driven here."""

ON_ONE_TURN = ("the measurement", "the fields", "the safety claim")
"""The live tests that read one stepper turn, and so have the stepper's second for it."""

SHOW_A_FRAME = ("the frames",)
"""The live tests that send a frame, which goes to OpenAI's own hosts alone, so the stand-in each
is driven against here is treated as OpenAI's (`_as_openai`) and is sent the fake key."""


@pytest.mark.parametrize("name", list(LIVE))
async def test_every_live_test_fails_naming_the_status_when_the_key_is_refused(
    name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A wrong key is every request answered 401. Each live test fails, and says so: none dies on
    a bare `KeyError` for the first field it missed, as the one that checks the router's fields
    did, and none passes, as the safety claim did and the one about options did, taking a 401
    for the API's bound on a choice."""
    refused = FakeOpenAIDecisions(script=[failing(401)])
    with refused as stand_in, pytest.raises(AssertionError) as failed:
        if name in SHOW_A_FRAME:
            _as_openai(monkeypatch, stand_in)
        await LIVE[name](_row_at(stand_in))
    assert "AuthenticationError: HTTP 401" in str(failed.value), str(failed.value)


@pytest.mark.parametrize("name", ON_ONE_TURN)
async def test_every_live_test_on_one_turn_fails_naming_a_first_turn_that_ran_out(
    name: str,
) -> None:
    """A first turn slower than the stepper's second has no answer in it either, and the test
    reading it says that the stepper's second ran out, rather than which field it missed."""
    slow = paced(answering(), wait_s=1.5 * TIMEOUT_S)
    with FakeOpenAIDecisions(script=[slow]) as stand_in, pytest.raises(AssertionError) as failed:
        await LIVE[name](_row_at(stand_in))
    said = f"TimeoutError: no answer within {TIMEOUT_S:.1f} s"
    assert said in str(failed.value), str(failed.value)


@pytest.mark.parametrize("name", ["the fields", "the safety claim"])
async def test_a_live_test_that_reads_an_answer_fails_naming_the_question_declined(
    name: str,
) -> None:
    """A `next_verb` the model declined has no choice, no confidence and no distribution. The test
    of the router's fields used to fail on the empty distribution with the sentence meant for a
    renamed field, and the safety claim passed. Both fail now, naming the question declined."""
    declines = FakeOpenAIDecisions(script=[answering(refuse=("next_verb",))])
    with declines as stand_in, pytest.raises(AssertionError, match="the model declined next_verb"):
        await LIVE[name](_row_at(stand_in))


async def test_the_measurements_measure_a_turn_with_a_question_declined_and_say_so(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A question declined is still a request answered, so the tests that measure take it, and
    print what was declined beside what they measured."""
    declines = answering(refuse=("next_verb", "pick"))
    with FakeOpenAIDecisions(script=[declines]) as stand_in:
        for name in ("the measurement", "many requests", "the options"):
            await LIVE[name](_row_at(stand_in))
    printed = capsys.readouterr().out
    assert "gate=refused" in printed and "refused: ['next_verb']" in printed
    assert "questions declined: [['next_verb']" in printed
    assert "options: taken, and the question declined" in printed


async def test_the_measurement_with_a_frame_prints_what_the_frame_added_and_what_was_declined(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Against a stand-in treated as OpenAI's, which counts a quarter of each body's characters
    as its tokens, so a body with a frame in it counts more than one without: the test sends
    every request it says it sends, the frame in each, prints both counts and what was declined,
    and passes on an answer with a question declined, as the other measurements do."""
    from quackd.agent.decision.frames import MAX_SIDE_PX

    declines = answering(refuse=("next_verb",))
    with FakeOpenAIDecisions(script=[declines]) as stand_in:
        _as_openai(monkeypatch, stand_in)
        await LIVE["the frames"](_row_at(stand_in))
        posts = stand_in.posts()
    printed = capsys.readouterr().out
    assert len(posts) == 1 + live.SAMPLES
    assert posts[0].pictures() == [] and all(len(post.pictures()) == 1 for post in posts[1:])
    assert f"requests with a {MAX_SIDE_PX} by 288 JPEG" in printed
    (counts,) = re.findall(r"input_tokens without the frame: (\d+); with it: (\d+)", printed)
    assert int(counts[1]) > int(counts[0]), counts
    assert "questions declined: [['next_verb']" in printed


async def test_the_first_live_test_prints_what_it_measured_before_it_fails(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Whatever a turn came to, its figures reach the terminal before any check can stop them,
    because those figures are what the test is for."""
    with FakeOpenAIDecisions(script=[failing(401)]) as stand_in, pytest.raises(AssertionError):
        await LIVE["the measurement"](_row_at(stand_in))
    printed = capsys.readouterr().out
    assert f"{OPENAI.name} {OPENAI.model}:" in printed
    assert "error: AuthenticationError: HTTP 401" in printed
