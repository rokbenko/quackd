"""The SO-101's MuJoCo model, fetched at run time and never shipped.

TheRobotStudio's SO-ARM100 is Apache-2.0, so unlike the Microduck's model nothing in its
licence keeps these files out of Roboglot. They stay out anyway, because every upstream asset
does (CONTRIBUTING.md): what travels in the wheel and the repository is a commit hash and a
sha256 per file (`upstream_api.FILES`). The first run that needs the model fetches each file
on its own from raw.githubusercontent at the pinned commit, rather than the repository's
archive, because GitHub puts the repository at about 200 MB and these files are 16 MB of it.
It checks each file against its hash as it arrives, and only when every one matches moves the
set into `~/.roboglot/cache/so-arm100/<pin>` in one rename, with the licence notice beside it.

`ROBOGLOT_LEROBOT_SIM_ASSETS` points at the `Simulation/SO101` directory of a checkout of your
own and skips the download. A file there that does not match the pin is a warning rather than
an error, because a newer model is what someone with a checkout may want, and the model is
then reported as not pinned. Line endings do not count as a difference there, because Git for
Windows checks the model out with CRLF. `ROBOGLOT_CACHE_DIR` moves the cache.
`tests/test_lerobot_sim_assets.py` drives all of this with the network stubbed, so none of it
fetches anything or needs the physics extra.

Nothing here imports `mujoco`. This module finds files; whatever loads them imports the
physics, inside `connect()`.
"""

from __future__ import annotations

import contextlib
import functools
import hashlib
import http.client
import logging
import os
import shutil
import time
import urllib.request
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

from roboglot.transport.base import TransportError
from roboglot_lerobot.sim import upstream_api as up

log = logging.getLogger("roboglot_lerobot.sim")

CACHE_ENV = "ROBOGLOT_CACHE_DIR"
ASSETS_ENV = "ROBOGLOT_LEROBOT_SIM_ASSETS"
CACHE_SUBDIR = "so-arm100"
NOTICE_FILE = "LICENSE-NOTICE.txt"
USER_AGENT = "Roboglot (https://github.com/rokbenko/quackd)"
FILE_LIMIT = 16 * 2**20
"""Refuse any one body larger than this. The largest file is a mesh of about 2 MB; the cap is
there so a wrong URL that answers with something enormous fails fast instead of filling
memory."""
LOCK_WAIT_S = 600.0
"""How long to wait for another Roboglot filling the same cache before giving up."""
LOCK_STALE_S = 1800.0
"""A lock, or a scratch directory, that nothing has touched for this long belonged to a
process that is gone. The run holding the lock touches it after every file, so this is the
time one file may take, not the whole download."""

NOTICE = f"""These files were fetched from {up.REPO} at commit {up.PIN} and are not part of
Roboglot.

They are the SO-101 follower arm's MuJoCo model and the meshes it names, from {up.SIM_DIR}
in that repository, whose LICENSE is the Apache License, Version 2.0
(https://www.apache.org/licenses/LICENSE-2.0). Roboglot neither ships nor redistributes them.
"""


class AssetError(TransportError):
    """The model could not be fetched or does not match its pin."""


@dataclass(frozen=True)
class SO101Model:
    """Where the SO-101's model is on this machine, verified."""

    model_path: Path
    """The model file, with its meshes under `assets/` beside it, as upstream lays them out."""
    pinned: bool
    """False when `ROBOGLOT_LEROBOT_SIM_ASSETS` supplied files that differ from the pin."""

    @property
    def directory(self) -> Path:
        return self.model_path.parent


def cache_root() -> Path:
    # expanduser, because `.env.example` suggests `ROBOGLOT_CACHE_DIR=~/.roboglot/cache` and a
    # shell that does not expand it would otherwise make a directory named `~` in the cwd.
    return Path(os.environ.get(CACHE_ENV) or "~/.roboglot/cache").expanduser()


