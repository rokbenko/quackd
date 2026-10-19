"""The discrete stepper, against a real decision LLM, over the network.

Every row that sends a key, which is every row with a hosted API behind it: `jev` and `openai`
today, read off the catalogue, so a hosted row added there is measured here the day it lands.
They are the rows there is an address to reach without standing a server up first, and each has
a maker who publishes something about its speed to check the measurement against: TypeSafe a
latency, OpenAI only a ratio to their own Responses API. Nothing here is about either vendor
otherwise. Adding a name to `ROWS` below -- `kev` on your own GPU, `von` on your own CPU -- is
the whole of what it takes to point these at it, and that is the point of running them at all.

Everything in `tests/test_decision.py` fakes the answer: a stub returns whatever the test
scripted, which proves the classification, the router and the loop hook and proves nothing at
all about the model. These tests are the other half, and there is one number they exist to
produce.

`docs/guides/decision-llms/README.md` estimates what a decision LLM saves from TypeSafe's published
0.114 s a call. Nobody has checked that against the shape of request quackd actually builds, from an
ordinary developer's network, and OpenAI publish no latency at all, so every speed figure in this
repository is arithmetic rather than a measurement. The first test here is what turns it into one
for each row, and it prints what it measured so the number can be read off the terminal and put
in an issue. The last two ask OpenAI's row what its page lists as unverified: how long it takes
over many requests rather than one, cold and warm, whether every answer comes back under the name
its question was asked by, what its usage block counts, and how many options one choice may carry.

They cost money and need that row's key, so they are opt-in twice over: the `live_decision`
marker and `QUACKD_LIVE_DECISION=1`. CI sets neither. Run them with

    QUACKD_LIVE_DECISION=1 uv run pytest tests/test_live_decision.py -m live_decision -s

in an environment that has the row's extra, `quackd[decision]` for jev and `quackd[openai]` for
openai: a row whose extra or key is missing is skipped, so one key measures one row. Keep them
cheap: each one is a single fan-out against the mock arm, not a run, except the openai
measurement, which is `SAMPLES` of them and prints what they cost. `-s` matters, because what
these are for is the numbers they print.

A test that has nothing to read fails, and says why. A wrong key, a model id the API does not
know, a request it turns down or a first turn slower than the stepper's second leaves a turn
with no answer in it, and a question the model declines leaves that question with none. A test
that reads a field off such a turn fails naming the error or the questions declined, where it
used to die on a bare `KeyError` or, for the safety claim, pass on an answer nobody gave. Each
such outcome is driven against the stand-in in `tests/test_decisions_api_wire.py`, with no key,
to hold these to that.
"""

from __future__ import annotations

import math
import os
import statistics
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from quackd.agent.decision.base import TIMEOUT_S, state_text
from quackd.agent.decision.catalogue import DECISIONS_API, PRESETS, DecisionSpec
from quackd.agent.decision.factory import make_decision_llm, resolve_decision_price
from quackd.agent.decision.stepper import FLOORS, Stepper, build_questions
from quackd.agent.providers.base import Observation
from quackd.agent.providers.pricing import cost_usd
from quackd.verbs.registry import registry_from_manifest
from tests.fake_openai_decisions import send_once_through_the_sdk

ALLOW = ["report_state", "stop", "gripper", "place", "move_joints"]
BUDGET = "step 0/12, llm calls 0/12, 0.0/3 min"

ROWS = tuple(name for name, spec in PRESETS.items() if spec.key_env)
"""Which decision LLMs these run against: every row that sends a key. Read off the catalogue
rather than written here, so a hosted row added there is measured the day it lands, and every
test below moves with it."""

SAMPLES = 20
"""How many requests the openai measurement makes: enough that its p95 is the nineteenth of
twenty rather than the largest of a handful, at a cost it prints, a fraction of a cent at the
row's own rate."""

PATIENCE_S = 10.0
"""How long the measurement lets one request take, in place of the stepper's `TIMEOUT_S`. What
is measured there is how long an answer takes, and a request cut off at one second would hide
exactly the slow ones the number is for."""


