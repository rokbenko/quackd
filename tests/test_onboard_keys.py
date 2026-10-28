"""The owner key sealed with a passphrase, the robot key in the clear, and the robot's directory.

Every test but one seals at a cost a test can afford (`cheap`): scrypt at quackd's real cost asks
128 MiB and a second or more for every derivation, and the changed-blob cases alone seal or open
dozens of times. `test_the_real_cost_seals_and_opens` proves the real cost once, and pins it.
"""

from __future__ import annotations

import base64
import copy
import json
import os
import stat
import sys
import unicodedata
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from quackd import dotenv_names
from quackd.onboard import keys, store
from quackd.passport.jws import b64url, b64url_decode
from quackd.passport.keys import SEED_BYTES, SigningKey

PASSPHRASE = "correct horse battery staple"
CHEAP_N = 2**4
"""scrypt's n for the tests that do not prove the cost itself."""

POSIX_ONLY = pytest.mark.skipif(os.name == "nt", reason="Windows has no file modes to check")


@pytest.fixture
def cheap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(keys, "SCRYPT_N", CHEAP_N)


@pytest.fixture(autouse=True)
def _no_onboard_dir_from_the_shell(monkeypatch: pytest.MonkeyPatch) -> None:
    """A developer's shell may export one, and every test here says which directory it means."""
    monkeypatch.delenv(store.ENV_DIR, raising=False)


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def _unb64(text: str) -> bytes:
    return b64url_decode(text, rule="test")


# ── sealing ─────────────────────────────────────────────────────────────────────────────


def test_a_sealed_key_opens_with_its_passphrase_and_holds_no_seed_in_the_clear(
    cheap: None,
) -> None:
    key = SigningKey.generate()
    blob = json.loads(json.dumps(keys.seal(key, PASSPHRASE)))  # as it comes back from a file
    assert (blob["version"], blob["kind"], blob["did"]) == (keys.VERSION, keys.KIND, key.did)
    kdf, cipher = blob["kdf"], blob["cipher"]
    assert (kdf["name"], kdf["n"], kdf["r"], kdf["p"]) == (
        "scrypt",
        keys.SCRYPT_N,
        keys.SCRYPT_R,
        keys.SCRYPT_P,
    )
    assert len(_unb64(kdf["salt"])) == keys.SALT_BYTES
    assert cipher["name"] == "aes-256-gcm" and len(_unb64(cipher["nonce"])) == keys.NONCE_BYTES
    assert len(_unb64(blob["sealed"])) == SEED_BYTES + keys.TAG_BYTES
    text = json.dumps(blob)
    for spelling in (key.seed.hex(), b64url(key.seed), base64.b64encode(key.seed).decode()):
        assert spelling not in text
    opened = keys.unseal(blob, PASSPHRASE)
    assert (opened.seed, opened.did) == (key.seed, key.did)
    again = keys.seal(key, PASSPHRASE)
    assert again["kdf"]["salt"] != kdf["salt"] and again["cipher"]["nonce"] != cipher["nonce"]


def test_the_real_cost_seals_and_opens() -> None:
    """The cost the plan gives, pinned, since a cheaper one would pass every other test here, and
    inside the most a sealed key may ask, so a key this quackd seals it can open. This runs the
    `cryptography` the lock file pins; the floor pyproject allows, 42.0.0, was checked by hand to
    derive at this cost as well."""
    assert (keys.SCRYPT_N, keys.SCRYPT_R, keys.SCRYPT_P) == (2**17, 8, 1)
    assert keys.SCRYPT_N * keys.SCRYPT_R * keys.SCRYPT_P <= keys.MAX_SCRYPT_COST
    assert keys.scrypt_memory(keys.SCRYPT_N, keys.SCRYPT_R, keys.SCRYPT_P) <= keys.MAX_SCRYPT_MEMORY
    key = SigningKey.generate()
    blob = keys.seal(key, PASSPHRASE)
    kdf = blob["kdf"]
    assert (kdf["n"], kdf["r"], kdf["p"]) == (keys.SCRYPT_N, keys.SCRYPT_R, keys.SCRYPT_P)
    assert keys.unseal(blob, PASSPHRASE).did == key.did


