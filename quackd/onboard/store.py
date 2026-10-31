"""The one directory a robot keeps everything in, on the robot itself.

`--dir`, else `$QUACKD_ONBOARD_DIR`, else `~/.quackd/onboard/`. One robot per computer is what
this is for, so the default needs no flag, and a second directory is how one laptop stands in for
two robots on the sim board. The systemd unit names the directory in `QUACKD_ONBOARD_DIR`.

    robot.yaml        0600  the one file a person edits; its passport section is what gets signed
    robot.env         0600  the brain's key, the unit's EnvironmentFile=
    keys/robot.json   0600  the robot key, in the clear, for the unattended service
    keys/owner.json   0600  the owner key, sealed with a passphrase (`keys.seal`)
    passport.jws      0644  what the owner signed, and what the service serves
    control.json      0600  the service's control port and its token
    status.json       0600  what the service is doing, rewritten every few seconds
    met.jsonl         0600  every meeting, and every robot refused and why
    runs/                   the run records, newest kept
    memory/                 what the robot remembers between runs
    cache/                  downloads quackd keeps
    service.lock            held by the one service that runs here

A `QUACKD_ONBOARD_DIR` that only a `.env` file set is refused, the way an endpoint's registry is
(`quackd_lerobot.policy.endpoint.reach`): a project cloned from somebody else brings its `.env`
along, and one that named a robot directory of its own would choose which keys this robot signs
and talks with, and which passport it serves.
"""

from __future__ import annotations

import contextlib
import os
from pathlib import Path

from quackd.dotenv_names import from_dotenv
from quackd.onboard.keys import (
    KeysError,
    read_robot_key,
    read_sealed,
    seal,
    sync_folder,
    unseal,
    write_robot_key,
    write_secret,
)
from quackd.passport.keys import SigningKey, short_id_of

DEFAULT_DIR = "~/.quackd/onboard"
ENV_DIR = "QUACKD_ONBOARD_DIR"


class OnboardDirError(Exception):
    """A robot directory quackd will not use, or cannot make. The CLI prints it as one line."""


def named_by_dotenv(override: str | Path | None = None) -> bool:
    """Whether the directory `onboard_dir(override)` would give is one a `.env` file chose: there
    is no `--dir`, and the `QUACKD_ONBOARD_DIR` in effect is the value a `.env` gave it rather
    than the shell (`dotenv_names`). The same test `registry.named_by_dotenv` makes."""
    return not override and bool(os.environ.get(ENV_DIR)) and from_dotenv(ENV_DIR)


def onboard_dir(override: str | Path | None = None) -> Path:
    """`--dir`, else `$QUACKD_ONBOARD_DIR`, else `~/.quackd/onboard`, made absolute, since the
    systemd unit and its `ReadWritePaths=` need it so."""
    if named_by_dotenv(override):
        raise OnboardDirError(
            f"{ENV_DIR} comes from a .env file here, and a robot's directory is read only from "
            "one this shell or --dir names, since a project's .env could otherwise choose which "
            "keys this robot signs with and which passport it serves: export it in the shell, "
            "or pass --dir"
        )
    raw = override or os.environ.get(ENV_DIR) or DEFAULT_DIR
    return Path(raw).expanduser().absolute()


class OnboardDir:
    """The files, under one directory. Every call re-reads what it needs, and nothing here makes
    a folder or a file until something has to be written."""

    def __init__(self, override: str | Path | None = None) -> None:
        self.root = onboard_dir(override)

    @property
    def robot_yaml(self) -> Path:
        return self.root / "robot.yaml"

    @property
    def robot_env(self) -> Path:
        return self.root / "robot.env"

    @property
    def keys(self) -> Path:
        return self.root / "keys"

    @property
    def robot_key(self) -> Path:
        return self.keys / "robot.json"

    @property
    def owner_key(self) -> Path:
        return self.keys / "owner.json"

    @property
    def passport_jws(self) -> Path:
        return self.root / "passport.jws"

    @property
    def control_json(self) -> Path:
        return self.root / "control.json"

    @property
    def status_json(self) -> Path:
        return self.root / "status.json"

    @property
    def met_log(self) -> Path:
        return self.root / "met.jsonl"

    @property
    def runs(self) -> Path:
        return self.root / "runs"

    @property
    def memory(self) -> Path:
        return self.root / "memory"

    @property
    def cache(self) -> Path:
        return self.root / "cache"

    @property
    def lock(self) -> Path:
        return self.root / "service.lock"

    def ensure(self) -> None:
        """Make the directory and its folders, each one this user alone can enter where the OS
        has modes. A folder already there keeps the mode it has: whoever chose it chose that
        too, and every secret in it is a file of mode 0600 regardless. Each folder made is synced
        into the one above it, so the keys written next do not vanish with a power cut."""
        for folder in (self.root, self.keys, self.runs, self.memory, self.cache):
            if folder.is_dir():
                continue
            try:
                folder.mkdir(mode=0o700, parents=True, exist_ok=True)
            except OSError as e:
                raise OnboardDirError(f"could not make {folder}: {e.strerror or e}") from e
            if os.name != "nt":
                with contextlib.suppress(OSError):
                    folder.chmod(0o700)  # mkdir's mode passes through the umask
            sync_folder(folder.parent)

    def owner_key_or_new(self, passphrase: str) -> tuple[SigningKey, bool]:
        """The owner key, opened with `passphrase`, or a new one sealed with it the first time.
        The flag says which. A passphrase too short to seal with is refused before anything is
        written, and a wrong one is `WrongPassphrase`, with the file left as it was. The new key
        is written only where nothing is: an owner key another command made meanwhile, or a link
        where the file should be, is left as it is, and that is `KeyExists`."""
        if self.owner_key.exists():
            return unseal(read_sealed(self.owner_key), passphrase), False
        key = SigningKey.generate()
        blob = seal(key, passphrase)
        self.ensure()
        write_secret(self.owner_key, blob, replace=False)
        return key, True

    def robot_key_or_new(self) -> tuple[SigningKey, bool]:
        """The robot key, or a new one the first time. The flag says which. Only the wizard makes
        one: a new robot key is a new robot to everyone that met the old one, so the service reads
        `robot_key` with `keys.read_robot_key` and never comes here. As with the owner key, the
        new one is written only where nothing is."""
        if self.robot_key.exists():
            return read_robot_key(self.robot_key), False
        key = SigningKey.generate()
        self.ensure()
        write_robot_key(self.robot_key, key, replace=False)
        return key, True

    def adopt_owner_key(self, source: str | Path, passphrase: str) -> SigningKey:
        """The owner key sealed in `source`, another robot's `keys/owner.json`, made this robot's
        owner key as well: how one owner signs a second robot. `passphrase` has to open it first,
        so a file nobody here can unlock is never copied, and the same sealed blob is written
        here, never the key in the clear.

        An owner key this robot already has is never replaced: every robot that pinned that owner
        would stop knowing this one. The same key again is nothing to do."""
        blob = read_sealed(source)
        key = unseal(blob, passphrase)
        if self.owner_key.exists():
            held = read_sealed(self.owner_key)
            if held["did"] == key.did:
                return key
            raise KeysError(
                f"{self.owner_key} already holds owner key {short_id_of(held['did'])}, not "
                f"{key.short_id}: this robot is signed by another owner. Move that file aside "
                "first if this robot really changes hands"
            )
        self.ensure()
        write_secret(self.owner_key, blob, replace=False)
        return key
