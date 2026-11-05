"""A task file's `policy:` section, made into the `manipulate` a run offers.

The verb a body registers takes any instruction, and its segment and its timeout are the body's
own defaults. A v3 task file says which instructions the policy may be told and how long each
segment runs, and this is where that becomes the verb itself rather than a sentence in the
prompt:

- `instruction` becomes a `Literal` of the listed instructions, an inline `enum` in the tool's
  JSON Schema, so the executor refuses any other words, the pilot LLM is shown exactly the list,
  and the stepper can offer each one (`discrete_calls`). An `Enum` class would reach the schema
  as a `$ref`, which the stepper does not follow, so it would never be a choice. A list of one
  is an `enum` of one as well, where pydantic would write a `const` that Gemini's schema
  refuses, and with it every tool of the run.
- `timeout_s` becomes the segment plus `SEGMENT_HEADROOM_S`, plus the wall seconds a
  simulator's clock stands still while its policy thinks (`frozen_inference_s`), which the
  executor's clock does not, held to `FROZEN_INFERENCE_MAX_S`.
- the segment's length reaches the verb through the transport (`set_segment_s`), which is where
  the verb reads it, so the segment the verb runs and the timeout it runs under come from the
  same number. A transport with no setter runs its own segment under the verb's own timeout.

It rebuilds the verb from the one the robot registered every time (`Verb.template`), never from
the last narrowing, so a second task file loaded over MCP is narrowed from the body's own verb
and not through the first file's list. A task with no section, or no task at all, gets the
named defaults and any words. A registry with no `manipulate` is left as it is.
"""

from __future__ import annotations

import dataclasses
import math
from typing import Any, Literal, cast

from pydantic import Field, create_model

from quackd.duckfile.schema import (
    POLICY_VERB,
    SEGMENT_HEADROOM_S,
    SEGMENT_MAX_S,
    DuckFrontmatter,
    PolicySection,
)
from quackd.verbs.registry import Verb, VerbRegistry

LISTED = (
    "One of the subtasks this task file lists, word for word: the arm's learned policy is told "
    "nothing else."
)
"""What the narrowed `instruction` says about itself in the tool's schema."""
FROZEN_INFERENCE_MAX_S = 10 * SEGMENT_MAX_S
"""The most wall seconds past a segment and its headroom that `manipulate`'s timeout covers
for a simulator's clock standing still while its policy thinks: ten times the longest segment,
ten minutes. On a lockstep clock the executor's timeout is the only thing on the wall's clock
that ends a runner that hangs, and a simulator's bound is what its policy declares and what one
frame cost at connect, times every request a segment can hold: one camera slow to draw at
connect makes it hours, and a transport that says something wilder, more. A segment whose clock
stands still longer than this is stopped as timed out, which is a rehearsal ended and never an
arm left moving."""


def frozen_inference_s(transport: Any, segment_s: float) -> float:
    """The wall seconds `transport` says its clock stands still over a segment of `segment_s`,
    held to `FROZEN_INFERENCE_MAX_S`, or 0 for a transport that does not say, whose answer
    raises, or that says something that is not a number of seconds above 0. Only a simulator's
    clock stands still while its policy thinks; on the wall's clock the thinking happens inside
    the segment's own seconds. Never a reason to refuse the run: a bound nobody can give leaves
    the tightest timeout, which only ends a segment sooner."""
    ask = getattr(transport, "frozen_inference_s", None)
    if not callable(ask):
        return 0.0
    try:
        said = ask(segment_s)
    except Exception:
        return 0.0
    if isinstance(said, bool) or not isinstance(said, int | float):
        return 0.0
    try:
        seconds = float(said)
    except OverflowError:  # an int past any float, which is past the maximum too
        seconds = math.inf
    if math.isnan(seconds) or seconds <= 0:
        return 0.0
    return min(seconds, FROZEN_INFERENCE_MAX_S)


def _as_enum(schema: dict[str, Any]) -> None:
    """The narrowed `instruction`'s schema with a `const` spelled as an `enum` of one: the same
    one word, which Gemini's schema takes as an `enum` and refuses as a `const`."""
    if "const" in schema:
        schema["enum"] = [schema.pop("const")]


def policy_problem(registry: VerbRegistry, contract: DuckFrontmatter | None) -> str | None:
    """Why this registry's `manipulate` cannot be held to `contract`'s policy section, as a
    sentence that says what to do, or None when it can or there is nothing to hold. Raised by
    `narrow_policy_verb` before it changes anything, which `RobotSession.load` calls before it
    adopts a file, so a file refused here leaves a session's task, verdict and verb as they
    were."""
    if (
        POLICY_VERB not in registry
        or contract is None
        or not contract.effective_policy.instructions
    ):
        return None
    current = registry.get(POLICY_VERB)
    template = current.template or current
    if "instruction" in template.params.model_fields:
        return None
    return (
        f"this robot's {POLICY_VERB} takes no instruction, so a task file's policy.instructions "
        "cannot narrow it: leave policy.instructions empty to run this task on it"
    )


def narrow_policy_verb(
    registry: VerbRegistry, contract: DuckFrontmatter | None, transport: Any
) -> Verb | None:
    """Register `manipulate` narrowed to `contract`'s policy section, or to the defaults when
    there is no contract or no section, and return it. None, with nothing changed, for a
    registry that has no `manipulate`.

    Called where the verbs a run offers become final: in the agent loop once the allowlist is,
    before the tools are built from it, in an MCP session after its robot connects, and once a
    task file it loads is validated, before the file is adopted. The pilot LLM's tools, the
    stepper's choices and the executor's check all read the one verb this registers. A
    ValueError, with nothing changed, for a registry whose `manipulate` takes no instruction to
    narrow (`policy_problem`), or a segment the transport's setter refuses. Nothing else it
    asks the transport can raise out of it (`frozen_inference_s`)."""
    if POLICY_VERB not in registry:
        return None
    if (problem := policy_problem(registry, contract)) is not None:
        raise ValueError(problem)
    current = registry.get(POLICY_VERB)
    template = current.template or current
    policy = contract.effective_policy if contract is not None else PolicySection()
    params = template.params
    if policy.instructions:
        listed = cast(Any, Literal)[tuple(policy.instructions)]
        params = create_model(
            template.params.__name__,
            __base__=template.params,
            instruction=(listed, Field(..., description=LISTED, json_schema_extra=_as_enum)),
        )
    frozen = frozen_inference_s(transport, policy.segment_s)
    setter = getattr(transport, "set_segment_s", None)
    if callable(setter):
        setter(policy.segment_s)
        timeout_s = policy.segment_s + SEGMENT_HEADROOM_S + frozen
        said = f" In this run each segment lasts up to {policy.segment_s:g} s, and the segments"
    else:
        # a body that cannot be told the segment's length runs its own, which only its own
        # timeout is known to cover
        timeout_s = template.timeout_s + frozen
        said = " In this run the segments"
    narrowed = dataclasses.replace(
        template,
        description=f"{template.description}{said} may run {policy.total_s:g} s in all.",
        params=params,
        timeout_s=timeout_s,
        template=template,
    )
    registry.register(narrowed, replace=True)
    return narrowed


__all__ = [
    "FROZEN_INFERENCE_MAX_S",
    "LISTED",
    "frozen_inference_s",
    "narrow_policy_verb",
    "policy_problem",
]
