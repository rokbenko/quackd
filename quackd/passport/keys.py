"""A robot's key: Ed25519, written as a `did:key`, with the short id beacons carry.

Spec sections 4.1 and 4.2. Ed25519 comes from `cryptography`; base58btc is written out here,
because the standard library has none and the alphabet is all there is to it."""

from __future__ import annotations

import hashlib
import os

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from quackd.passport.errors import PassportError

_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_INDEX = {c: i for i, c in enumerate(_ALPHABET)}

#: The multicodec of an Ed25519 public key, as the two bytes a `did:key` puts before it.
ED25519_PUB = b"\xed\x01"
DID_PREFIX = "did:key:z"
SEED_BYTES = 32


def b58encode(data: bytes) -> str:
    number = int.from_bytes(data, "big")
    out = ""
    while number:
        number, rest = divmod(number, 58)
        out = _ALPHABET[rest] + out
    return "1" * (len(data) - len(data.lstrip(b"\0"))) + out


def b58decode(text: str) -> bytes:
    number = 0
    for ch in text:
        if ch not in _INDEX:
            raise ValueError(f"{ch!r} is not a base58btc character")
        number = number * 58 + _INDEX[ch]
    body = number.to_bytes((number.bit_length() + 7) // 8, "big") if number else b""
    return b"\0" * (len(text) - len(text.lstrip("1"))) + body


def did_from_public(public: bytes) -> str:
    return DID_PREFIX + b58encode(ED25519_PUB + public)


def public_from_did(did: str) -> bytes:
    """The 32-byte Ed25519 key a `did:key` holds, or a refusal under rule 7.

    A `did:key` that decodes to the same key but is spelled differently is refused as well: one
    key has one spelling, so comparing two ids as strings is comparing two keys."""
    if not isinstance(did, str) or not did.startswith(DID_PREFIX):
        raise PassportError("rule 7", f"{did!r} is not a did:key")
    try:
        raw = b58decode(did[len(DID_PREFIX) :])
    except ValueError as e:
        raise PassportError("rule 7", f"{did!r} is not base58btc: {e}") from e
    if len(raw) != 2 + 32 or not raw.startswith(ED25519_PUB):
        raise PassportError("rule 7", f"{did!r} holds something other than an Ed25519 key")
    public = raw[2:]
    if did_from_public(public) != did:
        raise PassportError("rule 7", f"{did!r} is not the canonical spelling of its key")
    return public


def short_id(public: bytes) -> str:
    """The first 8 bytes of the SHA-256 of the key, as 16 hex characters (spec 4.2)."""
    return hashlib.sha256(public).digest()[:8].hex()


def short_id_of(robot_id: str) -> str | None:
    """A passport id's short id, or None for a `urn:uuid`, which holds no key."""
    if robot_id.startswith(DID_PREFIX):
        return short_id(public_from_did(robot_id))
    return None


def verify(public: bytes, signature: bytes, data: bytes) -> bool:
    try:
        Ed25519PublicKey.from_public_bytes(public).verify(signature, data)
    except (InvalidSignature, ValueError):
        return False
    return True


class SigningKey:
    """A private key, held as its 32-byte seed, which is everything there is to store."""

    def __init__(self, seed: bytes) -> None:
        if len(seed) != SEED_BYTES:
            raise ValueError(f"an Ed25519 seed is {SEED_BYTES} bytes, not {len(seed)}")
        self.seed = bytes(seed)
        self._private = Ed25519PrivateKey.from_private_bytes(self.seed)
        self.public = self._private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        self.did = did_from_public(self.public)
        self.short_id = short_id(self.public)

    @classmethod
    def generate(cls) -> SigningKey:
        return cls(os.urandom(SEED_BYTES))

    def sign(self, data: bytes) -> bytes:
        return self._private.sign(data)

    def __repr__(self) -> str:
        # never the seed: a key in a traceback or a log line must not be the key itself
        return f"SigningKey({self.did})"
