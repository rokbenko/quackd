"""OpenAI's Decisions API behind `--decision-llm openai`: the translation, the key and the address.

`test_decision_llms.py` holds the table every row is checked against, this one included. This
file is about what only this row does. It speaks another wire format, so its questions are
translated out and its answers back, and its client is an SDK that reads half its configuration
out of the environment, so where the key goes and which headers ride along are decided by quackd
rather than left to that SDK's defaults.

Nothing here needs the `openai` package: CI installs no provider SDK, and every test that builds a
client builds `tests/fake_decisions_api.py` in its place. Nothing here reaches the network.
"""

from __future__ import annotations

import dataclasses
import json
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from quackd.agent.decision import factory
from quackd.agent.decision.base import (
    TIMEOUT_S,
    DecisionError,
    DecisionMissingKey,
    DecisionNotInstalled,
)
from quackd.agent.decision.catalogue import PRESETS
from quackd.agent.decision.decisions_api import (
    DecisionsAPILLM,
    ambient_headers,
    is_openai_host,
    to_questions,
    to_result,
)
from quackd.agent.decision.factory import (
    decision_llm_is_available,
    make_decision_llm,
    resolve_decision_address,
    resolve_decision_price,
    resolve_decision_url,
)
from quackd.agent.decision.stepper import ESCALATE, Call, Stepper, build_questions, labels
from quackd.agent.providers.pricing import cost_usd
from tests import fake_decisions_api as fake_api
from tests.conftest import plain_text

OPENAI = PRESETS["openai"]
KEY = "sk-test-not-a-real-key"
"""Never a real one: nothing here leaves the machine, and a fake key is what proves it."""

#: Every variable the SDK reads for itself that this backend has a rule about.
SDK_VARIABLES = (
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "OPENAI_CUSTOM_HEADERS",
    "OPENAI_ORG_ID",
    "OPENAI_PROJECT_ID",
)


@pytest.fixture(autouse=True)
def _no_ambient_openai(monkeypatch: pytest.MonkeyPatch) -> None:
    """A developer who pilots with OpenAI has these in a `.env`, and the CLI loads it. Empty
    rather than deleted, because an empty value survives `load_dotenv` and a deleted one does
    not, and empty reads as unset everywhere this backend reads them."""
    for name in SDK_VARIABLES:
        monkeypatch.setenv(name, "")


def _built(
    monkeypatch: pytest.MonkeyPatch, url: str | None = None, **kwargs: Any
) -> tuple[Any, fake_api.FakeOpenAI]:
    fake = fake_api.install(
        monkeypatch, fake_api.FakeOpenAI(answers=kwargs.pop("answers", fake_api.turn("stop")))
    )
    return make_decision_llm(OPENAI, url=url, **kwargs), fake


def _stepper(llm: Any) -> Stepper:
    """A stepper offered the brake and nothing else, built bare: what is under test is the
    backend's answer and how it is read, and a registry of verbs would only be scenery."""
    stepper = Stepper(
        mode="on",
        goal="a goal",
        llm=llm,
        name=llm.name,
        model=llm.model,
        price=resolve_decision_price(OPENAI),
    )
    stepper.calls["stop"] = Call(name="stop", arguments={}, label="stop")
    stepper.classes["stop"] = "brake"
    stepper.what["stop"] = "the brake"
    stepper.early.add("stop")
    return stepper


async def _advise(stepper: Stepper) -> Any:
    return await stepper.advise(SimpleNamespace(features={}), cleared=True, budget="b")


# ── what goes out ───────────────────────────────────────────────────────────────────────


