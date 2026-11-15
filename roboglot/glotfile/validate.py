"""Check a `.glot` against a vocabulary: the Microduck's by default, or one or more robot
manifests. One implementation, one wording, shared by `roboglot validate`, the MCP
`robot_load_glotfile` tool and the team runner (ADR-0019).

Parse and schema errors are the parser's; this module only judges a parsed contract.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from roboglot.glotfile.schema import GlotFile

if TYPE_CHECKING:
    from roboglot.adapters.manifest import RobotManifest
    from roboglot.verbs.registry import VerbRegistry


@dataclass(frozen=True)
class Problem:
    field: str
    message: str
    robot: str | None = None
    verb: str | None = None

    def __str__(self) -> str:
        return f"{self.field}: {self.message}"


def validate_glot(
    duck: GlotFile,
    manifests: Sequence[RobotManifest] = (),
    *,
    registry: VerbRegistry | None = None,
    team: bool | None = None,
) -> list[Problem]:
    """Every problem, in a stable order; an empty list means the contract is honourable.

    With manifests, `requires` (for a v0 file: every allowed verb) must be provided by each
    robot for a solo task, or by at least one robot of a team; every team role must be
    fillable by at least one robot. Without manifests the vocabulary is the registry.

    `team` overrides where that fork is taken. None reads the file, which is right for
    `roboglot validate` and for a solo run. A task file with no `team:` block run against a
    stored team is a team all the same, and checking each of its bodies for every verb
    would refuse the arm for not being able to walk, so the caller says so (ADR-0034)."""
    fm = duck.frontmatter
    as_team = (fm.team is not None) if team is None else team
    problems: list[Problem] = []
    if not manifests:
        if registry is None:
            from roboglot.adapters.factory import installed_vocabulary

            # every body installed here, not one of them: a task file that named no robot is
            # being checked for coherence rather than against a particular arm
            registry = installed_vocabulary()
        unknown = registry.unknown(fm.verbs.allow)
        if unknown:
            problems.append(Problem("verbs.allow", f"unknown verbs: {', '.join(unknown)}"))
    if fm.learned_verbs:
        problems.append(
            Problem("learned_verbs", "must be empty (executing policies is a v2 feature)")
        )
    if fm.team is not None and fm.team.allocation.method == "auction" and fm.verbs.confirm:
        # An auction member is a state machine with nobody to ask, so a gated verb there can
        # only ever be refused. A pilot team has pilots, and `--yes` is the answer for all of
        # them at once, so the refusal belongs at the run rather than in the contract.
        problems.append(
            Problem("verbs.confirm", "a team cannot prompt y/N per duck: empty verbs.confirm")
        )
    if not manifests:
        return problems

    if fm.datasheet is not None:
        # the merge is where a task file's corrections meet the body's own invariants: an
        # armless body handed a payload is a contradiction, and it is caught here rather than
        # after the robot has connected
        from pydantic import ValidationError

        from roboglot.adapters.manifest import apply_datasheet_override

        for m in manifests:
            try:
                apply_datasheet_override(m, fm.datasheet)
            except ValidationError as e:
                why = "; ".join(str(err["msg"]).removeprefix("Value error, ") for err in e.errors())
                problems.append(Problem("datasheet", f"{m.id} ({m.model}): {why}", robot=m.id))

    reported: set[str] = set()
    if not as_team:
        for m in manifests:
            for verb in fm.effective_requires:
                if not m.provides(verb):
                    reported.add(verb)
                    problems.append(
                        Problem(
                            "requires",
                            f"requires {verb}, but {m.id} ({m.model}) does not provide it"
                            f"{_why_not(m, verb)}",
                            robot=m.id,
                            verb=verb,
                        )
                    )
    else:
        ids = ", ".join(m.id for m in manifests)
        for verb in fm.effective_requires:
            if not any(m.provides(verb) for m in manifests):
                reported.add(verb)
                problems.append(
                    Problem(
                        "requires", f"requires {verb}, but none of {ids} provides it", verb=verb
                    )
                )
        roles = fm.team.roles if fm.team is not None else None
        for role, spec in (roles or {}).items():
            fillers = [m for m in manifests if all(m.provides(v) for v in spec.requires)]
            if not fillers:
                problems.append(
                    Problem(
                        f"team.roles.{role}",
                        f"no robot provides all of {', '.join(spec.requires)}",
                    )
                )
                continue
            if not spec.needs:
                continue
            from roboglot.team.capability import missing_needs

            lacking = {m.id: missing_needs(spec.needs, m) for m in fillers}
            if all(lacking.values()):
                why = "; ".join(
                    f"{m.id} ({m.model}) lacks {', '.join(lacking[m.id])}" for m in fillers
                )
                problems.append(
                    Problem(f"team.roles.{role}.needs", f"no robot meets its needs: {why}")
                )
    # the weaker line: an allowed verb no robot has (a v1 task may allow more than it needs)
    for verb in fm.verbs.allow:
        if verb in reported or any(m.provides(verb) for m in manifests):
            continue
        if len(manifests) == 1:
            who = f"{manifests[0].id}{_why_not(manifests[0], verb)}"
        else:
            who = "any of " + ", ".join(m.id for m in manifests)
        problems.append(Problem("verbs.allow", f"{verb} is not provided by {who}", verb=verb))
    return problems


def _why_not(manifest: RobotManifest, verb: str) -> str:
    """Why `manifest` leaves `verb` out, in the body's own words, as a refusal ends with: a duck
    whose reading keeps no map says why it does not explore (`RobotManifest.not_offered`)."""
    why = manifest.not_offered().get(verb)
    return f" ({why})" if why else ""