def test_a_wrong_passphrase_is_wrong_passphrase_and_names_the_key(cheap: None) -> None:
    """Its own class, and not a `KeysError`, so a wizard can ask again for this and for nothing
    else."""
    key = SigningKey.generate()
    blob = keys.seal(key, PASSPHRASE)
    with pytest.raises(keys.WrongPassphrase) as caught:
        keys.unseal(blob, PASSPHRASE + "!")
    assert key.short_id in str(caught.value)
    assert not isinstance(caught.value, keys.KeysError)


def test_a_passphrase_under_the_minimum_is_refused_and_one_at_it_seals(cheap: None) -> None:
    key = SigningKey.generate()
    with pytest.raises(keys.PassphraseRefused, match=f"at least {keys.MIN_PASSPHRASE}"):
        keys.seal(key, "x" * (keys.MIN_PASSPHRASE - 1))
    at_it = "x" * keys.MIN_PASSPHRASE
    assert keys.unseal(keys.seal(key, at_it), at_it).did == key.did


def test_a_key_sealed_under_a_shorter_rule_still_opens(
    cheap: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The minimum binds what is sealed from now on, never what was sealed before."""
    key = SigningKey.generate()
    rule = keys.MIN_PASSPHRASE
    short = "x" * (rule - 1)
    monkeypatch.setattr(keys, "MIN_PASSPHRASE", rule - 1)
    blob = keys.seal(key, short)
    monkeypatch.setattr(keys, "MIN_PASSPHRASE", rule)
    assert keys.unseal(blob, short).did == key.did


@pytest.mark.parametrize(
    "passphrase",
    [" " + PASSPHRASE, PASSPHRASE + " ", PASSPHRASE + "\n", "a first line\nand a second"],
)
def test_a_passphrase_a_file_could_not_carry_is_refused(passphrase: str) -> None:
    """`read_passphrase_file` reads a first line, stripped, so a passphrase it could never give
    back is one no script could use."""
    with pytest.raises(keys.PassphraseRefused, match="one line"):
        keys.check_passphrase(passphrase)


def test_the_same_words_in_either_unicode_form_open_the_same_key(cheap: None) -> None:
    composed = "\u010debelica in medved"
    decomposed = unicodedata.normalize("NFD", composed)
    assert composed != decomposed
    key = SigningKey.generate()
    assert keys.unseal(keys.seal(key, decomposed), composed).did == key.did


DROP = object()
"""A member taken out of the blob, rather than changed."""


def _flipped(text: str) -> str:
    """One bit of a base64url value changed, its length kept."""
    raw = bytearray(_unb64(text))
    raw[0] ^= 1
    return b64url(bytes(raw))


_OVER = 1 << keys.MAX_SCRYPT_COST.bit_length()
"""A power of two above the most a sealed key may ask of scrypt."""

_CHANGES: list[tuple[tuple[str, ...], Any, type[Exception]]] = [
    (("version",), keys.VERSION + 1, keys.KeysError),
    (("version",), str(keys.VERSION), keys.KeysError),
    (("version",), True, keys.KeysError),
    (("kind",), "robot-passport robot key", keys.KeysError),
    (("did",), SigningKey.generate().did, keys.WrongPassphrase),
    (("did",), "did:web:example.org", keys.KeysError),
    (("kdf", "name"), "argon2id", keys.KeysError),
    (("kdf", "n"), lambda n: n * 2, keys.WrongPassphrase),
    (("kdf", "n"), lambda n: n + 1, keys.KeysError),
    (("kdf", "n"), _OVER, keys.KeysError),
    (("kdf", "r"), lambda r: r + 1, keys.WrongPassphrase),
    (("kdf", "r"), 0, keys.KeysError),
    (("kdf", "p"), lambda p: p + 1, keys.WrongPassphrase),
    (("kdf", "p"), str, keys.KeysError),
    (("kdf", "p"), True, keys.KeysError),
    (("kdf", "salt"), _flipped, keys.WrongPassphrase),
    (("kdf", "salt"), lambda s: b64url(_unb64(s)[:-1]), keys.KeysError),
    (("cipher", "name"), "chacha20-poly1305", keys.KeysError),
    (("cipher", "nonce"), _flipped, keys.WrongPassphrase),
    (("cipher", "nonce"), "not base64url!", keys.KeysError),
    (("cipher", "nonce"), lambda s: b64url(_unb64(s)[:8]), keys.KeysError),
    (("sealed",), _flipped, keys.WrongPassphrase),
    (("sealed",), lambda s: b64url(_unb64(s)[:-1]), keys.KeysError),
    *(
        ((*parents, name), DROP, keys.KeysError)
        for parents, names in (
            ((), ("version", "kind", "did", "kdf", "cipher", "sealed")),
            (("kdf",), ("name", "n", "r", "p", "salt")),
            (("cipher",), ("name", "nonce")),
        )
        for name in names
    ),
]


def _changed(blob: dict[str, Any], path: tuple[str, ...], change: Any) -> dict[str, Any]:
    out = copy.deepcopy(blob)
    *parents, last = path
    holder = out
    for name in parents:
        holder = holder[name]
    if change is DROP:
        del holder[last]
    else:
        holder[last] = change(holder[last]) if callable(change) else change
    return out


@pytest.mark.parametrize(
    ("path", "change", "refused"),
    _CHANGES,
    ids=[f"{'.'.join(p)}-{i}" for i, (p, _, _) in enumerate(_CHANGES)],
)
def test_a_changed_blob_never_opens_even_with_the_right_passphrase(
    cheap: None, path: tuple[str, ...], change: Any, refused: type[Exception]
) -> None:
    """Every member, changed or taken out. What the cipher binds (the did as associated data, the
    salt and the cost through the derived key, the nonce, the sealed bytes) fails GCM's tag and
    reads as a wrong passphrase, since GCM cannot tell the two apart; what is only a shape fails
    as a `KeysError` that names it. Nothing else escapes, and no key comes back."""
    blob = keys.seal(SigningKey.generate(), PASSPHRASE)
    with pytest.raises(refused):
        keys.unseal(_changed(blob, path, change), PASSPHRASE)


def test_a_blob_whose_seed_makes_another_did_is_refused(cheap: None) -> None:
    """Sealed here the way quackd seals, independently of `keys`, but with one key's seed under
    another key's did, which only somebody holding the passphrase could do. It opens, so this
    also pins the format: scrypt over the passphrase's UTF-8, AES-256-GCM, the did as associated
    data."""
    mine, other = SigningKey.generate(), SigningKey.generate()
    blob = keys.seal(mine, PASSPHRASE)
    kdf = blob["kdf"]
    derived = Scrypt(
        salt=_unb64(kdf["salt"]), length=keys.AES_KEY_BYTES, n=kdf["n"], r=kdf["r"], p=kdf["p"]
    ).derive(PASSPHRASE.encode("utf-8"))
    nonce = _unb64(blob["cipher"]["nonce"])
    blob["did"] = other.did
    blob["sealed"] = b64url(AESGCM(derived).encrypt(nonce, mine.seed, other.did.encode("ascii")))
    with pytest.raises(keys.KeysError, match="never seals a key that way"):
        keys.unseal(blob, PASSPHRASE)


def _never(*args: Any, **kwargs: Any) -> Any:
    raise AssertionError("reached, where the blob should have been refused before it")


@pytest.mark.parametrize(
    ("n", "r", "p"),
    [(1 << 16, 1, 1), (2, keys.MAX_SCRYPT_COST // 2, 1), (2, 1, keys.MAX_SCRYPT_COST)],
    ids=["an-n-scrypt-refuses-at-r-1", "the-memory-within-the-work", "the-work"],
)
def test_a_cost_scrypt_refuses_or_too_heavy_to_try_is_refused_before_scrypt_runs(
    cheap: None, monkeypatch: pytest.MonkeyPatch, n: int, r: int, p: int
) -> None:
    """OpenSSL refuses an n at or above 2 ** (16 r) with a `MemoryError`, and a damaged file
    chooses r as freely as n, so the work and the memory are each bounded. None of it reaches
    scrypt."""
    blob = keys.seal(SigningKey.generate(), PASSPHRASE)
    blob["kdf"].update(n=n, r=r, p=p)
    monkeypatch.setattr(keys, "_derived", _never)
    with pytest.raises(keys.KeysError):
        keys.unseal(blob, PASSPHRASE)


def test_a_did_too_long_to_be_one_is_refused_before_it_is_decoded(
    cheap: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """base58 decodes in time that grows with the square of its length, so a megabyte of did
    would hold the robot for minutes."""
    assert len(SigningKey.generate().did) <= keys.MAX_DID
    blob = keys.seal(SigningKey.generate(), PASSPHRASE)
    blob["did"] += "1" * keys.MAX_DID
    monkeypatch.setattr(keys, "public_from_did", _never)
    with pytest.raises(keys.KeysError, match="characters long"):
        keys.unseal(blob, PASSPHRASE)


_DUCK = "\U0001f986"
_HALVES = "".join(
    chr(int.from_bytes(_DUCK.encode("utf-16-le")[i : i + 2], "little")) for i in (0, 2)
)
"""The duck as Windows' hidden prompt hands it over: its two UTF-16 halves, as two characters."""


def test_a_passphrase_with_an_emoji_typed_on_windows_opens_on_the_robot(cheap: None) -> None:
    assert len(_HALVES) == 2 and _HALVES != _DUCK
    key = SigningKey.generate()
    on_windows, on_the_robot = PASSPHRASE + _HALVES, PASSPHRASE + _DUCK
    assert keys.unseal(keys.seal(key, on_windows), on_the_robot).did == key.did
    half = PASSPHRASE + _HALVES[0]
    with pytest.raises(keys.PassphraseRefused, match="half a character"):
        keys.seal(key, half)
    with pytest.raises(keys.WrongPassphrase):
        keys.unseal(keys.seal(key, PASSPHRASE), half)


def test_the_minimum_counts_characters_after_nfc() -> None:
    """Eleven accented letters typed decomposed are twenty-two code points, and still eleven
    characters."""
    short = unicodedata.normalize("NFD", "\u010d" * (keys.MIN_PASSPHRASE - 1))
    assert len(short) >= keys.MIN_PASSPHRASE
    with pytest.raises(keys.PassphraseRefused, match="at least"):
        keys.check_passphrase(short)


# ── the passphrase file ─────────────────────────────────────────────────────────────────


def test_a_passphrase_file_gives_its_first_line_stripped(tmp_path: Path) -> None:
    path = tmp_path / "passphrase"
    path.write_bytes(("\ufeff  " + PASSPHRASE + "  \r\nnot this line\r\n").encode("utf-8"))
    path.chmod(0o600)
    assert keys.read_passphrase_file(path) == PASSPHRASE


@pytest.mark.parametrize("content", ["", "\n" + PASSPHRASE + "\n", "   \n"])
def test_a_passphrase_file_with_nothing_on_its_first_line_is_refused(
    tmp_path: Path, content: str
) -> None:
    path = tmp_path / "passphrase"
    path.write_bytes(content.encode("utf-8"))
    path.chmod(0o600)
    with pytest.raises(keys.KeysError, match="first line"):
        keys.read_passphrase_file(path)


def test_a_passphrase_file_that_is_not_there_is_a_keys_error(tmp_path: Path) -> None:
    with pytest.raises(keys.KeysError, match="could not read"):
        keys.read_passphrase_file(tmp_path / "nowhere")


@POSIX_ONLY
@pytest.mark.parametrize("mode", [0o644, 0o640, 0o604, 0o620])
def test_a_passphrase_file_other_users_can_open_is_refused(tmp_path: Path, mode: int) -> None:
    path = tmp_path / "passphrase"
    path.write_bytes((PASSPHRASE + "\n").encode("utf-8"))
    path.chmod(mode)
    with pytest.raises(keys.KeysError, match="chmod 600"):
        keys.read_passphrase_file(path)
    path.chmod(0o600)
    assert keys.read_passphrase_file(path) == PASSPHRASE


# ── the robot key and secret files ──────────────────────────────────────────────────────


def test_a_robot_key_file_round_trips(tmp_path: Path) -> None:
    key = SigningKey.generate()
    path = tmp_path / "keys" / "robot.json"
    keys.write_robot_key(path, key)
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw == {"version": keys.VERSION, "did": key.did, "seed": key.seed.hex()}
    assert keys.read_robot_key(path).seed == key.seed


def _robot_file(key: SigningKey, **changed: Any) -> str:
    raw: dict[str, Any] = {"version": keys.VERSION, "did": key.did, "seed": key.seed.hex()}
    raw.update(changed)
    return json.dumps({k: v for k, v in raw.items() if v is not DROP})


_KEY = SigningKey.generate()
_DIGITS = SigningKey(bytes.fromhex("1" * 2 * SEED_BYTES))
"""A key whose seed in hex is all digits, so a JSON number of the same digits once made it too."""


@pytest.mark.parametrize(
    ("content", "said"),
    [
        (_robot_file(_KEY, did=SigningKey.generate().did), "its did is not the key its seed"),
        (_robot_file(_KEY, did=DROP), "its did is not the key its seed"),
        (_robot_file(_KEY, seed=_KEY.seed.hex()[:-2]), "no readable seed"),
        (_robot_file(_KEY, seed=DROP), "no readable seed"),
        (_robot_file(_DIGITS, seed=int("1" * 2 * SEED_BYTES)), "no readable seed"),
        (
            _robot_file(_KEY, seed=" ".join(_KEY.seed.hex()[i : i + 2] for i in range(0, 64, 2))),
            "no readable seed",
        ),
        (_robot_file(_KEY, version=keys.VERSION + 1), "newer than this quackd"),
        (_robot_file(_KEY, version=DROP), "no version"),
        ("[]", "expected an object"),
        ("{not json", "not valid JSON"),
    ],
    ids=[
        "another-did",
        "no-did",
        "short-seed",
        "no-seed",
        "seed-a-number",
        "seed-with-spaces",
        "newer",
        "no-version",
        "not-an-object",
        "not-json",
    ],
)
def test_a_robot_key_file_quackd_cannot_trust_is_refused(
    tmp_path: Path, content: str, said: str
) -> None:
    path = tmp_path / "robot.json"
    path.write_bytes(content.encode("utf-8"))
    with pytest.raises(keys.KeysError, match=said):
        keys.read_robot_key(path)


@pytest.mark.parametrize(
    "content",
    [
        "[" * (keys.MAX_FILE_BYTES // 2),
        '{"version": ' + "1" * (sys.get_int_max_str_digits() + 1) + "}",
        " " * (keys.MAX_FILE_BYTES + 1),
    ],
    ids=["nested", "a-number-past-the-digit-limit", "too-large"],
)
def test_a_key_file_no_reader_should_parse_is_a_keys_error(tmp_path: Path, content: str) -> None:
    path = tmp_path / "owner.json"
    path.write_bytes(content.encode("utf-8"))
    for read in (keys.read_robot_key, keys.read_sealed):
        with pytest.raises(keys.KeysError):
            read(path)


def test_a_secret_replaces_the_file_whole_and_leaves_no_temporary_file(tmp_path: Path) -> None:
    path = tmp_path / "robot.env"
    path.write_bytes(b"OLD=1\n")
    if os.name != "nt":
        path.chmod(0o644)
    keys.write_secret(path, "LOCAL_API_KEY=x\n")
    keys.write_secret(path, {"port": 1})
    assert json.loads(path.read_text(encoding="utf-8")) == {"port": 1}
    assert [p.name for p in tmp_path.iterdir()] == [path.name]
    if os.name != "nt":
        assert _mode(path) == 0o600, "the rename carries the temporary file's mode onto the file"


def test_a_secret_that_cannot_be_written_is_a_keys_error_and_leaves_nothing(
    tmp_path: Path,
) -> None:
    taken = tmp_path / "owner.json"
    taken.mkdir()  # a folder where the file goes, so the rename fails on every system
    with pytest.raises(keys.KeysError, match="could not write"):
        keys.write_secret(taken, {"seed": "never on disk"})
    assert [p.name for p in tmp_path.iterdir()] == [taken.name]


def test_a_secret_interrupted_mid_write_leaves_nothing_behind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ctrl-C after the secret is in the file and before it is in place: no temporary file holding
    it is left, and no half-written key either."""

    def interrupted(fd: int) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(keys.os, "fsync", interrupted)
    for replace in (True, False):
        with pytest.raises(KeyboardInterrupt):
            keys.write_secret(tmp_path / "robot.env", "LOCAL_API_KEY=x\n", replace=replace)
        assert list(tmp_path.iterdir()) == []
    with pytest.raises(keys.KeysError, match="UTF-8"):
        keys.write_secret(tmp_path / "robot.env", "LOCAL_API_KEY=" + chr(0xDC80))
    assert list(tmp_path.iterdir()) == []


def test_an_exclusive_write_never_writes_over_a_file_or_a_link(tmp_path: Path) -> None:
    path = tmp_path / "owner.json"
    path.write_bytes(b"kept")
    with pytest.raises(keys.KeyExists):
        keys.write_secret(path, {"seed": "new"}, replace=False)
    assert path.read_bytes() == b"kept"
    if os.name != "nt":  # Windows makes links only with a privilege
        link = tmp_path / "robot.json"
        link.symlink_to(tmp_path / "nowhere")
        with pytest.raises(keys.KeyExists):
            keys.write_secret(link, {"seed": "new"}, replace=False)
        assert link.is_symlink() and not (tmp_path / "nowhere").exists()


# ── the robot's directory ───────────────────────────────────────────────────────────────


def test_the_directory_is_the_flag_then_the_shell_then_the_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    default = Path(store.DEFAULT_DIR).expanduser().absolute()
    flag, shell = tmp_path / "flag", tmp_path / "shell"
    assert store.OnboardDir().root == default
    monkeypatch.setenv(store.ENV_DIR, str(shell))
    assert store.OnboardDir().root == shell
    assert store.OnboardDir(flag).root == flag
    monkeypatch.setenv(store.ENV_DIR, "")
    assert store.OnboardDir().root == default
    monkeypatch.chdir(tmp_path)
    assert store.OnboardDir("robot-a").root == tmp_path / "robot-a", "made absolute, for the unit"


def _as_a_dotenv_sets_it(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    """What the root command's `.env` loading leaves behind (`cli._main`): the variable set, and
    noted as the file's rather than the shell's."""
    before = set(os.environ)
    monkeypatch.setenv(store.ENV_DIR, value)
    monkeypatch.setattr(dotenv_names, "_LOADED", {})
    dotenv_names.remember(before)


def test_a_directory_only_a_dotenv_names_is_refused_and_the_flag_or_the_shell_still_choose(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cloned project's `.env` would otherwise choose which keys this robot signs with and
    which passport it serves. Refused before anything is read there, in the sentence an endpoint's
    registry is refused in, and `--dir`, or a value the shell exports, are the person's own."""
    _as_a_dotenv_sets_it(monkeypatch, str(tmp_path / "the-projects-robot"))
    assert store.named_by_dotenv()
    with pytest.raises(store.OnboardDirError) as caught:
        store.OnboardDir()
    said = str(caught.value)
    assert f"{store.ENV_DIR} comes from a .env file here" in said
    assert "export it in the shell, or pass --dir" in said
    flag = tmp_path / "flag"
    assert not store.named_by_dotenv(flag)
    assert store.OnboardDir(flag).root == flag
    exported = tmp_path / "exported"
    monkeypatch.setenv(store.ENV_DIR, str(exported))
    assert store.OnboardDir().root == exported


_PATHS = (
    "robot_yaml",
    "robot_env",
    "robot_key",
    "owner_key",
    "passport_jws",
    "control_json",
    "status_json",
    "met_log",
    "runs",
    "memory",
    "cache",
    "lock",
)
"""What the rest of `quackd onboard` reads off an `OnboardDir`."""


def test_every_path_is_its_own_inside_the_directory_and_nothing_is_made_until_asked(
    tmp_path: Path,
) -> None:
    properties = {n for n, v in vars(store.OnboardDir).items() if isinstance(v, property)}
    assert set(_PATHS) <= properties
    robot = store.OnboardDir(tmp_path / "robot")
    paths = [getattr(robot, name) for name in sorted(properties)]
    assert len(set(paths)) == len(paths)
    assert all(p.is_relative_to(robot.root) and p != robot.root for p in paths)
    assert not robot.root.exists()
    robot.ensure()
    robot.ensure()  # and again, over what is there
    for folder in (robot.root, robot.keys, robot.runs, robot.memory, robot.cache):
        assert folder.is_dir()
        if os.name != "nt":
            assert _mode(folder) == 0o700, folder


def test_the_owner_key_is_made_sealed_once_and_opened_after(tmp_path: Path, cheap: None) -> None:
    robot = store.OnboardDir(tmp_path / "robot")
    key, made = robot.owner_key_or_new(PASSPHRASE)
    assert made
    text = robot.owner_key.read_text(encoding="utf-8")
    assert key.seed.hex() not in text and b64url(key.seed) not in text
    assert keys.read_sealed(robot.owner_key)["did"] == key.did, "its did reads with no passphrase"
    again, made_again = robot.owner_key_or_new(PASSPHRASE)
    assert not made_again and again.seed == key.seed
    before = robot.owner_key.read_bytes()
    with pytest.raises(keys.WrongPassphrase):
        robot.owner_key_or_new(PASSPHRASE + "?")
    assert robot.owner_key.read_bytes() == before
    if os.name != "nt":
        assert _mode(robot.owner_key) == 0o600 and _mode(robot.keys) == 0o700


def test_an_owner_key_another_command_made_meanwhile_is_never_written_over(
    tmp_path: Path, cheap: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sealing takes a second or more at the real cost, and a second wizard can finish in it."""
    robot = store.OnboardDir(tmp_path / "robot")
    theirs = keys.seal(SigningKey.generate(), PASSPHRASE)
    sealing = store.seal

    def seal_while_another_finishes(key: SigningKey, passphrase: str) -> dict[str, Any]:
        mine = sealing(key, passphrase)
        keys.write_secret(robot.owner_key, theirs)
        return mine

    monkeypatch.setattr(store, "seal", seal_while_another_finishes)
    with pytest.raises(keys.KeyExists):
        robot.owner_key_or_new(PASSPHRASE)
    assert keys.read_sealed(robot.owner_key)["did"] == theirs["did"]


def test_a_passphrase_too_short_writes_nothing_at_all(tmp_path: Path, cheap: None) -> None:
    robot = store.OnboardDir(tmp_path / "robot")
    with pytest.raises(keys.PassphraseRefused):
        robot.owner_key_or_new("x" * (keys.MIN_PASSPHRASE - 1))
    assert not robot.root.exists()


def test_the_robot_key_is_made_once_and_read_after(tmp_path: Path) -> None:
    robot = store.OnboardDir(tmp_path / "robot")
    key, made = robot.robot_key_or_new()
    again, made_again = robot.robot_key_or_new()
    assert made and not made_again and again.seed == key.seed
    if os.name != "nt":
        assert _mode(robot.robot_key) == 0o600


def test_a_second_robot_takes_the_owner_key_only_with_its_passphrase(
    tmp_path: Path, cheap: None
) -> None:
    first, second, third = (store.OnboardDir(tmp_path / name) for name in ("a", "b", "c"))
    owner, _ = first.owner_key_or_new(PASSPHRASE)
    with pytest.raises(keys.WrongPassphrase):
        second.adopt_owner_key(first.owner_key, PASSPHRASE + "?")
    assert not second.owner_key.exists()
    assert second.adopt_owner_key(first.owner_key, PASSPHRASE).did == owner.did
    assert second.owner_key.read_bytes() == first.owner_key.read_bytes(), "the same sealed blob"
    assert second.adopt_owner_key(first.owner_key, PASSPHRASE).did == owner.did
    third.owner_key_or_new(PASSPHRASE)  # a robot of another owner
    held = third.owner_key.read_bytes()
    with pytest.raises(keys.KeysError, match="signed by another owner"):
        third.adopt_owner_key(first.owner_key, PASSPHRASE)
    assert third.owner_key.read_bytes() == held


def test_a_robot_key_file_is_not_a_sealed_owner_key(tmp_path: Path) -> None:
    path = tmp_path / "robot.json"
    keys.write_robot_key(path, SigningKey.generate())
    with pytest.raises(keys.KeysError, match="not a sealed owner key"):
        keys.read_sealed(path)
