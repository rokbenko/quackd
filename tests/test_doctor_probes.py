"""Doctor asks its servers all at once and reads the installed packages once, and neither shows in
the report: the rows, their order and every word in them are as they were when it asked one by
one."""

from __future__ import annotations

import _thread
import threading
import time
from collections.abc import Iterator

import pytest

from roboglot import doctor


@pytest.fixture
def _no_board(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ROBOGLOT_BASE_URL", raising=False)
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


def test_a_name_with_two_addresses_is_asked_at_both_at_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The presets are `localhost`, which has two addresses, and urllib asked them one after the
    other with the whole timeout each: on Windows a closed loopback port takes all of it to say
    so, so a server listening on 127.0.0.1 alone was reached only after ::1 had used up its 1.5 s.
    This catches it with a first address whose connect waits on an event nobody sets until the
    end: the second is connected to while the first is still waiting."""
    ipv6 = (doctor.socket.AF_INET6, doctor.socket.SOCK_STREAM, 6, "", ("::1", 8000, 0, 0))
    ipv4 = (doctor.socket.AF_INET, doctor.socket.SOCK_STREAM, 6, "", ("127.0.0.1", 8000))
    release = threading.Event()

    class Connected:
        closed = False

        def close(self) -> None:
            self.closed = True

    reached = Connected()
    refused = threading.Event()

    def connect_one(info: tuple[object, ...], timeout: float | None) -> object:
        if info is ipv6:
            release.wait(60)
            refused.set()
            raise ConnectionRefusedError("::1 refused, at last")
        return reached

    monkeypatch.setattr(doctor, "_addresses", lambda host, port: [ipv6, ipv4])
    monkeypatch.setattr(doctor, "_connect_one", connect_one)
    try:
        assert doctor._connection_to(("localhost", 8000), 1.5) is reached
        assert not refused.is_set(), "the second address was asked only once the first refused"
    finally:
        release.set()
    assert reached.closed is False


def test_the_servers_are_asked_before_any_package_is_imported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The probes were asked only after the core packages and every provider's SDK had been
    imported one after another, so the second or two the probes wait on closed ports came on top
    of the imports rather than under them. This catches it by holding the first import doctor
    makes until every probe has started: asked first, they all have; asked after the imports,
    none can."""
    monkeypatch.delenv("ROBOGLOT_BASE_URL", raising=False)
    urls = _urls()
    started: list[str] = []
    everyone = threading.Event()
    held: list[bool] = []

    def records(url: str, timeout_s: float = 1.5) -> tuple[str, str]:
        started.append(url)
        if len(started) == len(urls):
            everyone.set()
        return "down", "not running"

    real_import = doctor.importlib.import_module

    def first_waits(name: str, package: str | None = None) -> object:
        if not held:
            held.append(everyone.wait(20))
        return real_import(name, package)

    monkeypatch.setattr(doctor, "_probe_llms", records)
    monkeypatch.setattr(doctor, "_probe_placement", lambda root, timeout_s=1.5: ([], "not asked"))
    monkeypatch.setattr(doctor.importlib, "import_module", first_waits)
    doctor.collect()
    assert held == [True], f"the first import came before the probes: {started}"


@pytest.mark.parametrize("module", sorted(doctor._SDK_BY_METADATA))
def test_an_sdk_is_not_imported_to_read_its_version(
    monkeypatch: pytest.MonkeyPatch, module: str
) -> None:
    """Every provider SDK and most of the optional extras were imported only to read a version
    the installer had already written down, one after another, each import costing a fraction of
    a second or more. This catches it by refusing every import and answering the metadata: the
    version comes from the metadata, and nothing was imported for it."""

    def no_import(name: str, package: str | None = None) -> object:
        raise AssertionError(f"imported {name} to read its version")

    def version(distribution: str) -> str:
        assert distribution == doctor._SDK_BY_METADATA[module], distribution
        return "9.9.9"

    monkeypatch.setattr(doctor.importlib, "import_module", no_import)
    monkeypatch.setattr(doctor.md, "version", version)
    assert doctor._installed(module) == "9.9.9"


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


def test_a_probe_that_never_ends_is_given_up_on_by_the_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The join waited for every probe however long it took, so a server that kept its reply
    going held doctor for ever under a title promising a 1.5 s timeout. This catches it with a
    probe that waits on an event nobody sets until the end, and a clock that reads the deadline
    as passed once the probe has started: the answer comes, and says the server did not
    finish, while the probe is still waiting."""
    started, release = threading.Event(), threading.Event()

    def never_ends(url: str, timeout_s: float = 1.5) -> tuple[str, str]:
        started.set()
        release.wait(60)
        return "up", "too late to be read"

    def clock() -> float:
        return doctor.PROBES_DEADLINE_S + 1 if started.is_set() else 0.0

    monkeypatch.setattr(doctor, "_probe_llms", never_ends)
    monkeypatch.setattr(doctor, "_clock", clock)
    try:
        answers = doctor._probe_together(["http://never-ends"])
        assert not release.is_set() and started.is_set()
    finally:
        release.set()
    late = f"did not finish answering in {doctor.PROBES_DEADLINE_S:g} s"
    assert answers == {"http://never-ends": ("http", late)}


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