def _live_or_skip(spec: DecisionSpec) -> None:
    if os.environ.get("QUACKD_LIVE_DECISION") != "1":
        pytest.skip("live stepper tests are opt-in: set QUACKD_LIVE_DECISION=1")
    if spec.sdk:
        pytest.importorskip(spec.sdk, reason=f"needs the optional extra quackd[{spec.extra}]")
    # The CLI loads `.env` at startup and a developer's key usually lives there rather than in
    # the shell, so look there too before deciding there is no key, in the CLI's own order.
    try:
        from dotenv import load_dotenv

        load_dotenv(Path.cwd() / ".env")
        load_dotenv()
    except ImportError:  # pragma: no cover - dotenv ships with the CLI
        pass
    if spec.key_env and not os.environ.get(spec.key_env):
        pytest.skip(f"no {spec.key_env}, in the environment or a .env")


@pytest.fixture(params=ROWS)
def row(request: pytest.FixtureRequest) -> DecisionSpec:
    """One row a test runs against, skipped unless asked for and keyed here."""
    spec = PRESETS[request.param]
    _live_or_skip(spec)
    return spec


@pytest.fixture(params=[name for name in ROWS if PRESETS[name].backend == DECISIONS_API])
def decisions_row(request: pytest.FixtureRequest) -> DecisionSpec:
    """The same, for the rows that speak OpenAI's own format rather than System One."""
    spec = PRESETS[request.param]
    _live_or_skip(spec)
    return spec


async def _arm_stepper(spec: DecisionSpec, goal: str) -> tuple[Stepper, Observation]:
    """A stepper for the mock arm, and the observation of it a turn would hand that stepper."""
    from quackd_lerobot import LeRobotAdapter
    from quackd_lerobot.mock import LeRobotMock

    adapter = LeRobotAdapter(LeRobotMock())
    manifest = await adapter.connect()
    try:
        stepper = Stepper.build(
            mode="on",
            # Built here the way the CLI builds it, from the row and nothing else: the address,
            # the key variable, the model id and the rate all come off the row, so pointing
            # these tests at another decision LLM is one name in `ROWS`.
            llm=make_decision_llm(spec),
            price=resolve_decision_price(spec),
            registry=registry_from_manifest(manifest, adapter),
            allow=ALLOW,
            goal=goal,
            success=["You have said whether anything is held."],
            body=manifest.summary(),
        )
        obs = Observation(
            text="x",
            features={
                "state": (await adapter.get_state()).model_dump(),
                "detections": [],
                "last_result": None,
                "allowed": ALLOW,
            },
        )
    finally:
        await adapter.disconnect()
    return stepper, obs


async def _arm_turn(spec: DecisionSpec, goal: str, *, cleared: bool = True) -> tuple[Any, Any]:
    """One real fan-out against the mock arm's state. Returns (advice, stepper)."""
    stepper, obs = await _arm_stepper(spec, goal)
    advice = await stepper.advise(obs, cleared=cleared, budget=BUDGET)
    return advice, stepper


async def _first_use_paid(spec: DecisionSpec) -> bool:
    """Whether this row's SDK has done what it does once a process before anything here is timed.

    True for a row in OpenAI's own format, which sends one request now to a stand-in on this
    machine, keyless and free (`send_once_through_the_sdk`), so the request that is timed next
    opens its own connection and pays for nothing else. False for any other row, which quackd has
    no stand-in for, so its first timed request may also carry its SDK's first use in this
    process, and what it prints says so."""
    if spec.backend != DECISIONS_API:
        return False
    await send_once_through_the_sdk(spec)
    return True


def _seconds(value: Any) -> str:
    """A time off a record, printed as one, or said to be missing: a turn that failed before it
    was timed has none, and the line that prints it must still print."""
    return f"{value:.3f} s" if isinstance(value, int | float) else "no time"


def _failure(error: BaseException) -> str:
    """What a request that raised came to, as the record would put it: the type and the HTTP
    status where there is one, and never the message, which may quote the request."""
    status = getattr(error, "status_code", None)
    return f"{type(error).__name__}: HTTP {status}" if status else type(error).__name__


