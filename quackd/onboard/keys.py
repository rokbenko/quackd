"""The two keys a robot keeps: its owner's, sealed with a passphrase, and its own, in the clear.

The robot key signs every message the robot sends, so the service needs it at boot with nobody
there to type anything: it is a plain file only the robot's user can read, as an SSH host key is.
The owner key signs the passport, the claim another robot checks before it lets its brain listen,
and nothing unattended ever needs it. So it is sealed with a passphrase the owner types when they
sign, and a copy of the robot's disk is not a copy of the owner's signature.

Sealed with scrypt at n 2**17, r 8, p 1, the most memory-hungry of the five equivalent settings
the OWASP password storage cheat sheet gives for it: 128 MiB for every guess, which is what makes
guessing slow. The floor `cryptography` release quackd allows (42) derives at that cost, so there
is no smaller fallback. The seed is then encrypted with AES-256-GCM, with the key's own did as the
associated data, so a sealed seed moved under another did does not open. GCM cannot tell a wrong
passphrase from a file changed after sealing, and `WrongPassphrase` says both.

A passphrase is NFC-normalised before it is used, so the same words typed on two keyboards, or
written to a passphrase file by an editor that decomposes accents, open the same key. Before that,
the two UTF-16 halves Windows' hidden prompt hands a character like an emoji over as are joined,
so a passphrase typed there opens on the robot too.

No more than `MAX_FILE_BYTES` of a file is read here, and every number in a sealed key is bounded
before scrypt sees it: these files sit on a robot's disk, and a damaged one must be a sentence,
not a robot that hangs or runs out of memory.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
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


def scrypt_memory(n: int, r: int, p: int) -> int:
    """The bytes OpenSSL allocates for scrypt at that cost (`EVP_PBE_scrypt`): 128 r (n + 2) for
    its table and 128 r p for its blocks."""
    return 128 * r * (n + p + 2)


MAX_SCRYPT_COST = 8 * SCRYPT_N * SCRYPT_R * SCRYPT_P
"""The most work a sealed key may ask of scrypt, as n * r * p: eight times what this quackd seals
with, so a later quackd can raise the cost and this one still opens what it sealed."""
MAX_SCRYPT_MEMORY = 8 * scrypt_memory(SCRYPT_N, SCRYPT_R, SCRYPT_P)
"""And the most memory, eight times the 128 MiB this quackd seals with, so about 1 GiB: a damaged
file cannot ask for all of a robot's memory, whatever r it names."""

SALT_BYTES = 16
NONCE_BYTES = 12
TAG_BYTES = 16
AES_KEY_BYTES = 32

MIN_PASSPHRASE = 12
"""Characters, counted after NFC normalisation. A passphrase is the whole of what stands between
a stolen robot and its owner's signature, and scrypt only slows guessing down."""

MAX_FILE_BYTES = 64 * 1024
"""The most of a key file or a passphrase file read. Either is a few hundred bytes."""
MAX_DID = 64
"""An Ed25519 did:key is 56 characters. A longer one is refused before it is decoded, since
base58 decodes in time that grows with the square of its length."""

_SEED_HEX = re.compile(rf"[0-9a-fA-F]{{{2 * SEED_BYTES}}}")


class KeysError(Exception):
    """A key file, a sealed key or a passphrase file quackd will not use, in one sentence a
    person can act on. The CLI prints it as a line, never a traceback."""


class PassphraseRefused(KeysError):
    """A passphrase too short to seal a key with, or one a passphrase file could not carry. The
    wizard asks again; a script is told why."""


class KeyExists(KeysError):
    """A key file `write_secret(..., replace=False)` found already there, and left as it is."""


class WrongPassphrase(Exception):
    """The passphrase does not open the sealed key: it was mistyped, or the file was changed
    after it was sealed, which GCM cannot tell apart."""


