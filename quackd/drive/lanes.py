"""What a typed line means, before anything is asked of anybody: the brake, the session's own
words, and the exact lane, which matches a line that names a call outright in microseconds.

Everything here is pure and synchronous. A line the brake takes never waits on a network, a
parse or a lock, and a line the exact lane cannot read goes on to the fast lane or the pilot.
"""

from __future__ import annotations

import ast
import itertools
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from quackd.agent.decision.stepper import discrete_calls
from quackd.drive.presets import Preset
from quackd.verbs.aliases import ALIASES
from quackd.verbs.registry import Verb

BRAKE_WORDS = ("", "stop", "halt", "brake", "q")
"""A line that is one of these, or starts with one as a whole word, brakes: an empty Enter, `stop`,
`halt`, `brake` and `q`. By word, so "quack" never brakes, and "stop now" does."""

SESSION_WORDS = ("help", "status", "quit", "exit")
"""Words that are the session's own, read after the brake and before any call."""

PRESET_VERBS = ("walk", "go", "move")
"""What may stand before `forward` or `back`: "walk forward" is the preset `forward`."""

_WORD = re.compile(r"[a-z0-9_'.\-]+")


def words(line: str) -> list[str]:
    """A line's words as the lanes compare them: casefolded, punctuation around them dropped, and
    `_` read as a space, so `report_state` and "report state" are one call."""
    return _WORD.findall(line.casefold().replace("_", " ").replace(",", " "))


def is_brake(line: str) -> bool:
    said = words(line)
    return not said or said[0] in BRAKE_WORDS[1:]


def session_word(line: str) -> str | None:
    said = words(line)
    return said[0] if len(said) == 1 and said[0] in SESSION_WORDS else None


@dataclass(frozen=True)
class Matched:
    """A line the exact lane read: the call it names, as the executor will be handed it, and how
    it was said, or why such a call is not drive's to run."""

    verb: str
    arguments: dict[str, Any] = field(default_factory=dict)
    label: str = ""
    preset: str | None = None
    refused: str | None = None


def _key(said: Iterable[str]) -> str:
    return " ".join(said)


def _rendered(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value).casefold()


