"""Shared fixtures. Nothing here touches the network or needs an API key."""

from __future__ import annotations

import faulthandler
import re
import shutil
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from quackd.duckfile.parser import load_duck
from quackd.duckfile.schema import DuckFile
from quackd.transport.mock import MockTransport
from quackd.verbs.registry import VerbRegistry, default_registry

REPO = Path(__file__).resolve().parents[1]
DUCKS = REPO / "ducks"

EXIT_GRACE_S = 120


@pytest.hookimpl(trylast=True)
def pytest_unconfigure(config: pytest.Config) -> None:
    """If the interpreter has not exited two minutes after pytest is done, dump every thread
    and force the exit. `faulthandler_timeout` watches a test; nothing watches the shutdown
    after the last one, and that is where a `zmq.Context` left unclosed by a failing test
    was garbage collected into a `term()` that waits forever, which held three macOS jobs
    for six hours without ever saying what they were doing. This names the frame.

    Unconfigure rather than sessionfinish, and a flush first: the failure report is printed
    inside sessionfinish, and `_exit` flushes nothing, so arming the timer any earlier
    turned the one line that mattered into a lost buffer."""
    sys.stdout.flush()
    sys.stderr.flush()
    faulthandler.dump_traceback_later(EXIT_GRACE_S, exit=True)


