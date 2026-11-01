"""Whether one task file gives a pilot more than another, for `quackd serve-mcp --lock-duckfile`.

`robot_load_duckfile` is a tool the model holds, and by default a contract it loads replaces the one
the server started with: the allowlist and the limits become the new file's, and only what has been
spent carries over. That is what lets a client move on to the next task. An operator who started the
server to hold the model to one file can say so, and then a file that asks for more is refused.

Only what the executor enforces is compared. A file's prose, its success criteria and its advisory
abort lines are words to the model and gate nothing, so a file may reword them. The datasheet is the
one thing compared by being different at all, since it feeds the verdict the gate waits for and
there is no ordering of "more" among corrections to a body's sheet.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from quackd.duckfile.schema import POLICY_VERB, DuckFrontmatter

POLICY_VERBS = (POLICY_VERB, "pick")
"""The verbs a learned policy drives, whose segments a contract's `policy:` limits."""


def widened_by(
    locked: DuckFrontmatter, offered: DuckFrontmatter, canonical: Callable[[str], str]
) -> list[str]:
    """What `offered` would let a pilot do that `locked` does not, one line each, for saying why it
    was refused. Empty when it asks for nothing more. `canonical` is the registry's own, so a verb
    spelled by its alias is the same verb."""
    wider: list[str] = []

    allowed = {canonical(verb) for verb in locked.verbs.allow}
    wider += [f"it allows {verb}" for verb in offered.verbs.allow if canonical(verb) not in allowed]
    still_allowed = {canonical(verb) for verb in offered.verbs.allow}
    still_confirmed = {canonical(verb) for verb in offered.verbs.confirm}
    wider += [
        f"it stops asking before {verb}"
        for verb in locked.verbs.confirm
        if canonical(verb) in still_allowed and canonical(verb) not in still_confirmed
    ]

    for name in ("max_steps", "max_minutes", "max_llm_calls"):
        was, now = getattr(locked.budgets, name), getattr(offered.budgets, name)
        if now > was:
            wider.append(f"it raises {name} from {was:g} to {now:g}")

    battery = locked.battery_abort_percent
    offered_battery = offered.battery_abort_percent
    if battery is not None and (offered_battery is None or offered_battery < battery):
        wider.append(f"it lowers the battery abort from {battery:g}%")
    failures = locked.repeat_failure_abort
    offered_failures = offered.repeat_failure_abort
    if failures is not None and (offered_failures is None or offered_failures > failures):
        wider.append(f"it allows more than {failures} failures of a verb in a row")

    # A file allowing neither verb a policy drives gets no policy, whatever its section's defaults
    # would say, so it asks for nothing here.
    if still_allowed & {canonical(verb) for verb in POLICY_VERBS}:
        policy, offered_policy = locked.effective_policy, offered.effective_policy
        for name in ("segment_s", "total_s"):
            was, now = getattr(policy, name), getattr(offered_policy, name)
            if now > was:
                wider.append(f"it raises policy {name} from {was:g} to {now:g}")
        if policy.instructions:
            # an empty list is not a shorter one: it lets the pilot word each subtask itself
            words = set(policy.instructions)
            if not offered_policy.instructions:
                wider.append("it lets the pilot word the policy's instructions itself")
            else:
                wider += [
                    f"it adds the policy instruction {line!r}"
                    for line in offered_policy.instructions
                    if line not in words
                ]

    if _sheet(offered) != _sheet(locked):
        wider.append("it changes the robot's datasheet")
    return wider


def _sheet(contract: DuckFrontmatter) -> dict[str, Any]:
    """What a contract's datasheet section corrects: an empty one corrects nothing, as none does."""
    return contract.datasheet.model_dump(exclude_defaults=True) if contract.datasheet else {}
