"""`scripts/changelog_lock.py`: every released section of CHANGELOG.md keeps the bytes it was
locked with.

The real changelog is checked against the real lock, and every other case works on a copy of
the text in memory, or on copies in a scratch folder for the command line, so nothing here
writes the repository's own files. The lock hashes bytes and resolves nothing: a link inside a
released section is text to it, whether or not its target is still there.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.support import load_script

REPO = Path(__file__).resolve().parents[1]
lock_script = load_script(REPO / "scripts" / "changelog_lock.py", "changelog_lock")

TEXT = (REPO / "CHANGELOG.md").read_text(encoding="utf-8")
LOCK = json.loads((REPO / "tests" / "golden" / "changelog_released.json").read_text("utf-8"))
RELEASED = list(lock_script.released(TEXT))


def _changed_byte(text: str, version: str) -> str:
    """`text` with one character of `version`'s section, in the middle of its body, changed."""
    body = lock_script.released(text)[version]
    start = text.index(body)
    at = start + len(body) // 2
    while not text[at].isalpha():
        at += 1
    swapped = "x" if text[at] != "x" else "y"
    return text[:at] + swapped + text[at + 1 :]


def test_the_released_changelog_is_as_its_lock_holds_it() -> None:
    """The release step adds the newest section's hash. A failure here names each section that
    moved, and says how to put it back or to accept it with a reason."""
    assert lock_script.problems(TEXT, LOCK) == []
    assert list(LOCK["sections"]) == RELEASED, "the lock lists the sections in the file's order"
    assert len(RELEASED) > 40 and RELEASED[-1] == "0.1.0"
    assert "Unreleased" in lock_script.sections(TEXT)


@pytest.mark.parametrize("where", ["newest", "middle", "oldest"])
def test_one_changed_byte_in_a_released_section_names_that_version_and_no_other(
    where: str,
) -> None:
    version = {"newest": RELEASED[0], "middle": RELEASED[len(RELEASED) // 2], "oldest": "0.1.0"}[
        where
    ]
    found = lock_script.problems(_changed_byte(TEXT, version), LOCK)
    assert len(found) == 1 and found[0].startswith(f"[{version}] changed after it was released")


def test_the_date_on_a_released_heading_is_part_of_what_is_locked() -> None:
    heading = next(line for line in TEXT.splitlines() if line.startswith(f"## [{RELEASED[1]}]"))
    moved = TEXT.replace(heading, heading[:-1] + ("1" if heading[-1] != "1" else "2"), 1)
    assert [p.split("]")[0] for p in lock_script.problems(moved, LOCK)] == [f"[{RELEASED[1]}"]


def test_a_crlf_checkout_reads_the_same_bytes() -> None:
    """Git on Windows may check the file out with CRLF line ends; the lock reads LF."""
    assert lock_script.problems(TEXT.replace("\n", "\r\n"), LOCK) == []


def test_an_edit_to_unreleased_or_to_the_links_at_the_foot_is_not_the_locks() -> None:
    unreleased = TEXT.replace("## [Unreleased]\n", "## [Unreleased]\n\nA new line.\n", 1)
    assert unreleased != TEXT and lock_script.problems(unreleased, LOCK) == []
    first_link = next(line for line in TEXT.splitlines() if line.startswith("[Unreleased]: "))
    links = TEXT.replace(first_link, first_link + "\n[0.35.0]: https://example.org/compare", 1)
    assert links != TEXT and lock_script.problems(links, LOCK) == []


def test_a_released_section_left_without_a_hash_says_to_run_add() -> None:
    """Step 4 of RELEASING.md dates `[Unreleased]`; until `add` runs this fails, which is the
    step."""
    dated = TEXT.replace("## [Unreleased]", "## [9.9.9] — 2099-01-01", 1)
    (problem,) = lock_script.problems(dated, LOCK)
    assert problem.startswith("[9.9.9] is released and has no hash") and "add 9.9.9" in problem
    added = lock_script.add(dated, LOCK, "9.9.9")
    assert lock_script.problems(dated, added) == []
    assert next(iter(added["sections"])) == "9.9.9" and added["accepted"] == []


def test_add_locks_only_the_newest_released_section_and_only_once() -> None:
    dated = TEXT.replace("## [Unreleased]", "## [9.9.9] — 2099-01-01", 1)
    with pytest.raises(SystemExit, match=r"\[0\.1\.0\] is not the newest"):
        lock_script.add(dated, {"sections": {}, "accepted": []}, "0.1.0")
    with pytest.raises(SystemExit, match="locked already"):
        lock_script.add(TEXT, LOCK, RELEASED[0])
    with pytest.raises(SystemExit, match=r"no released section \[9\.9\.9\]"):
        lock_script.add(TEXT, LOCK, "9.9.9")


def test_update_takes_a_changed_hash_only_with_a_reason_and_keeps_it() -> None:
    changed = _changed_byte(TEXT, RELEASED[2])
    with pytest.raises(SystemExit, match="needs a reason"):
        lock_script.update(changed, LOCK, "  ", "2099-01-01")
    lock, versions = lock_script.update(changed, LOCK, "a mended typo", "2099-01-01")
    assert versions == [RELEASED[2]] and lock_script.problems(changed, lock) == []
    (entry,) = lock["accepted"]
    assert entry == {
        "version": RELEASED[2],
        "old": LOCK["sections"][RELEASED[2]],
        "new": lock["sections"][RELEASED[2]],
        "reason": "a mended typo",
        "date": "2099-01-01",
    }


def test_a_hash_whose_section_is_gone_is_named() -> None:
    body = lock_script.released(TEXT)[RELEASED[3]]
    found = lock_script.problems(TEXT.replace(body, "", 1), LOCK)
    assert f"[{RELEASED[3]}] has a hash and no section in CHANGELOG.md" in found


def test_the_command_line_checks_adds_and_accepts_on_the_files_it_is_given(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    changelog, lock = tmp_path / "CHANGELOG.md", tmp_path / "lock.json"
    changelog.write_bytes(TEXT.replace("## [Unreleased]", "## [9.9.9] — 2099-01-01", 1).encode())
    lock.write_text(json.dumps(LOCK), encoding="utf-8")
    paths = ["--changelog", str(changelog), "--lock", str(lock)]

    assert lock_script.main(paths) == 1
    assert "add 9.9.9" in capsys.readouterr().err
    assert lock_script.main([*paths, "add", "9.9.9"]) == 0
    assert lock_script.main(paths) == 0
    assert f"{len(RELEASED) + 1} released sections as locked" in capsys.readouterr().out

    changelog.write_bytes(_changed_byte(changelog.read_text(encoding="utf-8"), "9.9.9").encode())
    assert lock_script.main([*paths, "check"]) == 1
    assert lock_script.main([*paths, "update", "--accept", "a mended typo"]) == 0
    assert json.loads(lock.read_text(encoding="utf-8"))["accepted"][0]["version"] == "9.9.9"
    assert lock.read_bytes().endswith(b"}\n") and b"\r\n" not in lock.read_bytes()
