"""Write the Robot Passport test vectors in `docs/passport/vectors/`, or check they are current.

The vectors are the spec's conformance suite: passports, signed passports and talk messages,
each with the result a reader must reach. This script makes them from five published test keys,
and it signs with `cryptography` directly rather than through `quackd.passport`, so the module is
checked against a second implementation of the same rules rather than against itself. Ed25519
signatures are deterministic, so the same keys always write the same files.

    uv run python scripts/passport_vectors.py          # write them
    uv run python scripts/passport_vectors.py --check  # exit 1 if any file would change
"""

from __future__ import annotations

import base64
import hashlib
import json
import struct
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "docs" / "passport" / "vectors"

#: The instant every time-dependent vector is judged at.
REFERENCE = datetime(2026, 10, 29, tzinfo=UTC)
NOW = int(REFERENCE.timestamp())
#: When the good passports were signed and until when their owners' signatures hold.
ISSUED = "2026-10-28T00:00:00Z"
EXPIRES = "2027-10-28T00:00:00Z"

#: Test keys. Published, so never a real robot's or a real owner's.
SEEDS = {
    "robot-a": bytes(range(0, 32)),
    "owner": bytes(range(32, 64)),
    "robot-b": bytes(range(64, 96)),
    "intruder": bytes(range(96, 128)),
    "owner-b": bytes(range(128, 160)),
}

VERSION = "1.0"
PASSPORT_TYP = "robot-passport+jws"
MESSAGE_TYP = "robot-passport-msg+jws"

_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def b58encode(data: bytes) -> str:
    number = int.from_bytes(data, "big")
    out = ""
    while number:
        number, rest = divmod(number, 58)
        out = _B58[rest] + out
    return "1" * (len(data) - len(data.lstrip(b"\0"))) + out


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def compact(value: Any) -> bytes:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


class Key:
    def __init__(self, seed: bytes) -> None:
        self.private = Ed25519PrivateKey.from_private_bytes(seed)
        self.public = self.private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        self.did = "did:key:z" + b58encode(b"\xed\x01" + self.public)
        self.short_id = hashlib.sha256(self.public).digest()[:8].hex()

    def jws(self, payload: bytes, typ: str, *, alg: str = "Ed25519", kid: str | None = None) -> str:
        header = {"alg": alg, "kid": kid if kid is not None else self.did, "typ": typ}
        signing_input = b64url(compact(header)) + "." + b64url(payload)
        signature = self.private.sign(signing_input.encode("ascii"))
        return signing_input + "." + b64url(signature)


KEYS = {name: Key(seed) for name, seed in SEEDS.items()}
A, OWNER, B = KEYS["robot-a"], KEYS["owner"], KEYS["robot-b"]
INTRUDER, OWNER_B = KEYS["intruder"], KEYS["owner-b"]

#: What each robot's hellos say beside its passport: no owner signs these (spec 6.3).
RUNTIME_A = {"software": "example/1.0", "beacon": "a1a1a1a1a1a1a1a1", "reach": {"http_port": 8790}}
RUNTIME_B = {"software": "example/1.0", "beacon": "b2b2b2b2b2b2b2b2", "reach": {"http_port": 8790}}


def nonce(label: str) -> str:
    """A fixed 16-byte nonce, so the talk vectors are reproducible. Real nonces are random."""
    return b64url(hashlib.sha256(label.encode()).digest()[:16])


def minimal(key: Key, name: str, owner: Key = OWNER) -> dict[str, Any]:
    return {
        "passport": VERSION,
        "id": key.did,
        "name": name,
        "owner": {"id": owner.did},
        "issued": ISSUED,
        "expires": EXPIRES,
    }


def humanoid(key: Key) -> dict[str, Any]:
    """The full example from passport.md, for robot A: a humanoid as a simulation portrays it,
    with the figures its maker published and the board saying it is a stand-in."""
    source = "unitree.com/g1, read 2026-10-28"
    return {
        "passport": VERSION,
        "id": key.did,
        "name": "g1-edu-mock",
        "maker": {"vendor": "unitree", "model": "g1-edu"},
        "owner": {
            "id": OWNER.did,
            "label": "Example lab",
            "contact": "https://example.org/robots/g1-edu-mock",
        },
        "board": {"kind": "sim", "model": "a computer standing in for a robot's own"},
        "body": {
            "embodiment": "humanoid",
            "mobility": "legged",
            "manipulator": "arms",
            "terrain": "indoor",
            "tethered": False,
            "mass_kg": [35, "official", source],
            "height_m": [1.32, "official", source],
            "sensors": ["camera", "lidar", "microphone", "battery"],
        },
        "skills": ["report_state", "stop"],
        "courtesy": [],
        "cannot": ["move: a mock on a laptop, and no G1 is here"],
        "safety": {
            "stops_on_silence": False,
            "estop": "none",
            "how_to_stop": "Simulated: stop the process.",
        },
        "issued": ISSUED,
        "expires": EXPIRES,
    }


