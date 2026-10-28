"""The two keys a robot keeps: its owner's, sealed with a passphrase, and its own, in the clear.

The robot key signs every message the robot sends, so the service needs it at boot with nobody
there to type anything: it is a plain file only the robot's user can read, as an SSH host key is.
The owner key signs the passport, the claim another robot checks before it lets its brain listen,
and nothing unattended ever needs it. So it is sealed with a passphrase the owner types when they
sign, and a copy of the robot's disk is not a copy of the owner's signature.

Sealed with scrypt at n 2**17, r 8, p 1, the minimum the OWASP password storage cheat sheet gives
for it: 128 MiB of memory for every guess, which is what makes guessing slow. The floor
`cryptography` release quackd allows (42) derives at that cost, so there is no smaller fallback.
The seed is then encrypted with AES-256-GCM, with the key's own did as the associated data, so a
sealed seed moved under another did does not open. GCM cannot tell a wrong passphrase from a file
changed after sealing, and `WrongPassphrase` says both.

A passphrase is NFC-normalised before it is used, so the same words typed on two keyboards, or
written to a passphrase file by an editor that decomposes accents, open the same key.
"""

from __future__ import annotations

import contextlib
import json
import os
import stat
import tempfile
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from quackd.passport.errors import PassportError
from quackd.passport.jws import b64url, b64url_decode
from quackd.passport.keys import SEED_BYTES, SigningKey, public_from_did, short_id

VERSION = 1
"""Of both files, the sealed owner key and the robot key."""
KIND = "robot-passport owner key"

SCRYPT_N = 2**17
SCRYPT_R = 8
SCRYPT_P = 1
MAX_SCRYPT_COST = 8 * SCRYPT_N * SCRYPT_R * SCRYPT_P
"""The most a sealed key may ask of scrypt, as n * r * p: eight times what this quackd seals
with, so a later quackd can raise the cost and this one still opens what it sealed, and a
damaged file cannot ask for all of a robot's memory (1 GiB at most, for r 8)."""

SALT_BYTES = 16
NONCE_BYTES = 12
TAG_BYTES = 16
AES_KEY_BYTES = 32

MIN_PASSPHRASE = 12
"""Characters, counted after NFC normalisation. A passphrase is the whole of what stands between
a stolen robot and its owner's signature, and scrypt only slows guessing down."""


class KeysError(Exception):
    """A key file, a sealed key or a passphrase file quackd will not use, in one sentence a
    person can act on. The CLI prints it as a line, never a traceback."""


class PassphraseRefused(KeysError):
    """A passphrase too short to seal a key with, or one a passphrase file could not carry. The
    wizard asks again; a script is told why."""


class WrongPassphrase(Exception):
    """The passphrase does not open the sealed key: it was mistyped, or the file was changed
    after it was sealed, which GCM cannot tell apart."""


def check_passphrase(passphrase: str) -> None:
    """Refuse a passphrase no key should be sealed with. `seal` asks this first, and the wizard
    can ask it before asking a person to type the passphrase a second time.

    One line with no space at either end, because `read_passphrase_file` reads the first line
    of a file stripped: a passphrase that file could not carry is one a script could never use."""
    text = _normalised(passphrase)
    if len(text) < MIN_PASSPHRASE:
        raise PassphraseRefused(
            f"a passphrase needs at least {MIN_PASSPHRASE} characters, and this one has {len(text)}"
        )
    if text != text.strip() or len(text.splitlines()) != 1:
        raise PassphraseRefused(
            "a passphrase is one line with no space at either end, since a passphrase file is "
            "read that way"
        )


def seal(key: SigningKey, passphrase: str) -> dict[str, Any]:
    """`key` sealed with `passphrase`, as the JSON object `keys/owner.json` holds."""
    check_passphrase(passphrase)
    salt = os.urandom(SALT_BYTES)
    nonce = os.urandom(NONCE_BYTES)
    n, r, p = SCRYPT_N, SCRYPT_R, SCRYPT_P
    sealed = AESGCM(_derived(passphrase, salt, n, r, p)).encrypt(
        nonce, key.seed, key.did.encode("ascii")
    )
    return {
        "version": VERSION,
        "kind": KIND,
        "did": key.did,
        "kdf": {"name": "scrypt", "n": n, "r": r, "p": p, "salt": b64url(salt)},
        "cipher": {"name": "aes-256-gcm", "nonce": b64url(nonce)},
        "sealed": b64url(sealed),
    }


