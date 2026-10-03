"""The feasibility verdict: the vocabulary it is said in, and the gate that waits for it."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from quackd.adapters.factory import ADAPTER_NAMES, BACKENDS, RobotSpec, describe
from quackd.adapters.manifest import Datasheet, Figure, RobotManifest, Span, VerbSpec
from quackd.agent.prompts import ASSESS_TASK, DECLARE_NAMES, META_TOOL_NAMES
from quackd.safety import Executor, VerdictRequired
from quackd.transport.mock import MockTransport
from quackd.verbs.aliases import ALIASES, canonical
from quackd.verbs.registry import Verb, default_registry
from quackd.verdict import (
    BEFORE_VERDICT,
    MOVES_THE_BODY,
    NEEDS_NUMBERS,
    NEEDS_WORDS,
    Verdict,
    check_needs,
    datasheet_value,
    missing_needs,
    missing_needs_in,
    needs_properties,
    own_sheet_objection,
    solo_hint,
)
from quackd_microduck import microduck_manifest


def _body(**over: object) -> RobotManifest:
    base: dict[str, object] = {
        "id": "bot-01",
        "vendor": "acme",
        "model": "bot",
        "embodiment": "wheeled",
        "mobility": "wheeled",
        "intents": ["twist"],
        "verbs": [VerbSpec(name="move", core=True)],
    }
    base.update(over)
    return RobotManifest(**base)  # type: ignore[arg-type]


def _still(**over: object) -> RobotManifest:
    """A body that does not move: an arm on a table, with only the brake as a core verb."""
    arm: dict[str, object] = {
        "embodiment": "arm",
        "mobility": "none",
        "intents": ["joint"],
        "verbs": [VerbSpec(name="stop", core=True)],
    }
    return _body(**{**arm, **over})


# ── the vocabulary ──────────────────────────────────────────────────────────────────────


def test_the_tool_and_the_model_describe_the_same_verdict() -> None:
    schema = ASSESS_TASK["input_schema"]
    assert set(schema["properties"]) == set(Verdict.model_fields) - {"human"}
    assert schema["required"] == ["verdict", "reason"]
    assert ASSESS_TASK["name"] in META_TOOL_NAMES
    assert ASSESS_TASK["name"] not in DECLARE_NAMES, "a verdict is a gate, not an ending"
    assert set(schema["properties"]["needs"]["properties"]) == {*NEEDS_NUMBERS, *NEEDS_WORDS}


def test_the_two_vocabularies_of_one_tool_agree_on_a_duration() -> None:
    """`needs` speaks `endurance_min` and `estimates` could not say a duration at all, so a
    pilot could demand endurance and not estimate it. The model measured in #24 tried
    `quantity: "endurance_min"`, got a validation refusal, and spent an LLM call on it twice
    out of six. The enum is written by hand in two files, which is how they drifted, so this
    holds them to each other."""
    from typing import get_args

    from quackd.verdict import Estimate

    items = ASSESS_TASK["input_schema"]["properties"]["estimates"]["items"]  # type: ignore[index]
    quantities = items["properties"]["quantity"]["enum"]
    assert quantities == list(get_args(Estimate.model_fields["quantity"].annotation))
    assert "duration_min" in quantities
    assert any(key.endswith("_min") for key in NEEDS_NUMBERS), "the need that asked for one"


def test_the_needs_vocabulary_is_the_datasheets_own() -> None:
    fields = set(Datasheet.model_fields)
    for key in NEEDS_NUMBERS:
        assert key in fields or key == "work_height_m", key
    assert set(NEEDS_WORDS) <= fields | {"mobility"}


def test_every_need_says_what_it_means() -> None:
    """The enums went out bare, so a pilot saw `legged, wheeled, any` and nothing saying that
    `any` is refused by a body that stays put or how to say "goes nowhere", and
    `work_height_m` was described as "At least this much", which is the one thing the checker
    does not read it as. The schema is the only place a pilot learns the words, so each one
    says what it means, in the checker's own terms."""
    props = needs_properties()
    assert set(props) == {*NEEDS_NUMBERS, *NEEDS_WORDS}
    for key, spec in props.items():
        assert spec.get("description"), f"{key} goes out without saying what it means"
    for key in ("mobility", "manipulator"):
        assert props[key]["enum"] == list(NEEDS_WORDS[key])
        assert {"none", "any"} <= set(props[key]["enum"]), key
        assert "none:" in props[key]["description"] and "any:" in props[key]["description"]
    work = props["work_height_m"]["description"]
    assert "Not a minimum" in work and "at least" not in work.lower()
    assert "give 0" in work, "the zero rule reaches the working height, and the text says so"
    assert "does not move meets indoor_flat" in props["terrain"]["description"]
    # and the tool text agrees that a 0 or a none asks for nothing
    needs = str(ASSESS_TASK["input_schema"]["properties"]["needs"]["description"])  # type: ignore[index]
    assert "Name only what the task turns on" in needs
    assert "give 0 or none" in needs and "asks for nothing" in needs