def vectors() -> dict[str, str]:
    """Every vector file, by name, as the exact text written."""
    files: dict[str, str] = {}

    def doc(name: str, value: dict[str, Any]) -> None:
        files[name] = json.dumps(value, indent=2, ensure_ascii=False) + "\n"

    # ── documents ────────────────────────────────────────────────────────────────────────
    doc("minimal.json", minimal(A, "g1-edu-01"))
    doc("humanoid.json", humanoid(A))
    doc("unknown-member.json", {**minimal(A, "g1-edu-01"), "battery_swap": {"slots": 2}})
    unknown_enum = humanoid(A)
    unknown_enum["body"] = {"embodiment": "hexapod", "mobility": "legged", "sensors": ["sonar"]}
    unknown_enum["board"] = {"kind": "fpga-cluster", "model": "an unknown computer"}
    doc("unknown-enum.json", unknown_enum)
    doc(
        "unknown-safety.json",
        {**minimal(A, "g1-edu-01"), "safety": {"stops_on_silence": "yes", "estop": "teleport"}},
    )
    doc(
        "required-extension.json",
        {**minimal(A, "g1-edu-01"), "ext": {"org.example.lift": {"required": True, "floors": 3}}},
    )
    missing_name = minimal(A, "g1-edu-01")
    del missing_name["name"]
    doc("missing-name.json", missing_name)
    missing_owner = minimal(A, "g1-edu-01")
    del missing_owner["owner"]
    doc("missing-owner.json", missing_owner)
    missing_expires = minimal(A, "g1-edu-01")
    del missing_expires["expires"]
    doc("missing-expires.json", missing_expires)
    doc("major-version.json", {**minimal(A, "g1-edu-01"), "passport": "2.0"})
    doc("old-version.json", {"passport": "0.1", "id": A.did, "name": "g1-edu-01"})
    doc("minor-version.json", {**minimal(A, "g1-edu-01"), "passport": "1.9", "aura": "calm"})
    # A duplicate member cannot be built from a dict, so this one is written by hand.
    files["duplicate-member.json"] = (
        '{\n  "passport": "1.0",\n  "id": "' + A.did + '",\n  "name": "g1-edu-01",\n'
        '  "name": "somebody-else",\n  "owner": {"id": "' + OWNER.did + '"},\n'
        f'  "issued": "{ISSUED}",\n  "expires": "{EXPIRES}"\n}}\n'
    )
    doc("long-name.json", minimal(A, "g" * 65))
    # Valid in every way but its size: the padding is an unknown member a reader would ignore.
    doc("oversized.json", {**minimal(A, "g1-edu-01"), "padding": "x" * 8200})
    doc(
        "bad-id.json",
        {**minimal(A, "g1-edu-01"), "id": "urn:uuid:4f1c2a1e-9d4b-4c8e-8f3a-2b6d5e7a9c10"},
    )
    doc("too-long-validity.json", {**minimal(A, "g1-edu-01"), "expires": "2027-12-28T00:00:00Z"})

    # ── signed passports ──────────────────────────────────────────────────────────────────
    files["humanoid.jws"] = OWNER.jws(compact(humanoid(A)), PASSPORT_TYP) + "\n"
    files["eddsa-label.jws"] = (
        OWNER.jws(compact(minimal(A, "g1-edu-01")), PASSPORT_TYP, alg="EdDSA") + "\n"
    )
    good = OWNER.jws(compact(minimal(A, "g1-edu-01")), PASSPORT_TYP)
    head, _body, sig = good.split(".")
    forged = b64url(compact({**minimal(A, "g1-edu-01"), "name": "g1-edu-02"}))
    files["tampered.jws"] = f"{head}.{forged}.{sig}\n"
    files["wrong-key.jws"] = (
        INTRUDER.jws(compact(minimal(A, "g1-edu-01")), PASSPORT_TYP, kid=OWNER.did) + "\n"
    )
    files["kid-mismatch.jws"] = OWNER_B.jws(compact(minimal(A, "g1-edu-01")), PASSPORT_TYP) + "\n"
    # how 0.1 signed: the robot's own key, which a 1.x reader refuses whatever owner.id says
    files["robot-signed.jws"] = A.jws(compact(minimal(A, "g1-edu-01")), PASSPORT_TYP) + "\n"
    files["same-key.jws"] = A.jws(compact(minimal(A, "g1-edu-01", owner=A)), PASSPORT_TYP) + "\n"
    expired = {**minimal(A, "g1-edu-01"), "issued": "2025-01-01T00:00:00Z"}
    expired["expires"] = "2026-01-01T00:00:00Z"
    files["expired.jws"] = OWNER.jws(compact(expired), PASSPORT_TYP) + "\n"
    early = {**minimal(A, "g1-edu-01"), "issued": "2026-11-01T00:00:00Z"}
    early["expires"] = "2027-11-01T00:00:00Z"
    files["not-yet-valid.jws"] = OWNER.jws(compact(early), PASSPORT_TYP) + "\n"

    # ── talk: one hello between A and B, then a session ───────────────────────────────────
    n_a, n_b = nonce("robot-a/session-1"), nonce("robot-b/session-1")
    passport_a = OWNER.jws(compact(minimal(A, "g1-edu-01")), PASSPORT_TYP)
    passport_b = OWNER_B.jws(compact(minimal(B, "agile-one-sn-01", owner=OWNER_B)), PASSPORT_TYP)

    def msg(sender: Key, value: dict[str, Any]) -> str:
        return sender.jws(compact({"passport": VERSION, **value}), MESSAGE_TYP) + "\n"

    files["hello-1.jws"] = msg(
        A,
        {
            "type": "hello",
            "from": A.did,
            "to": B.did,
            "msg": nonce("msg-1"),
            "body": {"passport": passport_a, "nonce": n_a, **RUNTIME_A},
        },
    )
    files["hello-2.jws"] = msg(
        B,
        {
            "type": "hello",
            "from": B.did,
            "to": A.did,
            "msg": nonce("msg-2"),
            "re": nonce("msg-1"),
            "nonces": [n_a, n_b],
            "body": {"passport": passport_b, **RUNTIME_B},
        },
    )
    files["hello-3.jws"] = msg(
        A,
        {
            "type": "hello",
            "from": A.did,
            "to": B.did,
            "msg": nonce("msg-3"),
            "re": nonce("msg-2"),
            "nonces": [n_a, n_b],
            "body": {},
        },
    )
    files["say-in-session.jws"] = msg(
        B,
        {
            "type": "say",
            "from": B.did,
            "to": A.did,
            "msg": nonce("msg-4"),
            "nonces": [n_a, n_b],
            "body": {"text": "agile-one-sn-01 here, holding the door for you"},
        },
    )
    other_a, other_b = nonce("robot-a/session-0"), nonce("robot-b/session-0")
    files["say-replayed.jws"] = msg(
        B,
        {
            "type": "say",
            "from": B.did,
            "to": A.did,
            "msg": nonce("msg-0"),
            "nonces": [other_a, other_b],
            "body": {"text": "an old message from another session"},
        },
    )
    files["ask-pause.jws"] = msg(
        A,
        {
            "type": "ask",
            "from": A.did,
            "to": B.did,
            "msg": nonce("msg-5"),
            "nonces": [n_a, n_b],
            "body": {"skill": "pause", "args": {"seconds": 5}, "why": "passing on your left"},
        },
    )
    doc(
        "unsigned-say.json",
        {
            "passport": VERSION,
            "type": "say",
            "from": B.did,
            "to": A.did,
            "msg": nonce("msg-6"),
            "nonces": [n_a, n_b],
            "body": {"text": "anybody can write this, so a robot believes none of it"},
        },
    )
    files["hello-foreign-passport.jws"] = msg(
        A,
        {
            "type": "hello",
            "from": A.did,
            "to": B.did,
            "msg": nonce("msg-7"),
            "body": {"passport": passport_b, "nonce": n_a, **RUNTIME_A},
        },
    )

    # ── the keys themselves ───────────────────────────────────────────────────────────────
    doc(
        "keys.json",
        {
            "warning": "TEST KEYS. They are published, so they must never sign for a real robot.",
            "reference_time": REFERENCE.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "keys": {
                name: {
                    "seed_hex": SEEDS[name].hex(),
                    "public_hex": key.public.hex(),
                    "did": key.did,
                    "short_id": key.short_id,
                }
                for name, key in KEYS.items()
            },
            "session": {"nonce_a": n_a, "nonce_b": n_b},
            "runtime": {"robot-a": RUNTIME_A, "robot-b": RUNTIME_B},
        },
    )
    return files


