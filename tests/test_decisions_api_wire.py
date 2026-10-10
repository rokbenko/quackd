"""`--decision-llm openai` through the real SDK and a real `quackd run`, against a stand-in.

Skipped wherever the SDK is not installed, which includes CI: nothing in the suite installs a
provider SDK. To run it, sync an environment with the extra and run pytest there:

    UV_PROJECT_ENVIRONMENT=<scratch> uv sync --frozen --extra dev --extra openai

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

import json
import re
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("openai")

from typer.testing import CliRunner

from quackd.agent.decision import decisions_api
from quackd.agent.decision.base import TIMEOUT_S
from quackd.agent.decision.catalogue import PRESETS
from quackd.agent.decision.factory import make_decision_llm, resolve_decision_price
from quackd.agent.decision.stepper import Call, Stepper, build_questions
from quackd.agent.providers.pricing import cost_usd
from quackd.agent.transcript import Transcript
from quackd.cli import app
from tests.conftest import ReachedOpenAI, openai_address, plain_text
from tests.fake_openai_decisions import (
    CONFIDENCE,
    PATH,
    FakeOpenAIDecisions,
    answering,
    paced,
    redirecting,
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


def _run(stand_in: FakeOpenAIDecisions, tmp_path: Path, *, url: str | None = None) -> Any:
    """The read-only arm task in shadow mode, so the scripted pilot flies it and the stepper is
    asked on every turn without anything it says reaching the arm."""
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
    """Tell quackd's own rule that the stand-in is OpenAI's API, so the key and the account
    headers go to it as they would to OpenAI. The suite's guard keeps a rule of its own, sees
    127.0.0.1 and lets the request through."""
    real = decisions_api.is_openai_host
    monkeypatch.setattr(
        decisions_api,
        "is_openai_host",
        lambda url: url.strip().rstrip("/") == stand_in.url or real(url),
    )


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
        assert post.headers["authorization"] == "Bearer local"
        assert post.headers["user-agent"].startswith("AsyncOpenAI/Python"), "not the real SDK"
        for ambient in ("x-gateway-key", "openai-organization", "openai-project"):
            assert ambient not in post.headers, ambient
        assert set(post.body) == {"model", "input", "questions"}
        assert post.body["model"] == OPENAI.model
    start = _start(_records(tmp_path))
    assert start["decision_llm"] == {"name": "openai", "model": OPENAI.model, "url": stand_in.url}
    assert start["decision_price"]["source"] == "self-hosted", "a server you run is free"


def test_at_openais_own_address_the_key_and_the_account_headers_go_and_the_gateways_do_not(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """What the SDK sends where the key is meant to go, seen without the network: quackd's own
    rule is told the stand-in is OpenAI's API. The suite's guard keeps a rule of its own, sees
    127.0.0.1 and lets the requests through. The key goes as the bearer token, and the gateway's
    `Authorization` line never replaces it; OpenAI's account headers go as the SDK sends them; the
    gateway's own header does not; and the turns are costed at OpenAI's published rate."""
    rate = OPENAI.price
    assert rate is not None
    with FakeOpenAIDecisions() as stand_in:
        real = decisions_api.is_openai_host
        monkeypatch.setattr(
            decisions_api,
            "is_openai_host",
            lambda url: url.rstrip("/") == stand_in.url or real(url),
        )
        result = _run(stand_in, tmp_path)
    assert result.exit_code == 0, result.output
    posts = stand_in.posts()
    assert posts
    for post in posts:
        assert post.headers["authorization"] == f"Bearer {KEY}"
        assert post.headers["openai-organization"] == "org-x"
        assert post.headers["openai-project"] == "proj-y"
        assert "x-gateway-key" not in post.headers
    records = _records(tmp_path)
    assert _start(records)["decision_price"] == rate.record()
    for turn in _sent(records):
        assert turn["cost_usd"] > 0 and turn["cost_usd"] == cost_usd(turn["usage"], rate)


@pytest.mark.parametrize("at_openai", [False, True], ids=["a server you run", "openai"])
@pytest.mark.parametrize("spelling", ["Authorization", "authorization", "AUTHORIZATION"])
async def test_one_authorization_line_leaves_and_it_is_quackds_whatever_the_gateway_spelled(
    spelling: str, at_openai: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every line of the request read as it arrived, so a second authorization beside the
    first would show. The gateway's line in each spelling, and the key or the word is the one
    value that goes. `tests/test_decisions_api.py` holds the same rule to each kind of release
    the extra allows; this is the locked one, for real."""
    monkeypatch.setenv(
        "OPENAI_CUSTOM_HEADERS", f"{spelling}: Bearer {AMBIENT}\nX-Gateway-Key: {GATEWAY}"
    )
    with FakeOpenAIDecisions() as stand_in:
        if at_openai:
            _as_openai(monkeypatch, stand_in)
        await _decide_once(stand_in.url)
    (post,) = stand_in.posts()
    assert post.every("authorization") == [f"Bearer {KEY if at_openai else 'local'}"]
    assert post.every("x-gateway-key") == []


