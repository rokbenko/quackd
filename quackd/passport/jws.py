"""Compact JWS over exact bytes: the protocol's one signature format (spec 4.4, 4.5, 6.1).

The payload is signed as the bytes it is and carried unchanged, so nothing is canonicalised.
Every signature's protected header says what it is for (`typ`), and a reader checks it, so a key
that signs passports, stamps and messages can never have one passed off as another."""

from __future__ import annotations

import base64
import binascii
import re
from dataclasses import dataclass
from typing import Any

from quackd.passport import strict
from quackd.passport.errors import PassportError
from quackd.passport.keys import SigningKey, public_from_did, verify

#: `Ed25519` is RFC 9864's name; `EdDSA` is the older label most libraries still write.
ALGS = ("Ed25519", "EdDSA")
SIGNATURE_BYTES = 64
_B64URL = re.compile(r"^[A-Za-z0-9_-]*$")


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64url_decode(text: str, *, rule: str) -> bytes:
    if not _B64URL.match(text) or len(text) % 4 == 1:
        raise PassportError(rule, "a part of it is not base64url")
    try:
        return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except (binascii.Error, ValueError) as e:
        raise PassportError(rule, f"a part of it is not base64url: {e}") from e


def sign(payload: bytes, key: SigningKey, typ: str) -> str:
    """`payload` signed by `key` as a compact JWS whose header says it is a `typ`."""
    header = {"alg": "Ed25519", "kid": key.did, "typ": typ}
    signing_input = b64url(strict.dumps(header)) + "." + b64url(payload)
    return signing_input + "." + b64url(key.sign(signing_input.encode("ascii")))


@dataclass(frozen=True)
class Verified:
    """A JWS whose signature checked out: its header, the exact payload bytes, and its key."""

    header: dict[str, Any]
    payload: bytes
    kid: str


def looks_like_jws(text: str) -> bool:
    parts = text.strip().split(".")
    return len(parts) == 3 and all(_B64URL.match(p) and p for p in parts)


def verify_compact(token: str, typ: str, *, rule: str, payload_limit: int) -> Verified:
    """A compact JWS, checked: `alg`, `typ`, `kid`, no `crit`, and the signature itself.

    `rule` is the part of the specification a failure is reported under, since the same checks
    guard a passport (4.4), a stamp (4.5) and a message (6.1)."""
    token = token.strip()
    # base64url makes the payload a third longer; the header and signature add a few hundred
    if len(token) > payload_limit * 4 // 3 + 1024:
        raise PassportError("rule 1", f"{len(token)} characters, too long to hold what it may")
    parts = token.split(".")
    if len(parts) != 3:
        raise PassportError(rule, "it is not a compact JWS, which has three parts")
    head, body, sig = parts
    header = strict.loads(b64url_decode(head, rule=rule), limit=1024)
    if not isinstance(header, dict):
        raise PassportError(rule, "its protected header is not a JSON object")
    if header.get("alg") not in ALGS:
        raise PassportError(rule, f"its alg is {header.get('alg')!r}, not Ed25519")
    if header.get("typ") != typ:
        raise PassportError(rule, f"its typ is {header.get('typ')!r}, where {typ!r} belongs")
    if "crit" in header:
        raise PassportError(rule, "its header has crit, which this protocol never uses")
    kid = header.get("kid")
    if not isinstance(kid, str):
        raise PassportError(rule, "its header names no kid")
    public = public_from_did(kid)
    signature = b64url_decode(sig, rule=rule)
    if len(signature) != SIGNATURE_BYTES:
        raise PassportError(rule, f"its signature is {len(signature)} bytes, not 64")
    if not verify(public, signature, f"{head}.{body}".encode("ascii")):
        raise PassportError(rule, f"the signature does not verify with the key {kid}")
    return Verified(header, b64url_decode(body, rule=rule), kid)
