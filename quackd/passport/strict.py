"""JSON the way a passport reader takes it: bounded, UTF-8, and with no duplicate members.

Python's `json` keeps the last of two members with the same name, another parser keeps the first,
and a signature checked by one would then vouch for a document the other reads differently. So a
duplicate at any depth is refused (spec rule 2), and so are the `NaN` and `Infinity` the standard
library accepts and JSON does not."""

from __future__ import annotations

import json
import math
import re
from typing import Any

from quackd.passport.errors import PassportError

_BOM = b"\xef\xbb\xbf"


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise PassportError("rule 2", f"the member {key!r} appears twice")
        out[key] = value
    return out


def _no_constants(name: str) -> Any:
    raise PassportError("rule 2", f"{name} is not JSON")


def _number(text: str) -> float | int:
    """A number written with a fraction or an exponent. A whole one is the integer it equals,
    the way JSON Schema reads it, so `3600.0` and `3.6e3` are good wherever an integer belongs.
    One past what a double holds is refused, as I-JSON (RFC 7493) allows."""
    value = float(text)
    if not math.isfinite(value):
        raise PassportError("rule 2", f"{text} is a number too large for JSON to carry")
    if value.is_integer() and abs(value) <= 2**53:
        return int(value)
    return value


#: Half a surrogate pair: a code point no UTF-8 can carry, which only JSON's escapes can name.
_SURROGATE = re.compile("[\ud800-\udfff]")


def _integer(text: str) -> int:
    """An integer, if a double can hold it exactly. Python's own ints have no limit, and one of
    four hundred digits is valid JSON that no other reader agrees on, and that crashes arithmetic
    expecting a figure, so it is refused, as I-JSON (RFC 7493) allows."""
    if len(text.lstrip("-")) > 16 or abs(value := int(text)) > 2**53:
        raise PassportError("rule 2", f"{text[:20]} is a number too large for JSON to carry")
    return value


def loads(data: bytes, *, limit: int) -> Any:
    """`data` parsed, or a `PassportError` saying which rule it broke."""
    if len(data) > limit:
        raise PassportError("rule 1", f"{len(data)} bytes, over the {limit} a reader accepts")
    if data.startswith(_BOM):
        raise PassportError("rule 2", "it starts with a byte order mark")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as e:
        raise PassportError("rule 2", f"it is not UTF-8 ({e.reason} at byte {e.start})") from e
    try:
        return _no_surrogates(
            json.loads(
                text,
                object_pairs_hook=_no_duplicates,
                parse_constant=_no_constants,
                parse_float=_number,
                parse_int=_integer,
            )
        )
    except PassportError:
        raise
    except RecursionError as e:
        raise PassportError("rule 2", "it is nested too deeply to read") from e
    except ValueError as e:
        raise PassportError("rule 2", f"it is not JSON: {e}") from e


def _no_surrogates(value: Any) -> Any:
    """`value`, once no string in it holds half a surrogate pair: JSON's `\\ud800` escape decodes
    to one, which I-JSON (RFC 7493) refuses, and which no file writes as UTF-8, so a stranger's
    one word of it ended the run whose record tried to."""
    stack = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, str):
            if _SURROGATE.search(item):
                raise PassportError(
                    "rule 2", "it holds half a surrogate pair, which I-JSON refuses"
                )
        elif isinstance(item, dict):
            stack.extend(item)
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)
    return value


def dumps(value: Any) -> bytes:
    """The compact bytes quackd writes: no spaces, members in the order given, UTF-8."""
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
