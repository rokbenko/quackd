"""What quackd keeps on disk for Robot Passport: each robot's key, its owner, and pins.

It all lives under the registry directory (`--registry-dir`, `$QUACKD_REGISTRY_DIR`,
`~/.quackd`): the keys and what the owner said under `keys/`, and the robots and owners a person
chose to trust in `known_robots.json`, a file of its own so that an older quackd still reads
`robots.json`. A key file is written the way `robots.json` is, its mode set on the temporary file
before the secret goes in, so the key is never readable by others for the length of a write.
That is a no-op on Windows, and SECURITY.md calls it a file in a home directory rather than a
secret store, which is what it is.
"""

from __future__ import annotations

import contextlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from quackd.passport.document import ESTOPS
from quackd.passport.keys import SigningKey
from quackd.registry import registry_dir

KEYS_DIR = "keys"
ROBOTS_DIR = "robots"
OWNER_FILE = "owner.json"
KNOWN_FILE = "known_robots.json"
VERSION = 1
PIN_KINDS = ("robot", "owner")

#: A registered name, or the slug a bare `adapter:backend` keys its memory by.
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


class StoreError(Exception):
    """A key file that cannot be read, or a name that cannot be a file."""


@dataclass
class RobotRecord:
    """One robot's key, and what its owner said about stopping it."""

    key: SigningKey
    estop: str | None = None
    how_to_stop: str | None = None
    stamps: list[str] = field(default_factory=list)


@dataclass
class OwnerRecord:
    """Whoever owns the robots this quackd drives, as they want to be named in a passport, and
    the owner key that stamps those robots, once there is one."""

    label: str | None = None
    contact: str | None = None
    key: SigningKey | None = field(default=None, compare=False)
    stranger_pauses: bool = True
    """Whether a stranger may ask these robots for a pause at all; an owner can turn it off
    (docs/passport/trust.md#the-four-tiers)."""
    courtesy: tuple[str, ...] = ("pause", "identify")
    """What a robot stamped by this owner may ask these robots for: the owner's own list."""


@dataclass(frozen=True)
class Pin:
    """A key a person decided to trust: a robot's own, or an owner's, which covers every robot
    that owner stamps (docs/passport/trust.md#pinning-trust-on-first-use)."""

    id: str
    name: str
    kind: str
    pinned: str