def unseal(blob: Mapping[str, Any], passphrase: str) -> SigningKey:
    """The key `blob` holds, opened with `passphrase`.

    `KeysError` for a blob that is not a sealed key this quackd reads, `WrongPassphrase` for one
    the passphrase does not open. The minimum length is not asked here: it binds what is sealed
    from now on, and a key sealed under a shorter rule must still open."""
    held = _parsed(blob, "the sealed key")
    try:
        seed = AESGCM(_derived(passphrase, held.salt, held.n, held.r, held.p)).decrypt(
            held.nonce, held.sealed, held.did.encode("ascii")
        )
    except InvalidTag:
        raise WrongPassphrase(
            f"that passphrase does not open owner key {held.short_id}: it was mistyped, or the "
            "file was changed after it was sealed"
        ) from None
    key = SigningKey(seed)
    if key.did != held.did:
        # GCM checked the did as associated data, so only something that had the passphrase
        # could have sealed this: whoever did put one key's seed under another key's name
        raise KeysError(
            f"the sealed key says it is {held.did}, and the seed in it makes {key.did}: quackd "
            "never seals a key that way, so it is refused"
        )
    return key


def read_sealed(path: str | Path) -> dict[str, Any]:
    """A sealed owner key's file, checked to be one this quackd can try to open. Its `did` can be
    shown without the passphrase; nothing about it is proven until `unseal` opens it."""
    raw = _read_json(Path(path))
    _parsed(raw, str(path))
    return raw


def write_robot_key(path: str | Path, key: SigningKey) -> None:
    """The robot key, in the clear, readable by this user alone where the OS has modes."""
    write_secret(path, {"version": VERSION, "did": key.did, "seed": key.seed.hex()})


def read_robot_key(path: str | Path) -> SigningKey:
    path = Path(path)
    raw = _read_json(path)
    _version(raw, str(path))
    try:
        key = SigningKey(bytes.fromhex(str(raw.get("seed"))))
    except ValueError as e:
        raise KeysError(f"{path}: no readable seed: fix it or move it aside") from e
    if raw.get("did") != key.did:
        raise KeysError(f"{path}: its did is not the key its seed makes")
    return key


def read_passphrase_file(path: str | Path) -> str:
    """The first line of `path`, stripped: how a script hands quackd a passphrase, since no
    environment variable ever carries one.

    On a system with file modes, a file anybody but its owner can open is refused, the way SSH
    refuses a private key others can read: a passphrase there is the owner's signature for every
    robot it opens. Windows has no such modes, and the check is skipped there."""
    path = Path(path)
    try:
        mode = path.stat().st_mode
    except OSError as e:
        raise KeysError(f"could not read {path}: {e.strerror or e}") from e
    if os.name != "nt" and mode & 0o077:
        raise KeysError(
            f"{path} is open to other users of this computer (mode {stat.S_IMODE(mode):o}): "
            f"chmod 600 {path}, so only you can read it"
        )
    try:
        text = path.read_text(encoding="utf-8-sig")  # a BOM from Notepad is not the passphrase
    except OSError as e:
        raise KeysError(f"could not read {path}: {e.strerror or e}") from e
    except UnicodeDecodeError as e:
        raise KeysError(f"{path} is not UTF-8 text") from e
    lines = text.splitlines()
    line = lines[0].strip() if lines else ""
    if not line:
        raise KeysError(f"{path} has nothing on its first line, which is where the passphrase goes")
    return line


def write_secret(path: str | Path, data: str | Mapping[str, Any]) -> None:
    """`data` as the whole of `path`: a string as it is, a mapping as indented JSON.

    Readable by its owner alone from before the first byte of the secret is in it. The temporary
    file beside `path` is made by `mkstemp`, which creates a new file, exclusively, at mode 0600,
    and the rename carries that mode onto `path`, whatever mode an old `path` had. Setting the
    mode after the rename would leave the secret readable by others for the length of the write.
    The data is synced before the rename and the folder after it, since a robot's power can be cut
    at any moment, and a key that was announced but never reached the disk is lost for good.

    The modes are a no-op on Windows, where the file takes the permissions of the folder it sits
    in: a file in a home directory, not a secret store, as SECURITY.md says."""
    path = Path(path)
    text = data if isinstance(data, str) else json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    tmp: str | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        with os.fdopen(fd, "wb") as out:
            out.write(text.encode("utf-8"))
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp, path)
    except OSError as e:  # a full disk, a read-only folder, or on Windows a file held open
        if tmp is not None:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
        raise KeysError(f"could not write {path}: {e.strerror or e}") from e
    _sync_folder(path.parent)