class ExactLane:
    """Every line that names a call outright, for one body and one contract, built once when the
    session connects.

    Its keys are the stepper's own labels for every closed-set call an allowed verb has
    (`discrete_calls`, `kick(leg=left)`), the verb's name with those values in any order
    ("kick left"), the bare name of a verb with nothing required, which is the call with the verb's
    own defaults, each alias of those, and the presets' phrasings. A verb's own call beats a
    preset spelled the same way. Two more forms are read as they come: a call written out,
    `move(vx=0.2)`, whose values are taken as literals and never evaluated, and a verb whose one
    required parameter is a string, which takes the rest of the line ("say good morning")."""

    def __init__(self, verbs: Mapping[str, Verb], presets: Mapping[str, Preset]) -> None:
        self.verbs = dict(verbs)
        self.table: dict[str, Matched] = {}
        self.texts: dict[str, tuple[str, str]] = {}
        """A verb's spoken names to it and the string parameter the rest of the line fills."""
        self.names: dict[str, str] = {}
        """Every way a verb is spelled, its aliases included, to its own name."""
        for name in self.verbs:
            self.names[_key(words(name))] = name
        for alias, target in ALIASES.items():
            if target in self.verbs:
                self.names[_key(words(alias))] = target
        for preset in presets.values():
            for phrase in self._phrasings(preset.name):
                self.table[phrase] = Matched(
                    "move", dict(preset.call), preset.says(), preset=preset.name
                )
        for spoken, name in self.names.items():
            self._add_verb(spoken, name, self.verbs[name])

    @staticmethod
    def _phrasings(preset: str) -> list[str]:
        if preset in ("forward", "back"):
            return [preset, *(f"{verb} {preset}" for verb in PRESET_VERBS)]
        return [preset]

    def _add_verb(self, spoken: str, name: str, verb: Verb) -> None:
        schema = verb.tool_schema()
        inner = schema.get("input_schema") or {}
        required = list(inner.get("required") or ())
        properties: Mapping[str, Any] = inner.get("properties") or {}
        calls = discrete_calls({**schema, "name": name}) or []
        # The labels themselves, `kick(leg=left)`, are read as calls written out (`_written`),
        # which gives each the same arguments and the same spelling.
        for call in calls:
            matched = self._matched(name, verb, call.arguments, call.label)
            values = [_rendered(v) for v in call.arguments.values()]
            for order in itertools.permutations(values):
                self.table[_key([spoken, *order])] = matched
        if not required:
            # after the presets, so a verb's own name beats a preset spelled the same way
            self.table[spoken] = self._matched(name, verb, {}, name)
        if len(required) == 1 and (properties.get(required[0]) or {}).get("type") == "string":
            self.texts[spoken] = (name, required[0])

    @staticmethod
    def _matched(name: str, verb: Verb, arguments: dict[str, Any], label: str) -> Matched:
        refused = None
        if verb.safety_class == "dangerous":
            refused = (
                f"{name} is dangerous, and only a person answering at a terminal in `quackd run` "
                "may clear it: drive never runs it"
            )
        return Matched(name, dict(arguments), label, refused=refused)

    def parse(self, line: str) -> Matched | None:
        """The call `line` names outright, or None for a line this lane cannot read."""
        said = words(line)
        if not said:
            return None
        if (hit := self.table.get(_key(said))) is not None:
            return hit
        if (written := self._written(line)) is not None:
            return written
        for size in range(min(len(said) - 1, 3), 0, -1):
            spoken = _key(said[:size])
            if spoken in self.texts:
                name, prop = self.texts[spoken]
                rest = self._rest(line, size)
                if rest:
                    label = f"{name}({prop}={rest!r})"
                    return self._matched(name, self.verbs[name], {prop: rest}, label)
        return None

    @staticmethod
    def _rest(line: str, size: int) -> str:
        """The line after its first `size` words, as typed."""
        parts = line.strip().split(None, size)
        return parts[size].strip() if len(parts) > size else ""

    def _written(self, line: str) -> Matched | None:
        """A call written out as `name(key=value, ...)`, its values read as literals: numbers,
        strings, true and false, and a bare word as the string it spells. Nothing is evaluated,
        and anything else, a name it calls, an operator or a starred argument, is not a call
        this lane reads. The numbers are the person's own, and the executor validates and clamps
        them as it does anybody's."""
        text = line.strip()
        if "(" not in text or not text.endswith(")"):
            return None
        try:
            tree = ast.parse(text, mode="eval")
        except SyntaxError:
            return None
        node = tree.body
        if not isinstance(node, ast.Call) or node.args or not isinstance(node.func, ast.Name):
            return None
        name = self.names.get(_key(words(node.func.id)))
        if name is None:
            return None
        arguments: dict[str, Any] = {}
        for keyword in node.keywords:
            if keyword.arg is None:
                return None
            value = _literal(keyword.value)
            if value is _NOT_LITERAL:
                return None
            arguments[keyword.arg] = value
        shown = ", ".join(f"{k}={_rendered(v)}" for k, v in sorted(arguments.items()))
        return self._matched(name, self.verbs[name], arguments, f"{name}({shown})")


_NOT_LITERAL = object()


def _literal(node: ast.expr) -> Any:
    if isinstance(node, ast.Constant) and isinstance(node.value, int | float | str | bool):
        return node.value
    if (
        isinstance(node, ast.UnaryOp)
        and isinstance(node.op, ast.USub)
        and isinstance(node.operand, ast.Constant)
        and isinstance(node.operand.value, int | float)
        and not isinstance(node.operand.value, bool)
    ):
        return -node.operand.value
    if isinstance(node, ast.Name):
        lowered = node.id.casefold()
        if lowered in ("true", "false"):
            return lowered == "true"
        return node.id
    return _NOT_LITERAL
