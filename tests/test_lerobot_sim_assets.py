"""Fetching the SO-101's model: the pins, the hashes, the licence notice, the failures.

`quackd_lerobot/sim/assets.py` is what keeps `docs/reference/licenses.md`'s "never shipped" true for
the arm's simulator: the model and its meshes reach a user cache at run time, one file at a time,
each checked against the sha256 it was read at. Nothing here touches the network or needs the
physics extra. `fetch` is stubbed and the pins are moved onto files built in memory, so it runs
on every CI runner. The one test of `fetch` itself talks to a server on loopback.
"""

from __future__ import annotations

import contextlib
import hashlib
import http.client
import importlib.metadata
import os
import socket
import socketserver
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from quackd_lerobot.sim import assets
from quackd_lerobot.sim import upstream_api as up
from quackd_lerobot.sim.assets import (
    ASSETS_ENV,
    CACHE_SUBDIR,
    FILE_LIMIT,
    NOTICE_FILE,
    AssetError,
    cache_root,
    cached_so101,
    ensure_so101,
)

# The model is text and the meshes are binary, as upstream's are: line endings mean something
# different in each (see the checkout tests below).
BODIES = {
    up.MODEL_FILE: b"<mujoco>\n  <asset><mesh file='base.stl'/></asset>\n</mujoco>\n",
    "assets/base.stl": b"\x00\x00binary base\n\x00",
    "assets/jaw.stl": b"\x00\x00binary jaw\n\x00",
}