@pytest.mark.parametrize("at_openai", [False, True], ids=["a server you run", "openai"])
@pytest.mark.parametrize(
    "lines",
    ["OpenAI-Organization: gw-org\nOpenAI-Project: gw-proj", "openai-organization: gw-org"],
    ids=["the sdk's own spelling", "another spelling"],
)
async def test_the_account_headers_carry_the_variables_at_openai_whatever_the_gateway_names(
    lines: str, at_openai: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A gateway's line named in the SDK's own spelling used to take the place of the entry the
    SDK fills from `OPENAI_ORG_ID`, so the request reached OpenAI with no organisation at all and
    was billed to the key's default. At OpenAI the variables' values go, once each, whatever the
    gateway's line says; anywhere else neither goes."""
    monkeypatch.setenv("OPENAI_CUSTOM_HEADERS", lines)
    with FakeOpenAIDecisions() as stand_in:
        if at_openai:
            _as_openai(monkeypatch, stand_in)
        await _decide_once(stand_in.url)
    (post,) = stand_in.posts()
    assert post.every("openai-organization") == (["org-x"] if at_openai else [])
    assert post.every("openai-project") == (["proj-y"] if at_openai else [])


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
    and `escalate` last."""
    asked = build_questions([], {})
    with FakeOpenAIDecisions() as stand_in:
        result = _run(stand_in, tmp_path)
    assert result.exit_code == 0, result.output
    for post, turn in zip(stand_in.posts(), _sent(_records(tmp_path)), strict=True):
        sent = post.body["input"]
        assert isinstance(sent, str) and len(sent) == turn["state_chars"]
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
    and the backend refuses it as an answer."""
    with FakeOpenAIDecisions(script="not-json") as stand_in:
        result = _run(stand_in, tmp_path)
    assert result.exit_code == 0, result.output
    sent = _sent(_records(tmp_path))
    assert sent and len(stand_in.posts()) == len(sent)
    for turn in sent:
        assert turn["gate"] == "error" and turn["error"] == "ValueError"


TURNS = 4
"""How many turns the test of a slow server asks. On the old clocks each turn's label was close
to a coin toss, so four of them all coming out right by chance is unlikely, and four seconds of
turns is what the suite pays for asking."""


async def test_a_slow_servers_turns_are_all_recorded_as_the_steppers_timeout() -> None:
    """The client's clock used to be the stepper's own second, the two came due together, and the
    same slow server's turns were recorded some as quackd's `no answer within 1.0 s` and some as
    the SDK's bare `APITimeoutError`. The client's clock sits above the stepper's now, so the
    stepper's `wait_for` is the one that ends a turn, and every turn says the same thing."""
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
        started = time.perf_counter()
        with pytest.raises(TimeoutError):
            await _decide_once(stand_in.url, timeout_s=bound)
        took = time.perf_counter() - started
    assert len(stand_in.posts()) == 1
    assert took < whole, f"waited {took:.2f} s, for the whole answer rather than the bound"


async def test_no_turn_stops_to_ask_which_platform_the_sdk_is_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Before a client's first request every release asks which platform it is on, in a worker
    thread, and on a cold Windows machine that starts a subprocess. Asked inside the first stepper
    turn it could outlast the turn's second, which ended the turn as a timeout before anything was
    sent, and the record called it a request that went out and billed it. Made slower here than a
    whole turn, it is asked while the client is built, the client is handed the answer, and no
    turn asks it again."""
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


# ── the suite's own guard, on the real client ───────────────────────────────────────────


async def test_the_suites_guard_knows_the_real_client_and_stops_it_at_openais_address(
    monkeypatch: pytest.MonkeyPatch, _no_decision_request_reaches_openai: list[str]
) -> None:
    """`tests/conftest.py` knows the real client by its module and where it sends by its
    `base_url`, both the SDK's own, so this is the test that notices the SDK moving either.
    `tests/test_decisions_api.py` proves the guard fires and fails a test where the SDK is not
    installed; here the client is the SDK's own, built at OpenAI's address with a key that is
    not one. Its `post` is replaced on the class with one that answers in this process, so that
    nothing could leave the machine even if the guard did not fire."""
    import openai

    handed: list[str] = []

    async def post(self: Any, path: str, **kwargs: Any) -> Any:
        handed.append(path)
        return {"answers": [], "model": OPENAI.model, "usage": None}

    monkeypatch.setattr(openai.AsyncOpenAI, "post", post)
    llm: Any = make_decision_llm(OPENAI)
    assert type(llm._client) is openai.AsyncOpenAI
    assert openai_address(llm._client) == f"{OPENAI.url}/v1/"
    with pytest.raises(ReachedOpenAI):
        await llm.decide({"goal": "g"}, {"done": {"type": "noul", "instructions": "i"}})
    assert handed == [], "the request reached the client"
    assert _no_decision_request_reaches_openai == [f"POST {OPENAI.url}/v1/decisions"]
    _no_decision_request_reaches_openai.clear()
