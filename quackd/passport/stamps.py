"""Stamps: one key vouching that another key is its robot (spec 4.5).

A stamp vouches for a key, never for a passport's contents, so a robot can change its passport
without a new one. Whether to trust the key that stamped is the reader's decision, made by a
person pinning it, and nothing in a stamp can make that decision for them."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TypeGuard

from quackd.passport import jws, strict
from quackd.passport.errors import PassportError
from quackd.passport.keys import SigningKey, public_from_did

STAMP_TYP = "robot-passport-stamp+jwt"
#: The relations a 0.1 reader counts. `maker` is reserved, and a stamp with it is ignored.
RELATIONS = ("owner",)
#: How far ahead of the reader's clock a stamp may say it was issued.
SKEW_S = 300
#: What quackd stamps for unless told otherwise, the year the specification recommends at most.
DEFAULT_DAYS = 365
STAMP_LIMIT = 1024


def make(
    signer: SigningKey, subject: str, *, days: int = DEFAULT_DAYS, now: int | None = None
) -> str:
    """A stamp by `signer` saying `subject` is its robot, valid for `days` from `now`."""
    public_from_did(subject)
    issued = int(time.time()) if now is None else now
    claims = {
        "iss": signer.did,
        "sub": subject,
        "rel": "owner",
        "iat": issued,
        "exp": issued + days * 86_400,
    }
    return jws.sign(strict.dumps(claims), signer, STAMP_TYP)


@dataclass(frozen=True)
class Stamp:
    """One stamp as a reader judged it. `counts` is false with a `reason` when it does not."""

    token: str
    counts: bool
    reason: str | None = None
    iss: str | None = None
    rel: str | None = None
    iat: int | None = None
    exp: int | None = None


def check(token: str, *, subject: str, now: int | None = None) -> Stamp:
    """Whether `token` counts as a stamp on the robot whose id is `subject`."""
    moment = int(time.time()) if now is None else now
    try:
        verified = jws.verify_compact(token, STAMP_TYP, rule="4.5", payload_limit=STAMP_LIMIT)
        claims = strict.loads(verified.payload, limit=STAMP_LIMIT)
    except PassportError as e:
        return Stamp(token, False, str(e))
    if not isinstance(claims, dict):
        return Stamp(token, False, "its claims are not a JSON object")
    iss, sub, rel = claims.get("iss"), claims.get("sub"), claims.get("rel")
    iat, exp = claims.get("iat"), claims.get("exp")

    def judged(reason: str | None) -> Stamp:
        return Stamp(
            token,
            reason is None,
            reason,
            iss=iss if isinstance(iss, str) else None,
            rel=rel if isinstance(rel, str) else None,
            iat=iat if _is_int(iat) else None,
            exp=exp if _is_int(exp) else None,
        )

    if iss != verified.kid:
        return judged("its iss is not the key that signed it")
    if sub != subject:
        return judged(f"it stamps {sub!r}, not this robot")
    if rel not in RELATIONS:
        return judged(f"its rel is {rel!r}, which a 0.1 reader ignores")
    if (
        not isinstance(iat, int)
        or not isinstance(exp, int)
        or isinstance(iat, bool)
        or isinstance(exp, bool)
        or exp <= iat
    ):
        return judged("its iat and exp are not two times in order")
    if exp <= moment:
        return judged("it has expired")
    if iat > moment + SKEW_S:
        return judged("it says it was issued in the future")
    return judged(None)


def _is_int(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool)
