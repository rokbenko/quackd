"""`scripts/ci_failed_tests.py`: the tests that failed in a CI job, in the job's summary.

Every case writes a JUnit file shaped as pytest writes one (the element and attribute names a
real `--junitxml` run produced), runs the script's `main` on it and reads what it appended to
the file `$GITHUB_STEP_SUMMARY` names. None of it runs pytest or reaches the network.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "ci_failed_tests.py"

PASSED = """<?xml version="1.0" encoding="utf-8"?>
<testsuites name="pytest tests"><testsuite name="pytest" errors="0" failures="0" tests="2">
<testcase classname="tests.test_x" name="test_ok" time="0.042" />
<testcase classname="tests.test_x.TestK" name="test_p[1]" time="0.004" />
</testsuite></testsuites>
"""

FAILED = """<?xml version="1.0" encoding="utf-8"?>
<testsuites name="pytest tests"><testsuite name="pytest" errors="0" failures="2" tests="3">
<testcase classname="tests.test_x" name="test_ok" time="0.042" />
<testcase classname="tests.test_x" name="test_bad" time="0.003">
<failure message="assert 0">def test_bad():</failure></testcase>
<testcase classname="tests.test_x.TestK" name="test_p[a-b]" time="0.018">
<failure message="AssertionError">self = &lt;tests.test_x.TestK object&gt;</failure></testcase>
</testsuite></testsuites>
"""

ERRORED = """<?xml version="1.0" encoding="utf-8"?>
<testsuites name="pytest tests"><testsuite name="pytest" errors="2" failures="0" tests="2">
<testcase classname="tests.test_x" name="test_err" time="0.001">
<error message="failed on setup with &quot;RuntimeError: x&quot;">@pytest.fixture</error>
</testcase>
<testcase classname="" name="tests.test_y" time="0.000">
<error message="collection failure">ImportError while importing test module</error>
</testcase>
</testsuite></testsuites>
"""


def _script() -> ModuleType:
    """The script loaded by path, as `tests/test_upstream_drift.py` loads its own, and
    registered under its name first, which its dataclass's string annotations need."""
    spec = importlib.util.spec_from_file_location("ci_failed_tests", SCRIPT)
    assert spec and spec.loader, f"{SCRIPT} could not be loaded as a module"
    module = importlib.util.module_from_spec(spec)
    sys.modules["ci_failed_tests"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def checkout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A checkout with the two test files the JUnit above names, as the working directory the
    workflow runs the script in, and a job summary that already holds an earlier step's text."""
    root = tmp_path / "checkout"
    (root / "tests").mkdir(parents=True)
    (root / "tests" / "test_x.py").write_text("", encoding="utf-8")
    (root / "tests" / "test_y.py").write_text("", encoding="utf-8")
    monkeypatch.chdir(root)
    summary = tmp_path / "summary.md"
    summary.write_text("an earlier step's summary\n", encoding="utf-8")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    return root


def _junit(root: Path, name: str, text: str) -> Path:
    path = root.parent / "junit" / name
    path.parent.mkdir(exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _summary(root: Path) -> str:
    return (root.parent / "summary.md").read_text(encoding="utf-8")


def test_a_file_in_which_every_test_passed_adds_nothing_to_the_summary(checkout: Path) -> None:
    """A green job's summary is GitHub's own: a heading for a file with nothing in it would be a
    line on every green run that a person learns to skip, and then skips on a red one."""
    script = _script()
    assert script.main([str(_junit(checkout, "tests.xml", PASSED))]) == 0
    assert _summary(checkout) == "an earlier step's summary\n"


def test_a_failed_test_is_named_by_the_id_pytest_gives_it(checkout: Path) -> None:
    """JUnit keeps a dotted class name, `tests.test_x.TestK`, which no pytest command line takes:
    the summary names each failed test as pytest does, so it can be pasted after `pytest`."""
    script = _script()
    assert script.main([str(_junit(checkout, "tests.xml", FAILED))]) == 0
    text = _summary(checkout)
    assert text.startswith("an earlier step's summary\n"), "what was there stays"
    assert "### Tests that failed, from `tests.xml`" in text
    assert "2 failed:" in text
    assert "- `tests/test_x.py::test_bad` failed" in text
    assert "- `tests/test_x.py::TestK::test_p[a-b]` failed" in text
    assert "test_ok" not in text, "a test that passed is not named"


def test_an_error_in_setup_or_collection_is_named_as_errored(checkout: Path) -> None:
    """An error is not a failure, and a module that could not be collected has no class name in
    JUnit at all: both are named, and the module by its file."""
    script = _script()
    assert script.main([str(_junit(checkout, "serial.xml", ERRORED))]) == 0
    text = _summary(checkout)
    assert "2 errored:" in text
    assert "- `tests/test_x.py::test_err` errored" in text
    assert "- `tests/test_y.py` errored" in text


def test_a_missing_file_is_said_and_never_fails_the_job(checkout: Path) -> None:
    """The step runs whenever the job was not cancelled, so also after a sync that failed before
    pytest ran: a missing file is a line in the summary, and the exit is 0, since the job's
    verdict is pytest's and a second red step would only hide which one it was."""
    script = _script()
    missing = checkout.parent / "junit" / "never.xml"
    passed = _junit(checkout, "tests.xml", PASSED)
    assert script.main([str(missing), str(passed)]) == 0
    text = _summary(checkout)
    assert "No JUnit file at `" in text and "never.xml" in text
    assert "stopped before it wrote one" in text


@pytest.mark.parametrize(
    "body",
    ["\x00\x01 not xml at all", "<testsuites><testcase>", '<html lang="en"><body/></html>'],
    ids=["bytes", "cut-short", "html"],
)
def test_a_file_that_is_not_junit_xml_is_said_and_never_fails_the_job(
    checkout: Path, body: str
) -> None:
    """A run killed while it wrote its report leaves half a file, and a path can name something
    that is not a report at all: each is said in the summary, with the reason, and exits 0."""
    script = _script()
    assert script.main([str(_junit(checkout, "tests.xml", body))]) == 0
    text = _summary(checkout)
    assert "could not be read as JUnit XML" in text and "tests.xml" in text


def test_past_the_named_number_the_rest_are_counted(checkout: Path) -> None:
    """A change that breaks an import fails hundreds of tests at once, and a summary that named
    them all would bury the page: it names `LISTED` and counts the rest."""
    script = _script()
    over = script.LISTED + 7
    cases = "".join(
        f'<testcase classname="tests.test_x" name="test_{n}"><failure message="x"/></testcase>'
        for n in range(over)
    )
    junit = f'<testsuites><testsuite name="pytest">{cases}</testsuite></testsuites>'
    assert script.main([str(_junit(checkout, "tests.xml", junit))]) == 0
    text = _summary(checkout)
    named = [line for line in text.splitlines() if line.startswith("- `")]
    assert len(named) == script.LISTED
    assert f"{over} failed:" in text
    assert "- and 7 more, which the job's log names" in text


def test_a_backtick_in_a_test_id_does_not_end_its_code_span() -> None:
    """A parametrized id holds whatever the parameter's text is, and a code span that ends early
    turns the rest of the line into Markdown."""
    script = _script()
    assert script._code("test_p[a`b]") == "``test_p[a`b]``"
    assert script._code("`edge`") == "`` `edge` ``"
    assert script._code("x ``` y") == "````x ``` y````"


def test_without_a_job_summary_it_prints_and_a_summary_it_cannot_write_is_no_failure(
    checkout: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """By hand there is no `$GITHUB_STEP_SUMMARY`, so the same text goes to stdout; and a summary
    file that cannot be opened is said on stderr, with the text printed instead, and exits 0."""
    script = _script()
    junit = str(_junit(checkout, "tests.xml", FAILED))
    monkeypatch.delenv("GITHUB_STEP_SUMMARY")
    assert script.main([junit]) == 0
    assert "- `tests/test_x.py::test_bad` failed" in capsys.readouterr().out

    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(checkout))  # a directory, which open refuses
    assert script.main([junit]) == 0
    printed = capsys.readouterr()
    assert "could not write the job's summary" in printed.err
    assert "- `tests/test_x.py::test_bad` failed" in printed.out
