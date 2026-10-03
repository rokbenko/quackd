"""The Microduck's model and policies, fetched at run time and never shipped.

The meshes are CC BY-NC-SA and quackd is Apache-2.0, so they cannot travel in the wheel,
the repository or a test fixture (`docs/reference/licenses.md`). What can travel is a commit hash
and a sha256 per file: the first `--robot microduck:mujoco` downloads upstream's tarball at the
pinned commit, keeps the one directory it needs under `~/.quackd/cache`, checks every file
against the hash it was read at, writes the licence notice next to them and logs it once.
The policies are Apache-2.0 on the Hugging Face Hub and come the same way, by revision.

`QUACKD_MICRODUCK_ASSETS` points at a checkout of your own (`src/mjlab_microduck/robot/
microduck`) and skips the download; a file that does not match the pin is a warning there,
not an error, because a newer export is exactly what someone with a checkout may want.
`QUACKD_CACHE_DIR` moves the cache. `tests/test_sim3d_assets.py` drives all of this
against a tarball built in memory, with the network stubbed, so none of it needs the
physics extra and none of it fetches anything.
"""

from __future__ import annotations

import contextlib
import hashlib
import http.client
import io
import logging
import os
import shutil
import tarfile
import time
import urllib.request
import zlib
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

from quackd.transport.base import TransportError
from quackd_microduck.sim3d import upstream_api as up

log = logging.getLogger("quackd_microduck.sim3d")

CACHE_ENV = "QUACKD_CACHE_DIR"
ASSETS_ENV = "QUACKD_MICRODUCK_ASSETS"
NOTICE_FILE = "LICENSE-NOTICE.txt"
USER_AGENT = "quackd (https://github.com/rokbenko/quackd)"
TARBALL_LIMIT = 128 * 2**20
"""Refuse a body larger than this. The archive is about 10 MB; the cap is there so a wrong
URL that answers with something enormous fails fast instead of filling memory."""
POLICY_LIMIT = 16 * 2**20
LOCK_WAIT_S = 600.0
"""How long to wait for another quackd filling the same cache before giving up."""
LOCK_STALE_S = 1800.0
"""A lock older than this belonged to a process that is gone; take it."""

NOTICE = f"""These files were fetched from {up.REPO} at commit {up.PIN} and are not part of quackd.

Upstream's README says: "{up.MESH_LICENSE.name}". The code around them is Apache-2.0.
Non-commercial and share-alike terms apply to your use of these model files; quackd
neither ships nor redistributes them. https://creativecommons.org/licenses/by-nc-sa/4.0/

The policies were fetched from {up.POLICIES_REPO} at revision {up.POLICIES_PIN} and are
Apache-2.0.
"""


class AssetError(TransportError):
    """The model or a policy could not be fetched or does not match its pin."""


@dataclass(frozen=True)
class MicroduckAssets:
    """Where the model and the policies are on this machine, verified."""

    robot_dir: Path
    """Holds `robot_walk.xml` and `assets/*.stl`."""
    policies_dir: Path
    """Holds `alpha_walking.onnx`, `alpha_stand.onnx` and `manifest.json`."""
    pinned: bool
    """False when `QUACKD_MICRODUCK_ASSETS` supplied files that differ from the pin."""

    @property
    def robot_xml(self) -> Path:
        return self.robot_dir / up.ROBOT_XML.name

    def mujoco_assets(self) -> dict[str, bytes]:
        """The `assets` dict `MjModel.from_xml_string` resolves meshes and includes from."""
        out = {up.ROBOT_XML.name: self.robot_xml.read_bytes()}
        for name in up.MESH_SHA256:
            out[f"assets/{name}"] = (self.robot_dir / "assets" / name).read_bytes()
        return out


def cache_root() -> Path:
    # expanduser, because `.env.example` suggests `QUACKD_CACHE_DIR=~/.quackd/cache` and a
    # shell that does not expand it would otherwise make a directory named `~` in the cwd.
    return Path(os.environ.get(CACHE_ENV) or "~/.quackd/cache").expanduser()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _mismatches(robot_dir: Path) -> list[str]:
    """Files under `robot_dir` that are missing or differ from the pin."""
    wanted = {up.ROBOT_XML.name: up.ROBOT_XML_SHA256}
    wanted.update({f"assets/{name}": sha for name, sha in up.MESH_SHA256.items()})
    bad = []
    for rel, sha in wanted.items():
        path = robot_dir / rel
        if not path.is_file() or _sha256(path) != sha:
            bad.append(rel)
    return bad