def check_passphrase(passphrase: str) -> None:
    """Refuse a passphrase no key should be sealed with. `seal` asks this first, and the wizard
    can ask it before asking a person to type the passphrase a second time.

    One line with no space at either end, because `read_passphrase_file` reads the first line
    of a file stripped: a passphrase that file could not carry is one a script could never use."""
    try:
        text = _normalised(passphrase)
    except UnicodeError:
        raise PassphraseRefused(
            "a passphrase cannot hold half a character (a UTF-16 surrogate with no other half)"
        ) from None
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
    from now on, and a key sealed under a shorter rule must still open. Members it does not know
    are left alone, as a newer quackd may write some; every one it reads is checked."""
    held = _parsed(blob, "the sealed key")
    wrong = WrongPassphrase(
        f"that passphrase does not open owner key {held.short_id}: it was mistyped, or the file "
        "was changed after it was sealed"
    )
    try:
        derived = _derived(passphrase, held.salt, held.n, held.r, held.p)
    except UnicodeError:
        raise wrong from None  # half a character, which `seal` never takes
    except MemoryError as e:
        raise KeysError(
            f"opening owner key {held.short_id} asks scrypt for "
            f"{scrypt_memory(held.n, held.r, held.p) // 2**20} MiB, more than this computer gave it"
        ) from e
    try:
        seed = AESGCM(derived).decrypt(held.nonce, held.sealed, held.did.encode("ascii"))
    except InvalidTag:
        raise wrong from None
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


def write_robot_key(path: str | Path, key: SigningKey, *, replace: bool = False) -> None:
    """The robot key, in the clear, readable by this user alone where the OS has modes. A robot
    key already at `path` is left as it is (`KeyExists`) unless `replace` says otherwise: a new
    robot key is a new robot to everyone that met the old one."""
    payload = {"version": VERSION, "did": key.did, "seed": key.seed.hex()}
    write_secret(path, payload, replace=replace)


def read_robot_key(path: str | Path) -> SigningKey:
    path = Path(path)
    raw = _read_json(path)
    _version(raw, str(path))
    seed = raw.get("seed")
    if not isinstance(seed, str) or not _SEED_HEX.fullmatch(seed):
        raise KeysError(
            f"{path}: no readable seed, which is {2 * SEED_BYTES} hex digits: fix it or move it "
            "aside"
        )
    key = SigningKey(bytes.fromhex(seed))
    if raw.get("did") != key.did:
        raise KeysError(f"{path}: its did is not the key its seed makes")
    return key


def read_passphrase_file(path: str | Path) -> str:
    """The first line of `path`, stripped: how a script hands quackd a passphrase, since no
    environment variable ever carries one.

    On a system with file modes, a file anybody but its owner can open is refused, the way SSH
    refuses a private key others can read: a passphrase there is the owner's signature for every
    robot it opens. The mode is asked of the file as opened, so it cannot be swapped between the
    check and the read. Windows has no such modes, and the check is skipped there."""
    path = Path(path)
    try:
        with path.open("rb") as file:
            mode = os.fstat(file.fileno()).st_mode
            if os.name != "nt" and mode & 0o077:
                raise KeysError(
                    f"{path} is open to other users of this computer (mode "
                    f"{stat.S_IMODE(mode):o}): chmod 600 {path}, so only you can read it"
                )
            raw = file.read(MAX_FILE_BYTES)
    except OSError as e:
        raise KeysError(f"could not read {path}: {e.strerror or e}") from e
    try:
        text = raw.decode("utf-8-sig")  # a BOM from Notepad is not the passphrase
    except UnicodeDecodeError as e:
        raise KeysError(f"{path} is not UTF-8 text") from e
    lines = text.splitlines()
    line = lines[0].strip() if lines else ""
    if not line:
        raise KeysError(f"{path} has nothing on its first line, which is where the passphrase goes")
    return line


def write_secret(path: str | Path, data: str | Mapping[str, Any], *, replace: bool = True) -> None:
    """`data` as the whole of `path`: a string as it is, a mapping as indented JSON. Readable by
    its owner alone from before the first byte of the secret is in it.

    With `replace`, a temporary file beside `path` is made by `mkstemp`, which creates a new file,
    exclusively, at mode 0600, and renamed over `path`, which carries that mode onto it whatever
    mode an old `path` had; setting the mode after the rename would leave the secret readable by
    others for the length of the write. Without, `path` itself is made exclusively at 0600, so
    whatever is there already, a dangling link included, stays as it is and the call raises
    `KeyExists`: a key made by two commands at once, or written over a link, is a key lost.

    The data is synced before it is renamed into place, and on POSIX so is the folder after it,
    and the folders above any this call made, since a robot's power can be cut at any moment.
    Windows has no way to sync a folder, so there a power cut can still lose a rename made just
    before it; that is the laptop standing in for a robot, never the robot.

    The modes are a no-op on Windows, where the file takes the permissions of the folder it sits
    in: a file in a home directory, not a secret store, as SECURITY.md says."""
    path = Path(path)
    text = data if isinstance(data, str) else json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    try:
        payload = text.encode("utf-8")
    except UnicodeEncodeError as e:
        raise KeysError(f"could not write {path}: it holds text UTF-8 cannot carry") from e
    made = _missing_folders(path.parent)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if replace:
            _replace(path, payload)
        else:
            _write_new(path, payload)
    except OSError as e:  # a full disk, a read-only folder, or on Windows a file held open
        raise KeysError(f"could not write {path}: {e.strerror or e}") from e
    for folder in (path.parent, *(f.parent for f in made)):
        sync_folder(folder)


def sync_folder(folder: Path) -> None:
    """Make what was just renamed or made in `folder` survive a power cut. POSIX only: Windows
    cannot open a folder to sync it."""
    if os.name == "nt":
        return
    with contextlib.suppress(OSError):
        fd = os.open(folder, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _replace(path: Path, payload: bytes) -> None:
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(payload)
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp, path)
    except BaseException:  # Ctrl-C too: a temporary file holding a secret is never left behind
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def _write_new(path: Path, payload: bytes) -> None:
    """`payload` in a new file at `path`. O_EXCL refuses a name that is taken, even by a link
    that points nowhere. It is written in place, so a power cut in the middle can leave it half
    written, which can only happen to a key nothing has used yet."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    try:
        fd = os.open(path, flags, 0o600)
    except FileExistsError as e:
        raise KeyExists(f"{path} is already there, and quackd does not write over a key") from e
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(payload)
            out.flush()
            os.fsync(out.fileno())
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(path)
        raise


