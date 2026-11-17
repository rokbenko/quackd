"""Put the tests that failed in a CI job into the job's summary on GitHub.

Every pytest step of `.github/workflows/ci.yml` and `.github/workflows/fresh-install.yml` writes
a JUnit file (`--junitxml`), and the job's last step, which runs whenever the job was not
cancelled, hands them all to this script. It appends to the file `$GITHUB_STEP_SUMMARY` names
the ids of the tests that failed and errored in each, as pytest spells them, so a red job says
which tests on the page a person opens first rather than in a log of thousands of lines:

    python scripts/ci_failed_tests.py "$RUNNER_TEMP/junit/tests.xml" "$RUNNER_TEMP/junit/serial.xml"

With no `$GITHUB_STEP_SUMMARY`, as for a person running it by hand, it prints the same text. A
file in which every test passed adds nothing. A file that is missing, or that is not JUnit XML,
is said in the summary, and the script exits 0 whatever it found: the job's verdict is pytest's,
and this only says what pytest found. Standard library only, since it runs on whatever Python
the runner has.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

LISTED = 25
"""How many test ids one file's part of the summary names before it counts the rest: about a
screenful, and the job's log still has every one of them."""


@dataclass
class Outcome:
    """What one JUnit file says went wrong, each test by its pytest id."""

    failed: list[str] = field(default_factory=list)
    errored: list[str] = field(default_factory=list)


def node_id(classname: str, name: str, root: Path) -> str:
    """pytest's own id for a test, `tests/test_x.py::TestY::test_z`, from the dotted class name
    JUnit keeps for it (`tests.test_x.TestY`): the longest leading part that names a file under
    `root` is the module. A module that could not be collected has no class name and its own
    dotted name as the test's (`tests.test_y`), which reads as its file. A name that names no
    file is kept as it is."""
    if not classname:
        whole = Path(*name.split(".")).with_suffix(".py") if name and all(name.split(".")) else None
        return whole.as_posix() if whole and (root / whole).is_file() else name
    parts = classname.split(".")
    for cut in range(len(parts), 0, -1):
        if not all(parts[:cut]):
            continue
        module = Path(*parts[:cut]).with_suffix(".py")
        if (root / module).is_file():
            return "::".join([module.as_posix(), *parts[cut:], name])
    return f"{classname}::{name}"


def read(path: Path, root: Path) -> Outcome:
    """The failed and errored tests in one JUnit file. Raises `OSError` for a file that cannot be
    read, `ET.ParseError` for one that is not XML, and `ValueError` for XML that is not JUnit."""
    document = ET.parse(path).getroot()
    if document.tag not in ("testsuites", "testsuite"):
        raise ValueError(f"its root is <{document.tag}>, where JUnit's is <testsuites>")
    outcome = Outcome()
    for case in document.iter("testcase"):
        test = node_id(case.get("classname", ""), case.get("name", ""), root)
        if case.find("failure") is not None:
            outcome.failed.append(test)
        elif case.find("error") is not None:
            outcome.errored.append(test)
    return outcome


def _code(text: str) -> str:
    """Text in a Markdown code span whose fence is longer than any run of backticks in it, so
    nothing in a test's id or an error's words can end the span early."""
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    fence = "`" * (longest + 1)
    pad = " " if text.startswith("`") or text.endswith("`") else ""
    return f"{fence}{pad}{text}{pad}{fence}"


def _counted(outcome: Outcome) -> str:
    said = []
    if outcome.failed:
        said.append(f"{len(outcome.failed)} failed")
    if outcome.errored:
        said.append(f"{len(outcome.errored)} errored")
    return " and ".join(said)


def section(path: Path, root: Path) -> str:
    """One file's part of the summary, or nothing when every test in it passed."""
    try:
        outcome = read(path, root)
    except FileNotFoundError:
        return (
            f"No JUnit file at {_code(str(path))}: its pytest step stopped before it wrote one, "
            "or never ran.\n\n"
        )
    except (OSError, ET.ParseError, ValueError) as e:
        return f"{_code(str(path))} could not be read as JUnit XML: {_code(str(e))}\n\n"
    if not outcome.failed and not outcome.errored:
        return ""
    rows = [(test, "failed") for test in outcome.failed]
    rows += [(test, "errored") for test in outcome.errored]
    lines = [f"### Tests that failed, from {_code(path.name)}\n", f"{_counted(outcome)}:\n"]
    lines += [f"- {_code(test)} {what}" for test, what in rows[:LISTED]]
    if len(rows) > LISTED:
        lines.append(f"- and {len(rows) - LISTED} more, which the job's log names")
    return "\n".join(lines) + "\n\n"


def summary(paths: Sequence[Path], root: Path) -> str:
    return "".join(section(path, root) for path in paths)


def main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("junit", nargs="*", type=Path, help="JUnit files pytest wrote")
    args = parser.parse_args(argv)
    text = summary(args.junit, Path.cwd())
    if not text:
        return 0
    target = os.environ.get("GITHUB_STEP_SUMMARY")
    if not target:
        print(text, end="")
        return 0
    try:
        with open(target, "a", encoding="utf-8", newline="\n") as summary_file:
            summary_file.write(text)
    except OSError as e:
        # the summary is a courtesy, and the job's verdict is pytest's: say it here instead
        print(f"could not write the job's summary at {target}: {e}", file=sys.stderr)
        print(text, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
