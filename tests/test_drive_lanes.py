"""What a typed line means before anything is asked of anybody (`quackd.drive.lanes`)."""

from __future__ import annotations

from typing import Any

import pytest

import quackd_microduck
from quackd.adapters.factory import RobotSpec, describe
from quackd.agent.decision.stepper import discrete_calls
from quackd.drive.lanes import ExactLane, is_brake, session_word
from quackd.drive.presets import presets_for
from quackd.verbs.registry import NoParams, Verb, VerbResult, registry_from_manifest


def _lane(extra: list[Verb] | None = None) -> ExactLane:
    manifest = describe(RobotSpec("microduck", "mock"))
    adapter = quackd_microduck.MicroduckAdapter.__new__(quackd_microduck.MicroduckAdapter)
    registry = registry_from_manifest(manifest, adapter)
    for verb in extra or ():
        registry.register(verb, replace=True)
    verbs = {name: registry.get(name) for name in registry.names()}
    return ExactLane(verbs, presets_for(manifest, verbs))


async def _does_nothing(_ctx: Any, _p: Any) -> VerbResult:
    return VerbResult.success("done")


@pytest.mark.parametrize("line", ["", "   ", "stop", "STOP!", "stop now", "halt", "brake", "q"])
def test_these_brake(line: str) -> None:
    assert is_brake(line)


@pytest.mark.parametrize("line", ["quack", "stopwatch", "qq", "don't stop", "sit", "q-tip"])
def test_these_do_not(line: str) -> None:
    """By whole word and from the front only: "quack" is a duck's word and never a brake."""
    assert not is_brake(line)


def test_the_sessions_own_words_are_one_word_lines() -> None:
    assert session_word("help") == "help" and session_word(" Quit ") == "quit"
    assert session_word("help me sit") is None and session_word("sit") is None


def test_every_call_the_stepper_would_offer_reads_as_itself() -> None:
    """The labels a decision LLM chooses between are lines a person can type, and each means the
    same call here as there."""
    lane = _lane()
    for name, verb in lane.verbs.items():
        for call in discrete_calls(verb.tool_schema()) or []:
            matched = lane.parse(call.label)
            assert matched is not None, call.label
            assert (matched.verb, matched.arguments) == (name, call.arguments), call.label
            assert matched.label == call.label


@pytest.mark.parametrize(
    ("line", "verb", "arguments"),
    [
        ("kick left", "kick", {"leg": "left"}),
        ("Kick Right", "kick", {"leg": "right"}),
        ("gaze up", "gaze", {"direction": "up"}),
        ("report state", "report_state", {}),
        ("report_state", "report_state", {}),
        ("kick", "kick", {}),
        ("go to", "go_to", {}),
        ("walk to", "go_to", {}),
        ("say good morning", "say", {"text": "good morning"}),
        ("watch the red ball", "watch", {"target": "the red ball"}),
        ("move(vx=0.2, duration_s=2)", "move", {"vx": 0.2, "duration_s": 2}),
        ("walk(vx=-0.1)", "move", {"vx": -0.1}),
        ("go_to(target=person)", "go_to", {"target": "person"}),
    ],
)
def test_a_line_that_names_a_call_outright_is_that_call(
    line: str, verb: str, arguments: dict[str, Any]
) -> None:
    matched = _lane().parse(line)
    assert matched is not None, line
    assert (matched.verb, matched.arguments, matched.refused) == (verb, arguments, None)


@pytest.mark.parametrize(
    ("line", "preset"),
    [
        ("forward", "forward"),
        ("walk forward", "forward"),
        ("go back", "back"),
        ("turn left", "turn left"),
    ],
)
def test_a_preset_is_a_move_with_quackds_numbers(line: str, preset: str) -> None:
    matched = _lane().parse(line)
    assert matched is not None and matched.preset == preset and matched.verb == "move"
    assert matched.label.startswith(f"{preset}: move(")


def test_a_verbs_own_call_beats_a_preset_spelled_the_same_way() -> None:
    forward = Verb("forward", "the body's own forward", _does_nothing, NoParams)
    matched = _lane([forward]).parse("forward")
    assert matched is not None and matched.verb == "forward" and matched.preset is None


@pytest.mark.parametrize(
    "line",
    [
        "move(vx=__import__('os').system('echo hi'))",
        "move(vx=1+1)",
        "move(**{'vx': 1})",
        "move(0.2)",
        "nosuchverb(x=1)",
        "say",
        "please be careful",
    ],
)
def test_a_line_the_lane_cannot_read_is_left_for_the_others(line: str) -> None:
    """Nothing written out is evaluated: anything but a literal value, a name it calls, an operator
    or a starred argument, and the line is not a call this lane reads."""
    assert _lane().parse(line) is None


def test_a_dangerous_verb_is_named_and_refused() -> None:
    risky = Verb("cartwheel", "flips the body", _does_nothing, NoParams, safety_class="dangerous")
    matched = _lane([risky]).parse("cartwheel")
    assert matched is not None and matched.verb == "cartwheel"
    assert matched.refused is not None and "quackd run" in matched.refused