@dataclass(frozen=True)
class _Sealed:
    """A sealed key's members, each checked for shape, none of them yet proven."""

    did: str
    short_id: str
    n: int
    r: int
    p: int
    salt: bytes
    nonce: bytes
    sealed: bytes


def _parsed(blob: object, where: str) -> _Sealed:
    if not isinstance(blob, Mapping):
        raise KeysError(f"{where} is not a sealed key: expected an object")
    _version(blob, where)
    if blob.get("kind") != KIND:
        raise KeysError(f"{where} is not a sealed owner key: its kind is {blob.get('kind')!r}")
    did = blob.get("did")
    if not isinstance(did, str):
        raise KeysError(f"{where} names no key it could hold: its did is {did!r}")
    try:
        public = public_from_did(did)
    except PassportError as e:
        raise KeysError(f"{where} names no key it could hold: {e}") from e
    kdf = _object(blob, "kdf", where)
    if kdf.get("name") != "scrypt":
        raise KeysError(
            f"{where} is sealed with {kdf.get('name')!r}, which this quackd cannot open"
        )
    n, r, p = (_positive(kdf, name, where) for name in ("n", "r", "p"))
    if n < 2 or n & (n - 1):
        raise KeysError(f"{where}: kdf.n is {n}, and scrypt's n is a power of two above 1")
    if n * r * p > MAX_SCRYPT_COST:
        raise KeysError(
            f"{where} asks more of scrypt (n {n}, r {r}, p {p}) than this quackd spends opening a "
            "key: upgrade quackd if a newer one sealed it"
        )
    cipher = _object(blob, "cipher", where)
    if cipher.get("name") != "aes-256-gcm":
        raise KeysError(
            f"{where} is encrypted with {cipher.get('name')!r}, which this quackd cannot open"
        )
    return _Sealed(
        did=did,
        short_id=short_id(public),
        n=n,
        r=r,
        p=p,
        salt=_decoded(kdf.get("salt"), SALT_BYTES, where, "kdf.salt"),
        nonce=_decoded(cipher.get("nonce"), NONCE_BYTES, where, "cipher.nonce"),
        sealed=_decoded(blob.get("sealed"), SEED_BYTES + TAG_BYTES, where, "sealed"),
    )


def _derived(passphrase: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    kdf = Scrypt(salt=salt, length=AES_KEY_BYTES, n=n, r=r, p=p)
    return kdf.derive(_normalised(passphrase).encode("utf-8"))


def _normalised(passphrase: str) -> str:
    return unicodedata.normalize("NFC", passphrase)


def _version(raw: Mapping[str, Any], where: str) -> None:
    version = raw.get("version")
    if isinstance(version, bool) or not isinstance(version, int):
        raise KeysError(f"{where} carries no version this quackd reads ({version!r})")
    if version > VERSION:
        raise KeysError(f"{where} is version {version}, newer than this quackd: upgrade quackd")
    if version != VERSION:
        raise KeysError(f"{where} is version {version}, which this quackd does not read")


def _object(raw: Mapping[str, Any], name: str, where: str) -> Mapping[str, Any]:
    value = raw.get(name)
    if not isinstance(value, Mapping):
        raise KeysError(f"{where}: {name} is not an object")
    return value


def _positive(kdf: Mapping[str, Any], name: str, where: str) -> int:
    value = kdf.get(name)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise KeysError(f"{where}: kdf.{name} is not a whole number above 0 ({value!r})")
    return value


def _decoded(value: object, size: int, where: str, member: str) -> bytes:
    if isinstance(value, str):
        with contextlib.suppress(PassportError):
            data = b64url_decode(value, rule=member)
            if len(data) == size:
                return data
    raise KeysError(f"{where}: {member} is not {size} bytes of base64url")


def _read_json(path: Path) -> dict[str, Any]:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as e:
        raise KeysError(f"{path} does not exist") from e
    except OSError as e:
        raise KeysError(f"could not read {path}: {e.strerror or e}") from e
    except UnicodeDecodeError as e:
        raise KeysError(f"{path} is not UTF-8 text") from e
    except json.JSONDecodeError as e:
        raise KeysError(f"{path}: not valid JSON ({e.msg} at line {e.lineno})") from e
    if not isinstance(loaded, dict):
        raise KeysError(f"{path}: expected an object, found {type(loaded).__name__}")
    return loaded


def _sync_folder(folder: Path) -> None:
    """Make a rename in `folder` survive a power cut. POSIX only: Windows cannot open a folder
    this way, and NTFS journals the rename itself."""
    if os.name == "nt":
        return
    with contextlib.suppress(OSError):
        fd = os.open(folder, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