def test_the_four_questions_go_out_in_order_each_named_in_openais_shape() -> None:
    """A list rather than a mapping, a `predicate` where System One says `noul`, and a choice's
    criteria as `choices`, every one named because the name is what an answer comes back by."""
    offered = [
        Call(name="stop", arguments={}, label="stop"),
        Call(name="gripper", arguments={"open": True}, label="gripper(open=true)"),
    ]
    what = {"stop": "the brake", "gripper(open=true)": "opens the gripper"}
    questions = build_questions(offered, what)
    sent = to_questions(questions)
    assert [q["name"] for q in sent] == list(questions)
    assert [q["type"] for q in sent] == ["choice", "predicate", "predicate", "choice"]
    for q in sent:
        assert q["instructions"] == questions[q["name"]]["instructions"]
    verb = sent[0]
    assert [c["value"] for c in verb["choices"]] == labels(offered)
    assert verb["choices"][-1]["value"] == ESCALATE, "the way out is last, as it was built"
    assert [c["description"] for c in verb["choices"]] == list(
        questions["next_verb"]["criteria"].values()
    )
    assert all(isinstance(c["value"], str) for q in sent for c in q.get("choices", ()))
    assert "choices" not in sent[1] and "criteria" not in sent[1]


def test_a_score_goes_out_as_levels_in_the_order_they_were_written() -> None:
    sent = to_questions(
        {"tidy": {"type": "score", "instructions": "how tidy", "criteria": {"a": "x", "b": "y"}}}
    )
    assert sent == [
        {
            "type": "score",
            "name": "tidy",
            "instructions": "how tidy",
            "levels": [{"label": "a", "description": "x"}, {"label": "b", "description": "y"}],
        }
    ]


def test_a_question_the_api_has_no_type_for_is_refused_by_name() -> None:
    with pytest.raises(ValueError, match="free_text"):
        to_questions({"essay": {"type": "free_text", "instructions": "write"}})


async def test_one_request_goes_to_decisions_with_the_three_fields_the_api_takes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The generic request, at the path the SDK's own resource would use, handing back the
    decoded body untouched, and no field the API does not take."""
    monkeypatch.setenv("OPENAI_API_KEY", KEY)
    llm, fake = _built(monkeypatch)
    await llm.decide({"goal": "g"}, {"done": {"type": "noul", "instructions": "i"}})
    (post,) = fake.posts
    assert post["path"] == "/decisions"
    assert post["cast_to"] is object
    assert set(post["body"]) == {"model", "input", "questions"}
    assert post["body"]["model"] == OPENAI.model
    assert post["options"] == {}, "no per-request timeout unless one is asked for"


async def test_a_timeout_handed_to_decide_bounds_that_request_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", KEY)
    llm, fake = _built(monkeypatch)
    await llm.decide({"goal": "g"}, {}, timeout_s=1.5)
    await llm.decide({"goal": "g"}, {})
    assert [p["options"] for p in fake.posts] == [{"timeout": 1.5}, {}]


async def test_the_state_goes_out_as_the_json_the_stepper_measures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`state_chars` is the size of what was sent, so the input is that very string."""
    monkeypatch.setenv("OPENAI_API_KEY", KEY)
    llm, fake = _built(monkeypatch)
    advice = await _advise(_stepper(llm))
    sent = fake.posts[0]["body"]["input"]
    assert isinstance(sent, str)
    assert len(sent) == advice.record["state_chars"]
    assert json.loads(sent)["goal"] == "a goal"


# ── what comes back ─────────────────────────────────────────────────────────────────────


def test_answers_are_read_by_the_name_they_carry_and_never_by_position() -> None:
    """Shuffled, with one under a name nobody asked and one with no name at all. Read by
    position, `need_human`'s yes would have landed on `done`."""
    data = fake_api.decision(
        [
            fake_api.predicate("need_human", 0.9),
            fake_api.predicate("done", 0.1),
            fake_api.predicate("somebody_elses", 1.0),
            fake_api.predicate(None, 1.0),
            fake_api.choice("next_verb", "stop", 0.8),
        ]
    )
    result = to_result(data, ["next_verb", "done", "need_human"])
    assert result["answers"]["done"] == {"noul": 0.1}
    assert result["answers"]["need_human"] == {"noul": 0.9}
    assert set(result["answers"]) == {"next_verb", "done", "need_human"}