def test_the_prompt_says_the_same_thing_about_an_unseen_target_everywhere() -> None:
    """#25 corrected what `uncertain` is for, and the correction has to hold wherever the
    pilot reads it or it is pulled two ways. An audit of the first attempt found exactly
    that: the Rules line, labelled enforced and not optional, said flatly that an unfound
    target is no reason for `uncertain`, while the tool description beside it kept
    `uncertain` for a limit that turns on an unseen thing's mass. For "pick up the box",
    with the box out of frame, the two gave opposite answers and the more authoritative one
    was wrong.

    Each surface carries both halves now: not by itself a reason, and a reason when a limit
    turns on the thing nobody has seen. The MCP twin is held to the same words in
    `tests/test_mcp_server.py`, where there is a client to ask."""
    from pathlib import Path as _Path

    from quackd.agent.prompts import build_system_prompt
    from quackd.duckfile.parser import load_duck
    from quackd.verbs.registry import default_registry

    registry = default_registry()
    duck = load_duck("ducks/find-and-kick.duck")
    allow = [n for n in duck.frontmatter.verbs.allow if n in registry]
    prompt = build_system_prompt(duck, [registry.view(n) for n in allow], "sim2d")
    rule = next(line for line in prompt.splitlines() if "Until then" in line)
    repo = _Path(__file__).resolve().parents[1]
    surfaces = {
        "assess_task": str(ASSESS_TASK["description"]),
        "the rule line": rule,
        "docs/concepts/safety.md": (repo / "docs" / "concepts" / "safety.md").read_text(
            encoding="utf-8"
        ),
    }
    for where, text in surfaces.items():
        assert "not by itself" in text, f"{where} states the rule absolutely"
        assert "mass or size" in text, f"{where} drops the figure that does decide a limit"


def test_a_need_outside_the_vocabulary_is_refused() -> None:
    assert check_needs({"payload_kg": 3}) == {"payload_kg": 3.0}
    assert check_needs({"manipulator": "gripper"}) == {"manipulator": "gripper"}
    with pytest.raises(ValueError, match="not in the datasheet vocabulary"):
        check_needs({"strength": 3})
    with pytest.raises(ValueError, match="must be a number"):
        check_needs({"payload_kg": "heavy"})
    with pytest.raises(ValueError, match="must be 0 or more"):
        check_needs({"payload_kg": -1})
    with pytest.raises(ValueError, match="must be one of"):
        check_needs({"manipulator": "claw"})
    with pytest.raises(ValueError, match="must be a number"):
        check_needs({"arms": True})


def test_a_verdict_validates_its_own_needs() -> None:
    with pytest.raises(ValidationError, match="not in the datasheet vocabulary"):
        Verdict(verdict="infeasible", reason="too heavy", needs={"oomph": 2})
    with pytest.raises(ValidationError, match="at least 1 character"):
        Verdict(verdict="feasible", reason="")
    verdict = Verdict(verdict="uncertain", reason="cannot see it")
    assert not verdict.go
    verdict.human = "go"
    assert verdict.go
    verdict.human = "no_go"
    assert not verdict.go
    assert "said no" in verdict.blocking_reason()


# ── which verbs wait ────────────────────────────────────────────────────────────────────


def test_every_shipped_verb_was_classified_on_purpose() -> None:
    """A verb in neither set is a verb nobody decided about, and the gate would refuse it
    for ever without anybody noticing."""
    from quackd.adapters.factory import _module

    shipped = {canonical(v.name) for v in default_registry().verbs()}
    for adapter in ADAPTER_NAMES:
        shipped |= {canonical(name) for name in _module(adapter).implementations()}
    shipped |= {canonical(alias) for alias in ALIASES}

    assert not BEFORE_VERDICT & MOVES_THE_BODY, "a verb cannot be both"
    unclassified = shipped - BEFORE_VERDICT - MOVES_THE_BODY
    assert not unclassified, f"classify these, one way or the other: {sorted(unclassified)}"
    stale = (BEFORE_VERDICT | MOVES_THE_BODY) - shipped
    assert not stale, f"no shipped robot has these any more: {sorted(stale)}"
    assert "stop" in BEFORE_VERDICT, "the brake can never wait for a verdict"


def _shipped_verbs() -> list[Verb]:
    """Every `Verb` a shipped body can register: the default vocabulary, plus each official
    adapter's own implementations. `registry_from_manifest` copies `read_only` off these
    templates untouched, so what they say here is what the gate sees at run time."""
    from quackd.adapters.factory import _module

    verbs = list(default_registry().verbs())
    for adapter in ADAPTER_NAMES:
        verbs += list(_module(adapter).implementations().values())
    return verbs


