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
VERSION = 1

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
    """Whoever owns the robots this quackd drives, as they want to be named in a passport."""

    label: str | None = None
    contact: str | None = None


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
        return OwnerRecord(
            label if isinstance(label, str) and label else None,
            contact if isinstance(contact, str) and contact else None,
        )

    def save_owner(self, owner: OwnerRecord) -> None:
        payload: dict[str, Any] = dict(_read(self.owner_path)) if self.owner_path.exists() else {}
        payload["version"] = VERSION
        for name, value in (("label", owner.label), ("contact", owner.contact)):
            if value:
                payload[name] = value
            else:
                payload.pop(name, None)
        _write_secret(self.owner_path, payload)


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
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    with contextlib.suppress(OSError):
        tmp.touch(mode=0o600)
        tmp.chmod(0o600)
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    try:
        tmp.replace(path)
    except PermissionError as e:  # Windows: another program is holding the file open
        tmp.unlink(missing_ok=True)
        raise StoreError(f"could not replace {path}: another program has it open") from e
