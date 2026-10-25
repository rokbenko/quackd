"""The discrete stepper: which turns are a choice, and which belong to the model.

This is quackd's half of a decision LLM and it does not change with the vendor. Which turns are
a choice, what an answer has to clear before it moves a servo, what the record says afterwards:
none of that moves when the thing answering does. `tests/test_decision_llms.py` next door is
the other half -- the flags, the presets, the two backends and the plugin hook -- and this file
leaves all of it alone.

So nothing here imports `typesafe_sdk` and nothing here reaches the network: every test hands
`Stepper.build` a scripted `FakeDecisionLLM` as `llm=`, which is the seam the CLI hands a real
one through. The classification is pure besides -- it reads a tool's JSON Schema and answers --
so it is testable against every body quackd ships without connecting to any of them.

The table below is frozen on purpose, the way `MOVES_THE_BODY` is: a verb added to an adapter
with a parameter nobody thought about is a verb the stepper would either offer or refuse
silently, and this is the test that makes somebody say which.
"""

from __future__ import annotations

import asyncio
import base64
import itertools
import json
import pathlib
import re
from collections.abc import Sequence
from types import SimpleNamespace
from typing import Any

import pytest

from quackd.adapters.factory import ADAPTER_NAMES, _module
from quackd.agent.decision.base import TIMEOUT_S, UnreadableReply
from quackd.agent.decision.catalogue import PRESETS
from quackd.agent.decision.factory import resolve_decision_price
from quackd.agent.decision.stepper import (
    ESCALATE,
    FLOORS,
    MAX_CALLS_PER_VERB,
    build_questions,
    discrete_calls,
    labels,
    verb_class,
)
from quackd.agent.prompts import META_TOOLS, REMEMBER, TELL
from quackd.verbs.aliases import canonical
from quackd.verbs.registry import Verb, default_registry
from tests import fake_systemone

# (how many concrete calls, which confidence floor) per body, or None for "not a choice".
#
# Keyed by body and not by name, because the same word is a different question on a different
# robot: `gripper` is two calls on a one-armed SO-101 and six on a cart with two hands and a
# `side`, and `stand` is a safe verb on a duck and a confirm-gated one on a humanoid that
# cannot get up if it falls.
TABLE: dict[str, dict[str, tuple[int | None, str]]] = {
    "core": {
        "approach_and": (None, "motion"),
        "gaze": (5, "motion"),
        "go_to": (None, "motion"),
        "grab": (1, "motion"),
        "kick": (2, "motion"),
        "move": (None, "motion"),
        "observe": (1, "read"),
        "quack": (1, "motion"),
        "report_state": (1, "read"),
        "say": (None, "motion"),
        "search_scan": (None, "motion"),
        "sit": (1, "motion"),
        "stand": (1, "motion"),
        "stand_up": (1, "motion"),
        "stop": (1, "brake"),
        # a free-string target, so a value and never a choice: the stepper can never take it
        "watch": (None, "motion"),
    },
    "microduck": {
        "gaze": (5, "motion"),
        "grab": (1, "motion"),
        "kick": (2, "motion"),
        "quack": (1, "motion"),
        "say": (None, "motion"),
        "sit": (1, "motion"),
        "stand": (1, "motion"),
        "stand_up": (1, "motion"),
        "watch": (None, "motion"),
    },
    "lerobot": {
        "gripper": (2, "motion"),
        "manipulate": (None, "confirm"),
        "move_joints": (None, "motion"),
        "pick": (None, "confirm"),
        "place": (1, "motion"),
        "report_state": (1, "read"),
    },
    "rosbridge": {
        "introspect": (1, "read"),
    },
    "open_duck": {
        "express": (3, "motion"),
        "gaze": (5, "motion"),
        "quack": (1, "motion"),
        "say": (None, "motion"),
    },
    "xlerobot": {
        "gripper": (6, "motion"),
        "move_joints": (None, "confirm"),
    },
    "alohamini": {
        "gripper": (6, "motion"),
        "home_arms": (1, "confirm"),
        "lift": (None, "confirm"),
        "move_joints": (None, "confirm"),
    },
    "toddlerbot": {
        "grip": (6, "motion"),
        "look": (None, "motion"),
        "perform": (5, "confirm"),
        "search_scan": (None, "motion"),
        "stand": (1, "confirm"),
    },
    "huebot": {
        "kick": (2, "motion"),
        "quack": (1, "motion"),
        "say": (None, "motion"),
        "sit": (1, "motion"),
        "stand": (1, "motion"),
        "stand_up": (1, "motion"),
    },
    "moss": {
        # dangerous, so never a choice: a decision LLM cannot author it at any confidence
        "collect_litter": (None, "never"),
        "drive_to": (None, "motion"),
        "explore": (None, "confirm"),
        "pick_and_drop": (2, "confirm"),
        "stow_arm": (1, "confirm"),
    },
}


def _verbs(body: str) -> dict[str, Verb]:
    """The verb templates one body registers, named as that body spells them."""
    if body == "core":
        return {v.name: v for v in default_registry().verbs()}
    return dict(_module(body).implementations())


def _classify(verb: Verb, name: str) -> tuple[int | None, str]:
    calls = discrete_calls(verb.tool_schema())
    return (len(calls) if calls is not None else None, verb_class(verb, canonical(name)))


# ── the table ───────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("body", sorted(TABLE))
def test_every_verb_of_every_shipped_body_is_classified_and_this_table_says_how(body: str) -> None:
    """A new verb, or a new parameter on an old one, has to be classified on purpose.

    The stepper decides from the schema, so a parameter added without a thought about it
    silently changes what a cheap model is allowed to author. That is the failure this test
    exists to make loud, and the message names the verb rather than the diff."""
    verbs = _verbs(body)
    frozen = TABLE[body]
    assert set(verbs) == set(frozen), (
        f"{body}'s verbs changed: classify "
        f"{sorted(set(verbs) - set(frozen))} and drop {sorted(set(frozen) - set(verbs))}"
    )
    for name, verb in sorted(verbs.items()):
        assert _classify(verb, name) == frozen[name], f"{body}.{name} classifies differently now"


def test_the_table_covers_every_adapter_quackd_ships() -> None:
    """Otherwise a whole new body could arrive unclassified and the parametrised test above
    would simply not run for it."""
    assert set(TABLE) - {"core"} == set(ADAPTER_NAMES)


def test_every_class_in_the_table_has_a_floor_to_answer_to() -> None:
    used = {cls for body in TABLE.values() for _count, cls in body.values()}
    assert used <= set(FLOORS), f"no floor for {sorted(used - set(FLOORS))}"


# ── the rule, case by case ──────────────────────────────────────────────────────────────


def test_move_joints_is_never_a_choice_on_any_body_that_has_one() -> None:
    """The requirement the whole rule was written to satisfy.

    Three bodies offer `move_joints` and all three are refused, for the same reason twice
    over: `positions` is a required object, and its keys — the joint names — are not in the
    schema at all. They live in a `field_validator`, so there is nothing to enumerate even in
    principle, and a classifier that tried would be inventing angles."""
    bodies = [b for b in ADAPTER_NAMES if "move_joints" in _verbs(b)]
    assert len(bodies) == 3, f"expected three arms, found {bodies}"
    for body in bodies:
        verb = _verbs(body)["move_joints"]
        assert discrete_calls(verb.tool_schema()) is None, f"{body} would author joint angles"


def test_a_free_string_with_a_default_is_not_a_choice_even_though_omitting_it_is_legal() -> None:
    """`go_to()` is a legal call, and the string it defaults to is not the vocabulary.

    The detector's label set is what `target` can usefully be, and that set is nowhere in the
    schema. A stepper offered `go_to` would be choosing the word "ball" because somebody typed
    it as a default, which is not the same as choosing where to go."""
    core = _verbs("core")
    for name in ("go_to", "search_scan"):
        assert discrete_calls(core[name].tool_schema()) is None, name
    assert discrete_calls(_verbs("lerobot")["pick"].tool_schema()) is None


def test_a_number_with_a_number_for_a_default_is_a_speed_chosen_by_not_choosing() -> None:
    """`move()` walks at 0.15 m/s because that is the default, so omitting `vx` picks it."""
    assert discrete_calls(_verbs("core")["move"].tool_schema()) is None
    assert discrete_calls(_verbs("toddlerbot")["look"].tool_schema()) is None


def test_a_nullable_number_with_a_null_default_leaves_the_verb_a_choice() -> None:
    """The exemption, in both directions, because it is the one clause anybody will widen.

    `gaze` has an optional exact `bearing_deg` that defaults to null: left out it has no value
    at all and the verb does what its enum says, so the five directions are still five
    choices. `quack` is the same with its text. `move` and `look` are the other side."""
    for body in ("microduck", "open_duck"):
        gaze = discrete_calls(_verbs(body)["gaze"].tool_schema())
        assert gaze is not None and len(gaze) == 5, body
        assert all("bearing_deg" not in call.arguments for call in gaze), (
            "an inert parameter is left out of the call, never sent as null"
        )
        quack = discrete_calls(_verbs(body)["quack"].tool_schema())
        assert quack is not None and len(quack) == 1 and quack[0].arguments == {}


def test_a_verbs_labels_are_the_product_of_its_closed_sets_and_nothing_else() -> None:
    """And they read like something a person would say out loud, because the decision LLM
    chooses between these strings and the log prints them."""
    gripper = discrete_calls(_verbs("lerobot")["gripper"].tool_schema())
    assert gripper is not None
    assert [c.label for c in gripper] == ["gripper(open=true)", "gripper(open=false)"]
    assert [c.arguments for c in gripper] == [{"open": True}, {"open": False}]

    two_handed = discrete_calls(_verbs("xlerobot")["gripper"].tool_schema())
    assert two_handed is not None and len(two_handed) == 6
    assert "gripper(open=false, side=both)" in [c.label for c in two_handed]

    no_params = discrete_calls(_verbs("lerobot")["place"].tool_schema())
    assert no_params is not None
    assert [c.label for c in no_params] == ["place"] and no_params[0].arguments == {}


def test_a_verb_with_more_shapes_than_a_person_could_weigh_stops_being_a_choice() -> None:
    """A Choice is meant to be a gut-check. Past a dozen shapes of one verb it is not one,
    and the answer is to escalate rather than to offer a menu nobody can read."""
    wide = {
        "name": "wide",
        "input_schema": {
            "type": "object",
            "properties": {
                "a": {"enum": list("abcde")},
                "b": {"enum": list("fghij")},
            },
        },
    }
    assert discrete_calls(wide) is None
    narrow = dict(wide)
    narrow["input_schema"] = {"type": "object", "properties": {"a": {"enum": list("abcde")}}}
    calls = discrete_calls(narrow)
    assert calls is not None and len(calls) == 5 <= MAX_CALLS_PER_VERB


def test_perform_is_never_offered_a_motion_this_build_did_not_load() -> None:
    """A ToddlerBot reports which keyframes it managed to load, and `perform`'s schema is
    built from that list rather than from everything upstream ships."""
    from quackd_toddlerbot.verbs import toddlerbot_verbs

    verb = toddlerbot_verbs(motions=("hold", "kneel"))["perform"]
    calls = discrete_calls(verb.tool_schema())
    assert calls is not None
    assert [c.label for c in calls] == ["perform(motion=hold)", "perform(motion=kneel)"]


# ── what the stepper may never touch ────────────────────────────────────────────────────


def test_no_meta_tool_is_ever_a_choice() -> None:
    """A safety property rather than an accident.

    Every meta tool needs a sentence, and the stepper writes none, so it can never end a run,
    never record a feasibility verdict, never write to memory and never speak to a flock.
    Every ending goes through the model or through a budget."""
    for tool in [*META_TOOLS, REMEMBER, TELL]:
        assert discrete_calls(tool) is None, f"{tool['name']} must stay the model's"