def test_a_shipped_verb_that_only_reads_is_already_one_that_runs_first() -> None:
    """The gate reads `Verb.read_only` beside `BEFORE_VERDICT` (#26), and for a body quackd
    never shipped that is the whole point: it is the only way a stranger's `locate` can look
    before the pilot judges. For a body quackd does ship, the flag must restate the set and
    never widen it. The test above reads the two sets and cannot see the flag, so a shipped
    verb flagged read-only and filed under `MOVES_THE_BODY` would run before any verdict and
    nothing would say so."""
    flagged = {canonical(v.name) for v in _shipped_verbs() if v.read_only}
    assert {"observe", "report_state", "introspect"} <= flagged, (
        "the verbs that only read stopped saying so: the flag itself went missing"
    )
    widened = flagged - BEFORE_VERDICT
    assert not widened, (
        "these ship as read-only, so the gate lets them run before the verdict, and the set "
        f"that is supposed to be the record of that does not name them: {sorted(widened)}"
    )
    assert not flagged & MOVES_THE_BODY, "a verb cannot both move the body and only read"


# ── the gate ────────────────────────────────────────────────────────────────────────────


def _executor(
    registry: object | None = None, **over: object
) -> tuple[Executor, MockTransport, list[dict[str, object]]]:
    transport = MockTransport()
    events: list[dict[str, object]] = []

    class _EventLog:
        def emit(self, kind: str, **data: object) -> None:
            events.append({"kind": kind, **data})

    executor = Executor(
        registry=registry if registry is not None else default_registry(),  # type: ignore[arg-type]
        transport=transport,
        manifest=microduck_manifest("mock"),
        event_log=_EventLog(),  # type: ignore[arg-type]
        **over,  # type: ignore[arg-type]
    )
    return executor, transport, events


async def test_nothing_moves_until_a_verdict_clears_it() -> None:
    executor, transport, events = _executor(require_verdict=True)
    with pytest.raises(VerdictRequired, match="moves the body"):
        await executor.run_verb("walk", {"vx": 0.1, "duration_s": 0.1})
    gate = next(e for e in events if e["kind"] == "gate")
    assert gate["gate"] == "verdict" and gate["outcome"] == "refused"
    assert transport.intents_of("move") == []

    # looking and speaking are how a pilot works out what it has been asked to do
    for verb in ("quack", "get_frame", "report_state", "stop"):
        assert (await executor.run_verb(verb)).ok, verb

    executor.verdict = Verdict(verdict="feasible", reason="light enough")
    assert (await executor.run_verb("walk", {"vx": 0.1, "duration_s": 0.1})).ok
    assert transport.intents_of("move")


async def test_a_bodys_own_read_only_verb_looks_before_the_verdict() -> None:
    """`BEFORE_VERDICT` knows the verbs quackd ships. A body quackd never shipped brings its
    own sensing verb, and the pilot needs it for the very judgement the gate is waiting for:
    a `locate` that says where the thing is cannot be refused as "moves the body"."""
    from quackd.verbs.registry import NoParams, Verb, VerbResult

    async def looks(_ctx: object, _p: object) -> VerbResult:
        return VerbResult.success("the ball is 0.3 m ahead")

    async def moves(_ctx: object, _p: object) -> VerbResult:
        return VerbResult.success("reached")

    registry = default_registry()
    registry.register(Verb("locate", "where a thing is", looks, NoParams, read_only=True))
    registry.register(Verb("reach", "move a hand to it", moves, NoParams))
    executor, _transport, events = _executor(require_verdict=True, registry=registry)

    assert (await executor.run_verb("locate")).ok
    assert not [e for e in events if e["kind"] == "gate" and e["gate"] == "verdict"]
    with pytest.raises(VerdictRequired, match="reach moves the body"):
        await executor.run_verb("reach")

    # a learned verb never carries the flag: unproven, it waits like everything else
    from quackd.verbs.learned import LearnedVerbSpec, register_learned_verb

    register_learned_verb(
        registry,
        LearnedVerbSpec(name="wave", description="a policy", policy_path="wave.onnx"),
    )
    assert not registry.get("wave").read_only
    with pytest.raises(VerdictRequired, match="wave moves the body"):
        await executor.run_verb("wave")


async def test_a_learned_verb_cannot_take_a_name_that_runs_first() -> None:
    """`BEFORE_VERDICT` is matched by name, so a policy named `observe` would have run before
    any verdict. A learned verb is an unproven policy by definition, which is why
    `register_learned_verb` marks it `confirm`, and the docstring on that set promises it waits
    until somebody classifies it on purpose. Only the confirm gate was keeping that promise,
    and `--yes` answers the confirm gate.

    The name is free to take because a body with no camera has no `observe` of its own, so the
    registry accepts it: the arm is such a body."""
    from quackd.verbs.learned import LearnedVerbSpec, register_learned_verb

    registry = default_registry()
    registry._verbs.pop("observe")  # a body with no camera verb, as an arm is
    learned = register_learned_verb(
        registry, LearnedVerbSpec(name="observe", description="a policy", policy_path="p.onnx")
    )
    assert learned.kind == "learned" and not learned.read_only
    executor, _transport, _events = _executor(require_verdict=True, registry=registry)
    with pytest.raises(VerdictRequired, match="observe moves the body"):
        await executor.run_verb("observe")

    # and the prompt does not offer it either, by the same rule
    from quackd.agent.prompts import before_verdict_clause

    assert before_verdict_clause([learned]) == "only `stop` runs"


