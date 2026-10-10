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
"""

from __future__ import annotations

import math
import os
import statistics
import time
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


@pytest.mark.live_decision
async def test_a_real_decision_llm_answers_an_arm_state_and_says_how_long_it_took(
    row: DecisionSpec,
) -> None:
    """The measurement this repository does not have.

    The assertions are about the machinery rather than the answer: that the request quackd
    builds is one a real server accepts, that what comes back has the shape the router reads,
    and that it arrived inside the timeout the loop gives it. Which verb it picks on a folded
    mock arm is the model's business and not a thing to pin, and so is a question it declines.

    What matters is printed: the same kind of number each row's page quotes from its maker,
    measured on the request quackd actually sends, from wherever you are.
    """
    advice, stepper = await _arm_turn(row, "Say whether you are holding anything, then let it go")

    assert advice.record.get("error") is None, advice.record["error"]
    assert advice.gate in {"taken", "below_floor", "escalate", "done", "need_human", "refused"}
    latency = advice.record["latency_s"]
    chars = advice.record["state_chars"]
    print(
        f"\n{stepper.name} {stepper.model}: {latency:.3f} s, {chars} chars of state, "
        f"gate={advice.gate}, choice={advice.record.get('choice')!r}, "
        f"confidence={advice.record.get('confidence')}"
    )
    print(f"  probabilities: {advice.record.get('probabilities')}")
    print(f"  done={advice.record.get('done')} need_human={advice.record.get('need_human')}")
    print(f"  feasible: {advice.record.get('feasible')}")
    print(f"  refused: {advice.record.get('refused') or 'nothing'}")
    print(
        f"  usage: {advice.record.get('usage')}, estimated={advice.record.get('usage_estimated')}"
    )
    print(
        f"  docs/guides/decision-llms/{row.name}.md says what its maker publishes about its "
        "speed, and docs/guides/decision-llms/README.md estimates from TypeSafe's figure for "
        "every row, so if this one differs, those are what need correcting."
    )

    assert 0.0 < latency < TIMEOUT_S * 2, (
        f"{latency:.3f} s is outside what the router is built for: the stepper waits "
        f"{TIMEOUT_S} s for any row, and docs/guides/decision-llms/README.md assumes far less"
    )


@pytest.mark.live_decision
async def test_a_real_answer_carries_the_fields_the_router_reads(row: DecisionSpec) -> None:
    """Both of these are young: TypeSafe's SDK is early access and has spelled its answer
    container two ways in its own docs, and OpenAI's API is a public beta whose answers quackd
    translates. That is why `_answer` and `_field` are tolerant, and tolerant code hides a
    rename, so this is the test that would notice one: every field the router branches on,
    present and the right type, from a real response rather than from a stub that was written
    to match the documentation."""
    advice, _stepper = await _arm_turn(row, "Read the arm back and say what it reports")

    record = advice.record
    assert isinstance(record["confidence"], float), "a Choice came back with no confidence"
    assert 0.0 <= record["confidence"] <= 1.0
    assert isinstance(record["probabilities"], dict) and record["probabilities"], (
        "no distribution came back, so confidence routing has nothing under it"
    )
    assert record["choice"] in record["labels"], (
        f"answered {record['choice']!r}, which was not one of the options it was given"
    )
    for noul in ("done", "need_human"):
        assert isinstance(record[noul], float) and 0.0 <= record[noul] <= 1.0, noul
    assert record["feasible"]["choice"] in {"feasible", "infeasible", "uncertain", None}


@pytest.mark.live_decision
async def test_a_real_answer_before_a_verdict_cannot_reach_a_moving_verb(
    row: DecisionSpec,
) -> None:
    """The safety claim, against the real model rather than a stub told to behave.

    Before a feasibility verdict the stepper is offered the reads and the brake and nothing
    else, so whatever the decision LLM thinks the right action is, the call that comes back
    cannot be one that moves the arm."""
    advice, _stepper = await _arm_turn(
        row, "Close the gripper on whatever is in front of you", cleared=False
    )

    assert set(advice.record["labels"]) <= {"report_state", "stop", "escalate"}
    if advice.call is not None:
        assert advice.call.name in {"report_state", "stop"}
        assert advice.record["floor"] == FLOORS[advice.record["class"]]


@pytest.mark.live_decision
async def test_openais_answers_come_back_named_and_how_long_they_take_over_many(
    decisions_row: DecisionSpec,
) -> None:
    """`SAMPLES` requests of the arm turn quackd builds, one after another, each allowed
    `PATIENCE_S` rather than the stepper's second, so the slow ones are measured rather than cut
    off. The row's page lists what this answers as unverified.

    `LATENCY_UNDER_ONE_SECOND`: the median, the 95th percentile and the slowest, how many would
    have run out of the stepper's `TIMEOUT_S` and gone to the model, and the first request apart
    from the rest, because it opens the connection the others reuse, which is most of the
    difference between a turn in shadow mode and a turn in a streak. It is also this client's first
    request, and does once what no later one does, so it reads a little high for a shadow turn in
    a run that has asked before: the SDK's look at its platform, the costly part, is taken while
    the client is built (`decisions_api.warm_platform`), and the rest is in the figure, which says
    so. `ANSWER_NAMES_ECHOED`: every answer
    back under the name its question was asked by, in the order asked, which is asserted rather
    than printed, because quackd reads answers by name and nothing else. `CONFIDENCE_MEANS_THE_SAME`
    and `NO_CACHE_DISCOUNT`, printed for a person to read: the confidence beside the top
    probability on each answer, and the usage block as OpenAI sent it."""
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
    for _ in range(SAMPLES):
        started = time.perf_counter()
        results.append(await llm.decide(state, questions, timeout_s=PATIENCE_S))
        took.append(time.perf_counter() - started)

    asked = list(questions)
    for data in replies:
        names = [answer.get("name") for answer in data["answers"]]
        assert names == asked, f"asked {asked} and the answers came back as {names}"
    usage = replies[0]["usage"]
    assert isinstance(usage["input_tokens"], int) and usage["input_tokens"] > 0, usage
    spent = sum(
        cost_usd(
            {
                "input_tokens": data["usage"]["input_tokens"],
                "output_tokens": data["usage"].get("output_tokens") or 0,
            },
            stepper.price,
        )
        for data in replies
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
        f"  the first, on a new connection and the client's first use: {took[0]:.3f} s; the "
        f"other {SAMPLES - 1}, on the same one: p50 {statistics.median(took[1:]):.3f} s"
    )
    print(f"  {over} of {SAMPLES} took longer than the stepper's {TIMEOUT_S} s")
    print(f"  usage, as OpenAI sent it for the first: {usage}")
    print(f"  all {SAMPLES} at the row's rate: ${spent:.6f}")
    pairs = [
        (verb.get("confidence"), max(verb["probabilities"].values(), default=None))
        for result in results
        if (verb := result["answers"].get("next_verb"))
    ]
    print(f"  next_verb's confidence against its top probability: {pairs}")
    declined = [result["refused"] for result in results if result["refused"]]
    print(f"  questions declined: {declined or 'none'}")


@pytest.mark.live_decision
async def test_how_many_options_one_choice_may_carry(decisions_row: DecisionSpec) -> None:
    """`CHOICE_BOUNDS` on the row's page: one reading of OpenAI's reference said a choice takes 2
    to 255 options, and no source said so again. quackd always offers at least two, a call and
    `escalate`, so nothing depends on the answer yet. This asks with one option and with 256 and
    prints what came back. Either is an answer or the API saying no, and anything else, a
    timeout or a dropped connection, is a failure of the test rather than a bound."""
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
            assert isinstance(status, int) and 400 <= status < 500, (
                f"{count} options: {type(e).__name__}, which is not the API saying no"
            )
            print(f"\n{count} options: refused, {type(e).__name__} HTTP {status}: {e}")
        else:
            pick = result["answers"].get("pick") or {}
            print(
                f"\n{count} options: answered {pick.get('choice')!r}, with "
                f"{len(pick.get('probabilities') or {})} probabilities back"
            )