def test_the_arm_splits_the_way_the_docs_say_it_does() -> None:
    """The LeRobot claim `docs/guides/decision-llms/README.md` is built on, as an assertion.

    Six concrete calls the stepper may author, and every angle still the model's."""
    verbs = _verbs("lerobot")
    offered = sorted(
        call.label
        for name, verb in verbs.items()
        for call in (discrete_calls(verb.tool_schema()) or ())
    )
    core = _verbs("core")
    offered += [call.label for call in (discrete_calls(core["stop"].tool_schema()) or ())]
    offered += [call.label for call in (discrete_calls(core["observe"].tool_schema()) or ())]
    assert sorted(offered) == [
        "gripper(open=false)",
        "gripper(open=true)",
        "observe",
        "place",
        "report_state",
        "stop",
    ]


def test_the_way_out_is_on_every_label_set() -> None:
    """Without it a Choice always returns something, and the floor becomes the only thing
    between "none of these is right" and a servo."""
    calls = discrete_calls(_verbs("lerobot")["gripper"].tool_schema())
    assert calls is not None
    assert labels(calls) == ["gripper(open=true)", "gripper(open=false)", ESCALATE]
    assert labels([]) == [ESCALATE]


def test_the_brake_answers_to_the_lowest_floor_and_a_gated_verb_to_the_highest() -> None:
    assert FLOORS[verb_class(_verbs("core")["stop"])] == min(FLOORS.values())
    assert verb_class(_verbs("alohamini")["home_arms"], "home_arms") == "confirm"
    assert FLOORS["confirm"] > FLOORS["motion"] > FLOORS["read"] > FLOORS["brake"]


# ── the stepper ─────────────────────────────────────────────────────────────────────────


JEV = PRESETS["jev"]
"""The preset every fake here wears.

The record says which decision LLM answered as well as which model id, so a fake that called
itself nothing would be testing the gates against a name no run could ever produce. `jev` is
the row with a published rate, which is also what the billing tests read back."""


def _fake(**scripted: Any) -> fake_systemone.FakeDecisionLLM:
    """A decision LLM that answers whatever the test scripted, named as the jev preset.

    Handed to `Stepper.build` as `llm=` and never installed as an SDK: the client is built by
    `make_decision_llm` while the CLI is still parsing, so a stepper asks whatever it was given
    and builds nothing itself. `tests/test_decision_llms.py` is where the client-building seam
    is tested."""
    return fake_systemone.FakeDecisionLLM(name=JEV.name, model=JEV.model or "", **scripted)


async def _arm(
    mode: str = "on",
    allow: Sequence[str] | None = None,
    llm: Any = None,
    price: Any = None,
    frames: bool = False,
) -> tuple[Any, Any, Any]:
    """A stepper on the mock arm, with the adapter it was built from.

    `price` is resolved the way the CLI resolves it rather than taken from the row, so a test
    that sets `QUACKD_DECISION_PRICE` and nothing else still gets the rate it asked for."""
    from quackd.agent.decision.stepper import Stepper
    from quackd.verbs.registry import registry_from_manifest
    from quackd_lerobot import LeRobotAdapter
    from quackd_lerobot.mock import LeRobotMock

    adapter = LeRobotAdapter(LeRobotMock())
    manifest = await adapter.connect()
    registry = registry_from_manifest(manifest, adapter)
    names = list(allow or ["report_state", "stop", "gripper", "place", "move_joints"])
    stepper = Stepper.build(
        mode=mode,
        llm=llm if llm is not None else _fake(),
        price=price if price is not None else resolve_decision_price(JEV),
        registry=registry,
        allow=names,
        goal="Say whether you are holding anything, then let it go",
        success=["you have said whether anything is held"],
        body=manifest.summary(),
        frames=frames,
    )
    return stepper, adapter, manifest


async def _observation(adapter: Any, last: dict[str, Any] | None = None) -> Any:
    from quackd.agent.providers.base import Observation

    return Observation(
        text="x",
        features={
            "state": (await adapter.get_state()).model_dump(),
            "detections": [],
            "last_result": last,
            "allowed": [],
        },
    )


BUDGET = "step 0/12, llm calls 0/12, 0.0/3 min"


async def _advise(llm: Any, **over: Any) -> Any:
    """Build a stepper on the mock arm around `llm`, and take one turn."""
    stepper, adapter, _manifest = await _arm(
        mode=over.pop("mode", "on"),
        allow=over.pop("allow", None),
        llm=llm,
        price=over.pop("price", None),
    )
    if "goal" in over:
        stepper.goal = over.pop("goal")
    obs = await _observation(adapter, over.pop("last", None))
    advice = await stepper.advise(obs, budget=BUDGET, **over)
    await adapter.disconnect()
    return advice, stepper, llm


class _UsageThatRaises:
    """A `usage` whose read blows up in a way the tolerant reader does not catch.

    Not a hypothetical shape: the SDK is early access and both token counts are documented as
    optional, so a lazily parsed or computed `input_tokens` is exactly the kind of thing that
    raises something other than AttributeError on a bad response."""

    @property
    def input_tokens(self) -> int:
        raise ArithmeticError("the vendor's own parser fell over")

    output_tokens = 0


async def test_a_usage_that_raises_costs_the_turn_an_estimate_and_not_the_run() -> None:
    """Reading the vendor's count was the one read of its answer outside a guard, so a usage
    field that raised ended the whole run with a traceback while the arm was energised. That is
    the single thing this module must never do, and the neighbouring guard around `_route`
    exists for exactly this reason. The turn is charged at the estimate rather than lost."""
    fake = _fake(answers=fake_systemone.turn("report_state"))
    fake.usage = _UsageThatRaises()  # type: ignore[assignment]
    advice, stepper, _ = await _advise(fake, cleared=True)

    assert advice.record["usage_estimated"] is True, "an unreadable count is not a measurement"
    assert advice.record["usage"]["input_tokens"] > 0
    assert advice.record["cost_usd"] > 0
    assert stepper.summary()["cost_estimated"] is True
    assert advice.record["gate"] == "taken", "the answer itself was fine; only the bill was not"


async def test_a_token_count_that_is_not_positive_is_not_a_measurement() -> None:
    """A request always has input, so a zero or a negative is a field nobody filled in rather
    than a free turn. Taken at face value it recorded a negative token count at no cost with
    `usage_estimated` false, and it would have cancelled out the real tokens of the turns
    around it in the run's rollup."""
    fake = _fake(
        answers=fake_systemone.turn("report_state"),
        usage=fake_systemone.Usage(input_tokens=-10_000, output_tokens=0),
    )
    advice, stepper, _ = await _advise(fake, cleared=True)

    assert advice.record["usage"]["input_tokens"] > 0
    assert advice.record["usage_estimated"] is True
    assert stepper.summary()["usage"]["input_tokens"] > 0


async def test_questions_whose_text_is_under_other_names_still_bill() -> None:
    """The question half of the estimate is read off duck-typed attributes. An SDK that renamed
    them used to make `_question_chars` return zero, and a zero there does not merely
    under-bill the turn: `advise` reads it as "nothing was sent" and skips the bill for a
    request that did go out. The fallback is the wrong number in the right direction."""
    from quackd.agent.decision.stepper import _question_chars

    class _Opaque:
        def __init__(self, text: str) -> None:
            self.prompt = text

    assert _question_chars({}) == 0, "no questions really is nothing"
    assert _question_chars({"next_verb": _Opaque("choose one of these five verbs")}) > 0


async def test_the_arm_never_offers_the_stepper_a_pose() -> None:
    """The headline claim, on the body that has run on real hardware."""
    stepper, adapter, _m = await _arm()
    offered = [call.label for call in stepper.labels_for(cleared=True)]
    assert offered == [
        "report_state",
        "stop",
        "gripper(open=true)",
        "gripper(open=false)",
        "place",
    ]
    assert not any("move_joints" in label for label in offered)
    await adapter.disconnect()


async def test_before_a_verdict_the_stepper_is_offered_only_what_the_gate_would_pass() -> None:
    """So `VerdictRequired` is unreachable from a stepper-authored call rather than caught.

    This is what the hero run did unprompted: its first call was `report_state` and its second
    was the verdict."""
    stepper, adapter, _m = await _arm()
    assert [c.label for c in stepper.labels_for(cleared=False)] == ["report_state", "stop"]
    assert len(stepper.labels_for(cleared=True)) == 5
    await adapter.disconnect()


async def test_a_confident_choice_is_taken_and_becomes_a_real_tool_call() -> None:
    advice, stepper, _f = await _advise(
        _fake(answers=fake_systemone.turn("report_state", 0.91)),
        cleared=False,
    )
    assert advice.gate == "taken"
    assert advice.call is not None
    assert (advice.call.name, advice.call.arguments) == ("report_state", {})
    assert advice.record["class"] == "read" and advice.record["floor"] == FLOORS["read"]
    assert stepper.taken == 1 and stepper.asked == 1


async def test_a_choice_below_its_floor_goes_to_the_model_and_says_which_floor_it_missed() -> None:
    """Motion answers to a higher floor than a read, so the same 0.70 is taken for one and
    refused for the other. The record has to say which floor applied, or a calibration pass
    afterwards cannot tell a near miss from a wild guess."""
    advice, _s, _f = await _advise(
        _fake(answers=fake_systemone.turn("gripper(open=false)", 0.70)),
        cleared=True,
    )
    assert advice.gate == "below_floor" and advice.call is None
    assert advice.record["floor"] == FLOORS["motion"] == 0.85
    assert advice.record["confidence"] == 0.70


async def test_the_same_confidence_clears_a_read_and_misses_a_move() -> None:
    read, _s, _f = await _advise(
        _fake(answers=fake_systemone.turn("report_state", 0.70)),
        cleared=True,
    )
    move, _s2, _f2 = await _advise(
        _fake(answers=fake_systemone.turn("place", 0.70)),
        cleared=True,
    )
    assert read.gate == "taken" and move.gate == "below_floor"


async def test_the_way_out_hands_the_turn_back_however_confident_it_is() -> None:
    advice, _s, _f = await _advise(
        _fake(answers=fake_systemone.turn(ESCALATE, 1.0)),
        cleared=True,
    )
    assert advice.gate == "escalate" and advice.call is None


async def test_a_stepper_that_thinks_the_job_is_done_moves_nothing_else() -> None:
    """The Nouls are read before the Choice, so a finished task never gets one more move.

    A Noul carries no confidence, so 0.5 here is a raw probability and means "more likely
    than not"."""
    advice, _s, _f = await _advise(
        _fake(answers=fake_systemone.turn("place", 0.99, done=0.62)),
        cleared=True,
    )
    assert advice.gate == "done" and advice.call is None


async def test_a_stepper_that_wants_a_person_hands_the_turn_back() -> None:
    advice, _s, _f = await _advise(
        _fake(answers=fake_systemone.turn("place", 0.99, need_human=0.8)),
        cleared=True,
    )
    assert advice.gate == "need_human" and advice.call is None


async def test_a_decision_llm_that_raises_costs_the_turn_and_not_the_run() -> None:
    """An arm is energised while this runs. Whatever `decide` raises -- a hosted vendor's
    outage, a server on the bench that is not listening, a plugin with a bug in it -- escalates
    the turn and is written down; it never reaches the loop as an exception, and the model
    takes the turn as it would have done had the stepper merely declined it."""
    advice, stepper, _f = await _advise(
        _fake(raises=fake_systemone.APITimeoutError("took too long")),
        cleared=True,
    )
    assert advice.gate == "error" and advice.call is None
    assert "APITimeoutError" in advice.record["error"]
    assert stepper.errors == 1 and stepper.asked == 1


async def test_a_body_with_nothing_discrete_never_reaches_the_network() -> None:
    """A duck whose whole allowlist is a number is not a duck the stepper can help with, and
    finding that out must not cost a request."""
    fake = _fake(answers=fake_systemone.turn("move_joints", 0.99))
    advice, _s, used = await _advise(fake, cleared=True, allow=["move_joints"])
    assert advice.gate == "not_offered" and advice.call is None
    assert used.calls == [], "a question was asked when there was nothing to ask about"