def test_the_first_answer_under_a_name_is_the_one_kept() -> None:
    data = fake_api.decision([fake_api.predicate("done", 0.2), fake_api.predicate("done", 0.95)])
    assert to_result(data, ["done"])["answers"]["done"] == {"noul": 0.2}


def test_a_distribution_comes_back_as_a_mapping_of_numbers() -> None:
    """The stepper calls `.items()` on it, and a boolean is no more a probability here than it
    is a confidence in `stepper._finite`."""
    answer = fake_api.choice(
        "next_verb", "stop", 0.7, {"report_state": 0.2, "escalate": True, "observe": "high"}
    )
    answer["probabilities"].append({"probability": 0.1})
    result = to_result(fake_api.decision([answer]), ["next_verb"])
    assert result["answers"]["next_verb"] == {
        "choice": "stop",
        "confidence": 0.7,
        "probabilities": {"stop": 0.7, "report_state": 0.2},
    }


def test_a_field_the_api_left_out_stays_out() -> None:
    """Missing rather than `None`, so the stepper reads it as it reads a field a System One
    server dropped: a choice with no confidence is unreadable, not a choice of `None`."""
    answer = fake_api.choice("next_verb", "stop")
    del answer["confidence"]
    kept = to_result(fake_api.decision([answer]), ["next_verb"])["answers"]["next_verb"]
    assert "confidence" not in kept and kept["choice"] == "stop"
    bare = to_result(fake_api.decision([{"type": "predicate", "name": "done"}]), ["done"])
    assert "done" not in bare["answers"]


def test_a_score_comes_back_keyed_by_level() -> None:
    answer = {
        "type": "score",
        "name": "tidy",
        "score": 1.1,
        "confidence": 0.55,
        "probabilities": [
            {"value": 0, "label": "a", "probability": 0.1},
            {"value": 1, "label": "b", "probability": 0.7},
        ],
    }
    result = to_result(fake_api.decision([answer]), ["tidy"])
    assert result["answers"]["tidy"] == {
        "score": 1.1,
        "confidence": 0.55,
        "probabilities": {"a": 0.1, "b": 0.7},
    }


def test_a_refusal_answers_nothing_and_is_named_beside_the_answers() -> None:
    data = fake_api.decision(fake_api.turn("stop", refuse=("done",)))
    result = to_result(data, ["next_verb", "done", "need_human", "feasible"])
    assert "done" not in result["answers"]
    assert result["refused"] == ["done"]
    assert set(result["answers"]) == {"next_verb", "need_human", "feasible"}


def test_usage_and_model_pass_through_untouched() -> None:
    data = fake_api.decision([])
    result = to_result(data, [])
    assert result["usage"] == fake_api.USAGE and result["model"] == data["model"]


def test_answers_that_are_not_a_list_are_an_error_rather_than_an_empty_turn() -> None:
    with pytest.raises(ValueError, match="list of answers"):
        to_result({"answers": {"done": {"noul": 0.0}}}, ["done"])


async def test_a_body_that_is_not_json_is_an_error_and_is_not_quoted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The SDK hands back text when the body is not JSON. A gateway's error page is not a
    reason the record needs to carry."""
    monkeypatch.setenv("OPENAI_API_KEY", KEY)
    fake = fake_api.install(monkeypatch, fake_api.FakeOpenAI(script=["<html>bad gateway</html>"]))
    llm = make_decision_llm(OPENAI)
    with pytest.raises(ValueError) as e:
        await llm.decide({"goal": "g"}, {})
    assert "rather than a JSON object" in str(e.value) and "html" not in str(e.value)
    assert fake.posts


# ── a turn, end to end through the stepper ──────────────────────────────────────────────


async def test_a_turn_it_answers_is_taken_and_billed_at_what_openai_counted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Measured rather than estimated, at the row's published rate: `stepper._usage` reads the
    API's own `input_tokens`, which is what OpenAI bills."""
    monkeypatch.setenv("OPENAI_API_KEY", KEY)
    llm, _fake = _built(monkeypatch)
    stepper = _stepper(llm)
    advice = await _advise(stepper)
    assert advice.record["gate"] == "taken" and advice.call is not None
    counted = fake_api.USAGE["input_tokens"]
    assert advice.record["usage_estimated"] is False
    assert advice.record["usage"]["input_tokens"] == counted
    assert advice.record["cost_usd"] == cost_usd(
        {"input_tokens": counted, "output_tokens": 0}, stepper.price
    )