def _policy_mismatches(policies_dir: Path) -> list[str]:
    bad = []
    for name, sha in _policy_files().items():
        path = policies_dir / name
        if not path.is_file() or _sha256(path) != sha:
            bad.append(name)
    return bad


def _policy_files() -> dict[str, str]:
    """Read through `up` on every call, not captured at import, so a test can move the pins."""
    return {
        up.WALK_POLICY.name: up.WALK_POLICY_SHA256,
        up.STAND_POLICY.name: up.STAND_POLICY_SHA256,
        up.POLICY_MANIFEST.name: up.POLICY_MANIFEST_SHA256,
    }


def fetch(url: str, *, limit: int, timeout: float = 120.0) -> bytes:
    """The body at `url`, or an `AssetError` saying which URL and why.

    `OSError` alone was not enough: a truncated response raises `http.client.IncompleteRead`
    and a captive portal's HTML raises later, in the tar reader, so a broken download escaped
    as a bare traceback rather than the named error the docs promise.
    """
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # a pinned https URL
            declared = resp.headers.get("Content-Length")
            if declared is not None and int(declared) > limit:
                raise AssetError(f"{url} declares {declared} bytes; the limit is {limit}")
            body = bytes(resp.read(limit + 1))
    except (OSError, http.client.HTTPException, ValueError) as e:
        raise AssetError(f"could not fetch {url}: {e}") from e
    if len(body) > limit:
        raise AssetError(f"{url} sent more than {limit} bytes")
    return body


def _extract_robot_dir(tarball: bytes, robot_dir: Path) -> int:
    prefix = f"microduck_rl-{up.PIN}/{up.ROBOT_DIR.name}/"
    wanted = {up.ROBOT_XML.name, *(f"assets/{name}" for name in up.MESH_SHA256)}
    try:
        return _extract_members(tarball, robot_dir, prefix, wanted)
    except (tarfile.TarError, EOFError, zlib.error, OSError) as e:
        raise AssetError(
            f"the archive from {up.TARBALL} could not be read ({type(e).__name__}: {e}): a "
            "truncated download, or something other than GitHub answering, such as a captive "
            "portal"
        ) from e


def _extract_members(tarball: bytes, robot_dir: Path, prefix: str, wanted: set[str]) -> int:
    written = 0
    with tarfile.open(fileobj=io.BytesIO(tarball), mode="r:gz") as tar:
        for member in tar.getmembers():
            if not member.isfile() or not member.name.startswith(prefix):
                continue
            rel = member.name[len(prefix) :]
            if rel not in wanted:
                continue
            source = tar.extractfile(member)
            if source is None:
                continue
            dest = robot_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(source.read())
            written += 1
    return written


@contextlib.contextmanager
def _locked(root: Path) -> Iterator[None]:
    """Hold the cache while filling it, so two quackds do not fetch into each other.

    A flock, or simply a second terminal, can start two first runs at once. Without this they
    extract into the same directory and each verifies files the other is still writing, which
    surfaces as a hash mismatch blaming upstream for a race at home.
    """
    root.mkdir(parents=True, exist_ok=True)
    lock = root / ".lock"
    deadline = time.monotonic() + LOCK_WAIT_S
    announced = False
    while True:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            age = time.time() - lock.stat().st_mtime if lock.exists() else 0.0
            if age > LOCK_STALE_S:
                lock.unlink(missing_ok=True)  # its owner is gone
                continue
            if time.monotonic() > deadline:
                raise AssetError(
                    f"another quackd has held {lock} for {LOCK_WAIT_S:.0f}s; delete it if no "
                    "other run is filling the cache"
                ) from None
            if not announced:
                log.warning("another quackd is filling %s; waiting", root)
                announced = True
            time.sleep(0.5)
        except OSError as e:  # a read-only or missing cache directory
            raise AssetError(f"could not lock {root}: {e}") from e
    try:
        yield
    finally:
        os.close(fd)
        lock.unlink(missing_ok=True)


