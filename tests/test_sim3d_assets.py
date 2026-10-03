"""Fetching upstream's model: the hashes, the allowlist, the licence notice, the failures.

`quackd/sim3d/assets.py` is the only code in quackd that downloads anything, it is what makes
`docs/reference/licenses.md`'s "never vendored" true, and `SECURITY.md` makes a claim about the
tarball it extracts. It had no tests: the physics tests use the puppet, which needs none of this.

Nothing here touches the network or needs the physics extra. The tarball is built in memory,
`fetch` is stubbed, and the pins are moved to match, so it runs on every CI runner.
"""

from __future__ import annotations

import hashlib
import http.client
import io
import tarfile
import time
from pathlib import Path
from typing import Any

import pytest

from quackd_microduck.sim3d import assets
from quackd_microduck.sim3d import upstream_api as up
from quackd_microduck.sim3d.assets import (
    NOTICE_FILE,
    AssetError,
    cache_root,
    ensure_microduck,
)

MESHES = {"trunk.stl": b"solid trunk", "left_shell.stl": b"solid left"}
XML = b"<mujoco><asset><mesh file='trunk.stl'/></asset></mujoco>"
POLICIES = {"alpha_walking.onnx": b"walk-bytes", "alpha_stand.onnx": b"stand-bytes"}