def _answered(record: Mapping[str, Any]) -> None:
    """Fail, saying why, where a turn has no answer to read.

    A turn whose request failed has nothing in it but the failure, so a test that reads a field
    off it used to die on a bare `KeyError` for the first one, and a wrong key, a model id the
    API does not know and a slow first turn all read the same. And a question the model declined
    has no answer either, so the fields the router reads came back empty and the test said a
    field had been renamed. The safety claim fared worse: read off a turn like that, it passed,
    on an answer nobody gave."""
    assert record.get("error") is None, (
        f"the turn ended on {record.get('gate')} with {record.get('error')} after "
        f"{_seconds(record.get('latency_s'))}, so no answer came back to check"
    )
    declined = record.get("refused") or []
    assert not declined, (
        f"the model declined {', '.join(declined)}, so that question has no answer to check: "
        "ask again, and if it keeps declining, the state it was shown is worth an issue"
    )


@pytest.mark.live_decision
async def test_a_real_decision_llm_answers_an_arm_state_and_says_how_long_it_took(
    row: DecisionSpec,
) -> None:
    """The measurement this repository does not have.

    The assertions are about the machinery rather than the answer: that the request quackd
    builds is one a real server accepts, that what comes back has the shape the router reads,
    and that it arrived inside the timeout the loop gives it. Which verb it picks on a folded
    mock arm is the model's business and not a thing to pin, and so is a question it declines.

    What matters is printed, and printed before anything is asserted, so a turn that fails a
    check still leaves its figures on the terminal: the same kind of number each row's page
    quotes from its maker, measured on the request quackd actually sends, from wherever you
    are. It is one turn on a client built for it, so it opens a new connection, which is what a
    turn in shadow mode usually pays as well.
    """
    warm = await _first_use_paid(row)
    advice, stepper = await _arm_turn(row, "Say whether you are holding anything, then let it go")
    record = advice.record

    latency = record.get("latency_s")
    print(
        f"\n{stepper.name} {stepper.model}: {_seconds(latency)}, "
        f"{record.get('state_chars')} chars of state, gate={advice.gate}, "
        f"choice={record.get('choice')!r}, confidence={record.get('confidence')}"
    )
    print(
        "  timed on its client's first request, so on a new connection, "
        + (
            "with the SDK's first use in this process already paid against a stand-in"
            if warm
            else "and on its SDK's first use in this process, unless a test before it sent one"
        )
    )
    print(f"  error: {record.get('error') or 'none'}")
    print(f"  probabilities: {record.get('probabilities')}")
    print(f"  done={record.get('done')} need_human={record.get('need_human')}")
    print(f"  feasible: {record.get('feasible')}")
    print(f"  refused: {record.get('refused') or 'nothing'}")
    print(f"  usage: {record.get('usage')}, estimated={record.get('usage_estimated')}")
    print(
        f"  docs/guides/decision-llms/{row.name}.md says what its maker publishes about its "
        "speed, and docs/guides/decision-llms/README.md estimates from TypeSafe's figure for "
        "every row, so if this one differs, those are what need correcting."
    )

    assert record.get("error") is None, (
        f"the turn ended on {advice.gate} with {record.get('error')}, so nothing was measured"
    )
    assert advice.gate in {"taken", "below_floor", "escalate", "done", "need_human", "refused"}
    assert isinstance(latency, float) and 0.0 < latency < TIMEOUT_S * 2, (
        f"{_seconds(latency)} is outside what the router is built for: the stepper waits "
        f"{TIMEOUT_S} s for any row, and docs/guides/decision-llms/README.md assumes far less"
    )