async def test_quackds_own_record_beats_a_strangers_read_only_flag() -> None:
    """#26 lets a verb's own `read_only` open the gate, which is the only way a body quackd
    never shipped can look before it judges. It must not override the other half of quackd's
    own record: a verb arriving under a name this repository has classified as motion, and
    carrying `read_only`, is saying two contradictory things about itself, and the one to
    believe is the name."""
    from quackd.verbs.registry import NoParams, Verb, VerbResult

    async def looks(_ctx: object, _p: object) -> VerbResult:
        return VerbResult.success("sent nothing, honestly")

    registry = default_registry()
    registry.register(
        Verb("kick", "a kick that claims to only look", looks, NoParams, read_only=True),
        replace=True,
    )
    assert "kick" in MOVES_THE_BODY
    executor, _transport, _events = _executor(require_verdict=True, registry=registry)
    with pytest.raises(VerdictRequired, match="kick moves the body"):
        await executor.run_verb("kick")


async def test_an_unanswered_doubt_does_not_clear_the_gate() -> None:
    executor, _transport, _events = _executor(require_verdict=True)
    executor.verdict = Verdict(verdict="uncertain", reason="the basket is out of frame")
    with pytest.raises(VerdictRequired, match="nobody has cleared it"):
        await executor.run_verb("walk", {"vx": 0.1, "duration_s": 0.1})

    executor.verdict = Verdict(verdict="uncertain", reason="out of frame", human="no_go")
    with pytest.raises(VerdictRequired, match="said no"):
        await executor.run_verb("walk", {"vx": 0.1, "duration_s": 0.1})

    executor.verdict = Verdict(verdict="infeasible", reason="3 kg of clothes")
    with pytest.raises(VerdictRequired, match="judged infeasible"):
        await executor.run_verb("walk", {"vx": 0.1, "duration_s": 0.1})

    executor.verdict = Verdict(verdict="uncertain", reason="out of frame", human="go")
    assert (await executor.run_verb("walk", {"vx": 0.1, "duration_s": 0.1})).ok


async def test_a_flock_member_is_never_asked_for_a_verdict() -> None:
    """A member is a state machine, not a pilot: there is nobody there to ask, so the gate is
    off by default and the flock never turns it on."""
    import inspect

    from quackd.flock import member as flock_member

    executor, _transport, _events = _executor()
    assert executor.require_verdict is False
    assert (await executor.run_verb("walk", {"vx": 0.1, "duration_s": 0.1})).ok
    assert "require_verdict" not in inspect.getsource(flock_member)


# ── the matcher ─────────────────────────────────────────────────────────────────────────


def test_a_figure_nobody_published_is_not_a_yes() -> None:
    duck = describe(RobotSpec("microduck", "sim2d"))
    assert missing_needs({"payload_kg": 0.1}, duck) == ["payload_kg >= 0.1 (not published)"]
    assert missing_needs({"manipulator": "gripper"}, duck) == ["manipulator = gripper (has beak)"]

    cart = describe(RobotSpec("alohamini", "mock"))
    assert missing_needs({"payload_kg": 1.0, "reach_m": 0.5, "mobility": "wheeled"}, cart) == []
    assert missing_needs({"payload_kg": 1.1}, cart) == ["payload_kg >= 1.1 (has 1)"]

    arm = describe(RobotSpec("lerobot", "mock"))
    assert missing_needs({"mobility": "any"}, arm) == ["mobility = any (has none)"]
    assert missing_needs({"endurance_min": 90}, arm) == [], "mains powered: nothing runs down"

    toddler = describe(RobotSpec("toddlerbot", "mock"))
    assert missing_needs({"payload_kg": 1.4}, toddler) == []
    assert missing_needs({"payload_kg": 1.5}, toddler) == ["payload_kg >= 1.5 (has 1.484)"]


def test_a_working_height_is_a_band_not_a_maximum() -> None:
    cart = describe(RobotSpec("xlerobot", "mock"))
    assert missing_needs({"work_height_m": 0.9}, cart) == []
    assert missing_needs({"work_height_m": 0.2}, cart) == [
        "work_height_m = 0.2 (reaches 0.5 to 1.25 m)"
    ]
    arm = describe(RobotSpec("lerobot", "mock"))
    assert missing_needs({"work_height_m": 0.9}, arm) == ["work_height_m = 0.9 (not published)"]