# ── Bluetooth LE (spec 5.6): an advert, and the frames of a link ────────────────────────────

SPEC_URL = "https://github.com/rokbenko/quackd/blob/main/docs/passport/spec.md"
#: Beacon ids are 8 random bytes in a real advert, and these are fixed so the vectors are too.
#: Robot B's is the lower, so it is B that dials A once it hears A's advert.
BEACON_A = bytes(range(8, 16))
BEACON_B = bytes(range(8))
LINK_TAG = 0x5EED0001
FLAG_LAN, FLAG_LINK = 0x01, 0x02


def ble_uuid(name: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"{SPEC_URL}#{name}")


def frame(kind: int, body: bytes) -> str:
    """A link frame: framing 1 and its kind in one byte, the link's tag, then its body."""
    return (struct.pack(">BI", 0x10 | kind, LINK_TAG) + body).hex()


def ble_vectors() -> dict[str, str]:
    """Robot A's advert as the 31 bytes it puts on the air, and a link from B to A: one frame of
    each kind, and a request split into the 20-byte datagrams every link can carry."""
    service_data = bytes((1, FLAG_LAN | FLAG_LINK)) + BEACON_A
    service_ad = ble_uuid("ble-service").bytes[::-1] + service_data  # a UUID goes little-endian
    advert = bytes((2, 0x01, 0x06, 1 + len(service_ad), 0x21)) + service_ad
    hello = struct.pack(">HI", 182, 16_384)
    frames = [
        {
            "name": "open",
            "kind": "OPEN",
            "beacon_id": BEACON_B.hex(),
            "datagram_size": 182,
            "max_message": 16_384,
            "hex": frame(1, BEACON_B + hello),
        },
        {
            "name": "accept",
            "kind": "ACCEPT",
            "beacon_id": BEACON_A.hex(),
            "datagram_size": 182,
            "max_message": 16_384,
            "hex": frame(2, BEACON_A + hello),
        },
        {"name": "refuse-busy", "kind": "REFUSE", "reason": 1, "hex": frame(3, bytes((1,)))},
        {
            "name": "data-response-204",
            "kind": "DATA",
            "msg_id": 1,
            "seq": 0,
            "role": 1,
            "status": 204,
            "media": 0,
            "total": 0,
            "chunk_hex": "",
            "hex": frame(4, struct.pack(">HHBHBI", 1, 0, 1, 204, 0, 0)),
        },
        {
            "name": "abort-out-of-order",
            "kind": "ABORT",
            "msg_id": 0x8002,
            "reason": 2,
            "hex": frame(5, struct.pack(">HB", 0x8002, 2)),
        },
        {"name": "close-far", "kind": "CLOSE", "reason": 1, "hex": frame(6, bytes((1,)))},
    ]
    body = b"a hello, split into frames"
    first, later = 20 - 5 - 4 - 8, 20 - 5 - 4
    datagrams = [frame(4, struct.pack(">HHBHBI", 1, 0, 0, 0, 1, len(body)) + body[:first])]
    for seq, at in enumerate(range(first, len(body), later), start=1):
        datagrams.append(frame(4, struct.pack(">HH", 1, seq) + body[at : at + later]))
    link = {
        "warning": "TEST VALUES. A real beacon id is random and a real tag is drawn per link.",
        "uuids": {name: str(ble_uuid(name)) for name in ("ble-service", "ble-rx", "ble-tx")},
        "advert": {
            "file": "ble-advert.hex",
            "beacon_id": BEACON_A.hex(),
            "flags": ["LAN", "LINK"],
        },
        "tag": LINK_TAG,
        "frames": frames,
        "message": {
            "datagram_size": 20,
            "msg_id": 1,
            "role": 0,
            "status": 0,
            "media": 1,
            "body": body.decode("ascii"),
            "datagrams": datagrams,
        },
    }
    return {
        "ble-advert.hex": advert.hex() + "\n",
        "ble-link.json": json.dumps(link, indent=2) + "\n",
    }


def main(argv: list[str]) -> int:
    check = "--check" in argv
    stale = []
    for name, text in {**vectors(), **ble_vectors()}.items():
        path = OUT / name
        if check:
            if not path.exists() or path.read_text(encoding="utf-8") != text:
                stale.append(name)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8", newline="\n")
    if stale:
        print("stale vectors: " + ", ".join(stale))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