@pytest.mark.live_decision
async def test_a_real_answer_carries_the_fields_the_router_reads(row: DecisionSpec) -> None:
    """Both of these are young: TypeSafe's SDK is early access and has spelled its answer
    container two ways in its own docs, and OpenAI's API is a public beta whose answers quackd
    translates. That is why `_answer` and `_field` are tolerant, and tolerant code hides a
    rename, so this is the test that would notice one: every field the router branches on,
    present and the right type, from a real response rather than from a stub that was written
    to match the documentation. A turn with no answer in it, failed or declined, is no evidence
    of a rename, and fails saying which it was (`_answered`)."""
    advice, _stepper = await _arm_turn(row, "Read the arm back and say what it reports")

    record = advice.record
    _answered(record)
    confidence = record.get("confidence")
    assert isinstance(confidence, float), "a Choice came back with no confidence"
    assert 0.0 <= confidence <= 1.0
    probabilities = record.get("probabilities")
    assert isinstance(probabilities, dict) and probabilities, (
        "no distribution came back, so confidence routing has nothing under it"
    )
    assert record.get("choice") in record["labels"], (
        f"answered {record.get('choice')!r}, which was not one of the options it was given"
    )
    for noul in ("done", "need_human"):
        value = record.get(noul)
        assert isinstance(value, float) and 0.0 <= value <= 1.0, noul
    feasible = record.get("feasible") or {}
    assert feasible.get("choice") in {"feasible", "infeasible", "uncertain", None}


@pytest.mark.live_decision
async def test_a_real_answer_before_a_verdict_cannot_reach_a_moving_verb(
    row: DecisionSpec,
) -> None:
    """The safety claim, against the real model rather than a stub told to behave.

    Before a feasibility verdict the stepper is offered the reads and the brake and nothing
    else, so whatever the decision LLM thinks the right action is, the call that comes back
    cannot be one that moves the arm. That is a claim about an answer, so a turn that brought
    none back fails rather than passing on it: a request that failed, a `next_verb` declined, or
    one whose choice could not be read."""
    advice, _stepper = await _arm_turn(
        row, "Close the gripper on whatever is in front of you", cleared=False
    )

    record = advice.record
    _answered(record)
    assert record.get("choice") is not None, (
        f"the turn ended on {advice.gate} with no choice that could be read, so the claim was "
        "not put to the model"
    )
    assert set(record["labels"]) <= {"report_state", "stop", "escalate"}
    if advice.call is not None:
        assert advice.call.name in {"report_state", "stop"}
        assert record["floor"] == FLOORS[record["class"]]