def test_terrain_is_met_by_a_body_rated_for_more() -> None:
    rugged = _body(datasheet=Datasheet(manipulator="none", terrain="outdoor"))
    assert missing_needs({"terrain": "indoor_flat"}, rugged) == []
    indoor = _body(datasheet=Datasheet(manipulator="none", terrain="indoor_flat"))
    assert missing_needs({"terrain": "outdoor"}, indoor) == [
        "terrain = outdoor (rated indoor_flat)"
    ]
    silent = _body(datasheet=Datasheet(manipulator="none"))
    assert missing_needs({"terrain": "indoor"}, silent) == ["terrain = indoor (not published)"]
    assert missing_needs({"terrain": "outdoor"}, silent) == ["terrain = outdoor (not published)"]
    # the one exception, and it is the prompt's doing: a body whose terrain nobody published is
    # told "assume a flat indoor floor and decline anything else", so a pilot asking for
    # exactly that has done as it was told. Refusing it would refuse the honest answer, and
    # since #24 this reader can refuse a verdict rather than only rank a body.
    assert missing_needs({"terrain": "indoor_flat"}, silent) == []


def test_a_zero_asks_for_nothing_a_working_height_included() -> None:
    """`needs` is filled in even when the verdict is feasible, because a matcher reads it, so
    `payload_kg: 0` is the natural way to say the task carries nothing. Against a body that
    published no payload that read as "0 kg needed, and nobody said this body has any", which
    refused a pilot for answering fully.

    `work_height_m` was left out of that rule and read as "the ground", while the tool told the
    pilot in so many words that a 0 asks for nothing and is always met. So a pilot that did as
    it was told was refused on every body quackd ships: "(not published)" on those with no
    working height band, and below the band on the one that has one. The 2026-09-23 bench runs
    on an arm met that refusal as the y/N question on nearly every run. Every number now reads
    a zero the same way, band or no band, and a task whose hands really work at the floor says
    so with a height above zero."""
    duck = describe(RobotSpec("microduck", "sim2d"))
    nothing = dict.fromkeys(NEEDS_NUMBERS, 0)
    assert missing_needs(nothing, duck) == []
    assert own_sheet_objection(nothing, duck) is None
    assert missing_needs({"payload_kg": 0.1}, duck) == ["payload_kg >= 0.1 (not published)"]
    assert missing_needs({"work_height_m": 0}, duck) == []
    assert missing_needs({"work_height_m": 0.1}, duck) == ["work_height_m = 0.1 (not published)"]
    assert own_sheet_objection({"payload_kg": 0}, duck) is None

    for low, high in ((0.2, 0.6), (0.45, 1.1), (0.05, 0.3)):
        band = Span(low=low, high=high, confidence="official", source="the docs")
        banded = _body(datasheet=Datasheet(manipulator="gripper", arms=1, workspace_height_m=band))
        assert missing_needs({"work_height_m": 0}, banded) == [], "zero is no height at all"
        under = low / 2
        assert missing_needs({"work_height_m": under}, banded) == [
            f"work_height_m = {under:g} (reaches {low:g} to {high:g} m)"
        ], "a height above zero is still a point that has to be inside the band"


def test_the_objection_names_the_need_and_the_three_ways_out() -> None:
    """One sentence for the loop and for the MCP session, so a pilot hears the same words
    wherever it is driving from. It offers `uncertain` beside `infeasible` because the pilot
    measured on Qwen3-32B answered `uncertain` to a refusal that named only `infeasible`, and
    for a figure nobody published that is the right destination: it asks a person."""
    duck = describe(RobotSpec("microduck", "sim2d"))
    said = own_sheet_objection({"endurance_min": 45}, duck)
    assert said is not None
    assert said.startswith("this body does not meet what you said the task needs")
    assert "endurance_min >= 45 (not published)" in said
    for way in (
        "infeasible if that need decides the task",
        "feasible with the need corrected",
        "uncertain to put it to a person",
        "task file's own datasheet block",
        "nothing moves until you answer again",
    ):
        assert way in said, way
    assert "robot_assess_task" in (
        own_sheet_objection({"payload_kg": 3.0}, duck, tool="robot_assess_task") or ""
    )

    # the sheet agreeing with itself: legged, and rated for the floor it is asked to walk on
    assert own_sheet_objection({"mobility": "legged", "terrain": "indoor_flat"}, duck) is None
    assert own_sheet_objection({}, duck) is None, "a verdict that named no need has nothing to fail"
    assert own_sheet_objection({"payload_kg": 3.0}, None) is None, "no sheet, no objection"


