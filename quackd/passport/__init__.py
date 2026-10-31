"""Robot Passport: how a robot introduces itself to one it does not know.

The protocol's normative text is `docs/passport/spec.md`, and this package is quackd's
implementation of it. It imports nothing else from quackd, and a test holds it to that, because
the protocol is written to leave quackd one day and this package is meant to leave with it
whole. It needs the standard library and `cryptography`, which quackd already depends on.

- `document`: reading a passport (the rules of spec section 3) and the view a reader shows.
- `keys`: Ed25519 keys as `did:key`s, and short ids.
- `jws`: compact JWS over exact bytes, the one signature format.
- `strict`: JSON with no duplicate members, bounded.
- `talk`: messages, and the three-step hello in which two robots check each other.
"""

from __future__ import annotations

from quackd.passport.document import (
    MAX_BYTES,
    PASSPORT_TYP,
    VERSION,
    Reading,
    read,
    read_json,
    read_jws,
    sign,
)
from quackd.passport.errors import PassportError
from quackd.passport.keys import SigningKey, did_from_public, public_from_did, short_id_of

__all__ = [
    "MAX_BYTES",
    "PASSPORT_TYP",
    "VERSION",
    "PassportError",
    "Reading",
    "SigningKey",
    "did_from_public",
    "public_from_did",
    "read",
    "read_json",
    "read_jws",
    "short_id_of",
    "sign",
]
