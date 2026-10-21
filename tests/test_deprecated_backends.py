"""A backend its adapter is retiring is said once, just before a command connects through it.

An adapter's module may declare `DEPRECATED_BACKENDS`, a backend's name mapped to the one line
that says what to use instead and the release it goes in, and
`quackd.adapters.factory.deprecated_backend` reads it. `quackd run`, `quackd doctor`,
`quackd serve-mcp` and `quackd robot list --probe` print that line on stderr just before they
connect through the backend, once however many of their robots are on it, and never for a robot
they do not connect.

No adapter quackd publishes declares one, so these tests install one that does: `retiring`, a
third party's adapter as far as quackd can tell, which is the Microduck's body under another
name and is retiring its backend `old` for `new`. `old` refuses every connect unless a test says
otherwise, so each command's line can be read against the refusal it has to come before.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from typer.testing import CliRunner

import quackd_microduck
from quackd import cli, doctor
from quackd.adapters import factory
from quackd.adapters.base import AdapterNotInstalled
from quackd.adapters.manifest import RobotManifest
from quackd.cli import app
from quackd.registry import Registry, RobotEntry, StoredFlock
from quackd.transport.base import TransportError
from quackd.transport.mock import MockTransport
from quackd_microduck import MicroduckAdapter, microduck_manifest

RETIRING_LINE = "retiring:old is deprecated and is removed in X.Y.0: use --robot retiring:new"
"""The one line the adapter below declares for the backend it is retiring."""


def _refusal(backend: str) -> str:
    return f"retiring:{backend} has nothing to connect to"


REFUSAL = _refusal("old")
"""What `retiring:old` answers every connect with, unless a test says otherwise."""


class _Body(MockTransport):
    """A backend of the adapter below: the scripted mock under the backend's own name, which
    refuses every connect when it is told to, as a backend on its way out may."""

    def __init__(self, backend: str, *, refuses: bool) -> None:
        super().__init__()
        self.name = backend
        self.refuses = refuses

    async def connect(self) -> None:
        if self.refuses:
            raise TransportError(_refusal(self.name))
        await super().connect()


def _describe(backend: str, robot_id: str | None = None) -> RobotManifest:
    return microduck_manifest(backend, robot_id or "retiring")


@pytest.fixture(autouse=True)
def retiring(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """The adapter, installed here as `retiring` the way `tests/test_policy_plumbing.py` installs a
    third party's: a module in `sys.modules`, and a row in the factory's `_installed`. The module
    is new each test, and a test that changes its `DEPRECATED_BACKENDS`, or `REFUSING`, the
    backends whose connect is refused, changes it for that test alone."""
    module = ModuleType("quackd_retiring")

    def make(backend: str, *, robot_id: str | None = None, **_: Any) -> MicroduckAdapter:
        transport = _Body(backend, refuses=backend in vars(module)["REFUSING"])
        return MicroduckAdapter(transport, robot_id=robot_id or "retiring")

    vars(module).update(
        BACKENDS=("new", "old"),
        DEPRECATED_BACKENDS={"old": RETIRING_LINE},
        REFUSING={"old"},
        describe=_describe,
        implementations=quackd_microduck.implementations,
        conditions=quackd_microduck.conditions,
        make=make,
    )
    monkeypatch.setitem(sys.modules, "quackd_retiring", module)
    installed = {**factory._installed(), "retiring": "quackd_retiring"}
    monkeypatch.setattr(factory, "_installed", lambda: installed)
    return module


def _announced(stderr: str) -> list[str]:
    """The line wherever it was printed on a line of its own, which `cli._deprecated` does without
    folding it."""
    return [line.strip() for line in stderr.splitlines() if line.strip() == RETIRING_LINE]


def _run_fake(*argv: str, runs: Path) -> Any:
    """`quackd run` on the scripted pilot, with nothing remembered and nothing drawn."""
    return CliRunner().invoke(
        app, ["run", *argv, "--llm", "fake", "--no-gif", "--no-memory", "--runs-dir", str(runs)]
    )


# ── what is declared, and how it is read ────────────────────────────────────────────────


def test_only_a_backend_its_adapter_declares_is_announced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`deprecated_backend` reads each adapter module's own `DEPRECATED_BACKENDS`. `retiring:old`
    has the line, and `retiring:new` beside it has none. Every backend of the nine adapters
    quackd publishes has none either, since none of them declares one. An adapter that is not
    installed has nothing: a command asks only about a body it has built, and refuses one that
    is not installed before that, in its own words."""
    assert factory.deprecated_backend("retiring:old") == RETIRING_LINE
    assert factory.deprecated_backend("retiring:new") is None
    published = [f"{a}:{b}" for a, backends in factory.BACKENDS.items() for b in backends]
    assert published and [s for s in published if factory.deprecated_backend(s)] == []

    def absent(adapter: str) -> object:
        raise AdapterNotInstalled(adapter, "quackd[retiring]")

    monkeypatch.setattr(factory, "_module", absent)
    assert factory.deprecated_backend(factory.RobotSpec("retiring", "old")) is None
    assert factory.deprecated_backend("retiring:old") is None