def test_a_need_that_is_not_a_number_falls_through_to_the_refusal() -> None:
    """This reader is handed a raw dict off the wire before anything validates it:
    `robot_assess_task` computes `could` from the tool's own argument, and catches `ValueError`
    only. The zero guard called `float()` on whatever arrived, so a JSON null raised
    `TypeError` out of the MCP tool where before it read as a need nobody published.

    The same `float()` sat unguarded in three more places, reached only when the body DOES
    publish the figure: the band, the arm count and every other number. The duck publishes
    none of them, which is how the first fix passed its own test. So a null asked of a body
    with a payload, a band or arms raised just the same."""
    duck = describe(RobotSpec("microduck", "sim2d"))
    assert missing_needs({"payload_kg": None}, duck) == ["payload_kg >= None (not published)"]
    assert missing_needs({"payload_kg": "heavy"}, duck) == ["payload_kg >= heavy (not published)"]
    assert missing_needs({"payload_kg": True}, duck) == ["payload_kg >= True (not published)"]
    assert missing_needs({"payload_kg": 0}, duck) == [], "a real zero still asks for nothing"

    band = Span(low=0.3, high=0.8, confidence="official", source="the docs")
    sheet = Datasheet(
        manipulator="gripper",
        arms=2,
        payload_kg=Figure(value=1.5, confidence="official", source="the docs"),
        workspace_height_m=band,
    )
    published = _body(datasheet=sheet)
    for odd in (None, "heavy", True):
        assert missing_needs({"payload_kg": odd}, published) == [f"payload_kg >= {odd} (has 1.5)"]
        assert missing_needs({"arms": odd}, published) == [f"arms >= {odd} (has 2)"]
        assert missing_needs({"work_height_m": odd}, published) == [
            f"work_height_m = {odd} (reaches 0.3 to 0.8 m)"
        ]
        # and a pilot's own sheet excuses a missing band for a height, never for a non-number
        assert missing_needs({"work_height_m": odd}, duck, own_sheet=True) == [
            f"work_height_m = {odd} (not published)"
        ]


def test_an_unpublished_terrain_is_only_the_floor_where_the_prompt_says_so() -> None:
    """The exception exists because the prompt tells a body whose terrain nobody published to
    assume a flat indoor floor, so the two readers must agree. That sentence is only rendered
    for a body that moves and that has a datasheet at all: `body_lines` tells a body with no
    sheet to treat every limit as not published. So the exception stops where the promise
    does, which also keeps a bid that carried no datasheet from winning a role on it.

    A body that does not move is shown "it does not move" where a moving body is shown its
    terrain, and was refused "(not published)" for a flat indoor floor, a figure it was never
    shown and that means nothing for a body standing on a table. It now meets `indoor_flat`,
    and anything above it is refused in the words it was shown: "(it does not move)"."""
    silent = _body(datasheet=Datasheet(manipulator="none"))
    assert missing_needs({"terrain": "indoor_flat"}, silent) == []
    assert missing_needs_in({"terrain": "indoor_flat"}, {}, "wheeled") == [
        "terrain = indoor_flat (not published)"
    ], "a bid with no datasheet said nothing, and nothing is not a flat indoor floor"
    assert missing_needs_in({"terrain": "indoor_flat"}, {"terrain": None}, None) == [
        "terrain = indoor_flat (not published)"
    ], "a bid that did not say whether it moves was told nothing either"

    still = _still(datasheet=Datasheet(manipulator="gripper", arms=1))
    assert missing_needs({"terrain": "indoor_flat"}, still) == []
    assert own_sheet_objection({"terrain": "indoor_flat"}, still) is None
    for above in ("indoor", "outdoor"):
        assert missing_needs({"terrain": above}, still) == [f"terrain = {above} (it does not move)"]
    assert missing_needs_in({"terrain": "indoor_flat"}, {}, "none") == [
        "terrain = indoor_flat (not published)"
    ], "with no datasheet the prompt says treat terrain as not published, and so does this"
    # a rating it did publish still decides, moving or not
    rated = _still(datasheet=Datasheet(manipulator="none", terrain="indoor"))
    assert missing_needs({"terrain": "indoor"}, rated) == []
    assert missing_needs({"terrain": "outdoor"}, rated) == ["terrain = outdoor (rated indoor)"]


def test_none_asks_for_no_locomotion_and_nothing_held() -> None:
    """`any` means some kind, which a body bolted to a table does not have, and the vocabulary
    had no word for "this task goes nowhere": omitting the key was the only way to say it, and
    nothing told the pilot so. A pilot asked to fill `needs` in for every verdict reached for
    the word it had and was refused by its own sheet. `none` is now a word, and it asks for
    nothing, so it is met by a body with the thing as well as by one without."""
    for mobility in ("none", "legged", "wheeled"):
        for hands in ("none", "beak", "gripper"):
            arms = 1 if hands == "gripper" else 0
            sheet = Datasheet(manipulator=hands, arms=arms)
            body = (
                _still(datasheet=sheet)
                if mobility == "none"
                else _body(mobility=mobility, datasheet=sheet)
            )
            assert missing_needs({"mobility": "none", "manipulator": "none"}, body) == [], (
                mobility,
                hands,
            )
            if mobility == "none":
                # `any` still means some kind: a body that stays put is refused it
                assert missing_needs({"mobility": "any"}, body) == ["mobility = any (has none)"]
            else:
                assert missing_needs({"mobility": "any"}, body) == []
    # and the words are in the vocabulary a verdict is validated against
    assert check_needs({"mobility": "none", "manipulator": "none"}) == {
        "mobility": "none",
        "manipulator": "none",
    }
    assert missing_needs_in({"mobility": "none"}, {}, None) == [], "asks nothing, of anybody"