def _sha(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()


def _tarball(members: dict[str, bytes]) -> bytes:
    """A gzipped tar shaped like GitHub's codeload archive of the pinned commit."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, blob in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(blob)
            tar.addfile(info, io.BytesIO(blob))
    return buf.getvalue()


@pytest.fixture
def upstream(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Any]:
    """Move the pins onto a model we can build here, and answer `fetch` from memory."""
    monkeypatch.setenv("QUACKD_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.delenv("QUACKD_MICRODUCK_ASSETS", raising=False)
    monkeypatch.setattr(up, "ROBOT_XML_SHA256", _sha(XML))
    monkeypatch.setattr(up, "MESH_SHA256", {n: _sha(b) for n, b in MESHES.items()})
    monkeypatch.setattr(up, "WALK_POLICY_SHA256", _sha(POLICIES["alpha_walking.onnx"]))
    monkeypatch.setattr(up, "STAND_POLICY_SHA256", _sha(POLICIES["alpha_stand.onnx"]))
    manifest = b'{"obs": 61}'
    monkeypatch.setattr(up, "POLICY_MANIFEST_SHA256", _sha(manifest))
    policy_bodies = {**POLICIES, up.POLICY_MANIFEST.name: manifest}

    prefix = f"microduck_rl-{up.PIN}/{up.ROBOT_DIR.name}/"
    members = {prefix + up.ROBOT_XML.name: XML}
    members.update({f"{prefix}assets/{n}": b for n, b in MESHES.items()})
    state = {"tarball": _tarball(members), "policies": policy_bodies, "fetches": []}

    def fake_fetch(url: str, *, limit: int, timeout: float = 120.0) -> bytes:
        state["fetches"].append(url)  # type: ignore[attr-defined]
        if url == up.TARBALL:
            return state["tarball"]  # type: ignore[return-value]
        for name, blob in policy_bodies.items():
            if url == up.policy_url(name):
                return blob
        raise AssetError(f"unexpected url {url}")

    monkeypatch.setattr(assets, "fetch", fake_fetch)
    return state


# ── the happy path, and what it leaves on disk ──────────────────────────────────────────


def test_a_first_run_fetches_verifies_and_writes_the_licence_beside_the_files(
    upstream: dict[str, Any],
) -> None:
    got = ensure_microduck()
    assert got.pinned
    assert got.robot_xml.read_bytes() == XML
    for name, blob in MESHES.items():
        assert (got.robot_dir / "assets" / name).read_bytes() == blob
    # the notice is what makes the licence claim true on disk, so check its contents
    for directory in (got.robot_dir, got.policies_dir):
        notice = (directory / NOTICE_FILE).read_text(encoding="utf-8")
        assert up.PIN in notice and up.POLICIES_PIN in notice
        assert "creativecommons.org/licenses/by-nc-sa/4.0/" in notice
    assert not list(cache_root().glob("**/*.partial")), "nothing half-written was left behind"
    # and a second run is offline: everything it needs is already verified
    assert ensure_microduck(offline=True).robot_dir == got.robot_dir


def test_the_licence_notice_comes_back_if_it_is_deleted(upstream: dict[str, Any]) -> None:
    """It used to be written only on a fresh fetch, so deleting it was permanent short of
    clearing the cache."""
    got = ensure_microduck()
    (got.robot_dir / NOTICE_FILE).unlink()
    (got.policies_dir / NOTICE_FILE).unlink()
    again = ensure_microduck(offline=True)
    assert (again.robot_dir / NOTICE_FILE).is_file()
    assert (again.policies_dir / NOTICE_FILE).is_file()


def test_mujoco_assets_hands_over_the_xml_and_every_mesh(upstream: dict[str, Any]) -> None:
    got = ensure_microduck()
    blobs = got.mujoco_assets()
    assert blobs[up.ROBOT_XML.name] == XML
    assert {k for k in blobs if k.startswith("assets/")} == {f"assets/{n}" for n in MESHES}


# ── the tarball allowlist, which SECURITY.md makes a claim about ────────────────────────


def test_only_the_files_on_the_allowlist_are_ever_written(
    upstream: dict[str, Any], tmp_path: Path
) -> None:
    """SECURITY.md says a crafted archive cannot write outside the cache. The extractor takes
    a fixed prefix, a fixed set of names, and regular files only, so a traversal path, a
    sibling directory and a symlink all fall out. This pins that."""
    prefix = f"microduck_rl-{up.PIN}/{up.ROBOT_DIR.name}/"
    hostile = {
        prefix + up.ROBOT_XML.name: XML,
        **{f"{prefix}assets/{n}": b for n, b in MESHES.items()},
        f"{prefix}../../../evil.txt": b"nope",
        f"{prefix}assets/../../evil2.txt": b"nope",
        f"{prefix}assets/unwanted.stl": b"not on the list",
        "microduck_rl-other/robot/microduck/robot_walk.xml": b"wrong commit",
    }
    upstream["tarball"] = _tarball(hostile)
    got = ensure_microduck()
    written = sorted(p.name for p in got.robot_dir.rglob("*") if p.is_file())
    assert written == sorted([NOTICE_FILE, up.ROBOT_XML.name, *MESHES])
    assert not list(tmp_path.rglob("evil*.txt")), "nothing escaped the cache"


def test_a_symlink_member_is_not_followed(upstream: dict[str, Any]) -> None:
    """`member.isfile()` is False for a symlink, which is what keeps a link out of the cache
    on the platforms that would honour one."""
    prefix = f"microduck_rl-{up.PIN}/{up.ROBOT_DIR.name}/"
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, blob in {
            up.ROBOT_XML.name: XML,
            **{f"assets/{n}": b for n, b in MESHES.items()},
        }.items():
            info = tarfile.TarInfo(prefix + name)
            info.size = len(blob)
            tar.addfile(info, io.BytesIO(blob))
        link = tarfile.TarInfo(prefix + "assets/evil.stl")
        link.type = tarfile.SYMTYPE
        link.linkname = "/etc/passwd"
        tar.addfile(link)
    upstream["tarball"] = buf.getvalue()
    got = ensure_microduck()
    assert not (got.robot_dir / "assets" / "evil.stl").exists()


# ── what happens when the download is wrong ─────────────────────────────────────────────


def test_a_file_that_does_not_match_the_pin_is_refused_and_nothing_is_kept(
    upstream: dict[str, Any],
) -> None:
    prefix = f"microduck_rl-{up.PIN}/{up.ROBOT_DIR.name}/"
    tampered = {prefix + up.ROBOT_XML.name: XML + b" <!-- changed -->"}
    tampered.update({f"{prefix}assets/{n}": b for n, b in MESHES.items()})
    upstream["tarball"] = _tarball(tampered)
    with pytest.raises(AssetError, match="do not match the pin"):
        ensure_microduck()
    assert not list(cache_root().glob("**/*.partial")), "the scratch directory was cleaned up"
    with pytest.raises(AssetError, match="offline"):
        ensure_microduck(offline=True)  # and nothing usable was left behind


def test_a_policy_that_does_not_match_its_pin_is_refused(upstream: dict[str, Any]) -> None:
    upstream["policies"]["alpha_stand.onnx"] = b"something else"
    with pytest.raises(AssetError, match="do not match the pin"):
        ensure_microduck()


def test_an_interrupted_first_run_leaves_nothing_for_the_next_one_to_misread(
    upstream: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Files used to be written straight to their final path, so a Ctrl-C left a
    half-extracted model that the next run reported as a pin mismatch, blaming upstream for
    moving the archive."""

    def die(*_a: object, **_k: object) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(assets, "_extract_members", die)
    with pytest.raises(KeyboardInterrupt):
        ensure_microduck()
    assert not list(cache_root().glob("**/*.partial"))
    with pytest.raises(AssetError, match="offline"):
        ensure_microduck(offline=True)


def test_a_truncated_download_is_an_asset_error_not_a_bare_traceback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`fetch` caught OSError only. A truncated body raises `http.client.IncompleteRead`,
    which is not one, so a broken download escaped as a traceback rather than the named error
    the docs promise."""
    monkeypatch.setenv("QUACKD_CACHE_DIR", str(tmp_path))

    class Torn:
        headers: dict[str, str] = {}

        def read(self, _n: int = -1) -> bytes:
            raise http.client.IncompleteRead(b"half")

        def __enter__(self) -> Torn:
            return self

        def __exit__(self, *_a: object) -> None:
            return None

    monkeypatch.setattr(assets.urllib.request, "urlopen", lambda *_a, **_k: Torn())
    with pytest.raises(AssetError, match="could not fetch"):
        assets.fetch("https://example.invalid/x", limit=1024)


def test_something_that_is_not_an_archive_says_so(upstream: dict[str, Any]) -> None:
    """A captive portal answers 200 with HTML, which reaches the tar reader."""
    upstream["tarball"] = b"<html>sign in to the wifi</html>"
    with pytest.raises(AssetError, match="could not be read"):
        ensure_microduck()


def test_a_body_bigger_than_the_limit_is_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("QUACKD_CACHE_DIR", str(tmp_path))

    class Huge:
        headers = {"Content-Length": "999999999"}

        def read(self, _n: int = -1) -> bytes:
            return b"x" * 10

        def __enter__(self) -> Huge:
            return self

        def __exit__(self, *_a: object) -> None:
            return None

    monkeypatch.setattr(assets.urllib.request, "urlopen", lambda *_a, **_k: Huge())
    with pytest.raises(AssetError, match="declares"):
        assets.fetch("https://example.invalid/x", limit=1024)


def test_offline_says_which_half_of_the_cache_is_missing(upstream: dict[str, Any]) -> None:
    """These two strings are what `_cached_microduck()` prints into a skip reason, so a person
    reads them."""
    with pytest.raises(AssetError, match="model is not in the cache"):
        ensure_microduck(offline=True)
    ensure_microduck()
    import shutil

    shutil.rmtree(ensure_microduck(offline=True).policies_dir)
    with pytest.raises(AssetError, match="policies are not in the cache"):
        ensure_microduck(offline=True)


# ── your own checkout, instead of the download ──────────────────────────────────────────


def test_a_checkout_that_matches_the_pin_is_used_as_is(
    upstream: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ensure_microduck()  # fill the cache, then point the override at what it wrote
    cached = ensure_microduck(offline=True).robot_dir
    monkeypatch.setenv("QUACKD_MICRODUCK_ASSETS", str(cached))
    got = ensure_microduck(offline=True)
    assert got.robot_dir == cached and got.pinned


def test_a_checkout_that_differs_warns_and_says_the_walk_may_not_match(
    upstream: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: Any
) -> None:
    """A newer export is exactly what someone with a checkout may want, so this warns and
    carries on. What it must not do is claim the run was pinned."""
    ensure_microduck()
    cached = ensure_microduck(offline=True).robot_dir
    own = tmp_path / "checkout"
    own.mkdir()
    (own / "assets").mkdir()
    (own / up.ROBOT_XML.name).write_bytes(XML + b"<!-- my own export -->")
    for name, blob in MESHES.items():
        (own / "assets" / name).write_bytes(blob)
    monkeypatch.setenv("QUACKD_MICRODUCK_ASSETS", str(own))
    with caplog.at_level("WARNING"):
        got = ensure_microduck(offline=True)
    assert got.robot_dir == own
    assert not got.pinned, "and the state reports model_pinned False"
    assert "QUACKD_MICRODUCK_ASSETS" in caplog.text and up.PIN[:12] in caplog.text
    assert not (own / NOTICE_FILE).exists(), "quackd does not write into your checkout"
    assert cached != own


def test_a_checkout_without_the_model_names_the_directory_it_wanted(
    upstream: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("QUACKD_MICRODUCK_ASSETS", str(tmp_path / "empty"))
    with pytest.raises(AssetError, match=up.ROBOT_DIR.name):
        ensure_microduck(offline=True)


# ── the cache directory itself ──────────────────────────────────────────────────────────


def test_a_tilde_in_the_cache_variable_is_a_home_directory_not_a_folder_named_tilde(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`.env.example` suggests `QUACKD_CACHE_DIR=~/.quackd/cache`, and a shell that does not
    expand it would otherwise have made a directory called `~` in the working directory."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("QUACKD_CACHE_DIR", "~/.quackd/cache")
    assert cache_root() == tmp_path / ".quackd" / "cache"
    assert "~" not in str(cache_root())


def test_a_second_quackd_filling_the_same_cache_waits_rather_than_racing(
    upstream: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two first runs at once used to extract into one directory and verify files the other
    was still writing, which surfaced as a hash mismatch blaming upstream for a race at home.
    """
    root = cache_root()
    root.mkdir(parents=True, exist_ok=True)
    (root / ".lock").touch()
    monkeypatch.setattr(assets, "LOCK_WAIT_S", 0.2)
    with pytest.raises(AssetError, match="has held"):
        ensure_microduck()


def test_a_lock_left_behind_by_a_dead_process_is_taken(
    upstream: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    root = cache_root()
    root.mkdir(parents=True, exist_ok=True)
    lock = root / ".lock"
    lock.touch()
    import os

    old = time.time() - 3 * 3600
    os.utime(lock, (old, old))
    assert ensure_microduck().pinned, "the stale lock was cleared rather than waited on"