@pytest.mark.live_decision
async def test_openais_answers_come_back_named_and_how_long_they_take_over_many(
    decisions_row: DecisionSpec,
) -> None:
    """`SAMPLES` requests of the arm turn quackd builds, one after another, each allowed
    `PATIENCE_S` rather than the stepper's second, so the slow ones are measured rather than cut
    off. The row's page lists what this answers as unverified.

    `LATENCY_UNDER_ONE_SECOND`: the median, the 95th percentile and the slowest, how many would have
    run out of the stepper's `TIMEOUT_S` and gone to the model, and the first request apart from the
    rest, because it opens the connection the others reuse, which is most of the difference between
    a turn in shadow mode and a turn in a streak. What the SDK does once in a process is done
    before the clock starts, by a request to a stand-in on this machine (`_first_use_paid`), and
    its look at its platform while the client is built (`decisions_api.warm_platform`), so the
    first figure is a new connection and not the SDK getting ready. `ANSWER_NAMES_ECHOED`: every
    answer back under the name its question was asked by, in the order asked, which is asserted
    rather than printed, because quackd reads answers by name and nothing else.
    `CONFIDENCE_MEANS_THE_SAME` and `NO_CACHE_DISCOUNT`, printed for a person to read: the
    confidence beside the top probability on each answer, and the usage block as OpenAI sent it.
    All of it is printed before anything is asserted, and a request that fails ends the test
    naming what it came to."""
    await _first_use_paid(decisions_row)
    stepper, obs = await _arm_stepper(decisions_row, "Read the arm back and say what it reports")
    llm: Any = stepper.llm
    state, _trimmed = stepper._build_state(obs, budget=BUDGET, stepped=(), notes=None)
    questions = build_questions(stepper.labels_for(cleared=True), stepper.what)
    replies: list[Any] = []
    send = llm._post

    async def kept(body: Any, timeout_s: float | None) -> Any:
        data = await send(body, timeout_s)
        replies.append(data)
        return data

    llm._post = kept  # this one client only: what the API sent, before it was translated
    took: list[float] = []
    results: list[Any] = []
    for n in range(SAMPLES):
        started = time.perf_counter()
        try:
            results.append(await llm.decide(state, questions, timeout_s=PATIENCE_S))
        except Exception as e:
            raise AssertionError(
                f"request {n + 1} of {SAMPLES} failed with {_failure(e)}, so there is no answer "
                "to measure"
            ) from e
        took.append(time.perf_counter() - started)

    usages = [data.get("usage") or {} for data in replies]
    spent = sum(
        cost_usd(
            {
                "input_tokens": usage.get("input_tokens") or 0,
                "output_tokens": usage.get("output_tokens") or 0,
            },
            stepper.price,
        )
        for usage in usages
    )
    ordered = sorted(took)
    p95 = ordered[math.ceil(0.95 * len(ordered)) - 1]
    over = sum(seconds > TIMEOUT_S for seconds in took)
    print(
        f"\n{decisions_row.name} {llm.model}: {SAMPLES} requests of {len(state_text(state))} "
        f"chars of state, p50 {statistics.median(took):.3f} s, p95 {p95:.3f} s, "
        f"slowest {ordered[-1]:.3f} s"
    )
    print(
        f"  the first, on a new connection: {took[0]:.3f} s; the other {SAMPLES - 1}, on the "
        f"same one: p50 {statistics.median(took[1:]):.3f} s"
    )
    print(f"  {over} of {SAMPLES} took longer than the stepper's {TIMEOUT_S} s")
    print(f"  usage, as OpenAI sent it for the first: {usages[0]}")
    print(f"  all {SAMPLES} at the row's rate: ${spent:.6f}")
    pairs = [
        (verb.get("confidence"), max(verb["probabilities"].values(), default=None))
        for result in results
        if (verb := result["answers"].get("next_verb"))
    ]
    print(f"  next_verb's confidence against its top probability: {pairs}")
    declined = [result["refused"] for result in results if result["refused"]]
    print(f"  questions declined: {declined or 'none'}")

    asked = list(questions)
    for data in replies:
        names = [answer.get("name") for answer in data.get("answers") or []]
        assert names == asked, f"asked {asked} and the answers came back as {names}"
    first = usages[0].get("input_tokens")
    assert isinstance(first, int) and first > 0, usages[0]


@pytest.mark.live_decision
async def test_how_many_options_one_choice_may_carry(decisions_row: DecisionSpec) -> None:
    """`CHOICE_BOUNDS` on the row's page: one reading of OpenAI's reference said a choice takes 2
    to 255 options, and no source said so again. quackd always offers at least two, a call and
    `escalate`, so nothing depends on the answer yet. This asks with one option and with 256 and
    prints what came back. Either is an answer or the API turning the request down with a 400,
    which is how it says a request is not one it takes. Anything else fails the test rather than
    passing for a bound: a timeout or a dropped connection, and a 401, a 403, a 404 or a 429,
    which are the API saying no to the key, the account, the model or the pace, and would have
    said it to two options as readily. It used to take any 4xx for a bound, so a wrong key read
    as the API refusing one option and 256 alike."""
    llm: Any = make_decision_llm(decisions_row)
    for count in (1, 256):
        criteria = {f"option_{n}": f"the option numbered {n}" for n in range(count)}
        question = {"type": "choice", "instructions": "Pick any one option.", "criteria": criteria}
        try:
            result = await llm.decide(
                {"goal": "pick one of the options"}, {"pick": question}, timeout_s=PATIENCE_S
            )
        except Exception as e:
            status = getattr(e, "status_code", None)
            assert status == 400, (
                f"{count} options: {_failure(e)}, which is not the API turning down the options"
            )
            print(f"\n{count} options: turned down, {_failure(e)}: {e}")
        else:
            if "pick" in result["refused"]:
                print(f"\n{count} options: taken, and the question declined")
                continue
            pick = result["answers"].get("pick") or {}
            print(
                f"\n{count} options: answered {pick.get('choice')!r}, with "
                f"{len(pick.get('probabilities') or {})} probabilities back"
            )