def _missing_folders(folder: Path) -> list[Path]:
    """`folder` and each folder above it that is not there yet, nearest first."""
    missing: list[Path] = []
    while not folder.exists() and folder.parent != folder:
        missing.append(folder)
        folder = folder.parent
    return missing


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
    if len(did) > MAX_DID:
        raise KeysError(
            f"{where} names no key it could hold: its did is {len(did)} characters long, and a "
            f"did:key is {MAX_DID} at most"
        )
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
    _bounded(n, r, p, where)
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


def _bounded(n: int, r: int, p: int, where: str) -> None:
    """Refuse a cost scrypt would refuse, or one that would take too long or too much memory.
    `bit_length` stands in for 2 ** (16 r), which a damaged r would make an enormous number."""
    if n < 2 or n & (n - 1):
        raise KeysError(f"{where}: kdf.n is {n}, and scrypt's n is a power of two above 1")
    if n * r * p > MAX_SCRYPT_COST or scrypt_memory(n, r, p) > MAX_SCRYPT_MEMORY:
        raise KeysError(
            f"{where} asks more of scrypt (n {n}, r {r}, p {p}) than this quackd spends opening a "
            "key: upgrade quackd if a newer one sealed it"
        )
    if n.bit_length() > 16 * r:  # OpenSSL's own rule: n below 2 ** (16 r)
        raise KeysError(f"{where}: kdf.n {n} is too large for r {r}, which scrypt refuses")


def _derived(passphrase: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    kdf = Scrypt(salt=salt, length=AES_KEY_BYTES, n=n, r=r, p=p)
    return kdf.derive(_normalised(passphrase).encode("utf-8"))


def _normalised(passphrase: str) -> str:
    """NFC, after joining the UTF-16 halves Windows' hidden prompt hands some characters over
    as. `UnicodeError` for a half with no other half."""
    joined = passphrase.encode("utf-16-le", "surrogatepass").decode("utf-16-le")
    return unicodedata.normalize("NFC", joined)


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
        with path.open("rb") as file:
            raw = file.read(MAX_FILE_BYTES + 1)
    except FileNotFoundError as e:
        raise KeysError(f"{path} does not exist") from e
    except OSError as e:
        raise KeysError(f"could not read {path}: {e.strerror or e}") from e
    if len(raw) > MAX_FILE_BYTES:
        raise KeysError(f"{path} is larger than a key file is: fix it or move it aside")
    try:
        loaded = json.loads(raw.decode("utf-8"))
    except UnicodeDecodeError as e:
        raise KeysError(f"{path} is not UTF-8 text") from e
    except json.JSONDecodeError as e:
        raise KeysError(f"{path}: not valid JSON ({e.msg} at line {e.lineno})") from e
    except (ValueError, RecursionError) as e:  # a number past Python's digit limit, or nesting
        raise KeysError(f"{path}: not JSON quackd will read ({e})") from e
    if not isinstance(loaded, dict):
        raise KeysError(f"{path}: expected an object, found {type(loaded).__name__}")
    return loaded
