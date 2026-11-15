"""The verb registry is one list used everywhere; these tests pin its shape."""

from __future__ import annotations

import pytest

from roboglot.safety import Executor, allow_all
from roboglot.transport.mock import MockTransport
from roboglot.verbs.learned import LearnedVerbSpec, register_learned_verb
from roboglot.verbs.registry import VerbContext, VerbNotFound, VerbRegistry, VerbResult

BUILTINS = {
    "move",
    "sit",
    "stand",
    "kick",
    "grab",
    "stand_up",
    "stop",
    "quack",
    "gaze",
    "observe",
    "say",
    "report_state",
}
COMPOSITES = {"search_scan", "go_to", "approach_and"}


def test_default_registry_contents(registry: VerbRegistry) -> None:
    names = set(registry.names())
    assert names >= BUILTINS
    assert names >= COMPOSITES
    assert all(registry.get(n).kind == "builtin" for n in BUILTINS)
    assert all(registry.get(n).kind == "composite" for n in COMPOSITES)


def test_tool_schema_shape(registry: VerbRegistry) -> None:
    assert registry.get("walk").name == "move"  # stored canonically, resolved by alias
    schema = registry.view("walk").tool_schema()
    assert schema["name"] == "walk"  # shown as the .glot spelled it
    assert schema["input_schema"]["type"] == "object"
    assert set(schema["input_schema"]["properties"]) == {"vx", "vy", "wz", "duration_s"}
    assert schema["input_schema"]["additionalProperties"] is False
    assert "Done when" in schema["description"]
    stop = registry.get("stop").tool_schema()
    assert stop["input_schema"]["properties"] == {}


def test_unknown_and_duplicates(registry: VerbRegistry) -> None:
    with pytest.raises(VerbNotFound):
        registry.get("fly")
    assert registry.unknown(["walk", "fly"]) == ["fly"]
    with pytest.raises(ValueError):
        registry.register(registry.get("walk"))


async def test_learned_verb_registers_and_runs(registry: VerbRegistry) -> None:
    spec = LearnedVerbSpec(
        name="moonwalk", description="A learned moonwalk.", policy_path="moonwalk.onnx"
    )

    async def runner(spec_: LearnedVerbSpec, ctx: VerbContext) -> VerbResult:
        return VerbResult.success(f"ran {spec_.policy_path} at {spec_.control_hz:g} Hz")

    verb = register_learned_verb(registry, spec, runner)
    assert verb.kind == "learned"
    assert verb.safety_class == "confirm"  # unproven policies always ask
    executor = Executor(registry=registry, transport=MockTransport(), confirm=allow_all)
    result = await executor.run_verb("moonwalk", {})
    assert result.ok and "moonwalk.onnx" in result.summary


async def test_learned_verb_without_runner_explains_itself(registry: VerbRegistry) -> None:
    register_learned_verb(
        registry, LearnedVerbSpec(name="x", description="d", policy_path="x.onnx")
    )
    executor = Executor(registry=registry, transport=MockTransport(), confirm=allow_all)
    result = await executor.run_verb("x")
    assert not result.ok and "v2" in result.summary