def _sha(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()


def _home() -> Path:
    return cache_root() / CACHE_SUBDIR


def _scratch() -> list[Path]:
    """Anything beside the installed set: a scratch directory, or an old set moved aside."""
    return list(_home().glob(f"{up.PIN}.*")) if _home().exists() else []


@pytest.fixture
def upstream(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Any]:
    """Move the pins onto a model we can build here, and answer `fetch` from memory."""
    monkeypatch.setenv("QUACKD_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.delenv(ASSETS_ENV, raising=False)
    monkeypatch.setattr(up, "FILES", {rel: _sha(blob) for rel, blob in BODIES.items()})
    served = {up.raw(f"{up.SIM_DIR}/{rel}"): blob for rel, blob in BODIES.items()}
    state: dict[str, Any] = {"served": served, "fetches": [], "limits": set()}

    def fake_fetch(url: str, *, limit: int, timeout: float = 120.0) -> bytes:
        state["fetches"].append(url)
        state["limits"].add(limit)
        if url not in state["served"]:
            raise AssetError(f"unexpected url {url}")
        blob: bytes = state["served"][url]
        return blob

    monkeypatch.setattr(assets, "fetch", fake_fetch)
    return state


# ── the pins themselves ─────────────────────────────────────────────────────────────────


def test_every_pinned_file_is_the_model_or_a_mesh_beside_it_with_a_real_sha256() -> None:
    """The table the fetcher trusts. A typo in a hash is a first run that can never finish,
    and a path outside the model's own directory is one the fetcher should never write."""
    assert len(up.PIN) == 40 and all(c in "0123456789abcdef" for c in up.PIN)
    assert up.MODEL_FILE in up.FILES
    for rel, sha in up.FILES.items():
        assert len(sha) == 64 and all(c in "0123456789abcdef" for c in sha), rel
        assert ".." not in rel and not rel.startswith("/"), rel
        assert rel == up.MODEL_FILE or (rel.startswith("assets/") and rel.endswith(".stl")), rel
    assert len(set(up.FILES.values())) == len(up.FILES), "two files cannot share one hash"


def test_the_model_and_every_mesh_come_from_raw_github_at_the_pin() -> None:
    for rel in up.FILES:
        url = assets._url(rel)
        assert url.startswith(
            f"https://raw.githubusercontent.com/TheRobotStudio/SO-ARM100/{up.PIN}/{up.SIM_DIR}/"
        ), url
        assert url.endswith("/" + rel)


# ── the happy path, and what it leaves on disk ──────────────────────────────────────────


def test_a_first_run_fetches_each_file_verifies_it_and_writes_the_licence_beside_them(
    upstream: dict[str, Any],
) -> None:
    got = ensure_so101()
    assert got.pinned
    assert got.model_path == _home() / up.PIN / up.MODEL_FILE
    for rel, blob in BODIES.items():
        assert (got.directory / rel).read_bytes() == blob
    assert sorted(upstream["fetches"]) == sorted(upstream["served"]), "one fetch per file"
    assert upstream["limits"] == {FILE_LIMIT}
    notice = (got.directory / NOTICE_FILE).read_text(encoding="utf-8")
    assert up.PIN in notice and up.REPO in notice and "Apache License" in notice
    assert not _scratch(), "nothing half-written was left behind"
    assert not (_home() / ".lock").exists(), "the lock went with the run"


def test_a_verified_cache_is_used_as_it_is_and_never_fetched_again(
    upstream: dict[str, Any],
) -> None:
    first = ensure_so101()
    upstream["fetches"].clear()
    upstream["served"].clear()  # any fetch now is an AssetError
    again = ensure_so101()
    assert again == first
    assert upstream["fetches"] == []
    assert ensure_so101(offline=True) == first


def test_a_cached_file_that_changed_is_fetched_again(upstream: dict[str, Any]) -> None:
    """The cache is checked against the pin on every run, not trusted because it exists."""
    got = ensure_so101()
    (got.directory / "assets" / "jaw.stl").write_bytes(b"edited by hand")
    with pytest.raises(AssetError, match="offline"):
        ensure_so101(offline=True)
    assert ensure_so101().pinned
    assert (got.directory / "assets" / "jaw.stl").read_bytes() == BODIES["assets/jaw.stl"]
    assert not _scratch(), "the old set was moved aside and then deleted"


def test_the_cache_is_compared_byte_for_byte_line_endings_included(
    upstream: dict[str, Any],
) -> None:
    """quackd wrote the cache, so a CRLF model there is damage, unlike in a checkout."""
    got = ensure_so101()
    got.model_path.write_bytes(BODIES[up.MODEL_FILE].replace(b"\n", b"\r\n"))
    with pytest.raises(AssetError, match="offline"):
        ensure_so101(offline=True)


def test_a_cache_with_a_read_only_file_is_repaired_rather_than_left_half_deleted(
    upstream: dict[str, Any],
) -> None:
    """Windows will not delete a read-only file, nor POSIX a file in a read-only directory. A
    repair that deleted the old set in place would stop at that file every run, having deleted
    everything else, so the old set is renamed aside whole and deleted after."""
    got = ensure_so101()
    jaw = got.directory / "assets" / "jaw.stl"
    jaw.write_bytes(b"edited by hand")
    os.chmod(jaw, 0o444)
    os.chmod(jaw.parent, 0o555)
    try:
        again = ensure_so101()
    finally:
        for p in cache_root().rglob("*"):  # so the test's own cleanup can delete it all
            with contextlib.suppress(OSError):
                os.chmod(p, 0o700 if p.is_dir() else 0o600)
    assert again.pinned
    for rel, blob in BODIES.items():
        assert (again.directory / rel).read_bytes() == blob
    assert not _scratch()


def test_the_licence_notice_comes_back_if_it_is_deleted(upstream: dict[str, Any]) -> None:
    got = ensure_so101()
    (got.directory / NOTICE_FILE).unlink()
    again = ensure_so101(offline=True)
    assert (again.directory / NOTICE_FILE).is_file()


# ── what happens when the download is wrong ─────────────────────────────────────────────


def test_a_file_that_does_not_match_its_hash_is_refused_and_nothing_is_installed(
    upstream: dict[str, Any],
) -> None:
    bad_url = up.raw(f"{up.SIM_DIR}/assets/base.stl")
    upstream["served"][bad_url] = b"<html>sign in to the wifi</html>"
    with pytest.raises(AssetError, match="does not match the pin") as caught:
        ensure_so101()
    assert "assets/base.stl" in str(caught.value), "the refusal names the file"
    assert not (_home() / up.PIN).exists(), "nothing was installed"
    assert not _scratch(), "the scratch directory was cleaned up"
    assert not list(cache_root().rglob("*.stl")), "not even the files that matched"
    with pytest.raises(AssetError, match="offline"):
        ensure_so101(offline=True)


def test_a_mismatch_stops_the_fetch_at_that_file(upstream: dict[str, Any]) -> None:
    """Each hash is checked as its file arrives, so a portal answering every request is found
    on the first one rather than after all of them."""
    first = next(iter(BODIES))
    upstream["served"][up.raw(f"{up.SIM_DIR}/{first}")] = b"not the model"
    with pytest.raises(AssetError, match="does not match the pin"):
        ensure_so101()
    assert len(upstream["fetches"]) == 1


def test_a_set_that_fails_its_final_check_is_not_installed(
    upstream: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The second check, over the scratch directory, catches what a write got wrong after the
    per-file hash passed."""
    real_download = assets._download

    def download_then_damage(into: Path, **kw: Any) -> None:
        real_download(into, **kw)
        (into / "assets" / "jaw.stl").write_bytes(b"damaged on disk")

    monkeypatch.setattr(assets, "_download", download_then_damage)
    with pytest.raises(AssetError, match="do not match the pin"):
        ensure_so101()
    assert not (_home() / up.PIN).exists()
    assert not _scratch()


def test_a_set_changed_as_it_is_installed_is_refused_rather_than_reported_as_pinned(
    upstream: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """`pinned=True` is a claim about the directory the model will load from, so it is checked
    there after the rename, not only in the scratch directory before it. Another run writing
    into the cache at the same moment is what would change it."""
    real_install = assets._install

    def install_then_race(final: Path, *a: Any) -> None:
        real_install(final, *a)
        (final / "assets" / "jaw.stl").write_bytes(b"another run's half-written file")

    monkeypatch.setattr(assets, "_install", install_then_race)
    with pytest.raises(AssetError, match="just after it was installed") as caught:
        ensure_so101()
    assert "assets/jaw.stl" in str(caught.value)


def test_an_interrupted_first_run_leaves_nothing_for_the_next_one_to_misread(
    upstream: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Ctrl-C halfway through the files must not leave a half-filled model that the next run
    reports as a pin mismatch."""
    calls = {"n": 0}
    real_fetch = assets.fetch

    def interrupted(url: str, *, limit: int, timeout: float = 120.0) -> bytes:
        calls["n"] += 1
        if calls["n"] == 2:
            raise KeyboardInterrupt
        return real_fetch(url, limit=limit, timeout=timeout)

    monkeypatch.setattr(assets, "fetch", interrupted)
    with pytest.raises(KeyboardInterrupt):
        ensure_so101()
    assert not _scratch()
    assert not (_home() / up.PIN).exists()
    with pytest.raises(AssetError, match="offline"):
        ensure_so101(offline=True)
    assert not (_home() / ".lock").exists(), "the lock went with the run"


def test_a_connection_that_drops_partway_is_named_as_one_not_as_a_wrong_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A real HTTP stack, over loopback. `read(n)` returns what arrived and raises nothing
    when the server closes early, so only the declared length tells a dropped connection from
    a file that does not match its pin, and the two want different advice."""
    for var in ("NO_PROXY", "no_proxy"):  # a developer's proxy must not see loopback
        monkeypatch.setenv(var, "*")

    class Torn(socketserver.StreamRequestHandler):
        def handle(self) -> None:
            while self.rfile.readline() not in (b"\r\n", b""):
                pass
            self.wfile.write(b"HTTP/1.1 200 OK\r\nContent-Length: 800\r\n\r\n" + b"x" * 400)
            self.wfile.flush()
            self.connection.shutdown(socket.SHUT_WR)

    with socketserver.ThreadingTCPServer(("127.0.0.1", 0), Torn) as server:
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            url = f"http://127.0.0.1:{server.server_address[1]}/torn"
            with pytest.raises(AssetError, match="400 of the 800 bytes") as caught:
                assets.fetch(url, limit=1024, timeout=10)
        finally:
            server.shutdown()
    assert "dropped" in str(caught.value) and "run again" in str(caught.value)


def test_a_malformed_response_is_an_asset_error_not_a_bare_traceback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`http.client` raises its own exceptions for a response it cannot parse, and they are
    not `OSError`s."""

    def garbled(*_a: Any, **_k: Any) -> Any:
        raise http.client.BadStatusLine("HTTP/9 banana")

    monkeypatch.setattr(assets.urllib.request, "urlopen", garbled)
    with pytest.raises(AssetError, match="could not fetch"):
        assets.fetch("https://example.invalid/x", limit=1024)


def test_a_body_bigger_than_the_limit_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    class Huge:
        headers = {"Content-Length": "999999999"}

        def read(self, _n: int = -1) -> bytes:
            return b"x" * 10

        def __enter__(self) -> Huge:
            return self

        def __exit__(self, *_a: object) -> None:
            return None

    class Undeclared(Huge):
        headers: dict[str, str] = {}

        def read(self, n: int = -1) -> bytes:
            return b"x" * n

    monkeypatch.setattr(assets.urllib.request, "urlopen", lambda *_a, **_k: Huge())
    with pytest.raises(AssetError, match="declares"):
        assets.fetch("https://example.invalid/x", limit=1024)
    monkeypatch.setattr(assets.urllib.request, "urlopen", lambda *_a, **_k: Undeclared())
    with pytest.raises(AssetError, match="more than 1024 bytes"):
        assets.fetch("https://example.invalid/x", limit=1024)


def test_every_request_says_who_is_asking(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    class Fine:
        headers: dict[str, str] = {"Content-Length": "2"}

        def read(self, _n: int = -1) -> bytes:
            return b"ok"

        def __enter__(self) -> Fine:
            return self

        def __exit__(self, *_a: object) -> None:
            return None

    def urlopen(req: Any, **_k: Any) -> Fine:
        seen["agent"] = req.get_header("User-agent")
        return Fine()

    monkeypatch.setattr(assets.urllib.request, "urlopen", urlopen)
    assert assets.fetch("https://example.invalid/x", limit=1024) == b"ok"
    assert seen["agent"] == assets.USER_AGENT


# ── offline, which never fetches ────────────────────────────────────────────────────────


def test_offline_with_an_empty_cache_refuses_and_fetches_nothing(
    upstream: dict[str, Any],
) -> None:
    with pytest.raises(AssetError, match="SO-101 model is not in the cache"):
        ensure_so101(offline=True)
    assert upstream["fetches"] == []
    assert not _home().exists() or not any(_home().iterdir()), "offline wrote nothing either"


# ── your own checkout, instead of the download ──────────────────────────────────────────


def _checkout(root: Path, bodies: dict[str, bytes]) -> Path:
    for rel, blob in bodies.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(blob)
    return root


def test_a_checkout_that_matches_the_pin_is_used_as_is(
    upstream: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: Any
) -> None:
    own = _checkout(tmp_path / "SO-ARM100" / "Simulation" / "SO101", BODIES)
    monkeypatch.setenv(ASSETS_ENV, str(own))
    with caplog.at_level("WARNING"):
        got = ensure_so101()
    assert got.model_path == own / up.MODEL_FILE and got.pinned
    assert ASSETS_ENV not in caplog.text, "a matching checkout is not worth a warning"
    assert upstream["fetches"] == [], "the override skips the download"


def test_a_windows_checkout_of_the_pinned_commit_is_the_pinned_model(
    upstream: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: Any
) -> None:
    """Git for Windows checks text out with CRLF by default and upstream has no
    `.gitattributes`, so a checkout of the very commit quackd pins differs from it byte for
    byte in the model. That is not a different model, and a warning saying so would be false.
    """
    crlf = {**BODIES, up.MODEL_FILE: BODIES[up.MODEL_FILE].replace(b"\n", b"\r\n")}
    assert crlf[up.MODEL_FILE] != BODIES[up.MODEL_FILE]
    own = _checkout(tmp_path / "checkout", crlf)
    monkeypatch.setenv(ASSETS_ENV, str(own))
    with caplog.at_level("WARNING"):
        got = ensure_so101()
    assert got.pinned
    assert ASSETS_ENV not in caplog.text


def test_line_endings_changed_in_a_mesh_are_a_difference(
    upstream: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: Any
) -> None:
    """A mesh is binary, which git never converts, so a CR added to one is damage."""
    rel = "assets/base.stl"
    damaged = {**BODIES, rel: BODIES[rel].replace(b"\n", b"\r\n")}
    assert damaged[rel] != BODIES[rel]
    own = _checkout(tmp_path / "checkout", damaged)
    monkeypatch.setenv(ASSETS_ENV, str(own))
    with caplog.at_level("WARNING"):
        got = ensure_so101()
    assert not got.pinned
    assert rel in caplog.text


def test_a_checkout_that_differs_warns_and_is_not_reported_as_pinned(
    upstream: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: Any
) -> None:
    """A newer model is exactly what someone with a checkout may want, so this warns and
    carries on. What it must not do is claim the model is the pinned one."""
    newer = {**BODIES, up.MODEL_FILE: BODIES[up.MODEL_FILE] + b"<!-- my own edit -->"}
    own = _checkout(tmp_path / "checkout", newer)
    monkeypatch.setenv(ASSETS_ENV, str(own))
    with caplog.at_level("WARNING"):
        got = ensure_so101()
    assert got.model_path == own / up.MODEL_FILE
    assert not got.pinned
    assert ASSETS_ENV in caplog.text and up.PIN[:12] in caplog.text
    assert up.MODEL_FILE in caplog.text, "the warning names what differs"
    assert not (own / NOTICE_FILE).exists(), "quackd does not write into your checkout"
    assert upstream["fetches"] == []


def test_a_checkout_without_the_model_says_where_to_point_it(
    upstream: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ASSETS_ENV, str(tmp_path / "empty"))
    with pytest.raises(AssetError, match=up.SIM_DIR) as caught:
        ensure_so101()
    assert up.MODEL_FILE in str(caught.value)


# ── the cache directory itself ──────────────────────────────────────────────────────────


def test_a_tilde_in_the_cache_variable_is_a_home_directory_not_a_folder_named_tilde(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("QUACKD_CACHE_DIR", "~/.quackd/cache")
    assert cache_root() == tmp_path / ".quackd" / "cache"
    assert "~" not in str(cache_root())


def test_a_second_quackd_filling_the_same_cache_waits_rather_than_racing(
    upstream: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    _home().mkdir(parents=True, exist_ok=True)
    (_home() / ".lock").touch()
    monkeypatch.setattr(assets, "LOCK_WAIT_S", 0.2)
    with pytest.raises(AssetError, match="has held"):
        ensure_so101()
    assert upstream["fetches"] == [], "it never fetched under someone else's lock"


def test_a_run_that_waited_uses_what_the_other_quackd_filled(
    upstream: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The cache is checked again once the lock is ours, because whoever held it was most
    likely filling the same directory."""
    ensure_so101()
    upstream["fetches"].clear()
    real_mismatches = assets._mismatches
    calls = {"n": 0}

    def stale_first_look(directory: Path) -> list[str]:
        calls["n"] += 1
        return ["as if not there yet"] if calls["n"] == 1 else real_mismatches(directory)

    monkeypatch.setattr(assets, "_mismatches", stale_first_look)
    assert ensure_so101().pinned
    assert upstream["fetches"] == []


def test_a_lock_left_behind_by_a_dead_process_is_taken(upstream: dict[str, Any]) -> None:
    _home().mkdir(parents=True, exist_ok=True)
    lock = _home() / ".lock"
    lock.touch()
    old = time.time() - 3 * 3600
    os.utime(lock, (old, old))
    assert ensure_so101().pinned, "the stale lock was cleared rather than waited on"
    assert not lock.exists()


def test_a_lock_released_while_it_is_being_looked_at_is_taken_at_once(
    upstream: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other run can finish between our failed create and our look at the lock's age: the
    second terminal, the case the lock is for. That is a free lock, not an error and not a
    reason to wait."""
    _home().mkdir(parents=True, exist_ok=True)
    lock = _home() / ".lock"
    lock.touch()
    real_stat = Path.stat

    def released(self: Path, *a: Any, **kw: Any) -> os.stat_result:
        if self == lock and os.path.exists(lock):
            os.unlink(lock)  # the other run finishes this instant
        return real_stat(self, *a, **kw)

    def no_wait(_s: float) -> None:
        raise AssertionError("waited on a lock that had already been released")

    monkeypatch.setattr(Path, "stat", released)
    monkeypatch.setattr(assets.time, "sleep", no_wait)
    assert ensure_so101().pinned


def test_an_old_lock_that_cannot_be_removed_is_waited_on_not_a_traceback(
    upstream: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Windows will not delete a file another process has open, so a lock that looks old but
    will not go belongs to a run that is alive, however slow, and is waited on."""
    _home().mkdir(parents=True, exist_ok=True)
    lock = _home() / ".lock"
    lock.touch()
    old = time.time() - 2 * assets.LOCK_STALE_S
    os.utime(lock, (old, old))
    real_unlink = Path.unlink

    def in_use(self: Path, missing_ok: bool = False) -> None:
        if self == lock:
            raise PermissionError(13, "in use by another process", str(self))
        real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", in_use)
    monkeypatch.setattr(assets, "LOCK_WAIT_S", 0.2)
    with pytest.raises(AssetError, match="has held"):
        ensure_so101()
    assert upstream["fetches"] == []


def test_a_slow_download_keeps_its_lock_fresh(
    upstream: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A lock is judged stale by its age, so the run holding it touches it after every file.
    Otherwise a download slower than `LOCK_STALE_S` in all would have its lock taken, and a
    second run would fill the cache beside it."""
    lock = _home() / ".lock"
    ages: list[float] = []
    real_fetch = assets.fetch

    def slow(url: str, *, limit: int, timeout: float = 120.0) -> bytes:
        ages.append(time.time() - lock.stat().st_mtime)
        old = time.time() - 2 * assets.LOCK_STALE_S
        os.utime(lock, (old, old))  # as if this one file had taken that long
        return real_fetch(url, limit=limit, timeout=timeout)

    monkeypatch.setattr(assets, "fetch", slow)
    assert ensure_so101().pinned
    assert len(ages) == len(BODIES)
    assert all(age < assets.LOCK_STALE_S for age in ages), ages


def test_a_run_removes_only_its_own_lock(
    upstream: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Had another run wrongly judged this one dead and taken the lock, removing it at the end
    would let a third run in beside that one."""
    lock = _home() / ".lock"
    real_fetch = assets.fetch

    def taken_over(url: str, *, limit: int, timeout: float = 120.0) -> bytes:
        lock.write_bytes(b"another run's token")
        return real_fetch(url, limit=limit, timeout=timeout)

    monkeypatch.setattr(assets, "fetch", taken_over)
    ensure_so101()
    assert lock.read_bytes() == b"another run's token"


def test_a_scratch_directory_left_by_a_dead_run_is_swept_and_a_live_one_is_not(
    upstream: dict[str, Any],
) -> None:
    """A closed terminal skips every `except`, so its scratch directory stays, 16 MB a time.
    One nothing has written to for `LOCK_STALE_S` is gone by the lock's own rule. One written
    to just now is another run's, which a run that wrongly took the lock must not empty."""
    final = _home() / up.PIN
    dead = final.with_name(f"{up.PIN}.partial-{os.getpid() + 1}")
    live = final.with_name(f"{up.PIN}.partial-{os.getpid() + 2}")
    for d in (dead, live):
        (d / "assets").mkdir(parents=True)
        (d / "assets" / "base.stl").write_bytes(b"half")
    old = time.time() - 2 * assets.LOCK_STALE_S
    for p in (dead / "assets" / "base.stl", dead / "assets", dead):
        os.utime(p, (old, old))
    assert ensure_so101().pinned
    assert not dead.exists()
    assert (live / "assets" / "base.stl").read_bytes() == b"half"


def test_a_cache_that_cannot_be_made_says_which_variable_moves_it(
    upstream: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A cache path that is a file, or on a read-only disk, is refused with the variable that
    moves it rather than escaping as a bare OSError."""
    in_the_way = tmp_path / "not-a-directory"
    in_the_way.write_text("a file where the cache should be", encoding="utf-8")
    monkeypatch.setenv("QUACKD_CACHE_DIR", str(in_the_way))
    with pytest.raises(AssetError, match="QUACKD_CACHE_DIR"):
        ensure_so101()
    assert upstream["fetches"] == []


# ── what doctor says, which only looks ──────────────────────────────────────────────────


def test_looking_for_the_model_fetches_nothing_and_writes_nothing(
    upstream: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: Any
) -> None:
    """`quackd doctor` asks whether the model is here, and asking must not be the thing that
    fetches it, waits on another quackd's lock, or writes into somebody's checkout."""
    assert cached_so101() is None
    assert upstream["fetches"] == []
    assert not _home().exists(), "looking made the cache"
    first = ensure_so101()
    upstream["fetches"].clear()
    (first.directory / NOTICE_FILE).unlink()
    assert cached_so101() == first
    assert upstream["fetches"] == []
    assert not (first.directory / NOTICE_FILE).exists(), "looking wrote the licence notice"
    (first.directory / up.MODEL_FILE).write_bytes(b"<mujoco/>")
    assert cached_so101() is None, "a cache that no longer matches the pin is not the model"

    newer = {**BODIES, up.MODEL_FILE: BODIES[up.MODEL_FILE] + b"<!-- my own edit -->"}
    own = _checkout(tmp_path / "checkout", newer)
    monkeypatch.setenv(ASSETS_ENV, str(own))
    with caplog.at_level("WARNING"):
        found = cached_so101()
    assert found is not None and found.model_path == own / up.MODEL_FILE and not found.pinned
    assert ASSETS_ENV not in caplog.text, "doctor reports the difference; it does not warn"
    monkeypatch.setenv(ASSETS_ENV, str(tmp_path / "empty"))
    assert cached_so101() is None


def test_doctors_row_for_the_simulator_names_the_extra_the_pin_and_the_cache(
    upstream: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Doctor asks every installed adapter for its rows with no robot named, so the row says
    only what is true of this machine: MuJoCo's version, read from the installer's metadata
    rather than by importing it, the SO-101 model's pin, and whether it is in the cache. It
    makes no GL context, because it never imports MuJoCo at all: that check is the simulator's
    own connect, which `doctor --robot NAME` reaches for a registered simulator."""
    from quackd_lerobot import doctor_rows
    from quackd_lerobot.sim import SIM_EXTRA

    monkeypatch.setitem(sys.modules, "mujoco", None)  # any import of it now fails
    real_version = importlib.metadata.version

    def version(dist: str) -> str:
        if dist == "mujoco":
            return "3.99.0"
        return real_version(dist)

    monkeypatch.setattr(importlib.metadata, "version", version)
    (row,) = doctor_rows()
    assert (row.name, row.status) == ("lerobot:mujoco", "mujoco 3.99.0")
    assert row.note == (
        f"SO-ARM100 at {up.PIN[:7]}: not in the cache yet, and the first connect fetches it"
    )
    assert not row.found
    assert upstream["fetches"] == [], "doctor fetched the model"

    model = ensure_so101()
    (row,) = doctor_rows()
    assert row.note == f"SO-ARM100 at {up.PIN[:7]}: in the cache at {model.directory}"
    assert row.found

    def missing(dist: str) -> str:
        raise importlib.metadata.PackageNotFoundError(dist)

    monkeypatch.setattr(importlib.metadata, "version", missing)
    (row,) = doctor_rows()
    assert row.status == f"missing ({SIM_EXTRA})" and not row.found


def test_doctors_row_for_a_checkout_never_calls_it_the_cache_or_promises_a_fetch(
    upstream: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`QUACKD_LEROBOT_SIM_ASSETS` names somebody's own checkout, which a connect uses in place
    of the cache and never fetches for. So a checkout without the model is the one thing doctor
    can catch before a connect refuses it, and a checkout at the pin is not in the cache."""
    from quackd_lerobot import doctor_rows

    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv(ASSETS_ENV, str(empty))
    (row,) = doctor_rows()
    assert "fetches" not in row.note and not row.found
    assert row.note.startswith(f"{ASSETS_ENV} points at {empty}, which has no {up.MODEL_FILE}")
    assert up.SIM_DIR in row.note and "unset it" in row.note
    with pytest.raises(AssetError):
        ensure_so101()  # the connect this row speaks for does refuse
    assert upstream["fetches"] == []

    own = _checkout(tmp_path / "checkout", BODIES)
    monkeypatch.setenv(ASSETS_ENV, str(own))
    (row,) = doctor_rows()
    assert row.note == f"SO-ARM100 at {up.PIN[:7]} from {ASSETS_ENV} at {own}"
    assert "cache" not in row.note
    assert upstream["fetches"] == []