@pytest.mark.parametrize(
    ("module", "raised"),
    [
        ("quackd_broken_import", "ImportError('no module named quackd_sdk')"),
        ("quackd_broken_missing", "ModuleNotFoundError('no module named quackd_sdk')"),
        ("quackd_broken_runtime", "RuntimeError('the SDK does not run on this Python')"),
        ("quackd_broken_dll", "OSError('DLL load failed while importing _native')"),
    ],
)
def test_an_adapter_that_fails_to_import_has_no_line_whatever_it_raises(
    module: str, raised: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An import can fail with more than ImportError: a top level that raises RuntimeError, or
    an extension whose DLL will not load, which is an OSError. Whatever it raises, the adapter
    has no line, asked for by a spec already parsed and by one typed as text, which for a third
    party's adapter is parsed through its module, so the import happens there. Handed to the
    CLI among other robots, it adds nothing and stops nothing, and the retiring backend after it
    is still said. A real module on disk rather than a stand-in for `_module`, so the import is
    the one Python makes: a module whose top level raised is left out of sys.modules and
    imported again on every ask."""
    (tmp_path / f"{module}.py").write_text(f"raise {raised}\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    installed = factory._installed()
    monkeypatch.setattr(factory, "_installed", lambda: {**installed, "broken": module})

    assert factory.deprecated_backend(factory.RobotSpec("broken", "x")) is None
    assert factory.deprecated_backend("broken:x") is None
    cli._DEPRECATIONS.clear()
    cli._deprecated_backends([factory.RobotSpec("broken", "x"), "retiring:old"])
    assert cli._DEPRECATIONS == [RETIRING_LINE]
    cli._DEPRECATIONS.clear()


@pytest.mark.parametrize(
    ("declared", "line"),
    [(None, None), ({}, None), ({"old": ""}, None), ({"old": "go"}, "go")],
)
def test_a_declaration_is_a_mapping_of_a_backend_to_its_line(
    declared: Any, line: str | None, retiring: ModuleType
) -> None:
    """None and an empty mapping declare nothing, and so does an empty line."""
    vars(retiring)["DEPRECATED_BACKENDS"] = declared
    assert factory.deprecated_backend("retiring:old") == line


@pytest.mark.parametrize(
    "declared",
    [("old",), ["old"], "old", {"old": 42}, {"old": ("use", "x")}],
)
def test_a_declaration_quackd_cannot_read_is_refused_naming_the_module(
    declared: Any, retiring: ModuleType
) -> None:
    """`SIMULATOR_BACKENDS` is a tuple, so a tuple is the shape an adapter's author is likeliest
    to write here too, and read as nothing it would leave the backend they meant to retire
    connected through with no word said. So it is refused, as is a line that is not a string,
    with the module's name and the shape that is read."""
    vars(retiring)["DEPRECATED_BACKENDS"] = declared
    with pytest.raises(TypeError, match=r"quackd_retiring\.DEPRECATED_BACKENDS"):
        factory.deprecated_backend("retiring:old")


@pytest.mark.parametrize(
    "specs",
    [
        ["retiring:old", "microduck:mock", "retiring:old"],
        ["microduck:mock", "retiring:old"],
        ["microduck:mock", "retiring:new", "retiring:old"],
    ],
    ids=["first-and-third", "second", "third"],
)
def test_a_line_is_said_once_wherever_its_robots_come(specs: list[str]) -> None:
    """Every robot handed over is asked about, first or not, and a fleet of two robots on the
    retiring backend is told once. Asked again in the same command, as `robot list --probe` asks
    once for each robot it probes, the line is not said again. `_deprecated` keeps every line it
    printed for the run's saved terminal, which is what is read here."""
    cli._DEPRECATIONS.clear()
    cli._deprecated_backends(specs)
    cli._deprecated_backends(specs)
    assert cli._DEPRECATIONS == [RETIRING_LINE]
    cli._DEPRECATIONS.clear()


# ── where each command says it ──────────────────────────────────────────────────────────


def test_run_says_the_line_before_it_connects(tmp_path: Path) -> None:
    """Once, on stderr, and before the backend refuses the connect: the reader meets the way out
    before the error it explains."""
    result = _run_fake("hello-world", "--robot", "retiring:old", runs=tmp_path)
    assert result.exit_code == 1, result.output
    assert _announced(result.stderr) == [RETIRING_LINE]
    assert result.stderr.index(RETIRING_LINE) < result.stderr.index(REFUSAL)


def test_run_says_nothing_for_a_robot_it_never_connects(tmp_path: Path) -> None:
    """The line tells a reader the run is connecting through the backend, so a run says it only
    for the robot it drives, and only once nothing is left that can refuse the run. A task with
    no flock drives the first of its `--robots` and never the second, here the retiring backend,
    and a task the validator refuses connects nothing at all."""
    solo = _run_fake("hello-world", "--robots", "a=microduck:sim2d,b=retiring:old", runs=tmp_path)
    assert solo.exit_code == 0, solo.output
    assert _announced(solo.stderr) == []
    refused = _run_fake("arm-grip-check", "--robot", "retiring:old", runs=tmp_path)
    assert refused.exit_code == 1, refused.output
    assert "cannot run on retiring:old: requires gripper" in " ".join(refused.stderr.split())
    assert _announced(refused.stderr) == []


def test_a_pilot_flock_says_the_line_for_a_member_on_the_backend_wherever_it_comes(
    tmp_path: Path,
) -> None:
    """A pilot flock connects all its members at once, so the line is said for the member on the
    retiring backend, second in a task's `--robots` or third in a stored flock, once every body
    is built, which is after the flock's header and before that member refuses its connect."""
    second = _run_fake(
        "flock-hello", "--robots", "duck=microduck:mock,arm=retiring:old", runs=tmp_path
    )
    stored = tmp_path / "registry"
    registry = Registry(stored)
    for name, spec in (("a", "microduck:mock"), ("b", "microduck:sim2d"), ("c", "retiring:old")):
        registry.add_robot(RobotEntry(name=name, spec=spec))
    registry.add_flock(StoredFlock(name="trio", members=["a", "b", "c"]))
    trio = ("--flock", "trio", "--registry-dir", str(stored))
    third = _run_fake("flock-hello", *trio, runs=tmp_path)
    for result in (second, third):
        assert result.exit_code == 1, result.output
        assert _announced(result.stderr) == [RETIRING_LINE]
        # `output` is stdout and stderr as a terminal shows them, and the refusal is in the
        # flock's outcome on stdout
        assert result.output.index(RETIRING_LINE) < result.output.index(REFUSAL)


def test_a_pilot_flock_refused_before_its_members_connect_says_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The member on the retiring backend is built first and the body after it cannot be, which
    refuses the flock after its header with nothing connected. Nothing connected through the
    backend, so nothing is said: a flock says the line once every body is built, and not as
    soon as it knows its members."""
    from quackd.flock import pilots

    real = pilots.make_adapter

    def build(spec: Any, **kwargs: Any) -> Any:
        if spec.name == "arm":
            raise TransportError("the arm's camera will not open")
        return real(spec, **kwargs)

    monkeypatch.setattr(pilots, "make_adapter", build)
    result = _run_fake(
        "flock-hello", "--robots", "duck=retiring:old,arm=microduck:mock", runs=tmp_path
    )
    assert result.exit_code == 1, result.output
    assert "the arm's camera will not open" in result.stderr
    assert _announced(result.stderr) == []


def test_doctor_says_the_line_when_it_connects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With an address doctor connects, and says it first, whether the address is typed or was
    registered with the robot. On stderr under `--json` too, whose stdout stays one JSON
    document. Without an address nothing connects and nothing is said."""
    # doctor asks every local model server whether it is up, and none of this is about them
    monkeypatch.setattr(doctor, "_probe_models", lambda url, timeout_s=1.5: ("down", "not running"))
    for flags in ([], ["--json"]):
        argv = ["doctor", "--robot", "retiring:old", "--address", "tcp://127.0.0.1:9"]
        result = CliRunner().invoke(app, [*argv, *flags])
        assert result.exit_code == 1, result.output
        assert _announced(result.stderr) == [RETIRING_LINE], flags
    assert REFUSAL in json.loads(result.stdout)["robot"]["probe"]["error"]
    quiet = CliRunner().invoke(app, ["doctor", "--robot", "retiring:old"])
    assert _announced(quiet.stderr) == []
    registry = Registry(tmp_path)
    registry.add_robot(RobotEntry(name="far", spec="retiring:old", address="tcp://127.0.0.1:9"))
    registry.add_robot(RobotEntry(name="near", spec="retiring:old"))
    where = ["--registry-dir", str(tmp_path)]
    stored = CliRunner().invoke(app, ["doctor", "--robot", "far", *where])
    assert stored.exit_code == 1, stored.output
    assert _announced(stored.stderr) == [RETIRING_LINE]
    unaddressed = CliRunner().invoke(app, ["doctor", "--robot", "near", *where])
    assert _announced(unaddressed.stderr) == []


@pytest.mark.parametrize(
    ("robots", "refusing"),
    [
        ("a=retiring:old,b=retiring:old,c=retiring:new", "new"),
        ("a=microduck:mock,b=retiring:old", "old"),
        ("a=microduck:mock,b=microduck:sim2d,c=retiring:old", "old"),
    ],
    ids=["both", "second", "third"],
)
def test_serve_mcp_says_the_line_once(robots: str, refusing: str, retiring: ModuleType) -> None:
    """Through the CLI's own `_deprecated`, once however many robots are on the retiring backend
    and wherever the first of them comes, just before the server's start connects it, so after
    the robots ahead of it have connected and before the refusal that ends the start. Two robots
    on it both connect in the first case, where `old` is let connect and `new`, third, refuses,
    and the start says nothing before the second. Nothing on stdout, which is the MCP wire."""
    vars(retiring)["REFUSING"] = {refusing}
    result = CliRunner().invoke(app, ["serve-mcp", "--robots", robots, "--no-memory"])
    assert result.exit_code == 1, result.output
    assert _announced(result.stderr) == [RETIRING_LINE]
    assert cli._DEPRECATIONS == [RETIRING_LINE]
    assert result.stdout == ""
    assert result.stderr.index(RETIRING_LINE) < result.stderr.index(_refusal(refusing))


def test_serve_mcp_says_nothing_of_a_robot_whose_connect_never_comes(
    retiring: ModuleType,
) -> None:
    """The server's start connects its robots one at a time and stops at the first that fails,
    so a robot after that one is never connected, and nothing is said of it. `retiring:new` is
    retired as well here, for this test alone, and comes after `old`, whose refusal ends the
    start: `old`'s line is said, and `new`'s is not."""
    retired = "retiring:new is deprecated in this test alone"
    vars(retiring)["DEPRECATED_BACKENDS"]["new"] = retired
    result = CliRunner().invoke(
        app, ["serve-mcp", "--robots", "a=retiring:old,b=retiring:new", "--no-memory"]
    )
    assert result.exit_code == 1, result.output
    assert _announced(result.stderr) == [RETIRING_LINE]
    assert retired not in result.output


def test_a_server_started_without_the_cli_logs_the_line(caplog: pytest.LogCaptureFixture) -> None:
    """A program that calls `serve` hands it no `warn`, and has the line in its log at warning
    level, said as the start connects the robot. Building the plan connects nothing, so
    `fleet_from_flags` on its own says nothing. The runner's isolation stands in for the stdio
    the server reads and writes, as it does for the CLI's own tests."""
    from quackd.mcp_server import fleet_from_flags, serve

    def said() -> list[str]:
        return [r.getMessage() for r in caplog.records if r.name == "quackd.mcp"]

    with caplog.at_level("WARNING", logger="quackd.mcp"):
        fleet_from_flags(robot="retiring:old")
        assert said() == []
        with CliRunner().isolation(), pytest.raises((TransportError, BaseExceptionGroup)):
            serve(robot="retiring:old", memory=False)
    assert said() == [RETIRING_LINE]


def test_robot_list_probe_says_the_line_for_the_robots_it_connects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--probe` connects every robot it can, those on the retiring backend among them, so the
    line is said once on stderr however many robots are on it and wherever they come, and stdout
    stays one JSON line a robot. A robot the probe does not connect says nothing: the Microduck's
    physics backend, which the probe skips rather than load a model, is retired here for this
    test alone, and comes first."""
    retired = "microduck:mujoco is deprecated in this test alone"
    monkeypatch.setattr(quackd_microduck, "DEPRECATED_BACKENDS", {"mujoco": retired}, raising=False)
    registry = Registry(tmp_path)
    for name, spec in (
        ("a", "microduck:mujoco"),
        ("b", "microduck:mock"),
        ("c", "retiring:old"),
        ("d", "retiring:old"),
    ):
        registry.add_robot(RobotEntry(name=name, spec=spec))
    result = CliRunner().invoke(
        app, ["robot", "list", "--probe", "--json", "--registry-dir", str(tmp_path)]
    )
    assert result.exit_code == 1, result.output
    assert _announced(result.stderr) == [RETIRING_LINE]
    assert retired not in result.output
    rows = {row["name"]: row for row in map(json.loads, result.stdout.splitlines())}
    assert (rows["a"]["reachable"], rows["b"]["reachable"]) == (None, True)
    assert rows["c"]["reachable"] is False and rows["d"]["reachable"] is False
