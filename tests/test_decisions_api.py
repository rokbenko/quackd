"""OpenAI's Decisions API behind `--decision-llm openai`: the translation, the key and the address.

`test_decision_llms.py` holds the table every row is checked against, this one included. This
file is about what only this row does. It speaks another wire format, so its questions are
translated out and its answers back, and its client is an SDK that reads half its configuration
out of the environment, so where the key goes and which headers ride along are decided by quackd
rather than left to that SDK's defaults.

Nothing here needs the `openai` package: CI installs no provider SDK, and every test that builds a
client builds `tests/fake_decisions_api.py` in its place. Nothing here reaches the network, and the
last section proves the guard that makes sure of it (`tests/conftest.py`) fires.
"""

from __future__ import annotations

import asyncio
import base64
import dataclasses
import http.client
import json
import os
import platform
import re
import subprocess
import sys
import types
import urllib.parse
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from xml.etree import ElementTree

import pytest

from quackd.agent.decision import factory
from quackd.agent.decision.base import (
    TIMEOUT_S,
    DecisionError,
    DecisionMissingKey,
    DecisionNotInstalled,
    UnreadableReply,
    client_timeout,
)
from quackd.agent.decision.catalogue import PRESETS
from quackd.agent.decision.decisions_api import (
    OPENAI_HOSTS,
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
from tests.conftest import REPO, plain_text
from tests.fake_openai_decisions import PATH as STAND_IN_PATH
from tests.fake_openai_decisions import FakeOpenAIDecisions

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


async def test_a_timeout_handed_to_decide_sets_that_requests_own_clock_above_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The SDK's clock for that one request sits above the bound, where it cannot fire first, and
    a request with no bound keeps the clock the client was built with."""
    monkeypatch.setenv("OPENAI_API_KEY", KEY)
    llm, fake = _built(monkeypatch)
    await llm.decide({"goal": "g"}, {}, timeout_s=1.5)
    await llm.decide({"goal": "g"}, {})
    assert [p["options"] for p in fake.posts] == [{"timeout": client_timeout(1.5)}, {}]
    assert client_timeout(1.5) > 1.5


@pytest.mark.parametrize(
    "call",
    [*fake_api.REFUSED.values(), *fake_api.REFUSED_BY_THE_FLOOR.values()],
    ids=[*fake_api.REFUSED, *fake_api.REFUSED_BY_THE_FLOOR],
)
async def test_the_fake_sdk_refuses_a_request_some_release_of_the_sdk_would_not_send(
    call: dict[str, Any],
) -> None:
    """The fake is all CI ever runs, so a call it takes and the SDK refuses passes every test
    there and costs every turn of a real run, each recorded as `gate: error` while the run still
    exits 0. It used to take any keyword in a catch-all and read `options=None` as no options at
    all, so a backend that handed the SDK None, or the clock as a keyword of its own, passed.
    Each call here is one that a release the extra allows refuses or would not carry out, and the
    fake refuses it, before it records or answers anything."""
    fake = fake_api.FakeOpenAI(answers=fake_api.turn("stop"))
    with pytest.raises(TypeError):
        await fake.post("/decisions", cast_to=object, body={"model": OPENAI.model}, **call)
    assert fake.posts == []


class _Trickles(fake_api.FakeOpenAI):
    """A server that answers in its own time: every byte inside the SDK's limit for one phase of
    a request, the whole of it long after. The real SDK times each phase on its own, so this is a
    request its own clock never ends, which is the one a caller's bound is for."""

    def __init__(self, takes_s: float, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.takes_s = takes_s
        self.finished = False

    async def post(self, path: str, **kwargs: Any) -> Any:
        answer = await super().post(path, **kwargs)
        await asyncio.sleep(self.takes_s)
        self.finished = True
        return answer


async def test_a_timeout_handed_to_decide_bounds_the_whole_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`decide(timeout_s=...)` said it bounded the request, and handed the number to the SDK,
    which bounds each phase of one: a server that answered a piece at a time held it open several
    times as long. It raises `TimeoutError` at the bound now, whatever the server is doing."""
    monkeypatch.setenv("OPENAI_API_KEY", KEY)
    bound = TIMEOUT_S / 10
    fake = fake_api.install(
        monkeypatch, _Trickles(takes_s=20 * bound, answers=fake_api.turn("stop"))
    )
    llm = make_decision_llm(OPENAI)
    with pytest.raises(TimeoutError):
        await llm.decide(
            {"goal": "g"}, {"done": {"type": "noul", "instructions": "i"}}, timeout_s=bound
        )
    assert fake.posts and not fake.finished, "the request ran to its end past the bound"


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


async def test_the_state_goes_out_with_its_letters_as_letters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OpenAI's `input` is text its model reads as it is, never decoded. JSON's default escapes
    went out as written, `\\u017e` for every ž of a goal in Slovenian, and `state_chars` counted
    each escape as six characters. The letters go out as letters, the count is of them, and it is
    still the size of what was sent."""
    monkeypatch.setenv("OPENAI_API_KEY", KEY)
    llm, fake = _built(monkeypatch)
    stepper = _stepper(llm)
    stepper.goal = "poberi žogo in jo prinesi k meni"
    advice = await _advise(stepper)
    sent = fake.posts[0]["body"]["input"]
    assert stepper.goal in sent and "\\u" not in sent
    assert len(sent) == advice.record["state_chars"]


# ── what comes back ─────────────────────────────────────────────────────────────────────

ASKED = {
    question["name"]: question["type"]
    for question in to_questions(
        build_questions([Call(name="stop", arguments={}, label="stop")], {"stop": "the brake"})
    )
}
"""The stepper's four questions by the type each goes out as, read off the translation itself so
the answers below are held to what is really asked."""


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
    result = to_result(data, ASKED)
    assert result["answers"]["done"] == {"noul": 0.1}
    assert result["answers"]["need_human"] == {"noul": 0.9}
    assert set(result["answers"]) == {"next_verb", "done", "need_human"}


def test_a_name_answered_twice_answers_nothing() -> None:
    """OpenAI echo the unique names quackd sends, so two answers under one name are a server
    that does not, and the first one used to be kept: a `done` of 0.0 and then 0.99 settled as
    0.0, the reading that lets a verb through. Neither is kept, and the question is one nobody
    answered."""
    data = fake_api.decision([fake_api.predicate("done", 0.0), fake_api.predicate("done", 0.99)])
    result = to_result(data, ASKED)
    assert "done" not in result["answers"] and result["refused"] == []


@pytest.mark.parametrize(
    "given",
    [
        [fake_api.predicate("done", 0.0), fake_api.refusal("done")],
        [fake_api.refusal("done"), fake_api.predicate("done", 0.0)],
        [{"type": "predicate", "name": "done"}, fake_api.refusal("done")],
    ],
    ids=["after an answer", "before an answer", "after an answer with no probability"],
)
def test_a_refusal_under_a_name_declines_the_question_wherever_it_comes(
    given: list[dict[str, Any]],
) -> None:
    """Kept first, an answer ahead of a refusal hid it: the turn was taken with nothing on the
    record to say the vendor had declined, and with an answer that carried no probability the
    refusal was lost and the turn called unreadable."""
    result = to_result(fake_api.decision(given), ASKED)
    assert result["refused"] == ["done"] and "done" not in result["answers"]


def _without(answer: dict[str, Any], field: str) -> dict[str, Any]:
    return {key: value for key, value in answer.items() if key != field}


#: Answers that are not an answer to the question they name, each beside the name it is under.
#: Every one used to be kept, and the ones under `next_verb` that carry a confidence were then
#: read as a confident `escalate`.
NOT_AN_ANSWER = {
    "a score under next_verb": {
        "type": "score",
        "name": "next_verb",
        "score": 0.97,
        "confidence": 0.97,
        "probabilities": [{"value": 0, "label": "high", "probability": 0.97}],
    },
    "a predicate under next_verb": fake_api.predicate("next_verb", 0.97),
    "a choice under done": fake_api.choice("done", "yes", 0.97),
    "a choice with no choice": _without(fake_api.choice("next_verb", "stop", 0.97), "choice"),
    "a choice of null": {**fake_api.choice("next_verb", "stop", 0.97), "choice": None},
    "a choice under another field": {
        **_without(fake_api.choice("next_verb", "stop", 0.97), "choice"),
        "value": "stop",
    },
    "a score with no score": {
        "type": "score",
        "name": "tidy",
        "confidence": 0.97,
        "probabilities": [{"value": 0, "label": "a", "probability": 0.97}],
    },
}


@pytest.mark.parametrize("answer", list(NOT_AN_ANSWER.values()), ids=list(NOT_AN_ANSWER))
def test_an_answer_that_does_not_answer_its_question_answers_nothing(
    answer: dict[str, Any],
) -> None:
    """Kept only when it is the type its question went out as and carries the field that type
    answers with. Anything else is a question nobody answered, which the stepper reads as
    `unreadable` rather than as an answer."""
    asked = {**ASKED, "tidy": "score"}
    result = to_result(fake_api.decision([answer]), asked)
    assert answer["name"] not in result["answers"]


def test_a_distribution_comes_back_as_a_mapping_of_numbers() -> None:
    """The stepper calls `.items()` on it, and a boolean is no more a probability here than it
    is a confidence in `stepper._finite`. Neither is a NaN or an infinity, which the SDK's JSON
    reader takes from a server and a record could only write as a bare `NaN`."""
    answer = fake_api.choice(
        "next_verb",
        "stop",
        0.7,
        {
            "report_state": 0.2,
            "escalate": True,
            "observe": "high",
            "gripper(open=true)": float("nan"),
            "gripper(open=false)": float("inf"),
        },
    )
    answer["probabilities"].append({"probability": 0.1})
    result = to_result(fake_api.decision([answer]), ASKED)
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
    kept = to_result(fake_api.decision([answer]), ASKED)["answers"]["next_verb"]
    assert "confidence" not in kept and kept["choice"] == "stop"
    bare = to_result(fake_api.decision([{"type": "predicate", "name": "done"}]), ASKED)
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
    result = to_result(fake_api.decision([answer]), {"tidy": "score"})
    assert result["answers"]["tidy"] == {
        "score": 1.1,
        "confidence": 0.55,
        "probabilities": {"a": 0.1, "b": 0.7},
    }


def test_a_refusal_answers_nothing_and_is_named_beside_the_answers() -> None:
    data = fake_api.decision(fake_api.turn("stop", refuse=("done",)))
    result = to_result(data, ASKED)
    assert "done" not in result["answers"]
    assert result["refused"] == ["done"]
    assert set(result["answers"]) == {"next_verb", "need_human", "feasible"}


def test_usage_and_model_pass_through_untouched() -> None:
    data = fake_api.decision([])
    result = to_result(data, {})
    assert result["usage"] == fake_api.USAGE and result["model"] == data["model"]


def test_answers_that_are_not_a_list_are_an_error_rather_than_an_empty_turn() -> None:
    """In quackd's own words, which the record keeps (`base.UnreadableReply`), naming the type
    of what came where the list belongs."""
    with pytest.raises(UnreadableReply, match=r"without a list of answers \(dict\)"):
        to_result({"answers": {"done": {"noul": 0.0}}}, ASKED)


async def test_a_body_that_is_not_json_is_an_error_and_is_not_quoted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The SDK hands back text when the body is not JSON. A gateway's error page is not a
    reason the record needs to carry, and its type is: quackd's own sentence names it, and the
    record keeps the sentence and not the page."""
    monkeypatch.setenv("OPENAI_API_KEY", KEY)
    fake = fake_api.install(monkeypatch, fake_api.FakeOpenAI(script=["<html>bad gateway</html>"]))
    llm = make_decision_llm(OPENAI)
    with pytest.raises(UnreadableReply) as e:
        await llm.decide({"goal": "g"}, {})
    assert "rather than a JSON object" in str(e.value) and "html" not in str(e.value)
    assert fake.posts


async def test_a_reply_it_could_not_read_says_which_shape_on_the_record(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Through the stepper. Both shapes were recorded as a bare `ValueError`, so a page a proxy
    answered with and a JSON object with no answers in it read alike; each says which now, and
    neither quotes what the server sent."""
    monkeypatch.setenv("OPENAI_API_KEY", KEY)
    for reply, said in (
        ("<html>bad gateway</html>", "answered str rather than a JSON object"),
        ({"answers": None, "model": "gpt-6-luna"}, "answered without a list of answers (NoneType)"),
    ):
        fake_api.install(monkeypatch, fake_api.FakeOpenAI(script=[reply]))
        advice = await _advise(_stepper(make_decision_llm(OPENAI)))
        assert advice.record["gate"] == "error"
        assert advice.record["error"] == f"UnreadableReply: POST /v1/decisions {said}"
        assert "html" not in json.dumps(advice.event())


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


@pytest.mark.parametrize("declined", ["next_verb", "done", "need_human", "feasible"])
async def test_a_question_openai_declined_never_moves_the_body(
    declined: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A refusal is no answer at all, so the turn goes to the model on the gate that says so,
    naming the question, `feasible` included, though beside a declined `feasible` the other
    three answers would have taken `stop`."""
    monkeypatch.setenv("OPENAI_API_KEY", KEY)
    llm, _fake = _built(monkeypatch, answers=fake_api.turn("stop", refuse=(declined,)))
    stepper = _stepper(llm)
    advice = await _advise(stepper)
    assert advice.call is None
    assert advice.record["gate"] == "refused"
    assert advice.record["refused"] == [declined]
    assert stepper.summary()["refusals"] == 1


def _turn_with(*answers: dict[str, Any]) -> list[dict[str, Any]]:
    """A turn that would take `stop`, with `answers` in place of whichever questions they name."""
    named = {answer["name"] for answer in answers}
    return [*answers, *(answer for answer in fake_api.turn("stop") if answer["name"] not in named)]


#: Whole turns no gate should read as a decision, each beside the gate it has to end on. Each
#: of the first eight was taken, or recorded as a confident `escalate`, before the answers and
#: their numbers were read as strictly as a gate needs.
NO_DECISION = {
    "a score under next_verb": (_turn_with(NOT_AN_ANSWER["a score under next_verb"]), "unreadable"),
    "a choice with no choice": (_turn_with(NOT_AN_ANSWER["a choice with no choice"]), "unreadable"),
    "a choice of null": (_turn_with(NOT_AN_ANSWER["a choice of null"]), "unreadable"),
    "a choice under another field": (
        _turn_with(NOT_AN_ANSWER["a choice under another field"]),
        "unreadable",
    ),
    "done answered twice": (
        _turn_with(fake_api.predicate("done", 0.0), fake_api.predicate("done", 0.99)),
        "unreadable",
    ),
    "done answered, then declined": (
        _turn_with(fake_api.predicate("done", 0.0), fake_api.refusal("done")),
        "refused",
    ),
    "both nouls below zero": (
        _turn_with(fake_api.predicate("done", -1.0), fake_api.predicate("need_human", -1.0)),
        "unreadable",
    ),
    "a confidence above one": (_turn_with(fake_api.choice("next_verb", "stop", 5.0)), "unreadable"),
    "numbers written as text": (
        _turn_with(
            fake_api.choice("next_verb", "stop", "0.99"),
            fake_api.predicate("done", "0"),
            fake_api.predicate("need_human", "0"),
        ),
        "unreadable",
    ),
    "a percentage": (_turn_with(fake_api.choice("next_verb", "stop", 60)), "unreadable"),
    "done declined, then answered": (
        _turn_with(fake_api.refusal("done"), fake_api.predicate("done", 0.0)),
        "refused",
    ),
}


@pytest.mark.parametrize(("answers", "gate"), list(NO_DECISION.values()), ids=list(NO_DECISION))
async def test_an_answer_that_is_no_decision_never_moves_the_body_or_reads_as_one(
    answers: list[dict[str, Any]], gate: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The turn goes to the model on the gate that says what happened: `refused` where any answer
    under a name declined it, and `unreadable` for an answer of the wrong type, a choice with no
    choice, two answers to one question, or a number that is no probability, a percentage, a
    negative or one written as text. Never taken, and never a confident `escalate`."""
    monkeypatch.setenv("OPENAI_API_KEY", KEY)
    llm, _fake = _built(monkeypatch, answers=answers)
    advice = await _advise(_stepper(llm))
    assert advice.call is None
    assert advice.record["gate"] == gate


async def test_a_request_that_fails_costs_the_turn_and_not_the_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", KEY)
    fake_api.install(
        monkeypatch,
        fake_api.FakeOpenAI(
            raises=fake_api.status_error(
                fake_api.RateLimitError, 429, {"error": {"message": "slow down"}}
            )
        ),
    )
    advice = await _advise(_stepper(make_decision_llm(OPENAI)))
    assert advice.call is None and advice.record["gate"] == "error"
    assert advice.record["error"] == "RateLimitError: HTTP 429"


async def test_an_error_that_quotes_the_request_reaches_the_record_as_its_type_and_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The SDK writes a 4xx as `Error code: 400 - ` and the body it was sent back, and a body
    can quote the request it turned down: here the whole `input` the stepper sent, and a data
    URL besides. None of it reaches the record."""
    monkeypatch.setenv("OPENAI_API_KEY", KEY)
    picture = "data:image/jpeg;base64," + base64.b64encode(bytes(range(256)) * 3).decode()

    def turned_down(body: Any) -> Exception:
        reply = {
            "error": {
                "message": f"Invalid value for 'input': {body['input']}",
                "type": "invalid_request_error",
                "param": "input",
                "image_url": picture,
            }
        }
        return fake_api.status_error(fake_api.BadRequestError, 400, reply)

    fake = fake_api.install(monkeypatch, fake_api.FakeOpenAI(raises=turned_down))
    stepper = _stepper(make_decision_llm(OPENAI))
    stepper.goal = "fetch the zebra mug"
    advice = await _advise(stepper)
    assert "zebra" in fake.posts[0]["body"]["input"], "the request did not carry the goal"
    assert advice.record["gate"] == "error"
    assert advice.record["error"] == "BadRequestError: HTTP 400"
    recorded = json.dumps(advice.event())
    assert "zebra" not in recorded, "the state reached the record"
    assert "data:" not in recorded, "a data URL reached the record"
    assert re.search(r"[A-Za-z0-9+/=]{100,}", recorded) is None, "a base64 run reached it"


# ── the client, and where the key goes ──────────────────────────────────────────────────


def test_the_client_is_built_with_the_address_a_clock_above_the_steppers_and_no_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The SDK's clock at the stepper's own second raced it, and one slow server's turns were
    recorded now as quackd's `no answer within 1.0 s` and now as the SDK's `APITimeoutError`.
    Above it, the stepper's own bound is the one clock that ends a turn."""
    monkeypatch.setenv("OPENAI_API_KEY", KEY)
    llm, fake = _built(monkeypatch)
    assert fake.kwargs["base_url"] == f"{OPENAI.url}/v1"
    assert fake.kwargs["timeout"] == client_timeout(TIMEOUT_S) > TIMEOUT_S
    assert fake.kwargs["max_retries"] == 0
    assert fake.kwargs["api_key"] == KEY
    assert fake.headers["Authorization"] == f"Bearer {KEY}"
    assert (llm.name, llm.model, llm.url) == ("openai", OPENAI.model, OPENAI.url)


def test_the_client_follows_no_redirect(monkeypatch: pytest.MonkeyPatch) -> None:
    """Followed, a redirect would carry the account headers, and on a 307 or a 308 the state, to
    wherever it pointed. Told on the HTTP client the SDK is built on, which every release the
    extra allows reads, rather than per request, which 1.66.0 drops without a word."""
    monkeypatch.setenv("OPENAI_API_KEY", KEY)
    _llm, fake = _built(monkeypatch)
    http_client = fake.kwargs["http_client"]
    assert isinstance(http_client, fake_api.DefaultAsyncHttpxClient)
    assert http_client.follow_redirects is False


class _KeepsItsPlatform(fake_api.FakeOpenAI):
    """The fake with the attribute every release of the SDK keeps that answer in, empty until
    something asks, as it is in a client that has just been built."""

    _platform: Any = None


def _sdks_platform_function(monkeypatch: pytest.MonkeyPatch, probe: Any) -> None:
    """`probe` where the SDK keeps its `get_platform`, `openai._base_client`, for one test."""
    module = types.ModuleType("openai._base_client")
    module.get_platform = probe  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "openai._base_client", module)


def test_the_sdks_look_at_its_platform_is_taken_while_the_client_is_built(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each release of the SDK read asks which platform it is on before its first request, in a
    worker thread, and on Windows that is a WMI query or a subprocess, which inside the first
    stepper turn could take the whole of its second and the turn with it, before anything was sent.
    Asked while the client is built, by the SDK's own function, and handed to the client, it leaves
    the first request nothing to ask."""
    asked: list[str] = []

    def get_platform() -> str:
        asked.append("get_platform")
        return "Windows"

    _sdks_platform_function(monkeypatch, get_platform)
    client = _KeepsItsPlatform(answers=fake_api.turn("stop"))
    fake_api.install(monkeypatch, client)
    make_decision_llm(OPENAI, url="http://127.0.0.1:8765")
    assert asked == ["get_platform"], "the SDK was left to ask on the first turn"
    assert client._platform == "Windows", "the client would ask again on its first request"


def test_without_the_sdks_own_function_the_standard_library_is_asked_while_building(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A release that keeps it somewhere else, and the fakes on a machine without the SDK, still
    have the costly half asked before the robot connects: CPython keeps `platform.platform()`'s
    answer for the life of the process, so whatever asks it later finds it waiting."""
    monkeypatch.setitem(sys.modules, "openai._base_client", None)
    real = platform.platform
    asked: list[str] = []

    def recorded(*args: Any, **kwargs: Any) -> str:
        asked.append("platform")
        return real(*args, **kwargs)

    monkeypatch.setattr(platform, "platform", recorded)
    fake_api.install(monkeypatch, fake_api.FakeOpenAI(answers=fake_api.turn("stop")))
    make_decision_llm(OPENAI, url="http://127.0.0.1:8765")
    assert asked == ["platform"]


def test_a_look_at_the_platform_that_fails_never_stops_the_build(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """It only saves the first turn some time, so a release whose look fails leaves the client
    to ask for itself, the way it always did, and the run goes ahead."""

    def get_platform() -> str:
        raise OSError("ver would not start")

    _sdks_platform_function(monkeypatch, get_platform)
    client = _KeepsItsPlatform(answers=fake_api.turn("stop"))
    fake_api.install(monkeypatch, client)
    llm = make_decision_llm(OPENAI, url="http://127.0.0.1:8765")
    assert client._platform is None and llm.name == "openai"


def test_the_sdks_own_address_variable_never_moves_it(monkeypatch: pytest.MonkeyPatch) -> None:
    """Given no address, the SDK follows `OPENAI_BASE_URL`, which quackd's own local presets read
    as the address of a model server on your own machine (`providers/local.py`), so it may well
    be set where this row runs. The hosted key would follow it there."""
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
        # every regional host OpenAI's data-controls page lists for /v1/decisions (2026-10-10)
        ("https://us.api.openai.com", True),
        ("https://eu.api.openai.com", True),
        ("https://au.api.openai.com", True),
        ("https://ca.api.openai.com", True),
        ("https://jp.api.openai.com", True),
        ("https://in.api.openai.com", True),
        ("https://sg.api.openai.com", True),
        ("https://kr.api.openai.com", True),
        ("https://gb.api.openai.com", True),
        ("https://ae.api.openai.com", True),
        ("https://api.openai.com:443", True),
        # a host is case-insensitive, and `/` alone is the address with a slash on the end
        ("https://API.OpenAI.com/", True),
        ("http://127.0.0.1:8765", False),
        ("http://api.openai.com", False),
        ("https://api.openai.com:8443", False),
        # the same port to `urlsplit`, and not an authority OpenAI publish
        ("https://api.openai.com:0443", False),
        # a region OpenAI never named, however much it looks like the ones they did
        ("https://fr.api.openai.com", False),
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
    is sent. The client is built from the same parts the decision was made on, and the price is
    decided by the same rule, so a turn is costed at OpenAI's rate exactly where the key went."""
    monkeypatch.setenv("OPENAI_API_KEY", KEY)
    _llm, fake = _built(monkeypatch, url=url)
    sent = KEY if keyed else "local"
    assert fake.kwargs["api_key"] == sent
    assert fake.headers["Authorization"] == f"Bearer {sent}"
    assert is_openai_host(url) is keyed
    assert factory.sends_key(OPENAI, url) is keyed
    priced = resolve_decision_price(OPENAI, url=url).source
    assert priced == ("published" if keyed else "self-hosted")
    assert fake.kwargs["base_url"] == url.rstrip("/") + "/v1"


def test_the_hosts_are_the_ones_openai_document_for_this_endpoint_and_no_others() -> None:
    """The global API, which the Decisions guide's own examples call, and every regional host
    OpenAI's data-controls page lists with `/v1/decisions` among its endpoints, read 2026-10-10.
    A change here is a change to where a company's key goes, so it is spelled out rather than
    derived: a region OpenAI add is a host somebody has read about, not one that fits."""
    regions = ("us", "eu", "au", "ca", "jp", "in", "sg", "kr", "gb", "ae")
    assert {"api.openai.com", *(f"{region}.api.openai.com" for region in regions)} == OPENAI_HOSTS


@pytest.fixture
def _urlsplit_before_3_11_4(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """`urlsplit` as Python 3.11.0 to 3.11.3 had it, which `requires-python` allows: one that
    never checks what stands around and between `[` and `]`. 3.11.4 added `_check_bracketed_host`
    for what stands between (gh-103848), and later releases `_check_bracketed_netloc` for what
    stands before, which calls the first, so switching off whichever of the two this Python has
    is the older function. Its cache is emptied on the way in and out, because `urlsplit`
    remembers what it answered and an answer only the old one gives must not outlive the test.
    A Python whose `urlsplit` keeps no cache has nothing to empty."""
    forget = getattr(urllib.parse.urlsplit, "cache_clear", lambda: None)
    forget()
    for check in ("_check_bracketed_netloc", "_check_bracketed_host"):
        monkeypatch.setattr(urllib.parse, check, lambda text: None, raising=False)
    yield
    forget()


@pytest.mark.usefixtures("_urlsplit_before_3_11_4")
def test_an_address_an_older_python_reads_as_openais_is_not_sent_the_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """To the older `urlsplit`, `evil.example[api.openai.com]` is the host `api.openai.com`, and
    the rule that read that host sent the key and priced the run at OpenAI's rate, while the
    client was handed the authority whole. The rule reads the authority whole now, as the client
    does."""
    url = "https://evil.example[api.openai.com]"
    assert urllib.parse.urlsplit(url).hostname == "api.openai.com", "this is not the old reading"
    monkeypatch.setenv("OPENAI_API_KEY", KEY)
    _llm, fake = _built(monkeypatch, url=url)
    assert fake.kwargs["api_key"] == "local"
    assert is_openai_host(url) is False and factory.sends_key(OPENAI, url) is False
    assert resolve_decision_price(OPENAI, url=url).source == "self-hosted"


def test_an_address_with_whitespace_around_it_is_one_address_to_the_key_and_the_price(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The CLI takes the whitespace off before anything reads the address, and a library caller
    building the client directly need not. Read as given, `"https://api.openai.com "` was no host
    of OpenAI's to the key rule, so the word went, to a host with a space in its name, while the
    price, which takes the whitespace off, called it OpenAI's and costed every failed turn at
    OpenAI's rate. Both read the same string now."""
    monkeypatch.setenv("OPENAI_API_KEY", KEY)
    fake = fake_api.install(monkeypatch, fake_api.FakeOpenAI())
    url = "https://api.openai.com "
    llm = DecisionsAPILLM(OPENAI, url=url)
    assert fake.kwargs["api_key"] == KEY
    assert fake.kwargs["base_url"] == f"{OPENAI.url}/v1"
    assert llm.url == OPENAI.url
    assert is_openai_host(url) and factory.sends_key(OPENAI, url)
    assert resolve_decision_price(OPENAI, url=url) == OPENAI.price


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


@pytest.mark.parametrize("release", fake_api.RELEASES)
@pytest.mark.parametrize("spelling", ["Authorization", "authorization", "AUTHORIZATION"])
@pytest.mark.parametrize("url", ["https://api.openai.com", "http://127.0.0.1:8765"])
def test_an_authorization_line_of_the_sdks_variable_never_leaves_under_any_release(
    url: str, spelling: str, release: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One authorization line goes, and it is quackd's, whichever way the release merges.

    The rule used to lean on the locked release: name `Authorization`, and the SDK drops the
    variable's own authorization line in any spelling and merges the rest without regard to
    case. 3.1.0 does neither, and the extra allows it, so a gateway's `authorization` line went
    out beside quackd's own, to OpenAI or to whatever the address was."""
    monkeypatch.setenv("OPENAI_API_KEY", KEY)
    monkeypatch.setenv("OPENAI_CUSTOM_HEADERS", f"{spelling}: Bearer sk-ambient\nX-Gw: gk-secret")
    fake = fake_api.install(monkeypatch, fake_api.FakeOpenAI(release=release))
    make_decision_llm(OPENAI, url=url)
    sent = fake_api.sent(fake, release)
    word = KEY if url.startswith("https") else "local"
    assert [value for name, value in sent if name.lower() == "authorization"] == [f"Bearer {word}"]
    assert not [name for name, _ in sent if name.lower() == "x-gw"]


def test_a_release_that_would_still_send_the_variables_authorization_is_refused_by_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The backstop for a release nobody has read: once the client is built its own headers are
    read back, and an authorization quackd did not write stops the build, naming the release and
    the variable and never the value, which is the credential this exists to keep at home."""
    monkeypatch.setenv("OPENAI_CUSTOM_HEADERS", "authorization: Bearer sk-ambient")
    fake_api.install(monkeypatch, fake_api.FakeOpenAI(release="backwards"))
    with pytest.raises(DecisionError) as e:
        make_decision_llm(OPENAI, url="http://127.0.0.1:8765")
    said = str(e.value)
    assert f"openai {fake_api.__version__}" in said and "OPENAI_CUSTOM_HEADERS" in said
    assert "sk-ambient" not in said


@pytest.mark.parametrize("unset", [False, True], ids=["variables set", "variables unset"])
def test_a_line_spelled_as_an_account_header_carries_what_the_sdk_would_send_at_openai(
    unset: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Handed to the SDK under the very name it fills from `OPENAI_ORG_ID`, an omitted line took
    the place of the SDK's own entry, so the request reached OpenAI without the organisation and
    was billed to whatever the key's default is, all for the spelling of a gateway's header. The
    line carries the SDK's own value instead, and nothing where the variable is unset. In
    another spelling it is a header of its own, omitted, and the SDK's entry is left alone. Away
    from OpenAI every spelling is omitted."""
    monkeypatch.setenv("OPENAI_API_KEY", KEY)
    monkeypatch.setenv("OPENAI_CUSTOM_HEADERS", "OpenAI-Organization: GW\nopenai-project: GW")
    if unset:
        monkeypatch.delenv("OPENAI_ORG_ID")
        monkeypatch.delenv("OPENAI_PROJECT_ID")
    else:
        monkeypatch.setenv("OPENAI_ORG_ID", "org-x")
        monkeypatch.setenv("OPENAI_PROJECT_ID", "proj-y")
    _llm, at_openai = _built(monkeypatch)
    if unset:
        assert isinstance(at_openai.headers["OpenAI-Organization"], fake_api.Omit)
    else:
        assert at_openai.headers["OpenAI-Organization"] == "org-x"
    assert isinstance(at_openai.headers["openai-project"], fake_api.Omit)
    assert "OpenAI-Project" not in at_openai.headers, "the SDK's own entry was touched"
    _llm, at_yours = _built(monkeypatch, url="http://127.0.0.1:8765")
    for name in ("OpenAI-Organization", "OpenAI-Project", "openai-project"):
        assert isinstance(at_yours.headers[name], fake_api.Omit), name


def test_an_account_header_named_in_two_spellings_carries_the_sdks_value_in_both(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """3.22.1 merges without regard to case and keeps whichever spelling comes last. With the
    SDK's own spelling named first, an omitted second spelling would take the organisation off
    the request after all, so beside the SDK's own spelling every other one carries its value."""
    monkeypatch.setenv("OPENAI_API_KEY", KEY)
    monkeypatch.setenv("OPENAI_ORG_ID", "org-x")
    monkeypatch.setenv("OPENAI_CUSTOM_HEADERS", "OpenAI-Organization: GW\nopenai-organization: GW")
    _llm, fake = _built(monkeypatch)
    assert fake.headers["OpenAI-Organization"] == fake.headers["openai-organization"] == "org-x"


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


# ── the stand-in's own record ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "lines",
    [
        [("Authorization", "Bearer sk-ambient"), ("authorization", "Bearer local")],
        [("authorization", "Bearer local"), ("Authorization", "Bearer sk-ambient")],
    ],
    ids=["the ambient line first", "the ambient line last"],
)
def test_the_stand_in_keeps_every_value_of_a_header_sent_twice(
    lines: list[tuple[str, str]],
) -> None:
    """A second `Authorization` beside the first is the failure the wire tests are there to see,
    and the stand-in kept one value a name, the last: with a gateway's line sent first, the record
    said `Bearer local` and nothing else, and the line that should never have left was nowhere in
    it. Sent twice here, in both orders, on a plain socket, each value is in the record, under
    `every` one at a time and in `headers` folded together, so an assertion that the header said
    one thing fails whichever line came first."""
    body = json.dumps({"model": OPENAI.model, "input": "x", "questions": []}).encode()
    with FakeOpenAIDecisions() as stand_in:
        port = urllib.parse.urlsplit(stand_in.url).port
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
        try:
            connection.putrequest("POST", STAND_IN_PATH)
            for name, value in lines:
                connection.putheader(name, value)
            connection.putheader("Content-Type", "application/json")
            connection.putheader("Content-Length", str(len(body)))
            connection.endheaders(body)
            assert connection.getresponse().status == 200
        finally:
            connection.close()
    (post,) = stand_in.posts()
    sent = [value for _name, value in lines]
    assert post.every("authorization") == sent
    assert post.headers["authorization"] == ", ".join(sent)


# ── the suite's own guard ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize("url", [None, "https://eu.api.openai.com"])
async def test_the_suites_guard_stops_a_request_to_openai_before_the_client_has_it(
    url: str | None, monkeypatch: pytest.MonkeyPatch, _no_decision_request_reaches_openai: list[str]
) -> None:
    """`tests/conftest.py` stops any decision request a test sends to OpenAI's own API through the
    real SDK. The lookalike wears the SDK's name, so the guard takes it for the real client: the
    turn is the error the stepper makes of any exception, the client was never handed the
    request, and the guard wrote down where it was going. Taken back at the end, or this test
    would fail in teardown, which is what the test after next shows happening."""
    monkeypatch.setenv("OPENAI_API_KEY", KEY)
    fake = fake_api.install(monkeypatch, fake_api.SDKLookalike(answers=fake_api.turn("stop")))
    advice = await _advise(_stepper(make_decision_llm(OPENAI, url=url)))
    assert advice.record["gate"] == "error" and advice.record["error"] == "ReachedOpenAI"
    assert fake.posts == [], "the request reached the client"
    assert _no_decision_request_reaches_openai == [f"POST {url or OPENAI.url}/v1/decisions"]
    _no_decision_request_reaches_openai.clear()


class _Undressed(fake_api.SDKLookalike):
    """The lookalike under a module of the suite's own: the same client, and not the SDK's."""


_Undressed.__module__ = __name__


@pytest.mark.parametrize(
    ("client", "url"),
    [(fake_api.SDKLookalike, "http://127.0.0.1:8765"), (_Undressed, None)],
    ids=["a server you run", "a fake at openais address"],
)
async def test_the_suites_guard_lets_through_what_cannot_reach_openai(
    client: type[fake_api.FakeOpenAI],
    url: str | None,
    monkeypatch: pytest.MonkeyPatch,
    _no_decision_request_reaches_openai: list[str],
) -> None:
    """Two things go through untouched. A request to a server you run, because the stand-in on
    127.0.0.1 is what the wire tests point the real SDK at. And any client that is not the SDK's
    own, wherever it says it is going, because it answers in this process: every fake in this
    directory, the one that keeps OpenAI's address included."""
    monkeypatch.setenv("OPENAI_API_KEY", KEY)
    fake = fake_api.install(monkeypatch, client(answers=fake_api.turn("stop")))
    advice = await _advise(_stepper(make_decision_llm(OPENAI, url=url)))
    assert advice.record["gate"] == "taken" and len(fake.posts) == 1
    assert _no_decision_request_reaches_openai == []


GUARDED = '''
import pytest

from quackd.agent.decision.catalogue import PRESETS
from quackd.agent.decision.factory import make_decision_llm
from tests import fake_decisions_api as fake_api


async def _ask_openai(monkeypatch):
    """One request to OpenAI's own address through the lookalike, whatever comes back
    swallowed, as the stepper swallows it."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-a-real-key")
    fake = fake_api.install(monkeypatch, fake_api.SDKLookalike(answers=fake_api.turn("stop")))
    try:
        await make_decision_llm(PRESETS["openai"]).decide(
            {"goal": "g"}, {"done": {"type": "noul", "instructions": "i"}}
        )
    except AssertionError:
        pass
    return fake


async def test_unmarked(monkeypatch):
    fake = await _ask_openai(monkeypatch)
    assert fake.posts == []


@pytest.mark.live_decision
async def test_marked(monkeypatch):
    fake = await _ask_openai(monkeypatch)
    assert len(fake.posts) == 1
'''
"""Two tests that make the same request, for the session below to run."""


def test_the_suites_guard_fails_the_test_that_reached_for_openai_and_spares_a_live_one(
    tmp_path: Path,
) -> None:
    """End to end, in a pytest session of its own, because a test failing in its teardown is
    something only the session around it can see. Both tests ask OpenAI's address through the
    lookalike and swallow what comes back. The one without the marker passes, and then fails in
    teardown naming the request; the one marked `live_decision` passes, its request having
    reached the client. The suite's conftest is loaded as a plugin, because the file is not under
    `tests/` where it would be found."""
    inner = tmp_path / "test_reaching_openai.py"
    inner.write_text(GUARDED, encoding="utf-8")
    report = tmp_path / "report.xml"
    done = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            str(inner),
            "-p",
            "tests.conftest",
            "-c",
            str(REPO / "pyproject.toml"),
            "--rootdir",
            str(REPO),
            "-p",
            "no:cacheprovider",
            f"--basetemp={tmp_path / 'inner'}",
            f"--junitxml={report}",
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        timeout=600,
    )
    said = done.stdout + done.stderr
    assert report.exists(), said
    cases = {case.get("name"): case for case in ElementTree.parse(report).iter("testcase")}
    assert set(cases) == {"test_unmarked", "test_marked"}, said
    outcomes = [(child.tag, child.get("message") or "") for child in cases["test_unmarked"]]
    assert [tag for tag, _ in outcomes] == ["error"], said
    assert outcomes[0][1].startswith("failed on teardown"), said
    assert f"POST {OPENAI.url}/v1/decisions" in outcomes[0][1]
    assert list(cases["test_marked"]) == [], said
    assert done.returncode == 1, said
