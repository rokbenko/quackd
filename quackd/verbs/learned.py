"""The v2 extension point, and nothing more.

A *learned verb* is an ONNX policy plus metadata that registers as one more verb — the
same way `kick` or `walk_to` do — so an LLM-written reward (DrEureka-style) can, one day,
grow the vocabulary without touching the loop. This module defines the shape, a registration
helper, and a way to plug in a runner. It ships no policy, no training, and no ONNX runtime.
See `docs/concepts/learned-verbs.md`.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from quackd.verbs.registry import NoParams, Verb, VerbContext, VerbRegistry, VerbResult


class LearnedVerbSpec(BaseModel):
    """Everything needed to register a policy as a verb. Mirrors `.duck` `learned_verbs`."""

    model_config = ConfigDict(extra="forbid")

    name: str
    description: str
    policy_path: str = Field(..., description="ONNX file. obs[1,61] -> actions[1,14] on upstream.")
    observation_dim: int = Field(default=61, description="Upstream's unified observation width.")
    action_dim: int = Field(default=14)
    control_hz: float = Field(default=50.0)
    timeout_s: float = 10.0
    metadata: dict[str, Any] = Field(default_factory=dict)


LearnedRunner = Callable[[LearnedVerbSpec, VerbContext], Awaitable[VerbResult]]
"""Runs the policy. On hardware this would ship the ONNX to robotd's policy slot — an
upstream feature that does not exist yet (`robotd.toml [policy]` paths are static today)."""


async def _no_runner(spec: LearnedVerbSpec, ctx: VerbContext) -> VerbResult:
    return VerbResult.fail(
        f"learned verb {spec.name!r} has no runner: executing ONNX policies is a v2 feature "
        "(docs/concepts/learned-verbs.md)"
    )


def register_learned_verb(
    registry: VerbRegistry, spec: LearnedVerbSpec, runner: LearnedRunner | None = None
) -> Verb:
    run = runner or _no_runner

    async def execute(ctx: VerbContext, _p: NoParams) -> VerbResult:
        return await run(spec, ctx)

    return registry.register(
        Verb(
            spec.name,
            spec.description,
            execute,
            NoParams,
            timeout_s=spec.timeout_s,
            safety_class="confirm",  # a new policy is unproven by definition
            kind="learned",
        )
    )