class PassportStore:
    """The files, under one directory. Every call re-reads what it needs."""

    def __init__(self, base_dir: str | Path | None = None) -> None:
        self.base = registry_dir(base_dir)
        self.keys = self.base / KEYS_DIR

    # ── robots ──────────────────────────────────────────────────────────────────────

    def robot_path(self, name: str) -> Path:
        if not _NAME_RE.match(name):
            raise StoreError(f"{name!r} cannot name a key file")
        return self.keys / ROBOTS_DIR / f"{name}.json"

    def robot(self, name: str) -> RobotRecord | None:
        path = self.robot_path(name)
        if not path.exists():
            return None
        raw = _read(path)
        try:
            key = SigningKey(bytes.fromhex(str(raw["seed"])))
        except (KeyError, ValueError) as e:
            raise StoreError(f"{path}: no readable seed: fix it or move it aside") from e
        if raw.get("did") not in (None, key.did):
            raise StoreError(f"{path}: its did is not the key its seed makes")
        estop = raw.get("estop")
        how = raw.get("how_to_stop")
        stamps = raw.get("stamps") or []
        return RobotRecord(
            key,
            estop if estop in ESTOPS else None,
            how if isinstance(how, str) and how else None,
            [s for s in stamps if isinstance(s, str)],
        )

    def robot_or_new(self, name: str) -> tuple[RobotRecord, bool]:
        """This robot's record, made with a fresh key the first time. The flag says which."""
        existing = self.robot(name)
        if existing is not None:
            return existing, False
        record = RobotRecord(SigningKey.generate())
        self.save_robot(name, record)
        return record, True

    def robots(self) -> dict[str, RobotRecord]:
        """Every robot this quackd has made a key for, by name."""
        folder = self.keys / ROBOTS_DIR
        if not folder.is_dir():
            return {}
        out: dict[str, RobotRecord] = {}
        for path in sorted(folder.glob("*.json")):
            record = self.robot(path.stem) if _NAME_RE.match(path.stem) else None
            if record is not None:
                out[path.stem] = record
        return out

    def save_robot(self, name: str, record: RobotRecord) -> None:
        payload: dict[str, Any] = {
            "version": VERSION,
            "did": record.key.did,
            "seed": record.key.seed.hex(),
        }
        if record.estop is not None:
            payload["estop"] = record.estop
        if record.how_to_stop:
            payload["how_to_stop"] = record.how_to_stop
        if record.stamps:
            payload["stamps"] = record.stamps
        _write_secret(self.robot_path(name), payload)

    # ── the owner ───────────────────────────────────────────────────────────────────

    @property
    def owner_path(self) -> Path:
        return self.keys / OWNER_FILE

    def owner(self) -> OwnerRecord:
        if not self.owner_path.exists():
            return OwnerRecord()
        raw = _read(self.owner_path)
        label, contact = raw.get("label"), raw.get("contact")
        key: SigningKey | None = None
        if "seed" in raw:
            try:
                key = SigningKey(bytes.fromhex(str(raw["seed"])))
            except ValueError as e:
                raise StoreError(
                    f"{self.owner_path}: no readable seed: fix it or move it aside"
                ) from e
            if raw.get("did") not in (None, key.did):
                raise StoreError(f"{self.owner_path}: its did is not the key its seed makes")
        listed = raw.get("courtesy", ["pause", "identify"])
        if not isinstance(listed, list) or not all(c in ("pause", "identify") for c in listed):
            raise StoreError(f"{self.owner_path}: courtesy is not a list of pause and identify")
        return OwnerRecord(
            label if isinstance(label, str) and label else None,
            contact if isinstance(contact, str) and contact else None,
            key,
            stranger_pauses=raw.get("stranger_pauses", True) is not False,
            courtesy=tuple(listed),
        )

    def save_owner(self, owner: OwnerRecord) -> None:
        payload: dict[str, Any] = dict(_read(self.owner_path)) if self.owner_path.exists() else {}
        payload["version"] = VERSION
        for name, value in (("label", owner.label), ("contact", owner.contact)):
            if value:
                payload[name] = value
            else:
                payload.pop(name, None)
        if owner.key is not None:
            payload["did"] = owner.key.did
            payload["seed"] = owner.key.seed.hex()
        payload["stranger_pauses"] = owner.stranger_pauses
        payload["courtesy"] = list(owner.courtesy)
        _write_secret(self.owner_path, payload)

    def owner_key_or_new(self) -> tuple[SigningKey, bool]:
        """The owner key, made the first time something needs to stamp. The flag says which."""
        owner = self.owner()
        if owner.key is not None:
            return owner.key, False
        owner.key = SigningKey.generate()
        self.save_owner(owner)
        return owner.key, True

    # ── pins ────────────────────────────────────────────────────────────────────────

    @property
    def known_path(self) -> Path:
        return self.base / KNOWN_FILE

    def known(self) -> list[Pin]:
        """Every pin, robots and owners, in the order they were made."""
        if not self.known_path.exists():
            return []
        raw = _read(self.known_path)
        pins = raw.get("pins") or {}
        if not isinstance(pins, dict):
            raise StoreError(f"{self.known_path}: pins must be an object of id -> pin")
        out: list[Pin] = []
        for pin_id, entry in pins.items():
            if not isinstance(entry, dict) or entry.get("kind") not in PIN_KINDS:
                raise StoreError(f"{self.known_path}: {pin_id} is not a robot or an owner pin")
            out.append(
                Pin(
                    pin_id,
                    str(entry.get("name", "")),
                    str(entry["kind"]),
                    str(entry.get("pinned", "")),
                )
            )
        return sorted(out, key=lambda p: p.pinned)

    def pin(self, pin: Pin) -> None:
        if pin.kind not in PIN_KINDS:
            raise StoreError(f"a pin is a robot or an owner, not {pin.kind!r}")
        pins = {p.id: p for p in self.known()}
        pins[pin.id] = pin
        self._write_pins(pins.values())

    def forget(self, which: str) -> list[Pin]:
        """Remove the pin whose id, short id or name is `which`, and say which went.

        A name or short id more than one pin answers to removes none of them, and the error
        lists them: two owners can share a label, and a robot can name itself after another's
        short id, and forgetting both is not what a person asking for one meant."""
        from quackd.passport.keys import short_id_of

        pins = self.known()
        gone = [p for p in pins if p.id == which] or [
            p for p in pins if which in (p.name, short_id_of(p.id))
        ]
        if len(gone) > 1:
            listed = ", ".join(f"{p.kind} {p.name} ({short_id_of(p.id) or p.id})" for p in gone)
            raise StoreError(f"{len(gone)} pins answer to {which!r}: {listed}. Name one by its id")
        if gone:
            self._write_pins([p for p in pins if p.id != gone[0].id])
        return gone

    def _write_pins(self, pins: Any) -> None:
        payload = {
            "version": VERSION,
            "pins": {p.id: {"name": p.name, "kind": p.kind, "pinned": p.pinned} for p in pins},
        }
        _write_secret(self.known_path, payload)


def _read(path: Path) -> dict[str, Any]:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise StoreError(f"{path}: not valid JSON ({e.msg} at line {e.lineno})") from e
    if not isinstance(loaded, dict):
        raise StoreError(f"{path}: expected an object, found {type(loaded).__name__}")
    version = loaded.get("version", VERSION)
    if not isinstance(version, int) or version > VERSION:
        raise StoreError(f"{path} is version {version}, newer than this quackd: upgrade quackd")
    return loaded


def _write_secret(path: Path, payload: dict[str, Any]) -> None:
    tmp = path.with_suffix(".json.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(OSError):
            tmp.touch(mode=0o600)
            tmp.chmod(0o600)
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        tmp.replace(path)
    except OSError as e:  # a full disk, a read-only folder, or on Windows a file held open
        with contextlib.suppress(OSError):
            tmp.unlink(missing_ok=True)
        raise StoreError(f"could not write {path}: {e.strerror or e}") from e
