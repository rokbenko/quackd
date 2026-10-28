"""The JSON Schemas, the module and the pages agree about what a passport is.

The schema is what another implementer reads first, the module is what quackd runs, and
passport.md and the spec are what people read. Each test here fails the day one of them learns
a member the others do not know.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from quackd.passport import PassportError, document, jws
from tests.passport_helpers import REFERENCE

REPO = Path(__file__).resolve().parents[1]
PASSPORT_DIR = REPO / "docs" / "passport"
SCHEMAS = {
    name: Draft202012Validator(
        json.loads((PASSPORT_DIR / "schema" / f"{name}.schema.json").read_text(encoding="utf-8"))
    )
    for name in ("passport", "message")
}
VECTORS = PASSPORT_DIR / "vectors"
#: Vectors whose fault no schema can see: a reader refuses them for size, duplicates, or dates
#: that are each well formed but too far apart.
BEYOND_A_SCHEMA = {"oversized.json", "duplicate-member.json", "too-long-validity.json"}
#: Talk messages, which `message.schema.json` describes and test_passport_vectors reads.
MESSAGES = {"unsigned-say.json"}


def test_every_schema_is_a_valid_2020_12_schema() -> None:
    for validator in SCHEMAS.values():
        Draft202012Validator.check_schema(validator.schema)


@pytest.mark.parametrize(
    "name", sorted(p.name for p in VECTORS.glob("*.json") if p.name not in {"keys.json"})
)
def test_the_schema_and_the_module_agree_on_every_json_vector(name: str) -> None:
    text = (VECTORS / name).read_text(encoding="utf-8")
    if name in BEYOND_A_SCHEMA or name in MESSAGES:
        return
    value = json.loads(text)
    schema_says = not list(SCHEMAS["passport"].iter_errors(value))
    try:
        document.read(text.encode("utf-8"), now=REFERENCE)
        module_says = True
    except PassportError:
        module_says = False
    assert schema_says == module_says, f"{name}: schema {schema_says}, module {module_says}"


def test_the_module_knows_every_member_the_schema_names_and_no_other() -> None:
    props = SCHEMAS["passport"].schema["properties"]
    assert set(props) == set(document._MEMBERS[""])
    for section in ("maker", "owner", "board", "body", "safety"):
        assert set(props[section]["properties"]) == set(document._MEMBERS[section]), section


def _members_named(page: str) -> set[str]:
    text = (PASSPORT_DIR / page).read_text(encoding="utf-8")
    return set(re.findall(r"`([a-z_]+)`", text))


def test_the_spec_and_the_passport_page_name_every_member() -> None:
    props = SCHEMAS["passport"].schema["properties"]
    body = props["body"]["properties"]
    for page in ("spec.md", "passport.md"):
        named = _members_named(page)
        missing = (set(props) | set(body)) - named
        assert not missing, f"docs/passport/{page} never names {sorted(missing)}"


def test_the_vocabularies_in_the_spec_are_the_modules() -> None:
    spec = (PASSPORT_DIR / "spec.md").read_text(encoding="utf-8")
    for values in (
        document.EMBODIMENTS,
        document.MOBILITIES,
        document.MANIPULATORS,
        document.CORE_SENSORS,
        document.CORE_SKILLS,
        document.COURTESIES,
        document.ESTOPS,
        document.BOARDS,
    ):
        for value in values:
            assert f"`{value}`" in spec, f"spec.md never names `{value}`"


@pytest.mark.parametrize(
    "name",
    sorted(p.name for p in VECTORS.glob("*.jws") if p.name.startswith(("hello", "say", "ask"))),
)
def test_every_talk_vector_fits_the_message_schema(name: str) -> None:
    """The message schema is what another implementer builds a hello from, so every signed
    message the vectors hold, the hello's runtime members included, has to fit it."""
    token = (VECTORS / name).read_text(encoding="utf-8").strip()
    payload = json.loads(jws.b64url_decode(token.split(".")[1], rule="6.1"))
    errors = [e.message for e in SCHEMAS["message"].iter_errors(payload)]
    assert not errors, f"{name}: {errors}"