def _sha256(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()


def _mismatches(directory: Path, *, checkout: bool = False) -> list[str]:
    """Files under `directory` that are missing or differ from the pin, by their path there.

    A `checkout` is also compared as git committed it. Upstream has no `.gitattributes`, so
    Git for Windows, which converts line endings by default, checks the model out with CRLF,
    and byte for byte the pinned commit itself would read as a different model. Only a file
    with no NUL byte, which git would call text, is read that way: a mesh is binary and is
    compared as it is. The cache is always compared byte for byte, because Roboglot wrote it.

    `up.FILES` is read on every call rather than captured at import, so a test can move the
    pins onto files it built."""
    bad = []
    for rel, sha in up.FILES.items():
        path = directory / rel
        if not path.is_file():
            bad.append(rel)
            continue
        blob = path.read_bytes()
        if _sha256(blob) == sha:
            continue
        if checkout and b"\0" not in blob and _sha256(blob.replace(b"\r\n", b"\n")) == sha:
            continue
        bad.append(rel)
    return bad


def fetch(url: str, *, limit: int, timeout: float = 120.0) -> bytes:
    """The body at `url`, or an `AssetError` saying which URL and why.

    `OSError` alone is not enough: a malformed response raises an `http.client.HTTPException`
    that is not one, and would escape as a bare traceback rather than the named error. A
    connection that drops partway raises nothing at all, because `read(n)` returns what
    arrived, so the length is checked against the one the server declared. Without that, a
    dropped connection would reach the hash check and be reported as a file that does not
    match its pin, which sends someone looking for a captive portal instead of running again.
    """
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # a pinned https URL
            declared = resp.headers.get("Content-Length")
            expected = None if declared is None else int(declared)
            if expected is not None and expected > limit:
                raise AssetError(
                    f"{url} declares {declared} bytes, over the limit of {limit}. Something "
                    "other than GitHub may be answering; check the network and run again."
                )
            body = bytes(resp.read(limit + 1))
    except (OSError, http.client.HTTPException, ValueError) as e:
        raise AssetError(
            f"could not fetch {url} ({e}). Check that this machine can reach "
            "raw.githubusercontent.com and run again."
        ) from e
    if len(body) > limit:
        raise AssetError(
            f"{url} sent more than {limit} bytes. Something other than GitHub may be "
            "answering; check the network and run again."
        )
    if expected is not None and len(body) < expected:
        raise AssetError(
            f"{url} sent {len(body)} of the {expected} bytes it declared: the connection "
            "dropped partway. Check the network and run again."
        )
    return body


def _url(rel: str) -> str:
    return up.raw(f"{up.SIM_DIR}/{rel}")


def _download(into: Path, *, alive: Callable[[], None] = lambda: None) -> None:
    """Fetch every pinned file into `into`, refusing the first one that does not match.

    Each hash is checked as the file arrives, so a captive portal's page or a moved file stops
    the fetch there and names the file, rather than downloading the rest to find out. Only
    names Roboglot holds are written, so nothing upstream sends can choose a path. `alive` is
    called after every file, so the lock this runs under never looks abandoned while the
    download is still moving."""
    for rel, sha in up.FILES.items():
        url = _url(rel)
        body = fetch(url, limit=FILE_LIMIT)
        if (got := _sha256(body)) != sha:
            raise AssetError(
                f"{url} does not match the pin: its sha256 is {got}, and Roboglot recorded "
                f"{sha} when it read commit {up.PIN[:12]}. Nothing was installed. Something "
                "other than GitHub may have answered, such as a captive portal: run again on "
                "another network, and report it if it happens twice."
            )
        dest = into / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(body)
        alive()


@contextlib.contextmanager
def _locked(root: Path) -> Iterator[Callable[[], None]]:
    """Hold the cache while filling it, so two Roboglot processes do not fetch into each other.

    Two first runs at once, a second terminal say, would otherwise write into the same scratch
    directory, and each would verify files the other is still writing, which surfaces as a hash
    mismatch blaming upstream for a race at home.

    A lock nobody has touched for `LOCK_STALE_S` is taken, because its owner is gone. What
    this yields touches it, and the download calls it after every file, so a slow connection
    is never mistaken for a dead one. A lock that vanishes while it is being looked at was
    released, so the loop tries again at once. One that cannot be removed is still open in the
    process that holds it, which is how Windows says that process is alive, so the loop waits.
    The lock carries a token, and only the run whose token is in it removes it.
    """
    unwritable = f"Point {CACHE_ENV} at a directory you can write to and run again."
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise AssetError(f"could not create {root} ({e}). {unwritable}") from e
    lock = root / ".lock"
    token = f"{os.getpid()} {uuid.uuid4().hex}".encode()
    deadline = time.monotonic() + LOCK_WAIT_S
    announced = False
    while True:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            try:
                if time.time() - lock.stat().st_mtime > LOCK_STALE_S:
                    lock.unlink()  # its owner is gone
                    continue
            except FileNotFoundError:
                continue  # released while we looked: try for it again at once
            except OSError:
                pass  # still open in a live process, which Windows will not let us delete
            if time.monotonic() > deadline:
                raise AssetError(
                    f"another Roboglot has held {lock} for {LOCK_WAIT_S:.0f}s. Delete it if no "
                    "other run is filling the cache, and run again."
                ) from None
            if not announced:
                log.warning("another Roboglot is filling %s; waiting", root)
                announced = True
            time.sleep(0.5)
        except OSError as e:  # a read-only cache directory
            raise AssetError(f"could not lock {root} ({e}). {unwritable}") from e

    try:
        os.write(fd, token)
    except OSError as e:  # a full disk
        os.close(fd)
        with contextlib.suppress(OSError):
            lock.unlink()
        raise AssetError(f"could not lock {root} ({e}). {unwritable}") from e

    def alive() -> None:
        with contextlib.suppress(OSError):
            os.utime(lock)

    try:
        yield alive
    finally:
        os.close(fd)
        # only our own lock: had a run wrongly judged ours stale and taken it, removing its
        # lock here would let a third run in beside it
        with contextlib.suppress(OSError):
            if lock.read_bytes() == token:
                lock.unlink()


def _remove(path: Path) -> None:
    """Delete a directory Roboglot made, read-only files included, as far as it can.

    Windows refuses to delete a read-only file, so one file someone marked read-only would
    otherwise leave a cache that cannot be repaired, half deleted by every run that tries.
    Whatever still cannot go stays, and the rename that follows says so."""
    if not path.exists():
        return
    for p in (path, *path.rglob("*")):
        with contextlib.suppress(OSError):
            os.chmod(p, 0o700 if p.is_dir() else 0o600)
    shutil.rmtree(path, ignore_errors=True)


def _sweep(final: Path) -> None:
    """Scratch directories left by runs that died where no `except` could run.

    A closed terminal or a power cut leaves one behind, 16 MB each time. One nothing has
    written to for `LOCK_STALE_S` is judged gone by the same rule as the lock, so a run still
    downloading into its own directory is left alone."""
    now = time.time()
    for leftover in final.parent.glob(f"{final.name}.*-*"):
        with contextlib.suppress(OSError):
            newest = max(p.stat().st_mtime for p in (leftover, *leftover.rglob("*")))
            if now - newest > LOCK_STALE_S:
                _remove(leftover)


def _install(
    final: Path, fill: Callable[[Path], object], verify: Callable[[Path], list[str]]
) -> None:
    """Fill a scratch directory, check it, and only then let it be the real one.

    A file written straight to its final path would leave an interrupted first run as a
    half-filled model that the next run reports as a pin mismatch, blaming upstream for what
    was a Ctrl-C at home. The scratch directory is this process's own, so a run that took the
    lock from one it wrongly judged dead still cannot empty the directory the other is
    filling. A set already in place is renamed aside before the new one takes its name, and
    deleted after, so a file in it that will not delete cannot leave the cache half gone.
    """
    _sweep(final)
    partial = final.with_name(f"{final.name}.partial-{os.getpid()}")
    aside = final.with_name(f"{final.name}.old-{os.getpid()}")
    _remove(partial)
    try:
        partial.mkdir(parents=True, exist_ok=True)
        fill(partial)
        if bad := verify(partial):
            raise AssetError(
                f"the fetched files do not match the pin ({len(bad)} wrong: "
                f"{', '.join(bad[:3])}...). Nothing was installed. Run again, and report it "
                "if it happens twice."
            )
        (partial / NOTICE_FILE).write_text(NOTICE, encoding="utf-8")
        try:
            if final.exists():
                _remove(aside)
                os.replace(final, aside)
            os.replace(partial, final)
        except OSError as e:
            if aside.exists() and not final.exists():
                with contextlib.suppress(OSError):
                    os.replace(aside, final)  # put back what was there
            raise AssetError(
                f"could not replace {final} with the files just fetched ({e}). Something may "
                "hold a file in it open, or have made it read-only: close whatever that is, "
                "delete the directory, and run again."
            ) from e
    except BaseException:
        _remove(partial)  # Ctrl-C leaves nothing half-written
        raise
    _remove(aside)


def _ensure_notice(directory: Path) -> None:
    """The licence notice, rewritten if it went missing.

    Written only on a fresh fetch, deleting it would be permanent short of clearing the whole
    cache, and it is the thing that makes the licence claim true on disk.
    """
    notice = directory / NOTICE_FILE
    with contextlib.suppress(OSError):
        if not notice.is_file():
            notice.write_text(NOTICE, encoding="utf-8")


def cached_so101() -> SO101Model | None:
    """The SO-101's model where a connect would find it, or None when it is not there yet.

    The question `roboglot doctor` asks, and only asks: it never fetches, never waits on another
    Roboglot's lock and never writes, not even the licence notice. `ROBOGLOT_LEROBOT_SIM_ASSETS`
    is honoured as `ensure_so101` honours it, so a checkout that differs from the pin is found
    and reported as not pinned, without the warning a run gives."""
    override = os.environ.get(ASSETS_ENV)
    if override:
        directory = Path(override).expanduser()
        if not (directory / up.MODEL_FILE).is_file():
            return None
        pinned = not _mismatches(directory, checkout=True)
        return SO101Model(model_path=directory / up.MODEL_FILE, pinned=pinned)
    directory = cache_root() / CACHE_SUBDIR / up.PIN
    if _mismatches(directory):
        return None
    return SO101Model(model_path=directory / up.MODEL_FILE, pinned=True)


def ensure_so101(*, offline: bool = False) -> SO101Model:
    """The SO-101's model, fetched if it is not here yet.

    `offline=True` never touches the network: it is how a test asks whether the cache is
    usable without ever filling it.
    """
    override = os.environ.get(ASSETS_ENV)
    if override:
        directory = Path(override).expanduser()
        if not (directory / up.MODEL_FILE).is_file():
            raise AssetError(
                f"{ASSETS_ENV}={override!r} has no {up.MODEL_FILE}. Point it at the "
                f"{up.SIM_DIR} directory of an SO-ARM100 checkout, or unset it to let Roboglot "
                "fetch the pinned model."
            )
        bad = _mismatches(directory, checkout=True)
        if bad:
            log.warning(
                "%s differs from the pinned commit %s in %d file(s) (%s...): the simulated arm "
                "may not be the model Roboglot's upstream notes describe",
                ASSETS_ENV,
                up.PIN[:12],
                len(bad),
                ", ".join(bad[:3]),
            )
        return SO101Model(model_path=directory / up.MODEL_FILE, pinned=not bad)

    home = cache_root() / CACHE_SUBDIR
    directory = home / up.PIN
    if _mismatches(directory):
        if offline:
            raise AssetError(f"the SO-101 model is not in the cache at {directory} (offline)")
        with _locked(home) as alive:
            if _mismatches(directory):  # another Roboglot may have filled it while we waited
                log.warning(
                    "fetching the SO-101 model (%d files, about %d MB) from %s into %s. "
                    "Apache-2.0, never shipped with Roboglot",
                    len(up.FILES),
                    round(up.FILES_BYTES / 1e6),
                    up.REPO,
                    directory,
                )
                _install(directory, functools.partial(_download, alive=alive), _mismatches)
                # what was installed, checked where it now is: pinned=True is a claim about
                # this directory, and the scratch directory's check was about another one
                if bad := _mismatches(directory):
                    raise AssetError(
                        f"{directory} does not match the pin just after it was installed "
                        f"({len(bad)} wrong: {', '.join(bad[:3])}...). Another Roboglot may have "
                        "been filling it at the same moment: run again."
                    )
    _ensure_notice(directory)
    return SO101Model(model_path=directory / up.MODEL_FILE, pinned=True)
