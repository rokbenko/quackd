"""Keys and passports for the Robot Passport tests: the vectors' published test keys, and a 1.0
passport signed by an owner the way quackd signs one, valid from yesterday for a year."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from quackd.passport import SigningKey, document

VECTORS = Path(__file__).resolve().parents[1] / "docs" / "passport" / "vectors"
KEYS = json.loads((VECTORS / "keys.json").read_text(encoding="utf-8"))["keys"]
#: When the vectors are judged, and what each vector robot's hellos say beside its passport.
REFERENCE = int(
    datetime.fromisoformat(
        json.loads((VECTORS / "keys.json").read_text(encoding="utf-8"))["reference_time"].replace(
            "Z", "+00:00"
        )
    ).timestamp()
)
RUNTIME = json.loads((VECTORS / "keys.json").read_text(encoding="utf-8"))["runtime"]


def key(name: str) -> SigningKey:
    return SigningKey(bytes.fromhex(KEYS[name]["seed_hex"]))


A, B, OWNER, OWNER_B, INTRUDER = (
    key("robot-a"),
    key("robot-b"),
    key("owner"),
    key("owner-b"),
    key("intruder"),
)


def _stamp(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def doc(
    robot: SigningKey, owner: SigningKey = OWNER, name: str = "g1-edu-01", **members: Any
) -> dict[str, Any]:
    """A 1.0 passport for `robot`, owned by `owner`, issued yesterday and good for a year."""
    now = datetime.now(UTC).replace(microsecond=0)
    out: dict[str, Any] = {
        "passport": document.VERSION,
        "id": robot.did,
        "name": name,
        "owner": {"id": owner.did},
        "issued": _stamp(now - timedelta(days=1)),
        "expires": _stamp(now + timedelta(days=365)),
    }
    out.update(members)
    return out


def passport(
    robot: SigningKey, owner: SigningKey = OWNER, name: str = "g1-edu-01", **members: Any
) -> str:
    """That passport signed by its owner's key, as a robot carries it in its hello."""
    return document.sign(doc(robot, owner, name, **members), owner)