@pytest.fixture(autouse=True)
def _memory_in_tmp(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A test run must never write the developer's real `~/.quackd/memory`: every test that
    runs the CLI with memory on (the default) gets a throwaway directory instead. Not inside
    `tmp_path`: tests count the run directories they make there."""
    monkeypatch.setenv("QUACKD_MEMORY_DIR", str(tmp_path_factory.mktemp("quackd-memory")))


@pytest.fixture(autouse=True)
def _registry_in_tmp(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same rule as memory, for the same reason: a test that registers a robot must never
    write the developer's real `~/.quackd/robots.json`, and a developer who has one must never
    be running a different suite from CI because a flock they made is lying there."""
    monkeypatch.setenv("QUACKD_REGISTRY_DIR", str(tmp_path_factory.mktemp("quackd-registry")))


@pytest.fixture(autouse=True)
def _asset_cache_in_tmp(
    request: pytest.FixtureRequest,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The physics backend's downloaded model lives in `~/.quackd/cache`, and a developer who
    has one was running a different suite from CI: `ensure_microduck(offline=True)` found it
    and the tests that skip everywhere else ran here. Everything gets a throwaway cache and no
    checkout override, the Microduck's or the arm simulator's, so a skip is a skip on both
    machines.

    Except the `real_duck` tests and the arm simulator's `so101_model` tests, whose whole
    purpose is the developer's real cache or checkout. They still never fetch — an empty one
    skips them — so this decides which machine they run on, not whether they download."""
    if any(request.node.get_closest_marker(m) for m in ("real_duck", "so101_model")):
        return
    monkeypatch.setenv("QUACKD_CACHE_DIR", str(tmp_path_factory.mktemp("quackd-cache")))
    monkeypatch.delenv("QUACKD_MICRODUCK_ASSETS", raising=False)
    monkeypatch.delenv("QUACKD_LEROBOT_SIM_ASSETS", raising=False)


@pytest.fixture(autouse=True)
def _lerobot_calibration_in_tmp(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """LeRobot keeps an arm's calibration under the Hugging Face cache, and the arm simulator
    walks the same search for it. A developer who has calibrated an arm on this machine has
    that arm's file there, so a test that went looking would read the lab arm's travel and
    pass or fail on what one arm happened to record. Every step of the search points into a
    throwaway directory instead, and a test that means to walk it sets its own."""
    home = tmp_path_factory.mktemp("hf-home")
    monkeypatch.setenv("HF_LEROBOT_CALIBRATION", str(home / "lerobot" / "calibration"))
    monkeypatch.setenv("HF_LEROBOT_HOME", str(home / "lerobot"))
    monkeypatch.setenv("HF_HOME", str(home))


@pytest.fixture(autouse=True)
def _log_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """The log is on by default and goes to stderr, which CliRunner folds into `output`,
    so every CLI and acceptance test would carry pages of it in its failure message and its
    substring assertions would match by accident. Off for the suite; the tests that prove
    the default is on set `QUACKD_LOG` to an empty string themselves (an empty value is
    on, and unlike `delenv` it also shields them from a developer's own `.env`)."""
    monkeypatch.setenv("QUACKD_LOG", "0")


@pytest.fixture(autouse=True)
def _no_model_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """`QUACKD_LLM` pins the pilot, and `QUACKD_DECISION_LLM` switches a decision LLM on for
    every run. The CLI loads a developer's `.env` in its root callback, so one line in an
    untracked file could make half this suite assert against a pilot nobody chose, or pay for
    a stepper nobody asked for. Empty reads as unset everywhere any of them is consumed, and
    unlike `delenv` it survives `load_dotenv`, which does not overwrite a name already in the
    environment. The tests that exercise them set them themselves."""
    monkeypatch.setenv("QUACKD_LLM", "")
    monkeypatch.setenv("QUACKD_DECISION_LLM", "")
    monkeypatch.setenv("QUACKD_DECISION_MODE", "")
    monkeypatch.setenv("QUACKD_DECISION_URL", "")


@pytest.fixture(autouse=True)
def _no_price_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """`QUACKD_PRICE` and `QUACKD_DECISION_PRICE` cost a run money, and the CLI loads a developer's
    `.env` in its root callback, so one line in an untracked file could have half this suite
    asserting against a rate nobody chose. Empty reads as unset everywhere either is consumed,
    and unlike `delenv` it survives `load_dotenv`, which does not overwrite a name already in
    the environment. The tests that exercise them set them themselves."""
    monkeypatch.setenv("QUACKD_PRICE", "")
    monkeypatch.setenv("QUACKD_DECISION_PRICE", "")


@pytest.fixture(autouse=True)
def _no_extra_body(monkeypatch: pytest.MonkeyPatch) -> None:
    """`QUACKD_EXTRA_BODY` adds fields to every request an OpenAI-compatible provider sends, so
    one line in a developer's `.env` would reach every test in this suite that reads a request
    body, and the tests asserting a field is *absent* would fail on their machine and nowhere
    else. Empty reads as unset, and unlike `delenv` it survives `load_dotenv`. The tests that
    exercise the variable set it themselves."""
    monkeypatch.setenv("QUACKD_EXTRA_BODY", "")


@pytest.fixture(autouse=True)
def _no_host_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """`QUACKD_HOST` moves every local preset's model server to another machine, and the CLI
    loads a developer's `.env` in its root callback, so one line in an untracked file would
    point this suite's local-provider tests at a Jetson on somebody's desk. Its token rides
    along for the same reason. Empty reads as unset, and unlike `delenv` it survives
    `load_dotenv`. The tests that exercise them set them themselves."""
    monkeypatch.setenv("QUACKD_HOST", "")
    monkeypatch.setenv("QUACKD_HOST_TOKEN", "")


@pytest.fixture(autouse=True)
def _no_openrouter_listing(monkeypatch: pytest.MonkeyPatch) -> None:
    """`--llm openrouter:` an id quackd does not carry reads OpenRouter's public model list, and
    keeps it for the life of the process. No test may reach the real one: CI has no business on
    the network, and a list that changes weekly would make an assertion true on one day only. So
    every test starts with an empty cache, and a fetch from openrouter.ai fails the test that
    made it. A list at any other address, a stand-in on 127.0.0.1, still goes through for real."""
    from quackd.agent.providers import openrouter

    real = openrouter.fetch_models

    def guarded(base_url: str, **kwargs: object) -> list[dict[str, object]]:
        if "openrouter.ai" in base_url:
            raise AssertionError(
                "a test reached for OpenRouter's real model list: hand it one with "
                "`listed=`, a monkeypatched `fetch_models`, or a stand-in's base URL"
            )
        return real(base_url, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(openrouter, "_LISTINGS", {})
    monkeypatch.setattr(openrouter, "fetch_models", guarded)


@pytest.fixture
def registry() -> VerbRegistry:
    return default_registry()


@pytest.fixture
def mock_transport() -> MockTransport:
    return MockTransport()


@pytest.fixture
def hello_duck() -> DuckFile:
    return load_duck(str(DUCKS / "hello-world.duck"))


@pytest.fixture
def kick_duck() -> DuckFile:
    return load_duck(str(DUCKS / "find-and-kick.duck"))


@pytest.fixture(autouse=True)
def _colour_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """`--no-color` works by setting NO_COLOR, because Typer builds a console of its own for
    every `--help` it renders and reads the variable when it does. Setting a variable is a
    side effect on the process, so the next test must not inherit it, and a developer's own
    FORCE_COLOR must not reach the suite either."""
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("FORCE_COLOR", raising=False)


def help_text(argv: list[str]) -> str:
    """Help text with its styling taken off, flattened to one line of words.

    On GitHub Actions Typer forces coloured help: `typer.rich_utils` reads GITHUB_ACTIONS when
    it is imported, which is before any fixture can say otherwise. Rich then styles a name in
    pieces, so a phrase reaches a substring check as several spans with escape sequences
    between them, and an assertion about the words fails for a reason that has nothing to do
    with the words.

    The panel borders come off for the same reason: Rich draws each row of an options table
    inside a box, so a help sentence long enough to wrap picks up a border character in the
    middle of itself. Both spellings of the border, because Rich substitutes the ASCII box on
    a legacy Windows console and the test suite runs on three operating systems.

    It lives here rather than in one test module because it was duplicated into a second
    module without the stripping, and that reached main as a red CI on five runners.
    """
    from typer.testing import CliRunner

    from quackd.cli import app

    return plain_text(CliRunner().invoke(app, argv, env={"COLUMNS": "200"}).output)


def plain_text(out: str) -> str:
    """The same, for output a caller already has: styling off, borders off, one line.

    Split out of `help_text` because an error panel needs exactly this treatment and
    nothing else about a `--help` invocation, and because the docstring above is the
    record of what copying these two regular expressions into a second module cost the
    last time somebody did it.
    """
    plain = re.sub(r"\x1b\[[0-9;]*m", "", out)
    rows = [re.sub(r"^[\u2502|]\s?|\s?[\u2502|]$", "", line) for line in plain.splitlines()]
    return " ".join(" ".join(rows).split())


REPO_RUNS = Path(__file__).resolve().parent.parent / "runs"
"""The `runs/` a person's own runs land in, which is not the suite's to write into."""


@pytest.fixture(autouse=True)
def _no_test_writes_into_the_checkout() -> Iterator[None]:
    """Fail the test that leaves a run directory in the checkout, and name it.

    `RunConfig.runs_dir` defaults to the relative string `runs`, so a config built without one
    resolves against the working directory, which under pytest is the repository. `AgentLoop`
    makes the directory in `__init__`, before the run does anything and whatever the run then
    does, so a test that only wanted to watch a config get refused still leaves one behind.

    They accumulated for weeks before anybody noticed, because each is a directory holding one
    line and nothing fails. What made them worth stopping is that they sit among a person's own
    runs, which is where `quackd log` looks and where the evidence from a real robot lives.

    Only a directory with no `summary.json` is swept up, and the same rule is why: a run that
    finished wrote one, and the suite's leavings never get that far because the loop makes the
    directory before the run does anything. Somebody who starts a real run while the suite is
    going still fails a test here, which is noise, but their run is theirs and stays.
    """
    before = {p.name for p in REPO_RUNS.iterdir()} if REPO_RUNS.is_dir() else set()
    yield
    after = {p.name for p in REPO_RUNS.iterdir()} if REPO_RUNS.is_dir() else set()
    leaked = sorted(after - before)
    swept = [n for n in leaked if not (REPO_RUNS / n / "summary.json").exists()]
    for name in swept:
        shutil.rmtree(REPO_RUNS / name, ignore_errors=True)
    assert not leaked, (
        f"this test wrote {leaked} into the checkout's runs/. Pass runs_dir=tmp_path to the "
        "RunConfig (or runs_dir= to the flock runner): the default is relative and lands here."
    )