@pytest.mark.parametrize("declined", ["next_verb", "done", "need_human"])
async def test_a_question_openai_declined_never_moves_the_body(
    declined: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A refusal is no answer at all, and a question nobody answered hands the turn to the
    model rather than reading as a confident no."""
    monkeypatch.setenv("OPENAI_API_KEY", KEY)
    llm, _fake = _built(monkeypatch, answers=fake_api.turn("stop", refuse=(declined,)))
    advice = await _advise(_stepper(llm))
    assert advice.call is None
    assert advice.record["gate"] != "taken"


async def test_a_request_that_fails_costs_the_turn_and_not_the_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", KEY)
    fake_api.install(
        monkeypatch,
        fake_api.FakeOpenAI(raises=fake_api.RateLimitError("slow down", status_code=429)),
    )
    advice = await _advise(_stepper(make_decision_llm(OPENAI)))
    assert advice.call is None and advice.record["gate"] == "error"
    assert advice.record["error"].startswith("RateLimitError")


# ── the client, and where the key goes ──────────────────────────────────────────────────


def test_the_client_is_built_with_the_address_one_second_and_no_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", KEY)
    llm, fake = _built(monkeypatch)
    assert fake.kwargs["base_url"] == f"{OPENAI.url}/v1"
    assert fake.kwargs["timeout"] == TIMEOUT_S
    assert fake.kwargs["max_retries"] == 0
    assert fake.kwargs["api_key"] == KEY
    assert fake.headers["Authorization"] == f"Bearer {KEY}"
    assert (llm.name, llm.model, llm.url) == ("openai", OPENAI.model, OPENAI.url)


def test_the_sdks_own_address_variable_never_moves_it(monkeypatch: pytest.MonkeyPatch) -> None:
    """Given no address, the SDK follows `OPENAI_BASE_URL`, which `.env.example` tells a reader
    to set for a model on their own machine. The hosted key would follow it there."""
    monkeypatch.setenv("OPENAI_API_KEY", KEY)
    monkeypatch.setenv("OPENAI_BASE_URL", "http://127.0.0.1:11434/v1")
    _llm, fake = _built(monkeypatch)
    assert fake.kwargs["base_url"] == f"{OPENAI.url}/v1"


def test_quackds_own_address_variable_never_moves_it_either(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", KEY)
    monkeypatch.setenv(factory.ENV_URL, "http://127.0.0.1:8765")
    assert resolve_decision_url(OPENAI) == OPENAI.url
    _llm, fake = _built(monkeypatch)
    assert fake.kwargs["base_url"] == f"{OPENAI.url}/v1" and fake.kwargs["api_key"] == KEY


@pytest.mark.parametrize(
    ("url", "keyed"),
    [
        ("https://api.openai.com", True),
        ("https://us.api.openai.com", True),
        ("https://eu.api.openai.com", True),
        ("https://ae.api.openai.com", True),
        ("https://api.openai.com:443", True),
        # a host is case-insensitive, and `/` alone is the address with a slash on the end
        ("https://API.OpenAI.com/", True),
        ("http://127.0.0.1:8765", False),
        ("http://api.openai.com", False),
        ("https://api.openai.com:8443", False),
        # the trailing dot names the same machine in DNS, and is still not a host quackd named
        ("https://api.openai.com.", False),
        ("https://api.openai.com.evil.example", False),
        ("https://user:pw@api.openai.com", False),
        # read by `urlsplit` as api.openai.com with a user, and by a WHATWG client as evil.example
        ("https://evil.example\\@api.openai.com", False),
    ],
)
def test_the_key_goes_to_openais_own_api_and_nowhere_else(
    url: str, keyed: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every other address is a server somebody runs, and is sent the word every server you run
    is sent. The client is built from the same parts the decision was made on."""
    monkeypatch.setenv("OPENAI_API_KEY", KEY)
    _llm, fake = _built(monkeypatch, url=url)
    sent = KEY if keyed else "local"
    assert fake.kwargs["api_key"] == sent
    assert fake.headers["Authorization"] == f"Bearer {sent}"
    assert is_openai_host(url) is keyed
    assert fake.kwargs["base_url"] == url.rstrip("/") + "/v1"


def test_a_missing_key_at_openai_is_a_sentence_naming_the_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_api.install(monkeypatch, fake_api.FakeOpenAI())
    with pytest.raises(DecisionMissingKey, match="OPENAI_API_KEY"):
        make_decision_llm(OPENAI)


def test_a_server_you_run_is_built_with_no_key_in_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _llm, fake = _built(monkeypatch, url="http://127.0.0.1:8765")
    assert fake.kwargs["api_key"] == "local"


# ── the headers the SDK would add by itself ─────────────────────────────────────────────


@pytest.mark.parametrize("url", ["https://api.openai.com", "http://127.0.0.1:8765"])
def test_headers_from_the_sdks_own_variable_never_ride_along(
    url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`OPENAI_CUSTOM_HEADERS` is where a gateway's credential lives for a pilot that goes
    through one. It is that gateway's, so it goes neither to OpenAI nor to a server you run, and
    its own `Authorization` line, which the SDK drops only because quackd names one, never
    replaces the key or the word."""
    monkeypatch.setenv("OPENAI_API_KEY", KEY)
    monkeypatch.setenv(
        "OPENAI_CUSTOM_HEADERS",
        "Authorization: Bearer sk-ambient\nX-Gateway-Key: gk-secret\n"
        "  cf-aig-authorization : Bearer gw\nnot a header line",
    )
    _llm, fake = _built(monkeypatch, url=url)
    headers = fake.headers
    sent = KEY if url.startswith("https") else "local"
    assert headers["Authorization"] == f"Bearer {sent}"
    assert isinstance(headers["X-Gateway-Key"], fake_api.Omit)
    assert isinstance(headers["cf-aig-authorization"], fake_api.Omit)
    assert not any(isinstance(v, str) for k, v in headers.items() if k != "Authorization")


def test_an_ambient_authorization_line_in_any_case_never_takes_out_the_one_sent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The SDK merges headers without regard to case, so an omitted `authorization` would remove
    the `Authorization` quackd sends, and the request would leave with no key at all."""
    monkeypatch.setenv("OPENAI_CUSTOM_HEADERS", "authorization: Bearer sk-ambient")
    _llm, fake = _built(monkeypatch, url="http://127.0.0.1:8765")
    assert [k for k in fake.headers if k.lower() == "authorization"] == ["Authorization"]


def test_the_account_headers_go_to_openai_and_nowhere_else(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`OPENAI_ORG_ID` and `OPENAI_PROJECT_ID` are OpenAI's identifiers, so OpenAI is sent them
    as the SDK would send them, and a server you run is not."""
    monkeypatch.setenv("OPENAI_API_KEY", KEY)
    monkeypatch.setenv("OPENAI_ORG_ID", "org-x")
    monkeypatch.setenv("OPENAI_PROJECT_ID", "proj-y")
    _llm, at_openai = _built(monkeypatch)
    assert "OpenAI-Organization" not in at_openai.headers
    assert "OpenAI-Project" not in at_openai.headers
    _llm, at_yours = _built(monkeypatch, url="http://127.0.0.1:8765")
    assert isinstance(at_yours.headers["OpenAI-Organization"], fake_api.Omit)
    assert isinstance(at_yours.headers["OpenAI-Project"], fake_api.Omit)


def test_the_variable_is_read_exactly_as_the_sdk_reads_it() -> None:
    """Split on newlines, the name before the first colon, trimmed, its case kept, and a line
    with no colon not a header, so each name omitted is the very entry the SDK would add."""
    raw = "A: 1\nB:2\nno colon here\n: nameless\n  C-d : 3:4 \r"
    assert ambient_headers(raw) == ["A", "B", "", "C-d"]
    assert ambient_headers(None) == [] and ambient_headers("") == []


# ── the address ─────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("url", "what"),
    [
        ("https://api.openai.com/v1", "a path"),
        ("https://api.openai.com/v1/decisions", "a path"),
        ("https://api.openai.com?project=x", "a query"),
        ("https://api.openai.com#here", "a fragment"),
    ],
)
def test_an_address_with_more_than_a_host_is_refused_and_told_the_address_alone(
    url: str, what: str
) -> None:
    with pytest.raises(DecisionError) as e:
        resolve_decision_address(OPENAI, url)
    message = str(e.value)
    assert f"has {what}" in message
    assert "give the address alone, https://api.openai.com," in message
    assert "/v1/decisions" in message


def test_a_refused_address_never_quotes_its_password() -> None:
    with pytest.raises(DecisionError) as e:
        resolve_decision_address(OPENAI, "https://rok:hunter2@api.openai.com/v1")
    assert "hunter2" not in str(e.value) and "rok:***@api.openai.com" in str(e.value)


@pytest.mark.parametrize(
    "url", ["api.openai.com", "rok:hunter2@nowhere", "ftp://api.openai.com", "https://h:port"]
)
def test_something_that_is_not_an_http_address_is_refused(url: str) -> None:
    with pytest.raises(DecisionError) as e:
        resolve_decision_address(OPENAI, url)
    assert "hunter2" not in str(e.value), "an address redaction cannot read is never quoted"


def test_a_system_one_address_keeps_its_path() -> None:
    """A System One server's SDK appends its own path to whatever it is given, and a gateway at
    a path of its own is a real address. Only the row that refuses one is held to it."""
    assert resolve_decision_address(PRESETS["kev"], "https://gw.example/kev") == (
        "https://gw.example/kev"
    )


def test_the_client_refuses_the_same_address_for_whoever_builds_one_directly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_api.install(monkeypatch, fake_api.FakeOpenAI())
    with pytest.raises(DecisionError, match="has a path"):
        DecisionsAPILLM(OPENAI, url="http://127.0.0.1:8765/v1")


def test_a_row_of_somebody_elses_with_no_model_or_no_address_is_refused_not_guessed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The row has both, and `factory` always resolves the address. A spec built by hand may
    have neither, and an empty model id or an address nobody gave is a sentence rather than a
    request that leaves for somewhere."""
    fake = fake_api.install(monkeypatch, fake_api.FakeOpenAI())
    with pytest.raises(DecisionError, match="names no model"):
        DecisionsAPILLM(dataclasses.replace(OPENAI, model=None), url="http://127.0.0.1:8765")
    with pytest.raises(DecisionError, match="no address"):
        DecisionsAPILLM(dataclasses.replace(OPENAI, url=None))
    assert not fake.kwargs, "no client was built for either"


# ── is it here at all ───────────────────────────────────────────────────────────────────


def test_no_sdk_is_the_extra_that_installs_it(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "openai", None)
    ok, why = decision_llm_is_available(OPENAI)
    assert not ok and "quackd[openai]" in why
    with pytest.raises(DecisionNotInstalled, match=r"quackd\[openai\]"):
        make_decision_llm(OPENAI, url="http://127.0.0.1:8765")


def test_an_sdk_without_the_client_is_the_same_sentence_and_not_a_traceback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An `openai` that imports and has no `AsyncOpenAI` -- one from before 1.0 -- would raise
    an `AttributeError` past the CLI, which catches only a `DecisionError`."""
    monkeypatch.setitem(sys.modules, "openai", types.ModuleType("openai"))
    ok, why = decision_llm_is_available(OPENAI)
    assert not ok and "quackd[openai]" in why
    with pytest.raises(DecisionNotInstalled):
        make_decision_llm(OPENAI, url="http://127.0.0.1:8765")


@pytest.mark.parametrize(
    ("url", "wanted"),
    [
        (None, True),
        ("https://eu.api.openai.com", True),
        ("http://127.0.0.1:8765", False),
        ("https://gateway.example", False),
    ],
)
def test_the_key_is_wanted_only_where_it_would_be_sent(
    url: str | None, wanted: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A run at a server you run is never switched off for want of a key it would not send."""
    fake_api.install(monkeypatch, fake_api.FakeOpenAI())
    ok, why = decision_llm_is_available(OPENAI, url=url)
    assert ok is not wanted
    assert ("OPENAI_API_KEY" in why) is wanted


# ── what it costs ───────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("url", "source"),
    [
        (None, "published"),
        ("https://eu.api.openai.com", "published"),
        ("http://127.0.0.1:8765", "self-hosted"),
    ],
)
def test_openais_rate_is_charged_at_openai_and_a_server_you_run_is_free(
    url: str | None, source: str
) -> None:
    """The regional hosts carry a premium OpenAI publish for no Decisions row, so they are
    costed at the published rate, and `QUACKD_DECISION_PRICE` is how to say otherwise."""
    price = resolve_decision_price(OPENAI, url=url)
    assert price.source == source
    if source == "published":
        assert price == OPENAI.price
    else:
        assert (price.input, price.output) == (0.0, 0.0)


def test_the_price_variable_beats_the_address(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(factory.PRICE_ENV, "in=0.11,out=0")
    for url in (None, "http://127.0.0.1:8765"):
        price = resolve_decision_price(OPENAI, url=url)
        assert price.input == 0.11 and price.source == factory.PRICE_ENV


# ── the command line ────────────────────────────────────────────────────────────────────


def _run(tmp_path: Path, *extra: str) -> Any:
    from typer.testing import CliRunner

    from quackd.cli import app

    return CliRunner().invoke(
        app,
        [
            "run",
            "lerobot-lookout",
            "--robot",
            "lerobot:mock",
            "--llm",
            "fake",
            "--decision-llm",
            "openai",
            "--no-log",
            "--runs-dir",
            str(tmp_path),
            *extra,
        ],
    )


def test_a_path_in_the_address_stops_the_run_on_a_machine_without_the_sdk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No SDK and no key, which is CI and most machines. Checked only where the client is
    built, this typo would come out as "running without it" and a run that exited 0."""
    monkeypatch.setitem(sys.modules, "openai", None)
    result = _run(tmp_path, "--decision-url", "https://api.openai.com/v1")
    assert result.exit_code == 1, result.output
    said = plain_text(result.output)
    assert "--decision-url https://api.openai.com/v1 has a path" in said
    assert "give the address alone, https://api.openai.com," in said and "/v1/decisions" in said
    assert "running without it" not in said
    assert not list(tmp_path.iterdir()), "the run started before the typo was noticed"


def test_a_run_at_a_server_you_run_needs_no_key_and_is_costed_as_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The address reaches the probe and the rate as well as the client: no key wanted, the
    word sent, and the run's record costed at the self-hosted rate rather than OpenAI's."""
    fake = fake_api.install(monkeypatch, fake_api.FakeOpenAI(answers=fake_api.turn("stop")))
    result = _run(tmp_path, "--decision-url", "http://127.0.0.1:9", "--decision-mode", "shadow")
    assert result.exit_code == 0, result.output
    assert "running without it" not in result.output
    assert fake.kwargs["api_key"] == "local" and fake.posts
    (run_dir,) = tmp_path.iterdir()
    records = [
        json.loads(line)
        for line in (run_dir / "transcript.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    start = next(r for r in records if r.get("kind") == "run_start")
    assert start["decision_price"]["source"] == "self-hosted"
    assert start["decision_llm"] == {
        "name": "openai",
        "model": OPENAI.model,
        "url": "http://127.0.0.1:9",
    }
    decided = [r for r in records if r.get("kind") == "decision"]
    assert decided and all(r["usage_estimated"] is False for r in decided)