# ── the state ───────────────────────────────────────────────────────────────────────────


def _sentences_only(state: Any) -> None:
    """The state is named English fields, each one a sentence, and no part of a picture."""
    assert "goal" in state and "now" in state and "body" in state
    assert all(isinstance(v, str) for v in state.values()), "every field is a sentence"
    blob = " ".join(state.values()).lower()
    for forbidden in ("png", "base64", "data:image", "jpeg"):
        assert forbidden not in blob, f"{forbidden} reached the state"
    assert chr(10) not in blob, "a field carried layout rather than a sentence"


async def test_the_state_is_named_english_fields_and_carries_no_picture(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every decision LLM is sent this text, and no frame ever rides inside it. The one row that
    may see is shown the camera beside the state, on a run that asks with `--decision-frames`,
    and the state it is sent then is the same named English fields with one more sentence, which
    says an image went beside it. The instructions belong in the questions rather than in the
    state."""
    from quackd.agent.decision.factory import make_decision_llm
    from quackd.agent.decision.frames import Snapshot
    from quackd.transport.base import frames_of
    from tests import fake_decisions_api as fake_api

    _a, _s, fake = await _advise(
        _fake(answers=fake_systemone.turn("report_state", 0.91)),
        cleared=False,
    )
    state, questions = fake.calls[0]
    assert set(questions) == {"next_verb", "done", "need_human", "feasible"}
    _sentences_only(state)
    assert "picture" not in state

    # and the frames case: OpenAI's row, built to see, shown the mock arm's own camera
    sdk = fake_api.install(monkeypatch, fake_api.FakeOpenAI(answers=fake_api.turn("report_state")))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-a-real-key")
    seeing = make_decision_llm(PRESETS["openai"], frames=True)
    stepper, adapter, _m = await _arm(llm=seeing, frames=True)
    (frame,) = await frames_of(adapter)
    snapshot = Snapshot(frame.image, frame.name, tmp_path / "0000-decision.jpg", "x.jpg")
    advice = await stepper.advise(
        await _observation(adapter), cleared=False, budget=BUDGET, snapshot=snapshot
    )
    await adapter.disconnect()
    assert advice.record["frame"]["kept"] is True, "no frame went, so this proves nothing"
    (text, _image) = sdk.posts[0]["body"]["input"][0]["content"]
    shown = json.loads(text["text"])
    _sentences_only(shown)
    assert shown["picture"].startswith("The image sent with this state")
    assert {k: v for k, v in shown.items() if k != "picture"}.keys() == state.keys()


async def test_the_goal_survives_a_state_that_has_to_be_trimmed() -> None:
    """Notes go first and the goal never goes, and the record says what went, because a
    stepper answering badly on a long run is a different problem from one answering badly on
    a short one and afterwards the trim is the only way to tell."""
    _a, _s, fake = await _advise(
        _fake(answers=fake_systemone.turn("report_state", 0.91)),
        cleared=False,
        goal="Tidy the bench. " * 700,
        notes="a remembered fact worth keeping",
    )
    state, _questions = fake.calls[0]
    assert "goal" in state and "success_when" in state and "now" in state
    assert "notes" not in state and "camera" not in state, "the trim ran in its stated order"


async def test_a_state_nobody_could_answer_against_is_not_sent_at_all() -> None:
    """If the fields that can never be dropped are themselves over the hard cap, the turn goes
    to the model without a request being made."""
    fake = _fake(answers=fake_systemone.turn("report_state", 0.99))
    stepper, adapter, _m = await _arm(llm=fake)
    stepper.goal = "x" * 40_000
    advice = await stepper.advise(await _observation(adapter), cleared=False, budget=BUDGET)
    await adapter.disconnect()
    assert advice.gate == "state_too_large" and advice.call is None
    assert fake.calls == []


# ── the questions ───────────────────────────────────────────────────────────────────────


async def test_the_questions_go_out_as_plain_dicts_in_the_system_one_shape() -> None:
    """Plain dicts rather than an SDK's own `Choice` and `Noul` objects.

    The same four questions go to a hosted API, to a server on this machine and to a model in
    this process, and only the first of those has classes to build them with. A mapping is what
    the format publishes and what every backend reads, so it is what goes out."""
    _a, stepper, fake = await _advise(
        _fake(answers=fake_systemone.turn("report_state", 0.91)),
        cleared=True,
    )
    _state, questions = fake.calls[0]
    assert all(isinstance(q, dict) for q in questions.values()), "an SDK object went out"
    asked = ("next_verb", "done", "need_human", "feasible")
    assert [questions[name]["type"] for name in asked] == ["choice", "noul", "noul", "choice"]
    assert questions["next_verb"]["instructions"].startswith("Which single action")
    criteria = questions["next_verb"]["criteria"]
    assert set(criteria) == {*(c.label for c in stepper.labels_for(cleared=True)), ESCALATE}
    assert all(isinstance(line, str) and line for line in criteria.values()), (
        "an option was offered with nothing said about what it means"
    )
    # Nothing between `build_questions` and the backend rewraps them, which is the property
    # that keeps one set of questions readable by all three.
    assert questions == build_questions(stepper.labels_for(cleared=True), stepper.what)


class _OffTheWire:
    """A decision LLM whose answer is a mapping rather than an object.

    Which is not the odd case: it is the in-process backend, every plugin, and any server read
    straight off `POST /v1/systemone` without the SDK in between. The SDK is the only one of
    the four that hands back objects."""

    name = JEV.name
    url = None

    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.model = JEV.model or ""
        self.calls: list[tuple[dict[str, Any], dict[str, Any]]] = []

    async def decide(self, state: Any, questions: Any) -> dict[str, Any]:
        self.calls.append((dict(state), dict(questions)))
        return self.payload


async def test_an_answer_off_the_wire_is_read_exactly_like_an_sdk_object() -> None:
    """The in-process backend and every plugin hand back a plain mapping, and so does a
    server's own JSON body; only the SDK hands back objects. An earlier `_answer` found the
    `answers` key on neither and silently escalated every turn a mapping answered, which is a
    whole backend quietly doing nothing while the record said it had been asked."""
    objects, _s, _f = await _advise(
        _fake(answers=fake_systemone.turn("report_state", 0.93)), cleared=True
    )
    off_the_wire, stepper, _f2 = await _advise(
        _OffTheWire(
            fake_systemone.wire(
                {
                    "next_verb": {"choice": "report_state", "confidence": 0.93},
                    "done": {"noul": 0.0},
                    "need_human": {"noul": 0.0},
                    "feasible": {"choice": "feasible", "confidence": 0.9},
                }
            )
        ),
        cleared=True,
    )

    assert off_the_wire.gate == "taken" == objects.gate, "a mapping answered nothing"
    assert off_the_wire.call is not None
    assert off_the_wire.call.name == "report_state"
    assert off_the_wire.record["choice"] == objects.record["choice"] == "report_state"
    assert off_the_wire.record["confidence"] == 0.93
    assert stepper.taken == 1


# ── shadow ──────────────────────────────────────────────────────────────────────────────


async def test_shadow_records_what_the_stepper_would_have_done_beside_what_the_model_did() -> None:
    """The record that turns the arithmetic in docs/guides/decision-llms/README.md into a
    measurement."""
    from quackd.agent.providers.base import ToolCall

    advice, stepper, _f = await _advise(
        _fake(answers=fake_systemone.turn("report_state", 0.91)),
        mode="shadow",
        cleared=False,
    )
    agreed = stepper.shadow_event(
        advice, ToolCall(name="report_state"), {"latency_s": 8.2, "usage": {"input_tokens": 4465}}
    )
    assert agreed["agree"] is True and agreed["would_have_acted"] is True
    assert agreed["llm_latency_s"] == 8.2 and agreed["decision_choice"] == "report_state"

    differed = stepper.shadow_event(advice, ToolCall(name="move_joints"), None)
    assert differed["agree"] is False and differed["model_verb"] == "move_joints"


async def test_the_summary_block_counts_the_turns_it_answered() -> None:
    _a, stepper, _f = await _advise(
        _fake(answers=fake_systemone.turn("report_state", 0.91)),
        cleared=False,
    )
    block = stepper.summary()
    assert block["asked"] == 1 and block["taken"] == 1 and block["errors"] == 0
    assert block["mode"] == "on"
    # Both, and not just the id: `jev-1.13.0` names itself, but `kev-latest` on one machine
    # and on another are two different servers, and a record carrying only the id could not
    # tell a reader which of them answered.
    assert block["llm"] == "jev"
    assert block["model"].startswith("jev-"), "TypeSafe's own model id"


# ── end to end, through the loop ────────────────────────────────────────────────────────


def _lookout() -> Any:
    from quackd.duckfile.parser import load_duck
    from tests.conftest import DUCKS

    return load_duck(str(DUCKS / "lerobot-lookout.duck"))


async def _lookout_run(tmp_path: Any, **over: Any) -> Any:
    """`lerobot-lookout` on the mock arm with the scripted pilot, with or without a stepper."""
    from quackd.agent.loop import RunConfig, run_duck
    from quackd.agent.providers.fake import FakeProvider
    from quackd_lerobot import LeRobotAdapter
    from quackd_lerobot.mock import LeRobotMock

    duck = _lookout()
    return await run_duck(
        RunConfig(
            duck=duck,
            provider=FakeProvider.for_duck(duck.name),
            transport=LeRobotAdapter(LeRobotMock()),
            runs_dir=tmp_path,
            **over,
        )
    )


def _records(result: Any, kind: str) -> list[dict[str, Any]]:
    from quackd.agent.transcript import Transcript

    return [r for r in Transcript.read(result.run_dir / "transcript.jsonl") if r["kind"] == kind]


def _calls(result: Any) -> list[tuple[str, Any]]:
    return [(r["name"], r.get("params")) for r in _records(result, "verb")]


async def test_a_run_with_the_stepper_off_is_the_run_quackd_has_always_made(
    tmp_path: Any,
) -> None:
    """The off path is not a mode, it is the absence of one: no stepper is built, no record is
    written, and the verbs and the model calls are what they were."""
    plain = await _lookout_run(tmp_path / "plain")
    off = await _lookout_run(tmp_path / "off", decision="off")
    assert _calls(plain) == _calls(off)
    assert len(_records(plain, "llm")) == len(_records(off, "llm"))
    assert _records(off, "decision") == [] and _records(off, "decision_shadow") == []


async def test_shadow_mode_leaves_the_tool_call_stream_byte_identical(
    tmp_path: Any,
) -> None:
    """The point of shadow: it measures and changes nothing. Same verbs, same model calls,
    plus a record of what the stepper would have done instead."""
    off = await _lookout_run(tmp_path / "off", decision="off")
    shadow = await _lookout_run(
        tmp_path / "shadow",
        decision="shadow",
        decision_llm=_fake(answers=fake_systemone.turn("report_state", 0.99)),
        decision_price=resolve_decision_price(JEV),
    )
    assert _calls(shadow) == _calls(off)
    assert len(_records(shadow, "llm")) == len(_records(off, "llm"))
    assert _records(shadow, "decision"), "shadow recorded nothing"
    assert _records(shadow, "decision_shadow"), "nothing was compared against the model"
    assert all(r["source"] == "agent" for r in _records(shadow, "verb_start"))


class _Recorder:
    """A provider that remembers every history it was handed, and defers to the real one."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.seen: list[list[Any]] = []

    name = property(lambda self: self.inner.name)
    model = property(lambda self: self.inner.model)
    supports_vision = property(lambda self: self.inner.supports_vision)

    async def step(self, system: str, history: list[Any], tools: list[Any]) -> Any:
        self.seen.append(list(history))
        return await self.inner.step(system, history, tools)


async def _on_run(tmp_path: Any, llm: Any, **over: Any) -> Any:
    """`lerobot-lookout` with the stepper on, answered by `llm`.

    The decision LLM is handed to the run built, the way the CLI hands one over: a run is
    refused outright if a mode names nobody to answer it, and a price goes with it because a
    turn that cannot be costed is a turn missing from the summary."""
    from quackd.agent.loop import RunConfig, run_duck
    from quackd.agent.providers.fake import FakeProvider
    from quackd_lerobot import LeRobotAdapter
    from quackd_lerobot.mock import LeRobotMock

    duck = over.pop("duck", None) or _lookout()
    recorder = _Recorder(FakeProvider.for_duck(duck.name))
    result = await run_duck(
        RunConfig(
            duck=duck,
            provider=recorder,  # type: ignore[arg-type]
            transport=LeRobotAdapter(LeRobotMock()),
            runs_dir=tmp_path,
            decision="on",
            decision_llm=llm,
            decision_price=resolve_decision_price(JEV),
            **over,
        )
    )
    return result, recorder


async def test_nothing_the_stepper_chose_is_ever_replayed_to_the_model_as_its_own_words(
    tmp_path: Any,
) -> None:
    """The central guarantee.

    A stepper turn appends nothing to the model's history. Writing a `Decision` for it would
    hand the model back a tool call it never made, and on Gemini specifically an unsigned
    `function_call`, which that model refuses the next turn over."""
    result, recorder = await _on_run(
        tmp_path,
        _fake(answers=fake_systemone.turn("report_state", 0.99)),
    )
    assert result.outcome in ("success", "budget", "failure")
    stepper_verbs = [r for r in _records(result, "verb_start") if r["source"] == "decision"]
    assert stepper_verbs, "the stepper never got a turn, so this proves nothing"
    for history in recorder.seen:
        for exchange in history:
            decision = exchange.decision
            if decision is not None:
                assert decision.tool_call.id.startswith(("fake", "")), decision.tool_call.id
                assert not decision.tool_call.id.startswith("decision-"), (
                    "a stepper call was replayed to the model as something it said"
                )


async def test_the_model_is_told_what_the_stepper_did_while_it_was_away(tmp_path: Any) -> None:
    """It is not in its history, so the observation has to say so, and say who chose them."""
    _result, recorder = await _on_run(
        tmp_path,
        _fake(answers=fake_systemone.turn("report_state", 0.99)),
    )
    told = [
        text
        for history in recorder.seen
        for exchange in history
        if "stepper chose these" in (text := exchange.observation.text)
    ]
    assert told, "the model was never told what happened while it was not asked"
    assert "report_state" in told[-1]


async def test_a_stepper_turn_charges_no_model_call(tmp_path: Any) -> None:
    """`max_llm_calls` keeps meaning calls to the model. The verb still charges its step, so
    `max_steps` bounds the run exactly as it always did."""
    result, _rec = await _on_run(
        tmp_path / "on",
        _fake(answers=fake_systemone.turn("report_state", 0.99)),
    )
    taken = [r for r in _records(result, "decision") if r["gate"] == "taken"]
    assert taken, "no turn was taken, so there is nothing to count"
    # every turn is either the stepper's or the model's, and there is one model call per
    # model turn. A `step` is not a turn: a meta tool costs a call and no step, so several
    # turns share a number and the counts are what can be compared.
    asked = _records(result, "decision")
    assert len(_records(result, "llm")) == len(asked) - len(taken), (
        "a turn the stepper answered also cost a model call"
    )
    assert any(r["source"] == "decision" for r in _records(result, "verb_start")), (
        "the record does not say who chose the verb"
    )


async def test_the_stepper_does_not_answer_twice_running_with_the_same_call(tmp_path: Any) -> None:
    """A reflex that fires twice identically is looping, not deciding.

    Without this a `lerobot-lookout` whose stepper answered `report_state` at 0.99 every turn
    spent all twelve steps on it and ended `budget`, having asked the model nothing and so
    having recorded no verdict and declared no outcome. Only the model can end a run."""
    result, _rec = await _on_run(
        tmp_path,
        _fake(answers=fake_systemone.turn("report_state", 0.99)),
    )
    gates = [r["gate"] for r in _records(result, "decision")]
    assert "repeat" in gates, "the stepper repeated itself and nothing stopped it"
    assert result.outcome == "success", f"the run ended {result.outcome}, not on its own terms"
    assert _records(result, "llm"), "the model was never consulted"


async def test_a_stepper_that_never_repeats_still_has_to_hand_the_run_back() -> None:
    """The backstop behind the repeat rule: alternating two calls for ever is also a loop, and
    only the model can record a verdict or declare an outcome."""
    from quackd.agent.decision.stepper import MAX_IN_A_ROW

    fake = _fake(
        script=[
            fake_systemone.turn("gripper(open=true)" if i % 2 else "gripper(open=false)", 0.99)
            for i in range(MAX_IN_A_ROW + 3)
        ]
    )
    stepper, adapter, _m = await _arm(llm=fake)
    obs = await _observation(adapter)
    gates = []
    for _ in range(MAX_IN_A_ROW + 2):
        gates.append((await stepper.advise(obs, cleared=True, budget=BUDGET)).gate)
    await adapter.disconnect()
    assert gates[:MAX_IN_A_ROW] == ["taken"] * MAX_IN_A_ROW
    assert gates[MAX_IN_A_ROW] == "handover", "the stepper never gave the model a turn"
    # and the run of turns starts again from there, so the backstop is a rhythm and not a
    # one-shot fuse that quietly switches the stepper off for the rest of the run
    assert gates[MAX_IN_A_ROW + 1] == "taken"


async def test_a_wave_like_goal_never_gets_a_pose_out_of_the_stepper(tmp_path: Any) -> None:
    """The LeRobot claim `docs/guides/decision-llms/README.md` is built on, end to end and on the
    arm.

    The stub answers every question at 0.99, so anything the stepper is allowed to author it
    will. `move_joints` is never among the labels, so it can never be chosen, and every pose
    in the run came from a model call."""
    from quackd.duckfile.parser import duck_from_goal

    duck = duck_from_goal(
        "Wave to the camera with an extended arm",
        ["report_state", "stop", "gripper", "place", "move_joints"],
    )
    result, _rec = await _on_run(
        tmp_path,
        _fake(answers=fake_systemone.turn("move_joints", 0.99)),
        duck=duck,
    )
    asked = _records(result, "decision")
    assert asked, "the stepper was never asked"
    for record in asked:
        assert "move_joints" not in record["labels"], "a pose was on offer"
        assert (record.get("call") or {}).get("name") != "move_joints"
    poses = [r for r in _records(result, "verb_start") if r["name"] == "move_joints"]
    assert all(r["source"] == "agent" for r in poses), "a pose was not the model's"


async def test_a_choice_that_was_not_on_offer_this_turn_is_refused() -> None:
    """Checked against what was offered, not against everything the body can do.

    This is the whole of the verdict gate as the stepper sees it. Before a verdict `gripper`
    is not on the list, and a stepper that answered it anyway would have the executor refuse
    the call and waste the turn. It was doing exactly that until a real run showed it: two
    turns of `gripper REFUSED` and `place REFUSED` before the model was ever asked."""
    advice, stepper, _f = await _advise(
        _fake(answers=fake_systemone.turn("gripper(open=false)", 0.99)),
        cleared=False,
    )
    assert advice.gate == "escalate" and advice.call is None
    assert "gripper(open=false)" in stepper.calls, "the label exists, it was just not offered"
    assert "gripper(open=false)" not in advice.record["labels"]


async def test_the_verdict_gate_never_refuses_a_verb_the_stepper_chose(tmp_path: Any) -> None:
    """End to end, on the arm: no gate in the whole run refuses a call the stepper authored."""
    duck_path = pathlib.Path("ducks/arm-grip-check.duck")
    from quackd.duckfile.parser import load_duck

    result, _rec = await _on_run(
        tmp_path,
        _fake(
            script=[
                fake_systemone.turn("report_state", 0.97),
                fake_systemone.turn("gripper(open=false)", 0.93),
                fake_systemone.turn("report_state", 0.95),
                fake_systemone.turn("stop", 0.91),
            ],
            answers=fake_systemone.turn(ESCALATE, 0.99, done=0.9),
        ),
        duck=load_duck(str(duck_path)),
    )
    refused = [
        r for r in _records(result, "gate") if r.get("gate") == "verdict" and r["outcome"] != "ok"
    ]
    assert not refused, f"the stepper was offered a verb the verdict gate refuses: {refused}"
    assert any(r["gate"] == "taken" for r in _records(result, "decision"))


def test_a_stepper_that_cannot_run_warns_once_and_the_run_carries_on(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The stepper is an optimisation and the model is the pilot either way, so a script that
    always names a decision LLM still drives the robot on a machine where that one is not
    installed. The run is the run it would have been with no decision LLM at all, and the
    terminal says why once."""
    import sys

    from typer.testing import CliRunner

    from quackd.cli import app

    monkeypatch.setitem(sys.modules, "typesafe_sdk", None)
    result = CliRunner().invoke(
        app,
        [
            "run",
            "lerobot-lookout",
            "--robot",
            "lerobot:mock",
            "--llm",
            "fake",
            "--decision-llm",
            "jev",
            "--no-log",
            "--runs-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "running without it" in result.output
    assert "quackd[decision]" in result.output, "the warning does not say what to install"
    run_dir = next(tmp_path.iterdir())
    records = [
        r
        for r in (tmp_path / run_dir.name / "transcript.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if '"kind": "decision"' in r
    ]
    assert not records, "a stepper ran after the warning said it would not"


def test_a_mode_nobody_defined_never_reaches_the_robot(tmp_path: Any) -> None:
    """A missing install is somebody's machine. A mode that is not one of the three is a typo,
    and running the robot anyway would hide it.

    `tests/test_decision_llms.py` has the refusal itself; what this one adds is that it
    happens while the CLI is still reading flags, before anything is connected."""
    from typer.testing import CliRunner

    from quackd.cli import app

    result = CliRunner().invoke(
        app,
        [
            "run",
            "lerobot-lookout",
            "--robot",
            "lerobot:mock",
            "--llm",
            "fake",
            "--decision-mode",
            "maybe",
            "--runs-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code != 0
    assert "unknown --decision-mode 'maybe'" in result.output
    assert "off, shadow, on" in result.output
    assert not list(tmp_path.iterdir()), "the run started before the typo was noticed"


def test_switching_the_stepper_on_says_it_has_never_been_measured(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The plan for this feature gated `--decision-mode on` behind a measurement that does not
    exist.

    Refusing the flag would be the wrong shape of gate, because the executor binds a
    stepper-authored call exactly as it binds the model's, so `on` is not less safe than `off`,
    only less proven. What it is not is measured, and the person switching it on is the one who
    should be told. `shadow` says nothing, because shadow is how the measurement gets made.

    The one test in this file that installs an SDK at all, because the CLI is the half that
    builds a client and this run has to get that far before it can warn about the mode."""
    fake_systemone.install(monkeypatch, _fake(answers=fake_systemone.turn("stop")))
    monkeypatch.setenv("TYPESAFE_API_KEY", "sk-test")
    from typer.testing import CliRunner

    from quackd.cli import app

    def run(mode: str) -> str:
        return (
            CliRunner()
            .invoke(
                app,
                [
                    "run",
                    "lerobot-lookout",
                    "--robot",
                    "lerobot:mock",
                    "--llm",
                    "fake",
                    "--decision-llm",
                    "jev",
                    "--decision-mode",
                    mode,
                    "--no-log",
                    "--runs-dir",
                    str(tmp_path / mode),
                ],
            )
            .output
        )

    from quackd.cli import _DECISION_MODE_ON_UNMEASURED

    said = run("on")
    assert "has not been measured" in said
    assert "docs/guides/decision-llms/README.md" in said, (
        "the notice does not say where the estimates are"
    )
    # The whole sentence, which `tests/test_docs.py` holds to the rows the hub estimates for, so
    # that check is a check on what a person is shown.
    assert " ".join(_DECISION_MODE_ON_UNMEASURED.split()) in " ".join(said.split())
    assert "has not been measured" not in run("shadow"), "shadow is how it gets measured"


# ── what the adversarial pass found ─────────────────────────────────────────────────────


def test_agreement_is_about_the_call_and_not_the_word() -> None:
    """`gripper(open=true)` and `gripper(open=false)` are opposite instructions that share a
    name, and shadow mode's whole purpose is the agreement rate. Comparing verb names scored
    them as agreement, which corrupted the one measurement `docs/guides/decision-llms/README.md`
    says earns the right to move a floor, and did it worst on `arm-grip-check`, the benchmark that
    page names."""
    from quackd.agent.decision.stepper import Advice, Stepper
    from quackd.agent.providers.base import ToolCall

    stepper = Stepper(mode="shadow", goal="g")
    advice = Advice(None, {"choice": "gripper(open=true)", "confidence": 0.97, "gate": "taken"})

    opposite = stepper.shadow_event(advice, ToolCall(name="gripper", arguments={"open": False}))
    assert opposite["agree"] is False, "opposite calls of one verb are not agreement"
    assert opposite["same_verb"] is True, "the coarser comparison is still recorded"

    same = stepper.shadow_event(advice, ToolCall(name="gripper", arguments={"open": True}))
    assert same["agree"] is True

    other = stepper.shadow_event(advice, ToolCall(name="move_joints", arguments={"positions": {}}))
    assert other["agree"] is False and other["same_verb"] is False


def test_a_label_reads_the_same_whatever_order_the_arguments_arrive_in() -> None:
    """The label is matched back by string equality, and a call from a provider carries its
    arguments in whatever order the wire had them."""
    from quackd.agent.decision.stepper import _label

    assert _label("gripper", {"side": "left", "open": True}) == _label(
        "gripper", {"open": True, "side": "left"}
    )


async def test_a_number_that_is_not_a_number_hands_the_turn_back() -> None:
    """NaN is not a low confidence, it is no confidence, and it loses every comparison it is
    put through: `nan < 0.85` is False. Unguarded it cleared the motion floor, the done gate
    and the need_human gate at once and moved the body."""
    for bad in (float("nan"), float("inf")):
        answers = fake_systemone.turn("gripper(open=false)", 0.99)
        answers["next_verb"] = fake_systemone.ChoiceAnswer(
            choice="gripper(open=false)", confidence=bad, probabilities={}
        )
        advice, _s, _f = await _advise(_fake(answers=answers), cleared=True)
        assert advice.gate == "unreadable", f"{bad!r} was acted on"
        assert advice.call is None

    # and a NaN in either Noul is the same answer
    answers = fake_systemone.turn("gripper(open=false)", 0.99)
    answers["done"] = fake_systemone.NoulAnswer(float("nan"))
    advice, _s, _f = await _advise(_fake(answers=answers), cleared=True)
    assert advice.gate == "unreadable" and advice.call is None


async def test_an_answer_the_router_cannot_read_costs_the_turn_and_not_the_run() -> None:
    """Reading the answer is part of the call, so it has to fail the way the call does. The
    parse used to sit outside the guard, so an early-access SDK returning a confidence of
    "high" raised through the loop and ended the run with a traceback, mid-task, with the arm
    energised."""
    cases = [
        # a confidence that is not a number is caught at the edge by `_probability` and reads
        # as no confidence, which is the gentler of the two answers and still acts on nothing
        ({"confidence": "high"}, "unreadable"),
        # these two blow up inside the parse itself, which is what used to escape `advise`
        ({"probabilities": ["a"]}, "error"),
        ({"probabilities": {"a": "high"}}, "error"),
    ]
    for kwargs, expected in cases:
        broken = {
            "next_verb": fake_systemone.ChoiceAnswer(choice="stop", **kwargs)  # type: ignore[arg-type]
        }
        advice, stepper, _f = await _advise(_fake(answers=broken), cleared=True)
        assert advice.gate == expected, f"{kwargs} gave {advice.gate}"
        assert advice.call is None, "the body moved on an answer nobody could read"
        if expected == "error":
            assert advice.record["error"] and stepper.errors == 1


async def test_the_model_is_answered_with_its_own_verbs_result(tmp_path: Any) -> None:
    """A tool_result answers the tool call it is attached to. That is the protocol, and a
    stepper running in between must not change it.

    The observation built after a model-chosen verb is discarded when the stepper answers the
    next turn, so the id linkage was recomputed from the last surviving decision and the
    model's own result was silently replaced by a later stepper verb's. On `lerobot-lookout`
    that meant the model declared success on a reading it was never handed."""
    result, recorder = await _on_run(
        tmp_path,
        _fake(
            script=[fake_systemone.turn("report_state", 0.99)] * 4
            + [fake_systemone.turn("stop", 0.99)] * 8
        ),
    )
    history = recorder.seen[-1]
    answered = 0
    for i, exchange in enumerate(history):
        call_id = exchange.observation.tool_call_id
        if call_id is None:
            continue
        asked = next(
            (e for e in history[:i] if e.decision and e.decision.tool_call.id == call_id), None
        )
        if asked is None:
            continue
        lines = exchange.observation.text.splitlines()
        named = [ln for ln in lines if ln.startswith("last verb")]
        assert named, "a tool_result carried no result at all"
        assert f"`{asked.decision.tool_call.name}`" in named[0], (
            f"the answer to {asked.decision.tool_call.name} reports something else: {named[0]}"
        )
        answered += 1
    assert answered, "no tool_result was checked, so this proves nothing"
    assert result.outcome == "success"


# ── what a turn costs ───────────────────────────────────────────────────────────────────


async def test_a_turn_the_api_counted_is_billed_at_what_it_said_it_spent() -> None:
    """TypeSafe charge per input token, so a turn they counted is priced and never guessed.

    The rate is theirs and published: $0.042 per million input tokens, output not charged. It
    is the only published rate in the whole preset table -- every other row is a server you run
    -- and this multiplies it out here rather than trusting the number elsewhere, because the
    case for the stepper in `docs/guides/decision-llms/README.md` is a ratio, and a rate that
    quietly moved would move that ratio with it."""
    advice, stepper, _f = await _advise(
        _fake(
            answers=fake_systemone.turn("report_state", 0.91),
            usage=fake_systemone.Usage(input_tokens=1631, output_tokens=16),
        ),
        cleared=False,
    )
    assert JEV.price is not None
    assert (JEV.price.input, JEV.price.output) == (0.042, 0.0), "TypeSafe's published rate moved"
    assert advice.record["usage"] == {"input_tokens": 1631, "output_tokens": 16}
    assert advice.record["usage_estimated"] is False
    assert advice.record["cost_usd"] == round(1631 * 0.042 / 1_000_000, 9)
    assert stepper.input_tokens == 1631 and stepper.estimated is False


async def test_a_turn_the_api_did_not_count_is_estimated_from_the_whole_request() -> None:
    """The state was never the size of the request, and billing it as if it were bills a
    quarter of the turn.

    The four questions carry every label, every verb's one-line description and every
    criterion, which on this mock arm is over a thousand characters against 388 of state; the
    worked example in `docs/guides/decision-llms/README.md` counted 1,721 against that same 388. The
    record keeps `state_tokens_est` for the trim, and the estimate has to be strictly bigger than
    it."""
    from quackd.agent.decision.stepper import _question_chars

    advice, stepper, fake = await _advise(
        _fake(answers=fake_systemone.turn("report_state", 0.91), usage=None),
        cleared=False,
    )
    _state, questions = fake.calls[0]
    asked_chars = _question_chars(questions)
    assert asked_chars > 1_000, "the questions stopped being the larger half of the request"
    assert advice.record["usage_estimated"] is True and stepper.estimated is True
    assert (
        advice.record["usage"]["input_tokens"] == (advice.record["state_chars"] + asked_chars) // 4
    )
    assert advice.record["usage"]["input_tokens"] > advice.record["state_tokens_est"]


async def test_a_negotiated_rate_beats_the_published_one_and_the_run_says_which_it_used(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Somebody whose contract differs has to be able to say so, and the summary has to carry
    which rate the figure came from. A cost checked months later against the pricing page is
    the one way a wrong rate gets found, and it only works if the run wrote down its own."""
    monkeypatch.setenv("QUACKD_DECISION_PRICE", "in=0.42,out=0")
    advice, stepper, _f = await _advise(
        _fake(
            answers=fake_systemone.turn("report_state", 0.91),
            usage=fake_systemone.Usage(input_tokens=1631, output_tokens=16),
        ),
        cleared=False,
    )
    assert advice.record["cost_usd"] == round(1631 * 0.42 / 1_000_000, 9)
    assert advice.record["cost_usd"] > round(1631 * 0.042 / 1_000_000, 9), "the rate was ignored"
    price = stepper.summary()["price"]
    assert price["source"] == "QUACKD_DECISION_PRICE" and price["input"] == 0.42


async def test_a_turn_that_never_reached_the_network_is_not_billed_at_all() -> None:
    """Both of these gates answer before a request is made, so both owe nothing, and the way
    to say nothing is to write no keys rather than a zero. A `cost_usd: 0.0` on a turn that
    never happened reads as a turn that was free, which is a different and false claim."""
    nothing_discrete, idle, used = await _advise(
        _fake(answers=fake_systemone.turn("move_joints", 0.99)),
        cleared=True,
        allow=["move_joints"],
    )
    assert nothing_discrete.gate == "not_offered" and used.calls == []

    fake = _fake(answers=fake_systemone.turn("report_state", 0.99))
    stepper, adapter, _m = await _arm(llm=fake)
    stepper.goal = "x" * 40_000
    too_large = await stepper.advise(await _observation(adapter), cleared=False, budget=BUDGET)
    await adapter.disconnect()
    assert too_large.gate == "state_too_large" and fake.calls == []

    for advice, unbilled in ((nothing_discrete, idle), (too_large, stepper)):
        for key in ("usage", "usage_estimated", "cost_usd"):
            assert key not in advice.record, f"{advice.gate} was billed a {key}"
        assert unbilled.cost_usd == 0.0 and unbilled.input_tokens == 0


async def test_a_call_that_failed_after_the_request_left_is_billed_as_an_estimate() -> None:
    """Nobody publishes whether a call that failed on their side is charged, and between an
    estimate that is slightly high and a bill that silently is not there, the high one is the
    one nobody is hurt by.

    The other half of the same rule is why the bill hangs off what actually went out. A turn
    that fell over before the questions were built sent nothing and owes nothing, and writing
    a zero for it would be a different and false claim: that the request happened and was
    free. That half used to be reached by a machine with no `typesafe_sdk`, which reached it
    for the wrong reason; the client is built before the robot connects now, so a turn can
    only fail this early on its own questions."""
    advice, stepper, _f = await _advise(
        _fake(raises=fake_systemone.APITimeoutError("took too long")),
        cleared=True,
    )
    assert advice.gate == "error"
    assert advice.record["usage_estimated"] is True
    assert advice.record["usage"]["input_tokens"] > 0
    assert advice.record["cost_usd"] > 0
    assert stepper.cost_usd == advice.record["cost_usd"] and stepper.estimated is True

    never_sent, adapter, _m = await _arm()
    # A label offered with nothing said about what it means: `build_questions` reads the
    # descriptions by label, so this raises before a byte could have left the machine.
    never_sent.what.pop("report_state")
    unsent = await never_sent.advise(await _observation(adapter), cleared=False, budget=BUDGET)
    await adapter.disconnect()
    assert unsent.gate == "error", "a question that could not be built ended the turn otherwise"
    assert "cost_usd" not in unsent.record, "a turn that sent nothing was billed for a request"
    assert never_sent.cost_usd == 0.0


async def test_the_summary_block_carries_the_bill_and_says_when_it_was_guessed() -> None:
    """One measured turn and one the API did not count, and the run total says estimated.

    It has to say so on the first turn that guessed rather than on the last: a total a reader
    cannot tell from a measurement is worse than no total, and the console prints `~$` off
    exactly this flag."""
    fake = _fake(
        script=[fake_systemone.turn("report_state", 0.97), fake_systemone.turn("stop", 0.95)],
        usage=fake_systemone.Usage(input_tokens=1200, output_tokens=12),
    )
    stepper, adapter, _m = await _arm(llm=fake)
    obs = await _observation(adapter)
    measured = await stepper.advise(obs, cleared=False, budget=BUDGET)
    assert stepper.summary()["cost_estimated"] is False, "a counted turn claimed to be a guess"
    fake.usage = None  # the second turn comes back with no count at all
    guessed = await stepper.advise(obs, cleared=False, budget=BUDGET)
    await adapter.disconnect()

    block = stepper.summary()
    assert block["usage"]["input_tokens"] == (
        measured.record["usage"]["input_tokens"] + guessed.record["usage"]["input_tokens"]
    )
    assert block["usage"]["output_tokens"] == 12, "the uncounted turn invented an output count"
    assert block["cost_usd"] == round(measured.record["cost_usd"] + guessed.record["cost_usd"], 6)
    assert block["cost_estimated"] is True
    assert block["price"]["source"] == "published"
    assert block["price"]["unit"] == "USD per million tokens"


async def test_the_shadow_record_puts_the_two_bills_for_one_turn_side_by_side() -> None:
    """The number `--decision-mode shadow` exists to produce, and the one
    `docs/guides/decision-llms/README.md` could only reach by arithmetic: what the model charged for
    a turn, beside what the stepper charged for the same reading. Either half can be None, and None
    is not zero: the model's is missing when nobody publishes a rate for it, the stepper's when
    the turn never reached the network."""
    from quackd.agent.providers.base import ToolCall

    advice, stepper, _f = await _advise(
        _fake(
            answers=fake_systemone.turn("report_state", 0.91),
            usage=fake_systemone.Usage(input_tokens=1631, output_tokens=16),
        ),
        mode="shadow",
        cleared=False,
    )
    event = stepper.shadow_event(
        advice,
        ToolCall(name="report_state"),
        {"latency_s": 8.2, "usage": {"input_tokens": 4465}, "cost_usd": 0.0134},
    )
    assert event["llm_cost_usd"] == 0.0134
    assert event["decision_cost_usd"] == advice.record["cost_usd"]
    assert event["decision_cost_usd"] < event["llm_cost_usd"], (
        "the cheap half was not the cheap one"
    )

    unpriced = stepper.shadow_event(advice, ToolCall(name="report_state"), {"cost_usd": None})
    assert unpriced["llm_cost_usd"] is None and unpriced["decision_cost_usd"] is not None


async def test_the_runs_decision_block_is_what_its_own_turns_add_up_to(tmp_path: Any) -> None:
    """End to end, on the arm: the total in `summary.json` is the turns in `transcript.jsonl`.

    They are written by two different pieces of code from two different accumulators, and a
    reader who adds the records up and gets a different number has no way to tell which of
    them is lying, which is the whole value of the record."""
    result, _rec = await _on_run(
        tmp_path,
        _fake(
            answers=fake_systemone.turn("report_state", 0.99),
            usage=fake_systemone.Usage(input_tokens=1200, output_tokens=10),
        ),
    )
    billed = [r for r in _records(result, "decision") if "cost_usd" in r]
    assert billed, "no turn was billed, so there is nothing to add up"
    ended = _records(result, "run_end")
    assert len(ended) == 1
    block = ended[0]["decision"]
    assert block == result.summary["decision"], "the record and the result disagree on the bill"
    assert block["llm"] == "jev", "the summary does not say which decision LLM answered"
    assert block["cost_usd"] == round(sum(r["cost_usd"] for r in billed), 6)
    assert block["usage"]["input_tokens"] == sum(r["usage"]["input_tokens"] for r in billed)
    assert block["cost_estimated"] is False, "a turn the API counted was billed as a guess"


# ── a question the decision LLM declined ────────────────────────────────────────────────


def _answered(**over: Any) -> dict[str, Any]:
    """All four questions answered, off the wire, well enough to take `report_state`: a turn
    every gate after `refused` lets through. `over` replaces whichever answers it names."""
    answers: dict[str, Any] = {
        "next_verb": {"choice": "report_state", "confidence": 0.99},
        "done": {"noul": 0.0},
        "need_human": {"noul": 0.0},
        "feasible": {"choice": "feasible", "confidence": 0.9},
    }
    return {**answers, **over}


class _Scripted(_OffTheWire):
    """`_OffTheWire` answering one scripted payload a turn, and the last one for good."""

    def __init__(self, *payloads: dict[str, Any]) -> None:
        super().__init__(payloads[-1])
        self.script = list(payloads)

    async def decide(self, state: Any, questions: Any) -> dict[str, Any]:
        self.calls.append((dict(state), dict(questions)))
        return self.script.pop(0) if len(self.script) > 1 else self.script[0]


@pytest.mark.parametrize("declined", ["next_verb", "done", "need_human", "feasible"])
async def test_a_declined_question_ends_the_turn_on_its_own_gate_before_anything_is_read(
    declined: str,
) -> None:
    """OpenAI's API may decline any one question rather than answer it, and the question comes
    back answering nothing. Read as anything else, it was misnamed: a missing `next_verb`,
    `done` or `need_human` ended on `unreadable`, which says the backend sent something nobody
    could read, and a missing `feasible` went unnoticed, because nothing acts on it, so the
    stepper took the turn on a state its decision LLM had declined to judge."""
    answers = _answered()
    del answers[declined]
    advice, stepper, _f = await _advise(
        _OffTheWire({"answers": answers, "refused": [declined]}), cleared=True
    )
    assert advice.gate == "refused" and advice.call is None
    assert advice.record["refused"] == [declined]
    assert stepper.refusals == 1 and stepper.errors == 0 and stepper.taken == 0


async def test_a_refusal_hands_the_turn_back_however_good_every_answer_beside_it() -> None:
    """The refusal alone decides it. Every answer on the second turn clears its gate, and the
    record keeps them beside the name that was declined; the run of stepper turns ends there, as
    it does on every handover, and the refusal is read before the repeat it would otherwise be."""
    taken = {"answers": _answered()}
    declined = {"answers": _answered(), "refused": ["done", 3]}
    stepper, adapter, _m = await _arm(llm=_Scripted(taken, declined))
    obs = await _observation(adapter)
    first = await stepper.advise(obs, cleared=True, budget=BUDGET)
    assert first.gate == "taken" and stepper.last_call == "report_state"
    second = await stepper.advise(obs, cleared=True, budget=BUDGET)
    await adapter.disconnect()
    assert second.gate == "refused" and second.call is None
    assert second.record["refused"] == ["done"], "a name that is not a string is not a name"
    assert second.record["choice"] == "report_state" and second.record["done"] == 0.0
    assert stepper.last_call is None and stepper.in_a_row == 0
    block = stepper.summary()
    assert (block["asked"], block["taken"], block["refusals"], block["errors"]) == (2, 1, 1, 0)


@pytest.mark.parametrize("refused", [None, [], (), "done", [3, None]])
async def test_a_refused_that_names_nothing_declines_nothing(refused: Any) -> None:
    """Only a list of names is a refusal, so a missing one, an empty one or one holding no
    string declines nothing and the turn is read as its answers say. Nor is a bare string read
    as a name: iterated, `"done"` is four questions nobody asked."""
    advice, stepper, _f = await _advise(
        _OffTheWire({"answers": _answered(), "refused": refused}), cleared=True
    )
    assert advice.gate == "taken"
    assert "refused" not in advice.record and stepper.refusals == 0


_NO_NEXT_VERB = {k: v for k, v in _answered().items() if k != "next_verb"}
"""All four answered but `next_verb`, so the only thing missing is the choice itself."""


@pytest.mark.parametrize(
    ("payload", "gate", "confidence"),
    [
        ({"answers": _NO_NEXT_VERB}, "unreadable", None),
        ({"answers": _NO_NEXT_VERB, "refused": ["next_verb"]}, "refused", None),
        ({"answers": _answered(next_verb={"confidence": 0.99})}, "unreadable", 0.99),
        (
            {"answers": _answered(next_verb={"choice": None, "confidence": 0.99})},
            "unreadable",
            0.99,
        ),
    ],
    ids=["never answered", "declined", "a confidence and no choice", "a choice of null"],
)
async def test_a_next_verb_nobody_answered_records_no_choice(
    payload: dict[str, Any], gate: str, confidence: float | None
) -> None:
    """`escalate` is where such a turn is routed, and no answer anybody gave: written down as the
    choice, a `next_verb` declined or never answered read as a decision to take the way out, and
    every count of choices and every shadow row took it for one. The record says null, as it does
    for a `done` or a `need_human` nobody answered, keeps a confidence only where one was sent,
    and the shadow row says null beside the gate that explains it, with the questions declined
    where there were any."""
    from quackd.agent.providers.base import ToolCall

    advice, stepper, _f = await _advise(_OffTheWire(payload), mode="shadow", cleared=True)
    assert advice.gate == gate and advice.call is None
    assert advice.record["choice"] is None
    assert advice.record["confidence"] == confidence
    row = stepper.shadow_event(advice, ToolCall(id="x", name="stop", arguments={}))
    assert row["decision_choice"] is None and row["decision_gate"] == gate
    assert row["agree"] is False and row["same_verb"] is False
    assert row.get("refused") == (["next_verb"] if gate == "refused" else None)


async def test_a_shadow_row_names_what_was_declined_only_where_something_was() -> None:
    """The names ride on the shadow row of a turn that ended on `refused`, so a reader tallying
    shadow rows sees which question the vendor would not judge without joining back to the
    `decision` record, and on no other row, so every other row reads as it always did."""
    from quackd.agent.providers.base import ToolCall

    call = ToolCall(id="x", name="report_state", arguments={})
    declined, stepper, _f = await _advise(
        _OffTheWire({"answers": _answered(), "refused": ["need_human"]}),
        mode="shadow",
        cleared=True,
    )
    taken, _s, _f2 = await _advise(
        _OffTheWire({"answers": _answered()}), mode="shadow", cleared=True
    )
    row = stepper.shadow_event(declined, call)
    assert row["refused"] == ["need_human"] and row["decision_gate"] == "refused"
    assert row["decision_choice"] == "report_state", "what was answered is still the choice"
    assert "refused" not in stepper.shadow_event(taken, call)


async def test_the_runs_refusals_are_what_its_own_turns_add_up_to(tmp_path: Any) -> None:
    """End to end, on the arm: a decision LLM that declines every turn leaves the whole run to
    the model, each of its turns is on the record by the name it declined, and the summary
    counts every one of them."""
    result, _rec = await _on_run(
        tmp_path, _OffTheWire({"answers": _answered(), "refused": ["need_human"]})
    )
    decisions = _records(result, "decision")
    assert decisions, "the stepper was never asked, so this proves nothing"
    assert all(r["gate"] == "refused" and r["refused"] == ["need_human"] for r in decisions)
    assert result.summary["decision"]["refusals"] == len(decisions)
    assert not any(r["source"] == "decision" for r in _records(result, "verb_start"))
    assert result.outcome == "success"


# ── what a failed turn records ──────────────────────────────────────────────────────────


SECRET_WORD = "zebra"
"""A word put in the goal and nowhere else, so finding it in a record means the state got there."""

PICTURE = "data:image/jpeg;base64," + base64.b64encode(bytes(range(256)) * 3).decode()
"""A data URL with a base64 run long enough to find: what a reply could quote back from a run
that sent a picture."""

BASE64_RUN = re.compile(r"[A-Za-z0-9+/=]{100,}")


def _leaks(event: dict[str, Any]) -> list[str]:
    """What of a request or a reply reached this record, by name. Nothing, on a good one."""
    blob = json.dumps(event)
    found = []
    if SECRET_WORD in blob:
        found.append("the state")
    if "data:" in blob:
        found.append("a data URL")
    if BASE64_RUN.search(blob):
        found.append("a base64 run")
    return found


class _Rejected(Exception):
    """A 400 the way an SDK raises one: the status on the exception and the reply in the message,
    `Error code: 400 - ` and then the body, as OpenAI's writes it, the body quoting the request
    it turned down and a picture besides."""

    def __init__(self, state: Any) -> None:
        body = {
            "error": {
                "message": f"Invalid 'input': {json.dumps(dict(state))}",
                "type": "invalid_request_error",
                "param": PICTURE,
            }
        }
        super().__init__(f"Error code: 400 - {body}")
        self.status_code = 400
        self.body = body


class _Fails:
    """A decision LLM whose every request fails with what `error(state)` builds."""

    name = JEV.name
    url = None

    def __init__(self, error: Any) -> None:
        self.error = error
        self.model = JEV.model or ""

    async def decide(self, state: Any, questions: Any) -> Any:
        raise self.error(state)


async def test_a_failed_turn_records_the_type_and_the_status_and_never_the_reply() -> None:
    """An SDK puts the server's reply in its exception's message, and a reply can quote the
    request it turned down. The record used to carry the message whole, which put the state, and
    any data URL the reply held, into a transcript that otherwise keeps the state as a size and
    a shape. The type and the status say which failure it was."""
    advice, stepper, _f = await _advise(
        _Fails(_Rejected), cleared=True, goal=f"Say whether the {SECRET_WORD} mug is held"
    )
    assert advice.gate == "error" and advice.call is None
    assert advice.record["error"] == "_Rejected: HTTP 400"
    assert _leaks(advice.event()) == []
    assert stepper.errors == 1


async def test_an_answer_the_router_cannot_read_is_recorded_without_quoting_it() -> None:
    """The router's own failures quote the answer that broke them, `float()` of a probability
    that is not a number for one, and an answer is the other side's text as much as a reply is."""
    broken = _answered(
        next_verb={"choice": "stop", "confidence": 0.99, "probabilities": {"stop": PICTURE}}
    )
    advice, stepper, _f = await _advise(_OffTheWire({"answers": broken}), cleared=True)
    assert advice.gate == "error" and advice.record["error"] == "ValueError"
    assert _leaks(advice.event()) == []
    assert stepper.errors == 1


class _Response:
    def __init__(self, status_code: Any) -> None:
        self.status_code = status_code


class _HTTPStatusError(Exception):
    """httpx's shape: the status is on the response the exception holds."""

    def __init__(self, message: str, status_code: Any) -> None:
        super().__init__(message)
        self.response = _Response(status_code)


class _TypeSafeAPIError(Exception):
    """TypeSafe's shape, which every System One row raises: the status is the exception's own
    `status`, and the message is that status and then the reply's own message
    (`TypeSafeAPIError`, typesafe-sdk 0.7.2)."""

    def __init__(self, message: str, status: Any) -> None:
        super().__init__(message)
        self.status = status


class _StatusThatRaises(Exception):
    """A status read that fails, which must cost the record its status and nothing else."""

    @property
    def status_code(self) -> int:
        raise RuntimeError("the response was closed before it was read")


@pytest.mark.parametrize(
    ("error", "recorded"),
    [
        (_HTTPStatusError(f"Server error for {PICTURE}", 503), "_HTTPStatusError: HTTP 503"),
        (_TypeSafeAPIError(f"429 {PICTURE}", 429), "_TypeSafeAPIError: HTTP 429"),
        # a field by that name which is not a status is not mistaken for one
        (_TypeSafeAPIError(PICTURE, True), "_TypeSafeAPIError"),
        (_TypeSafeAPIError(PICTURE, "429"), "_TypeSafeAPIError"),
        (_TypeSafeAPIError(PICTURE, 42), "_TypeSafeAPIError"),
        (ValueError(f"could not convert string to float: {PICTURE!r}"), "ValueError"),
        (TimeoutError(f"timed out reading {PICTURE}"), "TimeoutError"),
        # and with nothing in it, unless the stepper's own budget is what ran out
        (TimeoutError(), "TimeoutError"),
        (_StatusThatRaises(PICTURE), "_StatusThatRaises"),
    ],
)
def test_a_failure_is_recorded_by_its_type_and_status_alone(
    error: Exception, recorded: str
) -> None:
    """The status is read wherever the client keeps it, OpenAI's on the exception, TypeSafe's
    as `status` and httpx's on the response, and the message is never read, whoever wrote it. A
    field by that name that is not a status is not taken for one, and a read that raises costs
    the record its status and nothing else."""
    from quackd.agent.decision.stepper import _failure

    assert _failure(error) == recorded


async def test_a_turn_that_ran_out_of_time_says_what_it_waited_and_nothing_else(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The one sentence a failed turn keeps is quackd's own: the stepper's timeout gives up with
    a `TimeoutError` that says nothing at all, and the budget is the number a reader would
    change. Shortened here so the test does not wait out the real one."""
    import asyncio

    from quackd.agent.decision import stepper as module

    monkeypatch.setattr(module, "TIMEOUT_S", 0.2)

    class Hangs(_Fails):
        async def decide(self, state: Any, questions: Any) -> Any:
            await asyncio.sleep(30)

    advice, _s, _f = await _advise(Hangs(None), cleared=True)
    assert advice.gate == "error"
    assert advice.record["error"] == "TimeoutError: no answer within 0.2 s"


@pytest.mark.parametrize("how", ["its own clock", "a bare raise"])
async def test_a_backends_own_timeout_is_recorded_by_its_type_alone(how: str) -> None:
    """The sentence is quackd's, and names quackd's budget, so it goes only on a turn that budget
    ended. A backend with a shorter clock of its own raises a `TimeoutError` with nothing in it
    too, and the record said `no answer within 1.0 s` beside a `latency_s` a twentieth of that,
    naming a budget that was not the one that ran out."""

    class GivesUp(_Fails):
        async def decide(self, state: Any, questions: Any) -> Any:
            if how == "a bare raise":
                raise TimeoutError
            async with asyncio.timeout(TIMEOUT_S / 20):
                await asyncio.sleep(30)

    advice, stepper, _f = await _advise(GivesUp(None), cleared=True)
    assert advice.gate == "error" and advice.record["error"] == "TimeoutError"
    assert stepper.errors == 1


def test_quackds_own_words_about_a_reply_are_kept_and_nobody_elses() -> None:
    """A reply quackd could not read is described in quackd's own words, by the type of what came
    back, and those words quote nothing, so the record keeps them: a page that was not JSON and
    an object with no list of answers read apart. They were a bare `ValueError` each, like every
    other message the rule throws away. A plain `ValueError` with the same words is still
    recorded by its type, because only quackd's own exception promises to quote nothing."""
    from quackd.agent.decision.stepper import _failure

    said = "POST /v1/decisions answered str rather than a JSON object"
    assert _failure(UnreadableReply(said)) == f"UnreadableReply: {said}"
    assert _failure(ValueError(said)) == "ValueError"
    assert _failure(UnreadableReply()) == "UnreadableReply", "nothing said, nothing kept"


async def test_a_reply_quackd_could_not_read_is_on_the_record_in_its_words() -> None:
    """Through the stepper, the way a backend raises it."""
    said = "POST /v1/decisions answered without a list of answers (NoneType)"

    class Unreadable(_Fails):
        async def decide(self, state: Any, questions: Any) -> Any:
            raise UnreadableReply(said)

    advice, _s, _f = await _advise(Unreadable(None), cleared=True)
    assert advice.gate == "error" and advice.record["error"] == f"UnreadableReply: {said}"


# ── how long since the last request ─────────────────────────────────────────────────────


class _Clock:
    """`time.perf_counter` for the stepper alone, moved by hand: by a backend for the seconds
    its request takes, and by a test for the seconds between turns."""

    def __init__(self) -> None:
        self.now = 1000.0

    def perf_counter(self) -> float:
        return self.now


class _Takes(_OffTheWire):
    """`_OffTheWire` whose every request takes `seconds` on `clock`, and fails while `fails`."""

    def __init__(self, clock: _Clock, seconds: float, payload: dict[str, Any]) -> None:
        super().__init__(payload)
        self.clock = clock
        self.seconds = seconds
        self.fails = False

    async def decide(self, state: Any, questions: Any) -> dict[str, Any]:
        self.clock.now += self.seconds
        if self.fails:
            raise ConnectionResetError("the server hung up")
        return await super().decide(state, questions)


def _clocked(monkeypatch: pytest.MonkeyPatch) -> _Clock:
    from quackd.agent.decision import stepper as module

    clock = _Clock()
    monkeypatch.setattr(module, "time", SimpleNamespace(perf_counter=clock.perf_counter))
    return clock


async def test_each_request_says_how_long_since_the_last_one_ended(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OpenAI's SDK and TypeSafe's both drop a connection that has been idle five seconds, and in
    shadow mode the model's own call comes between two of the stepper's, so a turn's `latency_s`
    may or may not include opening a new one. `idle_s` tells the two apart: from the end of the
    last request, which is when its connection went idle, to the start of this one, and null on
    the first, which has nothing to reuse."""
    clock = _clocked(monkeypatch)
    stepper, adapter, _m = await _arm(llm=_Takes(clock, 0.25, {"answers": _answered()}))
    obs = await _observation(adapter)
    first = await stepper.advise(obs, cleared=True, budget=BUDGET)
    clock.now += 7.0  # the model's turn, and a verb
    second = await stepper.advise(obs, cleared=True, budget=BUDGET)
    await adapter.disconnect()
    assert first.record["idle_s"] is None and first.record["latency_s"] == 0.25
    assert second.record["idle_s"] == 7.0, "not measured from when the last request ended"


async def test_the_block_gives_the_typical_answer_and_the_slow_one_beside_the_total(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`latency_s` is every request added up, the failed ones with them, because the run waited
    on all of them. A total cannot say whether a decision LLM is quick with one slow answer or
    slow throughout, so the block carries the answers' median, ninetieth percentile and longest,
    by nearest rank. A request that failed is in the total and in no percentile: its seconds are
    how long a failure took. A stepper whose every request failed has no figures at all."""
    clock = _clocked(monkeypatch)
    llm = _Takes(clock, 0.25, {"answers": _answered()})
    stepper, adapter, _m = await _arm(llm=llm)
    obs = await _observation(adapter)
    for seconds in (0.25, 0.25, 0.9):
        llm.seconds = seconds
        await stepper.advise(obs, cleared=True, budget=BUDGET)
    llm.seconds, llm.fails = 5.0, True
    await stepper.advise(obs, cleared=True, budget=BUDGET)
    block = stepper.summary()
    assert block["latency_s"] == pytest.approx(0.25 + 0.25 + 0.9 + 5.0)
    assert (block["latency_p50_s"], block["latency_p90_s"], block["latency_max_s"]) == (
        0.25,
        0.9,
        0.9,
    ), block

    failing = _Takes(clock, 0.4, {"answers": _answered()})
    failing.fails = True
    lost, _adapter, _m = await _arm(llm=failing)
    await lost.advise(obs, cleared=True, budget=BUDGET)
    await adapter.disconnect()
    await _adapter.disconnect()
    assert lost.summary()["latency_s"] == 0.4
    assert not {"latency_p50_s", "latency_p90_s", "latency_max_s"} & set(lost.summary())


async def test_a_failed_request_leaves_no_warm_connection_and_an_unsent_turn_moves_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A request that went out and failed may have taken its connection with it, or never had
    one: one the stepper's clock cut off is closed unread, and one refused never opened. So the
    turn after it says `idle_s` null, as the first turn does, rather than a number that reads as
    a connection kept warm for it, and the turn after that is measured from the request that
    answered. The failed turn keeps the `idle_s` it started with. A turn that never sent
    anything, whether its state was too large or its questions could not be built, made no
    request, carries no `idle_s`, and moves nothing the next one is measured from."""
    clock = _clocked(monkeypatch)
    llm = _Takes(clock, 1.0, {"answers": _answered()})
    stepper, adapter, _m = await _arm(llm=llm)
    obs = await _observation(adapter)
    first = await stepper.advise(obs, cleared=True, budget=BUDGET)
    clock.now += 2.0
    goal, stepper.goal = stepper.goal, "x" * 40_000
    too_large = await stepper.advise(obs, cleared=True, budget=BUDGET)
    stepper.goal = goal
    described = stepper.what.pop("report_state")
    unsent = await stepper.advise(obs, cleared=True, budget=BUDGET)
    stepper.what["report_state"] = described
    clock.now += 3.0
    answered = await stepper.advise(obs, cleared=True, budget=BUDGET)
    clock.now += 4.0
    llm.fails = True
    failed = await stepper.advise(obs, cleared=True, budget=BUDGET)
    llm.fails = False
    clock.now += 0.5
    after = await stepper.advise(obs, cleared=True, budget=BUDGET)
    clock.now += 6.0
    again = await stepper.advise(obs, cleared=True, budget=BUDGET)
    await adapter.disconnect()
    assert first.record["idle_s"] is None
    assert too_large.gate == "state_too_large" and "idle_s" not in too_large.record
    assert unsent.gate == "error" and "idle_s" not in unsent.record
    assert answered.record["idle_s"] == 5.0, "a turn that sent nothing moved the clock"
    assert failed.gate == "error" and failed.record["idle_s"] == 4.0
    assert after.record["idle_s"] is None, "a request that failed was counted as left warm"
    assert again.record["idle_s"] == 6.0, "not measured from the request that answered"


# ── a turn the model takes, and a turn an interrupt cut off ─────────────────────────────


class _Answering(_OffTheWire):
    """Answers each of `choices` in turn, confidently, with every other question answered well
    enough to take it, and fails while `fails` is set, as a server that hung up does."""

    def __init__(self, *choices: str) -> None:
        super().__init__({})
        self.choices = itertools.cycle(choices)
        self.fails = False

    async def decide(self, state: Any, questions: Any) -> dict[str, Any]:
        self.calls.append((dict(state), dict(questions)))
        if self.fails:
            raise ConnectionResetError("the server hung up")
        choice = next(self.choices)
        return {"answers": _answered(next_verb={"choice": choice, "confidence": 0.99})}


@pytest.mark.parametrize("how", ["error", "state_too_large", "not_offered"])
async def test_a_turn_the_model_took_ends_the_steppers_streak_however_it_came_to_it(
    how: str,
) -> None:
    """The repeat gate refuses the call the stepper made last turn, and a turn the model takes is
    a turn the stepper made no call on. A failed call used to be one way back to the model that
    left the stepper's last call standing, so after a `gripper(open=true)` it took and a turn that
    failed, which the model then took, the same confident answer was refused as a repeat of a
    call two turns old. The two turns that never ask, a state too large to send and a body with
    nothing on offer, left it standing the same way."""
    llm = _Answering("gripper(open=true)")
    stepper, adapter, _m = await _arm(llm=llm, allow=["gripper"])
    obs = await _observation(adapter)
    first = await stepper.advise(obs, cleared=True, budget=BUDGET)
    assert first.gate == "taken" and stepper.last_call == "gripper(open=true)"
    if how == "error":
        llm.fails = True
        middle = await stepper.advise(obs, cleared=True, budget=BUDGET)
        llm.fails = False
    elif how == "state_too_large":
        goal, stepper.goal = stepper.goal, "x" * 40_000
        middle = await stepper.advise(obs, cleared=True, budget=BUDGET)
        stepper.goal = goal
    else:
        # before a verdict the gripper is not on offer, and on this allowlist nothing else is
        middle = await stepper.advise(obs, cleared=False, budget=BUDGET)
    assert middle.gate == how and middle.call is None
    assert stepper.last_call is None and stepper.in_a_row == 0
    again = await stepper.advise(obs, cleared=True, budget=BUDGET)
    await adapter.disconnect()
    assert again.gate == "taken", "the call before the model's turn still counted as the last one"


async def test_a_run_of_stepper_turns_broken_by_a_failed_one_counts_again_from_nothing() -> None:
    """The streak limit hands the run back after so many stepper turns in a row, and a failed
    turn breaks the row: the model takes it. On a budget of four the limit is two, so two turns
    taken, one failed and one more taken is a stepper that has taken one turn running, not
    three, and the fourth is not a handover."""
    llm = _Answering("gripper(open=true)", "gripper(open=false)")
    stepper, adapter, _m = await _arm(llm=llm, allow=["gripper"])
    stepper.max_steps = 4
    assert stepper.streak_limit == 2, "this test is about a limit of two"
    obs = await _observation(adapter)
    gates = []
    for fails in (False, False, True, False):
        llm.fails = fails
        gates.append((await stepper.advise(obs, cleared=True, budget=BUDGET)).gate)
    await adapter.disconnect()
    assert gates == ["taken", "taken", "error", "taken"]
    assert stepper.in_a_row == 1


class _Interrupted(_OffTheWire):
    """A decision LLM whose request is out when the run is interrupted. It answers its first
    `answers` requests, and on the next one it is handed the questions and then either a second
    Ctrl-C lands (`KeyboardInterrupt`, raised where it is, as the default handler raises it) or
    the flock cancels the member, and it waits until it is cancelled."""

    def __init__(self, press: type[BaseException], answers: int = 0) -> None:
        super().__init__({"answers": _answered()})
        self.press = press
        self.answers = answers
        self.handed = asyncio.Event()

    async def decide(self, state: Any, questions: Any) -> dict[str, Any]:
        self.calls.append((dict(state), dict(questions)))
        if self.answers:
            self.answers -= 1
            return self.payload
        self.handed.set()
        if self.press is KeyboardInterrupt:
            raise KeyboardInterrupt
        await asyncio.sleep(30)
        raise AssertionError("never cancelled")


async def _interrupt(press: type[BaseException], llm: _Interrupted, asking: Any) -> None:
    """Await `asking` until `press` ends it. A Ctrl-C is awaited in this task, because one raised
    in a task of its own leaves through the event loop and ends the test session; a cancel is a
    real one, of the task the request is out in, as a flock cancels a member."""
    if press is KeyboardInterrupt:
        with pytest.raises(KeyboardInterrupt):
            await asking
        return
    task = asyncio.ensure_future(asking)
    await asyncio.wait_for(llm.handed.wait(), 30)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.parametrize("press", [KeyboardInterrupt, asyncio.CancelledError])
async def test_a_turn_an_interrupt_cut_off_is_counted_billed_and_kept_for_the_record(
    press: type[BaseException],
) -> None:
    """A second Ctrl-C or a flock's cancel is not an `Exception`, so it went past every handler a
    failed call has, and the request it cut off, already handed over, was neither counted, timed
    nor billed, and left nothing to write down. It is counted and billed now, as a failed one
    is, on a gate of its own because nothing failed and nobody takes the turn, and not among the
    errors. The record waits on `cut_off` for the loop, the interrupt goes on as it came, and the
    request after it claims no connection kept warm."""
    llm = _Interrupted(press, answers=1)
    stepper, adapter, _m = await _arm(llm=llm)
    obs = await _observation(adapter)
    first = await stepper.advise(obs, cleared=True, budget=BUDGET)
    assert first.gate == "taken" and stepper.cut_off is None
    await _interrupt(press, llm, stepper.advise(obs, cleared=True, budget=BUDGET))
    await adapter.disconnect()
    cut = stepper.cut_off
    assert cut is not None and cut.call is None
    assert cut.gate == "interrupted" and cut.record["error"] == press.__name__
    assert isinstance(cut.record["idle_s"], float) and cut.record["latency_s"] >= 0
    assert cut.record["usage_estimated"] is True and cut.record["cost_usd"] > 0
    block = stepper.summary()
    assert (block["asked"], block["taken"], block["errors"]) == (2, 1, 0)
    assert block["cost_usd"] == round(first.record["cost_usd"] + cut.record["cost_usd"], 6)
    assert stepper.last_request_ended is None


def _the_one_run(runs: Any) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """The summary and the transcript of the one run under `runs`, for a run that raised rather
    than returning a result that names its directory."""
    from quackd.agent.transcript import Transcript

    (written,) = pathlib.Path(runs).rglob("summary.json")
    summary = json.loads(written.read_text(encoding="utf-8"))
    return summary, Transcript.read(written.parent / "transcript.jsonl")


@pytest.mark.parametrize("press", [KeyboardInterrupt, asyncio.CancelledError])
async def test_a_run_interrupted_while_the_stepper_waits_has_that_turn_on_the_record(
    tmp_path: Any, press: type[BaseException]
) -> None:
    """End to end, on the arm. The loop wrote the `decision` record only once `advise` returned,
    so a run a person ended, or a flock cancelled, while the stepper's request was out left no
    record of that request, and its summary, written on the way out, counted none either. Now
    there is one record, on `interrupted`, and the summary's decision block is that turn."""
    llm = _Interrupted(press)
    await _interrupt(press, llm, _on_run(tmp_path, llm))
    summary, events = _the_one_run(tmp_path)
    decisions = [e for e in events if e["kind"] == "decision"]
    assert len(decisions) == 1, decisions
    (turn,) = decisions
    assert turn["gate"] == "interrupted" and turn["error"] == press.__name__
    block = summary["decision"]
    assert (block["asked"], block["errors"]) == (1, 0)
    assert block["cost_usd"] == round(turn["cost_usd"], 6) and block["cost_usd"] > 0
    assert block["latency_s"] == round(turn["latency_s"], 3)
    assert summary["outcome"] == "aborted"


async def test_a_press_while_the_decision_llm_is_thinking_ends_the_run_without_waiting(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The first Ctrl-C reaches the stepper's request as it reaches the model's: raced against
    the abort, so a decision LLM that has not answered is not waited for, the turn goes on the
    record as cut off, and the run ends `aborted`. The stepper's own timeout is raised here, so
    nothing but the stop could end the request quickly."""
    from quackd.agent.decision import stepper as module
    from quackd.agent.loop import AgentLoop, RunConfig
    from quackd.agent.providers.fake import FakeProvider
    from quackd_lerobot import LeRobotAdapter
    from quackd_lerobot.mock import LeRobotMock

    monkeypatch.setattr(module, "TIMEOUT_S", 30.0)
    asked = asyncio.Event()

    class Hangs(_Fails):
        async def decide(self, state: Any, questions: Any) -> Any:
            asked.set()
            await asyncio.sleep(30)

    duck = _lookout()
    loop = AgentLoop(
        RunConfig(
            duck=duck,
            provider=FakeProvider.for_duck(duck.name),
            transport=LeRobotAdapter(LeRobotMock()),
            runs_dir=tmp_path,
            decision="on",
            decision_llm=Hangs(None),
            decision_price=resolve_decision_price(JEV),
        )
    )

    async def press() -> None:
        await asked.wait()
        loop.executor.abort.set()  # the kill switch's first press

    clock = asyncio.get_running_loop()
    pressing = asyncio.ensure_future(press())
    started = clock.time()
    result = await loop.run()
    await asyncio.wait_for(pressing, timeout=5)
    assert clock.time() - started < 10, "the run waited for the decision LLM"
    assert result.outcome == "aborted" and result.reason == "kill switch", result.reason
    decided = _records(result, "decision")
    assert decided and decided[-1].get("gate") == "interrupted", decided[-1:]
