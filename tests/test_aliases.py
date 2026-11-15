"""The one alias table, and everything that must resolve through it."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from roboglot.glotfile.parser import parse_glot_text
from roboglot.glotfile.schema import VerbsSection
from roboglot.safety import Executor, allow_all
from roboglot.transport.mock import MockTransport
from roboglot.verbs.aliases import ALIASES, aliases_of, canonical
from roboglot.verbs.registry import NoParams, Verb, VerbContext, VerbRegistry, VerbResult


async def _ok(_ctx: VerbContext, _p: NoParams) -> VerbResult:
    return VerbResult.success("ok")


def _registry_with(*names: str, **kw: object) -> VerbRegistry:
    reg = VerbRegistry()
    for n in names:
        reg.register(Verb(n, f"{n} verb", _ok, NoParams, **kw))  # type: ignore[arg-type]
    return reg


def test_alias_table_is_the_three_renames() -> None:
    assert ALIASES == {"get_frame": "observe", "walk_to": "go_to", "walk": "move"}
    assert canonical("walk") == "move"
    assert canonical("kick") == "kick"  # identity for non-aliases
    assert aliases_of("move") == ["walk"]
    assert aliases_of("kick") == []


def test_alias_resolves_only_when_the_canonical_verb_exists() -> None:
    reg = _registry_with("move", "stop")
    assert reg.canonical("walk") == "move"
    assert reg.get("walk") is reg.get("move")
    assert "walk" in reg and "move" in reg and "fly" not in reg
    assert reg.unknown(["walk", "move", "fly"]) == ["fly"]
    assert reg.aliases() == {"walk": "move"}
    assert reg.names() == ["move", "stop"]  # canonical only
    # an alias whose canonical is absent stays unknown rather than being remapped
    assert reg.canonical("get_frame") == "get_frame"
    assert reg.unknown(["get_frame"]) == ["get_frame"]


def test_view_shows_the_name_the_caller_used() -> None:
    reg = _registry_with("move")
    assert reg.view("move").name == "move"
    assert reg.view("walk").name == "walk"
    assert reg.view("walk").execute is reg.get("move").execute
    assert reg.get("walk").name == "move"
    assert [t["name"] for t in reg.tool_schemas(["walk"])] == ["walk"]
    assert [t["name"] for t in reg.tool_schemas(["move"])] == ["move"]


def test_registering_an_alias_name_is_refused() -> None:
    with pytest.raises(ValueError, match="not its alias"):
        _registry_with("walk")


def test_same_verb_pairs() -> None:
    reg = _registry_with("move", "go_to")
    assert reg.same_verb(["walk", "move", "go_to"]) == [("walk", "move")]
    assert reg.same_verb(["walk", "go_to"]) == []


async def test_executor_allowlist_and_gates_are_alias_aware() -> None:
    reg = _registry_with("move", "stop")
    duck = parse_glot_text(
        "---\nglot: 0\nname: t\ndescription: d\nverbs:\n  allow: [walk]\n"
        "success: [x]\n---\n# T\nx\n",
        "t.glot",
    )
    ex = Executor(registry=reg, transport=MockTransport(), contract=duck.frontmatter)
    assert ex.allowed == ["walk"]  # the contract's own spelling, verbatim
    assert ex.is_allowed("walk") and ex.is_allowed("move") and ex.is_allowed("stop")
    assert not ex.is_allowed("fly")
    assert (await ex.run_verb("move")).ok
    assert (await ex.run_verb("walk")).ok
    assert ex.consecutive_failures == {"move": 0}  # one counter for both spellings


async def test_confirm_list_resolves_aliases_and_never_gates_stop() -> None:
    reg = _registry_with("move")
    reg.register(Verb("stop", "stop", _ok, NoParams, safety_class="confirm"))
    duck = parse_glot_text(
        "---\nglot: 0\nname: t\ndescription: d\nverbs:\n  allow: [move, stop]\n"
        "  confirm: [walk]\nsuccess: [x]\n---\n# T\nx\n",
        "t.glot",
    )
    ex = Executor(registry=reg, transport=MockTransport(), contract=duck.frontmatter)
    assert ex.needs_confirm(reg.get("move"))
    assert not ex.needs_confirm(reg.get("stop"))  # even with a confirm-class stop verb
    ex.confirm = allow_all
    assert (await ex.run_verb("stop")).ok


def test_duck_rejects_an_alias_next_to_its_canonical_and_stop_in_confirm() -> None:
    with pytest.raises(ValidationError, match="same verb"):
        VerbsSection(allow=["walk", "move"])
    with pytest.raises(ValidationError, match="never be confirm-gated"):
        VerbsSection(allow=["stop"], confirm=["stop"])
    section = VerbsSection(allow=["walk_to"], confirm=["go_to"])  # alias-aware subset check
    assert section.confirm == ["go_to"]
