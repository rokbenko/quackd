"""Doctor asks its servers all at once and reads the installed packages once, and neither shows in
the report: the rows, their order and every word in them are as they were when it asked one by
one."""

from __future__ import annotations

import _thread
import threading
import time
from collections.abc import Iterator

import pytest

from quackd import doctor


@pytest.fixture
def _no_board(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("QUACKD_BASE_URL", raising=False)
    monkeypatch.setattr(doctor, "_probe_placement", lambda root, timeout_s=1.5: ([], "not asked"))


@pytest.fixture
def _fresh_walks() -> Iterator[None]:
    doctor._packages_distributions.cache_clear()
    yield
    doctor._packages_distributions.cache_clear()


def _urls() -> list[str]:
    return [url for url in doctor.PRESETS.values() if url]


@pytest.mark.usefixtures("_no_board")
def test_every_server_is_asked_at_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each probe waits here until all of them have started, so asked one after another the first
    would wait alone until the barrier gave up on it."""
    everyone = threading.Barrier(len(_urls()), timeout=20)

    def waits_for_the_others(url: str, timeout_s: float = 1.5) -> tuple[str, str]:
        everyone.wait()
        return "down", "not running"

    monkeypatch.setattr(doctor, "_probe_llms", waits_for_the_others)
    report = doctor.collect()
    assert [s.state for s in report.servers if s.url] == ["down"] * len(_urls())


@pytest.mark.usefixtures("_no_board")
def test_the_rows_keep_their_order_whichever_answer_comes_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The slowest first, so answers arrive in exactly the reverse of the order the rows print in,
    and each row still holds its own server's answer."""
    urls = _urls()

    def slowest_first(url: str, timeout_s: float = 1.5) -> tuple[str, str]:
        time.sleep(0.05 * (len(urls) - urls.index(url)))
        return "up", f"the answer from {url}"

    monkeypatch.setattr(doctor, "_probe_llms", slowest_first)
    report = doctor.collect()
    assert [s.preset for s in report.servers] == list(doctor.PRESETS)
    for row in report.servers:
        if row.url:
            assert row.detail == f"the answer from {doctor.PRESETS[row.preset]}"


def test_a_probe_that_raises_still_fails_the_caller(monkeypatch: pytest.MonkeyPatch) -> None:
    """The real one never raises. A stand-in that does has to fail the test using it, from the
    thread it ran on, or a guard written as a raise would quietly pass."""

    def not_meant_to_be_asked(url: str, timeout_s: float = 1.5) -> tuple[str, str]:
        raise AssertionError(f"asked {url}")

    monkeypatch.setattr(doctor, "_probe_llms", not_meant_to_be_asked)
    with pytest.raises(AssertionError, match="asked http://a"):
        doctor._probe_together(["http://a"])


def test_nothing_to_ask_starts_nothing() -> None:
    assert doctor._probe_together([]) == {}


def test_ctrl_c_lands_while_a_probe_is_stuck(monkeypatch: pytest.MonkeyPatch) -> None:
    """A thread pool would hold the interrupt until every probe had given up. This is why these are
    daemon threads and a join that wakes."""
    release = threading.Event()

    def stuck(url: str, timeout_s: float = 1.5) -> tuple[str, str]:
        release.wait(60)
        return "down", "not running"

    monkeypatch.setattr(doctor, "_probe_llms", stuck)
    timer = threading.Timer(0.1, _thread.interrupt_main)
    began = time.monotonic()
    timer.start()
    try:
        with pytest.raises(KeyboardInterrupt):
            doctor._probe_together(["http://stuck"])
    finally:
        release.set()
        timer.join()
    assert time.monotonic() - began < 30, "the interrupt waited for the probe"


@pytest.mark.usefixtures("_no_board", "_fresh_walks")
def test_a_report_reads_the_installed_packages_once_at_most(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Over a second a time, and it used to be once per import name that needed it, four times in a
    report on a machine with nothing unusual installed."""
    walks: list[int] = []
    real = doctor.md.packages_distributions

    def counted() -> object:
        walks.append(1)
        return real()

    monkeypatch.setattr(doctor.md, "packages_distributions", counted)
    doctor.collect()
    doctor.collect()
    assert len(walks) <= 1, f"{len(walks)} walks of every installed package"


@pytest.mark.usefixtures("_fresh_walks")
@pytest.mark.parametrize(("module", "distribution"), [("PIL", "pillow"), ("zmq", "pyzmq")])
def test_a_name_with_one_distribution_needs_no_walk_at_all(
    module: str, distribution: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_walk() -> object:
        raise AssertionError("walked every installed package to find one version")

    monkeypatch.setattr(doctor.md, "packages_distributions", no_walk)
    assert doctor._installed(module) == doctor.md.version(distribution)


@pytest.mark.usefixtures("_fresh_walks")
def test_a_name_nothing_lists_is_still_found_by_the_walk(monkeypatch: pytest.MonkeyPatch) -> None:
    """The walk stays for what it was for: an import name whose distribution is called something
    else and which nothing here names. `yaml` is pyyaml."""
    assert "yaml" not in doctor._DISTRIBUTION_OF
    assert doctor._installed("yaml") == doctor.md.version("pyyaml")