def test_a_body_that_goes_nowhere_can_say_its_task_needs_nothing_it_lacks() -> None:
    """The needs a pilot on a bolted arm writes when it answers every field and asks for
    nothing it does not use: every number zero, no locomotion, nothing held, a flat indoor
    floor. Each of those used to be refused by the arm's own sheet: `work_height_m: 0` as
    unpublished, `terrain` as unpublished on a body that does not move, and the pilot had no
    word but `any` for mobility. A synthetic arm and the shipped one both let it through now,
    through the vocabulary the verdict is validated against as well as the checker."""
    nothing: dict[str, float | str] = {
        **dict.fromkeys(NEEDS_NUMBERS, 0),
        "mobility": "none",
        "manipulator": "none",
        "terrain": "indoor_flat",
    }
    verdict = Verdict(verdict="feasible", reason="a wave carries nothing", needs=nothing)
    for arm in (
        _still(datasheet=Datasheet(manipulator="gripper", arms=1, tethered=True)),
        describe(RobotSpec("lerobot", "mock")),
    ):
        assert arm.mobility == "none"
        assert own_sheet_objection(verdict.needs, arm) is None
        assert missing_needs(verdict.needs, arm) == []


def test_a_pilots_own_sheet_does_not_hold_an_unshown_working_height_against_it() -> None:
    """The prompt lists what a body's maker never published, and a working height band is left
    off that list on purpose (`Datasheet.FIGURES`): it is an extra where known, never a gap
    where not. So a pilot naming `work_height_m` for a body with no band had no way to know
    that was a gap, and the verdict gate refused it for one. The gate and `solo_hint`'s line
    about this body now read the pilot's own sheet as it was shown to the pilot.

    Everything that names a body to hand a task to stays strict, which is the asymmetry on
    purpose: a body that never said how high it works is not offered a task at a height."""
    from quackd.adapters.factory import bodies_that_could

    bandless = _still(datasheet=Datasheet(manipulator="gripper", arms=1))
    for height in (0.15, 0.6, 1.4):
        needs = {"work_height_m": height}
        assert own_sheet_objection(needs, bandless) is None
        assert missing_needs(needs, bandless, own_sheet=True) == []
        assert missing_needs(needs, bandless) == [f"work_height_m = {height:g} (not published)"]
        assert "This body publishes no working height band" in solo_hint(needs, bandless)
        for _name, other, _lacking in bodies_that_could(needs):
            assert other.datasheet is not None
            assert other.datasheet.workspace_height_m is not None, "strict for a stranger"

    # a band the sheet does publish still decides, own sheet or not
    band = Span(low=0.4, high=0.9, confidence="official", source="the docs")
    banded = _body(datasheet=Datasheet(manipulator="gripper", arms=1, workspace_height_m=band))
    assert own_sheet_objection({"work_height_m": 0.65}, banded) is None
    objection = own_sheet_objection({"work_height_m": 0.2}, banded)
    assert objection is not None and "work_height_m = 0.2 (reaches 0.4 to 0.9 m)" in objection
    # and a body with no datasheet at all is told height is not published, so no kindness
    sheetless = _body()
    objection = own_sheet_objection({"work_height_m": 0.6}, sheetless)
    assert objection is not None and "work_height_m = 0.6 (not published)" in objection


def test_a_bid_carries_its_facts_so_a_stranger_can_judge_them() -> None:
    """`missing_needs_in` reads a dumped datasheet, which is what arrives over a bus."""
    sheet = Datasheet(
        manipulator="gripper",
        arms=2,
        payload_kg=Figure(value=1.0, confidence="official", source="the docs"),
        workspace_height_m=Span(low=0.4, high=0.9, confidence="official", source="the docs"),
    )
    facts = sheet.model_dump()
    assert missing_needs_in({"payload_kg": 1.0, "arms": 2}, facts, "wheeled") == []
    assert missing_needs_in({"payload_kg": 2.0}, facts, "wheeled") == ["payload_kg >= 2 (has 1)"]
    assert missing_needs_in({"arms": 3}, facts, "wheeled") == ["arms >= 3 (has 2)"]
    assert missing_needs_in({"payload_kg": 1.0}, {}, None) == ["payload_kg >= 1 (not published)"]
    assert missing_needs_in({"mobility": "wheeled"}, {}, None) == [
        "mobility = wheeled (has unknown)"
    ]


def test_datasheet_value_reads_one_field_or_says_nothing() -> None:
    duck = describe(RobotSpec("microduck", "sim2d"))
    assert datasheet_value(duck, "mass_kg") == 0.8
    assert datasheet_value(duck, "payload_kg") is None
    assert datasheet_value(duck, "manipulator") == "beak"
    assert datasheet_value(duck, "mobility") == "legged"


# ── the hint ────────────────────────────────────────────────────────────────────────────