def _install(
    final: Path, fill: Callable[[Path], object], verify: Callable[[Path], list[str]]
) -> None:
    """Fill a scratch directory, check it, and only then let it be the real one.

    Files used to be written straight to their final path, so an interrupted first run left a
    half-extracted model that the next run reported as a pin mismatch, with a message blaming
    upstream for moving the archive.
    """
    partial = final.with_name(final.name + ".partial")
    shutil.rmtree(partial, ignore_errors=True)
    try:
        partial.mkdir(parents=True, exist_ok=True)
        fill(partial)
        if bad := verify(partial):
            raise AssetError(
                f"the fetched files do not match the pin ({len(bad)} wrong: "
                f"{', '.join(bad[:3])}...). Upstream may have moved the commit's archive; "
                "please report this"
            )
        (partial / NOTICE_FILE).write_text(NOTICE, encoding="utf-8")
        shutil.rmtree(final, ignore_errors=True)
        final.parent.mkdir(parents=True, exist_ok=True)
        os.replace(partial, final)
    except BaseException:
        shutil.rmtree(partial, ignore_errors=True)  # Ctrl-C leaves nothing half-written
        raise


def _ensure_notice(directory: Path) -> None:
    """The licence notice, rewritten if it went missing.

    It used to be written only on a fresh fetch, so deleting it was permanent short of
    clearing the whole cache. It is the thing that makes the licence claim true on disk.
    """
    notice = directory / NOTICE_FILE
    with contextlib.suppress(OSError):
        if not notice.is_file():
            notice.write_text(NOTICE, encoding="utf-8")


def ensure_microduck(*, offline: bool = False) -> MicroduckAssets:
    """The model and the policies, fetched if they are not here yet.

    `offline=True` never touches the network: it is how a test asks whether the cache is
    usable without ever filling it.
    """
    policies_dir = cache_root() / "microduck-policies" / up.POLICIES_PIN
    override = os.environ.get(ASSETS_ENV)
    if override:
        robot_dir = Path(override)
        if not (robot_dir / up.ROBOT_XML.name).is_file():
            raise AssetError(
                f"{ASSETS_ENV}={override!r} has no {up.ROBOT_XML.name}: point it at "
                f"{up.ROBOT_DIR.name} inside a microduck_rl checkout"
            )
        bad = _mismatches(robot_dir)
        pinned = not bad
        if bad:
            log.warning(
                "%s differs from the pinned commit %s in %d file(s) (%s...): the walk may not "
                "match what quackd measured",
                ASSETS_ENV,
                up.PIN[:12],
                len(bad),
                ", ".join(bad[:3]),
            )
    else:
        robot_dir = cache_root() / "microduck_rl" / up.PIN
        pinned = True
        if _mismatches(robot_dir):
            if offline:
                raise AssetError("the Microduck model is not in the cache (offline)")
            with _locked(cache_root()):
                if _mismatches(robot_dir):  # another quackd may have filled it while we waited
                    log.warning(
                        "fetching the Microduck model (about 10 MB compressed, %d MB of "
                        "meshes on disk) from %s into %s. %s",
                        up.MESH_BYTES // 2**20,
                        up.REPO,
                        robot_dir,
                        up.MESH_LICENSE.name,
                    )
                    tarball = fetch(up.TARBALL, limit=TARBALL_LIMIT)

                    def unpack(into: Path, blob: bytes = tarball) -> None:
                        _extract_robot_dir(blob, into)

                    _install(robot_dir, unpack, _mismatches)
        _ensure_notice(robot_dir)
    if _policy_mismatches(policies_dir):
        if offline:
            raise AssetError("the Microduck policies are not in the cache (offline)")
        with _locked(cache_root()):
            if _policy_mismatches(policies_dir):
                log.warning("fetching the walking and standing policies from %s", up.POLICIES_REPO)
                bodies = {n: fetch(up.policy_url(n), limit=POLICY_LIMIT) for n in _policy_files()}

                def write_policies(into: Path, blobs: dict[str, bytes] = bodies) -> None:
                    for name, blob in blobs.items():
                        (into / name).write_bytes(blob)

                _install(policies_dir, write_policies, _policy_mismatches)
    _ensure_notice(policies_dir)
    return MicroduckAssets(robot_dir=robot_dir, policies_dir=policies_dir, pinned=pinned)