def test_the_hint_names_a_body_that_could_or_says_none_does() -> None:
    duck = describe(RobotSpec("microduck", "sim2d"))
    able = solo_hint({"payload_kg": 1.0, "manipulator": "gripper"}, duck)
    assert able.startswith("By their datasheets, xlerobot and alohamini could")
    assert "needs manipulator=gripper, payload_kg=1" in able

    nobody = solo_hint({"payload_kg": 3.0}, duck)
    assert nobody.startswith("No robot installed here meets needs payload_kg=3")
    assert "the most is toddlerbot at 1.484 kg" in nobody

    assert solo_hint({}, duck) == "", "a task that named no need has nothing to match"


def test_the_hint_names_the_most_only_for_a_need_nobody_meets() -> None:
    """`_best_numeric` reported the first numeric key the needs named, met or not, so a task
    refused on its terrain, having asked for no payload at all, was told who carries the most.
    That explains a refusal that never happened, and a pilot handing the task on reads it as
    the reason. The need a "most" is named for is now above zero, met by nobody installed, and
    above the most anybody publishes, which is what "the most" answers."""
    from quackd.adapters.factory import bodies_that_could, installed_manifests

    duck = describe(RobotSpec("microduck", "sim2d"))
    unmet = {"payload_kg": 0, "terrain": "outdoor"}
    assert not bodies_that_could(unmet), "this test needs a need that nobody here meets"
    said = solo_hint(unmet, duck)
    assert said.startswith("No robot installed here meets needs payload_kg=0, terrain=outdoor")
    assert "the most is" not in said, said

    payloads = {
        name: value
        for name, manifest in installed_manifests()
        if isinstance(value := datasheet_value(manifest, "payload_kg"), float)
    }
    assert len(payloads) >= 2, "this test needs two bodies that publish a payload"
    least, most = min(payloads.values()), max(payloads.values())
    said = solo_hint({"payload_kg": least, "terrain": "outdoor"}, duck)
    assert "the most is" not in said, "somebody carries that much: it is not why nobody could"
    heaviest = max(payloads, key=lambda name: payloads[name])
    said = solo_hint({"payload_kg": most * 2, "terrain": "outdoor"}, duck)
    assert f"the most is {heaviest} at {most:g} kg" in said


def test_the_hint_says_so_when_this_body_already_meets_the_need() -> None:
    cart = describe(RobotSpec("alohamini", "mock"))
    text = solo_hint({"payload_kg": 0.5, "manipulator": "gripper"}, cart)
    assert "This body's own datasheet meets those needs" in text
    assert "the pilot's judgement rather than a limit" in text


def test_the_hint_says_a_sheet_meets_a_height_only_when_it_publishes_one_that_does() -> None:
    """The hint's last sentence reads the pilot's own sheet as the gate does, and that reading
    lets a working height through on a sheet with no working height band. It then said "This
    body's own datasheet meets those needs" right after "No robot installed here meets needs
    work_height_m=...", of a body installed here, for any height at all: a sheet that publishes
    no band does not meet a height, it never said. That case now says the body publishes no
    band and the height is the pilot's judgement; "meets" is left for a sheet whose published
    band holds the height, or needs that turn on no height. Synthetic sheets and heights."""
    band = Span(low=0.2, high=0.7, confidence="official", source="the docs")
    banded = _still(datasheet=Datasheet(manipulator="gripper", arms=1, workspace_height_m=band))
    bandless = _still(datasheet=Datasheet(manipulator="gripper", arms=1))
    for height in (band.low, (band.low + band.high) / 2, band.high):
        met = solo_hint({"work_height_m": height, "mobility": "none"}, banded)
        assert "This body's own datasheet meets those needs" in met, met
        assert "publishes no working height band" not in met, met
    heights = (band.high / 2, band.high * 3, band.high * 30)
    for height in heights:
        said = solo_hint({"work_height_m": height, "mobility": "none"}, bandless)
        assert "meets those needs" not in said, said
        assert said.endswith(
            " This body publishes no working height band, so whether it reaches that height "
            "is the pilot's judgement."
        ), said
    # needs that name no height are met the strict way too, band or no band
    for body in (banded, bandless):
        said = solo_hint({"manipulator": "gripper", "mobility": "none"}, body)
        assert "This body's own datasheet meets those needs" in said, said


def test_bodies_that_could_reads_only_the_static_descriptions() -> None:
    from quackd.adapters.factory import bodies_that_could, installed_manifests

    assert [name for name, _m in installed_manifests()] == list(ADAPTER_NAMES)
    assert [name for name, _m, _missing in bodies_that_could({"payload_kg": 3.0})] == []
    named = [name for name, _m, _missing in bodies_that_could({"manipulator": "gripper"})]
    assert named == ["lerobot", "xlerobot", "alohamini"]
    # every backend of a named body agrees, because the sheet does not vary with one
    for backend in BACKENDS["alohamini"]:
        sheet = describe(RobotSpec("alohamini", backend)).datasheet
        assert sheet is not None and sheet.manipulator == "gripper"
